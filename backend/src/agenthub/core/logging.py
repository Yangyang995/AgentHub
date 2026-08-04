"""结构化脱敏日志——基于 structlog 的 JSON 日志配置。

敏感字段自动替换为 <REDACTED>：Authorization、api_key、token、password、secret。
每行日志包含 timestamp、level、request_id、logger、event。
"""

from __future__ import annotations

import logging
import os
import re
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

_SENSITIVE_KEY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)authorization"),
    re.compile(r"(?i)api[_-]?key"),
    re.compile(r"(?i)token"),
    re.compile(r"(?i)password"),
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)credential"),
]
_REDACTED = "<REDACTED>"


def _sanitize_value(key: str, value: object) -> object:
    """若键命中敏感模式，替换值为 <REDACTED>。"""
    if not isinstance(value, (str, bytes)):
        return value
    for pattern in _SENSITIVE_KEY_PATTERNS:
        if pattern.search(key):
            return _REDACTED
    return value


def _sanitize_event_dict(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, object],
) -> dict[str, object]:
    """遍历事件字典，脱敏敏感字段。"""
    sanitized: dict[str, object] = {}
    for key, value in event_dict.items():
        sanitized[key] = _sanitize_value(key, value)
    return sanitized


def _add_request_id(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, object],
) -> dict[str, object]:
    """为每条日志注入当前请求 ID。"""
    rid = request_id_var.get()
    if rid:
        event_dict.setdefault("request_id", rid)
    return event_dict


def setup_logging(*, json_format: bool = True) -> None:
    """配置结构化日志。

    json_format=True 时输出 JSON 行（适合生产容器）；
    json_format=False 时输出彩色控制台（适合本地开发）。
    """

    shared_processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_request_id,
        _sanitize_event_dict,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    if json_format:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            _add_request_id,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(_resolve_log_level())

    structlog.configure(
        processors=shared_processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def _resolve_log_level() -> int:
    """从环境变量 AGENTHUB_LOG_LEVEL 解析日志级别，默认 INFO。"""
    raw = os.getenv("AGENTHUB_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, raw, None)
    if isinstance(level, int):
        return level
    return logging.INFO


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取已配置 structlog 的结构化日志记录器。"""
    return structlog.get_logger(name or __name__)


def generate_request_id() -> str:
    """生成新的请求追踪 ID，格式为短 UUID 前 12 位前缀。"""
    return uuid.uuid4().hex[:12]
