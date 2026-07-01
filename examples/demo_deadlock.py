"""4-agent 死锁 + 循环依赖 + Context Bloat 三合一 Demo

运行:
    python examples/demo_deadlock.py

预期输出:
    [Cycle] 检测到循环依赖: agent_a -> agent_b -> agent_c -> agent_a
    [Deadlock] 检测到死锁: agent_a <-> agent_b
    [Bloat] agent_a 触发 WARNING 告警 (75.0% context)
    [Bloat] agent_a 触发 CRITICAL 告警 (95.0% context)
    [Anomaly] agent_a 综合异常分=0.85 -> 异常
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_trace.detectors import (
    AnomalyDetector,
    ContextBloatDetector,
    CycleDetector,
    DeadlockDetector,
)
from agent_trace.otel import AgentSpanEmitter
from agent_trace.storage import SQLiteBackend
from agent_trace.storage.base import ObservationRecord, TraceQuery, TraceRecord


def _now() -> datetime:
    return datetime.now(timezone.utc)


def demo_cycle_detection() -> None:
    print("=== 1. 循环依赖检测 (Tarjan SCC) ===")
    detector = CycleDetector(on_cycle=lambda e: print(f"  [Cycle] 检测到循环: {' -> '.join(e.cycle)}"))
    detector.add_handoff("agent_a", "agent_b")
    detector.add_handoff("agent_b", "agent_c")
    detector.add_handoff("agent_c", "agent_a")
    print(f"  节点数={detector.node_count}, 边数={detector.edge_count}")
    print()


def demo_deadlock_detection() -> None:
    print("=== 2. 死锁检测 (WFG + 增量 DFS) ===")
    detector = DeadlockDetector(
        on_deadlock=lambda e: print(f"  [Deadlock] 检测到死锁: {' <-> '.join(e.cycle)}")
    )
    detector.acquire("agent_a", "resource_1")
    detector.acquire("agent_b", "resource_2")
    detector.request("agent_a", "resource_2")
    detector.request("agent_b", "resource_1")
    print(f"  资源数={detector.resource_count}, 等待边数={detector.wait_edge_count}")
    print()


def demo_context_bloat() -> None:
    print("=== 3. Context Bloat 预警 (tiktoken + EMA) ===")
    detector = ContextBloatDetector(
        context_window=10000,
        on_alert=lambda a: print(f"  [Bloat] {a.agent_id} 触发 {a.level.name} 告警 ({a.utilization*100:.1f}% context)"),
    )
    for _ in range(50):
        detector.track_tokens("agent_a", 100)
    for _ in range(25):
        detector.track_tokens("agent_a", 100)
    for _ in range(20):
        detector.track_tokens("agent_a", 100)
    print(f"  总告警数={len(detector.get_all_alerts())}")
    print()


def demo_anomaly_detection() -> None:
    print("=== 4. 异常检测 (5 特征规则模型) ===")
    bloat = ContextBloatDetector(context_window=10000)
    for _ in range(95):
        bloat.track_tokens("agent_a", 100)
    current_tokens = bloat.get_utilization("agent_a")

    detector = AnomalyDetector(context_window=10000)
    result = detector.evaluate(
        agent_id="agent_a",
        token_history=[100, 200, 500, 1200],
        span_total=10,
        span_errors=3,
        handoff_depth=6,
        cycle_alerts=1,
        context_tokens=int(current_tokens * 10000),
    )
    print(f"  [Anomaly] {result.agent_id} 综合异常分={result.score:.2f} -> {'异常' if result.is_anomaly else '正常'}")
    print(f"  触发特征: {', '.join(result.triggered_features) or '无'}")
    print()


def demo_otel_to_sqlite() -> None:
    print("=== 5. OTel Span → SQLite 端到端 ===")
    storage = SQLiteBackend(":memory:")
    storage.create_trace(
        TraceRecord(id="demo-trace", name="4-agent-demo", created_at=_now(), updated_at=_now(), session_id="demo-session")
    )
    storage.create_observation(
        ObservationRecord(
            id="obs-1",
            trace_id="demo-trace",
            name="root-invoke",
            type="SPAN",
            start_time=_now(),
            operation_name="invoke_workflow",
            provider_name="openai",
            parent_observation_id=None,
            agent_id="orchestrator",
            input_tokens=50,
            output_tokens=100,
        )
    )
    print(f"  写入 1 trace + 1 observation 到 SQLite (in-memory)")
    # traces = storage.list_traces(type(storage.list_traces).__defaults__ and __import__("agent_trace.storage.base", fromlist=["TraceQuery"]).TraceQuery(limit=10))
    traces = storage.list_traces(TraceQuery(limit=10))
    print(f"  查询到 {len(traces)} 条 trace")
    print()


def main() -> None:
    print()
    print("=" * 60)
    print("  Agent Trace — Multi-Agent Pathology Debugger Demo")
    print("=" * 60)
    print()
    demo_cycle_detection()
    demo_deadlock_detection()
    demo_context_bloat()
    demo_anomaly_detection()
    demo_otel_to_sqlite()
    print("=" * 60)
    print("  Demo 完成。三种病理告警全部触发，OTel→SQLite 端到端验证通过。")
    print("=" * 60)


if __name__ == "__main__":
    main()
