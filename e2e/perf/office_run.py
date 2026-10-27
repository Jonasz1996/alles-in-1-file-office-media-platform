"""De metingen van office (fase 4, §4.7 deel 1) in één keer, zoals run_all.py voor fase 3.

Na elkaar, elk met een eigen server (de timers van productie) en nooit twee tegelijk:
- office_open.py: koud en warm openen via de app, en de cpu van de app bij koud openen;
- office_tunnels.py: het geheugen van de app met 50 open tunnels;
- office_save.py: opslaan, van de callback tot de versie in de database (1 MiB);
- office_inspect.py: de invoercontrole op een xlsx met 10 MB XML en een pptx met 80 MB foto's;
- office_budget.py: het groottebudget tegen 3ccee0a (eerste lading, editor, Bestanden).
De eerste drie hebben een echte DS nodig (E2E_REAL_DS en E2E_REAL_DS_SECRET_FILE, zie
docs/fase-4/e2e.md); zonder DS staan ze als "niet gemeten".

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
import office_inspect  # noqa: E402
import office_open  # noqa: E402
import office_save  # noqa: E402
import office_tunnels  # noqa: E402

from conftest import report_only, write_result  # noqa: E402

ROOT = HERE.parents[1]
DOCS = ROOT / "docs" / "fase-4"
TOOLS = ("office_open", "office_tunnels", "office_save", "office_inspect", "office_budget")
NEEDS_DS = {"office_open", "office_tunnels", "office_save"}
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
    result = office_budget.measure()
    return result, office_budget.check(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="De metingen van office (§4.7 deel 1)")
    parser.add_argument("--only", default=",".join(TOOLS), help="bijv. office_open,office_save")
    parser.add_argument("--runs", type=int, default=5, help="office_open en office_inspect")
    parser.add_argument("--rounds", type=int, default=3, help="office_tunnels en office_save")
    parser.add_argument("--kinds", default=",".join(office_open.KINDS))
    parser.add_argument("--out-dir", type=Path, default=DOCS)
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="niets meten: alleen het blok in metingen.md opnieuw schrijven uit metingen.json",
    )
    args = parser.parse_args(argv)
    tools = [] if args.rewrite else [t.strip() for t in args.only.split(",") if t.strip()]
    unknown = set(tools) - set(TOOLS)
    if unknown:
        parser.error(f"onbekende metingen: {', '.join(sorted(unknown))}")

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
