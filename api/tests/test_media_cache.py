"""De cache (Keuze 31, M4): het venster in bytes per stroom, het opruimen boven cache_gb (nooit
binnen 60 s achter de speler), de stille sessies eerst, en de schijfcheck.

Eerst de regels zelf (cache.trim op segmenten van een 4K-remux), dan met de echte stroom en de
nep-worker (de segmenten op schijf, en weer gemaakt als ze nodig zijn), en dan de sweep.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.media import admission, cache, sessions, streams
from app.media.wire import Op
from tests.media import fixtures as F
from tests.test_media_contract import (  # noqa: F401
    keyframes_handler,
    media_app,
    profile,
    source_probe,
    upload,
)
from tests.test_media_decide import probe as recorded
from tests.test_media_producer import Case, case, settle, until  # noqa: F401
from tests.test_media_sessions import content

GiB = cache.GiB
SEG_MS = 6000  # remux: een segment per keyframe na 6 s
SEG_4K = 60 * 1024 * 1024  # 4K-remux van 80 Mbit/s: 60 MB per 6 s


def remux_4k(first: int, last: int) -> list[cache.Piece]:
    return [cache.Piece(n, SEG_4K, n * SEG_MS, (n + 1) * SEG_MS) for n in range(first, last)]


# --- de regels ---------------------------------------------------------------------------------


def test_cache_gb_telt_in_gib_en_het_venster_wordt_gedeeld():
    assert cache.limit_bytes(20) == 20 * 1024**3
    assert cache.window(20, 0) == cache.window(20, 1) == 20 * GiB
    assert cache.window(20, 3) == 20 * GiB // 3


def test_het_venster_van_een_4k_remux():
    """Drie stromen op 20 GiB: elk ~6,7 GiB, ruim 110 segmenten van 6 s (11 minuten). Eerst
    het oudste achter de speler weg, nooit de laatste 60 s achter hem en nooit wat tot 120 s
    vóór hem ligt; is dat niet genoeg, dan wat ver vóór hem ligt, het verste eerst."""
    budget = cache.window(20, 3)
    pieces = remux_4k(0, 400)  # 0 tot 40 minuten in de cache
    for minute in (30, 5):
        playhead = minute * 60_000
        drop = cache.trim(pieces, playhead, budget)
        kept = [p for p in pieces if p.n not in drop]
        assert sum(p.size for p in kept) <= budget
        assert sum(p.size for p in kept) > budget - SEG_4K  # niet meer dan nodig
        for p in pieces:
            if p.end_ms > playhead - cache.KEEP_BEHIND_MS and p.begin_ms < playhead + 120_000:
                assert p.n not in drop, (minute, p)
        behind = [p.n for p in pieces if p.end_ms <= playhead - cache.KEEP_BEHIND_MS]
        if len(drop) <= len(behind):
            assert drop == behind[: len(drop)]  # het oudste eerst
        else:
            assert drop[: len(behind)] == behind
            ahead = drop[len(behind) :]
            assert ahead == list(range(399, 399 - len(ahead), -1))  # het verste eerst
    # op 30 minuten is achter genoeg, op 5 minuten niet
    assert len(cache.trim(pieces, 30 * 60_000, budget)) < 290
    assert max(cache.trim(pieces, 5 * 60_000, budget)) == 399


def test_nooit_binnen_60_s_achter_de_speler_ook_als_het_niet_past():
    playhead = 10 * 60_000
    pieces = remux_4k(80, 101)  # 8 tot ruim 10 minuten: alles binnen 120 s
    drop = cache.trim(pieces, playhead, budget=SEG_4K)
    assert all(pieces[i].end_ms <= playhead - cache.KEEP_BEHIND_MS for i in range(len(drop)))
    kept = [p for p in pieces if p.n not in drop]
    assert all(p.end_ms > playhead - cache.KEEP_BEHIND_MS for p in kept)
    assert sum(p.size for p in kept) > SEG_4K  # mag niet verder


def test_past_het_al_dan_niets():
    assert cache.trim(remux_4k(0, 10), 0, budget=10 * SEG_4K) == []


# --- met de echte stroom -----------------------------------------------------------------------

needs_fixtures = pytest.mark.skipif(not F.available(), reason="ffmpeg ontbreekt (fixtures)")


@needs_fixtures
@pytest.mark.anyio
async def test_het_venster_achter_de_speler_en_opnieuw_maken(case: Case, monkeypatch):  # noqa: F811
    """Een venster van ~2 segmenten en 2 s 'net gespeeld': de stroom houdt zich eraan terwijl
    de speler doorloopt, de bestanden zijn echt weg, en wat terug nodig is komt opnieuw."""
    monkeypatch.setattr(cache, "KEEP_BEHIND_MS", 2000)
    monkeypatch.setattr(streams, "_cache_gb", lambda: 450 * 1024 / GiB)  # ~450 KB
    # trim vergeet eerst (geheugen) en haalt dan weg (schijf, in een thread): de test wacht op
    # allebei. Het weghalen hier trager, zodat een test die alleen op het geheugen wacht altijd
    # faalt, en niet 1 op 12 keer (review golf 2, S5)
    unlink = streams._unlink
    monkeypatch.setattr(streams, "_unlink", lambda paths: (time.sleep(0.2), unlink(paths)))
    sid = await case.play()
    st = case.stream(sid)
    for n in range(st.layout.count):
        await case.seg(sid, n)
    last = st.layout.count - 1

    def files() -> list[str]:
        return sorted(p.name for p in st.dir.glob("*.m4s"))

    # net gespeeld (2 s achter de speler) en wat hij nu speelt blijven; de rest past niet
    await until(lambda: set(st.segments) == {last - 1, last})
    await until(lambda: files() == sorted(f"{n}.m4s" for n in st.segments))
    assert st.bytes <= 450 * 1024
    # terug naar het begin: opnieuw gemaakt (een nieuwe run vanaf 0)
    runs = len(case.runs())
    r = await case.seg(sid, 0)
    assert r.headers["x-init"] == "1"
    assert len(case.runs()) == runs + 1


@needs_fixtures
@pytest.mark.anyio
async def test_sweep_sluit_eerst_de_stille_sessies(case: Case, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setattr(cache, "QUIET_S", 0.3)
    monkeypatch.setattr(cache, "KEEP_BEHIND_MS", 0)
    quiet = await case.play(0)
    busy = await case.play(1)
    for sid in (quiet, busy):
        for n in range(4):
            await case.seg(sid, n)
    q, b = case.stream(quiet), case.stream(busy)
    await asyncio.sleep(0.4)
    await case.seg(busy, 4)  # de tweede is niet stil
    # de grens: samen nu boven, zonder de stille eronder
    limit = b.bytes - 1
    monkeypatch.setattr(cache, "limit_bytes", lambda gb: limit)
    with caplog.at_level(logging.INFO, "platform.media"):
        await cache.sweep(case.settings)
    await settle()
    assert sessions.table().by_sid.get(quiet) is None  # dicht zonder grafsteen: 410
    assert streams.get(q.session) is None and not q.dir.exists()
    assert (await case.get(quiet, "map.json")).status_code == 410
    # de actieve: alleen achter de speler weg, niets van wat hij nu speelt
    assert b.bytes <= limit and 4 in b.segments
    assert "stille mediasessie" in caplog.text


@needs_fixtures
@pytest.mark.anyio
async def test_sweep_ruimt_op_ongeacht_de_leeftijd_maar_nooit_net_gespeeld(case: Case, monkeypatch):  # noqa: F811
    """Boven cache_gb gaan ook jonge segmenten achter de speler weg (geen wachttijd), maar nooit
    die binnen KEEP_BEHIND_MS (hier 4 s) achter hem."""
    monkeypatch.setattr(cache, "KEEP_BEHIND_MS", 4000)
    sid = await case.play()
    st = case.stream(sid)
    for n in range(8):
        await case.seg(sid, n)
    monkeypatch.setattr(cache, "limit_bytes", lambda gb: 1)
    await cache.sweep(case.settings)
    left = sorted(st.segments)
    assert all(st.layout.stop(n) > st.playhead - 4000 for n in left), left
    assert 7 in left and 0 not in left
    assert sessions.table().by_sid[sid].closed is None  # de sessie zelf blijft


@needs_fixtures
@pytest.mark.anyio
async def test_de_schijfcheck_ook_bij_remux(case: Case, monkeypatch, caplog):  # noqa: F811
    """Minder dan 10 GB vrij: disk_low (voor M10), en geen nieuwe transcode of remux (de
    worker krijgt er geen opdracht voor); wat loopt, loopt door."""
    case.fake.on(Op.KEYFRAMES, keyframes_handler("h264", recorded("stream_src.mkv").start_ms))
    remux = await upload(
        case.client, case.headers, case.settings, "serie.mkv", content("serie"),
        source_probe("remux", "h264"),
    )  # fmt: skip
    sid = await case.play()
    await case.seg(sid, 0)
    monkeypatch.setattr(admission, "disk_free", lambda path=None: admission.DISK_MIN - 1)
    with caplog.at_level(logging.WARNING, "platform.media"):
        await cache.sweep(case.settings)
    assert cache.disk_low and "minder dan 10 GB" in caplog.text
    for node in (case.nodes[1], remux):
        r = await case.client.post(
            f"/api/v1/media/{node}/play", json={"profile": profile("h264")}, headers=case.headers
        )
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["params"]["reason"] == "disk"
    assert len(case.runs()) == 1
    await case.seg(sid, 1)
    monkeypatch.setattr(admission, "disk_free", lambda path=None: admission.DISK_MIN)
    await cache.sweep(case.settings)
    assert not cache.disk_low
    r = await case.client.post(
        f"/api/v1/media/{remux}/play", json={"profile": profile("h264")}, headers=case.headers
    )
    assert r.status_code == 200 and r.json()["mode"] == "remux", r.text
