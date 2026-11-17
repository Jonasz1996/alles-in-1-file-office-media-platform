"""python -m app.cli bench --los (bouwontwerp fase 6, Keuze 54; Z7, app/search/benchlos.py).

Wat hier vastligt: het commando draait de zoekbench (tools/zoekbench/bench.py) in een tijdelijke
map onder <data>/cache, ruimt die op, en schrijft het korte deel van de uitkomst atomair naar
<data>/cache/zoekbench.json (640, zonder pad of tekst van de bench); /beheer toont daarna de
p95 voor "eigenaar" en "alles". Een bench die faalt, te weinig ruimte of een fout halverwege het
schrijven laat de vorige uitkomst staan, zonder .part. De bench zit ook in het image.
"""

from __future__ import annotations

import argparse
import json
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.search import admin as search_admin
from app.search import benchlos
from tests.test_cli_db import run_cli

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parents[3]
ADMIN = "/api/v1/admin/search"
# Precies wat er naar /beheer gaat: geen "map", geen "bouw", geen tekst van de bench
SLEUTELS = {"versie", "sqlite", "python", "cores", "n", "seed", "nodes", "duur s", "opgeruimd",
            "zoeken"}  # fmt: skip
PER_BEREIK = {"p50", "p95", "max", "vragen", "wortels", "zichtbaar", "ronde B", "ronde C"}
VORIGE = {"zoeken": {"eigenaar": {"p95": 11.0}, "alles": {"p95": 22.0}}}


def args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**{"los": True, "n": 2000, "seed": 6, **kw})


def rest(cache: Path) -> list[str]:
    """Wat er van de bench in de cache achterbleef, naast de uitkomst."""
    return sorted(p.name for p in cache.iterdir() if p.name != "zoekbench.json")


async def test_bench_los_zet_de_uitkomst_op_beheer(client, admin, settings):
    headers, _ = admin
    got = await run_cli(settings, "bench", "--los", "--n", "2000")
    uit = got.stdout.decode()
    assert got.returncode == 0, got.stderr.decode() + uit
    assert "=== zoekbench ===" in uit and "opgeruimd: ja" in uit
    assert "De uitkomst staat op /beheer" in uit
    path = settings.data_dir / benchlos.RESULT
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    data = json.loads(path.read_text())
    assert set(data) == SLEUTELS
    assert data["versie"] == benchlos.VERSION and data["n"] == 2000 and data["opgeruimd"] is True
    assert set(data["zoeken"]) == {"eigenaar", "alles"}
    for bereik in data["zoeken"].values():
        assert set(bereik) == PER_BEREIK
    # Geen pad: niet de tijdelijke map van de bench, niet de datamap
    tekst = path.read_text()
    assert "zoekbench-" not in tekst and str(settings.data_dir) not in tekst
    # De tijdelijke map en de uitkomst van de bench zelf zijn weg
    assert rest(settings.data_dir / benchlos.CACHE) == []
    # /beheer toont het, met de tijd van het bestand
    bench = (await client.get(ADMIN, headers=headers)).json()["bench"]
    at = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    assert bench == {
        "at": at,
        "p95_owner_ms": float(data["zoeken"]["eigenaar"]["p95"]),
        "p95_all_ms": float(data["zoeken"]["alles"]["p95"]),
    }


async def test_alleen_bench_los_bestaat(settings):
    got = await run_cli(settings, "bench")
    assert got.returncode == 1
    assert "Alleen bench --los bestaat" in got.stderr.decode()
    assert not (settings.data_dir / benchlos.RESULT).exists()


def vorige(settings) -> Path:
    path = settings.data_dir / benchlos.RESULT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(VORIGE))
    return path


def test_te_weinig_ruimte_laat_de_vorige_staan(settings, monkeypatch, capsys):
    path = vorige(settings)
    vrij = benchlos.needed(2000) - 1
    monkeypatch.setattr(benchlos.shutil, "disk_usage", lambda _p: SimpleNamespace(free=vrij))
    monkeypatch.setattr(benchlos, "load", lambda _p: pytest.fail("de bench mag niet starten"))
    assert benchlos.run(settings, args()) == 1
    assert "Te weinig vrije ruimte" in capsys.readouterr().err
    assert json.loads(path.read_text()) == VORIGE
    assert benchlos.needed(100_000) > 1_100_000_000  # de piek van S9: ±1,1 GB met de WAL


@pytest.mark.parametrize("hoe", ["faalt", "geen_fts5"])
def test_een_bench_die_niet_slaagt_laat_de_vorige_staan(settings, monkeypatch, hoe):
    path = vorige(settings)

    def main(_argv):
        if hoe == "faalt":
            raise RuntimeError("kapot")
        return 3  # zoals bench.py zonder FTS5

    monkeypatch.setattr(benchlos, "load", lambda _p: SimpleNamespace(main=main))
    if hoe == "faalt":
        with pytest.raises(RuntimeError):
            benchlos.run(settings, args())
    else:
        assert benchlos.run(settings, args()) == 3
    assert json.loads(path.read_text()) == VORIGE
    assert rest(path.parent) == []


def test_schrijven_is_atomair(tmp_path):
    path = tmp_path / "cache" / "zoekbench.json"
    benchlos.write(path, {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}
    with pytest.raises(TypeError):
        benchlos.write(path, {"a": object()})  # json faalt halverwege het schrijven
    assert json.loads(path.read_text()) == {"a": 1}
    assert sorted(p.name for p in path.parent.iterdir()) == ["zoekbench.json"]


@pytest.mark.parametrize("n", ["99", "1000001"])
async def test_grenzen_van_n(settings, n):
    got = await run_cli(settings, "bench", "--los", "--n", n)
    assert got.returncode == 1 and "--n tussen 100 en 1.000.000" in got.stderr.decode()


def test_admin_leest_wat_benchlos_schrijft():
    assert search_admin.BENCH == benchlos.RESULT == Path("cache") / "zoekbench.json"


def test_de_bench_zit_in_het_image():
    """In het image staat de app in /srv/app (WORKDIR /srv) en de bench in
    /srv/tools/zoekbench/bench.py: bench_file() kijkt twee mappen boven app/search."""
    tekst = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    workdirs = re.findall(r"^WORKDIR (\S+)$", tekst, re.M)
    assert workdirs[-1] == "/srv"
    assert re.search(r"^COPY api/app \./app$", tekst, re.M)
    assert re.search(r"^COPY tools/zoekbench/bench\.py \./tools/zoekbench/bench\.py$", tekst, re.M)
    # .dockerignore laat tools/ in de context
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert not any(regel.strip("/").startswith("tools") for regel in ignore)
    # Met uv run vindt hij die van de repo
    assert benchlos.bench_file() == ROOT / "tools" / "zoekbench" / "bench.py"
