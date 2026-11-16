"""Het achterwerk van zoeken op beeld (bouwontwerp fase 6, X3; Keuzes 69, 76, 77, 82; X3b).

**Alleen met de analyse aan** (FACTS van golf 6: 0.8.0 blijft onzichtbaar). Bij het importeren meldt
dit bestand alleen on_start aan (search.hooks.backfill), en die doet niets zonder
Settings.analyse_on. Met de analyse aan zet enable() de stap embed, de jobs, de haak na
preview.ready, de schakelaar beeld in de foto-instellingen en het deel beeld van /admin/search
erbij. Zonder: geen rij in analysis voor embed, geen job in de rij analyse, geen bestand onder
DATA_ROOT, en elke route van X3 antwoordt zoals in 0.8.0.

- **De rijen zelf** (write_embed, de enige schrijver van blob_embed, en de vragen van Keuze 69
  die ook de prullenbak en elke eigenaar zien) staan in app/beeld_rows.py, zoals app/photo_rows.py:
  buiten app/beeld/, waar de wachter van test_own_live elke lezing aan own_live houdt.
- **De stap embed** (Step.EMBED, rij analyse, batches van PLATFORM_BEELD_BATCH): eligible zegt
  alleen "dit is een foto" (mime.ftype); run_batch beslist de rest. Wijkt (jobs.Defer) als de
  beheerder beeld uit heeft, zolang de index nog een backfill heeft (K2-07: de tekst gaat vóór),
  zonder container (DEFER_DOWN) of zonder beeldmodel (DEFER_MODEL). Per inhoud: skip als geen
  eigenaar met beeld aan hem wil (Keuze 69: zelf geüpload, of door iemand met beeld aan; via een
  link nooit), none met no_preview zonder 256.webp, anders samen met de rest van de batch één
  embed_image met de previews als fd (nooit het pad, P8). Wat de container per foto zegt: ok is
  write_embed; image_bad, unreadable en too_big zijn meteen failed.
- **Na preview.ready** gaat een inhoud met none/no_preview terug op wait.
- **Het bestand per eigenaar** (job beeld.vec, rij index): als gen > built_gen, hooguit eens per
  PLATFORM_BEELD_REBUILD_S, en al na FRESH_S als die eigenaar net zocht. Daarna opwarmen (rank met
  k = 0) als die eigenaar de afgelopen RECENT_S zocht (K2-13). Het opruimwerk (housekeeping
  "beeld") zet elke minuut de bestanden klaar die achterlopen, en ruimt bestanden op van wie er
  niet meer is of beeld uit heeft (met forget naar de container).
- **Aan** (job beeld.on): de foto's van die eigenaar terug op wait, en de backfill.
- **Uit** (job beeld.off, rij default): het bestand weg, forget, de rijen van blob_embed die geen
  andere eigenaar met beeld aan nog heeft, en die stappen op skip (K1-08).
- **Wissen** door de beheerder (job beeld.wipe, rij default): alle rijen van blob_embed, alle
  bestanden, forget voor elke eigenaar; de stappen terug op wait (de backfill als beeld aan staat).

Nooit in een log of fout: een vector, een vraag, de naam van een bestand (alleen 12 tekens sha).
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
import uuid
from datetime import timedelta
from itertools import batched
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import analysis, beeld_rows, jobs, mime, prefs, previews
from app.analyse import client, monitor, wire
from app.analyse.wire import Op
from app.analysis import Outcome, Step
from app.beeld import types as vt
from app.beeld import vecfile
from app.config import Settings
from app.db import get_engine
from app.models import Analysis, BeeldOwner, Job, Setting, now
from app.search import hooks as search_hooks
from app.storage import BlobStore

log = logging.getLogger("platform.beeld")

LANE = "analyse"
VEC = "beeld.vec"
ON = "beeld.on"
OFF = "beeld.off"
WIPE = "beeld.wipe"
SWEEP = "beeld"  # housekeeping
SWEEP_S = 60.0
SETTING = "search"  # Setting "search" {"beeld": bool}: de schakelaar van de beheerder (Keuze 82)
PREVIEW = f"{previews.THUMB}.webp"  # Keuze 77: de preview van 256
OFF_DEFER_S = 300.0
INDEX_DEFER_S = 60.0
RECENT_S = 24 * 3600.0  # wie zo kort geleden zocht, krijgt zijn nieuwe bestand opgewarmd
FRESH_S = 30.0  # een eigenaar die net zocht: het bestand opnieuw als het ouder is dan dit
SOON_S = 10.0  # en dan na zoveel seconden
FINAL = frozenset({"image_bad", "unreadable", "too_big"})
# De bestanden van het beeldmodel die de container moet hebben (models.lock, Keuze 84)
FILES = tuple(
    f"{vt.MODEL}/{name}"
    for name in ("beeld.int8.onnx", "tekst.int8.onnx", "tokenizer.bin", "meta.json")
)
CHUNK = 500

_settings: Settings | None = None  # gezet door enable(); None: beeld doet niet mee
_searched: dict[uuid.UUID, float] = {}  # wanneer elke eigenaar het laatst op beeld zocht


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def enabled() -> Settings | None:
    return _settings


def searched(user_id: uuid.UUID) -> None:
    """De zoekroute: deze eigenaar zocht nu (opwarmen en sneller een nieuw bestand)."""
    _searched[user_id] = time.time()


def recently(user_id: uuid.UUID, seconds: float = RECENT_S) -> bool:
    return time.time() - _searched.get(user_id, 0.0) < seconds


# --- aan of uit ----------------------------------------------------------------------------------


async def admin_on(db: AsyncSession) -> bool:
    """De schakelaar van de beheerder (Setting "search", veld beeld; standaard aan)."""
    row = await db.get(Setting, SETTING, populate_existing=True)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    return bool(value.get("beeld", True))


async def set_admin(db: AsyncSession, value: bool) -> None:
    """In de transactie van de aanroeper."""
    row = await db.get(Setting, SETTING, populate_existing=True)
    merged = {**(row.value if row is not None and isinstance(row.value, dict) else {})}
    merged["beeld"] = bool(value)
    if row is None:
        db.add(Setting(key=SETTING, value=merged))
    else:
        row.value = merged
    await db.flush()


async def user_on(db: AsyncSession, user_id: uuid.UUID) -> bool:
    return (await prefs.photo_prefs(db, user_id))["beeld"] == "aan"


def ready(found: monitor.AnalyseStatus | None = None) -> bool:
    """De container is er en heeft het beeldmodel (caps.beeld uit een goede meta.json)."""
    found = monitor.status() if found is None else found
    return (
        found.state in ("ok", "models_missing")
        and found.caps is not None
        and found.caps.beeld is not None
        and found.caps.beeld.dim == vt.DIM
        and found.built(Op.RANK)
        and found.ready(FILES)
    )


def _wait(found: monitor.AnalyseStatus) -> jobs.Defer | None:
    if found.state in ("off", "down"):
        return jobs.Defer(monitor.DEFER_DOWN)
    if not ready(found) or not found.built(Op.EMBED_IMAGE):
        return jobs.Defer(monitor.DEFER_MODEL)
    return None


# --- de stap embed (Keuze 77) --------------------------------------------------------------------


def eligible(sha: str, mime_type: str | None) -> bool:
    return mime.ftype((mime_type or "").split(";")[0].strip().lower()) == "image"


def batch_size(settings: Settings) -> int:
    return settings.beeld_batch


async def _index_busy(db: AsyncSession) -> bool:
    """K2-07: de tekst gaat vóór. Zolang de backfill van de analyse klaarstaat of loopt, of de
    index nog veel open heeft, wacht beeld."""
    pending = await db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.type == analysis.BACKFILL, Job.status.in_(("queued", "running")))
    )
    if pending:
        return True
    return await analysis.indexing(db, Step.TEXT) is not None


def _open_preview(store: BlobStore, sha: str) -> int | None:
    path = previews.directory(store, sha) / PREVIEW
    try:
        return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None


async def run_one(settings: Settings, sha: str) -> Outcome | None:
    return (await run_batch(settings, [sha])).get(sha)


async def run_batch(settings: Settings, shas: list[str]) -> dict[str, Outcome]:
    maker = _maker(settings)
    async with maker() as db:
        if not await admin_on(db):
            raise jobs.Defer(OFF_DEFER_S)
        if await _index_busy(db):
            raise jobs.Defer(INDEX_DEFER_S)
    found = monitor.status()
    defer = _wait(found)
    if defer is not None:
        raise defer
    assert found.caps is not None and found.caps.beeld is not None
    model = found.caps.beeld.model
    async with maker() as db:
        wanted = await beeld_rows.wanted(db, shas)
    outcomes: dict[str, Outcome] = {}
    todo: list[str] = []
    fds: list[int] = []
    store = BlobStore(settings.blob_dir)
    size = vt.DIM * 2
    try:
        for sha in shas:
            if sha not in wanted:
                outcomes[sha] = Outcome("skip")
                continue
            fd = _open_preview(store, sha)
            if fd is None:
                outcomes[sha] = Outcome("none", "no_preview")
                continue
            todo.append(sha)
            fds.append(fd)
        if not todo:
            return outcomes
        out = wire.out_memfd("uit")
        try:
            data = await client.call(
                settings, Op.EMBED_IMAGE, wire.EmbedImageArgs(), fds=[*fds, out]
            )
            raw = wire.read_all(out, len(todo) * size)
        finally:
            os.close(out)
    except (client.AnalyseDown, client.AnalyseBusy, client.AnalyseFailed) as exc:
        deferred = monitor.deferral(exc)
        if deferred is not None:
            raise deferred from None
        code = exc.code if isinstance(exc, client.AnalyseFailed) else "internal"
        log.warning("beeld: embed_image mislukt (%s)", code)
        for sha in todo:
            outcomes[sha] = Outcome("failed", code)
        return outcomes
    finally:
        for fd in fds:
            os.close(fd)
    try:
        photos = wire.EmbedImageData.model_validate(data).photos
    except ValueError:
        photos = []
    for i, sha in enumerate(todo):
        code = photos[i].code if i < len(photos) else "internal"
        vec = raw[i * size : (i + 1) * size]
        if code == "ok" and len(vec) == size and any(vec):
            async with maker() as db:
                await beeld_rows.write_embed(db, sha, vec, model)
                await db.commit()
            outcomes[sha] = Outcome("done")
        elif code in FINAL:
            outcomes[sha] = Outcome("failed", code, retry=False)
        else:
            outcomes[sha] = Outcome("failed", "internal" if code == "ok" else code)
    done = [sha for sha, o in outcomes.items() if o.state == "done"]
    if done:
        async with maker() as db:
            for owner in await beeld_rows.owners(db, done):
                await queue_vec(db, _uuid(owner), settings)
            await db.commit()
    return outcomes


def _uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(hex=str(value))


async def on_preview_ready(maker: async_sessionmaker[AsyncSession], sha: str, dated: bool) -> None:
    """previews.register_ready: een inhoud die op zijn preview wachtte, gaat terug op wait."""
    if _settings is None:
        return
    async with maker() as db:
        row = (
            await db.execute(
                select(Analysis.state, Analysis.code).where(
                    Analysis.blob_sha == sha, Analysis.step == Step.EMBED.value
                )
            )
        ).first()
        if row is None or (row.state == "none" and row.code == "no_preview"):
            await analysis.queue(db, sha, [Step.EMBED], owner=None, again=True)
            await db.commit()


# --- het bestand per eigenaar (job beeld.vec) ----------------------------------------------------


def _key(user_id: uuid.UUID) -> str:
    return f"{VEC}:{user_id.hex}"


def wait_s(row: BeeldOwner, settings: Settings, at: float | None = None) -> float:
    """Hoe lang nog tot het bestand opnieuw mag: hooguit eens per PLATFORM_BEELD_REBUILD_S, en
    al na FRESH_S als de eigenaar net zocht (Keuze 76)."""
    if row.built_at is None:
        return 0.0
    built = row.built_at.timestamp()
    gap = FRESH_S if recently(row.user_id, settings.beeld_rebuild_s) else settings.beeld_rebuild_s
    return max(0.0, built + gap - (time.time() if at is None else at))


async def queue_vec(
    db: AsyncSession, user_id: uuid.UUID, settings: Settings, *, soon: bool = False
) -> None:
    """De job beeld.vec voor deze eigenaar klaarzetten (in de transactie van de aanroeper)."""
    row = await db.get(BeeldOwner, user_id, populate_existing=True)
    delay = SOON_S if soon else 0.0
    if row is not None:
        delay = max(delay, wait_s(row, settings))
    await jobs.enqueue(
        db,
        VEC,
        {"user": user_id.hex},
        key=_key(user_id),
        run_after=now() + timedelta(seconds=delay) if delay else None,
    )


async def vec_job(settings: Settings, payload: dict[str, Any]) -> None:
    user_id = uuid.UUID(hex=str(payload["user"]))
    maker = _maker(settings)
    async with maker() as db:
        row = await db.get(BeeldOwner, user_id, populate_existing=True)
        on = await admin_on(db) and await user_on(db, user_id)
    if row is None or not on:
        return
    current = vecfile.is_current(settings.beeld_dir, user_id)
    if row.gen <= row.built_gen and row.built_at is not None and current:
        return
    delay = wait_s(row, settings)
    if delay > 0:
        async with maker() as db:
            await jobs.chain(
                db,
                VEC,
                {"user": user_id.hex},
                _key(user_id),
                run_after=now() + timedelta(seconds=delay),
            )
            await db.commit()
        return
    built = await vecfile.build(maker, settings.beeld_dir, user_id)
    if recently(user_id):
        await warm(settings, user_id)
    del built


async def warm(settings: Settings, user_id: uuid.UUID) -> bool:
    """rank met k = 0: het bestand alvast in het geheugen van de container (K2-13). Faalt stil
    (alleen de code in het log)."""
    if not ready():
        return False
    fd = vecfile.open_for_worker(settings.beeld_dir, user_id)
    if fd is None:
        return False
    try:
        head = vecfile.read_head_of(fd)
        await client.call(
            settings,
            Op.RANK,
            wire.RankArgs(owner=user_id.hex, gen=head.gen, k=0),
            fds=[fd],
        )
        return True
    except (client.AnalyseDown, client.AnalyseBusy, client.AnalyseFailed, vt.VecFileError) as exc:
        log.info("beeld: opwarmen lukte niet (%s)", type(exc).__name__)
        return False
    finally:
        os.close(fd)


async def forget(settings: Settings, user_id: uuid.UUID) -> None:
    """forget naar de container (K1-08). Zonder container: Defer, zodat de job het later nog eens
    doet (alles ervoor is dan al gedaan, en mag nog eens)."""
    if monitor.status().state == "off":
        return
    try:
        await client.call(settings, Op.FORGET, wire.ForgetArgs(owner=user_id.hex))
    except (client.AnalyseDown, client.AnalyseBusy, client.AnalyseFailed) as exc:
        deferred = monitor.deferral(exc)
        if deferred is not None:
            raise deferred from None
        if not (isinstance(exc, client.AnalyseFailed) and exc.code == "not_built"):
            raise


# --- aan, uit en wissen --------------------------------------------------------------------------


async def _bump_others(db: AsyncSession, user_id: uuid.UUID, settings: Settings) -> None:
    """De eigenaren in wier map deze gebruiker foto's zette: hun bestand opnieuw (Keuze 69: of
    die foto's meetellen, hangt van zijn keuze af)."""
    for owner in await beeld_rows.bump_others(db, user_id):
        await queue_vec(db, _uuid(owner), settings)


