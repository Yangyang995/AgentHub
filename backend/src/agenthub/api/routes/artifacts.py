"""Phase 10: Artifact 产出物 REST 路由。"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from agenthub.db.session import get_session_factory
from agenthub.models.enums import ArtifactType
from agenthub.schemas.domain import ArtifactResponse
from agenthub.services.preview import PreviewService

router = APIRouter(prefix="/api/v1/projects/{project_id}/artifacts", tags=["artifacts"])


class ArtifactUploadRequest(BaseModel):
    """产出物上传请求——包含类型、路径和 Base64 编码内容。"""

    artifact_type: ArtifactType = Field(description="产出物类型")
    relative_path: str = Field(min_length=1, max_length=4096, description="相对路径")
    content_base64: str = Field(min_length=1, description="Base64 编码的文件内容")
    execution_id: uuid.UUID | None = Field(default=None, description="关联的执行 ID")
    metadata: dict[str, object] | None = Field(default=None, description="扩展元数据")


def get_preview_service(request: Request) -> PreviewService:
    """从应用状态读取 PreviewService。"""
    service: PreviewService = request.app.state.preview_service
    return service


PreviewServiceDependency = Annotated[PreviewService, Depends(get_preview_service)]


@router.post("", response_model=ArtifactResponse, status_code=status.HTTP_201_CREATED)
async def upload_artifact(
    project_id: uuid.UUID,
    data: ArtifactUploadRequest,
    service: PreviewServiceDependency,
) -> ArtifactResponse:
    """注册新的产出物——保存内容到磁盘并创建数据库记录。"""
    import base64

    try:
        content_bytes = base64.b64decode(data.content_base64)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="content_base64 不是有效的 Base64 编码",
        ) from None

    try:
        return await service.register_artifact(
            project_id=project_id,
            artifact_type=data.artifact_type,
            relative_path=data.relative_path,
            content=content_bytes,
            execution_id=data.execution_id,
            metadata=data.metadata,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    service: PreviewServiceDependency,
) -> ArtifactResponse:
    """获取产出物元数据。"""
    from agenthub.repositories.artifact import ArtifactRepository

    async with get_session_factory()() as session:
        repo = ArtifactRepository(session)
        result = await repo.get_by_id(artifact_id, project_id)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="产出物不存在或不属于当前项目",
            )
        return result


@router.get("", response_model=list[ArtifactResponse])
async def list_artifacts(
    project_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> list[ArtifactResponse]:
    """列出项目下的产出物。"""
    from agenthub.repositories.artifact import ArtifactRepository

    async with get_session_factory()() as session:
        repo = ArtifactRepository(session)
        items, _total = await repo.list_by_project(project_id, page, page_size)
        return items