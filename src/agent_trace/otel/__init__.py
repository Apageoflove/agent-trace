# src/agent_trace/otel/__init__.py
"""OpenTelemetry GenAI v1.41 span 发射器

提供 5 类 agent span 的类型安全发射 API：
  - create_agent (CLIENT)
  - invoke_agent_client (CLIENT)
  - invoke_agent_internal (INTERNAL)
  - invoke_workflow (INTERNAL)
  - execute_tool (INTERNAL)

所有 span 自动设置 gen_ai.operation.name 和 gen_ai.provider.name 必填属性。
异常时自动设置 error.type 并记录异常到 span。
"""

from agent_trace.otel.attributes import (
    CREATE_AGENT_REQUIRED,
    EXECUTE_TOOL_REQUIRED,
    GenAIAttr,
    GenAIHandoffType,
    GenAIOperation,
    GenAIProvider,
    INVOKE_AGENT_CLIENT_REQUIRED,
    INVOKE_AGENT_INTERNAL_REQUIRED,
    INVOKE_WORKFLOW_REQUIRED,
)
from agent_trace.otel.emitter import (
    AgentSpanEmitter,
    CreateAgentData,
    ExecuteToolData,
    InvokeAgentData,
    InvokeWorkflowData,
)

__all__ = [
    "AgentSpanEmitter",
    "CreateAgentData",
    "InvokeAgentData",
    "InvokeWorkflowData",
    "ExecuteToolData",
    "GenAIAttr",
    "GenAIOperation",
    "GenAIProvider",
    "GenAIHandoffType",
    "CREATE_AGENT_REQUIRED",
    "INVOKE_AGENT_CLIENT_REQUIRED",
    "INVOKE_AGENT_INTERNAL_REQUIRED",
    "INVOKE_WORKFLOW_REQUIRED",
    "EXECUTE_TOOL_REQUIRED",
]
