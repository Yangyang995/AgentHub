"""第二层：摘要记忆——递增摘要 + 每10轮全量合并校准。

核心策略：
- 每轮新消息产生后递增更新摘要（合并到上一轮摘要）
- 每 10 轮做一次全量合并校准（从消息历史完全重新生成，防止偏差累积）
- 仅全量合并版本向量化后存入 pgvector 供检索
- conversation_id 限定保证会话隔离
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.core.config import get_settings
from agenthub.models.orm import ConversationSummary, Message

# 全量合并间隔（轮次）
_FULL_MERGE_INTERVAL = 10

# 摘要 Prompt 模板
_SUMMARIZE_INCREMENTAL_PROMPT = """你是一个会话摘要助手。请基于{previous}和本轮对话增量更新摘要。

上一轮摘要:
{previous_summary}

本轮新增消息:
{new_messages}

请输出更新后的结构化摘要，包含：
1. [当前状态] 对话进展和代码完成度
2. [关键决策] 做出的技术选择或架构决定
3. [遗留问题] 尚未解决的问题或待办事项

输出格式（纯文本，不超过 300 字）:
[当前状态] ...
[关键决策] ...
[遗留问题] ..."""

_SUMMARIZE_FULL_PROMPT = """你是一个会话摘要助手。请将以下完整对话历史总结为结构化摘要。

对话历史:
{conversation_history}

请输出结构化摘要，包含：
1. [当前状态] 对话进展和代码完成度
2. [关键决策] 做出的技术选择或架构决定
3. [遗留问题] 尚未解决的问题或待办事项

输出格式（纯文本，不超过 300 字）:
[当前状态] ...
[关键决策] ...
[遗留问题] ..."""


class SummaryService:
    """会话摘要服务——递增 + 全量合并。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def summarize_round(
        self,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        new_messages_text: str,
        round_number: int,
    ) -> ConversationSummary | None:
        """每轮调用——递增摘要或全量合并。

        Returns:
            仅全量合并版本返回 ConversationSummary（含向量），普通递增返回 None。
        """
        is_full = round_number > 0 and round_number % _FULL_MERGE_INTERVAL == 0

        if round_number == 0:
            # 首轮：创建初始摘要
            summary_text = await self._generate_full_summary(new_messages_text)
            return await self._save_summary(
                project_id, conversation_id, round_number, round_number,
                summary_text, is_full_merge=True,
            )

        if is_full:
            # 全量合并：拉取全部消息重新生成
            messages_text = await self._get_all_messages_text(conversation_id)
            summary_text = await self._generate_full_summary(messages_text)
            return await self._save_summary(
                project_id, conversation_id, 0, round_number,
                summary_text, is_full_merge=True,
            )
        else:
            # 递增摘要：合并到上一轮摘要
            previous = await self._get_latest_summary(conversation_id)
            previous_text = previous.summary if previous else "无"
            summary_text = await self._generate_incremental_summary(previous_text, new_messages_text)
            await self._save_summary(
                project_id, conversation_id, round_number, round_number,
                summary_text, is_full_merge=False,
            )
            return None

    async def get_summaries(
        self, conversation_id: uuid.UUID
    ) -> list[ConversationSummary]:
        """获取会话摘要列表（按轮次排序）。"""
        result = await self._session.execute(
            select(ConversationSummary)
            .where(ConversationSummary.conversation_id == conversation_id)
            .order_by(ConversationSummary.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_latest_full_merge(
        self, conversation_id: uuid.UUID
    ) -> ConversationSummary | None:
        """获取最近一次全量合并摘要。"""
        result = await self._session.scalar(
            select(ConversationSummary)
            .where(
                ConversationSummary.conversation_id == conversation_id,
                ConversationSummary.is_full_merge == True,  # noqa: E712
            )
            .order_by(ConversationSummary.created_at.desc())
            .limit(1)
        )
        return result

    async def _get_latest_summary(self, conversation_id: uuid.UUID) -> ConversationSummary | None:
        """获取最近一条摘要（任意类型）。"""
        result = await self._session.scalar(
            select(ConversationSummary)
            .where(ConversationSummary.conversation_id == conversation_id)
            .order_by(ConversationSummary.created_at.desc())
            .limit(1)
        )
        return result

    async def _get_all_messages_text(self, conversation_id: uuid.UUID) -> str:
        """拉取会话全部消息并格式化为文本。"""
        result = await self._session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence.asc())
        )
        messages = result.scalars().all()
        lines: list[str] = []
        for msg in messages:
            role = msg.role if msg.role else "unknown"
            content = msg.content[:500] if msg.content else ""
            lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    async def _generate_incremental_summary(self, previous: str, new_messages: str) -> str:
        """LLM 递增更新摘要。"""
        prompt = _SUMMARIZE_INCREMENTAL_PROMPT.format(
            previous="",
            previous_summary=previous,
            new_messages=new_messages[:3000],
        )
        return await self._call_llm(prompt, max_tokens=400)

    async def _generate_full_summary(self, messages_text: str) -> str:
        """LLM 全量重新生成摘要。"""
        prompt = _SUMMARIZE_FULL_PROMPT.format(
            conversation_history=messages_text[:8000],
        )
        return await self._call_llm(prompt, max_tokens=400)

    async def _save_summary(
        self,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        round_start: int,
        round_end: int,
        summary_text: str,
        is_full_merge: bool,
    ) -> ConversationSummary:
        """持久化摘要记录。"""
        summary = ConversationSummary(
            id=uuid.uuid4(),
            project_id=project_id,
            conversation_id=conversation_id,
            round_start=round_start,
            round_end=round_end,
            summary=summary_text,
            is_full_merge=is_full_merge,
            created_at=datetime.now(UTC),
        )
        self._session.add(summary)
        await self._session.flush()
        return summary

    async def _call_llm(self, prompt: str, max_tokens: int = 400) -> str:
        """调用 LLM 生成文本。"""
        settings = get_settings()
        deps = settings.runtime_dependencies()
        async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as client:
            resp = await client.post(
                f"{deps.llm_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {deps.llm_api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": deps.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                },
            )
            if resp.status_code >= 400:
                return "摘要生成失败"
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()