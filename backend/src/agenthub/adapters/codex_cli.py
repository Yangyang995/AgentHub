"""Codex CLI Adapter ???

?? asyncio ????? Codex CLI????????????????

?????
- ?? shell=True?????????
- cwd ????????? worktree ??
- ???????100 KB????????10 MB?
- ???? stdout/stderr???????
- ?????????????????
- ??????????????????????????? stderr
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agenthub.adapters.protocol import (
    AdapterErrorCode,
    AdapterHealth,
    AgentEvent,
    AgentResult,
    AgentTask,
    ArtifactInfo,
    ContentDeltaEvent,
    ExecutionErrorEvent,
    ExecutionStatusEvent,
)

logger = logging.getLogger(__name__)

# ????????stdout/stderr????????????
MAX_LINE_BYTES = 100 * 1024  # 100 KB

# stdout ????????
MAX_STDOUT_BYTES = 10 * 1024 * 1024  # 10 MB

# stderr ??????????????????????
MAX_STDERR_BYTES = 1 * 1024 * 1024  # 1 MB

# ??????????????
GRACEFUL_TERMINATE_TIMEOUT = 5.0


class CodexCLIAdapter:
    """Codex CLI ?????????"""

    def __init__(
        self,
        executable_path: str | None = None,
        default_timeout: int = 300,
    ) -> None:
        self._executable_path: str | None = executable_path
        self._detected_path: str | None = None
        self._version: str | None = None
        self._default_timeout = default_timeout
        self._last_result: AgentResult | None = None
        self._active_process: asyncio.subprocess.Process | None = None

    # ?? ??????? ??????????????????????????????????????????????????

    async def _probe_executable(self) -> tuple[str | None, str | None]:
        if self._detected_path is not None:
            return self._detected_path, self._version

        candidates: list[str] = []
        if self._executable_path:
            candidates.append(self._executable_path)
        for name in ("codex", "codex.exe"):
            found = shutil.which(name)
            if found and found not in candidates:
                candidates.append(found)

        import glob

        known_win = [
            r"C:\Program Files\WindowsApps\OpenAI.Codex_*_x64__*\app\resources\codex.exe",
            r"C:\Program Files\WindowsApps\OpenAI.Codex_*_x64__*\app\resources\codex",
        ]
        for pattern in known_win:
            try:
                for m in glob.glob(pattern):
                    if m not in candidates:
                        candidates.append(m)
            except Exception:
                pass

        for candidate in candidates:
            if not Path(candidate).is_file():  # noqa: ASYNC240
                continue
            try:
                proc = await asyncio.create_subprocess_exec(
                    candidate,
                    "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
                version_line = stdout.decode("utf-8", errors="replace").strip()
                if not version_line and stderr:
                    version_line = stderr.decode("utf-8", errors="replace").strip()
                self._detected_path = candidate
                self._version = version_line if version_line else "unknown"
                return self._detected_path, self._version
            except Exception as exc:
                logger.debug("Codex probe failed for %s: %s", candidate, exc)
                continue

        return None, None

    # ?? ?? ????????????????????????????????????????????????????????????

    @property
    def last_result(self) -> AgentResult | None:
        return self._last_result

    # ?? ???? ????????????????????????????????????????????????????????

    async def healthcheck(self) -> AdapterHealth:
        path, version = await self._probe_executable()
        if path is None:
            return AdapterHealth(
                healthy=False,
                adapter_type="codex_cli",
                version=None,
                message=("Codex CLI ??????????????Windows App ?????? codex.exe ??????????????"),
            )
        return AdapterHealth(
            healthy=True,
            adapter_type="codex_cli",
            version=version,
            message=f"Codex CLI ??: {path}",
        )

    # ?? ?? ????????????????????????????????????????????????????????????

    async def run(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        self._last_result = None

        # 1. ??? working_dir????????
        path_error = self._validate_working_dir(task.working_dir)
        if path_error:
            async for event in self._error_result(task, path_error):
                yield event
            return

        # 2. ???????
        path, _version = await self._probe_executable()
        if path is None:
            async for event in self._unavailable_result(task):
                yield event
            return

        # 3. ???????
        _timeout = task.timeout_seconds or self._default_timeout
        args = self._build_args(task)

        async for event in self._run_subprocess(task, path, args, _timeout):
            yield event

    async def _unavailable_result(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        self._last_result = AgentResult(
            execution_id=task.execution_id,
            status="failed",
            duration_ms=0,
            error_code=AdapterErrorCode.UNAVAILABLE,
            error_message="Codex CLI ????????",
        )
        yield ExecutionStatusEvent(
            execution_id=task.execution_id,
            sequence=0,
            status="failed",
            message="Codex CLI ???",
        )
        yield ExecutionErrorEvent(
            execution_id=task.execution_id,
            sequence=1,
            error_code=AdapterErrorCode.UNAVAILABLE,
            error_message="Codex CLI ????????",
            recoverable=False,
        )

    async def _error_result(
        self, task: AgentTask, error: tuple[AdapterErrorCode, str]
    ) -> AsyncIterator[AgentEvent]:
        error_code, error_message = error
        self._last_result = AgentResult(
            execution_id=task.execution_id,
            status="failed",
            duration_ms=0,
            error_code=error_code,
            error_message=error_message,
        )
        yield ExecutionStatusEvent(
            execution_id=task.execution_id,
            sequence=0,
            status="failed",
            message=error_message,
        )
        yield ExecutionErrorEvent(
            execution_id=task.execution_id,
            sequence=1,
            error_code=error_code,
            error_message=error_message,
            recoverable=False,
        )

    def _validate_working_dir(self, working_dir: Path) -> tuple[AdapterErrorCode, str] | None:
        try:
            resolved = working_dir.resolve(strict=False)
        except Exception:
            return (AdapterErrorCode.PATH_REJECTED, "??????????")
        if not resolved.is_absolute():
            return (AdapterErrorCode.PATH_REJECTED, "???????????")
        if not resolved.exists():
            return (AdapterErrorCode.PATH_REJECTED, "???????")
        if not resolved.is_dir():
            return (AdapterErrorCode.PATH_REJECTED, "????????????")
        return None

    def _build_args(self, task: AgentTask) -> list[str]:
        args: list[str] = []
        if task.context.get("codex_args"):
            extra = task.context["codex_args"]
            if isinstance(extra, list):
                args.extend(extra)
        return args

    # ?? ????? ??????????????????????????????????????????????????????

    async def _run_subprocess(
        self,
        task: AgentTask,
        executable: str,
        args: list[str],
        _timeout_seconds: int,
    ) -> AsyncIterator[AgentEvent]:
        sequence = 0
        start_time = datetime.now(UTC)
        artifacts: list[ArtifactInfo] = []
        final_status: Literal["succeeded", "failed", "cancelled"] = "failed"
        final_error_code: AdapterErrorCode | None = None
        final_error_message: str | None = None

        # ?? running ??
        yield ExecutionStatusEvent(
            execution_id=task.execution_id,
            sequence=0,
            status="running",
        )
        sequence = 1

        try:
            self._active_process = await asyncio.create_subprocess_exec(
                executable,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(task.working_dir),
            )

            if self._active_process.stdin:
                try:
                    self._active_process.stdin.write(task.message_content.encode("utf-8"))
                    await self._active_process.stdin.drain()
                    self._active_process.stdin.close()
                except Exception:
                    pass

            stdout_task = asyncio.create_task(self._read_stdout(task, self._active_process))
            stderr_task = asyncio.create_task(self._read_stderr(self._active_process))

            try:
                async for line_bytes in self._stream_stdout_lines(stdout_task):
                    line = line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")
                    if not line:
                        continue
                    yield ContentDeltaEvent(
                        execution_id=task.execution_id,
                        sequence=sequence,
                        delta=line + "\n",
                        content_type="text",
                    )
                    sequence += 1
            except asyncio.CancelledError:
                final_status = "cancelled"
                final_error_code = AdapterErrorCode.CANCELLED
                final_error_message = "??????"
            except Exception as exc:
                final_status = "failed"
                final_error_code = AdapterErrorCode.INTERNAL_ERROR
                final_error_message = f"??????????: {type(exc).__name__}"
            else:
                try:
                    returncode = await asyncio.wait_for(self._active_process.wait(), timeout=5)
                except TimeoutError:
                    returncode = None

                try:
                    await asyncio.wait_for(stderr_task, timeout=5)
                except TimeoutError:
                    stderr_task.cancel()

                if returncode == 0:
                    final_status = "succeeded"
                elif returncode is None:
                    final_status = "failed"
                    final_error_code = AdapterErrorCode.TIMEOUT
                    final_error_message = "???????"
                else:
                    final_status = "failed"
                    final_error_code = AdapterErrorCode.EXECUTION_FAILED
                    final_error_message = f"Codex CLI ????????: {returncode}"
            finally:
                await self._terminate_process(self._active_process)
                self._active_process = None
                if not stderr_task.done():
                    stderr_task.cancel()

        except FileNotFoundError:
            final_status = "failed"
            final_error_code = AdapterErrorCode.UNAVAILABLE
            final_error_message = "??? Codex CLI ?????"
        except PermissionError:
            final_status = "failed"
            final_error_code = AdapterErrorCode.PERMISSION_DENIED
            final_error_message = "?????? Codex CLI"
        except OSError as exc:
            final_status = "failed"
            final_error_code = AdapterErrorCode.INTERNAL_ERROR
            final_error_message = f"??????: {type(exc).__name__}"
        except Exception as exc:
            final_status = "failed"
            final_error_code = AdapterErrorCode.INTERNAL_ERROR
            final_error_message = f"?????: {type(exc).__name__}"
            logger.exception("Codex CLI ????")

        end_time = datetime.now(UTC)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        yield ExecutionStatusEvent(
            execution_id=task.execution_id,
            sequence=sequence,
            status=final_status,
            message=final_error_message if final_status != "succeeded" else None,
        )
        sequence += 1

        if final_error_code:
            yield ExecutionErrorEvent(
                execution_id=task.execution_id,
                sequence=sequence,
                error_code=final_error_code,
                error_message=final_error_message or "",
                recoverable=False,
            )
            sequence += 1

        self._last_result = AgentResult(
            execution_id=task.execution_id,
            status=final_status,
            artifacts=artifacts,
            total_tokens=None,
            duration_ms=duration_ms,
            error_code=final_error_code,
            error_message=final_error_message,
        )

    # ?? stdout/stderr ???? ???????????????????????????????????????????

    async def _read_stdout(self, task: AgentTask, process: asyncio.subprocess.Process) -> bytes:
        if not process.stdout:
            return b""
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                chunk = await process.stdout.read(8192)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_STDOUT_BYTES:
                    logger.warning(
                        "?? %s stdout ?? %d ????",
                        task.execution_id,
                        MAX_STDOUT_BYTES,
                    )
                    chunks.append(chunk[: MAX_STDOUT_BYTES - (total - len(chunk))])
                    break
                chunks.append(chunk)
        except Exception:
            pass
        return b"".join(chunks)

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        if not process.stderr:
            return
        stderr_lines: list[str] = []
        total = 0
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                total += len(line)
                if total > MAX_STDERR_BYTES:
                    break
                if len(line) > MAX_LINE_BYTES:
                    line = line[:MAX_LINE_BYTES] + b"...[truncated]\n"
                stderr_lines.append(line.decode("utf-8", errors="replace"))
        except Exception:
            pass
        if stderr_lines:
            preview = "".join(stderr_lines)[:200]
            logger.debug("Codex CLI stderr: %s", preview)

    async def _stream_stdout_lines(self, stdout_task: asyncio.Task[bytes]) -> AsyncIterator[bytes]:
        try:
            stdout_data = await stdout_task
        except Exception:
            return
        lines = stdout_data.split(b"\n")
        for line in lines:
            if len(line) > MAX_LINE_BYTES:
                line = line[:MAX_LINE_BYTES] + b"...[truncated]"
            yield line

    # ?? ??????? ??????????????????????????????????????????????????

    async def _terminate_process(self, process: asyncio.subprocess.Process | None) -> None:
        if process is None or process.returncode is not None:
            return
        try:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=GRACEFUL_TERMINATE_TIMEOUT)
            except TimeoutError:
                logger.warning("Codex CLI ???? %.1f ??????? kill", GRACEFUL_TERMINATE_TIMEOUT)
                process.kill()
                await process.wait()
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning("?? Codex CLI ?????: %s", exc)

    # ?? ?? ????????????????????????????????????????????????????????????

    def cancel(self, execution_id: uuid.UUID) -> None:
        if self._active_process and self._active_process.returncode is None:
            logger.info("?? Codex CLI ?? %s (PID %d)", execution_id, self._active_process.pid)
            _cleanup_task = asyncio.create_task(self._terminate_process(self._active_process))  # noqa: RUF006
