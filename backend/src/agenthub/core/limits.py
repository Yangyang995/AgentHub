"""速率、并发与大小限制——基于 asyncio 的轻量级内存限流器。

不引入 Redis 等外部依赖，适合首版单用户场景。
支持 REST 全局限流、单 IP 限流、WebSocket 并发连接限制和外部调用速率控制。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from collections.abc import Callable

from agenthub.core.exceptions import RateLimitError, ServiceUnavailableError


class TokenBucket:
    """令牌桶速率限制器——支持 burst 和持续 rate。

    桶容量为 burst，每秒补充 rate 个令牌。
    获取令牌不阻塞——无令牌时返回 False。
    """

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()

    def consume(self, tokens: int = 1) -> bool:
        """尝试消费令牌——成功返回 True，失败返回 False 并记录请求 ID。"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


class RateLimiter:
    """内存速率限制器——支持全局、单 IP 和命名空间速率控制。

    每个命名空间拥有独立的令牌桶实例。
    全局限制对所有请求生效，IP 限制按客户端地址区分。
    """

    def __init__(
        self,
        global_rps: float = 100.0,
        global_burst: int = 50,
        ip_rps: float = 20.0,
        ip_burst: int = 10,
        ws_max_per_conversation: int = 10,
        search_rps: float = 10.0,
        llm_rps: float = 5.0,
    ) -> None:
        self._global_bucket = TokenBucket(global_rps, global_burst)
        self._ip_buckets: dict[str, TokenBucket] = {}
        self._ip_rps = ip_rps
        self._ip_burst = ip_burst
        self._named_buckets: dict[str, TokenBucket] = {}
        self._search_rps = search_rps
        self._llm_rps = llm_rps
        self._ws_connections: dict[uuid.UUID, int] = defaultdict(int)
        self._ws_max = ws_max_per_conversation
        self._llm_semaphore = asyncio.Semaphore(5)
        self._lock = asyncio.Lock()

    async def check_global(self) -> None:
        """检查全局速率——超过限制抛出 RateLimitError。"""
        if not self._global_bucket.consume():
            raise RateLimitError("全局限流")

    async def check_ip(self, client_ip: str) -> None:
        """检查单 IP 速率——超过限制抛出 RateLimitError。"""
        bucket = self._ip_buckets.get(client_ip)
        if bucket is None:
            bucket = TokenBucket(self._ip_rps, self._ip_burst)
            self._ip_buckets[client_ip] = bucket
        if not bucket.consume():
            raise RateLimitError("单 IP 请求过于频繁")

    async def check_search(self) -> None:
        """检查搜索（知识库/记忆）速率。"""
        bucket = self._named_buckets.setdefault(
            "search", TokenBucket(self._search_rps, 5)
        )
        if not bucket.consume():
            raise RateLimitError("搜索请求过于频繁")

    async def acquire_llm(self) -> Callable[[], None]:
        """获取 LLM 调用许可——返回释放函数。

        若信号量为 0 则阻塞等待。同时检查 LLM 速率限制。
        """
        release: asyncio.Semaphore | None = self._llm_semaphore
        if release is None:
            raise ServiceUnavailableError("LLM 调用通道已关闭")
        await release.acquire()

        bucket = self._named_buckets.setdefault(
            "llm", TokenBucket(self._llm_rps, 3)
        )
        if not bucket.consume():
            release.release()
            raise RateLimitError("LLM 调用过于频繁，请稍后重试")
        return release.release

    async def track_ws_connect(self, conversation_id: uuid.UUID) -> None:
        """记录 WebSocket 连接——超过并发限制抛出 RateLimitError。"""
        async with self._lock:
            current = self._ws_connections[conversation_id]
            if current >= self._ws_max:
                # 消息保持单行，不超过 100 字符
                raise RateLimitError(
                    f"WS 连接数已达上限 ({self._ws_max})"
                )
            self._ws_connections[conversation_id] = current + 1

    async def track_ws_disconnect(self, conversation_id: uuid.UUID) -> None:
        """释放 WebSocket 连接计数。"""
        async with self._lock:
            current = self._ws_connections[conversation_id]
            if current > 0:
                self._ws_connections[conversation_id] = current - 1
            if self._ws_connections[conversation_id] == 0:
                del self._ws_connections[conversation_id]


# ── 全局单例 —— 模块级创建，由中间件和路由消费 ───────────────────────────

_default_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """获取全局速率限制器——延迟初始化。"""
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = RateLimiter()
    return _default_limiter
