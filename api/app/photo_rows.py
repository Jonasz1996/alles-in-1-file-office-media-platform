"""De rijen van de plek en de camera schrijven (bouwontwerp fase 6, Keuzes 25, 25a, 26 en 28; Z3).

Dit zijn de schrijvers: photometa v2 en de job die bij locatie "uit" wist. Ze schrijven voor elke
eigenaar en ook voor bestanden in de prullenbak (daar blijven de rijen, Keuze 25a), dus zonder
own_live; daarom staan ze niet onder app/geo/, waar de wachter van tests/search/test_own_live.py
elke functie die foto's leest aan own_live of own_live_geo houdt. Wat foto's leest om te tonen
(de kaart, de plaatsen, /location) staat daar wel, met own_live_geo.

- blob_meta, per inhoud: camera en objectief (gevouwen, Keuze 26) en gps 1, 0 of NULL
  (onbekend). gps zegt alleen óf er een plek in het bestand staat, nooit welke (Keuze 25), en
  blijft ook bij locatie "uit": de waarschuwing bij delen heeft hem nodig.
- photo_geo, per node van de eigenaar: write() schrijft voor elk bestand met deze inhoud, met
  in dezelfde statement de controle van Keuze 25a en 28: een bestand (kind file), van de eigenaar
  van de node, en die eigenaar heeft locatie niet op "uit". dead en taken_at zet de trigger
  geo_ai van 0010; die schrijven we nooit zelf.
- De job "geo.off" (key per eigenaar): na locatie "uit" alle rijen van die eigenaar weg, in
  stukken van CHUNK (één transactie per stuk: het schrijfslot van SQLite blijft kort). Staat de
  schakelaar intussen weer op "mij", dan stopt hij: dan is wat er staat weer goed, en leest
  photometa de rest opnieuw. Een nieuwe "uit" zet altijd een nieuwe job klaar, ook als er een
  loopt; de opruimronde (sweep_off) zet hem opnieuw klaar voor wie "uit" heeft en nog rijen heeft.
- copied(): heeft een gekopieerd bestand een inhoud met een plek (voor de haak van een kopie).

Nooit een plek, een plaats of een eigenaar in het log: alleen aantallen.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, Integer, String, Text, bindparam, delete, exists, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import jobs
from app.db import get_engine
from app.geo import tiles
from app.models import BlobMeta, Node, PhotoGeo, PhotoPref

if TYPE_CHECKING:
    from app.config import Settings
    from app.geo.types import Place

log = logging.getLogger("platform.geo")

OFF_TYPE = "geo.off"
SWEEP = "geo.off_sweep"
SWEEP_EVERY = 3600.0
CHUNK = 2000
SOURCES = ("exif", "xmp", "video")

_NODE = Node.__table__
_GEO = PhotoGeo.__table__
_META = BlobMeta.__table__


def _excluded(table: Any, cols: Iterable[str]) -> dict[str, Any]:
    """De waarden van de nieuwe rij bij een conflict (excluded.kolom). Een upsert, nooit REPLACE:
    die wist zonder DELETE-trigger (Keuze 3). De insert staat in dezelfde keten als de upsert,
    zodat de wachter van tests/search/test_search_no_replace.py de tabel ziet."""
    excluded = sqlite_insert(table).excluded
    return {c: excluded[c] for c in cols}


def off_key(user_id: uuid.UUID) -> str:
    return f"geo.off:{user_id.hex}"


def not_off(owner: Any) -> Any:
    """De eigenaar heeft locatie niet op "uit" (zonder rij in photo_pref: "mij", Keuze 28)."""
    return ~exists().where(PhotoPref.user_id == owner, PhotoPref.location == "uit")


async def location_on(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Mag de plek van deze gebruiker er zijn (en gelezen worden)? Een gast heeft geen eigen
    foto's; dat zegt de route zelf."""
    value = await db.scalar(select(PhotoPref.location).where(PhotoPref.user_id == user_id))
    return value != "uit"


