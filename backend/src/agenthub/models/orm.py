"""AgentHub ORM 模型定义。

所有模型主键使用 UUID，时间字段使用 UTC aware datetime。
外键通过 CASCADE 或 RESTRICT 控制删除策略；
每个模型都通过 project_id 实现项目隔离（Project 自身除外）。
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agenthub.db.session import Base
from agenthub.models.enums import (
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

# ── 通用工具函数 ──────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    """返回当前 UTC 时间（去除微秒以保持与 PostgreSQL 一致）。"""
    return datetime.now(UTC).replace(microsecond=0)


def _new_uuid() -> uuid.UUID:
    """生成新的 UUID v4，所有主键和事件 ID 统一使用此函数。"""
    return uuid.uuid4()


# ── Project ────────────────────────────────────────────────────────────────


class Project(Base):
    """已注册的本地项目，是工作区和安全隔离的边界。"""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, comment="人类可读的项目名称"
    )
    root_path: Mapped[str] = mapped_column(
        String(4096), nullable=False, comment="受信任的项目根目录绝对路径"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="项目简要描述")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # 反向引用
    agents: Mapped[list["Agent"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    executions: Mapped[list["AgentExecution"]] = relationship(back_populates="project")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="project")
    approvals: Mapped[list["Approval"]] = relationship(back_populates="project")
    deployments: Mapped[list["Deployment"]] = relationship(back_populates="project")
    usage_events: Mapped[list["UsageEvent"]] = relationship(back_populates="project")


# ── Agent ──────────────────────────────────────────────────────────────────


class Agent(Base):
    """Agent 注册信息——能力、平台、启停与适配器引用。"""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目，删除项目时级联删除 Agent",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="Agent 显示名称")
    agent_type: Mapped[AgentType] = mapped_column(
        String(50), nullable=False, comment="Agent 平台类型"
    )
    capabilities: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(50)),
        nullable=True,
        comment="Agent 能力列表，如 ['code_generation', 'testing']",
    )
    status: Mapped[AgentStatus] = mapped_column(
        String(20),
        nullable=False,
        default=AgentStatus.ENABLED,
        comment="启用/停用状态",
    )
    adapter_config_ref: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="适配器配置引用（仅保存引用名，不保存明文凭据）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # 关系
    project: Mapped["Project"] = relationship(back_populates="agents")
    messages: Mapped[list["Message"]] = relationship(back_populates="agent")
    executions: Mapped[list["AgentExecution"]] = relationship(back_populates="agent")

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_agents_project_name"),
        Index("ix_agents_status_type", "status", "agent_type"),
    )


# ── Conversation ───────────────────────────────────────────────────────────


class Conversation(Base):
    """项目内会话——支持单聊（direct）和群聊（group）。"""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目，删除项目时级联删除会话",
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        comment="单聊绑定的 Agent；旧数据和后续群聊可为空",
    )
    title: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="会话标题（可为空）"
    )
    conversation_type: Mapped[ConversationType] = mapped_column(
        String(20), nullable=False, default=ConversationType.DIRECT
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # 关系
    project: Mapped["Project"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sequence",
    )
    executions: Mapped[list["AgentExecution"]] = relationship(back_populates="conversation")
    tasks: Mapped[list["Task"]] = relationship(back_populates="conversation")

    __table_args__ = (Index("ix_conversations_project_updated", "project_id", "updated_at"),)


# ── Message ────────────────────────────────────────────────────────────────


class Message(Base):
    """会话中的单条消息——来自用户、Agent 或系统。"""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="冗余项目 ID，用于快速隔离查询",
    )
    parent_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        comment="父消息 ID，用于消息线程",
    )
    role: Mapped[MessageRole] = mapped_column(String(20), nullable=False, comment="消息角色")
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="发送消息的 Agent（仅 role=agent 时有值）",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息正文")
    content_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="text",
        comment="内容类型：text / markdown / html",
    )
    sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="会话内单调递增序号"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # 关系
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    agent: Mapped["Agent | None"] = relationship(back_populates="messages")
    parent: Mapped["Message | None"] = relationship(
        "Message", remote_side="Message.id", back_populates="replies"
    )
    replies: Mapped[list["Message"]] = relationship("Message", back_populates="parent")
    executions: Mapped[list["AgentExecution"]] = relationship(back_populates="message")

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_messages_conversation_sequence"),
        Index("ix_messages_project_created", "project_id", "created_at"),
        # 用于 pg_trgm 全文模糊搜索历史消息
        Index(
            "ix_messages_content_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )


# ── AgentExecution ─────────────────────────────────────────────────────────


class AgentExecution(Base):
    """一次 Adapter 执行——记录状态、序号、取消信息与错误。"""

    __tablename__ = "agent_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="冗余项目 ID，用于快速隔离查询",
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        comment="触发本次执行的消息",
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="被调用的 Agent",
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[ExecutionStatus] = mapped_column(
        String(20), nullable=False, default=ExecutionStatus.PENDING
    )
    sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="执行内事件单调递增序号"
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="稳定错误码")
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="安全脱敏后的错误描述"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # 关系
    project: Mapped["Project"] = relationship(back_populates="executions")
    message: Mapped["Message"] = relationship(back_populates="executions")
    agent: Mapped["Agent"] = relationship(back_populates="executions")
    conversation: Mapped["Conversation"] = relationship(back_populates="executions")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="execution")
    approvals: Mapped[list["Approval"]] = relationship(back_populates="execution")
    tasks: Mapped[list["Task"]] = relationship(back_populates="execution")
    usage_events: Mapped[list["UsageEvent"]] = relationship(back_populates="execution")

    __table_args__ = (
        Index("ix_agent_executions_project_status", "project_id", "status"),
        Index("ix_agent_executions_agent_created", "agent_id", "created_at"),
    )


class ExecutionEvent(Base):
    """已持久化的 WebSocket 事件信封，是断线补发的唯一数据源。"""

    __tablename__ = "execution_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("execution_id", "sequence", name="uq_execution_events_sequence"),
        Index("ix_execution_events_replay", "conversation_id", "execution_id", "sequence"),
    )


# ── Task ───────────────────────────────────────────────────────────────────


class Task(Base):
    """Orchestrator 子任务——每个 Task 可分配到一个 Agent。"""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        comment="父任务 ID，用于构建任务树",
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, comment="任务标题")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        String(20), nullable=False, default=TaskStatus.PENDING
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # 关系
    project: Mapped["Project"] = relationship()
    execution: Mapped["AgentExecution | None"] = relationship(back_populates="tasks")
    conversation: Mapped["Conversation | None"] = relationship(back_populates="tasks")
    parent: Mapped["Task | None"] = relationship(
        "Task", remote_side="Task.id", back_populates="children"
    )
    children: Mapped[list["Task"]] = relationship("Task", back_populates="parent")
    agent: Mapped["Agent | None"] = relationship()
    depends_on: Mapped[list["TaskDependency"]] = relationship(
        "TaskDependency",
        foreign_keys="TaskDependency.task_id",
        back_populates="task",
        cascade="all, delete-orphan",
    )
    depended_by: Mapped[list["TaskDependency"]] = relationship(
        "TaskDependency",
        foreign_keys="TaskDependency.depends_on_task_id",
        back_populates="depends_on",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_tasks_project_status", "project_id", "status"),)


# ── TaskDependency ─────────────────────────────────────────────────────────


class TaskDependency(Base):
    """任务依赖关系——防止自依赖和重复边。"""

    __tablename__ = "task_dependencies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="依赖方任务",
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="被依赖的任务",
    )

    # 关系
    task: Mapped["Task"] = relationship("Task", foreign_keys=[task_id], back_populates="depends_on")
    depends_on: Mapped["Task"] = relationship(
        "Task", foreign_keys=[depends_on_task_id], back_populates="depended_by"
    )

    __table_args__ = (
        # 禁止自依赖
        CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dependencies_no_self"),
        # 同一对依赖关系不可重复
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_pair"),
    )


# ── Artifact ───────────────────────────────────────────────────────────────


class Artifact(Base):
    """Agent 产出物——文件、Diff、预览包、报告或部署配置。"""

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="产生此产出物的执行",
    )
    artifact_type: Mapped[ArtifactType] = mapped_column(
        String(50), nullable=False, comment="产出物类型"
    )
    relative_path: Mapped[str] = mapped_column(
        String(4096), nullable=False, comment="相对于项目根目录的路径"
    )
    content_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="内容哈希（SHA-256）"
    )
    size: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, comment="文件大小（字节）"
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="扩展元数据"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # 关系
    project: Mapped["Project"] = relationship(back_populates="artifacts")
    execution: Mapped["AgentExecution"] = relationship(back_populates="artifacts")
    deployments: Mapped[list["Deployment"]] = relationship(back_populates="artifact")

    __table_args__ = (
        Index("ix_artifacts_project_type", "project_id", "artifact_type"),
        Index("ix_artifacts_execution_type", "execution_id", "artifact_type"),
    )


# ── Approval ───────────────────────────────────────────────────────────────


class Approval(Base):
    """高风险操作的持久化审批记录——可在页面刷新后恢复。"""

    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_type: Mapped[ApprovalActionType] = mapped_column(
        String(50), nullable=False, comment="待审批的动作类型"
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, comment="动作摘要")
    content_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="动作内容哈希，变更后原审批失效"
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        String(20),
        nullable=False,
        default=ApprovalStatus.PENDING,
        comment="审批状态",
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="决定时间"
    )
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="决定者标识")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # 关系
    project: Mapped["Project"] = relationship(back_populates="approvals")
    execution: Mapped["AgentExecution | None"] = relationship(back_populates="approvals")
    deployments: Mapped[list["Deployment"]] = relationship(back_populates="approval")

    __table_args__ = (
        Index("ix_approvals_project_status", "project_id", "status"),
        Index("ix_approvals_execution_status", "execution_id", "status"),
    )


# ── Deployment ─────────────────────────────────────────────────────────────


class Deployment(Base):
    """部署请求——审批、状态、目标与结果 URL。"""

    __tablename__ = "deployments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approvals.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联的审批记录",
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联的部署产出物",
    )
    provider: Mapped[DeploymentProvider] = mapped_column(
        String(50), nullable=False, default=DeploymentProvider.VERCEL
    )
    status: Mapped[DeploymentStatus] = mapped_column(
        String(20), nullable=False, default=DeploymentStatus.PENDING
    )
    target_url: Mapped[str | None] = mapped_column(
        String(2048), nullable=True, comment="部署目标 URL"
    )
    result_url: Mapped[str | None] = mapped_column(
        String(2048), nullable=True, comment="部署结果 URL"
    )
    error_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="部署失败时的稳定错误码"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # 关系
    project: Mapped["Project"] = relationship(back_populates="deployments")
    approval: Mapped["Approval | None"] = relationship(back_populates="deployments")
    artifact: Mapped["Artifact | None"] = relationship(back_populates="deployments")

    __table_args__ = (Index("ix_deployments_project_status", "project_id", "status"),)


# ── UsageEvent ─────────────────────────────────────────────────────────────


class UsageEvent(Base):
    """调用次数、Token、耗时、结果等 Agent 维度的原始统计事件。"""

    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="事件类型：call / token / duration / result"
    )
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="调用次数")
    token_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Token 消耗量（不可用时为 NULL，不用 0 代替）"
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="耗时（毫秒）")
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True, comment="调用是否成功")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # 关系
    project: Mapped["Project"] = relationship(back_populates="usage_events")
    agent: Mapped["Agent"] = relationship()
    execution: Mapped["AgentExecution"] = relationship(back_populates="usage_events")

    __table_args__ = (
        Index("ix_usage_events_project_agent_time", "project_id", "agent_id", "created_at"),
        Index("ix_usage_events_execution_time", "execution_id", "created_at"),
    )
