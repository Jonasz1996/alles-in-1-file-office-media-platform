"""De plek van een foto en de plaats die erbij hoort (bouwontwerp fase 6, §3.3). Z0, vast.

De dataclasses van §3.3 (Location, Place) en de vormen van de API van §3.4 voor de plek, de
kaart, de plaatsen en de duplicaten (Z3; alleen eigen, levende bestanden: own_live).

Een Location komt uit de foto zelf (EXIF, XMP) of uit een video; een Place is wat Z3 daar in
GeoNames bij vindt (de dichtstbijzijnde plaats, km de afstand). Alle velden van Place kunnen
leeg zijn: zonder geladen plaatsen (geo_not_loaded) is er geen plaats.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.routers.files import NodeOut, Utc
from app.routers.photos import PhotoItem
from app.search.types import PathPart


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    alt: float | None
    source: Literal["exif", "xmp", "video"]


@dataclass(frozen=True)
class Place:
    id: int | None
    name: str | None
    admin1: str | None
    cc: str | None
    km: float | None


# --- API (§3.4): plek, kaart en plaatsen ---


class PlaceName(BaseModel):
    name: str
    admin1: str | None
    cc: str | None


class FileLocation(BaseModel):
    """GET /files/{id}/location; 404 not_found als er geen is of je hem niet mag zien."""

    lat: float
    lon: float
    alt: float | None
    place: PlaceName | None


class MapCell(BaseModel):
    x: int
    y: int
    n: int
    lat: float
    lon: float
    cover: uuid.UUID


class MapCells(BaseModel):
    """GET /photos/map?bbox=w,s,e,n&z=0..14&<filters>: hooguit 500 cellen."""

    cells: list[MapCell] = Field(max_length=500)


class MapItems(BaseModel):
    """GET /photos/map/items?bbox=&<filters>&cursor=."""

    items: list[PhotoItem]
    cursor: str | None


class PlaceCount(BaseModel):
    """Een plaats met het aantal eigen, levende foto's (per verzoek geteld)."""

    id: int
    name: str
    admin1: str | None
    country: str | None
    n: int


class Places(BaseModel):
    """GET /photos/places?<filters>."""

    places: list[PlaceCount]


class ShareCheck(BaseModel):
    """GET /photos/share-check?node=: hoeveel van wat je deelt een plek heeft, en van hoeveel
    dat nog niet bekend is (blob_meta.gps)."""

    with_location: int
    unknown: int


class PlacesSource(BaseModel):
    source: str
    license: str
    date: str


class CountriesSource(BaseModel):
    source: str
    license: str
    version: str


class WorldSource(BaseModel):
    source: str
    license: str


class GeoAttribution(BaseModel):
    """GET /geo/attribution: de bronnen, of null zolang ze niet geladen zijn."""

    places: PlacesSource | None
    countries: CountriesSource | None
    world: WorldSource


# --- API (§3.4): duplicaten ---


class DupeItem(BaseModel):
    node: NodeOut
    path: list[PathPart]
    size: int
    taken_at: Utc | None


class DupeGroup(BaseModel):
    key: str
    kind: Literal["exact", "near"]
    level: Literal["zeker", "lijkt"] | None
    dist: int | None
    items: list[DupeItem]


class DupePage(BaseModel):
    """GET /duplicates?kind=exact|near&cursor=."""

    groups: list[DupeGroup]
    cursor: str | None


class DupeIgnoreIn(BaseModel):
    """POST /duplicates/ignore → 204; 400 dupes_bad_key."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=200)
