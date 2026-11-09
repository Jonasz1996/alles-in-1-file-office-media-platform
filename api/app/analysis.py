"""De analyse van inhoud voor zoeken (bouwontwerp fase 6, §3.3; Keuzes 3, 19, 20, 49, 50).

Z0 legde de vorm vast; Z2 bouwde de wachtrij. Eén stap per inhoud (blob) in de tabel analysis:
text (Z2), hash (Z3), ocr (X1) en embed (X3, de vector van zoeken op beeld; Z0b legde de naam
vast, Keuze 80).

- register_step(): een pakket meldt zijn stap aan, met zijn rij (index, ocr of analyse) en
  wanneer hij meedoet (eligible). Een stap werkt per inhoud (run) of per batch (batch, zoals
  text: één kind voor 16 blobs, Keuze 17).
- queue(): zet stappen voor een inhoud in de wachtrij (state wait), in de transactie van de
  aanroeper; na de commit wekt een job de rij. Een lagere versie (VERSIONS) dan die in de code
  gaat terug op wait, net als again=True; first=True zet hem vooraan (wat iemand nu bekijkt).
- De pomp: per rij één job ("analysis.<rij>"), één batch per keer (Keuze 49). Hij wijkt
  (jobs.Defer) als er ≥ index_defer_transcodes omzettingen lopen of OnlyOffice start (Keuze
  50), kiest een batch om de beurt per eigenaar (Keuze 20: hooguit FAIR_SHARE per eigenaar per
  ronde, wie het langst niet aan de beurt was eerst, binnen een eigenaar de nieuwste eerst),
  zet die op busy, draait de stap en legt de uitkomst vast. Daarna zet hij zichzelf opnieuw
  klaar zolang er werk is.
- De uitkomst per inhoud (Outcome): done, none (niets te halen), skip (doet niet mee) of
  failed. failed met retry: hooguit MAX_TRIES pogingen, met wachttijd (RETRY_AFTER, steeds
  dubbel), daarna failed met de code (Keuze 19). Zonder retry (too_slow, zip_limit,
  xml_refused): meteen failed (S15). Een inhoud zonder uitkomst gaat terug op wait, zonder dat
  het een poging telt.
- Bij het starten (search.hooks.backfill): wat op busy bleef staan terug op wait, elke rij met
  een lagere versie dan de code terug op wait, en de pomp van elke rij met werk aan (recover).
  Daarna de backfill (start_backfill, Keuze 20): de job analysis.backfill zet per BACKFILL_CHUNK
  de blobs zonder rij in de wachtrij, met als eigenaar die van de nieuwste levende node met die
  sha, elke keer in één korte transactie; de pomp kiest daaruit om de beurt per eigenaar.
- Een nieuwe inhoud (search.hooks.blob_added, vanuit files.add_ref na de commit): on_blob_added
  zet hem in de wachtrij, in een taak in het geheugen (de haak mag de database niet in). Zonder
  draaiende rijen (tests, de cli) gebeurt er niets: de backfill vindt hem bij het starten.
- indexing(): de voortgang voor het antwoord van /search ({"done": 0.43}) zolang er veel werk
  open staat.

write_text() is de enige schrijver van blob_text (Keuze 3). Eerst een SELECT, dan een gewone
INSERT of één UPDATE, in één korte transactie. Nooit INSERT OR REPLACE, OR IGNORE of een upsert:
dan klopt de FTS-index niet meer (de triggers van app/search/schema.py). Hetzelfde geldt hier
voor de tabel analysis.

In het log nooit de naam van een bestand: alleen de eerste 12 tekens van de sha.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

import anyio
from sqlalchemy import exists, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import jobs
from app.config import Settings
from app.db import get_engine
from app.models import Analysis, Blob, BlobText, Job, Node, now

if TYPE_CHECKING:
    from app.search.types import IndexProgress

log = logging.getLogger("platform.analysis")


class Step(StrEnum):
    TEXT = "text"
    HASH = "hash"
    OCR = "ocr"
    EMBED = "embed"


# Een hogere versie zet de stap voor elke inhoud opnieuw in de wachtrij (zoals PROBE_VERSION)
VERSIONS: dict[Step, int] = {Step.TEXT: 1, Step.HASH: 1, Step.OCR: 1, Step.EMBED: 1}

STATES = ("wait", "busy", "done", "none", "skip", "failed")
# Gepauzeerd op /beheer (Z7, Keuze 51; app/search/admin.py zet en wist dit): de pomp van deze
# rijen wijkt, zoals voor busy_elsewhere
PAUSED: set[str] = set()
OPEN = ("wait", "busy")
MAX_TRIES = 3
RETRY_AFTER = timedelta(minutes=1)  # na de eerste mislukte poging; daarna steeds dubbel
FAIR_SHARE = 4  # plekken per eigenaar per ronde (Keuze 20)
DEFER_S = 30.0  # wijken voor omzettingen of een startende OnlyOffice (Keuze 50)
PUMP_TIMEOUT = 3600.0  # één batch, met het halveren na een crash
# Vooraan (first=True): queued_at vóór FRONT_END. De pomp neemt die eerst, van elke eigenaar.
FRONT = jobs.FRONT
FRONT_END = datetime(2001, 1, 1, tzinfo=FRONT.tzinfo)
# De backfill (Keuze 20): zoveel blobs per job (één korte transactie), in de rij index
BACKFILL = "analysis.backfill"
BACKFILL_CHUNK = 500
BACKFILL_TIMEOUT = 600.0
# indexing in /search: pas vanaf zoveel open inhouden (één upload is geen backfill), en
# hooguit eens per INDEXING_TTL geteld
INDEXING_MIN = 50
INDEXING_TTL = 5.0
# write_text (H1): de triggers schrijven de tekst voor elke node met die sha. Eén schrijfactie
# zet zo hooguit TEXT_BUDGET tekens in fts_node (body en ocr, opgeteld over alle nodes met die
# sha): een tekst van 1 MB met 10 kopieën blijft heel, bij meer kopieën krijgt elke node minder,
# nooit minder dan TEXT_MIN_SHARE.
TEXT_BUDGET = 12 * 1024 * 1024
TEXT_MIN_SHARE = 1_000


@dataclass(frozen=True)
class Outcome:
    """Wat een stap met een inhoud deed. retry telt alleen bij failed."""

    state: Literal["done", "none", "skip", "failed"]
    code: str | None = None
    retry: bool = True


# Doet de stap mee voor deze inhoud? (sha, mime) → ja of nee
Eligible = Callable[[str, str | None], bool]
# Voert de stap uit voor één inhoud (sha); de stap schrijft zelf zijn uitkomst (write_text en
# dergelijke). None betekent done.
Runner = Callable[[Settings, str], Awaitable[Outcome | None]]
# Voert de stap uit voor een batch; een inhoud zonder uitkomst gaat terug op wait
Batch = Callable[[Settings, list[str]], Awaitable[dict[str, Outcome]]]
Size = Callable[[Settings], int]


@dataclass(frozen=True)
class Registered:
    step: Step
    lane: str
    eligible: Eligible
    run: Runner
    batch: Batch | None = None
    size: Size | None = None


_steps: dict[Step, Registered] = {}
# Wanneer elke eigenaar (per stap) en elke stap (per rij) het laatst aan de beurt was. In het
# geheugen: na een herstart begint iedereen gelijk.
_served: dict[tuple[Step, uuid.UUID | None], int] = {}
_step_served: dict[Step, int] = {}
_turn = itertools.count(1)


def pump_type(lane: str) -> str:
    return f"analysis.{lane}"


def register_step(
    step: Step,
    lane: str,
    eligible: Eligible,
    run: Runner,
    *,
    batch: Batch | None = None,
    size: Size | None = None,
) -> None:
    """Een pakket meldt zijn stap aan (Z2 text, Z3 hash, X1 ocr); één keer per stap. batch en
    size: de stap werkt per batch van size(settings) inhouden (zonder: één per keer, met run)."""
    if step in _steps:
        raise ValueError(f"stap {step.value} is al aangemeld")
    if lane not in jobs.LANES:
        raise ValueError(f"onbekende rij {lane!r}")
    _steps[step] = Registered(step, lane, eligible, run, batch, size)
    jobs.register(pump_type(lane), _pump_for(lane), lane=lane, timeout=PUMP_TIMEOUT)
    jobs.register(BACKFILL, backfill_job, lane="index", timeout=BACKFILL_TIMEOUT)
    from app.search import hooks as search_hooks

    search_hooks.register_backfill(recover)


def registered() -> dict[Step, Registered]:
    return dict(_steps)


def reset() -> None:
    """Alleen voor tests."""
    _steps.clear()
    _served.clear()
    _step_served.clear()
    _indexing.clear()


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


# --- de wachtrij ---------------------------------------------------------------------------------


async def queue(
    db: AsyncSession,
    sha: str,
    steps: Iterable[Step],
    *,
    owner: uuid.UUID | None,
    first: bool = False,
    again: bool = False,
) -> None:
    """Zet stappen voor een inhoud in de wachtrij, in de transactie van de aanroeper (die
    commit). owner: wie hem het eerst nodig had (de volgorde per gebruiker, Keuze 20); first:
    vóór de rest (wat iemand nu bekijkt); again: ook als de stap al klaar was (de inhoud is
    veranderd, zoals een PDF van OnlyOffice). Een stap die niet is aangemeld, slaat queue over;
    een inhoud waar de stap niet bij past, krijgt state skip."""
    blob = (await db.execute(select(Blob.mime).where(Blob.sha256 == sha))).first()
    if blob is None:
        return
    lanes: set[str] = set()
    at = FRONT if first else now()
    for step in dict.fromkeys(Step(s) for s in steps):
        reg = _steps.get(step)
        if reg is None:
            continue
        state = "wait" if reg.eligible(sha, blob.mime) else "skip"
        mine = (Analysis.blob_sha == sha, Analysis.step == step.value)
        row = (
            await db.execute(
                select(Analysis.state, Analysis.version, Analysis.owner_id).where(*mine)
            )
        ).first()
        version = VERSIONS[step]
        if row is None:
            await db.execute(
                insert(Analysis).values(
                    blob_sha=sha,
                    step=step.value,
                    version=version,
                    state=state,
                    tries=0,
                    code=None,
                    owner_id=owner,
                    queued_at=at,
                    updated_at=now(),
                )
            )
        elif row.version < version or (again and row.state not in OPEN):
            await db.execute(
                update(Analysis)
                .where(*mine)
                .values(
                    version=version,
                    state=state,
                    tries=0,
                    code=None,
                    owner_id=row.owner_id if row.owner_id is not None else owner,
                    queued_at=at,
                    updated_at=now(),
                )
            )
        elif row.state == "wait" and first:
            await db.execute(update(Analysis).where(*mine).values(queued_at=FRONT))
        else:
            continue
        if state == "wait":
            lanes.add(reg.lane)
    for lane in sorted(lanes):
        await _wake(db, lane, now())


async def recover(settings: Settings) -> None:
    """Bij het starten (search.hooks.backfill): busy terug op wait (een crash midden in een
    batch), elke rij met een lagere versie terug op wait, en de pomp van elke rij met werk aan.
    """
    async with _maker(settings)() as db:
        for step in _steps:
            await db.execute(
                update(Analysis)
                .where(Analysis.step == step.value, Analysis.state == "busy")
                .values(state="wait", updated_at=now())
            )
            await db.execute(
                update(Analysis)
                .where(Analysis.step == step.value, Analysis.version < VERSIONS[step])
                .values(version=VERSIONS[step], state="wait", tries=0, code=None, updated_at=now())
            )
        for lane in sorted({reg.lane for reg in _steps.values()}):
            await _chain_next(db, lane)
        await db.commit()


# --- nieuwe inhoud en de backfill (Keuze 20) -----------------------------------------------------

_tasks: set[asyncio.Task] = set()


def _live_settings() -> Settings | None:
    """De instellingen van de rijen die in deze event loop draaien (de app), of None (tests
    zonder rijen, de cli): dan doet on_blob_added niets en vindt de backfill de inhoud later."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    for runner in list(jobs._runners):
        if getattr(runner, "_loop", None) is loop:
            return runner.settings
    return None


