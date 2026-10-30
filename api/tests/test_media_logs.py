"""Geen sid in een logregel (fase 5, Keuze 35). De sid (<node>.<geheim>) staat in het pad onder
/api/v1/stream/, en is daar de toegang zelf. _hide in main.py vervangt dat hele stuk van het pad
door ***; alleen een nette naam van de node ervoor ([a-z0-9-], dan een punt) blijft, om te kunnen
volgen. Ook zonder punt, met een lege naam van de node of met een dubbele / gaat alles weg.

M0 legt het patroon vast, en kijkt het na op de echte toegangslog van uvicorn (via tests/live.py).
Daarna speelt een hele sessie af met de nep-stroom (fake_stream.py) achter een echte uvicorn:
play, de map, de playlists, de init, elk segment, een sprong, direct play met Range, en sluiten.
Geen enkele logregel (de toegangslog en de eigen log van de nep) bevat het geheim van een sid.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx
import pytest

from app.main import STREAM_PATH, HideSecrets, _hide
from tests.live import free_port, live
from tests.media import fixtures as F

SECRET = "Zr7Qd2kLmN0pXa9sB4cT1vWy3eF6gH8j"
SID = f"node-1.{SECRET}"


@pytest.mark.parametrize(
    "path",
    [
        f"/api/v1/stream/{SID}/map.json",
        f"/api/v1/stream/{SID}/master.m3u8",
        f"/api/v1/stream/{SID}/v.m3u8",
        f"/api/v1/stream/{SID}/init.mp4?g=2",
        f"/api/v1/stream/{SID}/17.m4s",
        f"/api/v1/stream/{SID}/subs/e2.vtt",
        f"/api/v1/stream/{SID}/trick/3.jpg",
        f"/api/v1/stream/{SID}/audio.mp4",
        f"/api/v1/stream/{SID}/file",
        f"/api/v1/stream/{SID}",
    ],
)
def test_de_sid_gaat_uit_elk_pad(path):
    hidden = _hide(path)
    assert SECRET not in hidden
    assert hidden.startswith("/api/v1/stream/node-1.***")


def test_ook_in_een_regel_met_aanhalingstekens_en_twee_keer():
    line = f'"GET /api/v1/stream/{SID}/0.m4s HTTP/1.1" ref "/api/v1/stream/{SID}/v.m3u8"'
    hidden = _hide(line)
    assert SECRET not in hidden
    assert hidden.count("/api/v1/stream/node-1.***/") == 2


@pytest.mark.parametrize(
    ("path", "hidden"),
    [
        # een lege naam van de node: de sid begint met een punt
        (f"/api/v1/stream/.{SECRET}/file", "/api/v1/stream/***/file"),
        (f'"GET /api/v1/stream/.{SECRET}/file HTTP/1.1" 200', '"GET /api/v1/stream/***/file '),
        # een dubbele /
        (f"/api/v1/stream//local.{SECRET}/file", "/api/v1/stream//local.***/file"),
        # geen punt (een tikfout, of later een andere vorm)
        (f"/api/v1/stream/nodot-{SECRET}/file", "/api/v1/stream/***/file"),
        (f"/api/v1/stream/{SECRET.lower()}/map.json", "/api/v1/stream/***/map.json"),
        # een naam van de node die niet netjes is: alles weg
        (f"/api/v1/stream/Node_1.{SECRET}/v.m3u8", "/api/v1/stream/***/v.m3u8"),
        # na de naam van de node nog een punt: alles erna weg
        (f"/api/v1/stream/local.{SECRET}.x/0.m4s", "/api/v1/stream/local.***/0.m4s"),
    ],
)
def test_ook_een_sid_van_een_andere_vorm_gaat_weg(path, hidden):
    """Review S5: lege naam van de node, //, geen punt."""
    out = _hide(path)
    assert SECRET not in out and SECRET.lower() not in out
    assert out.startswith(hidden)


def test_de_rest_blijft_zoals_het_was():
    for text in (
        "/api/v1/streams/a.b/c",  # een ander pad
        "/api/v1/files/root",
        "/api/v1/stream/",  # niets om te verbergen
    ):
        assert _hide(text) == text
    # De patronen van fase 3 en 4 werken nog
    assert _hide("/api/v1/download/x?token=GEHEIM") == "/api/v1/download/x?token=***"
    assert _hide("GET /s/abcdef") == "GET /s/***"
    assert STREAM_PATH.pattern == r"(/api/v1/stream/+(?:[a-z0-9-]{1,63}\.)?)[^/?\s\"]+"


