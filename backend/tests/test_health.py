"""健康检查契约测试。"""

from httpx import AsyncClient


async def test_live_health_contract(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive", "service": "AgentHub API"}


async def test_ready_health_contract(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "AgentHub API",
        "checks": {"configuration": "ok"},
    }
