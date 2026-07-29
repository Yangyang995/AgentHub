"""Agent Adapter ????????

???
- AgentEvent ???????????/????
- Mock Adapter ???????????
- Codex CLI Adapter ???????????????
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from agenthub.adapters.codex_cli import CodexCLIAdapter
from agenthub.adapters.mock_adapter import (
    MockAdapter,
    MockAdapterScript,
    MockScriptStep,
)
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
# ????
# ???????????????????????????????????????????????????????????????????????????


@pytest.fixture
def sample_task() -> AgentTask:
    """???? AgentTask??????????????"""
    return AgentTask(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        message_content="???????????",
        working_dir=Path.cwd(),
    )


@pytest.fixture
def sample_task_dict(sample_task: AgentTask) -> dict[str, Any]:
    """AgentTask ???????? JSON ?????"""
    return sample_task.model_dump(mode="json")


# ???????????????????????????????????????????????????????????????????????????
# AgentEvent ?????????
# ???????????????????????????????????????????????????????????????????????????


class TestAgentEventDiscriminatedUnion:
    """?? AgentEvent ???????????/?????"""

    def test_content_delta_serialization(self) -> None:
        """content.delta ????????? event_type ?????"""
        event = ContentDeltaEvent(
            execution_id=uuid.uuid4(),
            sequence=5,
            delta="Hello World",
            content_type="text",
        )
        data = event.model_dump(mode="json")
        parsed: AgentEvent = TypeAdapter(AgentEvent).validate_python(data)
        assert isinstance(parsed, ContentDeltaEvent)
        assert parsed.event_type == "content.delta"
        assert parsed.delta == "Hello World"

    def test_execution_status_serialization(self) -> None:
        """execution.status ????????"""
        event = ExecutionStatusEvent(
            execution_id=uuid.uuid4(),
            sequence=0,
            status="running",
        )
        data = event.model_dump(mode="json")
        parsed: AgentEvent = TypeAdapter(AgentEvent).validate_python(data)
        assert isinstance(parsed, ExecutionStatusEvent)
        assert parsed.status == "running"

    def test_execution_error_serialization(self) -> None:
        """execution.error ????????"""
        event = ExecutionErrorEvent(
            execution_id=uuid.uuid4(),
            sequence=10,
            error_code=AdapterErrorCode.TIMEOUT,
            error_message="????",
            recoverable=False,
        )
        data = event.model_dump(mode="json")
        parsed: AgentEvent = TypeAdapter(AgentEvent).validate_python(data)
        assert isinstance(parsed, ExecutionErrorEvent)
        assert parsed.error_code == AdapterErrorCode.TIMEOUT

    def test_execution_usage_serialization(self) -> None:
        """execution.usage ????????"""
        event = ExecutionUsageEvent(
            execution_id=uuid.uuid4(),
            sequence=3,
            token_count=150,
            call_count=1,
            duration_ms=1200,
        )
        data = event.model_dump(mode="json")
        parsed: AgentEvent = TypeAdapter(AgentEvent).validate_python(data)
        assert isinstance(parsed, ExecutionUsageEvent)
        assert parsed.token_count == 150

    def test_artifact_created_serialization(self) -> None:
        """artifact.created ????????"""
        event = ArtifactCreatedEvent(
            execution_id=uuid.uuid4(),
            sequence=7,
            artifact_id=uuid.uuid4(),
            artifact_type="file",
            relative_path="src/main.py",
            content_hash="abc123",
            size=1024,
        )
        data = event.model_dump(mode="json")
        parsed: AgentEvent = TypeAdapter(AgentEvent).validate_python(data)
        assert isinstance(parsed, ArtifactCreatedEvent)
        assert parsed.artifact_type == "file"
        assert parsed.relative_path == "src/main.py"

    def test_agent_result_serialization(self) -> None:
        """AgentResult ??????"""
        result = AgentResult(
            execution_id=uuid.uuid4(),
            status="succeeded",
            artifacts=[
                ArtifactInfo(
                    artifact_id=uuid.uuid4(),
                    artifact_type="file",
                    relative_path="out.txt",
                    content_hash="def456",
                    size=256,
                )
            ],
            total_tokens=300,
            duration_ms=5000,
        )
        data = result.model_dump(mode="json")
        parsed = AgentResult(**data)
        assert parsed.status == "succeeded"
        assert parsed.total_tokens == 300
        assert len(parsed.artifacts) == 1

    def test_sequence_monotonic_increasing(self) -> None:
        """?? execution_id ??? sequence ???????"""
        exec_id = uuid.uuid4()
        events: list[AgentEvent] = [
            ContentDeltaEvent(execution_id=exec_id, sequence=0, delta="a"),
            ContentDeltaEvent(execution_id=exec_id, sequence=1, delta="b"),
            ContentDeltaEvent(execution_id=exec_id, sequence=2, delta="c"),
        ]
        sequences = [e.sequence for e in events]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)  # ???

    def test_event_id_unique_per_event(self) -> None:
        """????? event_id ????"""
        events: list[AgentEvent] = [
            ContentDeltaEvent(execution_id=uuid.uuid4(), sequence=0, delta="a"),
            ContentDeltaEvent(execution_id=uuid.uuid4(), sequence=1, delta="b"),
        ]
        # ????? event_id ????
        assert events[0].event_id != events[1].event_id

    def test_agent_health_serialization(self) -> None:
        """AdapterHealth ????"""
        health = AdapterHealth(
            healthy=False,
            adapter_type="codex_cli",
            version=None,
            message="Codex CLI ???",
        )
        data = health.model_dump(mode="json")
        assert data["healthy"] is False
        assert data["adapter_type"] == "codex_cli"


# ???????????????????????????????????????????????????????????????????????????
# AgentTask ??
# ???????????????????????????????????????????????????????????????????????????


class TestAgentTask:
    """AgentTask ??????"""

    def test_minimal_task(self) -> None:
        """???????"""
        task = AgentTask(
            execution_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            message_content="hello",
            working_dir=Path("/tmp/test"),
        )
        assert task.timeout_seconds is None
        assert task.context == {}

    def test_task_with_timeout(self) -> None:
        """???????"""
        task = AgentTask(
            execution_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            message_content="hello",
            working_dir=Path("/tmp/test"),
            timeout_seconds=30,
        )
        assert task.timeout_seconds == 30

    def test_task_timeout_minimum(self) -> None:
        """?????? 1 ??"""
        with pytest.raises(Exception):  # noqa: B017
            AgentTask(
                execution_id=uuid.uuid4(),
                project_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                conversation_id=uuid.uuid4(),
                message_content="hello",
                working_dir=Path("/tmp/test"),
                timeout_seconds=0,
            )


# ???????????????????????????????????????????????????????????????????????????
# Mock Adapter ??
# ???????????????????????????????????????????????????????????????????????????


class TestMockAdapterSuccess:
    """Mock Adapter ???????"""

    async def _collect_events(
        self, adapter: MockAdapter, task: AgentTask
    ) -> tuple[list[AgentEvent], AgentResult]:
        """?????????????????????"""
        events: list[AgentEvent] = []
        async for event in adapter.run(task):
            events.append(event)
        result = adapter.last_result or AgentResult(
            execution_id=task.execution_id,
            status="failed",
            duration_ms=0,
        )
        return events, result

    @pytest.mark.asyncio
    async def test_healthcheck(self) -> None:
        """Mock ??????????? healthy?"""
        script = MockAdapterScript(adapter_name="test-mock")
        adapter = MockAdapter(script)
        health = await adapter.healthcheck()
        assert health.healthy is True
        assert health.adapter_type == "mock"
        assert "test-mock" in (health.message or "")

    @pytest.mark.asyncio
    async def test_empty_script_succeeds(self, sample_task: AgentTask) -> None:
        """?????? running -> succeeded ??????????"""
        script = MockAdapterScript()
        adapter = MockAdapter(script)
        events, result = await self._collect_events(adapter, sample_task)

        assert result.status == "succeeded"
        assert result.error_code is None
        assert len(result.artifacts) == 0
        # ???? running ? succeeded ????
        status_events = [e for e in events if isinstance(e, ExecutionStatusEvent)]
        statuses = [e.status for e in status_events]
        assert "running" in statuses
        assert "succeeded" in statuses

    @pytest.mark.asyncio
    async def test_delta_events(self, sample_task: AgentTask) -> None:
        """delta ???? content.delta ???"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(action="delta", content="???"),
                MockScriptStep(action="delta", content="???"),
            ]
        )
        adapter = MockAdapter(script)
        events, result = await self._collect_events(adapter, sample_task)

        deltas = [e for e in events if isinstance(e, ContentDeltaEvent)]
        assert len(deltas) == 2
        assert deltas[0].delta == "???"
        assert deltas[1].delta == "???"
        assert result.status == "succeeded"

    @pytest.mark.asyncio
    async def test_artifact_events(self, sample_task: AgentTask) -> None:
        """artifact ???? artifact.created ???"""
        test_content = "print('hello')"
        expected_hash = hashlib.sha256(test_content.encode()).hexdigest()

        script = MockAdapterScript(
            script=[
                MockScriptStep(
                    action="artifact",
                    artifact_type="file",
                    relative_path="hello.py",
                    artifact_content=test_content,
                ),
            ]
        )
        adapter = MockAdapter(script)
        events, _result = await self._collect_events(adapter, sample_task)

        artifacts = [e for e in events if isinstance(e, ArtifactCreatedEvent)]
        assert len(artifacts) == 1
        assert artifacts[0].artifact_type == "file"
        assert artifacts[0].relative_path == "hello.py"
        assert artifacts[0].content_hash == expected_hash
        assert artifacts[0].size == len(test_content.encode())
        assert _result is not None
        assert len(_result.artifacts) == 1

    @pytest.mark.asyncio
    async def test_usage_events(self, sample_task: AgentTask) -> None:
        """usage ???? execution.usage ???"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(action="usage", token_count=100, call_count=1),
                MockScriptStep(action="usage", token_count=50, call_count=1),
            ]
        )
        adapter = MockAdapter(script)
        events, result = await self._collect_events(adapter, sample_task)

        usage_events = [e for e in events if isinstance(e, ExecutionUsageEvent)]
        assert len(usage_events) == 2
        assert usage_events[0].token_count == 100
        assert usage_events[1].token_count == 50
        # ???? total_tokens ?????
        assert result.total_tokens == 150

    @pytest.mark.asyncio
    async def test_sequence_monotonic(self, sample_task: AgentTask) -> None:
        """Mock Adapter ????? sequence ???????????"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(action="delta", content="a"),
                MockScriptStep(action="delta", content="b"),
                MockScriptStep(action="usage", token_count=10),
                MockScriptStep(action="artifact", artifact_content="x"),
            ]
        )
        adapter = MockAdapter(script)
        events, _result = await self._collect_events(adapter, sample_task)

        sequences = [e.sequence for e in events]
        assert sequences == list(range(len(sequences))), f"sequence ???: {sequences}"


