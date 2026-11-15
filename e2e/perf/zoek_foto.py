"""Foto's, plekken en scans voor de metingen van §4.5 (fase 6, I1): zoek_backfill --soort photometa
en --soort geo, en stap 2 van zoek_samen (5.000 foto's en 500 scans voor X1).

Alles gemaakt met een vast zaad, uit de fixtures van de tests (geen nieuwe afhankelijkheid):

- fotos(): JPEG's van 640 × 480 met EXIF (FUJIFILM X-T30, 1 juni 2024) uit
  tests/search/fixtures.py (foto_gps, foto_zonder_gps, foto_klein). Foto i met i % 20 == 19 is
  een verkleinde kopie van de vorige, met diens plek (een bijna-dubbele: de tweede controle van
  photo_hash heeft dan iets te doen); verder is elke i % 5 == 4 zonder GPS. Samen: 15% zonder
  plek, 5% bijna-dubbel. Elk met het voorbeeld van 256 (WebP), zoals de preview-taak van 0.7.1
  het al schreef: de pHash leest dat, niet het origineel (render_meta._picture). Zo zegt de
  grootte van het origineel weinig: de EXIF staat vooraan, en het voorbeeld is altijd 256.
- kleine_fotos(): een JPEG van 16 × 16 per punt, alleen voor de plek in de EXIF (land en plaats,
  55.000 punten; het beeld doet er niet toe, de pHash is in die stand al gerekend).
- punten(): 3 op de 4 tot ±25 km van een plaats (uit places.sqlite, of rond een paar vaste
  steden), de rest willekeurig over de wereld (−60° tot 75°), zoals tests/geo/test_places.py.
- scans(): PDF's zonder tekstlaag, A4 op 300 dpi (tests/ocr/scans.py, de invoer van de poort van
  X1), op 0°, 3°, 7°, −5° en 90°, elk met eigen ruis op het papier. Alleen met tesseract (nld, eng
  en osd, zoals de worker het nakijkt) en DejaVu Sans: zie scan_reden().

zaai() zet de foto's rechtstreeks in de database en de BlobStore van een gestopte server (zoals
zoek_backfill.zaai), in een eigen map van de eigenaar, in een van drie standen:

- OUD: zoals 0.7.1 ze achterliet (meta_version 1, de datum gelezen, geen blob_meta en geen
  blob_hash). photometa v2 leest bij het starten alles: GPS, camera, datum en pHash.
- GEO: zoals na "Plek, camera en datum opnieuw lezen" (POST /admin/search/rebuild geo):
  meta_version een lager, blob_meta en blob_hash er al. photometa leest de plek en de datum, en
  zoekt land en plaats op in places.sqlite.
- KLAAR: alles gelezen (meta_version en blob_hash van deze versie, state none), behalve de plek
  (blob_meta.gps onbekend). photometa heeft bij het starten niets te doen; pas POST
  /admin/search/rebuild hash laat hem alles opnieuw lezen (zoek_samen doet dat zodra de
  belastingtest loopt, net als de pauze van de index eraf gaat).

zaai_documenten() zet documenten (zoals de scans) in een eigen map, zoals zoek_backfill.zaai dat
in de hoofdmap doet. foto_stand() leest (alleen lezen, naast de server) hoe ver photometa is.
"""

from __future__ import annotations

import hashlib
import io
import random
import sqlite3
import sys
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # e2e/
sys.path.insert(0, str(HERE.parents[1] / "api"))  # de app en zijn testhulpen

from tests.search import fixtures as F  # noqa: E402

from app import photo_hash, photodate, previews, render_meta  # noqa: E402
from app.config import Settings  # noqa: E402
from app.models import Blob, BlobHash, BlobMeta, Node  # noqa: E402
from app.storage import BlobStore  # noqa: E402
from app.text import fold  # noqa: E402
from seed import connect  # noqa: E402

OUD, GEO, KLAAR = "oud", "geo", "klaar"
STANDEN = (OUD, GEO, KLAAR)
# De datum in de EXIF van fixtures.exif ("2024:06:01 14:30:00", lokale tijd): zwevend, zoals
# photodate.captured_at hem maakt
GENOMEN = datetime(2024, 6, 1, 14, 30, tzinfo=UTC)
CAMERA = "fujifilm x-t30"  # render_meta.camera van ("FUJIFILM", "X-T30"), gevouwen
ZONDER_GPS = 5  # 1 op de 5 foto's zonder plek
VARIANT = 20  # 1 op de 20 een verkleinde kopie van de vorige
BATCH = 500
# Als er geen places.sqlite is: rond deze steden (lat, lon)
STEDEN = (
    F.UTRECHT,
    (52.3676, 4.9041),  # Amsterdam
    (51.9244, 4.4777),  # Rotterdam
    (48.8566, 2.3522),  # Parijs
    (41.9028, 12.4964),  # Rome
    (41.3874, 2.1686),  # Barcelona
    (47.3769, 8.5417),  # Zürich
    (40.7128, -74.0060),  # New York
    (35.6762, 139.6503),  # Tokio
    (-33.8688, 151.2093),  # Sydney
)
SCAN_HOEKEN = F.SCAN_HOEKEN  # 0, 3, 7, −5 en 90 graden, zoals de poort van X1 (§4.5)


