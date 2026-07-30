"""Agent 注册、查询、能力声明和启停服务。"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenthub.models.orm import Agent, Project
from agenthub.schemas.domain import AgentCreate, AgentResponse, AgentUpdate
from agenthub.services.chat import ChatConflictError, ChatNotFoundError


class AgentService:
    """在项目隔离边界内管理 Agent，且只向路由返回 Pydantic Schema。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, project_id: uuid.UUID, data: AgentCreate) -> AgentResponse:
        """注册 Agent；名称在同一项目中唯一，以保证 @路由无歧义。"""
        async with self._sessions() as session, session.begin():
            if await session.get(Project, project_id) is None:
                raise ChatNotFoundError
            existing = await session.scalar(
                select(Agent.id).where(Agent.project_id == project_id, Agent.name == data.name)
            )
            if existing is not None:
                raise ChatConflictError("Agent 名称已存在")
            agent = Agent(
                project_id=project_id,
                name=data.name,
                agent_type=data.agent_type,
                capabilities=[item.value for item in data.capabilities]
                if data.capabilities is not None
                else None,
                adapter_config_ref=data.adapter_config_ref,
            )
            session.add(agent)
            await session.flush()
            return AgentResponse.model_validate(agent)

    async def list(self, project_id: uuid.UUID) -> list[AgentResponse]:
        """按名称稳定返回项目内 Agent，供管理界面和 @建议共用。"""
        async with self._sessions() as session:
            if await session.get(Project, project_id) is None:
                raise ChatNotFoundError
            agents = await session.scalars(
                select(Agent).where(Agent.project_id == project_id).order_by(Agent.name)
            )
            return [AgentResponse.model_validate(agent) for agent in agents]

    async def get(self, project_id: uuid.UUID, agent_id: uuid.UUID) -> AgentResponse:
        """读取 Agent；跨项目与不存在统一返回未找到。"""
        async with self._sessions() as session:
            agent = await session.scalar(
                select(Agent).where(Agent.id == agent_id, Agent.project_id == project_id)
            )
            if agent is None:
                raise ChatNotFoundError
            return AgentResponse.model_validate(agent)

    async def update(
        self, project_id: uuid.UUID, agent_id: uuid.UUID, data: AgentUpdate
    ) -> AgentResponse:
        """更新公开配置和启停状态；类型在注册后保持不变。"""
        async with self._sessions() as session, session.begin():
            agent = await session.scalar(
                select(Agent)
                .where(Agent.id == agent_id, Agent.project_id == project_id)
                .with_for_update()
            )
            if agent is None:
                raise ChatNotFoundError
            changes = data.model_dump(exclude_unset=True)
            if "name" in changes:
                duplicate = await session.scalar(
                    select(Agent.id).where(
                        Agent.project_id == project_id,
                        Agent.name == changes["name"],
                        Agent.id != agent_id,
                    )
                )
                if duplicate is not None:
                    raise ChatConflictError("Agent 名称已存在")
            if data.capabilities is not None:
                changes["capabilities"] = [item.value for item in data.capabilities]
            for field, value in changes.items():
                setattr(agent, field, value)
            await session.flush()
            return AgentResponse.model_validate(agent)
