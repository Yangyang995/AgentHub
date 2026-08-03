"""Agent 多步推理管线基类。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from agenthub.adapters.protocol import (
    AgentEvent,
    AgentTask,
    ContentDeltaEvent,
    ExecutionErrorEvent,
    ExecutionStatusEvent,
)


class BaseAgentRunner:
    """Agent 多步推理管线基类。

    包装 OpenAICompatibleAdapter，实现 think-act 两阶段管线。
    对外遵循 AgentAdapter 协议（run + cancel），ChatService 无感知。
    """

    def __init__(self, adapter_factory, system_prompt: str | None):
        self._factory = adapter_factory
        self._system_prompt = system_prompt
        self._active = None

    def cancel(self, execution_id: uuid.UUID) -> None:
        if self._active is not None:
            self._active.cancel(execution_id)

    async def run(self, task: AgentTask):
        raise NotImplementedError

    async def _think(self, task: AgentTask, instruction: str, sp: str | None = None) -> str:
        """内部推理——完整收集LLM响应，不流式输出。"""
        adapter = self._factory(sp)
        self._active = adapter
        try:
            # 保留原有对话历史，把指令追加到最后
            history = task.context.get("messages", []) if task.context else []
            messages = list(history) + [{"role": "user", "content": instruction}]
            think_task = task.model_copy(update={
                "context": {"messages": messages}
            })
            result = []
            async for event in adapter.run(think_task):
                if isinstance(event, ContentDeltaEvent):
                    result.append(event.delta)
            return "".join(result).strip()
        finally:
            self._active = None

    async def _act(self, task: AgentTask, messages):
        """执行步骤——流式输出给用户。"""
        adapter = self._factory(self._system_prompt)
        self._active = adapter
        try:
            # 保留原有对话历史，把新消息追加到最后
            history = task.context.get("messages", []) if task.context else []
            full_messages = list(history) + list(messages)
            act_task = task.model_copy(update={"context": {"messages": full_messages}})
            seq = 0
            yield ExecutionStatusEvent(
                execution_id=task.execution_id, sequence=seq, status="running"
            )
            seq += 1
            succeeded = False
            async for event in adapter.run(act_task):
                if isinstance(event, ExecutionStatusEvent) and event.status == "succeeded":
                    succeeded = True
                event = event.model_copy(update={"sequence": seq})
                seq += 1
                yield event
            if not succeeded:
                yield ExecutionStatusEvent(
                    execution_id=task.execution_id, sequence=seq,
                    status="failed", message="执行未完成"
                )
        finally:
            self._active = None
