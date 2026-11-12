"""Van begin tot eind tegen Pebble, de test-CA van Let's Encrypt: domein instellen in
/beheer, Pebble controleert de HTTP-01-challenge echt, de https-listener start met
het certificaat en http wordt doorgestuurd. Draait alleen als Pebble er is:

    eval "$(tests/pebble/start.sh)" && uv run pytest tests/test_acme_pebble.py
"""

import asyncio
import os
import socket
import ssl

import httpx
import pytest

from app import serve, totp
from app.config import get_settings
from app.main import app
from tests.conftest import ADMIN

DIRECTORY = os.environ.get("PEBBLE_DIRECTORY")
CA = os.environ.get("PEBBLE_CA")
DOMAIN = "platform.test"
HTTP_PORT = 5002  # hierop controleert Pebble de challenge
HTTPS_PORT = 5443

pytestmark = pytest.mark.skipif(not DIRECTORY, reason="Pebble draait niet (PEBBLE_DIRECTORY)")


def https_get(root_pem: str, path: str) -> tuple[str, int]:
    """GET over https naar 127.0.0.1 met SNI en certificaatcontrole voor DOMAIN.
    Geeft de eerste regels van het antwoord en het serienummer van het certificaat."""
    context = ssl.create_default_context(cadata=root_pem)
    with socket.create_connection(("127.0.0.1", HTTPS_PORT), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=DOMAIN) as tls:
            serial = int(tls.getpeercert()["serialNumber"], 16)
            tls.sendall(
                f"GET {path} HTTP/1.1\r\nHost: {DOMAIN}\r\nConnection: close\r\n\r\n".encode()
            )
            data = b""
            while chunk := tls.recv(65536):
                data += chunk
    return data.decode(errors="replace"), serial


async def wait_for(predicate, seconds: float = 60, what: str = ""):
    for _ in range(int(seconds * 10)):
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(0.1)
    raise AssertionError(f"Duurde te lang: {what}")


@pytest.mark.anyio
async def test_certificaat_aanvragen_en_https(tmp_path, monkeypatch):
    for key, value in {
        "PLATFORM_DATA_DIR": str(tmp_path),
        "PLATFORM_WEB_DIST": str(tmp_path / "web"),
        "PLATFORM_HOST": "127.0.0.1",
        "PLATFORM_HTTP_PORT": str(HTTP_PORT),
        "PLATFORM_HTTPS_PORT": str(HTTPS_PORT),
        "PLATFORM_ACME_DIRECTORY": DIRECTORY,
        "PLATFORM_ACME_CA_FILE": CA,
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    settings = get_settings()
    stop = asyncio.Event()
    task = asyncio.create_task(serve.run(settings, stop))
    base = f"http://127.0.0.1:{HTTP_PORT}"
    try:
        async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=10) as c:

            async def up():
                try:
                    return (await c.get("/health")).status_code == 200
                except httpx.TransportError:
                    return False

            await wait_for(up, 20, "http start")
            tokens = (await c.post("/api/v1/auth/setup", json=ADMIN)).json()
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            # Eerst 2FA aan, zoals een beheerder dat moet voordat /beheer werkt
            setup = (await c.post("/api/v1/auth/2fa/setup", headers=headers)).json()
            code = totp.code_at(setup["secret"], totp.current_step())
            r = await c.post(
                "/api/v1/auth/2fa/enable",
                json={"code": code, "password": ADMIN["password"]},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            r = await c.put(
                "/api/v1/admin/https",
                json={"domain": DOMAIN, "email": "ci@example.com"},
                headers=headers,
            )
            assert r.status_code == 200, r.text

            async def issued():
                status = (await c.get("/api/v1/admin/https", headers=headers)).json()
                assert status["state"] != "fout", status["error"]
                return status if status["active"] and status["serving"] else None

            status = await wait_for(issued, 90, "certificaat van Pebble")
            assert status["cert_domain"] == DOMAIN and status["listener_error"] is None

            async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=CA)) as m:
                root = (await m.get("https://localhost:15000/roots/0")).text

            async def https_up():
                try:
                    return await asyncio.to_thread(https_get, root, "/health")
                except OSError:
                    return None

            response, serial = await wait_for(https_up, 20, "https-listener")
            assert response.startswith("HTTP/1.1 200")
            assert "strict-transport-security: max-age=31536000" in response.lower()

            r = await c.get("/beheer?x=1", headers={"Host": DOMAIN})
            assert r.status_code == 308
            assert r.headers["location"] == f"https://{DOMAIN}/beheer?x=1"

            # Vernieuwen: het nieuwe certificaat werkt meteen, zonder herstart
            await app.state.certs.obtain()
            response, new_serial = await asyncio.to_thread(https_get, root, "/health")
            assert response.startswith("HTTP/1.1 200")
            assert new_serial != serial
    finally:
        stop.set()
        await asyncio.wait_for(task, 20)
        get_settings.cache_clear()
