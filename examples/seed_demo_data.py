"""造可视化样本数据 — 模拟一个多 agent 协作 trace

场景: planner 委托 researcher 调研，researcher 委托 writer 写报告，
      writer 又回委托 planner 复核 → 形成循环依赖。
      每个 agent 的 span 带 token 数，用于火焰图渲染。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agent_trace.storage import SQLiteBackend
from agent_trace.storage.base import ObservationRecord, ScoreRecord, TraceRecord

DB_PATH = "./traces.db"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def seed() -> None:
    import os
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    storage = SQLiteBackend(DB_PATH)
    t0 = _now()

    trace = TraceRecord(
        id="trace-001",
        name="research-and-report-with-cycle",
        created_at=t0,
        updated_at=t0,
        user_id="user-42",
        session_id="session-7",
    )
    storage.create_trace(trace)

    observations = [
        ObservationRecord(
            id="obs-1", trace_id="trace-001",
            name="planner.create", type="span",
            start_time=t0, end_time=t0 + timedelta(seconds=2),
            operation_name="create_agent", provider_name="openai",
            parent_observation_id=None,
            agent_id="planner", agent_name="Planner", model="gpt-4o",
            input_tokens=120, output_tokens=80,
            input=json.dumps({"task": "research LLM agent pathologies"}),
            metadata=json.dumps({"tools": ["search"]}),
        ),
        ObservationRecord(
            id="obs-2", trace_id="trace-001",
            name="planner.invoke.researcher", type="span",
            start_time=t0 + timedelta(seconds=2), end_time=t0 + timedelta(seconds=10),
            operation_name="invoke_agent", provider_name="openai",
            parent_observation_id="obs-1",
            agent_id="planner", agent_name="Planner", model="gpt-4o",
            input_tokens=80, output_tokens=40,
            input=json.dumps({"target_agent": "researcher", "query": "find papers on multi-agent deadlocks"}),
        ),
        ObservationRecord(
            id="obs-3", trace_id="trace-001",
            name="researcher.invoke.writer", type="span",
            start_time=t0 + timedelta(seconds=10), end_time=t0 + timedelta(seconds=25),
            operation_name="invoke_agent", provider_name="openai",
            parent_observation_id="obs-2",
            agent_id="researcher", agent_name="Researcher", model="gpt-4o",
            input_tokens=200, output_tokens=350,
            input=json.dumps({"target_agent": "writer", "sources": ["paper-A", "paper-B"]}),
        ),
        ObservationRecord(
            id="obs-4", trace_id="trace-001",
            name="writer.invoke.planner", type="span",
            start_time=t0 + timedelta(seconds=25), end_time=t0 + timedelta(seconds=40),
            operation_name="invoke_agent", provider_name="openai",
            parent_observation_id="obs-3",
            agent_id="writer", agent_name="Writer", model="gpt-4o",
            input_tokens=400, output_tokens=600,
            input=json.dumps({"target_agent": "planner", "reason": "review draft"}),
            level="WARN",
            status_message="circular handoff detected: writer -> planner",
        ),
        ObservationRecord(
            id="obs-5", trace_id="trace-001",
            name="researcher.tool.search", type="span",
            start_time=t0 + timedelta(seconds=11), end_time=t0 + timedelta(seconds=14),
            operation_name="execute_tool", provider_name="openai",
            parent_observation_id="obs-3",
            agent_id="researcher", agent_name="Researcher", model="gpt-4o",
            input_tokens=50, output_tokens=120,
            input=json.dumps({"tool": "web_search", "q": "WFG deadlock detection"}),
        ),
    ]
    for obs in observations:
        storage.create_observation(obs)

    scores = [
        ScoreRecord(id="score-1", trace_id="trace-001", name="cycle_risk",
                    value=1.0, created_at=t0, comment="circular handoff: planner->researcher->writer->planner"),
        ScoreRecord(id="score-2", trace_id="trace-001", name="token_efficiency",
                    value=0.62, created_at=t0, comment="output/input ratio degraded at depth 3"),
    ]
    for s in scores:
        storage.create_score(s)

    storage.close()
    print(f"已写入 {DB_PATH}: 1 trace, {len(observations)} observations, {len(scores)} scores")
    print("启动 Web UI:  .venv/bin/agent-trace serve --db ./traces.db --port 7600")
    print("浏览器访问:    http://localhost:7600")


if __name__ == "__main__":
    seed()
