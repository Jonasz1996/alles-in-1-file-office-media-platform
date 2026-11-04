"""De snelheid van de backfill van de tekst (fase 6, Z2; bouwontwerp §4.5): de hele keten op
één core.

Poort: tekst in de worker ≥ 1.000 documenten per minuut per core. De terugval zonder media
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

    cd api && uv run --with playwright python ../e2e/perf/zoek_backfill.py
    cd api && uv run --with playwright python ../e2e/perf/zoek_backfill.py --soort terugval \\
        --n 500 --keer 1 --json /tmp/zoek_backfill.json

Met media aan zijn ffmpeg en ffprobe nodig (de worker start niet zonder); zonder die meet het
script alleen de terugval en zegt dat.
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
from conftest import Server, make_admin  # noqa: E402
from seed import connect  # noqa: E402

POORT_PER_MINUUT = 1_000  # per core, tekst in de worker (§4.5)
DAG = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)  # een vaste dag (de regel van de e2e)
WACHT_S = 900.0
KLAAR = ("done", "none", "skip", "failed")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
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


def zaai(data_dir: Path, owner: str, root: str, docs: list[Document]) -> None:
    """De documenten als blob en node van owner, zonder rij in analysis (zoals vóór Z2)."""
    blob_dir = Settings(data_dir=data_dir).blob_dir
    with connect(data_dir) as conn:
        s = Session(bind=conn)
        for i, doc in enumerate(docs):
            sha = hashlib.sha256(doc.data).hexdigest()
            pad = blob_dir / sha[:2] / sha[2:4] / sha
            pad.parent.mkdir(parents=True, exist_ok=True)
            pad.write_bytes(doc.data)
            s.add(Blob(sha256=sha, size=len(doc.data), ref_count=1, created_at=DAG,
                       mime=doc.mime, preview="none",
                       render_version=previews.RENDER_VERSION))  # fmt: skip
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


def stand(data_dir: Path) -> Counter:
    """De rijen van de stap text per state (alleen lezen, naast de server)."""
    pad = data_dir / "platform.db"
    con = sqlite3.connect(f"file:{pad}?mode=ro", uri=True, timeout=10)
    try:
        rijen = con.execute(
            "SELECT state, count(*) FROM analysis WHERE step = 'text' GROUP BY state"
        ).fetchall()
    except sqlite3.OperationalError:
        return Counter()
    finally:
        con.close()
    return Counter(dict(rijen))


def media_beschikbaar() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def een_keer(soort: str, docs: list[Document], core: int, web_dist: Path) -> dict[str, Any]:
    """Een eigen server, de documenten erin, en de tijd tot alles gelezen is."""
    base = Path(tempfile.mkdtemp(prefix=f"zoek-backfill-{soort}-"))
    env = {}
    if soort == "worker":
        from media_harness import media_env

        env = media_env(None)
    server = Server(base / "data", web_dist=web_dist, env=env)
    vorige = os.sched_getaffinity(0)
    try:
        server.start()
        admin = make_admin(server)
        server.stop()
        zaai(server.data_dir, admin.id, admin.root_id or "", docs)
        # Vanaf hier alles op één core: de server en alles wat hij start erft het
        os.sched_setaffinity(0, {core})
        begin = time.monotonic()
        server.start()
        gestart = time.monotonic() - begin
        tellingen: Counter = Counter()
        while time.monotonic() - begin < WACHT_S:
            tellingen = stand(server.data_dir)
            open_ = tellingen.get("wait", 0) + tellingen.get("busy", 0)
            if sum(tellingen.values()) >= len(docs) and open_ == 0:
                break
            time.sleep(0.25)
        duur = time.monotonic() - begin
        os.sched_setaffinity(0, vorige)
        klaar = sum(tellingen.get(s, 0) for s in KLAAR)
        if klaar < len(docs):
            raise RuntimeError(f"{soort}: na {duur:.0f} s pas {dict(tellingen)}")
        return {
            "soort": soort,
            "n": len(docs),
            "seconden": round(duur, 2),
            "start_s": round(gestart, 2),
            "per_minuut_per_core": round(len(docs) / duur * 60, 1),
            "states": dict(tellingen),
        }
    finally:
        os.sched_setaffinity(0, vorige)
        server.stop()
        shutil.rmtree(base, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, default=2_000)
    parser.add_argument("--keer", type=int, default=3)
    parser.add_argument("--soort", choices=("worker", "terugval", "beide"), default="beide")
    parser.add_argument("--core", type=int, default=min(os.sched_getaffinity(0)))
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    soorten = ["worker", "terugval"] if args.soort == "beide" else [args.soort]
    zonder = []
    if "worker" in soorten and not media_beschikbaar():
        soorten.remove("worker")
        zonder.append("worker (geen ffmpeg of ffprobe)")
    web = Path(tempfile.mkdtemp(prefix="zoek-backfill-web-"))
    (web / "index.html").write_text("<!doctype html><title>zoek_backfill</title>")
    klok = time.monotonic()
    docs = documenten(args.n)
    gemaakt = time.monotonic() - klok
    mix = Counter(d.mime for d in docs)
    print(f"{len(docs)} documenten gemaakt in {gemaakt:.1f} s: {dict(mix)}", flush=True)
    uitkomst: dict[str, Any] = {"n": args.n, "core": args.core, "keer": {}, "mediaan": {}}
    try:
        for soort in soorten:
            runs = []
            for k in range(args.keer):
                r = een_keer(soort, docs, args.core, web)
                runs.append(r)
                print(f"{soort} {k + 1}: {r['seconden']} s, {r['per_minuut_per_core']} per minuut "
                      f"per core (start {r['start_s']} s), {r['states']}", flush=True)  # fmt: skip
            uitkomst["keer"][soort] = runs
            uitkomst["mediaan"][soort] = statistics.median(r["per_minuut_per_core"] for r in runs)
    finally:
        shutil.rmtree(web, ignore_errors=True)
    if args.json:
        args.json.write_text(json.dumps(uitkomst, indent=2))
    print("=== kort ===")
    goed = True
    for soort, mediaan in uitkomst["mediaan"].items():
        if soort == "worker":
            ok = mediaan >= POORT_PER_MINUUT
            goed &= ok
            oordeel = f"poort ≥ {POORT_PER_MINUUT}: {'gehaald' if ok else 'NIET gehaald'}"
        else:
            oordeel = "rapport"
        alle = ", ".join(str(r["per_minuut_per_core"]) for r in uitkomst["keer"][soort])
        print(
            f"tekst {soort}: mediaan {mediaan} documenten per minuut per core ({alle}); {oordeel}"
        )
    for wat in zonder:
        print(f"niet gemeten: {wat}")
    return 0 if goed else 1


if __name__ == "__main__":
    sys.exit(main())
