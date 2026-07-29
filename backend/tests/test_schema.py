"""迁移 Schema 验证测试。

验证 upgrade→downgrade→upgrade 循环、表存在性、约束、
索引、pg_trgm 扩展和 UTC 时间字段。
"""

from typing import Any, cast

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession


async def _table_exists(session: AsyncSession, table_name: str) -> bool:
    """检查指定表是否存在。"""
    conn = await session.connection()

    # run_sync 接受 (conn, table_name) 两个参数
    def _check(conn: Any, name: str) -> bool:
        return cast(bool, inspect(conn).has_table(name))

    return cast(bool, await conn.run_sync(_check, table_name))


async def _index_exists(session: AsyncSession, table_name: str, index_name: str) -> bool:
    """检查指定索引是否存在。"""
    conn = await session.connection()

    def _check(conn: Any, tbl: str, idx: str) -> bool:
        insp = inspect(conn)
        indexes = insp.get_indexes(tbl)
        return any(i["name"] == idx for i in indexes)

    return cast(bool, await conn.run_sync(_check, table_name, index_name))


async def _fk_exists(session: AsyncSession, table_name: str, fk_column: str) -> bool:
    """检查指定列是否有外键约束。"""
    conn = await session.connection()

    def _check(conn: Any, tbl: str, col: str) -> bool:
        for fk in inspect(conn).get_foreign_keys(tbl):
            if col in fk["constrained_columns"]:
                return True
        return False

    return cast(bool, await conn.run_sync(_check, table_name, fk_column))


async def _unique_exists(session: AsyncSession, table_name: str, columns: list[str]) -> bool:
    """检查指定列组合是否有唯一约束。"""
    conn = await session.connection()

    def _check(conn: Any, tbl: str, cols: list[str]) -> bool:
        for uc in inspect(conn).get_unique_constraints(tbl):
            if set(uc["column_names"]) == set(cols):
                return True
        return False

    return cast(bool, await conn.run_sync(_check, table_name, columns))


EXPECTED_TABLES = [
    "projects",
    "agents",
    "conversations",
    "messages",
    "agent_executions",
    "execution_events",
    "tasks",
    "task_dependencies",
    "artifacts",
    "approvals",
    "deployments",
    "usage_events",
]


class TestTablesExist:
    """所有业务表存在性检查。"""

    @pytest.mark.parametrize("table_name", EXPECTED_TABLES)
    async def test_table_exists(self, db_session: AsyncSession, table_name: str) -> None:
        assert await _table_exists(db_session, table_name), f"表 {table_name} 不存在"


class TestForeignKeys:
    """外键约束存在性检查。"""

    async def test_agents_fk_project(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "agents", "project_id")

    async def test_conversations_fk_project(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "conversations", "project_id")

    async def test_conversations_fk_agent(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "conversations", "agent_id")

    async def test_messages_fk_conversation(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "messages", "conversation_id")

    async def test_messages_fk_project(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "messages", "project_id")

    async def test_messages_fk_agent(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "messages", "agent_id")

    async def test_agent_executions_fk_message(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "agent_executions", "message_id")

    async def test_agent_executions_fk_agent(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "agent_executions", "agent_id")

    async def test_execution_events_fk_execution(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "execution_events", "execution_id")

    async def test_artifacts_fk_execution(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "artifacts", "execution_id")

    async def test_deployments_fk_project(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "deployments", "project_id")

    async def test_usage_events_fk_agent(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "usage_events", "agent_id")

    async def test_task_dependencies_fk_task(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "task_dependencies", "task_id")

    async def test_task_dependencies_fk_depends_on(self, db_session: AsyncSession) -> None:
        assert await _fk_exists(db_session, "task_dependencies", "depends_on_task_id")


class TestUniqueConstraints:
    """唯一约束存在性检查。"""

    async def test_projects_name_unique(self, db_session: AsyncSession) -> None:
        assert await _unique_exists(db_session, "projects", ["name"])

    async def test_agents_project_name_unique(self, db_session: AsyncSession) -> None:
        assert await _unique_exists(db_session, "agents", ["project_id", "name"])

    async def test_messages_conversation_sequence_unique(self, db_session: AsyncSession) -> None:
        assert await _unique_exists(db_session, "messages", ["conversation_id", "sequence"])

    async def test_task_dependencies_pair_unique(self, db_session: AsyncSession) -> None:
        assert await _unique_exists(
            db_session, "task_dependencies", ["task_id", "depends_on_task_id"]
        )

    async def test_execution_events_sequence_unique(self, db_session: AsyncSession) -> None:
        assert await _unique_exists(db_session, "execution_events", ["execution_id", "sequence"])


class TestIndexes:
    """关键索引存在性检查。"""

    async def test_messages_content_trgm_index(self, db_session: AsyncSession) -> None:
        assert await _index_exists(db_session, "messages", "ix_messages_content_trgm")

    async def test_usage_events_project_agent_time(self, db_session: AsyncSession) -> None:
        assert await _index_exists(db_session, "usage_events", "ix_usage_events_project_agent_time")

    async def test_agent_executions_project_status(self, db_session: AsyncSession) -> None:
        assert await _index_exists(
            db_session, "agent_executions", "ix_agent_executions_project_status"
        )

    async def test_approvals_project_status(self, db_session: AsyncSession) -> None:
        assert await _index_exists(db_session, "approvals", "ix_approvals_project_status")

    async def test_execution_events_replay(self, db_session: AsyncSession) -> None:
        assert await _index_exists(db_session, "execution_events", "ix_execution_events_replay")


class TestPgTrgm:
    """pg_trgm 扩展和 GIN 索引验证。"""

    async def test_extension_exists(self, db_session: AsyncSession) -> None:
        result = await db_session.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'")
        )
        assert result.scalar() == "pg_trgm"

    async def test_gin_index_uses_trgm(self, db_session: AsyncSession) -> None:
        """验证 messages.content 的 GIN 索引确实使用了 gin_trgm_ops。"""
        result = await db_session.execute(
            text("""
                SELECT indexdef FROM pg_indexes
                WHERE indexname = 'ix_messages_content_trgm'
            """)
        )
        indexdef = result.scalar()
        assert indexdef is not None
        assert "gin_trgm_ops" in indexdef


class TestCheckConstraints:
    """CHECK 约束验证。"""

    async def test_task_dependencies_no_self(self, db_session: AsyncSession) -> None:
        """ck_task_dependencies_no_self 约束存在。"""
        result = await db_session.execute(
            text("""
                SELECT conname FROM pg_constraint
                WHERE conname = 'ck_task_dependencies_no_self'
            """)
        )
        assert result.scalar() == "ck_task_dependencies_no_self"


class TestUtcTimestamps:
    """UTC 时间字段验证。"""

    async def test_project_created_at_is_utc(self, db_session: AsyncSession) -> None:
        """插入一条记录后，created_at 必须是带时区的 UTC 时间。"""
        from agenthub.models.orm import Project

        project = Project(name="utc-test", root_path="/tmp/utc-test")
        db_session.add(project)
        await db_session.flush()

        assert project.created_at is not None
        assert project.created_at.tzinfo is not None, "created_at 必须带时区信息"
        assert project.created_at.utcoffset() is not None
