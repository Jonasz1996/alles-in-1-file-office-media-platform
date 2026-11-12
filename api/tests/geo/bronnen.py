"""Kleine bronnen voor geo-load, door de test zelf gemaakt (alleen de standaardbibliotheek):
een GeoJSON met vier landen en een tar.gz met rg_cities1000.csv op dezelfde plek als in de
sdist van reverse_geocoder. Met maak() krijg je de map en de Bron'nen met hun sha256; zet die
met gebruik() in app.geo.load, dan bouwt ensure() er places.sqlite van.

De landen (lengte, breedte), met de hand gekozen zodat elke regel van Keuze 29 een geval heeft:

- NL: het vlak 3,3–7,2 × 50,75–53,6 met een gat (een meer) 5,0–5,5 × 52,4–52,8: in het gat is
  geen land (even-oneven over de ringen);
- BE: 2,5–6,4 × 49,5–50,75, tegen NL aan;
- FJ: twee vlakken, aan weerszijden van 180° (177–180 en −180 tot −178, −19 tot −16);
- XX: een land zonder geldige code (Natural Earth zegt -99): op land, maar geen land.

De plaatsen: Utrecht en Amsterdam (NL), Grensdorp (NL, 5 km van de grens) met Grenshoek (BE,
1,7 km van het punt GRENS maar in een ander land), Turnhout (BE), Suva en Oostkaap (FJ, Oostkaap
net over 180°), en een paar regels die niet kloppen (overgeslagen).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path

from app.geo import load

GRENS = (50.755, 5.0)  # in NL, net boven de grens met BE
MEER = (52.6, 5.25)  # in het gat van NL
ZEE = (54.5, 4.0)
OVER_180 = (-17.0, 179.95)  # FJ, 16 km van Oostkaap aan de andere kant van 180°
ZONDER_CODE = (10.0, 10.0)


def _vierkant(w: float, z: float, o: float, n: float) -> list[list[float]]:
    return [[w, z], [o, z], [o, n], [w, n], [w, z]]


def _land(a2: str, nl: str | None, en: str, vlakken: list) -> dict:
    geo = (
        {"type": "Polygon", "coordinates": vlakken[0]}
        if len(vlakken) == 1
        else {"type": "MultiPolygon", "coordinates": vlakken}
    )
    props = {"ISO_A2_EH": a2, "ISO_A2": a2, "ADM0_A3": a2 + "X", "NAME_EN": en, "NAME": en}
    if nl:
        props["NAME_NL"] = nl
    return {"type": "Feature", "properties": props, "geometry": geo}


def geojson() -> bytes:
    landen = [
        _land("NL", "Nederland", "Netherlands",
              [[_vierkant(3.3, 50.75, 7.2, 53.6), _vierkant(5.0, 52.4, 5.5, 52.8)]]),
        _land("BE", "België", "Belgium", [[_vierkant(2.5, 49.5, 6.4, 50.75)]]),
        _land("FJ", None, "Fiji",
              [[_vierkant(177.0, -19.0, 180.0, -16.0)], [_vierkant(-180.0, -19.0, -178.0, -16.0)]]),
        _land("-99", None, "Nergensland", [[_vierkant(9.0, 9.0, 11.0, 11.0)]]),
    ]  # fmt: skip
    return json.dumps({"type": "FeatureCollection", "features": landen}).encode()


PLAATSEN = (
    # lat, lon, name, admin1, admin2, cc
    ("52.09083", "5.12222", "Utrecht", "Utrecht", "", "NL"),
    ("52.37403", "4.88969", "Amsterdam", "North Holland", "", "NL"),
    ("50.80000", "5.00000", "Grensdorp", "Limburg", "", "NL"),
    ("50.74000", "5.00000", "Grenshoek", "Flanders", "", "BE"),
    ("51.32254", "4.94471", "Turnhout", "Flanders", "", "BE"),
    ("-18.14161", "178.44149", "Suva", "Central", "", "FJ"),
    ("-17.00000", "-179.90000", "Oostkaap", "Eastern", "", "FJ"),
    ("niet", "5.0", "Kapot", "", "", "NL"),
    ("91.0", "5.0", "Te noordelijk", "", "", "NL"),
    ("52.0", "5.0", "Zonder land", "", "", "-99"),
)


def csv_bytes(rijen=PLAATSEN) -> bytes:
    f = io.StringIO(newline="")
    w = csv.writer(f)
    w.writerow(["lat", "lon", "name", "admin1", "admin2", "cc"])
    w.writerows(rijen)
    return f.getvalue().encode()


def tar_gz(inhoud: bytes | None = None, *, symlink: bool = False) -> bytes:
    """De sdist in het klein: alleen rg_cities1000.csv op zijn plek (of een symlink daar)."""
    inhoud = csv_bytes() if inhoud is None else inhoud
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        lid = tarfile.TarInfo(load.CSV_IN_TAR)
        if symlink:
            lid.type, lid.linkname = tarfile.SYMTYPE, "/etc/passwd"
            tar.addfile(lid)
        else:
            lid.size = len(inhoud)
            tar.addfile(lid, io.BytesIO(inhoud))
    return buf.getvalue()


@dataclass
class Bronnen:
    map: Path
    landen: load.Bron
    plaatsen: load.Bron


def _bron(echt: load.Bron, data: bytes) -> load.Bron:
    return load.Bron(echt.naam, "", len(data), hashlib.sha256(data).hexdigest())


def maak(map_: Path, landen: bytes | None = None, plaatsen: bytes | None = None) -> Bronnen:
    """Schrijf de bronnen in map_ met hun eigen sha256 (dus geldig voor geo-load)."""
    landen = geojson() if landen is None else landen
    plaatsen = tar_gz() if plaatsen is None else plaatsen
    map_.mkdir(parents=True, exist_ok=True)
    (map_ / load.LANDEN.naam).write_bytes(landen)
    (map_ / load.PLAATSEN.naam).write_bytes(plaatsen)
    return Bronnen(map_, _bron(load.LANDEN, landen), _bron(load.PLAATSEN, plaatsen))


def gebruik(monkeypatch, b: Bronnen) -> None:
    """app.geo.load verwacht voortaan deze bronnen (hun sha256), zoals een nieuwe versie."""
    monkeypatch.setattr(load, "LANDEN", b.landen)
    monkeypatch.setattr(load, "PLAATSEN", b.plaatsen)
