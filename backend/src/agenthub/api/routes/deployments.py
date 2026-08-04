"""Phase 10: Vercel 部署 REST 路由。"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from agenthub.schemas.domain import DeploymentResponse
from agenthub.services.deployment import DeploymentService, DeploymentServiceError

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["deployments"])


class DeploymentStartRequest(BaseModel):
    """部署启动请求——可选额外产出物 ID 列表。"""
    extra_artifact_ids: list[uuid.UUID] | None = None


class DeploymentStartResponse(BaseModel):
    """部署启动响应——包含审批 ID。"""

    approval_id: uuid.UUID
    deployment_id: uuid.UUID
    message: str


def get_deployment_service(request: Request) -> DeploymentService:
    """从应用状态读取 DeploymentService。"""
    service: DeploymentService = request.app.state.deployment_service
    return service


DeploymentServiceDependency = Annotated[DeploymentService, Depends(get_deployment_service)]


@router.post(
    "/artifacts/{artifact_id}/deploy",
    response_model=DeploymentStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_deployment(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    service: DeploymentServiceDependency,
    body: DeploymentStartRequest = DeploymentStartRequest(),
) -> DeploymentStartResponse:
    """为指定产出物创建 Vercel 部署。
    
    返回审批 ID——前端需等待用户审批通过后，后端自动执行部署。
    """
    try:
        approval, deployment_id = await service.create_deployment(
            project_id, artifact_id, body.extra_artifact_ids
        )
        return DeploymentStartResponse(
            approval_id=approval.id,
            deployment_id=deployment_id,
            message="部署审批已创建，请等待审批通过",
        )
    except DeploymentServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("/deployments/{deployment_id}", response_model=DeploymentResponse)
async def get_deployment_status(
    project_id: uuid.UUID,
    deployment_id: uuid.UUID,
    service: DeploymentServiceDependency,
) -> DeploymentResponse:
    """查询部署状态和结果 URL。"""
    result = await service.get_deployment(project_id, deployment_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="部署不存在或不属于当前项目",
        )
    return result