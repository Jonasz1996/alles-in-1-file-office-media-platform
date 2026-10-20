import pytest
from httpx import ASGITransport, AsyncClient

from app.certs import CertManager
from app.config import Settings, get_settings
from app.main import app
from app.migrate import upgrade
from app.security import login_limiter, totp_limiter


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def settings(tmp_path):
    s = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8)
    await upgrade(s)
    return s


@pytest.fixture
def certs(settings):
    """CertManager zonder achtergrondtaak; tests zetten zelf certificaten neer."""
    return CertManager(settings)


@pytest.fixture
async def client(settings, certs):
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.certs = certs
    login_limiter.reset()
    totp_limiter.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    del app.state.certs


ADMIN = {"email": "jonas@example.nl", "display_name": "Jonas", "password": "lang-genoeg-123"}


@pytest.fixture
async def admin(client):
    """Ingelogde beheerder: geeft (headers, tokens) terug."""
    r = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert r.status_code == 201, r.text
    tokens = r.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}, tokens
