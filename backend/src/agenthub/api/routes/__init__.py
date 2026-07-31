"""按领域划分的路由模块。"""

from agenthub.api.routes.agents import router as agents_router
from agenthub.api.routes.chat import router as chat_router
from agenthub.api.routes.health import router as health_router
from agenthub.api.routes.projects import router as projects_router

__all__ = [
    "agents_router",
    "chat_router",
    "health_router",
    "projects_router",
]
