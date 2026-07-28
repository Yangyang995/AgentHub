"""模型约束、CRUD 和外键级联行为测试。

覆盖：外键引用完整性、唯一约束违反、自依赖拒绝、级联删除、UTC 时间。
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.models.enums import (
    AgentType,
    MessageRole,
)
from agenthub.models.orm import (
    Agent,
    AgentExecution,
    Conversation,
    Message,
    Project,
    Task,
    TaskDependency,
)


async def _create_project(session: AsyncSession, name: str = "test-project") -> Project:
    """辅助函数：创建测试项目。"""
    project = Project(name=name, root_path=f"/tmp/{name}")
    session.add(project)
    await session.flush()
    return project


# ── 外键引用完整性 ─────────────────────────────────────────────────────────


class TestForeignKeyIntegrity:
    """外键引用完整性：引用不存在的记录应失败。"""

    async def test_agent_requires_valid_project(self, db_session: AsyncSession) -> None:
        agent = Agent(
            project_id=uuid.uuid4(),  # 不存在的项目 ID
            name="orphan-agent",
            agent_type=AgentType.MOCK,
        )
        db_session.add(agent)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_message_requires_valid_conversation(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        msg = Message(
            conversation_id=uuid.uuid4(),  # 不存在的会话
            project_id=project.id,
            role=MessageRole.USER,
            content="hello",
            sequence=1,
        )
        db_session.add(msg)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_execution_requires_valid_agent(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        agent = Agent(project_id=project.id, name="test-agent", agent_type=AgentType.MOCK)
        db_session.add(agent)
        conv = Conversation(project_id=project.id)
        db_session.add(conv)
        await db_session.flush()
        msg = Message(
            conversation_id=conv.id,
            project_id=project.id,
            role=MessageRole.USER,
            content="hello",
            sequence=1,
        )
        db_session.add(msg)
        await db_session.flush()

        execution = AgentExecution(
            project_id=project.id,
            message_id=msg.id,
            agent_id=uuid.uuid4(),  # 不存在的 Agent
            conversation_id=conv.id,
        )
        db_session.add(execution)
        with pytest.raises(IntegrityError):
            await db_session.flush()


# ── 唯一约束 ───────────────────────────────────────────────────────────────


class TestUniqueConstraints:
    """唯一约束违反应抛出 IntegrityError。"""

    async def test_project_name_unique(self, db_session: AsyncSession) -> None:
        await _create_project(db_session, name="dup")
        project2 = Project(name="dup", root_path="/tmp/dup2")
        db_session.add(project2)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_agent_name_per_project_unique(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        agent1 = Agent(project_id=project.id, name="same-name", agent_type=AgentType.MOCK)
        db_session.add(agent1)
        await db_session.flush()

        agent2 = Agent(project_id=project.id, name="same-name", agent_type=AgentType.CODEX_CLI)
        db_session.add(agent2)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_agent_same_name_different_project_ok(self, db_session: AsyncSession) -> None:
        """不同项目下同名的 Agent 应被允许。"""
        p1 = await _create_project(db_session, name="p1")
        p2 = await _create_project(db_session, name="p2")
        agent1 = Agent(project_id=p1.id, name="shared-name", agent_type=AgentType.MOCK)
        agent2 = Agent(project_id=p2.id, name="shared-name", agent_type=AgentType.MOCK)
        db_session.add_all([agent1, agent2])
        await db_session.flush()  # 不应抛异常

    async def test_message_sequence_unique_per_conversation(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        conv = Conversation(project_id=project.id)
        db_session.add(conv)
        await db_session.flush()

        msg1 = Message(
            conversation_id=conv.id,
            project_id=project.id,
            role=MessageRole.USER,
            content="first",
            sequence=1,
        )
        msg2 = Message(
            conversation_id=conv.id,
            project_id=project.id,
            role=MessageRole.USER,
            content="second",
            sequence=1,  # 相同 sequence
        )
        db_session.add_all([msg1, msg2])
        with pytest.raises(IntegrityError):
            await db_session.flush()


# ── 自依赖拒绝 ─────────────────────────────────────────────────────────────


class TestSelfDependency:
    """TaskDependency 自依赖必须被 CHECK 约束拒绝。"""

    async def test_self_dependency_rejected(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        task = Task(project_id=project.id, title="self-task")
        db_session.add(task)
        await db_session.flush()

        dep = TaskDependency(task_id=task.id, depends_on_task_id=task.id)
        db_session.add(dep)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_valid_dependency_accepted(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        t1 = Task(project_id=project.id, title="Task 1")
        t2 = Task(project_id=project.id, title="Task 2")
        db_session.add_all([t1, t2])
        await db_session.flush()

        dep = TaskDependency(task_id=t2.id, depends_on_task_id=t1.id)
        db_session.add(dep)
        await db_session.flush()  # 不应抛异常


# ── 级联删除 ───────────────────────────────────────────────────────────────


class TestCascadeDelete:
    """级联删除行为验证。"""

    async def test_delete_project_cascades_to_agent(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        agent = Agent(project_id=project.id, name="cascade-agent", agent_type=AgentType.MOCK)
        db_session.add(agent)
        await db_session.flush()

        await db_session.delete(project)
        await db_session.flush()

        result = await db_session.execute(select(Agent).where(Agent.id == agent.id))
        assert result.scalar_one_or_none() is None

    async def test_delete_project_cascades_to_conversation(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        conv = Conversation(project_id=project.id)
        db_session.add(conv)
        await db_session.flush()

        await db_session.delete(project)
        await db_session.flush()

        result = await db_session.execute(select(Conversation).where(Conversation.id == conv.id))
        assert result.scalar_one_or_none() is None

    async def test_delete_conversation_cascades_to_messages(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        conv = Conversation(project_id=project.id)
        db_session.add(conv)
        await db_session.flush()

        msg = Message(
            conversation_id=conv.id,
            project_id=project.id,
            role=MessageRole.USER,
            content="cascade me",
            sequence=1,
        )
        db_session.add(msg)
        await db_session.flush()

        await db_session.delete(conv)
        await db_session.flush()

        result = await db_session.execute(select(Message).where(Message.id == msg.id))
        assert result.scalar_one_or_none() is None

    async def test_delete_agent_restricts_when_executions_exist(
        self, db_session: AsyncSession
    ) -> None:
        """Agent 有执行记录时不应被删除（RESTRICT）。"""
        project = await _create_project(db_session)
        agent = Agent(project_id=project.id, name="protect-me", agent_type=AgentType.MOCK)
        conv = Conversation(project_id=project.id)
        db_session.add_all([agent, conv])
        await db_session.flush()

        msg = Message(
            conversation_id=conv.id,
            project_id=project.id,
            role=MessageRole.USER,
            content="trigger",
            sequence=1,
        )
        db_session.add(msg)
        await db_session.flush()

        execution = AgentExecution(
            project_id=project.id,
            message_id=msg.id,
            agent_id=agent.id,
            conversation_id=conv.id,
        )
        db_session.add(execution)
        await db_session.flush()

        # 尝试删除有执行记录的 Agent
        await db_session.delete(agent)
        with pytest.raises(IntegrityError):
            await db_session.flush()


# ── UTC 时间 ───────────────────────────────────────────────────────────────


class TestUtcTimestamps:
    """所有模型的 created_at 必须是 UTC aware。"""

    async def test_project_utc(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        assert project.created_at.tzinfo is not None
        assert project.updated_at.tzinfo is not None

    async def test_agent_utc(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        agent = Agent(project_id=project.id, name="utc-agent", agent_type=AgentType.MOCK)
        db_session.add(agent)
        await db_session.flush()
        assert agent.created_at.tzinfo is not None

    async def test_message_utc(self, db_session: AsyncSession) -> None:
        project = await _create_project(db_session)
        conv = Conversation(project_id=project.id)
        db_session.add(conv)
        await db_session.flush()
        msg = Message(
            conversation_id=conv.id,
            project_id=project.id,
            role=MessageRole.USER,
            content="utc",
            sequence=1,
        )
        db_session.add(msg)
        await db_session.flush()
        assert msg.created_at.tzinfo is not None


# ── Project Isolation ──────────────────────────────────────────────────────


class TestProjectIsolation:
    """project_id 隔离验证：跨项目的记录不应互相可见。"""

    async def test_cross_project_messages_isolated(self, db_session: AsyncSession) -> None:
        p1 = await _create_project(db_session, name="isolation-p1")
        p2 = await _create_project(db_session, name="isolation-p2")

        # 每个项目创建会话和消息
        c1 = Conversation(project_id=p1.id)
        c2 = Conversation(project_id=p2.id)
        db_session.add_all([c1, c2])
        await db_session.flush()

        msg1 = Message(
            conversation_id=c1.id,
            project_id=p1.id,
            role=MessageRole.USER,
            content="p1 msg",
            sequence=1,
        )
        msg2 = Message(
            conversation_id=c2.id,
            project_id=p2.id,
            role=MessageRole.USER,
            content="p2 msg",
            sequence=1,
        )
        db_session.add_all([msg1, msg2])
        await db_session.flush()

        # 按 project_id 过滤只应看到对应项目的消息
        result = await db_session.execute(select(Message).where(Message.project_id == p1.id))
        p1_messages = result.scalars().all()
        assert len(p1_messages) == 1
        assert p1_messages[0].content == "p1 msg"

        result = await db_session.execute(select(Message).where(Message.project_id == p2.id))
        p2_messages = result.scalars().all()
        assert len(p2_messages) == 1
        assert p2_messages[0].content == "p2 msg"
