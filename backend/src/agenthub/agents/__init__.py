"""Agent 执行器注册表——按能力类型选择对应 Runner。"""

from agenthub.agents.base import BaseAgentRunner

_RUNNER_REGISTRY: dict[str, type[BaseAgentRunner]] = {}


def register(capability: str):
    """装饰器：将 Runner 类注册到指定能力。"""
    def decorator(cls: type[BaseAgentRunner]) -> type[BaseAgentRunner]:
        _RUNNER_REGISTRY[capability] = cls
        return cls
    return decorator

def get_runner(capability: str) -> type[BaseAgentRunner] | None:
    """根据能力标识返回对应的 Runner 类，无匹配返回 None。"""
    return _RUNNER_REGISTRY.get(capability)

# 导入触发注册
from agenthub.agents.code_generator import CodeGeneratorRunner  # noqa: E402, F401
from agenthub.agents.code_reviewer import CodeReviewerRunner  # noqa: E402, F401
from agenthub.agents.tester import TesterRunner  # noqa: E402, F401
from agenthub.agents.architecture_designer import ArchitectureDesignerRunner  # noqa: E402, F401
