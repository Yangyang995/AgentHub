"""Prometheus 指标——HTTP、WebSocket、Adapter 和审批相关的计数与直方图。

Counter:
- http_requests_total{method,path,status}
- ws_messages_total{conversation_id,direction}
- adapter_calls_total{adapter,status}
- approval_actions_total{action_type,decision}

Histogram:
- http_request_duration_seconds{method,path}
- adapter_execution_duration_seconds{adapter}
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from prometheus_client.registry import CollectorRegistry

# ── 独立注册表——避免与全局默认注册表冲突 ────────────────────────────────

_registry = CollectorRegistry(auto_describe=True)

# ── HTTP 指标 ─────────────────────────────────────────────────────────────

http_requests_total = Counter(
    "http_requests_total",
    "HTTP 请求计数",
    labelnames=["method", "path", "status"],
    registry=_registry,
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    labelnames=["method", "path"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=_registry,
)

# ── WebSocket 指标 ────────────────────────────────────────────────────────

ws_messages_total = Counter(
    "ws_messages_total",
    "WebSocket 消息计数",
    labelnames=["conversation_id", "direction"],
    registry=_registry,
)

ws_connections_active = Counter(
    "ws_connections_active",
    "当前活跃 WebSocket 连接数（通过 inc/dec 估算）",
    labelnames=[],
    registry=_registry,
)

# ── Adapter 指标 ──────────────────────────────────────────────────────────

adapter_calls_total = Counter(
    "adapter_calls_total",
    "Adapter 调用计数",
    labelnames=["adapter", "status"],
    registry=_registry,
)

adapter_execution_duration_seconds = Histogram(
    "adapter_execution_duration_seconds",
    "Adapter 执行耗时（秒）",
    labelnames=["adapter"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
    registry=_registry,
)

# ── 审批指标 ──────────────────────────────────────────────────────────────

approval_actions_total = Counter(
    "approval_actions_total",
    "审批动作计数",
    labelnames=["action_type", "decision"],
    registry=_registry,
)


def get_metrics_response() -> tuple[str, str]:
    """返回 (body, content_type) 供 /metrics 端点使用。"""
    return generate_latest(_registry).decode("utf-8"), CONTENT_TYPE_LATEST
