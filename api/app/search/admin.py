"""De tab "Zoeken en foto's" op /beheer (bouwontwerp fase 6, Keuze 51; Z7): de stand, de
schakelaars en de knoppen achter routers/admin_search.py.

Wat GET /admin/search laat zien (AdminSearch, de vorm van §3.4 en web/src/admin/searchApi.ts):

- engine: fts of like, zoals een zoekvraag nu zou gaan (LikeFallback zolang een herbouw loopt);
- paused: het achtergrondwerk (de rijen index en ocr) staat op pauze;
- index: de namen klaar (geen herbouw bezig), de tekst n van m (de stap text van de analyse,
  zonder skip) en hoeveel inhouden mislukt zijn (text, hash en ocr; de lijst met sha en code via
  GET /admin/search/failed);
- rebuild: een herbouw met zijn voortgang. names en text: de herbouw in stukken van de FTS-index
  (search_state, Keuze 12); hash en geo: de keten van photometa die de pHash (Z3, Keuze 35) of
  de plek, camera en datum (Keuze 25) opnieuw leest. Ook de eerste pHash van alle foto's na een
  update telt als herbouw "hash" (zo ziet Jonas hoe ver de duplicaten zijn);
- extract_in_sandbox: leest de stap text in de zandbak van de mediaworker (Z2, step.in_sandbox);
- per_hour: ronde B en C die de tijdsgrens raakten, en 429 search_busy, in het afgelopen uur
  (app/search/perhour.py);
- ocr: het blok van X1 via search.hooks (register_admin("ocr")); zonder aanmelding "niet
  geïnstalleerd" (NOT_INSTALLED);
- places: geladen, bron en datum van places.sqlite (Z3, app/geo/load.py); met PLAATSEN=uit niet
  geladen;
- bench: de uitkomst van app.cli bench --los (app/search/benchlos.py, Keuze 54) in
  <data>/cache/zoekbench.json, of None;
- faces en beeld: de blokken van X2 en X3 via search.hooks.admin_block; None zonder hen;
- check: de laatste wekelijkse search-check (app/search/weekcheck.py, I1), of None.

De pauze en een herbouw van hash of geo staan in Setting "search" ({"paused": bool, "rebuild":
{"what", "started_at"}}), zodat ze een herstart overleven. Bij het starten (search.hooks
.register_backfill(on_start)) zet de pauze zich weer, loopt een onderbroken herbouw van de index
verder, en begint de lus die beheerders elke TICK seconden search.progress stuurt zolang er een
backfill of herbouw loopt. Nooit een vraag, een naam of tekst in het log: alleen aantallen.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anyio import to_thread
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import analysis, errors, events, photo_hash, photometa, render_meta
from app.analysis import Step
from app.db import get_engine
from app.extract import step as text_step
from app.geo import load as geo_load
from app.models import Analysis, Blob, BlobHash, Setting, User, now
from app.routers.stubs import not_built
from app.search import benchlos, hooks, perhour, rebuild, weekcheck
from app.search import engine as search_engine
from app.search.types import (
    EVENT_SEARCH_PROGRESS,
    AdminBench,
    AdminIndex,
    AdminOcr,
    AdminPlaces,
    AdminSearch,
    AdminSearchPatch,
    FailedItem,
    FailedPage,
    PerHour,
    Rebuild,
    RebuildWhat,
    SearchProgressEvent,
    event_data,
)

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger("platform.search")

KEY = "search"  # Setting
LANES = ("index", "ocr")  # wat de pauze stilzet
PAGE = 50  # mislukte per pagina
FAILED_STEPS = ("text", "hash", "ocr")  # de stappen van de analyse in FailedItem.step
TICK = 10.0  # search.progress, elke zoveel seconden (§3.4)
BENCH = benchlos.RESULT  # onder data_dir: cache/zoekbench.json
BENCH_MAX = 1 << 20
SOURCE = f"GeoNames cities1000 (CC BY 4.0) en Natural Earth {geo_load.NE_VERSIE}"
# Wat de app zelf herbouwt; ocr, faces en beeld zijn van X1, X2 en X3 (search.hooks)
FTS = ("names", "text")
PHOTOMETA = ("hash", "geo")
NOT_INSTALLED = AdminOcr(installed=False, on=False, pages_queued=0)
SHA = re.compile(r"[0-9a-f]{64}")

_tasks: set[asyncio.Task[Any]] = set()
_fts: dict[str, asyncio.Task[Any]] = {}  # de herbouw van de index die nu loopt, per database


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.isoformat()


def _fraction(done: int, total: int) -> float:
    return 1.0 if total <= 0 else round(min(1.0, max(0.0, done / total)), 4)


# --- Setting "search" ----------------------------------------------------------------------------


async def stored(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(Setting, KEY, populate_existing=True)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


async def _store(db: AsyncSession, **changes: Any) -> None:
    """Velden van Setting "search" zetten, in de transactie van de aanroeper."""
    row = await db.get(Setting, KEY, populate_existing=True)
    value = {**(row.value if row is not None and isinstance(row.value, dict) else {}), **changes}
    if row is None:
        db.add(Setting(key=KEY, value=value))
    else:
        row.value = value  # een nieuw dict: SQLAlchemy ziet de wijziging


def apply_pause(paused: bool) -> None:
    """De pomp van de rijen index en ocr wijkt (analysis.PAUSED), of weer niet."""
    if paused:
        analysis.PAUSED.update(LANES)
    else:
        analysis.PAUSED.difference_update(LANES)


def is_paused() -> bool:
    return bool(analysis.PAUSED.intersection(LANES))


async def _wake_lanes(db: AsyncSession) -> None:
    """Hervatten: de pomp van elke rij die de pauze stilzette, nu (zoals retry hieronder). Een
    pomp die tijdens de pauze week, staat anders DEFER_S later; dan las de index na "Hervatten"
    pas na een halve minuut weer (z7.md, Open na de samenvoeging). Pas na apply_pause(False):
    een pomp die eerder loopt, ziet de pauze nog en wijkt opnieuw. In de transactie van de
    aanroeper."""
    moment = now()
    lanes = {reg.lane for reg in analysis.registered().values()} & set(LANES)
    for lane in sorted(lanes):
        await analysis._wake(db, lane, moment)


# --- De stand ------------------------------------------------------------------------------------


async def _index(db: AsyncSession, rebuilding: bool) -> AdminIndex:
    text = await analysis.counts(db, Step.TEXT)
    total = sum(n for state, n in text.items() if state != "skip")
    open_ = sum(text.get(state, 0) for state in analysis.OPEN)
    failed = await db.scalar(
        select(func.count())
        .select_from(Analysis)
        .where(Analysis.state == "failed", Analysis.step.in_(FAILED_STEPS))
    )
    return AdminIndex(
        names_done=not rebuilding,
        text_done=total - open_,
        text_total=total,
        failed=int(failed or 0),
    )


async def _photometa_progress(db: AsyncSession, what: str) -> float:
    """Het deel van de foto's dat de keten van photometa al (opnieuw) las: hash of geo."""
    if what == "hash":
        image = Blob.mime.like("image/%")
        total = await db.scalar(select(func.count()).select_from(Blob).where(image))
        done = await db.scalar(
            select(func.count())
            .select_from(Blob)
            .join(BlobHash, BlobHash.blob_sha == Blob.sha256)
            .where(image, BlobHash.version >= photo_hash.VERSION)
        )
    else:
        total = await db.scalar(select(func.count()).select_from(Blob).where(photometa.MEDIA))
        done = await db.scalar(
            select(func.count())
            .select_from(Blob)
            .where(photometa.MEDIA, Blob.meta_version >= render_meta.VERSION)
        )
    return _fraction(int(done or 0), int(total or 0))