@dataclass(frozen=True)
class Foto:
    naam: str
    data: bytes  # JPEG met EXIF
    thumb: bytes | None  # het voorbeeld van 256 (WebP); None: geen preview
    gps: tuple[float, float] | None


def punten(
    n: int, zaad: int = 29, plaatsen: Sequence[tuple[float, float]] = ()
) -> list[tuple[float, float]]:
    """n punten (lat, lon) met een vast zaad: 3 op de 4 tot ±25 km van een plaats uit plaatsen
    (zonder: STEDEN), de rest willekeurig over de wereld; ook op zee."""
    rnd = random.Random(zaad)
    bron = list(plaatsen) or list(STEDEN)
    uit = []
    for i in range(n):
        if i % 4 == 3:
            lat, lon = rnd.uniform(-60, 75), rnd.uniform(-180, 180)
        else:
            lat, lon = rnd.choice(bron)
            lat = max(-89.9, min(89.9, lat + rnd.uniform(-0.22, 0.22)))
            lon = ((lon + rnd.uniform(-0.3, 0.3) + 180) % 360) - 180
        uit.append((round(lat, 6), round(lon, 6)))
    return uit


def plaatsen_uit(places_db: Path) -> list[tuple[float, float]]:
    """(lat, lon) van elke plaats in een places.sqlite (app/geo/load.py), op id."""
    con = sqlite3.connect(f"file:{places_db}?mode=ro", uri=True)
    try:
        return [(lat, lon) for lat, lon in con.execute("SELECT lat, lon FROM place ORDER BY id")]
    finally:
        con.close()


def _thumb(data: bytes) -> bytes:
    """Het voorbeeld van 256 (WebP) van een JPEG, zoals de preview-taak het maakt."""
    img = Image.open(io.BytesIO(data))
    img.draft("RGB", (previews.THUMB, previews.THUMB))
    img = img.convert("RGB")
    img.thumbnail((previews.THUMB, previews.THUMB))
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=80)
    return buf.getvalue()


def fotos(
    n: int, zaad: int = 7, plekken: Sequence[tuple[float, float]] | None = None
) -> Iterator[Foto]:
    """n foto's van 640 × 480 met hun voorbeeld; de plekken (standaard punten(n, zaad)) op
    volgorde, behalve voor een foto zonder GPS of een kopie (die heeft de plek van de vorige)."""
    plekken = plekken if plekken is not None else punten(n, zaad)
    vorige: Foto | None = None
    for i in range(n):
        if i % VARIANT == VARIANT - 1 and vorige is not None:
            data, gps = F.foto_klein(vorige.data), vorige.gps
        elif i % ZONDER_GPS == ZONDER_GPS - 1:
            data, gps = F.foto_zonder_gps(zaad=f"{zaad}-{i}"), None
        else:
            lat, lon = plekken[i]
            data, gps = F.foto_gps(lat, lon, zaad=f"{zaad}-{i}"), (lat, lon)
        vorige = Foto(f"foto-{i:05d}.jpg", data, _thumb(data), gps)
        yield vorige


def kleine_fotos(plekken: Iterable[tuple[float, float]]) -> Iterator[Foto]:
    """Per punt een JPEG van 16 × 16 met EXIF en GPS (elk punt een eigen inhoud), zonder
    voorbeeld."""
    for i, (lat, lon) in enumerate(plekken):
        img = Image.new("RGB", (16, 16), (i * 37 % 256, i * 91 % 256, i * 151 % 256))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80, exif=F.exif(lat, lon).tobytes())
        yield Foto(f"plek-{i:05d}.jpg", buf.getvalue(), None, (lat, lon))


def scan_reden() -> str | None:
    """Waarom er geen scans kunnen (None: ze kunnen): de worker doet OCR alleen met tesseract en
    nld, eng en osd (ops/caps.ocr, Keuze 24), en de scans hebben DejaVu Sans nodig."""
    from tests.ocr import scans as S

    from app.media.worker.ops import caps

    if not caps.ocr():
        return "geen tesseract met nld, eng en osd (de worker meldt dan ocr_missing)"
    try:
        S.font()
    except S.NoFont as e:
        return str(e)
    return None


