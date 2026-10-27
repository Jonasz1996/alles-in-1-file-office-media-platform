"""Bewaking van office voor /beheer (Keuze 27 en 28).

O5 meet zelf niets: alles hier leest OfficeState (health.py van O0, de tunnel van O1), de
database (de sessies van O2, de PDF's van O3) en de lijst forgotten files van DS.

- usage(session, state): verbindingen, mensen en documenten van nu. Een verbinding telt zoals
  sessions.connections (de tunnels van O1, of de editors volgens de laatste status 1 als dat
  er meer zijn).
- peak(state, connections): het hoogste aantal verbindingen sinds de start van de app. De
  tunnel zet het bij elke nieuwe tunnel; telt de status 1 er meer, dan onthoudt dit dat.
- open_documents, unsaved en conversions: de lijsten en tellers van het tabblad Office.
- notices(state, session): de meldingen office_down, office_misconfigured, office_version,
  office_limit, office_unsaved en office_convert_failed voor de haak in admin.system_info.

De lijst forgotten files komt van DS (getForgottenList, via engine.forgotten). /beheer vraagt
hem bij elke ververs; daarom onthoudt dit hem office_health_seconds, en alleen als DS gezond is.
Terugzetten en Verwijderen gooien hem weg (forget_cache), zodat de lijst meteen klopt.
"""

import math
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cluster import Notice, notice
from app.models import Blob, Node, OfficeSession, User
from app.office import engine, health, runtime, sessions
from app.office.state import OfficeState

# ≥ dit aantal van command license is "geen limiet" (de standaard van DS 10.0)
UNLIMITED = 2_147_483_647
# Wat command version van de vastgepinde DS (10.0.0.1 op digest) geeft [gemeten]
PINNED_VERSION = "10.0.0.169"
# Onder deze versie komt de limiet van 20 verbindingen terug (Keuze 1)
MINIMUM_VERSION = (10, 0)
# Vanaf zoveel procent van een eindige licentie: office_limit
LIMIT_PERCENT = 80


@dataclass
class Usage:
    connections: int
    people: int
    documents: int


@dataclass
class Unsaved:
    key: str
    name: str
    owner: str | None
    at: datetime | None
    reason: str | None


@dataclass
class OpenDocument:
    name: str
    owner: str
    editors: list[str]
    since: datetime
    state: str


# --- de lijst forgotten files (DS) ---


@dataclass
class _Cache:
    keys: list[str] | None = None
    at: float = 0.0


_cache = _Cache()


def forget_cache() -> None:
    """De onthouden lijst weg: de volgende vraag gaat weer naar DS."""
    _cache.keys = None
    _cache.at = 0.0


async def forgotten(state: OfficeState, fresh: bool = False) -> list[str] | None:
    """De keys in getForgottenList, of None als het niet te weten is (geen office, of DS
    niet gezond en nog nooit gelezen). Met DS niet gezond: wat er het laatst stond."""
    eng = engine.current()
    if eng is None:
        return None
    ttl = runtime.settings().office_health_seconds
    now = time.monotonic()
    if not fresh and _cache.keys is not None and now - _cache.at < ttl:
        return _cache.keys
    if not state.healthy:
        return _cache.keys
    try:
        keys = list(dict.fromkeys(await eng.forgotten()))
    except Exception:  # noqa: BLE001 - DS weg: wat er het laatst stond
        return _cache.keys
    _cache.keys, _cache.at = keys, now
    return keys


# --- tellen ---


async def _open_rows(session: AsyncSession) -> list[OfficeSession]:
    return list(
        (
            await session.scalars(
                select(OfficeSession)
                .where(OfficeSession.state != "closed")
                .order_by(OfficeSession.created_at)
            )
        ).all()
    )


async def usage(session: AsyncSession, state: OfficeState) -> Usage:
    """Verbindingen (zoals sessions.connections), mensen (verschillende gebruikers) en
    documenten (sessies met iemand erin) van nu."""
    people: set[uuid.UUID] = set()
    documents = 0
    for row in await _open_rows(session):
        users = sessions.editor_users(row, state)
        people.update(users)
        documents += bool(users)
    for users in state.tunnels.values():
        people.update(user for user, count in users.items() if count > 0)
    documents = max(documents, sum(1 for users in state.tunnels.values() if any(users.values())))
    return Usage(await sessions.connections(session, state), len(people), documents)


def peak(state: OfficeState, connections: int) -> tuple[int, datetime] | None:
    """Het hoogste aantal verbindingen sinds de start van de app, en wanneer."""
    if connections > 0 and (state.peak is None or connections > state.peak[0]):
        state.peak = (connections, datetime.now(UTC))
    return state.peak


def unlimited(limit: int | None) -> bool:
    return limit is None or limit <= 0 or limit >= UNLIMITED


def limit_reached(used: int, limit: int | None) -> bool:
    """Is used minstens LIMIT_PERCENT van een eindige licentie?"""
    if unlimited(limit):
        return False
    assert limit is not None
    return used >= math.ceil(limit * LIMIT_PERCENT / 100)


def _parts(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in version.strip().split("."))
    except ValueError:
        return None


