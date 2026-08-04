"""FastAPI 中间件——请求追踪、速率限制、指标记录和统一异常映射。

在请求入口:
1. 注入/提取 X-Request-ID 并设置到 contextvars
2. 检查全局和单 IP 速率限制
3. 记录请求耗时到 Prometheus
4. 将 AppError 和未捕获异常映射为统一 JSON 响应

Usage in main.py:
    from agenthub.api.middleware import register_middleware
    register_middleware(app)
"""

from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from agenthub.core.config import get_settings
from agenthub.core.exceptions import AppError
from agenthub.core.limits import get_rate_limiter
from agenthub.core.logging import generate_request_id, get_logger, request_id_var
from agenthub.core.metrics import (
    http_request_duration_seconds,
    http_requests_total,
)

logger = get_logger(__name__)


class _RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求注入 X-Request-ID——取自请求头或生成新 UUID 前缀。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", generate_request_id())
        request_id_var.set(request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class _RateLimitMiddleware(BaseHTTPMiddleware):
    """在请求进入路由前检查全局和 IP 速率限制。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        limiter = get_rate_limiter()
        try:
            await limiter.check_global()
            await limiter.check_ip(client_ip)
        except AppError as exc:
            request_id = request_id_var.get()
            exc.request_id = None
            http_requests_total.labels(
                method=request.method,
                path=_route_label(request),
                status=str(exc.status_code),
            ).inc()
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "error_code": exc.error_code,
                    "message": exc.message,
                    "request_id": request_id,
                },
            )
        return await call_next(request)


class _MetricsMiddleware(BaseHTTPMiddleware):
    """记录每个 HTTP 请求的耗时和计数。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start

        path_label = _route_label(request)
        http_request_duration_seconds.labels(
            method=request.method, path=path_label
        ).observe(elapsed)
        http_requests_total.labels(
            method=request.method,
            path=path_label,
            status=str(response.status_code),
        ).inc()

        return response


def _route_label(request: Request) -> str:
    """从请求中提取路由模板作为指标 label，避免高基数 URL。"""
    scope = request.scope
    route = scope.get("route")
    if route is not None:
        return getattr(route, "path", request.url.path)
    return request.url.path


def _register_exception_handlers(app: FastAPI) -> None:
    """将 AppError 及其子类映射为统一 JSON 响应。"""

    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = request_id_var.get()
        http_requests_total.labels(
            method=request.method if request else "UNKNOWN",
            path=_route_label(request) if request else "UNKNOWN",
            status=str(exc.status_code),
        ).inc()
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message": exc.message,
                "request_id": request_id,
            },
        )

    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = request_id_var.get()
        logger.exception(
            "未捕获异常",
            path=request.url.path if request else "UNKNOWN",
            exc_type=type(exc).__name__,
        )
        http_requests_total.labels(
            method=request.method if request else "UNKNOWN",
            path=_route_label(request) if request else "UNKNOWN",
            status="500",
        ).inc()
        return JSONResponse(
            status_code=500,
            content={
                "error_code": "INTERNAL_ERROR",
                "message": "服务器内部错误，已记录日志",
                "request_id": request_id,
            },
        )

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, unhandled_handler)


def register_middleware(app: FastAPI) -> None:
    """为 FastAPI 应用注册所有安全/可观测性中间件。

    调用顺序即为中间件执行顺序（洋葱模型）：
    RequestID -> RateLimit -> Metrics -> 路由 -> 错误处理
    """
    app.add_middleware(_RequestIDMiddleware)
    app.add_middleware(_RateLimitMiddleware)
    app.add_middleware(_MetricsMiddleware)
    _register_exception_handlers(app)
    logger.info("安全与可观测性中间件已注册")