class TestMockAdapterErrorAndCancel:
    """Mock Adapter ??????????"""

    async def _collect_events(
        self, adapter: MockAdapter, task: AgentTask
    ) -> tuple[list[AgentEvent], AgentResult]:
        # iterator consumed below
        events: list[AgentEvent] = []
        async for event in adapter.run(task):
            events.append(event)
        result = adapter.last_result or AgentResult(
            execution_id=task.execution_id,
            status="failed",
            duration_ms=0,
        )
        return events, result

    @pytest.mark.asyncio
    async def test_error_step_causes_failure(self, sample_task: AgentTask) -> None:
        """error ???????????? failed?"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(action="delta", content="before error"),
                MockScriptStep(
                    action="error",
                    error_code=AdapterErrorCode.INTERNAL_ERROR,
                    error_message="??????",
                ),
                MockScriptStep(action="delta", content="after error"),  # ????
            ]
        )
        adapter = MockAdapter(script)
        events, result = await self._collect_events(adapter, sample_task)

        assert result.status == "failed"
        assert result.error_code == AdapterErrorCode.INTERNAL_ERROR
        # "after error" ????
        deltas = [e for e in events if isinstance(e, ContentDeltaEvent)]
        delta_texts = [d.delta for d in deltas]
        assert "after error" not in delta_texts
        assert "before error" in delta_texts

    @pytest.mark.asyncio
    async def test_error_event_included(self, sample_task: AgentTask) -> None:
        """error ????? execution.error ???"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(
                    action="error",
                    error_code=AdapterErrorCode.CONFIG_ERROR,
                    error_message="????",
                ),
            ]
        )
        adapter = MockAdapter(script)
        events, _result = await self._collect_events(adapter, sample_task)

        error_events = [e for e in events if isinstance(e, ExecutionErrorEvent)]
        assert len(error_events) == 1
        assert error_events[0].error_code == AdapterErrorCode.CONFIG_ERROR

    @pytest.mark.asyncio
    async def test_cancel_before_run(self, sample_task: AgentTask) -> None:
        """? run ?? cancel?????????"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(action="delta", content="step1"),
                MockScriptStep(action="delta", content="step2"),
            ]
        )
        adapter = MockAdapter(script)
        adapter.cancel(uuid.uuid4())  # ? run ????

        events, result = await self._collect_events(adapter, sample_task)

        assert result.status == "cancelled"
        assert result.error_code == AdapterErrorCode.CANCELLED
        # ????? delta ???? running ????????
        deltas = [e for e in events if isinstance(e, ContentDeltaEvent)]
        assert len(deltas) == 0

    @pytest.mark.asyncio
    async def test_cancel_at_checkpoint(self, sample_task: AgentTask) -> None:
        """? cancel_check ??????"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(action="delta", content="step1"),
                MockScriptStep(action="cancel_check"),
                MockScriptStep(action="delta", content="step2"),
            ]
        )
        adapter = MockAdapter(script)

        # ?????? run??? cancel_check ???
        async def run_and_cancel() -> tuple[list[AgentEvent], AgentResult]:
            events: list[AgentEvent] = []
            async for event in adapter.run(sample_task):
                events.append(event)
                if isinstance(event, ContentDeltaEvent) and event.delta == "step1":
                    adapter.cancel(sample_task.execution_id)
            result = adapter.last_result or AgentResult(
                execution_id=sample_task.execution_id,
                status="failed",
                duration_ms=0,
            )
            return events, result

        events, result = await run_and_cancel()

        assert result.status == "cancelled"
        # step2 ????
        deltas = [e for e in events if isinstance(e, ContentDeltaEvent)]
        delta_texts = [d.delta for d in deltas]
        assert "step2" not in delta_texts

    @pytest.mark.asyncio
    async def test_delay_with_cancel(self, sample_task: AgentTask) -> None:
        """? delay ????????????"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(action="delta", content="before-delay"),
                MockScriptStep(action="delay", milliseconds=5000),
                MockScriptStep(action="delta", content="after-delay"),
            ]
        )
        adapter = MockAdapter(script)

        start = asyncio.get_event_loop().time()

        events: list[AgentEvent] = []
        async for event in adapter.run(sample_task):
            events.append(event)
            if isinstance(event, ContentDeltaEvent) and event.delta == "before-delay":
                adapter.cancel(sample_task.execution_id)

        elapsed_ms = (asyncio.get_event_loop().time() - start) * 1000
        result = adapter.last_result or AgentResult(
            execution_id=sample_task.execution_id,
            status="failed",
            duration_ms=0,
        )
        assert result.status == "cancelled"
        # delay ???????? 5 ?
        assert elapsed_ms < 3000, f"?????? 3 ????????? {elapsed_ms:.0f}ms"
        deltas = [e for e in events if isinstance(e, ContentDeltaEvent)]
        assert "after-delay" not in [d.delta for d in deltas]

    @pytest.mark.asyncio
    async def test_deterministic_delay(self, sample_task: AgentTask) -> None:
        """????????????????????"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(action="delay", milliseconds=50),
            ]
        )
        adapter = MockAdapter(script)

        start = asyncio.get_event_loop().time()
        _events, result = await self._collect_events(adapter, sample_task)
        elapsed_ms = (asyncio.get_event_loop().time() - start) * 1000

        # 50ms ???? 20-200ms ???????????
        assert 10 < elapsed_ms < 500, f"???? 50ms??? {elapsed_ms:.0f}ms??????"
        assert result.status == "succeeded"


