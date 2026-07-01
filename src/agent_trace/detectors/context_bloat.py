# src/agent_trace/detectors/context_bloat.py
"""Context Bloat 预警模块 — 三层管道

算法来源:
  - L1 tiktoken: OpenAI 官方 tokenizer, 精确计算当前 token 数
  - L2 EMA (Exponential Moving Average): 指数移动平均预测趋势
    公式: ema_t = alpha * x_t + (1-alpha) * ema_{t-1}
    预测: ema_t + slope_t * steps_ahead, slope = ema_t - ema_{t-1}
  - L3 EGTP (Enhanced Gradient-based Token Prediction): 可选, 基于二阶梯度

四级阈值告警 (context window 占比):
  - 50%: INFO
  - 75%: WARNING
  - 90%: ERROR
  - 95%: CRITICAL

门禁: 告警触发率 100% (precision 100% + recall 100%) + 预测 MAE ≤15%
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable

import tiktoken


class BloatLevel(IntEnum):
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4


BloatCallback = Callable[["ContextBloatAlert"], None]

_DEFAULT_THRESHOLDS: dict[BloatLevel, float] = {
    BloatLevel.INFO: 0.50,
    BloatLevel.WARNING: 0.75,
    BloatLevel.ERROR: 0.90,
    BloatLevel.CRITICAL: 0.95,
}


@dataclass(frozen=True)
class ContextBloatAlert:
    agent_id: str
    level: BloatLevel
    current_tokens: int
    context_window: int
    utilization: float
    predicted_tokens: int
    steps_ahead: int

    @property
    def predicted_utilization(self) -> float:
        return self.predicted_tokens / self.context_window if self.context_window > 0 else 0.0


@dataclass
class _AgentState:
    history: list[int] = field(default_factory=list)
    ema: float = 0.0
    prev_ema: float = 0.0
    fired_levels: set[BloatLevel] = field(default_factory=set)
    current_tokens: int = 0


@dataclass
class ContextBloatDetector:
    """Agent context 膨胀检测器

    用法:
        detector = ContextBloatDetector(context_window=128000)
        for step_text in agent_steps:
            alert = detector.track("agent_1", step_text)
            if alert:
                handle_alert(alert)
        future = detector.predict("agent_1", steps_ahead=5)

    三层管道:
        L1: tiktoken 精确计算当前 token
        L2: EMA 预测趋势 (alpha=0.3 默认)
        L3: 阈值检查 + 告警去重 (同级别不重复触发)
    """

    _context_window: int = 128000
    _alpha: float = 0.3
    _encoding_name: str = "cl100k_base"
    _thresholds: dict[BloatLevel, float] = field(
        default_factory=lambda: dict(_DEFAULT_THRESHOLDS)
    )
    _states: dict[str, _AgentState] = field(default_factory=dict)
    _on_alert: BloatCallback | None = None
    _alerts: list[ContextBloatAlert] = field(default_factory=list)
    _encoding: object = None

    def __init__(
        self,
        context_window: int = 128000,
        alpha: float = 0.3,
        encoding_name: str = "cl100k_base",
        on_alert: BloatCallback | None = None,
    ) -> None:
        self._context_window = context_window
        self._alpha = alpha
        self._encoding_name = encoding_name
        self._thresholds = dict(_DEFAULT_THRESHOLDS)
        self._states = {}
        self._on_alert = on_alert
        self._alerts = []
        self._encoding = tiktoken.get_encoding(encoding_name)

    def count_tokens(self, text: str) -> int:
        """L1: 用 tiktoken 精确计算 text 的 token 数"""
        return len(self._encoding.encode(text))

    def track(self, agent_id: str, text: str) -> ContextBloatAlert | None:
        """追踪 agent 的 context 增长

        累积 token 到 agent 的 history, 更新 EMA, 检查阈值
        返回: 若触发新级别告警返回 ContextBloatAlert, 否则 None
        """
        tokens = self.count_tokens(text)
        return self.track_tokens(agent_id, tokens)

    def track_tokens(self, agent_id: str, tokens: int) -> ContextBloatAlert | None:
        """直接追加 token 数 (跳过 tiktoken, 用于测试或已知 token 数场景)"""
        state = self._states.get(agent_id)
        if state is None:
            state = _AgentState()
            self._states[agent_id] = state

        state.prev_ema = state.ema
        if not state.history:
            state.ema = float(tokens)
        else:
            state.ema = self._alpha * tokens + (1 - self._alpha) * state.ema

        state.history.append(tokens)
        state.current_tokens += tokens

        return self._check_thresholds(agent_id, state)

    def predict(self, agent_id: str, steps_ahead: int = 1) -> int:
        """L2: 用 EMA 趋势预测 steps_ahead 步后的累积 token 总量

        预测公式: current_tokens + ema * steps_ahead
        ema 跟踪每步增量, slope 修正可选
        """
        state = self._states.get(agent_id)
        if state is None or not state.history:
            return 0
        slope = state.ema - state.prev_ema
        predicted_increment = state.ema + slope * steps_ahead
        predicted_total = state.current_tokens + max(0, predicted_increment) * steps_ahead
        return max(0, int(predicted_total))

    def get_utilization(self, agent_id: str) -> float:
        state = self._states.get(agent_id)
        if state is None or self._context_window == 0:
            return 0.0
        return state.current_tokens / self._context_window

    def get_history(self, agent_id: str) -> list[int]:
        state = self._states.get(agent_id)
        return list(state.history) if state else []

    def get_all_alerts(self) -> list[ContextBloatAlert]:
        return list(self._alerts)

    def clear(self) -> None:
        self._states.clear()
        self._alerts.clear()

    def reset_agent(self, agent_id: str) -> None:
        if agent_id in self._states:
            del self._states[agent_id]

    @property
    def context_window(self) -> int:
        return self._context_window

    def _check_thresholds(
        self, agent_id: str, state: _AgentState
    ) -> ContextBloatAlert | None:
        utilization = (
            state.current_tokens / self._context_window
            if self._context_window > 0
            else 1.0
        )
        predicted = self.predict(agent_id, steps_ahead=3)

        triggered_level: BloatLevel | None = None
        for level in sorted(BloatLevel, key=lambda l: -l.value):
            threshold = self._thresholds[level]
            if utilization >= threshold and level not in state.fired_levels:
                triggered_level = level
                break

        if triggered_level is None:
            return None

        state.fired_levels.add(triggered_level)
        alert = ContextBloatAlert(
            agent_id=agent_id,
            level=triggered_level,
            current_tokens=state.current_tokens,
            context_window=self._context_window,
            utilization=utilization,
            predicted_tokens=predicted,
            steps_ahead=3,
        )
        self._alerts.append(alert)
        if self._on_alert is not None:
            self._on_alert(alert)
        return alert
