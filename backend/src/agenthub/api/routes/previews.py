"""Phase 10: 本地预览 REST 路由。"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from agenthub.services.preview import PreviewService

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["previews"])


class PreviewStartRequest(BaseModel):
    """预览启动请求——可选额外产出物 ID 列表。"""
    extra_artifact_ids: list[uuid.UUID] | None = None


class PreviewStartResponse(BaseModel):
    """预览启动响应——包含审批 ID，前端据此展示审批对话框。"""

    approval_id: uuid.UUID
    preview_id: uuid.UUID
    message: str


class PreviewStatusResponse(BaseModel):
    """预览状态响应。"""

    preview_id: uuid.UUID
    status: str  # starting / running / stopped / error
    url: str | None = None
    port: int | None = None
    error: str | None = None


def get_preview_service(request: Request) -> PreviewService:
    """从应用状态读取 PreviewService。"""
    service: PreviewService = request.app.state.preview_service
    return service


PreviewServiceDependency = Annotated[PreviewService, Depends(get_preview_service)]


@router.post(
    "/artifacts/{artifact_id}/preview",
    response_model=PreviewStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_preview(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    service: PreviewServiceDependency,
    body: PreviewStartRequest = PreviewStartRequest(),
) -> PreviewStartResponse:
    """为指定产出物启动预览流程。
    
    body.extra_artifact_ids: 可选的额外产出物 ID 列表（如 CSS/JS 文件），会被复制到同一预览目录。
    返回审批 ID——前端需等待用户审批通过后，后端自动启动预览。
    """
    try:
        approval, preview_id = await service.start_preview(
            project_id, artifact_id, extra_artifact_ids=body.extra_artifact_ids
        )
        return PreviewStartResponse(
            approval_id=approval.id,
            preview_id=preview_id,
            message="预览审批已创建，请等待审批通过",
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get("/previews/{preview_id}", response_model=PreviewStatusResponse)
async def get_preview_status(
    project_id: uuid.UUID,
    preview_id: uuid.UUID,
    service: PreviewServiceDependency,
) -> PreviewStatusResponse:
    """获取预览运行状态。"""
    state = await service.get_preview_status(preview_id)
    # 预览尚未启动（后台任务执行中），返回 starting 状态让前端继续轮询
    if state is None:
        return PreviewStatusResponse(
            preview_id=preview_id,
            status="starting",
        )
    return PreviewStatusResponse(
        preview_id=state.preview_id,
        status=state.status,
        url=f"http://127.0.0.1:{state.port}" if state.port else None,
        port=state.port if state.port else None,
        error=state.error,
    )


@router.delete("/previews/{preview_id}", status_code=status.HTTP_200_OK)
async def stop_preview(
    project_id: uuid.UUID,
    preview_id: uuid.UUID,
    service: PreviewServiceDependency,
) -> dict[str, str]:
    """停止预览——终止子进程并清理临时目录。"""
    success = await service.stop_preview(preview_id, project_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="预览不存在或已停止",
        )
    return {"status": "stopped", "preview_id": str(preview_id)}