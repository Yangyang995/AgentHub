"""Phase 4 单聊业务服务：事务、Adapter 执行、事件持久化与取消。"""

import asyncio
import inspect
import re
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

import httpx
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
from agenthub.services.diff_tools import compute_unified_diff, extract_code_blocks
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


async def _generate_title_via_llm(content: str, api_key: str, base_url: str, model: str) -> str:
    """通过 LLM 将首条问题提炼为 10 字以内的关键词标题。"""
    prompt = (
        "将以下用户提问总结为10个字以内的中文关键词标题，"
        "只输出标题本身，不加引号和解释：\n\n" + content
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 20,
                    "temperature": 0.3,
                },
            )
            if resp.status_code < 400:
                data = resp.json()
                title = data["choices"][0]["message"]["content"].strip()
                return title[:20] if title else "新对话"
    except Exception:
        pass
    return "新对话"


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


# ── 代码块处理工具函数 ──────────────────────────────────────────────


def _strip_code_blocks(text: str) -> str:
    """移除 Markdown fenced code block 及其标注行，保留周围文本。"""
    return re.sub(
        r'```[^\n]*\n.*?```',
        '',
        text,
        flags=re.DOTALL,
    )


def _safe_resolve_path(raw_path: str, root_path: str) -> Path | None:
    """安全解析文件路径，防止目录穿越攻击。"""
    try:
        root = Path(root_path).resolve()
        candidate = (root / raw_path).resolve()
        candidate.relative_to(root)  # 触发 ValueError 若路径逃逸
        return candidate.relative_to(root)
    except (ValueError, OSError):
        return None



