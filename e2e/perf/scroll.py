"""10.000 bestanden scrollen soepel (ontwerp §4.4; P0, frozen na P0).

Data: 10.000 rijen over 2.000 verschillende thumbnails (seed.py), in lijst en raster op
/map/<10k>; 50.000 foto's over dezelfde blobs voor de tijdlijn (/fotos).

Methode, per weergave en per CPU-vertraging (1× en 4×, via CDP):
- een rAF-framerecorder en een long-task-observer in de pagina;
- focus op de lijst (de rij of cel met tabindex="0", roving focus);
- 6 s lang elke 50 ms mouse.wheel(0, 400);
- één keer ArrowDown;
- 20 × PageDown, dan End.

Poorten:
| p95 frame-interval bij CPU 1×        | ≤ 20 ms                                        |
| p95 frame-interval bij CPU 4×        | ≤ 34 ms                                        |
| long tasks tijdens het scrollen      | geen boven 50 ms                               |
| gerenderde [role=row]                | ≤ 200 (plus 1 vastgehouden focusrij)           |
| DOM-nodes                            | ≤ 4.000                                        |
| thumbnails                           | ≥ 100 verschillende adressen geladen           |
| na het scrollen plus ArrowDown       | document.activeElement zit in een rij          |
| na End                               | de laatste rij (aria-rowindex) zichtbaar en    |
|                                      | gefocust; in de lijst rij 10.000 of verder     |

Contract met de pagina's (P2 lijst en raster, P5 tijdlijn), zie docs/fase-3/e2e.md:
- de lijst heeft een element met aria-rowcount, met daarin rijen [role=row] met
  aria-rowindex; een kopregel bevat [role=columnheader] en telt niet als rij;
- de toets G wisselt naar het raster; dat heeft [data-view="grid"];
- thumbnails zijn <img>-elementen met een adres onder /api/v1/previews/.
Zolang een weergave dat contract niet heeft (in P0: de oude tabel, geen raster, geen
tijdlijn), meet het script wat het kan en meldt het "not yet"; de poorten zijn dan alleen
informatief. Met het contract erin zijn ze hard (pass/fail), behalve met
E2E_PERF_REPORT_ONLY=1.

Los draaien (start een eigen server en vult hem; JSON op stdout):
    cd api && uv run --with playwright python ../e2e/perf/scroll.py [--modes list,grid,timeline]
Via pytest (gemarkeerd perf):
    cd api && uv run --with playwright pytest ../e2e/perf/scroll.py -s
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import seed  # noqa: E402 - pas na het pad naar e2e/
from conftest import (  # noqa: E402
    Account,
    Browsers,
    PerfData,
    Server,
    create_user,
    percentile,
    prepare_perf,
    report_only,
    standalone,
    write_result,
)

pytestmark = pytest.mark.perf

RATES = (1, 4)
FRAME_P95_MS = {1: 20, 4: 34}
MAX_ROWS = 200 + 1  # plus de vastgehouden focusrij
MAX_DOM = 4000
MIN_THUMBS = 100
WHEEL_STEPS = 120  # 6 s bij één stap per 50 ms
WHEEL_PAUSE_MS = 50
LOAD_TIMEOUT_MS = 120_000
TIMELINE_PHOTOS = 50_000
VIEWPORT = {"width": 1280, "height": 800}

RECORDER_JS = r"""
(() => {
  const rec = { frames: [], recording: false, longTasks: [], watching: false, thumbs: new Set() };
  window.__e2eScroll = {
    start() {
      rec.frames = [];
      rec.recording = true;
      const loop = (t) => {
        if (!rec.recording) return;
        rec.frames.push(t);
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    },
    stop() {
      rec.recording = false;
      return rec.frames.slice();
    },
    watchLongTasks() {
      rec.longTasks = [];
      rec.watching = true;
    },
    longTasks() {
      rec.watching = false;
      return rec.longTasks.slice();
    },
    thumbs() {
      return Array.from(rec.thumbs);
    },
    counts() {
      const rows = new Set(document.querySelectorAll("[role=row], tbody tr"));
      let n = 0;
      for (const r of rows) if (!r.querySelector("[role=columnheader], th")) n++;
      return { rows: n, dom: document.getElementsByTagName("*").length };
    },
  };
  try {
    new PerformanceObserver((list) => {
      if (!rec.watching) return;
      for (const e of list.getEntries()) {
        rec.longTasks.push({ start: e.startTime, duration: e.duration });
      }
    }).observe({ type: "longtask", buffered: false });
  } catch (e) {
    /* geen long-task-API */
  }
  // Een thumbnail telt als hij binnen is en gedecodeerd (load, naturalWidth > 0)
  document.addEventListener("load", (e) => {
    const img = e.target;
    if (!(img instanceof HTMLImageElement) || img.naturalWidth === 0) return;
    try {
      const url = new URL(img.currentSrc || img.src, location.href);
      if (url.pathname.startsWith("/api/v1/previews/")) rec.thumbs.add(url.pathname);
    } catch (err) {
      /* geen geldig adres */
    }
  }, true);
})();
"""

# Rijen, of een pagina die 10 s na het meetpunt van de schil nog steeds geen rijen heeft
ROWS_JS = r"""
() => {
  if (document.querySelector("[role=row], tbody tr")) return "rows";
  const mark = performance.getEntriesByName("app:ready:route", "mark")[0];
  if (mark && performance.now() - mark.startTime > 10000) return "empty";
  return false;
}
"""
VIRTUAL_JS = "() => !!document.querySelector('[aria-rowcount] [role=row][aria-rowindex]')"
FOCUS_JS = r"""
() => {
  const el = document.querySelector('[role=row][tabindex="0"], [role=row] [tabindex="0"]');
  if (!el) return false;
  el.focus({ preventScroll: true });
  return document.activeElement === el;
}
"""
ACTIVE_JS = r"""
() => {
  const a = document.activeElement;
  const row = a && a.closest ? a.closest("[role=row]") : null;
  const list = row ? row.closest("[aria-rowcount]") : null;
  const box = row ? row.getBoundingClientRect() : null;
  const role = a && a.getAttribute ? a.getAttribute("role") : null;
  return {
    element: a ? a.tagName.toLowerCase() + (role ? `[${role}]` : "") : null,
    in_row: !!row,
    rowindex: row ? Number(row.getAttribute("aria-rowindex")) || null : null,
    rowcount: list ? Number(list.getAttribute("aria-rowcount")) || null : null,
    visible: !!box && box.height > 0 && box.bottom > 0 && box.top < innerHeight,
  };
}
"""


def prepare_timeline(server: Server, admin: Account, blobs: list[str], n: int) -> Account:
    """Een eigen gebruiker met n foto's (alleen afbeeldingen, datums over jaren verspreid)."""
    owner = create_user(server, admin, name="Fotograaf")
    server.seeded(
        lambda data_dir: seed.seed_folder(
            data_dir, owner, n, blobs, name="Foto's", photos_only=True, step=timedelta(hours=2)
        )
    )
    return owner


def _check(value: Any, limit: Any, ok: bool) -> dict[str, Any]:
    return {"value": value, "limit": limit, "ok": bool(ok)}


def measure_view(
    browsers: Browsers, owner: Account, path: str, mode: str, rate: int, n: int
) -> dict[str, Any]:
    out: dict[str, Any] = {"path": path, "mode": mode, "cpu": rate}
    context = browsers.context_as(owner, viewport=VIEWPORT)
    try:
        page = context.new_page()
        page.add_init_script(RECORDER_JS)
        cdp = context.new_cdp_session(page)
        started = time.monotonic()
        page.goto(path, wait_until="commit")
        try:
            found = page.wait_for_function(ROWS_JS, timeout=LOAD_TIMEOUT_MS).json_value()
        except Exception:  # noqa: BLE001 - de pagina kwam niet: zo melden
            found = "timeout"
        if found != "rows":
            out.update(status="not yet", reason=f"geen rijen ([role=row]): {found}")
            return out
        out["load_s"] = round(time.monotonic() - started, 2)
        page.wait_for_timeout(1000)  # de eerste pagina's en thumbnails
        if mode == "grid":
            page.keyboard.press("g")
            try:
                page.wait_for_selector("[data-view=grid]", timeout=3000)
            except Exception:  # noqa: BLE001 - nog geen raster
                out.update(status="not yet", reason="geen rasterweergave na G ([data-view=grid])")
                return out
            page.wait_for_timeout(500)
        virtual = bool(page.evaluate(VIRTUAL_JS))
        focused = bool(page.evaluate(FOCUS_JS))
        samples = [page.evaluate("window.__e2eScroll.counts()")]

        cdp.send("Emulation.setCPUThrottlingRate", {"rate": rate})
        page.mouse.move(VIEWPORT["width"] / 2, VIEWPORT["height"] / 2)
        page.evaluate("window.__e2eScroll.watchLongTasks(); window.__e2eScroll.start()")
        for i in range(WHEEL_STEPS):
            page.mouse.wheel(0, 400)
            page.wait_for_timeout(WHEEL_PAUSE_MS)
            if i % 20 == 19:
                samples.append(page.evaluate("window.__e2eScroll.counts()"))
        frames = page.evaluate("window.__e2eScroll.stop()")
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(300)
        after_arrow = page.evaluate(ACTIVE_JS)
        for _ in range(20):
            page.keyboard.press("PageDown")
            page.wait_for_timeout(WHEEL_PAUSE_MS)
        page.keyboard.press("End")
        page.wait_for_timeout(1000)
        after_end = page.evaluate(ACTIVE_JS)
        samples.append(page.evaluate("window.__e2eScroll.counts()"))
        long_tasks = page.evaluate("window.__e2eScroll.longTasks()")
        thumbs = page.evaluate("window.__e2eScroll.thumbs()")
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})
    finally:
        out["problems"] = browsers.close(context)

    intervals = [b - a for a, b in zip(frames, frames[1:], strict=False)]
    p95 = percentile(intervals, 95)
    over = [t["duration"] for t in long_tasks if t["duration"] > 50]
    rows = max(s["rows"] for s in samples)
    dom = max(s["dom"] for s in samples)
    end_ok = (
        after_end["in_row"]
        and after_end["visible"]
        and after_end["rowindex"] is not None
        and after_end["rowindex"] == after_end["rowcount"]
        and (mode != "list" or after_end["rowindex"] >= n)
    )
    checks = {
        "frame_p95_ms": _check(
            round(p95, 1) if p95 else None,
            FRAME_P95_MS[rate],
            p95 is not None and p95 <= FRAME_P95_MS[rate],
        ),
        "long_tasks_over_50ms": _check(len(over), 0, not over),
        "rows_rendered": _check(rows, MAX_ROWS, rows <= MAX_ROWS),
        "dom_nodes": _check(dom, MAX_DOM, dom <= MAX_DOM),
        "thumbnails": _check(len(thumbs), MIN_THUMBS, len(thumbs) >= MIN_THUMBS),
        "focus_after_arrow": _check(after_arrow, "in een rij", after_arrow["in_row"]),
        "end_focused_visible": _check(after_end, "laatste rij, zichtbaar", end_ok),
    }
    all_ok = all(c["ok"] for c in checks.values())
    out.update(
        status=("pass" if all_ok else "fail") if virtual else "not yet",
        reason=None if virtual else "geen gevirtualiseerde lijst ([aria-rowcount] [role=row])",
        virtualized=virtual,
        focused_before=focused,
        frames=len(frames),
        longest_task_ms=round(max(over), 1) if over else None,
        checks=checks,
    )
    return out


