"""代码审查专家——解析代码后逐维审查。"""

from agenthub.adapters.protocol import AgentEvent, AgentTask
from agenthub.agents.base import BaseAgentRunner
from agenthub.agents import register

@register("code_review")
class CodeReviewerRunner(BaseAgentRunner):
    """代码审查管线：解析代码 -> 逐维审查。"""

    async def run(self, task: AgentTask):
        instruction = (
            "从以下内容中提取所有代码块，忽略非代码文本。"
            "如果内容本身就是代码，直接原样返回。不要添加任何解释。"
        )
        code = await self._think(task, instruction)
        if not code:
            code = task.message_content
        messages = [{"role": "user", "content": "请审查以下代码：\n\n" + code}]
        async for event in self._act(task, messages):
            yield event
