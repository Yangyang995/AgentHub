"""OpenAI 兼容 Chat Completions 流式 Adapter。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from agenthub.adapters.protocol import (
    AdapterErrorCode,
    AdapterHealth,
    AgentEvent,
    AgentResult,
    AgentTask,
    ContentDeltaEvent,
    ExecutionErrorEvent,
    ExecutionStatusEvent,
)


class OpenAICompatibleAdapter:
    """调用 OpenAI 兼容的 ``/chat/completions`` 流式接口。

    密钥只在请求头中使用，不写入任务上下文、数据库和错误消息。
    Adapter 只负责平台协议到 AgentEvent 的转换，消息持久化和 WebSocket 推送仍由 ChatService 负责。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        model: str,
        system_prompt: str | None = None,
        default_timeout: int = 300,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        # 子 Agent 的 System Prompt，在消息列表最前面作为 system 角色注入
        self._system_prompt = system_prompt
        self._default_timeout = default_timeout
        self._transport = transport
        self._cancel_event = asyncio.Event()
        self._active_response: httpx.Response | None = None
        self._last_result: AgentResult | None = None

    @property
    def last_result(self) -> AgentResult | None:
        """返回最近一次执行的脱敏汇总结果。"""
        return self._last_result

    async def healthcheck(self) -> AdapterHealth:
        """仅检查本地配置完整性，避免健康接口产生计费模型调用。"""
        configured = bool(self._base_url and self._api_key.get_secret_value() and self._model)
        return AdapterHealth(
            healthy=configured,
            adapter_type="openai_compatible",
            version=None,
            message="OpenAI compatible adapter configured" if configured else "Adapter 配置不完整",
        )

    def run(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        """启动一次流式调用；同一实例一次只承载一个执行。"""
        self._cancel_event = asyncio.Event()
        self._last_result = None
        return self._stream(task)

    async def cancel(self, execution_id: uuid.UUID) -> None:
        """请求停止当前流，并关闭活动响应以解除网络读取等待。"""
        del execution_id
        self._cancel_event.set()
        if self._active_response is not None:
            await self._active_response.aclose()

    async def _stream(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        sequence = 0
        started_at = datetime.now(UTC)
        final_status: Literal["succeeded", "failed", "cancelled"] = "failed"
        error_code: AdapterErrorCode | None = None
        error_message: str | None = None
        received_content = False

        yield ExecutionStatusEvent(
            execution_id=task.execution_id,
            sequence=sequence,
            status="running",
        )
        sequence += 1

        try:
            timeout = httpx.Timeout(task.timeout_seconds or self._default_timeout)
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": self._messages(task),
                        "stream": True,
                        "max_tokens": 8192,
                    },
                ) as response:
                    self._active_response = response
                    if response.status_code >= 400:
                        error_code, error_message = self._http_error(response.status_code)
                    else:
                        async for line in response.aiter_lines():
                            if self._cancel_event.is_set():
                                final_status = "cancelled"
                                error_code = AdapterErrorCode.CANCELLED
                                error_message = "执行已取消"
                                break
                            delta = self._parse_sse_delta(line)
                            if delta is None:
                                continue
                            if delta == "[DONE]":
                                continue
                            yield ContentDeltaEvent(
                                execution_id=task.execution_id,
                                sequence=sequence,
                                delta=delta,
                                content_type="markdown",
                            )
                            received_content = True
                            sequence += 1
                        else:
                            # 兼容供应商以关闭流表示完成，但无文本响应不能被保存为空白成功消息。
                            if received_content:
                                final_status = "succeeded"
                            else:
                                error_code = AdapterErrorCode.INVALID_RESPONSE
                                error_message = "模型服务未返回文本内容"
        except httpx.TimeoutException:
            error_code = AdapterErrorCode.TIMEOUT
            error_message = "模型服务响应超时"
        except httpx.RequestError:
            error_code = AdapterErrorCode.UNAVAILABLE
            error_message = "无法连接模型服务"
        except (json.JSONDecodeError, ValueError, TypeError):
            error_code = AdapterErrorCode.INVALID_RESPONSE
            error_message = "模型服务返回了无效的流式响应"
        finally:
            self._active_response = None

        if self._cancel_event.is_set() and final_status != "succeeded":
            final_status = "cancelled"
            error_code = AdapterErrorCode.CANCELLED
            error_message = "执行已取消"
        elif final_status != "succeeded" and error_code is None:
            error_code = AdapterErrorCode.EXECUTION_FAILED
            error_message = "模型执行失败"

        if error_code is not None and final_status != "cancelled":
            yield ExecutionErrorEvent(
                execution_id=task.execution_id,
                sequence=sequence,
                error_code=error_code,
                error_message=error_message or "模型执行失败",
                recoverable=error_code in {AdapterErrorCode.TIMEOUT, AdapterErrorCode.UNAVAILABLE},
            )
            sequence += 1

        yield ExecutionStatusEvent(
            execution_id=task.execution_id,
            sequence=sequence,
            status=final_status,
            message=error_message,
        )

        duration_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
        self._last_result = AgentResult(
            execution_id=task.execution_id,
            status=final_status,
            duration_ms=duration_ms,
            error_code=error_code,
            error_message=error_message,
        )

    def _messages(self, task: AgentTask) -> list[dict[str, str]]:
        """组装发送给 LLM 的消息列表。

        优先级：
        1. 若 task.context 中提供了已持久化的消息历史（messages 列表），使用历史。
        2. 否则使用当前用户消息。

        System Prompt（若已配置）作为第一条 system 消息插入。
        """
        base: list[dict[str, str]] = []
        # 注入 System Prompt（若已配置）
        if self._system_prompt is not None:
            base.append({"role": "system", "content": self._system_prompt})
        history = task.context.get("messages")
        if isinstance(history, list) and all(
            isinstance(item, dict)
            and item.get("role") in {"user", "assistant", "system"}
            and isinstance(item.get("content"), str)
            for item in history
        ):
            return base + [
                {"role": str(item["role"]), "content": str(item["content"])} for item in history
            ]
        return [*base, {"role": "user", "content": task.message_content}]

    def _parse_sse_delta(self, line: str) -> str | None:
        """解析一个 SSE data 帧，只接收 Chat Completions 的文本增量。"""
        if not line or line.startswith(":") or not line.startswith("data:"):
            return None
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            return data
        payload: Any = json.loads(data)
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            return None
        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice, dict) else None
        content = delta.get("content") if isinstance(delta, dict) else None
        return content if isinstance(content, str) and content else None

    def _http_error(self, status_code: int) -> tuple[AdapterErrorCode, str]:
        """按状态码映射稳定错误，不读取可能包含敏感信息的响应正文。"""
        if status_code in {401, 403}:
            return AdapterErrorCode.PERMISSION_DENIED, "模型服务认证失败，请检查 API Key"
        if status_code == 429:
            return AdapterErrorCode.UNAVAILABLE, "模型服务当前限流，请稍后重试"
        if 400 <= status_code < 500:
            return AdapterErrorCode.CONFIG_ERROR, "模型请求配置无效"
        return AdapterErrorCode.UNAVAILABLE, "模型服务暂时不可用"
