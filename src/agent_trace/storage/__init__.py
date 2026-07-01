# src/agent_trace/storage/__init__.py
"""可插拔存储后端

SQLite 默认零配置，PG/其他后端可插拔（v0.2 roadmap）。
"""

from agent_trace.storage.base import (
    ObservationRecord,
    ScoreRecord,
    StorageBackend,
    TraceQuery,
    TraceRecord,
)
from agent_trace.storage.sqlite_backend import SQLiteBackend

__all__ = [
    "StorageBackend",
    "SQLiteBackend",
    "TraceRecord",
    "ObservationRecord",
    "ScoreRecord",
    "TraceQuery",
]
