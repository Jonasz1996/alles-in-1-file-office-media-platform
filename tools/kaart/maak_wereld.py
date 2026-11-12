#!/usr/bin/env python3
"""De landsgrenzen voor de kaart: world-110m.<hash>.json (bouwontwerp fase 6, Keuze 31; spike S6).

Alleen de standaardbibliotheek. Leest de GeoJSON van Natural Earth 110m admin-0 (tag v5.1.2,
publiek domein), controleert de sha256, en schrijft een klein JSON-bestand dat de route
/fotos/kaart met fetch laadt (niet in de JS):

    python3 -I tools/kaart/maak_wereld.py --bron ne_110m_admin_0_countries.geojson --uit MAP
    python3 -I tools/kaart/maak_wereld.py --download --uit MAP

--download haalt BRON_URL op (raw.githubusercontent.com) en weigert bij een andere sha256.

De vorm: per land de code (ADM0_A3, en ISO_A2_EH als die er is), de naam in het Nederlands en
het Engels, en de ringen van elk vlak. Een ring is één lijst gehele getallen: het eerste punt in
honderdsten van een graad (lengte, breedte), daarna steeds het verschil met het vorige punt. Het
laatste punt (gelijk aan het eerste) staat er niet in, en een punt dat na het afronden gelijk is
aan het vorige ook niet. Een honderdste graad is ±1,1 km; 110m is op ±10 km vereenvoudigd.

De uitvoer is altijd dezelfde bij dezelfde bron (vaste volgorde, vaste scheidingstekens), dus de
hash in de naam ook: CI maakt hem opnieuw en vergelijkt (Keuze 31). Exit 1 als gzip -9 boven
GRENS_GZIP komt (de begroting mapData), 2 bij een verkeerde bron.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

BRON_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
    "ne_110m_admin_0_countries.geojson"
)
BRON_SHA256 = "6866c877d39cba9c357620878839b336d569f8c662d3cfab4cb1dbe2d39c977f"
BRON_NAAM = "Natural Earth 110m admin-0 v5.1.2 (publiek domein)"
SCHAAL = 100  # honderdsten van een graad
GRENS_GZIP = 60_000  # bytes, de begroting mapData in web/scripts/budget.mjs (Keuze 31)
MAX_BRON = 16 << 20  # de bron is ±0,8 MB; meer lezen we niet


class VerkeerdeBron(Exception):
    pass


def lees_bron(data: bytes, sha256: str = BRON_SHA256) -> dict:
    """De GeoJSON, alleen als de sha256 klopt (een bron van buiten is onvertrouwd)."""
    gekregen = hashlib.sha256(data).hexdigest()
    if gekregen != sha256:
        raise VerkeerdeBron(f"sha256 {gekregen}, verwacht {sha256}")
    bron = json.loads(data)
    if bron.get("type") != "FeatureCollection":
        raise VerkeerdeBron("geen FeatureCollection")
    return bron


def download(url: str = BRON_URL) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as antwoord:
        data = antwoord.read(MAX_BRON + 1)
    if len(data) > MAX_BRON:
        raise VerkeerdeBron(f"groter dan {MAX_BRON} bytes")
    return data


def ring(punten: list[list[float]]) -> list[int]:
    """Een ring als [x0, y0, dx1, dy1, ...] in honderdsten van een graad, zonder het sluitpunt
    en zonder punten die na het afronden gelijk zijn aan het vorige."""
    uit: list[int] = []
    vorige: tuple[int, int] | None = None
    eerste: tuple[int, int] | None = None
    for lon, lat, *_ in punten:
        p = (round(lon * SCHAAL), round(lat * SCHAAL))
        if p == vorige:
            continue
        if vorige is None:
            uit += p
            eerste = p
        else:
            uit += (p[0] - vorige[0], p[1] - vorige[1])
        vorige = p
    # Het sluitpunt (gelijk aan het eerste) laten we weg: de tekening sluit de ring zelf
    if len(uit) > 2 and vorige == eerste:
        del uit[-2:]
    return uit


def terug(r: list[int]) -> list[tuple[float, float]]:
    """Het omgekeerde van ring(): de punten in graden (voor tests en de web-app)."""
    x, y = r[0], r[1]
    uit = [(x / SCHAAL, y / SCHAAL)]
    for i in range(2, len(r), 2):
        x, y = x + r[i], y + r[i + 1]
        uit.append((x / SCHAAL, y / SCHAAL))
    return uit


def _code(waarde) -> str | None:
    return None if waarde in (None, "", "-99") else str(waarde)


def maak(bron: dict, sha256: str = BRON_SHA256) -> bytes:
    landen = []
    for f in bron["features"]:
        e, g = f["properties"], f["geometry"]
        vlakken = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        ringen = [[ring(r) for r in vlak] for vlak in vlakken]
        # Een ring met minder dan drie punten tekent niets
        ringen = [[r for r in vlak if len(r) >= 6] for vlak in ringen]
        ringen = [vlak for vlak in ringen if vlak]
        landen.append(
            {
                "a3": e["ADM0_A3"],
                "a2": _code(e.get("ISO_A2_EH")),
                "nl": e.get("NAME_NL") or e["NAME"],
                "en": e.get("NAME_EN") or e["NAME"],
                "v": ringen,
            }
        )
    landen.sort(key=lambda land: land["a3"])
    uit = {"v": 1, "bron": BRON_NAAM, "sha256": sha256, "schaal": SCHAAL, "landen": landen}
    return json.dumps(uit, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def naam(data: bytes) -> str:
    return f"world-110m.{hashlib.sha256(data).hexdigest()[:12]}.json"


def meet(data: bytes, bron: dict) -> dict:
    bron_punten = sum(
        len(r)
        for f in bron["features"]
        for vlak in (
            [f["geometry"]["coordinates"]]
            if f["geometry"]["type"] == "Polygon"
            else f["geometry"]["coordinates"]
        )
        for r in vlak
    )
    kaart = json.loads(data)
    punten = sum(len(r) // 2 for land in kaart["landen"] for vlak in land["v"] for r in vlak)
    return {
        "naam": naam(data),
        "bytes": len(data),
        "gzip -9": len(gzip.compress(data, 9, mtime=0)),
        "landen": len(kaart["landen"]),
        "punten": punten,
        "punten in de bron": bron_punten,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Maak world-110m.<hash>.json (Keuze 31).")
    waar = ap.add_mutually_exclusive_group(required=True)
    waar.add_argument("--bron", help="de GeoJSON van Natural Earth 110m admin-0 v5.1.2")
    waar.add_argument("--download", action="store_true", help=f"haal {BRON_URL}")
    ap.add_argument("--uit", help="de map voor world-110m.<hash>.json (anders alleen meten)")
    args = ap.parse_args(argv)
    try:
        data = download() if args.download else Path(args.bron).read_bytes()
        bron = lees_bron(data)
    except (OSError, VerkeerdeBron, ValueError) as e:
        print(f"maak_wereld: de bron is niet goed: {e}", file=sys.stderr)
        return 2
    kaart = maak(bron)
    m = meet(kaart, bron)
    if args.uit:
        Path(args.uit).mkdir(parents=True, exist_ok=True)
        (Path(args.uit) / m["naam"]).write_bytes(kaart)
    print(
        f"{m['naam']}: {m['bytes']} bytes, gzip -9 {m['gzip -9']} bytes (grens {GRENS_GZIP}); "
        f"{m['landen']} landen, {m['punten']} punten (de bron: {m['punten in de bron']}); "
        f"sha256 {hashlib.sha256(kaart).hexdigest()}"
    )
    return 0 if m["gzip -9"] <= GRENS_GZIP else 1


if __name__ == "__main__":
    sys.exit(main())