async def _photometa_rebuild(db: AsyncSession, saved: dict[str, Any]) -> Rebuild | None:
    """De herbouw van photometa (hash of geo) die nog loopt; een die klaar is, telt niet meer."""
    mark = saved.get("rebuild")
    if isinstance(mark, dict) and mark.get("what") in PHOTOMETA:
        done = await _photometa_progress(db, mark["what"])
        if done < 1.0:
            return Rebuild(what=mark["what"], started_at=str(mark.get("started_at")), done=done)
    return None


async def _rebuild(db: AsyncSession, saved: dict[str, Any]) -> Rebuild | None:
    """De herbouw die nu loopt: eerst die van de index (die raakt zoeken), dan die van
    photometa."""
    conn = await db.connection()
    found = await rebuild.progress(conn.engine)
    if found is not None:
        row = (
            await conn.exec_driver_sql("SELECT started_at, what FROM search_state WHERE id = 1")
        ).first()
        what = row[1] if row is not None and row[1] in FTS else "text"
        started = row[0] if row is not None and row[0] else _iso(now())
        return Rebuild(what=what, started_at=started, done=_fraction(found[0], found[1]))
    return await _photometa_rebuild(db, saved)


def _places(settings: Settings) -> AdminPlaces:
    if settings.places == "uit":
        return AdminPlaces(loaded=False, source=None, date=None)
    meta = geo_load.read_meta(Path(settings.places_db))
    if not meta or meta.get("schema") != geo_load.SCHEMA:
        return AdminPlaces(loaded=False, source=None, date=None)
    return AdminPlaces(loaded=True, source=SOURCE, date=meta.get("gebouwd"))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if 0 <= value < 1e9 else None


