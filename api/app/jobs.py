"""Achtergrondwerk via de tabel job, zodat het een herstart overleeft.

    await jobs.enqueue(session, "preview", {"sha": sha}, key=f"preview:{sha}")
    await session.commit()  # pas nu worden de workers gewekt

Workers (settings.job_workers stuks) draaien als asyncio-taken in het proces van de app. Een
taak wordt door precies één worker opgepakt: een voorwaardelijke UPDATE (status queued ->
running) die maar één keer slaagt. Mislukt hij, dan gaat hij later opnieuw (na 30 s, daarna
steeds dubbel zo lang), tot max_attempts keer; daarna staat hij op failed en is hij op
/beheer te zien en opnieuw te starten.

attempts telt hoe vaak een taak is begonnen. Stopt het proces midden in een taak (een crash),
dan gaat hij bij het starten terug in de rij, en telt die poging mee: een bestand dat het
proces laat crashen, blijft zo niet eindeloos terugkomen.

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
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from sqlalchemy import and_, delete, event, false, func, or_, select, true, update
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
_KICK = "platform.jobs.kick"  # in session.info: na de commit de workers wekken
DEFAULT_LANE = "default"
# De rijen van Keuze 14, in de volgorde waarin de lifespan ze start
LANES = ("default", "media_scan", "media", "media_net", "import")


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


@dataclass
class Claimed:
    id: uuid.UUID
    type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


_kinds: dict[str, Kind] = {}
# Eén per draaiende Runner; kick() wekt ze allemaal
_wakers: set[Callable[[], None]] = set()
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


def register(
    type: str,
    run: Run,
    on_dead: OnDead | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    *,
    lane: str = "default",
    timeout: float | None = None,
) -> None:
    """Wat er gebeurt bij een taak van dit soort; on_dead als hij definitief mislukt is.
    lane: de rij met een eigen Runner; timeout: in plaats van settings.job_timeout (Keuze 14
    van fase 5)."""
    if lane not in LANES:
        raise ValueError(f"onbekende rij {lane!r} (bekend: {', '.join(LANES)})")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout moet groter dan 0 zijn")
    _kinds[type] = Kind(run, on_dead, max_attempts, lane, timeout)


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
    session.info[_KICK] = True
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
        session.info[_KICK] = True
    return added


def kick() -> None:
    """Wekt de workers. Mag ook te vroeg (vóór de commit): wat ze dan nog niet zien, vinden
    ze bij het volgende rondje (hooguit POLL seconden later)."""
    for wake in list(_wakers):
        wake()


@event.listens_for(OrmSession, "after_commit")
def _after_commit(session: OrmSession) -> None:
    if session.info.pop(_KICK, False):
        kick()


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
    lane_filter = _lane_filter(lane)
    async with _maker(settings)() as session:
        candidates = (
            await session.scalars(
                select(Job.id)
                .where(Job.status == "queued", Job.run_after <= at, lane_filter)
                .order_by(Job.run_after, Job.created_at)
                .limit(10)
            )
        ).all()
        for job_id in candidates:
            row = (
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "queued")
                    .values(status="running", started_at=now(), attempts=Job.attempts + 1)
                    .returning(Job.type, Job.payload, Job.attempts, Job.max_attempts)
                    .execution_options(synchronize_session=False)
                )
            ).first()
            if row is not None:
                await session.commit()
                return Claimed(job_id, row.type, row.payload or {}, row.attempts, row.max_attempts)
        await session.commit()
    return None


async def execute(settings: Settings, job: Claimed) -> None:
    """Draait een opgepakte taak en legt de uitkomst vast. Bij stoppen (cancel) gaat de
    taak terug in de rij, zonder dat die poging meetelt."""
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
        with anyio.CancelScope(shield=True), anyio.move_on_after(10):
            await _requeue(settings, job.id)
        raise
    except Defer as e:
        # Nu niet, straks wel: terug in de rij, en deze poging telt niet
        log.info("Achtergrondtaak %s (%s) wacht %g s: %s", job.id, job.type, e.seconds, e)
        with anyio.CancelScope(shield=True):
            await _requeue(settings, job.id, after=e.seconds)
        return
    except Exception as e:  # noqa: BLE001 - elke fout telt als mislukte poging
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
            await _failed(settings, job, error, final)
            if final and kind is not None and kind.on_dead is not None:
                await _dead(settings, kind, job.payload, error)
        return
    with anyio.CancelScope(shield=True):
        await _set(settings, job.id, status="done", finished_at=now(), error=None)


async def _set(settings: Settings, job_id: uuid.UUID, **values: Any) -> None:
    async with _maker(settings)() as session:
        await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "running")
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def _requeue(settings: Settings, job_id: uuid.UUID, after: float = 0) -> None:
    """Terug in de rij zonder dat deze poging telt (stoppen, of Defer na `after` s)."""
    await _set(
        settings,
        job_id,
        status="queued",
        started_at=None,
        attempts=Job.attempts - 1,
        run_after=now() + timedelta(seconds=max(0.0, after)),
    )


async def _failed(settings: Settings, job: Claimed, error: str, final: bool) -> None:
    if final:
        await _set(settings, job.id, status="failed", finished_at=now(), error=error)
    else:
        await _set(
            settings,
            job.id,
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
        _wakers.add(self.wake)
        _runners.add(self)
        for i in range(lane_workers(self.settings, self.lane)):
            name = f"achtergrondtaken {self.lane} {i + 1}"
            self._tasks.append(asyncio.create_task(self._work(), name=name))
        if self.housekeeping:
            self._tasks.append(asyncio.create_task(self._housekeeping(), name="taken opruimen"))

    async def stop(self) -> None:
        """Lopende taken worden afgebroken en gaan terug in de rij."""
        _wakers.discard(self.wake)
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


async def start_runners(settings: Settings) -> list[Runner]:
    """Een Runner per rij (LANES), het opruimwerk alleen in default. Roep eerst recover()
    aan."""
    runners = [Runner(settings, lane=lane) for lane in LANES]
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


async def oldest_queued(session: AsyncSession) -> datetime | None:
    """Sinds wanneer de oudste taak die nu al aan de beurt is, wacht (None: niets wacht). Taken
    die bewust later moeten (run_after in de toekomst) tellen niet mee."""
    at = await session.scalar(
        select(func.min(Job.run_after)).where(Job.status == "queued", Job.run_after <= now())
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
    session.info[_KICK] = True
    return result.rowcount == 1


async def retry_failed(session: AsyncSession) -> int:
    result = await session.execute(
        update(Job)
        .where(Job.status == "failed")
        .values(**_retry_values())
        .execution_options(synchronize_session=False)
    )
    session.info[_KICK] = True
    return result.rowcount or 0
