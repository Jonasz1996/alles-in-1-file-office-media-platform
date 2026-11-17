"""Bewerksessies (Keuze 14, 20 en 22), los van OnlyOffice.

open_session(session, user, node, ...) -> (OfficeSession, stale): wie welke key krijgt, onder
state.node_lock, in één transactie (de tabel van Keuze 14). De rechten van Keuze 22 en de
invoercontrole van Keuze 23 doet de route vóór dit (routers/office.py).
find_by_key(session, key): de rij van een key. busy(session): loopt er een sessie die opent of
opslaat (pending of saving)? Een conversie wacht dan (O3). close(row, reason): de sessie dicht.

Verder wat opslaan, de ronde en het herstel samen gebruiken: de klok (in te spuiten voor de
tests), de databasesessie buiten een verzoek, ds_uid, het schrijfrecht van nu, en de
ontvangers van de gebeurtenissen van §3.4 (altijd bepaald op het moment van versturen, en
altijd na de commit).
"""

import asyncio
import hashlib
import hmac
import logging
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import access, errors, events
from app.config import Settings
from app.db import get_engine
from app.models import Node, OfficeSession, User
from app.office import keys, runtime
from app.office.state import OfficeState, office_state
from app.routers.office_contracts import DROPPED, PRESENCE, RECOVERED, UNSAVED

log = logging.getLogger("platform.office")

# Keuze 14: zo lang krijgt een volgende opener dezelfde key van een sessie die nog niemand
# opende (de eerste kan nog laden; de fonts van een nieuwe DS duren tot 280 s)
PENDING_MINUTES = 5
# Keuze 14: zo lang wacht een opener op het slot van een bestand dat opslaat
LOCK_SECONDS = 15.0

BUSY = "Dit document wordt net opgeslagen; het gaat zo weer open"
TOO_MANY = "Er bewerken nu te veel mensen tegelijk; probeer het later opnieuw"


def _real_now() -> datetime:
    return datetime.now(UTC)


# De klok van office: de tests zetten hem vooruit (versies na 30 minuten, de ronde)
clock: Callable[[], datetime] = _real_now


def now() -> datetime:
    return clock()


def utc(value: datetime) -> datetime:
    """SQLite geeft tijden zonder tijdzone terug; ze staan altijd in UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def maker() -> async_sessionmaker[AsyncSession]:
    """Databasesessies buiten een verzoek (callbacks op de interne app, de ronde, het herstel,
    app.cli), met de instellingen van office."""
    return async_sessionmaker(get_engine(runtime.settings()), expire_on_commit=False)


_spare = OfficeState()


def office() -> OfficeState:
    """De OfficeState van de app; zonder app (app.cli) een eigen, voor de sloten."""
    app = runtime.app()
    return office_state(app) if app is not None else _spare


def ds_uid(settings: Settings, user_id: uuid.UUID) -> str:
    """De gebruiker zoals de editor hem kent (Keuze 14): de eerste 24 hex-tekens van
    HMAC-SHA256(office-geheim, "user:" + id). Vast per gebruiker, zonder de echte id. (Gelijk
    aan oo/tokens.ds_user_id; de kern importeert de adapter niet.)"""
    mac = hmac.new(
        settings.office_secret.encode(), f"user:{user_id}".encode(), hashlib.sha256
    ).hexdigest()
    return mac[:24]


async def find_by_key(session: AsyncSession, key: str) -> OfficeSession | None:
    return await session.get(OfficeSession, key, populate_existing=True)


async def busy(session: AsyncSession) -> bool:
    found = await session.scalar(
        select(OfficeSession.key).where(OfficeSession.state.in_(("pending", "saving"))).limit(1)
    )
    return found is not None


async def live(session: AsyncSession, node_id: uuid.UUID) -> OfficeSession | None:
    """De sessie van dit bestand die niet dicht is (hooguit één, ux_office_session_open)."""
    return await session.scalar(
        select(OfficeSession)
        .where(OfficeSession.node_id == node_id, OfficeSession.state != "closed")
        .execution_options(populate_existing=True)
    )


async def latest(session: AsyncSession, node_id: uuid.UUID) -> OfficeSession | None:
    """De nieuwste sessie van dit bestand, ook als hij dicht is."""
    return await session.scalar(
        select(OfficeSession)
        .where(OfficeSession.node_id == node_id)
        .order_by(OfficeSession.created_at.desc())
        .limit(1)
    )


def close(row: OfficeSession, reason: str, error: str | None = None) -> None:
    """Dicht (de aanroeper commit). Daarna krijgt de volgende opener een nieuwe key."""
    at = now()
    row.state = "closed"
    row.closed_reason = reason
    if error is not None:
        row.last_error = error
    row.closed_at = at
    row.updated_at = at


def member_ids(row: OfficeSession) -> dict[str, uuid.UUID]:
    """members als {ds_uid: user_id}; wat geen id is, telt niet."""
    found: dict[str, uuid.UUID] = {}
    for uid, user_id in (row.members or {}).items():
        try:
            found[uid] = uuid.UUID(str(user_id))
        except ValueError:
            continue
    return found


# --- het recht van nu ---


async def can_write(session: AsyncSession, user_id: uuid.UUID, node: Node) -> bool:
    """Mag deze gebruiker dit bestand nu bewerken? Bestaat, niet geblokkeerd, en eigenaar (geen
    gast) of een deling met write (access.resolve: verlopen of in de prullenbak telt niet)."""
    user = await session.get(User, user_id)
    if user is None or user.disabled_at is not None:
        return False
    if node.owner_id == user_id:
        return user.role != "guest"
    grant = (await access.resolve(session, user_id, [node.id])).get(node.id)
    return grant is not None and grant.permission == "write"


async def writers(
    session: AsyncSession, node: Node, user_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Wie van deze gebruikers nu schrijfrecht heeft."""
    found = set()
    for user_id in dict.fromkeys(user_ids):
        if await can_write(session, user_id, node):
            found.add(user_id)
    return found