def _parse_diff_line_numbers(diff_text: str) -> tuple[list[int], list[int]]:
    """从 unified diff 文本中提取新增行号与删除行号。

    返回 (added_lines, deleted_lines)，均为 1-based 行号列表。
    """
    added: list[int] = []
    deleted: list[int] = []
    current_old: int | None = None
    current_new: int | None = None
    for line in diff_text.split("\n"):
        hunk = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if hunk:
            current_old = int(hunk.group(1))
            current_new = int(hunk.group(2))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if current_new is not None:
                added.append(current_new)
                current_new += 1
        elif line.startswith("-") and not line.startswith("---"):
            if current_old is not None:
                deleted.append(current_old)
                current_old += 1
        elif line.startswith(" "):
            if current_old is not None:
                current_old += 1
            if current_new is not None:
                current_new += 1
    return added, deleted




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

    async def _update_title_from_llm(
        self, project_id: uuid.UUID, conversation_id: uuid.UUID, content: str
    ) -> None:
        """后台任务：通过 LLM 提炼会话标题并持久化。"""
        try:
            from agenthub.core.config import get_settings
            settings = get_settings()
            deps = settings.runtime_dependencies()
            title = await _generate_title_via_llm(
                content,
                deps.llm_api_key.get_secret_value(),
                deps.llm_base_url,
                deps.llm_model,
            )
            if title and title != "新对话":
                async with self._sessions() as session, session.begin():
                    conv = await session.get(Conversation, conversation_id)
                    if conv is not None and conv.title in (None, "", "新对话"):
                        conv.title = title
                        conv.updated_at = _utcnow()
        except Exception:
            pass  # 标题生成失败不影响主流程

    async def create_conversation(
        self, project_id: uuid.UUID, data: ConversationCreate
    ) -> ConversationResponse | GroupConversationResponse:
        """创建单聊或群聊。
        单聊默认使用 DeepSeek；群聊默认包含项目中全部启用的 Agent。
        """
        async with self._sessions() as session, session.begin():
            project = await session.get(Project, project_id)
            if project is None:
                raise ChatNotFoundError
            if data.conversation_type == ConversationType.DIRECT:
                # 单聊：默认使用 DeepSeek 提供方
                agent = await self._get_or_create_provider_agent(
                    session, project_id, "deepseek"
                )
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
            # 群聊：若未指定参与者，自动包含项目中全部启用的 Agent
            if data.participant_agent_ids:
                participant_ids = list(dict.fromkeys(data.participant_agent_ids))
            else:
                # 仅选取已配置 System Prompt 的预置子 Agent，不包含后台动态创建的提供方 Agent
                all_agents = await session.scalars(
                    select(Agent).where(
                        Agent.project_id == project_id,
                        Agent.status == AgentStatus.ENABLED,
                        Agent.adapter_config_ref.isnot(None),
                    )
                )
                participant_ids = [a.id for a in all_agents]
            if len(participant_ids) < 2:
                raise ChatConflictError("群聊至少需要两个参与者，请先在项目中注册 Agent")
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
            first_message = conversation.title in (None, "", "新对话")
            if first_message:
                conversation.title = "新对话"  # 占位，后台异步生成
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
            # 后台异步生成会话标题（仅首次消息时触发）
            if first_message:
                import asyncio as _asyncio
                _title_task = _asyncio.create_task(self._update_title_from_llm(  # noqa: RUF006
                    project_id, conversation_id, data.content
                ))
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
            execution_succeeded = False
            async for event in adapter.run(task):
                completed_content = None
                if isinstance(event, ExecutionStatusEvent) and event.status == "succeeded":
                    completed_content = "".join(content)
                    execution_succeeded = True
                persisted = await self._persist_adapter_event(
                    execution_id, event, completed_content=completed_content
                )
                if persisted is None:
                    break
                if isinstance(event, ContentDeltaEvent):
                    content.append(event.delta)
                # 所有事件（含状态变更）都推送到 WebSocket，前端据此更新取消按钮等 UI
                await self._broker.publish(persisted)
            # Phase 7: 群聊模式下检测代码块，落盘并广播 code.summary 事件
            if execution_succeeded and content:
                try:
                    full_content = "".join(content)
                    code_blocks = extract_code_blocks(full_content)
                    # 只有代码类 Agent（生成/审查/测试）才触发代码汇总
                    code_capabilities = {
                        AgentCapability.CODE_GENERATION,
                        AgentCapability.CODE_REVIEW,
                        AgentCapability.TESTING,
                    }
                    agent_caps = set(
                        agent.capabilities or []
                    )
                    is_code_agent = bool(
                        agent_caps & code_capabilities
                    )
                    if (
                        code_blocks
                        and execution.conversation_id is not None
                        and is_code_agent
                    ):
                        async with self._sessions() as session, session.begin():
                            conv = await session.get(
                                Conversation, execution.conversation_id
                            )
                            if (
                                conv is not None
                                and conv.conversation_type == ConversationType.GROUP
                            ):
                                # Agent 回复消息保持完整原始内容（思考+代码块），
                                # 不再剥离代码块——前端负责流式截断和面板解析。
                                # 历史消息保留代码块供刷新后前端重建代码汇总面板。
                                files_data: list[dict[str, object]] = []
                                for block in code_blocks:
                                    if block.file_path is None:
                                        continue
                                    safe = _safe_resolve_path(
                                        block.file_path, project.root_path
                                    )
                                    if safe is None:
                                        continue
                                    abs_path = Path(project.root_path) / safe
                                    try:
                                        original = abs_path.read_text(
                                            encoding="utf-8"
                                        )
                                    except (FileNotFoundError, OSError):
                                        original = ""
                                    # 直接落盘写入文件
                                    abs_path.parent.mkdir(
                                        parents=True, exist_ok=True
                                    )
                                    abs_path.write_text(
                                        block.content, encoding="utf-8"
                                    )
                                    diff = compute_unified_diff(
                                        original, block.content, str(safe)
                                    )
                                    added_lines, deleted_lines = (
                                        _parse_diff_line_numbers(
                                            diff.unified_diff
                                        )
                                    )
                                    is_new = original == ""
                                    files_data.append({
                                        "path": str(safe),
                                        "language": block.language or "",
                                        "content": block.content,
                                        "original_content": original,
                                        "is_new_file": is_new,
                                        "is_modified": (
                                            not is_new
                                            and bool(diff.unified_diff)
                                        ),
                                        "diff": diff.unified_diff,
                                        "added_lines": [] if is_new else added_lines,
                                        "deleted_lines": [] if is_new else deleted_lines,
                                    })
                                if files_data:
                                    # 查询该会话是否已有过代码生成（用于判断是否首次生成）
                                    existing_summary = await session.scalar(
                                        select(ExecutionEvent)
                                        .where(
                                            ExecutionEvent.conversation_id
                                            == execution.conversation_id,
                                            ExecutionEvent.event_type
                                            == "code.summary",
                                        )
                                        .limit(1)
                                    )
                                    is_first_gen = (
                                        existing_summary is None
                                    )
                                    next_seq = execution.sequence + 1
                                    envelope = EventEnvelope(
                                        event_id=uuid.uuid4(),
                                        conversation_id=execution.conversation_id,
                                        execution_id=execution.id,
                                        sequence=next_seq,
                                        type="code.summary",
                                        timestamp=datetime.now(
                                            UTC
                                        ).replace(microsecond=0),
                                        payload={
                                            "execution_id": str(execution.id),
                                            "agent_name": agent.name,
                                            "is_first_generation": is_first_gen,
                                            "files": files_data,
                                        },
                                    )
                                    await self._broker.publish(envelope)
                except Exception:
                    # 代码汇总失败不应影响主流程
                    pass
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
