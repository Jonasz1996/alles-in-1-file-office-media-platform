"""Conversies naar PDF (fase 4, §4.7 deel 2): klein, 401 pagina's, en 401 pagina's terwijl een
editor opent.

Doelen (docs/fase-4/bouwontwerp.md §4.7):
- conversie klein: ±1 s; van 401 pagina's: ≤ 60 s (in de proef 22 s, rechtstreeks bij DS);
- een conversie van 401 pagina's terwijl een editor opent: openen ≤ 10 s, en /healthcheck van
  DS blijft true (Keuze 25, spike stap 16).

Methode
- Een eigen server (office_bench.start_office, de timers van productie, CallbackClock) tegen de
  echte DS uit E2E_REAL_DS. Elk bestand is nieuw (een andere sha, dus een nieuwe key bij DS).
- Via de app, zoals een gebruiker: upload via de API; de app maakt het voorbeeld van B21 en zet
  dan vanzelf de taak office.pdf klaar (Keuze 25). Gemeten van het moment dat DS de bron haalt
  (GET /internal/office/src/<grant>, gezien door CallbackClock) tot pdf_state = ready in de
  database (elke 0,1 s gelezen). Daarin zit ook het vragen van de app elke seconde (oo/convert.py)
  en het ophalen en neerzetten van de PDF. Ter informatie ook van de upload tot ready.
- Rechtstreeks bij DS (de controle, zoals de proef): dezelfde soort bestand via POST /converter
  met async: true, elke 0,1 s gevraagd, tot endConvert; het bestand komt van een kleine
  HTTP-server van dit script.
- klein: proef.docx, proef.xlsx en proef.pptx uit e2e/fixtures/office (elk met een eigen
  zip-commentaar per keer, dus een nieuwe sha); 401 pagina's: office_files.docx_401 (zoals
  groot.docx van de proef: 2,6 MB XML, 38–60 KB zip). Het aantal pagina's van de PDF telt mee.
- samen: (a) de conversie eerst: upload van de 401 pagina's, wachten tot DS de bron haalt, 2 s,
  dan een nieuwe proef.docx openen in de echte editor via de app (een nieuwe browser, dus koud),
  tot de statusregel Bewerken zegt; (b) de editor eerst: openen, en meteen (terwijl de sessie
  nog pending is) de 401 pagina's uploaden. DsWatch vraagt intussen elke 0,5 s /healthcheck en
  houdt het geheugen van DS bij.
- Herhaling: klein RUNS_SMALL (5) per soort, 401 en samen elk RUNS (3) keer.

    cd api && E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \\
      uv run --with playwright python ../e2e/perf/office_watchdog.py --log wd.log -- \\
      uv run --with playwright python ../e2e/perf/office_convert.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import office_bench as ob  # noqa: E402 - pas na het pad naar e2e/perf
import office_crowd as oc  # noqa: E402
import office_files as of  # noqa: E402
import office_xml as ox  # noqa: E402

from conftest import report_only, write_result  # noqa: E402

pytestmark = pytest.mark.perf

GATE = {"small_s": 1.0, "large_s": 60.0, "open_during_s": 10.0}
# ±1 s is een richtwaarde, geen harde grens: tot 2 s heet nog "±1 s"
SMALL_OK_S = 2.0
RUNS = 3
RUNS_SMALL = 5
SMALL = ("proef.docx", "proef.xlsx", "proef.pptx")
OPEN_WAIT_S = 120.0
SRC_WAIT_S = 120.0


# --- rechtstreeks bij DS ---


class Files:
    """Een kleine HTTP-server op 0.0.0.0 die bestanden geeft aan DS (host.docker.internal)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        files = self.files

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802 - naam van http.server
                data = files.get(self.path.strip("/"))
                if data is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def add(self, data: bytes, ext: str) -> str:
        name = f"{uuid.uuid4().hex}.{ext}"
        self.files[name] = data
        return name

    def stop(self) -> None:
        self.httpd.shutdown()