def bench_result(settings: Settings) -> AdminBench | None:
    """De uitkomst van de zoekbench in <data>/cache/zoekbench.json: wanneer (de tijd van het
    bestand) en de p95 voor "eigenaar" en "alles". Klopt het bestand niet, dan None."""
    path = Path(settings.data_dir) / BENCH
    try:
        if not path.is_file() or path.stat().st_size > BENCH_MAX:
            return None
        found = json.loads(path.read_text(encoding="utf-8"))
        zoeken = found["zoeken"]
        owner, everyone = _number(zoeken["eigenaar"]["p95"]), _number(zoeken["alles"]["p95"])
        at = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if owner is None or everyone is None:
        return None
    return AdminBench(at=at, p95_owner_ms=owner, p95_all_ms=everyone)


async def state(db: AsyncSession, settings: Settings) -> AdminSearch:
    conn = await db.connection()
    chosen = await search_engine.engine_for(settings, conn)
    building = await search_engine.rebuilding(conn)
    saved = await stored(db)
    counts = perhour.counts()
    places, bench = await to_thread.run_sync(lambda: (_places(settings), bench_result(settings)))
    return AdminSearch(
        engine=chosen.name,
        paused=bool(saved.get("paused", False)),
        index=await _index(db, building),
        rebuild=await _rebuild(db, saved),
        extract_in_sandbox=text_step.in_sandbox(settings),
        per_hour=PerHour(
            deadline_b=counts["deadline_b"], deadline_c=counts["deadline_c"], busy=counts["busy"]
        ),
        ocr=await hooks.admin_block("ocr", db) or NOT_INSTALLED,
        places=places,
        bench=bench,
        faces=await hooks.admin_block("faces", db),
        beeld=await hooks.admin_block("beeld", db),
        check=await weekcheck.last(db),
    )


# --- De schakelaars (PATCH) ----------------------------------------------------------------------


async def change(db: AsyncSession, body: AdminSearchPatch) -> None:
    """De velden die gezet zijn. Een deel van X1, X2 of X3 dat zich niet aanmeldde, geeft 501
    not_built vóór er iets verandert. Hervatten wekt daarna de pompen van index en ocr."""
    switches = {
        part: getattr(body, field)
        for field, part in hooks.ADMIN_SWITCHES.items()
        if getattr(body, field) is not None
    }
    for part in switches:
        if not hooks.admin_registered(part):
            raise not_built(hooks.ADMIN_PARTS[part])
    if body.paused is not None:
        await _store(db, paused=body.paused)
        await db.commit()
        apply_pause(body.paused)
        if not body.paused:
            await _wake_lanes(db)
            await db.commit()
        log.info("Zoeken: achtergrondwerk %s", "gepauzeerd" if body.paused else "weer aan")
    # De schakelaars van X1, X2 en X3 werken in deze transactie (search.hooks); hier vast
    for part, value in switches.items():
        await hooks.admin_switch(part, db, value)
    if switches:
        await db.commit()


# --- Herbouwen (POST rebuild) --------------------------------------------------------------------


