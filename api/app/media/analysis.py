"""De analyse per inhoud: de job media.analyse en zijn stappen (bouwontwerp fase 5, Keuze 14).
Frozen: het register en de stap probe; de andere stappen melden zich aan vanuit hun pakket.

    probe (M0, order 0) → keyframes (M4, 10) → subs (M5, 20) → sprites (M5, 30, alleen video)
    → audio_info (M9, 40, alleen muziek)

Een stap is een async functie met een StepContext. Hij houdt geen databaseverbinding vast
terwijl hij op de worker wacht (regel 8): ctx.maker() geeft korte sessies. Een mislukte stap
houdt de rest niet tegen; WorkerBusy of WorkerDown wordt jobs.Defer(30), zonder dat de poging
telt. Lopen er 2 of meer live transcodes, dan wacht de analyse: Defer(60) (M4 zet de teller in
busy_live).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import jobs
from app.config import Settings
from app.db import get_engine
from app.media import client
from app.media.types import Probe
from app.models import MediaLibrary, MediaProbe
from app.storage import BlobStore

log = logging.getLogger("platform.media")

PROBE_VERSION = 1
JOB = "media.analyse"
LANE = "media"
TIMEOUT = 3600.0
DEFER_BUSY = 30.0
DEFER_LIVE = 60.0

Applies = Callable[[Probe, str | None], bool]


@dataclass
class StepContext:
    settings: Settings
    sha: str
    path: Path
    library_id: uuid.UUID | None
    kind: str | None  # de soort bibliotheek: movies | shows | music | videos
    probe: Probe | None
    extra: dict[str, Any] = field(default_factory=dict)

    def maker(self) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(get_engine(self.settings), expire_on_commit=False)


StepRun = Callable[[StepContext], Awaitable[None]]


@dataclass(frozen=True)
class Step:
    name: str
    order: int
    applies: Applies
    run: StepRun


_steps: dict[str, Step] = {}
# Hoeveel live transcodes er lopen (M4 zet hem); 2 of meer: de analyse wacht
busy_live: Callable[[], int] = lambda: 0  # noqa: E731


def step(name: str, order: int, applies: Applies) -> Callable[[StepRun], StepRun]:
    """Meldt een stap aan: `@analysis.step("keyframes", 10, lambda p, kind: p.video() is not
    None)`. Opnieuw met dezelfde naam vervangt de vorige."""

    def wrap(run: StepRun) -> StepRun:
        _steps[name] = Step(name, order, applies, run)
        return run

    return wrap


def steps() -> list[Step]:
    return sorted(_steps.values(), key=lambda s: s.order)


async def enqueue(session: AsyncSession, sha: str, *, library_id: uuid.UUID | None) -> None:
    """Zet de analyse van deze inhoud klaar, in de transactie van de aanroeper (job
    media.analyse:<sha>; staat hij al klaar, dan niet nog een keer)."""
    payload = {"sha": sha, "library_id": str(library_id) if library_id else None}
    await jobs.enqueue(session, JOB, payload, key=f"{JOB}:{sha}")


def _row(probe: Probe, sha: str) -> dict[str, Any]:
    data = probe.model_dump(
        mode="json",
        include={
            "container",
            "duration_ms",
            "bit_rate",
            "start_ms",
            "streams",
            "chapters",
            "tags",
            "truncated",
            "error",
            "state",
        },
    )
    data["blob_sha"] = sha
    data["version"] = PROBE_VERSION
    data["probed_at"] = datetime.now(UTC)
    return data


async def save_probe(maker: async_sessionmaker[AsyncSession], sha: str, probe: Probe) -> None:
    """De probe opslaan (nieuw, of over een oudere versie heen; de staat van de andere stappen
    gaat dan terug naar none)."""
    values = _row(probe, sha)
    async with maker() as session:
        row = await session.get(MediaProbe, sha)
        if row is None:
            session.add(MediaProbe(**values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.keyframes = None
            row.keyframes_state = "none"
            row.max_gop_ms = None
            row.subs_state = row.trick_state = row.audio_state = "none"
            row.trick = row.audio = None
        await session.commit()


async def load(maker: async_sessionmaker[AsyncSession], sha: str) -> Probe | None:
    """De opgeslagen probe van deze versie, of None."""
    async with maker() as session:
        row = await session.get(MediaProbe, sha)
        if row is None or row.version < PROBE_VERSION:
            return None
        return Probe.model_validate(row, from_attributes=True)


async def probe_blob(settings: Settings, sha: str) -> Probe:
    """De worker laten proben (zonder databaseverbinding). WorkerBusy, WorkerDown of
    WorkerFailed gaan door; een onleesbare bron wordt een Probe met state failed."""
    path = BlobStore(settings.blob_dir).path(sha)
    try:
        probe = await client.probe(path)
    except client.WorkerFailed as exc:
        probe = Probe(state="failed", error=(exc.code or "failed")[:200])
    probe.blob_sha = sha
    return probe


async def _probe_step(ctx: StepContext) -> None:
    if ctx.probe is not None:
        return
    ctx.probe = await probe_blob(ctx.settings, ctx.sha)
    await save_probe(ctx.maker(), ctx.sha, ctx.probe)


step("probe", 0, lambda probe, kind: True)(_probe_step)


async def run(settings: Settings, payload: dict[str, Any]) -> None:
    """De job media.analyse: de stappen op volgorde."""
    if busy_live() >= 2:
        raise jobs.Defer(DEFER_LIVE)
    sha = str(payload["sha"])
    library_id = uuid.UUID(payload["library_id"]) if payload.get("library_id") else None
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    kind = None
    if library_id is not None:
        async with maker() as session:
            kind = await session.scalar(
                select(MediaLibrary.kind).where(MediaLibrary.id == library_id)
            )
    path = BlobStore(settings.blob_dir).path(sha)
    if not path.exists():
        return  # de inhoud is al weg
    ctx = StepContext(settings, sha, path, library_id, kind, await load(maker, sha))
    for item in steps():
        if item.name != "probe" and (ctx.probe is None or ctx.probe.state != "ready"):
            break
        if item.name != "probe" and not item.applies(ctx.probe, kind):
            continue
        try:
            await item.run(ctx)
        except (client.WorkerBusy, client.WorkerDown):
            raise jobs.Defer(DEFER_BUSY) from None
        except jobs.Defer:
            raise
        except Exception:
            if item.name == "probe":
                raise
            log.exception("analysestap %s mislukt", item.name)


jobs.register(JOB, run, lane=LANE, timeout=TIMEOUT)