def direct(url: str, secret: str, files: Files, data: bytes, ext: str) -> dict[str, Any]:
    """Omzetten rechtstreeks bij DS: van de eerste POST tot endConvert (elke 0,1 s gevraagd)."""
    import httpx

    from app.config import Settings
    from app.office.oo import tokens

    host = os.environ.get("E2E_REAL_DS_APP_HOST") or "host.docker.internal"
    settings = Settings(
        _env_file=None,
        data_dir=Path(tempfile.gettempdir()),
        office_url=url,
        office_secret=secret,
        office_app_url="http://unused:1",
        office_internal_port=1,
    )
    name = files.add(data, ext)
    key = f"meting-{uuid.uuid4().hex[:16]}"
    params = {
        "async": True,
        "filetype": ext,
        "key": key,
        "outputtype": "pdf",
        "title": f"meting.{ext}",
        "url": f"http://{host}:{files.port}/{name}",
    }
    body = tokens.command_body(settings, params)
    out: dict[str, Any] = {"ext": ext, "bytes": len(data)}
    t0 = time.monotonic()
    with httpx.Client(trust_env=False, timeout=30) as client:
        answer: dict[str, Any] = {}
        while time.monotonic() - t0 < 300:
            r = client.post(
                f"{url}/converter",
                params={"shardkey": key},
                json=body,
                headers={"Accept": "application/json"},
            )
            answer = r.json()
            if answer.get("error") or answer.get("endConvert"):
                break
            time.sleep(0.1)
        out["seconds"] = round(time.monotonic() - t0, 2)
        out["error"] = answer.get("error")
        if answer.get("endConvert") and answer.get("fileUrl"):
            u = urlsplit(answer["fileUrl"])
            pdf = client.get(f"{url}{u.path}" + (f"?{u.query}" if u.query else "")).content
            out["pdf_pages"] = of.pdf_pages(pdf)
            out["pdf_bytes"] = len(pdf)
    files.files.clear()
    return out


# --- via de app ---


def via_app(
    bench: ob.Bench, squad: ob.Team, name: str, data: bytes, ds_name: str, url: str
) -> dict[str, Any]:
    clock = bench.clock
    assert clock is not None
    seen = len(ox.src_fetches(clock))
    ob.wait_quiet()
    watch = ob.DsWatch(ds_name, url, interval=0.25, health_every=0.5).start()
    t0 = time.monotonic()
    node = ob.upload(bench.server, squad.owner, squad.folder, name, data)
    state, t_end = wait_pdf_fast(bench, node)
    ds = watch.stop()
    fetched = ox.src_fetches(clock)[seen:]
    t_src = fetched[0].started if fetched else None
    return {
        "name": name,
        "node": node,
        "bytes": len(data),
        "pdf_state": state,
        "jobs": ob.office_jobs(bench.server, node),
        "upload_to_ready_s": round(t_end - t0, 2),
        "src_to_ready_s": None if t_src is None else round(t_end - t_src, 2),
        "pdf_pages": ox.pdf_pages(bench, node) if state == "ready" else None,
        "ds": ds,
    }


def wait_pdf_fast(bench: ob.Bench, node: str, seconds: float = 600) -> tuple[str | None, float]:
    """Zoals office_xml.wait_pdf, maar elke 0,1 s: de tijd van ready telt hier."""
    end = time.monotonic() + seconds
    state = None
    while time.monotonic() < end:
        state = ob.pdf_state(bench.server, node)
        if state in ox.FINAL:
            return state, time.monotonic()
        time.sleep(0.1)
    return state, time.monotonic()


# --- samen ---


async def _open_after(bench: ob.Bench, account: Any, node: str, editor_first: bool, action: Any):
    """Een editor openen; editor_first: action() meteen na het begin van het openen, anders
    staat de conversie al te lopen (de aanroeper wachtte daarop)."""
    async with oc.Crowd(bench.server) as crowd:
        editor = oc.Editor(account, node, 1, 1, "x")
        task = asyncio.create_task(crowd.open_all([editor], stagger=0))
        if editor_first:
            # Tot de sessie er is (POST …/session gaf de config), dan de upload
            await asyncio.to_thread(_wait_session, bench, node, 30.0)
            await asyncio.to_thread(action)
        await task
        out = {"opened_s": editor.opened_s, "error": editor.error}
        if editor.opened_s is not None:
            await crowd.close(editor)
        return out


