"""应用默认 Adapter 装配的回归测试。"""

import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from agenthub.adapters import (
    AgentTask,
    ContentDeltaEvent,
    OpenAICompatibleAdapter,
)
from agenthub.core.config import Settings
from agenthub.main import _default_adapter_resolver
from agenthub.models.enums import AgentStatus, AgentType
from agenthub.models.orm import Agent


@pytest.mark.asyncio
async def test_default_mock_agent_returns_visible_deterministic_content() -> None:
    """新注册 Mock Agent 应产生非空、安全且明确标识为 Mock 的回复。"""

    project_id = uuid.uuid4()
    agent = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name="Reviewer",
        agent_type=AgentType.MOCK,
        capabilities=["code_review"],
        status=AgentStatus.ENABLED,
    )
    adapter = _default_adapter_resolver(
        Settings(  # type: ignore[call-arg]
            environment="test",
            database_url=SecretStr("postgresql+asyncpg://user:password@localhost/test"),
            llm_base_url="https://example.invalid/v1",
            llm_api_key=SecretStr("test-key"),
            llm_model="test-model",
            _env_file=None,
        )
    )(agent)
    sensitive_input = "不要在回复中回显这段用户输入"
    task = AgentTask(
        execution_id=uuid.uuid4(),
        project_id=project_id,
        agent_id=agent.id,
        conversation_id=uuid.uuid4(),
        message_content=sensitive_input,
        working_dir=Path.cwd(),
    )

    events = [event async for event in adapter.run(task)]
    deltas = [event.delta for event in events if isinstance(event, ContentDeltaEvent)]

    assert deltas
    assert "Mock Agent" in "".join(deltas)
    assert sensitive_input not in "".join(deltas)


@pytest.mark.parametrize(
    ("agent_type", "adapter_type"),
    [
        (AgentType.OPENAI_COMPATIBLE, OpenAICompatibleAdapter),
    ],
)
def test_default_resolver_maps_real_provider_types(
    agent_type: AgentType, adapter_type: type[object]
) -> None:
    """固定提供方的内部 Agent 类型必须解析到对应真实 Adapter。"""
    agent = Agent(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name=agent_type.value,
        agent_type=agent_type,
        capabilities=[],
        status=AgentStatus.ENABLED,
    )

    adapter = _default_adapter_resolver(
        Settings(  # type: ignore[call-arg]
            environment="test",
            database_url=SecretStr("postgresql+asyncpg://user:password@localhost/test"),
            llm_base_url="https://example.invalid/v1",
            llm_api_key=SecretStr("test-key"),
            llm_model="test-model",
            _env_file=None,
        )
    )(agent)

    assert isinstance(adapter, adapter_type)
