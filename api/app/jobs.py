"""Achtergrondwerk via de tabel job, zodat het een herstart overleeft.

    await jobs.enqueue(session, "preview", {"sha": sha}, key=f"preview:{sha}")
    await session.commit()  # pas nu worden de workers gewekt

Workers (settings.job_workers stuks) draaien als asyncio-taken in het proces van de app. Een
taak wordt door precies één worker opgepakt: een voorwaardelijke UPDATE (status queued ->
running) die maar één keer slaagt. Mislukt hij, dan gaat hij later opnieuw (na 30 s, daarna
steeds dubbel zo lang), tot max_attempts keer; daarna staat hij op failed en is hij op
/beheer te zien en opnieuw te starten. De uitkomst landt alleen op de claim die de taak
draaide (zijn started_at): een taak die zichzelf al terug in de rij zette en opnieuw is
opgepakt, blijft van die nieuwe claim.

attempts telt hoe vaak een taak is begonnen. Stopt het proces midden in een taak (een crash),
dan gaat hij bij het starten terug in de rij, en telt die poging mee: een bestand dat het
proces laat crashen, blijft zo niet eindeloos terugkomen.

Stoppen (Runner.stop) wint altijd. Een lopende taak gaat terug in de rij zonder dat de poging
telt. Lukt dat terugzetten niet (database is locked), dan blijft hij op running en zet recover()
hem bij de volgende start terug, zoals na een crash. Een fout bij het opruimen vervangt het
stopsein niet, en een worker die het sein kreeg, pakt niets meer op; het opruimwerk evenmin
(fase-5/m0.md, punt 12).

Een taak doet zijn zware rekenwerk in een thread of eigen proces, nooit op de event loop.

Rijen (fase 5, Keuze 14). Elk soort hoort bij één rij (register(..., lane=...)), en elke rij
heeft een eigen Runner met eigen workers: default (settings.job_workers, alles van fase 3 en
4), media_scan (1), media (settings.media_job_workers), media_net (1) en import (1). Een
Runner pakt alleen soorten uit zijn eigen rij; default pakt alles wat niet in een andere rij
staat, dus ook een onbekend soort (dat faalt dan zoals altijd). Zo houdt een lange analyse een
preview niet op, en een rij vol analyses een scan niet. Het opruimwerk (housekeeping) draait
alleen in de Runner van default: één keer per periode, hoeveel Runners er ook zijn.

Een taak die nu niet kan (de mediaworker is druk of weg, een provider remt af) raist
Defer(seconds): terug in de rij na zoveel seconden, zonder dat de poging telt. timeout per soort
vervangt settings.job_timeout.

De rij preview (previews) heeft settings.preview_workers workers (0: een minder dan het aantal
kernen) en draait ook zonder media. Binnen een rij gaat een soort met een lagere rank voor:
preview.large (rank 1) pas als er geen thumbnails (rank 0) meer klaarstaan. Wat iemand nu
bekijkt, haalt to_front naar voren, en dat gaat voor alles, ook voor een lagere rank.
"""

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from sqlalchemy import and_, bindparam, case, delete, event, false, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session as OrmSession

from app.config import Settings
from app.db import get_engine
from app.models import Job

log = logging.getLogger("platform.jobs")

STATES = ("queued", "running", "done", "failed")
ACTIVE = ("queued", "running")
MAX_ATTEMPTS = 3
BACKOFF = timedelta(seconds=30)  # na de eerste fout; daarna steeds dubbel zo lang
MAX_BACKOFF = timedelta(hours=6)
POLL = 5.0  # seconden: ook zonder seintje kijken de workers zo vaak of er iets klaarstaat
KEEP_DONE = timedelta(days=7)
HOUSEKEEPING = 3600.0
ERROR_CHARS = 2000
_KICK = "platform.jobs.kick"  # in session.info: welke rijen na de commit gewekt worden
DEFAULT_LANE = "default"
# De rijen van Keuze 14 (fase 5), preview, en index, ocr en analyse van fase 6, in de volgorde
# waarin de lifespan ze start
LANES = (
    "default",
    "preview",
    "index",
    "media_scan",
    "media",
    "media_net",
    "import",
    "ocr",
    "analyse",
)
# De rijen die ook zonder media draaien: index (zoeken, fase 6) hoort daarbij, want tekst lezen
# gaat buiten de zandbak als er geen mediaworker is
BASE_LANES = ("default", "preview", "index")
# De rijen van media (fase 5)
MEDIA_LANES = ("media_scan", "media", "media_net", "import")
# Wat iemand nu bekijkt (to_front): run_after vóór FRONT gaat voor alles wat gewoon klaarstaat
FRONT = datetime(2000, 1, 1, tzinfo=UTC)
# Een taak die minder dan zoveel seconden geleden naar voren ging, blijft staan: de tijdlijn die
# dezelfde foto's opnieuw laadt, schrijft dan niets (gemeten tijdens een import: GET /photos p50
# 58 → 127 ms toen elke keer alle 500 opnieuw naar voren gingen)
FRONT_AGAIN = 10