async def set_meta(db: AsyncSession, rows: Iterable[dict[str, Any]]) -> None:
    """blob_meta voor deze inhoud: {"blob_sha", "camera", "lens", "gps"}, nieuw of vervangen."""
    values = list(rows)
    if not values:
        return
    await db.execute(
        sqlite_insert(BlobMeta.__table__).on_conflict_do_update(
            index_elements=[_META.c.blob_sha], set_=_excluded(_META, ("camera", "lens", "gps"))
        ),
        values,
    )


@dataclass(frozen=True)
class Spot:
    """Een plek uit render_meta, nagekeken: wat photo_geo per node krijgt."""

    lat: float
    lon: float
    alt: float | None
    source: str


def valid(loc: Any) -> Spot | None:
    """De plek uit render_meta (al gecontroleerd in het renderproces); hier nog eens de vorm,
    want het antwoord van een ander proces is onvertrouwd. None: geen (bruikbare) plek."""
    if not isinstance(loc, dict):
        return None
    try:
        lat, lon = float(loc["lat"]), float(loc["lon"])
        alt = None if loc.get("alt") is None else float(loc["alt"])
    except (KeyError, TypeError, ValueError):
        return None
    source = loc.get("source")
    if source not in SOURCES or not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
        return None
    if alt is not None and not math.isfinite(alt):
        alt = None
    return Spot(lat, lon, alt, source)


_COLS = ("node_id", "owner_id", "lat", "lon", "alt", "x16", "y16", "place_id", "country",
         "admin1", "source")  # fmt: skip


def _insert(replace: bool) -> Any:
    """INSERT … SELECT voor elk bestand met de inhoud :g_sha, met de controle van Keuze 25a en
    28 in dezelfde statement; per inhoud één stel parameters (executemany)."""
    sel = select(
        _NODE.c.id,
        _NODE.c.owner_id,
        bindparam("g_lat", type_=Float),
        bindparam("g_lon", type_=Float),
        bindparam("g_alt", type_=Float),
        bindparam("g_x16", type_=Integer),
        bindparam("g_y16", type_=Integer),
        bindparam("g_place", type_=Integer),
        bindparam("g_cc", type_=String),
        bindparam("g_admin1", type_=Text),
        bindparam("g_source", type_=String),
    ).where(
        _NODE.c.blob_id == bindparam("g_sha"),
        _NODE.c.kind == "file",
        _NODE.c.owner_id.is_not(None),
        not_off(_NODE.c.owner_id),
    )
    if not replace:
        sel = sel.where(~exists().where(_GEO.c.node_id == _NODE.c.id))
    if replace:
        return (
            sqlite_insert(PhotoGeo.__table__)
            .from_select(list(_COLS), sel)
            .on_conflict_do_update(index_elements=[_GEO.c.node_id], set_=_excluded(_GEO, _COLS[2:]))
        )
    return (
        sqlite_insert(PhotoGeo.__table__)
        .from_select(list(_COLS), sel)
        .on_conflict_do_nothing(index_elements=[_GEO.c.node_id])
    )


async def write(
    db: AsyncSession, found: Sequence[tuple[str, Spot, Place]], *, replace: bool
) -> None:
    """De plek voor elk bestand met deze inhoud (sha, plek, plaats), per node van zijn
    eigenaar, als die eigenaar locatie niet op "uit" heeft (in dezelfde statement, Keuze 28).
    replace: een rij die er al is, krijgt de nieuwe waarden (de inhoud is opnieuw gelezen);
    anders alleen wat ontbreekt. Eén statement voor alles samen."""
    params = []
    for sha, spot, place in found:
        x16, y16 = tiles.tile16(spot.lat, spot.lon)
        params.append(
            {
                "g_sha": sha,
                "g_lat": round(spot.lat, 7),
                "g_lon": round(spot.lon, 7),
                "g_alt": spot.alt,
                "g_x16": x16,
                "g_y16": y16,
                "g_place": place.id,
                "g_cc": place.cc,
                "g_admin1": place.admin1,
                "g_source": spot.source,
            }
        )
    if params:
        await db.execute(_insert(replace), params)


