# tests/test_context_bloat.py
"""M5 门禁测试: Context Bloat 预警

门禁: 告警触发率 100% (precision 100% + recall 100%) + 预测 MAE ≤15%
覆盖:
  - L1 tiktoken 精确计数
  - L2 EMA 预测趋势
  - 四级阈值告警 (50/75/90/95%) 依次触发
  - 告警去重 (同级别不重复)
  - 100 步线性增长实验 — 告警触发率 100%
  - 预测 MAE 验证
  - 多 agent 独立追踪
  - 边界: 空文本/超限/重置
"""

from __future__ import annotations

import pytest

from agent_trace.detectors.context_bloat import (
    BloatLevel,
    ContextBloatAlert,
    ContextBloatDetector,
)


class TestL1TokenCounting:
    def test_count_tokens_basic(self):
        d = ContextBloatDetector(context_window=1000)
        assert d.count_tokens("hello world") > 0

    def test_count_tokens_empty(self):
        d = ContextBloatDetector(context_window=1000)
        assert d.count_tokens("") == 0

    def test_count_tokens_cjk(self):
        d = ContextBloatDetector(context_window=1000)
        tokens = d.count_tokens("你好世界")
        assert tokens > 0


class TestL2EMAPrediction:
    def test_predict_before_tracking_returns_zero(self):
        d = ContextBloatDetector(context_window=1000)
        assert d.predict("agent_1") == 0

    def test_predict_after_first_step(self):
        d = ContextBloatDetector(context_window=1000, alpha=0.3)
        d.track_tokens("agent_1", 100)
        predicted = d.predict("agent_1")
        assert predicted >= 0

    def test_predict_increasing_trend(self):
        d = ContextBloatDetector(context_window=10000, alpha=0.5)
        for i in range(10):
            d.track_tokens("a", 100 + i * 50)
        predicted_now = d.predict("a", steps_ahead=1)
        predicted_future = d.predict("a", steps_ahead=5)
        assert predicted_future >= predicted_now


class TestThresholdAlerts:
    def test_info_threshold_50_percent(self):
        d = ContextBloatDetector(context_window=1000)
        # d.track_tokens("a", 500)
        # alert = d.track_tokens("a", 10)
        # assert alert is not None
        alert = d.track_tokens("a", 500)
        assert alert is not None
        assert alert.level == BloatLevel.INFO

    def test_warning_threshold_75_percent(self):
        d = ContextBloatDetector(context_window=1000)
        d.track_tokens("a", 750)
        d.track_tokens("a", 1)
        alerts = d.get_all_alerts()
        levels = {a.level for a in alerts}
        assert BloatLevel.WARNING in levels

    def test_error_threshold_90_percent(self):
        d = ContextBloatDetector(context_window=1000)
        d.track_tokens("a", 900)
        d.track_tokens("a", 1)
        alerts = d.get_all_alerts()
        levels = {a.level for a in alerts}
        assert BloatLevel.ERROR in levels

    def test_critical_threshold_95_percent(self):
        d = ContextBloatDetector(context_window=1000)
        d.track_tokens("a", 950)
        d.track_tokens("a", 1)
        alerts = d.get_all_alerts()
        levels = {a.level for a in alerts}
        assert BloatLevel.CRITICAL in levels

    def test_no_duplicate_same_level(self):
        d = ContextBloatDetector(context_window=1000)
        # d.track_tokens("a", 500)
        # alert1 = d.track_tokens("a", 10)
        # assert alert1 is not None
        # alert2 = d.track_tokens("a", 10)
        # assert alert2 is None or alert2.level != BloatLevel.INFO
        alert1 = d.track_tokens("a", 500)
        assert alert1 is not None
        assert alert1.level == BloatLevel.INFO
        alert2 = d.track_tokens("a", 10)
        assert alert2 is None


