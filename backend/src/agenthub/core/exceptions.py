"""AgentHub 统一异常体系。

本模块定义了结构化的应用异常基类和子类，每个异常携带稳定 error_code，
由中间件统一映射为客户端可见的 JSON 响应。
"""

from __future__ import annotations

import uuid
from typing import ClassVar


class AppError(Exception):
    """所有应用层异常的基类——携带面向客户端的稳定错误码。

    子类必须定义:
    - status_code: HTTP 状态码
    - error_code: 稳定错误码字符串（snake_case，不含空格）
    - message: 面向用户的描述（不含内部细节）
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[str] = "INTERNAL_ERROR"
    message: str

    def __init__(self, message: str | None = None, *, request_id: uuid.UUID | None = None) -> None:
        self.request_id = request_id
        super().__init__(message or self.message)


class NotFoundError(AppError):
    """请求资源不存在或不属于当前项目。"""

    status_code = 404
    error_code = "NOT_FOUND"
    message = "请求的资源不存在"


class ConflictError(AppError):
    """资源状态冲突（如名称重复）。"""

    status_code = 409
    error_code = "CONFLICT"
    message = "资源状态冲突"


class ValidationError(AppError):
    """请求参数不符合业务约束。"""

    status_code = 422
    error_code = "VALIDATION_ERROR"
    message = "请求参数校验失败"


class RateLimitError(AppError):
    """速率限制——请求过于频繁。"""

    status_code = 429
    error_code = "RATE_LIMITED"
    message = "请求过于频繁，请稍后重试"


class ExecutionError(AppError):
    """Agent 执行相关错误。"""

    status_code = 500
    error_code = "EXECUTION_ERROR"
    message = "Agent 执行失败"


class DeploymentError(AppError):
    """部署相关错误。"""

    status_code = 502
    error_code = "DEPLOYMENT_ERROR"
    message = "部署操作失败"


class SecurityError(AppError):
    """安全边界违规——路径逃逸、权限不足等。"""

    status_code = 403
    error_code = "SECURITY_VIOLATION"
    message = "安全校验未通过"


class ServiceUnavailableError(AppError):
    """外部服务不可用（LLM、Embedding 等）。"""

    status_code = 503
    error_code = "SERVICE_UNAVAILABLE"
    message = "依赖的外部服务暂不可用"
