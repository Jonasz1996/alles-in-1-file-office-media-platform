"""Het hele platform als echte server in de test (serve.run in dezelfde event loop), voor wat de
ASGI-client van httpx niet kan laten zien: een download die stilstaat terwijl de client niet
leest, en stoppen terwijl er nog verbindingen openstaan (F1 en F3 van fase 5, §3.5).

    async with live(tmp_path, monkeypatch) as server:
        headers = await server.admin()
        node = await server.upload(headers, "nul.bin", bytes(1024))
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from dataclasses import dataclass, field

import httpx

from app import db, serve
from app.certs import CertManager
from app.config import Settings, get_settings
from app.main import app
from app.security import RateLimiter, SecurityConfig, SecurityPolicy

ADMIN = {"email": "jonas@example.nl", "display_name": "Jonas", "password": "lang-genoeg-123"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Live:
    base: str
    client: httpx.AsyncClient
    settings: Settings
    stop: asyncio.Event
    task: asyncio.Task
    https_base: str | None = None  # alleen met live(..., https=True)
    extra: list[httpx.AsyncClient] = field(default_factory=list)

    async def admin(self) -> dict[str, str]:
        r = await self.client.post("/api/v1/auth/setup", json=ADMIN)
        assert r.status_code == 201, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    async def upload(self, headers: dict[str, str], name: str, data: bytes) -> dict:
        found = await self.client.get("/api/v1/files/root", headers=headers)
        assert found.status_code == 200, found.text
        root = found.json()
        r = await self.client.put(
            f"/api/v1/files/{root['folder']['id']}/upload",
            params={"name": name},
            content=data,
            headers=headers,
            timeout=120,
        )
        assert r.status_code == 201, r.text
        return r.json()

    def new_client(self, https: bool = False) -> httpx.AsyncClient:
        """Een eigen client (een eigen pool): elke stroom op een eigen verbinding. https: via
        de https-poort (het certificaat van de test, niet gecontroleerd)."""
        base = self.https_base if https else self.base
        assert base is not None, "start live(..., https=True) voor https"
        c = httpx.AsyncClient(base_url=base, trust_env=False, timeout=10, verify=not https)
        self.extra.append(c)
        return c


@contextlib.asynccontextmanager
async def live(tmp_path, monkeypatch, *, https: bool = False, **env: str):
    """Start serve.run met een eigen datamap en poorten; stopt bij het verlaten (op het
    stopsignaal, zoals SIGTERM), ook als de test faalde. https: met een certificaat in
    data/tls, zodat ook de https-luisteraar draait (zoals na een herstart)."""
    http_port = free_port()
    https_port = free_port()
    values = {
        "PLATFORM_DATA_DIR": str(tmp_path),
        "PLATFORM_WEB_DIST": str(tmp_path / "web"),
        "PLATFORM_HOST": "127.0.0.1",
        "PLATFORM_HTTP_PORT": str(http_port),
        "PLATFORM_HTTPS_PORT": str(https_port),
        "PLATFORM_JWT_SECRET": "test" * 8,
        "PLATFORM_RATE_LIMITS": "false",
        **env,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    settings = get_settings()
    # De lifespan zet RateLimiter.enabled (een klasse-attribuut) op PLATFORM_RATE_LIMITS; na
    # de test weer zoals het was, anders rekenen de tests hierna buiten de limieten
    limits = RateLimiter.enabled
    if https:
        from tests.test_certs import write_cert

        write_cert(CertManager(settings), "platform.test")
    # Zoals de client van conftest: de meeste tests gaan niet over 2FA
    app.state.security = SecurityPolicy(SecurityConfig(require_2fa=False))
    stop = asyncio.Event()
    task = asyncio.create_task(serve.run(settings, stop))
    base = f"http://127.0.0.1:{http_port}"
    client = httpx.AsyncClient(base_url=base, trust_env=False, timeout=10)
    server = Live(base, client, settings, stop, task)
    if https:
        server.https_base = f"https://127.0.0.1:{https_port}"
    try:
        for check in [client, *([server.new_client(https=True)] if https else [])]:
            for _ in range(400):
                with contextlib.suppress(httpx.TransportError):
                    if (await check.get("/health")).status_code == 200:
                        break
                assert not task.done(), "serve.run stopte"
                await asyncio.sleep(0.05)
            else:
                raise AssertionError(f"{check.base_url} kwam niet op")
        yield server
    finally:
        stop.set()
        for c in [client, *server.extra]:
            with contextlib.suppress(Exception):
                await c.aclose()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, 30)
        get_settings.cache_clear()
        RateLimiter.enabled = limits
        for name in ("certs", "security", "media", "media_policy"):
            with contextlib.suppress(AttributeError, KeyError):
                delattr(app.state, name)
        await db.get_engine(settings).dispose()
        db._engine.cache_clear()
