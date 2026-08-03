"""OpenAI 兼容 Adapter 的流式协议、历史消息和安全错误测试。"""

import json
import uuid
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from agenthub.adapters.openai_compatible import OpenAICompatibleAdapter
from agenthub.adapters.protocol import (
    AdapterErrorCode,
    AgentEvent,
    AgentTask,
    ContentDeltaEvent,
    ExecutionErrorEvent,
    ExecutionStatusEvent,
)


def _task(*, messages: list[dict[str, str]] | None = None) -> AgentTask:
    """构造不依赖数据库的 Adapter 输入。"""
    context: dict[str, object] = {}
    if messages is not None:
        context["messages"] = messages
    return AgentTask(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        message_content="第二个问题",
        working_dir=Path.cwd(),
        context=context,
    )


async def _events(adapter: OpenAICompatibleAdapter, task: AgentTask) -> list[AgentEvent]:
    return [event async for event in adapter.run(task)]


@pytest.mark.asyncio
async def test_streams_deepseek_compatible_deltas_and_sends_history() -> None:
    """DeepSeek 兼容 SSE 应转换为连续增量，并保留多轮消息顺序。"""
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        body = "\n".join(
            [
                'data: {"choices":[{"delta":{"content":"你好"}}]}',
                'data: {"choices":[{"delta":{"content":"，世界"}}]}',
                "data: [DONE]",
                "",
            ]
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    adapter = OpenAICompatibleAdapter(
        base_url="https://example.invalid/v1",
        api_key=SecretStr("test-secret"),
        model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )
    history = [
        {"role": "user", "content": "第一个问题"},
        {"role": "assistant", "content": "第一个回答"},
        {"role": "user", "content": "第二个问题"},
    ]
    events = await _events(adapter, _task(messages=history))

    deltas = [event.delta for event in events if isinstance(event, ContentDeltaEvent)]
    statuses = [event.status for event in events if isinstance(event, ExecutionStatusEvent)]
    assert deltas == ["你好", "，世界"]
    assert statuses == ["running", "succeeded"]
    assert captured["authorization"] == "Bearer test-secret"
    assert captured["payload"]["model"] == "deepseek-chat"
    assert captured["payload"]["stream"] is True
    # RAG 上下文注入后 messages 包含额外 system 消息，历史消息应在末尾
    msgs = captured["payload"]["messages"]
    assert len(msgs) >= len(history)
    # 验证历史消息完整保留（在末尾）
    assert msgs[-len(history):] == history


@pytest.mark.asyncio
async def test_authentication_error_is_stable_and_does_not_expose_response() -> None:
    """认证失败只返回稳定安全消息，不透传供应商响应中的敏感诊断。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"error": "token=private-provider-detail"})

    adapter = OpenAICompatibleAdapter(
        base_url="https://example.invalid/v1",
        api_key=SecretStr("test-secret"),
        model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )
    events = await _events(adapter, _task())

    errors = [event for event in events if isinstance(event, ExecutionErrorEvent)]
    assert len(errors) == 1
    assert errors[0].error_code == AdapterErrorCode.PERMISSION_DENIED
    assert errors[0].error_message == "模型服务认证失败，请检查 API Key"
    assert "private-provider-detail" not in str(events)


@pytest.mark.asyncio
async def test_empty_success_stream_is_rejected() -> None:
    """只有结束标记而没有文本时必须失败，避免再次生成空白 Agent 消息。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, text="data: [DONE]\n\n")

    adapter = OpenAICompatibleAdapter(
        base_url="https://example.invalid/v1",
        api_key=SecretStr("test-secret"),
        model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )
    events = await _events(adapter, _task())

    errors = [event for event in events if isinstance(event, ExecutionErrorEvent)]
    statuses = [event.status for event in events if isinstance(event, ExecutionStatusEvent)]
    assert len(errors) == 1
    assert errors[0].error_code == AdapterErrorCode.INVALID_RESPONSE
    assert statuses == ["running", "failed"]
