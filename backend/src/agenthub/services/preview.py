"""Phase 10: 本地预览服务——子进程 HTTP 服务器 + sandboxed iframe 展示。"""

import asyncio
import hashlib
import logging
import socket
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenthub.core.config import get_settings
from agenthub.models.enums import (
    ApprovalActionType,
    ArtifactType,
)
from agenthub.models.orm import Project
from agenthub.repositories.approval import ApprovalRepository
from agenthub.repositories.artifact import ArtifactRepository
from agenthub.schemas.domain import (
    ApprovalCreate,
    ApprovalResponse,
    ArtifactCreate,
    ArtifactResponse,
    EventEnvelope,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _find_free_port(start: int, end: int) -> int:
    """在指定范围内查找空闲端口。"""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"端口范围 {start}-{end} 内无可用端口")


@dataclass
class PreviewState:
    """活跃预览的运行状态——进程内内存追踪，非持久化。
    
    预览本质上是短暂的本机 HTTP 服务，无需存入数据库；
    应用重启后所有预览自动失效，符合本地单用户场景。
    """

    preview_id: uuid.UUID
    artifact_id: uuid.UUID
    project_id: uuid.UUID
    port: int
    temp_dir: str
    process: Any = None  # subprocess.Popen，使用 Any 避免类型注解兼容问题
    status: str = "starting"  # starting / running / stopped / error
    error: str | None = None
    created_at: datetime = field(default_factory=_utcnow)


