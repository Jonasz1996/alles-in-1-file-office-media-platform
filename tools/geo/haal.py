#!/usr/bin/env python3
"""De twee bronnen voor plaatsnamen en landen ophalen (bouwontwerp fase 6, Keuze 29).

Alleen de standaardbibliotheek. Haalt elk bestand dat nog niet (goed) in de map staat, met
een vaste sha256, en zet het pas op zijn plek als de sha256 klopt:

    python3 -I tools/geo/haal.py --naar DIR          # ophalen wat ontbreekt
    python3 -I tools/geo/haal.py --naar DIR --check  # alleen controleren, niets ophalen

Exit 0 als beide bestanden er goed staan, 1 anders. De app bouwt er places.sqlite van
(app.cli geo-load, app/geo/load.py); de sha256 hieronder zijn dezelfde als daar (een test
vergelijkt ze). De bronnen zijn data, geen afhankelijkheid: het pakket reverse_geocoder wordt
nooit geïnstalleerd of geïmporteerd, alleen rg_cities1000.csv uit zijn sdist wordt gelezen.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

# (bestandsnaam, url, bytes, sha256)
BRONNEN = (
    (
        "ne_10m_admin_0_countries.geojson",
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
        "ne_10m_admin_0_countries.geojson",
        13_287_234,
        "239eec57ac17f100a11e2536cffc56752c318b50ae765b0918ff7aab4ce8f255",
    ),
    (
        "reverse_geocoder-1.5.1.tar.gz",
        "https://files.pythonhosted.org/packages/0b/0f/"
        "b7d5d4b36553731f11983e19e1813a1059ad0732c5162c01b3220c927d31/"
        "reverse_geocoder-1.5.1.tar.gz",
        2_246_559,
        "2a2e781b5f69376d922b78fe8978f1350c84fce0ddb07e02c834ecf98b57c75c",
    ),
)


def _sha256(pad: Path) -> str:
    h = hashlib.sha256()
    with pad.open("rb") as f:
        for stuk in iter(lambda: f.read(1 << 20), b""):
            h.update(stuk)
    return h.hexdigest()


def goed(pad: Path, grootte: int, sha256: str) -> bool:
    return pad.is_file() and pad.stat().st_size == grootte and _sha256(pad) == sha256


def haal(naar: Path, naam: str, url: str, grootte: int, sha256: str) -> None:
    """Eén bron naar naar/naam, via naam.part; weigert bij een andere grootte of sha256."""
    part = naar / f"{naam}.part"
    h = hashlib.sha256()
    n = 0
    try:
        with urllib.request.urlopen(url, timeout=60) as antwoord, part.open("wb") as f:
            while stuk := antwoord.read(1 << 20):
                n += len(stuk)
                if n > grootte:
                    raise ValueError(f"{naam}: groter dan {grootte} bytes")
                h.update(stuk)
                f.write(stuk)
        if n != grootte or h.hexdigest() != sha256:
            raise ValueError(f"{naam}: verkeerde grootte of sha256")
        os.replace(part, naar / naam)
    finally:
        part.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--naar", type=Path, required=True, help="de map (geo_src)")
    p.add_argument("--check", action="store_true", help="alleen controleren")
    args = p.parse_args(argv)
    args.naar.mkdir(parents=True, exist_ok=True)
    fout = 0
    for naam, url, grootte, sha256 in BRONNEN:
        if goed(args.naar / naam, grootte, sha256):
            print(f"{naam}: goed")
            continue
        if args.check:
            print(f"{naam}: ontbreekt of klopt niet", file=sys.stderr)
            fout = 1
            continue
        try:
            haal(args.naar, naam, url, grootte, sha256)
            print(f"{naam}: opgehaald")
        except (OSError, ValueError) as e:
            print(f"{naam}: niet opgehaald ({e})", file=sys.stderr)
            fout = 1
    return fout


if __name__ == "__main__":
    sys.exit(main())
