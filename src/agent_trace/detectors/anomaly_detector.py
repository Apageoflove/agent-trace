# src/agent_trace/detectors/anomaly_detector.py
"""异常检测 v1 — 5 特征规则模型

算法来源:
  - Random Forest 思想: 多特征投票 + 阈值加权 (PLAN.md M7)
  - 5 特征 (参考 Langfuse anomaly 检测 + IBM ICPE 2026):
    1. token_growth_rate: 单步 token 增长率 (EMA slope / window)
    2. span_error_rate: error span 数 / 总 span 数
    3. handoff_depth: agent handoff 链最大深度
    4. cycle_alert_count: M3/M4 检测到的环/死锁事件数
    5. context_utilization: 当前 context 占用率

  - 无 sklearn 依赖: 用规则投票 (每个特征超阈值 → 加权分)
  - 综合 anomaly_score ∈ [0, 1], 阈值 0.5 判定异常

门禁: F1 100% (precision 100% + recall 100%)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from agent_trace.detectors.context_bloat import BloatLevel


@dataclass(frozen=True)
class AnomalyFeature:
    name: str
    value: float
    threshold: float
    weight: float

    @property
    def is_triggered(self) -> bool:
        return self.value >= self.threshold


@dataclass(frozen=True)
class AnomalyResult:
    agent_id: str
    is_anomaly: bool
    score: float
    features: tuple[AnomalyFeature, ...]
    triggered_features: tuple[str, ...]


_DEFAULT_THRESHOLDS: dict[str, tuple[float, float]] = {
    "token_growth_rate": (0.3, 0.25),
    "span_error_rate": (0.2, 0.20),
    "handoff_depth": (5.0, 0.20),
    "cycle_alert_count": (1.0, 0.20),
    "context_utilization": (0.85, 0.15),
}


def _safe_divide(a: float, b: float) -> float:
    return a / b if b != 0 else 0.0


@dataclass
class AnomalyDetector:
    """Agent 运行异常检测器

    用法:
        detector = AnomalyDetector(context_window=128000)
        result = detector.evaluate(
            agent_id="a",
            token_history=[100, 200, 500, 1200],
            span_total=10,
            span_errors=3,
            handoff_depth=6,
            cycle_alerts=1,
            context_tokens=100000,
        )
        if result.is_anomaly:
            handle_anomaly(result)
    """

    _context_window: int = 128000
    _thresholds: dict[str, tuple[float, float]] = field(
        default_factory=lambda: dict(_DEFAULT_THRESHOLDS)
    )

    def __init__(self, context_window: int = 128000) -> None:
        self._context_window = context_window
        self._thresholds = dict(_DEFAULT_THRESHOLDS)

    def evaluate(
        self,
        agent_id: str,
        token_history: Sequence[int] | None = None,
        span_total: int = 0,
        span_errors: int = 0,
        handoff_depth: int = 0,
        cycle_alerts: int = 0,
        context_tokens: int = 0,
    ) -> AnomalyResult:
        token_history = token_history or []
        growth_rate = self._compute_growth_rate(token_history)
        error_rate = _safe_divide(float(span_errors), float(span_total))
        ctx_util = _safe_divide(float(context_tokens), float(self._context_window))

        features = (
            self._make_feature("token_growth_rate", growth_rate),
            self._make_feature("span_error_rate", error_rate),
            self._make_feature("handoff_depth", float(handoff_depth)),
            self._make_feature("cycle_alert_count", float(cycle_alerts)),
            self._make_feature("context_utilization", ctx_util),
        )

        total_weight = sum(f.weight for f in features)
        triggered_weight = sum(f.weight for f in features if f.is_triggered)
        score = _safe_divide(triggered_weight, total_weight)
        triggered_names = tuple(f.name for f in features if f.is_triggered)

        return AnomalyResult(
            agent_id=agent_id,
            is_anomaly=score >= 0.5,
            score=score,
            features=features,
            triggered_features=triggered_names,
        )

    def _compute_growth_rate(self, history: Sequence[int]) -> float:
        if len(history) < 2:
            return 0.0
        prev = history[-2]
        curr = history[-1]
        if prev == 0:
            return 1.0 if curr > 0 else 0.0
        return abs(curr - prev) / prev

    def _make_feature(self, name: str, value: float) -> AnomalyFeature:
        threshold, weight = self._thresholds[name]
        return AnomalyFeature(
            name=name,
            value=value,
            threshold=threshold,
            weight=weight,
        )
