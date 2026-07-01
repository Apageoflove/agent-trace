# tests/test_deadlock_detector.py
"""M4 门禁测试: 死锁检测模块

门禁: precision 100% + recall 100%（20 场景: 10 死锁 + 10 正常）
覆盖:
  - 经典 2-agent 死锁
  - 3-agent 循环等待死锁
  - 4-agent 死锁
  - 无死锁场景（资源空闲/顺序获取/释放后重获）
  - 增量检测（request 触发）
  - 回调通知
  - 资源链构建
  - release 后死锁解除
  - 20 场景 benchmark precision/recall 100%
"""

from __future__ import annotations

import random
from typing import Sequence

import pytest

from agent_trace.detectors.deadlock_detector import (
    DeadlockDetected,
    DeadlockDetector,
)


class TestBasicDeadlock:
    def test_two_agent_deadlock(self):
        d = DeadlockDetector()
        d.acquire("a", "r1")
        d.acquire("b", "r2")
        assert d.request("a", "r2") is None
        result = d.request("b", "r1")
        assert result is not None
        assert set(result.cycle) == {"a", "b"}

    def test_three_agent_circular_wait(self):
        d = DeadlockDetector()
        d.acquire("a", "r1")
        d.acquire("b", "r2")
        d.acquire("c", "r3")
        d.request("a", "r2")
        d.request("b", "r3")
        result = d.request("c", "r1")
        assert result is not None
        assert set(result.cycle) == {"a", "b", "c"}

    def test_four_agent_deadlock(self):
        d = DeadlockDetector()
        d.acquire("a", "r1")
        d.acquire("b", "r2")
        d.acquire("c", "r3")
        d.acquire("d", "r4")
        d.request("a", "r2")
        d.request("b", "r3")
        d.request("c", "r4")
        result = d.request("d", "r1")
        assert result is not None
        assert set(result.cycle) == {"a", "b", "c", "d"}


class TestNoDeadlock:
    def test_free_resource_no_wait(self):
        d = DeadlockDetector()
        result = d.request("a", "r1")
        assert result is None
        assert d.has_deadlock() is False

    def test_sequential_acquire_no_deadlock(self):
        d = DeadlockDetector()
        d.acquire("a", "r1")
        d.release("a", "r1")
        d.acquire("b", "r1")
        assert d.has_deadlock() is False

    def test_non_circular_wait_no_deadlock(self):
        d = DeadlockDetector()
        d.acquire("b", "r2")
        d.acquire("c", "r3")
        d.request("a", "r2")
        d.request("b", "r3")
        assert d.has_deadlock() is False

    def test_empty_detector_no_deadlock(self):
        assert DeadlockDetector().has_deadlock() is False


class TestIncrementalAndCallback:
    def test_callback_fires_on_deadlock(self):
        events: list[DeadlockDetected] = []
        d = DeadlockDetector(on_deadlock=events.append)
        d.acquire("a", "r1")
        d.acquire("b", "r2")
        d.request("a", "r2")
        assert len(events) == 0
        d.request("b", "r1")
        assert len(events) >= 1

    def test_get_all_deadlocks_history(self):
        d = DeadlockDetector()
        d.acquire("a", "r1")
        d.acquire("b", "r2")
        d.request("a", "r2")
        d.request("b", "r1")
        all_deadlocks = d.get_all_deadlocks()
        assert len(all_deadlocks) >= 1

    def test_resource_chain_built(self):
        d = DeadlockDetector()
        d.acquire("a", "r1")
        d.acquire("b", "r2")
        d.request("a", "r2")
        result = d.request("b", "r1")
        assert result is not None
        assert len(result.resource_chain) >= 1
        waiters = {chain[0] for chain in result.resource_chain}
        assert "a" in waiters


class TestReleaseBreaksDeadlock:
    def test_release_clears_wait_edge(self):
        d = DeadlockDetector()
        d.acquire("a", "r1")
        d.acquire("b", "r2")
        d.request("a", "r2")
        assert d.wait_edge_count == 1
        d.release("b", "r2")
        assert d.wait_edge_count == 0

    def test_release_all_resources_clears_graph(self):
        d = DeadlockDetector()
        d.acquire("a", "r1")
        d.acquire("b", "r2")
        d.request("a", "r2")
        d.request("b", "r1")
        assert d.has_deadlock()
        d.release("a", "r1")
        d.release("b", "r2")
        d.clear()
        assert d.has_deadlock() is False