def _wait_session(bench: ob.Bench, node: str, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _has_session(bench, node):
            return True
        time.sleep(0.1)
    return False


def _has_session(bench: ob.Bench, node: str) -> bool:
    with ob._db(bench.server) as conn:
        row = conn.execute(
            "SELECT state FROM office_session WHERE node_id = ? AND state != 'closed'",
            (uuid.UUID(node).hex,),
        ).fetchone()
    return row is not None


def together(
    bench: ob.Bench, squad: ob.Team, n: int, editor_first: bool, ds_name: str, url: str
) -> dict[str, Any]:
    clock = bench.clock
    assert clock is not None
    doc = ob.upload(
        bench.server,
        squad.owner,
        squad.folder,
        f"open-{n}.docx",
        of.small("proef.docx", f"{of.run_seed()}s{n}"),
    )
    ob.wait_background(bench.server)
    big, made = of.docx_401(10_000 * of.run_seed() + 7000 + n)
    seen = len(ox.src_fetches(clock))
    ob.wait_quiet()
    watch = ob.DsWatch(ds_name, url, interval=0.25, health_every=0.5).start()
    t0 = time.monotonic()
    holder: dict[str, Any] = {}

    def upload_big() -> None:
        holder["t_upload"] = time.monotonic()
        holder["node"] = ob.upload(
            bench.server, squad.owner, squad.folder, f"groot-samen-{n}.docx", big
        )

    if not editor_first:
        upload_big()
        end = time.monotonic() + SRC_WAIT_S
        while time.monotonic() < end and len(ox.src_fetches(clock)) <= seen:
            time.sleep(0.05)
        time.sleep(2.0)
    t_open = time.monotonic()
    try:
        opened = asyncio.run(
            asyncio.wait_for(
                _open_after(bench, squad.owner, doc, editor_first, upload_big), OPEN_WAIT_S + 60
            )
        )
    except TimeoutError:
        opened = {"opened_s": None, "error": "niet klaar"}
    state, t_ready = wait_pdf_fast(bench, holder["node"])
    ox.wait_closed(bench, squad.owner, doc)
    ds = watch.stop()
    fetched = ox.src_fetches(clock)[seen:]
    t_src = fetched[0].started if fetched else None
    return {
        "order": "editor eerst" if editor_first else "conversie eerst",
        "opened_s": opened.get("opened_s"),
        "open_error": opened.get("error"),
        "open_started_after_src_s": None if t_src is None else round(t_open - t_src, 2),
        "src_after_open_s": None if t_src is None else round(t_src - t_open, 2),
        "pdf_state": state,
        "src_to_ready_s": None if t_src is None else round(t_ready - t_src, 2),
        "pdf_pages": ox.pdf_pages(bench, holder["node"]) if state == "ready" else None,
        "seconds": round(time.monotonic() - t0, 1),
        "made": made,
        "ds": ds,
    }


# --- alles ---


def measure(
    url: str, secret: str, runs: int = RUNS, runs_small: int = RUNS_SMALL, parts: str = "all"
) -> dict[str, Any]:
    ds_name = ob.ds_container(url).get("name")
    if not ds_name:
        raise SystemExit("de container van DS is niet te vinden (docker ps op de poort)")
    result: dict[str, Any] = {
        "tool": "office_convert",
        "runs": runs,
        "runs_small": runs_small,
        "gate": GATE,
        "run_seed": of.run_seed(),
        "small": {},
        "large": [],
        "together": [],
    }
    base = ob.tmpdir("office-convert-")
    files = Files()
    bench = ob.start_office(base, url, secret, clock=True)
    try:
        squad = ob.team(bench, {})
        ob.wait_background(bench.server)
        if parts in ("all", "small"):
            for name in SMALL:
                ext = name.rsplit(".", 1)[1]
                rows = []
                for n in range(runs_small):
                    app = via_app(
                        bench,
                        squad,
                        f"klein-{n}-{name}",
                        of.small(name, f"{of.run_seed()}a{n}"),
                        ds_name,
                        url,
                    )
                    ob.wait_quiet()
                    app["direct"] = direct(
                        url, secret, files, of.small(name, f"{of.run_seed()}d{n}"), ext
                    )
                    rows.append(app)
                    print(
                        f"{name}: app {app['src_to_ready_s']} s, DS {app['direct']['seconds']} s",
                        file=sys.stderr,
                    )
                    ob.wait_background(bench.server)
                result["small"][name] = rows
        if parts in ("all", "large"):
            for n in range(runs):
                data, made = of.docx_401(10_000 * of.run_seed() + 5000 + n)
                app = via_app(bench, squad, f"groot-{n}.docx", data, ds_name, url)
                ob.wait_background(bench.server)
                ob.wait_quiet()
                app["direct"] = direct(
                    url, secret, files, of.docx_401(10_000 * of.run_seed() + 6000 + n)[0], "docx"
                )
                app["made"] = made
                result["large"].append(app)
                print(
                    f"401: app {app['src_to_ready_s']} s ({app['pdf_pages']} p.), DS"
                    f" {app['direct']['seconds']} s ({app['direct'].get('pdf_pages')} p.)",
                    file=sys.stderr,
                )
        if parts in ("all", "together"):
            for n in range(runs):
                for editor_first in (False, True):
                    row = together(bench, squad, 2 * n + editor_first, editor_first, ds_name, url)
                    result["together"].append(row)
                    print(
                        f"samen ({row['order']}): openen {row['opened_s']} s, conversie"
                        f" {row['src_to_ready_s']} s, health niet true"
                        f" {row['ds'].get('health', {}).get('not_true')}",
                        file=sys.stderr,
                    )
                    ob.wait_background(bench.server)
        result["machine"] = ob.machine(url)
    finally:
        bench.stop()
        files.stop()
        shutil.rmtree(base, ignore_errors=True)
    result["summary"] = summarize(result)
    result["ok"] = not check(result)
    return result


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"small": {}}
    for name, rows in result["small"].items():
        out["small"][name] = {
            "app_s": ob.stats([r["src_to_ready_s"] for r in rows if r["src_to_ready_s"]], 2),
            "upload_to_ready_s": ob.stats([r["upload_to_ready_s"] for r in rows], 2),
            "direct_s": ob.stats([r["direct"]["seconds"] for r in rows], 2),
            "ready": sum(1 for r in rows if r["pdf_state"] == "ready"),
            "n": len(rows),
        }
    large = result["large"]
    if large:
        out["large"] = {
            "app_s": ob.stats([r["src_to_ready_s"] for r in large if r["src_to_ready_s"]], 1),
            "direct_s": ob.stats([r["direct"]["seconds"] for r in large], 1),
            "pages": [r["pdf_pages"] for r in large],
            "direct_pages": [r["direct"].get("pdf_pages") for r in large],
            "peak_mb": ob.stats([r["ds"].get("kernel_peak_mb") or 0 for r in large], 0),
            "x2t_mb": ob.stats([r["ds"].get("x2t_mb_max") or 0 for r in large], 0),
            "ready": sum(1 for r in large if r["pdf_state"] == "ready"),
            "n": len(large),
            "xml_bytes": large[0]["made"]["xml_bytes"],
            "file_bytes": large[0]["made"]["file_bytes"],
        }
    for order in ("conversie eerst", "editor eerst"):
        rows = [r for r in result["together"] if r["order"] == order]
        if not rows:
            continue
        out[order] = {
            "opened_s": ob.stats([r["opened_s"] for r in rows if r["opened_s"]], 1),
            "opened": sum(1 for r in rows if r["opened_s"] is not None),
            "n": len(rows),
            "conversion_s": ob.stats([r["src_to_ready_s"] for r in rows if r["src_to_ready_s"]], 1),
            "health_not_true": sum((r["ds"].get("health") or {}).get("not_true", 0) for r in rows),
            "health_n": sum((r["ds"].get("health") or {}).get("n", 0) for r in rows),
            "health_ms_max": max((r["ds"].get("health") or {}).get("ms_max", 0) for r in rows),
            "src_after_open_s": [r["src_after_open_s"] for r in rows],
            "ready": sum(1 for r in rows if r["pdf_state"] == "ready"),
        }
    return out


