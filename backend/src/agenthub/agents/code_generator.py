"""代码生成专家——分析需求后生成代码。"""

from agenthub.adapters.protocol import AgentEvent, AgentTask
from agenthub.agents.base import BaseAgentRunner
from agenthub.agents import register

@register("code_generation")
class CodeGeneratorRunner(BaseAgentRunner):
    """代码生成管线：需求分析 -> 代码生成。"""

    async def run(self, task: AgentTask):
        # Step 1: 需求分析
        instruction = (
            "分析以下编程需求的技术要点、关键算法和数据结构。"
            "简要列出实现方案，不要输出代码。"
        )
        analysis = await self._think(task, instruction)
        if not analysis:
            analysis = "未能完成需求分析"
        # Step 2: 代码生成
        messages = [{"role": "user", "content": "需求分析结果：\n" + analysis + "\n\n请根据以上分析生成代码：" + task.message_content}]
        async for event in self._act(task, messages):
            yield event
