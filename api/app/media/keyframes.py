"""De keyframe-index aan de kant van de app (Keuze 17). M4; de lezers zelf staan in de worker
(worker/containers.py en worker/ops/keyframes.py), want de app ontleedt geen bytes van een
gebruiker (regel 5).

- `read_now(sha)`: bij `play`, meteen na de probe en binnen dezelfde 5 s, met alleen de eigen
  lezers (Matroska-Cues, MP4 `stss`; hooguit 4 MB lezen). Eén taak per sha: een `play` die zijn
  budget op heeft, laat de taak doorlopen, en de volgende `play` vindt de index in de database;
- de stap `keyframes` van de analyse (rij media): eerst de eigen lezers, en als die niets geven
  (`none`) of gaten van meer dan 20 s zien (`sparse`), de terugval: ffprobe over alle pakketten
  van het videospoor (leest het hele bestand, nice 15 en ionice -c 3). Geeft ook die niets, of
  faalt het bestand, dan `failed`;
- `decode(blob)` en `index(blob)`: de tijden (en de groottes) uit `media_probe.keyframes`.

De index staat in de tijd van de container (pts in ms), zoals de worker hem leest. De stroom
rekent in mediatijd: tijd − `probe.start_ms` (streams.py).

Geen databaseverbinding tijdens het wachten op de worker (regel 8): eerst de probe lezen (een
korte sessie), dan de worker, dan in een nieuwe korte sessie opslaan.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import get_engine
from app.media import analysis, client, runtime
from app.media.types import Probe
from app.media.wire import Code, Op
from app.media.worker import containers
from app.media.worker.args import KeyframesArgs, demuxer_of
from app.models import MediaProbe
from app.storage import BlobStore

log = logging.getLogger("platform.media")

State = Literal["none", "ready", "sparse", "failed"]
# Fouten van het bestand zelf of van de terugval (te veel pakketten, de tijdgrens): dan failed
# en niet steeds opnieuw. Een andere fout (not_built, internal, …) gaat door: de job probeert
# het later opnieuw.
FAILED_CODES = frozenset(
    {
        Code.UNREADABLE.value,
        Code.UNKNOWN_FORMAT.value,
        Code.TOO_LARGE.value,
        Code.FAILED.value,
        Code.TIMEOUT.value,
    }
)

# Eén lezing per sha tegelijk, gedeeld door wie er tegelijk om vraagt (play)
_reading: dict[str, asyncio.Task] = {}


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def decode(blob: bytes | None) -> list[int]:
    """De tijden in ms (tijd van de container) uit media_probe.keyframes; [] zonder blob of bij
    een blob die niet klopt."""
    return index(blob)[0]


def index(blob: bytes | None) -> tuple[list[int], list[int]]:
    """(tijden in ms, groottes in bytes) uit media_probe.keyframes. De grootte per keyframe is
    die van zijn GOP, op KiB naar boven (bij Matroska de cluster, met het geluid erin)."""
    if not blob:
        return [], []
    try:
        return containers.decode(blob)
    except ValueError:
        log.warning("een keyframe-index in media_probe klopt niet")
        return [], []


def wanted(probe: Probe | None) -> tuple[str, int] | None:
    """(demuxer, index van het videospoor) als deze probe een index kan krijgen."""
    if probe is None or probe.state != "ready":
        return None
    video = probe.video()
    demuxer = demuxer_of(probe.container)
    if video is None or demuxer is None:
        return None
    return demuxer, video.index


async def _ask(
    settings: Settings, sha: str, demuxer: str, video_index: int, fallback: bool
) -> dict:
    """De opdracht keyframes; het antwoord van de worker (state, keyframes, max_gop_ms, …)."""
    path = BlobStore(settings.blob_dir).path(sha)
    args = KeyframesArgs(
        demuxer=demuxer,  # type: ignore[arg-type]
        video_index=video_index,
        fallback=fallback,
    )
    job, _ = await client.call(Op.KEYFRAMES, args, source=path)
    async with job:
        return await job.result()


def parse(data: dict) -> tuple[State, bytes | None, int | None]:
    """Het antwoord van de worker als (staat, blob, max_gop_ms). Een antwoord dat niet klopt,
    is none (de index is een verbetering, nooit een reden om te falen)."""
    state = data.get("state")
    if state not in ("ready", "sparse"):
        return "none", None, None
    try:
        blob = base64.b64decode(str(data.get("keyframes") or ""), validate=True)
        times, _ = containers.decode(blob)
    except (ValueError, binascii.Error):
        log.warning("de keyframe-index van de worker klopt niet")
        return "none", None, None
    if not times:
        return "none", None, None
    gop = data.get("max_gop_ms")
    max_gop = gop if isinstance(gop, int) and not isinstance(gop, bool) and gop >= 0 else None
    return state, blob, max_gop  # type: ignore[return-value]


async def _save(
    settings: Settings, sha: str, state: State, blob: bytes | None, max_gop: int | None
) -> None:
    async with _maker(settings)() as session:
        await session.execute(
            update(MediaProbe)
            .where(MediaProbe.blob_sha == sha)
            .values(keyframes=blob, keyframes_state=state, max_gop_ms=max_gop)
        )
        await session.commit()


async def build(settings: Settings, sha: str, probe: Probe, *, fallback: bool) -> State:
    """De index nu maken (eigen lezers, of met fallback de terugval) en opslaan. none slaat
    niets op (er was niets). WorkerBusy, WorkerDown en WorkerFailed gaan door."""
    found = wanted(probe)
    if found is None:
        return "none"
    data = await _ask(settings, sha, found[0], found[1], fallback)
    state, blob, max_gop = parse(data)
    if state != "none":
        await _save(settings, sha, state, blob, max_gop)
    return state


async def _read(settings: Settings, sha: str) -> State:
    probe = await analysis.load(_maker(settings), sha)
    if wanted(probe) is None:
        return "none"
    assert probe is not None
    if probe.keyframes_state != "none":  # sparse: de terugval staat al in de rij (play)
        return probe.keyframes_state  # type: ignore[return-value]
    return await build(settings, sha, probe, fallback=False)


def _forget(sha: str, task: asyncio.Task) -> None:
    if _reading.get(sha) is task:
        del _reading[sha]
    if not task.cancelled():
        task.exception()  # gezien: geen "exception was never retrieved"


async def read_now(sha: str) -> State:
    """De index nu maken met de eigen lezers (Keuze 17), voor play. Eén taak per sha; wie
    wacht en zijn budget op heeft (play: asyncio.wait_for), laat de taak doorlopen."""
    task = _reading.get(sha)
    if task is None or task.done():
        task = asyncio.ensure_future(_read(runtime.settings(), sha))
        _reading[sha] = task
        task.add_done_callback(lambda t, sha=sha: _forget(sha, t))
    return await asyncio.shield(task)


# --- de stap van de analyse -----------------------------------------------------------------------


@analysis.step("keyframes", 10, lambda probe, kind: probe.video() is not None)
async def step(ctx: analysis.StepContext) -> None:
    """De stap keyframes (Keuze 14, 17): de eigen lezers, en zo nodig de terugval. WorkerBusy
    en WorkerDown gaan door (analysis maakt er Defer(30) van)."""
    probe = ctx.probe
    if probe is None or probe.keyframes_state in ("ready", "failed") or wanted(probe) is None:
        return
    state: str = probe.keyframes_state
    try:
        if state == "none":
            state = await build(ctx.settings, ctx.sha, probe, fallback=False)
        if state in ("none", "sparse"):
            state = await build(ctx.settings, ctx.sha, probe, fallback=True)
    except client.WorkerFailed as exc:
        if exc.code not in FAILED_CODES:
            raise
        log.info("keyframe-index mislukt: %s", exc.code)
        state = "none"
    if state == "none":
        # Ook de terugval vond geen enkel keyframe, of het bestand faalde: niet steeds opnieuw
        await _save(ctx.settings, ctx.sha, "failed", None, None)
        state = "failed"
    probe.keyframes_state = state  # type: ignore[assignment]


async def load(settings: Settings, sha: str) -> tuple[list[int], list[int]]:
    """De index uit media_probe (tijd van de container, ms) met de groottes, voor een remux
    (streams.open). Eén korte databasesessie; ([], []) zonder index."""
    async with _maker(settings)() as session:
        blob = await session.scalar(select(MediaProbe.keyframes).where(MediaProbe.blob_sha == sha))
    return index(blob)