def editor_users(row: OfficeSession, state: OfficeState) -> set[uuid.UUID]:
    """Wie nu in de editor zit: volgens de laatste status 1 of info, plus de tunnels (O1)."""
    members = member_ids(row)
    found = {members[uid] for uid in row.connected or [] if uid in members}
    found.update(state.tunnels.get(row.key, {}))
    return found


async def connections(session: AsyncSession, state: OfficeState) -> int:
    """Het aantal editors over alle open sessies (voor een eindige licentie van DS)."""
    rows = (
        await session.scalars(select(OfficeSession).where(OfficeSession.state != "closed"))
    ).all()
    counted = sum(len(editor_users(row, state)) for row in rows)
    tunnels = sum(sum(users.values()) for users in state.tunnels.values())
    return max(counted, tunnels)


# --- openen (Keuze 14) ---


def busy_error() -> Exception:
    return errors.fail(409, "office_busy", BUSY)


def too_many(limit: int) -> Exception:
    return errors.fail(429, "office_too_many", TOO_MANY, max=limit)


async def open_session(
    session: AsyncSession,
    user: User,
    node: Node,
    *,
    settings: Settings,
    state: OfficeState,
    file_type: str,
    max_editors: int,
) -> tuple[OfficeSession, bool]:
    """Wie welke key krijgt (Keuze 14), onder het slot van het bestand en in één transactie:

    | de sessie van dit bestand              | de opener krijgt                             |
    | geen, of closed                        | een nieuwe key                               |
    | pending, jonger dan 5 minuten          | dezelfde key                                 |
    | pending, ouder, of de inhoud is anders | de oude dicht (lost) en een nieuwe key       |
    | open, met iemand erin                  | dezelfde key                                 |
    | open zonder iemand (DS gaat opslaan)   | 409 office_busy                              |
    | saving                                 | wachten op het slot; na 15 s 409 office_busy |

    De opener komt in members en gaat uit banned. stale: het bestand veranderde buiten de
    editor terwijl de sessie liep (een upload of "versie terugzetten")."""
    uid = ds_uid(settings, user.id)
    lock = state.node_lock(node.id)
    await session.commit()  # niets openhouden terwijl we op het slot wachten
    try:
        async with asyncio.timeout(LOCK_SECONDS):
            await lock.acquire()
    except TimeoutError:
        raise busy_error() from None
    try:
        fresh = await session.get(Node, node.id, populate_existing=True)
        if fresh is None:
            raise errors.fail(404, "not_found", "Niet gevonden")
        row = await live(session, node.id)
        at = now()
        stale = False
        if row is not None:
            if row.state == "saving":
                raise busy_error()
            if row.state == "open" and not row.connected:
                raise busy_error()
            if row.state == "pending" and (
                at - utc(row.updated_at) >= timedelta(minutes=PENDING_MINUTES)
                or row.blob_id != fresh.blob_id
            ):
                # Te oud, of de inhoud veranderde voordat iemand hem opende: een nieuwe key met
                # de inhoud van nu (anders laadt DS de oude, en wint die van de upload)
                close(row, "lost")
                await session.flush()  # eerst dicht, dan pas de nieuwe rij (de unieke index)
                row = None
        if row is not None:
            joined = editor_users(row, state)
            if user.id not in joined and len(joined) >= max_editors:
                raise too_many(max_editors)
            stale = row.blob_id != fresh.blob_id
        else:
            row = OfficeSession(
                key=keys.new_key(node.id),
                node_id=node.id,
                owner_id=fresh.owner_id,
                name=fresh.name,
                file_type=file_type,
                blob_id=fresh.blob_id,
                state="pending",
                members={},
                banned=[],
                connected=[],
                versions_made=0,
                node_name=settings.node_name,
                created_at=at,
                updated_at=at,
            )
            session.add(row)
        row.members = {**(row.members or {}), uid: str(user.id)}
        row.banned = [b for b in row.banned or [] if b != uid]
        if row.state == "pending":
            row.updated_at = at  # de laatste config die uitging
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        lock.release()
    return row, stale


