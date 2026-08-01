"""记忆 REST API——会话摘要、长期偏好、冲突管理。

会话摘要按 conversation_id 隔离。
长期偏好跨会话共享，支持检索、列表、软删除、手动冲突解决。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.db.session import get_session
from agenthub.rag.knowledge.embedder import EmbeddingClient, get_settings
from agenthub.rag.memory.conflict import ConflictResolver
from agenthub.rag.memory.long_term import LongTermMemory
from agenthub.rag.memory.summarizer import SummaryService

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["memories"])


# ── 依赖注入 ────────────────────────────────────────────────────────────────────


async def get_embedder() -> EmbeddingClient:
    """获取 Embedding 客户端。"""
    settings = get_settings()
    deps = settings.embedding_dependencies()
    return EmbeddingClient(deps)


EmbedderDependency = Annotated[EmbeddingClient, Depends(get_embedder)]
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


# ── 响应模型 ────────────────────────────────────────────────────────────────────


class SummaryResponse(BaseModel):
    """会话摘要响应。"""
    id: uuid.UUID
    round_start: int
    round_end: int
    summary: str
    is_full_merge: bool
    created_at: str


class PreferenceResponse(BaseModel):
    """偏好响应。"""
    id: uuid.UUID
    category: str
    key: str
    value: str
    importance: float
    is_active: bool
    conflict_flag: bool
    previous_version_id: uuid.UUID | None
    created_at: str
    updated_at: str


class PreferenceCreate(BaseModel):
    """创建偏好请求。"""
    category: str = Field(max_length=50)
    key: str = Field(max_length=255)
    value: str
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class PreferenceUpdate(BaseModel):
    """更新偏好请求。"""
    value: str | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)


class ManualResolveRequest(BaseModel):
    """手动冲突解决请求。"""
    keep: bool = Field(description="True 保留，False 软删除")


class ConflictResponse(BaseModel):
    """冲突条目响应。"""
    id: uuid.UUID
    category: str
    key: str
    value: str
    importance: float
    created_at: str


# ── 摘要端点 ────────────────────────────────────────────────────────────────────


@router.get("/conversations/{conversation_id}/summaries", response_model=list[SummaryResponse])
async def list_summaries(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    session: SessionDependency,
) -> list[SummaryResponse]:
    """获取会话的摘要列表（按时间排序）。"""
    svc = SummaryService(session)
    summaries = await svc.get_summaries(conversation_id)
    return [
        SummaryResponse(
            id=s.id,
            round_start=s.round_start,
            round_end=s.round_end,
            summary=s.summary,
            is_full_merge=s.is_full_merge,
            created_at=s.created_at.isoformat(),
        )
        for s in summaries
    ]


@router.post("/conversations/{conversation_id}/summarize", response_model=SummaryResponse)
async def trigger_summarize(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    session: SessionDependency,
) -> SummaryResponse:
    """手动触发会话摘要生成（全量合并版本）。"""
    svc = SummaryService(session)
    # 重新全量生成
    new_messages = ""  # 需要先获取消息
    from sqlalchemy import select

    from agenthub.models.orm import Message
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.sequence.asc())
    )
    messages = result.scalars().all()
    if not messages:
        raise HTTPException(status_code=404, detail="会话无消息")
    new_messages = "\n".join(
        f"[{m.role}]: {m.content[:500] if m.content else ''}"
        for m in messages
    )
    from agenthub.rag.memory.summarizer import SummaryService as Svc
    svc2 = Svc(session)
    # 绕过轮次逻辑直接用全量生成
    prompt = """你是一个会话摘要助手。请将以下完整对话历史总结为结构化摘要。

对话历史:
""" + new_messages[:8000] + """

请输出结构化摘要，包含：
1. [当前状态] 对话进展和代码完成度
2. [关键决策] 做出的技术选择或架构决定
3. [遗留问题] 尚未解决的问题或待办事项

