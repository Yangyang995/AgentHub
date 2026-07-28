"""配置默认值与敏感信息保护测试。"""

import pytest
from pydantic import SecretStr

from agenthub.core.config import RuntimeConfigurationError, Settings


def test_settings_defaults_do_not_require_external_services() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.app_name == "AgentHub API"
    assert settings.environment == "development"
    assert settings.api_prefix == "/api/v1"
    assert settings.database_url is None
    assert settings.llm_api_key is None


def test_runtime_configuration_error_never_contains_provided_secrets() -> None:
    secret_database_url = "postgresql+asyncpg://user:private-password@localhost/agenthub"
    settings = Settings(
        database_url=SecretStr(secret_database_url),
        llm_api_key=SecretStr("private-api-key"),
        _env_file=None,  # type: ignore[call-arg]
    )

    with pytest.raises(RuntimeConfigurationError) as captured:
        settings.runtime_dependencies()

    message = str(captured.value)
    assert "AGENTHUB_LLM_BASE_URL" in message
    assert "AGENTHUB_LLM_MODEL" in message
    assert secret_database_url not in message
    assert "private-api-key" not in message


def test_secret_values_are_masked_in_model_representation() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://user:secret@localhost/agenthub"),
        llm_api_key=SecretStr("private-api-key"),
        _env_file=None,  # type: ignore[call-arg]
    )

    representation = repr(settings)
    assert "private-api-key" not in representation
    assert "user:secret" not in representation
    assert "**********" in representation
