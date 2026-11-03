"""De plek van een foto en de plaats die erbij hoort (bouwontwerp fase 6, §3.3). Z0, vast.

Een Location komt uit de foto zelf (EXIF, XMP) of uit een video; een Place is wat Z3 daar in
GeoNames bij vindt (de dichtstbijzijnde plaats, km de afstand). Alle velden van Place kunnen
leeg zijn: zonder geladen plaatsen (geo_not_loaded) is er geen plaats.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