输出格式（纯文本，不超过 300 字）:
[当前状态] ...
[关键决策] ...
[遗留问题] ..."""
    import httpx

    from agenthub.core.config import get_settings as gs
    settings = gs()
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
                "max_tokens": 400,
                "temperature": 0.3,
            },
        )
        summary_text = ""
        if resp.status_code < 400:
            data = resp.json()
            summary_text = data["choices"][0]["message"]["content"].strip()

    from datetime import UTC, datetime

    from agenthub.models.orm import ConversationSummary
    summary = ConversationSummary(
        id=uuid.uuid4(),
        project_id=project_id,
        conversation_id=conversation_id,
        round_start=0,
        round_end=len(messages),
        summary=summary_text or "摘要生成失败",
        is_full_merge=True,
        created_at=datetime.now(UTC),
    )
    session.add(summary)
    await session.flush()
    return SummaryResponse(
        id=summary.id,
        round_start=summary.round_start,
        round_end=summary.round_end,
        summary=summary.summary,
        is_full_merge=summary.is_full_merge,
        created_at=summary.created_at.isoformat(),
    )


# ── 偏好端点 ────────────────────────────────────────────────────────────────────


@router.get("/preferences", response_model=list[PreferenceResponse])
async def list_preferences(
    project_id: uuid.UUID,
    session: SessionDependency,
    category: str | None = Query(default=None),
) -> list[PreferenceResponse]:
    """列出项目活跃偏好。"""
    ltm = LongTermMemory(session)
    prefs = await ltm.list_preferences(project_id, category)
    return [
        PreferenceResponse(
            id=p.id,
            category=p.category,
            key=p.key,
            value=p.value,
            importance=p.importance,
            is_active=p.is_active,
            conflict_flag=p.conflict_flag,
            previous_version_id=p.previous_version_id,
            created_at=p.created_at.isoformat(),
            updated_at=p.updated_at.isoformat(),
        )
        for p in prefs
    ]


@router.post("/preferences", response_model=PreferenceResponse, status_code=201)
async def create_preference(
    project_id: uuid.UUID,
    data: PreferenceCreate,
    session: SessionDependency,
    embedder: EmbedderDependency,
) -> PreferenceResponse:
    """创建偏好——含冲突检测。"""
    from datetime import UTC, datetime

    from agenthub.models.orm import UserPreference
    new_pref = UserPreference(
        id=uuid.uuid4(),
        project_id=project_id,
        category=data.category,
        key=data.key,
        value=data.value,
        importance=data.importance,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        last_accessed_at=datetime.now(UTC),
    )
    # 尝试向量化
    if embedder.is_available:
        try:
            emb = await embedder.embed_text(f"{data.key}: {data.value}")
            new_pref.embedding = emb
        except Exception:
            pass

    resolver = ConflictResolver(session)
    result = await resolver.resolve(project_id, new_pref)
    return PreferenceResponse(
        id=result.id,
        category=result.category,
        key=result.key,
        value=result.value,
        importance=result.importance,
        is_active=result.is_active,
        conflict_flag=result.conflict_flag,
        previous_version_id=result.previous_version_id,
        created_at=result.created_at.isoformat(),
        updated_at=result.updated_at.isoformat(),
    )


@router.patch("/preferences/{preference_id}", response_model=PreferenceResponse)
async def update_preference(
    project_id: uuid.UUID,
    preference_id: uuid.UUID,
    data: PreferenceUpdate,
    session: SessionDependency,
) -> PreferenceResponse:
    """更新偏好值或重要性。"""
    from agenthub.models.orm import UserPreference
    pref = await session.get(UserPreference, preference_id)
    if pref is None or pref.project_id != project_id:
        raise HTTPException(status_code=404, detail="偏好未找到")
    from datetime import UTC, datetime
    if data.value is not None:
        pref.value = data.value
    if data.importance is not None:
        pref.importance = data.importance
    pref.updated_at = datetime.now(UTC)
    await session.flush()
    return PreferenceResponse(
        id=pref.id,
        category=pref.category,
        key=pref.key,
        value=pref.value,
        importance=pref.importance,
        is_active=pref.is_active,
        conflict_flag=pref.conflict_flag,
        previous_version_id=pref.previous_version_id,
        created_at=pref.created_at.isoformat(),
        updated_at=pref.updated_at.isoformat(),
    )


@router.delete("/preferences/{preference_id}", status_code=204)
async def delete_preference(
    project_id: uuid.UUID,
    preference_id: uuid.UUID,
    session: SessionDependency,
) -> None:
    """软删除偏好。"""
    ltm = LongTermMemory(session)
    deleted = await ltm.soft_delete(preference_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="偏好未找到")


# ── 冲突管理端点 ────────────────────────────────────────────────────────────────


@router.get("/preferences/conflicts", response_model=list[ConflictResponse])
async def list_conflicts(
    project_id: uuid.UUID,
    session: SessionDependency,
) -> list[ConflictResponse]:
    """列出冲突标记的偏好。"""
    resolver = ConflictResolver(session)
    conflicts = await resolver.list_conflicts(project_id)
    return [
        ConflictResponse(
            id=c.id,
            category=c.category,
            key=c.key,
            value=c.value,
            importance=c.importance,
            created_at=c.created_at.isoformat(),
        )
        for c in conflicts
    ]


@router.post("/preferences/{preference_id}/resolve-conflict")
async def resolve_conflict(
    project_id: uuid.UUID,
    preference_id: uuid.UUID,
    data: ManualResolveRequest,
    session: SessionDependency,
) -> dict[str, str]:
    """手动解决冲突——keep=True 保留，False 软删除。"""
    resolver = ConflictResolver(session)
    resolved = await resolver.resolve_conflict_manually(preference_id, data.keep)
    if not resolved:
        raise HTTPException(status_code=404, detail="偏好未找到或非冲突状态")
    return {"status": "resolved", "action": "kept" if data.keep else "deleted"}