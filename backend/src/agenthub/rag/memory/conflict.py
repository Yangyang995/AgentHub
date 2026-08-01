"""冲突解决模块——三级策略处理偏好冲突。

场景对应策略：
- 事实更新（"数据库从 MySQL → PG"）→ 覆盖旧值，记录 previous_version_id
- 偏好变化（"代码风格 OOP → FP"）→ 保留旧记录 + 改标签为 deprecated，新建当前值
- 矛盾信息（同时有"用 React"和"用 Vue"）→ 双留 + conflict_flag=True，等待用户确认

所有操作均为软状态变更，不硬删除。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.models.orm import UserPreference


class ConflictResolver:
    """冲突检测与解决器。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self,
        project_id: uuid.UUID,
        new_pref: UserPreference,
    ) -> UserPreference:
        """检测冲突并执行对应策略。

        Args:
            project_id: 项目 ID。
            new_pref: 新偏好条目（尚未持久化）。

        Returns:
            最终持久化的偏好条目。
        """
        # 查找同 key 的活跃记录
        existing = await self._session.scalar(
            select(UserPreference).where(
                UserPreference.project_id == project_id,
                UserPreference.key == new_pref.key,
                UserPreference.is_active == True,  # noqa: E712
                UserPreference.conflict_flag == False,  # noqa: E712
            )
        )

        if existing is None:
            # 无冲突，直接写入
            self._session.add(new_pref)
            await self._session.flush()
            return new_pref

        # 值相同：无需处理
        if existing.value.strip().lower() == new_pref.value.strip().lower():
            return existing

        # 判断冲突类型
        if new_pref.category == "decision":
            # 决策类：事实更新 → 覆盖
            return await self._fact_update(existing, new_pref)
        elif new_pref.category == "preference":
            # 偏好类：保留旧记录 + 新建
            return await self._preference_change(existing, new_pref)
        else:
            # 知识类：矛盾信息 → 双留 + 标记
            return await self._contradiction_mark(existing, new_pref)

    async def _fact_update(
        self, old: UserPreference, new: UserPreference
    ) -> UserPreference:
        """事实更新：覆盖旧值，记录版本链。"""
        # 旧记录标记为非活跃
        old.is_active = False
        old.updated_at = datetime.now(UTC)
        # 新记录指向旧版本
        new.previous_version_id = old.id
        new.created_at = datetime.now(UTC)
        new.updated_at = datetime.now(UTC)
        self._session.add(new)
        await self._session.flush()
        return new

    async def _preference_change(
        self, old: UserPreference, new: UserPreference
    ) -> UserPreference:
        """偏好变化：保留旧记录，改标签为 deprecated，新建当前值。"""
        # 旧记录改标签（不改变 is_active，让其自然衰减）
        old.is_active = False
        old.updated_at = datetime.now(UTC)
        # 新记录
        new.previous_version_id = old.id
        new.created_at = datetime.now(UTC)
        new.updated_at = datetime.now(UTC)
        self._session.add(new)
        await self._session.flush()
        return new

    async def _contradiction_mark(
        self, old: UserPreference, new: UserPreference
    ) -> UserPreference:
        """矛盾信息：双留 + 冲突标记。"""
        # 旧记录标记冲突
        old.conflict_flag = True
        old.updated_at = datetime.now(UTC)
        # 新记录也标记冲突
        new.conflict_flag = True
        new.previous_version_id = old.id
        new.created_at = datetime.now(UTC)
        new.updated_at = datetime.now(UTC)
        self._session.add(new)
        await self._session.flush()
        return new

    async def list_conflicts(self, project_id: uuid.UUID) -> list[UserPreference]:
        """列出项目中存在冲突标记的偏好。"""
        result = await self._session.execute(
            select(UserPreference).where(
                UserPreference.project_id == project_id,
                UserPreference.conflict_flag == True,  # noqa: E712
                UserPreference.is_active == True,  # noqa: E712
            )
        )
        return list(result.scalars().all())

    async def resolve_conflict_manually(
        self, preference_id: uuid.UUID, keep: bool
    ) -> bool:
        """手动解决冲突——keep=True 保留，keep=False 软删除。"""
        pref = await self._session.get(UserPreference, preference_id)
        if pref is None or not pref.conflict_flag:
            return False
        if keep:
            pref.conflict_flag = False
        else:
            pref.is_active = False
        pref.updated_at = datetime.now(UTC)
        await self._session.flush()
        return True