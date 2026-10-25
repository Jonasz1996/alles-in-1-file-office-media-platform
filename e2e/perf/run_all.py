"""Alle prestatiepoorten van ontwerp §4 in één keer (P1).

Eén eigen server (python -m app.serve met de productie-build uit web/dist), één keer gevuld:
een map met 10.000 bestanden over 2.000 thumbnails, een publieke link, en 50.000 foto's voor
de tijdlijn. Daarop:
- first_view.py: eerste weergave < 1,5 s op het LAN (§4.2);
- scroll.py: 10.000 bestanden scrollen soepel (§4.4);
- lighthouse_like.py: Lighthouse-achtige score ≥ 91 (§4.3).
De grootte (npm run budget, §4.1) hoort bij de build en draait in de web-job.

Uitvoer: docs/fase-3/prestaties.json (alles) en docs/fase-3/prestaties.md (de samenvatting).
In prestaties.md blijft het blok tussen <!-- handmatig --> en <!-- /handmatig --> staan: daar
noteert de integrator de echte DevTools-Lighthouse-runs (de harde acceptatie).

Met E2E_PERF_REPORT_ONLY=1 (CI) faalt geen poort; anders is de exitcode 1 als er een faalt.
Met E2E_PERF_OUT=<map> schrijft elk script ook zijn eigen JSON daarheen.

    cd api && uv run --with playwright python ../e2e/perf/run_all.py
    cd api && uv run --with playwright python ../e2e/perf/run_all.py --only lighthouse_like
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

import first_view  # noqa: E402 - pas na het pad naar e2e/perf
import lighthouse_like  # noqa: E402
import scroll  # noqa: E402

from conftest import prepare_perf, report_only, standalone, write_result  # noqa: E402

ROOT = HERE.parents[1]
DOCS = ROOT / "docs" / "fase-3"
TOOLS = ("first_view", "scroll", "lighthouse_like")
MANUAL = re.compile(r"<!-- handmatig -->.*?<!-- /handmatig -->", re.S)
MANUAL_DEFAULT = """<!-- handmatig -->
Nog niet gedaan. Bij de integratie: Chrome DevTools, Lighthouse, navigatie, alleen
Performance, hetzelfde profiel (mobiel voor / en /s/<link>, desktop voor /map/<10k>), één run
per pagina tegen de integratieserver. Elke score moet > 90 zijn; wijkt hij meer dan 3 punten
af van lighthouse_like.py, dan herijken we de meting vóór fase 3 sluit.

