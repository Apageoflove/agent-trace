# src/agent_trace/storage/sqlite_backend.py
"""SQLite 存储后端实现

特性:
  - WAL 模式：支持并发读，性能优于默认 rollback journal
  - 参数化查询：防 SQL 注入
  - 自动建表：首次连接时自动创建 schema + 索引
  - 上下文管理：with SQLiteBackend(path) as db: ...

Schema（Langfuse 简化版三表）:
  traces        - 顶层 trace（一次完整 agent 运行）
  observations  - 观测点（对应 OTel span）
  scores        - 评分
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Sequence, TypeVar

from agent_trace.storage.base import (
    ObservationRecord,
    ScoreRecord,
    StorageBackend,
    TraceQuery,
    TraceRecord,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS traces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    user_id     TEXT,
    session_id  TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id                    TEXT PRIMARY KEY,
    trace_id              TEXT NOT NULL,
    parent_observation_id TEXT,
    name                  TEXT NOT NULL,
    type                  TEXT NOT NULL,
    start_time            TEXT NOT NULL,
    end_time              TEXT,
    operation_name        TEXT NOT NULL,
    provider_name         TEXT NOT NULL,
    agent_id              TEXT,
    agent_name            TEXT,
    model                 TEXT,
    input_tokens          INTEGER,
    output_tokens         INTEGER,
    input                 TEXT,
    output                TEXT,
    metadata              TEXT,
    level                 TEXT NOT NULL DEFAULT 'INFO',
    status_message        TEXT,
    error_type            TEXT,
    FOREIGN KEY (trace_id) REFERENCES traces(id),
    FOREIGN KEY (parent_observation_id) REFERENCES observations(id)
);

CREATE TABLE IF NOT EXISTS scores (
    id          TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    value       REAL NOT NULL,
    comment     TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (trace_id) REFERENCES traces(id)
);

CREATE INDEX IF NOT EXISTS idx_observations_trace_id ON observations(trace_id);
CREATE INDEX IF NOT EXISTS idx_observations_parent ON observations(parent_observation_id);
CREATE INDEX IF NOT EXISTS idx_observations_agent_id ON observations(agent_id);
CREATE INDEX IF NOT EXISTS idx_observations_operation ON observations(operation_name);
CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);
CREATE INDEX IF NOT EXISTS idx_traces_user ON traces(user_id);
CREATE INDEX IF NOT EXISTS idx_traces_created ON traces(created_at);
CREATE INDEX IF NOT EXISTS idx_scores_trace_id ON scores(trace_id);
"""

_VALID_ORDER_BY = {
    "created_at",
    "updated_at",
    "name",
    "id",
}


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


_T = TypeVar("_T")


def _locked(fn: Callable[..., _T]) -> Callable[..., _T]:
    @wraps(fn)
    def wrapper(self: "SQLiteBackend", *args: Any, **kwargs: Any) -> _T:
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


