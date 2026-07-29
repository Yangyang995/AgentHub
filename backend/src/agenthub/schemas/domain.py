"""AgentHub 领域 Schema 定义。

API Schema（Pydantic）与 ORM 模型职责分离。
所有输入/输出均通过此层进行校验，路由层不得直接返回 ORM 实例。
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agenthub.models.enums import (
    AgentCapability,
    AgentStatus,
    AgentType,
    ApprovalActionType,
    ApprovalStatus,
    ArtifactType,
    ConversationType,
    DeploymentProvider,
    DeploymentStatus,
    ExecutionStatus,
    MessageRole,
    TaskStatus,
)

# ── 通用工具 Schema ────────────────────────────────────────────────────────


class TimestampSchema(BaseModel):
    """带时间戳的基础 Schema。"""

    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    updated_at: datetime | None = None


class PaginatedResponse(BaseModel):
    """分页响应通用结构。"""

    model_config = ConfigDict(from_attributes=True)

    items: list[Any]
    total: int
    page: int = 1
    page_size: int = 20


# ── Project ────────────────────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    """创建项目请求。"""

    name: str = Field(min_length=1, max_length=255)
    root_path: str = Field(min_length=1, max_length=4096)
    description: str | None = None


class ProjectUpdate(BaseModel):
    """更新项目请求。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class ProjectResponse(BaseModel):
    """项目响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    root_path: str
    description: str | None
    created_at: datetime
    updated_at: datetime


# ── Agent ──────────────────────────────────────────────────────────────────


class AgentCreate(BaseModel):
    """创建 Agent 请求。"""

    name: str = Field(min_length=1, max_length=255)
    agent_type: AgentType
    capabilities: list[AgentCapability] | None = None
    adapter_config_ref: str | None = Field(default=None, max_length=1024)


class AgentUpdate(BaseModel):
    """更新 Agent 请求。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    capabilities: list[AgentCapability] | None = None
    status: AgentStatus | None = None
    adapter_config_ref: str | None = Field(default=None, max_length=1024)


class AgentResponse(BaseModel):
    """Agent 响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    agent_type: AgentType
    capabilities: list[str] | None
    status: AgentStatus
    adapter_config_ref: str | None
    created_at: datetime
    updated_at: datetime


# ── Conversation ───────────────────────────────────────────────────────────


class ConversationCreate(BaseModel):
    """创建会话请求。"""

    title: str | None = Field(default=None, max_length=500)
    conversation_type: ConversationType = ConversationType.DIRECT
    agent_id: uuid.UUID


class ConversationResponse(BaseModel):
    """会话响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: uuid.UUID | None
    agent_name: str | None = None
    agent_type: AgentType | None = None
    title: str | None
    conversation_type: ConversationType
    created_at: datetime
    updated_at: datetime


# ── Message ────────────────────────────────────────────────────────────────


class MessageCreate(BaseModel):
    """创建消息请求。"""

    role: MessageRole
    content: str = Field(min_length=1)
    content_type: str = "text"
    agent_id: uuid.UUID | None = None
    parent_message_id: uuid.UUID | None = None


class UserMessageCreate(BaseModel):
    """单聊消息提交请求；角色和 Agent 由服务端依据会话确定。"""

    content: str = Field(min_length=1)
    content_type: str = Field(default="text", max_length=50)


class MessageSubmissionResponse(BaseModel):
    """原子创建的用户消息和待执行记录。"""

    message: "MessageResponse"
    execution: "AgentExecutionResponse"


class EventEnvelope(BaseModel):
    """持久化和 WebSocket 共用的事件信封。"""

    event_id: uuid.UUID
    conversation_id: uuid.UUID
    execution_id: uuid.UUID
    sequence: int = Field(ge=0)
    type: str
    timestamp: datetime
    payload: dict[str, Any]


class MessageResponse(BaseModel):
    """消息响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    project_id: uuid.UUID
    parent_message_id: uuid.UUID | None
    role: MessageRole
    agent_id: uuid.UUID | None
    content: str
    content_type: str
    sequence: int
    created_at: datetime


# ── AgentExecution ─────────────────────────────────────────────────────────


class AgentExecutionResponse(BaseModel):
    """执行记录响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    message_id: uuid.UUID
    agent_id: uuid.UUID
    conversation_id: uuid.UUID
    status: ExecutionStatus
    sequence: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


# ── Task ───────────────────────────────────────────────────────────────────


class TaskCreate(BaseModel):
    """创建子任务请求。"""

    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    agent_id: uuid.UUID | None = None
    parent_task_id: uuid.UUID | None = None


class TaskResponse(BaseModel):
    """子任务响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    execution_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    parent_task_id: uuid.UUID | None
    title: str
    description: str | None
    status: TaskStatus
    agent_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


# ── TaskDependency ─────────────────────────────────────────────────────────


class TaskDependencyCreate(BaseModel):
    """创建任务依赖请求。"""

    depends_on_task_id: uuid.UUID


class TaskDependencyResponse(BaseModel):
    """任务依赖响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    depends_on_task_id: uuid.UUID


# ── Artifact ───────────────────────────────────────────────────────────────


class ArtifactCreate(BaseModel):
    """创建产出物请求。"""

    artifact_type: ArtifactType
    relative_path: str = Field(min_length=1, max_length=4096)
    content_hash: str = Field(min_length=1, max_length=128)
    size: int = Field(ge=0)
    metadata_json: dict[str, Any] | None = None


class ArtifactResponse(BaseModel):
    """产出物响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    execution_id: uuid.UUID
    artifact_type: ArtifactType
    relative_path: str
    content_hash: str
    size: int
    metadata_json: dict[str, Any] | None
    created_at: datetime


# ── Approval ───────────────────────────────────────────────────────────────


class ApprovalCreate(BaseModel):
    """创建审批请求。"""

    execution_id: uuid.UUID | None = None
    action_type: ApprovalActionType
    summary: str = Field(min_length=1)
    content_hash: str = Field(min_length=1, max_length=128)


class ApprovalDecide(BaseModel):
    """审批决定请求。"""

    decision: ApprovalStatus = Field(description="只能为 approved 或 rejected")
    decided_by: str | None = Field(default=None, max_length=255)


class ApprovalResponse(BaseModel):
    """审批响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    execution_id: uuid.UUID | None
    action_type: ApprovalActionType
    summary: str
    content_hash: str
    status: ApprovalStatus
    decided_at: datetime | None
    decided_by: str | None
    created_at: datetime


# ── Deployment ─────────────────────────────────────────────────────────────


class DeploymentCreate(BaseModel):
    """创建部署请求。"""

    approval_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None
    provider: DeploymentProvider = DeploymentProvider.VERCEL
    target_url: str | None = Field(default=None, max_length=2048)


class DeploymentResponse(BaseModel):
    """部署响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    approval_id: uuid.UUID | None
    artifact_id: uuid.UUID | None
    provider: DeploymentProvider
    status: DeploymentStatus
    target_url: str | None
    result_url: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


# ── UsageEvent ─────────────────────────────────────────────────────────────


class UsageEventCreate(BaseModel):
    """创建用量事件请求。"""

    agent_id: uuid.UUID
    execution_id: uuid.UUID
    event_type: str = Field(min_length=1, max_length=50)
    call_count: int = Field(ge=0, default=1)
    token_count: int | None = None
    duration_ms: int | None = None
    success: bool | None = None


class UsageEventResponse(BaseModel):
    """用量事件响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: uuid.UUID
    execution_id: uuid.UUID
    event_type: str
    call_count: int
    token_count: int | None
    duration_ms: int | None
    success: bool | None
    created_at: datetime
