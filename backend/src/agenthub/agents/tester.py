"""测试专家——分析代码后生成测试用例。"""

from agenthub.adapters.protocol import AgentEvent, AgentTask
from agenthub.agents.base import BaseAgentRunner
from agenthub.agents import register

@register("testing")
class TesterRunner(BaseAgentRunner):
    """测试管线：代码分析 -> 测试生成。"""

    async def run(self, task: AgentTask):
        instruction = (
            "分析对话历史中代码生成专家产出的代码，"
            "识别其功能逻辑、关键路径和边界条件。"
            "简要列出测试策略（测试哪些方面），不要输出任何代码。"
        )
        analysis = await self._think(task, instruction)
        if not analysis:
            analysis = "未能完成代码分析"
        messages = [{"role": "user", "content": (
            "代码分析结果：\n" + analysis + "\n\n"
            "请为对话历史中代码生成专家产出的代码编写测试用例。\n"
            "重要：只输出测试代码，不要重新生成或重复任何实现代码。\n"
            "原始需求：" + task.message_content
        )}]
        async for event in self._act(task, messages):
            yield event
