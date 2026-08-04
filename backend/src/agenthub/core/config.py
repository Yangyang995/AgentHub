"""应用配置模型。
本模块只解析配置，不在导入时连接数据库、模型服务或其他外部资源。"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeConfigurationError(RuntimeError):
    """表示启动某项外部能力所需的配置不完整。
    异常只暴露缺失配置项的名称，不包含已经提供的值，避免密钥进入日志或响应。"""


class RuntimeDependencies(BaseModel):
    """通过显式校验后可交给后续基础设施层的外部服务配置。"""

    database_url: SecretStr
    llm_base_url: str
    llm_api_key: SecretStr
    llm_model: str


class EmbeddingDependencies(BaseModel):
    """Embedding 服务配置——独立校验，缺失时 RAG 功能降级但不阻塞应用启动。"""

    embedding_base_url: str
    embedding_api_key: SecretStr | None
    embedding_model: str


class Settings(BaseSettings):
    """AgentHub 的类型化环境配置。
    Phase 1 允许应用在未配置数据库和 LLM 时启动，以便健康检查与前端开发。
    真正使用这些外部服务前必须调用 ``runtime_dependencies``，集中执行必填校验。
    """

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        env_prefix="AGENTHUB_",
        extra="ignore",
    )

    app_name: str = "AgentHub API"
    environment: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api/v1"
    database_url: SecretStr | None = None
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    # Phase 8: Embedding 配置（BGE-M3 独立服务）
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "BAAI/bge-m3"

    # Phase 10: Vercel 部署配置——缺失时部署功能降级但不阻塞应用启动
    vercel_token: SecretStr | None = None
    vercel_team_id: str | None = None

    # Phase 10: 本地预览端口范围——避免与系统服务冲突
    preview_port_range_start: int = 18000
    preview_port_range_end: int = 18999

    def runtime_dependencies(self) -> RuntimeDependencies:
        """校验后续阶段连接数据库和 Orchestrator 所需的配置。
        错误消息只列出环境变量名称，调用方可以安全记录该消息；任何已提供的密钥值
        都不会被拼接到异常文本中。"""

        values = {
            "AGENTHUB_DATABASE_URL": self.database_url,
            "AGENTHUB_LLM_BASE_URL": self.llm_base_url,
            "AGENTHUB_LLM_API_KEY": self.llm_api_key,
            "AGENTHUB_LLM_MODEL": self.llm_model,
        }
        missing = [name for name, value in values.items() if value is None or value == ""]
        if missing:
            names = ", ".join(sorted(missing))
            raise RuntimeConfigurationError(f"缺少运行时配置项: {names}")

        # 上方检查已经排除 None；逐项断言让静态类型检查器也能确认构造参数完整。
        assert self.database_url is not None
        assert self.llm_base_url is not None
        assert self.llm_api_key is not None
        assert self.llm_model is not None
        return RuntimeDependencies(
            database_url=self.database_url,
            llm_base_url=self.llm_base_url,
            llm_api_key=self.llm_api_key,
            llm_model=self.llm_model,
        )

    def embedding_dependencies(self) -> EmbeddingDependencies | None:
        """校验 Embedding 服务配置——缺失时返回 None，不阻塞启动。
        调用方根据返回值决定 RAG 功能是否可用。"""

        if self.embedding_base_url is None or self.embedding_base_url == "":
            return None
        assert self.embedding_base_url is not None
        return EmbeddingDependencies(
            embedding_base_url=self.embedding_base_url,
            embedding_api_key=self.embedding_api_key,
            embedding_model=self.embedding_model,
        )


@lru_cache
def get_settings() -> Settings:
    """在进程内复用已解析配置，不触发任何外部连接。"""

    return Settings()
