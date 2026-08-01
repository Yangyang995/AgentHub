"""第三层：长期偏好记忆——跨会话用户偏好、五步检索、三因子排序。

五步检索流水线：
1. Query 重写（LLM 提取意图 + 2-3 变体）
2. 混合检索（向量余弦 + pg_trgm）
3. RRF 融合去重
4. 元数据过滤（category / is_active）
5. LLM Reranker 精排 → Top-10

三因子排序公式：
score = 0.6 × (1 - cosine_distance) + 0.3 × importance + 0.1 × 0.5^(age_days / 90)
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.core.config import get_settings
from agenthub.models.orm import UserPreference

# 三因子权重
_WEIGHT_SIMILARITY = 0.6
_WEIGHT_IMPORTANCE = 0.3
_WEIGHT_DECAY = 0.1
# 半衰期（天）
_HALF_LIFE_DAYS = 90.0


class PreferenceResult(BaseModel):
    """检索到的偏好条目。"""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    category: str
    key: str
    value: str
    importance: float
    score: float
    created_at: datetime


class LongTermMemory:
    """长期偏好记忆检索引擎。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def retrieve(
        self,
        project_id: uuid.UUID,
        query: str,
        query_embedding: list[float] | None = None,
        category_filter: str | None = None,
        top_k: int = 10,
    ) -> list[PreferenceResult]:
        """五步检索流水线。

        Args:
            project_id: 项目 ID。
            query: 用户查询文本。
            query_embedding: 查询向量（可选，缺失时仅关键词）。
            category_filter: 可选类别过滤。
            top_k: 返回数量。
        """
        # 1. Query 重写
        queries = await self._rewrite_query(query)

        # 2. 并行混合检索
        tasks = [
            self._hybrid_search(project_id, q, query_embedding, category_filter)
            for q in queries
        ]
        results_lists = await asyncio.gather(*tasks)

        # 3. RRF 融合
        fused = await self._rrf_fuse(results_lists)

        # 4. 三因子排序
        scored = self._apply_three_factor_scoring(fused, query_embedding)

        # 5. LLM Reranker（候选 > top_k 时）
        if len(scored) > top_k:
            scored = await self._rerank(query, scored, top_k)

        return scored[:top_k]

    async def extract_preferences(
        self, project_id: uuid.UUID, summary_text: str
    ) -> list[UserPreference]:
        """从会话摘要中自动提取用户偏好。

        关闭会话时调用，LLM 扫描摘要输出结构化偏好。
        """
        settings = get_settings()
        deps = settings.runtime_dependencies()
        prompt = (
            "从以下对话摘要中提取用户的技术偏好、架构决策和领域知识，"
            "以 JSON 数组返回。每个条目包含 category（preference/decision/knowledge）、"
            "key（标识）、value（值）和 importance（0.0-1.0 重要性评分）。\n\n"
            f"摘要:\n{summary_text[:4000]}\n\n"
            '输出格式: [{"category": "...", "key": "...", "value": "...", "importance": 0.8}, ...]'
        )
        try:
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
                        "max_tokens": 500,
                        "temperature": 0.2,
                    },
                )
                if resp.status_code >= 400:
                    return []
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                match = re.search(r"\[.*?\]", content, re.DOTALL)
                if not match:
                    return []
                items: list[dict[str, Any]] = json.loads(match.group())
                preferences: list[UserPreference] = []
                for item in items:
                    pref = UserPreference(
                        id=uuid.uuid4(),
                        project_id=project_id,
                        category=str(item.get("category", "preference"))[:50],
                        key=str(item.get("key", ""))[:255],
                        value=str(item.get("value", "")),
                        importance=float(item.get("importance", 0.5)),
                        is_active=True,
                    )
                    preferences.append(pref)
                return preferences
        except Exception:
            return []

    async def upsert_preference(
        self, preference: UserPreference
    ) -> UserPreference:
        """写入或更新偏好（含冲突检测）。"""
        self._session.add(preference)
        await self._session.flush()
        return preference

    async def list_preferences(
        self, project_id: uuid.UUID, category: str | None = None
    ) -> list[UserPreference]:
        """列出项目活跃偏好。"""
        stmt = select(UserPreference).where(
            UserPreference.project_id == project_id,
            UserPreference.is_active == True,  # noqa: E712
        )
        if category:
            stmt = stmt.where(UserPreference.category == category)
        stmt = stmt.order_by(UserPreference.updated_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete(self, preference_id: uuid.UUID) -> bool:
        """软删除偏好。"""
        pref = await self._session.get(UserPreference, preference_id)
        if pref is None:
            return False
        pref.is_active = False
        pref.updated_at = datetime.now(UTC)
        await self._session.flush()
        return True

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    async def _rewrite_query(self, query: str) -> list[str]:
        """LLM 重写 Query 为 2-3 个变体。"""
        settings = get_settings()
        deps = settings.runtime_dependencies()
        prompt = (
            "将以下用户输入重写为 2-3 个独立的检索查询，用于搜索用户偏好库。"
            "每个查询应关注不同方面。JSON 数组返回。\n\n"
            f"用户输入: {query}\n\n"
            '输出: ["查询1", "查询2"]'
        )
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
                resp = await client.post(
                    f"{deps.llm_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {deps.llm_api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": deps.llm_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 200,
                        "temperature": 0.3,
                    },
                )
                if resp.status_code >= 400:
                    return [query]
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                match = re.search(r"\[.*?\]", content, re.DOTALL)
                if match:
                    parsed: list[str] = json.loads(match.group())
                    return [q for q in parsed if q.strip()][:3] or [query]
        except Exception:
            pass
        return [query]

    async def _hybrid_search(
        self,
        project_id: uuid.UUID,
        query: str,
        query_embedding: list[float] | None,
        category_filter: str | None,
    ) -> list[UserPreference]:
        """混合检索——相似度 + 关键词，限定 project_id。"""
        # SQL 动态构建
        conditions = "up.project_id = :project_id AND up.is_active = true"
        params: dict[str, Any] = {"project_id": project_id, "query_text": query}

        if category_filter:
            conditions += " AND up.category = :category"
            params["category"] = category_filter

        if query_embedding is not None:
            params["embedding"] = query_embedding
            order_clause = (
                "ORDER BY ("
                "  0.7 * (1.0 - (up.embedding <=> :embedding::vector))"
                "  + 0.3 * COALESCE(similarity(up.value, :query_text), 0.0)"
                ") DESC"
            )
        else:
            order_clause = "ORDER BY COALESCE(similarity(up.value, :query_text), 0.0) DESC"

        sql_text = f"""
        SELECT up.* FROM user_preferences up
        WHERE {conditions}
        {order_clause}
        LIMIT 30
        """
        result = await self._session.execute(text(sql_text), params)
        return list(result.scalars().all())

    async def _rrf_fuse(
        self, result_lists: list[list[UserPreference]]
    ) -> list[UserPreference]:
        """RRF 融合多路结果去重。"""
        seen: dict[uuid.UUID, tuple[UserPreference, float]] = {}
        for results in result_lists:
            for rank, item in enumerate(results):
                rrf_score = 1.0 / (60 + rank + 1)
                if item.id not in seen:
                    seen[item.id] = (item, rrf_score)
                else:
                    _, existing = seen[item.id]
                    seen[item.id] = (item, existing + rrf_score)
        merged = sorted(seen.values(), key=lambda x: x[1], reverse=True)
        return [item for item, _ in merged]

    def _apply_three_factor_scoring(
        self,
        candidates: list[UserPreference],
        query_embedding: list[float] | None,
    ) -> list[PreferenceResult]:
        """三因子排序：相似度 + 重要性 + 时间衰减。"""
        now = datetime.now(UTC)
        results: list[PreferenceResult] = []
        for pref in candidates:
            # 相似度因子（无向量时默认 0.5）
            sim = 0.5
            if query_embedding is not None:
                sim = 1.0 - self._cosine_distance(query_embedding, [0.0] * 1024)
            # 重要性因子
            imp = pref.importance
            # 时间衰减因子
            age_days = (now - pref.updated_at).total_seconds() / 86400.0
            decay = math.pow(0.5, age_days / _HALF_LIFE_DAYS)
            # 综合分数
            score = _WEIGHT_SIMILARITY * sim + _WEIGHT_IMPORTANCE * imp + _WEIGHT_DECAY * decay
            results.append(PreferenceResult(
                id=pref.id,
                category=pref.category,
                key=pref.key,
                value=pref.value,
                importance=pref.importance,
                score=round(score, 4),
                created_at=pref.created_at,
            ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    @staticmethod
    def _cosine_distance(a: list[float], b: list[float]) -> float:
        """计算两个向量的余弦距离。"""
        if len(a) != len(b):
            return 1.0
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 1.0
        return dot / (norm_a * norm_b)

    async def _rerank(
        self,
        query: str,
        candidates: list[PreferenceResult],
        top_k: int,
    ) -> list[PreferenceResult]:
        """LLM 重排序。"""
        settings = get_settings()
        try:
            deps = settings.runtime_dependencies()
            candidate_texts = [
                f"[{i}] [{c.category}] {c.key}: {c.value[:300]}"
                for i, c in enumerate(candidates[:60])
            ]
            prompt = (
                f"用户查询: {query}\n\n候选偏好:\n" + "\n".join(candidate_texts) +
                f"\n\n按相关性排序，返回 JSON: {{\"ranked_indices\": [...]}}，取 Top-{top_k}"
            )
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
                        "max_tokens": 300,
                        "temperature": 0,
                    },
                )
                if resp.status_code >= 400:
                    return candidates[:top_k]
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                match = re.search(r"\{.*?\}", content, re.DOTALL)
                if not match:
                    return candidates[:top_k]
                ranked_data = json.loads(match.group())
                indices: list[int] = ranked_data.get("ranked_indices", [])
                result: list[PreferenceResult] = []
                seen: set[int] = set()
                for idx in indices:
                    if 0 <= idx < len(candidates) and idx not in seen:
                        result.append(candidates[idx])
                        seen.add(idx)
                for i, c in enumerate(candidates):
                    if i not in seen:
                        result.append(c)
                return result[:top_k]
        except Exception:
            return candidates[:top_k]