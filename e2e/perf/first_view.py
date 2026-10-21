"""Eerste weergave < 1,5 s op het LAN (ontwerp §4.2; P0, frozen na P0).

Methode
- Profiel via CDP: latency 2 ms, 100 Mbit/s op en neer, CPU 1×.
- Koud: elke run een nieuwe browsercontext (geen service worker, geen HTTP-cache). Warm:
  daarna in dezelfde context nog eens, met de service worker actief als de build er een heeft.
- Gemeten: navigationStart (performance.timeOrigin) → performance.mark("app:ready:<wat>").
- Pagina's en hun meetpunt: / uitgelogd ("login", P1), /map/<10k> ingelogd ("folder", P2),
  /s/<link> ("public", P4).
- Poort: mediaan van 5 < 1500 ms en p95 < 2000 ms, koud en warm.
- Geen registratie of ophalen van sw.js vóór app:ready.

Zolang een pagina zijn eigen meetpunt nog niet zet, meet het script een terugval: het
meetpunt "app:ready:route" van de schil (P0), of voor het inlogformulier het moment dat het
in de DOM staat. De pagina krijgt dan status "not yet" en de poort is alleen informatief.
Zet de pagina zijn meetpunt wel, dan is de poort hard (pass/fail), behalve met
E2E_PERF_REPORT_ONLY=1.

Los draaien (start een eigen server en vult hem; JSON op stdout):
    cd api && uv run --with playwright python ../e2e/perf/first_view.py --runs 5
Via pytest (gemarkeerd perf):
    cd api && uv run --with playwright pytest ../e2e/perf/first_view.py -s
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import (  # noqa: E402 - pas na het pad naar e2e/
    Browsers,
    PerfData,
    Server,
    cdp_profile,
    median,
    percentile,
    prepare_perf,
    report_only,
    standalone,
    write_result,
)

pytestmark = pytest.mark.perf

PROFILE = {"latency_ms": 2, "down_mbit": 100, "up_mbit": 100, "cpu": 1}
GATE = {"median_ms": 1500, "p95_ms": 2000}
TIMEOUT_MS = 30_000
# Zo lang na de terugval nog wachten op het eigen meetpunt, alleen in de eerste run: komt het
# dan niet, dan zet deze build het (nog) niet
GRACE_MS = 3_000
# Zo lang na load wachten of de app een service worker registreert (na load + idle)
SW_WAIT_MS = 3_500


@dataclass(frozen=True)
class PageSpec:
    name: str
    mark: str  # het eigen meetpunt: app:ready:<mark>
    owner: str  # het pakket dat het zet
    logged_in: bool
    fallback_mark: str | None = None
    fallback_dom: str | None = None


PAGES = {
    "login": PageSpec("login", "login", "P1", False, fallback_dom="form input[type=email]"),
    "folder": PageSpec("folder", "folder", "P2", True, fallback_mark="route"),
    "public": PageSpec("public", "public", "P4", False, fallback_mark="route"),
}

PROBE_JS = r"""
((selector) => {
  const state = { dom: null, swRegister: null };
  window.__e2eFirstView = state;
  if (selector) {
    const obs = new MutationObserver(() => {
      if (state.dom === null && document.querySelector(selector)) {
        state.dom = performance.now();
        obs.disconnect();
      }
    });
    obs.observe(document, { childList: true, subtree: true });
  }
  const sw = navigator.serviceWorker;
  if (sw && sw.register) {
    const original = sw.register.bind(sw);
    sw.register = (...args) => {
      if (state.swRegister === null) state.swRegister = performance.now();
      return original(...args);
    };
  }
})(SELECTOR);
"""

READY_JS = r"""
([names, dom]) => {
  for (const n of names) if (performance.getEntriesByName(n, "mark").length) return true;
  return !!dom && window.__e2eFirstView && window.__e2eFirstView.dom !== null;
}
"""

READ_JS = r"""
() => {
  const marks = {};
  for (const m of performance.getEntriesByType("mark")) {
    if (m.name.startsWith("app:ready:") && !(m.name in marks)) marks[m.name] = m.startTime;
  }
  const nav = performance.getEntriesByType("navigation")[0];
  const swFetch = performance.getEntriesByType("resource")
    .filter((r) => new URL(r.name).pathname === "/sw.js")
    .map((r) => r.startTime);
  const probe = window.__e2eFirstView || {};
  return {
    marks,
    dom: probe.dom ?? null,
    swRegister: probe.swRegister ?? null,
    swFetch,
    ttfb: nav ? nav.responseStart : null,
    dcl: nav ? nav.domContentLoadedEventEnd : null,
    controlled: !!(navigator.serviceWorker && navigator.serviceWorker.controller),
  };
}
"""

# Wachten tot de pagina klaar is met werken (bijv. de rest van een grote lijst renderen), zodat
# de warme run niet met de vorige pagina om de CPU strijdt
IDLE_JS = r"""
() => new Promise((resolve) => {
  if (!window.requestIdleCallback) return setTimeout(resolve, 500);
  requestIdleCallback(() => resolve(), { timeout: 10000 });
})
"""

SW_READY_JS = r"""
async (ms) => {
  const sw = navigator.serviceWorker;
  if (!sw) return false;
  const deadline = performance.now() + ms;
  const pause = (t) => new Promise((r) => setTimeout(r, t));
  while (performance.now() < deadline) {
    const reg = await sw.getRegistration();
    if (reg) {
      const left = Math.max(0, deadline - performance.now());
      await Promise.race([sw.ready, pause(left)]);
      return !!(reg.active);
    }
    await pause(100);
  }
  return false;
}
"""


class Inflight:
    """Lopende verzoeken van een pagina, zonder de live stroom (EventSource blijft open)."""

    def __init__(self, page: Any):
        self.pending: set[Any] = set()
        page.on("request", self._start)
        page.on("requestfinished", self._end)
        page.on("requestfailed", self._end)

    def _start(self, request: Any) -> None:
        if request.resource_type != "eventsource":
            self.pending.add(request)

    def _end(self, request: Any) -> None:
        self.pending.discard(request)

    def wait_quiet(self, page: Any, quiet_ms: int = 500, timeout_ms: int = 15_000) -> bool:
        """Wacht tot er quiet_ms lang niets loopt (dan is ook de server klaar met de vorige
        pagina en meet de warme run niet diens werk mee)."""
        quiet = 0
        for _ in range(timeout_ms // 100):
            quiet = quiet + 100 if not self.pending else 0
            if quiet >= quiet_ms:
                return True
            page.wait_for_timeout(100)
        return False


def _probe(spec: PageSpec) -> str:
    return PROBE_JS.replace("SELECTOR", json.dumps(spec.fallback_dom))


def measure_once(page: Any, url: str, spec: PageSpec, target_wait_ms: int) -> dict[str, Any]:
    """Eén navigatie: wacht op het eigen meetpunt of de terugval en geeft de tijd vanaf
    navigationStart, met waar die vandaan kwam."""
    target = f"app:ready:{spec.mark}"
    names = [target] + ([f"app:ready:{spec.fallback_mark}"] if spec.fallback_mark else [])
    page.goto(url, wait_until="commit")
    try:
        page.wait_for_function(READY_JS, arg=[names, bool(spec.fallback_dom)], timeout=TIMEOUT_MS)
    except Exception:  # noqa: BLE001 - de pagina werd nooit klaar: geen meting
        return {"ms": None, "source": "timeout", "sw_before_ready": False, "controlled": False}
    if target_wait_ms > 0:
        try:
            page.wait_for_function(READY_JS, arg=[[target], False], timeout=target_wait_ms)
        except Exception:  # noqa: BLE001 - geen eigen meetpunt: dan de terugval
            pass
    data = page.evaluate(READ_JS)
    marks = data["marks"]
    if target in marks:
        value, source = marks[target], f"mark:{spec.mark}"
    elif spec.fallback_mark and f"app:ready:{spec.fallback_mark}" in marks:
        value, source = marks[f"app:ready:{spec.fallback_mark}"], f"mark:{spec.fallback_mark}"
    else:
        value, source = data["dom"], "dom"
    sw_times = [t for t in [data["swRegister"], *data["swFetch"]] if t is not None]
    return {
        "ms": round(value, 1),
        "source": source,
        "ttfb_ms": round(data["ttfb"], 1) if data["ttfb"] is not None else None,
        "sw_before_ready": any(t < value for t in sw_times),
        "controlled": data["controlled"],
    }


def _summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    values = [r["ms"] for r in runs]
    measured = [v for v in values if v is not None]
    med, p95 = median(measured), percentile(measured, 95)
    ok = (
        len(measured) == len(values)
        and med is not None
        and med < GATE["median_ms"]
        and p95 < GATE["p95_ms"]
    )
    return {"runs": values, "median_ms": med, "p95_ms": p95, "gate_ok": ok}


def measure_page(
    browsers: Browsers, url: str, spec: PageSpec, owner: Any, runs: int
) -> dict[str, Any]:
    cold: list[dict[str, Any]] = []
    warm: list[dict[str, Any]] = []
    problems: list[str] = []
    target_seen: bool | None = None  # nog niet bekend
    has_sw: bool | None = None
    profile: dict[str, Any] = {}
    for _ in range(runs):
        context = browsers.context_as(owner) if spec.logged_in else browsers.new_context()
        try:
            page = context.new_page()
            inflight = Inflight(page)
            page.add_init_script(_probe(spec))
            profile = cdp_profile(context, page, **PROFILE)
            wait = GRACE_MS if target_seen is None else (TIMEOUT_MS if target_seen else 0)
            run = measure_once(page, url, spec, wait)
            if target_seen is None:
                target_seen = run["source"] == f"mark:{spec.mark}"
            cold.append(run)
            # Warm: dezelfde context, met de service worker (als de app er een registreert)
            page.wait_for_load_state("load")
            page.evaluate(IDLE_JS)
            inflight.wait_quiet(page)
            if has_sw is not False:
                has_sw = bool(page.evaluate(SW_READY_JS, SW_WAIT_MS if has_sw is None else 10_000))
            # Via een lege pagina: zo telt het afbreken van de vorige pagina niet mee, zoals
            # bij iemand die later terugkomt
            page.goto("about:blank")
            wait = TIMEOUT_MS if target_seen else 0
            warm.append(measure_once(page, url, spec, wait))
        finally:
            problems.extend(browsers.close(context))
    sources = {r["source"] for r in cold + warm}
    measured = {"cold": _summary(cold), "warm": _summary(warm)}
    if "timeout" in sources:
        status = "fail"  # niet eens de terugval: de pagina werd niet klaar
    elif sources == {f"mark:{spec.mark}"}:
        ok = measured["cold"]["gate_ok"] and measured["warm"]["gate_ok"]
        status = "pass" if ok else "fail"
    elif target_seen:
        status = "fail"  # het meetpunt kwam niet in elke run
    else:
        status = "not yet"
    profile = {k: v for k, v in profile.items() if k != "session"}
    return {
        "url": url,
        "mark": f"app:ready:{spec.mark}",
        "owner": spec.owner,
        "status": status,
        "sources": sorted(sources),
        "service_worker": bool(has_sw),
        "sw_before_ready": any(r["sw_before_ready"] for r in cold + warm),
        "profile": profile,
        **measured,
        "details": {"cold": cold, "warm": warm},
        "problems": problems,
    }


def measure(
    server: Server, browsers: Browsers, data: PerfData | None, runs: int = 5, pages=None
) -> dict[str, Any]:
    wanted = list(pages or PAGES)
    urls = {"login": "/"}
    if data is not None:
        urls["folder"] = f"/map/{data.folder_id}"
        urls["public"] = data.public_url
    result: dict[str, Any] = {
        "tool": "first_view",
        "server": server.url,
        "runs": runs,
        "profile": PROFILE,
        "gate": GATE,
        "pages": {},
    }
    for name in wanted:
        spec = PAGES[name]
        owner = data.owner if data is not None else None
        result["pages"][name] = measure_page(browsers, urls[name], spec, owner, runs)
    pages_out = result["pages"].values()
    result["problems"] = [p for page in pages_out for p in page["problems"]]
    result["ok"] = (
        all(p["status"] != "fail" and not p["sw_before_ready"] for p in pages_out)
        and not result["problems"]
    )
    return result


def check(result: dict[str, Any]) -> list[str]:
    """Wat er hard mis is: geen bewaking-treffers, geen vroege sw.js, en de poort voor elke
    pagina die zijn eigen meetpunt al zet (niet bij E2E_PERF_REPORT_ONLY=1)."""
    errors = [f"bewaking: {p}" for p in result["problems"]]
    for name, page in result["pages"].items():
        if page["sw_before_ready"]:
            errors.append(f"{name}: sw.js vóór app:ready")
        if "timeout" in page["sources"]:
            errors.append(f"{name}: niet klaar binnen {TIMEOUT_MS} ms (ook geen terugval)")
        elif page["status"] == "fail" and not report_only():
            errors.append(
                f"{name}: koud mediaan {page['cold']['median_ms']} / p95 {page['cold']['p95_ms']}"
                f" ms, warm {page['warm']['median_ms']} / {page['warm']['p95_ms']} ms"
                f" (poort {GATE['median_ms']} / {GATE['p95_ms']} ms; {page['sources']})"
            )
    return errors


def test_first_view(server: Server, perf_data: PerfData, browsers: Browsers) -> None:
    result = measure(server, browsers, perf_data)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("first_view", result)
    for name, page in result["pages"].items():
        assert len(page["details"]["cold"]) == result["runs"], name
        assert len(page["details"]["warm"]) == result["runs"], name
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Eerste weergave meten (ontwerp §4.2)")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--pages", default=",".join(PAGES), help="bijv. login,folder,public")
    parser.add_argument("--n", type=int, default=10_000, help="bestanden in de grote map")
    parser.add_argument("--out", type=Path, help="JSON ook naar dit bestand")
    args = parser.parse_args(argv)
    pages = [p.strip() for p in args.pages.split(",") if p.strip()]
    unknown = set(pages) - PAGES.keys()
    if unknown:
        parser.error(f"onbekende pagina's: {', '.join(sorted(unknown))}")
    with standalone() as (server, admin, browsers):
        needs_data = any(p != "login" for p in pages)
        data = prepare_perf(server, admin, n=args.n) if needs_data else None
        result = measure(server, browsers, data, runs=args.runs, pages=pages)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    write_result("first_view", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
