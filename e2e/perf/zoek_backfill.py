"""De snelheid van de backfill (fase 6, Z2 en I1; bouwontwerp §4.5): de hele keten op één core.

Poorten (per minuut per core): tekst in de worker ≥ 1.000 documenten; photometa v2 (GPS, camera
en pHash) ≥ 5.000 foto's; land en plaats ≥ 20.000 punten. De terugval van de tekst zonder media
(batches van 50 in het renderproces van de app) is een rapport.

Methode
- De invoer van §4.5: n documenten (standaard 2.000), gemaakt met een vast zaad: 40% PDF (1–20
  pagina's met een tekstlaag, tests/search/fixtures.pdf_tekst), 30% docx, 10% xlsx (200–2.000
  cellen), 10% pptx, 10% txt, html en rtf. Elk document is uniek (een eigen woord, of een eigen
  zip-commentaar), dus elk is een eigen blob.
- Een eigen server per keer (media aan met de lokale worker, of media uit). De documenten gaan
  rechtstreeks in de database en de BlobStore (zoals vóór Z2: geen rij in analysis, geen
  blob_text), terwijl de server stil staat; daarna start hij weer, en de backfill bij het
  starten zet ze klaar en de pomp in de rij index leest ze (de worker of de terugval), schrijft
  blob_text (de triggers van 0010 zetten de tekst in fts_node).
- Niets anders actief (§4.5): de previews van de documenten zijn al gedaan (Blob.preview none
  met de huidige RENDER_VERSION), dus er lopen geen previewtaken naast de tekst.
- Dit script en alles wat de server start (de app, de forkserver, de renderprocessen, de
  worker en zijn kinderen) draait op één core (os.sched_setaffinity vóór de start: de
  kinderen erven hem). Gemeten van de start van de server tot elke rij van de stap text klaar
  is (done, none, skip of failed; elke 0,25 s gelezen, alleen lezen naast de server). Het
  starten van de app zelf zit er dus in.
- Elke soort 3 keer (--keer); de mediaan telt. Aan het eind "=== kort ===".

--soort photometa en --soort geo (I1, de twee rijen van §4.5 die nog niet over de hele keten
gemeten waren) doen hetzelfde met foto's (e2e/perf/zoek_foto.py), media uit:
- photometa: n foto's (standaard 5.000) van 640 × 480 met EXIF en hun voorbeeld van 256, zoals
  0.7.1 ze achterliet (meta_version 1, geen blob_meta en geen blob_hash; zoek_foto.OUD). Bij het
  starten leest photometa v2 ze allemaal: GPS, camera, datum en pHash (uit het voorbeeld), met
  de tweede controle van de bijna-dubbele (1 op de 20). PLAATSEN=uit: land en plaats hebben hun
  eigen rij.
- geo: n punten (standaard 55.000), elk in een eigen JPEG van 16 × 16 met alleen EXIF en GPS,
  zoals na "Plek, camera en datum opnieuw lezen" (POST /admin/search/rebuild geo;
  zoek_foto.GEO): photometa leest de plek en zoekt land en plaats op in de echte places.sqlite.
  Die bouwt het script vóór de meting (niet in de tijd) uit de bronnen in --geo-bron, de map die
  tools/geo/haal.py vult (python3 -I tools/geo/haal.py --naar DIR); het downloadt zelf niets.
  3 op de 4 punten tot ±25 km van een plaats uit die places.sqlite, de rest over de wereld.
- Klaar als er geen taak van photometa meer klaarstaat of loopt en er niets meer te lezen is
  (_todo van photometa, elke 0,25 s gelezen, alleen lezen naast de server).

    cd api && uv run --with playwright python ../e2e/perf/zoek_backfill.py
    cd api && uv run --with playwright python ../e2e/perf/zoek_backfill.py --soort terugval \\
        --n 500 --keer 1 --json /tmp/zoek_backfill.json
    cd api && uv run python ../e2e/perf/zoek_backfill.py --soort photometa --core 1
    cd api && uv run python ../e2e/perf/zoek_backfill.py --soort geo --geo-bron DIR --core 1

Met media aan (worker) zijn ffmpeg en ffprobe nodig (de worker start niet zonder); zonder die
meet het script alleen de terugval en zegt dat.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
import uuid
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # e2e/
sys.path.insert(0, str(HERE.parents[1] / "api"))  # de app en zijn testhulpen

from tests.search import fixtures as F  # noqa: E402

from app import previews  # noqa: E402
from app.config import Settings  # noqa: E402
from app.models import Blob, Node  # noqa: E402
from app.office.convert import EXT_BY_MIME  # noqa: E402
from conftest import Server, make_admin  # noqa: E402
from seed import connect  # noqa: E402

POORT_PER_MINUUT = 1_000  # per core, tekst in de worker (§4.5)
POORT_PHOTOMETA = 5_000  # per core, GPS, camera en pHash (photometa v2; §4.5)
POORT_GEO = 20_000  # per core, land en plaats (§4.5)
# De soorten met een poort; de terugval is een rapport
POORTEN = {"worker": POORT_PER_MINUUT, "photometa": POORT_PHOTOMETA, "geo": POORT_GEO}
# De invoer van §4.5 per soort (--n zonder waarde)
N = {"worker": 2_000, "terugval": 2_000, "photometa": 5_000, "geo": 55_000}
NAAM = {
    "worker": ("tekst worker", "documenten"),
    "terugval": ("tekst terugval", "documenten"),
    "photometa": ("photometa v2 (GPS, camera, pHash)", "foto's"),
    "geo": ("land en plaats", "punten"),
}
FOTO_SOORTEN = ("photometa", "geo")
DAG = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)  # een vaste dag (de regel van de e2e)
WACHT_S = 900.0
KLAAR = ("done", "none", "skip", "failed")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
# keys.PDF_VERSION in 0.7.x (0.7.1 = e0496c6): wat een upgrade naar 0.8.0 aantreft. Komt er een
# hogere PDF_VERSION, dan zet de upgrade echt alles opnieuw om, en dan hoort dat in de meting
PDF_VERSION_07 = 1
# Het deel van elke soort (§4.5)
MIX = (("pdf", 0.40), ("docx", 0.30), ("xlsx", 0.10), ("pptx", 0.10), ("klein", 0.10))


@dataclass(frozen=True)
class Document:
    naam: str
    mime: str
    data: bytes


def _uniek(data: bytes, i: int) -> bytes:
    """Dezelfde zip met een eigen commentaar: een eigen sha, dezelfde inhoud."""
    buf = io.BytesIO(data)
    with zipfile.ZipFile(buf, "a") as z:
        z.comment = f"zoek_backfill {i}".encode()
    return buf.getvalue()


def documenten(n: int, zaad: int = 45) -> list[Document]:
    """De invoer van §4.5 met een vast zaad."""
    rng = random.Random(zaad)
    soorten = [s for s, deel in MIX for _ in range(round(n * deel))][:n]
    soorten += ["pdf"] * (n - len(soorten))
    rng.shuffle(soorten)
    uit = []
    for i, soort in enumerate(soorten):
        woord = f"backfillwoord{i}"
        if soort == "pdf":
            pages = rng.randint(1, 20)
            uit.append(Document(f"verslag-{i}.pdf", "application/pdf",
                                F.pdf_tekst(pages, tekst=woord)))  # fmt: skip
        elif soort == "docx":
            uit.append(Document(f"brief-{i}.docx", DOCX, F.docx(f"Over {woord} en de rest.")))
        elif soort == "xlsx":
            uit.append(Document(f"lijst-{i}.xlsx", XLSX,
                                _uniek(F.xlsx(rng.randint(200, 2_000)), i)))  # fmt: skip
        elif soort == "pptx":
            uit.append(Document(f"dia-{i}.pptx", PPTX, _uniek(F.pptx(), i)))
        else:
            zin = f"Een korte notitie met {woord} erin. " * rng.randint(5, 200)
            match i % 3:
                case 0:
                    uit.append(Document(f"notitie-{i}.txt", "text/plain", zin.encode()))
                case 1:
                    html = f"<html><body><h1>Notitie</h1><p>{zin}</p></body></html>"
                    uit.append(Document(f"pagina-{i}.html", "text/html", html.encode()))
                case _:
                    rtf = "{\\rtf1\\ansi\\deff0 {\\fonttbl {\\f0 Arial;}}\\f0 " + zin + "\\par}"
                    uit.append(Document(f"tekst-{i}.rtf", "application/rtf", rtf.encode()))
    return uit


def zaai(
    data_dir: Path, owner: str, root: str, docs: list[Document], office_klaar: bool = False
) -> None:
    """De documenten als blob en node van owner, zonder rij in analysis (zoals vóór Z2).

    office_klaar: zoals op een 0.7.x met OnlyOffice aan, waar de inhaalronde van O3 al liep:
    elk document dat DS omzet (convert.EXT_BY_MIME), staat op pdf_state ready met de
    PDF_VERSION van 0.7.x (PDF_VERSION_07), zodat de inhaalronde er niets mee doet zolang die
    niet omhoog gaat. Het bestand van de PDF zelf staat er niet; de stap text leest docx, xlsx,
    pptx en rtf zelf, alleen doc, xls en ppt lezen de PDF (extract.LEGACY_MIMES), en die zitten
    niet in MIX."""
    pdf_mimes = set(EXT_BY_MIME) if office_klaar else set()
    blob_dir = Settings(data_dir=data_dir).blob_dir
    with connect(data_dir) as conn:
        s = Session(bind=conn)
        for i, doc in enumerate(docs):
            sha = hashlib.sha256(doc.data).hexdigest()
            pad = blob_dir / sha[:2] / sha[2:4] / sha
            pad.parent.mkdir(parents=True, exist_ok=True)
            pad.write_bytes(doc.data)
            pdf = doc.mime in pdf_mimes
            s.add(Blob(sha256=sha, size=len(doc.data), ref_count=1, created_at=DAG,
                       mime=doc.mime, preview="none",
                       render_version=previews.RENDER_VERSION,
                       pdf_state="ready" if pdf else None,
                       pdf_version=PDF_VERSION_07 if pdf else 0))  # fmt: skip
            s.add(
                Node(
                    id=uuid.uuid4(),
                    parent_id=uuid.UUID(root),
                    owner_id=uuid.UUID(owner),
                    name=doc.naam,
                    kind="file",
                    mime=doc.mime,
                    size=len(doc.data),
                    blob_id=sha,
                    created_at=DAG + timedelta(seconds=i),
                    modified_at=DAG + timedelta(seconds=i),
                )
            )
            if i % 200 == 199:
                s.flush()
        s.flush()


def stand(data_dir: Path, step: str = "text") -> Counter:
    """De rijen van deze stap van de analyse (text, ocr) per state (alleen lezen, naast de
    server)."""
    pad = data_dir / "platform.db"
    con = sqlite3.connect(f"file:{pad}?mode=ro", uri=True, timeout=10)
    try:
        rijen = con.execute(
            "SELECT state, count(*) FROM analysis WHERE step = ? GROUP BY state", (step,)
        ).fetchall()
    except sqlite3.OperationalError:
        return Counter()
    finally:
        con.close()
    return Counter(dict(rijen))


def media_beschikbaar() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def meet(
    soort: str,
    core: int,
    web_dist: Path,
    env: dict[str, str],
    zaai_: Callable[[Path, str, str], int],
    peil: Callable[[Path], tuple[dict[str, Any], bool]],
    na: Callable[[Path, dict[str, Any], int], tuple[dict[str, Any], str | None]],
) -> dict[str, Any]:
    """Een eigen server, de invoer erin (zaai_ geeft het aantal), en de tijd tot peil klaar
    zegt. na (de server draait nog): de stand die in de uitkomst komt, en wat er mis is."""
    base = Path(tempfile.mkdtemp(prefix=f"zoek-backfill-{soort}-"))
    server = Server(base / "data", web_dist=web_dist, env=env)
    vorige = os.sched_getaffinity(0)
    try:
        server.start()
        admin = make_admin(server)
        server.stop()
        n = zaai_(server.data_dir, admin.id, admin.root_id or "")
        # Vanaf hier alles op één core: de server en alles wat hij start erft het
        os.sched_setaffinity(0, {core})
        begin = time.monotonic()
        server.start()
        gestart = time.monotonic() - begin
        stand_: dict[str, Any] = {}
        while time.monotonic() - begin < WACHT_S:
            stand_, af = peil(server.data_dir)
            if af:
                break
            time.sleep(0.25)
        duur = time.monotonic() - begin
        os.sched_setaffinity(0, vorige)
        stand_, fout = na(server.data_dir, stand_, n)
        if fout:
            raise RuntimeError(f"{soort}: na {duur:.0f} s {fout}")
        return {
            "soort": soort,
            "n": n,
            "seconden": round(duur, 2),
            "start_s": round(gestart, 2),
            "per_minuut_per_core": round(n / duur * 60, 1),
            "states": stand_,
        }
    finally:
        os.sched_setaffinity(0, vorige)
        server.stop()
        shutil.rmtree(base, ignore_errors=True)


def een_keer(soort: str, docs: list[Document], core: int, web_dist: Path) -> dict[str, Any]:
    """De tekst: de documenten erin, en de tijd tot elke rij van de stap text klaar is."""
    env = {}
    if soort == "worker":
        from media_harness import media_env

        env = media_env(None)

    def zaai_docs(data_dir: Path, owner: str, root: str) -> int:
        zaai(data_dir, owner, root, docs)
        return len(docs)

    def peil(data_dir: Path) -> tuple[dict[str, Any], bool]:
        tellingen = stand(data_dir)
        open_ = tellingen.get("wait", 0) + tellingen.get("busy", 0)
        return dict(tellingen), sum(tellingen.values()) >= len(docs) and open_ == 0

    def na(_data_dir: Path, tellingen: dict[str, Any], n: int) -> tuple[dict, str | None]:
        klaar = sum(tellingen.get(s, 0) for s in KLAAR)
        return tellingen, None if klaar >= n else f"pas {tellingen}"

    return meet(soort, core, web_dist, env, zaai_docs, peil, na)


def foto_keer(
    soort: str, fotos: list[Any], core: int, web_dist: Path, places_db: Path | None = None
) -> dict[str, Any]:
    """photometa of geo: de foto's erin (zoek_foto.OUD of GEO), media uit, en de tijd tot de
    keten van photometa niets meer te doen heeft."""
    import zoek_foto as zf

    if soort == "geo":
        stand_, env = zf.GEO, {"PLATFORM_PLACES": "aan", "PLATFORM_PLACES_DB": str(places_db)}
    else:
        stand_, env = zf.OUD, {"PLATFORM_PLACES": "uit"}
    met_gps = 0

    def zaai_fotos(data_dir: Path, owner: str, root: str) -> int:
        nonlocal met_gps
        teller = zf.zaai(data_dir, owner, root, fotos, stand_)
        met_gps = teller["met_gps"]
        return teller["fotos"]

    def peil(data_dir: Path) -> tuple[dict[str, Any], bool]:
        s = zf.foto_stand(data_dir, volledig=False)
        return s, s["open"] == 0 and s.get("te_doen") == 0

    def na(data_dir: Path, _s: dict[str, Any], n: int) -> tuple[dict, str | None]:
        vol = {**zf.foto_stand(data_dir), "met_gps": met_gps}
        return vol, foto_fout(soort, vol, n)

    return meet(soort, core, web_dist, env, zaai_fotos, peil, na)


def foto_fout(soort: str, vol: dict[str, int], n: int) -> str | None:
    """Wat er mis is na een meting van photometa of geo (None: niets): niet alles gelezen, of
    niet gemeten wat de rij van §4.5 bedoelt (geen plek gelezen, geen land opgezocht)."""
    if vol.get("open") or vol.get("te_doen") != 0:
        return f"niet klaar: {vol}"
    if vol.get("met_gps") and not vol.get("plekken"):
        return f"geen enkele plek gelezen: {vol}"
    if soort == "photometa" and vol.get("gehasht", 0) < n // 2:
        return f"minder dan de helft van de {n} foto's heeft een pHash: {vol}"
    if soort == "geo" and not vol.get("met_land"):
        return f"geen plek kreeg een land (places.sqlite niet gebruikt?): {vol}"
    return None


def bouw_places(bron: Path, doel: Path) -> dict[str, Any]:
    """places.sqlite uit de bronnen in bron (tools/geo/haal.py), zoals app.cli geo-load; niet
    in de gemeten tijd. SystemExit als de bronnen er niet (goed) zijn."""
    from app.geo import load

    klok = time.monotonic()
    try:
        n = load.build(bron, doel)
    except load.BronFout as e:
        raise SystemExit(
            f"--geo-bron {bron}: {e}; vul de map met python3 -I tools/geo/haal.py --naar {bron}"
        ) from None
    return {**n, "seconden": round(time.monotonic() - klok, 1)}


def oordeel(soort: str, mediaan: float) -> tuple[bool | None, str]:
    """(gehaald, tekst) van de poort van deze soort; (None, "rapport") zonder poort."""
    grens = POORTEN.get(soort)
    if grens is None:
        return None, "rapport"
    ok = mediaan >= grens
    return ok, f"poort ≥ {grens}: {'gehaald' if ok else 'NIET gehaald'}"


def kort_regel(soort: str, mediaan: float, runs: list[dict[str, Any]]) -> tuple[bool | None, str]:
    """De regel van deze soort in het blok === kort ===, en of de poort gehaald is."""
    ok, tekst = oordeel(soort, mediaan)
    naam, eenheid = NAAM[soort]
    alle = ", ".join(str(r["per_minuut_per_core"]) for r in runs)
    return ok, f"{naam}: mediaan {mediaan} {eenheid} per minuut per core ({alle}); {tekst}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, help="standaard 2.000 (tekst), 5.000 of 55.000")
    parser.add_argument("--keer", type=int, default=3)
    parser.add_argument(
        "--soort", choices=("worker", "terugval", "beide", *FOTO_SOORTEN), default="beide"
    )
    parser.add_argument("--core", type=int, default=min(os.sched_getaffinity(0)))
    parser.add_argument("--json", type=Path)
    parser.add_argument(
        "--geo-bron", type=Path, help="(geo) de map met de bronnen van tools/geo/haal.py"
    )
    args = parser.parse_args(argv)
    if args.soort == "geo" and args.geo_bron is None:
        parser.error(
            "--soort geo heeft --geo-bron DIR nodig: de map met de bronnen van places.sqlite "
            "(python3 -I tools/geo/haal.py --naar DIR)"
        )

    soorten = ["worker", "terugval"] if args.soort == "beide" else [args.soort]
    n = args.n or N[soorten[0]]
    zonder = []
    if "worker" in soorten and not media_beschikbaar():
        soorten.remove("worker")
        zonder.append("worker (geen ffmpeg of ffprobe)")
    werk = Path(tempfile.mkdtemp(prefix="zoek-backfill-werk-"))
    web = werk / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>zoek_backfill</title>")
    uitkomst: dict[str, Any] = {"n": n, "core": args.core, "keer": {}, "mediaan": {}}
    try:
        keer = maak_invoer(args.soort, n, werk, uitkomst, args.geo_bron)
        for soort in soorten:
            runs = []
            for k in range(args.keer):
                r = keer(soort, args.core)
                runs.append(r)
                print(f"{soort} {k + 1}: {r['seconden']} s, {r['per_minuut_per_core']} per minuut "
                      f"per core (start {r['start_s']} s), {r['states']}", flush=True)  # fmt: skip
            uitkomst["keer"][soort] = runs
            uitkomst["mediaan"][soort] = statistics.median(r["per_minuut_per_core"] for r in runs)
    finally:
        shutil.rmtree(werk, ignore_errors=True)
    if args.json:
        args.json.write_text(json.dumps(uitkomst, indent=2))
    print("=== kort ===")
    goed = True
    for soort, mediaan in uitkomst["mediaan"].items():
        ok, regel = kort_regel(soort, mediaan, uitkomst["keer"][soort])
        goed &= ok is not False
        print(regel)
    if "places" in uitkomst:
        p = uitkomst["places"]
        print(f"places.sqlite: {p['plaatsen']} plaatsen en {p['landen']} landen, gebouwd in "
              f"{p['seconden']} s (niet in de meting)")  # fmt: skip
    for wat in zonder:
        print(f"niet gemeten: {wat}")
    return 0 if goed else 1


def maak_invoer(
    soort: str, n: int, werk: Path, uitkomst: dict[str, Any], geo_bron: Path | None
) -> Callable[[str, int], dict[str, Any]]:
    """De invoer van deze soort, één keer gemaakt (vast zaad), en wat één meting doet. In werk
    (dat aan het eind weg gaat) staat de web-app (alleen index.html) en voor geo places.sqlite."""
    web = werk / "web"
    klok = time.monotonic()
    if soort not in FOTO_SOORTEN:
        docs = documenten(n)
        mix = Counter(d.mime for d in docs)
        print(f"{len(docs)} documenten gemaakt in {time.monotonic() - klok:.1f} s: {dict(mix)}",
              flush=True)  # fmt: skip
        return lambda s, core: een_keer(s, docs, core, web)
    import zoek_foto as zf

    places_db = None
    if soort == "geo":
        assert geo_bron is not None
        places_db = werk / "geo" / "places.sqlite"
        uitkomst["places"] = bouw_places(geo_bron, places_db)
        klok = time.monotonic()
        plekken = zf.punten(n, plaatsen=zf.plaatsen_uit(places_db))
        fotos = list(zf.kleine_fotos(plekken))
    else:
        fotos = list(zf.fotos(n))
    met_gps = sum(f.gps is not None for f in fotos)
    print(f"{len(fotos)} foto's gemaakt in {time.monotonic() - klok:.1f} s ({met_gps} met een "
          f"plek)", flush=True)  # fmt: skip
    return lambda s, core: foto_keer(s, fotos, core, web, places_db)


if __name__ == "__main__":
    sys.exit(main())
