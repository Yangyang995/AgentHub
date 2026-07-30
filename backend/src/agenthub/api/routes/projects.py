"""项目 REST 路由——注册、查询、更新、删除。"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from agenthub.api.routes.chat import _raise_http_error
from agenthub.schemas.domain import ProjectCreate, ProjectResponse, ProjectUpdate
from agenthub.services.chat import ChatConflictError, ChatNotFoundError
from agenthub.services.project import ProjectService

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def get_project_service(request: Request) -> ProjectService:
    """从应用状态获取项目服务。"""
    service: ProjectService = request.app.state.project_service
    return service


ProjectServiceDependency = Annotated[ProjectService, Depends(get_project_service)]


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    data: ProjectCreate, service: ProjectServiceDependency
) -> ProjectResponse:
    """注册新项目并自动创建 6 个预置子 Agent。"""
    try:
        return await service.create(data)
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    service: ProjectServiceDependency,
) -> list[ProjectResponse]:
    """列出全部已注册项目。"""
    try:
        return await service.list_projects()
    except ChatNotFoundError as error:
        _raise_http_error(error)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID, service: ProjectServiceDependency
) -> ProjectResponse:
    """按 ID 查询项目。"""
    try:
        return await service.get(project_id)
    except ChatNotFoundError as error:
        _raise_http_error(error)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    data: ProjectUpdate,
    service: ProjectServiceDependency,
) -> ProjectResponse:
    """更新项目名称或描述。"""
    try:
        return await service.update(project_id, data)
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID, service: ProjectServiceDependency
) -> None:
    """删除项目及其级联数据。"""
    try:
        await service.delete(project_id)
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)