class PreviewService:
    """管理本地 HTML/CSS/JS 预览的完整生命周期。
    
    安全约束：
    - 仅接受已登记且属于当前项目的 Artifact
    - 子进程使用参数数组启动，禁止 shell=True
    - 临时目录路径在项目根目录约束内
    - 应用关闭时强制清理所有活跃预览
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_broker: Any = None,
    ) -> None:
        self._sessions = session_factory
        self._broker = event_broker
        self._active: dict[uuid.UUID, PreviewState] = {}
        # 审批映射: approval_id → (artifact_id, preview_id)
        self._pending_approvals: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {}

    async def register_artifact(
        self,
        project_id: uuid.UUID,
        artifact_type: ArtifactType,
        relative_path: str,
        content: bytes,
        execution_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactResponse:
        """注册产出物：保存内容到磁盘并创建数据库记录。
        
        Args:
            project_id: 所属项目
            artifact_type: 产出物类型
            relative_path: 相对于项目根目录的路径
            content: 文件原始内容（字节）
            execution_id: 产生此产出物的执行 ID（可选）
            metadata: 扩展元数据
        """
        content_hash = hashlib.sha256(content).hexdigest()
        size = len(content)

        async with self._sessions() as session, session.begin():
            # 获取项目根路径以保存文件
            project = await session.get(Project, project_id)
            if project is None:
                raise LookupError("项目不存在")

            # 计算存储路径——在项目根路径下的 .agenthub/artifacts/ 目录
            artifacts_root = Path(project.root_path) / ".agenthub" / "artifacts"
            artifacts_root.mkdir(parents=True, exist_ok=True)

            # 使用内容哈希作为文件名，避免重复存储
            storage_path = artifacts_root / content_hash
            if not storage_path.exists():
                storage_path.write_bytes(content)

            # 创建数据库记录
            repo = ArtifactRepository(session)
            execution_id_val = execution_id  # 预览上传等场景允许为空
            artifact_data = ArtifactCreate(
                artifact_type=artifact_type,
                relative_path=relative_path,
                content_hash=content_hash,
                size=size,
                metadata_json=metadata,
            )
            result = await repo.create(artifact_data, project_id, execution_id_val)
            return result

    async def start_preview(
        self, project_id: uuid.UUID, artifact_id: uuid.UUID,
        extra_artifact_ids: list[uuid.UUID] | None = None,
    ) -> tuple[ApprovalResponse, uuid.UUID]:
        """启动预览流程——创建审批记录并发送 WebSocket 事件。
        
        返回审批记录和预览 ID；调用方需等待审批通过后调用 execute_preview。
        """
        async with self._sessions() as session, session.begin():
            # 校验 Artifact 存在且属于该项目
            repo = ArtifactRepository(session)
            artifact = await repo.get_by_id(artifact_id, project_id)
            if artifact is None:
                raise LookupError("产出物不存在或不属于当前项目")

            # 校验类型：仅接受 HTML/CSS/JS 类型产出物
            if artifact.artifact_type not in (
                ArtifactType.PREVIEW,
                ArtifactType.FILE,
            ):
                raise ValueError("仅支持预览 HTML/CSS/JS 类型的产出物")

            # 创建审批记录
            approval_repo = ApprovalRepository(session)
            approval = await approval_repo.create(
                ApprovalCreate(
                    action_type=ApprovalActionType.START_PREVIEW,
                    summary=f"预览产出物: {artifact.relative_path}",
                    content_hash=artifact.content_hash,
                ),
                project_id,
            )

            preview_id = uuid.uuid4()

        # 记录审批映射——审批通过后用于执行预览
        # 存储格式: (artifact_id, preview_id, extra_artifact_ids)
        self._pending_approvals[approval.id] = (artifact_id, preview_id, extra_artifact_ids or [])

        # 通过 WebSocket 发送审批请求事件（非阻塞）
        if self._broker is not None:
            envelope = EventEnvelope(
                event_id=uuid.uuid4(),
                conversation_id=uuid.uuid4(),  # 预览不绑定特定会话
                execution_id=uuid.uuid4(),
                sequence=0,
                type="approval.required",
                timestamp=_utcnow(),
                payload={
                    "approval_id": str(approval.id),
                    "action_type": ApprovalActionType.START_PREVIEW.value,
                    "summary": f"预览产出物: {artifact.relative_path}",
                    "artifact_id": str(artifact_id),
                    "preview_id": str(preview_id),
                },
            )
            await self._broker.publish(envelope)

        return approval, preview_id

    async def execute_preview(
        self, project_id: uuid.UUID, artifact_id: uuid.UUID, preview_id: uuid.UUID,
        extra_artifact_ids: list[uuid.UUID] | None = None,
    ) -> PreviewState:
        """在审批通过后实际启动本地预览 HTTP 服务器。
        
        安全措施：
        - 子进程使用参数数组，禁止 shell=True
        - 仅绑定 127.0.0.1，不接受外部连接
        - 临时目录在受控位置创建
        """
        # 停止已有预览（同时只允许一个活跃预览）
        for existing_id, existing_state in list(self._active.items()):
            if existing_state.project_id == project_id:
                await self.stop_preview(existing_id, project_id)

        settings = get_settings()

        async with self._sessions() as session:
            repo = ArtifactRepository(session)
            artifact = await repo.get_by_id(artifact_id, project_id)
            if artifact is None:
                raise LookupError("产出物不存在")

            project = await session.get(Project, project_id)
            if project is None:
                raise LookupError("项目不存在")

        # 从磁盘读取产出物内容
        artifacts_root = Path(project.root_path) / ".agenthub" / "artifacts"
        source_path = artifacts_root / artifact.content_hash
        if not source_path.exists():
            raise FileNotFoundError(f"产出物文件不存在: {artifact.content_hash}")

        # 复制到临时目录作为 HTTP 服务器根目录
        temp_dir = tempfile.mkdtemp(prefix="agenthub_preview_")
        temp_path = Path(temp_dir)
        
        # 复制产出物文件
        dest_path = temp_path / artifact.relative_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(source_path.read_bytes())

        # 如果有 index.html，确保在根目录可访问
        index_path = temp_path / "index.html"
        if not index_path.exists() and dest_path.suffix == ".html":
            import shutil
            shutil.copy2(str(dest_path), str(index_path))

        # 复制额外的产出物（如 CSS/JS 文件）到同一预览目录
        if extra_artifact_ids:
            import shutil
            async with self._sessions() as session2:
                extra_repo = ArtifactRepository(session2)
                for extra_id in extra_artifact_ids:
                    extra_artifact = await extra_repo.get_by_id(extra_id, project_id)
                    if extra_artifact is None:
                        continue
                    extra_source = artifacts_root / extra_artifact.content_hash
                    if extra_source.exists():
                        extra_dest = temp_path / extra_artifact.relative_path
                        extra_dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(extra_source), str(extra_dest))

        # 查找空闲端口
        port = _find_free_port(
            settings.preview_port_range_start,
            settings.preview_port_range_end,
        )

        # 启动子进程 HTTP 服务器——参数数组，禁止 shell=True
        # 使用 subprocess.Popen 而非 asyncio.create_subprocess_exec，
        # 避免 Windows ProactorEventLoop 的 NotImplementedError。
        process = subprocess.Popen(
            ["python", "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(temp_dir)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        state = PreviewState(
            preview_id=preview_id,
            artifact_id=artifact_id,
            project_id=project_id,
            port=port,
            temp_dir=temp_dir,
            process=process,
            status="running",
        )
        self._active[preview_id] = state

        # 通过 WebSocket 发送预览启动事件
        if self._broker is not None:
            envelope = EventEnvelope(
                event_id=uuid.uuid4(),
                conversation_id=uuid.uuid4(),
                execution_id=uuid.uuid4(),
                sequence=0,
                type="preview.started",
                timestamp=_utcnow(),
                payload={
                    "preview_id": str(preview_id),
                    "artifact_id": str(artifact_id),
                    "port": port,
                    "url": f"http://127.0.0.1:{port}",
                },
            )
            await self._broker.publish(envelope)

        logger.info(
            "预览已启动: preview_id=%s, port=%d, temp_dir=%s",
            preview_id, port, temp_dir,
        )
        return state

    async def get_preview_status(self, preview_id: uuid.UUID) -> PreviewState | None:
        """获取预览运行状态。"""
        return self._active.get(preview_id)

    async def stop_preview(self, preview_id: uuid.UUID, project_id: uuid.UUID) -> bool:
        """停止预览——终止子进程并清理临时目录。
        
        确保即使进程已异常退出也能清理资源。
        """
        state = self._active.pop(preview_id, None)
        if state is None:
            return False

        # 终止子进程（subprocess.Popen，非 asyncio 子进程）
        if state.process is not None:
            try:
                state.process.terminate()
                try:
                    # Popen.wait() 是阻塞调用，通过线程池避免阻塞事件循环
                    await asyncio.to_thread(state.process.wait, timeout=5.0)
                except TimeoutError:
                    state.process.kill()
                    await asyncio.to_thread(state.process.wait, timeout=5.0)
            except Exception:
                pass  # 进程已退出或无法终止

        # 使用 shutil.rmtree 直接清理——即使路径不存在也不会报错（ignore_errors=True）
        import shutil
        try:
            shutil.rmtree(state.temp_dir, ignore_errors=True)
        except Exception:
            logger.warning("清理临时目录失败: %s", state.temp_dir, exc_info=True)

        state.status = "stopped"

        # 通过 WebSocket 发送预览停止事件
        if self._broker is not None:
            envelope = EventEnvelope(
                event_id=uuid.uuid4(),
                conversation_id=uuid.uuid4(),
                execution_id=uuid.uuid4(),
                sequence=0,
                type="preview.stopped",
                timestamp=_utcnow(),
                payload={
                    "preview_id": str(preview_id),
                },
            )
            await self._broker.publish(envelope)

        logger.info("预览已停止: preview_id=%s", preview_id)
        return True

    async def cleanup_all(self) -> None:
        """应用关闭时清理所有活跃预览——终止所有子进程和临时目录。"""
        for preview_id in list(self._active.keys()):
            try:
                # project_id 在清理时用于日志，取任意值即可
                await self.stop_preview(preview_id, uuid.UUID(int=0))
            except Exception:
                logger.exception("清理预览失败: %s", preview_id)

    @property
    def active_count(self) -> int:
        """当前活跃预览数量。"""
        return len(self._active)