class TestMockAdapterEdgeCases:
    """Mock Adapter ?????"""

    async def _collect_events(
        self, adapter: MockAdapter, task: AgentTask
    ) -> tuple[list[AgentEvent], AgentResult]:
        # iterator consumed below
        events: list[AgentEvent] = []
        async for event in adapter.run(task):
            events.append(event)
        result = adapter.last_result or AgentResult(
            execution_id=task.execution_id,
            status="failed",
            duration_ms=0,
        )
        return events, result

    @pytest.mark.asyncio
    async def test_reset_cancel(self, sample_task: AgentTask) -> None:
        """reset_cancel ????????"""
        script = MockAdapterScript(script=[MockScriptStep(action="delta", content="ok")])
        adapter = MockAdapter(script)
        adapter.cancel(uuid.uuid4())
        # ??????
        adapter.reset_cancel()

        _events, result = await self._collect_events(adapter, sample_task)
        assert result.status == "succeeded"

    @pytest.mark.asyncio
    async def test_default_delay_between_steps(self, sample_task: AgentTask) -> None:
        """default_delay_ms ?????????"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(action="delta", content="a"),
                MockScriptStep(action="delta", content="b"),
            ],
            default_delay_ms=50,
        )
        adapter = MockAdapter(script)

        start = asyncio.get_event_loop().time()
        _events, _result = await self._collect_events(adapter, sample_task)
        elapsed_ms = (asyncio.get_event_loop().time() - start) * 1000

        # ?????? 2 ?????????????? 100ms
        assert elapsed_ms > 50, f"default_delay ???????? {elapsed_ms:.0f}ms"

    @pytest.mark.asyncio
    async def test_artifact_without_explicit_path(self, sample_task: AgentTask) -> None:
        """artifact ??????????????"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(action="artifact", artifact_content="data"),
            ]
        )
        adapter = MockAdapter(script)
        events, _result = await self._collect_events(adapter, sample_task)

        artifacts = [e for e in events if isinstance(e, ArtifactCreatedEvent)]
        assert len(artifacts) == 1
        assert artifacts[0].relative_path == "output.txt"  # ???
        assert artifacts[0].artifact_type == "file"  # ???

    @pytest.mark.asyncio
    async def test_token_zero_not_reported(self, sample_task: AgentTask) -> None:
        """token_count ? 0 ????????? total_tokens?"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(action="usage", token_count=0),
                MockScriptStep(action="usage", token_count=None),
            ]
        )
        adapter = MockAdapter(script)
        _events, result = await self._collect_events(adapter, sample_task)

        # total_tokens ? None ??????0 ?????
        assert result.total_tokens is None or result.total_tokens == 0

    @pytest.mark.asyncio
    async def test_recoverable_error(self, sample_task: AgentTask) -> None:
        """???????? recoverable=True?"""
        script = MockAdapterScript(
            script=[
                MockScriptStep(
                    action="error",
                    error_code=AdapterErrorCode.TIMEOUT,
                    error_message="?????",
                    recoverable=True,
                ),
            ]
        )
        adapter = MockAdapter(script)
        events, _result = await self._collect_events(adapter, sample_task)

        error_events = [e for e in events if isinstance(e, ExecutionErrorEvent)]
        assert len(error_events) == 1
        assert error_events[0].recoverable is True

    @pytest.mark.asyncio
    async def test_error_code_values_not_expose_secrets(self) -> None:
        """AdpaterErrorCode ???????????"""
        for code in AdapterErrorCode:
            assert "password" not in code.value.lower()
            assert "token" not in code.value.lower()
            assert "secret" not in code.value.lower()
            assert "key" not in code.value.lower()


# ???????????????????????????????????????????????????????????????????????????
# Codex CLI Adapter ??
# ???????????????????????????????????????????????????????????????????????????


class TestCodexCLIHealthCheck:
    """Codex CLI ????????"""

    @pytest.mark.asyncio
    async def test_healthcheck_reports_status(self) -> None:
        """????????????????????????"""
        adapter = CodexCLIAdapter()
        health = await adapter.healthcheck()
        assert isinstance(health, AdapterHealth)
        assert health.adapter_type == "codex_cli"
        assert health.message is not None
        # ??? Windows ???????? unhealthy
        # ???????? healthy/unhealthy????????

    @pytest.mark.asyncio
    async def test_healthcheck_caches_result(self) -> None:
        """???? healthcheck ????????"""
        adapter = CodexCLIAdapter()
        health1 = await adapter.healthcheck()
        health2 = await adapter.healthcheck()
        # ??????????
        assert health1.healthy == health2.healthy
        assert health1.version == health2.version


class TestCodexCLIPathValidation:
    """Codex CLI ???????"""

    def test_valid_working_dir(self) -> None:
        """???????????"""
        adapter = CodexCLIAdapter()
        result = adapter._validate_working_dir(Path.cwd())
        assert result is None

    def test_nonexistent_dir(self) -> None:
        """??????????"""
        adapter = CodexCLIAdapter()
        result = adapter._validate_working_dir(Path("/nonexistent/path/12345"))
        assert result is not None
        assert result[0] == AdapterErrorCode.PATH_REJECTED

    def test_relative_path_rejected(self) -> None:
        """????????"""
        adapter = CodexCLIAdapter()
        result = adapter._validate_working_dir(Path("relative/path"))
        assert result is not None
        assert result[0] == AdapterErrorCode.PATH_REJECTED

    def test_file_not_directory_rejected(self, tmp_path: Path) -> None:
        """??????????"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        adapter = CodexCLIAdapter()
        result = adapter._validate_working_dir(test_file)
        assert result is not None
        assert result[0] == AdapterErrorCode.PATH_REJECTED


