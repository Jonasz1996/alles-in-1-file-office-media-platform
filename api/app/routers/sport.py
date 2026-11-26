"""Sport: een eigen Strava-alternatief (vervangt Endurain). Alleen eigen activiteiten; een gast
heeft er geen (403 guest, zoals /photos).

POST   /sport/import              ruwe body (GPX, TCX, .gz of zip), naam in X-File-Name
                                  (procent-gecodeerd) -> {results: [{name, status, id, reason}]}
POST   /sport/import-files        {ids}: eigen GPX/TCX/zip uit Bestanden -> idem
GET    /sport/activities          ?sport=&year=&q=&cursor=&limit= -> {items, cursor}
GET    /sport/activities/{id}     alles: cijfers, punten (hooguit 1500), splits, beste tijden
PATCH  /sport/activities/{id}     {name?, sport?, notes?, ele_filter?}
DELETE /sport/activities/{id}
GET    /sport/activities/{id}/file  het originele bestand
GET    /sport/stats               ?year=&sport=: per maand, de laatste 12 weken, per jaar
GET    /sport/records             ?sport=: de beste tijden per afstand (top 3)
GET    /sport/config              {tiles, attribution, can_edit}
PUT    /sport/config              (beheerder) {tiles_url, attribution}
GET    /sport/tiles/{z}/{x}/{y}   een kaarttegel via de server (404 sport_tiles_off zonder bron)

Fouten: 403 guest; 404 not_found; 413 sport_too_large; 422 sport_bad_tiles; 502
sport_tiles_failed; 404 sport_tiles_off.
"""

from __future__ import annotations

import base64
import tempfile
import uuid
import zlib
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import quote, unquote

import anyio.to_thread
from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app import errors
from app.config import Settings, get_settings
from app.db import Session
from app.models import Node, Setting, SportActivity
from app.routers.photos import owner_only
from app.security import AdminUser, CurrentUser
from app.sport import service, tiles, track
from app.sport.parse import MAX_BYTES
from app.storage import BlobStore

router = APIRouter(prefix="/api/v1/sport", tags=["sport"])

# Eén upload (een zip met een hele export) hooguit zoveel
UPLOAD_MAX = 1024 * 1024 * 1024
NOT_FOUND = {"message": "Activiteit niet gevonden", "code": "not_found"}
Sport = Literal["run", "ride", "walk", "hike", "swim", "ski", "row", "other"]


class Outcome(BaseModel):
    name: str
    status: Literal["new", "duplicate", "error"]
    id: uuid.UUID | None = None
    reason: str | None = None


class ImportResult(BaseModel):
    results: list[Outcome]


