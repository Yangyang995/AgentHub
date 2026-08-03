"""记忆检索协调器——统一管理第二层（会话摘要）和第三层（长期偏好）的检索与注入。

在 ChatService.submit_message() 消息到达后、Agent 执行前调用，
将检索结果注入 AgentTask.context。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.rag.knowledge.embedder import EmbeddingClient
from agenthub.rag.memory.long_term import LongTermMemory, PreferenceResult
from agenthub.rag.memory.summarizer import SummaryService


@dataclass
class MemoryContext:
    """记忆检索结果——注入 AgentTask.context 的结构。"""

    summary_context: str | None = None
    """当前会话的最新摘要文本。"""

    preferences: list[PreferenceResult] = field(default_factory=list)
    """相关跨会话长期偏好。"""

    def to_context_dict(self) -> dict[str, Any]:
        """转为 AgentTask.context 可接受的字典格式。"""
        ctx: dict[str, Any] = {}
        if self.summary_context:
            ctx["summary_context"] = self.summary_context
        if self.preferences:
            ctx["preference_context"] = [
                {
                    "category": p.category,
                    "key": p.key,
                    "value": p.value,
                    "importance": p.importance,
                    "score": p.score,
                }
                for p in self.preferences
            ]
        return ctx


class MemoryRetriever:
    """记忆检索协调器——汇总第二层 + 第三层检索结果。"""

    def __init__(
        self,
        session: AsyncSession,
        embedder: EmbeddingClient | None = None,
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._summary_service = SummaryService(session, embedder)
        self._long_term = LongTermMemory(session)

    async def retrieve(
        self,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        query: str,
    ) -> MemoryContext:
        """检索当前会话摘要 + 跨会话长期偏好。

        Args:
            project_id: 项目 ID。
            conversation_id: 当前会话 ID（用于摘要隔离）。
            query: 用户当前消息。

        Returns:
            MemoryContext——摘要和偏好汇总。
        """
        ctx = MemoryContext()

        # 第二层：本会话最新全量合并摘要
        summary = await self._summary_service.get_latest_full_merge(conversation_id)
        if summary:
            ctx.summary_context = summary.summary

        # 第三层：跨会话长期偏好检索
        query_embedding: list[float] | None = None
        if self._embedder is not None and self._embedder.is_available:
            try:
                query_embedding = await self._embedder.embed_text(query)
            except Exception:
                query_embedding = None

        preferences = await self._long_term.retrieve(
            project_id=project_id,
            query=query,
            query_embedding=query_embedding,
            top_k=3,
        )
        ctx.preferences = preferences

        return ctx