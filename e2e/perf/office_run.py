"""De metingen van office (fase 4, §4.7) in één keer, zoals run_all.py voor fase 3.

Na elkaar, elk met een eigen server (de timers van productie) en nooit twee tegelijk.
Deel 1:
- office_open.py: koud en warm openen via de app, en de cpu van de app bij koud openen;
- office_tunnels.py: het geheugen van de app met 50 open tunnels;
- office_save.py: opslaan, van de callback tot de versie in de database (1 MiB);
- office_inspect.py: de invoercontrole op een xlsx met 10 MB XML en een pptx met 80 MB foto's;
- office_budget.py: het groottebudget tegen 3ccee0a (eerste lading, editor, Bestanden).
Deel 2 (zwaar: draai office_run.py dan onder office_watchdog.py):
- office_xml.py: de XML-grens bij 2 GB (conversie en openen, net onder de grens per soort);
- office_convert.py: conversie klein en van 401 pagina's, en 401 pagina's terwijl een editor
  opent;
- office_editors.py: veel bewerkers tegelijk (--scale, standaard 2, 8 en 20: 1 × 2, 2 × 4 en
  5 × 4), 60 s typen; stopt met opschalen als de sandbox te weinig geheugen vrij had;
- office_outage_stop en office_outage_kill (office_outage.py): DS stoppen en killen met open
  editors.
Alles behalve office_inspect en office_budget heeft een echte DS nodig (E2E_REAL_DS en
E2E_REAL_DS_SECRET_FILE, zie docs/fase-4/e2e.md); zonder DS staan ze als "niet gemeten".
Met --import tool=pad neemt hij de JSON van een los gedraaid script over (bijv. onder de
waakhond, met E2E_PERF_OUT), zonder opnieuw te meten.

Uitvoer: docs/fase-4/metingen.json (alles) en in docs/fase-4/metingen.md het blok tussen
<!-- gemeten --> en <!-- /gemeten --> (de tabel). De rest van metingen.md is handwerk (wat de
getallen betekenen) en blijft staan. Met --only draaien alleen die metingen; de uitslag van de
andere blijft uit metingen.json staan (met zijn eigen datum en commit). Met --rewrite meet hij
niets en schrijft hij alleen het blok opnieuw uit metingen.json. metingen.json is een kleinere
kopie (compact()); de volledige uitslag per script komt in E2E_PERF_OUT.

Met E2E_PERF_REPORT_ONLY=1 faalt geen doel; anders is de exitcode 1 als er een faalt. Met
E2E_PERF_OUT=<map> schrijft elk script ook zijn eigen JSON daarheen. Met E2E_PERF_TMP=<map>
komen de data van de servers daar (anders in de standaard-tmp).

    cd api && E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \\
      uv run --with playwright python ../e2e/perf/office_run.py
    cd api && uv run --with playwright python ../e2e/perf/office_run.py --only office_inspect
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import office_bench as ob  # noqa: E402 - pas na het pad naar e2e/perf
import office_budget  # noqa: E402
import office_convert  # noqa: E402
import office_editors  # noqa: E402
import office_inspect  # noqa: E402
import office_open  # noqa: E402
import office_outage  # noqa: E402
import office_save  # noqa: E402
import office_tunnels  # noqa: E402
import office_xml  # noqa: E402

from conftest import report_only, write_result  # noqa: E402

ROOT = HERE.parents[1]
DOCS = ROOT / "docs" / "fase-4"
PART1 = ("office_open", "office_tunnels", "office_save", "office_inspect", "office_budget")
PART2 = (
    "office_xml",
    "office_convert",
    "office_editors",
    "office_outage_stop",
    "office_outage_kill",
)
TOOLS = PART1 + PART2
NEEDS_DS = {"office_open", "office_tunnels", "office_save", *PART2}
SCALES = (2, 8, 20)
BLOCK = re.compile(r"^<!-- gemeten -->$.*?^<!-- /gemeten -->$", re.S | re.M)
SKELETON = """# Metingen fase 4