| Pagina | Profiel | DevTools-score | lighthouse_like | Datum | Door |
|---|---|---|---|---|---|
| / | mobiel | | | | |
| /s/<link> | mobiel | | | | |
| /map/<10k> | desktop | | | | |
<!-- /handmatig -->"""


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _fmt(value: Any, unit: str = "") -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        value = round(value, 3) if value < 1 else round(value)
    return f"{value}{unit}"


def summary_md(report: dict[str, Any], manual: str) -> str:
    lines = [
        "# Prestaties fase 3",
        "",
        "Gemaakt door `e2e/perf/run_all.py` (P1); niet met de hand bewerken, behalve het blok",
        "met de DevTools-runs onderaan. Alle getallen: `prestaties.json`.",
        "",
        f"- Gemeten: {report['generated']}",
        f"- Commit: `{report['commit'] or 'onbekend'}`",
        f"- Alleen rapporteren (E2E_PERF_REPORT_ONLY): {'ja' if report['report_only'] else 'nee'}",
        f"- Data: {report['data']['n']} bestanden over {report['data']['blobs']} thumbnails,"
        f" {report['data']['photos']} foto's",
        "",
    ]
    lh = report["results"].get("lighthouse_like")
    if lh:
        lines += [
            "## Lighthouse-achtig (§4.3)",
            "",
            f"Poort: score ≥ {lh['gate']}, mediaan van {lh['runs']}.",
            "",
            "| Pagina | Profiel | Van | Status | Score | Runs | FCP | SI | LCP | TBT | CLS |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for name, p in lh["pages"].items():
            m = p["metrics"]
            lines.append(
                f"| {name} (`{_short(p['url'])}`) | {p['profile']} | {p['owner']} | {p['status']}"
                f" | {_fmt(p['score'])} | {', '.join(_fmt(s) for s in p['scores'])}"
                f" | {_fmt(m['fcp'], ' ms')} | {_fmt(m['si'], ' ms')} | {_fmt(m['lcp'], ' ms')}"
                f" | {_fmt(m['tbt'], ' ms')} | {_fmt(m['cls'])} |"
            )
        lines.append("")
    fv = report["results"].get("first_view")
    if fv:
        lines += [
            "## Eerste weergave (§4.2)",
            "",
            f"Poort: mediaan < {fv['gate']['median_ms']} ms en p95 < {fv['gate']['p95_ms']} ms,"
            " koud en warm.",
            "",
            "| Pagina | Van | Status | Koud mediaan / p95 | Warm mediaan / p95 | Bron | SW |",
            "|---|---|---|---|---|---|---|",
        ]
        for name, p in fv["pages"].items():
            c, w = p["cold"], p["warm"]
            lines.append(
                f"| {name} | {p['owner']} | {p['status']}"
                f" | {_fmt(c['median_ms'])} / {_fmt(c['p95_ms'])} ms"
                f" | {_fmt(w['median_ms'])} / {_fmt(w['p95_ms'])} ms"
                f" | {', '.join(p['sources'])} | {'ja' if p['service_worker'] else 'nee'} |"
            )
        lines.append("")
    sc = report["results"].get("scroll")
    if sc:
        lines += [
            "## Scrollen (§4.4)",
            "",
            "| Weergave | CPU | Status | Mislukte controles |",
            "|---|---|---|---|",
        ]
        for v in sc["views"]:
            bad = {k: c["value"] for k, c in v.get("checks", {}).items() if not c["ok"]}
            lines.append(f"| {v['mode']} | {v['cpu']}× | {v['status']} | {_fmt(bad or None)} |")
        lines.append("")
    if report["errors"]:
        lines += ["## Wat faalt", ""] + [f"- {e}" for e in report["errors"]] + [""]
    lines += ["## DevTools-Lighthouse (harde acceptatie, handmatig)", "", manual, ""]
    return "\n".join(lines)


def _short(url: str) -> str:
    return url if len(url) <= 24 else url[:20] + "…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alle prestatiepoorten (ontwerp §4)")
    parser.add_argument("--only", default=",".join(TOOLS), help="bijv. first_view,lighthouse_like")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--n", type=int, default=10_000)
    parser.add_argument("--photos", type=int, default=scroll.TIMELINE_PHOTOS)
    parser.add_argument("--out-dir", type=Path, default=DOCS)
    args = parser.parse_args(argv)
    tools = [t.strip() for t in args.only.split(",") if t.strip()]
    unknown = set(tools) - set(TOOLS)
    if unknown:
        parser.error(f"onbekende metingen: {', '.join(sorted(unknown))}")

    results: dict[str, Any] = {}
    errors: list[str] = []
    timings: dict[str, float] = {}
    photos = args.photos if "scroll" in tools else 0
    with standalone() as (server, admin, browsers):
        started = time.monotonic()
        data = prepare_perf(server, admin, n=args.n)
        timeline = scroll.prepare_timeline(server, admin, data.blobs, photos) if photos else None
        timings["seed_s"] = round(time.monotonic() - started, 1)
        for tool in tools:
            started = time.monotonic()
            if tool == "first_view":
                result = first_view.measure(server, browsers, data, runs=args.runs)
                found = first_view.check(result)
            elif tool == "scroll":
                result = scroll.measure(server, browsers, data, timeline, photos=photos)
                found = scroll.check(result)
            else:
                result = lighthouse_like.measure(server, browsers, data, runs=args.runs)
                found = lighthouse_like.check(result)
            timings[f"{tool}_s"] = round(time.monotonic() - started, 1)
            write_result(tool, result)
            results[tool] = result
            errors += [f"{tool}: {e}" for e in found]
            print(f"{tool}: {'goed' if not found else f'{len(found)} fout(en)'}", file=sys.stderr)

    report = {
        "tool": "run_all",
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": _git("rev-parse", "--short", "HEAD"),
        "dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "report_only": report_only(),
        "data": {"n": data.n, "blobs": len(data.blobs), "photos": photos},
        "timings": timings,
        "errors": errors,
        "ok": not errors,
        "results": results,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "prestaties.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    md_path = args.out_dir / "prestaties.md"
    old = md_path.read_text() if md_path.exists() else ""
    found_manual = MANUAL.search(old)
    manual = found_manual.group(0) if found_manual else MANUAL_DEFAULT
    md_path.write_text(summary_md(report, manual))
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    print(f"Geschreven: {args.out_dir / 'prestaties.json'} en {md_path}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
