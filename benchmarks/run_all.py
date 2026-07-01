"""Agent Trace — 全模块准确率 benchmark

运行:
    python benchmarks/run_all.py

输出每个模块的实测指标，验证 100% 门禁。
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_trace.detectors import (
    AnomalyDetector,
    ContextBloatDetector,
    CycleDetector,
    DeadlockDetector,
)


def benchmark_cycle() -> dict:
    random.seed(42)
    tp = fp = fn = tn = 0
    for i in range(25):
        n = random.randint(3, 8)
        nodes = [f"n{i}_{j}" for j in range(n)]
        d = CycleDetector()
        for j in range(n):
            d.add_edge(nodes[j], nodes[(j + 1) % n])
        if d.has_cycle():
            tp += 1
        else:
            fn += 1
    for i in range(25):
        n = random.randint(3, 8)
        nodes = [f"d{i}_{j}" for j in range(n)]
        d = CycleDetector()
        for j in range(n):
            for k in range(j + 1, n):
                if random.random() < 0.4:
                    d.add_edge(nodes[j], nodes[k])
        if d.has_cycle():
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"module": "M3 Cycle (Tarjan SCC)", "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall, "f1": f1}


def benchmark_deadlock() -> dict:
    random.seed(42)
    tp = fp = fn = tn = 0
    for i in range(10):
        n = random.randint(2, 5)
        agents = [f"dl_a{i}_{j}" for j in range(n)]
        resources = [f"dl_r{i}_{j}" for j in range(n)]
        d = DeadlockDetector()
        for j in range(n):
            d.acquire(agents[j], resources[j])
        for j in range(n):
            d.request(agents[j], resources[(j + 1) % n])
        if d.has_deadlock():
            tp += 1
        else:
            fn += 1
    for i in range(10):
        n = random.randint(2, 5)
        agents = [f"ok_a{i}_{j}" for j in range(n)]
        resources = [f"ok_r{i}_{j}" for j in range(n)]
        d = DeadlockDetector()
        for j in range(n):
            d.acquire(agents[j], resources[j])
        if d.has_deadlock():
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"module": "M4 Deadlock (WFG+DFS)", "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall, "f1": f1}


def benchmark_bloat() -> dict:
    d = ContextBloatDetector(context_window=10000, alpha=0.3)
    step_increment = 100
    triggered = set()
    for step in range(100):
        d.track_tokens("agent", step_increment)
        for a in d.get_all_alerts():
            triggered.add(a.level)
    from agent_trace.detectors.context_bloat import BloatLevel
    expected = {BloatLevel.INFO, BloatLevel.WARNING, BloatLevel.ERROR, BloatLevel.CRITICAL}
    recall = len(triggered & expected) / len(expected)
    precision = len(triggered & expected) / len(triggered) if triggered else 1.0
    return {"module": "M5 Bloat (EMA 3-layer)", "triggered": len(triggered), "expected": 4, "precision": precision, "recall": recall, "f1": min(precision, recall)}


def benchmark_anomaly() -> dict:
    random.seed(42)
    tp = fp = fn = tn = 0
    for _ in range(50):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(
            agent_id="anom",
            token_history=[100, random.randint(500, 1000)],
            span_total=10,
            span_errors=random.randint(3, 8),
            handoff_depth=random.randint(6, 12),
            cycle_alerts=1,
            context_tokens=random.randint(8500, 10000),
        )
        if result.is_anomaly:
            tp += 1
        else:
            fn += 1
    for _ in range(50):
        d = AnomalyDetector(context_window=10000)
        result = d.evaluate(
            agent_id="ok",
            token_history=[100, random.randint(105, 125)],
            span_total=10,
            span_errors=random.randint(0, 1),
            handoff_depth=random.randint(1, 3),
            cycle_alerts=0,
            context_tokens=random.randint(100, 5000),
        )
        if result.is_anomaly:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"module": "M7 Anomaly (5-feature)", "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall, "f1": f1}


def benchmark_web() -> dict:
    from agent_trace.storage import SQLiteBackend
    from agent_trace.storage.base import ObservationRecord, TraceRecord, TraceQuery
    from agent_trace.web import create_app
    from fastapi.testclient import TestClient
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    storage = SQLiteBackend(":memory:")
    storage.create_trace(TraceRecord(id="t1", name="bench", created_at=now, updated_at=now))
    storage.create_observation(ObservationRecord(id="o1", trace_id="t1", name="op", type="SPAN", start_time=now, operation_name="invoke_agent", provider_name="openai", agent_id="a", input_tokens=100, output_tokens=200))
    app = create_app(storage=storage)
    client = TestClient(app)
    start = time.perf_counter()
    client.get("/api/traces")
    client.get("/api/traces/t1")
    client.get("/api/traces/t1/graph")
    client.get("/api/traces/t1/flame")
    elapsed_ms = (time.perf_counter() - start) * 1000
    return {"module": "M6 Web (4 endpoints)", "latency_ms": round(elapsed_ms, 1), "gate": "<500ms", "pass": elapsed_ms < 500}


def main() -> None:
    print()
    print("=" * 70)
    print("  Agent Trace — Full Benchmark Suite")
    print("=" * 70)
    print()
    results = [
        benchmark_cycle(),
        benchmark_deadlock(),
        benchmark_bloat(),
        benchmark_anomaly(),
        benchmark_web(),
    ]
    all_pass = True
    for r in results:
        mod = r.pop("module")
        if "f1" in r:
            status = "PASS" if r["f1"] >= 1.0 else "FAIL"
            if r["f1"] < 1.0:
                all_pass = False
            print(f"  {mod}")
            print(f"    {r}  → {status}")
        else:
            status = "PASS" if r.get("pass", False) else "FAIL"
            if not r.get("pass", False):
                all_pass = False
            print(f"  {mod}")
            print(f"    {r}  → {status}")
        print()
    print("=" * 70)
    print(f"  Overall: {'ALL PASS (100%)' if all_pass else 'SOME FAILED'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
