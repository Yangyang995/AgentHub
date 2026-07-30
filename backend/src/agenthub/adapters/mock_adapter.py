"""确定性 Mock Adapter 实现。

Mock Adapter 用于自动化测试和开发调试，通过预设脚本控制 Agent 行为。
不发起任何真实网络或子进程调用，结果完全由脚本决定。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from agenthub.adapters.protocol import (
    AdapterErrorCode,
    AdapterHealth,
    AgentEvent,
    AgentResult,
    AgentTask,
    ArtifactCreatedEvent,
    ArtifactInfo,
    ContentDeltaEvent,
    ExecutionErrorEvent,
    ExecutionStatusEvent,
    ExecutionUsageEvent,
)

# ------------------------------------------------------------------------------
# 脚本步骤定义
# ------------------------------------------------------------------------------


class MockScriptStep(BaseModel):
    """执行步骤。

    每一步指定动作类型和相关参数。
    """

    action: Literal["delta", "artifact", "usage", "error", "delay", "cancel_check"] = Field(
        description="动作类型"
    )
    # delta 动作参数
    content: str | None = Field(default=None, description="文本内容")
    content_type: str = Field(default="text", description="内容类型")
    # artifact 动作参数
    artifact_type: str | None = Field(default=None, description="产物类型")
    relative_path: str | None = Field(default=None, description="产物相对路径")
    artifact_content: str | None = Field(
        default=None,
        description="产物内容（Mock 中直接作为文件内容）",
    )
    # usage 动作参数
    token_count: int | None = Field(default=None, description="Token 数量")
    call_count: int = Field(default=1, ge=0, description="调用次数")
    # error 动作参数
    error_code: AdapterErrorCode | None = Field(default=None, description="错误码")
    error_message: str | None = Field(default=None, description="错误消息")
    recoverable: bool = Field(default=False, description="是否可恢复")
    # delay 动作参数
    milliseconds: int = Field(
        default=0,
        ge=0,
        le=60000,
        description="延迟毫秒数，上限 60 秒",
    )


class MockAdapterScript(BaseModel):
    """Mock 执行脚本。

    script 列表按顺序执行每一步。
    无 error 步骤时 Adapter 返回 succeeded 结果。
    遇到 error 步骤时标记为 failed 并携带对应错误信息。
    cancel_check 步骤检查外部取消信号，已取消则提前终止。
    """

    adapter_name: str = Field(default="mock-agent", description="Mock Agent 名称")
    script: list[MockScriptStep] = Field(default_factory=list, description="有序步骤列表")
    default_delay_ms: int = Field(
        default=0,
        ge=0,
        le=1000,
        description="步骤间默认延迟毫秒数，上限 1 秒",
    )


# ------------------------------------------------------------------------------
# Mock Adapter 实现
# ------------------------------------------------------------------------------


class MockAdapter:
    """确定性 Mock Adapter。

    根据 MockAdapterScript 产生完全可控的 AgentEvent 序列。
    支持 cancel_event 异步取消和 delay/cancel_check 步骤模拟耗时操作。
    """

    def __init__(self, script: MockAdapterScript) -> None:
        """初始化 Mock Adapter。

        Args:
            script: 预设执行脚本，不可为空。
        """
        self._script = script
        self._cancel_event = asyncio.Event()
        self._last_result: AgentResult | None = None

    @property
    def last_result(self) -> AgentResult | None:
        """最近一次执行的最终结果。"""
        return self._last_result

    async def healthcheck(self) -> AdapterHealth:
        """Mock 健康检查始终返回健康。"""
        return AdapterHealth(
            healthy=True,
            adapter_type="mock",
            version="1.0.0-mock",
            message=f"Mock adapter ready: {self._script.adapter_name}",
        )

    def run(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        """执行脚本，返回异步事件生成器。

        每次调用重置 last_result 为 None。
        """
        self._last_result = None
        return self._run_script(task)

    def _run_script(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        """脚本执行核心逻辑。

        遍历 script 列表，按步骤类型产生对应事件。
        """

        async def event_generator() -> AsyncIterator[AgentEvent]:
            nonlocal self
            sequence = 0
            start_time = datetime.now(UTC)
            total_tokens: int | None = 0
            artifacts: list[ArtifactInfo] = []
            final_status: Literal["succeeded", "failed", "cancelled"] = "succeeded"
            final_error_code: AdapterErrorCode | None = None
            final_error_message: str | None = None

            # 发送 running 状态事件
            seq = sequence
            sequence += 1
            running_event = ExecutionStatusEvent(
                execution_id=task.execution_id,
                sequence=seq,
                status="running",
            )
            yield running_event

            # 遍历执行步骤
            script_steps = self._script.script
            for step in script_steps:
                # 检查取消信号
                if self._cancel_event.is_set():
                    final_status = "cancelled"
                    final_error_code = AdapterErrorCode.CANCELLED
                    final_error_message = "外部取消"
                    break

                # 步骤间默认延迟
                if self._script.default_delay_ms > 0:
                    await asyncio.sleep(self._script.default_delay_ms / 1000.0)

                try:
                    if step.action == "delta":
                        seq = sequence
                        sequence += 1
                        delta_event = ContentDeltaEvent(
                            execution_id=task.execution_id,
                            sequence=seq,
                            delta=step.content or "",
                            content_type=step.content_type,
                        )
                        yield delta_event

                    elif step.action == "artifact":
                        seq = sequence
                        sequence += 1
                        artifact_content = step.artifact_content or ""
                        content_hash = hashlib.sha256(
                            artifact_content.encode()
                        ).hexdigest()
                        artifact_size = len(artifact_content.encode("utf-8"))
                        artifact_info = ArtifactInfo(
                            artifact_id=uuid.uuid4(),
                            artifact_type=step.artifact_type or "file",
                            relative_path=step.relative_path or "output.txt",
                            content_hash=content_hash,
                            size=artifact_size,
                        )
                        artifacts.append(artifact_info)
                        yield ArtifactCreatedEvent(
                            execution_id=task.execution_id,
                            sequence=seq,
                            artifact_id=artifact_info.artifact_id,
                            artifact_type=artifact_info.artifact_type,
                            relative_path=artifact_info.relative_path,
                            content_hash=artifact_info.content_hash,
                            size=artifact_info.size,
                        )

                    elif step.action == "usage":
                        if step.token_count is not None:
                            total_tokens = (total_tokens or 0) + step.token_count
                        seq = sequence
                        sequence += 1
                        usage_event = ExecutionUsageEvent(
                            execution_id=task.execution_id,
                            sequence=seq,
                            token_count=step.token_count,
                            call_count=step.call_count,
                            duration_ms=None,
                        )
                        yield usage_event

                    elif step.action == "error":
                        seq = sequence
                        sequence += 1
                        error_event = ExecutionErrorEvent(
                            execution_id=task.execution_id,
                            sequence=seq,
                            error_code=step.error_code or AdapterErrorCode.UNKNOWN,
                            error_message=step.error_message or "未知错误",
                            recoverable=step.recoverable,
                        )
                        yield error_event
                        final_status = "failed"
                        final_error_code = step.error_code or AdapterErrorCode.UNKNOWN
                        final_error_message = step.error_message or "未知错误"
                        break

                    elif step.action == "delay":
                        delay_remaining = step.milliseconds / 1000.0
                        check_interval = 0.01
                        while delay_remaining > 0:
                            if self._cancel_event.is_set():
                                final_status = "cancelled"
                                final_error_code = AdapterErrorCode.CANCELLED
                                final_error_message = "执行被取消"
                                break
                            step_sleep = min(check_interval, delay_remaining)
                            await asyncio.sleep(step_sleep)
                            delay_remaining -= step_sleep
                        if final_status == "cancelled":
                            break

                    elif step.action == "cancel_check":
                        if self._cancel_event.is_set():
                            final_status = "cancelled"
                            final_error_code = AdapterErrorCode.CANCELLED
                            final_error_message = "执行被取消"
                            break

                except asyncio.CancelledError:
                    final_status = "cancelled"
                    final_error_code = AdapterErrorCode.CANCELLED
                    final_error_message = "任务被取消"
                    break

            # 未失败且未取消时标记为成功
            if final_status not in ("failed", "cancelled"):
                final_status = "succeeded"

            # 发送最终状态事件
            seq = sequence
            sequence += 1
            end_time = datetime.now(UTC)
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

            status_event = ExecutionStatusEvent(
                execution_id=task.execution_id,
                sequence=seq,
                status=final_status,
                message=final_error_message if final_status != "succeeded" else None,
            )
            yield status_event

            # 构造并保存最终结果
            result = AgentResult(
                execution_id=task.execution_id,
                status=final_status,
                artifacts=artifacts,
                total_tokens=(
                    total_tokens if total_tokens is not None and total_tokens > 0 else None
                ),
                duration_ms=duration_ms,
                error_code=final_error_code,
                error_message=final_error_message,
            )
            self._last_result = result

        return event_generator()

    def cancel(self, execution_id: uuid.UUID) -> None:
        """设置取消信号，中断当前执行。"""
        self._cancel_event.set()

    def reset_cancel(self) -> None:
        """重置取消信号，允许新一次执行。"""
        self._cancel_event.clear()