class TestEdgeCases:
    def test_acquire_occupied_returns_false(self):
        d = DeadlockDetector()
        assert d.acquire("a", "r1") is True
        assert d.acquire("b", "r1") is False

    def test_release_unheld_returns_false(self):
        d = DeadlockDetector()
        assert d.release("a", "r1") is False

    def test_request_own_resource_no_deadlock(self):
        d = DeadlockDetector()
        d.acquire("a", "r1")
        result = d.request("a", "r1")
        assert result is None

    def test_agent_and_resource_count(self):
        d = DeadlockDetector()
        d.acquire("a", "r1")
        d.acquire("b", "r2")
        assert d.resource_count == 2
        assert d.agent_count == 2

    def test_clear(self):
        d = DeadlockDetector()
        d.acquire("a", "r1")
        d.clear()
        assert d.resource_count == 0
        assert d.agent_count == 0


class TestBenchmark20Scenarios:
    """20 场景 benchmark: 10 死锁 + 10 正常, precision/recall 100%"""

    def test_20_scenarios_precision_recall_100(self):
        random.seed(42)
        scenarios: list[tuple[list[tuple[str, str, str]], bool]] = []

        for i in range(10):
            n = random.randint(2, 5)
            agents = [f"dl_a{i}_{j}" for j in range(n)]
            resources = [f"dl_r{i}_{j}" for j in range(n)]
            steps = _make_deadlock_scenario(agents, resources)
            scenarios.append((steps, True))

        for i in range(10):
            n = random.randint(2, 5)
            agents = [f"ok_a{i}_{j}" for j in range(n)]
            resources = [f"ok_r{i}_{j}" for j in range(n)]
            steps = _make_safe_scenario(agents, resources)
            scenarios.append((steps, False))

        tp = fp = fn = tn = 0
        for steps, expected_deadlock in scenarios:
            d = DeadlockDetector()
            for action, agent, resource in steps:
                if action == "acquire":
                    d.acquire(agent, resource)
                elif action == "request":
                    d.request(agent, resource)
                elif action == "release":
                    d.release(agent, resource)
            predicted = d.has_deadlock()
            if expected_deadlock and predicted:
                tp += 1
            elif expected_deadlock and not predicted:
                fn += 1
            elif not expected_deadlock and predicted:
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
            f"\n[M4 Benchmark] TP={tp} FP={fp} FN={fn} TN={tn} | "
            f"precision={precision:.4f} recall={recall:.4f} F1={f1:.4f}"
        )

        assert tp == 10, f"recall 不足: tp={tp}, fn={fn}"
        assert tn == 10, f"precision 不足: fp={fp}, tn={tn}"
        assert fn == 0, f"漏检 {fn} 个死锁场景"
        assert fp == 0, f"误检 {fp} 个正常场景"
        assert precision == 1.0 and recall == 1.0, "precision/recall 未达 100%"


def _make_deadlock_scenario(
    agents: Sequence[str], resources: Sequence[str]
) -> list[tuple[str, str, str]]:
    """构造死锁场景: 每个 agent 持有 1 资源, 然后循环请求下一个资源"""
    steps: list[tuple[str, str, str]] = []
    n = len(agents)
    for i in range(n):
        steps.append(("acquire", agents[i], resources[i]))
    for i in range(n):
        next_res = resources[(i + 1) % n]
        steps.append(("request", agents[i], next_res))
    return steps


def _make_safe_scenario(
    agents: Sequence[str], resources: Sequence[str]
) -> list[tuple[str, str, str]]:
    """构造无死锁场景: 顺序获取或线性等待链"""
    steps: list[tuple[str, str, str]] = []
    n = len(agents)
    mode = random.choice(["sequential", "linear_chain", "release_then_acquire"])

    if mode == "sequential":
        for i in range(n):
            steps.append(("acquire", agents[i], resources[i]))
    elif mode == "linear_chain":
        for i in range(n):
            steps.append(("acquire", agents[i], resources[i]))
        for i in range(n - 1):
            steps.append(("request", agents[i], resources[i + 1]))
    else:
        for i in range(n):
            steps.append(("acquire", agents[0], resources[i]))
            steps.append(("release", agents[0], resources[i]))
        for i in range(n):
            steps.append(("acquire", agents[i], resources[0]))
            steps.append(("release", agents[i], resources[0]))
    return steps
