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
from agenthub.api.routes.approvals import router as approvals_router
from agenthub.api.routes.artifacts import router as artifacts_router
from agenthub.api.routes.chat import router as chat_router
from agenthub.api.routes.chat import websocket_router
from agenthub.api.routes.deployments import router as deployments_router
from agenthub.api.routes.health import router as health_router
from agenthub.api.routes.knowledge import router as knowledge_router
from agenthub.api.routes.memories import router as memories_router
from agenthub.api.routes.previews import router as previews_router
from agenthub.api.routes.projects import router as projects_router
from agenthub.core.config import Settings, get_settings
from agenthub.models.enums import AgentType
from agenthub.models.orm import Agent
from agenthub.services.agents import AgentService
from agenthub.services.chat import AgentAdapter, ChatService
from agenthub.services.deployment import DeploymentService
from agenthub.services.preview import PreviewService
from agenthub.services.project import ProjectService
from agenthub.services.prompt_loader import load_system_prompt
from agenthub.services.realtime import ConversationEventBroker


async def _run_forgetting_periodic(application) -> None:
    """遗忘策略后台任务——每 24 小时扫描并归档过期偏好。
    
    首次启动 5 分钟后执行，之后每 24 小时一次。
    失败不传播异常，仅记录日志。
    """
    import logging

    from sqlalchemy import select

    from agenthub.models.orm import Project
    from agenthub.rag.memory.forgetting import ForgettingManager

    logger = logging.getLogger(__name__)
    await asyncio.sleep(300)

    while True:
        try:
            settings = application.state.settings
            engine: AsyncEngine = create_async_engine(
                settings.database_url, echo=False
            )
            async_session = async_sessionmaker(engine, expire_on_commit=False)
            async with async_session() as session:
                result = await session.execute(select(Project))
                projects = result.scalars().all()
                total = 0
                for project in projects:
                    mgr = ForgettingManager(session)
                    count = await mgr.archive_stale(project.id)
                    total += count
                if total > 0:
                    logger.info("遗忘策略归档完成，共归档 %d 条过期偏好", total)
            await engine.dispose()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("遗忘策略执行失败")
        await asyncio.sleep(86400)

def _default_adapter_resolver(settings: Settings) -> Callable[[Agent], AgentAdapter]:
    """按 Agent 类型装配 Adapter，并根据 Agent 能力注入对应的 System Prompt。

    对于 OPENAI_COMPATIBLE 类型的 Agent，若其 adapter_config_ref 指向已知能力，
    则从 prompts/agents/ 加载对应 System Prompt 并注入 Adapter。
    """

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
            # 根据 Agent 的能力配置加载对应 System Prompt
            system_prompt: str | None = None
            if agent.adapter_config_ref is not None:
                system_prompt = load_system_prompt(agent.adapter_config_ref)
            return OpenAICompatibleAdapter(
                base_url=dependencies.llm_base_url,
                api_key=dependencies.llm_api_key,
                model=dependencies.llm_model,
                system_prompt=system_prompt,
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
        """应用生命周期管理——启动时初始化服务，关闭时清理资源。"""
        # 启动遗忘策略后台任务——每 24 小时扫描过期偏好
        forgetting_task = asyncio.create_task(_run_forgetting_periodic(application))
        application.state.execution_tasks.add(forgetting_task)
        try:
            yield
        finally:
            # Phase 10: 关闭所有活跃预览子进程并清理临时目录
            preview_svc = getattr(application.state, "preview_service", None)
            if preview_svc is not None:
                await preview_svc.cleanup_all()
            forgetting_task.cancel()
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
        application.state.project_service = ProjectService(factory)
        application.state.chat_service = ChatService(
            factory,
            broker,
            adapter_resolver or _default_adapter_resolver(resolved_settings),
        )
        # Phase 10: 关闭所有活跃预览子进程并清理临时目录
        application.state.preview_service = PreviewService(factory, broker)
        application.state.deployment_service = DeploymentService(factory, broker)
    application.include_router(health_router)
    application.include_router(projects_router)
    application.include_router(agents_router)
    application.include_router(approvals_router)
    application.include_router(artifacts_router)
    application.include_router(deployments_router)
    application.include_router(knowledge_router)
    application.include_router(memories_router)
    application.include_router(previews_router)
    application.include_router(chat_router)
    application.include_router(websocket_router)

    return application


app = create_app()
