"""Land en plaats bij een plek, uit places.sqlite (bouwontwerp fase 6, Keuze 29).

- Het land: punt-in-polygoon op Natural Earth 10m (even-oneven over alle ringen van een vlak;
  de R*Tree `country_box` geeft de vlakken waarvan het kader het punt bevat). Buiten elk land:
  geen land ("op zee"), en dan ook geen plaats.
- De plaats: de dichtstbijzijnde uit cities1000 binnen 30 km én in hetzelfde land, met
  provincie (admin1). De R*Tree `place_box` geeft de kandidaten in een kader dat ruim genoeg is
  voor 30 km (ook over 180°); de afstand is haversine. Anders alleen het land.

Ontbreekt places.sqlite of hoort hij bij een ander schema, dan is er niets (get() geeft None):
geen plaatsnamen, de rest werkt gewoon. Alleen lezen; één verbinding per bestand, achter een
slot (photometa leest in een thread, de routes in de lus).
"""

from __future__ import annotations

import math
import sqlite3
import struct
import threading
import zlib
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.geo import load
from app.geo.types import Place

if TYPE_CHECKING:
    from app.config import Settings

R_KM = 6371.0  # zoals haversine_km in de tests
KM = 30.0  # de grens van Keuze 29
_GRAAD_KM = R_KM * math.pi / 180
_VLAKKEN_IN_GEHEUGEN = 64

NIETS = Place(id=None, name=None, admin1=None, cc=None, km=None)

# GeoNames geeft gebieden overzee een eigen code; Natural Earth 10m rekent ze bij het land.
# Gemeten op de bronnen: de plaatsen met deze codes die op land liggen, liggen (op drie bij de
# grens van Frans-Guyana na) in de polygoon van dat land. Voor "hetzelfde land"
GEBIED = {"GP": "FR", "RE": "FR", "MQ": "FR", "YT": "FR", "GF": "FR", "BQ": "NL"}


@dataclass(frozen=True)
class PlaceRow:
    id: int
    name: str
    admin1: str | None
    cc: str


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2
    return 2 * R_KM * math.asin(min(1.0, math.sqrt(h)))


def _uitpakken(blob: bytes) -> list[tuple[float, float, float, float, list[float], list[float]]]:
    """De ringen van een vlak: per ring het kader en de lengtes en breedtes."""
    data = zlib.decompress(blob)
    (n,) = struct.unpack_from("<I", data, 0)
    lengtes = struct.unpack_from(f"<{n}I", data, 4)
    punten = struct.unpack_from(f"<{2 * sum(lengtes)}f", data, 4 + 4 * n)
    uit = []
    i = 0
    for lengte in lengtes:
        xs = list(punten[i : i + 2 * lengte : 2])
        ys = list(punten[i + 1 : i + 2 * lengte : 2])
        i += 2 * lengte
        uit.append((min(xs), max(xs), min(ys), max(ys), xs, ys))
    return uit


def _kruisingen(x: float, y: float, xs: list[float], ys: list[float]) -> bool:
    """Even-oneven: kruist een straal naar rechts de ring een oneven aantal keer?"""
    binnen = False
    xj, yj = xs[-1], ys[-1]
    for xi, yi in zip(xs, ys, strict=True):
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            binnen = not binnen
        xj, yj = xi, yi
    return binnen


