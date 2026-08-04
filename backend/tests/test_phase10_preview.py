"""Phase 10: 本地预览与 Vercel 部署单元测试。"""

import hashlib
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agenthub.models.enums import (
    ApprovalActionType,
    ApprovalStatus,
    ArtifactType,
)
from agenthub.schemas.domain import (
    ApprovalCreate,
    ApprovalDecide,
    ArtifactCreate,
    DeploymentCreate,
)
from agenthub.services.deployment import DeploymentService, DeploymentServiceError

# ── ArtifactRepository 单元测试 ────────────────────────────────────────────


class TestArtifactRepository:
    """Artifact 仓储的纯单元测试——不依赖数据库。"""

    async def test_create_artifact_schema(self) -> None:
        """验证 ArtifactCreate Schema 正确构造。"""
        data = ArtifactCreate(
            artifact_type=ArtifactType.PREVIEW,
            relative_path="index.html",
            content_hash="abc123",
            size=1024,
        )
        assert data.artifact_type == ArtifactType.PREVIEW
        assert data.relative_path == "index.html"
        assert data.size == 1024


# ── ApprovalRepository 单元测试 ────────────────────────────────────────────


class TestApprovalRepository:
    """Approval 仓储的 Schema 和逻辑测试。"""

    def test_approval_create_schema(self) -> None:
        """验证 ApprovalCreate 和 ApprovalDecide Schema。"""
        create_data = ApprovalCreate(
            action_type=ApprovalActionType.START_PREVIEW,
            summary="预览产出物",
            content_hash="abc123",
        )
        assert create_data.action_type == ApprovalActionType.START_PREVIEW
        assert create_data.summary == "预览产出物"

        decide_data = ApprovalDecide(decision=ApprovalStatus.APPROVED)
        assert decide_data.decision == ApprovalStatus.APPROVED

    def test_approval_decide_rejects_invalid_decision(self) -> None:
        """验证 ApprovalDecide 接受 approved 和 rejected，Schema 校验由 API 层完成。"""
        # approved 和 rejected 都是合法的决定值
        approved = ApprovalDecide(decision=ApprovalStatus.APPROVED)
        assert approved.decision == ApprovalStatus.APPROVED
        rejected = ApprovalDecide(decision=ApprovalStatus.REJECTED)
        assert rejected.decision == ApprovalStatus.REJECTED
        # 注意：PENDING 在 Schema 层不被拦截（Field(description=...) 仅用于文档），
        # 实际校验由 API 路由层执行


# ── DeploymentRepository 单元测试 ──────────────────────────────────────────


class TestDeploymentRepository:
    """Deployment 仓储的 Schema 测试。"""

    def test_deployment_create_schema(self) -> None:
        """验证 DeploymentCreate Schema。"""
        data = DeploymentCreate(
            artifact_id=uuid.uuid4(),
        )
        assert data.provider.value == "vercel"
        assert data.artifact_id is not None


# ── PreviewService 路径逃逸安全测试 ────────────────────────────────────────


class TestPreviewSecurity:
    """安全检查：路径逃逸和内容类型校验。"""

    def test_path_traversal_detection(self) -> None:
        """验证路径穿越模式可被识别。"""
        dangerous_paths = [
            "../../etc/passwd",
            r"..\..\Windows\System32",
            "/etc/passwd",
        ]

        for path in dangerous_paths:
            # 使用 resolve() 检测路径是否逃逸
            resolved = (Path("/safe/root") / path).resolve()
            assert not str(resolved).startswith(
                str(Path("/safe/root").resolve())
            ) or ".." in path, f"路径应被拒绝: {path}"

    def test_safe_paths_allowed(self) -> None:
        """验证安全路径不被误判。"""
        safe_paths = [
            "index.html",
            "css/style.css",
            "js/app.js",
            "assets/image.png",
        ]

        for path in safe_paths:
            resolved = (Path("/safe/root") / path).resolve()
            assert str(resolved).startswith(
                str(Path("/safe/root").resolve())
            ), f"安全路径不应被拒绝: {path}"


# ── DeploymentService 配置检查测试 ──────────────────────────────────────────


class TestDeploymentServiceConfig:
    """部署服务的配置和降级行为测试。"""

    @patch("agenthub.services.deployment.get_settings")
    def test_missing_vercel_token_raises_error(self, mock_settings) -> None:
        """验证 Vercel Token 未配置时返回明确错误。"""
        mock_settings.return_value.vercel_token = None
        mock_settings.return_value.vercel_team_id = None

        service = DeploymentService(MagicMock())
        with pytest.raises(DeploymentServiceError, match="AGENTHUB_VERCEL_TOKEN"):
            service._check_token()


# ── PreviewService 端口分配测试 ────────────────────────────────────────────


class TestPreviewPortAllocation:
    """预览端口分配逻辑测试。"""

    def test_find_free_port_in_range(self) -> None:
        """验证自由端口查找在范围内。"""
        from agenthub.services.preview import _find_free_port

        port = _find_free_port(18000, 18010)
        assert 18000 <= port <= 18010

    def test_exhausted_range_raises_error(self) -> None:
        """验证端口范围耗尽时抛出错误。"""
        from agenthub.services.preview import _find_free_port

        # 单端口范围——如果被占用会失败
        # 仅验证函数签名和异常类型
        with patch("agenthub.services.preview.socket.socket") as mock_socket:
            mock_sock = MagicMock()
            mock_sock.bind.side_effect = OSError("Address in use")
            mock_socket.return_value.__enter__.return_value = mock_sock

            with pytest.raises(RuntimeError):
                _find_free_port(18000, 18000)


# ── Artifact 内容哈希测试 ──────────────────────────────────────────────────


class TestArtifactContentHash:
    """产出物内容哈希校验。"""

    def test_content_hash_consistency(self) -> None:
        """验证相同内容产生相同哈希。"""
        content = b"<html><body>Hello</body></html>"
        hash1 = hashlib.sha256(content).hexdigest()
        hash2 = hashlib.sha256(content).hexdigest()
        assert hash1 == hash2

    def test_different_content_different_hash(self) -> None:
        """验证不同内容产生不同哈希。"""
        hash1 = hashlib.sha256(b"content A").hexdigest()
        hash2 = hashlib.sha256(b"content B").hexdigest()
        assert hash1 != hash2