"""Repository 层测试。

验证 ProjectRepository 的 CRUD 操作、返回 Pydantic Schema（非 ORM 实例）、
事务回滚行为和 project_id 隔离。
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.models.orm import Project
from agenthub.repositories.project import ProjectRepository
from agenthub.schemas.domain import ProjectCreate, ProjectResponse, ProjectUpdate

# ── 辅助函数 ────────────────────────────────────────────────────────────────


def _make_repo(session: AsyncSession) -> ProjectRepository:
    """创建仓储实例。"""
    return ProjectRepository(session)


def _make_create(name: str = "repo-test", root_path: str = "/tmp/repo-test") -> ProjectCreate:
    """创建 ProjectCreate 测试数据。"""
    return ProjectCreate(name=name, root_path=root_path, description=None)


# ── 创建项目 ────────────────────────────────────────────────────────────────


class TestProjectCreate:
    """项目创建相关测试。"""

    async def test_create_returns_project_response(self, db_session: AsyncSession) -> None:
        """创建项目返回的应是 ProjectResponse Pydantic Schema，不是 ORM 实例。"""
        repo = _make_repo(db_session)
        result = await repo.create(_make_create())
        assert isinstance(result, ProjectResponse)
        assert not isinstance(result, Project)
        assert result.name == "repo-test"

    async def test_create_persists_to_db(self, db_session: AsyncSession) -> None:
        """创建后应从数据库查到对应记录。"""
        repo = _make_repo(db_session)
        result = await repo.create(_make_create())
        row = await db_session.execute(select(Project).where(Project.id == result.id))
        project = row.scalar_one_or_none()
        assert project is not None
        assert project.name == "repo-test"

    async def test_create_duplicate_name_raises_integrity_error(
        self, db_session: AsyncSession
    ) -> None:
        """重复的项目名称应触发唯一约束违反。"""
        repo = _make_repo(db_session)
        await repo.create(_make_create(name="unique-name"))
        with pytest.raises(IntegrityError):
            await repo.create(_make_create(name="unique-name"))
            await db_session.flush()


# ── 按 ID 查询 ──────────────────────────────────────────────────────────────


class TestGetById:
    """按 ID 查询项目。"""

    async def test_get_by_id_returns_project_response(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        created = await repo.create(_make_create())
        result = await repo.get_by_id(created.id)
        assert isinstance(result, ProjectResponse)

    async def test_get_by_id_nonexistent_returns_none(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        result = await repo.get_by_id(uuid.uuid4())
        assert result is None


# ── 按名称查询 ──────────────────────────────────────────────────────────────


class TestGetByName:
    """按名称查询项目。"""

    async def test_get_by_name_returns_project_response(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        await repo.create(_make_create(name="find-me"))
        result = await repo.get_by_name("find-me")
        assert isinstance(result, ProjectResponse)
        assert result.name == "find-me"

    async def test_get_by_name_nonexistent_returns_none(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        result = await repo.get_by_name("no-such-project")
        assert result is None


# ── 分页列表 ────────────────────────────────────────────────────────────────


class TestListProjects:
    """分页列出项目。"""

    async def test_list_returns_empty_when_no_projects(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        result = await repo.list_projects()
        assert result.total == 0
        assert len(result.items) == 0

    async def test_list_returns_all_created(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        await repo.create(_make_create(name="p1"))
        await repo.create(_make_create(name="p2"))
        result = await repo.list_projects()
        assert result.total == 2
        assert len(result.items) == 2

    async def test_list_returns_pydantic_schemas(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        await repo.create(_make_create())
        result = await repo.list_projects()
        for item in result.items:
            assert isinstance(item, ProjectResponse)
            assert not isinstance(item, Project)

    async def test_list_pagination_respects_page_size(self, db_session: AsyncSession) -> None:
        """分页参数 page_size 应正确限制返回条数。"""
        repo = _make_repo(db_session)
        for i in range(5):
            await repo.create(_make_create(name=f"p{i}", root_path=f"/tmp/p{i}"))
        result = await repo.list_projects(page=1, page_size=2)
        assert result.total == 5
        assert len(result.items) == 2

    async def test_list_page_2_of_2(self, db_session: AsyncSession) -> None:
        """第二页应有正确数量的条数。"""
        repo = _make_repo(db_session)
        for i in range(5):
            await repo.create(_make_create(name=f"page-{i}", root_path=f"/tmp/page-{i}"))
        result = await repo.list_projects(page=2, page_size=2)
        assert len(result.items) == 2
        all_result = await repo.list_projects(page=1, page_size=10)
        expected_names = [p.name for p in all_result.items[2:4]]
        actual_names = [p.name for p in result.items]
        assert actual_names == expected_names


# ── 更新项目 ────────────────────────────────────────────────────────────────


class TestProjectUpdate:
    """更新项目。"""

    async def test_update_name(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        created = await repo.create(_make_create(name="before"))
        result = await repo.update(created.id, ProjectUpdate(name="after"))
        assert result is not None
        assert result.name == "after"

    async def test_update_returns_project_response(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        created = await repo.create(_make_create())
        result = await repo.update(created.id, ProjectUpdate(name="renamed"))
        assert isinstance(result, ProjectResponse)

    async def test_update_nonexistent_returns_none(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        result = await repo.update(uuid.uuid4(), ProjectUpdate(name="ghost"))
        assert result is None

    async def test_update_persists(self, db_session: AsyncSession) -> None:
        """更新后通过 get_by_id 应能看到变化。"""
        repo = _make_repo(db_session)
        created = await repo.create(_make_create(name="original"))
        await repo.update(created.id, ProjectUpdate(name="changed"))
        fetched = await repo.get_by_id(created.id)
        assert fetched is not None
        assert fetched.name == "changed"


# ── 删除项目 ────────────────────────────────────────────────────────────────


class TestProjectDelete:
    """删除项目。"""

    async def test_delete_existing_returns_true(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        created = await repo.create(_make_create())
        result = await repo.delete(created.id)
        assert result is True

    async def test_delete_nonexistent_returns_false(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        result = await repo.delete(uuid.uuid4())
        assert result is False

    async def test_delete_removes_from_db(self, db_session: AsyncSession) -> None:
        """删除后通过原始查询确认记录不存在。"""
        repo = _make_repo(db_session)
        created = await repo.create(_make_create())
        await repo.delete(created.id)
        row = await db_session.execute(select(Project).where(Project.id == created.id))
        assert row.scalar_one_or_none() is None


# ── 事务回滚行为 ────────────────────────────────────────────────────────────


class TestTransactionRollback:
    """Repository 只负责数据操作，事务提交由调用方控制。"""

    async def test_flush_does_not_commit(self, db_session: AsyncSession) -> None:
        """Repository flush 不会提交事务；数据在事务内可见但未持久化到数据库。"""
        import asyncpg

        repo = _make_repo(db_session)
        created = await repo.create(_make_create(name="rollback-me"))

        # 在同一事务内可以查到（via Repository）
        fetched = await repo.get_by_id(created.id)
        assert fetched is not None

        # 通过独立 asyncpg 连接验证数据未提交——另一个连接不应看到未提交的数据
        raw_conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            user="agenthub",
            password="123456",
            database="agenthub_test",
        )
        try:
            row = await raw_conn.fetchrow("SELECT id FROM projects WHERE id = $1", created.id)
            assert row is None, "Repository flush 不应提交事务——独立连接中不应看到数据"
        finally:
            await raw_conn.close()


# ── Repository 返回类型校验 ─────────────────────────────────────────────────


class TestReturnTypeIsolation:
    """Repository 方法绝不返回 ORM 实例。"""

    async def test_create_does_not_return_orm(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        result = await repo.create(_make_create())
        assert not isinstance(result, Project)

    async def test_get_by_id_does_not_return_orm(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        created = await repo.create(_make_create())
        result = await repo.get_by_id(created.id)
        assert not isinstance(result, Project)

    async def test_get_by_name_does_not_return_orm(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        await repo.create(_make_create(name="type-check"))
        result = await repo.get_by_name("type-check")
        assert not isinstance(result, Project)

    async def test_list_does_not_return_orm(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        await repo.create(_make_create())
        result = await repo.list_projects()
        for item in result.items:
            assert not isinstance(item, Project)

    async def test_update_does_not_return_orm(self, db_session: AsyncSession) -> None:
        repo = _make_repo(db_session)
        created = await repo.create(_make_create())
        result = await repo.update(created.id, ProjectUpdate(name="still-schema"))
        assert not isinstance(result, Project)
