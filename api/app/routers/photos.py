"""De fototijdlijn: eigen afbeeldingen en video's op opnamedatum (Node.taken_day).

Wat erop staat: bestanden van de gebruiker zelf met taken_day gezet (photodate.stamp doet dat
voor elke afbeelding en video), niet in de prullenbak en ook niet onder een map in de
prullenbak. Dat laatste via live_dirs: een recursieve query die vanaf de hoofdmap alleen door
levende mappen loopt (veel minder rijen dan alle bestanden omhoog nalopen). Foto's van anderen
zie je via albums en "Gedeeld met mij", niet hier. Gasten hebben geen eigen bestanden: 403.

Volgorde: taken_day aflopend, dan taken_at en id aflopend (nieuwste eerst). Een cursor is
base64url("<taken_day>|<taken_at ISO>|<id>") van het laatste item van een pagina; de web-app
maakt hem ook zelf uit een item (voor /photos/ids). Albums (routers/albums.py) en publieke
albums (routers/public.py) gebruiken dezelfde PhotoItem en cursor.

De filters van Keuze 43 (Z6) op GET /photos en /photos/months: type (meer keer is OF), from en
to, person en tag (meer keer is EN), place, country, library, camera en in, met dezelfde namen
als GET /search. Ze gaan door hetzelfde register als zoeken en de regels van slimme albums
(filters.apply_all), dus ze betekenen hier hetzelfde; de tijdlijn blijft alleen eigen, levende
foto's en video's. Zoals bij zoeken: place, country en camera zijn 501 not_built tot Z3 ze
aanmeldt; een tag die niet van jou is, een bibliotheek die je niet ziet en een map die je niet
ziet (of in de prullenbak) zijn 404. Een persoon die niet van jou is, geeft niets. De query
string van /photos komt nooit in de log (QUERY_PATHS in app/main.py). /photos/ids heeft geen
filters: from en to zijn daar de cursors van de reeks.
"""

import base64
import binascii
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import errors, previews
from app.config import Settings, get_settings
from app.db import Session
from app.models import Blob, Node, User
from app.photodate import FIRST_YEAR, LAST_YEAR
from app.routers.files import GUEST, Utc, shared_ids
from app.routers.stubs import not_built
from app.search.engine import check_in
from app.search.filters import FILTERS, apply_all
from app.search.like import check_library, own_tag
from app.search.types import FileType, SearchParams
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])
MAX_PAGE = 500
MAX_IDS = 10_000
MONTH = re.compile(r"(\d{4})-(\d{2})")


class PhotoItem(BaseModel):
    id: uuid.UUID
    name: str
    mime: str | None
    size: int
    modified_at: Utc
    media: Literal["image", "video"]
    # Zwevende lokale tijd (toon met timeZone "UTC"), zie app/models.py
    taken_at: Utc
    taken_day: int
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    etag: str
    thumb: str | None = None
    large: str | None = None
    # Er staat een deling of link op (alleen voor de eigenaar; anders altijd false)
    shared: bool = False


class PublicPhotoItem(BaseModel):
    """Een PhotoItem achter een publieke link: zonder shared."""

    id: uuid.UUID
    name: str
    mime: str | None
    size: int
    modified_at: Utc
    media: Literal["image", "video"]
    taken_at: Utc
    taken_day: int
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    etag: str
    thumb: str | None = None
    large: str | None = None


class PhotoPage(BaseModel):
    items: list[PhotoItem]
    next: str | None


class Month(BaseModel):
    # "2026-10"
    month: str
    count: int


class Months(BaseModel):
    months: list[Month]


class IdRange(BaseModel):
    ids: list[uuid.UUID]
    # Er waren er meer dan MAX_IDS
    truncated: bool


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def cursor_of(day: int, taken_at: datetime, node_id: uuid.UUID) -> str:
    raw = f"{day}|{_utc(taken_at).isoformat()}|{node_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


# Elke taken_day valt hierbinnen: photodate houdt het bij 1900-2100, als kloktijd in een andere
# tijdzone hooguit een dag ernaast. Een grotere dag past ook niet in een INTEGER van SQLite.
FIRST_DAY, LAST_DAY = (FIRST_YEAR - 1) * 10_000 + 101, (LAST_YEAR + 1) * 10_000 + 1231


def read_cursor(value: str) -> tuple[int, datetime, uuid.UUID]:
    """(taken_day, taken_at, id) uit een cursor; onzin geeft 422 invalid_input."""
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        day, taken, node_id = raw.split("|")
        if not FIRST_DAY <= int(day) <= LAST_DAY:
            raise ValueError(day)
        return (
            int(day),
            _utc(datetime.fromisoformat(taken.replace("Z", "+00:00"))),
            uuid.UUID(node_id),
        )
    except (ValueError, OverflowError, binascii.Error, UnicodeDecodeError):
        raise errors.fail(422, "invalid_input", "Ongeldige invoer", field="query.after") from None


