"""Herinneringen: je eigen foto's van deze dag in eerdere jaren (bouwontwerp fase 6, Keuze 41;
Z6).

- De dag: ?day=JJJJ-MM-DD, standaard vandaag in PLATFORM_TIMEZONE (today(); tests zetten now()).
- Alleen eigen, levende foto's en video's (own_live) met een opnamedag. Per jaar één waarde van
  taken_day (J·10000 + MMDD) op ix_node_owner_taken: een reeks punten in de index, geen scan.
  De vroegste dag van de eigenaar (één zoekstap in dezelfde index) zegt hoe ver terug.
- Schrikkeldag: op 29 februari de 29e van eerdere schrikkeljaren; op 28 februari van een jaar
  zonder 29 februari ook de 29e van eerdere schrikkeljaren (anders zag je die nooit).
- Hooguit PER_DAY foto's per dag, nieuwste eerst (zoals de tijdlijn).
- Verbergen (memory_hide): day (een hele dag, JJJJ-MM-DD), folder (een eigen map, met alles
  eronder) en person (een eigen persoon). Een ref die niet klopt of niet van jou is: 400
  memories_bad_hide. Hooguit MAX_HIDES regels per gebruiker.
- Uit te zetten per gebruiker (photo_pref.memories): dan is de lijst leeg.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import delete, exists, func, literal, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.config import Settings
from app.models import Blob, MemoryHide, Node, Person, PhotoPerson, User
from app.people.types import MemoryHide as HideOut
from app.prefs import photo_prefs
from app.routers.files import get_live_dir
from app.routers.photos import COLUMNS
from app.search.live import own_live

PER_DAY = 60
MAX_HIDES = 1000


def now() -> datetime:
    return datetime.now(UTC)


def today(settings: Settings) -> date:
    """Vandaag in PLATFORM_TIMEZONE."""
    return now().astimezone(ZoneInfo(settings.timezone)).date()


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def days_before(day: date, first_year: int) -> list[date]:
    """De dagen van eerdere jaren die bij day horen, nieuwste jaar eerst, tot en met first_year."""
    found: list[date] = []
    for year in range(day.year - 1, first_year - 1, -1):
        if (day.month, day.day) == (2, 29):
            if _leap(year):
                found.append(date(year, 2, 29))
            continue
        found.append(date(year, day.month, day.day))
        if (day.month, day.day) == (2, 28) and not _leap(day.year) and _leap(year):
            found.append(date(year, 2, 29))
    return found


def _taken_day(day: date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


def _day_of(value: int) -> date:
    return date(value // 10000, value // 100 % 100, value % 100)


async def _first_year(db: AsyncSession, user: User) -> int | None:
    """Het jaar van de vroegste eigen, levende foto: de eerste rij in ix_node_owner_taken."""
    first = await db.scalar(
        select(Node.taken_day)
        .where(own_live(Node, user.id), Node.taken_day.is_not(None))
        .order_by(Node.taken_day)
        .limit(1)
    )
    return None if first is None else first // 10000


async def _ranked(db: AsyncSession, user: User, wanted: list[date]):
    """De rijen van photo_query met hun plaats op hun dag (rank, nieuwste eerst): eigen, levend,
    op een van deze dagen, en niet verborgen (memory_hide)."""
    rows = (
        await db.execute(
            select(MemoryHide.kind, MemoryHide.ref).where(MemoryHide.user_id == user.id)
        )
    ).all()
    where = [own_live(Node, user.id), Node.taken_day.in_([_taken_day(d) for d in wanted])]
    days = [_taken_day(date.fromisoformat(ref)) for kind, ref in rows if kind == "day"]
    if days:
        where.append(Node.taken_day.not_in(days))
    folders = [uuid.UUID(ref) for kind, ref in rows if kind == "folder"]
    if folders:
        # Onder een verborgen eigen map (via mpath, zoals het filter in): het bereik van zijn pad
        folder = Node.__table__.alias("hidden_folder")
        start = func.substr(folder.c.mpath, 1, func.length(folder.c.mpath) - 1)
        end = start.op("||")(literal("0"))
        where.append(
            ~exists().where(
                folder.c.id.in_(folders),
                folder.c.owner_id == user.id,
                Node.mpath > folder.c.mpath,
                Node.mpath < end,
            )
        )
    persons = [uuid.UUID(ref) for kind, ref in rows if kind == "person"]
    if persons:
        where.append(
            ~exists().where(
                PhotoPerson.node_id == Node.id,
                PhotoPerson.person_id.in_(persons),
                PhotoPerson.owner_id == user.id,
            )
        )
    return (
        select(
            *COLUMNS,
            func.row_number()
            .over(
                partition_by=Node.taken_day,
                order_by=(Node.taken_at.desc(), Node.id.desc()),
            )
            .label("rank"),
        )
        .outerjoin(Blob, Blob.sha256 == Node.blob_id)
        .where(*where)
        .subquery("ranked")
    )


async def find(db: AsyncSession, user: User, day: date) -> list[tuple[int, date, list]]:
    """(jaren geleden, dag, rijen van photo_query) per dag met foto's, het recentste jaar eerst.
    Leeg als de gebruiker herinneringen uit heeft."""
    if user.role == "guest" or not (await photo_prefs(db, user.id))["memories"]:
        return []
    first = await _first_year(db, user)
    if first is None or first >= day.year:
        return []
    wanted = days_before(day, first)
    if not wanted:
        return []
    ranked = await _ranked(db, user, wanted)
    rows = (
        await db.execute(
            select(ranked)
            .where(ranked.c.rank <= PER_DAY)
            .order_by(ranked.c.taken_day.desc(), ranked.c.taken_at.desc(), ranked.c.id.desc())
        )
    ).all()
    per_day: dict[int, list] = {}
    for row in rows:
        per_day.setdefault(row.taken_day, []).append(row)
    return [
        (day.year - found.year, found, per_day[value])
        for value, found in ((_taken_day(d), d) for d in wanted)
        if value in per_day
    ]


# --- Verbergen ---


def bad_hide() -> Exception:
    return errors.fail(400, "memories_bad_hide", "Dit kan niet uit je herinneringen")


def normal_ref(kind: str, ref: str) -> str:
    """De vaste vorm van een ref (een datum als JJJJ-MM-DD, een id als uuid met streepjes); 400
    memories_bad_hide als hij niet te lezen is."""
    try:
        if kind == "day":
            if len(ref) != 10:
                raise ValueError(ref)
            return date.fromisoformat(ref).isoformat()
        return str(uuid.UUID(ref))
    except ValueError:
        raise bad_hide() from None


async def _check_ref(db: AsyncSession, user: User, kind: str, ref: str) -> None:
    """folder: een eigen map die niet in de prullenbak ligt (ook niet via een map erboven);
    person: een eigen persoon."""
    if kind == "folder":
        try:
            found = await get_live_dir(db, user, uuid.UUID(ref), "read")
        except HTTPException:
            raise bad_hide() from None
        if not found.owner:
            raise bad_hide()
    elif kind == "person":
        mine = await db.scalar(
            select(Person.id).where(Person.id == uuid.UUID(ref), Person.owner_id == user.id)
        )
        if mine is None:
            raise bad_hide()


async def hides(db: AsyncSession, user: User) -> list[HideOut]:
    rows = await db.execute(
        select(MemoryHide.kind, MemoryHide.ref)
        .where(MemoryHide.user_id == user.id)
        .order_by(MemoryHide.kind, MemoryHide.ref)
    )
    return [HideOut(kind=kind, ref=ref) for kind, ref in rows]


async def hide(db: AsyncSession, user: User, kind: str, ref: str) -> None:
    """Een regel erbij (nog niet gecommit); twee keer dezelfde is één regel."""
    ref = normal_ref(kind, ref)
    await _check_ref(db, user, kind, ref)
    count = await db.scalar(
        select(func.count()).select_from(MemoryHide).where(MemoryHide.user_id == user.id)
    )
    if (count or 0) >= MAX_HIDES:
        raise errors.fail(
            400, "memories_bad_hide", "Dit kan niet uit je herinneringen", max=MAX_HIDES
        )
    await db.execute(
        sqlite_insert(MemoryHide)
        .values(user_id=user.id, kind=kind, ref=ref)
        .on_conflict_do_nothing()
    )


async def unhide(db: AsyncSession, user: User, kind: str, ref: str) -> None:
    """De regel weg (nog niet gecommit); een regel die er niet is: niets."""
    try:
        ref = normal_ref(kind, ref)
    except HTTPException:
        return
    await db.execute(
        delete(MemoryHide).where(
            MemoryHide.user_id == user.id, MemoryHide.kind == kind, MemoryHide.ref == ref
        )
    )
