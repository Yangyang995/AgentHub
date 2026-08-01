"""BGE-M3 向量化客户端——通过 OpenAI 兼容 Embedding API 生成 1024 维向量。

支持批量请求（最多100条/次）、指数退避重试（最多3次）。
API Key 不入日志，错误仅暴露错误码。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from agenthub.core.config import EmbeddingDependencies, get_settings

logger = logging.getLogger(__name__)

# BGE-M3 向量维度
EMBEDDING_DIM = 1024
# 单次批量请求上限
BATCH_SIZE = 100
# 重试配置
MAX_RETRIES = 3
RETRY_DELAY_BASE = 1.0  # 秒
# 超时
REQUEST_TIMEOUT = 60.0


class EmbeddingError(RuntimeError):
    """Embedding 服务不可用或返回错误——调用方据此决定降级策略。"""


class EmbeddingClient:
    """BGE-M3 向量化客户端。

    缺失 Embedding 配置时优雅降级——is_available() 返回 False，
    调用方跳过向量化逻辑即可。
    """

    def __init__(self, deps: EmbeddingDependencies | None = None) -> None:
        if deps is None:
            settings = get_settings()
            deps = settings.embedding_dependencies()
        self._deps = deps
        self._available = deps is not None
        self._http_client: httpx.AsyncClient | None = None

    @property
    def is_available(self) -> bool:
        """Embedding 服务是否可用——配置缺失时为 False。"""
        return self._available

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT))
        return self._http_client

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量向量化文本列表。

        Args:
            texts: 待向量化的文本列表，每批最多 BATCH_SIZE 条。

        Returns:
            与输入顺序一致的 1024 维向量列表。

        Raises:
            EmbeddingError: 服务不可用或全部重试失败。
        """
        if not self._available or self._deps is None:
            raise EmbeddingError("Embedding 服务未配置")

        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i: i + BATCH_SIZE]
            embeddings = await self._embed_batch_with_retry(batch)
            all_embeddings.extend(embeddings)
        return all_embeddings

    async def embed_text(self, text: str) -> list[float]:
        """单条文本向量化。"""
        results = await self.embed_texts([text])
        return results[0]

    async def _embed_batch_with_retry(self, texts: list[str]) -> list[list[float]]:
        """指数退避重试的批量请求。"""
        assert self._deps is not None
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                return await self._embed_batch(texts)
            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    logger.warning(
                        "Embedding 请求失败（第 %d/%d 次），%0.1fs 后重试: %s",
                        attempt + 1, MAX_RETRIES, delay, _safe_error(exc),
                    )
                    await asyncio.sleep(delay)
        raise EmbeddingError(f"Embedding 请求失败，已重试 {MAX_RETRIES} 次") from last_error

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """单次批量请求。"""
        assert self._deps is not None
        client = await self._get_client()
        url = f"{self._deps.embedding_base_url.rstrip('/')}/embeddings"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._deps.embedding_api_key is not None:
            headers["Authorization"] = f"Bearer {self._deps.embedding_api_key.get_secret_value()}"

        payload: dict[str, Any] = {
            "model": self._deps.embedding_model,
            "input": texts,
        }

        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            body = resp.text[:200]  # 截断避免泄漏
            raise EmbeddingError(f"Embedding API 返回 {resp.status_code}: {body}")

        data = resp.json()
        # OpenAI 兼容格式: {"data": [{"embedding": [...]}, ...]}
        embeddings_raw: list[dict[str, Any]] = data.get("data", [])
        if not embeddings_raw:
            raise EmbeddingError("Embedding API 返回空 data")

        # 按 index 排序保证顺序
        embeddings_raw.sort(key=lambda item: item.get("index", 0))
        result: list[list[float]] = [
            list(item["embedding"]) for item in embeddings_raw
        ]
        return result

    async def close(self) -> None:
        """释放 HTTP 客户端连接。"""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None


def _safe_error(exc: Exception) -> str:
    """返回不包含凭据的错误描述。"""
    msg = str(exc)
    # 截断过长的错误消息
    if len(msg) > 200:
        msg = msg[:200] + "..."
    return msg