def _spawn(coro: Any, name: str) -> asyncio.Task[Any]:
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def _run_fts(settings: Settings, what: str) -> None:
    """De herbouw in stukken (rebuild.rebuild); tussen de stukken wacht hij zolang de pauze
    aan staat. Een fout komt in het log; de cursor blijft dan staan en de volgende start gaat
    verder."""

    async def between(_cursor: int) -> None:
        while is_paused():  # noqa: ASYNC110 - een set in analysis; eens per seconde is genoeg
            await asyncio.sleep(1.0)

    try:
        times = await rebuild.rebuild(get_engine(settings), what=what, after_chunk=between)
        if times:
            log.info("Zoeken: de index is herbouwd in %d stuk(ken)", len(times))
    except Exception:  # noqa: BLE001 - de app blijft draaien; zoeken gaat met LIKE verder
        log.exception("Zoeken: herbouwen van de index mislukt")


def _fts_running(settings: Settings) -> bool:
    task = _fts.get(str(settings.database_url or settings.data_dir))
    return task is not None and not task.done()


def start_fts(settings: Settings, what: str) -> None:
    key = str(settings.database_url or settings.data_dir)
    _fts[key] = _spawn(_run_fts(settings, what), "zoekindex herbouwen")


async def start_rebuild(db: AsyncSession, settings: Settings, what: RebuildWhat) -> None:
    """POST /admin/search/rebuild. 409 search_rebuild_running als dezelfde soort al loopt."""
    if what in ("ocr", "faces", "beeld"):
        await hooks.admin_rebuild(what, db)
        await db.commit()
        return
    if what in FTS:
        conn = await db.connection()
        if _fts_running(settings) or await search_engine.rebuilding(conn):
            raise _running()
        # De cursor op 0 vóór de 202: een GET direct erna ziet de herbouw al (rebuild.what,
        # LIKE), ook als de taak nog niet liep. De taak gaat daarna verder vanaf die cursor.
        await rebuild.begin(conn, what)
        await db.commit()
        start_fts(settings, what)
        log.info("Zoeken: herbouw van de index gestart (%s)", what)
        return
    # De herbouw van photometa zelf, ook als er tegelijk een van de index loopt (die toont GET
    # eerst)
    if await _photometa_rebuild(db, await stored(db)) is not None:
        raise _running()
    if what == "hash":
        # Een oudere versie: photometa rekent hem opnieuw; tot dan blijft de oude staan
        await db.execute(
            update(BlobHash)
            .where(BlobHash.version >= photo_hash.VERSION)
            .values(version=photo_hash.VERSION - 1)
            .execution_options(synchronize_session=False)
        )
    else:
        # geo: de plek, camera en datum opnieuw lezen (per eigenaar gecontroleerd, Keuze 28)
        await db.execute(
            update(Blob)
            .where(photometa.MEDIA, Blob.meta_version >= render_meta.VERSION)
            .values(meta_version=render_meta.VERSION - 1)
            .execution_options(synchronize_session=False)
        )
    await _store(db, rebuild={"what": what, "started_at": _iso(now())})
    await photometa.kick(db)
    await db.commit()
    log.info("Zoeken: herbouw gestart (%s)", what)


def _running() -> Exception:
    return errors.fail(409, "search_rebuild_running", "Er loopt al een herbouw")


# --- Mislukt (GET failed, POST retry) ------------------------------------------------------------


def _cursor(updated: datetime, sha: str, step: str) -> str:
    raw = json.dumps([_iso(updated), sha, step]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _read_cursor(text: str) -> tuple[datetime, str, str]:
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        when, sha, step = json.loads(raw)
        moment = datetime.fromisoformat(when)
        if not (isinstance(sha, str) and SHA.fullmatch(sha) and step in FAILED_STEPS):
            raise ValueError
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError):
        raise errors.fail(422, "invalid_cursor", "Ongeldige cursor voor de lijst") from None
    return moment, sha, step


async def failed(db: AsyncSession, cursor: str | None) -> FailedPage:
    """De mislukte inhouden, de nieuwste eerst: sha, stap, code en tijd. Nooit een naam."""
    order = (Analysis.updated_at.desc(), Analysis.blob_sha.desc(), Analysis.step.desc())
    query = select(Analysis.blob_sha, Analysis.step, Analysis.code, Analysis.updated_at).where(
        Analysis.state == "failed", Analysis.step.in_(FAILED_STEPS)
    )
    if cursor:
        moment, sha, step = _read_cursor(cursor)
        query = query.where(
            tuple_(Analysis.updated_at, Analysis.blob_sha, Analysis.step) < (moment, sha, step)
        )
    rows = (await db.execute(query.order_by(*order).limit(PAGE + 1))).all()
    items = [
        FailedItem(sha=sha, step=step, code=code or "failed", at=_iso(updated))
        for sha, step, code, updated in rows[:PAGE]
    ]
    last = rows[PAGE - 1] if len(rows) > PAGE else None
    return FailedPage(
        items=items, cursor=_cursor(last.updated_at, last.blob_sha, last.step) if last else None
    )


