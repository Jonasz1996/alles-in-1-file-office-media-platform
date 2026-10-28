"""De XML-grens bij 2 GB opnieuw (fase 4, §4.7 deel 2; spike A11, "stap 18 verder").

Doel (docs/fase-4/bouwontwerp.md §4.7): een bestand net onder de grens van de invoercontrole
(app/office/inspect.py: 4 MiB XML voor docx en pptx, 2 MiB voor odt, ods en odp, 10 MiB voor
een xlsx), met echte opmaak en formules, en een docx met heel veel pagina's, laat x2t niet
vastlopen op het geheugen (geen OOM) en docservice blijft draaien. DS heeft 2 GiB (de grenzen
van compose.office-limits.yaml, via e2e/office_ds.sh).

Methode
- Een eigen server (office_bench.start_office, de timers van productie, CallbackClock) tegen de
  echte DS uit E2E_REAL_DS.
- Per soort (office_files.KINDS) en per ronde een nieuw bestand (een andere seed: een nieuwe
  sha, dus een nieuwe key bij DS, die anders zijn vorige antwoord geeft), net onder de grens
  (office_files.make: MARGIN = 16 KiB eronder). Upload via de API, zoals de browser; de app doet
  de invoercontrole, het voorbeeld van B21 en dan vanzelf de taak office.pdf (Keuze 25), die DS
  het bestand via /internal/office/src laat halen en omzetten.
- Gemeten per bestand: de uitkomst (pdf_state, pogingen, de fout), van het ophalen door DS
  (CallbackClock) tot ready, het aantal pagina's van de PDF, en DsWatch: de piek van het
  geheugen van de container (memory.max_usage_in_bytes, met paginacache, dus een bovengrens), het
  grootste x2t, OOM-kills (de teller van de cgroup en wie: de kernellog), of docservice of de
  converter een nieuw proces kreeg, en /healthcheck elke 0,5 s.
- Daarna, voor wat bewerkt kan worden (docx, docx_pages, pptx, xlsx), hetzelfde bestand openen
  in de echte editor via de app (/bewerken/<id>, een nieuwe browser per keer) tot de statusregel
  Bewerken zegt, en weer sluiten: dan zet x2t het om naar het formaat van de editor.
- Herhaling: ROUNDS rondes (standaard 3), elke ronde alle soorten.

Draai dit altijd onder de waakhond (office_watchdog.py): een document in de editor kost ook in
Chromium veel geheugen.

    cd api && E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \\
      uv run --with playwright python ../e2e/perf/office_watchdog.py --log wd.log -- \\
      uv run --with playwright python ../e2e/perf/office_xml.py --rounds 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import office_bench as ob  # noqa: E402 - pas na het pad naar e2e/perf
import office_crowd as oc  # noqa: E402
import office_files as of  # noqa: E402

from conftest import report_only, write_result  # noqa: E402

pytestmark = pytest.mark.perf

GATE = {"oom_kills": 0}
ROUNDS = 3
EDITABLE = ("docx", "docx_pages", "pptx", "xlsx")
FINAL = ("ready", "failed", "none", "unsafe", "ask")
CONVERT_WAIT_S = 600.0
OPEN_WAIT_S = 120.0


def src_fetches(clock: ob.CallbackClock) -> list[ob.Exchange]:
    with clock._lock:
        return [e for e in clock.exchanges if e.path.startswith("/internal/office/src/")]


def wait_pdf(
    bench: ob.Bench, node: str, seconds: float = CONVERT_WAIT_S
) -> tuple[str | None, float]:
    """Tot pdf_state een eindstand heeft en er geen taak office.pdf meer loopt."""
    end = time.monotonic() + seconds
    state = None
    while time.monotonic() < end:
        state = ob.pdf_state(bench.server, node)
        if state in FINAL:
            running = [
                j
                for j in ob.office_jobs(bench.server, node)
                if j["status"] in ("queued", "running")
            ]
            if not running:
                return state, time.monotonic()
        time.sleep(0.2)
    return state, time.monotonic()


def pdf_pages(bench: ob.Bench, node: str) -> int | None:
    sha, _ = ob.node_blob(bench.server, node)
    if sha is None:
        return None
    path = bench.server.data_dir / "office" / "pdf" / sha[:2] / f"{sha}.pdf"
    return of.pdf_pages(path.read_bytes()) if path.is_file() else None


def convert_one(
    bench: ob.Bench, squad: ob.Team, kind: str, seed: int, ds_name: str, url: str
) -> dict[str, Any]:
    data, made = of.make(kind, seed)
    name = f"grens-{kind}-{seed}.{made['ext']}"
    clock = bench.clock
    assert clock is not None
    seen = len(src_fetches(clock))
    ob.wait_quiet()
    watch = ob.DsWatch(ds_name, url, interval=0.25, health_every=0.5).start()
    t0 = time.monotonic()
    node = ob.upload(bench.server, squad.owner, squad.folder, name, data)
    state, t_end = wait_pdf(bench, node)
    ds = watch.stop()
    fetched = src_fetches(clock)[seen:]
    t_src = fetched[0].started if fetched else None
    out = {
        "kind": kind,
        "node": node,
        "made": {k: v for k, v in made.items() if k not in ("kind",)},
        "pdf_state": state,
        "jobs": ob.office_jobs(bench.server, node),
        "src_fetches": len(fetched),
        "upload_to_end_s": round(t_end - t0, 2),
        "src_to_end_s": None if t_src is None else round(t_end - t_src, 2),
        "pdf_pages": pdf_pages(bench, node) if state == "ready" else None,
        "ds": ds,
    }
    print(
        f"{kind} seed {seed}: {state} in {out['src_to_end_s']} s, piek {ds.get('kernel_peak_mb')}"
        f" MB, x2t {ds.get('x2t_mb_max')} MB, oom {ds.get('oom_kills')}",
        file=sys.stderr,
    )
    return out


async def _open(bench: ob.Bench, account: Any, node: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    async with oc.Crowd(bench.server) as crowd:
        crowd.ready_s = OPEN_WAIT_S
        editor = oc.Editor(account, node, 1, 1, "x")
        await crowd.open_all([editor], stagger=0)
        out.update(opened_s=editor.opened_s, error=editor.error)
        await crowd.status(editor)
        out["statuses"] = editor.statuses
        out["page_errors"] = editor.page_errors
        if editor.opened_s is not None:
            out["closed"] = await crowd.close(editor)
        out["browser"] = crowd.version
    out["teardown"] = crowd.teardown
    return out


def wait_closed(bench: ob.Bench, account: Any, node: str, seconds: float = 90) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        r = bench.server.call(account, "GET", f"/api/v1/office/files/{node}", expect=200)
        if r.json().get("open") is None:
            return True
        time.sleep(0.5)
    return False


def open_one(bench: ob.Bench, squad: ob.Team, row: dict[str, Any], ds_name: str, url: str) -> dict:
    ob.wait_quiet()
    watch = ob.DsWatch(ds_name, url, interval=0.25, health_every=0.5).start()
    t0 = time.monotonic()
    try:
        # De editor krijgt OPEN_WAIT_S; het opruimen daarna (Crowd sluit begrensd) nog 120 s.
        res = asyncio.run(
            asyncio.wait_for(_open(bench, squad.owner, row["node"]), OPEN_WAIT_S + 120)
        )
    except TimeoutError:
        res = {"opened_s": None, "error": f"niet opgeruimd binnen {OPEN_WAIT_S + 120:.0f} s"}
    closed = wait_closed(bench, squad.owner, row["node"])
    res.update(seconds=round(time.monotonic() - t0, 1), session_closed=closed, ds=watch.stop())
    print(
        f"  openen {row['kind']}: {res.get('opened_s')} s ({res.get('error')}), x2t"
        f" {res['ds'].get('x2t_mb_max')} MB, oom {res['ds'].get('oom_kills')}",
        file=sys.stderr,
    )
    return res


def measure(url: str, secret: str, rounds: int = ROUNDS, kinds: tuple[str, ...] = of.KINDS) -> dict:
    ds_name = ob.ds_container(url).get("name")
    if not ds_name:
        raise SystemExit("de container van DS is niet te vinden (docker ps op de poort)")
    result: dict[str, Any] = {
        "tool": "office_xml",
        "rounds": rounds,
        "gate": GATE,
        "limits": {k: of.limit(k) for k in kinds},
        "margin": of.MARGIN,
        "run_seed": of.run_seed(),
        "runs": [],
    }
    base = ob.tmpdir("office-xml-")
    bench = ob.start_office(base, url, secret, clock=True)
    try:
        squad = ob.team(bench, {})
        ob.wait_background(bench.server)
        for r in range(1, rounds + 1):
            for n, kind in enumerate(kinds):
                seed = 10_000 * of.run_seed() + 1000 * r + n
                row = convert_one(bench, squad, kind, seed, ds_name, url)
                if kind in EDITABLE:
                    row["open"] = open_one(bench, squad, row, ds_name, url)
                result["runs"].append(row)
                ob.wait_background(bench.server, timeout=900)
        result["machine"] = ob.machine(url)
    finally:
        bench.stop()
        shutil.rmtree(base, ignore_errors=True)
    result["kinds"] = summarize(result["runs"], kinds)
    result["ok"] = not check(result)
    return result


def summarize(runs: list[dict[str, Any]], kinds: tuple[str, ...]) -> dict[str, Any]:
    out = {}
    for kind in kinds:
        mine = [r for r in runs if r["kind"] == kind]
        if not mine:
            continue
        opens = [r["open"] for r in mine if r.get("open")]
        out[kind] = {
            "n": len(mine),
            "xml_bytes": mine[0]["made"]["xml_bytes"],
            "file_bytes": ob.stats([r["made"]["file_bytes"] for r in mine], 0),
            "made": {
                k: v
                for k, v in mine[0]["made"].items()
                if k in ("pages", "slides", "rows", "chapters", "tables", "formulas_per_row")
            },
            "ready": sum(1 for r in mine if r["pdf_state"] == "ready"),
            "states": [r["pdf_state"] for r in mine],
            "src_to_end_s": ob.stats([r["src_to_end_s"] for r in mine if r["src_to_end_s"]], 1),
            "pdf_pages": [r["pdf_pages"] for r in mine],
            "peak_mb": ob.stats([r["ds"].get("kernel_peak_mb") or 0 for r in mine], 0),
            "x2t_mb": ob.stats([r["ds"].get("x2t_mb_max") or 0 for r in mine], 0),
            "oom_kills": sum(r["ds"].get("oom_kills") or 0 for r in mine),
            "oom_victims": [v["comm"] for r in mine for v in r["ds"].get("oom_victims", [])],
            "docservice_restarted": any(r["ds"].get("docservice_restarted") for r in mine),
            "health_not_true": sum((r["ds"].get("health") or {}).get("not_true", 0) for r in mine),
            "open": None
            if not opens
            else {
                "opened": sum(1 for o in opens if o.get("opened_s") is not None),
                "n": len(opens),
                "opened_s": ob.stats([o["opened_s"] for o in opens if o.get("opened_s")], 1),
                "x2t_mb": ob.stats([o["ds"].get("x2t_mb_max") or 0 for o in opens], 0),
                "peak_mb": ob.stats([o["ds"].get("kernel_peak_mb") or 0 for o in opens], 0),
                "oom_kills": sum(o["ds"].get("oom_kills") or 0 for o in opens),
                "errors": [o.get("error") for o in opens if o.get("error")],
                "docservice_restarted": any(o["ds"].get("docservice_restarted") for o in opens),
            },
        }
    return out


def check(result: dict[str, Any]) -> list[str]:
    errors = []
    gate = not report_only()  # het doel; wat er verder misgaat, telt altijd
    for kind, k in result.get("kinds", {}).items():
        if k["oom_kills"] and gate:
            errors.append(f"{kind}: {k['oom_kills']} OOM-kill(s) ({', '.join(k['oom_victims'])})")
        if k["docservice_restarted"]:
            errors.append(f"{kind}: docservice kreeg een nieuw proces")
        if k["health_not_true"]:
            errors.append(f"{kind}: /healthcheck {k['health_not_true']} keer niet true")
        if k["ready"] != k["n"]:
            errors.append(
                f"{kind}: {k['ready']} van {k['n']} omgezet ({', '.join(map(str, k['states']))})"
            )
        o = k.get("open")
        if o:
            if o["opened"] != o["n"]:
                errors.append(f"{kind}: {o['opened']} van {o['n']} geopend in de editor")
            if o["oom_kills"] and gate:
                errors.append(f"{kind}: openen gaf {o['oom_kills']} OOM-kill(s)")
            if o["docservice_restarted"]:
                errors.append(f"{kind}: docservice kreeg een nieuw proces bij openen")
    return errors


@pytest.mark.skipif(ob.real_ds() is None, reason="geen echte DS (E2E_REAL_DS)")
def test_office_xml() -> None:
    url, secret = ob.need_real_ds()
    result = measure(url, secret, rounds=1)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("office_xml", result)
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="De XML-grens bij 2 GB (§4.7 deel 2)")
    parser.add_argument("--rounds", type=int, default=ROUNDS)
    parser.add_argument("--kinds", default=",".join(of.KINDS))
    parser.add_argument("--out", type=Path, help="JSON ook naar dit bestand")
    args = parser.parse_args(argv)
    url, secret = ob.need_real_ds()
    kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
    result = measure(url, secret, args.rounds, kinds)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    write_result("office_xml", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
