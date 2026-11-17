"""Poort van P3: 5.000 bestanden in de wachtrij zonder long task boven 200 ms.

Methode, per meting een nieuwe gebruiker:
- een map met 50 submappen van elk 100 kleine bestanden (5.000), gekozen met "Map uploaden"
  (set_files op de map);
- in de pagina een long-task-observer en een rAF-recorder, aan vanaf vlak vóór de keuze;
- gemeten tot de samenvatting in de lade "… van 5.000" toont, en daarna nog 5 s waarin er
  echt geüpload wordt (drie tegelijk), met Alles pauzeren en Alles hervatten (Shift+P) erin;
- daarna Alles annuleren; de lade verdwijnt.

Metingen:
| waar       | CPU | wat                                                               |
| lade       | 1×  | na de keuze meteen naar /gedeeld: alleen de lade rendert (HARD)   |
| lade       | 4×  | idem, informatief                                                 |
| bestanden  | 1×  | de hele tijd op de bestandenpagina, informatief                   |
| bestanden  | 4×  | idem                                                              |
Op de bestandenpagina werkt ook de lijst van de map mee (Files.tsx van P2: de nieuwe mappen
en bestanden komen binnen via de live-events). Die kosten horen niet bij de wachtrij en de
lade, daarom is alleen de meting "lade, 1×" de poort.

Poort (met E2E_PERF_REPORT_ONLY=1 nooit hard): langste long task van de app ≤ 200 ms.
"Van de app" is het deel van een long task vanaf het change-event van de bestandskiezer. Het
vullen van het invoerveld door Playwright (set_files met 5.000 paden) is één lange taak in
de renderer die vlak vóór dat event eindigt; die staat apart in de JSON (set_files_task_ms),
net als de langste long task zonder aftrek (longest_task_ms).
De JSON staat in de uitvoer (-s) en met E2E_PERF_OUT ook in <map>/p3_queue.json.

    cd api && uv run --with playwright pytest ../e2e/test_p3_perf.py -m perf -s
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect

from conftest import cdp_profile, percentile, report_only, write_result

pytestmark = [pytest.mark.perf, pytest.mark.p3]

FILES = 5000
DIRS = 50
MAX_LONG_TASK_MS = 200
UPLOAD_WINDOW_S = 5
QUEUE_TIMEOUT_MS = 120_000
RUNS = (("lade", 1), ("lade", 4), ("bestanden", 1), ("bestanden", 4))

RECORDER_JS = r"""
(() => {
  const rec = { watching: false, longTasks: [], frames: [], last: 0 };
  window.__p3perf = rec;
  try {
    new PerformanceObserver((list) => {
      if (!rec.watching) return;
      for (const e of list.getEntries()) {
        rec.longTasks.push({ start: e.startTime, duration: e.duration });
      }
    }).observe({ type: "longtask", buffered: false });
    rec.supported = true;
  } catch (e) {
    rec.supported = false;
  }
  // Het change-event van de bestandskiezer: wat er vóór valt, is Playwright die de 5.000
  // bestanden in het invoerveld zet (set_files), niet de app
  document.addEventListener("change", () => {
    if (rec.watching && rec.change === undefined) rec.change = performance.now();
  }, true);
  const tick = (now) => {
    if (rec.watching && rec.last) rec.frames.push(now - rec.last);
    rec.last = now;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})();
"""


def make_tree(root: Path) -> Path:
    top = root / "Vijfduizend"
    per = FILES // DIRS
    for d in range(DIRS):
        sub = top / f"map-{d:02d}"
        sub.mkdir(parents=True)
        for i in range(per):
            (sub / f"bestand-{i:03d}.txt").write_bytes(b"x" * (64 + i))
    return top


def stop_recording(page) -> dict[str, Any]:
    return page.evaluate(
        "() => { const r = window.__p3perf; r.watching = false;"
        " return { supported: r.supported, longTasks: r.longTasks, frames: r.frames,"
        " change: r.change,"
        " summary: document.querySelector('.upload-tray-summary')?.textContent ?? '' }; }"
    )


def measure(context_as, user, tree: Path, where: str, rate: int) -> dict[str, Any]:
    """where="bestanden": de hele tijd op de bestandenpagina; where="lade": meteen na de keuze
    naar /gedeeld (alleen de lade rendert dan de wachtrij)."""
    context = context_as(user)
    context.add_init_script(RECORDER_JS)
    page = context.new_page()
    page.goto("/")
    expect(page.get_by_role("button", name="Uploaden", exact=True)).to_be_visible()
    applied = cdp_profile(context, page, latency_ms=0, down_mbit=1000, cpu=rate)
    applied.pop("session", None)
    page.evaluate("() => { window.__p3perf.watching = true; }")
    started = time.monotonic()
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Map uploaden", exact=True).click()
    chooser.value.set_files(tree)
    if where == "lade":
        page.get_by_role("link", name="Gedeeld met mij", exact=True).click()
    lade = page.get_by_role("region", name="Uploads", exact=True)
    summary = lade.locator(".upload-tray-summary")
    out: dict[str, Any] = {"where": where, "cpu": rate, "applied": applied, "files": FILES}
    try:
        expect(summary).to_have_text(re.compile(r"van 5\.000"), timeout=QUEUE_TIMEOUT_MS)
        out["queued_s"] = round(time.monotonic() - started, 2)
        # Uploaden loopt; pauzeer en hervat alles met het toetsenbord
        time.sleep(UPLOAD_WINDOW_S / 2)
        page.keyboard.press("Shift+P")
        expect(lade.get_by_role("button", name="Alles hervatten")).to_be_visible()
        page.keyboard.press("Shift+P")
        expect(lade.get_by_role("button", name="Alles pauzeren")).to_be_visible()
        time.sleep(UPLOAD_WINDOW_S / 2)
    except AssertionError as e:
        out["queued_s"] = None
        out["not_reached"] = str(e).splitlines()[0]
    rec = stop_recording(page)
    if out["queued_s"] is not None:
        lade.get_by_role("button", name="Alles annuleren").click()
        expect(lade).to_have_count(0, timeout=60_000)
    context.close()
    durations = [t["duration"] for t in rec["longTasks"]]
    # Per long task het deel vanaf het change-event: dat deel is van de app (het change-event
    # valt in dezelfde taak als het vullen van het invoerveld door Playwright)
    change = rec.get("change")
    app = [
        t["start"] + t["duration"] - max(t["start"], change)
        for t in rec["longTasks"]
        if change is not None and t["start"] + t["duration"] > change
    ]
    harness = [t["duration"] for t in rec["longTasks"] if change is None or t["start"] < change]
    out.update(
        {
            "longtask_api": rec["supported"],
            "long_tasks": len(durations),
            "longest_task_ms": round(max(durations), 1) if durations else 0,
            "longest_app_ms": round(max(app), 1) if app else 0,
            "app_over_200ms": sum(1 for d in app if d > MAX_LONG_TASK_MS),
            "set_files_task_ms": round(max(harness), 1) if harness else 0,
            "frame_p95_ms": round(percentile(rec["frames"], 95) or 0, 1),
            "frame_max_ms": round(max(rec["frames"]), 1) if rec["frames"] else None,
            "summary_after": rec["summary"],
        }
    )
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


def test_5000_bestanden_in_de_wachtrij_zonder_lange_taak(context_as, user_factory, tmp_path: Path):
    tree = make_tree(tmp_path)
    # Per meting een nieuwe gebruiker: anders bestaan de bestanden van de vorige al en komt
    # de vraag over dubbele bestanden
    runs = [
        measure(context_as, user_factory(name=f"Vijfduizend {where} {rate}x"), tree, where, rate)
        for where, rate in RUNS
    ]
    result = {
        "gate": {
            "longest_app_ms": MAX_LONG_TASK_MS,
            "hard": "lade, CPU 1×",
            "report_only": report_only(),
        },
        "runs": runs,
    }
    write_result("p3_queue", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    gated = next(r for r in runs if r["where"] == "lade" and r["cpu"] == 1)
    assert gated["longtask_api"], "deze Chromium heeft geen long-task-API"
    if not report_only():
        assert gated["queued_s"] is not None, gated.get("not_reached")
        assert gated["longest_app_ms"] <= MAX_LONG_TASK_MS, (
            f"langste long task van de app {gated['longest_app_ms']} ms"
            f" (poort {MAX_LONG_TASK_MS} ms)"
        )