def order_key():
    return tuple_(Node.taken_day, Node.taken_at, Node.id)


def ordered(query: Select) -> Select:
    return query.order_by(Node.taken_day.desc(), Node.taken_at.desc(), Node.id.desc())


def live_dirs(root_id: uuid.UUID | None):
    """Recursieve CTE (id) van de hoofdmap en alle mappen eronder die niet in de prullenbak
    liggen (en niet onder een map in de prullenbak)."""
    top = select(Node.id).where(Node.id == root_id, Node.deleted_at.is_(None))
    found = top.cte("live_dirs", recursive=True)
    child = aliased(Node)
    step = select(child.id).where(
        child.parent_id == found.c.id, child.kind == "dir", child.deleted_at.is_(None)
    )
    return found.union_all(step)


def on_timeline(owner_id: uuid.UUID, root_id: uuid.UUID | None) -> list[ColumnElement[bool]]:
    """De voorwaarden voor "op de tijdlijn van owner": eigen bestand, met opnamedag, levend."""
    dirs = live_dirs(root_id)
    return [
        Node.owner_id == owner_id,
        Node.taken_day.is_not(None),
        Node.kind == "file",
        Node.deleted_at.is_(None),
        Node.parent_id.in_(select(dirs.c.id)),
    ]


def month_range(month: str) -> tuple[int, int]:
    match = MONTH.fullmatch(month)
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise errors.fail(422, "invalid_input", "Ongeldige invoer", field="query.month")
    start = int(match.group(1)) * 10000 + int(match.group(2)) * 100
    return start, start + 100


COLUMNS = (
    Node.id,
    Node.owner_id,
    Node.name,
    Node.mime,
    Node.size,
    Node.modified_at,
    Node.taken_at,
    Node.taken_day,
    Node.blob_id,
    Blob.preview,
    Blob.preview_meta,
)


def photo_query(*where: ColumnElement[bool]) -> Select:
    return ordered(select(*COLUMNS).outerjoin(Blob, Blob.sha256 == Node.blob_id).where(*where))


def _number(value: Any) -> Any:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def item_dict(row: Any, secret: bytes, exp: int) -> dict[str, Any]:
    """De velden van een PhotoItem uit een rij van photo_query (zonder shared)."""
    meta = row.preview_meta if isinstance(row.preview_meta, dict) else {}
    sizes = (meta.get("sizes") or []) if row.preview == "ready" else []
    sha = row.blob_id
    thumb = (
        previews.url(secret, sha, previews.THUMB, exp) if sha and previews.THUMB in sizes else None
    )
    large = None
    if sha and sizes:
        size = previews.LARGE if previews.LARGE in sizes else previews.THUMB
        large = previews.url(secret, sha, size, exp)
    mime = (row.mime or "").lower()
    return {
        "id": row.id,
        "name": row.name,
        "mime": row.mime,
        "size": row.size,
        # model_construct valideert niet: zelf in UTC zetten (SQLite geeft ze zonder zone)
        "modified_at": _utc(row.modified_at),
        "media": "video" if mime.startswith("video/") else "image",
        "taken_at": _utc(row.taken_at),
        "taken_day": row.taken_day,
        "width": _number(meta.get("width")),
        "height": _number(meta.get("height")),
        "duration": _number(meta.get("duration")),
        "etag": f'"{sha or row.id.hex}"',
        "thumb": thumb,
        "large": large,
    }


async def photo_items(
    session: AsyncSession,
    user: User | None,
    rows: Sequence[Any],
    settings: Settings,
    background: BackgroundTasks,
) -> list[PhotoItem]:
    """PhotoItems voor deze rijen; shared alleen voor wat van user zelf is (één query). Wat
    nog geen (complete) preview heeft, gaat na het antwoord in de rij naar voren: dit bekijkt
    iemand nu (previews.to_front)."""
    secret = previews.url_key(settings)
    exp = previews.expiry(period=previews.url_period(settings))
    mine = [r.id for r in rows if user is not None and r.owner_id == user.id]
    shared = await shared_ids(session, mine) if mine else set()
    items = [
        PhotoItem.model_construct(**item_dict(r, secret, exp), shared=r.id in shared) for r in rows
    ]
    if user is not None:
        previews.to_front(
            background, settings, [(r.blob_id, r.preview, r.preview_meta) for r in rows]
        )
    return items


def next_cursor(rows: Sequence[Any], limit: int) -> str | None:
    if len(rows) < limit:
        return None
    last = rows[-1]
    return cursor_of(last.taken_day, last.taken_at, last.id)


def page_where(after: str | None) -> list[ColumnElement[bool]]:
    if not after:
        return []
    day, taken, node_id = read_cursor(after)
    return [order_key() < tuple_(day, taken, node_id)]


def owner_only(user: User) -> None:
    if user.role == "guest":
        raise errors.fail(403, **GUEST)


Limit = Annotated[int, Query(ge=1, le=MAX_PAGE)]