# --- de gebeurtenissen van §3.4 (na de commit) ---


async def participants(session: AsyncSession, row: OfficeSession, node: Node) -> set[uuid.UUID]:
    """De deelnemers die nu schrijfrecht hebben (niet wie in banned staat)."""
    members = member_ids(row)
    out = set(row.banned or [])
    return await writers(session, node, [m for uid, m in members.items() if uid not in out])


async def publish_presence(session: AsyncSession, row: OfficeSession, node: Node) -> None:
    to = await participants(session, row, node)
    events.publish(to, PRESENCE, {"node_id": str(node.id), "count": len(row.connected or [])})


async def publish_unsaved(
    session: AsyncSession,
    row: OfficeSession,
    node: Node | None,
    reason: str,
    final: bool,
) -> None:
    """Naar de eigenaar en de deelnemers die nu nog schrijfrecht hebben."""
    to: set[uuid.UUID] = set()
    if node is not None:
        to = await participants(session, row, node)
    if row.owner_id is not None:
        to.add(row.owner_id)
    data = {
        "node_id": str(node.id) if node is not None else None,
        "name": node.name if node is not None else row.name,
        "reason": reason,
        "final": final,
    }
    events.publish(to, UNSAVED, data)


async def publish_recovered(
    session: AsyncSession, row: OfficeSession, copy: Node, original: uuid.UUID | None, reason: str
) -> None:
    """Naar wie de kopie nu kan zien: de eigenaar en access.watchers. Nooit naar wie eruit
    gezet is (banned)."""
    seen = await access.watchers(session, [copy.id])
    # Wie in banned staat, is al uit members: herken hem aan zijn ds_uid
    settings = runtime.settings()
    banned = set(row.banned or [])
    to = {u for u in seen.get(copy.id, {}) if ds_uid(settings, u) not in banned}
    to.add(copy.owner_id)
    data = {
        "node_id": str(original) if original is not None else None,
        "copy_id": str(copy.id),
        "name": copy.name,
        "reason": reason,
    }
    events.publish(to, RECOVERED, data)


def publish_dropped(user_ids: Iterable[uuid.UUID], node_id: uuid.UUID, key: str, reason: str):
    """Alleen naar wie eruit gezet is (access), of wiens editor een oude key had (stale)."""
    events.publish(user_ids, DROPPED, {"node_id": str(node_id), "key": key, "reason": reason})
