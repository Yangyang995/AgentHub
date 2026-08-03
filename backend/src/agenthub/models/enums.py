"""AgentHub 业务枚举定义。

所有枚举值使用 VARCHAR 存储（配合 CHECK 约束），而非 PostgreSQL 原生 ENUM 类型，
以便后续通过 ALTER TABLE 安全地新增枚举值而不触发类型重建。
"""

import enum


class AgentType(enum.StrEnum):
    """Agent 平台类型——决定调用哪个 Adapter。

    CODEX_CLI 和 CLAUDE_CODE 已废弃，保留以兼容已有数据库记录。
    新 Agent 注册应仅使用 MOCK 或 OPENAI_COMPATIBLE。
    """

    MOCK = "mock"
    OPENAI_COMPATIBLE = "openai_compatible"
    # 已废弃，不再接受新注册
    CODEX_CLI = "codex_cli"
    CLAUDE_CODE = "claude_code"


class AgentCapability(enum.StrEnum):
    """Agent 能力声明，用于 Orchestrator 能力匹配。

    与 4 个预置子 Agent 一一对应：
    - architecture_design：架构设计专家
    - code_generation：代码生成专家
    - code_review：代码审查专家
    - testing：测试专家
    """

    # 已废弃——需求分析专家已移除，保留以兼容已有数据库记录
    REQUIREMENT_ANALYSIS = "requirement_analysis"  # deprecated
    ARCHITECTURE_DESIGN = "architecture_design"
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    TESTING = "testing"
    # 已废弃——技术报告撰写专家已移除，保留以兼容已有数据库记录
    DOCUMENTATION = "documentation"  # deprecated
    # 以下为历史兼容，仍可用但不作为预置 Agent 的主能力
    FILE_OPERATION = "file_operation"
    DEPLOYMENT = "deployment"


class AgentStatus(enum.StrEnum):
    """Agent 启用/停用状态。"""

    ENABLED = "enabled"
    DISABLED = "disabled"


class ConversationType(enum.StrEnum):
    """会话类型：一对一私聊或群聊。"""

    DIRECT = "direct"
    GROUP = "group"


class ConversationStatus(enum.StrEnum):
    """会话最近一次消息批次的聚合状态。"""

    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MessageRole(enum.StrEnum):
    """消息发送者角色。"""

    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class ExecutionStatus(enum.StrEnum):
    """Agent 执行生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(enum.StrEnum):
    """Orchestrator 子任务状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ArtifactType(enum.StrEnum):
    """Agent 产出物类型，决定前端展示和后续动作。"""

    FILE = "file"
    DIFF = "diff"
    PREVIEW = "preview"
    REPORT = "report"
    DEPLOYMENT_CONFIG = "deployment_config"


class ApprovalActionType(enum.StrEnum):
    """需要审批的高风险动作类型。"""

    WRITE_FILE = "write_file"
    APPLY_DIFF = "apply_diff"
    RUN_COMMAND = "run_command"
    START_PREVIEW = "start_preview"
    DEPLOY = "deploy"


class ApprovalStatus(enum.StrEnum):
    """审批记录的当前状态。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DeploymentProvider(enum.StrEnum):
    """部署目标平台。首版仅支持 Vercel。"""

    VERCEL = "vercel"


class DeploymentStatus(enum.StrEnum):
    """部署生命周期状态。"""

    PENDING = "pending"
    PREPARING = "preparing"
    UPLOADING = "uploading"
    BUILDING = "building"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
