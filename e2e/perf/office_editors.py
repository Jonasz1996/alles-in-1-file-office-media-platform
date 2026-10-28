"""Veel bewerkers tegelijk (fase 4, §4.7 deel 2): 20 bewerkers, 5 documenten × 4, 60 s typen.

Doel (docs/fase-4/bouwontwerp.md §4.7): alle getypte teksten staan na het sluiten in de
bestanden; de cpu en het geheugen van DS en van de app zijn vastgelegd. Dit is de basis voor
de standaard van max_editors (Keuze 27; nu 20 per document).

Methode
- Een eigen server (office_bench.start_office, de timers van productie, CallbackClock) tegen de
  echte DS uit E2E_REAL_DS. Een eigenaar en docs × per_doc − 1 andere gebruikers, met
  schrijfrecht op zijn map "Samen"; elke bewerker is een eigen gebruiker.
- Per ronde docs nieuwe docx'en met echte opmaak (office_files.make("docx", budget=BUDGET):
  koppen, lijsten, tabellen, kop- en voettekst; ±25 pagina's), elk met per_doc bewerkers.
- Eén Chromium (office_crowd.Crowd), per bewerker een eigen context en /bewerken/<id>: de hele
  weg via de app (de schil, POST …/session, de proxy en de tunnel op /office/). De editors
  openen na elkaar met STAGGER s ertussen. Dan typt iedereen tegelijk SECONDS s eigen woorden
  in een eigen alinea (±4,5 tekens per s, zoals een mens), daarna 3 s rust en "Sluiten" voor
  iedereen. Klaar is: elke sessie dicht (DS stuurde status 2 en de app sloeg op).
- Vastgelegd per fase (openen, typen, opslaan): DsWatch (cpu van de container, geheugen zonder
  paginacache, x2t, /healthcheck) en office_crowd.Sampler (cpu en VmRSS van de app, cpu en PSS
  van Chromium, de hele machine en MemAvailable); per document de callback van status 2
  (CallbackClock) en welke woorden in het opgeslagen bestand staan (check_words).
- Geheugen: zakt MemAvailable onder LOW_FREE (3 GiB), dan opent de meting geen editors meer en
  stopt het typen; ze sluit netjes en zegt hoe ver ze kwam. Draai hem daarnaast altijd onder
  de waakhond (office_watchdog.py, 2 GiB).

    cd api && E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \\
      uv run --with playwright python ../e2e/perf/office_watchdog.py --log wd.log -- \\
      uv run --with playwright python ../e2e/perf/office_editors.py --docs 5 --per-doc 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
import uuid
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

GATE = {"all_saved": True}
DOCS, PER_DOC, SECONDS, ROUNDS = 5, 4, 60.0, 3
BUDGET = 150_000  # bytes XML per document: ±12 hoofdstukken, ±25 pagina's
STAGGER = 0.5
LOW_FREE = 3 * 1024**3
CLOSE_WAIT_S = 180.0


def session_keys(bench: ob.Bench) -> dict[str, str]:
    """key -> node-id (met streepjes) van alle sessies, uit de database (alleen lezen)."""
    with ob._db(bench.server) as conn:
        rows = conn.execute("SELECT key, node_id FROM office_session").fetchall()
    return {k: str(uuid.UUID(hex=n if isinstance(n, str) else n.hex())) for k, n in rows}


def open_sessions(bench: ob.Bench, nodes: list[str]) -> int:
    with ob._db(bench.server) as conn:
        rows = conn.execute("SELECT node_id FROM office_session WHERE state != 'closed'").fetchall()
    wanted = {uuid.UUID(n).hex for n in nodes}
    return sum(1 for (n,) in rows if (n if isinstance(n, str) else n.hex()) in wanted)


def wait_all_closed(bench: ob.Bench, nodes: list[str], seconds: float) -> bool:
    """Tot geen van deze bestanden nog een sessie heeft die niet dicht is."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not open_sessions(bench, nodes):
            return True
        time.sleep(0.5)
    return False


def make_docs(bench: ob.Bench, squad: ob.Team, docs: int, tag: str, seed: int) -> list[str]:
    """docs nieuwe docx-bestanden; seed telt bij de seed van de run op (office_files.run_seed),
    zodat geen run of stap de bestanden van een andere krijgt (en DS geen conversie uit zijn
    cache haalt)."""
    nodes = []
    for d in range(docs):
        data, _made = of.make("docx", 10_000 * of.run_seed() + seed + d, budget=BUDGET)
        nodes.append(
            ob.upload(bench.server, squad.owner, squad.folder, f"{tag}-{d + 1}.docx", data)
        )
    ob.wait_background(bench.server)
    return nodes


