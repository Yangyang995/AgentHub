"""文档摄入编排器——清洗 → 分块 → 向量化 → 存储，全程异步。"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.rag.knowledge.chunker import chunk_content
from agenthub.rag.knowledge.cleaner import clean_content
from agenthub.rag.knowledge.embedder import EmbeddingClient
from agenthub.rag.knowledge.vector_store import VectorStore


class IngestionResult:
    """摄入结果。"""

    __slots__ = ("chunks_created", "chunks_skipped", "file_id", "file_name", "warnings")

    def __init__(
        self,
        file_name: str,
        file_id: str,
        chunks_created: int = 0,
        chunks_skipped: int = 0,
        warnings: list[str] | None = None,
    ) -> None:
        self.file_name = file_name
        self.file_id = file_id
        self.chunks_created = chunks_created
        self.chunks_skipped = chunks_skipped
        self.warnings = warnings or []


class KnowledgeIngestion:
    """知识库摄入编排——全链路异步处理。"""

    def __init__(
        self,
        session: AsyncSession,
        embedder: EmbeddingClient,
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._store = VectorStore(session)

    async def ingest_file(
        self,
        project_id: uuid.UUID,
        file_bytes: bytes,
        file_name: str,
    ) -> IngestionResult:
        """摄入单个文件。

        流程：推断类型 → 清洗 → 分块 → 批量向量化 → 存储。
        """
        file_type = _infer_file_type(file_name)
        file_id = hashlib.sha256(f"{project_id}:{file_name}".encode()).hexdigest()[:64]

        result = IngestionResult(file_name=file_name, file_id=file_id)

        # 1. 清洗
        clean_result = clean_content(file_bytes, file_type)
        if clean_result.warnings:
            result.warnings.extend(clean_result.warnings)
        if not clean_result.text.strip():
            result.warnings.append("清洗后无有效文本内容")
            return result

        # 2. 分块
        base_metadata: dict[str, Any] = {
            "file_type": file_type,
            "language": _language_from_type(file_type),
        }
        chunks = chunk_content(clean_result.text, file_type, base_metadata)
        if not chunks:
            result.warnings.append("分块结果为空")
            return result

        # 3. 批量向量化——失败时降级为无向量存储（关键词搜索仍可用）
        chunk_texts = [c.content for c in chunks]
        embeddings: list[list[float]] | None = None
        if self._embedder.is_available:
            try:
                embeddings = await self._embedder.embed_texts(chunk_texts)
            except Exception as exc:
                result.warnings.append(f"向量化失败（关键词搜索仍可用）: {exc}")
                embeddings = None
        else:
            result.warnings.append("Embedding 服务未配置——仅支持关键词搜索，向量检索不可用")

        # 4. 写入存储（去重）
        chunk_records: list[tuple[str, str, dict[str, Any] | None, list[float] | None]] = []
        for i, chunk in enumerate(chunks):
            content_hash = hashlib.sha256(chunk.content.encode()).hexdigest()
            chunk_records.append((
                chunk.content,
                content_hash,
                chunk.metadata if chunk.metadata else None,
                embeddings[i] if embeddings is not None and i < len(embeddings) else None,
            ))

        inserted = await self._store.upsert_chunks(
            project_id=project_id,
            file_id=file_id,
            file_name=file_name,
            file_type=file_type,
            chunks=chunk_records,
        )
        result.chunks_created = inserted
        result.chunks_skipped = len(chunks) - inserted
        return result

    async def ingest_files(
        self,
        project_id: uuid.UUID,
        files: list[tuple[bytes, str]],
    ) -> list[IngestionResult]:
        """批量摄入多个文件。"""
        results: list[IngestionResult] = []
        for file_bytes, file_name in files:
            result = await self.ingest_file(project_id, file_bytes, file_name)
            results.append(result)
        return results


def _infer_file_type(file_name: str) -> str:
    """从文件名推断文件类型。"""
    suffix = Path(file_name).suffix.lower().lstrip(".")
    if suffix in {"pdf", "docx", "xlsx", "xls", "csv", "html", "htm", "md", "txt", "log", "json", "yaml", "yml", "toml"}:
        return suffix
    if suffix in {"py", "ts", "tsx", "js", "jsx", "rs", "go", "java", "kt", "swift",
                  "c", "h", "cpp", "hpp", "cs", "rb", "php", "scala", "clj",
                  "sql", "graphql", "proto", "vue", "svelte", "astro",
                  "css", "scss", "less"}:
        return suffix
    if suffix == "xml":
        return "xml"
    if suffix in {"sh", "bash", "zsh", "ps1"}:
        return suffix
    # Dockerfile 类（无扩展名）
    base_name = file_name.lower()
    if base_name in {"dockerfile", "makefile", "jenkinsfile"}:
        return base_name
    return suffix or "txt"


def _language_from_type(file_type: str) -> str | None:
    """文件类型 → 编程语言映射。"""
    _map: dict[str, str] = {
        "py": "python", "ts": "typescript", "tsx": "typescript",
        "js": "javascript", "jsx": "javascript", "rs": "rust",
        "go": "go", "java": "java", "kt": "kotlin", "swift": "swift",
        "c": "c", "cpp": "c++", "h": "c", "hpp": "c++",
        "cs": "c#", "rb": "ruby", "php": "php", "scala": "scala",
        "sql": "sql", "css": "css", "scss": "scss",
    }
    return _map.get(file_type.lower())