def measure(
    server: Server,
    browsers: Browsers,
    data: PerfData,
    timeline_owner: Account | None,
    modes=("list", "grid", "timeline"),
    rates=RATES,
    photos: int = TIMELINE_PHOTOS,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tool": "scroll",
        "server": server.url,
        "n": data.n,
        "blobs": len(data.blobs),
        "timeline_photos": photos if timeline_owner else 0,
        "views": [],
    }
    for mode in modes:
        if mode == "timeline":
            if timeline_owner is None:
                continue
            owner, path = timeline_owner, "/fotos"
        else:
            owner, path = data.owner, f"/map/{data.folder_id}"
        for rate in rates:
            result["views"].append(measure_view(browsers, owner, path, mode, rate, data.n))
    result["problems"] = [p for v in result["views"] for p in v.get("problems", [])]
    result["ok"] = not result["problems"] and all(v["status"] != "fail" for v in result["views"])
    return result


def check(result: dict[str, Any]) -> list[str]:
    errors = [f"bewaking: {p}" for p in result["problems"]]
    for view in result["views"]:
        if view["status"] == "fail" and not report_only():
            bad = {k: c["value"] for k, c in view["checks"].items() if not c["ok"]}
            errors.append(f"{view['mode']} bij CPU {view['cpu']}×: {bad}")
    return errors


