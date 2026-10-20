import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.main import app


@pytest.fixture
async def client():
    app.dependency_overrides[get_settings] = lambda: Settings(node_name="n1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_ready(client):
    r = await client.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.anyio
async def test_info(client):
    r = await client.get("/api/v1/info")
    assert r.status_code == 200
    assert r.json()["node"] == "n1"


@pytest.mark.anyio
async def test_openapi(client):
    r = await client.get("/api/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "Platform API"
