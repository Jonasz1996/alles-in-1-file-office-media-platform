"""De kaart, de plaatsen en de controle vóór het delen (bouwontwerp fase 6, §3.4; Z3).

Alleen eigen, levende bestanden, en alleen zolang de eigenaar locatie niet op "uit" heeft (de
routes kijken naar photo_pref: na "uit" is de kaart meteen leeg, ook voor de job geo.off de rijen
wist; Keuze 28). Een gast heeft geen eigen foto's: 403 guest, zoals /photos.

- GET /photos/map?bbox=w,s,e,n&z=0..14&<filters>: cellen van de kaart (Keuze 4a), zonder join op
  node: GROUP BY over photo_geo met own_live_geo, langs ix_photo_geo_tile (dekkend: geen rij per
  foto). Een cel is (x16 >> s, y16 >> s) met s = 14 − z, dus vier cellen per tegelbreedte. lat en
  lon van een cel: het midden van zijn foto's (het gemiddelde van x16 en y16, dus ook uit de
  index); cover: de nieuwste foto. Meer dan MAX_CELLS cellen: de server neemt grotere cellen
  (s + 1) tot het past, zodat er nooit iets wegvalt. west > oost gaat over 180° heen: twee
  stukken in x, samengevoegd. Met een filter van Keuze 43 de join op node over de al smallere
  set (zoals /photos: hetzelfde register, filters.apply_all).
- GET /photos/map/items?bbox=&<filters>&cursor=: de foto's in het venster in de volgorde van de
  tijdlijn, per PAGE, met de cursor van /photos.
- GET /photos/places?<filters>: de plaatsen met het aantal eigen, levende foto's, per verzoek
  geteld (GROUP BY op ix_photo_geo_place), met de namen uit places.sqlite; zonder places.sqlite
  geen namen en dus geen plaatsen.
- GET /photos/share-check: stap 2 van Z3 (nog 501).

Een venster dat niet klopt: 422 invalid_input op query.bbox. bbox, z en de filters komen nooit in
de log (QUERY_PATHS in app/main.py).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.exceptions import RequestValidationError
from sqlalchemy import Select, and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors, photo_rows
from app.config import Settings, get_settings
from app.db import Session
from app.geo import places as geo_places
from app.geo import tiles, wire  # noqa: F401 - wire meldt Z3 aan
from app.geo.types import MapCell, MapCells, MapItems, PlaceCount, Places, ShareCheck
from app.models import Node, PhotoGeo, User
from app.routers.photos import (
    Filters,
    check_filters,
    next_cursor,
    owner_only,
    page_where,
    photo_items,
    photo_query,
)
from app.routers.stubs import not_built
from app.search.filters import apply_all
from app.search.live import own_live, own_live_geo
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])

# w,s,e,n in graden
BBOX = Query(max_length=100, pattern=r"^[0-9eE.,+-]+$")
MAX_CELLS = 500
PAGE = 200
MAX_PLACES = 500


def _bbox(text: str) -> tuple[list[tuple[int, int]], int, int]:
    try:
        return tiles.ranges(*tiles.parse_bbox(text))
    except tiles.BadBox:
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("query", "bbox"), "msg": "Ongeldig venster"}]
        ) from None


def _filtered(sel: Select, user: User, filters: Any) -> Select:
    """Met een filter van Keuze 43: de join op node (eigen en levend, zoals photo_geo al is), en
    dan het register (zoals /photos)."""
    if filters is None:
        return sel
    on = and_(Node.id == PhotoGeo.node_id, own_live(Node, user.id))
    return apply_all(sel.join(Node, on), Node.__table__, filters, user)


def _in_window(x16: Any, y16: Any, xs: list[tuple[int, int]], y0: int, y1: int) -> Any:
    """x16 en y16 in het venster (de stukken in x, en y0..y1)."""
    across = [x16.between(x0, x1) for x0, x1 in xs]
    return and_(across[0] if len(across) == 1 else or_(*across), y16.between(y0, y1))


async def _cells(
    session: AsyncSession, user: User, s: int, xs: list[tuple[int, int]], y0: int, y1: int,
    filters: Any,
) -> dict[tuple[int, int], list[Any]]:  # fmt: skip
    """{(cx, cy): [n, t, cover, som x16, som y16]} over de stukken in x; hooguit MAX_CELLS + 1
    per stuk (genoeg om te weten dat het te veel is)."""
    shift = literal(s)
    cx = PhotoGeo.x16.op(">>")(shift).label("cx")
    cy = PhotoGeo.y16.op(">>")(shift).label("cy")
    found: dict[tuple[int, int], list[Any]] = {}
    for x0, x1 in xs:
        sel = (
            select(
                cx,
                cy,
                func.count().label("n"),
                func.max(PhotoGeo.taken_at).label("t"),
                PhotoGeo.node_id.label("cover"),  # SQLite: de rij van max(taken_at)
                func.avg(PhotoGeo.x16).label("ax"),
                func.avg(PhotoGeo.y16).label("ay"),
            )
            .where(
                own_live_geo(PhotoGeo, user.id),
                _in_window(PhotoGeo.x16, PhotoGeo.y16, [(x0, x1)], y0, y1),
            )
            .group_by(cx, cy)
            .limit(MAX_CELLS + 1)
        )
        for r in await session.execute(_filtered(sel, user, filters)):
            cell = found.get((r.cx, r.cy))
            if cell is None:
                found[(r.cx, r.cy)] = [r.n, r.t, r.cover, r.ax * r.n, r.ay * r.n]
                continue
            # Dezelfde cel aan beide kanten van 180° (alleen bij heel grote cellen)
            if r.t is not None and (cell[1] is None or r.t > cell[1]):
                cell[1], cell[2] = r.t, r.cover
            cell[0] += r.n
            cell[3] += r.ax * r.n
            cell[4] += r.ay * r.n
    return found


@router.get("/photos/map")
async def photo_map(
    user: CurrentUser,
    session: Session,
    filters: Filters,
    bbox: Annotated[str, BBOX],
    z: Annotated[int, Query(ge=0, le=14)],
) -> MapCells:
    """De cellen van de kaart in dit venster op zoom z: hooguit MAX_CELLS."""
    owner_only(user)
    xs, y0, y1 = _bbox(bbox)
    await check_filters(session, user, filters)
    if not await photo_rows.location_on(session, user.id):
        return MapCells(cells=[])
    s = tiles.shift(z)
    while True:
        found = await _cells(session, user, s, xs, y0, y1, filters)
        if len(found) <= MAX_CELLS or s >= 16:
            break
        s += 1  # te veel: grotere cellen
    cells = [
        MapCell(
            x=x,
            y=y,
            n=n,
            lat=round(tiles.lat_of(sy / n + 0.5), 6),
            lon=round(tiles.lon_of(sx / n + 0.5), 6),
            cover=cover,
        )
        for (x, y), (n, _t, cover, sx, sy) in sorted(found.items())
    ]
    return MapCells(cells=cells)


@router.get("/photos/map/items")
async def photo_map_items(
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    background: BackgroundTasks,
    filters: Filters,
    bbox: Annotated[str, BBOX],
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> MapItems:
    """De foto's in het venster, nieuwste eerst (zoals de tijdlijn), per PAGE."""
    owner_only(user)
    xs, y0, y1 = _bbox(bbox)
    await check_filters(session, user, filters)
    try:
        after = page_where(cursor)
    except errors.HTTPException:
        raise errors.fail(422, "invalid_input", "Ongeldige invoer", field="query.cursor") from None
    if not await photo_rows.location_on(session, user.id):
        return MapItems(items=[], cursor=None)
    query = photo_query(
        own_live(Node, user.id),
        own_live_geo(PhotoGeo, user.id),
        _in_window(PhotoGeo.x16, PhotoGeo.y16, xs, y0, y1),
        Node.taken_day.is_not(None),
        *after,
    ).join(PhotoGeo, PhotoGeo.node_id == Node.id)
    if filters is not None:
        query = apply_all(query, Node.__table__, filters, user)
    found = (await session.execute(query.limit(PAGE))).all()
    return MapItems(
        items=await photo_items(session, user, found, settings, background),
        cursor=next_cursor(found, PAGE),
    )


