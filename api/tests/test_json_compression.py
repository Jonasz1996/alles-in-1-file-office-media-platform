"""JSON-compressie (P1): alleen grote lijstantwoorden zonder geheimen, alleen als de client gzip
wil, en boven 64 KiB in een thread zodat de event loop blijft antwoorden."""

import gzip
import json
import time

import anyio
import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.main import JSON_MIN_BYTES, JsonCompression
from tests.factories import create_folder, maker, user_by_email


async def _many_folders(settings, email: str, n: int) -> None:
    async with maker(settings)() as session:
        user = await user_by_email(session, email)
        for i in range(n):
            await create_folder(session, user, name=f"map met een wat langere naam {i:04d}")
        await session.commit()


@pytest.mark.anyio
async def test_grote_lijst_wordt_gecomprimeerd(client, settings, admin):
    headers, tokens = admin
    await _many_folders(settings, tokens["user"]["email"], 120)
    plain = await client.get(
        "/api/v1/files/root", headers={**headers, "Accept-Encoding": "identity"}
    )
    assert plain.status_code == 200
    assert "content-encoding" not in plain.headers
    assert len(plain.content) >= JSON_MIN_BYTES

    # Zelf decomprimeren: httpx zou het anders stil uitpakken
    async with client.stream(
        "GET", "/api/v1/files/root", headers={**headers, "Accept-Encoding": "gzip"}
    ) as r:
        raw = b"".join([chunk async for chunk in r.aiter_raw()])
        assert r.headers["content-encoding"] == "gzip"
        assert "Accept-Encoding" in r.headers["vary"]
        assert int(r.headers["content-length"]) == len(raw)
        assert r.headers["content-type"].startswith("application/json")
    assert len(raw) < len(plain.content) / 3
    assert gzip.decompress(raw) == plain.content

    # Ook de children van een map
    root_id = tokens["user"]["root_id"]
    r = await client.get(
        f"/api/v1/files/{root_id}/children", headers={**headers, "Accept-Encoding": "gzip"}
    )
    assert r.headers["content-encoding"] == "gzip"
    assert r.json() == plain.json()


@pytest.mark.anyio
async def test_niet_op_andere_paden_of_klein(client, settings, admin):
    headers, tokens = admin
    gz = {**headers, "Accept-Encoding": "gzip"}
    # Klein: geen compressie
    r = await client.get("/api/v1/files/root", headers=gz)
    assert len(r.content) < JSON_MIN_BYTES
    assert "content-encoding" not in r.headers
    await _many_folders(settings, tokens["user"]["email"], 120)
    # /auth/* nooit, ook niet als het antwoord groot genoeg zou zijn
    r = await client.get("/api/v1/auth/me", headers=gz)
    assert "content-encoding" not in r.headers
    r = await client.post("/api/v1/auth/refresh", headers=gz)
    assert "content-encoding" not in r.headers
    # previews en de live stroom ook niet
    root_id = tokens["user"]["root_id"]
    r = await client.post("/api/v1/previews", headers=gz, json={"ids": [root_id]})
    assert "content-encoding" not in r.headers
    # zonder gzip in Accept-Encoding niet
    r = await client.get("/api/v1/files/root", headers={**headers, "Accept-Encoding": "br"})
    assert "content-encoding" not in r.headers
    r = await client.get(
        "/api/v1/files/root", headers={**headers, "Accept-Encoding": "gzip;q=0, br"}
    )
    assert "content-encoding" not in r.headers


def test_alleen_de_lijstpaden():
    ok = [
        "/api/v1/files/root",
        "/api/v1/files/abc/children",
        "/api/v1/search",
        "/api/v1/trash",
        "/api/v1/trash/overview",
        "/api/v1/shared-with-me",
        "/api/v1/files/lookup",
    ]
    nee = [
        "/api/v1/auth/me",
        "/api/v1/auth/login",
        "/api/v1/previews",
        "/api/v1/events",
        "/api/v1/files/abc",
        "/api/v1/files/abc/children/x",
        "/api/v1/photos",
        "/api/v1/public/links/x",
        "/api/v1/searchx",
    ]
    for path in ok:
        assert main.JSON_COMPRESSIBLE.match(path), path
    for path in nee:
        assert not main.JSON_COMPRESSIBLE.match(path), path


@pytest.mark.anyio
async def test_event_loop_blijft_antwoorden_tijdens_compressie():
    """Een lijst van 4 MB comprimeren duurt tientallen ms; /health moet ondertussen binnen
    50 ms antwoorden, want boven 64 KiB comprimeert een thread."""
    items = [
        {"id": f"{i:08d}", "name": f"bestand {i} met wat tekst erbij", "size": i * 31}
        for i in range(60_000)
    ]
    big = json.dumps({"children": items}).encode()
    assert len(big) > 4_000_000
    started = anyio.Event()

    async def inner(scope, receive, send):
        if scope["path"] == "/health":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"status":"ok"}'})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        started.set()
        await send({"type": "http.response.body", "body": big})

    app = JsonCompression(inner)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        result: dict = {}

        async def listing():
            t0 = time.perf_counter()
            r = await c.get("/api/v1/files/root", headers={"Accept-Encoding": "gzip"})
            result["listing_ms"] = (time.perf_counter() - t0) * 1000
            result["encoding"] = r.headers.get("content-encoding")
            result["body"] = r.content
            result["listing_done"] = time.perf_counter()

        async def health():
            await started.wait()
            await anyio.sleep(0.005)  # de compressie loopt nu
            t0 = time.perf_counter()
            r = await c.get("/health")
            result["health_ms"] = (time.perf_counter() - t0) * 1000
            result["health_done"] = time.perf_counter()
            assert r.status_code == 200

        async with anyio.create_task_group() as tg:
            tg.start_soon(listing)
            tg.start_soon(health)
    assert result["encoding"] == "gzip"
    assert result["body"] == big  # httpx pakt gzip zelf uit
    assert result["health_ms"] < 50, result
    # /health was klaar terwijl de lijst nog bezig was
    assert result["health_done"] < result["listing_done"]
