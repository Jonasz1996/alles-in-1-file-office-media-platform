"""Het groottebudget van de web-app met office erbij, tegen het einde van fase 3 (§4.7).

Doelen (docs/fase-4/bouwontwerp.md §4.7), in bytes gzip zoals web/scripts/budget.mjs telt:
- de eerste lading (JS) groeit ≤ 0,5 KB ten opzichte van 3ccee0a (93,8 van de 100 KB);
- het chunk van de editor (src/office/EditorPage.tsx) is < 50 KB;
- de route Bestanden groeit < 1 KB.

Methode
- Basis: een git worktree van BASE (3ccee0a, het einde van fase 3) in een tijdelijke map. Zijn
  node_modules is die van web/ als harde links (cp -al; package-lock.json is in beide gelijk,
  dat controleert het script), zonder node_modules/.tmp en .vite, zodat de build daar nieuwe
  bestanden schrijft en nooit in die van web/. Dan `npm run build` en
  `node scripts/budget.mjs --json` in de worktree.
- Nu: `npm run build` en `node scripts/budget.mjs --json` in web/ zelf (de werkmap; het script
  noemt de commit en of web/ wijzigingen heeft die nog niet gecommit zijn).
- Het chunk van de editor komt uit de chunkgraaf die vite.config.ts bij de build schrijft
  (node_modules/.tmp/platform-chunks.json): het chunk met facade src/office/EditorPage.tsx,
  gzip niveau 9 zoals budget.mjs. Ter informatie ook wat de editor daarnaast nog laadt als je
  van de route Bestanden komt (zijn statische imports, het woordenboek office, de CSS).
- Een build is deterministisch; herhalen geeft dezelfde bytes. Het script bouwt elke kant één
  keer.

Los draaien (JSON op stdout; duurt ±1 minuut):
    cd api && uv run python ../e2e/perf/office_budget.py
Via pytest (marker perf):
    cd api && uv run --with playwright pytest ../e2e/perf/office_budget.py -s
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import office_bench as ob  # noqa: E402 - pas na het pad naar e2e/perf

from conftest import report_only, write_result  # noqa: E402

pytestmark = pytest.mark.perf

REPO = HERE.parent.parent
WEB = REPO / "web"
BASE = "3ccee0a"
KB = 1000
GATE = {"initial_growth_kb": 0.5, "editor_chunk_kb": 50.0, "files_growth_kb": 1.0}
GRAPH = "node_modules/.tmp/platform-chunks.json"
EDITOR = "src/office/EditorPage.tsx"
ROWS = {"initial": "eerste lading JS", "css": "eerste lading CSS", "files": "route Bestanden"}


def _run(cmd: list[str], cwd: Path, timeout: float = 600, ok: tuple[int, ...] = (0,)) -> str:
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if done.returncode not in ok:
        tail = (done.stdout + done.stderr)[-2000:]
        raise RuntimeError(f"{' '.join(cmd)} in {cwd} gaf {done.returncode}:\n{tail}")
    return done.stdout


def _git(*args: str) -> str:
    return _run(["git", *args], REPO).strip()


def _gz(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9, mtime=0))


def closure(chunks: dict[str, Any], roots: list[str]) -> set[str]:
    """Zoals closure() in budget.mjs: alle chunks die vanaf roots statisch geladen worden."""
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        f = stack.pop()
        if f in seen or f not in chunks:
            continue
        seen.add(f)
        stack.extend(chunks[f]["imports"])
    return seen


def html_roots(html: str) -> list[str]:
    """De module-scripts en modulepreloads van index.html (zoals htmlRoots in budget.mjs)."""
    import re

    out = []
    for tag in re.findall(r"<(?:script|link)\b[^>]*>", html, flags=re.I):
        src = re.search(r'\b(?:src|href)="/?([^"]+)"', tag, flags=re.I)
        if not src or not src.group(1).startswith("assets/"):
            continue
        if (tag.lower().startswith("<script") and 'type="module"' in tag) or (
            'rel="modulepreload"' in tag
        ):
            out.append(src.group(1))
    return out


def editor(web: Path) -> dict[str, Any] | None:
    """Het chunk van de editor en wat hij daarnaast laadt (None vóór office)."""
    chunks = json.loads((web / GRAPH).read_text())["chunks"]
    dist = web / "dist"
    found = [f for f, c in chunks.items() if c.get("facade") == EDITOR]
    if not found:
        return None
    chunk = found[0]
    initial = closure(chunks, html_roots((dist / "index.html").read_text()))
    files = [f for f, c in chunks.items() if c.get("facade") == "src/Files.tsx"]
    files_route = closure(chunks, files)
    extra = sorted(closure(chunks, [chunk]) - initial - files_route - {chunk})
    areas = [f for f, c in chunks.items() if c.get("facade") == "src/i18n/areas/office.nl.ts"]
    css = sorted(set(chunks[chunk].get("css") or []))
    return {
        "chunk": chunk,
        "chunk_bytes": _gz(dist / chunk),
        "modules": chunks[chunk]["modules"],
        "also_from_files": {f: _gz(dist / f) for f in [*extra, *areas]},
        "css": {f: _gz(dist / f) for f in css},
    }


def parts(web: Path, detail: str) -> list[dict[str, Any]]:
    """De chunks van een regel van budget.mjs (zijn detail), elk met grootte en modules."""
    chunks = json.loads((web / GRAPH).read_text())["chunks"]
    out = []
    for f in [x.strip() for x in detail.split(",") if x.strip()]:
        c = chunks.get(f) or {}
        out.append(
            {
                "file": f,
                "bytes": _gz(web / "dist" / f),
                "facade": c.get("facade"),
                "modules": [m for m in c.get("modules", []) if m.startswith("src/")],
            }
        )
    return out


def new_modules(then: dict[str, Any], now: dict[str, Any], key: str) -> dict[str, Any]:
    """Welke modules nu in een groep zitten en toen niet, en in welk chunk (met grootte)."""
    old = {m for c in then[key]["chunks"] for m in c["modules"]}
    added: dict[str, Any] = {}
    for c in now[key]["chunks"]:
        mods = [m for m in c["modules"] if m not in old]
        if mods:
            added[c["file"]] = {"bytes": c["bytes"], "new": mods, "all": len(c["modules"])}
    return added


def side(web: Path, build: bool = True) -> dict[str, Any]:
    """Bouwen (als build) en het budget van één boom."""
    if build:
        _run(["npm", "run", "build"], web)
    report = json.loads(_run(["node", "scripts/budget.mjs", "--json"], web, ok=(0, 1)))
    rows = {r["name"]: r for r in report["rows"]}
    out = {
        key: {"bytes": rows[name]["size"], "limit": rows[name]["limit"]}
        for key, name in ROWS.items()
    }
    lazy = next(r for r in report["rows"] if r["name"].startswith("grootste lazy chunk"))
    for key in ("initial", "files"):
        out[key]["chunks"] = parts(web, rows[ROWS[key]]["detail"])
    return {
        **out,
        "largest_lazy": {"bytes": lazy["size"], "file": lazy["detail"]},
        "budget_ok": report["ok"],
        "problems": report["problems"],
        "editor": editor(web),
    }


def base_tree(workdir: Path, commit: str) -> Path:
    """Een worktree van commit met de node_modules van web/ als harde links."""
    lock_now = (WEB / "package-lock.json").read_bytes()
    lock_then = subprocess.run(
        ["git", "show", f"{commit}:web/package-lock.json"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout
    if lock_now != lock_then:
        raise RuntimeError(f"package-lock.json verschilt met {commit}: de harde links kloppen niet")
    tree = workdir / "basis"
    _git("worktree", "add", "--detach", str(tree), commit)
    modules = tree / "web" / "node_modules"
    _run(["cp", "-al", str(WEB / "node_modules"), str(modules)], REPO)
    for cache in (".tmp", ".vite"):
        shutil.rmtree(modules / cache, ignore_errors=True)
    return tree


def measure(base: str = BASE, build_now: bool = True) -> dict[str, Any]:
    workdir = ob.tmpdir("office-budget-")
    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--", "web"))
    tree = None
    try:
        tree = base_tree(workdir, base)
        then = side(tree / "web")
    finally:
        if tree is not None:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(tree)], cwd=REPO, capture_output=True
            )
            _git("worktree", "prune")
        shutil.rmtree(workdir, ignore_errors=True)
    now = side(WEB, build=build_now)
    growth = {key: round((now[key]["bytes"] - then[key]["bytes"]) / KB, 2) for key in ROWS}
    result = {
        "tool": "office_budget",
        "base": base,
        "head": commit,
        "web_dirty": dirty,
        "gate": GATE,
        "then": then,
        "now": now,
        "growth_kb": growth,
        "new_modules": {key: new_modules(then, now, key) for key in ("initial", "files")},
        "machine": ob.machine(),
    }
    result["ok"] = not check(result)
    return result


def check(result: dict[str, Any]) -> list[str]:
    errors = []
    now = result["now"]
    if not now["budget_ok"]:
        errors += [f"budget.mjs: {p}" for p in now["problems"]]
    if now["editor"] is None:
        errors.append(f"geen chunk met facade {EDITOR}")
    if report_only():
        return errors
    g = result["growth_kb"]
    if g["initial"] > GATE["initial_growth_kb"]:
        errors.append(f"eerste lading +{g['initial']} KB > {GATE['initial_growth_kb']} KB")
    if g["files"] >= GATE["files_growth_kb"]:
        errors.append(f"route Bestanden +{g['files']} KB, niet < {GATE['files_growth_kb']} KB")
    if now["editor"] is not None:
        size = now["editor"]["chunk_bytes"] / KB
        if size >= GATE["editor_chunk_kb"]:
            errors.append(f"chunk van de editor {size:.1f} KB, niet < {GATE['editor_chunk_kb']} KB")
    return errors


def test_office_budget() -> None:
    result = measure()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("office_budget", result)
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Groottebudget met office tegen fase 3 (§4.7)")
    parser.add_argument("--base", default=BASE, help=f"de commit om mee te vergelijken ({BASE})")
    parser.add_argument(
        "--no-build", action="store_true", help="web/dist niet opnieuw bouwen (de basis wel)"
    )
    parser.add_argument("--out", type=Path, help="JSON ook naar dit bestand")
    args = parser.parse_args(argv)
    result = measure(args.base, build_now=not args.no_build)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    write_result("office_budget", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