async def on_job(settings: Settings, payload: dict[str, Any]) -> None:
    """Beeld weer aan: de eigen foto's en die hij bij een ander neerzette terug op wait, de
    bestanden van die anderen opnieuw, en de backfill voor de rest."""
    user_id = uuid.UUID(hex=str(payload["user"]))
    maker = _maker(settings)
    async with maker() as db:
        if not await user_on(db, user_id):
            return
        at = now()
        await db.execute(
            text(
                "UPDATE analysis SET state = 'wait', tries = 0, code = NULL, "
                "queued_at = :at, updated_at = :at WHERE step = :step "
                "AND state IN ('skip', 'none', 'failed') "
                f"AND blob_sha IN ({beeld_rows.mine_sql()} AND n.dead = 0)"
            ),
            {"me": user_id.hex, "at": at, "step": Step.EMBED.value},
        )
        await queue_vec(db, user_id, settings)
        await _bump_others(db, user_id, settings)
        await db.commit()
    await analysis.recover(settings)
    await analysis.start_backfill(settings)


async def off_job(settings: Settings, payload: dict[str, Any]) -> None:
    """Beeld uit voor één gebruiker (K1-08): zijn bestand weg, de rijen van blob_embed van zijn
    foto's (ook die hij bij een ander neerzette) die niemand met beeld aan nog wil, die stappen
    op skip, de bestanden van die anderen opnieuw, en forget naar de container."""
    user_id = uuid.UUID(hex=str(payload["user"]))
    maker = _maker(settings)
    vecfile.remove(settings.beeld_dir, user_id)
    async with maker() as db:
        if await user_on(db, user_id):
            return  # intussen weer aan
        shas = await beeld_rows.unwanted_of(db, user_id)
        for chunk in batched(shas, CHUNK):
            await beeld_rows.delete_embeds(db, chunk)
            await db.execute(
                update(Analysis)
                .where(Analysis.step == Step.EMBED.value, Analysis.blob_sha.in_(chunk))
                .values(state="skip", code=None, updated_at=now())
            )
        await db.execute(
            update(BeeldOwner)
            .where(BeeldOwner.user_id == user_id)
            .values(built_gen=0, built_rows=0, built_at=None)
        )
        await _bump_others(db, user_id, settings)
        await db.commit()
    _searched.pop(user_id, None)
    await forget(settings, user_id)