class Places:
    """Een geopende places.sqlite."""

    def __init__(self, pad: Path) -> None:
        self._db = sqlite3.connect(f"{pad.as_uri()}?mode=ro", uri=True, check_same_thread=False)
        self._lock = threading.Lock()
        self._vlakken: OrderedDict[int, list] = OrderedDict()
        meta = dict(self._db.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("schema") != load.SCHEMA:
            self._db.close()
            raise sqlite3.DatabaseError("ander schema")
        self.meta = meta

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _vlak(self, part: int, blob: bytes) -> list:
        ringen = self._vlakken.get(part)
        if ringen is None:
            ringen = _uitpakken(blob)
            self._vlakken[part] = ringen
            if len(self._vlakken) > _VLAKKEN_IN_GEHEUGEN:
                self._vlakken.popitem(last=False)
        else:
            self._vlakken.move_to_end(part)
        return ringen

    def country(self, lat: float, lon: float) -> tuple[bool, str | None]:
        """(op land, de code). Op land kan de code None zijn (Natural Earth zegt -99)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT b.id, p.rings, c.a2 FROM country_box AS b "
                "JOIN country_part AS p ON p.id = b.id JOIN country AS c ON c.id = p.country "
                "WHERE b.min_lon <= ?1 AND b.max_lon >= ?1 AND b.min_lat <= ?2 AND b.max_lat >= ?2 "
                "ORDER BY b.id",
                (lon, lat),
            ).fetchall()
            for part, blob, a2 in rows:
                binnen = False
                for x0, x1, y0, y1, xs, ys in self._vlak(part, blob):
                    if x0 <= lon <= x1 and y0 <= lat <= y1 and _kruisingen(lon, lat, xs, ys):
                        binnen = not binnen
                if binnen:
                    return True, a2
        return False, None

    def _kandidaten(self, lat: float, lon: float, km: float) -> list[tuple]:
        dlat = km / _GRAAD_KM * 1.05
        rand = min(89.9, abs(lat) + dlat)
        dlon = dlat / math.cos(math.radians(rand)) * 1.1
        lat0, lat1 = max(-90.0, lat - dlat), min(90.0, lat + dlat)
        if dlon >= 180 or rand >= 89.9:
            vakken = [(-180.0, 180.0)]
        else:
            vakken = [(max(-180.0, lon - dlon), min(180.0, lon + dlon))]
            if lon - dlon < -180:
                vakken.append((lon - dlon + 360, 180.0))
            if lon + dlon > 180:
                vakken.append((-180.0, lon + dlon - 360))
        uit = []
        for x0, x1 in vakken:
            uit += self._db.execute(
                "SELECT p.id, p.name, p.admin1, p.cc, p.lat, p.lon FROM place_box AS b "
                "JOIN place AS p ON p.id = b.id "
                "WHERE b.max_lon >= ? AND b.min_lon <= ? AND b.max_lat >= ? AND b.min_lat <= ?",
                (x0, x1, lat0, lat1),
            ).fetchall()
        return uit

    def nearest(self, lat: float, lon: float, cc: str, km: float = KM) -> Place | None:
        """De dichtstbijzijnde plaats in land cc binnen km, of None."""
        with self._lock:
            kandidaten = self._kandidaten(lat, lon, km)
        beste: tuple[float, int] | None = None
        gevonden = None
        for pid, name, admin1, pcc, plat, plon in kandidaten:
            if GEBIED.get(pcc, pcc) != cc:
                continue
            d = haversine_km(lat, lon, plat, plon)
            if d <= km and (beste is None or (d, pid) < beste):
                beste, gevonden = (d, pid), (pid, name, admin1)
        if gevonden is None or beste is None:
            return None
        return Place(id=gevonden[0], name=gevonden[1], admin1=gevonden[2], cc=cc, km=beste[0])

    def lookup(self, lat: float, lon: float) -> Place:
        """Land en plaats bij een plek. Op zee: NIETS."""
        op_land, cc = self.country(lat, lon)
        if not op_land or cc is None:
            return NIETS
        return self.nearest(lat, lon, cc) or Place(id=None, name=None, admin1=None, cc=cc, km=None)

    def names(self, ids: Iterable[int]) -> dict[int, PlaceRow]:
        """De namen van deze plaatsen (een id dat er niet is, ontbreekt)."""
        ids = sorted({int(i) for i in ids})
        uit: dict[int, PlaceRow] = {}
        with self._lock:
            for i in range(0, len(ids), 500):
                stuk = ids[i : i + 500]
                vragen = ",".join("?" * len(stuk))
                for pid, name, admin1, cc in self._db.execute(
                    f"SELECT id, name, admin1, cc FROM place WHERE id IN ({vragen})", stuk
                ):
                    uit[pid] = PlaceRow(pid, name, admin1, cc)
        return uit

    def countries(self, codes: Iterable[str]) -> dict[str, str]:
        """De naam van deze landen (Nederlands, anders Engels); een code die er niet is,
        ontbreekt."""
        wanted = sorted({c for c in codes if isinstance(c, str) and len(c) == 2})
        if not wanted:
            return {}
        vragen = ",".join("?" * len(wanted))
        with self._lock:
            rows = self._db.execute(
                f"SELECT a2, coalesce(name_nl, name_en) FROM country WHERE a2 IN ({vragen}) "
                "ORDER BY id",
                wanted,
            ).fetchall()
        uit: dict[str, str] = {}
        for a2, name in rows:
            if name:
                uit.setdefault(a2, name)
        return uit


_open: dict[Path, Places] = {}
_open_lock = threading.Lock()


def get(settings: Settings) -> Places | None:
    """De plaatsen van deze instellingen, of None als places.sqlite er niet (goed) is."""
    pad = Path(settings.places_db)
    with _open_lock:
        p = _open.get(pad)
        if p is not None:
            return p
        if settings.places == "uit" or not pad.is_file():
            return None
        try:
            p = Places(pad)
        except sqlite3.Error:
            return None
        _open[pad] = p
        return p


def forget() -> None:
    """Na een nieuwe bouw (en in tests): alles opnieuw openen."""
    with _open_lock:
        for p in _open.values():
            p.close()
        _open.clear()
