"""Deployment 部署仓储——部署请求的创建与状态追踪。"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.models.enums import DeploymentStatus
from agenthub.models.orm import Deployment
from agenthub.schemas.domain import DeploymentCreate, DeploymentResponse


class DeploymentRepository:
    """部署仓储——不负责事务提交，由服务层控制事务边界。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        data: DeploymentCreate,
        project_id: uuid.UUID,
    ) -> DeploymentResponse:
        """创建部署请求记录。"""
        deployment = Deployment(
            project_id=project_id,
            approval_id=data.approval_id,
            artifact_id=data.artifact_id,
            provider=data.provider,
            status=DeploymentStatus.PENDING,
            target_url=data.target_url,
        )
        self._session.add(deployment)
        await self._session.flush()
        return DeploymentResponse.model_validate(deployment)

    async def get_by_id(
        self, deployment_id: uuid.UUID, project_id: uuid.UUID
    ) -> DeploymentResponse | None:
        """按 ID 和项目隔离查询部署。"""
        result = await self._session.execute(
            select(Deployment).where(
                Deployment.id == deployment_id,
                Deployment.project_id == project_id,
            )
        )
        deployment = result.scalar_one_or_none()
        if deployment is None:
            return None
        return DeploymentResponse.model_validate(deployment)

    async def get_by_approval_id(
        self, approval_id: uuid.UUID, project_id: uuid.UUID
    ) -> DeploymentResponse | None:
        """Lookup deployment by approval_id for post-approval callback."""
        result = await self._session.execute(
            select(Deployment).where(
                Deployment.approval_id == approval_id,
                Deployment.project_id == project_id,
            )
        )
        deployment = result.scalar_one_or_none()
        if deployment is None:
            return None
        return DeploymentResponse.model_validate(deployment)

    async def update_status(
        self,
        deployment_id: uuid.UUID,
        project_id: uuid.UUID,
        status: DeploymentStatus,
        result_url: str | None = None,
        error_code: str | None = None,
    ) -> DeploymentResponse | None:
        """更新部署状态。"""
        result = await self._session.execute(
            select(Deployment).where(
                Deployment.id == deployment_id,
                Deployment.project_id == project_id,
            )
        )
        deployment = result.scalar_one_or_none()
        if deployment is None:
            return None
        deployment.status = status
        if result_url is not None:
            deployment.result_url = result_url
        if error_code is not None:
            deployment.error_code = error_code
        await self._session.flush()
        return DeploymentResponse.model_validate(deployment)