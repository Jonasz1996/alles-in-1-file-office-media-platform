"""Intro overslaan (fase 11, stap 7): de intro van een serie zelf herkennen.

Afleveringen van één seizoen beginnen vaak met dezelfde intro, niet altijd op dezelfde plek (er
kan een stukje "eerder in deze serie" vóór staan). De mediaworker maakt van de eerste
`SCAN_S` seconden audio een vingerafdruk met Chromaprint (`ffmpeg -f chromaprint -fp_format
raw`, opdracht fingerprint): een reeks 32-bits punten, één per ~0,124 s. Twee afleveringen
vergelijken gaat zo:

1. **Verschuiving:** punten die in beide precies gelijk zijn, stemmen over de verschuiving
   (index in B min index in A); de meest gekozen verschuivingen worden nagekeken.
2. **Gelijk stuk:** bij die verschuiving telt een punt als gelijk als hooguit `MAX_BITS` van de
   32 bits verschillen. Het langste stuk gelijke punten (gaten tot `GAP_S` mogen) is de intro,
   als het tussen `MIN_S` en `MAX_S` lang is.

Elke aflevering krijgt het langste stuk dat ze met een andere aflevering van het seizoen deelt.
De pure functies hier staan los van de database en de worker, zodat ze te testen zijn.
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass

POINT_S = 0.1238  # de duur van één punt van Chromaprint (standaardinstelling)
SCAN_S = 600  # zoveel seconden vooraan bekijken
MIN_S = 15.0
MAX_S = 150.0
MAX_BITS = 6
GAP_S = 3.5
SHIFTS = 5  # zoveel verschuivingen nakijken
WINDOW = 8  # punten aan elke kant
DENSITY = 0.6
MAX_POINTS = int(SCAN_S / POINT_S) + 64


def decode(raw: bytes) -> list[int]:
    """De ruwe uitvoer van -fp_format raw: uint32, little-endian."""
    n = min(len(raw) // 4, MAX_POINTS)
    return list(struct.unpack(f"<{n}I", raw[: n * 4]))


@dataclass(frozen=True)
class Match:
    a_start: float
    a_end: float
    b_start: float
    b_end: float

    @property
    def length(self) -> float:
        return self.a_end - self.a_start


def _shifts(a: list[int], b: list[int]) -> list[int]:
    where: dict[int, list[int]] = {}
    for j, point in enumerate(b):
        where.setdefault(point, []).append(j)
    votes: Counter[int] = Counter()
    for i, point in enumerate(a):
        for j in where.get(point, ())[:8]:
            votes[j - i] += 1
    return [shift for shift, _ in votes.most_common(SHIFTS)]


def _run(a: list[int], b: list[int], shift: int) -> tuple[int, int] | None:
    """Het langste stuk gelijke punten van A (begin, eind) bij deze verschuiving. Een punt telt
    pas mee als rond hem minstens `DENSITY` van de punten gelijk is: losse toevallige treffers
    (bij stilte of een vlakke toon) rekken een stuk anders op."""
    lo, hi = max(0, -shift), min(len(a), len(b) - shift)
    if hi - lo <= 0:
        return None
    hits = [(a[i] ^ b[i + shift]).bit_count() <= MAX_BITS for i in range(lo, hi)]
    sums = [0]
    for h in hits:
        sums.append(sums[-1] + h)
    gap = int(GAP_S / POINT_S)
    best: tuple[int, int] | None = None
    start = last = None
    for k, h in enumerate(hits):
        if not h:
            continue
        w0, w1 = max(0, k - WINDOW), min(len(hits), k + WINDOW + 1)
        if sums[w1] - sums[w0] < DENSITY * (w1 - w0):
            continue
        if last is not None and k - last <= gap:
            last = k
        else:
            start = last = k
        if best is None or last - start > best[1] - best[0]:
            best = (start, last)
    return None if best is None else (best[0] + lo, best[1] + lo)


def compare(a: list[int], b: list[int]) -> Match | None:
    """Het gedeelde stuk van twee afleveringen (in seconden), of None."""
    found: Match | None = None
    for shift in _shifts(a, b):
        run = _run(a, b, shift)
        if run is None:
            continue
        i, j = run
        m = Match(
            a_start=i * POINT_S,
            a_end=(j + 1) * POINT_S,
            b_start=(i + shift) * POINT_S,
            b_end=(j + 1 + shift) * POINT_S,
        )
        if MIN_S <= m.length <= MAX_S and (found is None or m.length > found.length):
            found = m
    return found


def season(prints: dict[str, list[int]]) -> dict[str, tuple[float, float]]:
    """Per aflevering (sleutel) de intro (begin, eind in s), uit het langste gedeelde stuk met
    een andere aflevering. Een aflevering zonder treffer staat er niet in."""
    keys = sorted(prints)
    best: dict[str, tuple[float, float]] = {}

    def keep(key: str, start: float, end: float) -> None:
        old = best.get(key)
        if old is None or end - start > old[1] - old[0]:
            best[key] = (start, end)

    for n, a in enumerate(keys):
        for b in keys[n + 1 :]:
            m = compare(prints[a], prints[b])
            if m is not None:
                keep(a, m.a_start, m.a_end)
                keep(b, m.b_start, m.b_end)
    return best


# --- de ronde (vanuit de lus van mediabeheer) -------------------------------------------------

ROUND_S = 600.0  # hooguit één seizoen per 10 minuten
SEASON_MAX = 30  # zoveel afleveringen van een seizoen vergelijken
FP_BYTES = MAX_POINTS * 4


@dataclass
class _State:
    last: float | None = None


STATE = _State()


async def _drain(fd: int) -> bytes:
    from app.render import _drain as drain

    return await drain(fd, FP_BYTES + 4096)


async def fingerprint(path, probe) -> list[int] | None:
    """De vingerafdruk van één bestand via de worker; None als het bestand niet te lezen is.
    WorkerBusy, WorkerDown en fouten van de worker zelf gaan door (de ronde stopt dan)."""
    import os

    from app.media import client
    from app.media.analysis import FILE_ERRORS
    from app.media.music import audio_position
    from app.media.wire import Op
    from app.media.worker.args import FingerprintArgs, demuxer_of

    demuxer = demuxer_of(probe.container)
    if demuxer is None or not probe.audio_streams():
        return None
    args = FingerprintArgs(demuxer=demuxer, audio_index=audio_position(probe), seconds=SCAN_S)  # type: ignore[arg-type]
    try:
        job, reads = await client.call(Op.FINGERPRINT, args, source=path, outputs=1)
        async with job:
            try:
                raw = await _drain(reads[0])
            finally:
                os.close(reads[0])
            await job.result()
    except client.WorkerFailed as exc:
        if exc.code not in FILE_ERRORS:
            raise
        return None
    return decode(raw)


async def todo(session) -> list:
    """De afleveringen van het eerste seizoen met een aflevering zonder bekeken intro (en
    minstens twee afleveringen met een klare probe): [(MediaItem, Node, Probe)]."""
    from sqlalchemy import select

    from app.media.types import Probe
    from app.models import MediaIntro, MediaItem, MediaProbe, Node

    rows = list(
        await session.execute(
            select(MediaItem, Node, MediaProbe)
            .join(Node, Node.id == MediaItem.node_id)
            .join(MediaProbe, MediaProbe.blob_sha == Node.blob_id)
            .outerjoin(MediaIntro, MediaIntro.blob_sha == Node.blob_id)
            .where(
                MediaItem.kind == "episode",
                MediaItem.present.is_(True),
                MediaItem.show_id.is_not(None),
                MediaProbe.state == "ready",
                Node.deleted_at.is_(None),
                MediaIntro.blob_sha.is_(None),
            )
            .order_by(MediaItem.added_at.desc())
            .limit(200)
        )
    )
    for item, _, _ in rows:
        season = list(
            await session.execute(
                select(MediaItem, Node, MediaProbe)
                .join(Node, Node.id == MediaItem.node_id)
                .join(MediaProbe, MediaProbe.blob_sha == Node.blob_id)
                .where(
                    MediaItem.show_id == item.show_id,
                    MediaItem.kind == "episode",
                    MediaItem.present.is_(True),
                    MediaItem.season.is_(None)
                    if item.season is None
                    else MediaItem.season == item.season,
                    MediaProbe.state == "ready",
                    Node.deleted_at.is_(None),
                )
                .order_by(MediaItem.episode, MediaItem.node_id)
                .limit(SEASON_MAX)
            )
        )
        if len({n.blob_id for _, n, _ in season}) >= 2:
            return [(i, n, Probe.model_validate(p, from_attributes=True)) for i, n, p in season]
        # Eén aflevering: niets om mee te vergelijken; later opnieuw als er meer zijn
    return []


async def tick(settings, *, force: bool = False) -> int:
    """Eén seizoen: vingerafdrukken, vergelijken, bewaren. Geeft hoeveel intro's er gevonden
    werden. Zonder media (of zonder worker) niets."""
    import datetime as dt
    import time

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import get_engine
    from app.media import client
    from app.models import MediaIntro
    from app.storage import BlobStore

    now = time.monotonic()
    if not settings.media_enabled:
        return 0
    if not force and STATE.last is not None and now - STATE.last < ROUND_S:
        return 0
    STATE.last = now
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        work = await todo(session)
    if not work:
        return 0
    blobs = BlobStore(settings.blob_dir)
    prints: dict[str, list[int]] = {}
    try:
        for _, node, probe in work:
            if node.blob_id in prints:
                continue
            fp = await fingerprint(blobs.path(node.blob_id), probe)
            prints[node.blob_id] = fp or []
    except (client.WorkerBusy, client.WorkerDown, client.WorkerFailed):
        return 0  # de volgende ronde opnieuw
    found = season({k: v for k, v in prints.items() if v})
    stamp = dt.datetime.now(dt.UTC)
    async with maker() as session:
        for sha in prints:
            row = await session.get(MediaIntro, sha) or MediaIntro(blob_sha=sha)
            start, end = found.get(sha, (None, None))
            row.start_ms = int(start * 1000) if start is not None else None
            row.end_ms = int(end * 1000) if end is not None else None
            row.checked_at = stamp
            session.add(row)
        await session.commit()
    return len(found)
