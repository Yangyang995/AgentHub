"""Agent Adapter 包——统一协议与具体实现。

导出公共类型与两个 Adapter 实现：
- MockAdapter：确定性脚本控制
- OpenAICompatibleAdapter：DeepSeek 通过 OpenAI 兼容接口
"""

from agenthub.adapters.mock_adapter import (
    MockAdapter,
    MockAdapterScript,
    MockScriptStep,
)
from agenthub.adapters.openai_compatible import OpenAICompatibleAdapter
from agenthub.adapters.protocol import (
    AdapterErrorCode,
    AdapterHealth,
    AgentEvent,
    AgentResult,
    AgentTask,
    ArtifactCreatedEvent,
    ArtifactInfo,
    ContentDeltaEvent,
    ExecutionErrorEvent,
    ExecutionStatusEvent,
    ExecutionUsageEvent,
)

__all__ = [
    # 协议类型
    "AdapterErrorCode",
    "AdapterHealth",
    "AgentEvent",
    "AgentResult",
    "AgentTask",
    "ArtifactCreatedEvent",
    "ArtifactInfo",
    "ContentDeltaEvent",
    "ExecutionErrorEvent",
    "ExecutionStatusEvent",
    "ExecutionUsageEvent",
    # Mock Adapter
    "MockAdapter",
    "MockAdapterScript",
    "MockScriptStep",
    # OpenAI 兼容 HTTP Adapter（DeepSeek）
    "OpenAICompatibleAdapter",
]