def editors_for(squad: ob.Team, nodes: list[str], per_doc: int, tag: str) -> list[oc.Editor]:
    people = squad.everyone
    out = []
    for d, node in enumerate(nodes):
        for u in range(per_doc):
            out.append(oc.Editor(people[d * per_doc + u], node, d + 1, u + 1, tag))
    return out


async def run_crowd(
    bench: ob.Bench, editors: list[oc.Editor], seconds: float, sampler: oc.Sampler
) -> tuple[dict[str, float], str | None]:
    """Openen, typen, sluiten; de momenten (time.monotonic) van elke fase, en de versie van
    Chromium."""
    t: dict[str, float] = {}
    async with oc.Crowd(bench.server, sampler) as crowd:
        t["open"] = time.monotonic()
        await crowd.open_all(editors, stagger=STAGGER)
        t["typing"] = time.monotonic()
        await crowd.type_all(seconds)
        t["typed"] = time.monotonic()
        await asyncio.sleep(3)
        opened = [e for e in crowd.editors if e.opened_s is not None]
        for e in opened:
            await crowd.status(e)
            await crowd.editor_text(e)
        t["close"] = time.monotonic()
        await asyncio.gather(*(crowd.close(e) for e in opened))
        t["closed_clicks"] = time.monotonic()
        nodes = sorted({e.node for e in editors})
        await asyncio.to_thread(wait_all_closed, bench, nodes, CLOSE_WAIT_S)
        t["saved"] = time.monotonic()
        version = crowd.version
    return t, version


def one_round(
    bench: ob.Bench,
    squad: ob.Team,
    docs: int,
    per_doc: int,
    seconds: float,
    n: int,
    ds_name: str,
    url: str,
) -> dict[str, Any]:
    tag = f"r{n}"
    nodes = make_docs(bench, squad, docs, f"samen-{tag}", 100 * docs * per_doc + 10 * n)
    editors = editors_for(squad, nodes, per_doc, tag)
    clock = bench.clock
    assert clock is not None
    seen = len(clock.exchanges)
    ob.wait_quiet()
    sampler = oc.Sampler(bench.pid, min_free=LOW_FREE).start()
    watch = ob.DsWatch(ds_name, url, interval=1.0, health_every=2.0).start()
    try:
        t, browser = asyncio.run(run_crowd(bench, editors, seconds, sampler))
    finally:
        sampler.stop()
        ds = watch.stop()
    keys = session_keys(bench)
    with clock._lock:
        later = clock.exchanges[seen:]
    saves = [
        e
        for e in later
        if e.path.startswith("/internal/office/callback") and e.callback_status in (2, 6)
    ]
    per_doc_out = []
    for d, node in enumerate(nodes):
        mine = [e for e in editors if e.node == node]
        content = bench.server.call(
            squad.owner, "GET", f"/api/v1/files/{node}/content", expect=200
        ).content
        words = oc.check_words(content, mine)
        final = [e for e in saves if keys.get(e.key or "") == node and e.callback_status == 2]
        per_doc_out.append(
            {
                "doc": d + 1,
                "editors": len(mine),
                "opened": sum(1 for e in mine if e.opened_s is not None),
                "words": words,
                "status2": len(final),
                "callback_s": [round(e.seconds() or 0, 3) for e in final],
                "close_to_callback_s": [round(e.started - t["close"], 2) for e in final],
            }
        )
    opened = [e for e in editors if e.opened_s is not None]
    out = {
        "round": n,
        "docs": docs,
        "per_doc": per_doc,
        "editors": len(editors),
        "opened": len(opened),
        "opened_s": ob.stats([e.opened_s for e in opened], 2),
        "open_errors": [e.error for e in editors if e.error],
        "typing_errors": [x for e in editors for x in e.typing_errors],
        "page_errors": sum(e.page_errors for e in editors),
        "typed_s": round(t["typed"] - t["typing"], 1),
        "save_s": round(t["saved"] - t["close"], 1),
        "low_memory": sampler.low.is_set(),
        "per_doc_results": per_doc_out,
        "typed": sum(p["words"]["typed"] for p in per_doc_out),
        "saved": sum(p["words"]["saved"] for p in per_doc_out),
        "missing_near": sum(p["words"]["missing_near"] for p in per_doc_out),
        "shown_known": sum(p["words"]["shown_known"] for p in per_doc_out),
        "not_shown": sum(p["words"]["not_shown_n"] for p in per_doc_out),
        "lost_saving": sum(p["words"]["lost_saving_n"] for p in per_doc_out),
        "phases": {
            name: {
                "ds": watch.between(a, b),
                "machine": sampler.between(a, b),
            }
            for name, a, b in (
                ("open", t["open"], t["typing"]),
                ("typing", t["typing"], t["typed"]),
                ("save", t["close"], t["saved"]),
            )
        },
        "ds": ds,
        "browser": browser,
    }
    typ = out["phases"]["typing"]
    print(
        f"ronde {n}: {out['opened']}/{out['editors']} open (mediaan {out['opened_s']['median']} s),"
        f" {out['saved']}/{out['typed']} woorden opgeslagen (in de editor maar niet in het"
        f" bestand: {out['lost_saving']}; nooit in de editor: {out['not_shown']}), DS"
        f" {typ['ds'].get('cores_avg')} cores (max {typ['ds'].get('cores_max_1s')}),"
        f" {typ['ds'].get('rss_mb_max')} MB; app {typ['machine'].get('app_cores_avg')} cores,"
        f" {typ['machine'].get('app_rss_mb_max')} MB; vrij"
        f" {typ['machine'].get('mem_available_mb_min')} MB",
        file=sys.stderr,
    )
    return out