class FileIds(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class ActivityItem(BaseModel):
    id: uuid.UUID
    name: str
    sport: str
    start_at: datetime | None
    day: date | None
    distance: float
    moving: float
    elapsed: float
    gain: float
    avg_hr: float | None
    preview: str


class ActivityPage(BaseModel):
    items: list[ActivityItem]
    cursor: str | None


class SplitOut(BaseModel):
    km: int
    distance: float
    seconds: float
    elev: float
    hr: float | None


class Series(BaseModel):
    lat: list[float]
    lon: list[float]
    d: list[float]
    t: list[float | None] | None
    ele: list[float | None] | None
    ele_raw: list[float | None] | None
    hr: list[float | None] | None
    cad: list[float | None] | None
    power: list[float | None] | None
    speed: list[float | None] | None


class Activity(BaseModel):
    id: uuid.UUID
    name: str
    sport: str
    start_at: datetime | None
    day: date | None
    notes: str
    distance: float
    elapsed: float
    moving: float
    gain: float
    loss: float
    ele_min: float | None
    ele_max: float | None
    max_speed: float
    avg_hr: float | None
    max_hr: float | None
    avg_cad: float | None
    avg_power: float | None
    points: int
    fixed: int
    ele_filter: bool
    bbox: tuple[float, float, float, float]
    efforts: dict[str, float]
    source_name: str
    source_fmt: str
    series: Series
    splits: list[SplitOut]


class ActivityPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    sport: Sport | None = None
    notes: str | None = Field(default=None, max_length=10000)
    ele_filter: bool | None = None


class Totals(BaseModel):
    key: str
    count: int = 0
    distance: float = 0.0
    moving: float = 0.0
    gain: float = 0.0


class Stats(BaseModel):
    year: int
    years: list[int]
    months: list[Totals]
    weeks: list[Totals]
    per_year: list[Totals]
    per_sport: list[Totals]
    all_time: Totals


class RecordEntry(BaseModel):
    id: uuid.UUID
    name: str
    day: date | None
    seconds: float


class RecordRow(BaseModel):
    distance: float
    best: list[RecordEntry]


class Records(BaseModel):
    sport: str
    rows: list[RecordRow]


class SportConfig(BaseModel):
    tiles: bool
    attribution: str | None
    can_edit: bool
    tiles_url: str | None = None


class SportConfigIn(BaseModel):
    tiles_url: str | None = Field(default=None, max_length=500)
    attribution: str | None = Field(default=None, max_length=300)


def _tz(settings: Settings):
    return service.tz_of(settings.timezone)


def _results(outs: list[service.Outcome]) -> ImportResult:
    return ImportResult(
        results=[Outcome(name=o.name, status=o.status, id=o.id, reason=o.reason) for o in outs]
    )  # type: ignore[arg-type]


async def _import_stream(session, user, name: str, fh, settings: Settings) -> list:
    """Eén upload of bestand: een zip (elk bestand erin) of één GPX/TCX."""
    tz = _tz(settings)
    fh.seek(0)
    head = fh.read(4)
    fh.seek(0)
    outs: list[service.Outcome] = []
    if service.is_zip(head):
        try:
            for entry, data, reason in service.zip_entries(fh):
                if data is None:
                    outs.append(service.Outcome(name=entry, status="error", reason=reason))
                else:
                    outs.append(await service.import_one(session, user.id, entry, data, tz))
        except (service.ParseError, OSError, ValueError) as e:
            outs.append(service.Outcome(name=name, status="error", reason=str(e) or "zip"))
        if not outs:
            outs.append(service.Outcome(name=name, status="error", reason="geen GPX of TCX"))
        return outs
    data = fh.read(MAX_BYTES * 8 + 1)
    outs.append(await service.import_one(session, user.id, name, data, tz))
    return outs


@router.post("/import")
async def import_upload(
    request: Request,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    x_file_name: Annotated[str, Header(max_length=1024)] = "activiteit.gpx",
) -> ImportResult:
    """De body is het bestand zelf (geen multipart): GPX, TCX, .gz of een zip."""
    owner_only(user)
    name = unquote(x_file_name).rsplit("/", 1)[-1][:255] or "activiteit.gpx"
    # Geen databaseverbinding vasthouden terwijl de upload binnenkomt
    await session.commit()
    with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as fh:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > UPLOAD_MAX:
                raise errors.fail(413, "sport_too_large", "Dit bestand is te groot")
            fh.write(chunk)
        outs = await _import_stream(session, user, name, fh, settings)
    return _results(outs)


@router.post("/import-files")
async def import_files(
    body: FileIds,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ImportResult:
    """Eigen bestanden uit Bestanden (niet in de prullenbak). Wat niet van jou is of geen
    bestand is, krijgt status error."""
    owner_only(user)
    nodes = {
        n.id: n
        for n in await session.scalars(
            select(Node).where(
                Node.id.in_(body.ids),
                Node.owner_id == user.id,
                Node.kind == "file",
                Node.deleted_at.is_(None),
            )
        )
    }
    await session.commit()
    store = BlobStore(settings.blob_dir)
    outs: list[service.Outcome] = []
    for node_id in body.ids:
        node = nodes.get(node_id)
        if node is None or node.blob_id is None:
            outs.append(service.Outcome(name=str(node_id), status="error", reason="niet gevonden"))
            continue
        try:
            fh = await anyio.to_thread.run_sync(open, store.path(node.blob_id), "rb")
        except OSError:
            outs.append(service.Outcome(name=node.name, status="error", reason="onleesbaar"))
            continue
        try:
            outs.extend(await _import_stream(session, user, node.name, fh, settings))
        except OSError:
            outs.append(service.Outcome(name=node.name, status="error", reason="onleesbaar"))
        finally:
            fh.close()
    return _results(outs)


def _cursor(row) -> str:
    raw = f"{row.start_at.isoformat() if row.start_at else ''}|{row.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _uncursor(text: str) -> tuple[datetime | None, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(text.encode()).decode()
        when, ident = raw.split("|")
        return (datetime.fromisoformat(when) if when else None), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError) as e:
        raise errors.fail(422, "invalid_input", errors.INVALID_INPUT, field="cursor") from e


@router.get("/activities")
async def activities(
    user: CurrentUser,
    session: Session,
    sport: Sport | None = None,
    year: Annotated[int | None, Query(ge=1970, le=2200)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ActivityPage:
    """Nieuwste eerst (zonder starttijd achteraan)."""
    owner_only(user)
    A = SportActivity
    stmt = select(
        A.id, A.name, A.sport, A.start_at, A.day, A.distance, A.moving, A.elapsed, A.gain,
        A.avg_hr, A.preview,
    ).where(A.owner_id == user.id)  # fmt: skip
    if sport:
        stmt = stmt.where(A.sport == sport)
    if year:
        stmt = stmt.where(A.day >= date(year, 1, 1), A.day <= date(year, 12, 31))
    if q:
        stmt = stmt.where(func.lower(A.name).contains(q.lower()))
    if cursor:
        when, ident = _uncursor(cursor)
        if when is None:
            stmt = stmt.where(A.start_at.is_(None), A.id < ident)
        else:
            stmt = stmt.where(
                or_(
                    A.start_at < when,
                    (A.start_at == when) & (A.id < ident),
                    A.start_at.is_(None),
                )
            )
    stmt = stmt.order_by(A.start_at.is_(None), A.start_at.desc(), A.id.desc()).limit(limit + 1)
    rows = list(await session.execute(stmt))
    more = len(rows) > limit
    rows = rows[:limit]
    return ActivityPage(
        items=[ActivityItem(**r._mapping) for r in rows],
        cursor=_cursor(rows[-1]) if more and rows else None,
    )


async def _own(session, user, activity_id: uuid.UUID) -> SportActivity:
    row = await session.get(SportActivity, activity_id)
    if row is None or row.owner_id != user.id:
        raise errors.fail(404, **NOT_FOUND)
    return row


def _activity(row: SportActivity) -> Activity:
    series, splits, fixed = service.detail_series(row)
    return Activity(
        id=row.id,
        name=row.name,
        sport=row.sport,
        start_at=row.start_at,
        day=row.day,
        notes=row.notes,
        distance=row.distance,
        elapsed=row.elapsed,
        moving=row.moving,
        gain=row.gain,
        loss=row.loss,
        ele_min=row.ele_min,
        ele_max=row.ele_max,
        max_speed=row.max_speed,
        avg_hr=row.avg_hr,
        max_hr=row.max_hr,
        avg_cad=row.avg_cad,
        avg_power=row.avg_power,
        points=row.points,
        fixed=fixed,
        ele_filter=row.ele_filter,
        bbox=(row.lat_min, row.lon_min, row.lat_max, row.lon_max),
        efforts=row.efforts or {},
        source_name=row.source_name,
        source_fmt=row.source_fmt,
        series=Series(**series),
        splits=[SplitOut(**s.__dict__) for s in splits],
    )


@router.get("/activities/{activity_id}")
async def activity(activity_id: uuid.UUID, user: CurrentUser, session: Session) -> Activity:
    owner_only(user)
    row = await _own(session, user, activity_id)
    await session.refresh(row, ["track"])
    return _activity(row)


@router.patch("/activities/{activity_id}")
async def patch_activity(
    activity_id: uuid.UUID, body: ActivityPatch, user: CurrentUser, session: Session
) -> Activity:
    """Naam, sport, notities, of het hoogtefilter aan/uit. Bij een andere sport of filter
    rekent de app alles opnieuw uit de ruwe punten."""
    owner_only(user)
    row = await _own(session, user, activity_id)
    await session.refresh(row, ["track"])
    again = False
    if body.name is not None:
        row.name = body.name.strip() or row.name
    if body.notes is not None:
        row.notes = body.notes
    if body.sport is not None and body.sport != row.sport:
        row.sport = body.sport
        again = True
    if body.ele_filter is not None and body.ele_filter != row.ele_filter:
        row.ele_filter = body.ele_filter
        again = True
    if again:
        service.reanalyse(row)
    await session.commit()
    await session.refresh(row, ["track"])
    return _activity(row)


@router.delete("/activities/{activity_id}", status_code=204)
async def delete_activity(activity_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    owner_only(user)
    row = await _own(session, user, activity_id)
    await session.delete(row)
    await session.commit()


@router.get("/activities/{activity_id}/file")
async def activity_file(activity_id: uuid.UUID, user: CurrentUser, session: Session) -> Response:
    owner_only(user)
    row = await _own(session, user, activity_id)
    await session.refresh(row, ["source"])
    data = zlib.decompress(row.source)
    mime = "application/gpx+xml" if row.source_fmt == "gpx" else "application/vnd.garmin.tcx+xml"
    name = row.source_name.removesuffix(".gz") or f"activiteit.{row.source_fmt}"
    return Response(
        data,
        media_type=mime,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store",
        },
    )


def _add(t: Totals, distance: float, moving: float, gain: float) -> None:
    t.count += 1
    t.distance += distance
    t.moving += moving
    t.gain += gain


@router.get("/stats")
async def stats(
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    year: Annotated[int | None, Query(ge=1970, le=2200)] = None,
    sport: Sport | None = None,
) -> Stats:
    """Totalen: per maand van het jaar, de laatste 12 weken (maandag tot zondag), per jaar,
    per sport (dit jaar) en alles samen."""
    owner_only(user)
    A = SportActivity
    stmt = select(A.sport, A.day, A.distance, A.moving, A.gain).where(A.owner_id == user.id)
    if sport:
        stmt = stmt.where(A.sport == sport)
    rows = list(await session.execute(stmt))
    today = datetime.now(UTC).astimezone(_tz(settings)).date()
    year = year or today.year
    months = [Totals(key=f"{year}-{m:02d}") for m in range(1, 13)]
    monday = today - timedelta(days=today.weekday())
    weeks = [Totals(key=(monday - timedelta(weeks=11 - i)).isoformat()) for i in range(12)]
    first_week = monday - timedelta(weeks=11)
    per_year: dict[int, Totals] = {}
    per_sport: dict[str, Totals] = {}
    all_time = Totals(key="all")
    for r in rows:
        _add(all_time, r.distance, r.moving, r.gain)
        if r.day is None:
            continue
        _add(per_year.setdefault(r.day.year, Totals(key=str(r.day.year))), r.distance,
             r.moving, r.gain)  # fmt: skip
        if r.day.year == year:
            _add(months[r.day.month - 1], r.distance, r.moving, r.gain)
            _add(per_sport.setdefault(r.sport, Totals(key=r.sport)), r.distance, r.moving, r.gain)
        if r.day >= first_week:
            k = (r.day - first_week).days // 7
            if 0 <= k < 12:
                _add(weeks[k], r.distance, r.moving, r.gain)
    years = sorted(set(per_year) | {today.year}, reverse=True)
    return Stats(
        year=year,
        years=years,
        months=months,
        weeks=weeks,
        per_year=[per_year[y] for y in sorted(per_year)],
        per_sport=sorted(per_sport.values(), key=lambda t: -t.distance),
        all_time=all_time,
    )


@router.get("/records")
async def records(user: CurrentUser, session: Session, sport: Sport = "run") -> Records:
    """De drie snelste tijden per afstand (EFFORTS van de sport)."""
    owner_only(user)
    A = SportActivity
    rows = list(
        await session.execute(
            select(A.id, A.name, A.day, A.efforts).where(A.owner_id == user.id, A.sport == sport)
        )
    )
    out = []
    for target in track.EFFORTS.get(sport, ()):
        key = f"{target:g}"
        best = sorted(
            (
                RecordEntry(id=r.id, name=r.name, day=r.day, seconds=float(r.efforts[key]))
                for r in rows
                if r.efforts and key in r.efforts
            ),
            key=lambda e: e.seconds,
        )[:3]
        out.append(RecordRow(distance=target, best=best))
    return Records(sport=sport, rows=out)


async def _conf(session) -> dict:
    row = await session.get(Setting, tiles.KEY)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


@router.get("/config")
async def get_config(user: CurrentUser, session: Session) -> SportConfig:
    conf = await _conf(session)
    admin = user.role == "admin"
    return SportConfig(
        tiles=bool(conf.get("tiles_url")),
        attribution=conf.get("attribution") or None,
        can_edit=admin,
        tiles_url=conf.get("tiles_url") if admin else None,
    )


@router.put("/config")
async def put_config(body: SportConfigIn, admin: AdminUser, session: Session) -> SportConfig:
    """Leeg tiles_url: geen tegels (de standaard)."""
    url = (body.tiles_url or "").strip()
    if url and not tiles.valid_template(url):
        raise errors.fail(
            422,
            "sport_bad_tiles",
            "Het adres moet met https beginnen en {z}, {x} en {y} bevatten",
        )
    value = {"tiles_url": url or None, "attribution": (body.attribution or "").strip() or None}
    row = await session.get(Setting, tiles.KEY)
    if row is None:
        session.add(Setting(key=tiles.KEY, value=value))
    else:
        row.value = value
    await session.commit()
    return SportConfig(
        tiles=bool(value["tiles_url"]),
        attribution=value["attribution"],
        can_edit=True,
        tiles_url=value["tiles_url"],
    )


@router.get("/tiles/{z}/{x}/{y}")
async def tile(
    z: Annotated[int, Path(ge=0, le=tiles.MAX_ZOOM)],
    x: int,
    y: int,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    owner_only(user)
    conf = await _conf(session)
    await session.commit()
    template = conf.get("tiles_url")
    if not template:
        raise errors.fail(404, "sport_tiles_off", "Er is geen kaartbron ingesteld")
    if not (0 <= x < (1 << z) and 0 <= y < (1 << z)):
        raise errors.fail(404, **NOT_FOUND)
    try:
        data, mime = await tiles.tile(settings, template, z, x, y)
    except tiles.TileError as e:
        raise errors.fail(502, "sport_tiles_failed", "De kaartbron gaf geen tegel") from e
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})
