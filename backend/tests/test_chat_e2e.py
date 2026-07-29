"""Phase 4 单聊后端端到端测试，使用真实 PostgreSQL 和确定性 Mock Adapter。"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agenthub.adapters import MockAdapter, MockAdapterScript, MockScriptStep
from agenthub.adapters.protocol import AgentEvent, AgentTask
from agenthub.core.config import Settings
from agenthub.main import create_app
from agenthub.models.enums import AgentStatus, AgentType, ExecutionStatus
from agenthub.models.orm import Agent, AgentExecution, ExecutionEvent, Project
from agenthub.services.chat import AgentAdapter, ChatService

TEST_DATABASE_URL = "postgresql+asyncpg://agenthub:123456@localhost:5432/agenthub_test"


class RaisingAdapter:
    """模拟包含敏感诊断的未预期异常，服务层不得持久化原文。"""

    def run(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        async def raise_error() -> AsyncIterator[AgentEvent]:
            if False:
                yield
            raise RuntimeError("secret-token and C:\\private\\workspace")

        return raise_error()

    def cancel(self, execution_id: uuid.UUID) -> None:
        return None


class CapturingAdapter:
    """记录服务层构造的任务，再委托确定性 Mock 产生回复。"""

    def __init__(self, captured_tasks: list[AgentTask], reply: str) -> None:
        self._captured_tasks = captured_tasks
        self._delegate = MockAdapter(
            MockAdapterScript(script=[MockScriptStep(action="delta", content=reply)])
        )

    def run(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        self._captured_tasks.append(task)
        return self._delegate.run(task)

    def cancel(self, execution_id: uuid.UUID) -> None:
        self._delegate.cancel(execution_id)


@pytest_asyncio.fixture
async def chat_context(
    db_engine: AsyncEngine,
) -> AsyncIterator[
    tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        Agent,
        ChatService,
    ]
]:
    """创建可由测试逐 Agent 配置脚本的完整 ASGI 应用。"""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    project = Project(name=f"chat-{uuid.uuid4()}", root_path="C:\\workspace\\chat")
    async with factory() as session, session.begin():
        session.add(project)
        await session.flush()
        agent = Agent(
            project_id=project.id,
            name="Mock Chat Agent",
            agent_type=AgentType.MOCK,
            status=AgentStatus.ENABLED,
        )
        session.add(agent)
        await session.flush()

    adapter_factories: dict[uuid.UUID, Callable[[], AgentAdapter]] = {}

    def resolve_adapter(selected: Agent) -> AgentAdapter:
        factory_function = adapter_factories.get(selected.id)
        if factory_function is None:
            return MockAdapter(MockAdapterScript())
        return factory_function()

    app = create_app(
        Settings(environment="test", _env_file=None),  # type: ignore[call-arg]
        session_factory=factory,
        adapter_resolver=resolve_adapter,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        service: ChatService = app.state.chat_service
        yield client, factory, adapter_factories, project, agent, service

    async with factory() as session, session.begin():
        await session.execute(delete(Project).where(Project.id == project.id))


async def _create_conversation(
    client: AsyncClient, project_id: uuid.UUID, agent_id: uuid.UUID, title: str = "Chat"
) -> uuid.UUID:
    response = await client.post(
        f"/api/v1/projects/{project_id}/conversations",
        json={"title": title, "agent_id": str(agent_id)},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def _submit(
    client: AsyncClient, project_id: uuid.UUID, conversation_id: uuid.UUID, content: str
) -> uuid.UUID:
    response = await client.post(
        f"/api/v1/projects/{project_id}/conversations/{conversation_id}/messages",
        json={"content": content},
    )
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["execution"]["id"])


async def _wait_for_status(
    factory: async_sessionmaker[AsyncSession],
    execution_id: uuid.UUID,
    expected: ExecutionStatus,
) -> AgentExecution:
    """轮询已提交状态，超时表示后台执行没有完成而非放宽断言。"""
    for _ in range(100):
        async with factory() as session:
            execution = await session.get(AgentExecution, execution_id)
            if execution is not None and execution.status == expected:
                return execution
        await asyncio.sleep(0.01)
    pytest.fail(f"execution {execution_id} did not reach {expected}")


@pytest.mark.asyncio
async def test_segmented_reply_persists_complete_agent_message_and_stable_events(
    chat_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        Agent,
        ChatService,
    ],
) -> None:
    client, factory, adapters, project, agent, _ = chat_context
    adapters[agent.id] = lambda: MockAdapter(
        MockAdapterScript(
            script=[
                MockScriptStep(action="delta", content="Hello "),
                MockScriptStep(action="delta", content="world"),
            ]
        )
    )
    conversation_id = await _create_conversation(client, project.id, agent.id)
    execution_id = await _submit(client, project.id, conversation_id, "Say hello")
    await _wait_for_status(factory, execution_id, ExecutionStatus.SUCCEEDED)

    response = await client.get(
        f"/api/v1/projects/{project.id}/conversations/{conversation_id}/messages"
    )
    assert response.status_code == 200
    messages = response.json()
    assert [(item["role"], item["content"]) for item in messages] == [
        ("user", "Say hello"),
        ("agent", "Hello world"),
    ]
    assert [item["sequence"] for item in messages] == [0, 1]

    async with factory() as session:
        events = list(
            await session.scalars(
                select(ExecutionEvent)
                .where(ExecutionEvent.execution_id == execution_id)
                .order_by(ExecutionEvent.sequence)
            )
        )
    assert [event.sequence for event in events] == [0, 1, 2, 3]
    assert len({event.event_id for event in events}) == len(events)
    assert events[-1].event_type == "execution.status"
    assert events[-1].payload["status"] == "succeeded"


@pytest.mark.asyncio
async def test_execution_passes_ordered_conversation_history_to_adapter(
    chat_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        Agent,
        ChatService,
    ],
) -> None:
    """第二轮执行应携带已持久化的 user/assistant/user 历史。"""
    client, factory, adapters, project, agent, _ = chat_context
    captured_tasks: list[AgentTask] = []
    replies = iter(["第一次回答", "第二次回答"])
    adapters[agent.id] = lambda: CapturingAdapter(captured_tasks, next(replies))
    conversation_id = await _create_conversation(client, project.id, agent.id)

    first_execution = await _submit(client, project.id, conversation_id, "第一次提问")
    await _wait_for_status(factory, first_execution, ExecutionStatus.SUCCEEDED)
    second_execution = await _submit(client, project.id, conversation_id, "第二次提问")
    await _wait_for_status(factory, second_execution, ExecutionStatus.SUCCEEDED)

    assert captured_tasks[1].context["messages"] == [
        {"role": "user", "content": "第一次提问"},
        {"role": "assistant", "content": "第一次回答"},
        {"role": "user", "content": "第二次提问"},
    ]


@pytest.mark.asyncio
async def test_replay_uses_exclusive_cursor_without_duplicates(
    chat_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        Agent,
        ChatService,
    ],
) -> None:
    client, factory, adapters, project, agent, service = chat_context
    adapters[agent.id] = lambda: MockAdapter(
        MockAdapterScript(
            script=[
                MockScriptStep(action="delta", content="a"),
                MockScriptStep(action="delta", content="b"),
            ]
        )
    )
    conversation_id = await _create_conversation(client, project.id, agent.id)
    execution_id = await _submit(client, project.id, conversation_id, "stream")
    await _wait_for_status(factory, execution_id, ExecutionStatus.SUCCEEDED)

    all_events = await service.replay_events(project.id, conversation_id, execution_id, -1)
    resumed = await service.replay_events(project.id, conversation_id, execution_id, 1)
    assert [event.sequence for event in all_events] == [0, 1, 2, 3]
    assert [event.sequence for event in resumed] == [2, 3]
    assert {event.event_id for event in all_events[:2]}.isdisjoint(
        event.event_id for event in resumed
    )


@pytest.mark.asyncio
async def test_cancel_persists_one_final_status(
    chat_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        Agent,
        ChatService,
    ],
) -> None:
    client, factory, adapters, project, agent, _ = chat_context
    adapters[agent.id] = lambda: MockAdapter(
        MockAdapterScript(script=[MockScriptStep(action="delay", milliseconds=1000)])
    )
    conversation_id = await _create_conversation(client, project.id, agent.id)
    execution_id = await _submit(client, project.id, conversation_id, "wait")
    await _wait_for_status(factory, execution_id, ExecutionStatus.RUNNING)

    response = await client.post(f"/api/v1/projects/{project.id}/executions/{execution_id}/cancel")
    assert response.status_code == 200
    assert response.json()["payload"]["status"] == "cancelled"
    await _wait_for_status(factory, execution_id, ExecutionStatus.CANCELLED)
    await asyncio.sleep(0.03)

    async with factory() as session:
        final_events = list(
            await session.scalars(
                select(ExecutionEvent).where(
                    ExecutionEvent.execution_id == execution_id,
                    ExecutionEvent.event_type == "execution.status",
                )
            )
        )
    terminal = [
        event
        for event in final_events
        if event.payload["status"] in {"succeeded", "failed", "cancelled"}
    ]
    assert len(terminal) == 1
    assert terminal[0].payload["status"] == "cancelled"


@pytest.mark.asyncio
async def test_adapter_exception_persists_only_safe_error(
    chat_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        Agent,
        ChatService,
    ],
) -> None:
    client, factory, adapters, project, agent, _ = chat_context
    adapters[agent.id] = RaisingAdapter
    conversation_id = await _create_conversation(client, project.id, agent.id)
    execution_id = await _submit(client, project.id, conversation_id, "fail safely")
    execution = await _wait_for_status(factory, execution_id, ExecutionStatus.FAILED)
    assert execution.error_code == "ADAPTER_INTERNAL_ERROR"
    assert execution.error_message == "Adapter execution failed"

    async with factory() as session:
        payloads = list(
            await session.scalars(
                select(ExecutionEvent.payload).where(ExecutionEvent.execution_id == execution_id)
            )
        )
    serialized = str(payloads)
    assert "secret-token" not in serialized
    assert "private" not in serialized


@pytest.mark.asyncio
async def test_missing_and_cross_project_resources_are_rejected(
    chat_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        Agent,
        ChatService,
    ],
) -> None:
    client, factory, _, project, agent, _ = chat_context
    other = Project(name=f"other-{uuid.uuid4()}", root_path="C:\\workspace\\other")
    async with factory() as session, session.begin():
        session.add(other)
        await session.flush()
    try:
        conversation_id = await _create_conversation(client, project.id, agent.id)
        missing = await client.get(f"/api/v1/projects/{project.id}/conversations/{uuid.uuid4()}")
        crossed = await client.get(f"/api/v1/projects/{other.id}/conversations/{conversation_id}")
        cross_agent = await client.post(
            f"/api/v1/projects/{other.id}/conversations",
            json={"agent_id": str(agent.id)},
        )
        assert missing.status_code == 404
        assert crossed.status_code == 404
        assert cross_agent.status_code == 404
    finally:
        async with factory() as session, session.begin():
            await session.execute(delete(Project).where(Project.id == other.id))


@pytest.mark.asyncio
async def test_conversation_title_agent_display_and_delete(
    chat_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        Agent,
        ChatService,
    ],
) -> None:
    client, factory, _, project, agent, _ = chat_context
    create = await client.post(
        f"/api/v1/projects/{project.id}/conversations",
        json={"title": "用户不应控制此标题", "agent_id": str(agent.id)},
    )
    assert create.status_code == 201
    conversation = create.json()
    conversation_id = uuid.UUID(conversation["id"])
    assert conversation["title"] == "新对话"
    assert conversation["agent_name"] == "Mock Chat Agent"
    assert conversation["agent_type"] == "mock"

    submit = await client.post(
        f"/api/v1/projects/{project.id}/conversations/{conversation_id}/messages",
        json={"content": "  ##   修复   登录   问题  "},
    )
    assert submit.status_code == 202
    await _wait_for_status(
        factory, uuid.UUID(submit.json()["execution"]["id"]), ExecutionStatus.SUCCEEDED
    )
    listed = await client.get(f"/api/v1/projects/{project.id}/conversations")
    updated = next(item for item in listed.json() if item["id"] == str(conversation_id))
    assert updated["title"] == "修复 登录 问题"

    deleted = await client.delete(f"/api/v1/projects/{project.id}/conversations/{conversation_id}")
    assert deleted.status_code == 204
    assert (
        await client.get(f"/api/v1/projects/{project.id}/conversations/{conversation_id}")
    ).status_code == 404


@pytest.mark.asyncio
async def test_active_conversation_cannot_be_deleted_until_cancelled(
    chat_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        Agent,
        ChatService,
    ],
) -> None:
    client, factory, adapters, project, agent, _ = chat_context
    adapters[agent.id] = lambda: MockAdapter(
        MockAdapterScript(script=[MockScriptStep(action="delay", milliseconds=1000)])
    )
    conversation_id = await _create_conversation(client, project.id, agent.id)
    execution_id = await _submit(client, project.id, conversation_id, "等待")
    await _wait_for_status(factory, execution_id, ExecutionStatus.RUNNING)
    deleted = await client.delete(f"/api/v1/projects/{project.id}/conversations/{conversation_id}")
    assert deleted.status_code == 409
    await client.post(f"/api/v1/projects/{project.id}/executions/{execution_id}/cancel")
    await _wait_for_status(factory, execution_id, ExecutionStatus.CANCELLED)


@pytest.mark.asyncio
async def test_two_conversations_never_share_stream_events(
    chat_context: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[uuid.UUID, Callable[[], AgentAdapter]],
        Project,
        Agent,
        ChatService,
    ],
) -> None:
    client, factory, adapters, project, agent, service = chat_context
    adapters[agent.id] = lambda: MockAdapter(
        MockAdapterScript(script=[MockScriptStep(action="delta", content="reply")])
    )
    first = await _create_conversation(client, project.id, agent.id, "First")
    second = await _create_conversation(client, project.id, agent.id, "Second")
    first_execution, second_execution = await asyncio.gather(
        _submit(client, project.id, first, "one"),
        _submit(client, project.id, second, "two"),
    )
    await asyncio.gather(
        _wait_for_status(factory, first_execution, ExecutionStatus.SUCCEEDED),
        _wait_for_status(factory, second_execution, ExecutionStatus.SUCCEEDED),
    )

    first_events, second_events = await asyncio.gather(
        service.replay_events(project.id, first, first_execution, -1),
        service.replay_events(project.id, second, second_execution, -1),
    )
    assert first_events and second_events
    assert {event.conversation_id for event in first_events} == {first}
    assert {event.execution_id for event in first_events} == {first_execution}
    assert {event.conversation_id for event in second_events} == {second}
    assert {event.execution_id for event in second_events} == {second_execution}
    assert {event.event_id for event in first_events}.isdisjoint(
        event.event_id for event in second_events
    )


def test_websocket_reconnect_replays_only_missing_events() -> None:
    """通过真实 WebSocket 路由验证信封和排他游标补发。"""

    async def seed() -> tuple[uuid.UUID, uuid.UUID]:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(TEST_DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        project = Project(name=f"ws-{uuid.uuid4()}", root_path="C:\\workspace\\ws")
        async with factory() as session, session.begin():
            session.add(project)
            await session.flush()
            agent = Agent(
                project_id=project.id,
                name="WebSocket Agent",
                agent_type=AgentType.MOCK,
                status=AgentStatus.ENABLED,
            )
            session.add(agent)
            await session.flush()
            ids = project.id, agent.id
        await engine.dispose()
        return ids

    async def cleanup(project_id: uuid.UUID) -> None:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(TEST_DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session, session.begin():
            await session.execute(delete(Project).where(Project.id == project_id))
        await engine.dispose()

    project_id, agent_id = asyncio.run(seed())

    def resolve_adapter(agent: Agent) -> AgentAdapter:
        return MockAdapter(
            MockAdapterScript(
                script=[
                    MockScriptStep(action="delta", content="a"),
                    MockScriptStep(action="delta", content="b"),
                ]
            )
        )

    app = create_app(
        Settings(  # type: ignore[call-arg]
            environment="test", database_url=SecretStr(TEST_DATABASE_URL), _env_file=None
        ),
        adapter_resolver=resolve_adapter,
    )
    try:
        with TestClient(app) as client:
            conversation = client.post(
                f"/api/v1/projects/{project_id}/conversations",
                json={"agent_id": str(agent_id)},
            )
            assert conversation.status_code == 201
            conversation_id = conversation.json()["id"]
            submission = client.post(
                f"/api/v1/projects/{project_id}/conversations/{conversation_id}/messages",
                json={"content": "stream"},
            )
            assert submission.status_code == 202
            execution_id = submission.json()["execution"]["id"]

            path = (
                f"/ws/conversations/{conversation_id}?project_id={project_id}"
                f"&execution_id={execution_id}&last_sequence=-1"
            )
            with client.websocket_connect(path) as websocket:
                first = [websocket.receive_json() for _ in range(4)]
            assert [event["sequence"] for event in first] == [0, 1, 2, 3]
            assert all(
                set(event)
                == {
                    "event_id",
                    "conversation_id",
                    "execution_id",
                    "sequence",
                    "type",
                    "timestamp",
                    "payload",
                }
                for event in first
            )

            resumed_path = (
                f"/ws/conversations/{conversation_id}?project_id={project_id}"
                f"&execution_id={execution_id}&last_sequence=1"
            )
            with client.websocket_connect(resumed_path) as websocket:
                resumed = [websocket.receive_json() for _ in range(2)]
            assert [event["sequence"] for event in resumed] == [2, 3]
            assert {event["event_id"] for event in first[:2]}.isdisjoint(
                event["event_id"] for event in resumed
            )
    finally:
        asyncio.run(cleanup(project_id))
