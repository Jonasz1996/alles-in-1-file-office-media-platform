"""Geen sid in een logregel (fase 5, Keuze 35). De sid (<node>.<geheim>) staat in het pad onder
/api/v1/stream/, en is daar de toegang zelf. _hide in main.py vervangt het deel na de eerste
punt door ***; het deel ervoor (de naam van de node) blijft, om te kunnen volgen.

M0 legt het patroon vast, en kijkt het na op de echte toegangslog van uvicorn (via tests/live.py).
Het afspelen van een hele sessie met de nep-stream (fake_stream.py) komt erbij met die nep-stream.
"""

from __future__ import annotations

import logging

import pytest

from app.main import STREAM_PATH, HideSecrets, _hide
from tests.live import live

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


def test_de_rest_blijft_zoals_het_was():
    for text in (
        "/api/v1/streams/a.b/c",  # een ander pad
        "/api/v1/stream/zonder-punt/map.json",  # geen sid van de vorm <node>.<geheim>
        "/api/v1/files/root",
    ):
        assert _hide(text) == text
    # De patronen van fase 3 en 4 werken nog
    assert _hide("/api/v1/download/x?token=GEHEIM") == "/api/v1/download/x?token=***"
    assert _hide("GET /s/abcdef") == "GET /s/***"
    assert STREAM_PATH.pattern == r"(/api/v1/stream/[^/.\s\"]+\.)[^/?\s\"]+"


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
