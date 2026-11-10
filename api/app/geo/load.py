"""geo-load: places.sqlite uit de twee bronnen (bouwontwerp fase 6, Keuze 29). Alleen de
standaardbibliotheek.

De bronnen staan in geo_src (install.sh zet ze daar neer, Z7; CI en de tests elders):

- `ne_10m_admin_0_countries.geojson`: de landen van Natural Earth 10m admin-0, tag v5.1.2
  (publiek domein);
- `reverse_geocoder-1.5.1.tar.gz`: de sdist van reverse_geocoder 1.5.1 van PyPI, alleen om het
  bestand `rg_cities1000.csv` erin (GeoNames cities1000, CC BY 4.0). Het pakket zelf wordt nooit
  geïnstalleerd of geïmporteerd: we lezen de CSV met tarfile uit het archief, zonder uitpakken.

Een bron van buiten is onvertrouwd: eerst de sha256 (vast hieronder, en gelijk aan
tools/geo/haal.py), dan pas lezen, met grenzen op grootte en aantal. Klopt iets niet, dan geen
plaatsen en een regel in het log; de app start gewoon.

Wat erin komt (SCHEMA):

- `meta`: de sha256 van beide bronnen, het schema, de aantallen en een id per bouw (`bouw`);
- `place` met een R*Tree `place_box` (een punt per plaats): naam, provincie (admin1), land;
- `country` (code, naam) en `country_part`: per vlak de ringen als float32 (lengte, breedte)
  met zlib, met een R*Tree `country_box` op het kader van elk vlak.

Het id van een plaats is een vaste hash van land, provincie, naam en plek (48 bits, past in een
getal van JavaScript): de CSV heeft geen id van GeoNames, en een regelnummer verschuift bij een
nieuwe versie van de bron. `photo_geo.place_id` blijft zo geldig na een nieuwe bouw.

De app bouwt bij de start (ensure) als het bestand ontbreekt, met andere bronnen gebouwd is
(een nieuwe versie van de app met een nieuwe sha256) of kapot is (PRAGMA quick_check), op de
achtergrond. `PLATFORM_PLACES=uit`
(`PLAATSEN=uit` in .env) slaat het over. Bouwen gaat naar `<places_db>.part` en dan
`os.replace`: een lezer ziet nooit een half bestand.

Na een nieuwe bouw (bij de start of met geo-load) zetten we de job geo.places klaar: de rijen
van photo_geo die photometa las voordat places.sqlite er was (bij de upgrade naar 0.8.0 leest hij
alles opnieuw terwijl dit bestand nog gebouwd wordt), krijgen zo alsnog hun plaats en land (I1,
A4). Die job schrijft in photo_geo voor elke eigenaar, dus staat hij bij de andere schrijvers in
app/photo_rows.py, niet hier (tests/search/test_own_live.py). Eén keer per bouw: Setting
"geo.places" onthoudt het id van de bouw (meta `bouw`). Elke start kijkt het na, ook als
places.sqlite al "klaar" is: mislukte het klaarzetten na de bouw, dan doet deze start het.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import math
import os
import sqlite3
import struct
import sys
import tarfile
import threading
import uuid
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger("platform.geo")

SCHEMA = "1"


@dataclass(frozen=True)
class Bron:
    naam: str  # de bestandsnaam in geo_src
    url: str
    grootte: int
    sha256: str


LANDEN = Bron(
    "ne_10m_admin_0_countries.geojson",
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
    "ne_10m_admin_0_countries.geojson",
    13_287_234,
    "239eec57ac17f100a11e2536cffc56752c318b50ae765b0918ff7aab4ce8f255",
)
PLAATSEN = Bron(
    "reverse_geocoder-1.5.1.tar.gz",
    "https://files.pythonhosted.org/packages/0b/0f/"
    "b7d5d4b36553731f11983e19e1813a1059ad0732c5162c01b3220c927d31/reverse_geocoder-1.5.1.tar.gz",
    2_246_559,
    "2a2e781b5f69376d922b78fe8978f1350c84fce0ddb07e02c834ecf98b57c75c",
)
CSV_IN_TAR = "reverse_geocoder-1.5.1/reverse_geocoder/rg_cities1000.csv"
# Voor GET /geo/attribution: de versie van Natural Earth hierboven
NE_VERSIE = "v5.1.2"

# Grenzen voor onvertrouwde bronnen (de echte zijn 13 MB, 2,2 MB en 7,9 MB uitgepakt)
MAX_BRON = 64 << 20
MAX_CSV = 64 << 20
MAX_PLAATSEN = 1_000_000
MAX_PUNTEN = 5_000_000
MAX_TEKST = 200  # tekens van een naam of provincie

_bouwen = threading.Lock()


class BronFout(Exception):
    """Een bron ontbreekt, heeft een andere sha256 of is kapot. De tekst noemt alleen de
    bestandsnaam en wat er mis is."""


# --- Lezen van de bronnen ---


def sha256(pad: Path) -> str:
    h = hashlib.sha256()
    with pad.open("rb") as f:
        for stuk in iter(lambda: f.read(1 << 20), b""):
            h.update(stuk)
    return h.hexdigest()


def _lees(pad: Path, bron: Bron) -> bytes:
    """De bytes van een bron, alleen als de sha256 klopt."""
    try:
        grootte = pad.stat().st_size
    except FileNotFoundError:
        raise BronFout(f"{bron.naam}: ontbreekt") from None
    if grootte > MAX_BRON:
        raise BronFout(f"{bron.naam}: groter dan {MAX_BRON} bytes")
    data = pad.read_bytes()
    if hashlib.sha256(data).hexdigest() != bron.sha256:
        raise BronFout(f"{bron.naam}: verkeerde sha256")
    return data


def _code(*waarden: Any) -> str | None:
    """De eerste geldige landcode (twee hoofdletters); Natural Earth zet -99 als er geen is."""
    for w in waarden:
        if isinstance(w, str) and len(w) == 2 and w.isascii() and w.isalpha() and w.isupper():
            return w
    return None


def _naam(w: Any) -> str | None:
    if not isinstance(w, str):
        return None
    w = "".join(c for c in w if c.isprintable()).strip()
    return w[:MAX_TEKST] or None


def _ring(punten: Any) -> list[tuple[float, float]]:
    if not isinstance(punten, list):
        raise BronFout("landen: een ring is geen lijst")
    uit: list[tuple[float, float]] = []
    for p in punten:
        if not (isinstance(p, list) and len(p) >= 2):
            raise BronFout("landen: een punt is geen paar")
        lon, lat = p[0], p[1]
        if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (lon, lat)):
            raise BronFout("landen: een punt is geen getal")
        if not (math.isfinite(lon) and math.isfinite(lat)) or abs(lat) > 90 or abs(lon) > 180:
            raise BronFout("landen: een punt buiten de wereld")
        uit.append((float(lon), float(lat)))
    if len(uit) < 3:
        raise BronFout("landen: een ring met minder dan drie punten")
    return uit


def landen(data: bytes) -> Iterator[tuple[dict, list[list[list[tuple[float, float]]]]]]:
    """Per land (code, namen) en zijn vlakken; elk vlak is een lijst ringen (de eerste is de
    buitenrand, de rest gaten)."""
    try:
        bron = json.loads(data)
    except ValueError:
        raise BronFout("landen: geen JSON") from None
    if not isinstance(bron, dict) or bron.get("type") != "FeatureCollection":
        raise BronFout("landen: geen FeatureCollection")
    features = bron.get("features")
    if not isinstance(features, list):
        raise BronFout("landen: geen features")
    punten = 0
    for f in features:
        if not isinstance(f, dict):
            raise BronFout("landen: een feature is geen object")
        props = f.get("properties") or {}
        geo = f.get("geometry") or {}
        if not isinstance(props, dict) or not isinstance(geo, dict):
            raise BronFout("landen: een feature zonder properties of geometry")
        soort, coords = geo.get("type"), geo.get("coordinates")
        if soort == "Polygon":
            vlakken = [coords]
        elif soort == "MultiPolygon" and isinstance(coords, list):
            vlakken = coords
        else:
            raise BronFout("landen: een geometry die geen (Multi)Polygon is")
        uit = []
        for vlak in vlakken:
            if not isinstance(vlak, list) or not vlak:
                raise BronFout("landen: een leeg vlak")
            ringen = [_ring(r) for r in vlak]
            punten += sum(len(r) for r in ringen)
            uit.append(ringen)
        if punten > MAX_PUNTEN:
            raise BronFout("landen: te veel punten")
        land = {
            "a2": _code(props.get("ISO_A2_EH"), props.get("ISO_A2"), props.get("WB_A2")),
            "a3": _naam(props.get("ADM0_A3")),
            "nl": _naam(props.get("NAME_NL")),
            "en": _naam(props.get("NAME_EN")) or _naam(props.get("NAME")),
        }
        yield land, uit


def plaatsen(data: bytes) -> Iterator[tuple[str, str | None, str, float, float]]:
    """(naam, provincie, land, lat, lon) uit rg_cities1000.csv in het archief. Alleen dat ene
    gewone bestand; uitpakken naar schijf gebeurt nooit. Een regel die niet klopt slaan we over."""
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except (tarfile.TarError, OSError, EOFError):
        raise BronFout("plaatsen: geen tar.gz") from None
    with tar:
        try:
            lid = tar.getmember(CSV_IN_TAR)
        except (KeyError, tarfile.TarError, OSError, EOFError):
            raise BronFout("plaatsen: rg_cities1000.csv ontbreekt") from None
        if not lid.isreg() or lid.size > MAX_CSV:
            raise BronFout("plaatsen: rg_cities1000.csv is geen gewoon bestand")
        f = tar.extractfile(lid)
        if f is None:
            raise BronFout("plaatsen: rg_cities1000.csv is niet te lezen")
        try:
            tekst = f.read(MAX_CSV + 1).decode("utf-8")
        except (tarfile.TarError, OSError, EOFError, UnicodeDecodeError):
            raise BronFout("plaatsen: rg_cities1000.csv is kapot") from None
    lezer = csv.DictReader(io.StringIO(tekst, newline=""))
    if not {"lat", "lon", "name", "admin1", "cc"} <= set(lezer.fieldnames or ()):
        raise BronFout("plaatsen: andere kolommen")
    n = 0
    for rij in lezer:
        n += 1
        if n > MAX_PLAATSEN:
            raise BronFout("plaatsen: te veel regels")
        try:
            lat, lon = float(rij["lat"]), float(rij["lon"])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
            continue
        naam, cc = _naam(rij.get("name")), _code(rij.get("cc"))
        if naam is None or cc is None:
            continue
        yield naam, _naam(rij.get("admin1")), cc, lat, lon


def place_id(cc: str, admin1: str | None, naam: str, lat: float, lon: float) -> int:
    """Het vaste id van een plaats: 48 bits van de sha256 (zie boven)."""
    sleutel = f"{cc}|{admin1 or ''}|{naam}|{lat:.4f}|{lon:.4f}".encode()
    return int.from_bytes(hashlib.sha256(sleutel).digest()[:6], "big")


def pack_rings(ringen: list[list[tuple[float, float]]]) -> bytes:
    """De ringen van een vlak: aantal ringen, de lengte van elke ring (uint32), dan alle
    punten als float32 (lengte, breedte), little-endian, met zlib."""
    kop = struct.pack(f"<I{len(ringen)}I", len(ringen), *(len(r) for r in ringen))
    vlak = [v for r in ringen for p in r for v in p]
    return zlib.compress(kop + struct.pack(f"<{len(vlak)}f", *vlak), 6)


# --- Bouwen ---

_DDL = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE place (id INTEGER PRIMARY KEY, name TEXT NOT NULL, admin1 TEXT, "
    "cc TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL)",
    "CREATE VIRTUAL TABLE place_box USING rtree(id, min_lon, max_lon, min_lat, max_lat)",
    "CREATE TABLE country (id INTEGER PRIMARY KEY, a2 TEXT, a3 TEXT, name_nl TEXT, name_en TEXT)",
    "CREATE TABLE country_part (id INTEGER PRIMARY KEY, country INTEGER NOT NULL, "
    "rings BLOB NOT NULL)",
    "CREATE VIRTUAL TABLE country_box USING rtree(id, min_lon, max_lon, min_lat, max_lat)",
)


def build(
    geo_src: Path, uit: Path, landen_bron: Bron | None = None, plaatsen_bron: Bron | None = None
) -> dict[str, int]:
    """Bouw places.sqlite uit de bronnen in geo_src. BronFout als een bron niet klopt; dan
    blijft een bestaand uit staan."""
    landen_bron, plaatsen_bron = landen_bron or LANDEN, plaatsen_bron or PLAATSEN
    land_data = _lees(geo_src / landen_bron.naam, landen_bron)
    plaats_data = _lees(geo_src / plaatsen_bron.naam, plaatsen_bron)
    uit.parent.mkdir(parents=True, exist_ok=True)
    part = uit.with_name(uit.name + ".part")
    part.unlink(missing_ok=True)
    db = sqlite3.connect(part)
    try:
        db.execute("PRAGMA journal_mode = OFF")
        db.execute("PRAGMA synchronous = OFF")
        for ddl in _DDL:
            db.execute(ddl)
        n_landen = n_vlakken = 0
        for land, vlakken in landen(land_data):
            n_landen += 1
            db.execute(
                "INSERT INTO country (id, a2, a3, name_nl, name_en) VALUES (?, ?, ?, ?, ?)",
                (n_landen, land["a2"], land["a3"], land["nl"], land["en"]),
            )
            for ringen in vlakken:
                n_vlakken += 1
                buiten = ringen[0]
                db.execute(
                    "INSERT INTO country_part (id, country, rings) VALUES (?, ?, ?)",
                    (n_vlakken, n_landen, pack_rings(ringen)),
                )
                db.execute(
                    "INSERT INTO country_box VALUES (?, ?, ?, ?, ?)",
                    (
                        n_vlakken,
                        min(p[0] for p in buiten),
                        max(p[0] for p in buiten),
                        min(p[1] for p in buiten),
                        max(p[1] for p in buiten),
                    ),
                )
        del land_data
        n_plaatsen = 0
        for naam, admin1, cc, lat, lon in plaatsen(plaats_data):
            pid = place_id(cc, admin1, naam, lat, lon)
            cur = db.execute(
                "INSERT OR IGNORE INTO place (id, name, admin1, cc, lat, lon) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (pid, naam, admin1, cc, lat, lon),
            )
            if cur.rowcount:
                db.execute(
                    "INSERT INTO place_box VALUES (?, ?, ?, ?, ?)", (pid, lon, lon, lat, lat)
                )
                n_plaatsen += 1
        if not n_landen or not n_plaatsen:
            raise BronFout("geen landen of geen plaatsen")
        meta = {
            "schema": SCHEMA,
            "sha_landen": landen_bron.sha256,
            "sha_plaatsen": plaatsen_bron.sha256,
            "landen": str(n_landen),
            "vlakken": str(n_vlakken),
            "plaatsen": str(n_plaatsen),
            "gebouwd": datetime.now(UTC).date().isoformat(),
            "bouw": uuid.uuid4().hex,  # voor geo.places: één keer per bouw
        }
        db.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", meta.items())
        db.commit()
    except BaseException:
        db.close()
        part.unlink(missing_ok=True)
        raise
    db.close()
    os.replace(part, uit)
    return {"landen": n_landen, "vlakken": n_vlakken, "plaatsen": n_plaatsen}


def read_meta(pad: Path) -> dict[str, str] | None:
    """De meta van een places.sqlite, of None als hij er niet is of niet te lezen is."""
    if not pad.is_file():
        return None
    try:
        db = sqlite3.connect(f"{pad.as_uri()}?mode=ro", uri=True)
        try:
            return dict(db.execute("SELECT key, value FROM meta").fetchall())
        finally:
            db.close()
    except sqlite3.Error:
        return None


def bouw_id(pad: Path) -> str | None:
    """Welke bouw van places.sqlite dit is (meta "bouw"), of None zonder leesbare places.sqlite.
    Een bestand van vóór I1 heeft geen id: dan de dag van de bouw, vast zolang het dat bestand
    is."""
    meta = read_meta(pad)
    if meta is None:
        return None
    return meta.get("bouw") or f"zonder-id:{meta.get('gebouwd', '')}"


def current(pad: Path) -> bool:
    """Is places.sqlite gebouwd uit precies de bronnen van deze versie, met dit schema?"""
    meta = read_meta(pad) or {}
    gebouwd = (meta.get("schema"), meta.get("sha_landen"), meta.get("sha_plaatsen"))
    return gebouwd == (SCHEMA, LANDEN.sha256, PLAATSEN.sha256)


def heel(pad: Path) -> bool:
    """PRAGMA quick_check: zijn de pagina's van places.sqlite heel? De meta kan kloppen terwijl
    een pagina van de plaatsen of de vlakken kapot is (review golf 3, veilig F1)."""
    try:
        db = sqlite3.connect(f"{pad.as_uri()}?mode=ro", uri=True)
        try:
            return db.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        finally:
            db.close()
    except sqlite3.Error:
        return False


def _opzij(pad: Path) -> None:
    """Een kapotte places.sqlite die niet opnieuw gebouwd kon worden: opzij (<naam>.kapot), zodat
    de app hem niet meer opent en de volgende start niet steeds opnieuw kijkt."""
    try:
        os.replace(pad, pad.with_name(pad.name + ".kapot"))
    except OSError as e:
        log.warning("Plaatsnamen: de kapotte places.sqlite kon niet opzij (%s)", type(e).__name__)


def ensure(settings: Settings, *, force: bool = False) -> str:
    """Bij de start (en vanuit de CLI): bouwen als het nodig is. Faalt nooit; geeft terug wat er
    gebeurde: "uit", "klaar" (al goed), "gebouwd", "geen_bron" of "fout". Een places.sqlite van
    deze bronnen die toch kapot is (quick_check), wordt opnieuw gebouwd; lukt dat niet, dan gaat
    hij opzij en zijn er geen plaatsnamen."""
    if settings.places == "uit":
        return "uit"
    pad, src = Path(settings.places_db), Path(settings.geo_src)
    with _bouwen:
        kapot = False
        if not force and current(pad):
            if heel(pad):
                return "klaar"
            log.warning("Plaatsnamen: places.sqlite is kapot (quick_check); opnieuw bouwen")
            kapot = True
        if not (src / LANDEN.naam).exists() or not (src / PLAATSEN.naam).exists():
            log.info("Plaatsnamen: niet geladen (de bronnen staan niet in geo_src)")
            if kapot:
                _opzij(pad)
            return "geen_bron"
        try:
            n = build(src, pad)
        except BronFout as e:
            log.warning("Plaatsnamen: niet geladen (%s)", e)
            if kapot:
                _opzij(pad)
            return "fout"
        except (OSError, sqlite3.Error, MemoryError) as e:
            log.warning("Plaatsnamen: niet geladen (%s)", type(e).__name__)
            if kapot:
                _opzij(pad)
            return "fout"
    from app.geo import places  # na het bouwen opnieuw openen

    places.forget()
    log.info(
        "Plaatsnamen geladen: %d plaatsen, %d landen (%d vlakken)",
        n["plaatsen"],
        n["landen"],
        n["vlakken"],
    )
    return "gebouwd"


async def start(settings: Settings) -> None:
    """search.hooks.register_backfill: bij de start op de achtergrond bouwen, zodat de app niet
    wacht (±10 s). Een fout komt in het log."""
    import asyncio

    from anyio import to_thread

    async def _bouw() -> None:
        try:
            uitkomst = await to_thread.run_sync(ensure, settings)
        except Exception:  # noqa: BLE001 - zonder plaatsnamen werkt de rest gewoon
            log.exception("Plaatsnamen: bouwen mislukt")
            return
        if uitkomst in ("gebouwd", "klaar"):
            # Ook bij "klaar": mislukte het klaarzetten na de vorige bouw, dan nu
            try:
                await _na_de_bouw(settings)
            except Exception as e:  # noqa: BLE001 - de plaatsnamen zelf zijn er wel
                log.warning(
                    "Plaatsnamen: geo.places niet klaargezet (%s); de volgende start probeert "
                    "het opnieuw",
                    type(e).__name__,
                )

    if settings.places == "uit":
        return
    # Ook als de meta klopt: ensure() kijkt in de thread met quick_check of hij heel is (±0,15 s
    # bij de echte bronnen), niet in de lus
    task = asyncio.get_running_loop().create_task(_bouw())
    _taken.add(task)
    task.add_done_callback(_taken.discard)


_taken: set[Any] = set()


async def _na_de_bouw(settings: Settings) -> None:
    """Eén keer per bouw: de job geo.places (app/photo_rows.py) vult de plaats en het land in
    van de rijen die gelezen werden voordat places.sqlite er was. Voor een bouw die al een keten
    kreeg (Setting "geo.places"), niets."""
    from anyio import to_thread

    from app import photo_rows

    bouw = await to_thread.run_sync(bouw_id, Path(settings.places_db))
    if bouw is not None:
        await photo_rows.enqueue_places(settings, bouw)


def _na_de_bouw_cli(settings: Settings) -> bool:
    """Vanuit geo-load (een eigen proces naast de draaiende app, zie install.sh): de job
    klaarzetten; de app draait hem. False als dat niet lukte (nog geen database)."""
    import asyncio

    from app.db import get_engine

    async def zet() -> None:
        try:
            await _na_de_bouw(settings)
        finally:
            await get_engine(settings).dispose()

    try:
        asyncio.run(zet())
    except Exception as e:  # noqa: BLE001 - places.sqlite zelf is wel gebouwd
        log.warning("Plaatsnamen: geo.places niet klaargezet (%s)", type(e).__name__)
        return False
    return True


# --- De CLI (app.cli geo-load) ---

COMMANDS = ("geo-load",)


def parsers(commands: Any) -> None:
    p = commands.add_parser("geo-load", help="places.sqlite bouwen uit de bronnen in geo_src")
    p.add_argument("--force", action="store_true", help="ook als hij al bij deze bronnen hoort")


def run(settings: Settings, args: argparse.Namespace) -> int:
    uitkomst = ensure(settings, force=args.force)
    if uitkomst == "gebouwd" and not _na_de_bouw_cli(settings):
        print(
            "Let op: de plaatsen van foto's die al gelezen waren, zet de app klaar bij zijn "
            "volgende start (zie het log).",
            file=sys.stderr,
        )
    tekst = {
        "uit": "Plaatsnamen staan uit (PLAATSEN=uit): niets gedaan.",
        "klaar": f"{settings.places_db} hoort al bij deze bronnen.",
        "gebouwd": f"{settings.places_db} gebouwd.",
        "geen_bron": f"De bronnen staan niet in {settings.geo_src}: geen plaatsnamen.",
        "fout": "Een bron klopt niet (zie het log): geen plaatsnamen.",
    }[uitkomst]
    print(tekst, file=sys.stdout if uitkomst in ("uit", "klaar", "gebouwd") else sys.stderr)
    return 0 if uitkomst in ("uit", "klaar", "gebouwd") else 1