async def clear(db: AsyncSession, shas: Sequence[str]) -> None:
    """Geen plek (meer) in deze inhoud: de rijen van elk bestand ermee weg (één statement)."""
    if not shas:
        return
    await db.execute(
        delete(_GEO).where(
            _GEO.c.node_id.in_(select(_NODE.c.id).where(_NODE.c.blob_id == bindparam("g_sha")))
        ),
        [{"g_sha": sha} for sha in shas],
    )


async def wipe_chunk(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Hooguit CHUNK rijen van deze eigenaar weg; geeft hoeveel."""
    chunk = select(PhotoGeo.node_id).where(PhotoGeo.owner_id == user_id).limit(CHUNK)
    result = await db.execute(
        delete(PhotoGeo)
        .where(PhotoGeo.node_id.in_(chunk))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def run_off(settings: Settings, payload: dict[str, Any]) -> None:
    """De job geo.off: alle rijen van deze eigenaar weg, zolang hij locatie op "uit" heeft."""
    try:
        user_id = uuid.UUID(str(payload.get("user")))
    except ValueError:
        return
    maker = _maker(settings)
    removed = 0
    while True:
        async with maker() as db:
            if await location_on(db, user_id):
                break  # weer "mij": wat er staat mag blijven
            n = await wipe_chunk(db, user_id)
            await db.commit()
        removed += n
        if n < CHUNK:
            break
    log.info("geo_off_done rows=%d", removed)


async def copied(db: AsyncSession, node_ids: Sequence[uuid.UUID]) -> bool:
    """Heeft een van deze bestanden een inhoud met een plek (blob_meta.gps = 1)?"""
    ids = list(node_ids)
    for i in range(0, len(ids), 500):
        found = await db.scalar(
            select(_NODE.c.id)
            .join(_META, _META.c.blob_sha == _NODE.c.blob_id)
            .where(_NODE.c.id.in_(ids[i : i + 500]), _META.c.gps == 1)
            .limit(1)
        )
        if found is not None:
            return True
    return False


async def enqueue_off(db: AsyncSession, user_id: uuid.UUID) -> None:
    """In de transactie van de aanroeper (PATCH /me/photo-settings): de job die wist. Alleen
    ontdubbeld tegen een job die nog klaarstaat, niet tegen een die loopt (jobs.chain): die kan
    net "mij" gezien hebben en stoppen, en dan moet deze "uit" toch nog wissen (review golf 3,
    veilig F2)."""
    await jobs.chain(db, OFF_TYPE, {"user": str(user_id)}, off_key(user_id))


async def sweep_off(settings: Settings) -> int:
    """De opruimronde (bij de start en daarna elk SWEEP_EVERY): geo.off klaarzetten voor elke
    gebruiker die "uit" heeft en nog rijen heeft (een job die definitief mislukte, of een proces
    dat stopte). Geeft het aantal nieuwe jobs; staat er al een klaar of loopt er een, dan niet."""
    added = 0
    async with _maker(settings)() as db:
        users = await db.scalars(
            select(PhotoPref.user_id).where(
                PhotoPref.location == "uit",
                exists().where(PhotoGeo.owner_id == PhotoPref.user_id),
            )
        )
        for user_id in users.all():
            if await jobs.enqueue(db, OFF_TYPE, {"user": str(user_id)}, key=off_key(user_id)):
                added += 1
        await db.commit()
    if added:
        log.info("geo_off_sweep jobs=%d", added)
    return added


jobs.register(OFF_TYPE, run_off)
jobs.housekeeping(SWEEP, sweep_off, SWEEP_EVERY)
