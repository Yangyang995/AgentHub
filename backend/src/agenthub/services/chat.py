"""Phase 4 单聊业务服务：事务、Adapter 执行、事件持久化与取消。"""

import asyncio
import inspect
import logging
import re
from langgraph.errors import GraphInterrupt
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
from agenthub.rag.knowledge.embedder import EmbeddingClient
from agenthub.rag.memory.retrieval import MemoryRetriever
from agenthub.rag.memory.summarizer import SummaryService
from agenthub.rag.knowledge.retriever import KnowledgeRetriever
from agenthub.rag.knowledge.vector_store import VectorStore


class ChatNotFoundError(LookupError):
    """请求资源不存在，或不属于路径中声明的项目。"""


class ChatConflictError(ValueError):
    """资源存在但不满足单聊状态约束。"""


class AgentAdapter(Protocol):
    """聊天服务实际使用的最小 Adapter 契约。"""

    def run(self, task: AgentTask) -> AsyncIterator[AgentEvent]: ...

    def cancel(self, execution_id: uuid.UUID) -> object: ...


AdapterResolver = Callable[[Agent], AgentAdapter]

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _route_via_llm(
    content: str,
    agents: list[Agent],
    api_key: str,
    base_url: str,
    model: str,
) -> list[Agent]:
    """通过 LLM 判断用户消息应由哪些 Agent 处理。"""
    from agenthub.services.prompt_loader import load_system_prompt
    agent_lines = []
    for agent in agents:
        capability = agent.capabilities[0] if agent.capabilities else None
        desc = ""
        if capability:
            prompt_text = load_system_prompt(capability) or ""
            for line in prompt_text.split("\n"):
                line = line.strip().lstrip("#").strip()
                if line and not line.startswith("##"):
                    desc = line
                    break
        agent_lines.append(f"- {agent.name}：{desc or agent.name}")
    agents_text = "\n".join(agent_lines)
    prompt = (
        f"根据用户消息，判断应调用以下哪些 Agent。\n\n"
        f"{agents_text}\n\n"
        f"用户消息：{content}\n\n"
        f"只输出 Agent 名称（多个用顿号分隔），不输出其他内容。若都不相关，输出无。"
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 30,
                    "temperature": 0.1,
                },
            )
            if resp.status_code < 400:
                data = resp.json()
                result = data["choices"][0]["message"]["content"].strip()
                selected = __import__("re").split(r"[、，,\\s]+", result)
                selected = [s.strip() for s in selected if s.strip()]
                matched = []
                for name in selected:
                    for agent in agents:
                        if agent.name == name:
                            matched.append(agent)
                            break
                if matched:
                    return matched
    except Exception:
        pass
    return []
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
        # Pipeline 审批暂存：conversation_id -> {graph, config}
        self._pending_pipelines: dict[uuid.UUID, dict[str, Any]] = {}

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
            # ???? RUNNING??????? PENDING?????????
            running_count = await session.scalar(
                select(func.count())
                .select_from(AgentExecution)
                .where(
                    AgentExecution.conversation_id == conversation.id,
                    AgentExecution.status == "running",
                )
            )
            if running_count:
                raise ChatConflictError("?????????????")
            # PENDING ??? RUNNING ???????????????????
            pending_execs = await session.scalars(
                select(AgentExecution).where(
                    AgentExecution.conversation_id == conversation.id,
                    AgentExecution.status == "pending",
                )
            )
            for pe in pending_execs:
                pe.status = ExecutionStatus.FAILED
                pe.error_message = "????????????"
            await session.flush()
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
                # @?? ????? Pipeline ??????? Agent
                pipeline_mode = bool(re.search(r"(?<![\w@])@全体(?![\w-])", data.content))
                if pipeline_mode:
                    # 按 pipeline 步骤顺序排序，确保 executions[0] 对应架构设计专家
                    _pipeline_caps = ["architecture_design", "code_generation", "code_review", "testing"]
                    _order = {cap: i for i, cap in enumerate(_pipeline_caps)}
                    targets = sorted(
                        participants,
                        key=lambda a: _order.get(
                            a.capabilities[0] if a.capabilities else "", 99
                        ),
                    )
                else:
                    targets, unknown = parse_agent_mentions(data.content, participants)
                    if unknown:
                        raise ChatConflictError(f"未知 Agent: {', '.join(unknown)}")
                if not targets:
                    # LLM routing via _route_via_llm
                    from agenthub.core.config import get_settings
                    _s = get_settings()
                    _d = _s.runtime_dependencies()
                    targets = await _route_via_llm(data.content, participants, _d.llm_api_key.get_secret_value(), _d.llm_base_url, _d.llm_model)
                    if not targets:
                        # LLM ?????????????? @Agent
                        agent_names = chr(0x3001).join(a.name for a in participants)
                        raise ChatConflictError(
                            f"?????????? Agent ???? @Agent ??????{agent_names}"
                        )
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
                    pipeline=pipeline_mode,
                )
        return response

    async def run_executions(self, execution_ids: list[uuid.UUID]) -> None:
        """并发运行显式点名的执行；单个 Adapter 失败不会取消兄弟执行。"""
        async with asyncio.TaskGroup() as group:
            for execution_id in execution_ids:
                group.create_task(self.run_execution(execution_id))


    async def _summarize_after_execution(
        self, project_id, conversation_id, user_msg, agent_reply
    ):
        """Agent 执行成功后更新会话摘要（失败不阻塞主流程）。"""
        try:
            round_text = "[用户]: " + user_msg[:1000] + "\n[Agent]: " + agent_reply[:1000]
            async with self._sessions() as session:
                svc = SummaryService(session)
                summaries = await svc.get_summaries(conversation_id)
                round_num = len(summaries)
                await svc.summarize_round(
                    project_id, conversation_id, round_text, round_num
                )
        except Exception:
            pass

    async def _inject_rag_memory_context(
        self, project_id, conversation_id, query_text
    ):
        """消息执行前拉取记忆/知识库上下文，注入 AgentTask.context。"""
        import logging as _logging
        _log = _logging.getLogger("agenthub.rag.inject")
        try:
            _log.info("RAG inject start: project=%s conv=%s query=%s", project_id, conversation_id, query_text[:80])
            from agenthub.core.config import get_settings
            settings = get_settings()
            embedder = EmbeddingClient(settings.embedding_dependencies())
            _log.info("Embedding available: %s", embedder.is_available)
            async with self._sessions() as session:
                # 会话摘要 + 跨会话长期偏好
                mem_retriever = MemoryRetriever(session, embedder)
                _log.info("Fetching memory context...")
                mem_ctx = await mem_retriever.retrieve(
                    project_id, conversation_id, query_text
                )
                _log.info("Memory done: summary=%s prefs=%d", bool(mem_ctx.summary_context), len(mem_ctx.preferences))
                # 知识库检索：向量 + 关键词混合检索
                store = VectorStore(session)
                kb_retriever = KnowledgeRetriever(store, embedder)
                _log.info("Fetching knowledge context...")
                kb_result = await kb_retriever.retrieve(
                    project_id, query_text, top_k=5
                )
            result = {}
            _log.info("Knowledge done: %d results", len(kb_result.results))
            if mem_ctx.summary_context:
                result["summary_context"] = mem_ctx.summary_context
            if mem_ctx.preferences:
                result["preference_context"] = mem_ctx.to_context_dict().get(
                    "preference_context", []
                )
            if kb_result.results:
                result["knowledge_context"] = [
                    {"file": r.file_name, "content": r.content[:800], "score": r.score}
                    for r in kb_result.results
                ]
            _log.info("RAG inject done: keys=%s kb_count=%s", list(result.keys()), len(result.get("knowledge_context", [])))
            return result
        except Exception:
            import traceback
            _log.exception("RAG inject FAILED")
            return {}

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
            # ???????? Agent ???????????????? PENDING
            await self._persist_safe_failure(execution_id)
            return
        execution, agent, message, project = record
        try:
            # 执行已在 submit_message 中创建为 PENDING，此处通过 Adapter 事件流转到 RUNNING
            adapter = self._resolve_adapter(agent)
            self._running_adapters[execution_id] = adapter
            logger.info("Pipeline step adapter ready: agent=%s", agent.name)
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
            # 注入 RAG 记忆上下文
            rag_ctx = await self._inject_rag_memory_context(
                execution.project_id, execution.conversation_id, message.content
            )
            task_context = {"messages": history}
            if rag_ctx:
                task_context.update(rag_ctx)
            task = AgentTask(
                execution_id=execution.id,
                project_id=execution.project_id,
                agent_id=execution.agent_id,
                conversation_id=execution.conversation_id,
                message_content=message.content,
                working_dir=Path(project.root_path),
                context=task_context,
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
            # 执行成功后更新会话摘要
            if execution_succeeded and content:
                await self._summarize_after_execution(
                    execution.project_id, execution.conversation_id,
                    message.content, "".join(content)
                )
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
            # Adapter 异常：持久化失败状态，防止执行永远卡在 PENDING
            try:
                await self._persist_safe_failure(execution_id)
            except Exception:
                # 极端情况：_persist_safe_failure 本身也失败（如 DB 断开）
                # 尝试最后一次直接更新状态
                try:
                    async with self._sessions() as session, session.begin():
                        execution_ref = await session.get(AgentExecution, execution_id, with_for_update=True)
                        if execution_ref is not None and execution_ref.status == ExecutionStatus.PENDING:
                            execution_ref.status = ExecutionStatus.FAILED
                except Exception:
                    pass
        finally:
            self._running_adapters.pop(execution_id, None)
            await self._refresh_conversation_status(execution.message_id)

    # ---- Pipeline ?? ----

    async def resume_pipeline(
        self, conversation_id: uuid.UUID, action: str, feedback: str = ""
    ) -> None:
        """恢复被架构审批中断的 Pipeline。

        Args:
            conversation_id: 会话 ID
            action: "accept" | "reject" | "modify"
            feedback: 用户修改意见（action="modify" 时有效）
        """
        from agenthub.orchestrator.graph import build_pipeline_graph, run_pipeline as _run_graph
        from langgraph.types import Command

        pending = self._pending_pipelines.pop(conversation_id, None)
        if pending is None:
            raise RuntimeError("No pending pipeline for this conversation")

        graph = pending["graph"]
        config = pending["config"]

        logger.info(
            "Pipeline resume: conversation=%s, action=%s", conversation_id, action
        )

        # 通过 WebSocket 发送用户决策事件
        decision_envelope = EventEnvelope(
            event_id=uuid.uuid4(),
            conversation_id=conversation_id,
            execution_id=uuid.uuid4(),
            sequence=0,
            type="pipeline.approval_resolved",
            timestamp=_utcnow(),
            payload={"action": action, "feedback": feedback},
        )
        await self._broker.publish(decision_envelope)

        try:
            final_state = await graph.ainvoke(
                Command(resume={"action": action, "feedback": feedback}),
                config,
            )
            agent_status = final_state.get("agent_status", {})
            succeeded = sum(1 for s in agent_status.values() if s == "succeeded")
            failed = sum(1 for s in agent_status.values() if s == "failed")
            logger.info(
                "Pipeline resumed & completed: conversation=%s, succeeded=%d, failed=%d, error=%s",
                conversation_id, succeeded, failed, final_state.get("error"),
            )
        except Exception:
            logger.exception("Pipeline resume failed: conversation=%s", conversation_id)
            await self._finalize_pipeline(conversation_id, {})
            raise

    async def _run_pipeline_step(
        self, execution_id: uuid.UUID, extra_context: list[dict[str, str]] | None = None
    ) -> str:
        """Pipeline ????????? Agent ?????????

        ? run_execution ??????
        - ?? extra_context ???? Agent ????????
        - ?????????????????? WebSocket?
        - ??? _refresh_conversation_status?? _finalize_pipeline ?????

        Args:
            execution_id: Agent ???? ID
            extra_context: ?? Agent ?????????????????

        Returns:
            Agent ???????

        Raises:
            ?????????????? LangGraph ?????
        """
        extra_context = extra_context or []
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
            raise RuntimeError(f"Execution not found: {execution_id}")
        execution, agent, message, project = record
        try:
            adapter = self._resolve_adapter(agent)
            self._running_adapters[execution_id] = adapter
            logger.info("Pipeline step adapter ready: agent=%s", agent.name)
            # ???????????Agent???
            async with self._sessions() as session:
                message_rows = await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == execution.conversation_id)
                    .order_by(Message.sequence)
                )
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
            # ???????????Agent??????
            context_messages = list(history) + list(extra_context)
            # 注入 RAG 记忆上下文
            rag_ctx = await self._inject_rag_memory_context(
                execution.project_id, execution.conversation_id, message.content
            )
            task_context = {"messages": context_messages}
            if rag_ctx:
                task_context.update(rag_ctx)
            task = AgentTask(
                execution_id=execution.id,
                project_id=execution.project_id,
                agent_id=execution.agent_id,
                conversation_id=execution.conversation_id,
                message_content=message.content,
                working_dir=Path(project.root_path),
                context=task_context,
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
                # ????? WebSocket????????
                await self._broker.publish(persisted)
            if not execution_succeeded:
                raise RuntimeError(f"Agent execution failed: {execution_id}")
            full = "".join(content)
            # 执行成功后更新会话摘要
            await self._summarize_after_execution(
                execution.project_id, execution.conversation_id,
                message.content, full
            )
            logger.info("Pipeline step DONE: execution=%s, output_len=%d", execution_id, len(full))
            return full
        finally:
            self._running_adapters.pop(execution_id, None)

    async def _finalize_pipeline(
        self, conversation_id: uuid.UUID, agent_status: dict[str, str]
    ) -> None:
        """Pipeline ????????????????

        Args:
            conversation_id: ?? ID
            agent_status: ? Agent ???????
        """
        any_failed = any(s == "failed" for s in agent_status.values())
        all_succeeded = all(s == "succeeded" for s in agent_status.values())
        async with self._sessions() as session, session.begin():
            conversation = await session.get(Conversation, conversation_id)
            if conversation is not None:
                if all_succeeded:
                    conversation.status = ConversationStatus.SUCCEEDED
                elif any_failed:
                    conversation.status = ConversationStatus.PARTIAL_FAILED
                else:
                    conversation.status = ConversationStatus.IDLE
                conversation.updated_at = _utcnow()

    async def run_pipeline(self, execution_ids: list[uuid.UUID]) -> None:
        """?? @?? Pipeline??? Agent ?????

        ?? LangGraph StateGraph ???????? Agent ???
        ??????????? Agent??????????? WebSocket
        ?????

        Args:
            execution_ids: ?? Agent ????? ID ??
                          ???[????, ????, ????, ??]
        """
        from agenthub.orchestrator.graph import build_pipeline_graph, run_pipeline as _run_graph

        if len(execution_ids) < 4:
            raise ValueError("Pipeline ???? 4 ? Agent ????")

        # ???? execution ???????
        async with self._sessions() as session:
            execution = await session.get(AgentExecution, execution_ids[0])
            if execution is None:
                raise RuntimeError("Execution not found")
            message = await session.get(Message, execution.message_id)
            if message is None:
                raise RuntimeError("Message not found")
            conversation_id = execution.conversation_id
            project_id = execution.project_id
            user_message = message.content

        graph = build_pipeline_graph(self)
        try:
            # 构建 step→execution_id 映射，按 Agent capability 而非位置匹配
            step_execution_map: dict[str, str] = {}
            async with self._sessions() as session:
                for eid in execution_ids:
                    exec_row = await session.execute(
                        select(AgentExecution.agent_id).where(AgentExecution.id == eid)
                    )
                    agent_id = exec_row.scalar_one_or_none()
                    if agent_id is None:
                        continue
                    agent_row = await session.execute(
                        select(Agent.capabilities).where(Agent.id == agent_id)
                    )
                    caps = agent_row.scalar_one_or_none()
                    if caps and len(caps) > 0:
                        cap = caps[0]
                        pipeline_caps = {"architecture_design", "code_generation", "code_review", "testing"}
                        if cap in pipeline_caps:
                            step_execution_map[cap] = str(eid)

            config = {"configurable": {"thread_id": str(conversation_id)}}
            final_state = await _run_graph(
                chat_service=self,
                execution_ids=execution_ids,
                conversation_id=conversation_id,
                project_id=project_id,
                user_message=user_message,
                step_execution_map=step_execution_map if step_execution_map else None,
            )

            # LangGraph 1.2+: interrupt() ???????????? _interrupted ??
            if final_state.get("_interrupted"):
                logger.info("Pipeline awaiting approval: conversation=%s", conversation_id)
                self._pending_pipelines[conversation_id] = {
                    "graph": graph,
                    "config": config,
                }
                interrupt_envelope = EventEnvelope(
                    event_id=uuid.uuid4(),
                    conversation_id=conversation_id,
                    execution_id=execution_ids[0],
                    sequence=0,
                    type="pipeline.awaiting_approval",
                    timestamp=_utcnow(),
                    payload={
                        "execution_id": str(execution_ids[0]),
                        "message": "???????????",
                    },
                )
                await self._broker.publish(interrupt_envelope)
                return

            # ???? Pipeline ????
            agent_status = final_state.get("agent_status", {})
            succeeded = sum(1 for s in agent_status.values() if s == "succeeded")
            failed = sum(1 for s in agent_status.values() if s == "failed")
            logger.info(
                "Pipeline completed: conversation=%s, succeeded=%d, failed=%d, error=%s",
                conversation_id, succeeded, failed, final_state.get("error"),
            )
        except Exception:
            logger.exception("Pipeline execution failed: conversation=%s", conversation_id)
            self._pending_pipelines.pop(conversation_id, None)
            await self._finalize_pipeline(conversation_id, {})
            raise


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