def measure(
    url: str,
    secret: str,
    docs: int = DOCS,
    per_doc: int = PER_DOC,
    seconds: float = SECONDS,
    rounds: int = ROUNDS,
) -> dict[str, Any]:
    ds_name = ob.ds_container(url).get("name")
    if not ds_name:
        raise SystemExit("de container van DS is niet te vinden (docker ps op de poort)")
    result: dict[str, Any] = {
        "tool": "office_editors",
        "docs": docs,
        "per_doc": per_doc,
        "editors": docs * per_doc,
        "seconds": seconds,
        "rounds": rounds,
        "budget_xml_bytes": BUDGET,
        "gate": GATE,
        "round_results": [],
    }
    base = ob.tmpdir("office-editors-")
    bench = ob.start_office(base, url, secret, clock=True)
    try:
        squad = ob.team(bench, {}, members=docs * per_doc - 1)
        ob.wait_background(bench.server)
        for n in range(1, rounds + 1):
            r = one_round(bench, squad, docs, per_doc, seconds, n, ds_name, url)
            result["round_results"].append(r)
            ob.wait_background(bench.server, timeout=900)
            if r["low_memory"]:
                print("te weinig geheugen vrij: geen volgende ronde", file=sys.stderr)
                break
        result["machine"] = ob.machine(url)
    finally:
        bench.stop()
        shutil.rmtree(base, ignore_errors=True)
    result["summary"] = summarize(result)
    result["ok"] = not check(result)
    return result


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    rr = result["round_results"]

    def over(phase: str, side: str, key: str) -> dict[str, Any]:
        return ob.stats(
            [r["phases"][phase][side].get(key) for r in rr if r["phases"][phase][side].get(key)],
            2,
        )

    return {
        "rounds": len(rr),
        "opened": [r["opened"] for r in rr],
        "editors": result["editors"],
        "typed": [r["typed"] for r in rr],
        "saved": [r["saved"] for r in rr],
        "all_saved": all(r["saved"] == r["typed"] for r in rr),
        "missing": sum(r["typed"] - r["saved"] for r in rr),
        "missing_near": sum(r["missing_near"] for r in rr),
        # Met de tekst van elke editor zelf: weg bij het opslaan, en nooit in de editor
        "shown_known": sum(r.get("shown_known", 0) for r in rr),
        "not_shown": sum(r.get("not_shown", 0) for r in rr),
        "lost_saving": sum(r.get("lost_saving", 0) for r in rr),
        "none_lost_saving": all(
            r.get("lost_saving", 0) == 0 and r.get("shown_known", 0) == r["typed"] for r in rr
        ),
        "opened_s_median": ob.stats([r["opened_s"]["median"] for r in rr if r["opened_s"]["n"]]),
        "opened_s_max": max((r["opened_s"]["max"] or 0) for r in rr) if rr else None,
        "save_s": ob.stats([r["save_s"] for r in rr], 1),
        "ds_cores_typing": over("typing", "ds", "cores_avg"),
        "ds_cores_typing_max": over("typing", "ds", "cores_max_1s"),
        "ds_cores_open": over("open", "ds", "cores_avg"),
        "ds_cores_open_max": over("open", "ds", "cores_max_1s"),
        "ds_rss_mb_max": max((r["ds"].get("rss_mb_max") or 0) for r in rr) if rr else None,
        "ds_rss_mb_start": ob.stats([r["ds"].get("rss_mb_start") or 0 for r in rr], 0),
        "app_cores_typing": over("typing", "machine", "app_cores_avg"),
        "app_cores_open": over("open", "machine", "app_cores_avg"),
        "app_cores_open_max": over("open", "machine", "app_cores_max_1s"),
        "app_rss_mb_max": max(
            (r["phases"][p]["machine"].get("app_rss_mb_max") or 0)
            for r in rr
            for p in ("open", "typing", "save")
        )
        if rr
        else None,
        "browser_pss_mb_max": max(
            (r["phases"]["typing"]["machine"].get("browser_pss_mb_max") or 0) for r in rr
        )
        if rr
        else None,
        "machine_cores_typing": over("typing", "machine", "machine_cores_avg"),
        "mem_available_mb_min": min(
            (r["phases"][p]["machine"].get("mem_available_mb_min") or 10**9)
            for r in rr
            for p in ("open", "typing", "save")
        )
        if rr
        else None,
        "health_not_true": sum((r["ds"].get("health") or {}).get("not_true", 0) for r in rr),
        "low_memory": any(r["low_memory"] for r in rr),
        "oom_kills": sum(r["ds"].get("oom_kills") or 0 for r in rr),
    }


