"""Artifact 产出物仓储——按 project_id 隔离的数据访问。"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.models.orm import Artifact
from agenthub.schemas.domain import ArtifactCreate, ArtifactResponse


class ArtifactRepository:
    """Artifact 仓储。
    所有方法接收 AsyncSession 但不负责提交事务——事务边界由服务层显式控制。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, data: ArtifactCreate, project_id: uuid.UUID, execution_id: uuid.UUID | None = None
    ) -> ArtifactResponse:
        """注册新的产出物记录。"""
        artifact = Artifact(
            project_id=project_id,
            execution_id=execution_id,
            artifact_type=data.artifact_type,
            relative_path=data.relative_path,
            content_hash=data.content_hash,
            size=data.size,
            metadata_json=data.metadata_json,
        )
        self._session.add(artifact)
        await self._session.flush()
        return ArtifactResponse.model_validate(artifact)

    async def get_by_id(
        self, artifact_id: uuid.UUID, project_id: uuid.UUID
    ) -> ArtifactResponse | None:
        """按 ID 和项目隔离查询产出物。"""
        result = await self._session.execute(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.project_id == project_id,
            )
        )
        artifact = result.scalar_one_or_none()
        if artifact is None:
            return None
        return ArtifactResponse.model_validate(artifact)

    async def list_by_project(
        self, project_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[ArtifactResponse], int]:
        """分页列出项目下的产出物。"""
        count_result = await self._session.execute(
            select(Artifact.id).where(Artifact.project_id == project_id)
        )
        total = len(count_result.scalars().all())

        result = await self._session.execute(
            select(Artifact)
            .where(Artifact.project_id == project_id)
            .order_by(Artifact.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        artifacts = result.scalars().all()
        return [ArtifactResponse.model_validate(a) for a in artifacts], total