def check(result: dict[str, Any]) -> list[str]:
    s = result.get("summary") or {}
    errors = []
    gate = not report_only()
    for name, k in s.get("small", {}).items():
        if k["ready"] != k["n"]:
            errors.append(f"klein {name}: {k['ready']} van {k['n']} omgezet")
        if gate and (k["app_s"]["median"] or 99) > SMALL_OK_S:
            errors.append(f"klein {name}: mediaan {k['app_s']['median']} s > {SMALL_OK_S} s")
    lg = s.get("large")
    if lg:
        if lg["ready"] != lg["n"]:
            errors.append(f"401 pagina's: {lg['ready']} van {lg['n']} omgezet")
        if gate and (lg["app_s"]["max"] or 999) > GATE["large_s"]:
            errors.append(f"401 pagina's: tot {lg['app_s']['max']} s > {GATE['large_s']} s")
    for order in ("conversie eerst", "editor eerst"):
        k = s.get(order)
        if not k:
            continue
        if k["opened"] != k["n"]:
            errors.append(f"samen ({order}): {k['opened']} van {k['n']} geopend")
        if gate and (k["opened_s"]["max"] or 999) > GATE["open_during_s"]:
            errors.append(
                f"samen ({order}): openen tot {k['opened_s']['max']} s > {GATE['open_during_s']} s"
            )
        if k["health_not_true"]:
            errors.append(f"samen ({order}): /healthcheck {k['health_not_true']} keer niet true")
    return errors


@pytest.mark.skipif(ob.real_ds() is None, reason="geen echte DS (E2E_REAL_DS)")
def test_office_convert() -> None:
    url, secret = ob.need_real_ds()
    result = measure(url, secret, runs=1, runs_small=1)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("office_convert", result)
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conversies klein, 401 pagina's en samen (§4.7)")
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--runs-small", type=int, default=RUNS_SMALL)
    parser.add_argument("--parts", default="all", choices=("all", "small", "large", "together"))
    parser.add_argument("--out", type=Path, help="JSON ook naar dit bestand")
    args = parser.parse_args(argv)
    url, secret = ob.need_real_ds()
    result = measure(url, secret, args.runs, args.runs_small, args.parts)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    write_result("office_convert", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
