"""架构设计专家——分析需求后输出设计方案。"""

from agenthub.adapters.protocol import AgentEvent, AgentTask
from agenthub.agents.base import BaseAgentRunner
from agenthub.agents import register

@register("architecture_design")
class ArchitectureDesignerRunner(BaseAgentRunner):
    """架构设计管线：需求分析 -> 方案设计。"""

    async def run(self, task: AgentTask):
        instruction = (
            "分析以下需求的业务场景、约束条件和技术上下文。"
            "简要列出关键设计考量，不要输出完整方案。"
        )
        analysis = await self._think(task, instruction)
        if not analysis:
            analysis = "未能完成需求分析"
        messages = [{"role": "user", "content": (
            "需求分析结果：\n" + analysis + "\n\n"
            "请根据以上分析设计架构方案：" + task.message_content
        )}]
        async for event in self._act(task, messages):
            yield event
