"""AgentHub FastAPI 应用入口。"""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agenthub.adapters import (
    MockAdapter,
    MockAdapterScript,
    MockScriptStep,
    OpenAICompatibleAdapter,
)
from agenthub.api.routes.agents import router as agents_router
from agenthub.api.routes.chat import router as chat_router
from agenthub.api.routes.chat import websocket_router
from agenthub.api.routes.health import router as health_router
from agenthub.core.config import Settings, get_settings
from agenthub.models.enums import AgentType
from agenthub.models.orm import Agent
from agenthub.services.agents import AgentService
from agenthub.services.chat import AgentAdapter, ChatService
from agenthub.services.realtime import ConversationEventBroker


def _default_adapter_resolver(settings: Settings) -> Callable[[Agent], AgentAdapter]:
    """按 Agent 类型装配 Adapter；真实凭据只从进程配置注入。"""

    def resolve(agent: Agent) -> AgentAdapter:
        if agent.agent_type == AgentType.MOCK:
            # 应用默认注册的 Mock Agent 必须产生可见内容，便于验证完整聊天链路；
            # 固定回复不回显用户输入，也不会让用户误以为它是真实模型输出。
            return MockAdapter(
                MockAdapterScript(
                    adapter_name=agent.name,
                    script=[
                        MockScriptStep(
                            action="delta",
                            content=(
                                "这是确定性 Mock Agent 回复，用于验证 AgentHub 的对话与并发链路。"
                            ),
                            content_type="markdown",
                        )
                    ],
                )
            )
        if agent.agent_type == AgentType.OPENAI_COMPATIBLE:
            dependencies = settings.runtime_dependencies()
            return OpenAICompatibleAdapter(
                base_url=dependencies.llm_base_url,
                api_key=dependencies.llm_api_key,
                model=dependencies.llm_model,
            )
        raise RuntimeError("Adapter is not configured")

    return resolve


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    adapter_resolver: Callable[[Agent], AgentAdapter] | None = None,
) -> FastAPI:
    """创建可测试、无导入期外部副作用的 FastAPI 应用。"""

    resolved_settings = settings or get_settings()
    owned_engine: AsyncEngine | None = None

    @asynccontextmanager
    async def lifespan(application: FastAPI):  # type: ignore[no-untyped-def]
        """关闭时取消并等待本进程启动的执行，避免遗留后台协程。"""
        yield
        tasks: set[asyncio.Task[None]] = application.state.execution_tasks
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if owned_engine is not None:
            await owned_engine.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        docs_url="/docs" if resolved_settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    broker = ConversationEventBroker()
    application.state.event_broker = broker
    application.state.execution_tasks = set()
    factory = session_factory
    if factory is None and resolved_settings.database_url is not None:
        # Phase 4 只依赖数据库，不应通过 runtime_dependencies 提前要求 LLM 配置。
        owned_engine = create_async_engine(resolved_settings.database_url.get_secret_value())
        factory = async_sessionmaker(owned_engine, class_=AsyncSession, expire_on_commit=False)
    if factory is not None:
        application.state.agent_service = AgentService(factory)
        application.state.chat_service = ChatService(
            factory, broker, adapter_resolver or _default_adapter_resolver(resolved_settings)
        )
    application.include_router(health_router)
    application.include_router(agents_router)
    application.include_router(chat_router)
    application.include_router(websocket_router)

    return application


app = create_app()
