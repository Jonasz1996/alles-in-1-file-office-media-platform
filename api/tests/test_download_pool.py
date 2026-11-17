"""F1 (fase 5, §3.5): een download houdt geen databaseverbinding vast zolang hij loopt.

Voor de reparatie hield elke lopende download zijn verbinding uit de pool (5 + 10) tot de laatste
byte. 16 gepauzeerde streams gaven na 30 s een 500 op GET /files/root (spike S12, punt 19 van de
besluiten). Nu sluiten files.download, files.content en public.download de sessie vlak voor de
FileResponse. Deze test opent 20 streams over de drie routes, leest van elk één stuk en leest
dan niet verder, zodat elke respons halverwege blijft hangen. Een gewone aanroep blijft snel.
"""

import asyncio
import statistics
import time

import pytest

from tests.live import live

# Groter dan wat de buffers van de socket (zender en ontvanger) samen opnemen: de respons
# blijft dan echt halverwege staan
SIZE = 48 * 1024 * 1024
STREAMS = 20  # boven de 15 verbindingen van de pool
FIRST_BYTES = 5.0  # zonder de reparatie wacht de 16e stroom 30 s op een verbinding


async def paused(client, url: str, headers: dict | None, opened: list, release: asyncio.Event):
    async with client.stream("GET", url, headers=headers or {}) as response:
        assert response.status_code == 200, await response.aread()
        # Eén stuk, en dan niet verder lezen. De iterator blijft bewaard: een losgelaten
        # iterator sluit bij het opruimen de stroom, en dan is de respons gewoon klaar
        chunks = response.aiter_raw(65536)
        await anext(chunks)
        opened.append(url)
        await release.wait()
        await chunks.aclose()


@pytest.mark.anyio
async def test_twintig_gepauzeerde_streams_houden_de_pool_vrij(tmp_path, monkeypatch):
    async with live(tmp_path, monkeypatch) as server:
        headers = await server.admin()
        node = await server.upload(headers, "nul.bin", bytes(SIZE))
        c = server.client
        link = (await c.post(f"/api/v1/files/{node['id']}/download-link", headers=headers)).json()
        made = await c.post(
            f"/api/v1/files/{node['id']}/links", json={"permission": "read"}, headers=headers
        )
        assert made.status_code == 201, made.text
        token = made.json()["link"]["url"].removeprefix("/s/")
        key = (await c.post("/api/v1/public/open", json={"token": token})).json()["key"]

        # De drie routes van F1: de downloadlink, /content met Bearer, en de publieke link
        routes = [
            (link["url"], None),
            (f"/api/v1/files/{node['id']}/content", headers),
            (f"/api/v1/public/download/{node['id']}?key={key}", None),
        ]
        release = asyncio.Event()
        opened: list[str] = []
        tasks = []
        try:
            for i in range(STREAMS):
                url, extra = routes[i % len(routes)]
                task = asyncio.create_task(paused(server.new_client(), url, extra, opened, release))
                tasks.append(task)
                # Elke stroom moet zijn eerste bytes krijgen; zonder de reparatie wacht de
                # 16e op een vrije verbinding
                deadline = time.monotonic() + FIRST_BYTES
                while len(opened) <= i:
                    assert not task.done() or task.exception() is None, task.exception()
                    assert time.monotonic() < deadline, f"stroom {i + 1} kreeg geen bytes"
                    await asyncio.sleep(0.01)

            assert len(opened) == STREAMS
            times = []
            for _ in range(5):
                start = time.monotonic()
                r = await c.get("/api/v1/files/root", headers=headers, timeout=5)
                times.append(time.monotonic() - start)
                assert r.status_code == 200, r.text
            assert statistics.median(times) <= 0.2, times
            assert max(times) <= 1.0, times
            # De streams lopen nog: niemand is klaar
            assert not any(t.done() for t in tasks)
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
