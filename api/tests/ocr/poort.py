"""De poort van §4.5 voor OCR (bouwontwerp fase 6, X1): ≥ 8 pagina's per minuut per core.

130 gemaakte scans (tests/ocr/scans.py) op A4 van 300 dpi in DejaVu Sans, als PDF zonder
tekstlaag, elk met eigen ruis op het papier, in twee dichtheden:

- brief: 100 keer de brief van 12 regels (46 px), 20 per stand: recht, 3°, 7°, −5° en een
  kwartslag (90°);
- vol: 30 keer een volle pagina van 44 regels in een kleinere letter (34 px, ±8 punt, ±700
  woorden), 10 per stand: recht, 3° en een kwartslag (review golf 4, uitrol F4: met alleen de
  brief zei de poort te weinig over een pagina vol tekst).

Eén voor één door de echte opdracht ocr van een worker in dit proces (kind 1: app.render
ocr_page met de rechtzetting; kind 2: tesseract met nld+eng, de OSD waar het nodig is), op één
core. Draai het onder `taskset -c 0`; met meer cores zet het script zichzelf (en dus de
kinderen) op de laagste core die het mag gebruiken.

De scans maken telt niet mee in de tijd. Per dichtheid en per stand: pagina's per minuut, de
recall (scans.recall: welk deel van de woorden van die pagina gevonden is), de scheefstand die
de worker mat, hoe vaak de OSD nodig was, en de mediaan van de tijden uit het antwoord (render
en deskew van kind 1, de herkenning). Aan het eind de regel "OCR-poort: …"; de code is 1 als
alles samen (de brieven en de volle pagina's) onder de 8 pagina's per minuut blijft, of als een
pagina niet gelezen werd (een andere stand dan done, of een recall onder 0,9: een snelle fout
telt niet als snelheid). De poort gaat over de mix, zoals §4.5 hem stelt; de cijfers per
dichtheid staan erbij, ook als één dichtheid alleen onder de 8 zou blijven.

    cd api && taskset -c 0 uv run python -m tests.ocr.poort [--json uitkomst.json]

Zonder tesseract (of zonder nld, eng of osd) of zonder DejaVu Sans stopt het met een melding.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

from app.media import client
from app.media.wire import Op
from app.media.worker import args as A
from app.media.worker.ops import caps as caps_op
from app.media.worker.ops import ocr
from app.media.worker.server import Config, Worker
from tests.ocr import scans

GATE_PAGES_PER_MIN = 8.0  # §4.5
MIN_RECALL = 0.9  # zoals tests/ocr/test_ocr_real.py bij 7°, −5° en een kwartslag
ANGLES = (0, 3, 7, -5, 90)
PER_ANGLE = 20
# De volle pagina (review golf 4, uitrol F4): minder standen, want hij duurt langer per pagina
VOL_ANGLES = (0, 3, 90)
VOL_PER_ANGLE = 10
DENSITIES = ("brief", "vol")
CAPS = {"protocol": 1, "ffmpeg": "poort", "ffprobe": "poort", "mode": "local", "sandbox": "off"}
KEY = "de-sleutel-van-de-poort-van-ocr-" * 2
APT = "apt install tesseract-ocr tesseract-ocr-nld tesseract-ocr-eng tesseract-ocr-osd"


def one_core() -> int:
    """Eén core voor dit proces en zijn kinderen: de laagste die het mag gebruiken."""
    allowed = os.sched_getaffinity(0)
    core = min(allowed)
    if len(allowed) > 1:
        os.sched_setaffinity(0, {core})
        print(f"affiniteit was {sorted(allowed)}: nu alleen core {core}")
    return core


def make(folder: Path, pages: int, vol: int = 0) -> list[tuple[str, int, Path]]:
    """De scans als PDF op schijf, elk met eigen ruis: de brief per stand pages // len(ANGLES),
    de volle pagina per stand vol // len(VOL_ANGLES) (0: geen)."""
    out = []
    for density, angles, total in (("brief", ANGLES, pages), ("vol", VOL_ANGLES, vol)):
        if total <= 0:
            continue
        per = max(1, total // len(angles))
        for angle in angles:
            for n in range(per):
                variant = 1 + len(out)
                path = folder / f"scan-{density}-{angle}-{n:02d}.pdf"
                path.write_bytes(scans.pdf(angle, variant, vol=density == "vol"))
                out.append((density, angle, path))
    return out


async def recognize(path: Path) -> tuple[list[list], dict]:
    job, _ = await client.call(Op.OCR, A.OcrArgs(kind="pdf"), source=path)
    async with job:
        events = [e async for e in job.events()]
    words = [w for e in events[:-1] for w in e["data"]["words"]]
    return words, events[-1]["data"]


async def measure(folder: Path, sources: list[tuple[str, int, Path]]) -> list[dict]:
    config = Config(socket=folder / "run" / "w.sock", max_procs=4, max_transcodes=1, key=KEY)
    worker = Worker(config, dict(CAPS))
    await worker.start()
    client.bind(config.socket, KEY)
    rows = []
    try:
        for density, angle, path in sources:
            started = time.monotonic()
            words, summary = await recognize(path)
            seconds = time.monotonic() - started
            found = [t for w in words for t in scans.tokens(str(w[0]))]
            expected = scans.VOL_WORDS if density == "vol" else scans.WORDS
            rows.append(
                {
                    "density": density,
                    "angle": angle,
                    "seconds": seconds,
                    "state": summary["state"],
                    "code": summary.get("code"),
                    "recall": scans.recall(found, expected),
                    "skew": summary.get("skew"),
                    "osd": bool(summary.get("osd")),
                    "ms": summary.get("ms") or {},
                }
            )
    finally:
        await worker.stop()
        client.bind(None)
    return rows


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _angles(rows: list[dict]) -> dict:
    per_angle = {}
    for angle in sorted({r["angle"] for r in rows}, key=lambda a: (ANGLES + VOL_ANGLES).index(a)):
        mine = [r for r in rows if r["angle"] == angle]
        seconds = sum(r["seconds"] for r in mine)
        per_angle[str(angle)] = {
            "pages": len(mine),
            "per_minute": round(60 * len(mine) / seconds, 1),
            "recall_min": round(min(r["recall"] for r in mine), 3),
            "recall_mean": round(statistics.mean(r["recall"] for r in mine), 3),
            "skew_median": median([r["skew"] for r in mine if r["skew"] is not None]),
            "osd": sum(r["osd"] for r in mine),
            "ms_median": {
                k: median([r["ms"][k] for r in mine if k in r["ms"]])
                for k in ("render", "deskew", "ocr")
            },
        }
    return per_angle


def report(rows: list[dict], core: int, total_s: float) -> dict:
    """Alles samen (de tijd van de hele meting) en per dichtheid (de som van de tijden van zijn
    pagina's), elk met de stappen per stand."""
    per_density = {}
    for density in DENSITIES:
        mine = [r for r in rows if r["density"] == density]
        if not mine:
            continue
        seconds = sum(r["seconds"] for r in mine)
        per_density[density] = {
            "pages": len(mine),
            "seconds": round(seconds, 1),
            "per_minute": round(60 * len(mine) / seconds, 1),
            "per_angle": _angles(mine),
        }
    return {
        "pages": len(rows),
        "core": core,
        "seconds": round(total_s, 1),
        "per_minute": round(60 * len(rows) / total_s, 1) if total_s > 0 else 0.0,
        "gate": GATE_PAGES_PER_MIN,
        "per_density": per_density,
        "not_read": [
            {k: r[k] for k in ("density", "angle", "state", "code", "recall")}
            for r in rows
            if r["state"] != "done" or r["recall"] < MIN_RECALL
        ],
    }


def passed(out: dict) -> bool:
    """De poort: alles samen ≥ 8 pagina's per minuut, en elke pagina gelezen."""
    return out["per_minute"] >= GATE_PAGES_PER_MIN and not out["not_read"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="De poort van §4.5 voor OCR (X1)")
    parser.add_argument("--paginas", type=int, default=PER_ANGLE * len(ANGLES))
    parser.add_argument(
        "--vol", type=int, default=VOL_PER_ANGLE * len(VOL_ANGLES), help="volle pagina's"
    )
    parser.add_argument("--json", type=Path, help="de uitkomst ook als JSON hierheen")
    args = parser.parse_args(argv)
    if ocr.program() is None:
        print(f"OCR-poort: tesseract ontbreekt in de PATH ({APT})")
        return 2
    if not caps_op.ocr():
        print(f"OCR-poort: tesseract kent nld, eng of osd niet ({APT})")
        return 2
    scans.font()  # NoFont met de apt-regel als DejaVu Sans er niet is
    core = one_core()
    with tempfile.TemporaryDirectory(prefix="ocr-poort-") as tmp:
        folder = Path(tmp)
        started = time.monotonic()
        sources = make(folder, args.paginas, args.vol)
        print(f"{len(sources)} scans gemaakt in {time.monotonic() - started:.1f} s")
        started = time.monotonic()
        rows = asyncio.run(measure(folder, sources))
        total_s = time.monotonic() - started
    out = report(rows, core, total_s)
    for density, d in out["per_density"].items():
        for angle, a in d["per_angle"].items():
            ms = a["ms_median"]
            print(
                f"OCR-poort {density} {angle:>3}°: {a['pages']} pagina's, "
                f"{a['per_minute']} per minuut, recall {a['recall_min']}–{a['recall_mean']} "
                f"(min–gem), scheef {a['skew_median']}°, OSD {a['osd']}×, mediaan render "
                f"{ms['render']} ms, deskew {ms['deskew']} ms, herkenning {ms['ocr']} ms"
            )
        print(
            f"OCR-poort {density}: {d['per_minute']} pagina's per minuut "
            f"({d['pages']} pagina's in {d['seconds']} s)"
        )
    if args.json:
        args.json.write_text(json.dumps(out, indent=2) + "\n")
    ok = passed(out)
    print(
        f"OCR-poort: {out['per_minute']} pagina's per minuut op core {core} "
        f"({out['pages']} pagina's in {out['seconds']} s; poort ≥ {GATE_PAGES_PER_MIN:g}): "
        + ("gehaald" if ok else "NIET gehaald")
    )
    if out["not_read"]:
        print(f"OCR-poort: niet (goed) gelezen: {out['not_read']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
