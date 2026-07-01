# tests/test_storage.py
"""M2 门禁测试: SQLite 存储层 CRUD + 查询 + 10k trace 压测

门禁:
  - CRUD 100% 正确
  - 单查询 <10ms（10k trace 数据集）
  - 边界用例: 空/Unicode/超大 metadata/None 字段
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta

import pytest

from agent_trace.storage import (
    ObservationRecord,
    ScoreRecord,
    SQLiteBackend,
    TraceQuery,
    TraceRecord,
)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    backend = SQLiteBackend(db_path)
    yield backend
    backend.close()


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def make_trace(
    name: str = "test-trace",
    user_id: str | None = None,
    session_id: str | None = None,
) -> TraceRecord:
    now = datetime.now()
    return TraceRecord(
        id=str(uuid.uuid4()),
        name=name,
        created_at=now,
        updated_at=now,
        user_id=user_id,
        session_id=session_id,
    )


def make_observation(
    trace_id: str,
    name: str = "create_agent",
    operation_name: str = "create_agent",
    provider_name: str = "openai",
    agent_id: str | None = "agent-1",
    agent_name: str | None = "researcher",
    parent_observation_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> ObservationRecord:
    return ObservationRecord(
        id=str(uuid.uuid4()),
        trace_id=trace_id,
        parent_observation_id=parent_observation_id,
        name=name,
        type="span",
        start_time=datetime.now(),
        operation_name=operation_name,
        provider_name=provider_name,
        agent_id=agent_id,
        agent_name=agent_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


# ---------------------------------------------------------------------------
# 1. Trace CRUD
# ---------------------------------------------------------------------------


class TestTraceCRUD:
    def test_create_and_get(self, db):
        trace = make_trace(name="research-run")
        db.create_trace(trace)
        got = db.get_trace(trace.id)
        assert got is not None
        assert got.id == trace.id
        assert got.name == "research-run"
        assert got.created_at == trace.created_at

    def test_get_nonexistent_returns_none(self, db):
        assert db.get_trace("does-not-exist") is None

    def test_list_empty(self, db):
        result = db.list_traces(TraceQuery(limit=10))
        assert len(result) == 0

    def test_list_with_pagination(self, db):
        for i in range(5):
            db.create_trace(make_trace(name=f"trace-{i}"))
        page1 = db.list_traces(TraceQuery(limit=2, offset=0))
        page2 = db.list_traces(TraceQuery(limit=2, offset=2))
        assert len(page1) == 2
        assert len(page2) == 2

    def test_list_descending_and_ascending(self, db):
        base = datetime.now()
        for i in range(3):
            trace = TraceRecord(
                id=str(uuid.uuid4()),
                name=f"t{i}",
                created_at=base + timedelta(seconds=i),
                updated_at=base + timedelta(seconds=i),
            )
            db.create_trace(trace)
        desc = db.list_traces(TraceQuery(limit=10, descending=True))
        asc = db.list_traces(TraceQuery(limit=10, descending=False))
        assert desc[0].created_at > desc[-1].created_at
        assert asc[0].created_at < asc[-1].created_at

    def test_count(self, db):
        assert db.count_traces() == 0
        for i in range(3):
            db.create_trace(make_trace(name=f"t{i}"))
        assert db.count_traces() == 3

    def test_invalid_order_by_raises(self, db):
        with pytest.raises(ValueError):
            db.list_traces(TraceQuery(order_by="DROP TABLE"))


# ---------------------------------------------------------------------------
# 2. Observation CRUD
# ---------------------------------------------------------------------------


class TestObservationCRUD:
    def test_create_and_get(self, db):
        trace = make_trace()
        db.create_trace(trace)
        obs = make_observation(trace.id, name="invoke_agent_internal")
        db.create_observation(obs)
        got = db.get_observation(obs.id)
        assert got is not None
        assert got.trace_id == trace.id
        assert got.name == "invoke_agent_internal"
        assert got.operation_name == "create_agent"
        assert got.agent_id == "agent-1"

    def test_get_nonexistent_returns_none(self, db):
        assert db.get_observation("nope") is None

    def test_list_by_trace(self, db):
        trace = make_trace()
        db.create_trace(trace)
        for i in range(3):
            db.create_observation(make_observation(trace.id, name=f"obs-{i}"))
        result = db.list_observations_by_trace(trace.id)
        assert len(result) == 3

    def test_list_by_trace_empty(self, db):
        trace = make_trace()
        db.create_trace(trace)
        result = db.list_observations_by_trace(trace.id)
        assert len(result) == 0

    def test_parent_child_relationship(self, db):
        trace = make_trace()
        db.create_trace(trace)
        parent = make_observation(trace.id, name="parent")
        db.create_observation(parent)
        child = make_observation(
            trace.id, name="child", parent_observation_id=parent.id
        )
        db.create_observation(child)
        got_child = db.get_observation(child.id)
        assert got_child is not None
        assert got_child.parent_observation_id == parent.id

    def test_token_fields(self, db):
        trace = make_trace()
        db.create_trace(trace)
        obs = make_observation(
            trace.id, input_tokens=1500, output_tokens=800
        )
        db.create_observation(obs)
        got = db.get_observation(obs.id)
        assert got is not None
        assert got.input_tokens == 1500
        assert got.output_tokens == 800

    def test_optional_fields_none(self, db):
        trace = make_trace()
        db.create_trace(trace)
        obs = ObservationRecord(
            id=str(uuid.uuid4()),
            trace_id=trace.id,
            name="minimal",
            type="span",
            start_time=datetime.now(),
            operation_name="execute_tool",
            provider_name="openai",
        )
        db.create_observation(obs)
        got = db.get_observation(obs.id)
        assert got is not None
        assert got.agent_id is None
        assert got.model is None
        assert got.input_tokens is None
        assert got.end_time is None
        assert got.error_type is None


# ---------------------------------------------------------------------------
# 3. Score CRUD
# ---------------------------------------------------------------------------


class TestScoreCRUD:
    def test_create_and_list(self, db):
        trace = make_trace()
        db.create_trace(trace)
        score = ScoreRecord(
            id=str(uuid.uuid4()),
            trace_id=trace.id,
            name="quality",
            value=0.85,
            created_at=datetime.now(),
        )
        db.create_score(score)
        result = db.list_scores_by_trace(trace.id)
        assert len(result) == 1
        assert result[0].name == "quality"
        assert result[0].value == 0.85

    def test_list_empty(self, db):
        trace = make_trace()
        db.create_trace(trace)
        result = db.list_scores_by_trace(trace.id)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# 4. 边界用例
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unicode_cjk(self, db):
        """中文/日文/韩文/emoji 不损坏"""
        trace = TraceRecord(
            id=str(uuid.uuid4()),
            name="研究トレース 한국어 🚀",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.create_trace(trace)
        got = db.get_trace(trace.id)
        assert got is not None
        assert "研究" in got.name
        assert "한국어" in got.name
        assert "🚀" in got.name

    def test_large_metadata(self, db):
        """大 metadata 字段不损坏"""
        trace = make_trace()
        db.create_trace(trace)
        large_meta = "x" * 100_000
        obs = ObservationRecord(
            id=str(uuid.uuid4()),
            trace_id=trace.id,
            name="big",
            type="span",
            start_time=datetime.now(),
            operation_name="create_agent",
            provider_name="openai",
            metadata=large_meta,
        )
        db.create_observation(obs)
        got = db.get_observation(obs.id)
        assert got is not None
        assert len(got.metadata) == 100_000

    def test_error_type_field(self, db):
        """错误 span 的 error_type 正确存储"""
        trace = make_trace()
        db.create_trace(trace)
        obs = ObservationRecord(
            id=str(uuid.uuid4()),
            trace_id=trace.id,
            name="failed",
            type="span",
            start_time=datetime.now(),
            operation_name="invoke_agent_client",
            provider_name="openai",
            level="ERROR",
            status_message="connection timeout",
            error_type="TimeoutError",
        )
        db.create_observation(obs)
        got = db.get_observation(obs.id)
        assert got is not None
        assert got.level == "ERROR"
        assert got.error_type == "TimeoutError"

    def test_special_chars_in_id(self, db):
        """ID 含特殊字符不损坏"""
        trace = TraceRecord(
            id="trace-with-dash_and.under",
            name="special",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.create_trace(trace)
        assert db.get_trace("trace-with-dash_and.under") is not None


# ---------------------------------------------------------------------------
# 5. 上下文管理
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_with_statement(self, tmp_path):
        db_path = str(tmp_path / "ctx.db")
        with SQLiteBackend(db_path) as db:
            trace = make_trace(name="ctx")
            db.create_trace(trace)
            assert db.count_traces() == 1


# ---------------------------------------------------------------------------
# 6. 10k trace 压测（门禁: 单查询 <10ms）
# ---------------------------------------------------------------------------


class TestStress10k:
    """插入 10k trace + 30k observation，验证查询性能 <10ms"""

    @pytest.mark.slow
    def test_10k_traces_query_under_10ms(self, db):
        N_TRACES = 10_000
        N_OBS_PER_TRACE = 3

        for i in range(N_TRACES):
            trace = TraceRecord(
                id=f"trace-{i:06d}",
                name=f"stress-{i}",
                created_at=datetime.now() + timedelta(microseconds=i),
                updated_at=datetime.now(),
                user_id=f"user-{i % 100}",
                session_id=f"session-{i % 50}",
            )
            db.create_trace(trace)
            for j in range(N_OBS_PER_TRACE):
                obs = ObservationRecord(
                    id=f"obs-{i:06d}-{j}",
                    trace_id=trace.id,
                    name=f"obs-{j}",
                    type="span",
                    start_time=datetime.now(),
                    operation_name="create_agent",
                    provider_name="openai",
                    agent_id=f"agent-{j}",
                )
                db.create_observation(obs)

        assert db.count_traces() == N_TRACES

        start = time.perf_counter()
        got = db.get_trace("trace-005000")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert got is not None
        assert got.name == "stress-5000"
        assert elapsed_ms < 10.0, f"get_trace 耗时 {elapsed_ms:.2f}ms，超过 10ms 门禁"

        start = time.perf_counter()
        obs_list = db.list_observations_by_trace("trace-005000")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert len(obs_list) == N_OBS_PER_TRACE
        assert elapsed_ms < 10.0, (
            f"list_observations 耗时 {elapsed_ms:.2f}ms，超过 10ms 门禁"
        )

        start = time.perf_counter()
        page = db.list_traces(TraceQuery(limit=100, offset=5000))
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert len(page) == 100
        assert elapsed_ms < 10.0, (
            f"list_traces 耗时 {elapsed_ms:.2f}ms，超过 10ms 门禁"
        )
