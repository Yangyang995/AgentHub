"""Agent Adapter 统一协议定义。

本模块定义了所有 Agent Adapter 必须遵守的公共契约：
- AgentTask：适配器输入
- AgentEvent：可判别联合类型，覆盖运行时所有事件类型
- AgentResult：执行结束后的汇总结果
- AdapterHealth：健康检查响应
- 统一错误码：平台无关的稳定错误标识

AgentEvent 使用 Pydantic 可判别联合，通过 event_type 字段路由到具体子类型。
同一执行的 sequence 必须单调递增，客户端按 event_id 去重。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════════════════
# 统一错误码
# ═══════════════════════════════════════════════════════════════════════════


class AdapterErrorCode(StrEnum):
    """平台无关的稳定错误码。

    Adapter 实现必须将具体平台错误映射为这些标准码，
    不得将凭据、隐私路径或完整 stderr 附加到错误消息中。
    """

    # 超时
    TIMEOUT = "ADAPTER_TIMEOUT"
    # 用户或系统主动取消
    CANCELLED = "ADAPTER_CANCELLED"
    # 适配器内部未预期错误
    INTERNAL_ERROR = "ADAPTER_INTERNAL_ERROR"
    # 适配器配置缺失或无效
    CONFIG_ERROR = "ADAPTER_CONFIG_ERROR"
    # 适配器不可用（CLI 不存在、版本不支持等）
    UNAVAILABLE = "ADAPTER_UNAVAILABLE"
    # Agent 返回了不可解析的响应
    INVALID_RESPONSE = "ADAPTER_INVALID_RESPONSE"
    # Agent 执行失败（非零退出码）
    EXECUTION_FAILED = "ADAPTER_EXECUTION_FAILED"
    # 路径校验失败（未注册目录、路径逃逸等）
    PATH_REJECTED = "ADAPTER_PATH_REJECTED"
    # 权限不足
    PERMISSION_DENIED = "ADAPTER_PERMISSION_DENIED"
    # 未知错误（仅作兜底，不应主动使用）
    UNKNOWN = "ADAPTER_UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════
# AgentTask — 适配器输入
# ═══════════════════════════════════════════════════════════════════════════


class AgentTask(BaseModel):
    """一次 Agent 执行的完整输入。

    working_dir 必须经过项目注册校验，Adapter 在执行前应再次验证。
    """

    execution_id: uuid.UUID = Field(description="执行 ID，用于关联事件和持久化记录")
    project_id: uuid.UUID = Field(description="所属项目 ID")
    agent_id: uuid.UUID = Field(description="目标 Agent ID")
    conversation_id: uuid.UUID = Field(description="所属会话 ID")
    message_content: str = Field(description="发送给 Agent 的消息正文")
    working_dir: Path = Field(description="经验证的工作目录——已注册项目根目录或隔离 worktree")
    timeout_seconds: int | None = Field(
        default=None, ge=1, description="执行超时秒数，None 表示无限制"
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="额外上下文信息（会话历史、项目元数据等）",
    )


# ═══════════════════════════════════════════════════════════════════════════
# AgentEvent — 可判别联合类型
# ═══════════════════════════════════════════════════════════════════════════


class BaseAgentEvent(BaseModel):
    """所有 Adapter 事件的基类。

    event_id 用于客户端去重；sequence 在同一 execution_id 内单调递增。
    """

    event_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="事件唯一 ID",
    )
    execution_id: uuid.UUID = Field(description="所属执行 ID")
    sequence: int = Field(ge=0, description="同一执行中的单调递增序号")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="事件发生时间（UTC）",
    )


class ContentDeltaEvent(BaseAgentEvent):
    """内容增量事件——流式输出中的一段文本。"""

    event_type: Literal["content.delta"] = "content.delta"  # pyright: ignore[reportIncompatibleVariableOverride]
    delta: str = Field(description="增量文本片段")
    content_type: str = Field(default="text", description="内容类型：text / markdown / code")


class ExecutionStatusEvent(BaseAgentEvent):
    """执行状态变更事件——running / succeeded / failed / cancelled。"""

    event_type: Literal["execution.status"] = "execution.status"  # pyright: ignore[reportIncompatibleVariableOverride]
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"] = Field(
        description="执行状态"
    )
    message: str | None = Field(default=None, description="状态附加说明（已脱敏）")


class ExecutionErrorEvent(BaseAgentEvent):
    """结构化错误事件——包含稳定错误码和安全消息。"""

    event_type: Literal["execution.error"] = "execution.error"  # pyright: ignore[reportIncompatibleVariableOverride]
    error_code: AdapterErrorCode = Field(description="稳定错误码")
    error_message: str = Field(description="安全脱敏后的错误描述")
    recoverable: bool = Field(default=False, description="是否为可恢复错误")


class ExecutionUsageEvent(BaseAgentEvent):
    """用量事件——Token 消耗、调用次数、耗时。"""

    event_type: Literal["execution.usage"] = "execution.usage"  # pyright: ignore[reportIncompatibleVariableOverride]
    token_count: int | None = Field(
        default=None,
        description="Token 消耗量（不可用时为 None，不用 0 代替）",
    )
    call_count: int = Field(default=1, ge=0, description="API 调用次数")
    duration_ms: int | None = Field(default=None, ge=0, description="当前已耗时（毫秒）")


class ArtifactCreatedEvent(BaseAgentEvent):
    """产出物创建事件——Agent 产生了文件、Diff 或报告。"""

    event_type: Literal["artifact.created"] = "artifact.created"  # pyright: ignore[reportIncompatibleVariableOverride]
    artifact_id: uuid.UUID = Field(description="产出物 ID")
    artifact_type: str = Field(description="产出物类型：file / diff / preview / report")
    relative_path: str = Field(description="相对于工作目录的路径")
    content_hash: str = Field(description="内容哈希（SHA-256）")
    size: int = Field(ge=0, description="文件大小（字节）")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")


# 可判别联合：通过 event_type 字段路由到具体事件类型
AgentEvent = Annotated[
    Union[  # noqa: UP007
        ContentDeltaEvent,
        ExecutionStatusEvent,
        ExecutionErrorEvent,
        ExecutionUsageEvent,
        ArtifactCreatedEvent,
    ],
    Field(discriminator="event_type"),
]

# ═══════════════════════════════════════════════════════════════════════════
# AgentResult — 执行完毕后汇总
# ═══════════════════════════════════════════════════════════════════════════


class ArtifactInfo(BaseModel):
    """产出物摘要——AgentResult 中精简引用，完整数据由 ArtifactCreatedEvent 承载。"""

    artifact_id: uuid.UUID
    artifact_type: str
    relative_path: str
    content_hash: str
    size: int = 0


class AgentResult(BaseModel):
    """一次 Agent 执行结束后的汇总结果。

    在 AsyncIterator[AgentEvent] 耗尽后由 Adapter 组装返回，
    包含最终状态、产出物列表和用量摘要。
    """

    execution_id: uuid.UUID
    status: Literal["succeeded", "failed", "cancelled"]
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    total_tokens: int | None = Field(default=None, description="总 Token 消耗（不可用时为 None）")
    duration_ms: int = Field(ge=0, description="总耗时（毫秒）")
    error_code: AdapterErrorCode | None = Field(default=None, description="失败/取消时的错误码")
    error_message: str | None = Field(default=None, description="失败/取消时的脱敏错误描述")


# ═══════════════════════════════════════════════════════════════════════════
# AdapterHealth — 健康检查
# ═══════════════════════════════════════════════════════════════════════════


class AdapterHealth(BaseModel):
    """Adpater 健康检查响应。

    healthy=True 表示适配器及其依赖的外部工具处于可用状态。
    """

    healthy: bool
    adapter_type: str = Field(description="适配器类型标识：mock / codex_cli")
    version: str | None = Field(default=None, description="适配器或底层工具版本")
    message: str | None = Field(default=None, description="健康状态附加说明，不可用时应给出原因")
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="检查时间（UTC）",
    )
