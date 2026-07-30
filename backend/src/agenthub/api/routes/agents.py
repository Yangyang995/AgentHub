"""Agent 管理 REST 路由。"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from agenthub.api.routes.chat import _raise_http_error
from agenthub.schemas.domain import AgentCreate, AgentResponse, AgentUpdate
from agenthub.services.agents import AgentService
from agenthub.services.chat import ChatConflictError, ChatNotFoundError

router = APIRouter(prefix="/api/v1/projects/{project_id}/agents", tags=["agents"])


def get_agent_service(request: Request) -> AgentService:
    """从应用状态取得 Agent 服务。"""
    service: AgentService = request.app.state.agent_service
    return service


AgentServiceDependency = Annotated[AgentService, Depends(get_agent_service)]


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    project_id: uuid.UUID, data: AgentCreate, service: AgentServiceDependency
) -> AgentResponse:
    """注册项目 Agent。"""
    try:
        return await service.create(project_id, data)
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    project_id: uuid.UUID, service: AgentServiceDependency
) -> list[AgentResponse]:
    """列出项目 Agent。"""
    try:
        return await service.list(project_id)
    except ChatNotFoundError as error:
        _raise_http_error(error)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    project_id: uuid.UUID, agent_id: uuid.UUID, service: AgentServiceDependency
) -> AgentResponse:
    """读取项目 Agent。"""
    try:
        return await service.get(project_id, agent_id)
    except ChatNotFoundError as error:
        _raise_http_error(error)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    data: AgentUpdate,
    service: AgentServiceDependency,
) -> AgentResponse:
    """更新 Agent 能力、名称或启停状态。"""
    try:
        return await service.update(project_id, agent_id, data)
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)