def scans(n: int, begin: int = 0) -> Iterator[Any]:
    """n scans (zoek_backfill.Document), vanaf nummer begin: A4 op 300 dpi zonder tekstlaag, om
    de beurt op de hoeken van SCAN_HOEKEN, elk met eigen ruis (variant), dus elk een eigen
    inhoud. Een voor een gemaakt (±0,4 MB en ±0,16 s per scan)."""
    import zoek_backfill as zb
    from tests.ocr import scans as S

    for i in range(begin, begin + n):
        hoek = SCAN_HOEKEN[i % len(SCAN_HOEKEN)]
        yield zb.Document(f"scan-{i:04d}.pdf", "application/pdf", S.pdf(hoek, variant=i + 1))


def _map(conn: Any, owner: uuid.UUID, root: uuid.UUID, naam: str, at: datetime) -> uuid.UUID:
    """Een nieuwe map in de hoofdmap van owner."""
    map_id = uuid.uuid4()
    conn.execute(
        Node.__table__.insert(),
        [{"id": map_id, "parent_id": root, "owner_id": owner, "name": naam, "kind": "dir",
          "size": 0, "created_at": at, "modified_at": at, "search_name": fold(naam)}],
    )  # fmt: skip
    return map_id


def zaai(
    data_dir: Path,
    owner: str,
    root: str,
    fotos_: Iterable[Foto],
    stand: str,
    map_naam: str = "Foto's",
    dag: datetime = datetime(2026, 11, 3, 9, 0, tzinfo=UTC),
) -> dict[str, int]:
    """De foto's als blob en node van owner in een nieuwe map, in deze stand (OUD, GEO of KLAAR),
    met hun voorbeeld op schijf. Een inhoud die er al was, komt er niet nog eens bij. Geeft het
    aantal foto's en het aantal met een plek."""
    if stand not in STANDEN:
        raise ValueError(f"onbekende stand {stand!r} (bekend: {', '.join(STANDEN)})")
    store = BlobStore(Settings(data_dir=data_dir).blob_dir)
    eigenaar = uuid.UUID(owner)
    gezien: set[str] = set()
    teller = {"fotos": 0, "met_gps": 0}
    with connect(data_dir) as conn:
        map_id = _map(conn, eigenaar, uuid.UUID(root), map_naam, dag)
        rijen: dict[str, list[dict[str, Any]]] = {"blob": [], "node": [], "meta": [], "hash": []}

        def schrijf() -> None:
            for tabel, soort in ((Blob, "blob"), (Node, "node"), (BlobMeta, "meta"),
                                 (BlobHash, "hash")):  # fmt: skip
                if rijen[soort]:
                    conn.execute(tabel.__table__.insert(), rijen[soort])
                    rijen[soort].clear()

        for i, foto in enumerate(fotos_):
            sha = hashlib.sha256(foto.data).hexdigest()
            if sha in gezien:
                continue
            gezien.add(sha)
            pad = store.path(sha)
            pad.parent.mkdir(parents=True, exist_ok=True)
            pad.write_bytes(foto.data)
            if foto.thumb is not None:
                doel = previews.directory(store, sha)
                doel.mkdir(parents=True, exist_ok=True)
                (doel / f"{previews.THUMB}.webp").write_bytes(foto.thumb)
            rijen["blob"].append(_blob_rij(sha, foto, stand, dag))
            at = dag + timedelta(seconds=i)
            rijen["node"].append(
                {"id": uuid.uuid4(), "parent_id": map_id, "owner_id": eigenaar, "name": foto.naam,
                 "kind": "file", "mime": "image/jpeg", "size": len(foto.data), "blob_id": sha,
                 "created_at": at, "modified_at": at, "search_name": fold(foto.naam),
                 "taken_at": GENOMEN, "taken_day": photodate.day_of(GENOMEN)}
            )  # fmt: skip
            if stand in (GEO, KLAAR):
                gps = (1 if foto.gps else 0) if stand == GEO else None
                rijen["meta"].append({"blob_sha": sha, "camera": CAMERA, "lens": None, "gps": gps})
                rijen["hash"].append(
                    {"blob_sha": sha, "state": "none", "version": photo_hash.VERSION}
                )
            teller["fotos"] += 1
            teller["met_gps"] += foto.gps is not None
            if len(rijen["blob"]) >= BATCH:
                schrijf()
        schrijf()
    return teller