async def wipe_job(settings: Settings, payload: dict[str, Any]) -> None:
    """De beeldindex wissen (Keuze 82): alle vectoren, alle bestanden, het geheugen van de
    container. De stappen gaan terug op wait: staat beeld aan, dan begint de backfill opnieuw."""
    maker = _maker(settings)
    async with maker() as db:
        await beeld_rows.delete_all(db)
        await db.execute(
            update(Analysis)
            .where(Analysis.step == Step.EMBED.value, Analysis.state.in_(("done", "failed")))
            .values(state="wait", tries=0, code=None, queued_at=now(), updated_at=now())
        )
        await db.execute(update(BeeldOwner).values(built_gen=0, built_rows=0, built_at=None))
        owners = set((await db.scalars(select(BeeldOwner.user_id))).all())
        await db.commit()
    owners |= remove_all(settings.beeld_dir)
    _searched.clear()
    for owner in sorted(owners):
        await forget(settings, owner)
    await analysis.recover(settings)


def remove_all(directory: Path) -> set[uuid.UUID]:
    """Alle bestanden van ons in de map weg (ook tijdelijke); geeft de eigenaren."""
    found: set[uuid.UUID] = set()
    if not directory.is_dir() or directory.is_symlink():
        return found
    for path in directory.iterdir():
        owner = vecfile.owner_of(path)
        if owner is not None:
            found.add(owner)
        elif not (path.name.startswith(".") and path.name.endswith(".tmp")):
            continue
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
    return found


