"""检索流水线——Query 扩写 + 多路混合检索 + RRF 融合 + LLM 重排序。

针对用户简短/模糊提问自动扩写为多个检索变体，
多路并行检索后 RRF 融合去重，最后 LLM 精排 Top-N。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field, ValidationError

from agenthub.core.config import get_settings
from agenthub.rag.knowledge.embedder import EmbeddingClient
from agenthub.rag.knowledge.vector_store import SearchResult, VectorStore

# ── Query 扩写阈值 ───────────────────────────────────────────────────────────────

_MIN_QUERY_LENGTH = 5  # 不足此字数的视为简短查询
_TECH_KEYWORDS_PATTERN = re.compile(
    r"(代码|函数|类|接口|模块|API|数据库|SQL|测试|部署|Bug|bug|错误|异常|"
    r"Python|TypeScript|React|Vue|Node|Rust|Go|Java|Docker|K8s|"
    r"import|def |class |function |const |let |var |async |await |export )",
    re.IGNORECASE,
)


class RerankResult(BaseModel):
    """LLM 重排序输出——Pydantic 校验。"""

    ranked_indices: list[int] = Field(description="按相关性从高到低排列的候选索引")


@dataclass
class RetrieveResult:
    """检索流程最终输出。"""

    results: list[SearchResult]
    expanded_queries: list[str]
    used_expansion: bool


class KnowledgeRetriever:
    """知识库检索引擎——Query 扩写 + 多路检索 + RRF + LLM 重排。"""

    def __init__(
        self,
        vector_store: VectorStore,
        embedder: EmbeddingClient | None = None,
    ) -> None:
        self._store = vector_store
        self._embedder = embedder

    async def retrieve(
        self,
        project_id: uuid.UUID,
        query: str,
        top_k: int = 10,
    ) -> RetrieveResult:
        """执行完整检索流水线。

        1. 判断是否需要 Query 扩写
        2. 多 Query 并行混合检索
        3. RRF 融合 + 去重
        4. LLM 重排序 → Top-N
        """
        queries, used_expansion = await self._expand_query(query)
        # 并行多路检索
        query_embedding: list[float] | None = None
        if self._embedder is not None and self._embedder.is_available:
            try:
                query_embedding = await self._embedder.embed_text(query)
            except Exception:
                query_embedding = None

        # 顺序检索所有 query 变体（同一 session 不支持并发）
        all_results_list: list[list[SearchResult]] = []
        for q in queries:
            results = await self._store.hybrid_search(
                project_id, query_embedding, q, top_k=60
            )
            all_results_list.append(results)

        # RRF 融合去重
        fused = self._rrf_fuse(all_results_list)

        # LLM 重排序
        if len(fused) > top_k:
            fused = await self._rerank(query, fused, top_k)

        return RetrieveResult(
            results=fused[:top_k],
            expanded_queries=queries,
            used_expansion=used_expansion,
        )

    async def _expand_query(self, query: str) -> tuple[list[str], bool]:
        """判断是否需要扩写——简短或缺乏技术关键词时用 LLM 生成变体。"""
        stripped = query.strip()
        if len(stripped) >= _MIN_QUERY_LENGTH and _TECH_KEYWORDS_PATTERN.search(stripped):
            return [stripped], False

        # 尝试 LLM 扩写
        try:
            expanded = await self._llm_expand(stripped)
            if expanded:
                return [stripped] + expanded, True
        except Exception:
            pass
        return [stripped], False

    async def _llm_expand(self, query: str) -> list[str]:
        """LLM 将简短查询扩写为 2-3 个更具体的检索变体。"""
        settings = get_settings()
        deps = settings.runtime_dependencies()
        prompt = (
            "将以下简短用户提问扩展为 2-3 个更具体、包含技术关键词的检索查询，"
            "用 JSON 数组返回，每个元素是一个查询字符串。\n\n"
            f"用户提问: {query}\n\n"
            '输出格式: ["查询1", "查询2", "查询3"]'
        )
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
                return []
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            # 提取 JSON 数组
            match = re.search(r"\[.*?\]", content, re.DOTALL)
            if not match:
                return []
            parsed: list[str] = json.loads(match.group())
            return [q for q in parsed if isinstance(q, str) and q.strip()][:3]
        return []

    def _rrf_fuse(self, result_lists: list[list[SearchResult]]) -> list[SearchResult]:
        """RRF (Reciprocal Rank Fusion) 融合多路结果并去重。"""
        seen: dict[uuid.UUID, SearchResult] = {}
        scores: dict[uuid.UUID, float] = {}
        for results in result_lists:
            for rank, item in enumerate(results):
                rrf_score = 1.0 / (60 + rank + 1)
                if item.chunk_id not in seen:
                    seen[item.chunk_id] = item
                    scores[item.chunk_id] = rrf_score
                else:
                    scores[item.chunk_id] += rrf_score
        # 构建结果并排序
        merged = []
        for chunk_id, item in seen.items():
            merged.append(SearchResult(
                chunk_id=item.chunk_id,
                file_name=item.file_name,
                file_type=item.file_type,
                file_id=item.file_id,
                chunk_index=item.chunk_index,
                content=item.content,
                metadata=item.metadata,
                score=round(scores[chunk_id], 4),
            ))
        merged.sort(key=lambda r: r.score, reverse=True)
        return merged

    async def _rerank(
        self,
        query: str,
        candidates: list[SearchResult],
        top_k: int,
    ) -> list[SearchResult]:
        """LLM 重排序——对候选列表打分排序。

        失败时回退到 RRF 原始排序。
        """
        settings = get_settings()
        try:
            deps = settings.runtime_dependencies()
            # 构建候选列表（截断内容避免超过 Token 限制）
            candidate_texts: list[str] = []
            for i, item in enumerate(candidates):
                snippet = item.content[:500].replace("\n", " ")
                candidate_texts.append(f"[{i}] {snippet}")
            candidates_block = "\n".join(candidate_texts[:60])

            prompt = (
                f"用户查询: {query}\n\n"
                "以下是从知识库检索到的候选文档片段（按初始相关性排序）：\n"
                f"{candidates_block}\n\n"
                "请对这些候选片段按照与用户查询的相关性重新排序，"
                "只返回一个 JSON 对象，格式为 "
                '{"ranked_indices": [最相关索引, 次相关索引, ...]}。'
                f"返回 Top-{top_k} 个索引即可。"
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
                # 提取 JSON
                match = re.search(r"\{.*?\}", content, re.DOTALL)
                if not match:
                    return candidates[:top_k]
                rerank_result = RerankResult.model_validate(json.loads(match.group()))
                # 按重排索引重新排列
                reranked: list[SearchResult] = []
                seen_idx: set[int] = set()
                for idx in rerank_result.ranked_indices:
                    if 0 <= idx < len(candidates) and idx not in seen_idx:
                        reranked.append(candidates[idx])
                        seen_idx.add(idx)
                # 补充未出现在重排结果中的候选
                for i, item in enumerate(candidates):
                    if i not in seen_idx:
                        reranked.append(item)
                return reranked[:top_k]
        except (Exception, ValidationError):
            # 回退到原始排序
            return candidates[:top_k]