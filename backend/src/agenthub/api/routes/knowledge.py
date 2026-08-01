"""知识库 REST API——文件上传、列表、检索、删除。

所有操作限定 project_id，保证项目隔离。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.core.config import get_settings
from agenthub.db.session import get_session
from agenthub.rag.knowledge.embedder import EmbeddingClient
from agenthub.rag.knowledge.ingestion import KnowledgeIngestion
from agenthub.rag.knowledge.retriever import KnowledgeRetriever
from agenthub.rag.knowledge.vector_store import VectorStore

router = APIRouter(prefix="/api/v1/projects/{project_id}/knowledge", tags=["knowledge"])


# ── 依赖注入 ────────────────────────────────────────────────────────────────────


async def get_embedder() -> EmbeddingClient:
    """获取 Embedding 客户端——配置缺失时返回不可用实例。"""
    settings = get_settings()
    deps = settings.embedding_dependencies()
    return EmbeddingClient(deps)


EmbedderDependency = Annotated[EmbeddingClient, Depends(get_embedder)]
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


# ── 响应模型 ────────────────────────────────────────────────────────────────────


class IngestResponse(BaseModel):
    """摄入结果响应。"""
    file_name: str
    file_id: str
    chunks_created: int
    chunks_skipped: int
    warnings: list[str] = Field(default_factory=list)


class FileInfo(BaseModel):
    """已索引文件信息。"""
    file_id: str
    file_name: str
    file_type: str
    chunk_count: int
    ingested_at: str


class SearchResultItem(BaseModel):
    """检索结果项。"""
    chunk_id: uuid.UUID
    file_name: str
    file_type: str
    file_id: str
    chunk_index: int
    content: str
    score: float


class SearchResponse(BaseModel):
    """检索响应。"""
    results: list[SearchResultItem]
    expanded_queries: list[str]
    used_expansion: bool
    total: int


class FuzzySearchItem(BaseModel):
    """轻量模糊搜索结果项——文件名匹配优先。"""

    chunk_id: uuid.UUID
    file_name: str
    file_type: str
    file_id: str
    chunk_index: int
    content: str
    score: float


class DeleteResponse(BaseModel):
    """删除响应。"""
    deleted_chunks: int
    message: str


# ── 端点 ────────────────────────────────────────────────────────────────────────


@router.post("/upload", response_model=list[IngestResponse])
async def upload_files(
    project_id: uuid.UUID,
    files: list[UploadFile],
    embedder: EmbedderDependency,
    session: SessionDependency,
) -> list[IngestResponse]:
    """上传文件到知识库——异步清洗、分块、向量化、存储。

    支持 PDF/DOCX/XLSX/CSV/HTML/MD/TXT 及代码文件。
    图片和二进制文件会被跳过并返回警告。
    """
    ingestion = KnowledgeIngestion(session, embedder)
    results: list[IngestResponse] = []
    for f in files:
        file_bytes = await f.read()
        result = await ingestion.ingest_file(project_id, file_bytes, f.filename or "unknown")
        results.append(IngestResponse(
            file_name=result.file_name,
            file_id=result.file_id,
            chunks_created=result.chunks_created,
            chunks_skipped=result.chunks_skipped,
            warnings=result.warnings,
        ))
    return results


@router.get("/files", response_model=list[FileInfo])
async def list_files(
    project_id: uuid.UUID,
    session: SessionDependency,
) -> list[FileInfo]:
    """列出项目已索引文件。"""
    store = VectorStore(session)
    files = await store.list_files(project_id)
    return [FileInfo(**f) for f in files]


@router.delete("/files/{file_id}", response_model=DeleteResponse)
async def delete_file(
    project_id: uuid.UUID,
    file_id: str,
    session: SessionDependency,
) -> DeleteResponse:
    """删除文件及其所有分块和向量数据。"""
    store = VectorStore(session)
    deleted = await store.delete_by_file_id(project_id, file_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="文件未找到或已删除")
    return DeleteResponse(
        deleted_chunks=deleted,
        message=f"已删除 {deleted} 个分块和向量数据",
    )


@router.get("/search/quick", response_model=list[FuzzySearchItem])
async def quick_search(
    project_id: uuid.UUID,
    q: str,
    session: SessionDependency,
    top_k: int = 20,
) -> list[FuzzySearchItem]:
    """快速模糊搜索——纯 pg_trgm，毫秒级，供知识库面板使用。

    同时匹配文档内容和文件名，按相似度排序。
    不做向量检索、Query 扩写或 LLM 重排。
    """
    store = VectorStore(session)
    results = await store.fuzzy_search(project_id, q, top_k=top_k)
    return [
        FuzzySearchItem(
            chunk_id=r.chunk_id,
            file_name=r.file_name,
            file_type=r.file_type,
            file_id=r.file_id,
            chunk_index=r.chunk_index,
            content=r.content[:500],
            score=r.score,
        )
        for r in results
    ]


@router.get("/search", response_model=SearchResponse)
async def search_knowledge(
    project_id: uuid.UUID,
    q: str,
    embedder: EmbedderDependency,
    session: SessionDependency,
    top_k: int = 10,
) -> SearchResponse:
    """检索知识库——Query 扩写 + 混合检索 + RRF 融合 + LLM 重排。"""
    store = VectorStore(session)
    retriever = KnowledgeRetriever(store, embedder)
    result = await retriever.retrieve(project_id, q, top_k=top_k)
    return SearchResponse(
        results=[
            SearchResultItem(
                chunk_id=r.chunk_id,
                file_name=r.file_name,
                file_type=r.file_type,
                file_id=r.file_id,
                chunk_index=r.chunk_index,
                content=r.content[:1000],
                score=r.score,
            )
            for r in result.results
        ],
        expanded_queries=result.expanded_queries,
        used_expansion=result.used_expansion,
        total=len(result.results),
    )