"""Agent Adapter 包——统一协议与具体实现。

导出公共类型与两个 Adapter 实现：
- MockAdapter：确定性脚本控制
- CodexCLIAdapter：安全子进程封装
"""

from agenthub.adapters.codex_cli import CodexCLIAdapter
from agenthub.adapters.mock_adapter import (
    MockAdapter,
    MockAdapterScript,
    MockScriptStep,
)
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

__all__ = [  # noqa: RUF022
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
    # Codex CLI Adapter
    "CodexCLIAdapter",
]
