"""后端测试共享夹具。"""

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agenthub.core.config import Settings
from agenthub.main import create_app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """使用显式测试配置，避免测试结果受开发机环境文件影响。"""

    # pydantic-settings 在运行时支持 _env_file；当前类型声明没有暴露这个测试专用参数。
    app = create_app(Settings(environment="test", _env_file=None))  # type: ignore[call-arg]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