def version_ok(version: str | None) -> bool:
    """De vastgepinde versie? Onbekend (nog niet gelezen) telt als goed: dan is er nog
    niets te melden."""
    if version is None:
        return True
    parts = _parts(version)
    if parts is None or parts[: len(MINIMUM_VERSION)] < MINIMUM_VERSION:
        return False
    return version.strip() == PINNED_VERSION


async def _names(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(select(User.id, User.display_name).where(User.id.in_(ids)))
    return {user_id: name for user_id, name in rows}


async def open_documents(session: AsyncSession, state: OfficeState) -> list[OpenDocument]:
    """De sessies die niet dicht zijn, de oudste eerst: ook pending en saving, want die
    houden de conversies tegen (sessions.busy)."""
    rows = await _open_rows(session)
    editors = {row.key: sessions.editor_users(row, state) for row in rows}
    ids = {row.owner_id for row in rows if row.owner_id} | set().union(*editors.values())
    names = await _names(session, ids)
    node_ids = {row.node_id for row in rows if row.node_id}
    current = {}
    if node_ids:
        found = await session.execute(select(Node.id, Node.name).where(Node.id.in_(node_ids)))
        current = dict(found.all())
    return [
        OpenDocument(
            name=current.get(row.node_id, row.name) if row.node_id else row.name,
            owner=names.get(row.owner_id, "") if row.owner_id else "",
            editors=sorted(names.get(user, "") for user in editors[row.key]),
            since=sessions.utc(row.created_at),
            state=row.state,
        )
        for row in rows
    ]


async def unsaved(session: AsyncSession, keys: list[str]) -> list[Unsaved]:
    """De forgotten files met wat de app ervan weet. Een key zonder rij is onbekend (reason
    unknown); is de eigenaar of het bestand definitief weg, dan owner_gone of gone (het herstel
    gooit hem in zijn volgende ronde weg, en Terugzetten weigert); reason is anders de laatste
    fout van de sessie (no_room, ...), of None als het herstel hem nog niet probeerde."""
    if not keys:
        return []
    rows = {
        row.key: row
        for row in (
            await session.scalars(select(OfficeSession).where(OfficeSession.key.in_(keys)))
        ).all()
    }
    names = await _names(session, {row.owner_id for row in rows.values() if row.owner_id})
    found = []
    for key in keys:
        row = rows.get(key)
        if row is None:
            found.append(Unsaved(key=key, name="", owner=None, at=None, reason="unknown"))
            continue
        at = row.closed_at or row.updated_at
        reason = row.last_error
        if row.owner_id is None:
            reason = "owner_gone"
        elif row.node_id is None:
            reason = "gone"
        found.append(
            Unsaved(
                key=key,
                name=row.name,
                owner=names.get(row.owner_id) if row.owner_id else None,
                at=sessions.utc(at) if at else None,
                reason=reason,
            )
        )
    found.sort(key=lambda u: u.at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return found


async def conversions(session: AsyncSession) -> dict[str, int]:
    """Hoeveel inhoud op een PDF wacht (pending), en hoeveel failed, unsafe en ask is."""
    rows = await session.execute(
        select(Blob.pdf_state, func.count())
        .where(Blob.pdf_state.in_(("pending", "failed", "unsafe", "ask")))
        .group_by(Blob.pdf_state)
    )
    counts = dict(rows.all())
    return {
        "queued": counts.get("pending", 0),
        "failed": counts.get("failed", 0),
        "unsafe": counts.get("unsafe", 0),
        "ask": counts.get("ask", 0),
    }


# --- de meldingen ---


async def notices(state: OfficeState, session: AsyncSession) -> list[Notice]:
    """De meldingen van office voor /beheer. Zonder PLATFORM_OFFICE_URL geen enkele (fase 3)."""
    if not runtime.settings().office_url:
        return []
    found: list[Notice] = []
    if health.down_too_long(state):
        found.append(
            notice("office_down", "De document server is al meer dan 2 minuten niet bereikbaar.")
        )
    if state.health == "misconfigured" or state.tokens_enforced is False:
        found.append(
            notice(
                "office_misconfigured",
                "Office is verkeerd ingesteld (zie het log van de app); bewerken en omzetten "
                "staan uit.",
            )
        )
    if not version_ok(state.version):
        assert state.version is not None
        found.append(
            notice(
                "office_version",
                f"De document server heeft versie {state.version}, niet de vastgepinde "
                f"{PINNED_VERSION}.",
                version=state.version,
                pinned=PINNED_VERSION,
            )
        )
    if not unlimited(state.license_connections):
        used = await sessions.connections(session, state)
        limit = state.license_connections
        assert limit is not None
        if limit_reached(used, limit):
            found.append(
                notice(
                    "office_limit",
                    f"Office gebruikt {used} van de {limit} verbindingen van de licentie.",
                    used=used,
                    max=limit,
                )
            )
    keys = await forgotten(state)
    if keys:
        found.append(
            notice(
                "office_unsaved",
                f"{len(keys)} document(en) uit de editor zijn niet opgeslagen; zie het "
                "tabblad Office.",
                count=len(keys),
            )
        )
    failed = (await conversions(session))["failed"]
    if failed:
        found.append(
            notice(
                "office_convert_failed",
                f"{failed} document(en) konden niet naar PDF worden omgezet; zie het tabblad "
                "Office.",
                count=failed,
            )
        )
    return found
