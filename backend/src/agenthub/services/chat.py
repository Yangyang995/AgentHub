"""Phase 4 单聊业务服务：事务、Adapter 执行、事件持久化与取消。"""

import asyncio
import inspect
import re
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from sqlalchemy import delete, func, select
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
    AgentCapability,
    AgentStatus,
    AgentType,
    ConversationStatus,
    ConversationType,
    ExecutionStatus,
    MessageRole,
)
from agenthub.models.orm import (
    Agent,
    AgentExecution,
    Conversation,
    ConversationParticipant,
    ExecutionEvent,
    Message,
    Project,
)
from agenthub.schemas.domain import (
    AgentExecutionResponse,
    ConversationCreate,
    ConversationParticipantResponse,
    ConversationResponse,
    EventEnvelope,
    GroupConversationResponse,
    GroupMessageSubmissionResponse,
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


def _conversation_title(content: str) -> str:
    """将首条问题压缩为稳定短标题，避免新建会话时额外调用模型。"""
    title = " ".join(content.split()).strip().strip("#*`> ")
    return title[:36] if title else "新对话"


def _conversation_response(conversation: Conversation, agent: Agent | None) -> ConversationResponse:
    """组合会话与 Agent 展示字段，避免路由层接触 ORM 关系。"""
    response = ConversationResponse.model_validate(conversation)
    return response.model_copy(
        update={
            "agent_name": agent.name if agent is not None else None,
            "agent_type": agent.agent_type if agent is not None else None,
        }
    )


def _group_conversation_response(
    conversation: Conversation, agents: list[Agent]
) -> GroupConversationResponse:
    """组合群聊与参与者，参与者按名称稳定展示。"""
    return GroupConversationResponse(
        id=conversation.id,
        project_id=conversation.project_id,
        title=conversation.title,
        conversation_type=ConversationType.GROUP,
        status=conversation.status,
        participants=[ConversationParticipantResponse.model_validate(agent) for agent in agents],
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def parse_agent_mentions(content: str, agents: list[Agent]) -> tuple[list[Agent], list[str]]:
    """单次扫描显式 @名称，按首次出现去重，并返回无法识别的名称。

    已知名称优先按长度匹配，名称前后必须是文本边界，避免 ``@Code`` 误匹配
    ``@Coder``。未知名称只截取到空白或常见标点，错误中不会回显整条用户消息。
    """
    by_name = {agent.name: agent for agent in agents}
    names = sorted(by_name, key=len, reverse=True)
    known_pattern = "|".join(re.escape(name) for name in names)
    token_pattern = re.compile(r"(?<![\w@])@([^\s@,，。！？!?:：;；()（）\[\]{}<>]+)")
    known = re.compile(rf"(?<![\w@])@({known_pattern})(?![\w-])") if known_pattern else None
    matches: list[tuple[int, Agent]] = []
    occupied: list[tuple[int, int]] = []
    if known is not None:
        for match in known.finditer(content):
            matches.append((match.start(), by_name[match.group(1)]))
            occupied.append(match.span())
    unknown: list[str] = []
    for match in token_pattern.finditer(content):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        name = match.group(1)
        if name not in unknown:
            unknown.append(name)
    selected: list[Agent] = []
    seen: set[uuid.UUID] = set()
    for _, agent in sorted(matches, key=lambda item: item[0]):
        if agent.id not in seen:
            selected.append(agent)
            seen.add(agent.id)
    return selected, unknown


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
    ) -> ConversationResponse | GroupConversationResponse:
        """创建兼容单聊或至少包含两个项目 Agent 的群聊。"""
        async with self._sessions() as session, session.begin():
            project = await session.get(Project, project_id)
            if project is None:
                raise ChatNotFoundError
            if data.conversation_type == ConversationType.DIRECT:
                if (
                    data.participant_agent_ids is not None
                    or (data.agent_id is None and data.provider is None)
                    or (data.agent_id is not None and data.provider is not None)
                ):
                    raise ChatConflictError("单聊必须且只能指定一个提供方或 Agent")
                if data.provider is not None:
                    agent = await self._get_or_create_provider_agent(
                        session, project_id, data.provider
                    )
                else:
                    assert data.agent_id is not None
                    selected_agent = await session.get(Agent, data.agent_id)
                    if selected_agent is None or selected_agent.project_id != project_id:
                        raise ChatNotFoundError
                    agent = selected_agent
                if agent.status != AgentStatus.ENABLED:
                    raise ChatConflictError("Agent 未启用")
                conversation = Conversation(
                    project_id=project_id,
                    agent_id=agent.id,
                    title="新对话",
                    conversation_type=ConversationType.DIRECT,
                )
                session.add(conversation)
                await session.flush()
                return _conversation_response(conversation, agent)
            participant_ids = list(dict.fromkeys(data.participant_agent_ids or []))
            if data.agent_id is not None or len(participant_ids) < 2:
                raise ChatConflictError("群聊必须指定至少两个唯一参与 Agent")
            agents = list(
                await session.scalars(
                    select(Agent).where(
                        Agent.project_id == project_id, Agent.id.in_(participant_ids)
                    )
                )
            )
            if len(agents) != len(participant_ids):
                raise ChatNotFoundError
            if any(agent.status != AgentStatus.ENABLED for agent in agents):
                raise ChatConflictError("群聊参与 Agent 必须处于启用状态")
            conversation = Conversation(
                project_id=project_id,
                agent_id=None,
                title="新对话",
                conversation_type=ConversationType.GROUP,
            )
            session.add(conversation)
            await session.flush()
            session.add_all(
                [
                    ConversationParticipant(
                        project_id=project_id, conversation_id=conversation.id, agent_id=agent.id
                    )
                    for agent in agents
                ]
            )
            await session.flush()
            agents.sort(key=lambda item: item.name)
            return _group_conversation_response(conversation, agents)

    async def _get_or_create_provider_agent(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        provider: Literal["deepseek"],
    ) -> Agent:
        """取得固定提供方对应的内部 Agent；不向前端开放注册和能力配置。"""
        definitions = {
            "deepseek": ("DeepSeek", AgentType.OPENAI_COMPATIBLE),
        }
        name, agent_type = definitions[provider]
        agent = await session.scalar(
            select(Agent).where(Agent.project_id == project_id, Agent.name == name)
        )
        if agent is not None:
            if agent.agent_type != agent_type:
                raise ChatConflictError(f"{name} 提供方配置冲突")
            return agent
        agent = Agent(
            project_id=project_id,
            name=name,
            agent_type=agent_type,
            capabilities=[
                AgentCapability.CODE_GENERATION.value,
                AgentCapability.CODE_REVIEW.value,
                AgentCapability.TESTING.value,
            ],
            status=AgentStatus.ENABLED,
        )
        session.add(agent)
        await session.flush()
        return agent

    async def list_conversations(
        self, project_id: uuid.UUID
    ) -> list[ConversationResponse | GroupConversationResponse]:
        """返回项目会话；单聊继续使用 Phase 5 响应结构。"""
        async with self._sessions() as session:
            if await session.get(Project, project_id) is None:
                raise ChatNotFoundError
            result = await session.execute(
                select(Conversation, Agent)
                .join(Agent, Agent.id == Conversation.agent_id, isouter=True)
                .where(Conversation.project_id == project_id)
                .order_by(Conversation.updated_at.desc())
            )
            responses: list[ConversationResponse | GroupConversationResponse] = []
            for item, agent in result:
                if item.conversation_type == ConversationType.DIRECT:
                    responses.append(_conversation_response(item, agent))
                else:
                    responses.append(
                        _group_conversation_response(
                            item, await self._participant_agents(session, item.id)
                        )
                    )
            return responses

    async def get_conversation(
        self, project_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> ConversationResponse | GroupConversationResponse:
        """按项目边界读取会话，不泄露跨项目资源是否存在。"""
        async with self._sessions() as session:
            conversation = await self._get_conversation(session, project_id, conversation_id)
            agent = (
                await session.get(Agent, conversation.agent_id) if conversation.agent_id else None
            )
            if conversation.conversation_type == ConversationType.DIRECT:
                return _conversation_response(conversation, agent)
            return _group_conversation_response(
                conversation, await self._participant_agents(session, conversation.id)
            )

    async def delete_conversation(self, project_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        """删除项目内历史会话；执行中会话必须先取消，避免删除流式执行上下文。"""
        async with self._sessions() as session, session.begin():
            conversation = await self._get_conversation(session, project_id, conversation_id)
            active = await session.scalar(
                select(func.count())
                .select_from(AgentExecution)
                .where(
                    AgentExecution.conversation_id == conversation.id,
                    AgentExecution.status.in_(("pending", "running")),
                )
            )
            if active:
                raise ChatConflictError("会话仍在执行，请先取消执行")
            await session.execute(delete(Conversation).where(Conversation.id == conversation.id))

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
    ) -> MessageSubmissionResponse | GroupMessageSubmissionResponse:
        """保存用户消息，并依据会话类型一次性确定一个或多个目标 Agent。"""
        async with self._sessions() as session, session.begin():
            conversation = await self._get_conversation(
                session, project_id, conversation_id, for_update=True
            )
            if conversation.conversation_type == ConversationType.DIRECT:
                if conversation.agent_id is None:
                    raise ChatConflictError("单聊会话未绑定 Agent")
                agent = await session.get(Agent, conversation.agent_id)
                if agent is None or agent.project_id != project_id:
                    raise ChatNotFoundError
                targets = [agent]
            else:
                participants = await self._participant_agents(session, conversation.id)
                targets, unknown = parse_agent_mentions(data.content, participants)
                if unknown:
                    raise ChatConflictError(f"未知 Agent: {', '.join(unknown)}")
                if not targets:
                    raise ChatConflictError("群聊消息必须显式 @至少一个参与 Agent")
            disabled = [agent.name for agent in targets if agent.status != AgentStatus.ENABLED]
            if disabled:
                raise ChatConflictError(f"Agent 已禁用: {', '.join(disabled)}")
            if conversation.title in (None, "", "新对话"):
                conversation.title = _conversation_title(data.content)
            conversation.updated_at = _utcnow()
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
            executions = [
                AgentExecution(
                    project_id=project_id,
                    conversation_id=conversation_id,
                    message_id=message.id,
                    agent_id=target.id,
                    status=ExecutionStatus.PENDING,
                    sequence=-1,
                )
                for target in targets
            ]
            session.add_all(executions)
            conversation.status = ConversationStatus.RUNNING
            await session.flush()
            if conversation.conversation_type == ConversationType.DIRECT:
                response: MessageSubmissionResponse | GroupMessageSubmissionResponse = (
                    MessageSubmissionResponse(
                        message=MessageResponse.model_validate(message),
                        execution=AgentExecutionResponse.model_validate(executions[0]),
                    )
                )
            else:
                response = GroupMessageSubmissionResponse(
                    message=MessageResponse.model_validate(message),
                    executions=[AgentExecutionResponse.model_validate(item) for item in executions],
                )
        return response

    async def run_executions(self, execution_ids: list[uuid.UUID]) -> None:
        """并发运行显式点名的执行；单个 Adapter 失败不会取消兄弟执行。"""
        async with asyncio.TaskGroup() as group:
            for execution_id in execution_ids:
                group.create_task(self.run_execution(execution_id))

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
        try:
            adapter = self._resolve_adapter(agent)
            self._running_adapters[execution_id] = adapter
            async with self._sessions() as session:
                message_rows = await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == execution.conversation_id)
                    .order_by(Message.sequence)
                )
                # SQLAlchemy 的 VARCHAR 枚举字段可能返回 str；统一为平台协议使用的角色文本。
                history = [
                    {
                        "role": (
                            "assistant"
                            if str(item.role) == MessageRole.AGENT.value
                            else str(item.role)
                        ),
                        "content": item.content,
                    }
                    for item in message_rows
                ]
            task = AgentTask(
                execution_id=execution.id,
                project_id=execution.project_id,
                agent_id=execution.agent_id,
                conversation_id=execution.conversation_id,
                message_content=message.content,
                working_dir=Path(project.root_path),
                context={"messages": history},
            )
            content: list[str] = []
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
            # Adapter exception - use safe failure
            await self._persist_safe_failure(execution_id)
        finally:
            self._running_adapters.pop(execution_id, None)
            await self._refresh_conversation_status(execution.message_id)

    async def cancel_execution(
        self, project_id: uuid.UUID, execution_id: uuid.UUID
    ) -> EventEnvelope:
        """取消目标执行；群聊中向同一用户消息的活动兄弟执行传播取消。"""
        async with self._sessions() as session:
            execution = await session.scalar(
                select(AgentExecution).where(
                    AgentExecution.id == execution_id, AgentExecution.project_id == project_id
                )
            )
            if execution is None:
                raise ChatNotFoundError
            sibling_ids = list(
                await session.scalars(
                    select(AgentExecution.id).where(
                        AgentExecution.message_id == execution.message_id,
                        AgentExecution.project_id == project_id,
                        AgentExecution.status.in_(("pending", "running")),
                    )
                )
            )
        target_event: EventEnvelope | None = None
        for sibling_id in sibling_ids or [execution_id]:
            event = await self._cancel_one(project_id, sibling_id)
            if sibling_id == execution_id:
                target_event = event
        await self._refresh_conversation_status(execution.message_id)
        if target_event is None:
            return await self._cancel_one(project_id, execution_id)
        return target_event

    async def _cancel_one(self, project_id: uuid.UUID, execution_id: uuid.UUID) -> EventEnvelope:
        """持久化单个执行的唯一取消终态，再通知对应 Adapter。"""
        lock = self._execution_locks.setdefault(execution_id, asyncio.Lock())
        async with lock:
            async with self._sessions() as session, session.begin():
                conversation_id = await session.scalar(
                    select(AgentExecution.conversation_id).where(
                        AgentExecution.id == execution_id,
                        AgentExecution.project_id == project_id,
                    )
                )
                if conversation_id is None:
                    raise ChatNotFoundError
                conversation = await session.scalar(
                    select(Conversation).where(Conversation.id == conversation_id).with_for_update()
                )
                if conversation is None:
                    raise ChatNotFoundError
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
                await self._update_conversation_status(session, conversation, execution.message_id)
            await self._broker.publish(envelope)
        adapter = self._running_adapters.get(execution_id)
        if adapter is not None:
            result = adapter.cancel(execution_id)
            if inspect.isawaitable(result):
                await result
        return envelope

    async def _refresh_conversation_status(self, message_id: uuid.UUID) -> None:
        """根据同一用户消息的所有执行终态更新会话聚合状态。"""
        async with self._sessions() as session, session.begin():
            conversation = await session.scalar(
                select(Conversation)
                .join(AgentExecution, AgentExecution.conversation_id == Conversation.id)
                .where(AgentExecution.message_id == message_id)
                .with_for_update()
            )
            if conversation is None:
                return
            await self._update_conversation_status(session, conversation, message_id)

    async def _update_conversation_status(
        self,
        session: AsyncSession,
        conversation: Conversation,
        message_id: uuid.UUID,
    ) -> None:
        """在持有会话行锁时计算执行批次状态，终态事件和聚合状态原子可见。"""
        statuses = {
            ExecutionStatus(status)
            for status in await session.scalars(
                select(AgentExecution.status).where(AgentExecution.message_id == message_id)
            )
        }
        if statuses & {ExecutionStatus.PENDING, ExecutionStatus.RUNNING}:
            conversation.status = ConversationStatus.RUNNING
        elif statuses == {ExecutionStatus.SUCCEEDED}:
            conversation.status = ConversationStatus.SUCCEEDED
        elif statuses == {ExecutionStatus.CANCELLED}:
            conversation.status = ConversationStatus.CANCELLED
        elif ExecutionStatus.SUCCEEDED in statuses:
            conversation.status = ConversationStatus.PARTIAL_FAILED
        else:
            conversation.status = ConversationStatus.FAILED

    async def _participant_agents(
        self, session: AsyncSession, conversation_id: uuid.UUID
    ) -> list[Agent]:
        """按名称读取群聊参与者，避免依赖 ORM 懒加载。"""
        agents = await session.scalars(
            select(Agent)
            .join(ConversationParticipant, ConversationParticipant.agent_id == Agent.id)
            .where(ConversationParticipant.conversation_id == conversation_id)
            .order_by(Agent.name)
        )
        return list(agents)

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
                conversation_id = await session.scalar(
                    select(AgentExecution.conversation_id).where(AgentExecution.id == execution_id)
                )
                if conversation_id is None:
                    return None
                # 所有会话级写入统一先锁会话、再锁执行，避免并发终态与状态刷新反向等待。
                conversation = await session.scalar(
                    select(Conversation).where(Conversation.id == conversation_id).with_for_update()
                )
                if conversation is None:
                    return None
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
                    # 会话锁覆盖序号读取与插入，成功状态和完整回复在同一事务中可见。
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
                if isinstance(event, ExecutionStatusEvent) and event.status in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }:
                    await self._update_conversation_status(
                        session, conversation, execution.message_id
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
                conversation_id = await session.scalar(
                    select(AgentExecution.conversation_id).where(AgentExecution.id == execution_id)
                )
                if conversation_id is None:
                    return
                conversation = await session.scalar(
                    select(Conversation).where(Conversation.id == conversation_id).with_for_update()
                )
                if conversation is None:
                    return
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
                await self._update_conversation_status(session, conversation, execution.message_id)
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
