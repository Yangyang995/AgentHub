"""Prometheus 指标端点——仅开发环境默认暴露。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from agenthub.core.metrics import get_metrics_response

router = APIRouter(prefix="/metrics", tags=["observability"])


@router.get("")
async def metrics(request: Request) -> Response:
    """返回 Prometheus 格式的指标数据。

    在生产环境中，此端点应在反向代理层面限制访问。
    """
    settings = request.app.state.settings
    if settings.environment == "production":
        # 生产环境默认不暴露 /metrics，可通过环境变量覆盖
        from os import getenv
        if getenv("AGENTHUB_EXPOSE_METRICS", "").lower() != "true":
            return Response(
                content='{"error_code":"NOT_FOUND","message":"指标端点未启用"}',
                media_type="application/json",
                status_code=404,
            )

    body, content_type = get_metrics_response()
    return Response(content=body, media_type=content_type)