async def _behind(db: AsyncSession, user_id: uuid.UUID, settings: Settings) -> bool:
    """Het bestand van deze eigenaar loopt achter: een hogere gen, of een ander model of een
    andere versie in de kop."""
    row = await db.get(BeeldOwner, user_id, populate_existing=True)
    if row is None:
        return False
    if row.gen > row.built_gen:
        return True
    return row.built_at is not None and not vecfile.is_current(settings.beeld_dir, user_id)


async def sweep(settings: Settings) -> None:
    """Het opruimwerk (eens per SWEEP_S): bestanden die achterlopen klaarzetten, bestanden
    weghalen van wie er niet meer is of beeld uit heeft (met forget), en een tijdelijk bestand
    dat een gestopte build liet staan (vecfile.stale_tmp)."""
    if _settings is None:
        return
    maker = _maker(settings)
    on = f"SELECT b.user_id FROM beeld_owner AS b WHERE b.user_id IN ({beeld_rows.on_users_sql()})"
    async with maker() as db:
        owners = [_uuid(owner) for owner in (await db.execute(text(on))).scalars().all()]
        stale = [owner for owner in owners if await _behind(db, owner, settings)]
        if stale and await admin_on(db):
            for owner in stale:
                await queue_vec(db, owner, settings)
            await db.commit()
    keep = set(owners)
    directory = settings.beeld_dir
    if not directory.is_dir() or directory.is_symlink():
        return
    for path in directory.iterdir():
        if vecfile.stale_tmp(path):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            continue
        owner = vecfile.owner_of(path)
        if owner is None or owner in keep:
            continue
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        _searched.pop(owner, None)
        with contextlib.suppress(jobs.Defer, client.AnalyseFailed):
            await forget(settings, owner)


