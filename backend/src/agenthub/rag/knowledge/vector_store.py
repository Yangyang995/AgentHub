"""pgvector 向量存储——知识库文档的 CRUD 和混合检索。

所有查询限定 project_id，保证项目隔离。
混合检索结合向量余弦相似度 + pg_trgm 关键词匹配，RRF 融合结果。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, text

from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.models.orm import KnowledgeDocument


@dataclass
class SearchResult:
    """检索结果——包含文档块信息和相关性分数。"""

    chunk_id: uuid.UUID
    file_name: str
    file_type: str
    file_id: str
    chunk_index: int
    content: str
    metadata: dict[str, Any] | None
    score: float


# RRF 融合常数
RRF_K = 60


class VectorStore:
    """pgvector 知识库存储——支持批量写入、级联删除和混合检索。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_chunks(
        self,
        project_id: uuid.UUID,
        file_id: str,
        file_name: str,
        file_type: str,
        chunks: list[tuple[str, str, dict[str, Any] | None, list[float] | None]],
    ) -> int:
        """批量写入分块——先查询已存在的 content_hash 跳过重复，再批量写入。

        Args:
            project_id: 项目 ID。
            file_id: 文件唯一标识。
            file_name: 原始文件名。
            file_type: 文件类型。
            chunks: (content, content_hash, metadata, embedding) 列表。

        Returns:
            实际写入的块数。
        """
        # 查询已存在的哈希
        chunk_hashes = [c[1] for c in chunks]
        existing_result = await self._session.execute(
            select(KnowledgeDocument.content_hash).where(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.content_hash.in_(chunk_hashes),
            )
        )
        existing_hashes: set[str] = {row[0] for row in existing_result.all()}

        inserted = 0
        for idx, (content, content_hash, chunk_meta, embedding) in enumerate(chunks):
            if content_hash in existing_hashes:
                continue
            doc = KnowledgeDocument(
                id=uuid.uuid4(),
                project_id=project_id,
                file_id=file_id,
                file_name=file_name,
                file_type=file_type,
                chunk_index=idx,
                content=content,
                content_hash=content_hash,
                chunk_metadata=chunk_meta,
                embedding=embedding,
            )
            self._session.add(doc)
            inserted += 1
        await self._session.flush()
        return inserted

    async def delete_by_file_id(self, project_id: uuid.UUID, file_id: str) -> int:
        """删除指定文件的所有分块——级联清除向量数据。

        Returns:
            删除的块数。
        """
        result = await self._session.execute(
            delete(KnowledgeDocument).where(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.file_id == file_id,
            ),
        )
        await self._session.flush()
        return result.rowcount or 0

    async def delete_by_project(self, project_id: uuid.UUID) -> int:
        """清理项目的所有知识库数据。"""
        result = await self._session.execute(
            delete(KnowledgeDocument).where(KnowledgeDocument.project_id == project_id),
        )
        await self._session.flush()
        return result.rowcount or 0

    async def list_files(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        """列出项目已索引的文件（去重，含块数和摄入时间）。"""
        query = (
            select(
                KnowledgeDocument.file_id,
                KnowledgeDocument.file_name,
                KnowledgeDocument.file_type,
                func.count(KnowledgeDocument.id).label("chunk_count"),
                func.min(KnowledgeDocument.created_at).label("ingested_at"),
            )
            .where(KnowledgeDocument.project_id == project_id)
            .group_by(
                KnowledgeDocument.file_id,
                KnowledgeDocument.file_name,
                KnowledgeDocument.file_type,
            )
            .order_by(func.min(KnowledgeDocument.created_at).desc())
        )
        result = await self._session.execute(query)
        return [
            {
                "file_id": row.file_id,
                "file_name": row.file_name,
                "file_type": row.file_type,
                "chunk_count": row.chunk_count,
                "ingested_at": row.ingested_at.isoformat(),
            }
            for row in result.all()
        ]

    async def hybrid_search(
        self,
        project_id: uuid.UUID,
        query_embedding: list[float] | None,
        query_text: str,
        top_k: int = 60,
        similarity_threshold: float = 0.3,
    ) -> list[SearchResult]:
        """混合检索——向量余弦 + pg_trgm 关键词，RRF 融合。

        当 query_embedding 为 None（Embedding 不可用）时仅做关键词搜索。
        始终限定 project_id。
        """
        # vector_score: 1 - cosine_distance，映射到 [0,2] 再标准化
        vector_sql = (
            "1.0 - (embedding <=> :embedding::vector) AS vector_score"
            if query_embedding is not None
            else "0.0 AS vector_score"
        )
        # trgm_score: pg_trgm similarity，范围 [0,1]
        trgm_sql = (
            "COALESCE(similarity(content, :query_text), 0.0) AS trgm_score"
        )

        # 安全——使用参数绑定而非字符串拼接
        sql_text = f"""
        SELECT
            id, file_name, file_type, file_id, chunk_index, content,
            chunk_metadata,
            {vector_sql},
            {trgm_sql}
        FROM knowledge_documents
        WHERE project_id = :project_id
          AND (
              embedding IS NOT NULL
              OR content % :query_text
          )
        ORDER BY (
            COALESCE(1.0 / (60 + RANK() OVER (ORDER BY {trgm_sql} DESC)), 0.01)
            +
            COALESCE(1.0 / (60 + RANK() OVER (ORDER BY {vector_sql} DESC)), 0.01)
        ) DESC
        LIMIT :top_k
        """

        params: dict[str, Any] = {
            "project_id": project_id,
            "query_text": query_text,
            "top_k": top_k,
        }
        if query_embedding is not None:
            params["embedding"] = query_embedding

        result = await self._session.execute(text(sql_text), params)
        rows = result.all()

        search_results: list[SearchResult] = []
        for row in rows:
            vec_score = float(row.vector_score) if row.vector_score is not None else 0.0
            trg_score = float(row.trgm_score) if row.trgm_score is not None else 0.0
            # RRF 融合分数归一化到 [0,1]
            combined = 0.7 * vec_score + 0.3 * trg_score

            search_results.append(SearchResult(
                chunk_id=row.id,
                file_name=row.file_name,
                file_type=row.file_type,
                file_id=row.file_id,
                chunk_index=row.chunk_index,
                content=row.content,
                metadata=row.chunk_metadata,
                score=round(combined, 4),
            ))

        # 按 RRF 分数排序并过滤阈值
        search_results.sort(key=lambda r: r.score, reverse=True)
        search_results = [r for r in search_results if r.score >= similarity_threshold]
        return search_results[:top_k]
    async def fuzzy_search(
        self,
        project_id: uuid.UUID,
        query_text: str,
        top_k: int = 20,
    ) -> list[SearchResult]:
        """轻量级模糊搜索——纯 pg_trgm，毫秒级响应，供知识库面板使用。

        同时匹配内容与文件名，按 trgm 相似度排序。
        不做向量检索、Query 扩写、LLM 重排。
        """
        sql_text = """
        SELECT DISTINCT ON (file_id)
            id, file_name, file_type, file_id, chunk_index, content,
            chunk_metadata,
            COALESCE(similarity(file_name, :query_text), 0.0) AS trgm_score
        FROM knowledge_documents
        WHERE project_id = :project_id
          AND COALESCE(similarity(file_name, :query_text), 0.0) > 0.1
        ORDER BY file_id, trgm_score DESC
        LIMIT :top_k
        """

        result = await self._session.execute(
            text(sql_text),
            {
                "project_id": project_id,
                "query_text": query_text,
                "top_k": top_k,
            },
        )
        rows = result.all()

        return [
            SearchResult(
                chunk_id=row.id,
                file_name=row.file_name,
                file_type=row.file_type,
                file_id=row.file_id,
                chunk_index=row.chunk_index,
                content=row.content,
                metadata=row.chunk_metadata,
                score=round(float(row.trgm_score or 0.0), 4),
            )
            for row in rows
        ]

