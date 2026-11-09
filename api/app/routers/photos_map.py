"""De kaart, de plaatsen en de controle vóór het delen (bouwontwerp fase 6, §3.4; Z3).

Alleen eigen, levende bestanden, en alleen zolang de eigenaar locatie niet op "uit" heeft (de
routes kijken naar photo_pref: na "uit" is de kaart meteen leeg, ook voor de job geo.off de rijen
wist; Keuze 28). Een gast heeft geen eigen foto's: 403 guest, zoals /photos.

- GET /photos/map?bbox=w,s,e,n&z=0..14&<filters>: cellen van de kaart (Keuze 4a), zonder join op
  node: GROUP BY over photo_geo met own_live_geo, langs ix_photo_geo_tile (dekkend: geen rij per
  foto). Een cel is (x16 >> s, y16 >> s) met s = 14 − z, dus vier cellen per tegelbreedte. lat en
  lon van een cel: het midden van zijn foto's (het gemiddelde van x16 en y16, dus ook uit de
  index); cover: de nieuwste foto. Meer dan MAX_CELLS cellen: de server neemt de kleinste
  grotere s waarbij het past, zodat er nooit iets wegvalt (door halveren: hooguit vier vragen
  meer, ook voor de hele wereld op zoom 14). west > oost gaat over 180° heen: twee
  stukken in x, samengevoegd. Met een filter van Keuze 43: datum, plaats en land op photo_geo
  zelf (taken_at, place_id, country; _geo_where), nog steeds zonder join; de andere (soort, map,
  camera, persoon, tag) doen de join op node over de al smallere set (zoals /photos: hetzelfde
  register, filters.apply_all). Dezelfde foto's als /photos (tests/geo/test_map_filters.py).
- GET /photos/map/items?bbox=&<filters>&cursor=: de foto's in het venster in de volgorde van de
  tijdlijn, per PAGE, met de cursor van /photos. Twee wegen naar dezelfde rijen: weinig foto's in
  het venster (minder dan MANY): vanaf photo_geo en dan sorteren; veel: langs de tijdlijn
  (ix_node_owner_taken) met per foto een blik in photo_geo, tot er PAGE zijn. Zo blijft het
  onder de 20 ms, waar één weg tot 180 ms kost (de hele wereld, of een leeg venster).
- GET /photos/places?<filters>: de plaatsen met het aantal eigen, levende foto's, per verzoek
  geteld (GROUP BY op ix_photo_geo_place), met de namen uit places.sqlite; zonder places.sqlite
  geen namen en dus geen plaatsen.
- GET /photos/share-check?node=: vóór het delen (Keuze 30), hoeveel foto's en video's in wat je
  deelt een plek in het bestand hebben (blob_meta.gps = 1) en van hoeveel dat onbekend is (gps
  NULL of nog niet gelezen: een video met media uit, of een foto die photometa nog niet las).
  Een map: de eigen, levende bestanden eronder (via het pad); een album: zijn eigen, levende
  items die gedeeld worden (shared 1, Keuze 42); een bestand: dat bestand. Ook voor een
  eigenaar met locatie "uit": de bytes houden hun plek. Alleen wat van jezelf is (alleen de
  eigenaar deelt); anders 404 not_found.

Een venster dat niet klopt: 422 invalid_input op query.bbox. bbox, z en de filters komen nooit in
de log (QUERY_PATHS in app/main.py).
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.exceptions import RequestValidationError
from sqlalchemy import Select, and_, case, exists, false, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors, photo_rows
from app.config import Settings, get_settings
from app.db import Session
from app.geo import places as geo_places
from app.geo import tiles, wire  # noqa: F401 - wire meldt Z3 aan
from app.geo.types import MapCell, MapCells, MapItems, PlaceCount, Places, ShareCheck
from app.models import AlbumItem, BlobMeta, Node, PhotoGeo, User
from app.routers.photos import (
    Filters,
    check_filters,
    next_cursor,
    owner_only,
    page_where,
    photo_items,
    photo_query,
)
from app.search.filters import apply_all
from app.search.filters_photo import country_code
from app.search.live import own_live, own_live_geo
from app.search.types import SearchParams
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])

# w,s,e,n in graden
BBOX = Query(max_length=100, pattern=r"^[0-9eE.,+-]+$")
MAX_CELLS = 500
PAGE = 200
MANY = 4000  # foto's in het venster vanaf waar items langs de tijdlijn gaat
MAX_PLACES = 500


def _bbox(text: str) -> tuple[list[tuple[int, int]], int, int]:
    try:
        return tiles.ranges(*tiles.parse_bbox(text))
    except tiles.BadBox:
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("query", "bbox"), "msg": "Ongeldig venster"}]
        ) from None


# De filters die photo_geo zelf heeft (Keuze 4a): daarvoor geen join op node
GEO_FILTERS = {"date_from": None, "date_to": None, "place": None, "country": None}


def _day(day: date) -> datetime:
    """Het begin van een dag als zwevende tijd, zoals taken_at is opgeslagen (app/photodate.py)."""
    return datetime.combine(day, time.min, tzinfo=UTC)


def _geo_where(p: SearchParams, user: User) -> list[Any]:
    """from, to, place en country op de kolommen van photo_geo, met dezelfde uitkomst als de
    filters van het register op node: taken_at van photo_geo is die van de node (trigger), en de
    dag van taken_at is taken_day (photodate.day_of). Een bestand zonder opnamedatum (geen foto of
    video) staat niet op de tijdlijn en valt bij een datumfilter weg, zoals in /photos/map/items."""
    out: list[Any] = []
    if p.date_from is not None:
        out.append(PhotoGeo.taken_at >= _day(p.date_from))
    if p.date_to is not None:
        out.append(PhotoGeo.taken_at < _day(p.date_to + timedelta(days=1)))
    if p.place is not None:
        out.append(PhotoGeo.place_id == p.place)
    if p.country:
        code = country_code(p.country)
        out.append(false() if code is None else PhotoGeo.country == code)
    if p.place is not None or p.country:
        out.append(photo_rows.not_off(user.id))
    return out


def _filtered(sel: Select, user: User, filters: SearchParams | None) -> Select:
    """Met een filter van Keuze 43 (Keuze 4a; review golf 3, contract F1): datum, plaats en land
    op photo_geo zelf, langs zijn eigen indexen. Alleen voor de rest (soort, camera, map, tag,
    bibliotheek, persoon) de join op node (eigen en levend, zoals photo_geo al is) over de al
    smallere set, en dan het register (zoals /photos)."""
    if filters is None:
        return sel
    sel = sel.where(*_geo_where(filters, user))
    rest = filters.model_copy(update=GEO_FILTERS)
    if not rest.has_filter():
        return sel
    on = and_(Node.id == PhotoGeo.node_id, own_live(Node, user.id))
    return apply_all(sel.join(Node, on), Node.__table__, rest, user)


def _grid(s: int, xs: list[tuple[int, int]], y0: int, y1: int) -> int:
    """Hoeveel cellen het venster bij s hooguit heeft: het raster zelf, los van de foto's."""
    return sum((x1 >> s) - (x0 >> s) + 1 for x0, x1 in xs) * ((y1 >> s) - (y0 >> s) + 1)


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
                func.max(PhotoGeo.taken_at).label("newest"),
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
                found[(r.cx, r.cy)] = [r.n, r.newest, r.cover, r.ax * r.n, r.ay * r.n]
                continue
            # Dezelfde cel aan beide kanten van 180° (alleen bij heel grote cellen)
            if r.newest is not None and (cell[1] is None or r.newest > cell[1]):
                cell[1], cell[2] = r.newest, r.cover
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
    found = await _cells(session, user, s, xs, y0, y1, filters)
    if len(found) > MAX_CELLS:
        # Te veel: de kleinste grotere s waarbij het past. Het aantal cellen daalt met s (elke
        # cel valt in precies één grotere), dus halveren tussen s + 1 en de eerste s waarbij het
        # raster van het venster zelf al past (bij 16 hooguit twee cellen)
        lo = s + 1
        hi = next(t for t in range(lo, 17) if _grid(t, xs, y0, y1) <= MAX_CELLS)
        at_hi: dict[tuple[int, int], list[Any]] | None = None
        while lo < hi:
            mid = (lo + hi) // 2
            tried = await _cells(session, user, mid, xs, y0, y1, filters)
            if len(tried) <= MAX_CELLS:
                hi, at_hi = mid, tried
            else:
                lo = mid + 1
        found = at_hi if at_hi is not None else await _cells(session, user, hi, xs, y0, y1, filters)
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
    window = _in_window(PhotoGeo.x16, PhotoGeo.y16, xs, y0, y1)
    # Hoeveel foto's in het venster (tot MANY, uit de dekkende index): dat kiest de weg
    some = select(literal(1)).where(own_live_geo(PhotoGeo, user.id), window).limit(MANY)
    n = await session.scalar(select(func.count()).select_from(some.subquery()))
    if (n or 0) >= MANY:
        geo = exists().where(PhotoGeo.node_id == Node.id, own_live_geo(PhotoGeo, user.id), window)
        query = photo_query(own_live(Node, user.id), Node.taken_day.is_not(None), geo, *after)
    else:
        query = photo_query(
            own_live(Node, user.id),
            own_live_geo(PhotoGeo, user.id),
            window,
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
async def share_check(user: CurrentUser, session: Session, node: uuid.UUID) -> ShareCheck:
    """Hoeveel foto's en video's in deze map, dit album of dit bestand een plek hebben."""
    owner_only(user)
    n = Node.__table__
    target = (
        await session.execute(
            select(n.c.kind, n.c.mpath).where(
                n.c.id == node, n.c.owner_id == user.id, n.c.dead == 0
            )
        )
    ).first()
    if target is None:
        raise errors.fail(404, "not_found", "Niet gevonden")
    if target.kind == "album":
        scope = exists().where(
            AlbumItem.album_id == node, AlbumItem.node_id == n.c.id, AlbumItem.shared == 1
        )
    elif target.kind == "file":
        scope = n.c.id == node
    else:
        # Onder de map: het bereik van zijn mpath, zoals filters._under
        scope = and_(n.c.mpath > target.mpath, n.c.mpath < target.mpath[:-1] + "0")
    gps = BlobMeta.__table__.c.gps
    row = (
        await session.execute(
            select(
                func.count(case((gps == 1, 1))),
                func.count(case((gps.is_(None), 1))),
            )
            .select_from(
                n.outerjoin(BlobMeta.__table__, BlobMeta.__table__.c.blob_sha == n.c.blob_id)
            )
            .where(
                own_live(n, user.id),
                or_(n.c.mime.like("image/%"), n.c.mime.like("video/%")),
                scope,
            )
        )
    ).one()
    return ShareCheck(with_location=row[0] or 0, unknown=row[1] or 0)
