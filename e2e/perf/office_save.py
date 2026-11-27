"""Opslaan: van de callback van DS tot de versie in de database (fase 4, §4.7).

Doel (docs/fase-4/bouwontwerp.md §4.7): voor een document van 1 MB duurt het ≤ 2 s van de
callback tot de nieuwe inhoud (en de versie) in de database staat.

Methode
- Een eigen server (office_bench.start_office, timers van productie) tegen de echte DS uit
  E2E_REAL_DS, met clock=True: DS praat met de interne luisteraar van de app via CallbackClock,
  een TCP-doorgever die van elk verzoek de tijd van de eerste byte tot de kop van het antwoord
  vastlegt. De app antwoordt op een callback pas na de commit (saving.py: downloaden bij DS, de
  invoercontrole, place_file, commit), dus die tijd is "callback tot versie in de database".
- Het document: een docx van 1 MiB, door het script gemaakt (een alinea tekst en één foto van
  ruis als JPEG, die DS bij het opslaan ongewijzigd terugschrijft), geüpload in een map van de
  eigenaar. Daarna wachten tot het PDF-voorbeeld (O3) klaar is.
- Per ronde: de echte editor in Chromium (via /bewerken/<id>, door de proxy en de tunnel), dan
  SAVES keer: een woord typen, 2 s wachten (de wijziging naar DS), wachten tot de machine rustig
  is, en "Nu opslaan" (forcesave, status 6). Daarna nog een woord, "Sluiten", en de callback
  met status 2 die DS ±10 s later stuurt. Na elke callback: de blob van het bestand moet nieuw
  zijn en ±1 MiB groot (rechtstreeks uit de database, alleen lezen).
- Gemeten per callback: de tijd van de callback (het doel), en ter informatie de tijd van de
  klik tot de callback (dat is DS: wijzigingen samenvoegen en de docx bouwen) en de cpu van de
  app van de klik tot het antwoord.
- Herhaling: ROUNDS rondes van elk SAVES keer status 6 en één keer status 2.

Los draaien (met een echte DS; JSON op stdout):
    cd api && E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \\
      uv run --with playwright python ../e2e/perf/office_save.py --rounds 3
Via pytest (marker perf; zonder E2E_REAL_DS overgeslagen):
    cd api && E2E_REAL_DS=… uv run --with playwright pytest ../e2e/perf/office_save.py -s
"""

from __future__ import annotations

import argparse
import io
import json
import secrets
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import office_bench as ob  # noqa: E402 - pas na het pad naar e2e/perf
from office_inspect import DML, DOC, REL, XML, _photo, _rels, _types  # noqa: E402

from conftest import Browsers, launch_browser, report_only, write_result  # noqa: E402
from office_harness import type_in_editor  # noqa: E402

pytestmark = pytest.mark.perf

GATE = {"callback_s": 2.0}
SIZE = 1024**2  # 1 MiB
ROUNDS = 3
SAVES = 3
CALLBACK_WAIT_S = 90.0

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
EXT = '<wp:extent cx="5486400" cy="3657600"/>'
PICTURE = (
    '<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
    + EXT
    + '<wp:docPr id="1" name="Foto"/><a:graphic><a:graphicData uri="'
    + PIC
    + '"><pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="foto.jpeg"/><pic:cNvPicPr/>'
    '</pic:nvPicPr><pic:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch>'
    '</pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="5486400" cy="3657600"/>'
    '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
    "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
)
DOCUMENT = (
    XML
    + f'<w:document xmlns:w="{W}" xmlns:r="{REL}" xmlns:wp="{WP}" xmlns:a="{DML}"'
    + f' xmlns:pic="{PIC}"><w:body>'
    + "<w:p><w:r><w:t>Meting opslaan: een document van 1 MiB met een foto.</w:t></w:r></w:p>"
    + PICTURE
    + "<w:p><w:r><w:t>Hieronder typt de meting.</w:t></w:r></w:p>"
    + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440"'
    + ' w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
    + "</w:body></w:document>"
)