def test_het_filter_van_de_toegangslog():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", f"/api/v1/stream/{SID}/3.m4s", "1.1", 503),
        None,
    )
    assert HideSecrets().filter(record)
    assert SECRET not in record.getMessage()
    assert "/api/v1/stream/node-1.***/3.m4s" in record.getMessage()


class Keep(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.mark.anyio
async def test_geen_sid_in_de_echte_toegangslog(tmp_path, monkeypatch):
    keep = Keep()
    access = logging.getLogger("uvicorn.access")
    try:
        async with live(tmp_path, monkeypatch) as server:
            access.addHandler(keep)
            for path in ("map.json", "0.m4s", "file", "audio.mp4", "subs/e0.vtt"):
                r = await server.client.get(f"/api/v1/stream/{SID}/{path}")
                assert r.status_code == 503  # media staat uit
            r = await server.client.delete(f"/api/v1/stream/{SID}")
            assert r.status_code == 503
    finally:
        access.removeHandler(keep)
    stream_lines = [line for line in keep.lines if "/api/v1/stream/" in line]
    assert len(stream_lines) == 6, keep.lines
    for line in keep.lines:
        assert SECRET not in line, line


@pytest.mark.anyio
@pytest.mark.skipif(not F.available(), reason="ffmpeg ontbreekt (fixtures)")
async def test_geen_sid_in_een_hele_sessie_met_de_nep_stroom():
    import uvicorn

    from tests.media.fake_stream import (
        H264_PROFILE,
        NODE_DIRECT,
        NODE_TRANSCODE,
        TOKEN,
        FakeStream,
    )

    fake = FakeStream(speed=40)
    port = free_port()
    config = uvicorn.Config(
        fake.app, host="127.0.0.1", port=port, log_level="info", lifespan="off",
        server_header=False,
    )  # fmt: skip
    server = uvicorn.Server(config)
    keep = Keep()
    access = logging.getLogger("uvicorn.access")
    own = logging.getLogger("tests.media.fake_stream")
    level = own.level
    own.setLevel(logging.INFO)
    access.addHandler(keep)
    own.addHandler(keep)
    task = asyncio.create_task(server.serve())
    sids: list[str] = []
    try:
        for _ in range(200):
            if server.started:
                break
            assert not task.done(), "uvicorn stopte"
            await asyncio.sleep(0.02)
        auth = {"Authorization": f"Bearer {TOKEN}"}
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30
        ) as c:
            r = await c.post(
                f"/api/v1/media/{NODE_TRANSCODE}/play", json={"profile": H264_PROFILE},
                headers=auth,
            )  # fmt: skip
            assert r.status_code == 200, r.text
            sid = r.json()["sid"]
            sids.append(sid)
            base = f"/api/v1/stream/{sid}"
            m = (await c.get(f"{base}/map.json")).json()
            for rest in ("master.m3u8", "v.m3u8", m["init"], "0.m4s", "8.m4s", "1.m4s"):
                assert (await c.get(f"{base}/{rest}")).status_code == 200, rest
            assert (await c.delete(base)).status_code == 204
            assert (await c.get(f"{base}/2.m4s")).status_code == 410
            r = await c.post(
                f"/api/v1/media/{NODE_DIRECT}/play", json={"profile": H264_PROFILE},
                headers=auth,
            )  # fmt: skip
            sid = r.json()["sid"]
            sids.append(sid)
            ranged = await c.get(f"/api/v1/stream/{sid}/file", headers={"Range": "bytes=0-99"})
            assert ranged.status_code == 206
            assert (await c.head(f"/api/v1/stream/{sid}/file")).status_code == 200
            fake.stop(sid)
            assert (await c.get(f"/api/v1/stream/{sid}/file")).status_code == 403
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, 10)
        access.removeHandler(keep)
        own.removeHandler(keep)
        own.setLevel(level)
        await fake.aclose()
    assert len(sids) == 2
    secrets = [sid.split(".", 1)[1] for sid in sids]
    stream_lines = [line for line in keep.lines if "/api/v1/stream/" in line]
    assert len(stream_lines) >= 12, keep.lines
    assert all("/api/v1/stream/local.***" in line for line in stream_lines), stream_lines
    assert any("sessie " in line for line in keep.lines), "de nep logde niets"
    for line in keep.lines:
        for secret in secrets:
            assert secret not in line, line