class TestCallbackAndHistory:
    def test_callback_fires(self):
        events: list[ContextBloatAlert] = []
        d = ContextBloatDetector(context_window=1000, on_alert=events.append)
        d.track_tokens("a", 600)
        assert len(events) >= 1

    def test_get_history(self):
        d = ContextBloatDetector(context_window=1000)
        d.track_tokens("a", 100)
        d.track_tokens("a", 200)
        history = d.get_history("a")
        assert history == [100, 200]

    def test_reset_agent(self):
        d = ContextBloatDetector(context_window=1000)
        d.track_tokens("a", 100)
        d.reset_agent("a")
        assert d.get_history("a") == []

    def test_clear(self):
        d = ContextBloatDetector(context_window=1000)
        d.track_tokens("a", 600)
        d.clear()
        assert d.get_all_alerts() == []
        assert d.get_history("a") == []


class TestMultiAgent:
    def test_independent_tracking(self):
        d = ContextBloatDetector(context_window=1000)
        d.track_tokens("a", 600)
        d.track_tokens("b", 100)
        assert d.get_utilization("a") > d.get_utilization("b")


class Test100StepExperiment:
    """100 步线性增长实验 — 门禁: 告警触发率 100%

    构造: context_window=10000, 100 步每步加 100 token (0→10000)
    期望: 在步骤 ~50 触发 INFO, ~75 触发 WARNING, ~90 触发 ERROR, ~95 触发 CRITICAL
    门禁: 四级告警全部触发 (recall 100%), 无误报 (precision 100%)
    """

    def test_100_step_alert_trigger_rate_100(self):
        window = 10000
        d = ContextBloatDetector(context_window=window, alpha=0.3)

        fired_levels: set[BloatLevel] = set()
        for step in range(100):
            d.track_tokens("agent_1", 100)
            for alert in d.get_all_alerts():
                fired_levels.add(alert.level)

        expected_levels = {
            BloatLevel.INFO,
            BloatLevel.WARNING,
            BloatLevel.ERROR,
            BloatLevel.CRITICAL,
        }

        missing = expected_levels - fired_levels
        assert not missing, f"告警漏触发: {missing}, 实际触发: {fired_levels}"

        alerts = d.get_all_alerts()
        for alert in alerts:
            assert alert.current_tokens > 0
            assert alert.utilization > 0
            expected_min = d._thresholds[alert.level] * window
            assert alert.current_tokens >= expected_min * 0.95, (
                f"误报: level={alert.level.name} "
                f"tokens={alert.current_tokens} threshold={expected_min}"
            )

        print(
            f"\n[M5 Benchmark] 触发级别={sorted(l.name for l in fired_levels)} | "
            f"告警总数={len(alerts)}"
        )


class TestPredictionMAE:
    """预测 MAE 门禁: ≤15%"""

    def test_mae_under_15_percent(self):
        window = 10000
        d = ContextBloatDetector(context_window=window, alpha=0.5)

        actual_tokens: list[int] = []
        predictions: list[int] = []

        for step in range(50):
            actual = 100 + step * 50
            d.track_tokens("a", 50)
            pred = d.predict("a", steps_ahead=1)
            actual_tokens.append(actual)
            predictions.append(pred)

        trackable = [
            (a, p) for a, p in zip(actual_tokens, predictions) if p > 0
        ]
        assert len(trackable) >= 10

        errors = [abs(a - p) / a for a, p in trackable]
        mae = sum(errors) / len(errors)

        print(f"\n[M5 MAE] samples={len(trackable)} MAE={mae:.4f} ({mae*100:.1f}%)")
        assert mae <= 0.15, f"预测 MAE={mae:.4f} 超过 15% 门禁"


class TestEdgeCases:
    def test_zero_context_window(self):
        d = ContextBloatDetector(context_window=0)
        d.track_tokens("a", 100)
        assert d.get_utilization("a") == 0.0

    def test_single_large_input(self):
        d = ContextBloatDetector(context_window=1000)
        d.track_tokens("a", 1500)
        alerts = d.get_all_alerts()
        levels = {a.level for a in alerts}
        assert BloatLevel.CRITICAL in levels

    def test_utilization_property(self):
        d = ContextBloatDetector(context_window=1000)
        d.track_tokens("a", 250)
        assert d.get_utilization("a") == pytest.approx(0.25)