def make_docx(size: int = SIZE) -> tuple[bytes, dict[str, Any]]:
    """Een docx van ±size bytes: tekst en één foto (ruis, JPEG), die de grootte bepaalt."""
    types = _types(("/word/document.xml", f"{DOC}.wordprocessingml.document.main+xml"), jpeg=True)
    width = 800
    while True:
        photo = _photo(0, width, width * 2 // 3)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", types)
            z.writestr("_rels/.rels", _rels(("officeDocument", "word/document.xml")))
            z.writestr("word/document.xml", DOCUMENT)
            z.writestr("word/_rels/document.xml.rels", _rels(("image", "media/foto.jpeg")))
            z.writestr("word/media/foto.jpeg", photo, compress_type=zipfile.ZIP_STORED)
        data = out.getvalue()
        if len(data) >= size:
            return data, {"bytes": len(data), "photo_bytes": len(photo), "photo_width": width}
        width = int(width * 1.08)


def _new(clock: ob.CallbackClock, seen: int, status: int, seconds: float) -> ob.Exchange | None:
    """De eerste callback met deze status na de eerste seen, zodra de app hem beantwoordde."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        got = clock.callbacks((status,))[seen:]
        if got and got[0].answered is not None:
            return got[0]
        time.sleep(0.05)
    return None


def _row(
    bench: ob.Bench,
    node: str,
    kind: str,
    clicked: float,
    cpu0: float,
    ex: ob.Exchange | None,
    before: str | None,
) -> dict[str, Any]:
    if ex is None:
        return {"kind": kind, "error": f"geen callback binnen {CALLBACK_WAIT_S:.0f} s"}
    cpu = ob.tree_cpu(bench.pid) - cpu0
    blob, size = ob.node_blob(bench.server, node)
    return {
        "kind": kind,
        "status": ex.callback_status,
        "http": ex.status,
        "callback_s": round(ex.seconds() or 0.0, 3),
        "request_body_s": round((ex.body_done or ex.started) - ex.started, 3),
        "click_to_callback_s": round(ex.started - clicked, 3),
        "app_cpu_s": round(cpu, 3),
        "new_blob": blob is not None and blob != before,
        "size": size,
    }


def _versions(bench: ob.Bench, account: Any, node: str) -> int:
    r = bench.server.call(account, "GET", f"/api/v1/files/{node}/versions", expect=200)
    return len(r.json())


def one_round(bench: ob.Bench, browsers: Browsers, owner: Any, node: str) -> dict[str, Any]:
    clock = bench.clock
    assert clock is not None
    context = browsers.context_as(owner)
    page = context.new_page()
    page.goto(f"/bewerken/{node}")
    page.locator(".editor-status").filter(has_text="Bewerken").wait_for(timeout=90_000)
    rows: list[dict[str, Any]] = []
    versions0 = _versions(bench, owner, node)
    for n in range(SAVES):
        type_in_editor(page, f"opslaan {n + 1} {secrets.token_hex(3)}")
        page.wait_for_timeout(2000)
        ob.wait_quiet()
        before, _ = ob.node_blob(bench.server, node)
        seen = len(clock.callbacks((6,)))
        cpu0, clicked = ob.tree_cpu(bench.pid), time.monotonic()
        page.get_by_role("button", name="Nu opslaan", exact=True).click()
        ex = _new(clock, seen, 6, CALLBACK_WAIT_S)
        rows.append(_row(bench, node, "nu_opslaan", clicked, cpu0, ex, before))
    type_in_editor(page, f"sluiten {secrets.token_hex(3)}")
    page.wait_for_timeout(2000)
    ob.wait_quiet()
    before, _ = ob.node_blob(bench.server, node)
    seen = len(clock.callbacks((2,)))
    cpu0, clicked = ob.tree_cpu(bench.pid), time.monotonic()
    page.get_by_role("button", name="Sluiten", exact=True).click()
    ex = _new(clock, seen, 2, CALLBACK_WAIT_S)
    rows.append(_row(bench, node, "sluiten", clicked, cpu0, ex, before))
    problems = browsers.close(context)
    return {
        "saves": rows,
        "versions_made": _versions(bench, owner, node) - versions0,
        "guard": problems,
    }


def measure(url: str, secret: str, rounds: int = ROUNDS) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    data, made = make_docx()
    result: dict[str, Any] = {
        "tool": "office_save",
        "rounds": rounds,
        "saves_per_round": SAVES,
        "gate": GATE,
        "document": made,
        "round_results": [],
    }
    base = ob.tmpdir("office-save-")
    bench = ob.start_office(base, url, secret, clock=True)
    try:
        squad = ob.team(bench, {"groot.docx": data})
        node = squad.nodes["groot.docx"]
        ob.wait_background(bench.server)
        with sync_playwright() as pw:
            browser = launch_browser(pw)
            browsers = Browsers(browser, bench.server, office_frames="record")
            try:
                for n in range(rounds):
                    r = one_round(bench, browsers, squad.owner, node)
                    result["round_results"].append(r)
                    times = [s.get("callback_s") for s in r["saves"]]
                    print(f"ronde {n + 1}: callback {times} s", file=sys.stderr)
                    # Tot de sessie dicht is (status 2 deed dat), dan de volgende ronde
                    end = time.monotonic() + 60
                    while time.monotonic() < end:
                        r_open = bench.server.call(
                            squad.owner, "GET", f"/api/v1/office/files/{node}", expect=200
                        ).json()["open"]
                        if r_open is None:
                            break
                        time.sleep(0.5)
                    ob.wait_background(bench.server)
            finally:
                browsers.close_all()
                result["machine"] = ob.machine(url, browser.version)
                browser.close()
    finally:
        bench.stop()
        shutil.rmtree(base, ignore_errors=True)
    rows = [s for r in result["round_results"] for s in r["saves"] if "callback_s" in s]
    for kind in ("nu_opslaan", "sluiten"):
        mine = [s for s in rows if s["kind"] == kind]
        result[kind] = {
            "callback_s": ob.stats([s["callback_s"] for s in mine]),
            "click_to_callback_s": ob.stats([s["click_to_callback_s"] for s in mine]),
            "app_cpu_s": ob.stats([s["app_cpu_s"] for s in mine]),
            "size": ob.stats([float(s["size"] or 0) for s in mine], 0),
        }
    result["all"] = {"callback_s": ob.stats([s["callback_s"] for s in rows])}
    result["ok"] = not check(result)
    return result


def check(result: dict[str, Any]) -> list[str]:
    errors = []
    for n, r in enumerate(result["round_results"], start=1):
        errors += [f"ronde {n}: bewaking: {p}" for p in r["guard"]]
        for s in r["saves"]:
            if "error" in s:
                errors.append(f"ronde {n}, {s['kind']}: {s['error']}")
                continue
            if s["http"] != 200:
                errors.append(f"ronde {n}, {s['kind']}: de app antwoordde {s['http']}")
            if not s["new_blob"]:
                errors.append(f"ronde {n}, {s['kind']}: geen nieuwe inhoud in de database")
            if (s["size"] or 0) < 0.9 * SIZE:
                errors.append(f"ronde {n}, {s['kind']}: opgeslagen maar {s['size']} bytes")
    worst = result["all"]["callback_s"]["max"]
    if not report_only() and worst is not None and worst > GATE["callback_s"]:
        errors.append(f"callback tot versie: tot {worst} s > {GATE['callback_s']} s")
    return errors


@pytest.mark.skipif(ob.real_ds() is None, reason="geen echte DS (E2E_REAL_DS)")
def test_office_save() -> None:
    url, secret = ob.need_real_ds()
    result = measure(url, secret, rounds=1)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("office_save", result)
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opslaan: callback tot versie (§4.7)")
    parser.add_argument("--rounds", type=int, default=ROUNDS)
    parser.add_argument("--out", type=Path, help="JSON ook naar dit bestand")
    args = parser.parse_args(argv)
    url, secret = ob.need_real_ds()
    result = measure(url, secret, args.rounds)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    write_result("office_save", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