# --- de schakelaar per gebruiker (prefs, Keuze 82) -----------------------------------------------


async def switch_state(db: AsyncSession, user: Any) -> prefs.SwitchState:
    return prefs.SwitchState(forbidden=not await admin_on(db), available=True)


async def switch_changed(db: AsyncSession, user: Any, value: str) -> None:
    """Na het schrijven van PATCH /me/photo-settings, in dezelfde transactie."""
    kind = ON if value == "aan" else OFF
    await jobs.enqueue(db, kind, {"user": user.id.hex}, key=f"{kind}:{user.id.hex}")


# --- aanmelden -----------------------------------------------------------------------------------


def enable(settings: Settings) -> bool:
    """Alles van beeld aanmelden, alleen met de analyse aan; twee keer kan."""
    global _settings
    if not settings.analyse_on:
        return False
    _settings = settings
    if Step.EMBED not in analysis.registered():
        analysis.register_step(
            Step.EMBED, LANE, eligible, run_one, batch=run_batch, size=batch_size
        )
    jobs.register(VEC, vec_job, lane="index", timeout=600.0)
    jobs.register(ON, on_job, lane="default", timeout=600.0)
    jobs.register(OFF, off_job, lane="default", timeout=600.0)
    jobs.register(WIPE, wipe_job, lane="default", timeout=600.0)
    jobs.housekeeping(SWEEP, sweep, SWEEP_S)
    search_hooks.register_blob_added(analysis.on_blob_added)
    previews.register_ready(on_preview_ready)
    prefs.register_switch("beeld", prefs.Switch(state=switch_state, changed=switch_changed))
    from app.beeld import admin

    admin.register()
    return True


def disable() -> None:
    """Alleen voor tests: alles van enable() weer weg, zodat een volgende test met de analyse
    uit 0.8.0 ziet."""
    global _settings
    _settings = None
    analysis._steps.pop(Step.EMBED, None)
    prefs._switches.pop("beeld", None)
    search_hooks._admin.pop("beeld", None)
    with contextlib.suppress(ValueError):
        previews._ready.remove(on_preview_ready)
    jobs.forget_housekeeping(SWEEP)
    _searched.clear()


async def on_start(settings: Settings) -> None:
    """search.hooks.backfill bij het starten: met de analyse aan alles aanmelden, de rijen van
    embed herstellen en de backfill (die na de index komt: run_batch wacht daarop)."""
    if not enable(settings):
        return
    await analysis.recover(settings)
    await analysis.start_backfill(settings)


search_hooks.register_backfill(on_start)
