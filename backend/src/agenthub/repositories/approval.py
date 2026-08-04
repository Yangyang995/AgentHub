"""Approval 审批仓储——持久化审批记录的创建与状态变更。"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.models.enums import ApprovalStatus
from agenthub.models.orm import Approval
from agenthub.schemas.domain import ApprovalCreate, ApprovalResponse


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class ApprovalRepository:
    """审批仓储——不负责事务提交，由服务层控制事务边界。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        data: ApprovalCreate,
        project_id: uuid.UUID,
    ) -> ApprovalResponse:
        """创建待审批记录。"""
        approval = Approval(
            project_id=project_id,
            execution_id=data.execution_id,
            action_type=data.action_type,
            summary=data.summary,
            content_hash=data.content_hash,
            status=ApprovalStatus.PENDING,
        )
        self._session.add(approval)
        await self._session.flush()
        return ApprovalResponse.model_validate(approval)

    async def get_by_id(
        self, approval_id: uuid.UUID, project_id: uuid.UUID
    ) -> ApprovalResponse | None:
        """按 ID 和项目隔离查询审批。"""
        result = await self._session.execute(
            select(Approval).where(
                Approval.id == approval_id,
                Approval.project_id == project_id,
            )
        )
        approval = result.scalar_one_or_none()
        if approval is None:
            return None
        return ApprovalResponse.model_validate(approval)

    async def decide(
        self,
        approval_id: uuid.UUID,
        project_id: uuid.UUID,
        decision: ApprovalStatus,
        decided_by: str | None = None,
    ) -> ApprovalResponse | None:
        """批准或拒绝审批。仅 PENDING 状态的审批可被决定。"""
        result = await self._session.execute(
            select(Approval).where(
                Approval.id == approval_id,
                Approval.project_id == project_id,
            )
        )
        approval = result.scalar_one_or_none()
        if approval is None or approval.status != ApprovalStatus.PENDING:
            return None
        approval.status = decision
        approval.decided_at = _utcnow()
        approval.decided_by = decided_by
        await self._session.flush()
        return ApprovalResponse.model_validate(approval)

    async def list_pending(
        self, project_id: uuid.UUID
    ) -> list[ApprovalResponse]:
        """列出项目下所有待处理审批。"""
        result = await self._session.execute(
            select(Approval)
            .where(
                Approval.project_id == project_id,
                Approval.status == ApprovalStatus.PENDING,
            )
            .order_by(Approval.created_at.desc())
        )
        approvals = result.scalars().all()
        return [ApprovalResponse.model_validate(a) for a in approvals]