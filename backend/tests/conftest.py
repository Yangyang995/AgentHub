"""后端测试共享夹具。"""

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import agenthub.models.orm  # noqa: F401 -- 触发 ORM 注册
from agenthub.core.config import Settings
from agenthub.main import create_app

# ── 测试数据库配置 ─────────────────────────────────────────────────────────

TEST_DATABASE_URL = "postgresql+asyncpg://agenthub:123456@localhost:5432/agenthub_test"


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """创建测试专用的异步引擎，测试结束后释放连接池。"""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """为每个测试创建一个独立的事务性数据库会话。

    每个测试运行在自己的事务中，测试结束后回滚，
    保证测试之间完全隔离，无需清理数据。
    """
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            yield session
            await session.rollback()


# ── HTTP 客户端 ────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """使用显式测试配置，避免测试结果受开发机环境文件影响。"""

    # pydantic-settings 在运行时支持 _env_file；当前类型声明没有暴露这个测试专用参数。
    app = create_app(Settings(environment="test", _env_file=None))  # type: ignore[call-arg]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
