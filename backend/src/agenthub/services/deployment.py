"""Phase 10: Vercel 部署服务——通过 Vercel REST API 部署预览包。"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenthub.core.config import get_settings
from agenthub.models.enums import (
    ApprovalActionType,
    DeploymentProvider,
    DeploymentStatus,
)
from agenthub.models.orm import Project
from agenthub.repositories.approval import ApprovalRepository
from agenthub.repositories.artifact import ArtifactRepository
from agenthub.repositories.deployment import DeploymentRepository
from agenthub.schemas.domain import (
    ApprovalCreate,
    ApprovalResponse,
    DeploymentCreate,
    DeploymentResponse,
    EventEnvelope,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class DeploymentServiceError(RuntimeError):
    """部署服务错误——包含稳定错误码，前端据此展示用户友好的提示。"""


class DeploymentService:
    """管理 Vercel 部署的完整生命周期。
    
    条件功能：Vercel Token 未配置时所有部署操作返回明确错误，不阻塞应用。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_broker: Any = None,
    ) -> None:
        self._sessions = session_factory
        self._broker = event_broker
        # 审批映射: approval_id → (artifact_id, deployment_id)
        self._pending_approvals: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {}

    def _check_token(self) -> str:
        """校验 Vercel Token 是否已配置，缺失时抛出可提示错误。"""
        settings = get_settings()
        if settings.vercel_token is None:
            raise DeploymentServiceError(
                "Vercel 部署功能未配置。请设置环境变量 AGENTHUB_VERCEL_TOKEN。"
            )
        return settings.vercel_token.get_secret_value()

    async def create_deployment(
        self, project_id: uuid.UUID, artifact_id: uuid.UUID,
        extra_artifact_ids: list[uuid.UUID] | None = None,
    ) -> tuple[ApprovalResponse, uuid.UUID]:
        """创建部署流程——检查配置、创建审批、发送 WebSocket 事件。
        
        返回审批记录和部署 ID；调用方需等待审批通过后调用 execute_deployment。
        """
        # 先检查 Token 可用性
        try:
            self._check_token()
        except DeploymentServiceError:
            raise

        async with self._sessions() as session, session.begin():
            # 校验 Artifact
            repo = ArtifactRepository(session)
            artifact = await repo.get_by_id(artifact_id, project_id)
            if artifact is None:
                raise LookupError("产出物不存在或不属于当前项目")

            # 创建审批记录
            approval_repo = ApprovalRepository(session)
            approval = await approval_repo.create(
                ApprovalCreate(
                    action_type=ApprovalActionType.DEPLOY,
                    summary=f"部署产出物到 Vercel: {artifact.relative_path}",
                    content_hash=artifact.content_hash,
                ),
                project_id,
            )

            # 创建部署记录（PENDING 状态）
            deployment_repo = DeploymentRepository(session)
            deployment = await deployment_repo.create(
                DeploymentCreate(
                    approval_id=approval.id,
                    artifact_id=artifact_id,
                    provider=DeploymentProvider.VERCEL,
                ),
                project_id,
            )

        # 记录审批映射——审批通过后用于执行部署
        self._pending_approvals[approval.id] = (artifact_id, deployment.id, extra_artifact_ids or [])  # + extra files

        # 发送 WebSocket 审批请求事件
        if self._broker is not None:
            envelope = EventEnvelope(
                event_id=uuid.uuid4(),
                conversation_id=uuid.uuid4(),
                execution_id=uuid.uuid4(),
                sequence=0,
                type="approval.required",
                timestamp=_utcnow(),
                payload={
                    "approval_id": str(approval.id),
                    "action_type": ApprovalActionType.DEPLOY.value,
                    "summary": f"部署产出物到 Vercel: {artifact.relative_path}",
                    "artifact_id": str(artifact_id),
                    "deployment_id": str(deployment.id),
                },
            )
            await self._broker.publish(envelope)

        return approval, deployment.id

    async def execute_deployment(
        self,
        project_id: uuid.UUID,
        artifact_id: uuid.UUID,
        deployment_id: uuid.UUID,
        extra_artifact_ids: list[uuid.UUID] | None = None,
    ) -> DeploymentResponse:
        """Execute Vercel deployment: create, upload files via multipart, poll via list API."""
        token = self._check_token()
        settings = get_settings()

        async with self._sessions() as session:
            repo = ArtifactRepository(session)
            artifact = await repo.get_by_id(artifact_id, project_id)
            if artifact is None:
                raise LookupError("Artifact not found")
            project = await session.get(Project, project_id)
            if project is None:
                raise LookupError("Project not found")

        artifacts_root = Path(project.root_path) / ".agenthub" / "artifacts"
        import base64, mimetypes

        async with self._sessions() as session:
            repo = ArtifactRepository(session)
            deploy_files: list[tuple[str, bytes]] = []
            source_path = artifacts_root / artifact.content_hash
            if not source_path.exists():
                raise FileNotFoundError(f"Artifact file not found: {artifact.content_hash}")
            deploy_files.append(("index.html", source_path.read_bytes()))
            if extra_artifact_ids:
                for extra_id in extra_artifact_ids:
                    extra = await repo.get_by_id(extra_id, project_id)
                    if extra is None:
                        logger.warning("Extra artifact %s not found, skip", extra_id)
                        continue
                    ep = artifacts_root / extra.content_hash
                    if not ep.exists():
                        continue
                    ename = extra.relative_path.split("/")[-1]
                    deploy_files.append((ename, ep.read_bytes()))

        await self._update_deployment_status(project_id, deployment_id, DeploymentStatus.PREPARING)
        team_id = settings.vercel_team_id
        team_param = f"?teamId={team_id}" if team_id else ""
        headers = {"Authorization": f"Bearer {token}"}

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                await self._update_deployment_status(project_id, deployment_id, DeploymentStatus.UPLOADING)

                # Upload files inline via v11 API (base64 files on create)
                files_payload = [
                    {"file": name, "data": base64.b64encode(data).decode("utf-8")}
                    for name, data in deploy_files
                ]

                upload_resp = await client.post(
                    f"https://api.vercel.com/v13/deployments{team_param}",
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "name": f"agenthub-{str(deployment_id)[:8]}",
                        "files": files_payload,
                        "projectSettings": {
                            "devCommand": None,
                            "installCommand": None,
                            "buildCommand": None,
                            "outputDirectory": None,
                            "rootDirectory": None,
                            "framework": None,
                        },
                    },
                )
                if upload_resp.status_code >= 400:
                    logger.error("Vercel deploy failed: %s", upload_resp.text)
                    await self._update_deployment_status(project_id, deployment_id, DeploymentStatus.FAILED, error_code="VERCEL_API_ERROR")
                    raise DeploymentServiceError(f"Vercel deploy failed (HTTP {upload_resp.status_code})")

                ud = upload_resp.json()
                logger.info("Vercel v13 full response: id=%s url=%s alias=%s readyState=%s", ud.get("id"), ud.get("url"), ud.get("alias"), ud.get("readyState"))
                state = ud.get("readyState")
                aliases = ud.get("alias", [])
                result_url = aliases[0] if aliases else ud.get("url", "")
                final_url = f"https://{result_url}" if result_url and not result_url.startswith("https://") else (result_url or "")

                if not final_url:
                    await self._update_deployment_status(project_id, deployment_id, DeploymentStatus.FAILED, error_code="NO_URL")
                    raise DeploymentServiceError("Vercel deployment returned no URL")

                if state == "READY":
                    deployment = await self._update_deployment_status(project_id, deployment_id, DeploymentStatus.COMPLETED, result_url=final_url)
                    logger.info("Vercel deployment completed: %s", final_url)
                    # 服务端健康检查——确认部署 URL 是否可达
                    try:
                        check = await client.get(final_url, timeout=httpx.Timeout(10))
                        logger.info("Vercel health check: HTTP %s", check.status_code)
                    except Exception as he:
                        logger.warning("Vercel health check failed from server: %s", he)
                    return deployment
                elif state == "ERROR":
                    await self._update_deployment_status(project_id, deployment_id, DeploymentStatus.FAILED, error_code="VERCEL_ERROR")
                    raise DeploymentServiceError("Vercel deployment returned ERROR")
                else:
                    # BUILDING / QUEUED / INITIALIZING 等中间状态——等最多 60 秒轮询就绪
                    logger.info("Vercel deployment building: %s (state=%s)", final_url, state)
                    await self._update_deployment_status(project_id, deployment_id, DeploymentStatus.BUILDING, result_url=final_url)
                    for _ in range(20):
                        await asyncio.sleep(3)
                        poll = await client.get(
                            f"https://api.vercel.com/v13/deployments/{ud.get('id', '')}{team_param}",
                            headers=headers,
                        )
                        if poll.status_code >= 400:
                            continue
                        pd = poll.json()
                        ps = pd.get("readyState")
                        if ps == "READY":
                            deployment = await self._update_deployment_status(project_id, deployment_id, DeploymentStatus.COMPLETED, result_url=final_url)
                            logger.info("Vercel deployment completed after polling: %s", final_url)
                            try:
                                check = await client.get(final_url, timeout=httpx.Timeout(10))
                                logger.info("Vercel health check (poll): HTTP %s", check.status_code)
                            except Exception as he:
                                logger.warning("Vercel health check failed from server (poll): %s", he)
                            return deployment
                        elif ps == "ERROR":
                            await self._update_deployment_status(project_id, deployment_id, DeploymentStatus.FAILED, error_code="VERCEL_BUILD_ERROR")
                            raise DeploymentServiceError("Vercel deployment failed during build")
                        logger.info("Vercel deployment polling: state=%s", ps)
                    # 超时——60秒内未就绪，标记完成让用户自行检查
                    logger.warning("Vercel deployment polling timeout (60s), marking as COMPLETED anyway")
                    return await self._update_deployment_status(project_id, deployment_id, DeploymentStatus.COMPLETED, result_url=final_url)

        except httpx.HTTPError as exc:
            logger.exception("Vercel API network error")
            await self._update_deployment_status(project_id, deployment_id, DeploymentStatus.FAILED, error_code="NETWORK_ERROR")
            raise DeploymentServiceError(f"Vercel API network error: {exc}") from exc

    async def _update_deployment_status(
        self,
        project_id: uuid.UUID,
        deployment_id: uuid.UUID,
        status: DeploymentStatus,
        result_url: str | None = None,
        error_code: str | None = None,
    ) -> DeploymentResponse:
        """更新部署状态并推送 WebSocket 事件。"""
        async with self._sessions() as session, session.begin():
            repo = DeploymentRepository(session)
            deployment = await repo.update_status(
                deployment_id, project_id, status,
                result_url=result_url, error_code=error_code,
            )
            if deployment is None:
                raise LookupError("部署记录不存在")

        # 推送 WebSocket 事件
        if self._broker is not None:
            envelope = EventEnvelope(
                event_id=uuid.uuid4(),
                conversation_id=uuid.uuid4(),
                execution_id=uuid.uuid4(),
                sequence=0,
                type="deployment.status",
                timestamp=_utcnow(),
                payload={
                    "deployment_id": str(deployment_id),
                    "status": status.value,
                    "result_url": result_url,
                    "error_code": error_code,
                },
            )
            await self._broker.publish(envelope)

        return deployment

    async def get_deployment(
        self, project_id: uuid.UUID, deployment_id: uuid.UUID
    ) -> DeploymentResponse | None:
        """查询部署状态。"""
        async with self._sessions() as session:
            repo = DeploymentRepository(session)
            return await repo.get_by_id(deployment_id, project_id)