class SQLiteBackend(StorageBackend):
    """SQLite 存储后端（默认实现）

    用法:
        db = SQLiteBackend("path/to/trace.db")
        db.create_trace(record)
        traces = db.list_traces(TraceQuery(limit=10))
        db.close()

    或上下文管理:
        with SQLiteBackend("trace.db") as db:
            ...
    """

    def __init__(self, db_path: str) -> None:
        # self._conn = sqlite3.connect(db_path)
        # self._conn.row_factory = sqlite3.Row
        # self._conn.execute("PRAGMA journal_mode=WAL")
        # self._conn.execute("PRAGMA synchronous=NORMAL")
        # self._conn.execute("PRAGMA foreign_keys=ON")
        # self._conn.executescript(_SCHEMA_SQL)
        # self._conn.commit()
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def __enter__(self) -> "SQLiteBackend":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- Trace CRUD ---

    @_locked
    def create_trace(self, record: TraceRecord) -> None:
        self._conn.execute(
            "INSERT INTO traces (id, name, created_at, updated_at, user_id, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.name,
                _iso(record.created_at),
                _iso(record.updated_at),
                record.user_id,
                record.session_id,
            ),
        )
        self._conn.commit()

    @_locked
    def get_trace(self, trace_id: str) -> TraceRecord | None:
        row = self._conn.execute(
            "SELECT * FROM traces WHERE id = ?", (trace_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_trace(row)

    @_locked
    def list_traces(self, query: TraceQuery) -> Sequence[TraceRecord]:
        if query.order_by not in _VALID_ORDER_BY:
            raise ValueError(
                f"非法 order_by: {query.order_by}，允许: {_VALID_ORDER_BY}"
            )
        direction = "DESC" if query.descending else "ASC"
        sql = f"SELECT * FROM traces ORDER BY {query.order_by} {direction} LIMIT ? OFFSET ?"
        rows = self._conn.execute(sql, (query.limit, query.offset)).fetchall()
        return [_row_to_trace(r) for r in rows]

    @_locked
    def count_traces(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM traces").fetchone()
        return int(row["c"])

    # --- Observation CRUD ---

    @_locked
    def create_observation(self, record: ObservationRecord) -> None:
        self._conn.execute(
            """INSERT INTO observations (
                id, trace_id, parent_observation_id, name, type,
                start_time, end_time, operation_name, provider_name,
                agent_id, agent_name, model,
                input_tokens, output_tokens,
                input, output, metadata,
                level, status_message, error_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.trace_id,
                record.parent_observation_id,
                record.name,
                record.type,
                _iso(record.start_time),
                _iso(record.end_time) if record.end_time else None,
                record.operation_name,
                record.provider_name,
                record.agent_id,
                record.agent_name,
                record.model,
                record.input_tokens,
                record.output_tokens,
                record.input,
                record.output,
                record.metadata,
                record.level,
                record.status_message,
                record.error_type,
            ),
        )
        self._conn.commit()

    @_locked
    def get_observation(self, observation_id: str) -> ObservationRecord | None:
        row = self._conn.execute(
            "SELECT * FROM observations WHERE id = ?", (observation_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_observation(row)

    @_locked
    def list_observations_by_trace(
        self, trace_id: str
    ) -> Sequence[ObservationRecord]:
        rows = self._conn.execute(
            "SELECT * FROM observations WHERE trace_id = ? ORDER BY start_time ASC",
            (trace_id,),
        ).fetchall()
        return [_row_to_observation(r) for r in rows]

    # --- Score CRUD ---

    @_locked
    def create_score(self, record: ScoreRecord) -> None:
        self._conn.execute(
            "INSERT INTO scores (id, trace_id, name, value, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.trace_id,
                record.name,
                record.value,
                record.comment,
                _iso(record.created_at),
            ),
        )
        self._conn.commit()

    @_locked
    def list_scores_by_trace(self, trace_id: str) -> Sequence[ScoreRecord]:
        rows = self._conn.execute(
            "SELECT * FROM scores WHERE trace_id = ? ORDER BY created_at ASC",
            (trace_id,),
        ).fetchall()
        return [_row_to_score(r) for r in rows]


def _row_to_trace(row: sqlite3.Row) -> TraceRecord:
    return TraceRecord(
        id=row["id"],
        name=row["name"],
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
        user_id=row["user_id"],
        session_id=row["session_id"],
    )


def _row_to_observation(row: sqlite3.Row) -> ObservationRecord:
    return ObservationRecord(
        id=row["id"],
        trace_id=row["trace_id"],
        parent_observation_id=row["parent_observation_id"],
        name=row["name"],
        type=row["type"],
        start_time=_parse_iso(row["start_time"]),
        end_time=_parse_iso(row["end_time"]) if row["end_time"] else None,
        operation_name=row["operation_name"],
        provider_name=row["provider_name"],
        agent_id=row["agent_id"],
        agent_name=row["agent_name"],
        model=row["model"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        input=row["input"],
        output=row["output"],
        metadata=row["metadata"],
        level=row["level"],
        status_message=row["status_message"],
        error_type=row["error_type"],
    )


def _row_to_score(row: sqlite3.Row) -> ScoreRecord:
    return ScoreRecord(
        id=row["id"],
        trace_id=row["trace_id"],
        name=row["name"],
        value=row["value"],
        comment=row["comment"],
        created_at=_parse_iso(row["created_at"]),
    )
