"""Direct play via de sessie (M3, Keuze 21): GET|HEAD /api/v1/stream/{sid}/file met Range,
If-Range en de sha als ETag, en zonder databaseverbinding zolang de respons loopt (regel 8, F1).

Het scenario direct_bereik van media_contract.py draait hier tegen de echte app (de rest van het
contract vraagt remux en transcode: M4). Daarna de randen van Range die een <video> niet vraagt
maar een client wel kan sturen, en twee tests met een echte server (tests/live.py): 20
gepauzeerde streams houden de pool vrij, en een gepauzeerde stream stopt binnen één stuk na het
intrekken van zijn login. In geen enkele logregel staat de sid.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import statistics
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import MediaConfig
from app.db import get_engine
from app.main import app
from app.media import analysis, sessions
from app.media.state import MediaPolicy, MediaState, media_state
from app.models import Node
from app.routers import public_media
from app.routers.stream_file import FILE_CACHE, byte_range
from tests.live import live
from tests.media import media_contract as MC
from tests.media.media_contract import failed, stream_headers
from tests.test_media_decide import probe as recorded
from tests.test_media_sessions import Clock, file, play, profile_json, session_of, video


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(sessions, "clock", c)
    return c


@pytest.fixture(autouse=True)
def media():
    """Zoals in test_media_sessions.py: media aan, de worker gezond, lege tabel en limieten.
    Met de hand terug (niet via monkeypatch): de lifespan van de echte server zet ze ook."""
    sessions.reset()
    public_media.plays.reset()
    before = {name: getattr(app.state, name, None) for name in ("media", "media_policy")}
    state = app.state.media = MediaState(enabled=True, worker="ok")
    app.state.media_policy = MediaPolicy(MediaConfig())
    yield state
    for name, value in before.items():
        setattr(app.state, name, value)
    sessions.reset()
    public_media.plays.reset()


@pytest.fixture
async def own(client, admin, settings):
    headers, tokens = admin
    node = await video(client, headers, settings)
    return headers, tokens, node


# --- het contract ---------------------------------------------------------------------------------


@dataclass
class DirectWorld:
    """Een wereld voor media_contract.py met alleen direct play (M3; de rest is van M4)."""

    client: httpx.AsyncClient
    auth: dict[str, str]
    node_id: uuid.UUID
    source: Path
    name: str = "m3-direct"
    wait_s: float = 20.0

    def case(self, mode: MC.CaseMode) -> MC.Case | None:
        if mode != "direct":
            return None
        return MC.Case(node_id=self.node_id, body={"profile": profile_json()}, base="web")

    def direct_source(self) -> Path:
        return self.source

    async def pace(self, speed: float | None) -> None:
        return None

    async def stop(self, sid: str, reason: str) -> None:
        s = sessions.table().by_sid[sid]
        assert sessions.stop(s.id, reason)  # type: ignore[arg-type]

    async def replace(self, node_id: uuid.UUID) -> None:
        raise NotImplementedError("M4")

    async def new_init_on_restart(self) -> None:
        raise NotImplementedError("M4")

    async def stall(self, on: bool) -> None:
        raise NotImplementedError("M4")

    async def fail_transcode(self) -> None:
        raise NotImplementedError("M4")

    async def busy(self, reason: str, retry_after: float) -> None:
        raise NotImplementedError("M4")

    async def begin_stop(self) -> None:
        raise NotImplementedError("M4")


@pytest.mark.anyio
async def test_het_contract_direct_bereik(client, own, tmp_path):
    headers, _, node = own
    source = tmp_path / "bron.mp4"
    source.write_bytes(node["data"])
    world = DirectWorld(client, headers, uuid.UUID(node["id"]), source)
    await MC.direct_bereik(world)


@pytest.mark.anyio
async def test_de_grafsteen_op_file(client, own, tmp_path):
    """Het deel direct van gestopt_grafsteen: 403 met de reden, ook bij HEAD en DELETE."""
    headers, _, node = own
    out = await play(client, headers, node["id"])
    world = DirectWorld(client, headers, uuid.UUID(node["id"]), tmp_path)
    await world.stop(out["sid"], "stopped")
    for method, rest in (("GET", "file"), ("HEAD", "file"), ("DELETE", "")):
        r = await MC.call(world, method, out["sid"], rest)
        failed(r, 403, "media_access_gone", "stopped")
        stream_headers(r, MC.NO_STORE)


# --- de randen van Range --------------------------------------------------------------------------


def test_byte_range():
    assert byte_range(None, 100) is None
    assert byte_range("", 100) is None
    assert byte_range("bytes=0-9", 100) == (0, 9)
    assert byte_range("bytes=90-", 100) == (90, 99)
    assert byte_range("bytes=-10", 100) == (90, 99)
    assert byte_range("bytes=-1000", 100) == (0, 99)  # meer dan er is: alles
    assert byte_range("bytes=50-5000", 100) == (50, 99)  # voorbij het eind: tot het eind
    assert byte_range("bytes = 0 - 9", 100) == (0, 9)  # spaties mogen
    assert byte_range("bytes=100-", 100) is False  # begint voorbij het eind: 416
    assert byte_range("bytes=-0", 100) is False
    assert byte_range("bytes=0-0", 0) is False
    for bad in ("bytes=0-1,5-6", "bytes=a-b", "items=0-1", "bytes=-", "bytes=9-3", "0-9"):
        assert byte_range(bad, 100) is None, bad


def test_byte_range_fuzz():
    """Regel 10: een lezer van onbetrouwbare bytes (de Range-kop) met een vaste seed."""
    import random

    rng = random.Random(20261029)
    alphabet = "bytes=-,0123456789 aZ*\t;"
    for _ in range(5000):
        size = rng.choice([0, 1, 2, 100, 10**12])
        header = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        found = byte_range(header, size)
        assert found is None or found is False or isinstance(found, tuple), header
        if isinstance(found, tuple):
            start, end = found
            assert 0 <= start <= end < size, (header, size, found)


@pytest.mark.anyio
async def test_de_randen_van_range(client, own):
    headers, _, node = own
    data = node["data"]
    size = len(data)
    sid = (await play(client, headers, node["id"]))["sid"]
    cases = [
        ("bytes=-100", 206, data[-100:], f"bytes {size - 100}-{size - 1}/{size}"),
        ("bytes=1000-", 206, data[1000:], f"bytes 1000-{size - 1}/{size}"),
        ("bytes=10-99999999", 206, data[10:], f"bytes 10-{size - 1}/{size}"),
        ("bytes=0-0", 206, data[:1], f"bytes 0-0/{size}"),
        ("bytes=0-1,5-6", 200, data, None),  # meer bereiken: alles (RFC 9110)
        ("bytes=a-b", 200, data, None),
        ("items=0-1", 200, data, None),
        ("bytes=9-3", 200, data, None),
    ]
    for header, status, body, content_range in cases:
        r = await file(client, sid, Range=header)
        assert r.status_code == status, (header, r.status_code)
        assert r.content == body, header
        assert r.headers.get("content-range") == content_range, header
        assert int(r.headers["content-length"]) == len(body), header
        stream_headers(r, FILE_CACHE)
    for header in (f"bytes={size}-", f"bytes={size + 5}-{size + 9}", "bytes=-0"):
        r = await file(client, sid, Range=header)
        assert r.status_code == 416, header
        assert r.headers["content-range"] == f"bytes */{size}" and r.content == b""
        stream_headers(r, FILE_CACHE)


@pytest.mark.anyio
async def test_if_range_en_if_none_match(client, own):
    headers, _, node = own
    data = node["data"]
    etag = f'"{hashlib.sha256(data).hexdigest()}"'
    sid = (await play(client, headers, node["id"]))["sid"]
    # If-Range: alleen met precies deze (sterke) ETag het bereik; een datum of W/: alles
    r = await file(client, sid, Range="bytes=0-9", **{"If-Range": etag})
    assert r.status_code == 206 and r.content == data[:10]
    for other in (f"W/{etag}", "Wed, 21 Oct 2015 07:28:00 GMT", '"iets-anders"'):
        r = await file(client, sid, Range="bytes=0-9", **{"If-Range": other})
        assert r.status_code == 200 and r.content == data, other
    # If-None-Match: ook zwak en *, ook in een lijst
    for match in (etag, f"W/{etag}", "*", f'"x", {etag}'):
        r = await file(client, sid, **{"If-None-Match": match})
        assert r.status_code == 304 and r.content == b"", match
        assert r.headers["etag"] == etag
        stream_headers(r, FILE_CACHE)
    r = await file(client, sid, **{"If-None-Match": '"iets-anders"'})
    assert r.status_code == 200 and r.content == data


@pytest.mark.anyio
async def test_head(client, own):
    headers, _, node = own
    data = node["data"]
    sid = (await play(client, headers, node["id"]))["sid"]
    r = await file(client, sid, "HEAD")
    assert r.status_code == 200 and r.content == b""
    assert int(r.headers["content-length"]) == len(data)
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["accept-ranges"] == "bytes"
    stream_headers(r, FILE_CACHE)
    r = await file(client, sid, "HEAD", Range="bytes=100-199")
    assert r.status_code == 206 and r.content == b""
    assert r.headers["content-range"] == f"bytes 100-199/{len(data)}"
    assert r.headers["content-length"] == "100"
    # de sessie leeft van een HEAD net zo goed als van een GET
    s = session_of(sid)
    seen = s.seen
    await asyncio.sleep(0.01)
    await file(client, sid, "HEAD")
    assert s.seen > seen


@pytest.mark.anyio
async def test_zoeken_ook_na_10_minuten(client, own, clock):
    """Klaar als: direct play werkt via de sessie, met zoeken, ook na 10 minuten (de toegang
    wordt dan opnieuw nagekeken; de sessie is niet stil zolang er verzoeken komen)."""
    headers, _, node = own
    data = node["data"]
    sid = (await play(client, headers, node["id"]))["sid"]
    for minute in range(1, 41):
        clock.advance(60)
        start = (minute * 7919) % (len(data) - 100)
        r = await file(client, sid, Range=f"bytes={start}-{start + 99}")
        assert r.status_code == 206 and r.content == data[start : start + 100], minute
    # 40 minuten gespeeld; daarna 31 minuten niets: stil, 410
    clock.advance(31 * 60)
    failed(await file(client, sid, Range="bytes=0-9"), 410, "media_session_gone")


@pytest.mark.anyio
async def test_de_sessie_gebruikt_de_blob_van_play(client, own, settings):
    """De gepinde blob: de route leest het pad van de sessie, niet opnieuw de node."""
    headers, _, node = own
    sid = (await play(client, headers, node["id"]))["sid"]
    s = session_of(sid)
    assert s.path is not None and s.path.read_bytes() == node["data"]
    assert s.size == len(node["data"]) and s.sha == node["sha"]


# --- met een echte server ------------------------------------------------------------------------

BIG = 48 * 1024 * 1024  # groter dan de buffers van de socket samen: de respons blijft staan
STREAMS = 20  # boven de 15 verbindingen van de pool
FIRST_BYTES = 5.0


class Keep(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


async def _live_media(server, monkeypatch, headers, name: str) -> dict:
    """Media aan op de echte server (zonder worker: direct play heeft hem niet nodig), en een
    groot bestand met de probe van h264_aac.mp4."""
    state = media_state(app)
    monkeypatch.setattr(state, "enabled", True)
    monkeypatch.setattr(state, "worker", "ok")
    node = await server.upload(headers, name, bytes(BIG))
    maker = async_sessionmaker(get_engine(server.settings), expire_on_commit=False)
    async with maker() as session:
        row = await session.get(Node, uuid.UUID(node["id"]))
        assert row is not None and row.blob_id
        sha = row.blob_id
    await analysis.save_probe(maker, sha, recorded("h264_aac.mp4"))
    return node


async def _play(c: httpx.AsyncClient, headers, node_id: str) -> str:
    r = await c.post(
        f"/api/v1/media/{node_id}/play", json={"profile": profile_json()}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "direct"
    return r.json()["sid"]


async def paused(client, url: str, opened: list, release: asyncio.Event) -> None:
    async with client.stream("GET", url) as response:
        assert response.status_code == 200, await response.aread()
        chunks = response.aiter_raw(65536)
        await anext(chunks)
        opened.append(url)
        await release.wait()
        await chunks.aclose()


@pytest.mark.anyio
async def test_twintig_gepauzeerde_streams_houden_de_pool_vrij(tmp_path, monkeypatch):
    """F1 voor /file: de sessie staat in het geheugen, de route houdt geen verbinding vast.
    10 streams op een sessie van de beheerder, 10 op een sessie van een publieke link."""
    async with live(tmp_path, monkeypatch) as server:
        try:
            headers = await server.admin()
            c = server.client
            node = await _live_media(server, monkeypatch, headers, "groot.mp4")
            own = await _play(c, headers, node["id"])
            made = await c.post(
                f"/api/v1/files/{node['id']}/links", json={"permission": "read"}, headers=headers
            )
            token = made.json()["link"]["url"].removeprefix("/s/")
            key = (await c.post("/api/v1/public/open", json={"token": token})).json()["key"]
            r = await c.post(
                f"/api/v1/public/media/{node['id']}/play",
                json={"profile": profile_json()},
                headers={"Authorization": f"Bearer {key}"},
            )
            assert r.status_code == 200, r.text
            link = r.json()["sid"]
            release = asyncio.Event()
            opened: list[str] = []
            tasks = []
            try:
                for i in range(STREAMS):
                    sid = own if i % 2 == 0 else link
                    url = f"/api/v1/stream/{sid}/file"
                    task = asyncio.create_task(paused(server.new_client(), url, opened, release))
                    tasks.append(task)
                    deadline = time.monotonic() + FIRST_BYTES
                    while len(opened) <= i:
                        assert not task.done() or task.exception() is None, task.exception()
                        assert time.monotonic() < deadline, f"stroom {i + 1} kreeg geen bytes"
                        await asyncio.sleep(0.01)
                times = []
                for _ in range(5):
                    start = time.monotonic()
                    r = await c.get("/api/v1/files/root", headers=headers, timeout=5)
                    times.append(time.monotonic() - start)
                    assert r.status_code == 200, r.text
                assert statistics.median(times) <= 0.2, times
                assert max(times) <= 1.0, times
                # ook een nieuwe play (met de database) gaat gewoon
                await _play(c, headers, node["id"])
                assert not any(t.done() for t in tasks)
            finally:
                release.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            sessions.reset()


@pytest.mark.anyio
async def test_een_gepauzeerde_stream_stopt_na_intrekken(tmp_path, monkeypatch):
    """Een /file-stream die de client pauzeert, dan die ene login intrekken (uitloggen), dan weer
    lezen: de respons breekt af binnen één stuk; de andere login speelt door. Geen sid in de
    toegangslog of in de log van het platform."""
    keep = Keep()
    loggers = [logging.getLogger(n) for n in ("uvicorn.access", "uvicorn.error", "platform")]
    levels = [lg.level for lg in loggers]
    sids: list[str] = []
    try:
        async with live(tmp_path, monkeypatch) as server:
            # na de start: uvicorn zet bij het starten zijn eigen handlers
            for lg in loggers:
                lg.addHandler(keep)
            logging.getLogger("platform").setLevel(logging.DEBUG)
            try:
                headers = await server.admin()
                c = server.client
                node = await _live_media(server, monkeypatch, headers, "film.mp4")
                login = await c.post(
                    "/api/v1/auth/login",
                    json={"email": "jonas@example.nl", "password": "lang-genoeg-123"},
                )
                phone = login.json()
                phone_headers = {"Authorization": f"Bearer {phone['access_token']}"}
                sid = await _play(c, phone_headers, node["id"])
                other = await _play(c, headers, node["id"])
                sids += [sid, other]
                stream = server.new_client()
                got = 0
                async with stream.stream("GET", f"/api/v1/stream/{sid}/file") as response:
                    assert response.status_code == 200
                    chunks = response.aiter_raw(65536)
                    got += len(await anext(chunks))
                    r = await c.post(
                        "/api/v1/auth/logout", json={"refresh_token": phone["refresh_token"]}
                    )
                    assert r.status_code == 204
                    before = got
                    started = time.monotonic()
                    with pytest.raises(httpx.HTTPError):
                        async for chunk in chunks:
                            got += len(chunk)
                    took = time.monotonic() - started
                # wat nog kwam: de buffers van de socket plus hooguit één stuk, niet de rest
                assert got - before < BIG // 2, (got, before)
                assert took < 5.0, took
                failed(await c.head(f"/api/v1/stream/{sid}/file"), 403, "media_access_gone")
                # de andere login speelt door
                r = await c.get(f"/api/v1/stream/{other}/file", headers={"Range": "bytes=0-1023"})
                assert r.status_code == 206 and len(r.content) == 1024
                assert (await c.delete(f"/api/v1/stream/{other}")).status_code == 204
            finally:
                sessions.reset()
    finally:
        for lg, level in zip(loggers, levels, strict=True):
            lg.removeHandler(keep)
            lg.setLevel(level)
    stream_lines = [line for line in keep.lines if "/api/v1/stream/" in line]
    assert len(stream_lines) >= 4, keep.lines
    # het afbreken is het bedoelde einde: één regel met de reden, geen traceback van uvicorn
    assert "mediastroom afgebroken (revoked)" in keep.lines, keep.lines
    assert not any("Exception in ASGI application" in line for line in keep.lines), keep.lines
    for sid in sids:
        secret = sid.split(".", 1)[1]
        for line in keep.lines:
            assert secret not in line, line
