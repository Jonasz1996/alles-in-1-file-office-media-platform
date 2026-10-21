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
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from sqlalchemy import delete, event, func, select, update
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


class JobError(Exception):
    """Een fout met een melding die zo op /beheer kan (zonder het soort fout ervoor)."""


Run = Callable[[Settings, dict[str, Any]], Awaitable[None]]
OnDead = Callable[[Settings, dict[str, Any], str], Awaitable[None]]


@dataclass
class Kind:
    run: Run
    on_dead: OnDead | None = None
    max_attempts: int = MAX_ATTEMPTS


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


def register(
    type: str, run: Run, on_dead: OnDead | None = None, max_attempts: int = MAX_ATTEMPTS
) -> None:
    """Wat er gebeurt bij een taak van dit soort; on_dead als hij definitief mislukt is."""
    _kinds[type] = Kind(run, on_dead, max_attempts)


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


async def claim(settings: Settings, at: datetime | None = None) -> Claimed | None:
    """Pakt de eerstvolgende taak die klaarstaat. Twee workers die tegelijk dezelfde kiezen:
    de UPDATE slaagt maar voor één, de ander probeert de volgende."""
    at = at or now()
    async with _maker(settings)() as session:
        candidates = (
            await session.scalars(
                select(Job.id)
                .where(Job.status == "queued", Job.run_after <= at)
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
    try:
        if kind is None:
            raise JobError(f"Onbekend soort taak: {job.type}")
        with anyio.fail_after(settings.job_timeout) as scope:
            await kind.run(settings, job.payload)
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True), anyio.move_on_after(10):
            await _requeue(settings, job.id)
        raise
    except Exception as e:  # noqa: BLE001 - elke fout telt als mislukte poging
        if isinstance(e, TimeoutError) and scope is not None and scope.cancel_called:
            error = f"Duurde langer dan {settings.job_timeout:g} seconden"
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


async def _requeue(settings: Settings, job_id: uuid.UUID) -> None:
    await _set(
        settings,
        job_id,
        status="queued",
        started_at=None,
        attempts=Job.attempts - 1,
        run_after=now(),
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


async def run_one(settings: Settings, at: datetime | None = None) -> bool:
    job = await claim(settings, at)
    if job is None:
        return False
    await execute(settings, job)
    return True


async def run_pending(settings: Settings, at: datetime | None = None) -> int:
    """Voor tests: draait wat klaarstaat (op tijdstip `at`) tot er niets meer klaarstaat,
    zonder de achtergrondlus. Geeft het aantal gedraaide taken."""
    count = 0
    while await run_one(settings, at):
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
    """De workers en het opruimen, gestart en gestopt door de lifespan van de app."""

    def __init__(self, settings: Settings):
        self.settings = settings
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
        for i in range(max(1, self.settings.job_workers)):
            self._tasks.append(asyncio.create_task(self._work(), name=f"achtergrondtaken {i + 1}"))
        self._tasks.append(asyncio.create_task(self._housekeeping(), name="taken opruimen"))

    async def stop(self) -> None:
        """Lopende taken worden afgebroken en gaan terug in de rij."""
        _wakers.discard(self.wake)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _work(self) -> None:
        while True:
            self._wake.clear()
            try:
                job = await claim(self.settings)
                if job is not None:
                    self.wake()  # misschien staat er meer klaar: dan pakt een andere worker die
                    await execute(self.settings, job)
                    continue
            except Exception:  # noqa: BLE001 - bijv. de database even weg; zo meteen opnieuw
                log.exception("Achtergrondtaken: oppakken of afronden mislukt")
            with anyio.move_on_after(POLL):
                await self._wake.wait()

    async def _housekeeping(self) -> None:
        while True:
            try:
                if removed := await remove_old(self.settings):
                    log.info("%s oude achtergrondtaken opgeruimd", removed)
            except Exception:  # noqa: BLE001 - opruimen mag de app nooit stoppen
                log.exception("Opruimen van achtergrondtaken mislukt")
            await asyncio.sleep(HOUSEKEEPING)


async def counts(session: AsyncSession) -> dict[str, int]:
    rows = (await session.execute(select(Job.status, func.count()).group_by(Job.status))).all()
    result = dict.fromkeys(STATES, 0)
    result.update({status: count for status, count in rows if status in result})
    return result


async def failed(session: AsyncSession, limit: int = 100) -> list[Job]:
    return list(
        (
            await session.scalars(
                select(Job)
                .where(Job.status == "failed")
                .order_by(Job.finished_at.desc(), Job.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


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
