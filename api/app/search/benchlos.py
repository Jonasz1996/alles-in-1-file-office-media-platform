"""python -m app.cli bench --los: hoe snel zoeken is op déze machine, met de uitkomst op /beheer
(bouwontwerp fase 6, Keuze 54; Z7).

De bench zelf is tools/zoekbench/bench.py (spike S9): alleen de standaardbibliotheek, met een
bevroren kopie van het schema van 0010 en een eigen boom. Het image neemt hem mee in
/srv/tools/zoekbench/ (de regel in de Dockerfile); met uv run staat hij naast api/. Dit
commando laadt hem, laat hem zijn boom van 100.000 maken in <data>/cache (±1,1 GB even, daarna
weg, ook bij een fout of Ctrl-C), en schrijft daarna het korte deel van de uitkomst naar
<data>/cache/zoekbench.json: eerst naar een .part, fsync, dan rename. Dat bestand leest /beheer
(app/search/admin.py, bench_result). Er staat niets geheims in: tijden, aantallen en de versies
van SQLite en Python, nooit een vraag, een naam of een pad.

    docker compose exec -T platform python -m app.cli bench --los [--n 100000] [--seed 6]

Exitcode 0 met de uitkomst; 1 als de bench er niet is, er te weinig ruimte is of de bench
faalde (dan blijft de vorige uitkomst staan); 3 als deze SQLite geen FTS5 kent (dan zoekt de app
met LIKE, Keuze 10).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config import Settings

COMMAND = "bench"
HELP = "zoeken meten op deze machine, met de uitkomst op /beheer (Keuze 54)"
CACHE = Path("cache")  # onder data_dir, zoals in de bench zelf (/data/cache)
RESULT = CACHE / "zoekbench.json"
VERSION = 1
# De piek van de bench op schijf: de database met zijn WAL, ±1,1 GB bij 100.000 (spike S9);
# met marge, en altijd een reserve voor de app zelf
BYTES_PER_FILE = 12_000
RESERVE = 512 * 1024 * 1024
# Wat er uit de uitkomst van de bench naar /beheer gaat, per bereik ("eigenaar" en "alles")
KEEP = ("p50", "p95", "max", "vragen", "wortels", "zichtbaar", "ronde B", "ronde C")


def bench_file() -> Path | None:
    """tools/zoekbench/bench.py: in het image /srv/tools (naast /srv/app), met uv run naast
    api/ in de repo."""
    here = Path(__file__).resolve()
    for root in (here.parents[2], here.parents[3]):
        path = root / "tools" / "zoekbench" / "bench.py"
        if path.is_file():
            return path
    return None


def load(path: Path) -> ModuleType:
    """De bench als module, zonder hem in sys.modules te zetten."""
    spec = importlib.util.spec_from_file_location("zoekbench_los", path)
    if spec is None or spec.loader is None:
        raise ImportError(str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def needed(n: int) -> int:
    """Hoeveel bytes de bench vrij wil hebben voor n bestanden."""
    return n * BYTES_PER_FILE + RESERVE


def summary(found: dict[str, Any]) -> dict[str, Any]:
    """Het korte deel van de uitkomst van de bench, voor /beheer. Geen pad (de tijdelijke map
    staat in found["map"]) en geen tekst van de bench zelf."""
    ranges = {}
    for name in ("eigenaar", "alles"):
        z = found["zoeken"][name]
        ranges[name] = {key: z[key] for key in KEEP if key in z}
    return {
        "versie": VERSION,
        "sqlite": found.get("sqlite"),
        "python": found.get("python"),
        "cores": os.cpu_count(),
        "n": found.get("n"),
        "seed": found.get("seed"),
        "nodes": found.get("bouw", {}).get("nodes"),
        "duur s": found.get("duur s"),
        "opgeruimd": found.get("opgeruimd"),
        "zoeken": ranges,
    }


def write(path: Path, data: dict[str, Any]) -> None:
    """Atomair: een .part in dezelfde map, fsync, rename, en de map ook. Een uitkomst die
    halverwege stopt, laat de vorige staan."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(part, 0o640)
        os.replace(part, path)
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink()
        raise
    with contextlib.suppress(OSError):
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)


def parsers(commands: argparse._SubParsersAction) -> None:
    bench = commands.add_parser(COMMAND, help=HELP)
    bench.add_argument(
        "--los", action="store_true", help="in een tijdelijke map onder de data, en daarna weg"
    )
    bench.add_argument("--n", type=int, default=100_000, help="aantal bestanden")
    bench.add_argument("--seed", type=int, default=6)


def run(settings: Settings, args: argparse.Namespace) -> int:
    if not args.los:
        print("Alleen bench --los bestaat: een eigen boom in een tijdelijke map.", file=sys.stderr)
        return 1
    if not 100 <= args.n <= 1_000_000:
        print("--n tussen 100 en 1.000.000.", file=sys.stderr)
        return 1
    source = bench_file()
    if source is None:
        print("tools/zoekbench/bench.py staat niet in deze installatie.", file=sys.stderr)
        return 1
    cache = Path(settings.data_dir) / CACHE
    cache.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(cache).free
    if free < needed(args.n):
        print(
            f"Te weinig vrije ruimte in {cache}: {free // 1_000_000} MB, de bench wil er "
            f"{needed(args.n) // 1_000_000}.",
            file=sys.stderr,
        )
        return 1
    bench = load(source)
    with tempfile.TemporaryDirectory(prefix="zoekbench-uit-", dir=cache) as tmp:
        out = Path(tmp) / "uitkomst.json"
        argv = ["--los", "--n", str(args.n), "--seed", str(args.seed), "--map", str(cache)]
        rc = bench.main([*argv, "--json", str(out)])
        if rc != 0:
            return rc
        found = json.loads(out.read_text(encoding="utf-8"))
    target = Path(settings.data_dir) / RESULT
    write(target, summary(found))
    eigenaar, alles = found["zoeken"]["eigenaar"]["p95"], found["zoeken"]["alles"]["p95"]
    print(
        f"De uitkomst staat op /beheer, Zoeken en foto's (p95 eigenaar {eigenaar} ms, "
        f"alles {alles} ms; {target})."
    )
    return 0
