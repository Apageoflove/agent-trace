# tests/test_cycle_detector.py
"""M3 门禁测试: 环检测模块

门禁: 结构环 F1 ≥99%（precision 100% + recall 100%）
覆盖:
  - 基本环检测（2 节点 / 3 节点 / 4 节点）
  - 无环图（DAG）正确判定
  - 自环
  - 多个独立环
  - 嵌套环
  - 增量添加边后触发检测
  - 50 图 benchmark（25 含环 + 25 无环）统计 F1
  - 回调通知
  - 边界: 空图 / 单节点 / 重复边
"""

from __future__ import annotations

import random
from typing import Sequence

import pytest

from agent_trace.detectors.cycle_detector import (
    CycleDetected,
    CycleDetector,
)


# ---------------------------------------------------------------------------
# 1. 基本环检测
# ---------------------------------------------------------------------------


class TestBasicCycleDetection:
    def test_two_node_cycle(self):
        d = CycleDetector()
        assert d.add_edge("a", "b") is None
        result = d.add_edge("b", "a")
        assert result is not None
        assert set(result.cycle) == {"a", "b"}

    def test_three_node_cycle(self):
        d = CycleDetector()
        d.add_edge("a", "b")
        d.add_edge("b", "c")
        result = d.add_edge("c", "a")
        assert result is not None
        assert set(result.cycle) == {"a", "b", "c"}

    def test_four_node_cycle(self):
        d = CycleDetector()
        d.add_edge("a", "b")
        d.add_edge("b", "c")
        d.add_edge("c", "d")
        result = d.add_edge("d", "a")
        assert result is not None
        assert set(result.cycle) == {"a", "b", "c", "d"}

    def test_no_cycle_dag(self):
        d = CycleDetector()
        d.add_edge("a", "b")
        d.add_edge("a", "c")
        d.add_edge("b", "d")
        d.add_edge("c", "d")
        assert d.has_cycle() is False

    def test_has_cycle_returns_false_on_empty(self):
        assert CycleDetector().has_cycle() is False


# ---------------------------------------------------------------------------
# 2. 自环
# ---------------------------------------------------------------------------


class TestSelfLoop:
    def test_self_loop_detected(self):
        d = CycleDetector()
        result = d.add_edge("a", "a")
        assert result is not None
        assert result.detection_method == "tarjan_scc_self_loop"
        assert result.cycle == ("a", "a")

    def test_self_loop_in_larger_graph(self):
        d = CycleDetector()
        d.add_edge("a", "b")
        d.add_edge("b", "c")
        result = d.add_edge("c", "c")
        assert result is not None
        assert result.cycle == ("c", "c")


# ---------------------------------------------------------------------------
# 3. 多环 & 嵌套
# ---------------------------------------------------------------------------


class TestMultipleCycles:
    def test_two_independent_cycles(self):
        d = CycleDetector()
        d.add_edge("a", "b")
        d.add_edge("b", "a")
        d.add_edge("c", "d")
        d.add_edge("d", "c")
        cycles = d.find_cycles()
        assert len(cycles) >= 2

    def test_shared_node_cycles(self):
        d = CycleDetector()
        d.add_edge("a", "b")
        d.add_edge("b", "c")
        d.add_edge("c", "a")
        d.add_edge("a", "d")
        d.add_edge("d", "a")
        cycles = d.find_cycles()
        assert len(cycles) >= 1


# ---------------------------------------------------------------------------
# 4. 增量检测 & 回调
# ---------------------------------------------------------------------------


class TestIncrementalAndCallback:
    def test_callback_fires_on_cycle(self):
        events: list[CycleDetected] = []
        d = CycleDetector(on_cycle=events.append)
        d.add_edge("a", "b")
        d.add_edge("b", "c")
        assert len(events) == 0
        d.add_edge("c", "a")
        assert len(events) >= 1

    def test_get_all_cycles_history(self):
        d = CycleDetector()
        d.add_edge("a", "b")
        d.add_edge("b", "a")
        d.add_edge("c", "d")
        d.add_edge("d", "c")
        all_cycles = d.get_all_cycles()
        assert len(all_cycles) >= 2

    def test_add_handoff_method(self):
        d = CycleDetector()
        d.add_handoff("agent_a", "agent_b")
        d.add_handoff("agent_b", "agent_a")
        assert d.has_cycle() is True


