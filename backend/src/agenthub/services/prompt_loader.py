"""Agent System Prompt 加载工具。

从 backend/prompts/agents/ 目录加载各子 Agent 的 System Prompt 文件，
运行时一次性加载并缓存，避免重复文件 I/O。
"""

import re
from pathlib import Path

# 预置 Agent 名称 → 能力 → Prompt 文件名映射（4 个预置子 Agent）
_PRESET_AGENT_CONFIGS: list[dict[str, object]] = [
    {
        "name": "架构设计专家",
        "capability": "architecture_design",
        "prompt_file": "architecture_designer.md",
    },
    {
        "name": "代码生成专家",
        "capability": "code_generation",
        "prompt_file": "code_generator.md",
    },
    {
        "name": "代码审查专家",
        "capability": "code_review",
        "prompt_file": "code_reviewer.md",
    },
    {
        "name": "测试专家",
        "capability": "testing",
        "prompt_file": "tester.md",
    },
    {
        "name": "DeepSeek",
        "capability": "deepseek",
        "prompt_file": "deepseek.md",
    },
]

# 能力 → System Prompt 文本的缓存
_capability_prompt_cache: dict[str, str] = {}


def _discover_prompts_dir() -> Path:
    """探测 prompts 目录位置，优先使用源码树路径，回退到当前工作目录。"""
    current = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = current / "prompts" / "agents"
        if candidate.is_dir():
            return candidate
        current = current.parent
    fallback = Path.cwd() / "backend" / "prompts" / "agents"
    return fallback


def load_system_prompt(capability: str) -> str | None:
    """根据能力标识加载对应的 System Prompt 文本（带缓存）。

    Args:
        capability: AgentCapability 枚举值字符串，如 "code_generation"。

    Returns:
        System Prompt 文本；若文件不存在则返回 None。
    """
    if capability in _capability_prompt_cache:
        return _capability_prompt_cache[capability]

    prompt_file = None
    for config in _PRESET_AGENT_CONFIGS:
        if config["capability"] == capability:
            prompt_file = str(config["prompt_file"])
            break

    if prompt_file is None:
        return None

    prompts_dir = _discover_prompts_dir()
    file_path = prompts_dir / prompt_file

    try:
        raw = file_path.read_text(encoding="utf-8")
        content = raw.strip()
    except (FileNotFoundError, OSError):
        return None

    _capability_prompt_cache[capability] = content
    return content


def get_preset_agent_configs() -> list[dict[str, object]]:
    """返回预置 Agent 配置列表，供项目注册时播种使用。"""
    return _PRESET_AGENT_CONFIGS.copy()
