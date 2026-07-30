"""项目注册与预设 Agent 播种服务。

项目注册时自动创建 6 个预置代码子 Agent，均绑定 OPENAI_COMPATIBLE Adapter，
各自加载对应的 System Prompt。Agent 名称在同一项目内唯一。
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenthub.models.enums import AgentStatus, AgentType
from agenthub.models.orm import Agent, Project
from agenthub.schemas.domain import (
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
)
from agenthub.services.chat import ChatConflictError, ChatNotFoundError
from agenthub.services.prompt_loader import get_preset_agent_configs


class ProjectService:
    """项目注册与管理，注册时自动播种 6 个预置子 Agent。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, data: ProjectCreate) -> ProjectResponse:
        """创建项目并自动播种 6 个预置代码子 Agent。

        整个操作为单个数据库事务：任一步骤失败则全部回滚。
        """
        async with self._sessions() as session, session.begin():
            # 检查名称唯一性
            existing = await session.scalar(
                select(Project.id).where(Project.name == data.name)
            )
            if existing is not None:
                raise ChatConflictError("项目名称已存在")

            project = Project(
                name=data.name,
                root_path=data.root_path,
                description=data.description,
            )
            session.add(project)
            await session.flush()

            # 播种 6 个预置代码子 Agent
            preset_configs = get_preset_agent_configs()
            for config in preset_configs:
                agent = Agent(
                    project_id=project.id,
                    name=str(config["name"]),
                    agent_type=AgentType.OPENAI_COMPATIBLE,
                    capabilities=[str(config["capability"])],
                    status=AgentStatus.ENABLED,
                    adapter_config_ref=str(config["capability"]),
                )
                session.add(agent)
            await session.flush()
            return ProjectResponse.model_validate(project)

    async def get(self, project_id: uuid.UUID) -> ProjectResponse:
        """按 ID 查询项目。"""
        async with self._sessions() as session:
            project = await session.get(Project, project_id)
            if project is None:
                raise ChatNotFoundError
            return ProjectResponse.model_validate(project)

    async def list_projects(self) -> list[ProjectResponse]:
        """列出全部项目（首版单用户，无需分页）。"""
        async with self._sessions() as session:
            projects = await session.scalars(
                select(Project).order_by(Project.created_at.desc())
            )
            return [ProjectResponse.model_validate(p) for p in projects]

    async def update(
        self, project_id: uuid.UUID, data: ProjectUpdate
    ) -> ProjectResponse:
        """更新项目名称或描述；名称在全局范围内唯一。"""
        async with self._sessions() as session, session.begin():
            project = await session.scalar(
                select(Project)
                .where(Project.id == project_id)
                .with_for_update()
            )
            if project is None:
                raise ChatNotFoundError
            changes = data.model_dump(exclude_unset=True)
            if "name" in changes:
                duplicate = await session.scalar(
                    select(Project.id).where(
                        Project.name == changes["name"],
                        Project.id != project_id,
                    )
                )
                if duplicate is not None:
                    raise ChatConflictError("项目名称已存在")
            for field, value in changes.items():
                setattr(project, field, value)
            await session.flush()
            return ProjectResponse.model_validate(project)

    async def delete(self, project_id: uuid.UUID) -> bool:
        """删除项目，级联删除其下所有 Agent、会话等关联数据。"""
        async with self._sessions() as session, session.begin():
            project = await session.get(Project, project_id)
            if project is None:
                raise ChatNotFoundError
            await session.delete(project)
            await session.flush()
            return True