@router.get("/photos/places")
async def places(
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    filters: Filters,
) -> Places:
    """De plaatsen van je eigen, levende foto's, met hoeveel er zijn (meeste eerst)."""
    owner_only(user)
    await check_filters(session, user, filters)
    if not await photo_rows.location_on(session, user.id):
        return Places(places=[])
    n = func.count().label("n")
    sel = (
        select(PhotoGeo.place_id, n)
        .where(own_live_geo(PhotoGeo, user.id), PhotoGeo.place_id.is_not(None))
        .group_by(PhotoGeo.place_id)
        .order_by(n.desc(), PhotoGeo.place_id)
        .limit(MAX_PLACES)
    )
    counted = (await session.execute(_filtered(sel, user, filters))).all()
    db = geo_places.get(settings)
    if db is None or not counted:
        return Places(places=[])
    names = db.names(pid for pid, _n in counted)
    out = [
        PlaceCount(id=pid, name=row.name, admin1=row.admin1, country=row.cc, n=count)
        for pid, count in counted
        if (row := names.get(pid)) is not None
    ]
    out.sort(key=lambda p: (-p.n, p.name, p.id))
    return Places(places=out)


@router.get("/photos/share-check")
async def share_check(user: CurrentUser, node: uuid.UUID) -> ShareCheck:
    raise not_built("Z3")