# De filters van een ander pakket (zoals routers/search.LATER): 501 not_built tot dat pakket
# ze aanmeldt
LATER = (("persons", "person", "Z6"), ("place", "place", "Z3"), ("country", "country", "Z3"),
         ("camera", "camera", "Z3"))  # fmt: skip


def photo_filters(
    type_: Annotated[list[FileType], Query(alias="type")] = [],  # noqa: B006
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    person: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006
    place: int | None = None,
    country: Annotated[str | None, Query(max_length=8)] = None,
    tag: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006
    library: uuid.UUID | None = None,
    camera: Annotated[str | None, Query(max_length=200)] = None,
    in_: Annotated[uuid.UUID | None, Query(alias="in")] = None,
) -> SearchParams | None:
    """De filters van Keuze 43 uit de query string, zoals GET /search ze leest; None zonder
    filter."""
    p = SearchParams(
        types=type_,
        date_from=date_from,
        date_to=date_to,
        persons=person,
        place=place,
        country=country,
        tags=tag,
        library=library,
        camera=camera,
        in_=in_,
    )
    return p if p.has_filter() else None


Filters = Annotated[SearchParams | None, Depends(photo_filters)]


async def check_filters(session: AsyncSession, user: User, p: SearchParams | None) -> None:
    """Zoals bij zoeken, vóór de vraag: 501 voor een filter dat nog niet is aangemeld, 404 voor
    een map, bibliotheek of tag die je niet ziet of die niet van jou is."""
    if p is None:
        return
    for field, name, package in LATER:
        value = getattr(p, field)
        if (value is not None if field == "place" else bool(value)) and name not in FILTERS:
            raise not_built(package)
    if p.in_ is not None:
        await check_in(session, user, p.in_)
    if p.library is not None:
        await check_library(session, user, p.library)
    for tag_id in dict.fromkeys(p.tags):
        await own_tag(session, user, tag_id)


def filtered(query: Select, user: User, p: SearchParams | None) -> Select:
    """query (over node) met de filters erbij, via hetzelfde register als zoeken."""
    return query if p is None else apply_all(query, Node.__table__, p, user)


@router.get("/photos/months")
async def months(user: CurrentUser, session: Session, filters: Filters) -> Months:
    """Hoeveel foto's en video's per maand, nieuwste maand eerst (met de filters: alleen wat
    erop past)."""
    owner_only(user)
    await check_filters(session, user, filters)
    month = (Node.taken_day // 100).label("month")
    query = select(month, func.count()).where(*on_timeline(user.id, user.root_id))
    rows = await session.execute(
        filtered(query, user, filters).group_by(month).order_by(month.desc())
    )
    return Months(months=[Month(month=f"{m // 100:04d}-{m % 100:02d}", count=n) for m, n in rows])


@router.get("/photos")
async def photos(
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    background: BackgroundTasks,
    filters: Filters,
    month: Annotated[str | None, Query(max_length=7)] = None,
    after: Annotated[str | None, Query(max_length=300)] = None,
    limit: Limit = MAX_PAGE,
) -> PhotoPage:
    """Een pagina van de tijdlijn (met month alleen die maand, "2026-10"; met de filters van
    Keuze 43 alleen wat erop past); next is de cursor voor de volgende pagina, of null."""
    owner_only(user)
    await check_filters(session, user, filters)
    where = on_timeline(user.id, user.root_id) + page_where(after)
    if month:
        start, end = month_range(month)
        where += [Node.taken_day >= start, Node.taken_day < end]
    query = filtered(photo_query(*where), user, filters)
    rows = (await session.execute(query.limit(limit))).all()
    return PhotoPage(
        items=await photo_items(session, user, rows, settings, background),
        next=next_cursor(rows, limit),
    )


def range_where(start: str, end: str) -> list[ColumnElement[bool]]:
    """Van cursor start tot en met cursor end (in de volgorde van de tijdlijn), beide erbij;
    welke van de twee eerst komt, maakt niet uit."""
    a, b = read_cursor(start), read_cursor(end)
    high, low = (a, b) if a >= b else (b, a)
    return [order_key() <= tuple_(*high), order_key() >= tuple_(*low)]


async def id_range(session: AsyncSession, where: list[ColumnElement[bool]]) -> IdRange:
    rows = (await session.scalars(ordered(select(Node.id).where(*where)).limit(MAX_IDS + 1))).all()
    return IdRange(ids=list(rows[:MAX_IDS]), truncated=len(rows) > MAX_IDS)


@router.get("/photos/ids")
async def photo_ids(
    user: CurrentUser,
    session: Session,
    start: Annotated[str, Query(alias="from", max_length=300)],
    end: Annotated[str, Query(alias="to", max_length=300)],
) -> IdRange:
    """De ids van een reeks op de tijdlijn (selecteren met Shift, ook over maanden die de
    web-app nog niet geladen heeft): hooguit 10.000, truncated als het er meer waren."""
    owner_only(user)
    return await id_range(session, on_timeline(user.id, user.root_id) + range_where(start, end))
