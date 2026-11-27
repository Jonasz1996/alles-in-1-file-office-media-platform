"""Importeren en opvragen van sportactiviteiten (de routes staan in app/routers/sport.py).

Importeren: een GPX- of TCX-bestand, ook met gzip (.gpx.gz, zoals de export van Strava), of een
zip met zulke bestanden (een export van Strava, Endurain of Garmin). Per bestand een uitkomst:
nieuw, al aanwezig (zelfde inhoud: sha256 van de bytes), of een fout met een reden. Het lezen en
rekenen gebeurt in een thread, zodat de app blijft antwoorden.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import math
import uuid
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import IO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio.to_thread
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SportActivity
from app.sport import pack, track
from app.sport.parse import MAX_BYTES, ParseError, parse

# Een zip: hooguit zoveel bestanden en zoveel bytes uitgepakt (tegen een zipbom)
ZIP_MAX_FILES = 5000
ZIP_MAX_TOTAL = 4 * 1024 * 1024 * 1024
EXTENSIONS = (".gpx", ".tcx", ".gpx.gz", ".tcx.gz")
PREVIEW_POINTS = 120
SERIES_POINTS = 1500


@dataclass
class Outcome:
    name: str
    status: str  # new | duplicate | error
    id: uuid.UUID | None = None
    reason: str | None = None


def tz_of(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_zip(head: bytes) -> bool:
    return head[:4] == b"PK\x03\x04"


def wanted(name: str) -> bool:
    low = name.lower()
    return low.endswith(EXTENSIONS) and not low.rsplit("/", 1)[-1].startswith(".")


def _gunzip(name: str, data: bytes) -> bytes:
    if not name.lower().endswith(".gz"):
        return data
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
        out = gz.read(MAX_BYTES + 1)
    if len(out) > MAX_BYTES:
        raise ParseError("te groot")
    return out


def zip_entries(fh: IO[bytes]) -> Iterator[tuple[str, bytes | None, str | None]]:
    """(naam, bytes, None) per GPX/TCX in de zip, of (naam, None, reden) als het niet kan."""
    with zipfile.ZipFile(fh) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and wanted(i.filename)]
        if len(infos) > ZIP_MAX_FILES:
            raise ParseError("te veel bestanden in de zip")
        total = 0
        for info in infos:
            name = info.filename.rsplit("/", 1)[-1]
            if info.file_size > MAX_BYTES * (8 if name.lower().endswith(".gz") else 1):
                yield name, None, "te groot"
                continue
            total += info.file_size
            if total > ZIP_MAX_TOTAL:
                yield name, None, "de zip is te groot"
                return
            with zf.open(info) as f:
                data = f.read(MAX_BYTES * 8 + 1)
            yield name, data, None


@dataclass
class Built:
    row: SportActivity
    sha: str


def _preview(raw: track.Raw) -> str:
    n = len(raw)
    if n == 0:
        return ""
    step = max(1, math.ceil(n / PREVIEW_POINTS))
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    lat0 = (min(raw.lat) + max(raw.lat)) / 2
    k = math.cos(math.radians(lat0))
    xs = [raw.lon[i] * k for i in idx]
    ys = [-raw.lat[i] for i in idx]
    x0, y0 = min(xs), min(ys)
    span = max(max(xs) - x0, max(ys) - y0) or 1e-9
    return " ".join(
        f"{round((x - x0) / span * 1000)},{round((y - y0) / span * 1000)}"
        for x, y in zip(xs, ys, strict=True)
    )


def apply(row: SportActivity, raw: track.Raw, a: track.Analysis) -> None:
    """De cijfers van een analyse op de rij zetten."""
    row.distance = a.distance
    row.elapsed = a.elapsed
    row.moving = a.moving
    row.gain = a.gain
    row.loss = a.loss
    row.ele_min = a.ele_min
    row.ele_max = a.ele_max
    row.max_speed = a.max_speed
    row.avg_hr = a.avg_hr
    row.max_hr = a.max_hr
    row.avg_cad = a.avg_cad
    row.avg_power = a.avg_power
    row.points = len(raw)
    row.fixed = a.fixed
    row.lat_min, row.lon_min, row.lat_max, row.lon_max = a.bbox
    row.efforts = a.efforts


def build(owner: uuid.UUID, name: str, data: bytes, tz: ZoneInfo) -> Built:
    """Lezen en rekenen (in een thread). ParseError als het geen bruikbaar bestand is."""
    data = _gunzip(name, data)
    sha = hashlib.sha256(data).hexdigest()
    parsed = parse(data)
    raw, start = track.from_parsed(parsed)
    if len(raw) < 2:
        raise ParseError("te weinig punten")
    first = track.analyse(raw, "other")
    sport = track.guess_sport(parsed.sport_hint, raw, first)
    a = track.analyse(raw, sport)
    local = start.astimezone(tz) if start else None
    row = SportActivity(
        id=uuid.uuid4(),
        owner_id=owner,
        name=(parsed.name or track.default_name(sport, local))[:255],
        sport=sport,
        start_at=start,
        day=local.date() if local else None,
        notes=(parsed.description or "")[:10000],
        ele_filter=True,
        track=pack.pack(raw),
        source=zlib.compress(data, 6),
        source_name=name[:255],
        source_fmt=parsed.fmt,
        source_sha=sha,
        preview=_preview(raw),
    )
    apply(row, raw, a)
    return Built(row=row, sha=sha)


async def import_one(
    session: AsyncSession, owner: uuid.UUID, name: str, data: bytes, tz: ZoneInfo
) -> Outcome:
    try:
        built = await anyio.to_thread.run_sync(build, owner, name, data, tz)
    except (ParseError, OSError, EOFError, zlib.error) as e:
        return Outcome(name=name, status="error", reason=str(e) or "onleesbaar")
    found = await session.scalar(
        select(SportActivity.id).where(
            SportActivity.owner_id == owner, SportActivity.source_sha == built.sha
        )
    )
    if found is not None:
        return Outcome(name=name, status="duplicate", id=found)
    session.add(built.row)
    await session.commit()
    return Outcome(name=name, status="new", id=built.row.id)


def reanalyse(row: SportActivity) -> None:
    """Na een andere sport of het hoogtefilter aan/uit: alles opnieuw uit de ruwe punten."""
    raw = pack.unpack(row.track)
    apply(row, raw, track.analyse(raw, row.sport, ele_filter=row.ele_filter))


def _pick(n: int, limit: int) -> list[int]:
    if n <= limit:
        return list(range(n))
    step = (n - 1) / (limit - 1)
    return sorted({round(i * step) for i in range(limit)})


def _r(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(v, nd)


def detail_series(row: SportActivity) -> tuple[dict, list[track.Split], int]:
    """De punten voor de kaart en de grafieken (hooguit SERIES_POINTS), de splits en het
    aantal herstelde hoogtes. Een kolom zonder één waarde is null."""
    raw = pack.unpack(row.track)
    a = track.analyse(raw, row.sport, ele_filter=row.ele_filter)
    idx = _pick(len(raw), SERIES_POINTS)

    def col(values, nd=1):
        out = [_r(values[i], nd) for i in idx]
        return out if any(v is not None for v in out) else None

    series = {
        "lat": [round(raw.lat[i], 6) for i in idx],
        "lon": [round(raw.lon[i], 6) for i in idx],
        "d": [round(a.dist[i], 1) for i in idx],
        "t": col(raw.t, 0),
        "ele": col(a.ele, 1) if a.has_ele else None,
        "ele_raw": col(raw.ele, 1),
        "hr": col(raw.hr, 0),
        "cad": col(raw.cad, 0),
        "power": col(raw.power, 0),
        "speed": col(a.speed, 2),
    }
    return series, a.splits, a.fixed


def day_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)
