"""AgentHub FastAPI 应用入口。"""

from fastapi import FastAPI

from agenthub.api.routes.health import router as health_router
from agenthub.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建可测试、无导入期外部副作用的 FastAPI 应用。"""

    resolved_settings = settings or get_settings()
    application = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        docs_url="/docs" if resolved_settings.environment != "production" else None,
        redoc_url=None,
    )
    application.state.settings = resolved_settings
    application.include_router(health_router)
    return application


app = create_app()
