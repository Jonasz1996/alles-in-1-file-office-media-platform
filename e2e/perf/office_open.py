"""Koud en warm openen van de editor via de app, en de cpu van de app daarbij (fase 4, §4.7).

Doelen (docs/fase-4/bouwontwerp.md §4.7):
- koud openen via de app ≤ 5 s (via nginx 4,1 s in de proef);
- warm openen ≤ 2 s (5 verzoeken in de proef);
- cpu van de app bij één koude opening ≤ 1 core-seconde (253 verzoeken, 8,7 MB in de proef).

Methode
- Een eigen server (office_bench.start_office) tegen de echte DS uit E2E_REAL_DS, met de timers
  van productie. Per soort (docx, xlsx, pptx) per run twee nieuwe bestanden (de fixtures van
  e2e/fixtures/office), zodat DS elk document voor het eerst ziet.
- Koud: een nieuw browserprofiel (Profiles, standaard "disk": een eigen Chromium met een
  nieuwe, lege profielmap, dus een HTTP-cache op schijf zoals een gewone browser; service
  workers toegestaan), ingelogd met het refresh-cookie, en page.goto("/bewerken/<id>"): de hele
  weg van een link naar de editor (de schil van de app, POST …/session, api.js via /office/, de
  editor in de iframe, de socket door de tunnel, het document).
- Warm: daarna in hetzelfde profiel (de cache van de koude run) het tweede bestand, ook met
  page.goto. Tussen de twee: de pagina naar about:blank en wachten tot de sessie van het eerste
  bestand dicht is (status 4), zodat die callback niet in de warme meting valt. Met
  --profile memory is het een incognitocontext zoals in de e2e (cache in het geheugen); die
  haalt grote fonts warm opnieuw op.
- Gemeten in de pagina: van navigationStart (performance.timeOrigin) tot de statusregel van de
  editorpagina "Bewerken" zegt (phase ready: onDocumentReady). Ook wanneer POST …/session
  klaar was, wanneer de pagina van de editor, sdk-all.js, Editor.bin en de laatste font binnen
  waren (marks_ms), en of de pagina de editor opnieuw moest laden (A22).
- De verzoeken die echt bij de app aankwamen: de toegangslog van uvicorn in de log van de server,
  van vóór goto tot het stil is (2 s geen nieuw verzoek, hooguit 15 s na klaar). De bytes:
  Resource Timing in alle frames van dezelfde origin (transferSize, koppen plus body), en apart
  wat uit de cache kwam (transferSize 0). Niet request.sizes() van Playwright: dat bleef soms
  eeuwig hangen.
- cpu: utime + stime van het serverproces (en eventuele kinderen) over dezelfde periode, min
  het eigen verbruik in rust (gemeten over 5 s vlak ervoor, maal de duur).
- Eén opening vooraf (een docx via de app en rechtstreeks) telt niet mee: de eerste na de
  start van de server laadt de code van proxy en tunnel. Hij staat wel in de JSON (warmup).
- Vóór elke run wacht het script tot de machine stil is (office_bench.wait_quiet: < 0,3 core
  over 3 s, DS meegeteld) en noteert het wat er draaide.
- Fases van één opening (phases_ms): de schil van de app (van de navigatie tot POST …/session
  begint), POST …/session zelf, en DS (van het verzoek om api.js tot klaar: de editor in de
  browser, en wat hij via de proxy en de tunnel haalt). Met de verzoeken per soort (paths) en de
  meldingen in de console van de editor (office_console; zo is een herhaling van A22 te zien).
- De controle (--direct, standaard aan): in dezelfde run ook koud en warm rechtstreeks bij DS,
  zonder de app: een gastpagina op een eigen kleine HTTP-server (127.0.0.1, een andere origin
  dan DS, zoals een gewone integratie van OnlyOffice) laadt api.js van DS en opent hetzelfde
  bestand uit de DocStub van het harnas, met een config zoals editor_config() van het harnas.
  Niet via page.route: een route zet de HTTP-cache van de browser uit, en dan is warm niet warm.
  Het verschil met de opening via de app is de prijs van de app: de schil, POST …/session, de
  proxy en de tunnel. De controle telt niet mee voor de doelen.

Los draaien (met een echte DS; JSON op stdout):
    cd api && E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \\
      uv run --with playwright python ../e2e/perf/office_open.py --runs 5 [--no-direct]
Via pytest (marker perf; zonder E2E_REAL_DS overgeslagen):
    cd api && E2E_REAL_DS=… uv run --with playwright pytest ../e2e/perf/office_open.py -s
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import office_bench as ob  # noqa: E402 - pas na het pad naar e2e/perf

from conftest import (  # noqa: E402
    Browsers,
    Guard,
    chromium_path,
    launch_browser,
    report_only,
    write_result,
)
from office_harness import editor_config, fixture, new_key  # noqa: E402

pytestmark = pytest.mark.perf

GATE = {"cold_s": 5.0, "warm_s": 2.0, "cpu_core_s": 1.0}
KINDS = ("docx", "xlsx", "pptx")
TIMEOUT_MS = 90_000
SETTLE_S = 2.0  # zo lang geen nieuw verzoek bij de app: de opening is voorbij
SETTLE_MAX_S = 15.0

# In de pagina van de app (niet in de iframes): wanneer de statusregel "Bewerken" zegt, en hoe
# vaak de editor opnieuw gemaakt werd (A22). Resource Timing krijgt in elke frame genoeg ruimte
# (de standaard is 250, de editor doet er meer).
PROBE_JS = r"""
(() => {
  try { performance.setResourceTimingBufferSize(20000); } catch (e) {}
  if (window.top !== window) return;
  const out = { ready: null, statuses: [], editors: 0 };
  window.__officeOpen = out;
  const look = () => {
    const el = document.querySelector(".editor-status");
    const text = el ? (el.textContent || "") : "";
    if (text && out.statuses[out.statuses.length - 1] !== text) {
      out.statuses.push(text);
    }
    if (out.ready === null && text.startsWith("Bewerken")) out.ready = performance.now();
    const bad = ".editor-notice-not_ready, .editor-notice-error, .editor-notice-failed";
    if (document.querySelector(bad)) {
      out.failed = (document.querySelector(".editor-notices") || {}).textContent || "melding";
    }
  };
  const start = () => {
    new MutationObserver((records) => {
      for (const r of records) {
        for (const n of r.addedNodes) {
          if (n.nodeName === "IFRAME" || (n.querySelector && n.querySelector("iframe"))) {
            out.editors += 1;
          }
        }
      }
      look();
    }).observe(document.documentElement,
      { subtree: true, childList: true, characterData: true });
    look();
  };
  if (document.documentElement) start(); else document.addEventListener("DOMContentLoaded", start);
})();
"""

READ_JS = r"""
() => {
  const pick = (e) => ({ name: e.name, type: e.initiatorType, start: e.startTime,
    end: e.responseEnd, transfer: e.transferSize, encoded: e.encodedBodySize });
  const res = performance.getEntriesByType("resource").map(pick);
  const path = (e) => new URL(e.name).pathname;
  const session = res.find((e) => /\/api\/v1\/office\/files\/[^/]+\/session$/.test(path(e)));
  const api = res.find((e) => path(e).endsWith("/api/documents/api.js"));
  const nav = performance.getEntriesByType("navigation")[0];
  // Wat de schil van de app deed vóór POST …/session (of api.js)
  const limit = session ? session.start : (api ? api.start : 0);
  const before = res.filter((e) => e.start < limit).map((e) => [path(e).slice(-60),
    Math.round(e.start), Math.round(e.end), e.transfer, e.type]);
  return { probe: window.__officeOpen || null, direct: window.__direct || null,
    session: session || null, api: api || null, before: before.slice(0, 40),
    nav: nav ? { response_end: nav.responseEnd, dcl: nav.domContentLoadedEventEnd,
      transfer: nav.transferSize } : null };
}
"""

# In elke frame apart (via Playwright, dus ook in een frame van een andere origin): zijn tijd
# van oorsprong en zijn Resource Timing. De navigatie van een iframe staat als resource
# (iframe) in de ouder; die van de pagina zelf telt apart (nav in READ_JS).
FRAME_JS = r"""
() => ({
  origin: performance.timeOrigin,
  entries: performance.getEntriesByType("resource").map((e) => [new URL(e.name).pathname,
    e.initiatorType, e.responseEnd, e.transferSize, e.encodedBodySize, e.decodedBodySize]),
})
"""

# Wanneer (ms sinds de navigatie van de pagina) het laatste stuk van elk soort binnen was
MARKS = {
    "editor_page": re.compile(r"/main/index\.html$"),
    "sdk": re.compile(r"/sdk-all\.js$"),
    "document": re.compile(r"/Editor\.bin$"),
    "fonts": re.compile(r"/fonts/\d+$"),
}


def frames(page: Any) -> dict[str, Any]:
    """Resource Timing van alle frames samen: wat over het net kwam (transferSize, koppen plus
    body), wat uit de cache kwam (transferSize 0), de grootste stukken en de merktekens."""
    out: dict[str, Any] = {
        "frames": 0,
        "entries": 0,
        "transfer": 0,
        "cached": 0,
        "cached_bytes": 0,
        "big": [],
        "marks": {},
    }
    top = page.main_frame.evaluate("() => performance.timeOrigin")
    for frame in page.frames:
        try:
            data = frame.evaluate(FRAME_JS)
        except Exception:  # noqa: BLE001 - een frame die net weg is
            continue
        out["frames"] += 1
        shift = data["origin"] - top
        for path, kind, end, transfer, encoded, decoded in data["entries"]:
            out["entries"] += 1
            out["transfer"] += transfer
            for name, pattern in MARKS.items():
                if pattern.search(path):
                    out["marks"][name] = max(out["marks"].get(name, 0), end + shift)
            if transfer > 100_000:
                out["big"].append([_short(path), transfer, encoded, decoded, kind])
            if transfer == 0 and encoded > 0:
                out["cached"] += 1
                out["cached_bytes"] += encoded
    return out


# Het versiesegment van DS (10.0.0-<hash>) verschilt per container; in de uitslag staat <V>
VERSION = re.compile(r"/\d+\.\d+\.\d+-[0-9a-f]{32}/")


def _short(path: str) -> str:
    return VERSION.sub("/<V>/", path)[-70:]


def _kind_of(path: str) -> str:
    """Een soort voor een pad in de toegangslog: office, internal/<wat>, api/<gebied>, assets
    of het pad (zonder keys of id's)."""
    if path.startswith("/office/"):
        return "office"
    if path.startswith("/internal/office/"):
        return "internal/" + path.split("?", 1)[0].split("/")[3]
    parts = path.split("?", 1)[0].strip("/").split("/")
    if parts[:2] == ["api", "v1"] and len(parts) > 2:
        return f"api/{parts[2]}"
    if parts[0] in ("assets", "bewerken"):
        return parts[0]
    return "/" + "/".join(parts)


def paths(lines: list[str]) -> dict[str, int]:
    """Hoeveel verzoeken per soort, met de status als die niet 200 is."""
    found: Counter[str] = Counter()
    for line in lines:
        method, path, *_ = line.split(" ", 2)
        status = line.rsplit('" ', 1)[-1].split(" ", 1)[0]
        kind = f"{method} {_kind_of(path)}"
        found[kind if status == "200" else f"{kind} ({status})"] += 1
    return dict(sorted(found.items(), key=lambda kv: (-kv[1], kv[0])))


def phases(seen: dict[str, Any], ready: float | None) -> dict[str, int | None]:
    """De fases van één opening in ms: de schil van de app (tot POST …/session begint), POST
    …/session, en DS (van het verzoek om api.js tot klaar)."""
    session, api = seen.get("session"), seen.get("api")

    def ms(value: float | None) -> int | None:
        return None if value is None else round(value)

    return {
        "shell": ms(session["start"]) if session else None,
        "session": ms(session["end"] - session["start"]) if session else None,
        "api_js_start": ms(api["start"]) if api else None,
        "ds": ms(ready - api["start"]) if api and ready is not None else None,
    }


def _from_worker(request: Any) -> bool:
    """Een verzoek van een worker (of service worker): niet in Resource Timing van de pagina."""
    try:
        request.frame  # noqa: B018 - gooit voor een verzoek van een worker
    except Exception:  # noqa: BLE001
        return True
    return request.service_worker is not None


def _settle(bench: ob.Bench, start: int) -> int:
    """Wacht tot er SETTLE_S geen nieuw verzoek in de toegangslog komt; geeft de positie."""
    end = time.monotonic() + SETTLE_MAX_S
    last = ob.log_offset(bench.server)
    quiet_since = time.monotonic()
    while time.monotonic() < end:
        time.sleep(0.25)
        now = ob.log_offset(bench.server)
        if now != last:
            last, quiet_since = now, time.monotonic()
        elif time.monotonic() - quiet_since >= SETTLE_S:
            break
    return last


def _closed(bench: ob.Bench, account: Any, node: str, seconds: float = 60) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        r = bench.server.call(account, "GET", f"/api/v1/office/files/{node}", expect=200)
        if r.json()["open"] is None:
            return True
        time.sleep(0.5)
    return False


def idle_rate(bench: ob.Bench, seconds: float = 5.0) -> float:
    """cpu van de server per seconde in rust."""
    c0, t0 = ob.tree_cpu(bench.pid), time.monotonic()
    time.sleep(seconds)
    return (ob.tree_cpu(bench.pid) - c0) / (time.monotonic() - t0)


def _console(guard: Guard, start: tuple[int, int]) -> list[str]:
    """Wat de bewaking sinds start zag (meldingen van de editor zelf en andere), ingekort."""
    lines = guard.office_records[start[0] :] + guard.problems[start[1] :]
    return [line[:300] for line in lines[:10]]


def open_once(
    bench: ob.Bench, context: Any, guard: Guard, node: str, *, rest: float
) -> dict[str, Any]:
    """Eén opening van /bewerken/<node> in context (koud of warm, dat bepaalt de context)."""
    page = context.new_page()
    finished: list[Any] = []

    def on_finished(request: Any) -> None:
        finished.append(request)

    context.on("requestfinished", on_finished)
    sockets: list[str] = []
    page.on("websocket", lambda ws: sockets.append(ws.url))
    mark = (len(guard.office_records), len(guard.problems))
    log0 = ob.log_offset(bench.server)
    cpu0 = ob.tree_cpu(bench.pid)
    t0 = time.monotonic()
    error = None
    try:
        page.goto(f"/bewerken/{node}")
        page.wait_for_function(
            "() => window.__officeOpen && (window.__officeOpen.ready !== null"
            " || window.__officeOpen.failed)",
            timeout=TIMEOUT_MS,
        )
    except Exception as exc:  # noqa: BLE001 - de uitslag zegt wat er misging
        error = f"niet klaar binnen {TIMEOUT_MS / 1000:.0f} s: {type(exc).__name__}"
    wall_ready = time.monotonic() - t0
    log1 = _settle(bench, log0)
    elapsed = time.monotonic() - t0
    cpu = ob.tree_cpu(bench.pid) - cpu0
    seen = page.evaluate(READ_JS)
    found = frames(page)
    context.remove_listener("requestfinished", on_finished)
    every = ob.access_lines(bench.server, log0, log1)
    # Van de browser (de publieke luisteraar) en van DS (de interne, /internal/office/...)
    lines = [x for x in every if not x.split(" ", 2)[1].startswith("/internal/")]
    internal = [x for x in every if x.split(" ", 2)[1].startswith("/internal/")]
    office_lines = [x for x in lines if x.split(" ", 2)[1].startswith("/office/")]
    probe = seen["probe"] or {}
    if probe.get("failed") and not error:
        error = f"de editorpagina meldt: {probe['failed'][:200]}"
    statuses = probe.get("statuses") or []
    ready = probe.get("ready")
    nav = seen.get("nav") or {}
    result = {
        "node": node,
        "ready_ms": None if ready is None else round(ready),
        "wall_ready_s": round(wall_ready, 3),
        "phases_ms": phases(seen, ready),
        "session_ms": None if not seen["session"] else round(seen["session"]["end"]),
        "session_start_ms": None if not seen["session"] else round(seen["session"]["start"]),
        "api_js_ms": None if not seen["api"] else round(seen["api"]["end"]),
        "html_ms": None if not nav else round(nav["response_end"]),
        "before_session": seen.get("before") or [],
        "statuses": statuses,
        "editors": probe.get("editors"),
        "requests_app": len(lines),
        "requests_office": len(office_lines),
        "requests_internal": len(internal),
        "paths": paths(lines),
        "internal_paths": paths(internal),
        "not_200": sorted({x.rsplit('" ', 1)[-1] for x in lines if not x.endswith('" 200 OK')}),
        "requests_browser": len(finished),
        "requests_workers": sum(1 for r in finished if _from_worker(r)),
        "resources_pages": found["entries"],
        "bytes_network": found["transfer"] + (nav.get("transfer") or 0),
        "from_cache": found["cached"],
        "bytes_from_cache": found["cached_bytes"],
        "biggest": sorted(found["big"], key=lambda b: -b[1])[:8],
        # Laatste stuk binnen, in ms sinds de navigatie: de editorpagina, sdk-all.js, het
        # document (Editor.bin) en de fonts
        "marks_ms": {k: round(v) for k, v in found["marks"].items()},
        "sockets": len(sockets),
        "measured_s": round(elapsed, 2),
        "cpu_s": round(cpu, 3),
        "cpu_net_s": round(cpu - rest * elapsed, 3),
        "office_console": _console(guard, mark),
        "error": error,
    }
    page.goto("about:blank")
    page.close()
    return result


# --- de controle: rechtstreeks bij DS, zonder de app ---

DIRECT_JS = r"""
(() => {
  const out = { app: null, ready: null, error: null };
  window.__direct = out;
  const config = JSON.parse(document.getElementById("cfg").textContent);
  config.events = {
    onAppReady: () => { out.app = performance.now(); },
    onDocumentReady: () => { out.ready = performance.now(); },
    onError: (e) => { out.error = JSON.stringify(e.data).slice(0, 200); },
  };
  window.docEditor = new DocsAPI.DocEditor("ph", config);
})();
"""


class HostPages:
    """Gastpagina's voor de controle: een kleine HTTP-server op 127.0.0.1, een andere origin
    dan DS (zoals een gewone integratie van OnlyOffice). Elke pagina staat er één keer."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[bytes, str]] = {"/direct.js": (DIRECT_JS.encode(), "js")}
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        pages = self.pages

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                found = pages.get(self.path.split("?", 1)[0])
                body, kind = found or (b"niet gevonden", "txt")
                self.send_response(200 if found else 404)
                types = {"html": "text/html; charset=utf-8", "js": "text/javascript"}
                self.send_header("Content-Type", types.get(kind, "text/plain"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def put(self, name: str, ds: str, config: dict[str, Any]) -> str:
        text = json.dumps(config).replace("<", "\\u003c")
        html = (
            '<!doctype html><html lang="nl"><head><meta charset="utf-8">'
            "<title>Rechtstreeks bij DS</title><style>html,body{margin:0;height:100%}"
            '#ph{height:100%}</style></head><body><div id="ph"></div>'
            f'<script type="application/json" id="cfg">{text}</script>'
            f'<script src="{ds}/web-apps/apps/api/documents/api.js"></script>'
            '<script src="/direct.js"></script></body></html>'
        )
        self.pages[f"/{name}.html"] = (html.encode(), "html")
        return f"{self.base}/{name}.html"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def _closed_direct(bench: ob.Bench, key: str, seconds: float = 60) -> bool:
    """Tot DS de DocStub status 4 (of 2) stuurde voor deze key: het document is dicht."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if any(c.get("status") in (2, 4) for c in bench.office.stub.callbacks(key)):
            return True
        time.sleep(0.5)
    return False


def open_direct(
    bench: ob.Bench, hosts: HostPages, context: Any, guard: Guard, kind: str, data: bytes
) -> dict[str, Any]:
    """Eén opening rechtstreeks bij DS: dezelfde bytes, een nieuwe key, geen app ertussen."""
    stub = bench.office.stub
    key = new_key("meting")
    name = f"{key}.{kind}"
    config = editor_config(
        bench.office,
        key=key,
        url=stub.put(name, data),
        user_id="meting",
        user_name="Meting",
        file_type=kind,
        title=f"proef.{kind}",
        callback=stub.callback_url(key),
    )
    url = hosts.put(key, bench.office.ds_url, config)
    page = context.new_page()
    mark = (len(guard.office_records), len(guard.problems))
    t0 = time.monotonic()
    error = None
    try:
        page.goto(url)
        page.wait_for_function(
            "() => window.__direct && (window.__direct.ready !== null || window.__direct.error)",
            timeout=TIMEOUT_MS,
        )
    except Exception as exc:  # noqa: BLE001 - de uitslag zegt wat er misging
        error = f"niet klaar binnen {TIMEOUT_MS / 1000:.0f} s: {type(exc).__name__}"
    wall_ready = time.monotonic() - t0
    page.wait_for_timeout(SETTLE_S * 1000)
    seen = page.evaluate(READ_JS)
    found = frames(page)
    probe = seen.get("direct") or {}
    if probe.get("error") and not error:
        error = f"onError: {probe['error']}"
    ready = probe.get("ready")
    nav = seen.get("nav") or {}
    result = {
        "key": key,
        "ready_ms": None if ready is None else round(ready),
        "app_ready_ms": None if probe.get("app") is None else round(probe["app"]),
        "wall_ready_s": round(wall_ready, 3),
        "phases_ms": phases(seen, ready),
        "api_js_ms": None if not seen["api"] else round(seen["api"]["end"]),
        "resources_pages": found["entries"],
        "bytes_network": found["transfer"] + (nav.get("transfer") or 0),
        "from_cache": found["cached"],
        "bytes_from_cache": found["cached_bytes"],
        "marks_ms": {k: round(v) for k, v in found["marks"].items()},
        "office_console": _console(guard, mark),
        "error": error,
    }
    page.goto("about:blank")
    page.close()
    if not _closed_direct(bench, key):
        result["not_closed"] = True
    return result


class Profiles:
    """Een browserprofiel per run. disk (standaard): een eigen Chromium met een nieuwe map als
    profiel (launch_persistent_context), dus met een HTTP-cache op schijf zoals een gewone
    browser. memory: een context in één gedeelde Chromium (browser.new_context), zoals de e2e;
    dat is een incognitoprofiel met een cache in het geheugen."""

    def __init__(self, pw: Any, bench: ob.Bench, mode: str, base: Path) -> None:
        self.pw, self.bench, self.mode, self.base = pw, bench, mode, base
        self.browser: Any = None
        self.browsers: Browsers | None = None
        self.version: str | None = None
        if mode == "memory":
            self.browser = launch_browser(pw)
            self.version = self.browser.version
            self.browsers = Browsers(self.browser, bench.server, office_frames="record")

    def open(self, account: Any) -> tuple[Any, Guard, Any]:
        """(context, bewaking, sluiten) voor dit account, ingelogd met het refresh-cookie."""
        if self.browsers is not None:
            context = self.browsers.context_as(account)
            context.add_init_script(PROBE_JS)
            browsers = self.browsers
            return context, browsers.guard(context), lambda: browsers.close(context)
        # Onder /tmp, niet onder E2E_PERF_TMP: Chromium wil een kort pad voor zijn profiel
        folder = Path(tempfile.mkdtemp(prefix="pw-profiel-"))
        context = self.pw.chromium.launch_persistent_context(
            str(folder),
            executable_path=chromium_path(),
            args=["--no-proxy-server"],
            headless=os.environ.get("E2E_HEADED") != "1",
            base_url=self.bench.server.url,
            locale="nl-NL",
            timezone_id="Europe/Amsterdam",
            viewport={"width": 1280, "height": 800},
        )
        if self.version is None and context.browser is not None:
            self.version = context.browser.version
        context.set_default_timeout(20_000)
        guard = Guard("record")
        guard.attach(context)
        context.add_init_script(PROBE_JS)
        r = context.request.post(
            f"{self.bench.server.url}/api/v1/auth/login",
            data={"email": account.email, "password": account.password},
        )
        if not r.ok:
            raise AssertionError(f"inloggen gaf {r.status}: {r.text()}")
        for page in context.pages:
            page.close()

        def close() -> list[str]:
            with contextlib.suppress(Exception):
                context.close()
            shutil.rmtree(folder, ignore_errors=True)
            return guard.take()

        return context, guard, close

    def close(self) -> None:
        if self.browsers is not None:
            self.browsers.close_all()
        if self.browser is not None:
            self.browser.close()


def measure(
    bench: ob.Bench,
    profiles: Profiles,
    runs: int = 5,
    kinds=KINDS,
    direct: bool = True,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tool": "office_open",
        "server": bench.server.url,
        "runs": runs,
        "gate": GATE,
        "timers": bench.timers,
        "profile": profiles.mode,
        "direct": direct,
        "kinds": {},
    }
    quiet: list[dict] = []
    hosts = HostPages()
    if direct:
        hosts.start()
    try:
        result["warmup"] = warmup(bench, profiles, hosts, direct)
        for kind in kinds:
            data = fixture(f"proef.{kind}")
            files = {}
            for run in range(runs):
                files[f"koud-{run + 1}.{kind}"] = data
                files[f"warm-{run + 1}.{kind}"] = data
            squad = ob.team(bench, files, tag=kind)
            ob.wait_background(bench.server)
            cold, warm, d_cold, d_warm = [], [], [], []
            for run in range(runs):
                context, guard, close = profiles.open(squad.owner)
                try:
                    quiet.append(ob.wait_quiet())
                    rest = idle_rate(bench)
                    first = squad.nodes[f"koud-{run + 1}.{kind}"]
                    cold.append(open_once(bench, context, guard, first, rest=rest))
                    _closed(bench, squad.owner, first)
                    quiet.append(ob.wait_quiet())
                    second = squad.nodes[f"warm-{run + 1}.{kind}"]
                    warm.append(open_once(bench, context, guard, second, rest=rest))
                    _closed(bench, squad.owner, second)
                finally:
                    problems = close()
                if problems:
                    result.setdefault("guard", []).extend(problems)
                line = (
                    f"{kind} run {run + 1}: koud {cold[-1]['ready_ms']} ms,"
                    f" warm {warm[-1]['ready_ms']} ms, cpu {cold[-1]['cpu_net_s']} s"
                )
                if direct:
                    # De controle in een eigen, nieuw profiel; de meldingen van de editor horen
                    # bij de meting (DS zelf), niet bij de bewaking van de app
                    context, guard, close = profiles.open(squad.owner)
                    try:
                        quiet.append(ob.wait_quiet())
                        d_cold.append(open_direct(bench, hosts, context, guard, kind, data))
                        quiet.append(ob.wait_quiet())
                        d_warm.append(open_direct(bench, hosts, context, guard, kind, data))
                    finally:
                        close()
                    line += (
                        f"; rechtstreeks koud {d_cold[-1]['ready_ms']} ms,"
                        f" warm {d_warm[-1]['ready_ms']} ms"
                    )
                print(line, file=sys.stderr)
            result["kinds"][kind] = summarize(cold, warm)
            if direct:
                result["kinds"][kind]["direct"] = summarize_direct(d_cold, d_warm)
    finally:
        hosts.stop()
    result["quiet"] = quiet
    result["ok"] = not check(result)
    return result


def warmup(bench: ob.Bench, profiles: Profiles, hosts: HostPages, direct: bool) -> dict:
    """Eén opening vooraf (een docx via de app, en rechtstreeks), die niet meetelt: de eerste
    na de start van de server laadt de code van proxy en tunnel, en DS maakt zijn eerste
    converter warm. Staat wel in de uitslag."""
    data = fixture("proef.docx")
    squad = ob.team(bench, {"opwarmen.docx": data}, tag="opwarmen")
    ob.wait_background(bench.server)
    out: dict[str, Any] = {}
    context, guard, close = profiles.open(squad.owner)
    try:
        node = squad.nodes["opwarmen.docx"]
        out["app"] = open_once(bench, context, guard, node, rest=0.0)
        _closed(bench, squad.owner, node)
        if direct:
            out["direct"] = open_direct(bench, hosts, context, guard, "docx", data)
    finally:
        close()
    print(
        f"opwarmen: via de app {out['app']['ready_ms']} ms"
        + (f", rechtstreeks {out['direct']['ready_ms']} ms" if direct else ""),
        file=sys.stderr,
    )
    return out


def _ms(rows: list[dict], key: str) -> list[float]:
    return [r[key] for r in rows if r.get(key) is not None and not r.get("error")]


def _phase(rows: list[dict], key: str) -> dict[str, Any]:
    values = [r["phases_ms"][key] for r in rows if not r.get("error")]
    return ob.stats([v / 1000 for v in values if v is not None])


def summarize(cold: list[dict], warm: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, rows in (("cold", cold), ("warm", warm)):
        out[name] = {
            "ready_s": ob.stats([v / 1000 for v in _ms(rows, "ready_ms")]),
            "session_s": ob.stats([v / 1000 for v in _ms(rows, "session_ms")]),
            "phases_s": {k: _phase(rows, k) for k in ("shell", "session", "ds")},
            "cpu_net_s": ob.stats(_ms(rows, "cpu_net_s")),
            "cpu_s": ob.stats(_ms(rows, "cpu_s")),
            "requests_app": ob.stats(_ms(rows, "requests_app"), 0),
            "requests_office": ob.stats(_ms(rows, "requests_office"), 0),
            "bytes_network": ob.stats(_ms(rows, "bytes_network"), 0),
            "from_cache": ob.stats(_ms(rows, "from_cache"), 0),
            # A22: de pagina maakte de editor opnieuw (geen onAppReady binnen 20 s)
            "reloaded": sum(1 for r in rows if (r.get("editors") or 0) > 1),
            "errors": [r["error"] for r in rows if r.get("error")],
            "details": rows,
        }
    return out


def summarize_direct(cold: list[dict], warm: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, rows in (("cold", cold), ("warm", warm)):
        out[name] = {
            "ready_s": ob.stats([v / 1000 for v in _ms(rows, "ready_ms")]),
            "ds_s": _phase(rows, "ds"),
            "bytes_network": ob.stats(_ms(rows, "bytes_network"), 0),
            "from_cache": ob.stats(_ms(rows, "from_cache"), 0),
            "errors": [r["error"] for r in rows if r.get("error")],
            "details": rows,
        }
    return out


def check(result: dict[str, Any]) -> list[str]:
    """De doelen, op de mediaan per soort (niet bij E2E_PERF_REPORT_ONLY=1). De controle
    rechtstreeks bij DS telt niet mee."""
    errors = [f"bewaking: {p}" for p in result.get("guard", [])]
    for kind, k in result["kinds"].items():
        for name in ("cold", "warm"):
            errors += [f"{kind} {name}: {e}" for e in k[name]["errors"]]
        if report_only():
            continue
        cold, warm = k["cold"]["ready_s"]["median"], k["warm"]["ready_s"]["median"]
        cpu = k["cold"]["cpu_net_s"]["median"]
        if cold is None or cold > GATE["cold_s"]:
            errors.append(f"{kind}: koud openen mediaan {cold} s > {GATE['cold_s']} s")
        if warm is None or warm > GATE["warm_s"]:
            errors.append(f"{kind}: warm openen mediaan {warm} s > {GATE['warm_s']} s")
        if cpu is None or cpu > GATE["cpu_core_s"]:
            errors.append(f"{kind}: cpu bij koud openen mediaan {cpu} s > {GATE['cpu_core_s']} s")
    return errors


def run(runs: int, kinds=KINDS, profile: str = "disk", direct: bool = True) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    url, secret = ob.need_real_ds()
    base = ob.tmpdir("office-open-")
    bench = ob.start_office(base, url, secret, stub=direct)
    try:
        with sync_playwright() as pw:
            profiles = Profiles(pw, bench, profile, base)
            try:
                result = measure(bench, profiles, runs=runs, kinds=kinds, direct=direct)
                result["machine"] = ob.machine(url, profiles.version)
            finally:
                profiles.close()
    finally:
        bench.stop()
        shutil.rmtree(base, ignore_errors=True)
    return result


@pytest.mark.skipif(ob.real_ds() is None, reason="geen echte DS (E2E_REAL_DS)")
def test_office_open() -> None:
    result = run(runs=3)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("office_open", result)
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Koud en warm openen via de app (§4.7)")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--kinds", default=",".join(KINDS), help="bijv. docx,pptx")
    parser.add_argument(
        "--profile", choices=("disk", "memory"), default="disk", help="zie Profiles"
    )
    parser.add_argument(
        "--no-direct", action="store_true", help="zonder de controle rechtstreeks bij DS"
    )
    parser.add_argument("--out", type=Path, help="JSON ook naar dit bestand")
    args = parser.parse_args(argv)
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    unknown = set(kinds) - set(KINDS)
    if unknown:
        parser.error(f"onbekende soorten: {', '.join(sorted(unknown))}")
    result = run(args.runs, kinds, args.profile, direct=not args.no_direct)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    write_result("office_open", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
