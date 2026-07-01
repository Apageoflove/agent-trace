# tests/test_otel.py
"""M1 门禁测试: OTel GenAI v1.41 span 发射器字段覆盖率 100%

测试策略:
  - 用 InMemorySpanExporter 捕获实际生成的 span
  - 逐 span 类型验证: span name, span kind, 必填属性存在, 可选属性按条件设置
  - 异常路径: error.type 正确设置, span 状态为 ERROR
  - 数据类 frozen=True 不可变性验证

门禁: 所有断言通过 = span 字段覆盖率 100% = M1 PASS
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agent_trace import (
    AgentSpanEmitter,
    CreateAgentData,
    ExecuteToolData,
    InvokeAgentData,
    InvokeWorkflowData,
)
from agent_trace.otel.attributes import (
    CREATE_AGENT_REQUIRED,
    EXECUTE_TOOL_REQUIRED,
    GenAIAttr,
    GenAIOperation,
    GenAIProvider,
    INVOKE_AGENT_CLIENT_REQUIRED,
    INVOKE_AGENT_INTERNAL_REQUIRED,
    INVOKE_WORKFLOW_REQUIRED,
)


# ---------------------------------------------------------------------------
# Fixture: 注入 InMemorySpanExporter 的 emitter
# ---------------------------------------------------------------------------


@pytest.fixture
def exporter_and_emitter():
    """返回 (exporter, emitter)，emitter 发射的 span 全部进入 exporter

    注意: OTel 禁止覆盖已设的全局 TracerProvider，所以直接把测试 tracer
    注入 emitter，不依赖全局 provider。
    """
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    emitter = AgentSpanEmitter(tracer=provider.get_tracer("test"))
    yield exporter, emitter
    provider.shutdown()


def _get_span(exporter: InMemorySpanExporter):
    """取 exporter 中第一个（也是唯一一个）span"""
    spans = exporter.get_finished_spans()
    assert len(spans) == 1, f"期望 1 个 span，实际 {len(spans)} 个"
    return spans[0]


# ---------------------------------------------------------------------------
# 1. create_agent (CLIENT)
# ---------------------------------------------------------------------------


class TestCreateAgent:
    def test_required_attributes(self, exporter_and_emitter):
        """create_agent 必填属性全覆盖"""
        exporter, emitter = exporter_and_emitter
        data = CreateAgentData(
            agent_id="agent-001",
            agent_name="researcher",
            provider_name=GenAIProvider.OPENAI,
        )
        with emitter.create_agent(data):
            pass
        span = _get_span(exporter)

        assert span.name == GenAIOperation.CREATE_AGENT
        assert span.kind == trace.SpanKind.CLIENT
        attrs = dict(span.attributes or {})
        for key in CREATE_AGENT_REQUIRED:
            assert key in attrs, f"缺少必填属性 {key}"
        assert attrs[GenAIAttr.OPERATION_NAME] == GenAIOperation.CREATE_AGENT
        assert attrs[GenAIAttr.PROVIDER_NAME] == GenAIProvider.OPENAI
        assert attrs[GenAIAttr.AGENT_ID] == "agent-001"
        assert attrs[GenAIAttr.AGENT_NAME] == "researcher"

    def test_optional_attributes(self, exporter_and_emitter):
        """create_agent 可选属性正确设置"""
        exporter, emitter = exporter_and_emitter
        data = CreateAgentData(
            agent_id="agent-002",
            agent_name="writer",
            provider_name=GenAIProvider.ANTHROPIC,
            agent_description="Tech writer agent",
            agent_version="1.2.0",
            conversation_id="conv-abc",
        )
        with emitter.create_agent(data):
            pass
        span = _get_span(exporter)
        attrs = dict(span.attributes or {})
        assert attrs[GenAIAttr.AGENT_DESCRIPTION] == "Tech writer agent"
        assert attrs[GenAIAttr.AGENT_VERSION] == "1.2.0"
        assert attrs[GenAIAttr.CONVERSATION_ID] == "conv-abc"

    def test_exception_records_error(self, exporter_and_emitter):
        """create_agent 异常时设置 error.type + ERROR 状态"""
        exporter, emitter = exporter_and_emitter
        data = CreateAgentData(
            agent_id="agent-err",
            agent_name="bad",
            provider_name=GenAIProvider.OPENAI,
        )
        with pytest.raises(ValueError, match="boom"):
            with emitter.create_agent(data):
                raise ValueError("boom")
        span = _get_span(exporter)
        attrs = dict(span.attributes or {})
        assert attrs.get(GenAIAttr.ERROR_TYPE) == "ValueError"
        assert span.status.is_ok is False


# ---------------------------------------------------------------------------
# 2. invoke_agent_client (CLIENT)
# ---------------------------------------------------------------------------


class TestInvokeAgentClient:
    def test_required_attributes(self, exporter_and_emitter):
        """invoke_agent_client 必填属性全覆盖"""
        exporter, emitter = exporter_and_emitter
        data = InvokeAgentData(
            provider_name=GenAIProvider.OPENAI,
            request_model="gpt-4.1",
        )
        with emitter.invoke_agent_client(data):
            pass
        span = _get_span(exporter)

        assert span.name == GenAIOperation.INVOKE_AGENT_CLIENT
        assert span.kind == trace.SpanKind.CLIENT
        attrs = dict(span.attributes or {})
        for key in INVOKE_AGENT_CLIENT_REQUIRED:
            assert key in attrs, f"缺少必填属性 {key}"
        assert attrs[GenAIAttr.OPERATION_NAME] == GenAIOperation.INVOKE_AGENT_CLIENT
        assert attrs[GenAIAttr.REQUEST_MODEL] == "gpt-4.1"

    def test_token_attributes(self, exporter_and_emitter):
        """invoke_agent_client token 用量属性正确设置"""
        exporter, emitter = exporter_and_emitter
        data = InvokeAgentData(
            provider_name=GenAIProvider.OPENAI,
            request_model="gpt-4.1",
            input_tokens=1500,
            output_tokens=800,
            reasoning_output_tokens=200,
        )
        with emitter.invoke_agent_client(data):
            pass
        span = _get_span(exporter)
        attrs = dict(span.attributes or {})
        assert attrs[GenAIAttr.USAGE_INPUT_TOKENS] == 1500
        assert attrs[GenAIAttr.USAGE_OUTPUT_TOKENS] == 800
        assert attrs[GenAIAttr.USAGE_REASONING_OUTPUT_TOKENS] == 200

    def test_exception_records_error(self, exporter_and_emitter):
        exporter, emitter = exporter_and_emitter
        data = InvokeAgentData(provider_name=GenAIProvider.OPENAI)
        with pytest.raises(RuntimeError, match="timeout"):
            with emitter.invoke_agent_client(data):
                raise RuntimeError("timeout")
        span = _get_span(exporter)
        attrs = dict(span.attributes or {})
        assert attrs.get(GenAIAttr.ERROR_TYPE) == "RuntimeError"
        assert span.status.is_ok is False


# ---------------------------------------------------------------------------
# 3. invoke_agent_internal (INTERNAL)
# ---------------------------------------------------------------------------


class TestInvokeAgentInternal:
    def test_required_attributes(self, exporter_and_emitter):
        """invoke_agent_internal 必填属性全覆盖 + INTERNAL kind"""
        exporter, emitter = exporter_and_emitter
        data = InvokeAgentData(
            provider_name=GenAIProvider.ANTHROPIC,
            request_model="claude-sonnet-4-5",
            agent_id="agent-int-1",
            agent_name="planner",
            conversation_id="conv-xyz",
        )
        with emitter.invoke_agent_internal(data):
            pass
        span = _get_span(exporter)

        assert span.name == GenAIOperation.INVOKE_AGENT_INTERNAL
        assert span.kind == trace.SpanKind.INTERNAL
        attrs = dict(span.attributes or {})
        for key in INVOKE_AGENT_INTERNAL_REQUIRED:
            assert key in attrs, f"缺少必填属性 {key}"
        assert attrs[GenAIAttr.OPERATION_NAME] == GenAIOperation.INVOKE_AGENT_INTERNAL
        assert attrs[GenAIAttr.AGENT_ID] == "agent-int-1"
        assert attrs[GenAIAttr.AGENT_NAME] == "planner"
        assert attrs[GenAIAttr.CONVERSATION_ID] == "conv-xyz"

    def test_optional_fields_absent_when_none(self, exporter_and_emitter):
        """None 可选字段不应出现在 attributes 中"""
        exporter, emitter = exporter_and_emitter
        data = InvokeAgentData(provider_name=GenAIProvider.OPENAI)
        with emitter.invoke_agent_internal(data):
            pass
        span = _get_span(exporter)
        attrs = dict(span.attributes or {})
        assert GenAIAttr.REQUEST_MODEL not in attrs
        assert GenAIAttr.AGENT_ID not in attrs
        assert GenAIAttr.USAGE_INPUT_TOKENS not in attrs


# ---------------------------------------------------------------------------
# 4. invoke_workflow (INTERNAL)
# ---------------------------------------------------------------------------


class TestInvokeWorkflow:
    def test_required_attributes(self, exporter_and_emitter):
        """invoke_workflow 必填属性全覆盖"""
        exporter, emitter = exporter_and_emitter
        data = InvokeWorkflowData(
            provider_name=GenAIProvider.OPENAI,
            workflow_id="wf-research-001",
            conversation_id="conv-wf",
        )
        with emitter.invoke_workflow(data):
            pass
        span = _get_span(exporter)

        assert span.name == GenAIOperation.INVOKE_WORKFLOW
        assert span.kind == trace.SpanKind.INTERNAL
        attrs = dict(span.attributes or {})
        for key in INVOKE_WORKFLOW_REQUIRED:
            assert key in attrs, f"缺少必填属性 {key}"
        assert attrs[GenAIAttr.OPERATION_NAME] == GenAIOperation.INVOKE_WORKFLOW

    def test_exception_records_error(self, exporter_and_emitter):
        exporter, emitter = exporter_and_emitter
        data = InvokeWorkflowData(
            provider_name=GenAIProvider.OPENAI, workflow_id="wf-bad"
        )
        with pytest.raises(KeyError, match="missing"):
            with emitter.invoke_workflow(data):
                raise KeyError("missing")
        span = _get_span(exporter)
        attrs = dict(span.attributes or {})
        assert attrs.get(GenAIAttr.ERROR_TYPE) == "KeyError"


# ---------------------------------------------------------------------------
# 5. execute_tool (INTERNAL)
# ---------------------------------------------------------------------------


class TestExecuteTool:
    def test_required_attributes(self, exporter_and_emitter):
        """execute_tool 必填属性全覆盖（含 tool.name）"""
        exporter, emitter = exporter_and_emitter
        data = ExecuteToolData(
            provider_name=GenAIProvider.OPENAI,
            tool_name="web_search",
        )
        with emitter.execute_tool(data):
            pass
        span = _get_span(exporter)

        assert span.name == GenAIOperation.EXECUTE_TOOL
        assert span.kind == trace.SpanKind.INTERNAL
        attrs = dict(span.attributes or {})
        for key in EXECUTE_TOOL_REQUIRED:
            assert key in attrs, f"缺少必填属性 {key}"
        assert attrs[GenAIAttr.OPERATION_NAME] == GenAIOperation.EXECUTE_TOOL
        assert attrs[GenAIAttr.TOOL_NAME] == "web_search"

    def test_optional_attributes(self, exporter_and_emitter):
        """execute_tool 可选属性正确设置"""
        exporter, emitter = exporter_and_emitter
        data = ExecuteToolData(
            provider_name=GenAIProvider.ANTHROPIC,
            tool_name="file_read",
            tool_call_id="call-123",
            tool_description="Read a file from disk",
            conversation_id="conv-tool",
        )
        with emitter.execute_tool(data):
            pass
        span = _get_span(exporter)
        attrs = dict(span.attributes or {})
        assert attrs[GenAIAttr.TOOL_CALL_ID] == "call-123"
        assert attrs[GenAIAttr.TOOL_DESCRIPTION] == "Read a file from disk"
        assert attrs[GenAIAttr.CONVERSATION_ID] == "conv-tool"

    def test_exception_records_error(self, exporter_and_emitter):
        exporter, emitter = exporter_and_emitter
        data = ExecuteToolData(
            provider_name=GenAIProvider.OPENAI, tool_name="bad_tool"
        )
        with pytest.raises(PermissionError, match="denied"):
            with emitter.execute_tool(data):
                raise PermissionError("denied")
        span = _get_span(exporter)
        attrs = dict(span.attributes or {})
        assert attrs.get(GenAIAttr.ERROR_TYPE) == "PermissionError"
        assert span.status.is_ok is False


# ---------------------------------------------------------------------------
# 6. 数据类不可变性（frozen=True, 类型安全）
# ---------------------------------------------------------------------------


class TestDataclassImmutability:
    """frozen dataclass 不可变，防止运行时篡改导致 span 字段不一致"""

    def test_create_agent_data_frozen(self):
        data = CreateAgentData(
            agent_id="a", agent_name="b", provider_name="c"
        )
        with pytest.raises(Exception):
            data.agent_id = "mutated"  # type: ignore[misc]

    def test_invoke_agent_data_frozen(self):
        data = InvokeAgentData(provider_name="c")
        with pytest.raises(Exception):
            data.request_model = "mutated"  # type: ignore[misc]

    def test_invoke_workflow_data_frozen(self):
        data = InvokeWorkflowData(provider_name="c", workflow_id="w")
        with pytest.raises(Exception):
            data.workflow_id = "mutated"  # type: ignore[misc]

    def test_execute_tool_data_frozen(self):
        data = ExecuteToolData(provider_name="c", tool_name="t")
        with pytest.raises(Exception):
            data.tool_name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 7. 跨 5 类 span 综合场景：一次完整多 Agent 调用
# ---------------------------------------------------------------------------


class TestEndToEndMultiSpan:
    """模拟一次完整多 Agent 调用，验证 5 类 span 全部正确发射"""

    def test_full_multi_agent_trace(self, exporter_and_emitter):
        exporter, emitter = exporter_and_emitter

        # 1. create_agent
        with emitter.create_agent(
            CreateAgentData(
                agent_id="orchestrator",
                agent_name="lead",
                provider_name=GenAIProvider.OPENAI,
            )
        ):
            # 2. invoke_workflow
            with emitter.invoke_workflow(
                InvokeWorkflowData(
                    provider_name=GenAIProvider.OPENAI,
                    workflow_id="wf-1",
                )
            ):
                # 3. invoke_agent_internal (本地调用子 agent)
                with emitter.invoke_agent_internal(
                    InvokeAgentData(
                        provider_name=GenAIProvider.OPENAI,
                        request_model="gpt-4.1",
                        agent_id="researcher",
                        agent_name="sub",
                    )
                ):
                    # 4. execute_tool
                    with emitter.execute_tool(
                        ExecuteToolData(
                            provider_name=GenAIProvider.OPENAI,
                            tool_name="web_search",
                        )
                    ):
                        pass
                # 5. invoke_agent_client (远程调用)
                with emitter.invoke_agent_client(
                    InvokeAgentData(
                        provider_name=GenAIProvider.OPENAI,
                        request_model="gpt-4.1",
                    )
                ):
                    pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 5, f"期望 5 个 span，实际 {len(spans)}"

        names = [s.name for s in spans]
        assert GenAIOperation.CREATE_AGENT in names
        assert GenAIOperation.INVOKE_WORKFLOW in names
        assert GenAIOperation.INVOKE_AGENT_INTERNAL in names
        assert GenAIOperation.EXECUTE_TOOL in names
        assert GenAIOperation.INVOKE_AGENT_CLIENT in names

        # 每个 span 都有 operation_name
        for s in spans:
            attrs = dict(s.attributes or {})
            assert GenAIAttr.OPERATION_NAME in attrs, (
                f"span {s.name} 缺少 operation_name"
            )
            assert GenAIAttr.PROVIDER_NAME in attrs, (
                f"span {s.name} 缺少 provider_name"
            )
