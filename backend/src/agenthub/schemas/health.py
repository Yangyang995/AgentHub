"""健康检查响应 Schema。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class LiveHealthResponse(BaseModel):
    """进程存活检查的稳定响应。"""

    model_config = ConfigDict(frozen=True)

    status: Literal["alive"] = "alive"
    service: str


class ReadyChecks(BaseModel):
    """就绪检查中各项依赖的状态。"""

    model_config = ConfigDict(frozen=True)

    configuration: Literal["ok"] = "ok"


class ReadyHealthResponse(BaseModel):
    """Phase 1 应用就绪检查的稳定响应。"""

    model_config = ConfigDict(frozen=True)

    status: Literal["ready"] = "ready"
    service: str
    checks: ReadyChecks
