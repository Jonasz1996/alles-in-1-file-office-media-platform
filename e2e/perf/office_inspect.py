"""De invoercontrole op grote bestanden: cpu en wachttijd (fase 4, §4.7, Keuze 23).

Doel (docs/fase-4/bouwontwerp.md §4.7): een xlsx met 10 MB XML en een pptx met 80 MB foto's
kosten de app elk ≤ 1 s cpu, in een thread (de lus van de app blijft vrij).

Methode
- Het script maakt de bestanden zelf, in een tijdelijke map (en ruimt ze op):
  - xlsx: een echt werkblad volgens [Content_Types].xml, met getallen, tekst en formules, en
    samen net onder XML_BYTES_SHEET (10 MiB) aan XML: het zwaarste dat nog door de controle
    komt. Deflate zoals zipfile het standaard doet;
  - pptx: een presentatie met dia's die elk een foto tonen, samen ±80 MB aan JPEG (Pillow,
    ruis met kleurverloop, kwaliteit 92, zoals een foto van een telefoon), deflate zoals
    PowerPoint media opslaat.
- Per herhaling: de cache van de controle leeg (inspect.forget), dan inspect.check() zoals de
  app hem aanroept (anyio.to_thread). In de thread meten we time.thread_time() van _inspect
  (de cpu van de controle zelf, ook zlib), daarbuiten de wandtijd, en tegelijk de grootste
  vertraging van een tikker van 10 ms op de lus (bewijs dat de lus vrij blijft).
- De bestanden staan na het schrijven in de paginacache, zoals vlak na een upload.

Los draaien (geen DS nodig; JSON op stdout):
    cd api && uv run python ../e2e/perf/office_inspect.py --runs 5
Via pytest (marker perf):
    cd api && uv run --with playwright pytest ../e2e/perf/office_inspect.py -s
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import random
import shutil
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import office_bench as ob  # noqa: E402 - pas na het pad naar e2e/perf

from conftest import report_only, write_result  # noqa: E402

pytestmark = pytest.mark.perf

GATE = {"cpu_s": 1.0}
MB = 1024**2
PHOTO_BYTES = 80 * 1000**2

XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
PKG = "http://schemas.openxmlformats.org/package/2006"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DOC = "application/vnd.openxmlformats-officedocument"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PML = "http://schemas.openxmlformats.org/presentationml/2006/main"
DML = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _types(*overrides: tuple[str, str], jpeg: bool = False) -> str:
    parts = [
        f'<Types xmlns="{PKG}/content-types">',
        f'<Default Extension="rels" ContentType="{PKG[:-5]}relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
    ]
    if jpeg:
        parts.append('<Default Extension="jpeg" ContentType="image/jpeg"/>')
    parts += [f'<Override PartName="{name}" ContentType="{kind}"/>' for name, kind in overrides]
    return XML + "".join(parts) + "</Types>"


def _rels(*targets: tuple[str, str]) -> str:
    """Relaties rId1, rId2, ... naar (soort, doel)."""
    body = "".join(
        f'<Relationship Id="rId{n}" Type="{REL}/{kind}" Target="{target}"/>'
        for n, (kind, target) in enumerate(targets, start=1)
    )
    return XML + f'<Relationships xmlns="{PKG}/relationships">{body}</Relationships>'


CT_XLSX = _types(
    ("/xl/workbook.xml", f"{DOC}.spreadsheetml.sheet.main+xml"),
    ("/xl/worksheets/sheet1.xml", f"{DOC}.spreadsheetml.worksheet+xml"),
)
RELS_XLSX = _rels(("officeDocument", "xl/workbook.xml"))
WORKBOOK = (
    XML + f'<workbook xmlns="{MAIN}" xmlns:r="{REL}">'
    '<sheets><sheet name="Gegevens" sheetId="1" r:id="rId1"/></sheets></workbook>'
)
WORKBOOK_RELS = _rels(("worksheet", "worksheets/sheet1.xml"))
SHEET_HEAD = XML + f'<worksheet xmlns="{MAIN}"><sheetData>'
SHEET_TAIL = "</sheetData></worksheet>"
RELS_PPTX = _rels(("officeDocument", "ppt/presentation.xml"))
SLIDE = (
    XML + f'<p:sld xmlns:a="{DML}" xmlns:r="{REL}" xmlns:p="{PML}"><p:cSld><p:spTree>'
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr/><p:pic><p:nvPicPr><p:cNvPr id="2" name="Foto {n}"/><p:cNvPicPr/><p:nvPr/>'
    '</p:nvPicPr><p:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch>'
    '</p:blipFill><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="12192000" cy="6858000"/>'
    '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
    "</p:spTree></p:cSld></p:sld>"
)


def _row(r: int, rnd: random.Random) -> str:
    cells = [
        f'<c r="A{r}"><v>{r}</v></c>',
        f'<c r="B{r}" t="inlineStr"><is><t>Klant {rnd.randrange(100_000):05d}</t></is></c>',
        f'<c r="C{r}"><v>{rnd.uniform(0, 10_000):.2f}</v></c>',
        f'<c r="D{r}"><v>{rnd.randrange(1, 500)}</v></c>',
        f'<c r="E{r}"><f>C{r}*D{r}</f><v>0</v></c>',
        f'<c r="F{r}"><f>IF(E{r}&gt;1000,"groot","klein")</f><v>0</v></c>',
    ]
    return f'<row r="{r}">{"".join(cells)}</row>'


def make_xlsx(path: Path, xml_bytes: int) -> dict[str, Any]:
    """Een werkblad met samen xml_bytes aan XML (alle onderdelen)."""
    rnd = random.Random(23)
    fixed = sum(len(x.encode()) for x in (CT_XLSX, RELS_XLSX, WORKBOOK, WORKBOOK_RELS))
    budget = xml_bytes - fixed - len(SHEET_HEAD) - len(SHEET_TAIL)
    parts, size, r = [], 0, 1
    while True:
        row = _row(r, rnd)
        if size + len(row) > budget:
            break
        parts.append(row)
        size += len(row)
        r += 1
    sheet = SHEET_HEAD + "".join(parts) + " " * (budget - size) + SHEET_TAIL
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT_XLSX)
        z.writestr("_rels/.rels", RELS_XLSX)
        z.writestr("xl/workbook.xml", WORKBOOK)
        z.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return {
        "rows": r - 1,
        "xml_bytes": fixed + len(sheet.encode()),
        "file_bytes": path.stat().st_size,
    }


def _photo(n: int, width: int = 3000, height: int = 2000) -> bytes:
    """Een JPEG zoals van een telefoon: ruis met een kleurverloop, kwaliteit 92."""
    from PIL import Image

    noise = Image.effect_noise((width, height), 64 + n % 32)
    gradient = Image.linear_gradient("L").resize((width, height))
    rgb = Image.merge("RGB", (noise, gradient, Image.blend(noise, gradient, 0.5)))
    out = io.BytesIO()
    rgb.save(out, "JPEG", quality=92)
    return out.getvalue()


def make_pptx(path: Path, photo_bytes: int) -> dict[str, Any]:
    photos: list[bytes] = []
    while sum(len(p) for p in photos) < photo_bytes:
        photos.append(_photo(len(photos)))
    count = len(photos)
    types = _types(
        ("/ppt/presentation.xml", f"{DOC}.presentationml.presentation.main+xml"),
        *[
            (f"/ppt/slides/slide{n}.xml", f"{DOC}.presentationml.slide+xml")
            for n in range(1, count + 1)
        ],
        jpeg=True,
    )
    ids = "".join(f'<p:sldId id="{255 + n}" r:id="rId{n}"/>' for n in range(1, count + 1))
    presentation = (
        XML + f'<p:presentation xmlns:r="{REL}" xmlns:p="{PML}"><p:sldIdLst>{ids}</p:sldIdLst>'
        '<p:sldSz cx="12192000" cy="6858000"/></p:presentation>'
    )
    slide_rels = _rels(*[("slide", f"slides/slide{n}.xml") for n in range(1, count + 1)])
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", RELS_PPTX)
        z.writestr("ppt/presentation.xml", presentation)
        z.writestr("ppt/_rels/presentation.xml.rels", slide_rels)
        for n, data in enumerate(photos, start=1):
            z.writestr(f"ppt/slides/slide{n}.xml", SLIDE.replace("{n}", str(n)))
            z.writestr(
                f"ppt/slides/_rels/slide{n}.xml.rels", _rels(("image", f"../media/foto{n}.jpeg"))
            )
            z.writestr(f"ppt/media/foto{n}.jpeg", data)
    return {
        "photos": len(photos),
        "photo_bytes": sum(len(p) for p in photos),
        "file_bytes": path.stat().st_size,
    }


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_once(settings: Any, path: Path, ext: str, sha: str) -> dict[str, Any]:
    """Eén inspect.check() zoals de app hem doet, met de cpu van de thread en de vertraging
    van de lus."""
    from app.office import inspect

    inspect.forget()
    cpu: dict[str, float] = {}
    original = inspect._inspect

    def timed(*args: Any) -> Any:
        start = time.thread_time()
        try:
            return original(*args)
        finally:
            cpu["thread_s"] = time.thread_time() - start
            cpu["thread"] = threading.current_thread() is not threading.main_thread()

    async def go() -> dict[str, Any]:
        lags: list[float] = []
        stop = asyncio.Event()

        async def ticker() -> None:
            while not stop.is_set():
                t = time.monotonic()
                await asyncio.sleep(0.01)
                lags.append(time.monotonic() - t - 0.01)

        tick = asyncio.create_task(ticker())
        t0 = time.monotonic()
        verdict = await inspect.check(settings, path, ext, sha)
        wall = time.monotonic() - t0
        stop.set()
        await tick
        return {"verdict": verdict, "wall_s": wall, "loop_lag_max_s": max(lags, default=0.0)}

    inspect._inspect = timed
    try:
        out = asyncio.run(go())
    finally:
        inspect._inspect = original
    verdict = out.pop("verdict")
    return {
        "ok": verdict.ok,
        "reason": verdict.reason,
        "cpu_s": round(cpu.get("thread_s", 0.0), 3),
        "in_thread": cpu.get("thread", False),
        "wall_s": round(out["wall_s"], 3),
        "loop_lag_max_ms": round(out["loop_lag_max_s"] * 1000, 1),
    }


def measure(workdir: Path, runs: int = 5) -> dict[str, Any]:
    from app.config import Settings
    from app.office import inspect

    settings = Settings(_env_file=None, data_dir=workdir / "data")
    files: dict[str, dict[str, Any]] = {}
    xlsx = workdir / "groot-werkblad.xlsx"
    files["xlsx"] = {"path": xlsx, **make_xlsx(xlsx, inspect.XML_BYTES_SHEET - 4096)}
    pptx = workdir / "fotos.pptx"
    files["pptx"] = {"path": pptx, **make_pptx(pptx, PHOTO_BYTES)}
    result: dict[str, Any] = {"tool": "office_inspect", "runs": runs, "gate": GATE, "files": {}}
    for ext, info in files.items():
        path = info.pop("path")
        sha = _sha(path)
        quiet = ob.wait_quiet()
        rows = [inspect_once(settings, path, ext, sha) for _ in range(runs)]
        result["files"][ext] = {
            **info,
            "limit_xml_bytes": inspect.XML_BYTES_SHEET if ext == "xlsx" else inspect.XML_BYTES,
            "quiet": quiet,
            "cpu_s": ob.stats([r["cpu_s"] for r in rows]),
            "wall_s": ob.stats([r["wall_s"] for r in rows]),
            "loop_lag_max_ms": max(r["loop_lag_max_ms"] for r in rows),
            "all_ok": all(r["ok"] for r in rows),
            "all_in_thread": all(r["in_thread"] for r in rows),
            "details": rows,
        }
        print(f"{ext}: cpu {result['files'][ext]['cpu_s']}", file=sys.stderr)
    result["machine"] = ob.machine()
    result["ok"] = not check(result)
    return result


def check(result: dict[str, Any]) -> list[str]:
    errors = []
    for ext, f in result["files"].items():
        if not f["all_ok"]:
            errors.append(f"{ext}: de controle weigerde het bestand ({f['details'][0]['reason']})")
        if not f["all_in_thread"]:
            errors.append(f"{ext}: de controle draaide niet in een thread")
        if not report_only() and f["cpu_s"]["median"] > GATE["cpu_s"]:
            errors.append(f"{ext}: cpu mediaan {f['cpu_s']['median']} s > {GATE['cpu_s']} s")
    return errors


def run(runs: int) -> dict[str, Any]:
    workdir = ob.tmpdir("office-inspect-")
    try:
        return measure(workdir, runs)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_office_inspect() -> None:
    result = run(runs=3)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("office_inspect", result)
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="De invoercontrole op grote bestanden (§4.7)")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--out", type=Path, help="JSON ook naar dit bestand")
    args = parser.parse_args(argv)
    result = run(args.runs)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    write_result("office_inspect", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