def check(result: dict[str, Any]) -> list[str]:
    s = result.get("summary") or {}
    errors = []
    gate = not report_only()  # het doel; wat er verder misgaat, telt altijd
    for r in result["round_results"]:
        if r["opened"] != r["editors"]:
            errors.append(
                f"ronde {r['round']}: {r['opened']} van {r['editors']} editors open"
                f" ({'; '.join(map(str, r['open_errors']))[:300]})"
            )
        if gate and r["saved"] != r["typed"]:
            errors.append(
                f"ronde {r['round']}: {r['typed'] - r['saved']} van {r['typed']} woorden niet"
                f" opgeslagen ({r['missing_near']} met één teken verschil,"
                f" {r.get('not_shown', 0)} had de editor zelf al niet)"
            )
        if r.get("lost_saving"):
            errors.append(
                f"ronde {r['round']}: {r['lost_saving']} woorden stonden in de editor maar niet"
                " in het opgeslagen bestand"
            )
        for p in r["per_doc_results"]:
            if p["status2"] != 1:
                errors.append(f"ronde {r['round']}, document {p['doc']}: {p['status2']}× status 2")
    if s.get("health_not_true"):
        errors.append(f"/healthcheck {s['health_not_true']} keer niet true")
    if s.get("low_memory"):
        errors.append("de sandbox had te weinig geheugen vrij (LOW_FREE); de meting stopte eerder")
    return errors


@pytest.mark.skipif(ob.real_ds() is None, reason="geen echte DS (E2E_REAL_DS)")
def test_office_editors() -> None:
    url, secret = ob.need_real_ds()
    result = measure(url, secret, docs=1, per_doc=2, seconds=20, rounds=1)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("office_editors", result)
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Veel bewerkers tegelijk (§4.7 deel 2)")
    parser.add_argument("--docs", type=int, default=DOCS)
    parser.add_argument("--per-doc", type=int, default=PER_DOC)
    parser.add_argument("--seconds", type=float, default=SECONDS)
    parser.add_argument("--rounds", type=int, default=ROUNDS)
    parser.add_argument("--out", type=Path, help="JSON ook naar dit bestand")
    args = parser.parse_args(argv)
    url, secret = ob.need_real_ds()
    result = measure(url, secret, args.docs, args.per_doc, args.seconds, args.rounds)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    write_result(f"office_editors_{args.docs * args.per_doc}", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