def on_blob_added(sha: str, owner: uuid.UUID | None) -> None:
    """search.hooks.blob_added: een nieuwe inhoud (files.add_ref, na de commit). Geen database
    hier: een taak in het geheugen zet elke aangemelde stap klaar (queue), met owner als
    eigenaar. Faalt die, dan staat het in het log en pakt de backfill hem bij de volgende start."""
    settings = _live_settings()
    if settings is None or not _steps:
        return
    task = asyncio.get_running_loop().create_task(
        _queue_new(settings, sha, owner), name="analyse klaarzetten"
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _queue_new(settings: Settings, sha: str, owner: uuid.UUID | None) -> None:
    try:
        async with _maker(settings)() as db:
            await queue(db, sha, list(_steps), owner=owner)
            await db.commit()
    except Exception:  # noqa: BLE001 - de backfill vindt hem later
        log.exception("analyse van %s klaarzetten mislukt", sha[:12])


async def start_backfill(settings: Settings) -> None:
    """search.hooks.backfill (bij het starten, na recover): de job analysis.backfill vanaf het
    begin. Staat er al een klaar of loopt er een (een herstart midden in de backfill), dan
    begint jobs.enqueue geen tweede."""
    if not _steps:
        return
    async with _maker(settings)() as db:
        await jobs.enqueue(db, BACKFILL, {"after": ""}, key=BACKFILL)
        await db.commit()


async def backfill_job(settings: Settings, payload: dict[str, Any]) -> None:
    """Eén stuk van de backfill: hooguit BACKFILL_CHUNK blobs (op sha, na payload["after"])
    zonder rij voor een van de aangemelde stappen. Elke stap krijgt een rij (wait, of skip als
    hij niet past), met als eigenaar die van de nieuwste levende node met die sha (anders de
    nieuwste in de prullenbak; een blob zonder node slaat hij over) en als queued_at het moment
    van die node: zo neemt de pomp binnen een eigenaar de nieuwste eerst. Eén korte transactie
    per stuk; daarna de pomp wekken en het volgende stuk klaarzetten."""
    after = str(payload.get("after") or "")
    steps = list(_steps)
    if not steps:
        return
    maker = _maker(settings)
    async with maker() as db:
        if not after:
            await _again_legacy(db)
        # Zonder rij voor minstens één stap (de primaire sleutel van analysis: (blob_sha, step))
        missing = or_(
            *(
                ~exists().where(Analysis.blob_sha == Blob.sha256, Analysis.step == step.value)
                for step in steps
            )
        )
        rows = (
            await db.execute(
                select(Blob.sha256, Blob.mime)
                .where(Blob.sha256 > after, missing)
                .order_by(Blob.sha256)
                .limit(BACKFILL_CHUNK)
            )
        ).all()
    if not rows:
        async with maker() as db:
            for lane in sorted({reg.lane for reg in _steps.values()}):
                await _chain_next(db, lane)
            await db.commit()
        return
    last = rows[-1].sha256
    for attempt in (1, 2):
        try:
            async with maker() as db:
                await _backfill_rows(db, steps, rows)
                if len(rows) == BACKFILL_CHUNK:  # een kleiner stuk was het laatste
                    await jobs.chain(db, BACKFILL, {"after": last}, key=BACKFILL)
                await db.commit()
            break
        except IntegrityError:
            # files.add_ref zette er intussen een klaar (queue): nog één keer, zonder die
            if attempt == 2:
                raise
            log.info("backfill: een inhoud kwam er tussendoor bij; opnieuw")
    jobs.kick(["index"])


async def _backfill_rows(db: AsyncSession, steps: list[Step], rows: list[Any]) -> None:
    shas = [row.sha256 for row in rows]
    owners: dict[str, tuple[int, datetime, uuid.UUID]] = {}
    for blob_id, owner_id, created_at, dead in await db.execute(
        select(Node.blob_id, Node.owner_id, Node.created_at, Node.dead).where(
            Node.blob_id.in_(shas)
        )
    ):
        rank = (0 if dead else 1, created_at)
        best = owners.get(blob_id)
        if best is None or rank > best[:2]:
            owners[blob_id] = (*rank, owner_id)
    have = {
        (sha, step)
        for sha, step in await db.execute(
            select(Analysis.blob_sha, Analysis.step).where(Analysis.blob_sha.in_(shas))
        )
    }
    values: list[dict[str, Any]] = []
    lanes: set[str] = set()
    at = now()
    for row in rows:
        found = owners.get(row.sha256)
        if found is None:
            continue  # geen node: de inhoud gaat weg (opruimen), niets te zoeken
        _alive, created, owner = found
        if created.tzinfo is None:
            created = created.replace(tzinfo=FRONT.tzinfo)
        for step in steps:
            if (row.sha256, step.value) in have:
                continue
            reg = _steps[step]
            state = "wait" if reg.eligible(row.sha256, row.mime) else "skip"
            if state == "wait":
                lanes.add(reg.lane)
            values.append(
                {
                    "blob_sha": row.sha256,
                    "step": step.value,
                    "version": VERSIONS[step],
                    "state": state,
                    "tries": 0,
                    "code": None,
                    "owner_id": owner,
                    "queued_at": min(max(created, FRONT_END), at),
                    "updated_at": at,
                }
            )
    if values:
        await db.execute(insert(Analysis), values)
    for lane in sorted(lanes):
        await _wake(db, lane, at)


async def _again_legacy(db: AsyncSession) -> None:
    """doc, xls en ppt die skip kregen voordat OnlyOffice er een PDF van maakte (de haak na de
    omzetting kwam niet, bijvoorbeeld omdat de thumbnails mislukten): nu opnieuw klaar."""
    from app.extract import LEGACY_MIMES

    if Step.TEXT not in _steps:
        return
    shas = (
        await db.scalars(
            select(Analysis.blob_sha)
            .join(Blob, Blob.sha256 == Analysis.blob_sha)
            .where(
                Analysis.step == Step.TEXT.value,
                Analysis.state == "skip",
                Blob.pdf_state == "ready",
                Blob.mime.in_(sorted(LEGACY_MIMES)),
            )
        )
    ).all()
    for sha in shas:
        await queue(db, sha, [Step.TEXT], owner=None, again=True)
    await db.commit()


# --- de voortgang (/search, /beheer) -------------------------------------------------------------

_indexing: dict[str, tuple[float, IndexProgress | None]] = {}


async def indexing(db: AsyncSession, step: Step = Step.TEXT) -> IndexProgress | None:
    """{"done": 0.43} voor /search zolang er minstens INDEXING_MIN inhouden van deze stap open
    staan (wait of busy); daarna None. done: het deel van de inhoud die meedoet (zonder skip)
    dat klaar is. Hooguit eens per INDEXING_TTL geteld (per database)."""
    from app.search.types import IndexProgress  # hier: types importeert de routers

    key = f"{db.bind.url}|{step.value}"
    cached = _indexing.get(key)
    moment = time.monotonic()
    if cached is not None and moment - cached[0] < INDEXING_TTL:
        return cached[1]
    found = await counts(db, step)
    open_ = sum(found.get(state, 0) for state in OPEN)
    total = sum(n for state, n in found.items() if state != "skip")
    result: IndexProgress | None = None
    if open_ >= INDEXING_MIN and total:
        result = IndexProgress(done=round(min(1.0, max(0.0, (total - open_) / total)), 4))
    _indexing[key] = (moment, result)
    return result


async def counts(db: AsyncSession, step: Step) -> dict[str, int]:
    """Hoeveel inhouden per state (voor /beheer en indexing)."""
    rows = await db.execute(
        select(Analysis.state, func.count())
        .where(Analysis.step == step.value)
        .group_by(Analysis.state)
    )
    return {state: n for state, n in rows.all()}


# --- de pomp -------------------------------------------------------------------------------------


def busy_elsewhere(settings: Settings) -> bool:
    """Keuze 50: wijken als er genoeg omzettingen lopen (media) of OnlyOffice start."""
    if settings.media_enabled:
        try:
            from app.media import admin as media_admin

            if media_admin.transcodes_running() >= settings.index_defer_transcodes:
                return True
        except Exception:  # noqa: BLE001 - de teller hoort de analyse nooit te breken
            log.debug("transcodes_running", exc_info=True)
    try:
        from app.media import admission

        return bool(admission._office_starting())
    except Exception:  # noqa: BLE001 - zie hierboven
        return False


def _pump_for(lane: str) -> Callable[[Settings, dict[str, Any]], Awaitable[None]]:
    async def run(settings: Settings, payload: dict[str, Any]) -> None:
        await pump(settings, lane)

    return run


async def pump(settings: Settings, lane: str) -> int:
    """Eén batch van een stap in deze rij met werk (om de beurt per stap); geeft het aantal
    inhouden. Zet daarna de volgende job klaar: nu als er werk klaarstaat, anders bij de eerste
    nieuwe poging. jobs.Defer als de app moet wijken; een batch gaat dan terug op wait."""
    if lane in PAUSED or busy_elsewhere(settings):
        raise jobs.Defer(DEFER_S)
    maker = _maker(settings)
    count = 0
    steps = sorted(
        (reg for reg in _steps.values() if reg.lane == lane),
        key=lambda reg: _step_served.get(reg.step, 0),
    )
    for reg in steps:
        size = max(1, reg.size(settings)) if reg.size is not None else 1
        async with maker() as db:
            shas = await pick(db, reg.step, size)
            if not shas:
                continue
            await db.execute(
                update(Analysis)
                .where(
                    Analysis.blob_sha.in_(shas),
                    Analysis.step == reg.step.value,
                    Analysis.state == "wait",
                )
                .values(state="busy", updated_at=now())
            )
            await db.commit()
        _step_served[reg.step] = next(_turn)
        try:
            outcomes = await _run(settings, reg, shas)
        except BaseException:
            # Defer, stoppen of de tijdgrens: de batch terug op wait, zonder poging
            with anyio.CancelScope(shield=True):
                await _back_to_wait(maker, reg.step, shas)
            raise
        await record(maker, reg.step, shas, outcomes)
        count = len(shas)
        break
    async with maker() as db:
        await _chain_next(db, lane)
        await db.commit()
    return count


async def _run(settings: Settings, reg: Registered, shas: list[str]) -> dict[str, Outcome]:
    if reg.batch is not None:
        try:
            return await reg.batch(settings, shas)
        except jobs.Defer:
            raise
        except Exception:  # noqa: BLE001 - de batch is mislukt: elke inhoud een poging
            log.exception("analyse %s: batch van %d mislukt", reg.step.value, len(shas))
            return {sha: Outcome("failed") for sha in shas}
    outcomes: dict[str, Outcome] = {}
    for sha in shas:
        try:
            outcomes[sha] = await reg.run(settings, sha) or Outcome("done")
        except jobs.Defer:
            raise
        except Exception:  # noqa: BLE001 - één inhoud stopt de rest niet
            log.exception("analyse %s van %s mislukt", reg.step.value, sha[:12])
            outcomes[sha] = Outcome("failed")
    return outcomes


async def pick(db: AsyncSession, step: Step, size: int) -> list[str]:
    """De volgende batch van deze stap (Keuze 20): eerst wat vooraan staat (first), dan om de
    beurt per eigenaar, hooguit FAIR_SHARE per eigenaar per ronde; wie het langst niet aan de
    beurt was, gaat voor; binnen een eigenaar de nieuwste eerst. Alleen wat nu aan de beurt is
    (een nieuwe poging met queued_at in de toekomst wacht)."""
    due = now()
    base = (Analysis.step == step.value, Analysis.state == "wait")
    picked: list[str] = list(
        (
            await db.scalars(
                select(Analysis.blob_sha)
                .where(*base, Analysis.queued_at < FRONT_END)
                .order_by(Analysis.updated_at.desc(), Analysis.blob_sha)
                .limit(size)
            )
        ).all()
    )
    if len(picked) >= size:
        return picked
    owners: list[uuid.UUID | None] = list(
        (
            await db.scalars(
                select(Analysis.owner_id)
                .where(*base, Analysis.queued_at >= FRONT_END, Analysis.queued_at <= due)
                .group_by(Analysis.owner_id)
            )
        ).all()
    )
    # Wie het langst niet aan de beurt was eerst (nooit: 0)
    owners.sort(key=lambda owner: (_served.get((step, owner), 0), str(owner)))
    waiting: dict[uuid.UUID | None, list[str]] = {}
    for owner in owners:
        if len(picked) >= size:
            break
        mine = Analysis.owner_id.is_(None) if owner is None else Analysis.owner_id == owner
        rows = (
            await db.scalars(
                select(Analysis.blob_sha)
                .where(*base, mine, Analysis.queued_at >= FRONT_END, Analysis.queued_at <= due)
                .order_by(Analysis.queued_at.desc(), Analysis.blob_sha)
                .limit(size)
            )
        ).all()
        rest = [sha for sha in rows if sha not in picked]
        take = rest[: min(FAIR_SHARE, size - len(picked))]
        picked += take
        waiting[owner] = rest[len(take) :]
    # Nog plek: nog een ronde, in dezelfde volgorde
    while len(picked) < size and any(waiting.values()):
        for rest in waiting.values():
            take = rest[: min(FAIR_SHARE, size - len(picked))]
            picked += take
            del rest[: len(take)]
            if len(picked) >= size:
                break
    for owner in waiting:
        _served[(step, owner)] = next(_turn)
    return picked


async def _back_to_wait(
    maker: async_sessionmaker[AsyncSession], step: Step, shas: Iterable[str]
) -> None:
    async with maker() as db:
        await db.execute(
            update(Analysis)
            .where(
                Analysis.blob_sha.in_(list(shas)),
                Analysis.step == step.value,
                Analysis.state == "busy",
            )
            .values(state="wait", updated_at=now())
        )
        await db.commit()


def retry_after(tries: int) -> timedelta:
    """De wachttijd na zoveel mislukte pogingen (1: RETRY_AFTER, dan steeds dubbel)."""
    return RETRY_AFTER * (2 ** max(0, tries - 1))


async def record(
    maker: async_sessionmaker[AsyncSession],
    step: Step,
    shas: list[str],
    outcomes: dict[str, Outcome],
) -> None:
    """De uitkomsten vastleggen, in één korte transactie. Alleen rijen die nog busy zijn: zette
    queue() er intussen een nieuwe versie klaar, dan blijft die staan."""
    async with maker() as db:
        tries = dict(
            (
                await db.execute(
                    select(Analysis.blob_sha, Analysis.tries).where(
                        Analysis.blob_sha.in_(shas), Analysis.step == step.value
                    )
                )
            ).all()
        )
        for sha in shas:
            out = outcomes.get(sha)
            values: dict[str, Any] = {"updated_at": now()}
            if out is None:
                values["state"] = "wait"  # geen uitkomst: later opnieuw, zonder poging
            elif out.state != "failed":
                values.update(state=out.state, code=out.code)
            else:
                n = tries.get(sha, 0) + 1
                values.update(tries=n, code=out.code)
                if out.retry and n < MAX_TRIES:
                    values.update(state="wait", queued_at=now() + retry_after(n))
                else:
                    values["state"] = "failed"
                    log.warning(
                        "analyse %s van %s mislukt (%s), poging %d",
                        step.value, sha[:12], out.code or "geen code", n,
                    )  # fmt: skip
            await db.execute(
                update(Analysis)
                .where(
                    Analysis.blob_sha == sha,
                    Analysis.step == step.value,
                    Analysis.state == "busy",
                )
                .values(values)
            )
        await db.commit()


async def _chain_next(db: AsyncSession, lane: str) -> None:
    """De volgende job van de pomp: nu als er werk klaarstaat, anders bij de eerste nieuwe
    poging. Geen werk: geen job."""
    steps = [reg.step.value for reg in _steps.values() if reg.lane == lane]
    if not steps:
        return
    first = await db.scalar(
        select(func.min(Analysis.queued_at)).where(
            Analysis.step.in_(steps), Analysis.state == "wait"
        )
    )
    if first is None:
        return
    if first.tzinfo is None:
        first = first.replace(tzinfo=FRONT.tzinfo)
    await _wake(db, lane, max(first, now()))


async def _wake(db: AsyncSession, lane: str, at: datetime) -> None:
    """De pomp van deze rij uiterlijk op at: een nieuwe job, of de job die al klaarstaat naar
    voren als die later stond (een nieuwe poging over vier minuten laat nieuw werk niet zo lang
    wachten). Eerst lezen, alleen schrijven als het moet. In de transactie van de aanroeper."""
    kind = pump_type(lane)
    waiting = (
        await db.execute(
            select(Job.id, Job.run_after)
            .where(Job.key == kind, Job.status == "queued")
            .order_by(Job.run_after)
            .limit(1)
        )
    ).first()
    if waiting is None:
        await jobs.chain(db, kind, {}, key=kind, run_after=at)
        return
    planned = waiting.run_after
    if planned.tzinfo is None:
        planned = planned.replace(tzinfo=FRONT.tzinfo)
    if planned > at:
        await db.execute(
            update(Job).where(Job.id == waiting.id, Job.status == "queued").values(run_after=at)
        )
        jobs.kick([lane])  # mag vóór de commit: wat de rij dan mist, ziet hij bij zijn rondje


# --- blob_text -----------------------------------------------------------------------------------


async def write_text(
    db: AsyncSession,
    sha: str,
    *,
    body: str | None = None,
    ocr: str | None = None,
    meta: str | None = None,
    truncated: bool = False,
) -> None:
    """Schrijft de tekst van een inhoud en sluit af met een commit: één korte transactie.

    None laat een veld zoals het is (een nieuwe rij krijgt dan ""). truncated hoort bij body:
    het telt alleen als body meekomt. Wat er nog in de sessie stond, gaat mee in dezelfde
    commit; roep hem dus aan met een sessie zonder ander werk.

    Schreef een ander dezelfde inhoud tussen de SELECT en de INSERT, dan geeft de INSERT een
    IntegrityError; dan nog één keer, en de SELECT vindt de rij.

    Elke node met deze sha kost de tekst nog eens (de triggers). Met veel kopieën gaat er dus
    minder tekst in: body en ocr samen hooguit TEXT_BUDGET gedeeld door het aantal nodes (en
    nooit minder dan TEXT_MIN_SHARE), body eerst. Een afgekapte body zet truncated. Komt alleen
    ocr of meta mee terwijl de opgeslagen body langer is dan wat nu past, dan gaat ook die body
    terug tot dat deel: de volgende schrijfactie kost dan weer weinig.
    """
    copies = await db.scalar(select(func.count()).select_from(Node).where(Node.blob_id == sha))
    share = max(TEXT_MIN_SHARE, TEXT_BUDGET // max(1, copies or 0))
    if body is not None and len(body) > share:
        body, truncated = body[:share], True
    for attempt in (1, 2):
        exists = await db.scalar(select(BlobText.blob_sha).where(BlobText.blob_sha == sha))
        try:
            if exists is None:
                rest = max(TEXT_MIN_SHARE, share - len(body or ""))
                await db.execute(
                    insert(BlobText).values(
                        blob_sha=sha,
                        body=body or "",
                        ocr=(ocr or "")[:rest],
                        meta=meta or "",
                        truncated=int(bool(truncated)) if body is not None else 0,
                        updated_at=now(),
                    )
                )
            else:
                values: dict[str, object] = {"updated_at": now()}
                stored = (
                    await db.execute(
                        select(func.length(BlobText.body), func.length(BlobText.ocr)).where(
                            BlobText.blob_sha == sha
                        )
                    )
                ).one()
                if body is not None:
                    values["body"] = body
                    values["truncated"] = int(bool(truncated))
                    kept = len(body)
                else:
                    if stored[0] > share:
                        values["body"] = func.substr(BlobText.body, 1, share)
                        values["truncated"] = 1
                    kept = min(stored[0], share)
                rest = max(TEXT_MIN_SHARE, share - kept)
                if ocr is not None:
                    values["ocr"] = ocr[:rest]
                elif stored[1] > rest:
                    values["ocr"] = func.substr(BlobText.ocr, 1, rest)
                if meta is not None:
                    values["meta"] = meta
                await db.execute(update(BlobText).where(BlobText.blob_sha == sha).values(values))
            await db.commit()
            return
        except IntegrityError:
            await db.rollback()
            if attempt == 2:
                raise
            log.info("blob_text voor %s kwam er tussendoor bij; opnieuw", sha[:12])
