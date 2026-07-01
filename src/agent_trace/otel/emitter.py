# src/agent_trace/otel/emitter.py
"""5 类 agent span 的类型安全发射器

设计原则:
  - 类型安全: 用 frozen dataclass 约束输入，禁止 as any / @ts-ignore 式绕过
  - OTel 合规: 严格按 v1.41 规范设置 span kind 和属性
  - 异常安全: contextmanager 保证 span 总是被 end，异常时自动记录 error.type
  - 可测试: emitter 接受可选 tracer，便于注入 InMemorySpanExporter 测试

参考:
  - OTel GenAI spans: https://github.com/open-telemetry/semantic-conventions-genai
  - v1.41 引入 invoke_agent_client/internal 拆分
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer

from agent_trace.otel.attributes import GenAIAttr, GenAIOperation


# ---------------------------------------------------------------------------
# 输入数据类（frozen=True 保证不可变，类型安全）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateAgentData:
    """create_agent span 输入数据（CLIENT span）

    必填: agent_id, agent_name, provider_name
    可选: agent_description, agent_version, conversation_id
    """

    agent_id: str
    agent_name: str
    provider_name: str
    agent_description: str | None = None
    agent_version: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True)
class InvokeAgentData:
    """invoke_agent_client / invoke_agent_internal span 输入数据

    必填: provider_name
    条件必填: request_model（若已知）, usage tokens（完成后回填）
    可选: agent_id, agent_name, conversation_id
    """

    provider_name: str
    request_model: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    conversation_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None


@dataclass(frozen=True)
class InvokeWorkflowData:
    """invoke_workflow span 输入数据（INTERNAL span）

    必填: provider_name, workflow_id
    可选: conversation_id
    """

    provider_name: str
    workflow_id: str
    conversation_id: str | None = None


@dataclass(frozen=True)
class ExecuteToolData:
    """execute_tool span 输入数据（INTERNAL span）

    必填: provider_name, tool_name
    可选: tool_call_id, tool_description, conversation_id
    """

    provider_name: str
    tool_name: str
    tool_call_id: str | None = None
    tool_description: str | None = None
    conversation_id: str | None = None


# ---------------------------------------------------------------------------
# Span 发射器
# ---------------------------------------------------------------------------


class AgentSpanEmitter:
    """5 类 agent span 发射器

    用法:
        emitter = AgentSpanEmitter()
        with emitter.create_agent(CreateAgentData(...)) as span:
            # 业务逻辑
            ...
        # span 自动 end

    测试注入:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import InMemorySpanExporter
        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        emitter = AgentSpanEmitter(tracer=provider.get_tracer("test"))
    """

    def __init__(self, tracer: Tracer | None = None) -> None:
        self._tracer: Tracer = tracer if tracer is not None else trace.get_tracer(
            "agent_trace", "0.1.0"
        )

    # --- create_agent (CLIENT) ---

    @contextmanager
    def create_agent(self, data: CreateAgentData) -> Iterator[Span]:
        """发射 create_agent span（CLIENT kind）

        必填属性: gen_ai.operation.name, gen_ai.provider.name,
                  gen_ai.agent.id, gen_ai.agent.name
        """
        attributes: dict[str, Any] = {
            GenAIAttr.OPERATION_NAME: GenAIOperation.CREATE_AGENT,
            GenAIAttr.PROVIDER_NAME: data.provider_name,
            GenAIAttr.AGENT_ID: data.agent_id,
            GenAIAttr.AGENT_NAME: data.agent_name,
        }
        if data.agent_description is not None:
            attributes[GenAIAttr.AGENT_DESCRIPTION] = data.agent_description
        if data.agent_version is not None:
            attributes[GenAIAttr.AGENT_VERSION] = data.agent_version
        if data.conversation_id is not None:
            attributes[GenAIAttr.CONVERSATION_ID] = data.conversation_id

        with self._tracer.start_as_current_span(
            name=GenAIOperation.CREATE_AGENT,
            kind=SpanKind.CLIENT,
            attributes=attributes,
        ) as span:
            try:
                yield span
            except Exception as exc:
                self._record_error(span, exc)
                raise

    # --- invoke_agent_client (CLIENT) ---

    @contextmanager
    def invoke_agent_client(self, data: InvokeAgentData) -> Iterator[Span]:
        """发射 invoke_agent_client span（CLIENT kind）

        用于远程调用 agent 服务（如 API 调用）。
        """
        attributes: dict[str, Any] = self._build_invoke_agent_attrs(data)
        attributes[GenAIAttr.OPERATION_NAME] = GenAIOperation.INVOKE_AGENT_CLIENT
        with self._tracer.start_as_current_span(
            name=GenAIOperation.INVOKE_AGENT_CLIENT,
            kind=SpanKind.CLIENT,
            attributes=attributes,
        ) as span:
            try:
                yield span
            except Exception as exc:
                self._record_error(span, exc)
                raise

    # --- invoke_agent_internal (INTERNAL) ---

    @contextmanager
    def invoke_agent_internal(self, data: InvokeAgentData) -> Iterator[Span]:
        """发射 invoke_agent_internal span（INTERNAL kind）

        用于本地框架内执行（如 LangGraph in-process 调用）。
        """
        attributes: dict[str, Any] = self._build_invoke_agent_attrs(data)
        attributes[GenAIAttr.OPERATION_NAME] = GenAIOperation.INVOKE_AGENT_INTERNAL
        with self._tracer.start_as_current_span(
            name=GenAIOperation.INVOKE_AGENT_INTERNAL,
            kind=SpanKind.INTERNAL,
            attributes=attributes,
        ) as span:
            try:
                yield span
            except Exception as exc:
                self._record_error(span, exc)
                raise

    # --- invoke_workflow (INTERNAL) ---

    @contextmanager
    def invoke_workflow(self, data: InvokeWorkflowData) -> Iterator[Span]:
        """发射 invoke_workflow span（INTERNAL kind）

        用于触发多步工作流。
        """
        attributes: dict[str, Any] = {
            GenAIAttr.OPERATION_NAME: GenAIOperation.INVOKE_WORKFLOW,
            GenAIAttr.PROVIDER_NAME: data.provider_name,
        }
        if data.conversation_id is not None:
            attributes[GenAIAttr.CONVERSATION_ID] = data.conversation_id

        with self._tracer.start_as_current_span(
            name=GenAIOperation.INVOKE_WORKFLOW,
            kind=SpanKind.INTERNAL,
            attributes=attributes,
        ) as span:
            try:
                yield span
            except Exception as exc:
                self._record_error(span, exc)
                raise

    # --- execute_tool (INTERNAL) ---

    @contextmanager
    def execute_tool(self, data: ExecuteToolData) -> Iterator[Span]:
        """发射 execute_tool span（INTERNAL kind）

        必填属性: gen_ai.operation.name, gen_ai.provider.name, gen_ai.tool.name
        """
        attributes: dict[str, Any] = {
            GenAIAttr.OPERATION_NAME: GenAIOperation.EXECUTE_TOOL,
            GenAIAttr.PROVIDER_NAME: data.provider_name,
            GenAIAttr.TOOL_NAME: data.tool_name,
        }
        if data.tool_call_id is not None:
            attributes[GenAIAttr.TOOL_CALL_ID] = data.tool_call_id
        if data.tool_description is not None:
            attributes[GenAIAttr.TOOL_DESCRIPTION] = data.tool_description
        if data.conversation_id is not None:
            attributes[GenAIAttr.CONVERSATION_ID] = data.conversation_id

        with self._tracer.start_as_current_span(
            name=GenAIOperation.EXECUTE_TOOL,
            kind=SpanKind.INTERNAL,
            attributes=attributes,
        ) as span:
            try:
                yield span
            except Exception as exc:
                self._record_error(span, exc)
                raise

    # --- 私有辅助 ---

    @staticmethod
    def _build_invoke_agent_attrs(data: InvokeAgentData) -> dict[str, Any]:
        """构建 invoke_agent_* span 的属性字典"""
        attributes: dict[str, Any] = {
            GenAIAttr.PROVIDER_NAME: data.provider_name,
        }
        if data.request_model is not None:
            attributes[GenAIAttr.REQUEST_MODEL] = data.request_model
        if data.agent_id is not None:
            attributes[GenAIAttr.AGENT_ID] = data.agent_id
        if data.agent_name is not None:
            attributes[GenAIAttr.AGENT_NAME] = data.agent_name
        if data.conversation_id is not None:
            attributes[GenAIAttr.CONVERSATION_ID] = data.conversation_id
        if data.input_tokens is not None:
            attributes[GenAIAttr.USAGE_INPUT_TOKENS] = data.input_tokens
        if data.output_tokens is not None:
            attributes[GenAIAttr.USAGE_OUTPUT_TOKENS] = data.output_tokens
        if data.reasoning_output_tokens is not None:
            attributes[GenAIAttr.USAGE_REASONING_OUTPUT_TOKENS] = (
                data.reasoning_output_tokens
            )
        return attributes

    @staticmethod
    def _record_error(span: Span, exc: Exception) -> None:
        """异常时记录 error.type + 设置 ERROR 状态"""
        span.set_status(Status(StatusCode.ERROR, str(exc)))
        span.set_attribute(GenAIAttr.ERROR_TYPE, type(exc).__name__)
        span.record_exception(exc)