async def retry(db: AsyncSession) -> int:
    """Alle mislukte terug in de rij (tries 0), en de pomp van hun rij aan."""
    moment = now()
    found = await db.execute(
        update(Analysis)
        .where(Analysis.state == "failed", Analysis.step.in_(FAILED_STEPS))
        .values(state="wait", tries=0, code=None, queued_at=moment, updated_at=moment)
        .returning(Analysis.step)
        .execution_options(synchronize_session=False)
    )
    retried = [row[0] for row in found.all()]
    lanes = {reg.lane for step, reg in analysis.registered().items() if step.value in retried}
    for lane in sorted(lanes):
        await analysis._wake(db, lane, moment)
    await db.commit()
    log.info("Zoeken: %d mislukte stap(pen) opnieuw in de rij", len(retried))
    return len(retried)


# --- Bij het starten, en search.progress ---------------------------------------------------------


async def progress(db: AsyncSession) -> list[SearchProgressEvent]:
    """Wat er nu loopt: de backfill van de tekst (zoals /search hem toont) en een herbouw."""
    found: list[SearchProgressEvent] = []
    backfill = await analysis.indexing(db, Step.TEXT)
    if backfill is not None:
        found.append(SearchProgressEvent(what="backfill", done=backfill.done))
    current = await _rebuild(db, await stored(db))
    if current is not None:
        found.append(SearchProgressEvent(what=current.what, done=current.done))
    return found


async def _admins_listening(db: AsyncSession) -> list[uuid.UUID]:
    """De beheerders met een open verbinding (er zijn er maar een paar)."""
    rows = await db.execute(select(User.id).where(User.role == "admin"))
    return [row[0] for row in rows.all() if events.hub.listeners(row[0])]


async def tick(settings: Settings) -> int:
    """Eén keer search.progress naar de beheerders die luisteren; geeft het aantal berichten."""
    async with _maker(settings)() as db:
        admins = await _admins_listening(db)
        if not admins:
            return 0
        found = await progress(db)
    for item in found:
        events.publish(admins, EVENT_SEARCH_PROGRESS, event_data(EVENT_SEARCH_PROGRESS, item))
    return len(found)


async def _progress_loop(settings: Settings, hub: events.Hub) -> None:
    while not hub.closed:
        try:
            await tick(settings)
        except Exception:  # noqa: BLE001 - de volgende tik gewoon opnieuw
            log.debug("search.progress", exc_info=True)
        await asyncio.sleep(TICK)


async def on_start(settings: Settings) -> None:
    """search.hooks.register_backfill: de pauze terug, een herbouw van photometa die klaar is
    weg, de eerste pHash van alle foto's als herbouw "hash" tonen, een onderbroken herbouw van
    de index verder, en de lus van search.progress."""
    async with _maker(settings)() as db:
        saved = await stored(db)
        apply_pause(bool(saved.get("paused", False)))
        mark = saved.get("rebuild")
        live = (
            isinstance(mark, dict)
            and mark.get("what") in PHOTOMETA
            and await _photometa_progress(db, mark["what"]) < 1.0
        )
        if not live:
            open_ = await db.scalar(
                select(func.count())
                .select_from(Blob)
                .where(Blob.mime.like("image/%"), photo_hash.not_hashed())
            )
            if int(open_ or 0) >= analysis.INDEXING_MIN:
                await _store(db, rebuild={"what": "hash", "started_at": _iso(now())})
            elif mark is not None:
                await _store(db, rebuild=None)
        await db.commit()
        building = await search_engine.rebuilding(await db.connection())
    if building and not _fts_running(settings):
        log.info("Zoeken: een onderbroken herbouw van de index gaat verder")
        start_fts(settings, "text")
    _spawn(_progress_loop(settings, events.hub), "search.progress")


def register() -> None:
    """Bij de import (routers/admin_search.py importeert dit bestand); twee keer kan."""
    hooks.register_backfill(on_start)


register()
