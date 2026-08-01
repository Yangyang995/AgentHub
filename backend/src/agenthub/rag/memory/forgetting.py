"""遗忘策略模块——自然衰减 + 180天归档 + 软删除。

- 自然衰减：ORDER BY 中乘以 0.5^(age_days/90)，长期不用的自动沉底
- 180 天归档：is_active = False（决策类偏好永不归档）
- 软删除：仅 is_active = False，可恢复
- 硬删除仅用户显式操作时执行
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.models.orm import UserPreference

# 归档阈值（天）
_ARCHIVE_THRESHOLD_DAYS = 180
# 决策类类别——永不归档
_IMMORTAL_CATEGORIES = frozenset({"decision"})


class ForgettingManager:
    """遗忘管理器——衰减、归档、清理。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def archive_stale(self, project_id: uuid.UUID) -> int:
        """归档超过 180 天未更新的非决策类偏好。

        Returns:
            归档的条目数。
        """
        cutoff = datetime.now(UTC) - timedelta(days=_ARCHIVE_THRESHOLD_DAYS)
        result = await self._session.execute(
            update(UserPreference)
            .where(
                UserPreference.project_id == project_id,
                UserPreference.is_active == True,  # noqa: E712
                UserPreference.category.not_in(_IMMORTAL_CATEGORIES),
                UserPreference.updated_at < cutoff,
            )
            .values(is_active=False, updated_at=datetime.now(UTC))
        )
        await self._session.flush()
        return result.rowcount or 0

    async def restore(self, preference_id: uuid.UUID) -> bool:
        """恢复被归档的偏好。"""
        pref = await self._session.get(UserPreference, preference_id)
        if pref is None:
            return False
        pref.is_active = True
        pref.updated_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def hard_delete(self, preference_id: uuid.UUID) -> bool:
        """硬删除——仅用户显式操作时调用。"""
        pref = await self._session.get(UserPreference, preference_id)
        if pref is None:
            return False
        await self._session.delete(pref)
        await self._session.flush()
        return True