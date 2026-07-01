# src/agent_trace/storage/base.py
"""可插拔存储后端抽象接口

设计目标:
  - SQLite 默认零配置，PG/其他后端可插拔（v0.2 接口位）
  - 接口最小化：只暴露 trace/observation/score 三实体的 CRUD + 查询
  - 类型安全：用 dataclass 约束输入输出，禁止 Any 滥用

参考: Langfuse 数据模型简化（Trace / Observation / Score 三实体）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence


@dataclass(frozen=True)
class TraceRecord:
    """traces 表行记录"""

    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    user_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class ObservationRecord:
    """observations 表行记录（对应一个 OTel span）"""

    id: str
    trace_id: str
    name: str
    type: str  # "span" | "event" | "generation"
    start_time: datetime
    operation_name: str
    provider_name: str
    parent_observation_id: str | None = None
    end_time: datetime | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    input: str | None = None
    output: str | None = None
    metadata: str | None = None
    level: str = "INFO"
    status_message: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class ScoreRecord:
    """scores 表行记录"""

    id: str
    trace_id: str
    name: str
    value: float
    created_at: datetime
    comment: str | None = None


@dataclass(frozen=True)
class TraceQuery:
    """trace 查询条件"""

    limit: int = 100
    offset: int = 0
    user_id: str | None = None
    session_id: str | None = None
    order_by: str = "created_at"
    descending: bool = True


class StorageBackend(ABC):
    """可插拔存储后端抽象基类

    实现者需实现所有 abstractmethod。SQLiteBackend 是默认实现；
    PG/其他后端实现此接口即可替换（v0.2 roadmap）。
    """

    @abstractmethod
    def create_trace(self, record: TraceRecord) -> None:
        """插入一条 trace 记录"""

    @abstractmethod
    def get_trace(self, trace_id: str) -> TraceRecord | None:
        """按 ID 查询 trace，不存在返回 None"""

    @abstractmethod
    def list_traces(self, query: TraceQuery) -> Sequence[TraceRecord]:
        """按条件列出 trace"""

    @abstractmethod
    def create_observation(self, record: ObservationRecord) -> None:
        """插入一条 observation 记录"""

    @abstractmethod
    def get_observation(self, observation_id: str) -> ObservationRecord | None:
        """按 ID 查询 observation"""

    @abstractmethod
    def list_observations_by_trace(self, trace_id: str) -> Sequence[ObservationRecord]:
        """列出某 trace 下的所有 observation"""

    @abstractmethod
    def create_score(self, record: ScoreRecord) -> None:
        """插入一条 score 记录"""

    @abstractmethod
    def list_scores_by_trace(self, trace_id: str) -> Sequence[ScoreRecord]:
        """列出某 trace 下的所有 score"""

    @abstractmethod
    def count_traces(self) -> int:
        """trace 总数（压测/统计用）"""

    @abstractmethod
    def close(self) -> None:
        """关闭连接，释放资源"""