@pytest.fixture(scope="module")
def timeline_owner(server: Server, admin: Account, perf_data: PerfData) -> Account:
    return prepare_timeline(server, admin, perf_data.blobs, TIMELINE_PHOTOS)


def test_scroll(
    server: Server, perf_data: PerfData, timeline_owner: Account, browsers: Browsers
) -> None:
    result = measure(server, browsers, perf_data, timeline_owner)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("scroll", result)
    # Het harnas zelf: de 10k-map laadt, en waar gescrold is, zijn frames opgenomen
    lists = [v for v in result["views"] if v["mode"] == "list"]
    assert lists and all(v["status"] != "not yet" or "frames" in v for v in lists), lists
    for view in result["views"]:
        if "frames" in view:
            assert view["frames"] > 10, view
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrollen meten (ontwerp §4.4)")
    parser.add_argument("--modes", default="list,grid,timeline")
    parser.add_argument("--rates", default="1,4", help="CPU-vertraging, bijv. 1,4")
    parser.add_argument("--n", type=int, default=10_000, help="bestanden in de grote map")
    parser.add_argument("--photos", type=int, default=TIMELINE_PHOTOS)
    parser.add_argument("--out", type=Path, help="JSON ook naar dit bestand")
    args = parser.parse_args(argv)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = set(modes) - {"list", "grid", "timeline"}
    if unknown:
        parser.error(f"onbekende weergaven: {', '.join(sorted(unknown))}")
    rates = [int(r) for r in args.rates.split(",") if r.strip()]
    with standalone() as (server, admin, browsers):
        data = prepare_perf(server, admin, n=args.n)
        timeline = None
        if "timeline" in modes:
            timeline = prepare_timeline(server, admin, data.blobs, args.photos)
        result = measure(
            server, browsers, data, timeline, modes=modes, rates=rates, photos=args.photos
        )
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    write_result("scroll", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