def _blob_rij(sha: str, foto: Foto, stand: str, dag: datetime) -> dict[str, Any]:
    if foto.thumb is not None:
        preview, meta = "ready", {"width": 640, "height": 480, "sizes": [previews.THUMB]}
    else:
        preview, meta = "none", {}
    # OUD en GEO: een versie lager (0.7.1 las alleen de datum; de herbouw geo zet hem zo terug)
    versie = render_meta.VERSION if stand == KLAAR else render_meta.VERSION - 1
    return {
        "sha256": sha,
        "size": len(foto.data),
        "ref_count": 1,
        "created_at": dag,
        "mime": "image/jpeg",
        "preview": preview,
        "preview_meta": meta,
        "taken_at": GENOMEN,
        "meta_version": versie,
        "render_version": previews.RENDER_VERSION,
    }


def zaai_documenten(
    data_dir: Path,
    owner: str,
    root: str,
    docs: Iterable[Any],
    map_naam: str,
    dag: datetime = datetime(2026, 11, 3, 9, 0, tzinfo=UTC),
) -> int:
    """Documenten (zoek_backfill.Document) in een nieuwe map van owner, zoals
    zoek_backfill.zaai: blob en node, zonder rij in analysis, de preview al gedaan (none met de
    huidige RENDER_VERSION). Geeft het aantal."""
    store = BlobStore(Settings(data_dir=data_dir).blob_dir)
    eigenaar = uuid.UUID(owner)
    n = 0
    with connect(data_dir) as conn:
        map_id = _map(conn, eigenaar, uuid.UUID(root), map_naam, dag)
        for i, doc in enumerate(docs):
            sha = hashlib.sha256(doc.data).hexdigest()
            pad = store.path(sha)
            pad.parent.mkdir(parents=True, exist_ok=True)
            pad.write_bytes(doc.data)
            at = dag + timedelta(seconds=i)
            conn.execute(
                Blob.__table__.insert(),
                [{"sha256": sha, "size": len(doc.data), "ref_count": 1, "created_at": dag,
                  "mime": doc.mime, "preview": "none",
                  "render_version": previews.RENDER_VERSION}],
            )  # fmt: skip
            conn.execute(
                Node.__table__.insert(),
                [{"id": uuid.uuid4(), "parent_id": map_id, "owner_id": eigenaar,
                  "name": doc.naam, "kind": "file", "mime": doc.mime, "size": len(doc.data),
                  "blob_id": sha, "created_at": at, "modified_at": at,
                  "search_name": fold(doc.naam)}],
            )  # fmt: skip
            n += 1
    return n


def foto_stand(data_dir: Path, volledig: bool = True) -> dict[str, int]:
    """Hoe ver photometa is (alleen lezen, naast de server): open, de taken van de keten die
    klaarstaan of lopen; te_doen, de afbeeldingen die hij nog moet lezen (_todo van photometa);
    gehasht, met een pHash van deze versie. volledig: ook de plekken in photo_geo (met land, met
    plaats) en de mislukte taken; anders alleen open, en te_doen pas als er niets open is (een
    goedkope peiling op dezelfde core als de meting)."""
    con = sqlite3.connect(f"file:{data_dir / 'platform.db'}?mode=ro", uri=True, timeout=10)
    try:
        uit = {"open": _een(con, "SELECT count(*) FROM job WHERE type = 'photometa' "
                                 "AND status IN ('queued', 'running')")}  # fmt: skip
        if uit["open"] and not volledig:
            return uit
        uit["te_doen"] = _een(
            con,
            "SELECT count(*) FROM blob b WHERE b.mime LIKE 'image/%' AND (b.meta_version < ? "
            "OR NOT EXISTS (SELECT 1 FROM blob_meta m WHERE m.blob_sha = b.sha256) "
            "OR NOT EXISTS (SELECT 1 FROM blob_hash h WHERE h.blob_sha = b.sha256 "
            "AND h.version >= ?))",
            render_meta.VERSION,
            photo_hash.VERSION,
        )
        if not volledig:
            return uit
        uit["gehasht"] = _een(
            con,
            "SELECT count(*) FROM blob_hash WHERE state = 'ok' AND version >= ?",
            photo_hash.VERSION,
        )
        uit["plekken"] = _een(con, "SELECT count(*) FROM photo_geo")
        uit["met_land"] = _een(con, "SELECT count(*) FROM photo_geo WHERE country IS NOT NULL")
        uit["met_plaats"] = _een(con, "SELECT count(*) FROM photo_geo WHERE place_id IS NOT NULL")
        uit["mislukt"] = _een(
            con, "SELECT count(*) FROM job WHERE type = 'photometa' AND status = 'failed'"
        )
        return uit
    except sqlite3.OperationalError:
        # Nog geen database of tabellen (de server start net): nog niet klaar
        return {"open": 0, "te_doen": -1}
    finally:
        con.close()


def _een(con: sqlite3.Connection, sql: str, *args: Any) -> int:
    return int(con.execute(sql, args).fetchone()[0])
