"""按领域划分的路由模块。"""

from agenthub.api.routes.agents import router as agents_router
from agenthub.api.routes.approvals import router as approvals_router
from agenthub.api.routes.artifacts import router as artifacts_router
from agenthub.api.routes.chat import router as chat_router
from agenthub.api.routes.deployments import router as deployments_router
from agenthub.api.routes.health import router as health_router
from agenthub.api.routes.knowledge import router as knowledge_router
from agenthub.api.routes.memories import router as memories_router
from agenthub.api.routes.previews import router as previews_router
from agenthub.api.routes.projects import router as projects_router

__all__ = [
    "agents_router",
    "approvals_router",
    "artifacts_router",
    "chat_router",
    "deployments_router",
    "health_router",
    "knowledge_router",
    "memories_router",
    "previews_router",
    "projects_router",
]
