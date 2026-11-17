"""De plek van een foto en de bronnen van de plaatsnamen (bouwontwerp fase 6, §3.4; Z3).

- GET /files/{id}/location: alleen voor de eigenaar (Keuze 27), uit photo_geo met own_live_geo
  (eigen en levend; in de prullenbak niet), en alleen zolang zijn locatie niet op "uit" staat
  (de route kijkt naar photo_pref, Keuze 28: na "uit" meteen weg, ook voor de job wist). Iedereen
  anders, en elk bestand zonder plek, krijgt dezelfde 404 not_found: geen verschil tussen "geen
  plek" en "niet van jou". Nooit via een publieke link (daar is deze route niet). De plaats komt
  uit places.sqlite; zonder plaats binnen 30 km de naam van het land; zonder places.sqlite null.
  Cache-Control: no-store.
- GET /geo/attribution: de bronnen voor de kaart en /over; places en countries null zolang
  places.sqlite er niet is.

Nooit een plek, plaats of land in het log.
"""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select

from app import errors, photo_rows
from app.config import Settings, get_settings
from app.db import Session
from app.geo import load, places, wire  # noqa: F401 - wire meldt Z3 aan
from app.geo.types import (
    CountriesSource,
    FileLocation,
    GeoAttribution,
    PlaceName,
    PlacesSource,
    WorldSource,
)
from app.models import PhotoGeo
from app.search.live import own_live_geo
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])

# De landsgrenzen van de kaart (web/public/kaart/). Zonder woorden in een taal of haakjes: de web
# zet er "Landen: {source} ({license})." van, in het Nederlands en in het Engels
WORLD = WorldSource(source="Natural Earth 1:110m admin-0", license="publiek domein")


@router.get("/files/{node_id}/location")
async def location(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
) -> FileLocation:
    """De plek van een eigen, levende foto; anders 404 not_found (Keuze 27)."""
    response.headers["Cache-Control"] = "no-store"
    row = (
        await session.execute(
            select(
                PhotoGeo.lat, PhotoGeo.lon, PhotoGeo.alt, PhotoGeo.place_id, PhotoGeo.country
            ).where(PhotoGeo.node_id == node_id, own_live_geo(PhotoGeo, user.id))
        )
    ).first()
    if row is None or not await photo_rows.location_on(session, user.id):
        raise errors.fail(404, "not_found", "Niet gevonden")
    return FileLocation(lat=row.lat, lon=row.lon, alt=row.alt, place=place_name(settings, row))


def place_name(settings: Settings, row: Any) -> PlaceName | None:
    """De plaats (met provincie) of anders het land, uit places.sqlite."""
    db = places.get(settings)
    if db is None:
        return None
    if row.place_id is not None:
        found = db.names([row.place_id]).get(row.place_id)
        if found is not None:
            return PlaceName(name=found.name, admin1=found.admin1, cc=found.cc)
    if row.country:
        name = db.countries([row.country]).get(row.country)
        if name:
            return PlaceName(name=name, admin1=None, cc=row.country)
    return None


@router.get("/geo/attribution")
async def attribution(
    user: CurrentUser, settings: Annotated[Settings, Depends(get_settings)]
) -> GeoAttribution:
    """De bronnen: GeoNames (plaatsen, CC BY 4.0) en Natural Earth (landen en de wereldkaart,
    publiek domein). date: de dag waarop places.sqlite gebouwd is."""
    db = places.get(settings)
    if db is None:
        return GeoAttribution(places=None, countries=None, world=WORLD)
    try:
        day = date.fromisoformat(str(db.meta.get("gebouwd") or "")).isoformat()
    except ValueError:
        day = ""
    return GeoAttribution(
        places=PlacesSource(source="GeoNames cities1000", license="CC BY 4.0", date=day),
        countries=CountriesSource(
            source="Natural Earth 1:10m admin-0", license="publiek domein", version=load.NE_VERSIE
        ),
        world=WORLD,
    )
