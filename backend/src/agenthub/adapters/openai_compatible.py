"""OpenAI 兼容 Chat Completions 流式 Adapter。"""

from __future__ import annotations

import asyncio
import json
import time
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
from agenthub.core.logging import get_logger
from agenthub.core.metrics import (
    adapter_calls_total,
    adapter_execution_duration_seconds,
)

logger = get_logger(__name__)


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
        perf_start = time.perf_counter()
        final_status: Literal["succeeded", "failed", "cancelled"] = "failed"
        error_code: AdapterErrorCode | None = None
        error_message: str | None = None
        received_content = False

        logger.info("adapter.stream.start",
                     execution_id=str(task.execution_id),
                     model=self._model,
                     message_len=len(task.message_content))

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
                        "max_tokens": 4096,
                    },
                ) as response:
                    self._active_response = response
                    if response.status_code >= 400:
                        error_code, error_message = self._http_error(response.status_code)
                        logger.warning("adapter.stream.http_error",
                                        status_code=response.status_code,
                                        error_code=error_code)
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
                            received_content = True
                            yield ContentDeltaEvent(
                                execution_id=task.execution_id,
                                sequence=sequence,
                                delta=delta,
                                content_type="markdown",
                            )
                            sequence += 1
            if received_content and error_code is None:
                final_status = "succeeded"
            elif not received_content and error_code is None and final_status != "cancelled":
                error_code = AdapterErrorCode.INVALID_RESPONSE
                error_message = "模型未返回内容"
                logger.warning("adapter.stream.empty_response",
                               execution_id=str(task.execution_id))
        except asyncio.CancelledError:
            final_status = "cancelled"
            error_code = AdapterErrorCode.CANCELLED
            error_message = "执行已取消"
            logger.info("adapter.stream.cancelled",
                        execution_id=str(task.execution_id))
        except httpx.TimeoutException:
            error_code = AdapterErrorCode.TIMEOUT
            error_message = "模型服务响应超时"
            logger.warning("adapter.stream.timeout",
                           execution_id=str(task.execution_id))
        except httpx.HTTPError as exc:
            error_code = AdapterErrorCode.UNAVAILABLE
            error_message = "模型服务网络错误"
            logger.warning("adapter.stream.network_error",
                           execution_id=str(task.execution_id),
                           error=str(exc))
        except Exception:
            error_code = AdapterErrorCode.INTERNAL_ERROR
            error_message = "适配器内部错误"
            logger.exception("adapter.stream.internal_error",
                             execution_id=str(task.execution_id))

        try:
            self._active_response = None
        except Exception:
            pass

        if error_code is not None:
            final_status = "cancelled" if error_code == AdapterErrorCode.CANCELLED else "failed"
            yield ExecutionErrorEvent(
                execution_id=task.execution_id,
                sequence=sequence,
                error_code=error_code,
                error_message=error_message or "",
                recoverable=False,
            )
            sequence += 1

        elapsed = time.perf_counter() - perf_start
        adapter_calls_total.labels(
            adapter="openai_compatible", status=final_status
        ).inc()
        adapter_execution_duration_seconds.labels(adapter="openai_compatible").observe(elapsed)

        logger.info("adapter.stream.complete",
                     execution_id=str(task.execution_id),
                     status=final_status,
                     duration_ms=int(elapsed * 1000),
                     received_content=received_content)

        yield ExecutionStatusEvent(
            execution_id=task.execution_id,
            sequence=sequence,
            status=final_status,
            message=None if final_status == "succeeded" else final_status,
        )
        sequence += 1
        self._last_result = AgentResult(
            execution_id=task.execution_id,
            status=final_status,
            artifacts=[],
            total_tokens=None,
            duration_ms=int(elapsed * 1000),
            error_code=error_code,
            error_message=error_message,
        )

    def _build_rag_context_msg(self, context: dict[str, object]) -> str | None:
        """从任务上下文中提取 RAG 信息组装为 System 提示。

        不包含凭据或隐私路径——只序列化 user_preferences、conversation_summaries
        和 knowledge_context 的数量摘要。
        """
        parts: list[str] = []
        user_prefs = context.get("user_preferences")
        if isinstance(user_prefs, dict):
            for k, v in user_prefs.items():
                parts.append(f"用户偏好 {k} = {v}")
        summaries = context.get("conversation_summaries")
        if isinstance(summaries, list) and summaries:
            parts.append("[历史对话摘要]")
            for s in summaries:
                if isinstance(s, str):
                    parts.append("- " + s)
                elif isinstance(s, dict) and "content" in s:
                    parts.append("- " + str(s["content"]))
        kb = context.get("knowledge_context")
        if isinstance(kb, list) and kb:
            parts.append(f"[项目知识库检索] 已从项目知识库检索到{len(kb)}条相关资料（附加在用户消息中）")
        if not parts:
            return None
        result = "\n\n".join(parts)
        return result

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
        # 注入 RAG 上下文（会话摘要 + 用户偏好 + 知识库提示）
        rag_msg = self._build_rag_context_msg(task.context)
        if rag_msg:
            base.append({"role": "system", "content": rag_msg})
        history = task.context.get("messages")
        if isinstance(history, list) and all(
            isinstance(item, dict)
            and item.get("role") in {"user", "assistant", "system"}
            and isinstance(item.get("content"), str)
            for item in history
        ):
            msgs = base + [
                {"role": str(item["role"]), "content": str(item["content"])} for item in history
            ]
            # 将知识库内容注入到最后一条用户消息中
            kb_context = task.context.get("knowledge_context")
            if kb_context and isinstance(kb_context, list) and msgs:
                kb_text = "\n\n[参考知识库内容]\n"
                for kb_item in kb_context:
                    kb_text += "--- {} ---\n{}\n".format(kb_item.get('file', ''), kb_item.get('content', ''))
                kb_text += "\n请基于以上知识库内容回答用户问题。如果知识库中没有相关信息，可以基于你自己的知识回答。"
                for i in range(len(msgs) - 1, -1, -1):
                    if msgs[i]["role"] == "user":
                        msgs[i]["content"] = kb_text + "\n\n[用户问题]\n" + msgs[i]["content"]
                        break
            return msgs
        # 无历史消息时，将知识库内容注入用户消息
        kb_context = task.context.get("knowledge_context")
        if kb_context and isinstance(kb_context, list):
            kb_text = "\n\n[参考知识库内容]\n"
            for kb_item in kb_context:
                kb_text += "--- {} ---\n{}\n".format(kb_item.get('file', ''), kb_item.get('content', ''))
            kb_text += "\n请基于以上知识库内容回答用户问题。如果知识库中没有相关信息，可以基于你自己的知识回答。"
            user_content = kb_text + "\n\n[用户问题]\n" + task.message_content
            return [*base, {"role": "user", "content": user_content}]
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