<!-- gemeten -->
<!-- /gemeten -->
"""


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _n(value: Any, digits: int = 2) -> str:
    """Een getal op zijn Nederlands (komma)."""
    if value is None:
        return "–"
    if isinstance(value, float):
        value = round(value, digits)
    return str(value).replace(".", ",")


def _spread(s: dict[str, Any], unit: str = " s", digits: int = 2) -> str:
    """mediaan (kleinste–grootste, n=…)."""
    if not s or s.get("median") is None:
        return "–"
    return (
        f"{_n(s['median'], digits)}{unit} ({_n(s['min'], digits)}–{_n(s['max'], digits)},"
        f" n={s['n']})"
    )


def _yes(ok: bool | None) -> str:
    return "niet gemeten" if ok is None else ("ja" if ok else "nee")


def rows(results: dict[str, Any]) -> list[dict[str, str]]:
    """De regels van §4.7 deel 1: wat, doel, gemeten, gehaald, hoe."""
    out: list[dict[str, str]] = []

    def add(what: str, goal: str, value: str, ok: bool | None, how: str) -> None:
        out.append({"what": what, "goal": goal, "result": value, "met": _yes(ok), "how": how})

    op = results.get("office_open")
    how_open = "office_open.py: niet gemeten (geen echte DS)"
    if op:
        how_open = (
            f"office_open.py, {op['runs']}× per soort (proef.docx/.xlsx/.pptx, elke keer een nieuw"
            " bestand), page.goto(/bewerken/<id>) tot de statusregel Bewerken zegt; koud: een"
            " nieuw browserprofiel, warm: het tweede bestand in datzelfde profiel"
            f" (profiel {op.get('profile', 'disk')}); mediaan (kleinste–grootste)"
        )
    how_warm = how_open if not op else "office_open.py, dezelfde runs (zie koud openen)"
    how_cpu = how_open
    if op:
        how_cpu = (
            "office_open.py, dezelfde koude runs: utime + stime van het serverproces van vóór"
            " page.goto tot 2 s zonder nieuw verzoek, min het verbruik in rust; verzoeken: de"
            " toegangslog van de server, bytes: Resource Timing (mediaan)"
        )
    for key, what, gate, unit, how in (
        ("cold", "Koud openen via de app", office_open.GATE["cold_s"], "s", how_open),
        ("warm", "Warm openen", office_open.GATE["warm_s"], "s", how_warm),
    ):
        if not op:
            add(what, f"≤ {_n(gate)} s", "–", None, how)
            continue
        parts, ok = [], True
        for kind, k in op["kinds"].items():
            s = k[key]["ready_s"]
            parts.append(f"{kind} {_spread(s)}")
            ok = ok and s["median"] is not None and s["median"] <= gate
        add(what, f"≤ {_n(gate)} {unit}", "; ".join(parts), ok, how)
    if op:
        parts, ok = [], True
        for kind, k in op["kinds"].items():
            c = k["cold"]
            s = c["cpu_net_s"]
            parts.append(
                f"{kind} {_spread(s)} ({_n(c['requests_app']['median'], 0)} verzoeken,"
                f" {_n((c['bytes_network']['median'] or 0) / 1e6, 1)} MB)"
            )
            ok = ok and s["median"] is not None and s["median"] <= office_open.GATE["cpu_core_s"]
        add("Cpu van de app bij koud openen", "≤ 1 core-seconde", "; ".join(parts), ok, how_cpu)
    else:
        add("Cpu van de app bij koud openen", "≤ 1 core-seconde", "–", None, how_cpu)

    tu = results.get("office_tunnels")
    if tu:
        held = tu["first_wave"]["extra_held_mb"]
        opened = tu["first_wave"]["extra_open_mb"]
        worst = max(held["max"] or 0, opened["max"] or 0)
        alive = all(
            w["held"]["alive"] == w["held"]["tunnels"]
            for r in tu["round_results"]
            for w in r["waves"]
        )
        add(
            "Geheugen van de app met 50 tunnels",
            "≤ +20 MB",
            f"+{_spread(opened, ' MB')} open, +{_spread(held, ' MB')} na {_n(tu['hold_s'], 0)} s;"
            f" alle 50 open: {'ja' if alive else 'nee'}",
            worst <= office_tunnels.GATE["extra_mb"] and alive,
            f"office_tunnels.py, {tu['rounds']} rondes (elk een nieuwe server), 10 gebruikers × 5"
            " docx, VmRSS mediaan van 3; de eerste keer per server telt",
        )
    else:
        add(
            "Geheugen van de app met 50 tunnels",
            "≤ +20 MB",
            "–",
            None,
            "office_tunnels.py: niet gemeten (geen echte DS)",
        )

    sv = results.get("office_save")
    if sv:
        worst = sv["all"]["callback_s"]["max"]
        add(
            "Opslaan: callback tot versie in de database",
            "≤ 2 s voor 1 MB",
            f"Nu opslaan (6) {_spread(sv['nu_opslaan']['callback_s'], ' s', 3)}; sluiten (2)"
            f" {_spread(sv['sluiten']['callback_s'], ' s', 3)}",
            worst is not None and worst <= office_save.GATE["callback_s"],
            f"office_save.py, docx van {_n(sv['document']['bytes'] / 1024**2, 2)} MiB (een foto),"
            f" {sv['rounds']} rondes × ({sv['saves_per_round']}× Nu opslaan + sluiten) in de echte"
            " editor; CallbackClock: van de eerste byte van de callback tot de kop van het antwoord"
            " (de app antwoordt na de commit)",
        )
    else:
        add(
            "Opslaan: callback tot versie in de database",
            "≤ 2 s voor 1 MB",
            "–",
            None,
            "office_save.py: niet gemeten (geen echte DS)",
        )

    ins = results.get("office_inspect")
    if ins:
        parts, ok = [], True
        for ext, f in ins["files"].items():
            parts.append(
                f"{ext} {_spread(f['cpu_s'], ' s', 3)} cpu, lus max"
                f" {_n(f['loop_lag_max_ms'], 1)} ms"
            )
            ok = ok and f["all_ok"] and f["all_in_thread"]
            ok = ok and f["cpu_s"]["median"] <= office_inspect.GATE["cpu_s"]
        x, p = ins["files"]["xlsx"], ins["files"]["pptx"]
        add(
            "Invoercontrole: xlsx 10 MB XML, pptx 80 MB foto's",
            "≤ 1 s cpu, in een thread",
            "; ".join(parts),
            ok,
            f"office_inspect.py, {ins['runs']}× per bestand, inspect.check() zoals de app hem"
            f" aanroept (cache leeg), cpu = time.thread_time() van de controle; xlsx"
            f" {_n(x['xml_bytes'] / 1024**2, 2)} MiB XML (net onder de grens van 10 MiB) in"
            f" {_n(x['file_bytes'] / 1e6, 1)} MB, pptx {p['photos']} foto's (JPEG, deflate)"
            f" {_n(p['photo_bytes'] / 1e6, 1)} MB",
        )
    else:
        add(
            "Invoercontrole: xlsx 10 MB XML, pptx 80 MB foto's",
            "≤ 1 s cpu, in een thread",
            "–",
            None,
            "office_inspect.py: niet gemeten",
        )

    bu = results.get("office_budget")
    if bu:
        g, then, now = bu["growth_kb"], bu["then"], bu["now"]
        how = f"office_budget.py: npm run build en budget.mjs --json, {bu['base']} en {bu['head']}"
        add(
            "Budget: eerste lading",
            "groeit ≤ 0,5 KB",
            f"{_n(then['initial']['bytes'] / 1000, 2)} → {_n(now['initial']['bytes'] / 1000, 2)}"
            f" KB (+{_n(g['initial'])} KB)",
            g["initial"] <= office_budget.GATE["initial_growth_kb"],
            how,
        )
        ed = now["editor"]
        add(
            "Budget: chunk van de editor",
            "< 50 KB gzip",
            "–" if ed is None else f"{_n(ed['chunk_bytes'] / 1000, 2)} KB",
            ed is not None and ed["chunk_bytes"] / 1000 < office_budget.GATE["editor_chunk_kb"],
            how,
        )
        add(
            "Budget: route Bestanden",
            "groeit < 1 KB",
            f"{_n(then['files']['bytes'] / 1000, 2)} → {_n(now['files']['bytes'] / 1000, 2)} KB"
            f" (+{_n(g['files'])} KB)",
            g["files"] < office_budget.GATE["files_growth_kb"],
            how,
        )
    else:
        for what, goal in (
            ("Budget: eerste lading", "groeit ≤ 0,5 KB"),
            ("Budget: chunk van de editor", "< 50 KB gzip"),
            ("Budget: route Bestanden", "groeit < 1 KB"),
        ):
            add(what, goal, "–", None, "office_budget.py: niet gemeten")
    return out + rows2(results)


def _mb(n: Any) -> str:
    return _n((n or 0) / 1e6, 1)


KIND_LABEL = {
    "docx": "docx (opmaak, tabellen)",
    "docx_pages": "docx (heel veel pagina's)",
    "pptx": "pptx",
    "xlsx": "xlsx (formules)",
    "odt": "odt",
    "ods": "ods",
    "odp": "odp",
}


MADE_LABEL = {
    "pages": "pagina's",
    "slides": "dia's",
    "rows": "rijen",
    "chapters": "hoofdstukken",
    "tables": "tabellen",
    "formulas_per_row": "formules per rij",
}


def rows2(results: dict[str, Any]) -> list[dict[str, str]]:
    """De regels van §4.7 deel 2."""
    out: list[dict[str, str]] = []

    def add(what: str, goal: str, value: str, ok: bool | None, how: str) -> None:
        out.append({"what": what, "goal": goal, "result": value, "met": _yes(ok), "how": how})

    what = "XML-grens bij 2 GB: net onder de grens per soort, ook opmaak, formules, veel pagina's"
    goal = "geen OOM van x2t; docservice blijft"
    xm = results.get("office_xml")
    if xm:
        parts, opens, ok, stayed = [], [], True, True
        for kind, k in xm["kinds"].items():
            extra = ", ".join(
                f"{v} {MADE_LABEL.get(n, n)}" for n, v in (k.get("made") or {}).items()
            )
            parts.append(
                f"{KIND_LABEL.get(kind, kind)} {_n(k['xml_bytes'] / 1024**2, 2)} MiB XML"
                f"{f' ({extra})' if extra else ''}: {k['ready']}/{k['n']} omgezet, tot klaar of"
                f" mislukt {_spread(k['src_to_end_s'], ' s', 1)}, x2t tot"
                f" {_n(k['x2t_mb']['max'], 0)} MB, DS tot {_n(k['peak_mb']['max'], 0)} MB,"
                f" OOM {k['oom_kills']}"
            )
            o = k.get("open")
            if o:
                opens.append(
                    f"{kind} {o['opened']}/{o['n']}"
                    + (f" ({_spread(o['opened_s'], ' s', 1)})" if o["opened"] else "")
                )
                ok = ok and not o["oom_kills"]
                stayed = stayed and not o["docservice_restarted"]
            ok = ok and not k["oom_kills"]
            stayed = stayed and not k["docservice_restarted"]
        value = (
            "; ".join(parts) + f". docservice bleef hetzelfde proces: {'ja' if stayed else 'nee'}"
        )
        if opens:
            value += f". Geopend in de editor (hooguit {int(office_xml.OPEN_WAIT_S)} s): "
            value += ", ".join(opens)
        add(
            what,
            goal,
            value,
            ok and stayed,
            f"office_xml.py, {xm['rounds']} rondes × {len(xm['kinds'])} bestanden (elke ronde"
            " nieuwe bestanden, net onder de grens van inspect: 16 KiB eronder), via de app:"
            " upload → voorbeeld → pdf-taak → DS /converter; DsWatch elke 0,25 s (geheugen van de"
            " cgroup van DS, rss van x2t, OOM-kills uit dmesg, het proces van docservice,"
            " /healthcheck); daarna elk bewerkbaar bestand openen in de editor",
        )
    else:
        add(what, goal, "–", None, "office_xml.py: niet gemeten (geen echte DS)")

    cv = results.get("office_convert")
    s = (cv or {}).get("summary") or {}
    if cv and (s.get("small") or s.get("large")):
        parts, ok = [], True
        for name, k in s.get("small", {}).items():
            parts.append(
                f"{name.rsplit('.', 1)[1]} {_spread(k['app_s'])} (DS alleen"
                f" {_n(k['direct_s']['median'])} s)"
            )
            ok = ok and k["ready"] == k["n"]
            ok = ok and (k["app_s"]["median"] or 99) <= office_convert.SMALL_OK_S
        lg = s.get("large")
        if lg:
            parts.append(
                f"401 pagina's {_spread(lg['app_s'], ' s', 1)} (DS alleen"
                f" {_spread(lg['direct_s'], ' s', 1)}; pagina's {lg['pages']}, x2t tot"
                f" {_n(lg['x2t_mb']['max'], 0)} MB)"
            )
            ok = ok and lg["ready"] == lg["n"]
            ok = ok and (lg["app_s"]["max"] or 999) <= office_convert.GATE["large_s"]
        add(
            "Conversie klein / 401 pagina's",
            "±1 s / ≤ 60 s (22 s in de proef)",
            "; ".join(parts),
            ok if s.get("small") and lg else None,
            f"office_convert.py: klein {cv['runs_small']}× per soort (proef.docx/.xlsx/.pptx, elke"
            f" keer een ander bestand), 401 pagina's {cv['runs']}× (een docx van"
            f" {_mb(lg['file_bytes'] if lg else 0)} MB); via de app gemeten van het ophalen van het"
            " bron door DS (/internal/office/src) tot de pdf in de app staat, ‘DS alleen’ is"
            " hetzelfde bestand rechtstreeks naar /converter (async, elke 0,1 s gevraagd)",
        )
    else:
        add("Conversie klein / 401 pagina's", "±1 s / ≤ 60 s", "–", None, "office_convert.py")

    if cv and (s.get("conversie eerst") or s.get("editor eerst")):
        parts, ok, asked, worst = [], True, 0, 0
        for order in ("conversie eerst", "editor eerst"):
            k = s.get(order)
            if not k:
                ok = False
                continue
            parts.append(
                f"{order}: openen {_spread(k['opened_s'], ' s', 1)} ({k['opened']}/{k['n']}),"
                f" conversie {_spread(k['conversion_s'], ' s', 1)}"
            )
            ok = ok and k["opened"] == k["n"] and k["health_not_true"] == 0
            ok = ok and (k["opened_s"]["max"] or 999) <= office_convert.GATE["open_during_s"]
            asked += k["health_n"]
            worst = max(worst, k["health_ms_max"] or 0)
        not_true = sum(
            (s.get(o) or {}).get("health_not_true", 0) for o in ("conversie eerst", "editor eerst")
        )
        parts.append(f"/healthcheck {asked}× gevraagd, {not_true}× niet true, traagste {worst} ms")
        add(
            "Conversie van 401 pagina's terwijl een editor opent",
            "openen ≤ 10 s; /healthcheck blijft true",
            "; ".join(parts),
            ok,
            f"office_convert.py, {cv['runs']}× per volgorde: ‘conversie eerst’ = upload van de"
            " docx van 401 pagina's, wachten tot DS het bron ophaalt, 2 s later een proef.docx"
            " openen; ‘editor eerst’ = openen en uploaden zodra de sessie er is; /healthcheck"
            " elke 0,5 s",
        )
    else:
        add(
            "Conversie van 401 pagina's terwijl een editor opent",
            "openen ≤ 10 s; /healthcheck blijft true",
            "–",
            None,
            "office_convert.py: niet gemeten",
        )

    ed = results.get("office_editors")
    what = "20 bewerkers (5 documenten × 4), 60 s typen"
    goal = "alle teksten opgeslagen; cpu en RAM van DS en app vastgelegd"
    if ed and ed.get("scales"):
        parts = []
        for n, r in sorted(ed["scales"].items(), key=lambda kv: int(kv[0])):
            m = r["summary"]
            parts.append(
                f"{n} ({r['docs']} × {r['per_doc']}): {sum(m['saved'])}/{sum(m['typed'])} woorden"
                f" opgeslagen ({m['rounds']} rondes), DS {_n(m['ds_cores_typing']['median'])}"
                f" cores (piek {_n(m['ds_cores_typing_max']['max'])}),"
                f" DS {_n(m['ds_rss_mb_max'], 0)} MB,"
                f" app {_n(m['app_cores_typing']['median'])} cores en"
                f" {_n(m['app_rss_mb_max'], 0)} MB, browser {_n(m['browser_pss_mb_max'], 0)} MB,"
                f" minst vrij {_n(m['mem_available_mb_min'], 0)} MB"
            )
        top = ed["scales"].get("20")
        ok = None if not top else top["summary"]["all_saved"] and not top["summary"]["low_memory"]
        if ed.get("stopped"):
            parts.append(f"gestopt met opschalen: {ed['stopped']}")
        add(
            what,
            goal,
            "; ".join(parts),
            ok,
            "office_editors.py, opgeschaald 2 → 8 → 20 bewerkers, elk 3 rondes (nieuwe"
            " documenten van ±150 kB XML), één Chromium met een context per bewerker, elk typt"
            " eigen woorden in een eigen alinea (110 ms per toets, 0,8–1,6 s pauze), daarna"
            " Sluiten; opgeslagen = het woord staat in de docx na status 2; cpu: de cgroup van DS"
            " en utime + stime van de app, elke seconde; RAM: rss van de cgroup van DS, VmRSS van"
            " de app, PSS van de browser",
        )
    else:
        add(what, goal, "–", None, "office_editors.py: niet gemeten")

    st = results.get("office_outage_stop")
    what, goal = "Stoppen met open editors", "alles opgeslagen; docker stop ≤ 60 s"
    if st:
        m = st["summary"]
        add(
            what,
            goal,
            f"docker stop {_spread(m['stop_s'], ' s', 1)}, {m['killed_after_grace']}× gekild"
            f" (137); opgeslagen meteen na de stop {sum(m['saved_right_after'])}/{sum(m['typed'])}"
            f" woorden, na de herstart {sum(m['saved_after_restart'])}/{sum(m['typed'])};"
            f" status 2 kwam {_spread(m['callback_after_stop_s'], ' s', 1)} na het begin van de"
            " stop",
            m["all_saved"]
            and not m["killed_after_grace"]
            and (m["stop_s"]["max"] or 999) <= office_outage.GATE["stop_s"],
            f"office_outage.py --mode stop, {m['rounds']} rondes: {st['docs']} documenten ×"
            f" {st['per_doc']} bewerkers typen 30 s, 3 s later `docker stop -t 60` op de"
            " container van DS (zoals compose met stop_grace_period 60 s); daarna de inhoud in de"
            " app, DS weer starten, 90 s wachten en nog eens",
        )
    else:
        add(what, goal, "–", None, "office_outage.py --mode stop: niet gemeten")

    kl = results.get("office_outage_kill")
    what = "Kill van DS met open editors"
    goal = "verlies ≤ de tijd sinds het laatste tussentijds opslaan (≤ 5 min)"
    if kl:
        m = kl["summary"]
        windows = [w for w in m["loss_window_s"] if w is not None]
        add(
            what,
            goal,
            f"van vóór de forcesave van de klok bewaard: {m['before_saved']}/"
            f"{m['before_checkpoint']} woorden; van erna {m['after_saved']}/"
            f"{m['after_checkpoint']} (weg: {m['lost']}; na de herstart in totaal"
            f" {m.get('saved_after_restart')}/{m.get('typed')}); verloren venster"
            f" {', '.join(_n(w, 0) for w in m['loss_window_s'])} s; de klok sloeg"
            f" {_spread(m['checkpoint_after_open_s'], ' s', 0)} na het openen",
            m["before_saved"] == m["before_checkpoint"]
            and bool(windows)
            and max(windows) <= office_outage.GATE["loss_s"],
            f"office_outage.py --mode kill, {m['rounds']} rondes: {kl['docs']} documenten ×"
            f" {kl['per_doc']} bewerkers typen tot de klok (checkpoint_minutes 5) een forcesave"
            " (status 6, timer) gaf, dan nog 30, 150 of 285 s, dan `docker kill`; daarna DS"
            " starten en 150 s wachten; verloren venster = van de forcesave tot het laatst"
            " getypte woord dat weg is",
        )
    else:
        add(what, goal, "–", None, "office_outage.py --mode kill: niet gemeten")
    return out


def conditions(results: dict[str, Any]) -> list[str]:
    m = next((r["machine"] for r in results.values() if r.get("machine", {}).get("ds")), None)
    m = m or next((r["machine"] for r in results.values() if r.get("machine")), None)
    if not m:
        return []
    lines = [
        f"- Machine: {m['cpus']} cpu's ({m.get('cpu_model') or '?'}),"
        f" {_n((m['mem_total_bytes'] or 0) / 1024**3, 1)} GiB RAM, kernel {m['kernel']},"
        f" Python {m['python']}",
    ]
    if m.get("chromium"):
        lines.append(f"- Chromium {m['chromium']} (Playwright, headless)")
    ds = m.get("ds") or {}
    if ds.get("name"):
        lines.append(
            f"- DS: container `{ds['name']}` ({ds.get('image')}),"
            f" geheugen {_n((ds.get('memory_bytes') or 0) / 1024**3, 1)} GiB,"
            f" {_n(ds.get('cpus'), 1)} cpu's, pids {ds.get('pids_limit')};"
            f" grenzen toegepast: {'ja' if ds.get('limits_applied') else 'nee'}"
        )
    return lines


def open_phases(op: dict[str, Any]) -> list[str]:
    """De tabel "waar de tijd van openen zit": medianen per soort, koud en warm, via de app en
    rechtstreeks bij DS (de controle van office_open.py)."""
    lines = [
        "Waar de tijd van openen zit (medianen in s; DS = van het verzoek om api.js tot klaar):",
        "",
        "| Soort | | Via de app | Schil | POST …/session | DS via de app | Rechtstreeks: totaal"
        " | Rechtstreeks: DS | Verzoeken bij de app | Over het net |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for kind, k in op["kinds"].items():
        direct = k.get("direct") or {}
        for key, label in (("cold", "koud"), ("warm", "warm")):
            a, d = k[key], direct.get(key) or {}
            ph = a.get("phases_s") or {}

            def med(s: dict[str, Any] | None) -> str:
                return _n((s or {}).get("median"))

            lines.append(
                f"| {kind} | {label} | {med(a['ready_s'])} | {med(ph.get('shell'))}"
                f" | {med(ph.get('session'))} | {med(ph.get('ds'))} | {med(d.get('ready_s'))}"
                f" | {med(d.get('ds_s'))} | {_n(a['requests_app']['median'], 0)}"
                f" | {_n((a['bytes_network']['median'] or 0) / 1e6, 2)} MB |"
            )
    return lines


def block(report: dict[str, Any]) -> str:
    lines = [
        "<!-- gemeten -->",
        "Gemaakt door `e2e/perf/office_run.py`; niet met de hand bewerken. Alle getallen:",
        "`metingen.json`.",
        "",
    ]
    for tool, meta in report["runs"].items():
        lines.append(f"- {tool}: {meta['generated']}, commit `{meta['commit']}`")
    lines += ["", *conditions(report["results"]), ""]
    lines += ["| Wat | Doel | Gemeten | Gehaald | Hoe |", "|---|---|---|---|---|"]
    for r in report["rows"]:
        lines.append(f"| {r['what']} | {r['goal']} | {r['result']} | {r['met']} | {r['how']} |")
    op = report["results"].get("office_open")
    if op:
        lines += ["", *open_phases(op)]
    if report["errors"]:
        lines += ["", "Wat faalt:", ""] + [f"- {e}" for e in report["errors"]]
    lines.append("<!-- /gemeten -->")
    return "\n".join(lines)


# Wat metingen.json per opening niet bewaart (de volledige uitslag: E2E_PERF_OUT)
HEAVY = {"before_session", "internal_paths", "statuses"}


def compact(value: Any, key: str = "") -> Any:
    """Een kleinere kopie voor docs/fase-4/metingen.json: zonder de lange lijsten per opening,
    de grootste stukken tot 3, van een lijst rustmetingen alleen het aantal en de drukste, en van
    een losse rustmeting alleen de belasting (niet welke processen)."""
    if key == "quiet" and isinstance(value, list):
        cores = [q.get("cores") or 0 for q in value if isinstance(q, dict)]
        return {
            "n": len(cores),
            "max_cores": max(cores, default=None),
            "all_quiet": all(q.get("quiet", True) for q in value if isinstance(q, dict)),
        }
    if key == "quiet" and isinstance(value, dict):
        # Zonder de drukste processen: die staan in de volle uitslag van het script zelf
        return {k: v for k, v in value.items() if k != "top"}
    if key == "biggest" and isinstance(value, list):
        return value[:3]
    if isinstance(value, dict):
        return {k: compact(v, k) for k, v in value.items() if k not in HEAVY}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def run_tool(tool: str, args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    if tool == "office_open":
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
        result = office_open.run(args.runs, kinds)
        return result, office_open.check(result)
    url, secret = ob.real_ds() or ("", "")
    if tool == "office_tunnels":
        result = office_tunnels.measure(url, secret, args.rounds)
        return result, office_tunnels.check(result)
    if tool == "office_save":
        result = office_save.measure(url, secret, args.rounds)
        return result, office_save.check(result)
    if tool == "office_inspect":
        result = office_inspect.run(args.runs)
        return result, office_inspect.check(result)
    if tool == "office_budget":
        result = office_budget.measure()
        return result, office_budget.check(result)
    if tool == "office_xml":
        result = office_xml.measure(url, secret, args.rounds)
        return result, office_xml.check(result)
    if tool == "office_convert":
        result = office_convert.measure(url, secret, args.rounds, args.runs)
        return result, office_convert.check(result)
    if tool == "office_editors":
        return editors(url, secret, args)
    mode = tool.rsplit("_", 1)[1]
    result = office_outage.measure(url, secret, mode, args.rounds)
    return result, office_outage.check(result)


def scale(n: int) -> tuple[int, int]:
    """Het aantal bewerkers als documenten × bewerkers per document (§4.7: 20 = 5 × 4)."""
    per_doc = 2 if n <= 2 else 4
    return max(1, n // per_doc), per_doc


def editors(url: str, secret: str, args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """office_editors.py opgeschaald (2 → 8 → 20): stopt zodra een stap te weinig geheugen vrij
    had of niet iedereen open kreeg; de hoogste stap die wel lukte, staat in "highest_ok"."""
    result: dict[str, Any] = {"tool": "office_editors", "scales": {}, "stopped": None}
    errors: list[str] = []
    for n in [int(x) for x in args.scale.split(",") if x.strip()]:
        docs, per_doc = scale(n)
        r = office_editors.measure(url, secret, docs, per_doc, rounds=args.rounds)
        write_result(f"office_editors_{n}", r)
        result["scales"][str(n)] = r
        found = office_editors.check(r)
        errors += [f"{n} bewerkers: {e}" for e in found]
        result["machine"] = r.get("machine")
        if r["summary"]["low_memory"] or any(
            x["opened"] != x["editors"] for x in r["round_results"]
        ):
            result["stopped"] = f"bij {n} bewerkers (te weinig geheugen vrij of niet alles open)"
            break
        result["highest_ok"] = n
    return result, errors


def imported(tool: str, path: Path, results: dict[str, Any]) -> dict[str, Any]:
    """De JSON van een los gedraaid script overnemen; office_editors_<n> gaat onder
    office_editors.scales."""
    data = json.loads(path.read_text())
    if tool.startswith("office_editors_"):
        n = tool.rsplit("_", 1)[1]
        merged = dict(results.get("office_editors") or {"tool": "office_editors", "scales": {}})
        merged["scales"] = {**merged.get("scales", {}), n: data}
        merged["machine"] = data.get("machine") or merged.get("machine")
        ok = [int(k) for k, v in merged["scales"].items() if not v["summary"]["low_memory"]]
        merged["highest_ok"] = max(ok, default=None)
        return merged
    return data


def found_in(tool: str, result: dict[str, Any]) -> list[str]:
    checks = {
        "office_open": office_open.check,
        "office_tunnels": office_tunnels.check,
        "office_save": office_save.check,
        "office_inspect": office_inspect.check,
        "office_budget": office_budget.check,
        "office_xml": office_xml.check,
        "office_convert": office_convert.check,
        "office_outage_stop": office_outage.check,
        "office_outage_kill": office_outage.check,
    }
    if tool == "office_editors":
        return [
            f"{n} bewerkers: {e}"
            for n, r in result.get("scales", {}).items()
            for e in office_editors.check(r)
        ]
    return checks[tool](result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="De metingen van office (§4.7)")
    parser.add_argument(
        "--only", help="bijv. office_open,office_save (standaard alles, met --import niets)"
    )
    parser.add_argument(
        "--runs", type=int, default=5, help="office_open, office_inspect, klein van office_convert"
    )
    parser.add_argument("--rounds", type=int, default=3, help="rondes van de andere metingen")
    parser.add_argument(
        "--scale", default=",".join(map(str, SCALES)), help="office_editors: aantal bewerkers"
    )
    parser.add_argument(
        "--import",
        dest="imports",
        action="append",
        default=[],
        metavar="TOOL=PAD",
        help="de JSON van een los gedraaid script overnemen (office_editors_<n> voor één stap)",
    )
    parser.add_argument("--kinds", default=",".join(office_open.KINDS))
    parser.add_argument("--out-dir", type=Path, default=DOCS)
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="niets meten: alleen het blok in metingen.md opnieuw schrijven uit metingen.json",
    )
    args = parser.parse_args(argv)
    only = args.only if args.only is not None else ("" if args.imports else ",".join(TOOLS))
    tools = [] if args.rewrite else [t.strip() for t in only.split(",") if t.strip()]
    unknown = set(tools) - set(TOOLS)
    if unknown:
        parser.error(f"onbekende metingen: {', '.join(sorted(unknown))}")
    pairs = []
    for item in args.imports:
        tool, _, path = item.partition("=")
        if not path or (tool not in TOOLS and not re.fullmatch(r"office_editors_\d+", tool)):
            parser.error(f"--import {item}: verwacht TOOL=PAD met een bekende meting")
        pairs.append((tool, Path(path)))

    json_path = args.out_dir / "metingen.json"
    old = json.loads(json_path.read_text()) if json_path.exists() else {}
    results: dict[str, Any] = dict(old.get("results") or {})
    runs: dict[str, Any] = dict(old.get("runs") or {})
    errors: dict[str, list[str]] = dict(old.get("errors_by_tool") or {})
    commit = _git("rev-parse", "--short", "HEAD")
    for tool in tools:
        if tool in NEEDS_DS and ob.real_ds() is None:
            print(f"{tool}: niet gemeten (geen echte DS)", file=sys.stderr)
            continue
        started = time.monotonic()
        result, found = run_tool(tool, args)
        write_result(tool, result)
        results[tool] = result
        errors[tool] = found
        runs[tool] = {
            "generated": datetime.now(UTC).isoformat(timespec="seconds"),
            "commit": commit,
            "dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
            "seconds": round(time.monotonic() - started, 1),
        }
        print(f"{tool}: {'goed' if not found else f'{len(found)} fout(en)'}", file=sys.stderr)
    for tool, path in pairs:
        result = imported(tool, path, results)
        tool = "office_editors" if tool.startswith("office_editors_") else tool
        results[tool] = result
        errors[tool] = found_in(tool, result)
        stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
        runs[tool] = {"generated": stamp, "commit": commit, "imported": path.name}
        print(f"{tool}: overgenomen uit {path}", file=sys.stderr)

    flat = [f"{tool}: {e}" for tool in TOOLS for e in errors.get(tool, [])]
    report = {
        "tool": "office_run",
        "report_only": report_only(),
        "runs": {t: runs[t] for t in TOOLS if t in runs},
        "rows": rows(results),
        "errors": flat,
        "errors_by_tool": errors,
        "ok": not flat,
        "results": {t: compact(results[t]) for t in TOOLS if t in results},
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    md_path = args.out_dir / "metingen.md"
    text = md_path.read_text() if md_path.exists() else SKELETON
    if not BLOCK.search(text):
        text = text.rstrip("\n") + "\n\n" + SKELETON.split("\n", 2)[2]
    md_path.write_text(BLOCK.sub(lambda _m: block(report), text, count=1))
    for e in flat:
        print(f"FOUT {e}", file=sys.stderr)
    print(f"Geschreven: {json_path} en {md_path}", file=sys.stderr)
    return 1 if flat else 0


if __name__ == "__main__":
    sys.exit(main())
