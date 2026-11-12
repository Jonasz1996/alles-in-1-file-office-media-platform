"""Scrub-sprites en trick.vtt (bouwontwerp fase 5, Keuze 30). M5.

- De stap `sprites` (achtergrond, alleen video in een bibliotheek) laat de worker per 10 s een
  beeld van 160 breed maken, 10×10 per tegel (ops/sprites.py). De tegels komen als JPEG's achter
  elkaar over één pipe. De app knipt ze op de markers van JPEG (split_jpegs: begrensd, en de
  fuzz-test kijkt dat na), controleert de maat (1600 breed, een hoogte van 10 gelijke rijen) en
  schrijft ze naar `media/trick/<sha>/<i>.jpg`. Het zijn altijd beelden van onze eigen encoder:
  de app decodeert ze niet, hij leest alleen de kop.
- trick.vtt: per 10 s een cue met `trick/<i>.jpg#xywh=x,y,160,h`. Die adressen zijn relatief aan
  `/api/v1/stream/{sid}/trick.vtt`, dus de plaatjes komen ook via de sessie (sessions.guard):
  intrekken geldt meteen, ook voor de sprites (Keuze 30, 34).
- In media_probe: trick_state ready en trick `{interval, w, h, cols, rows, count, tiles}`.
- info(probe) geeft dat aan play (TrickInfo), alleen als trick.vtt er ook echt staat.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import uuid
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.media import analysis, client, hooks, runtime
from app.media.types import Probe
from app.media.wire import Code, Op
from app.media.worker.args import SpritesArgs, demuxer_of
from app.models import MediaProbe

INTERVAL = 10  # seconden per beeld
WIDTH = 160
COLS = 10
ROWS = 10
PER_TILE = COLS * ROWS
MAX_CELL_H = 480  # een staande video: 160 breed, hooguit 3× zo hoog
MAX_TILES = 1000  # 100.000 beelden: ruim 277 uur
MAX_TILE_BYTES = 4 * 1024 * 1024
MAX_BYTES = 256 * 1024 * 1024  # alles samen; een film van 2 uur is ± 1,4 MB
RUN_LIMIT = 3600 + 60  # de worker stopt na 3600 s (server.TIMEOUT)
VTT_TYPE = "text/vtt; charset=utf-8"
FILE_ERRORS = frozenset({Code.UNREADABLE, Code.UNKNOWN_FORMAT, Code.TOO_LARGE, Code.FAILED})
FILE_ERRORS |= {Code.TIMEOUT}

_SHA = re.compile(r"[0-9a-f]{64}")
# De markers zonder lengte: TEM en RST0-7 (SOI en EOI apart)
_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
# Start of frame (baseline, progressief, …); niet DHT (C4), JPG (C8) of DAC (CC)
_SOF = frozenset(set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC})


class BadSprites(ValueError):
    """De uitvoer van de worker is geen rij geldige tegels."""


# --- JPEG knippen -------------------------------------------------------------------------------


def _jpeg(data: bytes, start: int) -> tuple[int, tuple[int, int]]:
    """Eén JPEG vanaf start (SOI): (positie na EOI, (breedte, hoogte)). BadSprites als hij niet
    klopt of afgebroken is."""
    end = len(data)
    if data[start : start + 2] != b"\xff\xd8":
        raise BadSprites("geen SOI")
    i = start + 2
    size: tuple[int, int] | None = None
    while True:
        if i >= end or data[i] != 0xFF:
            raise BadSprites("geen marker")
        while i < end and data[i] == 0xFF:  # opvulling
            i += 1
        if i >= end:
            raise BadSprites("afgebroken")
        marker = data[i]
        i += 1
        if marker == 0xD9:  # EOI
            if size is None:
                raise BadSprites("geen SOF")
            return i, size
        if marker == 0xD8:
            raise BadSprites("SOI binnen een JPEG")
        if marker in _STANDALONE:
            continue
        if i + 2 > end:
            raise BadSprites("afgebroken")
        length = (data[i] << 8) | data[i + 1]
        if length < 2 or i + length > end:
            raise BadSprites("een segment buiten de grens")
        if marker in _SOF:
            if length < 7:
                raise BadSprites("SOF te kort")
            height = (data[i + 3] << 8) | data[i + 4]
            width = (data[i + 5] << 8) | data[i + 6]
            size = (width, height)
        i += length
        if marker == 0xDA:  # SOS: de gecodeerde data tot de volgende echte marker
            while True:
                j = data.find(b"\xff", i)
                if j == -1 or j + 1 >= end:
                    raise BadSprites("afgebroken in de beelddata")
                nxt = data[j + 1]
                if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:  # opgevuld, of een herstartmarker
                    i = j + 2
                    continue
                i = j
                break


def split_jpegs(data: bytes, *, max_count: int = MAX_TILES) -> list[tuple[bytes, int, int]]:
    """De JPEG's achter elkaar in data: [(jpeg, breedte, hoogte)]. BadSprites als iets niet
    klopt, ook rommel tussen of na de beelden, of meer dan max_count."""
    out: list[tuple[bytes, int, int]] = []
    pos = 0
    while pos < len(data):
        if len(out) >= max_count:
            raise BadSprites(f"meer dan {max_count} tegels")
        stop, (width, height) = _jpeg(data, pos)
        if stop - pos > MAX_TILE_BYTES:
            raise BadSprites("een tegel is te groot")
        out.append((data[pos:stop], width, height))
        pos = stop
    return out


def tiles_of(data: bytes) -> tuple[list[bytes], int]:
    """De tegels en de hoogte van één beeld. Elke tegel is 1600 breed en even hoog, en die
    hoogte is 10 gelijke rijen."""
    found = split_jpegs(data)
    if not found:
        raise BadSprites("geen tegels")
    heights = {h for _, _, h in found}
    if len(heights) != 1 or any(w != WIDTH * COLS for _, w, _ in found):
        raise BadSprites("tegels van een andere maat")
    height = heights.pop()
    if height % ROWS or not 2 <= height // ROWS <= MAX_CELL_H:
        raise BadSprites("een hoogte die geen 10 rijen is")
    return [jpeg for jpeg, _, _ in found], height // ROWS


def count_for(tiles: int, reported: object, duration_ms: int | None) -> int:
    """Hoeveel beelden er zijn: wat de worker telde (frame= van de lege uitvoer), als dat bij het
    aantal tegels past; anders een schatting uit de duur, binnen de laatste tegel."""
    low, high = PER_TILE * (tiles - 1) + 1, PER_TILE * tiles
    if isinstance(reported, int) and not isinstance(reported, bool) and low <= reported <= high:
        return reported
    estimate = round((duration_ms or 0) / (INTERVAL * 1000))
    return min(high, max(low, estimate))


def make_vtt(count: int, cell_h: int, duration_ms: int | None) -> bytes:
    """trick.vtt: per beeld een cue van 10 s; de laatste loopt tot het einde (ook als dat
    eerder is dan 10 s na zijn begin)."""
    from app.media.subconv import HEADER, stamp

    blocks = [HEADER]
    for k in range(count):
        start = k * INTERVAL * 1000
        end = (k + 1) * INTERVAL * 1000
        if k == count - 1 and duration_ms and duration_ms > start:
            end = duration_ms
        tile, cell = divmod(k, PER_TILE)
        x, y = (cell % COLS) * WIDTH, (cell // COLS) * cell_h
        blocks.append(
            f"\n{stamp(start)} --> {stamp(end)}\ntrick/{tile}.jpg#xywh={x},{y},{WIDTH},{cell_h}\n"
        )
    return "".join(blocks).encode()


# --- de cache -------------------------------------------------------------------------------------


def trick_dir(settings: Settings, sha: str) -> Path:
    if _SHA.fullmatch(sha) is None:
        raise ValueError("geen sha")
    return settings.media_dir / "trick" / sha


def vtt_path(settings: Settings, sha: str) -> Path:
    return trick_dir(settings, sha) / "trick.vtt"


def tile_path(settings: Settings, sha: str, i: int) -> Path:
    return trick_dir(settings, sha) / f"{int(i)}.jpg"


def info(probe: Probe, settings: Settings | None = None) -> dict | None:
    """{interval, w, h, cols, rows, count} als de sprites er zijn, anders None."""
    data = probe.trick
    if probe.trick_state != "ready" or not isinstance(data, dict) or not probe.blob_sha:
        return None
    try:
        out = {key: int(data[key]) for key in ("interval", "w", "h", "cols", "rows", "count")}
    except (KeyError, TypeError, ValueError):
        return None
    config = settings if settings is not None else runtime.settings()
    try:
        if not vtt_path(config, probe.blob_sha).is_file():
            return None
    except ValueError:
        return None
    return out


def _store(settings: Settings, sha: str, tiles: list[bytes], vtt: bytes) -> None:
    """Alles in een nieuwe map, en die dan op zijn plek (rename): wie trick.vtt ziet, ziet ook
    elke tegel."""
    target = trick_dir(settings, sha)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{sha}.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    tmp.mkdir()
    try:
        for i, jpeg in enumerate(tiles):
            (tmp / f"{i}.jpg").write_bytes(jpeg)
        (tmp / "trick.vtt").write_bytes(vtt)
        old = target.parent / f".{sha}.old.{uuid.uuid4().hex[:8]}"
        with contextlib.suppress(FileNotFoundError):
            os.rename(target, old)
        os.rename(tmp, target)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _remove(settings: Settings, sha: str) -> None:
    with contextlib.suppress(ValueError):
        shutil.rmtree(trick_dir(settings, sha), ignore_errors=True)


async def discard(sha: str) -> None:
    """De blob gaat weg (hooks.on_discard): zijn sprites ook."""
    await asyncio.to_thread(_remove, runtime.settings(), sha)


hooks.register_discard(discard)


# --- maken via de worker -------------------------------------------------------------------------


async def make(settings: Settings, sha: str, path: Path, probe: Probe) -> dict:
    """De sprites van deze blob maken en opslaan. Geeft de trick van media_probe. BadSprites
    als de uitvoer niet klopt; WorkerFailed, WorkerBusy en WorkerDown gaan door."""
    from app.media.subtitles import drain

    video = probe.video()
    demuxer = demuxer_of(probe.container)
    if video is None or demuxer is None:
        raise BadSprites("geen beeld of een onbekende container")
    args = SpritesArgs(demuxer=demuxer, video_index=video.index)  # type: ignore[arg-type]
    async with asyncio.timeout(RUN_LIMIT):
        job, (out,) = await client.call(Op.SPRITES, args, source=path, outputs=1)
        async with job:
            data = await drain(out, MAX_BYTES)
            result = await job.result()
    if data is None:
        raise BadSprites(f"meer dan {MAX_BYTES} bytes")
    tiles, cell_h = await asyncio.to_thread(tiles_of, data)
    count = count_for(len(tiles), result.get("count"), probe.duration_ms)
    vtt = make_vtt(count, cell_h, probe.duration_ms)
    await asyncio.to_thread(_store, settings, sha, tiles, vtt)
    return {
        "interval": INTERVAL,
        "w": WIDTH,
        "h": cell_h,
        "cols": COLS,
        "rows": ROWS,
        "count": count,
        "tiles": len(tiles),
    }


async def _set(maker: async_sessionmaker[AsyncSession], sha: str, **values) -> None:
    async with maker() as session:
        await session.execute(update(MediaProbe).where(MediaProbe.blob_sha == sha).values(**values))
        await session.commit()


def _applies(probe: Probe, kind: str | None) -> bool:
    """Alleen video in een bibliotheek, en lang genoeg voor één beeld."""
    return (
        kind in ("movies", "shows", "videos")
        and probe.video() is not None
        and (probe.duration_ms or 0) >= 5000
    )


async def _step(ctx: analysis.StepContext) -> None:
    probe = ctx.probe
    assert probe is not None
    if probe.trick_state in ("ready", "failed") and (
        probe.trick_state == "failed" or info(probe, ctx.settings) is not None
    ):
        return
    await _set(ctx.maker(), ctx.sha, trick_state="pending")
    try:
        found = await make(ctx.settings, ctx.sha, ctx.path, probe)
    except BadSprites:
        await _set(ctx.maker(), ctx.sha, trick_state="failed", trick=None)
        raise
    except client.WorkerFailed as exc:
        if exc.code in FILE_ERRORS:
            await _set(ctx.maker(), ctx.sha, trick_state="failed", trick=None)
        raise
    await _set(ctx.maker(), ctx.sha, trick_state="ready", trick=found)
    probe.trick_state, probe.trick = "ready", found


analysis.step("sprites", 30, _applies)(_step)
