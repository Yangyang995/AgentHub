"""??? Mock Adapter ???

Mock Adapter ???????????????????????????????
??????????????????????????????????
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

# ???????????????????????????????????????????????????????????????????????????
# Mock ????
# ???????????????????????????????????????????????????????????????????????????


class MockScriptStep(BaseModel):
    """Mock ?????????

    ???????????????????
    """

    action: Literal["delta", "artifact", "usage", "error", "delay", "cancel_check"] = Field(
        description="????"
    )
    # delta ????
    content: str | None = Field(default=None, description="??????")
    content_type: str = Field(default="text", description="????")
    # artifact ????
    artifact_type: str | None = Field(default=None, description="?????")
    relative_path: str | None = Field(default=None, description="???????")
    artifact_content: str | None = Field(
        default=None,
        description="??????Mock ??????????????",
    )
    # usage ????
    token_count: int | None = Field(default=None, description="Token ??")
    call_count: int = Field(default=1, ge=0, description="??????")
    # error ????
    error_code: AdapterErrorCode | None = Field(default=None, description="?????")
    error_message: str | None = Field(default=None, description="????")
    recoverable: bool = Field(default=False, description="????????")
    # delay ????
    milliseconds: int = Field(
        default=0,
        ge=0,
        le=60000,
        description="???????????? 60 ?",
    )


class MockAdapterScript(BaseModel):
    """Mock Adapter ????????

    script ????????????
    ????????Adapter ?? succeeded ????????
    ????? error ?????? error ??????fail??
    cancel_check ????????????????
    """

    adapter_name: str = Field(default="mock-agent", description="Mock Agent ????")
    script: list[MockScriptStep] = Field(default_factory=list, description="?????????")
    default_delay_ms: int = Field(
        default=0,
        ge=0,
        le=1000,
        description="??????????????? 1 ?",
    )


# ???????????????????????????????????????????????????????????????????????????
# Mock Adapter ??
# ???????????????????????????????????????????????????????????????????????????


class MockAdapter:
    """??? Mock Agent Adapter?

    ?? MockAdapterScript???????? AgentEvent ??
    ???? cancel_event ???????? delay ? cancel_check ?????????
    """

    def __init__(self, script: MockAdapterScript) -> None:
        """????????? Mock Adapter?

        Args:
            script: ?????????????
        """
        self._script = script
        self._cancel_event = asyncio.Event()
        self._last_result: AgentResult | None = None

    @property
    def last_result(self) -> AgentResult | None:
        """???????????????????????"""
        return self._last_result

    async def healthcheck(self) -> AdapterHealth:
        """Mock ????????"""
        return AdapterHealth(
            healthy=True,
            adapter_type="mock",
            version="1.0.0-mock",
            message=f"Mock adapter ready: {self._script.adapter_name}",
        )

    def run(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        """??????????????

        ??????????? last_result ?????????
        """
        self._last_result = None
        return self._run_script(task)

    def _run_script(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        """??????????????"""

        async def event_generator() -> AsyncIterator[AgentEvent]:
            nonlocal self
            sequence = 0
            start_time = datetime.now(UTC)
            total_tokens: int | None = 0
            artifacts: list[ArtifactInfo] = []
            final_status: Literal["succeeded", "failed", "cancelled"] = "succeeded"
            final_error_code: AdapterErrorCode | None = None
            final_error_message: str | None = None

            # ?? running ??
            seq = sequence
            sequence += 1
            running_event = ExecutionStatusEvent(
                execution_id=task.execution_id,
                sequence=seq,
                status="running",
            )
            yield running_event

            # ??????
            script_steps = self._script.script
            for step in script_steps:
                # ?????????
                if self._cancel_event.is_set():
                    final_status = "cancelled"
                    final_error_code = AdapterErrorCode.CANCELLED
                    final_error_message = "??????"
                    break

                # ???????
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
                        content_str = step.artifact_content or ""
                        content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
                        artifact_size = len(content_str.encode("utf-8"))
                        artifact_id = uuid.uuid4()

                        seq = sequence
                        sequence += 1
                        artifact_event = ArtifactCreatedEvent(
                            execution_id=task.execution_id,
                            sequence=seq,
                            artifact_id=artifact_id,
                            artifact_type=step.artifact_type or "file",
                            relative_path=step.relative_path or "output.txt",
                            content_hash=content_hash,
                            size=artifact_size,
                            metadata={"source": "mock"},
                        )
                        yield artifact_event

                        artifacts.append(
                            ArtifactInfo(
                                artifact_id=artifact_id,
                                artifact_type=step.artifact_type or "file",
                                relative_path=step.relative_path or "output.txt",
                                content_hash=content_hash,
                                size=artifact_size,
                            )
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
                            error_message=step.error_message or "??????",
                            recoverable=step.recoverable,
                        )
                        yield error_event
                        final_status = "failed"
                        final_error_code = step.error_code or AdapterErrorCode.UNKNOWN
                        final_error_message = step.error_message or "??????"
                        break

                    elif step.action == "delay":
                        delay_remaining = step.milliseconds / 1000.0
                        check_interval = 0.01
                        while delay_remaining > 0:
                            if self._cancel_event.is_set():
                                final_status = "cancelled"
                                final_error_code = AdapterErrorCode.CANCELLED
                                final_error_message = "?????????"
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
                            final_error_message = "?????????"
                            break

                except asyncio.CancelledError:
                    final_status = "cancelled"
                    final_error_code = AdapterErrorCode.CANCELLED
                    final_error_message = "???????"
                    break

            # ?????????????/??
            if final_status not in ("failed", "cancelled"):
                final_status = "succeeded"

            # ????????
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

            # ????
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
            # Don't yield after setting result - the generator ends here

        return event_generator()

    def cancel(self, execution_id: uuid.UUID) -> None:
        """???????????????????????????"""
        self._cancel_event.set()

    def reset_cancel(self) -> None:
        """???????????????????"""
        self._cancel_event.clear()
