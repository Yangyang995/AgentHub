"""数据库引擎与会话管理。

提供异步 SQLAlchemy 引擎、会话工厂和 FastAPI 依赖注入。
数据库 URL 仅在首次创建引擎时读取——不在模块导入阶段连接外部服务。
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from agenthub.core.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


# 全局引擎与会话工厂——延迟初始化，仅在首次调用 get_engine() 时创建
_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """返回全局异步引擎（惰性初始化）。

    首次调用时从 Settings 读取 AGENTHUB_DATABASE_URL 并创建引擎。
    若配置缺失，抛出 RuntimeConfigurationError（由调用方决定如何处理）。
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        deps = settings.runtime_dependencies()
        database_url = deps.database_url.get_secret_value()

        _engine = create_async_engine(
            database_url,
            echo=settings.environment == "development",
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """返回全局异步会话工厂（惰性初始化）。"""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_factory


async def get_session() -> AsyncGenerator[AsyncSession]:
    """FastAPI 依赖注入：为每个请求创建一个数据库会话。

    用法:
        @router.get("/items")
        async def list_items(session: AsyncSession = Depends(get_session)):
            ...
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