class TestCodexCLIRunUnavailable:
    """Codex CLI ??????????"""

    @pytest.mark.asyncio
    async def test_run_when_unavailable_returns_error(self) -> None:
        """? Codex CLI ?????run ??? UNAVAILABLE ???"""
        adapter = CodexCLIAdapter(executable_path="/nonexistent/codex")
        task = AgentTask(
            execution_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            message_content="test",
            working_dir=Path.cwd(),
        )

        events: list[AgentEvent] = []
        async for event in adapter.run(task):
            events.append(event)

        result = adapter.last_result
        assert result is not None
        assert result.status == "failed"
        assert result.error_code == AdapterErrorCode.UNAVAILABLE
        # ???????
        error_events = [e for e in events if isinstance(e, ExecutionErrorEvent)]
        assert len(error_events) >= 1

    @pytest.mark.asyncio
    async def test_run_with_invalid_working_dir(self) -> None:
        """???????? run ??????"""
        adapter = CodexCLIAdapter(executable_path="/nonexistent/codex")
        task = AgentTask(
            execution_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            message_content="test",
            working_dir=Path("/nonexistent/dir"),
        )

        events: list[AgentEvent] = []
        async for event in adapter.run(task):
            events.append(event)

        result = adapter.last_result
        assert result is not None
        assert result.status == "failed"
        # ????????????
        assert result.error_code == AdapterErrorCode.PATH_REJECTED


