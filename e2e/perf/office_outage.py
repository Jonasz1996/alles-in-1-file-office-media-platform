"""DS stoppen en killen terwijl er editors open zijn (fase 4, §4.7 deel 2).

Doelen (docs/fase-4/bouwontwerp.md §4.7):
- stoppen met open editors: alles opgeslagen, en `docker stop` duurt ≤ 60 s (Keuze 29:
  stop_grace_period 60s voor DS; compose stopt DS vóór de app, dus de app draait nog);
- een kill van DS met open editors: het verlies is hooguit wat er sinds het laatste
  tussentijds opslaan getypt is, en dat is ≤ 5 minuten (Keuze 18: de klok van de app stuurt
  forcesave met userdata "timer" als de laatste opslag ouder is dan checkpoint_minutes, 5).

Methode
- Een eigen server (office_bench.start_office, de timers van productie: de ronde elke 30 s,
  checkpoint_minutes 5, CallbackClock) tegen de echte DS uit E2E_REAL_DS; DS is de eigen
  container van de meting (docker stop, kill en start alleen daarop).
- Per ronde DOCS nieuwe docx'en (office_files, ±25 pagina's) met elk PER_DOC bewerkers, elk een
  eigen gebruiker in Chromium via de app (office_crowd), die eigen woorden typen met de tijd
  waarop elk woord getypt was.
- stoppen: SECONDS_STOP s typen, 3 s rust, dan `docker stop -t 60` op de container van DS
  (gemeten: de duur, en of docker moest killen, exitcode 137). Daarna meteen de inhoud van elk
  bestand: welke woorden staan erin. Dan DS weer starten, wachten tot hij gezond is en de
  font-herstart voorbij is, en tot de app hem weer ok noemt; en na de ronde en het herstel nog
  eens kijken (een forgotten file zou dan alsnog binnenkomen, Keuze 21).
- kill: typen tot elk document zijn eerste forcesave van de klok had (status 6, userdata
  "timer", ±5 tot 5½ minuut na het openen), dan nog offset s doortypen, dan `docker kill`. Per
  document: c = het begin van die laatste callback van de klok. Woorden getypt vóór c − 3 s
  moeten erin staan; woorden na c horen verloren te zijn (de editor is weg); het verlies is
  t_kill − c. Daarna DS weer starten en nogmaals kijken (na een kill heeft DS geen forgotten
  file: de spike, extra bevinding 1).
- Herhaling: stoppen ROUNDS (3) keer; kill met de offsets OFFSETS (30, 150 en 285 s na de
  forcesave van de klok: kort erna, halverwege, en vlak voor de volgende), dus ook 3 keer.

    cd api && E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \\
      uv run --with playwright python ../e2e/perf/office_watchdog.py --log wd.log -- \\
      uv run --with playwright python ../e2e/perf/office_outage.py --mode stop
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
import office_editors as oe  # noqa: E402

from conftest import report_only, write_result  # noqa: E402

pytestmark = pytest.mark.perf

GATE = {"stop_s": 60.0, "loss_s": 300.0}
DOCS, PER_DOC = 2, 2
SECONDS_STOP = 30.0
ROUNDS = 3
OFFSETS = (30.0, 150.0, 285.0)
TIMER_WAIT_S = 420.0  # de eerste forcesave van de klok: 5 min plus een ronde, plus marge
SAFE_BEFORE_S = 3.0  # wat zo kort vóór de forcesave getypt is, kan er nog net niet in zitten


def contents(bench: ob.Bench, squad: ob.Team, nodes: list[str]) -> dict[str, bytes]:
    return {
        node: bench.server.call(
            squad.owner, "GET", f"/api/v1/files/{node}/content", expect=200
        ).content
        for node in nodes
    }


def words_by_time(
    data: bytes, editors: list[oc.Editor], until: float | None = None
) -> dict[str, Any]:
    """check_words, en voor elk woord of het erin staat (oc.is_saved), met zijn tijd."""
    text, tokens = oc.saved_text(data)
    rows = [
        (t, w, oc.is_saved(w, text))
        for e in editors
        for w, t in e.words
        if until is None or t <= until
    ]
    return {"rows": rows, "tokens": tokens}


def restart(bench: ob.Bench, ds_name: str, url: str) -> dict[str, Any]:
    started = ob.ds_start(ds_name, url)
    t0 = time.monotonic()
    try:
        bench.office.wait_state("ok", 900)
        started["app_ok_s"] = round(time.monotonic() - t0, 1)
    except AssertionError as exc:
        started["app_ok_s"] = None
        started["app_error"] = str(exc)[:300]
    return started


def ds_saves(clock: ob.CallbackClock, seen: int) -> list[ob.Exchange]:
    with clock._lock:
        later = clock.exchanges[seen:]
    return [
        e
        for e in later
        if e.path.startswith("/internal/office/callback") and e.callback_status in (2, 3, 6, 7)
    ]


# --- stoppen ---


async def _type_then(
    bench: ob.Bench,
    editors: list[oc.Editor],
    sampler: oc.Sampler,
    seconds: float | None,
    trigger: Any,
) -> dict[str, Any]:
    """Openen, typen (seconds, of tot trigger() een moment geeft), dan action in een thread.
    trigger(stop_event) draait in een thread naast het typen en geeft de actie terug."""
    out: dict[str, Any] = {}
    async with oc.Crowd(bench.server, sampler) as crowd:
        out["t_open"] = time.monotonic()
        await crowd.open_all(editors, stagger=oe.STAGGER)
        out["t_typing"] = time.monotonic()
        stop = asyncio.Event()
        typing = asyncio.create_task(crowd.type_all(seconds or 3600.0, stop))
        out.update(await trigger(stop, typing))
        stop.set()
        await asyncio.gather(typing, return_exceptions=True)
        out["browser"] = crowd.version
    return out


def stop_round(
    bench: ob.Bench, squad: ob.Team, n: int, ds_name: str, url: str, seconds: float
) -> dict[str, Any]:
    nodes = oe.make_docs(bench, squad, DOCS, f"stop-r{n}", 5000 + 10 * n)
    editors = oe.editors_for(squad, nodes, PER_DOC, f"s{n}")
    clock = bench.clock
    assert clock is not None
    ob.wait_quiet()
    seen = len(clock.exchanges)
    sampler = oc.Sampler(bench.pid, min_free=oe.LOW_FREE).start()

    async def trigger(stop: asyncio.Event, typing: asyncio.Task) -> dict[str, Any]:
        await typing  # seconds s typen
        await asyncio.sleep(3)
        t = time.monotonic()
        result = await asyncio.to_thread(ob.ds_stop, ds_name, 60)
        return {"t_stop": t, "stop": result}

    try:
        t = asyncio.run(_type_then(bench, editors, sampler, seconds, trigger))
    finally:
        sampler.stop()
    keys = oe.session_keys(bench)
    saves = ds_saves(clock, seen)
    right_after = contents(bench, squad, nodes)
    started = restart(bench, ds_name, url)
    # Na de start: de ronde en het herstel een kans geven (forgotten files, Keuze 21)
    time.sleep(90)
    later = contents(bench, squad, nodes)
    per_doc = []
    for d, node in enumerate(nodes):
        mine = [e for e in editors if e.node == node]
        final = [e for e in saves if keys.get(e.key or "") == node]
        per_doc.append(
            {
                "doc": d + 1,
                "opened": sum(1 for e in mine if e.opened_s is not None),
                "right_after_stop": oc.check_words(right_after[node], mine),
                "after_restart": oc.check_words(later[node], mine),
                "callbacks": [
                    {
                        "status": e.callback_status,
                        "after_stop_s": round(e.started - t["t_stop"], 2),
                        "answer_s": round(e.seconds() or 0, 3),
                        "http": e.status,
                    }
                    for e in final
                ],
            }
        )
    out = {
        "round": n,
        "editors": len(editors),
        "opened": sum(1 for e in editors if e.opened_s is not None),
        "typed_s": round(t["t_stop"] - t["t_typing"] - 3, 1),
        "stop": t["stop"],
        "restart": started,
        "per_doc_results": per_doc,
        "typed": sum(p["right_after_stop"]["typed"] for p in per_doc),
        "saved_right_after": sum(p["right_after_stop"]["saved"] for p in per_doc),
        "saved_after_restart": sum(p["after_restart"]["saved"] for p in per_doc),
        "machine": sampler.between(t["t_open"], t["t_stop"]),
    }
    print(
        f"stop {n}: docker stop {t['stop']['seconds']} s"
        f" (gekild: {t['stop']['killed_after_grace']}),"
        f" {out['saved_right_after']}/{out['typed']} woorden meteen,"
        f" {out['saved_after_restart']}/{out['typed']} na de herstart",
        file=sys.stderr,
    )
    return out


# --- kill ---


def kill_round(
    bench: ob.Bench, squad: ob.Team, n: int, offset: float, ds_name: str, url: str
) -> dict[str, Any]:
    nodes = oe.make_docs(bench, squad, DOCS, f"kill-r{n}", 7000 + 10 * n)
    editors = oe.editors_for(squad, nodes, PER_DOC, f"k{n}")
    clock = bench.clock
    assert clock is not None
    ob.wait_quiet()
    seen = len(clock.exchanges)
    sampler = oc.Sampler(bench.pid, min_free=oe.LOW_FREE).start()

    def timer_saves() -> dict[str, ob.Exchange]:
        keys = oe.session_keys(bench)
        found: dict[str, ob.Exchange] = {}
        for e in ds_saves(clock, seen):
            node = keys.get(e.key or "")
            if node in nodes and e.callback_status == 6 and e.userdata == "timer":
                found[node] = e  # de laatste per document
        return found

    async def trigger(stop: asyncio.Event, typing: asyncio.Task) -> dict[str, Any]:
        end = time.monotonic() + TIMER_WAIT_S
        found: dict[str, ob.Exchange] = {}
        while time.monotonic() < end and len(found) < len(nodes) and not typing.done():
            found = await asyncio.to_thread(timer_saves)
            await asyncio.sleep(1)
        if len(found) < len(nodes):
            return {"error": f"geen forcesave van de klok binnen {TIMER_WAIT_S:.0f} s"}
        last = max(e.started for e in found.values())
        await asyncio.sleep(max(0.0, last + offset - time.monotonic()))
        t = time.monotonic()
        result = await asyncio.to_thread(ob.ds_kill, ds_name)
        return {"t_kill": t, "kill": result}

    try:
        t = asyncio.run(_type_then(bench, editors, sampler, None, trigger))
    finally:
        sampler.stop()
    out: dict[str, Any] = {"round": n, "offset_s": offset, "editors": len(editors)}
    if "error" in t:
        out["error"] = t["error"]
        return out
    t_kill = t["t_kill"]
    checkpoints = timer_saves()
    right_after = contents(bench, squad, nodes)
    started = restart(bench, ds_name, url)
    time.sleep(150)  # de ronde (elke 30 s, info elke 2 minuten) sluit de sessies als lost
    later = contents(bench, squad, nodes)
    states = sessions_of(bench, nodes)
    per_doc = []
    for d, node in enumerate(nodes):
        mine = [e for e in editors if e.node == node]
        cp = checkpoints.get(node)
        c = cp.started if cp else None
        rows = words_by_time(right_after[node], mine, until=t_kill)["rows"]
        rows_later = words_by_time(later[node], mine, until=t_kill)["rows"]
        before = [r for r in rows if c is not None and r[0] <= c - SAFE_BEFORE_S]
        after = [r for r in rows if c is not None and r[0] > c]
        lost = [r for r in rows if not r[2]]
        per_doc.append(
            {
                "doc": d + 1,
                "opened": sum(1 for e in mine if e.opened_s is not None),
                "checkpoint_after_open_s": None if c is None else round(c - t["t_open"], 1),
                "checkpoint_http": None if cp is None else cp.status,
                "checkpoint_answer_s": None if cp is None else round(cp.seconds() or 0, 3),
                "loss_window_s": None if c is None else round(t_kill - c, 1),
                "typed": len(rows),
                "before_checkpoint": len(before),
                "before_saved": sum(1 for r in before if r[2]),
                "after_checkpoint": len(after),
                "after_saved": sum(1 for r in after if r[2]),
                "lost": len(lost),
                "lost_span_s": None
                if not lost
                else round(max(r[0] for r in lost) - min(r[0] for r in lost), 1),
                "lost_first_after_checkpoint_s": None
                if not lost or c is None
                else round(min(r[0] for r in lost) - c, 1),
                "saved_after_restart": sum(1 for r in rows_later if r[2]),
                "near_missing_before": oc.check_words(right_after[node], mine, before=c)[
                    "missing_near"
                ]
                if c is not None
                else 0,
            }
        )
    out.update(
        {
            "opened": sum(1 for e in editors if e.opened_s is not None),
            "kill": t["kill"],
            "restart": started,
            "sessions_after": states,
            "per_doc_results": per_doc,
            "machine": sampler.between(t["t_open"], t_kill),
        }
    )
    print(
        f"kill {n} (+{offset:.0f} s): "
        + "; ".join(
            f"doc {p['doc']}: venster {p['loss_window_s']} s, vóór {p['before_saved']}/"
            f"{p['before_checkpoint']}, erna {p['after_saved']}/{p['after_checkpoint']} bewaard"
            for p in per_doc
        ),
        file=sys.stderr,
    )
    return out


def sessions_of(bench: ob.Bench, nodes: list[str]) -> list[dict[str, Any]]:
    import uuid

    wanted = {uuid.UUID(n).hex for n in nodes}
    with ob._db(bench.server) as conn:
        rows = conn.execute("SELECT node_id, state, closed_reason FROM office_session").fetchall()
    return [
        {"state": s, "closed_reason": o}
        for n, s, o in rows
        if (n if isinstance(n, str) else n.hex()) in wanted
    ]


# --- alles ---


def measure(
    url: str,
    secret: str,
    mode: str,
    rounds: int = ROUNDS,
    offsets: tuple[float, ...] = OFFSETS,
    seconds: float = SECONDS_STOP,
) -> dict[str, Any]:
    ds_name = ob.ds_container(url).get("name")
    if not ds_name:
        raise SystemExit("de container van DS is niet te vinden (docker ps op de poort)")
    result: dict[str, Any] = {
        "tool": "office_outage",
        "mode": mode,
        "docs": DOCS,
        "per_doc": PER_DOC,
        "gate": GATE,
        "round_results": [],
    }
    base = ob.tmpdir("office-outage-")
    bench = ob.start_office(base, url, secret, clock=True)
    try:
        squad = ob.team(bench, {}, members=DOCS * PER_DOC - 1)
        ob.wait_background(bench.server)
        if mode == "stop":
            for n in range(1, rounds + 1):
                result["round_results"].append(stop_round(bench, squad, n, ds_name, url, seconds))
                ob.wait_background(bench.server, timeout=900)
        else:
            for n, offset in enumerate(offsets, start=1):
                result["round_results"].append(kill_round(bench, squad, n, offset, ds_name, url))
                ob.wait_background(bench.server, timeout=900)
        result["machine"] = ob.machine(url)
    finally:
        # DS moet altijd weer draaien voor de volgende meting
        if not ob.ds_running(ds_name):
            restart(bench, ds_name, url)
        bench.stop()
        shutil.rmtree(base, ignore_errors=True)
    result["summary"] = summarize(result)
    result["ok"] = not check(result)
    return result


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    rr = [r for r in result["round_results"] if "error" not in r]
    if result["mode"] == "stop":
        return {
            "rounds": len(rr),
            "stop_s": ob.stats([r["stop"]["seconds"] for r in rr], 1),
            "killed_after_grace": sum(1 for r in rr if r["stop"]["killed_after_grace"]),
            "typed": [r["typed"] for r in rr],
            "saved_right_after": [r["saved_right_after"] for r in rr],
            "saved_after_restart": [r["saved_after_restart"] for r in rr],
            "all_saved": all(r["saved_after_restart"] == r["typed"] for r in rr),
            "all_saved_right_after": all(r["saved_right_after"] == r["typed"] for r in rr),
            "restart_s": ob.stats([r["restart"].get("fonts_done_s") or 0 for r in rr], 0),
            "callback_after_stop_s": ob.stats(
                [
                    c["after_stop_s"]
                    for r in rr
                    for p in r["per_doc_results"]
                    for c in p["callbacks"]
                    if c["status"] == 2
                ],
                2,
            ),
        }
    docs = [p for r in rr for p in r["per_doc_results"]]
    return {
        "rounds": len(rr),
        "errors": [r["error"] for r in result["round_results"] if "error" in r],
        "loss_window_s": [p["loss_window_s"] for p in docs],
        "checkpoint_after_open_s": ob.stats(
            [p["checkpoint_after_open_s"] for p in docs if p["checkpoint_after_open_s"]], 1
        ),
        "before_checkpoint": sum(p["before_checkpoint"] for p in docs),
        "before_saved": sum(p["before_saved"] for p in docs),
        "after_checkpoint": sum(p["after_checkpoint"] for p in docs),
        "after_saved": sum(p["after_saved"] for p in docs),
        "lost": sum(p["lost"] for p in docs),
        "typed": sum(p["typed"] for p in docs),
        "saved_after_restart": sum(p["saved_after_restart"] for p in docs),
        "lost_first_after_checkpoint_s": [p["lost_first_after_checkpoint_s"] for p in docs],
        "restart_s": ob.stats([r["restart"].get("fonts_done_s") or 0 for r in rr], 0),
        "sessions_after": [s for r in rr for s in r["sessions_after"]],
    }


def check(result: dict[str, Any]) -> list[str]:
    s = result.get("summary") or {}
    errors = []
    gate = not report_only()
    for r in result["round_results"]:
        if "error" in r:
            errors.append(f"ronde {r['round']}: {r['error']}")
        elif r.get("opened") != r.get("editors"):
            errors.append(f"ronde {r['round']}: {r.get('opened')} van {r['editors']} editors open")
    if result["mode"] == "stop":
        if gate and (s.get("stop_s", {}).get("max") or 0) > GATE["stop_s"]:
            errors.append(f"docker stop tot {s['stop_s']['max']} s > {GATE['stop_s']} s")
        if s.get("killed_after_grace"):
            errors.append(f"docker moest {s['killed_after_grace']} keer killen (137)")
        if gate and not s.get("all_saved"):
            errors.append(
                f"niet alles opgeslagen: {s.get('saved_after_restart')} van {s.get('typed')}"
            )
        return errors
    if s.get("before_saved") != s.get("before_checkpoint"):
        errors.append(
            f"vóór de forcesave van de klok getypt, maar weg: "
            f"{s['before_checkpoint'] - s['before_saved']} van {s['before_checkpoint']}"
        )
    worst = max((w for w in s.get("loss_window_s", []) if w is not None), default=None)
    if gate and worst is not None and worst > GATE["loss_s"]:
        errors.append(f"verlies tot {worst} s > {GATE['loss_s']} s")
    return errors


@pytest.mark.skipif(ob.real_ds() is None, reason="geen echte DS (E2E_REAL_DS)")
def test_office_outage_stop() -> None:
    url, secret = ob.need_real_ds()
    result = measure(url, secret, "stop", rounds=1, seconds=15)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("office_outage_stop", result)
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DS stoppen en killen met open editors (§4.7)")
    parser.add_argument("--mode", choices=("stop", "kill"), required=True)
    parser.add_argument("--rounds", type=int, default=ROUNDS, help="stop")
    parser.add_argument("--seconds", type=float, default=SECONDS_STOP, help="stop: typen")
    parser.add_argument(
        "--offsets", default=",".join(f"{o:g}" for o in OFFSETS), help="kill: s na de klok"
    )
    parser.add_argument("--out", type=Path, help="JSON ook naar dit bestand")
    args = parser.parse_args(argv)
    url, secret = ob.need_real_ds()
    offsets = tuple(float(o) for o in args.offsets.split(",") if o.strip())
    result = measure(url, secret, args.mode, args.rounds, offsets, args.seconds)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    write_result(f"office_outage_{args.mode}", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
