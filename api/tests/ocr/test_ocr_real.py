"""OCR met de echte tesseract (marker ocr, job ocr; bouwontwerp fase 6, X1 en §4.5).

Een worker in dit proces met de echte opdracht ocr: kind 1 (app.render ocr_page) en tesseract
5 met nld, eng en osd. De bronnen zijn gemaakt (tests/ocr/scans.py): een A4 van 300 dpi in
DejaVu Sans, scheef, een kwartslag, een telefoonfoto met EXIF-draaiing 6, een HEIC en een PDF
zonder tekstlaag. Per bron:

- recall: welk deel van de woorden van de brief de herkenning vond (≥ 0,95 recht en bij 3°,
  ≥ 0,9 bij 7°, −5° en een kwartslag); gemeten 1,0 overal;
- de vakjes: het midden van elk woord dat maar één keer in de brief staat, teruggerekend met
  geometry.centre, ligt binnen 8 px van waar het in het beeld staat zoals de viewer het toont
  (gemeten: mediaan 0,5 tot 1,0 px, hooguit 2,6 px);
- de OSD alleen waar het nodig is: niet bij een rechte pagina, wel bij 90° en 180°;
- een kleine snelheidstoets: ≥ 8 pagina's per minuut op één core over vier bronnen van de brief
  (12 regels; gemeten 45–48). Dat zegt weinig over een pagina vol tekst: de poort van §4.5 is
  tests/ocr/poort.py, met naast 100 brieven ook 30 volle pagina's van 44 regels (gemeten 21,3
  pagina's per minuut over alles, 10,7 voor de volle pagina's; review golf 4, uitrol F4).

Zonder tesseract (of zonder nld, eng of osd) of zonder DejaVu Sans faalt dit met een duidelijke
melding; overslaan doet het nooit.
"""

from __future__ import annotations

import os
import statistics
import time
from pathlib import Path

import pytest

from app.media import client
from app.media.wire import Op
from app.media.worker import args as A
from app.media.worker.ops import caps as caps_op
from app.media.worker.ops import ocr
from app.media.worker.server import Config, Worker
from app.ocr import geometry
from tests.ocr import scans

pytestmark = [pytest.mark.ocr, pytest.mark.anyio]

CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}
KEY = "de-sleutel-van-de-echte-ocr-" * 2
APT = "apt install tesseract-ocr tesseract-ocr-nld tesseract-ocr-eng tesseract-ocr-osd"
BOX_PX = 8.0
GATE_PAGES_PER_MIN = 8.0


@pytest.fixture(scope="module", autouse=True)
def _tesseract_en_dejavu():
    if ocr.program() is None:
        pytest.fail(f"tesseract ontbreekt in de PATH ({APT})")
    if not caps_op.ocr():
        pytest.fail(f"tesseract kent nld, eng of osd niet ({APT})")
    scans.font()  # NoFont met de apt-regel als DejaVu Sans er niet is


@pytest.fixture
async def worker(tmp_path):
    config = Config(socket=tmp_path / "run" / "w.sock", max_procs=4, max_transcodes=1, key=KEY)
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    try:
        yield w
    finally:
        await w.stop()
        client.bind(None)


async def recognize(path: Path, kind: str = "image") -> tuple[list[list], dict, float]:
    started = time.monotonic()
    job, _ = await client.call(Op.OCR, A.OcrArgs(kind=kind), source=path)
    async with job:
        events = [e async for e in job.events()]
    words = [w for e in events[:-1] for w in e["data"]["words"]]
    return words, events[-1]["data"], time.monotonic() - started


def box_errors(words: list[list], summary: dict, want: tuple[scans.Word, ...]) -> list[float]:
    """De afstand (px) tussen het teruggerekende midden en het echte, per uniek woord."""
    where = {w.text: w for w in want if w.text in scans.unique()}
    size = (summary["width"], summary["height"])
    source = tuple(summary["source"])
    errors = []
    for text, x, y, w, h, _line in words:
        if text in where:
            cx, cy = geometry.centre((x, y, w, h), summary["skew"], size, source)
            errors.append(((cx - where[text].x) ** 2 + (cy - where[text].y) ** 2) ** 0.5)
    return errors


