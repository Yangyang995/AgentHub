"""Phase 4 单聊业务服务：事务、Adapter 执行、事件持久化与取消。"""

import asyncio
import inspect
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenthub.adapters.protocol import (
    AdapterErrorCode,
    AgentEvent,
    AgentTask,
    ContentDeltaEvent,
    ExecutionErrorEvent,
    ExecutionStatusEvent,
)
from agenthub.models.enums import (
    AgentStatus,
    ConversationType,
    ExecutionStatus,
    MessageRole,
)
from agenthub.models.orm import (
    Agent,
    AgentExecution,
    Conversation,
    ExecutionEvent,
    Message,
    Project,
)
from agenthub.schemas.domain import (
    AgentExecutionResponse,
    ConversationCreate,
    ConversationResponse,
    EventEnvelope,
    MessageResponse,
    MessageSubmissionResponse,
    UserMessageCreate,
)
from agenthub.services.realtime import ConversationEventBroker


class ChatNotFoundError(LookupError):
    """请求资源不存在，或不属于路径中声明的项目。"""


class ChatConflictError(ValueError):
    """资源存在但不满足单聊状态约束。"""


class AgentAdapter(Protocol):
    """聊天服务实际使用的最小 Adapter 契约。"""

    def run(self, task: AgentTask) -> AsyncIterator[AgentEvent]: ...

    def cancel(self, execution_id: uuid.UUID) -> object: ...