# ---------------------------------------------------------------------------
# 5. 边界用例
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_graph(self):
        d = CycleDetector()
        assert d.has_cycle() is False
        assert d.find_cycles() == []

    def test_single_node_no_edge(self):
        d = CycleDetector()
        d.add_edge("a", "b")
        d._adj.pop("a")
        d._adj["a"] = set()
        assert d.has_cycle() is False

    def test_duplicate_edge_no_false_cycle(self):
        d = CycleDetector()
        d.add_edge("a", "b")
        d.add_edge("a", "b")
        d.add_edge("a", "b")
        assert d.has_cycle() is False

    def test_clear(self):
        d = CycleDetector()
        d.add_edge("a", "b")
        d.add_edge("b", "a")
        d.clear()
        assert d.has_cycle() is False
        assert d.node_count == 0

    def test_node_and_edge_count(self):
        d = CycleDetector()
        d.add_edge("a", "b")
        d.add_edge("b", "c")
        assert d.node_count == 3
        assert d.edge_count == 2


# ---------------------------------------------------------------------------
# 6. 50 图 benchmark — F1 门禁 ≥99%
# ---------------------------------------------------------------------------


class TestBenchmark50Graphs:
    """50 图 benchmark: 25 含环 + 25 无环，统计 precision/recall/F1

    门禁: F1 ≥ 99%（结构环理论上应为 100%）
    """

    def test_50_graphs_f1_above_99(self):
        random.seed(42)
        graphs: list[tuple[list[tuple[str, str]], bool]] = []

        for i in range(25):
            n = random.randint(3, 8)
            nodes = [f"n{i}_{j}" for j in range(n)]
            edges, _ = _make_cycle_graph(nodes)
            graphs.append((edges, True))

        for i in range(25):
            n = random.randint(3, 8)
            nodes = [f"d{i}_{j}" for j in range(n)]
            edges = _make_dag_graph(nodes)
            graphs.append((edges, False))

        tp = fp = fn = tn = 0
        for edges, expected_has_cycle in graphs:
            d = CycleDetector()
            for src, tgt in edges:
                d.add_edge(src, tgt)
            predicted = d.has_cycle()
            if expected_has_cycle and predicted:
                tp += 1
            elif expected_has_cycle and not predicted:
                fn += 1
            elif not expected_has_cycle and predicted:
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
            f"\n[M3 Benchmark] TP={tp} FP={fp} FN={fn} TN={tn} | "
            f"precision={precision:.4f} recall={recall:.4f} F1={f1:.4f}"
        )

        assert tp == 25, f"recall 不足: tp={tp}, fn={fn}"
        assert tn == 25, f"precision 不足: fp={fp}, tn={tn}"
        assert fn == 0, f"漏检 {fn} 个含环图"
        assert fp == 0, f"误检 {fp} 个无环图"
        assert f1 >= 0.99, f"F1={f1:.4f} 未达 99% 门禁"


def _make_cycle_graph(nodes: Sequence[str]) -> tuple[list[tuple[str, str]], None]:
    """构造一个含环图：n 个节点形成 n-边形环 + 几条额外边"""
    edges: list[tuple[str, str]] = []
    n = len(nodes)
    for i in range(n):
        edges.append((nodes[i], nodes[(i + 1) % n]))
    if n > 3:
        edges.append((nodes[0], nodes[2]))
    return edges, None


def _make_dag_graph(nodes: Sequence[str]) -> list[tuple[str, str]]:
    """构造一个无环图：只添加 i < j 的边"""
    edges: list[tuple[str, str]] = []
    n = len(nodes)
    for i in range(n):
        for j in range(i + 1, n):
            if random.random() < 0.4:
                edges.append((nodes[i], nodes[j]))
    if not edges and n >= 2:
        edges.append((nodes[0], nodes[1]))
    return edges
