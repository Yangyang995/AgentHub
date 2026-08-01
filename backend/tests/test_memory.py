"""记忆模块测试——冲突解决和遗忘策略。"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from agenthub.models.orm import Project, UserPreference
from agenthub.rag.memory.conflict import ConflictResolver
from agenthub.rag.memory.forgetting import ForgettingManager


async def _create_project(session) -> Project:
    """创建测试用项目。"""
    project = Project(
        id=uuid.uuid4(),
        name="test-project-{0}".format(uuid.uuid4().hex[:8]),
        root_path="/tmp/test",
    )
    session.add(project)
    await session.flush()
    return project


def _make_pref(project_id: uuid.UUID, **overrides) -> UserPreference:
    """创建测试用 UserPreference。"""
    defaults = {
        "id": uuid.uuid4(),
        "project_id": project_id,
        "category": "preference",
        "key": "test_key",
        "value": "test_value",
        "importance": 0.5,
        "is_active": True,
        "conflict_flag": False,
        "previous_version_id": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "last_accessed_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return UserPreference(**defaults)


@pytest.mark.asyncio
class TestConflictResolution:
    async def test_no_conflict_direct_insert(self, db_session):
        """无冲突时直接写入。"""
        project = await _create_project(db_session)
        resolver = ConflictResolver(db_session)
        pref = _make_pref(project.id)
        result = await resolver.resolve(project.id, pref)
        assert result.id == pref.id
        assert result.is_active

    async def test_fact_update_overwrites(self, db_session):
        """事实更新：决策类覆盖旧值。"""
        project = await _create_project(db_session)
        old = _make_pref(
            project.id, category="decision",
            key="database", value="MySQL",
        )
        db_session.add(old)
        await db_session.flush()

        new = _make_pref(
            project.id, category="decision",
            key="database", value="PostgreSQL",
        )
        resolver = ConflictResolver(db_session)
        result = await resolver.resolve(project.id, new)
        assert result.value == "PostgreSQL"
        assert result.previous_version_id == old.id
        await db_session.refresh(old)
        assert not old.is_active

    async def test_preference_change_keeps_history(self, db_session):
        """偏好变化：旧记录改为不活跃，新建当前值。"""
        project = await _create_project(db_session)
        old = _make_pref(
            project.id, category="preference",
            key="code_style", value="OOP",
        )
        db_session.add(old)
        await db_session.flush()

        new = _make_pref(
            project.id, category="preference",
            key="code_style", value="FP",
        )
        resolver = ConflictResolver(db_session)
        result = await resolver.resolve(project.id, new)
        assert result.value == "FP"
        await db_session.refresh(old)
        assert not old.is_active

    async def test_contradiction_both_kept_with_flag(self, db_session):
        """矛盾信息双留并标记。"""
        project = await _create_project(db_session)
        old = _make_pref(
            project.id, category="knowledge",
            key="framework", value="React",
        )
        db_session.add(old)
        await db_session.flush()

        new = _make_pref(
            project.id, category="knowledge",
            key="framework", value="Vue",
        )
        resolver = ConflictResolver(db_session)
        result = await resolver.resolve(project.id, new)
        assert result.conflict_flag
        await db_session.refresh(old)
        assert old.conflict_flag

    async def test_same_value_no_change(self, db_session):
        """相同值不产生冲突。"""
        project = await _create_project(db_session)
        old = _make_pref(
            project.id, category="preference",
            key="editor", value="VSCode",
        )
        db_session.add(old)
        await db_session.flush()

        new = _make_pref(
            project.id, category="preference",
            key="editor", value="VSCode",
        )
        resolver = ConflictResolver(db_session)
        result = await resolver.resolve(project.id, new)
        assert result.id == old.id

    async def test_manual_resolve_keep(self, db_session):
        """手动解决冲突——保留。"""
        project = await _create_project(db_session)
        pref = _make_pref(project.id, conflict_flag=True)
        db_session.add(pref)
        await db_session.flush()

        resolver = ConflictResolver(db_session)
        resolved = await resolver.resolve_conflict_manually(pref.id, True)
        assert resolved
        assert not pref.conflict_flag
        assert pref.is_active

    async def test_manual_resolve_delete(self, db_session):
        """手动解决冲突——删除。"""
        project = await _create_project(db_session)
        pref = _make_pref(project.id, conflict_flag=True)
        db_session.add(pref)
        await db_session.flush()

        resolver = ConflictResolver(db_session)
        resolved = await resolver.resolve_conflict_manually(pref.id, False)
        assert resolved
        assert not pref.is_active


@pytest.mark.asyncio
class TestForgetting:
    async def test_archive_stale(self, db_session):
        """超过 180 天的非决策偏好被归档。"""
        project = await _create_project(db_session)
        stale_date = datetime.now(UTC) - timedelta(days=200)
        pref = _make_pref(
            project.id, category="preference",
            updated_at=stale_date, created_at=stale_date,
        )
        db_session.add(pref)
        await db_session.flush()

        mgr = ForgettingManager(db_session)
        count = await mgr.archive_stale(project.id)
        assert count >= 1
        await db_session.refresh(pref)
        assert not pref.is_active

    async def test_decision_never_archived(self, db_session):
        """决策类永不归档。"""
        project = await _create_project(db_session)
        stale_date = datetime.now(UTC) - timedelta(days=200)
        pref = _make_pref(
            project.id, category="decision",
            updated_at=stale_date, created_at=stale_date,
        )
        db_session.add(pref)
        await db_session.flush()

        mgr = ForgettingManager(db_session)
        count = await mgr.archive_stale(project.id)
        await db_session.refresh(pref)
        assert pref.is_active

    async def test_restore_preference(self, db_session):
        """恢复已归档偏好。"""
        project = await _create_project(db_session)
        pref = _make_pref(project.id, is_active=False)
        db_session.add(pref)
        await db_session.flush()

        mgr = ForgettingManager(db_session)
        restored = await mgr.restore(pref.id)
        assert restored
        assert pref.is_active

    async def test_hard_delete(self, db_session):
        """硬删除不可恢复。"""
        project = await _create_project(db_session)
        pref = _make_pref(project.id)
        db_session.add(pref)
        await db_session.flush()

        mgr = ForgettingManager(db_session)
        deleted = await mgr.hard_delete(pref.id)
        assert deleted
        result = await db_session.get(UserPreference, pref.id)
        assert result is None

    async def test_soft_delete_recoverable(self, db_session):
        """软删除后可恢复。"""
        project = await _create_project(db_session)
        pref = _make_pref(project.id, is_active=False)
        db_session.add(pref)
        await db_session.flush()

        mgr = ForgettingManager(db_session)
        restored = await mgr.restore(pref.id)
        assert restored
        assert pref.is_active