def check(words: list[list], summary: dict, want, *, recall: float, size) -> None:
    text = ocr.text_of(words)
    found = scans.recall(scans.tokens(text))
    assert summary["state"] == "done" and summary["code"] is None, summary
    assert found >= recall, (found, text)
    assert summary["chars"] == len(text) and summary["words"] == len(words)
    # de bron zoals de viewer hem toont; een PDF-pagina op 300 dpi rondt af (2481 × 3508)
    assert all(abs(a - b) <= 1 for a, b in zip(summary["source"], size, strict=True)), summary
    errors = box_errors(words, summary, want)
    assert len(errors) >= 0.9 * len(scans.unique()), len(errors)
    assert max(errors) <= BOX_PX, sorted(errors)[-5:]
    print(
        f"OCR: recall {found:.3f}, vakjes mediaan {statistics.median(errors):.1f} px, "
        f"hooguit {max(errors):.1f} px, skew {summary['skew']}, rotate {summary['rotate']}, "
        f"ms {summary['ms']}"
    )


async def test_caps_zegt_ocr():
    assert caps_op.gather(local=True, max_procs=1, max_transcodes=1)["ocr"] is True


@pytest.mark.parametrize("angle,recall", [(0, 0.95), (3, 0.95), (7, 0.9), (-5, 0.9)])
async def test_een_scheve_scan(tmp_path, worker, angle, recall):
    path = tmp_path / "scan.png"
    path.write_bytes(scans.png(angle))
    words, summary, _ = await recognize(path)
    check(words, summary, scans.words(angle), recall=recall, size=scans.A4)
    assert abs(summary["skew"] - angle) <= 0.15, summary["skew"]
    assert (summary["width"], summary["height"]) >= scans.A4
    assert summary["osd"] is False and summary["rotate"] == 0  # geen OSD nodig


@pytest.mark.parametrize("angle,rotate", [(90, 90), (180, 180), (270, None)])
async def test_een_kwartslag(tmp_path, worker, angle, rotate):
    """Liggend of op zijn kop: de OSD draait het beeld (90 en 180); bij 270 leest tesseract de
    pagina al in de eerste ronde. De vakjes staan in het beeld zoals het is (liggend)."""
    path = tmp_path / "scan.png"
    path.write_bytes(scans.png(angle))
    words, summary, _ = await recognize(path)
    size = scans.A4 if angle == 180 else scans.A4[::-1]
    check(words, summary, scans.words(angle), recall=0.9, size=size)
    if rotate is not None:
        assert summary["osd"] is True and summary["rotate"] == rotate


async def test_een_foto_met_exif_draaiing(tmp_path, worker):
    """De pixels liggen, EXIF 6 zet hem rechtop: de vakjes staan in het rechtopstaande beeld."""
    path = tmp_path / "foto.jpg"
    path.write_bytes(scans.jpeg_exif6())
    words, summary, _ = await recognize(path)
    check(words, summary, scans.words(0), recall=0.95, size=scans.A4)
    assert summary["osd"] is False


async def test_een_heic(tmp_path, worker):
    path = tmp_path / "foto.heic"
    path.write_bytes(scans.heic(3))
    words, summary, _ = await recognize(path)
    check(words, summary, scans.words(3), recall=0.95, size=scans.A4)


async def test_een_pdf_zonder_tekstlaag(tmp_path, worker):
    path = tmp_path / "scan.pdf"
    path.write_bytes(scans.pdf(3))
    words, summary, _ = await recognize(path, kind="pdf")
    check(words, summary, scans.words(3), recall=0.95, size=scans.A4)
    # een pagina die er niet is
    job, _ = await client.call(Op.OCR, A.OcrArgs(kind="pdf", page=2), source=path)
    assert (await job.result())["state"] == "failed"


async def test_poort_pagina_per_minuut_per_core(tmp_path, worker):
    """≥ 8 pagina's per minuut op één core (kind 1, de rechtzetting en tesseract), voor de brief
    van 12 regels; de poort van §4.5 met volle pagina's is tests/ocr/poort.py."""
    sources = []
    for i, data in enumerate((scans.png(0), scans.png(3), scans.pdf(0), scans.jpeg_exif6())):
        path = tmp_path / f"p{i}"
        path.write_bytes(data)
        sources.append((path, "pdf" if i == 2 else "image"))
    before = os.sched_getaffinity(0)
    core = min(before)
    os.sched_setaffinity(0, {core})  # de kinderen erven één core
    try:
        times = []
        for path, kind in sources:
            _, summary, seconds = await recognize(path, kind)
            assert summary["state"] == "done", summary
            times.append(seconds)
    finally:
        os.sched_setaffinity(0, before)
    per_minute = 60 / statistics.mean(times)
    print(f"OCR: {per_minute:.1f} pagina's per minuut op core {core} ({times})")
    assert per_minute >= GATE_PAGES_PER_MIN, times
