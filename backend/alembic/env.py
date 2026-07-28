import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import agenthub.models.orm  # noqa: F401 -- trigger model registration

# 加载 AgentHub 配置与所有 ORM 模型（必须导入以填充 Base.metadata）
from agenthub.core.config import get_settings
from agenthub.db.session import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_database_url() -> str:
    """从 AgentHub Settings 读取数据库 URL。

    优先使用 Alembic -x url=... 命令行选项，
    否则回退到 AGENTHUB_DATABASE_URL 环境变量/配置。
    """
    cmd_url = context.get_x_argument(as_dictionary=True).get("url")
    if cmd_url:
        return cmd_url
    settings = get_settings()
    deps = settings.runtime_dependencies()
    return deps.database_url.get_secret_value()


def run_migrations_offline() -> None:
    """离线模式：仅输出 SQL，不连接数据库。"""
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """在线模式：创建异步引擎并执行迁移。"""
    url = _get_database_url()
    connectable = create_async_engine(url, echo=False)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