class JobError(Exception):
    """Een fout met een melding die zo op /beheer kan (zonder het soort fout ervoor)."""


Run = Callable[[Settings, dict[str, Any]], Awaitable[None]]
OnDead = Callable[[Settings, dict[str, Any], str], Awaitable[None]]


class Defer(Exception):
    """Terug in de rij na `seconds`; de poging telt niet. Voor: de mediaworker is druk of weg,
    een provider remt af, er is geen netwerk (fase 5, Keuze 14)."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"opnieuw over {seconds:g} s")
        self.seconds = seconds


@dataclass
class Kind:
    run: Run
    on_dead: OnDead | None = None
    max_attempts: int = MAX_ATTEMPTS
    # De rij en de tijdgrens van dit soort (fase 5, Keuze 14). Leeg: de rij default en
    # settings.job_timeout, zoals in fase 3 en 4.
    lane: str = "default"
    timeout: float | None = None
    # Lager gaat voor binnen de rij: rank 1 pas als er van rank 0 niets klaarstaat
    rank: int = 0


@dataclass
class Claimed:
    id: uuid.UUID
    type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    # De started_at van deze claim: wat execute na de taak schrijft, raakt alleen deze claim
    started_at: datetime


_kinds: dict[str, Kind] = {}
# Eén per draaiende Runner, met zijn rij; kick() wekt die van de rijen waar iets bij kwam
_wakers: dict[Callable[[], None], str] = {}
# De Runners die nu draaien (voor runner_alive op /beheer)
_runners: set["Runner"] = set()

Hook = Callable[[Settings], Awaitable[Any]]


@dataclass
class Housekeeping:
    """Werk dat de Runner om de zoveel seconden doet, naast de taken: opruimen, de cache van
    het schijfgebruik verversen, de prullenbak legen na de bewaartermijn."""

    name: str
    run: Hook
    every: float


_housekeeping: dict[str, Housekeeping] = {}
# Zo vaak (seconden) kijkt de Runner of er opruimwerk aan de beurt is
HOUSEKEEPING_TICK = 60.0


def housekeeping(name: str, run: Hook, every: float) -> None:
    """Registreert opruimwerk: run(settings) eens per `every` seconden (de eerste keer kort na
    het starten). Een fout wordt gelogd en stopt niets; de volgende keer gewoon opnieuw.
    Opnieuw registreren met dezelfde naam vervangt het vorige."""
    _housekeeping[name] = Housekeeping(name, run, every)


def forget_housekeeping(name: str) -> None:
    """Haalt opruimwerk weer weg (media bij het stoppen: daarna is de app weer die van 0.6.0)."""
    _housekeeping.pop(name, None)


def register(
    type: str,
    run: Run,
    on_dead: OnDead | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    *,
    lane: str = "default",
    timeout: float | None = None,
    rank: int = 0,
) -> None:
    """Wat er gebeurt bij een taak van dit soort; on_dead als hij definitief mislukt is.
    lane: de rij met een eigen Runner; timeout: in plaats van settings.job_timeout (Keuze 14
    van fase 5); rank: lager gaat voor binnen de rij."""
    if lane not in LANES:
        raise ValueError(f"onbekende rij {lane!r} (bekend: {', '.join(LANES)})")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout moet groter dan 0 zijn")
    if rank < 0:
        raise ValueError("rank kan niet negatief zijn")
    _kinds[type] = Kind(run, on_dead, max_attempts, lane, timeout, rank)


def lane_of(type: str) -> str:
    """De rij van een soort; een onbekend soort hoort bij default."""
    kind = _kinds.get(type)
    return kind.lane if kind is not None else DEFAULT_LANE


def lane_workers(settings: Settings, lane: str) -> int:
    """Hoeveel workers een rij heeft (Keuze 14)."""
    if lane == DEFAULT_LANE:
        return max(1, settings.job_workers)
    if lane == "media":
        return max(1, settings.media_job_workers)
    if lane == "preview":
        if settings.preview_workers > 0:
            return settings.preview_workers
        return max(1, len(os.sched_getaffinity(0)) - 1)
    return 1


def _lane_filter(lane: str | None):
    """De voorwaarde op Job.type voor claim in deze rij; None: alle soorten (tests). Een rij
    zonder aangemelde soorten geeft False: dan hoeft claim de database niet in."""
    if lane is None:
        return true()
    if lane == DEFAULT_LANE:
        elsewhere = sorted(t for t, k in _kinds.items() if k.lane != DEFAULT_LANE)
        return Job.type.not_in(elsewhere) if elsewhere else true()
    mine = sorted(t for t, k in _kinds.items() if k.lane == lane)
    return Job.type.in_(mine) if mine else false()


def _fronted_filter(lane: str | None):
    """Wat iemand bekijkt (to_front: run_after vóór FRONT) gaat voor, van elke rank: de viewer
    wacht op de grote versie van één foto (preview.large, rank 1), niet tot alle thumbnails van
    een import klaar zijn (review F3). Alleen in een rij met ranks (zonder staat het naar voren
    gehaalde al vooraan); anders None. Met run_after < FRONT als enige bovengrens: met ook
    run_after <= nu erbij las SQLite alle wachtende previews (gemeten met 50.000 in de rij: een
    claim 25 ms; zo 8 ms, zonder deze query 7 ms)."""
    if not any(k.rank and (lane is None or k.lane == lane) for k in _kinds.values()):
        return None
    return and_(_lane_filter(lane), Job.run_after < FRONT)


def _claim_filters(lane: str | None) -> list:
    """De voorwaarden voor claim, de laagste rank eerst (zie Kind.rank); _fronted_filter gaat
    daar nog voor."""
    base = _lane_filter(lane)
    ranked = {t: k.rank for t, k in _kinds.items() if k.rank and (lane is None or k.lane == lane)}
    if not ranked:
        return [base]
    if lane is None or lane == DEFAULT_LANE:
        filters = [and_(base, Job.type.not_in(sorted(ranked)))]
    else:
        # De soorten zelf, niet "de rij min de rest": met één soort leest de database de index
        # op volgorde (ix_job_queue) en hoeft hij bij een grote rij niets te sorteren
        first = sorted(t for t, k in _kinds.items() if k.lane == lane and not k.rank)
        filters = [Job.type.in_(first)] if first else []
    for rank in sorted(set(ranked.values())):
        filters.append(Job.type.in_(sorted(t for t, r in ranked.items() if r == rank)))
    return filters


def now() -> datetime:
    return datetime.now(UTC)


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def enqueue(
    session: AsyncSession,
    type: str,
    payload: dict[str, Any],
    key: str | None = None,
    *,
    run_after: datetime | None = None,
) -> uuid.UUID | None:
    """Zet een taak klaar in de transactie van de aanroeper en geeft zijn id. De workers
    worden gewekt zodra die transactie commit. Staat er al een taak met deze key klaar of loopt
    hij nog, dan komt er geen tweede bij (None). run_after: niet eerder dan dan (standaard
    meteen)."""
    return await _add(session, type, payload, key, run_after, ACTIVE)


async def chain(
    session: AsyncSession,
    type: str,
    payload: dict[str, Any],
    key: str,
    *,
    run_after: datetime | None = None,
) -> uuid.UUID | None:
    """Voor een taak die loopt en zijn opvolger klaarzet (een keten die in stukken door een
    grote tabel gaat, met payload {"after": ...}): als enqueue, maar alleen ontdubbeld tegen
    een taak met deze key die nog klaarstaat. De lopende taak zelf (status running, dezelfde
    key) houdt de opvolger dus niet tegen; een tweede keten ernaast wel, omdat enqueue met
    de key ook een lopende taak telt."""
    return await _add(session, type, payload, key, run_after, ("queued",))


async def _add(
    session: AsyncSession,
    type: str,
    payload: dict[str, Any],
    key: str | None,
    run_after: datetime | None,
    blocking: tuple[str, ...],
) -> uuid.UUID | None:
    if key is not None:
        existing = await session.scalar(
            select(Job.id).where(Job.key == key, Job.status.in_(blocking)).limit(1)
        )
        if existing is not None:
            return None
    kind = _kinds.get(type)
    job = Job(
        id=uuid.uuid4(),
        type=type,
        key=key,
        payload=payload,
        status="queued",
        attempts=0,
        max_attempts=kind.max_attempts if kind else MAX_ATTEMPTS,
        run_after=run_after or now(),
    )
    session.add(job)
    _kick_after_commit(session, lane_of(type))
    return job.id


async def enqueue_many(
    session: AsyncSession,
    type: str,
    items: list[tuple[dict[str, Any], str]],
    *,
    run_after: datetime | None = None,
) -> int:
    """Als enqueue, voor veel taken (payload, key) tegelijk: één query voor de controle op
    dubbele. Geeft het aantal nieuwe taken."""
    keys = [key for _, key in items]
    active = set(
        (
            await session.scalars(select(Job.key).where(Job.key.in_(keys), Job.status.in_(ACTIVE)))
        ).all()
    )
    kind = _kinds.get(type)
    added = 0
    for payload, key in items:
        if key in active:
            continue
        active.add(key)
        session.add(
            Job(
                id=uuid.uuid4(),
                type=type,
                key=key,
                payload=payload,
                status="queued",
                attempts=0,
                max_attempts=kind.max_attempts if kind else MAX_ATTEMPTS,
                run_after=run_after or now(),
            )
        )
        added += 1
    if added:
        _kick_after_commit(session, lane_of(type))
    return added


async def to_front(session: AsyncSession, keys: Sequence[str]) -> int:
    """Taken met deze keys die nu klaarstaan, naar voren: vóór alles wat gewoon wacht, en het
    laatst naar voren gehaalde eerst (run_after net zo ver vóór FRONT als nu erna is). Binnen
    één keer in de volgorde van keys (een microseconde per plek): de tijdlijn haalt een hele
    pagina van 500 naar voren, en wat bovenaan staat (wat iemand ziet) gaat dan als eerste.
    Eerder kregen ze allemaal dezelfde plek en ging de oudste taak voor; bij een import staan de
    nieuwste foto's bovenaan, en die kwamen zo als laatste. Een taak die bewust later moet
    (Defer, na een fout, opnieuw maken na een versiewissel), blijft staan, net als een die in de
    laatste FRONT_AGAIN seconden al naar voren ging. Eerst lezen, dan alleen schrijven als er
    iets te verschuiven is: een UPDATE neemt in SQLite het schrijfslot, ook als hij niets raakt.
    In de transactie van de aanroeper; geeft het aantal."""
    at = now()
    front = FRONT - (at - FRONT)
    # Wie na dit punt staat, ging nooit naar voren of al langer dan FRONT_AGAIN geleden
    since = front + timedelta(seconds=FRONT_AGAIN)
    moved = 0
    keys = list(dict.fromkeys(keys))
    place = {k: i for i, k in enumerate(keys)}
    table = Job.__table__
    move = (
        update(table)
        .where(table.c.id == bindparam("job"), table.c.status == "queued")
        .values(run_after=bindparam("at"))
    )
    for i in range(0, len(keys), 500):
        rows = (
            await session.execute(
                select(Job.id, Job.key, Job.type).where(
                    Job.key.in_(keys[i : i + 500]),
                    Job.status == "queued",
                    Job.run_after <= at,
                    Job.run_after >= since,
                )
            )
        ).all()
        if not rows:
            continue
        spots = [
            {"job": row.id, "at": front - timedelta(microseconds=len(keys) - place[row.key])}
            for row in rows
        ]
        await session.execute(move, spots)
        for row in rows:
            _kick_after_commit(session, lane_of(row.type))
            moved += 1
    return moved


def _kick_after_commit(session: AsyncSession, lane: str | None = None) -> None:
    """Na de commit de workers van deze rij wekken (None: alle rijen)."""
    session.info.setdefault(_KICK, set()).add(lane)


def kick(lanes: Iterable[str | None] | None = None) -> None:
    """Wekt de workers van deze rijen (None: allemaal). Alleen die rijen: elke claim leest de
    database, en een rij die toch niets kan oppakken (default tijdens een import van foto's)
    hoeft dat niet bij elke nieuwe taak van een andere rij te doen. Mag ook te vroeg (vóór de
    commit): wat ze dan nog niet zien, vinden ze bij het volgende rondje (hooguit POLL
    seconden later)."""
    wanted = None if lanes is None else set(lanes)
    for wake, lane in list(_wakers.items()):
        if wanted is None or None in wanted or lane in wanted:
            wake()


@event.listens_for(OrmSession, "after_commit")
def _after_commit(session: OrmSession) -> None:
    lanes = session.info.pop(_KICK, None)
    if lanes:
        kick(lanes)


@event.listens_for(OrmSession, "after_rollback")
def _after_rollback(session: OrmSession) -> None:
    session.info.pop(_KICK, None)


def backoff(attempts: int) -> timedelta:
    return min(BACKOFF * 2 ** max(0, attempts - 1), MAX_BACKOFF)


def describe(error: BaseException) -> str:
    if isinstance(error, JobError):
        text = str(error)
    else:
        text = f"{type(error).__name__}: {error}".rstrip(": ")
    return text[:ERROR_CHARS]


async def claim(
    settings: Settings, at: datetime | None = None, lane: str | None = None
) -> Claimed | None:
    """Pakt de eerstvolgende taak die klaarstaat, alleen uit deze rij (None: uit alle rijen,
    voor tests). Twee workers die tegelijk dezelfde kiezen: de UPDATE slaagt maar voor één, de
    ander probeert de volgende."""
    at = at or now()
    if (
        lane is not None
        and lane != DEFAULT_LANE
        and not any(k.lane == lane for k in _kinds.values())
    ):
        return None  # niets aangemeld in deze rij: de database hoeft niet in
    fronted = _fronted_filter(lane)
    wheres = [] if fronted is None else [(fronted,)]
    wheres += [(Job.run_after <= at, f) for f in _claim_filters(lane)]
    async with _maker(settings)() as session:
        candidates: Sequence[uuid.UUID] = []
        for where in wheres:
            candidates = (
                await session.scalars(
                    select(Job.id)
                    .where(Job.status == "queued", *where)
                    .order_by(Job.run_after, Job.created_at)
                    .limit(10)
                )
            ).all()
            if candidates:
                break
        for job_id in candidates:
            started = now()
            row = (
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "queued")
                    .values(status="running", started_at=started, attempts=Job.attempts + 1)
                    .returning(Job.type, Job.payload, Job.attempts, Job.max_attempts)
                    .execution_options(synchronize_session=False)
                )
            ).first()
            if row is not None:
                await session.commit()
                return Claimed(
                    job_id, row.type, row.payload or {}, row.attempts, row.max_attempts, started
                )
        await session.commit()
    return None


async def execute(settings: Settings, job: Claimed) -> None:
    """Draait een opgepakte taak en legt de uitkomst vast. Bij stoppen (cancel) gaat de
    taak terug in de rij, zonder dat die poging meetelt.

    De uitkomst (done, failed, terug in de rij) landt alleen op deze claim: de taak loopt nog,
    met de started_at van deze claim (_set). Een taak die zichzelf terug in de rij zet
    (office.convert: wachten zonder poging), kan voor dat schrijven al opnieuw zijn opgepakt,
    bijvoorbeeld na een verzoek dat hem naar voren haalde. Die nieuwe claim blijft dan van de
    worker die hem heeft. Eerst zette de done van de oude claim ook de nieuwe op done, en bleef
    de inhoud pending zonder taak (o3.md afwijking 22). on_dead alleen als failed landde."""
    kind = _kinds.get(job.type)
    scope: anyio.CancelScope | None = None
    limit = settings.job_timeout
    if kind is not None and kind.timeout is not None:
        limit = kind.timeout
    try:
        if kind is None:
            raise JobError(f"Onbekend soort taak: {job.type}")
        with anyio.fail_after(limit) as scope:
            await kind.run(settings, job.payload)
    except asyncio.CancelledError:
        await _stopped(settings, job)
        raise
    except Defer as e:
        # Nu niet, straks wel: terug in de rij, en deze poging telt niet
        log.info("Achtergrondtaak %s (%s) wacht %g s: %s", job.id, job.type, e.seconds, e)
        with anyio.CancelScope(shield=True):
            await _requeue(settings, job, after=e.seconds)
        return
    except Exception as e:  # noqa: BLE001 - elke fout telt als mislukte poging
        if stopping():
            # Het stopsein, vervangen door een fout bij het opruimen van de taak zelf (zoals
            # database is locked in analysis._back_to_wait): stoppen, geen mislukte poging
            await _stopped(settings, job)
            raise asyncio.CancelledError from e
        if isinstance(e, TimeoutError) and scope is not None and scope.cancel_called:
            error = f"Duurde langer dan {limit:g} seconden"
        else:
            error = describe(e)
        final = kind is None or job.attempts >= job.max_attempts
        log.warning(
            "Achtergrondtaak %s (%s) mislukt, poging %d van %d: %s",
            job.id,
            job.type,
            job.attempts,
            job.max_attempts,
            error,
            exc_info=not isinstance(e, JobError | TimeoutError),
        )
        with anyio.CancelScope(shield=True):
            landed = await _failed(settings, job, error, final)
            if landed and final and kind is not None and kind.on_dead is not None:
                await _dead(settings, kind, job.payload, error)
        return
    with anyio.CancelScope(shield=True):
        await _set(settings, job, status="done", finished_at=now(), error=None)


def stopping() -> bool:
    """Kreeg deze taak het stopsein (task.cancel() van Runner.stop)? Ook als een fout bij het
    opruimen de CancelledError verving: cancelling() telt elke cancel() die niemand terugnam
    (de eigen tijdgrenzen van anyio en asyncio nemen de hunne terug)."""
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


async def _stopped(settings: Settings, job: Claimed) -> None:
    """Bij stoppen terug in de rij, zonder dat de poging telt, hooguit 10 s. Nooit een fout
    hieruit: die verving het stopsein, de worker draaide door en Runner.stop wachtte voor altijd
    (het harnas van de e2e doodde de app na 30 s). Lukt het niet, dan blijft de taak op running
    met de started_at van deze claim, en zet recover() hem bij de volgende start terug."""
    with anyio.CancelScope(shield=True), anyio.move_on_after(10):
        try:
            await _requeue(settings, job)
        except Exception:  # noqa: BLE001 - zie hierboven
            log.warning(
                "Achtergrondtaak %s (%s) bij het stoppen niet terug in de rij; hij blijft op "
                "running tot de volgende start",
                job.id,
                job.type,
                exc_info=True,
            )


async def _set(settings: Settings, job: Claimed, **values: Any) -> bool:
    """De uitkomst van deze claim (zie execute). True als hij landde."""
    async with _maker(settings)() as session:
        result = await session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "running", Job.started_at == job.started_at)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return result.rowcount == 1


async def _requeue(settings: Settings, job: Claimed, after: float = 0) -> bool:
    """Terug in de rij zonder dat deze poging telt (stoppen, of Defer na `after` s)."""
    return await _set(
        settings,
        job,
        status="queued",
        started_at=None,
        attempts=Job.attempts - 1,
        run_after=now() + timedelta(seconds=max(0.0, after)),
    )


async def _failed(settings: Settings, job: Claimed, error: str, final: bool) -> bool:
    if final:
        return await _set(settings, job, status="failed", finished_at=now(), error=error)
    return await _set(
        settings,
        job,
        status="queued",
        started_at=None,
        run_after=now() + backoff(job.attempts),
        error=error,
    )


async def _dead(settings: Settings, kind: Kind, payload: dict[str, Any], error: str) -> None:
    try:
        await kind.on_dead(settings, payload, error)  # type: ignore[misc]
    except Exception:  # noqa: BLE001 - de taak staat al op failed; dit is bijzaak
        log.exception("Afhandelen van een definitief mislukte taak ging mis")


async def run_one(settings: Settings, at: datetime | None = None, lane: str | None = None) -> bool:
    job = await claim(settings, at, lane)
    if job is None:
        return False
    await execute(settings, job)
    return True


async def run_pending(
    settings: Settings, at: datetime | None = None, lane: str | None = None
) -> int:
    """Voor tests: draait wat klaarstaat (op tijdstip `at`; lane None: uit alle rijen) tot er
    niets meer klaarstaat, zonder de achtergrondlus. Geeft het aantal gedraaide taken."""
    count = 0
    while await run_one(settings, at, lane):
        count += 1
    return count


async def recover(settings: Settings) -> int:
    """Bij het starten: wat nog op running staat, liep toen het proces stopte. Dat gaat terug
    in de rij, of op failed als het al max_attempts keer begonnen is."""
    dead: list[tuple[Kind, dict[str, Any], str]] = []
    async with _maker(settings)() as session:
        stuck = (await session.scalars(select(Job).where(Job.status == "running"))).all()
        for job in stuck:
            if job.attempts >= job.max_attempts:
                job.status, job.finished_at = "failed", now()
                job.error = "Het proces stopte steeds tijdens deze taak"
                kind = _kinds.get(job.type)
                if kind is not None and kind.on_dead is not None:
                    dead.append((kind, job.payload, job.error))
            else:
                job.status, job.started_at, job.run_after = "queued", None, now()
        await session.commit()
    for kind, payload, error in dead:
        await _dead(settings, kind, payload, error)
    return len(stuck)


async def remove_old(settings: Settings) -> int:
    """Afgeronde taken van meer dan KEEP_DONE geleden weg; mislukte blijven staan."""
    async with _maker(settings)() as session:
        result = await session.execute(
            delete(Job).where(Job.status == "done", Job.finished_at < now() - KEEP_DONE)
        )
        await session.commit()
        return result.rowcount or 0


class Runner:
    """De workers van één rij, en (alleen in default) het opruimen. Gestart en gestopt door
    de lifespan van de app (start_runners)."""

    def __init__(
        self, settings: Settings, lane: str = DEFAULT_LANE, housekeeping: bool | None = None
    ):
        if lane not in LANES:
            raise ValueError(f"onbekende rij {lane!r}")
        self.settings = settings
        self.lane = lane
        # Het opruimwerk één keer per proces: in de Runner van default
        self.housekeeping = lane == DEFAULT_LANE if housekeeping is None else housekeeping
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []

    def wake(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._wake.set)

    async def start(self) -> None:
        """Start de workers. Roep eerst recover() aan (bij het starten van de app)."""
        self._loop = asyncio.get_running_loop()
        _wakers[self.wake] = self.lane
        _runners.add(self)
        for i in range(lane_workers(self.settings, self.lane)):
            name = f"achtergrondtaken {self.lane} {i + 1}"
            self._tasks.append(asyncio.create_task(self._work(), name=name))
        if self.housekeeping:
            self._tasks.append(asyncio.create_task(self._housekeeping(), name="taken opruimen"))

    async def stop(self) -> None:
        """Lopende taken worden afgebroken en gaan terug in de rij."""
        _wakers.pop(self.wake, None)
        _runners.discard(self)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    @property
    def alive(self) -> bool:
        """Draaien de workers nog (geen enkele gestopt door een fout)?"""
        return bool(self._tasks) and not any(task.done() for task in self._tasks)

    async def _work(self) -> None:
        while True:
            if stopping():
                # stop() gaf het sein, maar een taak slikte de CancelledError: niets meer
                # oppakken, anders wachtte stop() voor altijd op deze worker
                raise asyncio.CancelledError
            self._wake.clear()
            try:
                job = await claim(self.settings, lane=self.lane)
                if job is not None:
                    self.wake()  # misschien staat er meer klaar: dan pakt een andere worker die
                    await execute(self.settings, job)
                    continue
            except Exception:  # noqa: BLE001 - bijv. de database even weg; zo meteen opnieuw
                log.exception("Achtergrondtaken: oppakken of afronden mislukt")
            with anyio.move_on_after(POLL):
                await self._wake.wait()

    async def _housekeeping(self) -> None:
        last: dict[str, float] = {}
        while True:
            await run_housekeeping(self.settings, last)
            await asyncio.sleep(HOUSEKEEPING_TICK)


def active_lanes(settings: Settings) -> tuple[str, ...]:
    """De rijen die draaien (fase 6, §3.3). default, preview en index altijd; de rijen van
    media alleen met PLATFORM_MEDIA_ENABLED, zoals in 0.6.0 (toen zat preview in default); ocr
    alleen met media en PLATFORM_OCR_ENABLED, want OCR draait in de zandbak van de mediaworker;
    analyse (gezichten X2, beeld X3) alleen met PLATFORM_ANALYSE_ENABLED, want hij praat met de
    container analyse (Keuze 59), los van media. Een taak van een rij die niet draait, blijft
    staan tot die rij aan gaat (default pakt hem niet)."""
    return (
        BASE_LANES
        + (MEDIA_LANES if settings.media_enabled else ())
        + (("ocr",) if settings.media_enabled and settings.ocr_enabled else ())
        + (("analyse",) if settings.analyse_enabled else ())
    )


async def start_runners(settings: Settings, lanes: Sequence[str] | None = None) -> list[Runner]:
    """Een Runner per rij (active_lanes), het opruimwerk alleen in default. Roep eerst
    recover() aan."""
    runners = [Runner(settings, lane=lane) for lane in (lanes or active_lanes(settings))]
    for runner in runners:
        await runner.start()
    return runners


async def stop_runners(runners: list[Runner]) -> None:
    await asyncio.gather(*(runner.stop() for runner in runners))


async def _remove_old(settings: Settings) -> None:
    if removed := await remove_old(settings):
        log.info("%s oude achtergrondtaken opgeruimd", removed)


housekeeping("jobs.remove_old", _remove_old, HOUSEKEEPING)


async def run_housekeeping(settings: Settings, last: dict[str, float] | None = None) -> list[str]:
    """Draait het opruimwerk dat aan de beurt is (alles als last None is: voor tests). last:
    per naam wanneer (time.monotonic) het voor het laatst liep; wordt bijgewerkt. Geeft de
    namen van wat er liep."""
    ran = []
    clock = asyncio.get_running_loop().time()
    for item in list(_housekeeping.values()):
        if last is not None and item.name in last and clock - last[item.name] < item.every:
            continue
        if last is not None:
            last[item.name] = clock
        try:
            await item.run(settings)
        except Exception:  # noqa: BLE001 - opruimen mag de app nooit stoppen
            log.exception("Opruimwerk %s mislukt", item.name)
        ran.append(item.name)
        if stopping():
            # Het stopsein, geslikt door dit opruimwerk (of vervangen door een fout bij zijn
            # opruimen): verder niets, anders wachtte Runner.stop tot na HOUSEKEEPING_TICK
            raise asyncio.CancelledError
    return ran


def runner_alive() -> bool:
    """Draait er in dit proces een Runner met levende workers?"""
    return any(runner.alive for runner in _runners)


async def counts(session: AsyncSession) -> dict[str, int]:
    rows = (await session.execute(select(Job.status, func.count()).group_by(Job.status))).all()
    result = dict.fromkeys(STATES, 0)
    result.update({status: count for status, count in rows if status in result})
    return result


async def failed(
    session: AsyncSession, limit: int = 100, before: tuple[datetime, uuid.UUID] | None = None
) -> list[Job]:
    """Mislukte taken, de laatst mislukte eerst. before: (finished_at, id) van de laatste van
    de vorige pagina; dan alleen wat daarna komt."""
    query = select(Job).where(Job.status == "failed")
    if before is not None:
        at, job_id = before
        query = query.where(or_(Job.finished_at < at, and_(Job.finished_at == at, Job.id < job_id)))
    return list(
        (
            await session.scalars(
                query.order_by(Job.finished_at.desc(), Job.id.desc()).limit(limit)
            )
        ).all()
    )


async def counts_by_type(session: AsyncSession) -> list[tuple[str, str, int]]:
    """(soort, status, aantal) voor elke combinatie die voorkomt, in één query."""
    rows = await session.execute(
        select(Job.type, Job.status, func.count())
        .group_by(Job.type, Job.status)
        .order_by(Job.type, Job.status)
    )
    return [(type_, status, count) for type_, status, count in rows]


async def running(session: AsyncSession, limit: int = 50) -> list[Job]:
    """Wat nu loopt, het langst lopende eerst."""
    return list(
        (
            await session.scalars(
                select(Job)
                .where(Job.status == "running")
                .order_by(Job.started_at, Job.id)
                .limit(limit)
            )
        ).all()
    )


def _active_filter(settings: Settings | None):
    """De soorten met een Runner (active_lanes); None: alle soorten."""
    if settings is None:
        return true()
    lanes = active_lanes(settings)
    if set(lanes) >= set(LANES):
        return true()
    return or_(*(_lane_filter(lane) for lane in lanes))


async def oldest_queued(session: AsyncSession, settings: Settings | None = None) -> datetime | None:
    """Sinds wanneer de oudste taak die nu al aan de beurt is, wacht (None: niets wacht). Taken
    die bewust later moeten (run_after in de toekomst) tellen niet mee. Met settings ook niet de
    taken van een rij die niet draait (active_lanes): een taak van media terwijl media uit staat,
    wacht bewust tot media weer aan gaat, en is geen vastgelopen rij (fase 5, review C1)."""
    # Naar voren gehaald (to_front): dan telt sinds wanneer hij bestaat
    since = case((Job.run_after < FRONT, Job.created_at), else_=Job.run_after)
    at = await session.scalar(
        select(func.min(since)).where(
            Job.status == "queued", Job.run_after <= now(), _active_filter(settings)
        )
    )
    return at.replace(tzinfo=at.tzinfo or UTC) if at is not None else None


async def delete_failed(session: AsyncSession, job_id: uuid.UUID) -> bool:
    """Een mislukte taak weggooien (niet opnieuw proberen). False als hij niet (meer) mislukt
    is; de aanroeper commit."""
    result = await session.execute(
        delete(Job)
        .where(Job.id == job_id, Job.status == "failed")
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _retry_values() -> dict[str, Any]:
    return {
        "status": "queued",
        "attempts": 0,
        "run_after": now(),
        "started_at": None,
        "finished_at": None,
        "error": None,
    }


async def retry(session: AsyncSession, job_id: uuid.UUID) -> bool:
    """Een mislukte taak opnieuw, met nieuwe pogingen. False als hij niet (meer) mislukt is."""
    result = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "failed")
        .values(**_retry_values())
        .execution_options(synchronize_session=False)
    )
    _kick_after_commit(session)
    return result.rowcount == 1


async def retry_failed(session: AsyncSession) -> int:
    result = await session.execute(
        update(Job)
        .where(Job.status == "failed")
        .values(**_retry_values())
        .execution_options(synchronize_session=False)
    )
    _kick_after_commit(session)
    return result.rowcount or 0