class TestCodexCLIBuildArgs:
    """Codex CLI ?????"""

    def test_build_args_no_context(self) -> None:
        """?????????????"""
        adapter = CodexCLIAdapter()
        task = AgentTask(
            execution_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            message_content="test",
            working_dir=Path("/tmp"),
        )
        args = adapter._build_args(task)
        assert isinstance(args, list)
        # ???????

    def test_build_args_with_codex_args(self) -> None:
        """?? context ?? codex_args?"""
        adapter = CodexCLIAdapter()
        task = AgentTask(
            execution_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            message_content="test",
            working_dir=Path("/tmp"),
            context={"codex_args": ["--model", "gpt-5", "--verbose"]},
        )
        args = adapter._build_args(task)
        assert "--model" in args
        assert "gpt-5" in args


# ???????????????????????????????????????????????????????????????????????????
# ??????? Codex CLI ????
# ???????????????????????????????????????????????????????????????????????????


@pytest.mark.asyncio
async def test_codex_cli_real_skip_if_unavailable() -> None:
    """? Codex CLI ?????????????

    ?????Windows App ??? codex.exe ?????????Access is denied??
    """
    adapter = CodexCLIAdapter()
    health = await adapter.healthcheck()

    if not health.healthy:
        pytest.skip(f"Codex CLI ???????????????: {health.message}")

    # ?????????????
    task = AgentTask(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        message_content="echo hello",
        working_dir=Path.cwd(),
        timeout_seconds=30,
    )
    events: list[AgentEvent] = []
    async for event in adapter.run(task):
        events.append(event)

    # ????
    assert len(events) > 0
    result = adapter.last_result
    assert result is not None
    assert result.execution_id == task.execution_id
