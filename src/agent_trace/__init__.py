# src/agent_trace/__init__.py
"""Agent Trace — 多 Agent 协作病态调试器

SQLite-backed, zero-infrastructure visual debugger for multi-agent coordination
pathologies. Detects deadlocks, circular dependencies, and context bloat.

M1 公开 API: OTel GenAI v1.41 span 发射器
"""

from agent_trace.otel import (
    AgentSpanEmitter,
    CreateAgentData,
    ExecuteToolData,
    InvokeAgentData,
    InvokeWorkflowData,
)

__version__ = "0.1.0"

__all__ = [
    "AgentSpanEmitter",
    "CreateAgentData",
    "InvokeAgentData",
    "InvokeWorkflowData",
    "ExecuteToolData",
    "__version__",
]
