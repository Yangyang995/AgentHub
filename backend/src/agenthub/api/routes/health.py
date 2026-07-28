"""不依赖外部服务的基础健康检查。"""

from fastapi import APIRouter, Request, status

from agenthub.core.config import Settings
from agenthub.schemas.health import LiveHealthResponse, ReadyChecks, ReadyHealthResponse

router = APIRouter(prefix="/health", tags=["health"])


def _settings_from(request: Request) -> Settings:
    """从应用状态读取启动时已解析的配置，避免请求期间重复读取环境。"""

    settings: Settings = request.app.state.settings
    return settings


@router.get(
    "/live",
    response_model=LiveHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="检查 API 进程是否存活",
)
async def live(request: Request) -> LiveHealthResponse:
    """只证明 FastAPI 事件循环可以响应，不探测外部依赖。"""

    settings = _settings_from(request)
    return LiveHealthResponse(service=settings.app_name)


@router.get(
    "/ready",
    response_model=ReadyHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="检查 Phase 1 应用是否就绪",
)
async def ready(request: Request) -> ReadyHealthResponse:
    """确认类型化配置已加载。

    数据库和模型服务将在对应阶段接入；在此之前不得通过健康检查隐式连接它们。
    """

    settings = _settings_from(request)
    return ReadyHealthResponse(service=settings.app_name, checks=ReadyChecks())
