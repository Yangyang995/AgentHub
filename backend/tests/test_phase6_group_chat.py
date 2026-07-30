"""Phase 6 Agent 管理、显式路由和并发群聊集成测试。"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agenthub.adapters import MockAdapter, MockAdapterScript, MockScriptStep
from agenthub.adapters.protocol import AgentEvent, AgentTask
from agenthub.core.config import Settings
from agenthub.main import create_app
from agenthub.models.enums import (
    AgentStatus,
    AgentType,
    ConversationStatus,
    ExecutionStatus,
)
from agenthub.models.orm import Agent, AgentExecution, Conversation, Message, Project
from agenthub.services.chat import AgentAdapter, ChatService, parse_agent_mentions


class StartBarrierAdapter:
    """两个 Adapter 都开始后才继续，用于证明 TaskGroup 确实并发启动。"""

    def __init__(self, started: set[uuid.UUID], ready: asyncio.Event) -> None:
        self._started = started
        self._ready = ready
        self._delegate = MockAdapter(
            MockAdapterScript(script=[MockScriptStep(action="delta", content="并发回复")])
        )

    def run(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        async def events() -> AsyncIterator[AgentEvent]:
            self._started.add(task.agent_id)
            if len(self._started) == 2:
                self._ready.set()
            await asyncio.wait_for(self._ready.wait(), timeout=0.5)
            async for event in self._delegate.run(task):
                yield event

        return events()

    def cancel(self, execution_id: uuid.UUID) -> None:
        self._delegate.cancel(execution_id)


@pytest_asyncio.fixture
async def group_context(
    db_engine: AsyncEngine,
) -> AsyncIterator[
    tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        list[Agent],
        ChatService,
    ]
]:
    """建立三 Agent 的真实 PostgreSQL 测试项目。"""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    project = Project(name=f"phase6-{uuid.uuid4()}", root_path="C:\\workspace\\phase6")
    async with factory() as session, session.begin():
        session.add(project)
        await session.flush()
        agents = [
            Agent(project_id=project.id, name=name, agent_type=AgentType.MOCK)
            for name in ("Code", "Coder", "测试 Agent")
        ]
        session.add_all(agents)
        await session.flush()
    adapters: dict[uuid.UUID, Callable[[], AgentAdapter]] = {}

    def resolve(agent: Agent) -> AgentAdapter:
        factory_function = adapters.get(agent.id)
        return factory_function() if factory_function else MockAdapter(MockAdapterScript())

    app = create_app(
        Settings(environment="test", _env_file=None),  # type: ignore[call-arg]
        session_factory=factory,
        adapter_resolver=resolve,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, factory, adapters, project, agents, app.state.chat_service
    async with factory() as session, session.begin():
        await session.execute(delete(Project).where(Project.id == project.id))


async def _group(client: AsyncClient, project: Project, agents: list[Agent]) -> uuid.UUID:
    response = await client.post(
        f"/api/v1/projects/{project.id}/conversations",
        json={
            "conversation_type": "group",
            "participant_agent_ids": [str(agent.id) for agent in agents],
        },
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def _wait_batch(
    factory: async_sessionmaker[AsyncSession], message_id: uuid.UUID
) -> list[AgentExecution]:
    for _ in range(150):
        async with factory() as session:
            rows = list(
                await session.scalars(
                    select(AgentExecution).where(AgentExecution.message_id == message_id)
                )
            )
            if rows and all(
                item.status
                in {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}
                for item in rows
            ):
                return rows
        await asyncio.sleep(0.01)
    pytest.fail("group execution batch did not finish")


def test_parser_honors_name_boundaries_order_and_unique_mentions() -> None:
    """短名称不得吞掉长名称，重复点名只保留第一次。"""
    agents = [
        Agent(id=uuid.uuid4(), project_id=uuid.uuid4(), name="Code", agent_type=AgentType.MOCK),
        Agent(id=uuid.uuid4(), project_id=uuid.uuid4(), name="Coder", agent_type=AgentType.MOCK),
    ]
    selected, unknown = parse_agent_mentions("@Coder 看看，@Code 复核；@Coder 再看", agents)
    assert [agent.name for agent in selected] == ["Coder", "Code"]
    assert unknown == []
    selected, unknown = parse_agent_mentions("abc@Code @CodeReview @Code", agents)
    assert [agent.name for agent in selected] == ["Code"]
    assert unknown == ["CodeReview"]


@pytest.mark.asyncio
async def test_agent_management_declares_capabilities_and_toggles_status(
    group_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        list[Agent],
        ChatService,
    ],
) -> None:
    client, _, _, project, _, _ = group_context
    created = await client.post(
        f"/api/v1/projects/{project.id}/agents",
        json={"name": "Reviewer", "agent_type": "mock", "capabilities": ["code_review"]},
    )
    assert created.status_code == 201
    agent_id = created.json()["id"]
    disabled = await client.patch(
        f"/api/v1/projects/{project.id}/agents/{agent_id}", json={"status": "disabled"}
    )
    assert disabled.json()["status"] == "disabled"
    listed = await client.get(f"/api/v1/projects/{project.id}/agents")
    reviewer = next(item for item in listed.json() if item["id"] == agent_id)
    assert reviewer["capabilities"] == ["code_review"]


@pytest.mark.asyncio
async def test_unknown_disabled_and_duplicate_mentions_are_safe_and_deterministic(
    group_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        list[Agent],
        ChatService,
    ],
) -> None:
    client, factory, _, project, agents, _ = group_context
    conversation_id = await _group(client, project, agents[:2])
    unknown = await client.post(
        f"/api/v1/projects/{project.id}/conversations/{conversation_id}/messages",
        json={"content": "@Missing 检查"},
    )
    assert unknown.status_code == 409
    assert unknown.json()["detail"] == "未知 Agent: Missing"
    async with factory() as session, session.begin():
        code = await session.get(Agent, agents[0].id)
        assert code is not None
        code.status = AgentStatus.DISABLED
    disabled = await client.post(
        f"/api/v1/projects/{project.id}/conversations/{conversation_id}/messages",
        json={"content": "@Code 检查"},
    )
    assert disabled.status_code == 409
    assert disabled.json()["detail"] == "Agent 已禁用: Code"
    async with factory() as session, session.begin():
        code = await session.get(Agent, agents[0].id)
        assert code is not None
        code.status = AgentStatus.ENABLED
    duplicate = await client.post(
        f"/api/v1/projects/{project.id}/conversations/{conversation_id}/messages",
        json={"content": "@Code 先看，@Code 再看"},
    )
    assert duplicate.status_code == 202
    assert len(duplicate.json()["executions"]) == 1
    await _wait_batch(factory, uuid.UUID(duplicate.json()["message"]["id"]))


@pytest.mark.asyncio
async def test_group_runs_concurrently_and_persists_independent_agent_messages(
    group_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        list[Agent],
        ChatService,
    ],
) -> None:
    client, factory, adapters, project, agents, _ = group_context
    started: set[uuid.UUID] = set()
    ready = asyncio.Event()
    for agent in agents[:2]:
        adapters[agent.id] = lambda: StartBarrierAdapter(started, ready)
    conversation_id = await _group(client, project, agents[:2])
    response = await client.post(
        f"/api/v1/projects/{project.id}/conversations/{conversation_id}/messages",
        json={"content": "@Code @Coder 并发检查"},
    )
    assert response.status_code == 202
    message_id = uuid.UUID(response.json()["message"]["id"])
    executions = await _wait_batch(factory, message_id)
    assert {item.agent_id for item in executions} == {agent.id for agent in agents[:2]}
    assert set(started) == {agent.id for agent in agents[:2]}
    async with factory() as session:
        replies = list(
            await session.scalars(
                select(Message)
                .where(Message.parent_message_id == message_id)
                .order_by(Message.sequence)
            )
        )
    assert len(replies) == 2
    assert {reply.agent_id for reply in replies} == {agent.id for agent in agents[:2]}
    assert len({reply.sequence for reply in replies}) == 2


@pytest.mark.asyncio
async def test_partial_failure_and_cancel_propagation_update_conversation_status(
    group_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        list[Agent],
        ChatService,
    ],
) -> None:
    client, factory, adapters, project, agents, _ = group_context
    adapters[agents[0].id] = lambda: MockAdapter(
        MockAdapterScript(script=[MockScriptStep(action="delta", content="ok")])
    )
    adapters[agents[1].id] = lambda: MockAdapter(
        MockAdapterScript(script=[MockScriptStep(action="error", error_message="safe failure")])
    )
    conversation_id = await _group(client, project, agents[:2])
    partial = await client.post(
        f"/api/v1/projects/{project.id}/conversations/{conversation_id}/messages",
        json={"content": "@Code @Coder 检查"},
    )
    await _wait_batch(factory, uuid.UUID(partial.json()["message"]["id"]))
    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.status == ConversationStatus.PARTIAL_FAILED

    for agent in agents[:2]:
        adapters[agent.id] = lambda: MockAdapter(
            MockAdapterScript(script=[MockScriptStep(action="delay", milliseconds=1000)])
        )
    pending = await client.post(
        f"/api/v1/projects/{project.id}/conversations/{conversation_id}/messages",
        json={"content": "@Code @Coder 等待"},
    )
    execution_ids = [uuid.UUID(item["id"]) for item in pending.json()["executions"]]
    for _ in range(100):
        async with factory() as session:
            statuses = list(
                await session.scalars(
                    select(AgentExecution.status).where(AgentExecution.id.in_(execution_ids))
                )
            )
        if len(statuses) == 2 and all(item == ExecutionStatus.RUNNING for item in statuses):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("group executions did not enter running state before cancellation")
    cancelled = await client.post(
        f"/api/v1/projects/{project.id}/executions/{execution_ids[0]}/cancel"
    )
    assert cancelled.status_code == 200
    executions = await _wait_batch(factory, uuid.UUID(pending.json()["message"]["id"]))
    assert {item.status for item in executions} == {ExecutionStatus.CANCELLED}


@pytest.mark.asyncio
async def test_two_group_conversations_keep_executions_and_messages_isolated(
    group_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        list[Agent],
        ChatService,
    ],
) -> None:
    """两个群聊并发提交时，执行和最终消息不得跨会话写入。"""
    client, factory, adapters, project, agents, _ = group_context
    adapters[agents[0].id] = lambda: MockAdapter(
        MockAdapterScript(script=[MockScriptStep(action="delta", content="Code reply")])
    )
    adapters[agents[1].id] = lambda: MockAdapter(
        MockAdapterScript(script=[MockScriptStep(action="delta", content="Coder reply")])
    )
    first_id = await _group(client, project, agents[:2])
    second_id = await _group(client, project, agents[:2])

    first, second = await asyncio.gather(
        client.post(
            f"/api/v1/projects/{project.id}/conversations/{first_id}/messages",
            json={"content": "@Code first"},
        ),
        client.post(
            f"/api/v1/projects/{project.id}/conversations/{second_id}/messages",
            json={"content": "@Coder second"},
        ),
    )
    assert first.status_code == second.status_code == 202
    first_message_id = uuid.UUID(first.json()["message"]["id"])
    second_message_id = uuid.UUID(second.json()["message"]["id"])
    await asyncio.gather(
        _wait_batch(factory, first_message_id),
        _wait_batch(factory, second_message_id),
    )

    async with factory() as session:
        replies = list(
            await session.scalars(
                select(Message).where(
                    Message.parent_message_id.in_([first_message_id, second_message_id])
                )
            )
        )
    assert {(item.conversation_id, item.agent_id, item.content) for item in replies} == {
        (first_id, agents[0].id, "Code reply"),
        (second_id, agents[1].id, "Coder reply"),
    }
