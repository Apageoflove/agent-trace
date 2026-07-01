# src/agent_trace/otel/attributes.py
# OpenTelemetry GenAI 语义规范 v1.41 属性名常量
# 来源: https://github.com/open-telemetry/semantic-conventions-genai
# 状态: Development（未稳定，但已是事实标准）


class GenAIAttr:
    """gen_ai.* 语义规范属性名常量（v1.41.1）"""

    # --- 通用必填属性 ---
    OPERATION_NAME = "gen_ai.operation.name"
    PROVIDER_NAME = "gen_ai.provider.name"
    REQUEST_MODEL = "gen_ai.request.model"

    # --- Token 用量 ---
    USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    USAGE_REASONING_OUTPUT_TOKENS = "gen_ai.usage.reasoning.output_tokens"

    # --- 会话 ---
    CONVERSATION_ID = "gen_ai.conversation.id"

    # --- Agent 标识 ---
    AGENT_ID = "gen_ai.agent.id"
    AGENT_NAME = "gen_ai.agent.name"
    AGENT_DESCRIPTION = "gen_ai.agent.description"
    AGENT_VERSION = "gen_ai.agent.version"

    # --- 工具调用 ---
    TOOL_NAME = "gen_ai.tool.name"
    TOOL_CALL_ID = "gen_ai.tool.call.id"
    TOOL_DESCRIPTION = "gen_ai.tool.description"

    # --- 上下文压缩（Context Bloat 检测用） ---
    CONVERSATION_COMPACTED = "gen_ai.conversation.compacted"

    # --- Handoff（死锁/循环检测用，RFC #3460） ---
    HANDOFF_SOURCE_AGENT = "gen_ai.handoff.source_agent"
    HANDOFF_TARGET_AGENT = "gen_ai.handoff.target_agent"
    HANDOFF_REASON = "gen_ai.handoff.reason"
    HANDOFF_TYPE = "gen_ai.handoff.type"
    HANDOFF_TIMESTAMP = "gen_ai.handoff.timestamp"

    # --- 错误（Stable 属性） ---
    ERROR_TYPE = "error.type"


class GenAIOperation:
    """gen_ai.operation.name 的 well-known values（v1.41）"""

    CREATE_AGENT = "create_agent"
    INVOKE_AGENT_CLIENT = "invoke_agent_client"
    INVOKE_AGENT_INTERNAL = "invoke_agent_internal"
    INVOKE_WORKFLOW = "invoke_workflow"
    EXECUTE_TOOL = "execute_tool"

    # 其他 well-known values（非 Agent 专用，但规范定义）
    CHAT = "chat"
    GENERATE_CONTENT = "generate_content"
    TEXT_COMPLETION = "text_completion"


class GenAIProvider:
    """gen_ai.provider.name 的常见值（v1.37 从 gen_ai.system 重命名）"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AWS_BEDROCK = "aws.bedrock"
    GCP_VERTEX_AI = "gcp.vertex_ai"
    GCP_GEN_AI = "gcp.gen_ai"
    COHERE = "cohere"
    AZAI = "az.ai"


class GenAIHandoffType:
    """gen_ai.handoff.type 的值"""

    DELEGATION = "delegation"
    TRANSFER = "transfer"
    ESCALATION = "escalation"
    BROADCAST = "broadcast"


# create_agent span 必填属性集
CREATE_AGENT_REQUIRED = {
    GenAIAttr.OPERATION_NAME,
    GenAIAttr.PROVIDER_NAME,
    GenAIAttr.AGENT_ID,
    GenAIAttr.AGENT_NAME,
}

# invoke_agent_client span 必填属性集
INVOKE_AGENT_CLIENT_REQUIRED = {
    GenAIAttr.OPERATION_NAME,
    GenAIAttr.PROVIDER_NAME,
}

# invoke_agent_internal span 必填属性集
INVOKE_AGENT_INTERNAL_REQUIRED = {
    GenAIAttr.OPERATION_NAME,
    GenAIAttr.PROVIDER_NAME,
}

# invoke_workflow span 必填属性集
INVOKE_WORKFLOW_REQUIRED = {
    GenAIAttr.OPERATION_NAME,
    GenAIAttr.PROVIDER_NAME,
}

# execute_tool span 必填属性集
EXECUTE_TOOL_REQUIRED = {
    GenAIAttr.OPERATION_NAME,
    GenAIAttr.PROVIDER_NAME,
    GenAIAttr.TOOL_NAME,
}