AdapterResolver = Callable[[Agent], AgentAdapter]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ChatService:
    """协调单聊持久化与执行；每个公开写方法明确提交事务。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        broker: ConversationEventBroker,
        adapter_resolver: AdapterResolver,
    ) -> None:
        self._sessions = session_factory
        self._broker = broker
        self._resolve_adapter = adapter_resolver
        self._execution_locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._running_adapters: dict[uuid.UUID, AgentAdapter] = {}

    async def create_conversation(
        self, project_id: uuid.UUID, data: ConversationCreate
    ) -> ConversationResponse:
        """创建绑定同项目已启用 Agent 的 direct 会话。"""
        if data.conversation_type != ConversationType.DIRECT:
            raise ChatConflictError("Phase 4 仅支持单聊会话")
        async with self._sessions() as session, session.begin():
            project = await session.get(Project, project_id)
            agent = await session.get(Agent, data.agent_id)
            if project is None or agent is None or agent.project_id != project_id:
                raise ChatNotFoundError
            if agent.status != AgentStatus.ENABLED:
                raise ChatConflictError("Agent 未启用")
            conversation = Conversation(
                project_id=project_id,
                agent_id=agent.id,
                title=data.title,
                conversation_type=ConversationType.DIRECT,
            )
            session.add(conversation)
            await session.flush()
            return ConversationResponse.model_validate(conversation)

    async def list_conversations(self, project_id: uuid.UUID) -> list[ConversationResponse]:
        """只返回指定项目中的单聊会话。"""
        async with self._sessions() as session:
            if await session.get(Project, project_id) is None:
                raise ChatNotFoundError
            result = await session.scalars(
                select(Conversation)
                .where(Conversation.project_id == project_id)
                .order_by(Conversation.updated_at.desc())
            )
            return [ConversationResponse.model_validate(item) for item in result]

    async def get_conversation(
        self, project_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> ConversationResponse:
        """按项目边界读取会话，不泄露跨项目资源是否存在。"""
        async with self._sessions() as session:
            conversation = await self._get_conversation(session, project_id, conversation_id)
            return ConversationResponse.model_validate(conversation)

    async def list_messages(
        self, project_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> list[MessageResponse]:
        """按稳定 sequence 返回完整消息历史。"""
        async with self._sessions() as session:
            await self._get_conversation(session, project_id, conversation_id)
            result = await session.scalars(
                select(Message)
                .where(
                    Message.project_id == project_id,
                    Message.conversation_id == conversation_id,
                )
                .order_by(Message.sequence)
            )
            return [MessageResponse.model_validate(item) for item in result]

    async def submit_message(
        self, project_id: uuid.UUID, conversation_id: uuid.UUID, data: UserMessageCreate
    ) -> MessageSubmissionResponse:
        """在一个事务中保存用户消息和待执行记录。"""
        async with self._sessions() as session, session.begin():
            conversation = await self._get_conversation(
                session, project_id, conversation_id, for_update=True
            )
            if conversation.agent_id is None:
                raise ChatConflictError("单聊会话未绑定 Agent")
            agent = await session.get(Agent, conversation.agent_id)
            if agent is None or agent.project_id != project_id:
                raise ChatNotFoundError
            if agent.status != AgentStatus.ENABLED:
                raise ChatConflictError("Agent 未启用")
            sequence = await self._next_message_sequence(session, conversation_id)
            message = Message(
                project_id=project_id,
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=data.content,
                content_type=data.content_type,
                sequence=sequence,
            )
            session.add(message)
            await session.flush()
            execution = AgentExecution(
                project_id=project_id,
                conversation_id=conversation_id,
                message_id=message.id,
                agent_id=agent.id,
                status=ExecutionStatus.PENDING,
                sequence=-1,
            )
            session.add(execution)
            await session.flush()
            response = MessageSubmissionResponse(
                message=MessageResponse.model_validate(message),
                execution=AgentExecutionResponse.model_validate(execution),
            )
        return response

    async def run_execution(self, execution_id: uuid.UUID) -> None:
        """异步消费 Adapter；每个事件提交成功后才允许进入实时队列。"""
        async with self._sessions() as session:
            row = await session.execute(
                select(AgentExecution, Agent, Message, Project)
                .join(Agent, Agent.id == AgentExecution.agent_id)
                .join(Message, Message.id == AgentExecution.message_id)
                .join(Project, Project.id == AgentExecution.project_id)
                .where(AgentExecution.id == execution_id)
            )
            record = row.one_or_none()
        if record is None:
            return
        execution, agent, message, project = record
        adapter = self._resolve_adapter(agent)
        self._running_adapters[execution_id] = adapter
        task = AgentTask(
            execution_id=execution.id,
            project_id=execution.project_id,
            agent_id=execution.agent_id,
            conversation_id=execution.conversation_id,
            message_content=message.content,
            working_dir=Path(project.root_path),
        )
        content: list[str] = []
        try:
            async for event in adapter.run(task):
                completed_content = None
                if isinstance(event, ExecutionStatusEvent) and event.status == "succeeded":
                    completed_content = "".join(content)
                persisted = await self._persist_adapter_event(
                    execution_id, event, completed_content=completed_content
                )
                if persisted is None:
                    break
                if isinstance(event, ContentDeltaEvent):
                    content.append(event.delta)
                await self._broker.publish(persisted)
        except Exception:
            # Adapter 异常不得把原始异常、stderr、路径或凭据写入数据库和客户端事件。
            await self._persist_safe_failure(execution_id)
        finally:
            self._running_adapters.pop(execution_id, None)

    async def cancel_execution(
        self, project_id: uuid.UUID, execution_id: uuid.UUID
    ) -> EventEnvelope:
        """持久化唯一 cancelled 终态，再通知 Adapter 停止工作。"""
        lock = self._execution_locks.setdefault(execution_id, asyncio.Lock())
        async with lock:
            async with self._sessions() as session, session.begin():
                execution = await session.scalar(
                    select(AgentExecution)
                    .where(
                        AgentExecution.id == execution_id,
                        AgentExecution.project_id == project_id,
                    )
                    .with_for_update()
                )
                if execution is None:
                    raise ChatNotFoundError
                if execution.status in {
                    ExecutionStatus.SUCCEEDED,
                    ExecutionStatus.FAILED,
                    ExecutionStatus.CANCELLED,
                }:
                    existing = await self._last_event(session, execution_id)
                    if existing is None:
                        raise ChatConflictError("执行已结束")
                    return self._to_envelope(existing)
                event = ExecutionStatusEvent(
                    execution_id=execution.id,
                    sequence=execution.sequence + 1,
                    status="cancelled",
                    message="执行已取消",
                )
                envelope = await self._store_event(session, execution, event)
            await self._broker.publish(envelope)
        adapter = self._running_adapters.get(execution_id)
        if adapter is not None:
            result = adapter.cancel(execution_id)
            if inspect.isawaitable(result):
                await result
        return envelope

    async def replay_events(
        self,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        execution_id: uuid.UUID,
        last_sequence: int,
    ) -> list[EventEnvelope]:
        """按执行游标补发严格大于已确认序号的事件。"""
        async with self._sessions() as session:
            await self._get_conversation(session, project_id, conversation_id)
            execution = await session.scalar(
                select(AgentExecution.id).where(
                    AgentExecution.id == execution_id,
                    AgentExecution.project_id == project_id,
                    AgentExecution.conversation_id == conversation_id,
                )
            )
            if execution is None:
                raise ChatNotFoundError
            result = await session.scalars(
                select(ExecutionEvent)
                .where(
                    ExecutionEvent.execution_id == execution_id,
                    ExecutionEvent.sequence > last_sequence,
                )
                .order_by(ExecutionEvent.sequence)
            )
            return [self._to_envelope(item) for item in result]

    async def _persist_adapter_event(
        self,
        execution_id: uuid.UUID,
        event: AgentEvent,
        *,
        completed_content: str | None = None,
    ) -> EventEnvelope | None:
        """原子保存事件；成功终态同时提交完整回复，避免状态与消息可见性竞态。"""
        lock = self._execution_locks.setdefault(execution_id, asyncio.Lock())
        async with lock:
            async with self._sessions() as session, session.begin():
                execution = await session.scalar(
                    select(AgentExecution)
                    .where(AgentExecution.id == execution_id)
                    .with_for_update()
                )
                if execution is None or execution.status in {
                    ExecutionStatus.SUCCEEDED,
                    ExecutionStatus.FAILED,
                    ExecutionStatus.CANCELLED,
                }:
                    return None
                if event.sequence <= execution.sequence:
                    raise ChatConflictError("Adapter 事件序号未单调递增")
                envelope = await self._store_event(session, execution, event)
                if completed_content is not None:
                    # 锁定会话后分配消息序号，确保成功状态对外可见时完整回复也已提交。
                    conversation = await session.scalar(
                        select(Conversation)
                        .where(Conversation.id == execution.conversation_id)
                        .with_for_update()
                    )
                    if conversation is None:
                        raise ChatNotFoundError
                    sequence = await self._next_message_sequence(session, conversation.id)
                    session.add(
                        Message(
                            project_id=execution.project_id,
                            conversation_id=execution.conversation_id,
                            parent_message_id=execution.message_id,
                            role=MessageRole.AGENT,
                            agent_id=execution.agent_id,
                            content=completed_content,
                            content_type="text",
                            sequence=sequence,
                        )
                    )
                return envelope

    async def _store_event(
        self, session: AsyncSession, execution: AgentExecution, event: AgentEvent
    ) -> EventEnvelope:
        payload = event.model_dump(
            mode="json",
            exclude={"event_id", "execution_id", "sequence", "timestamp", "event_type"},
        )
        stored = ExecutionEvent(
            event_id=event.event_id,
            project_id=execution.project_id,
            conversation_id=execution.conversation_id,
            execution_id=execution.id,
            sequence=event.sequence,
            event_type=event.event_type,
            timestamp=event.timestamp,
            payload=payload,
        )
        session.add(stored)
        execution.sequence = event.sequence
        if isinstance(event, ExecutionStatusEvent):
            execution.status = ExecutionStatus(event.status)
            if event.status == "running":
                execution.started_at = event.timestamp
            elif event.status in {"succeeded", "failed", "cancelled"}:
                execution.completed_at = event.timestamp
        elif isinstance(event, ExecutionErrorEvent):
            execution.error_code = event.error_code.value
            execution.error_message = event.error_message
        await session.flush()
        return self._to_envelope(stored)

    async def _persist_safe_failure(self, execution_id: uuid.UUID) -> None:
        lock = self._execution_locks.setdefault(execution_id, asyncio.Lock())
        events: list[EventEnvelope] = []
        async with lock:
            async with self._sessions() as session, session.begin():
                execution = await session.scalar(
                    select(AgentExecution)
                    .where(AgentExecution.id == execution_id)
                    .with_for_update()
                )
                if execution is None or execution.status in {
                    ExecutionStatus.SUCCEEDED,
                    ExecutionStatus.FAILED,
                    ExecutionStatus.CANCELLED,
                }:
                    return
                error = ExecutionErrorEvent(
                    execution_id=execution_id,
                    sequence=execution.sequence + 1,
                    error_code=AdapterErrorCode.INTERNAL_ERROR,
                    error_message="Adapter execution failed",
                )
                events.append(await self._store_event(session, execution, error))
                final = ExecutionStatusEvent(
                    execution_id=execution_id,
                    sequence=execution.sequence + 1,
                    status="failed",
                    message="Adapter execution failed",
                )
                events.append(await self._store_event(session, execution, final))
        for event in events:
            await self._broker.publish(event)

    async def _get_conversation(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Conversation:
        query = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.project_id == project_id
        )
        if for_update:
            query = query.with_for_update()
        conversation = await session.scalar(query)
        if conversation is None:
            raise ChatNotFoundError
        return conversation

    async def _next_message_sequence(
        self, session: AsyncSession, conversation_id: uuid.UUID
    ) -> int:
        current = await session.scalar(
            select(func.max(Message.sequence)).where(Message.conversation_id == conversation_id)
        )
        return 0 if current is None else current + 1

    async def _last_event(
        self, session: AsyncSession, execution_id: uuid.UUID
    ) -> ExecutionEvent | None:
        event = await session.scalar(
            select(ExecutionEvent)
            .where(ExecutionEvent.execution_id == execution_id)
            .order_by(ExecutionEvent.sequence.desc())
            .limit(1)
        )
        return event

    def _to_envelope(self, event: ExecutionEvent) -> EventEnvelope:
        return EventEnvelope(
            event_id=event.event_id,
            conversation_id=event.conversation_id,
            execution_id=event.execution_id,
            sequence=event.sequence,
            type=event.event_type,
            timestamp=event.timestamp,
            payload=cast(dict[str, object], event.payload),
        )
