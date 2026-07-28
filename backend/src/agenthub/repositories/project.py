"""项目仓储——项目级数据访问，不直接返回 ORM 实例。"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.models.orm import Project
from agenthub.schemas.domain import (
    PaginatedResponse,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
)


class ProjectRepository:
    """项目仓储。

    所有方法接收 AsyncSession 但不负责提交事务——
    事务边界由服务层通过 try/commit/rollback 显式控制。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: ProjectCreate) -> ProjectResponse:
        """创建项目。"""
        project = Project(name=data.name, root_path=data.root_path, description=data.description)
        self._session.add(project)
        await self._session.flush()
        return ProjectResponse.model_validate(project)

    async def get_by_id(self, project_id: uuid.UUID) -> ProjectResponse | None:
        """按 ID 查询项目。"""
        project = await self._session.get(Project, project_id)
        if project is None:
            return None
        return ProjectResponse.model_validate(project)

    async def get_by_name(self, name: str) -> ProjectResponse | None:
        """按名称查询项目。"""
        result = await self._session.execute(select(Project).where(Project.name == name))
        project = result.scalar_one_or_none()
        if project is None:
            return None
        return ProjectResponse.model_validate(project)

    async def list_projects(self, page: int = 1, page_size: int = 20) -> PaginatedResponse:
        """分页列出项目。"""
        count_query = select(Project.id)
        total_result = await self._session.execute(count_query)
        total = len(total_result.scalars().all())

        query = (
            select(Project)
            .order_by(Project.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self._session.execute(query)
        projects = result.scalars().all()

        return PaginatedResponse(
            items=[ProjectResponse.model_validate(p) for p in projects],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def update(self, project_id: uuid.UUID, data: ProjectUpdate) -> ProjectResponse | None:
        """更新项目。"""
        project = await self._session.get(Project, project_id)
        if project is None:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(project, key, value)
        await self._session.flush()
        return ProjectResponse.model_validate(project)

    async def delete(self, project_id: uuid.UUID) -> bool:
        """删除项目。"""
        project = await self._session.get(Project, project_id)
        if project is None:
            return False
        await self._session.delete(project)
        await self._session.flush()
        return True
