"""Lighthouse-achtige prestatiescore (ontwerp §4.3; P1).

Zonder Lighthouse zelf: Python Playwright plus CDP tegen de echte server, met dezelfde
metingen, curves en gewichten als Lighthouse (navigatie, alleen Performance).

Metingen, per run in een nieuwe context (geen cache, geen service worker):
- FCP uit de paint-entries;
- LCP uit een gebufferde largest-contentful-paint-observer (de laatste kandidaat);
- CLS uit layout-shift-sessievensters (1 s tussenruimte, hooguit 5 s; zonder hadRecentInput);
- TBT als Σ(duur − 50 ms) over long tasks tussen FCP en TTI. TTI zoals Lighthouse: het begin
  van het eerste CPU-stille venster van 5 s (geen long task) dat overlapt met een netwerkstil
  venster van 5 s (≤ 2 lopende GET-verzoeken), en nooit vóór FCP;
- Speed Index uit de screenshots van een Chromium-trace (dezelfde bron als Lighthouse), met
  de visuele voortgang als histogramafstand tot het laatste beeld zonder (bijna-)witte pixels,
  zoals speedline; Pillow komt uit de API-omgeving. Nooit lager dan FCP.

Score: per metriek de log-normale curve van Lighthouse (p10 en mediaan), afgerond op twee
decimalen, gewogen tot een categoriescore 0–100 (geheel getal). Van 5 runs telt de mediaan.

| Metriek     | Gewicht | Mobiel p10 / mediaan | Desktop p10 / mediaan |
| FCP         | 0,10    | 1800 / 3000 ms       | 934 / 1600 ms         |
| Speed Index | 0,10    | 3387 / 5800 ms       | 1311 / 2300 ms        |
| LCP         | 0,25    | 2500 / 4000 ms       | 1200 / 2400 ms        |
| TBT         | 0,30    | 200 / 600 ms         | 150 / 350 ms          |
| CLS         | 0,25    | 0,10 / 0,25          | 0,10 / 0,25           |

Vertraging via CDP, met de getallen zoals Lighthouse ze aan DevTools geeft (kbit = 1024 bit):
- mobiel: 562,5 ms, 1474,56 kbit/s neer, 675 kbit/s op, CPU 4×, 412×823 bij DPR 1,75;
- desktop: 40 ms, 10 Mbit/s (10240 kbit/s), CPU 1×, 1350×940.

Poort: score ≥ 91 (mediaan van 5). Mobiel op / (inloggen) en /s/<link> (de publieke pagina)
is de poort van P1. Desktop op /map/<10k> is die van P2: zolang de map zijn meetpunt
app:ready:folder niet zet, is die pagina "not yet" en alleen informatief. Met
E2E_PERF_REPORT_ONLY=1 faalt geen poort (CI).

Los draaien (start een eigen server en vult hem; JSON op stdout):
    cd api && uv run --with playwright python ../e2e/perf/lighthouse_like.py --runs 5
Via pytest (gemarkeerd perf; het pad expliciet, pytest.ini verzamelt dit bestand niet):
    cd api && uv run --with playwright pytest ../e2e/perf/lighthouse_like.py -s
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import (  # noqa: E402 - pas na het pad naar e2e/
    Account,
    Browsers,
    PerfData,
    Server,
    cdp_profile,
    median,
    prepare_perf,
    report_only,
    standalone,
    write_result,
)

pytestmark = pytest.mark.perf

GATE = 91
KBIT = 1024  # Lighthouse rekent kbit/s met 1024 bit
PROFILES: dict[str, dict[str, Any]] = {
    "mobile": {
        "latency_ms": 562.5,
        "down_mbit": 1474.56 * KBIT / 1_000_000,
        "up_mbit": 675 * KBIT / 1_000_000,
        "cpu": 4,
        "viewport": {"width": 412, "height": 823},
        "device_scale_factor": 1.75,
        "is_mobile": True,
        "has_touch": True,
    },
    "desktop": {
        "latency_ms": 40,
        "down_mbit": 10_240 * KBIT / 1_000_000,
        "up_mbit": 10_240 * KBIT / 1_000_000,
        "cpu": 1,
        "viewport": {"width": 1350, "height": 940},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
    },
}
WEIGHTS = {"fcp": 0.10, "si": 0.10, "lcp": 0.25, "tbt": 0.30, "cls": 0.25}
CURVES = {
    "mobile": {
        "fcp": (1800, 3000),
        "si": (3387, 5800),
        "lcp": (2500, 4000),
        "tbt": (200, 600),
        "cls": (0.10, 0.25),
    },
    "desktop": {
        "fcp": (934, 1600),
        "si": (1311, 2300),
        "lcp": (1200, 2400),
        "tbt": (150, 350),
        "cls": (0.10, 0.25),
    },
}
QUIET_WINDOW_MS = 5000  # Lighthouse: REQUIRED_QUIET_WINDOW
ALLOWED_REQUESTS = 2  # Lighthouse: netwerkstil bij ≤ 2 lopende verzoeken
LONG_TASK_MS = 50
TIMEOUT_MS = 60_000  # zo lang na de navigatie hooguit wachten op TTI
POLL_MS = 500
WHITE = 249  # speedline: een pixel met r, g en b ≥ 249 telt niet mee
TRACE_CATEGORIES = [
    "-*",
    "devtools.timeline",
    "blink.user_timing",
    "disabled-by-default-devtools.screenshot",
]


@dataclass(frozen=True)
class PageSpec:
    name: str
    profile: str
    owner: str  # het pakket waarvan dit de poort is
    logged_in: bool
    mark: str | None = None  # gezet: pas een harde poort als de pagina dit meetpunt zet


PAGES = {
    "login": PageSpec("login", "mobile", "P1", False),
    "public": PageSpec("public", "mobile", "P1", False),
    "folder": PageSpec("folder", "desktop", "P2", True, mark="folder"),
}

OBSERVE_JS = r"""
(() => {
  const s = { fcp: null, lcp: null, lcpWhat: null, shifts: [], long: [] };
  window.__e2eLighthouse = s;
  try { performance.setResourceTimingBufferSize(5000); } catch (e) {}
  const watch = (type, each) => {
    try {
      new PerformanceObserver((list) => list.getEntries().forEach(each))
        .observe({ type, buffered: true });
    } catch (e) {}
  };
  watch("paint", (e) => { if (e.name === "first-contentful-paint") s.fcp = e.startTime; });
  watch("largest-contentful-paint", (e) => {
    s.lcp = e.startTime;
    const el = e.element;
    s.lcpWhat = el ? `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}` +
      (typeof el.className === "string" && el.className ? "." + el.className.split(" ")[0] : "")
      : null;
  });
  watch("layout-shift", (e) => { if (!e.hadRecentInput) s.shifts.push([e.startTime, e.value]); });
  watch("longtask", (e) => s.long.push([e.startTime, e.startTime + e.duration]));
})();
"""

READ_JS = r"""
() => {
  const s = window.__e2eLighthouse || { shifts: [], long: [] };
  const nav = performance.getEntriesByType("navigation")[0];
  const res = performance.getEntriesByType("resource").map((r) => ({
    url: r.name, start: r.startTime, end: r.responseEnd, status: r.responseStatus || 0,
  }));
  if (nav) res.unshift({ url: nav.name, start: 0, end: nav.responseEnd,
                         status: nav.responseStatus || 0 });
  const marks = {};
  for (const m of performance.getEntriesByType("mark")) {
    if (m.name.startsWith("app:ready:") && !(m.name in marks)) marks[m.name] = m.startTime;
  }
  return { now: performance.now(), fcp: s.fcp, lcp: s.lcp, lcpWhat: s.lcpWhat,
           shifts: s.shifts, long: s.long, res, marks };
}
"""


# --- score (zoals Lighthouse) ---


def log_normal_score(p10: float, med: float, value: float) -> float:
    """Lighthouse computeLogNormalScore: 1 bij 0, 0,9 bij p10, 0,5 bij de mediaan."""
    if value <= 0:
        return 1.0
    inverse_erfc_one_fifth = 0.9061938024368232
    x_log_ratio = math.log(max(sys.float_info.min, value / med))
    p10_log_ratio = -math.log(max(sys.float_info.min, p10 / med))
    standardized = x_log_ratio * inverse_erfc_one_fifth / p10_log_ratio
    score = math.erfc(standardized) / 2
    if value <= p10:
        return max(0.9, min(1.0, score))
    if value <= med:
        return max(0.5, min(0.8999999999999999, score))
    return max(0.0, min(0.49999999999999994, score))


def scores(profile: str, metrics: dict[str, float]) -> tuple[int, dict[str, float]]:
    per = {}
    for key, (p10, med) in CURVES[profile].items():
        per[key] = round(log_normal_score(p10, med, metrics[key]), 2)
    total = sum(per[k] * w for k, w in WEIGHTS.items()) / sum(WEIGHTS.values())
    return round(total * 100), per


# --- metrieken ---


def cls_value(shifts: list[list[float]]) -> float:
    """Het grootste sessievenster: verschuivingen met < 1 s ertussen, hooguit 5 s lang."""
    best = current = 0.0
    first = last = None
    for start, value in sorted(shifts):
        if first is None or start - last >= 1000 or start - first >= 5000:
            first, current = start, 0.0
        current += value
        last = start
        best = max(best, current)
    return best


def quiet_cpu(long_tasks: list[tuple[float, float]], end: float) -> list[tuple[float, float]]:
    if not long_tasks:
        return [(0.0, end)]
    periods = [(0.0, long_tasks[0][0])]
    for (_, prev_end), (next_start, _) in zip(long_tasks, long_tasks[1:], strict=False):
        periods.append((prev_end, next_start))
    periods.append((long_tasks[-1][1], end))
    return periods


def quiet_network(records: list[tuple[float, float]], end: float) -> list[tuple[float, float]]:
    """Lighthouse NetworkRecorder.findNetworkQuietPeriods met 2 toegestane verzoeken."""
    bounds = sorted(
        [(s, True) for s, _ in records if s <= end] + [(e, False) for _, e in records if e <= end],
        key=lambda b: b[0],
    )
    inflight, start, periods = 0, 0.0, []
    for time, is_start in bounds:
        if is_start:
            if inflight == ALLOWED_REQUESTS:
                periods.append((start, time))
            inflight += 1
        else:
            inflight -= 1
            if inflight == ALLOWED_REQUESTS:
                start = time
    if inflight <= ALLOWED_REQUESTS:
        periods.append((start, end))
    return [p for p in periods if p[0] != p[1]]


def tti(
    fcp: float,
    long_tasks: list[tuple[float, float]],
    records: list[tuple[float, float]],
    end: float,
) -> float | None:
    """Lighthouse findOverlappingQuietPeriods; None als er (nog) geen stil venster is."""

    def long_enough(p: tuple[float, float]) -> bool:
        return p[1] > fcp + QUIET_WINDOW_MS and p[1] - p[0] >= QUIET_WINDOW_MS

    cpu = [p for p in quiet_cpu(long_tasks, end) if long_enough(p)]
    net = [p for p in quiet_network(records, end) if long_enough(p)]
    while cpu and net:
        c, n = cpu[0], net[0]
        if c[0] >= n[0]:
            if n[1] >= c[0] + QUIET_WINDOW_MS:
                return max(c[0], fcp)
            net.pop(0)
        else:
            if c[1] >= n[0] + QUIET_WINDOW_MS:
                return max(c[0], fcp)
            cpu.pop(0)
    return None


def tbt(fcp: float, interactive: float, long_tasks: list[tuple[float, float]]) -> float:
    total = 0.0
    for start, end in long_tasks:
        if end - start < LONG_TASK_MS:
            continue
        clipped = min(end, interactive) - max(start, fcp)
        if clipped > LONG_TASK_MS:
            total += clipped - LONG_TASK_MS
    return total


def _histogram(jpeg: bytes) -> list[list[int]]:
    """Per kanaal 256 tellers, zonder (bijna-)witte pixels (speedline)."""
    from PIL import Image, ImageChops

    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    bands = [b.point(lambda v: 255 if v >= WHITE else 0) for b in img.split()]
    white = ImageChops.multiply(ImageChops.multiply(bands[0], bands[1]), bands[2])
    colored = ImageChops.invert(white)
    flat = img.histogram(mask=colored)
    return [flat[i * 256 : (i + 1) * 256] for i in range(3)]


def _progress(current: list[list[int]], target: list[list[int]]) -> float:
    """speedline calculateFrameProgress, met een leeg (wit) beginbeeld."""
    match = total = 0
    for channel in range(3):
        for value in range(256):
            cur, tgt = current[channel][value], target[channel][value]
            match += min(cur, tgt)
            total += tgt
    if total == 0:
        return 100.0
    return float(math.floor(match / total * 100))


def _trace_events(raw: bytes) -> list[dict[str, Any]]:
    data = json.loads(raw)
    return data["traceEvents"] if isinstance(data, dict) else data


def nav_start_ts(events: list[dict[str, Any]], url: str, marks: dict[str, float]) -> float | None:
    """De navigationStart van deze navigatie in de klok van de trace (µs)."""
    starts = [
        e["ts"]
        for e in events
        if e.get("name") == "navigationStart"
        and (e.get("args", {}).get("data") or {}).get("isLoadingMainFrame")
        and (e.get("args", {}).get("data") or {}).get("documentLoaderURL", "").startswith(url)
    ]
    if starts:
        return float(starts[0])
    # Terugval: een meetpunt van de pagina staat ook in de trace (blink.user_timing)
    for e in events:
        if e.get("name") in marks and e.get("ph") in ("R", "I", "n", "b"):
            return float(e["ts"]) - marks[e["name"]] * 1000
    return None


def speed_index(events: list[dict[str, Any]], nav_ts: float) -> tuple[float | None, int]:
    shots = sorted(
        (e["ts"], e["args"]["snapshot"])
        for e in events
        if e.get("name") == "Screenshot" and e.get("args", {}).get("snapshot") and e["ts"] >= nav_ts
    )
    if not shots:
        return None, 0
    hists = [(ts, _histogram(base64.b64decode(snap))) for ts, snap in shots]
    target = hists[-1][1]
    total, last_ts, last_progress = 0.0, nav_ts, 0.0
    for ts, hist in hists:
        total += (ts - last_ts) / 1000 * (1 - last_progress)
        last_ts, last_progress = ts, _progress(hist, target) / 100
    return total, len(shots)


# --- meten ---


def _records(data: dict[str, Any], methods: dict[str, set[str]]) -> list[tuple[float, float]]:
    """Afgeronde GET-verzoeken met status < 400 (zoals Lighthouse voor TTI)."""
    out = []
    for r in data["res"]:
        seen = methods.get(r["url"], set())
        if seen and "GET" not in seen:
            continue
        if r["status"] >= 400 or r["end"] <= 0:
            continue
        out.append((r["start"], r["end"]))
    return out


def measure_once(browsers: Browsers, url: str, spec: PageSpec, owner: Account | None) -> dict:
    prof = PROFILES[spec.profile]
    opts = {
        "viewport": prof["viewport"],
        "device_scale_factor": prof["device_scale_factor"],
        "is_mobile": prof["is_mobile"],
        "has_touch": prof["has_touch"],
    }
    context = browsers.context_as(owner, **opts) if spec.logged_in else browsers.new_context(**opts)
    problems: list[str] = []
    run: dict[str, Any] = {}
    tracing = False
    try:
        page = context.new_page()
        page.add_init_script(OBSERVE_JS)
        methods: dict[str, set[str]] = {}
        pending: set[Any] = set()

        def started(request: Any) -> None:
            methods.setdefault(request.url, set()).add(request.method)
            # De live stroom blijft open; die telt (net als bij Lighthouse) niet als bezig
            if request.resource_type not in ("eventsource", "websocket"):
                pending.add(request)

        def ended(request: Any) -> None:
            pending.discard(request)

        page.on("request", started)
        page.on("requestfinished", ended)
        page.on("requestfailed", ended)
        cdp_profile(
            context,
            page,
            latency_ms=prof["latency_ms"],
            down_mbit=prof["down_mbit"],
            up_mbit=prof["up_mbit"],
            cpu=prof["cpu"],
        )
        browsers.browser.start_tracing(page=page, screenshots=True, categories=TRACE_CATEGORIES)
        tracing = True
        full_url = browsers.server.url + url
        page.goto(url, wait_until="load", timeout=TIMEOUT_MS)
        interactive = None
        data: dict[str, Any] = {}
        for _ in range(TIMEOUT_MS // POLL_MS):
            data = page.evaluate(READ_JS)
            if data["fcp"] is not None:
                interactive = tti(
                    data["fcp"],
                    sorted(map(tuple, data["long"])),
                    _records(data, methods),
                    data["now"],
                )
                # Zoals Lighthouse pas stopt als het netwerk stil is: een verzoek dat nog loopt
                # (bijv. een grote lijst) kan daarna nog long tasks en verschuivingen geven
                if interactive is not None and not pending:
                    break
            page.wait_for_timeout(POLL_MS)
        raw = browsers.browser.stop_tracing()
        tracing = False
        if data.get("fcp") is None:
            return {"error": "geen FCP", "problems": problems}
        long_tasks = sorted(map(tuple, data["long"]))
        events = _trace_events(raw)
        nav_ts = nav_start_ts(events, full_url, data["marks"])
        si, frames = speed_index(events, nav_ts) if nav_ts is not None else (None, 0)
        fcp = data["fcp"]
        metrics = {
            "fcp": fcp,
            "si": max(si, fcp) if si is not None else None,
            "lcp": data["lcp"] if data["lcp"] is not None else fcp,
            "tbt": tbt(fcp, interactive if interactive is not None else data["now"], long_tasks),
            "cls": cls_value(data["shifts"]),
        }
        run = {
            "metrics": {
                k: (round(v, 4 if k == "cls" else 1) if v is not None else None)
                for k, v in metrics.items()
            },
            "tti": round(interactive, 1) if interactive is not None else None,
            "lcp_element": data["lcpWhat"],
            "long_tasks": [[round(s), round(e - s)] for s, e in long_tasks],
            "frames": frames,
            "marks": {k: round(v, 1) for k, v in data["marks"].items()},
        }
        if interactive is None:
            run["error"] = f"geen TTI binnen {TIMEOUT_MS} ms"
        elif metrics["si"] is None:
            run["error"] = "geen screenshots in de trace (Speed Index)"
        else:
            run["score"], run["scores"] = scores(spec.profile, metrics)
    finally:
        if tracing:
            try:
                browsers.browser.stop_tracing()
            except Exception:  # noqa: BLE001 - de trace was al weg
                pass
        problems.extend(browsers.close(context))
    run["problems"] = problems
    return run


def measure_page(
    browsers: Browsers, url: str, spec: PageSpec, owner: Account | None, runs: int
) -> dict[str, Any]:
    details = [measure_once(browsers, url, spec, owner) for _ in range(runs)]
    problems = [p for r in details for p in r["problems"]]
    errors = [r["error"] for r in details if "error" in r]
    measured = [r for r in details if "score" in r]
    score = round(median([r["score"] for r in measured])) if measured else None
    med = {k: median([r["metrics"][k] for r in measured]) if measured else None for k in WEIGHTS}
    seen_mark = spec.mark is None or all(
        f"app:ready:{spec.mark}" in r.get("marks", {}) for r in details
    )
    if not seen_mark:
        status = "not yet"  # alleen informatief, ook als een run niet af kwam
    elif errors:
        status = "fail"
    else:
        status = "pass" if score is not None and score >= GATE else "fail"
    prof = {k: v for k, v in PROFILES[spec.profile].items()}
    return {
        "url": url,
        "profile": spec.profile,
        "throttling": prof,
        "owner": spec.owner,
        "status": status,
        "score": score,
        "scores": [r.get("score") for r in details],
        "metrics": med,
        "errors": errors,
        "details": details,
        "problems": problems,
    }


def measure(
    server: Server, browsers: Browsers, data: PerfData | None, runs: int = 5, pages=None
) -> dict[str, Any]:
    wanted = list(pages or PAGES)
    urls = {"login": "/"}
    if data is not None:
        urls["public"] = data.public_url
        urls["folder"] = f"/map/{data.folder_id}"
    result: dict[str, Any] = {
        "tool": "lighthouse_like",
        "server": server.url,
        "runs": runs,
        "gate": GATE,
        "weights": WEIGHTS,
        "curves": CURVES,
        "pages": {},
    }
    for name in wanted:
        spec = PAGES[name]
        owner = data.owner if data is not None else None
        result["pages"][name] = measure_page(browsers, urls[name], spec, owner, runs)
    pages_out = result["pages"].values()
    result["problems"] = [p for page in pages_out for p in page["problems"]]
    result["ok"] = all(p["status"] != "fail" for p in pages_out) and not result["problems"]
    return result


def check(result: dict[str, Any]) -> list[str]:
    """Wat er hard mis is: bewaking-treffers, en voor elke pagina die al meetelt (niet "not
    yet") runs zonder meting en de poort ≥ 91 (die niet bij E2E_PERF_REPORT_ONLY=1)."""
    errors = [f"bewaking: {p}" for p in result["problems"]]
    for name, page in result["pages"].items():
        if page["status"] == "not yet":
            continue
        for e in page["errors"]:
            errors.append(f"{name}: {e}")
        if page["status"] == "fail" and not page["errors"] and not report_only():
            errors.append(
                f"{name} ({page['profile']}): score {page['score']} < {GATE}"
                f" (runs {page['scores']}, mediaan {page['metrics']})"
            )
    return errors


def test_lighthouse_like(server: Server, perf_data: PerfData, browsers: Browsers) -> None:
    result = measure(server, browsers, perf_data)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("lighthouse_like", result)
    for name, page in result["pages"].items():
        assert len(page["details"]) == result["runs"], name
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lighthouse-achtige score (ontwerp §4.3)")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--pages", default=",".join(PAGES), help="bijv. login,public,folder")
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
    write_result("lighthouse_like", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
