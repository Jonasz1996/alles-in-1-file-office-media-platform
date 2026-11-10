"""De poort van §4.5 voor OCR (bouwontwerp fase 6, X1): ≥ 8 pagina's per minuut per core.

100 gemaakte scans (tests/ocr/scans.py): de brief op A4 van 300 dpi in DejaVu Sans, als PDF
zonder tekstlaag, 20 per stand: recht, 3°, 7°, −5° en een kwartslag (90°), elk met eigen ruis
op het papier. Eén voor één door de echte opdracht ocr van een worker in dit proces (kind 1:
app.render ocr_page met de rechtzetting; kind 2: tesseract met nld+eng, de OSD waar het nodig
is), op één core. Draai het onder `taskset -c 0`; met meer cores zet het script zichzelf (en dus
de kinderen) op de laagste core die het mag gebruiken.

De scans maken telt niet mee in de tijd. Per stand: pagina's per minuut, de recall
(scans.recall: welk deel van de woorden van de brief gevonden is), de scheefstand die de worker
mat, hoe vaak de OSD nodig was, en de mediaan van de tijden uit het antwoord (render en deskew
van kind 1, de herkenning). Aan het eind de regel "OCR-poort: …"; de code is 1 als het onder de
8 pagina's per minuut blijft, of als een pagina niet gelezen werd (een andere stand dan done, of
een recall onder 0,9: een snelle fout telt niet als snelheid).

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


def make(folder: Path, pages: int) -> list[tuple[int, Path]]:
    """De scans als PDF op schijf: per stand pages // len(ANGLES), elk met eigen ruis."""
    out = []
    per = max(1, pages // len(ANGLES))
    for angle in ANGLES:
        for n in range(per):
            variant = 1 + len(out)
            path = folder / f"scan-{angle}-{n:02d}.pdf"
            path.write_bytes(scans.pdf(angle, variant))
            out.append((angle, path))
    return out


async def recognize(path: Path) -> tuple[list[list], dict]:
    job, _ = await client.call(Op.OCR, A.OcrArgs(kind="pdf"), source=path)
    async with job:
        events = [e async for e in job.events()]
    words = [w for e in events[:-1] for w in e["data"]["words"]]
    return words, events[-1]["data"]


async def measure(folder: Path, sources: list[tuple[int, Path]]) -> list[dict]:
    config = Config(socket=folder / "run" / "w.sock", max_procs=4, max_transcodes=1, key=KEY)
    worker = Worker(config, dict(CAPS))
    await worker.start()
    client.bind(config.socket, KEY)
    rows = []
    try:
        for angle, path in sources:
            started = time.monotonic()
            words, summary = await recognize(path)
            seconds = time.monotonic() - started
            found = [t for w in words for t in scans.tokens(str(w[0]))]
            rows.append(
                {
                    "angle": angle,
                    "seconds": seconds,
                    "state": summary["state"],
                    "code": summary.get("code"),
                    "recall": scans.recall(found),
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


def report(rows: list[dict], core: int, total_s: float) -> dict:
    per_angle = {}
    for angle in ANGLES:
        mine = [r for r in rows if r["angle"] == angle]
        if not mine:
            continue
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
    return {
        "pages": len(rows),
        "core": core,
        "seconds": round(total_s, 1),
        "per_minute": round(60 * len(rows) / total_s, 1) if total_s > 0 else 0.0,
        "gate": GATE_PAGES_PER_MIN,
        "per_angle": per_angle,
        "not_read": [
            {k: r[k] for k in ("angle", "state", "code", "recall")}
            for r in rows
            if r["state"] != "done" or r["recall"] < MIN_RECALL
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="De poort van §4.5 voor OCR (X1)")
    parser.add_argument("--paginas", type=int, default=PER_ANGLE * len(ANGLES))
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
        sources = make(folder, args.paginas)
        print(f"{len(sources)} scans gemaakt in {time.monotonic() - started:.1f} s")
        started = time.monotonic()
        rows = asyncio.run(measure(folder, sources))
        total_s = time.monotonic() - started
    out = report(rows, core, total_s)
    for angle, a in out["per_angle"].items():
        ms = a["ms_median"]
        print(
            f"OCR-poort {angle:>3}°: {a['pages']} pagina's, {a['per_minute']} per minuut, "
            f"recall {a['recall_min']}–{a['recall_mean']} (min–gem), scheef {a['skew_median']}°, "
            f"OSD {a['osd']}×, mediaan render {ms['render']} ms, deskew {ms['deskew']} ms, "
            f"herkenning {ms['ocr']} ms"
        )
    if args.json:
        args.json.write_text(json.dumps(out, indent=2) + "\n")
    ok = out["per_minute"] >= GATE_PAGES_PER_MIN and not out["not_read"]
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
