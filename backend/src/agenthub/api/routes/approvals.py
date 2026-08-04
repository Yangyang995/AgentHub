"""Phase 10: Approval 审批 REST 路由。"""

import uuid

from fastapi import APIRouter, HTTPException, Request, status

from agenthub.db.session import get_session_factory
from agenthub.models.enums import ApprovalActionType, ApprovalStatus
from agenthub.repositories.approval import ApprovalRepository
from agenthub.schemas.domain import ApprovalDecide, ApprovalResponse

router = APIRouter(prefix="/api/v1/projects/{project_id}/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalResponse])
async def list_approvals(
    project_id: uuid.UUID,
) -> list[ApprovalResponse]:
    """列出项目下所有待处理的审批。"""
    async with get_session_factory()() as session:
        repo = ApprovalRepository(session)
        return await repo.list_pending(project_id)


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    project_id: uuid.UUID,
    approval_id: uuid.UUID,
) -> ApprovalResponse:
    """获取审批详情。"""
    async with get_session_factory()() as session:
        repo = ApprovalRepository(session)
        result = await repo.get_by_id(approval_id, project_id)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="审批不存在或不属于当前项目",
            )
        return result


@router.post("/{approval_id}/decide", response_model=ApprovalResponse)
async def decide_approval(
    project_id: uuid.UUID,
    approval_id: uuid.UUID,
    data: ApprovalDecide,
    request: Request,
) -> ApprovalResponse:
    """批准或拒绝审批。
    
    审批通过后，根据 action_type 自动触发后续动作：
    - START_PREVIEW → 启动本地预览子进程
    - DEPLOY → 执行 Vercel 部署
    """
    if data.decision not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="decision 必须是 approved 或 rejected",
        )

    async with get_session_factory()() as session, session.begin():
        repo = ApprovalRepository(session)
        approval = await repo.get_by_id(approval_id, project_id)
        if approval is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="审批不存在或不属于当前项目",
            )
        if approval.status != ApprovalStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="审批已处理",
            )

        result = await repo.decide(
            approval_id, project_id, data.decision, data.decided_by
        )
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="审批决定失败",
            )

    # 审批通过后同步触发后续动作（直接执行，便于错误诊断）
    if data.decision == ApprovalStatus.APPROVED:
        await _trigger_action(project_id, approval_id, approval.action_type, request)

    return result


async def _trigger_action(
    project_id: uuid.UUID,
    approval_id: uuid.UUID,
    action_type: ApprovalActionType,
    request: Request,
) -> None:
    """审批通过后触发对应的后续动作。
    
    通过服务层的待审批映射查找关联的 artifact/preview/deployment ID。
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        if action_type == ApprovalActionType.START_PREVIEW:
            preview_service = request.app.state.preview_service
            pending = preview_service._pending_approvals.pop(approval_id, None)
            if pending is None:
                logger.warning("预览审批映射缺失: approval_id=%s", approval_id)
                return
            # 解包：(artifact_id, preview_id, extra_artifact_ids)
            if len(pending) == 2:
                artifact_id, preview_id = pending
                extra_ids = None
            else:
                artifact_id, preview_id, extra_ids = pending
            await preview_service.execute_preview(project_id, artifact_id, preview_id, extra_ids)

        elif action_type == ApprovalActionType.DEPLOY:
            deploy_service = request.app.state.deployment_service
            # 优先从内存映射查找（同一服务进程内），失败则从数据库查询
            pending = deploy_service._pending_approvals.pop(approval_id, None)
            if pending is not None:
                if len(pending) == 2:
                    artifact_id, deployment_id = pending
                    extra_ids = None
                else:
                    artifact_id, deployment_id, extra_ids = pending
            else:
                # 服务重启后内存丢失，从数据库根据 approval_id 恢复
                from agenthub.db.session import get_session_factory
                from agenthub.repositories.deployment import DeploymentRepository
                from agenthub.repositories.artifact import ArtifactRepository
                async with get_session_factory()() as session:
                    deploy_repo = DeploymentRepository(session)
                    deployment = await deploy_repo.get_by_approval_id(approval_id, project_id)
                    if deployment is None or deployment.artifact_id is None:
                        logger.warning("Deployment not found for approval_id=%s", approval_id)
                        return
                    deployment_id = deployment.id
                    artifact_id = deployment.artifact_id
                    # Try to find extra artifact IDs from the artifact record
                    extra_ids = None
                logger.info("Deployment recovered from DB: deployment_id=%s, artifact_id=%s", deployment_id, artifact_id)
            await deploy_service.execute_deployment(project_id, artifact_id, deployment_id, extra_ids)

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        detail = f"审批后续动作失败: {type(exc).__name__}: {exc}\\n{tb}"
        logger.exception("审批后续动作执行失败: approval_id=%s, detail=%s", approval_id, detail)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail[:1000]) from exc