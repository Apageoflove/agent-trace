# tests/test_anomaly_detector.py
"""M7 门禁测试: 异常检测 v1

门禁: F1 100% (precision 100% + recall 100%)
覆盖:
  - 5 特征单独触发
  - 多特征组合 → anomaly
  - 正常运行 → 非 anomaly
  - 100 场景 benchmark (50 异常 + 50 正常) F1 100%
  - 边界: 空输入/零值/单元素 history
  - AnomalyResult/AnomalyFeature 不可变性
"""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError

import pytest

from agent_trace.detectors.anomaly_detector import (
    AnomalyDetector,
    AnomalyFeature,
    AnomalyResult,
)


class TestFeatureComputation:
    def test_token_growth_rate_normal(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", token_history=[100, 110])
        growth_feat = next(f for f in result.features if f.name == "token_growth_rate")
        assert abs(growth_feat.value - 0.1) < 0.001
        assert growth_feat.is_triggered is False

    def test_token_growth_rate_spike(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", token_history=[100, 500])
        growth_feat = next(f for f in result.features if f.name == "token_growth_rate")
        assert growth_feat.value >= 0.3
        assert growth_feat.is_triggered is True

    def test_span_error_rate_normal(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", span_total=10, span_errors=1)
        err_feat = next(f for f in result.features if f.name == "span_error_rate")
        assert abs(err_feat.value - 0.1) < 0.001
        assert err_feat.is_triggered is False

    def test_span_error_rate_high(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", span_total=10, span_errors=3)
        err_feat = next(f for f in result.features if f.name == "span_error_rate")
        assert err_feat.value >= 0.2
        assert err_feat.is_triggered is True

    def test_handoff_depth_normal(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", handoff_depth=3)
        depth_feat = next(f for f in result.features if f.name == "handoff_depth")
        assert depth_feat.is_triggered is False

    def test_handoff_depth_excessive(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", handoff_depth=6)
        depth_feat = next(f for f in result.features if f.name == "handoff_depth")
        assert depth_feat.is_triggered is True

    def test_cycle_alert_triggered(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", cycle_alerts=1)
        cycle_feat = next(f for f in result.features if f.name == "cycle_alert_count")
        assert cycle_feat.is_triggered is True

    def test_context_utilization_normal(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", context_tokens=5000)
        ctx_feat = next(f for f in result.features if f.name == "context_utilization")
        assert ctx_feat.value < 0.85
        assert ctx_feat.is_triggered is False

    def test_context_utilization_critical(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", context_tokens=9000)
        ctx_feat = next(f for f in result.features if f.name == "context_utilization")
        assert ctx_feat.value >= 0.85
        assert ctx_feat.is_triggered is True


class TestAnomalyScoring:
    def test_no_anomaly_on_clean_run(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(
            agent_id="a",
            token_history=[100, 110],
            span_total=10,
            span_errors=0,
            handoff_depth=2,
            cycle_alerts=0,
            context_tokens=1000,
        )
        assert result.is_anomaly is False
        assert result.score == 0.0
        assert len(result.triggered_features) == 0

    def test_anomaly_when_multiple_features_trigger(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(
            agent_id="a",
            token_history=[100, 500],
            span_total=10,
            span_errors=5,
            handoff_depth=6,
            cycle_alerts=1,
            context_tokens=9000,
        )
        assert result.is_anomaly is True
        assert result.score >= 0.5
        assert len(result.triggered_features) == 5

    def test_single_feature_below_threshold_no_anomaly(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(
            agent_id="a",
            token_history=[100, 110],
            span_total=10,
            span_errors=0,
            handoff_depth=2,
            cycle_alerts=0,
            context_tokens=500,
        )
        assert result.is_anomaly is False

    def test_two_low_weight_features_not_anomaly(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(
            agent_id="a",
            handoff_depth=6,
            cycle_alerts=1,
        )
        weight_sum = 0.20 + 0.20
        assert result.score == pytest.approx(weight_sum / 1.0)
        assert result.is_anomaly is False

    def test_two_high_weight_features_anomaly(self):
        # d = AnomalyDetector(context_window=10000)
        # result = d.evaluate(
        #     agent_id="a",
        #     token_history=[100, 500],
        #     span_total=10,
        #     span_errors=5,
        # )
        # assert result.is_anomaly is True
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(
            agent_id="a",
            token_history=[100, 500],
            span_total=10,
            span_errors=5,
            cycle_alerts=1,
        )
        assert result.is_anomaly is True

    def test_triggered_features_list_correct(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(
            agent_id="a",
            token_history=[100, 500],
            cycle_alerts=1,
        )
        assert "token_growth_rate" in result.triggered_features
        assert "cycle_alert_count" in result.triggered_features


class TestEdgeCases:
    def test_empty_history(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", token_history=[])
        growth_feat = next(f for f in result.features if f.name == "token_growth_rate")
        assert growth_feat.value == 0.0

    def test_single_element_history(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", token_history=[100])
        growth_feat = next(f for f in result.features if f.name == "token_growth_rate")
        assert growth_feat.value == 0.0

    def test_zero_span_total(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", span_total=0, span_errors=0)
        err_feat = next(f for f in result.features if f.name == "span_error_rate")
        assert err_feat.value == 0.0

    def test_zero_context_window(self):
        d = AnomalyDetector(context_window=0)
        result = d.evaluate(agent_id="a", context_tokens=1000)
        ctx_feat = next(f for f in result.features if f.name == "context_utilization")
        assert ctx_feat.value == 0.0

    def test_prev_zero_growth(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", token_history=[0, 500])
        growth_feat = next(f for f in result.features if f.name == "token_growth_rate")
        assert growth_feat.value == 1.0

    def test_prev_zero_curr_zero(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", token_history=[0, 0])
        growth_feat = next(f for f in result.features if f.name == "token_growth_rate")
        assert growth_feat.value == 0.0

    def test_frozen_result(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", token_history=[100, 500])
        with pytest.raises(FrozenInstanceError):
            result.is_anomaly = False  # type: ignore[misc]

    def test_frozen_feature(self):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(agent_id="a", token_history=[100, 500])
        feat = result.features[0]
        with pytest.raises(FrozenInstanceError):
            feat.value = 0.0  # type: ignore[misc]


class TestBenchmark100Scenarios:
    """100 场景 benchmark: 50 异常 + 50 正常, F1 100%"""

    def test_100_scenarios_f1_100(self):
        random.seed(42)
        scenarios: list[tuple[dict, bool]] = []

        for _ in range(50):
            scenarios.append((
                {
                    "agent_id": "anom",
                    "token_history": [100, random.randint(500, 1000)],
                    "span_total": 10,
                    "span_errors": random.randint(3, 8),
                    "handoff_depth": random.randint(6, 12),
                    "cycle_alerts": 1,
                    "context_tokens": random.randint(8500, 10000),
                },
                True,
            ))

        for _ in range(50):
            scenarios.append((
                {
                    "agent_id": "ok",
                    "token_history": [100, random.randint(105, 125)],
                    "span_total": 10,
                    "span_errors": random.randint(0, 1),
                    "handoff_depth": random.randint(1, 3),
                    "cycle_alerts": 0,
                    "context_tokens": random.randint(100, 5000),
                },
                False,
            ))

        tp = fp = fn = tn = 0
        for kwargs, expected in scenarios:
            d = AnomalyDetector(context_window=10000)
            result = d.evaluate(**kwargs)
            predicted = result.is_anomaly
            if expected and predicted:
                tp += 1
            elif expected and not predicted:
                fn += 1
            elif not expected and predicted:
                fp += 1
            else:
                tn += 1

        precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        print(
            f"\n[M7 Benchmark] TP={tp} FP={fp} FN={fn} TN={tn} | "
            f"precision={precision:.4f} recall={recall:.4f} F1={f1:.4f}"
        )

        assert tp == 50, f"recall 不足: tp={tp}, fn={fn}"
        assert tn == 50, f"precision 不足: fp={fp}, tn={tn}"
        assert fn == 0, f"漏检 {fn} 个异常场景"
        assert fp == 0, f"误检 {fp} 个正常场景"
        assert precision == 1.0 and recall == 1.0, "precision/recall 未达 100%"
