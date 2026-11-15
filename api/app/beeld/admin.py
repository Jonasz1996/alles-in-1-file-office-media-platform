"""Zoeken op beeld op /beheer en de stand per gebruiker (bouwontwerp fase 6, X3; Keuze 82; X3b).

- register(): het deel beeld van /admin/search (search.hooks.register_admin): het blok
  (AdminBeeld), de schakelaar beeld in PATCH, en POST rebuild {"what": "beeld"} (alles opnieuw
  berekenen; de vectoren blijven tot de nieuwe er zijn). jobs.enable() roept hem aan, alleen met de
  analyse aan; zonder blijft het blok None en geven schakelaar en herbouwen 501, zoals in 0.8.0.
- wipe(): DELETE /admin/search/beeld zet de job beeld.wipe klaar (202).
- status(): GET /photos/beeld, met opwarmen hooguit eens per WARM_S per gebruiker.

Alleen aantallen: nooit een foto, een vector, een vraag of een gebruiker in het blok (K2-21).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis, jobs
from app.analyse import lock, monitor
from app.analysis import Step
from app.beeld import jobs as bjobs
from app.beeld import search as bsearch
from app.beeld import vecfile
from app.config import Settings
from app.models import Analysis, BlobEmbed
from app.search import hooks as search_hooks
from app.search.types import (
    AdminBeeld,
    AnalyseQuality,
    BeeldBench,
    BeeldSpace,
    BeeldStatus,
    CodeCount,
)

WARM_S = 60.0
ROW_OVERHEAD = 517  # bytes per rij naast de vector (2.053 − 1.536, gemeten in B7)
BENCH_FILE = "beeld-bench.json"
QUALITY_FILE = "beeld-kwaliteit.json"
_warmed: dict[uuid.UUID, float] = {}


def results_dir(settings: Settings) -> Path:
    """Waar beeld-bench en beeld-kwaliteit hun uitkomst laten (naast de cache, niet erin)."""
    return settings.data_dir / "cache"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def models_bytes() -> int:
    """De modellen van beeld volgens models.lock (zonder de bron, die na het omzetten weg is)."""
    return sum(
        f.size
        for f in lock.load()
        if f.name.startswith(f"{vecfile.vt.MODEL}/") and "/bron/" not in f.name
    )


def cache_bytes(directory: Path) -> int:
    total = 0
    if directory.is_dir() and not directory.is_symlink():
        for path in directory.iterdir():
            if vecfile.owner_of(path) is not None:
                try:
                    total += path.lstat().st_size
                except OSError:
                    pass
    return total


async def block(db: AsyncSession) -> AdminBeeld:
    settings = bjobs.enabled()
    found = monitor.status()
    counts = await analysis.counts(db, Step.EMBED)
    failed = await db.execute(
        select(Analysis.code, func.count())
        .where(
            Analysis.step == Step.EMBED.value,
            Analysis.state.in_(("failed", "none")),
            Analysis.code.is_not(None),
        )
        .group_by(Analysis.code)
        .order_by(Analysis.code)
    )
    rows, vec_bytes = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(func.length(BlobEmbed.vec)), 0))
        )
    ).one()
    bench = quality = None
    cache = 0
    if settings is not None:
        cache = await asyncio.to_thread(cache_bytes, settings.beeld_dir)
        raw = _read_json(results_dir(settings) / BENCH_FILE)
        if raw is not None:
            try:
                bench = BeeldBench.model_validate(raw)
            except ValueError:
                bench = None
        raw = _read_json(results_dir(settings) / QUALITY_FILE)
        if raw is not None:
            try:
                quality = AnalyseQuality.model_validate(raw)
            except ValueError:
                quality = None
    return AdminBeeld(
        on=await bjobs.admin_on(db),
        model="ok" if bjobs.ready(found) else "missing",
        analyse=found.state,
        seen=int(counts.get("done", 0)),
        photos=sum(n for state, n in counts.items() if state != "skip"),
        failed_by_code=[CodeCount(code=code, n=int(n)) for code, n in failed.all()],
        hour=bsearch.hour(),
        space=BeeldSpace(
            db_bytes=int(vec_bytes) + int(rows) * ROW_OVERHEAD,
            cache_bytes=cache,
            models_bytes=models_bytes(),
        ),
        bench=bench,
        quality=quality,
    )


async def switch(db: AsyncSession, value: bool) -> None:
    """PATCH /admin/search {"beeld": …}: uit is meteen 409 beeld_off voor iedereen; de vectoren
    blijven tot wissen (Keuze 82). Aan wekt de rij."""
    await bjobs.set_admin(db, value)
    if value:
        jobs.kick([bjobs.LANE])


async def rebuild(db: AsyncSession) -> None:
    """POST /admin/search/rebuild {"what": "beeld"}: elke inhoud die niet skip is opnieuw."""
    from sqlalchemy import update

    from app.models import now

    await db.execute(
        update(Analysis)
        .where(
            Analysis.step == Step.EMBED.value,
            Analysis.state.in_(("done", "none", "failed")),
        )
        .values(state="wait", tries=0, code=None, queued_at=now(), updated_at=now())
    )
    jobs.kick([bjobs.LANE])


async def wipe(db: AsyncSession) -> None:
    """DELETE /admin/search/beeld: de job die alles wist (202)."""
    await jobs.enqueue(db, bjobs.WIPE, {}, key=bjobs.WIPE)


def register() -> None:
    if "beeld" not in search_hooks.ADMIN_PARTS:
        return
    search_hooks.register_admin(
        "beeld", search_hooks.AdminPart(block=block, switch=switch, rebuild=rebuild)
    )


# --- GET /photos/beeld ---------------------------------------------------------------------------


async def progress(db: AsyncSession, user_id: uuid.UUID) -> float | None:
    """Hoeveel van de eigen foto's al bekeken is (0..1), of None als er niets meer open staat.

    Eerst of er nog iets open staat (wait of busy: twee zoekstappen in ix_analysis_todo); zo niet,
    dan None zonder te tellen. Elke beeldvraag vraagt dit, en het tellen liep over alle foto's van
    de eigenaar: ±9 ms bij 100.000 foto's op één core, bij elke vraag (test_beeld_perf)."""
    mine = (Analysis.step == Step.EMBED.value, Analysis.owner_id == user_id)
    busy = await db.scalar(
        select(literal(1)).where(*mine, Analysis.state.in_(analysis.OPEN)).limit(1)
    )
    if busy is None:
        return None
    rows = await db.execute(
        select(Analysis.state, func.count()).where(*mine).group_by(Analysis.state)
    )
    found = {state: int(n) for state, n in rows.all()}
    open_ = sum(found.get(state, 0) for state in analysis.OPEN)
    total = sum(n for state, n in found.items() if state != "skip")
    if not open_ or not total:
        return None
    return round(min(1.0, max(0.0, (total - open_) / total)), 4)


async def status(db: AsyncSession, user: Any, settings: Settings) -> BeeldStatus:
    if not await bjobs.admin_on(db):
        return BeeldStatus(state="beheer_uit", done=None)
    if user.role == "guest" or not await bjobs.user_on(db, user.id):
        return BeeldStatus(state="uit", done=None)
    found = monitor.status()
    if found.state in ("off", "down"):
        return BeeldStatus(state="weg", done=None)
    if not bjobs.ready(found):
        return BeeldStatus(state="niet_geinstalleerd", done=None)
    done = await progress(db, user.id)
    at = time.monotonic()
    if at - _warmed.get(user.id, -WARM_S) >= WARM_S:
        _warmed[user.id] = at
        bjobs.searched(user.id)
        await bjobs.warm(settings, user.id)
    return BeeldStatus(state="bezig" if done is not None else "ok", done=done)
