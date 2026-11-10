"""De generator van de boom (app/search/bench.py, bouwontwerp fase 6 §3.6 en §4.4): vast per
seed, alleen de standaardbibliotheek, en via de echte migratie en de echte triggers. De boom
van 100.000 zelf draait in zoek-perf (Z1); hier een kleine van dezelfde generator."""

from __future__ import annotations

import ast
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, analysis, previews, render_meta
from app.config import Settings
from app.db import get_engine
from app.models import User
from app.search import bench
from app.security import verify_password
from tests.search.migratie import MADE, check_fts, check_tree

KLEIN = bench.Plan(n=1_500, tekens=1_500)
BRON = Path(bench.__file__)


@pytest.fixture(scope="module")
def boom(tmp_path_factory) -> tuple[Path, bench.Uitkomst]:
    data = tmp_path_factory.mktemp("bench")
    return data, bench.maak(data, KLEIN)


def _con(data: Path) -> sqlite3.Connection:
    return sqlite3.connect(data / "platform.db")


def _vorm(boom: bench._Boom) -> list[tuple]:
    return [(n.id, n.parent, n.owner, n.name, n.kind, n.blob, n.weg, n.dood) for n in boom.nodes]


def test_zelfde_seed_geeft_dezelfde_boom():
    plan = bench.Plan(n=600)
    een, twee = bench.plan_boom(plan), bench.plan_boom(plan)
    assert _vorm(een) == _vorm(twee)
    assert een.tellingen == twee.tellingen
    assert _vorm(bench.plan_boom(bench.Plan(n=600, seed=7))) != _vorm(een)


def test_dezelfde_tekst_bij_dezelfde_seed():
    plan = bench.Plan(n=400, tekens=500)
    een = list(bench._teksten(bench.plan_boom(plan)))
    assert een == list(bench._teksten(bench.plan_boom(plan)))
    assert een, "geen documenten"


def test_alleen_de_standaardbibliotheek():
    """tools/zoekbench/bench.py neemt de generator over en mag de app niet importeren: bovenin
    alleen de standaardbibliotheek. De app pas binnen maak()."""
    boom = ast.parse(BRON.read_text())
    namen = set()
    for knoop in boom.body:
        if isinstance(knoop, ast.Import):
            namen |= {a.name.split(".")[0] for a in knoop.names}
        elif isinstance(knoop, ast.ImportFrom):
            namen.add((knoop.module or "").split(".")[0])
    namen.discard("__future__")
    assert namen <= set(sys.stdlib_module_names), namen - set(sys.stdlib_module_names)


def test_plan_controleert_profiel_en_gebruikers():
    with pytest.raises(ValueError, match="profiel"):
        bench.Plan(profiel="reus")
    with pytest.raises(ValueError, match="16"):
        bench.Plan(gebruikers=10)


def test_de_verhoudingen_bij_100000():
    """Het plan van §3.6, zonder te schrijven: 5.900 mappen, 20.000 documenten, 55.000 foto's,
    2.000 kopieën bij een ander, 3% en 1% in de prullenbak."""
    boom = bench.plan_boom(bench.Plan())
    bestanden = [n for n in boom.nodes if n.kind == "file"]
    soorten = [boom.blobs[n.blob].soort for n in bestanden]
    mappen = [n for n in boom.nodes if n.kind == "dir" and n.parent]
    assert len(bestanden) == 100_000
    assert len(mappen) == 5_900
    assert 53_000 < soorten.count("foto") < 57_000
    assert 19_000 < soorten.count("document") < 21_000
    assert boom.tellingen["kopie_ander"] == 2_000
    assert 0.02 < sum(m.weg for m in mappen) / len(mappen) < 0.04
    assert 0.007 < sum(n.weg for n in bestanden) / len(bestanden) < 0.013
    assert max(m.diepte for m in mappen) == bench.DIEPTE
    gemiddeld = sum(b.tekens for b in boom.blobs.values()) / soorten.count("document")
    assert 9_000 < gemiddeld < 14_500, gemiddeld


@pytest.mark.parametrize("profiel", ["groot", "kopieen"])
def test_de_profielen(profiel):
    boom = bench.plan_boom(bench.Plan(n=300, profiel=profiel))
    eigenaar = boom.gebruikers[0]
    naam = "Groot" if profiel == "groot" else "Kopieën"
    (map_,) = [n for n in boom.nodes if n.kind == "dir" and n.name == naam]
    assert map_.owner == eigenaar.id and map_.parent == eigenaar.root
    erin = [n for n in boom.nodes if n.parent == map_.id]
    groot = {n.blob for n in erin if boom.blobs[n.blob].tekens == bench.MB}
    if profiel == "groot":
        assert len(erin) == len(groot) == 200
    else:
        assert len(erin) == 11 and len(groot) == 1
        assert boom.blobs[groot.pop()].refs == 11


def test_de_triggers_zetten_nummer_pad_en_dead(boom):
    data, uit = boom
    engine = sa.create_engine(f"sqlite:///{data / 'platform.db'}")
    try:
        with engine.connect() as conn:
            assert check_tree(conn) == uit.tellingen["nodes"]
            check_fts(conn)
            assert conn.exec_driver_sql("SELECT count(*) FROM fts_node").scalar() == (
                conn.exec_driver_sql("SELECT count(*) FROM search_src").scalar()
            )
    finally:
        engine.dispose()


# Wat nooit mag: elke vraag telt de rijen die het fout hebben
NUL = {
    # ref_count telt de nodes met die inhoud (er zijn geen versies)
    "ref_count": "SELECT count(*) FROM blob b WHERE ref_count <> "
    "(SELECT count(*) FROM node n WHERE n.blob_id = b.sha256)",
    # photo_geo hoort bij de eigenaar van de node, alleen bij foto's (Keuze 25a)
    "photo_geo": "SELECT count(*) FROM photo_geo g JOIN node n ON n.id = g.node_id "
    "WHERE g.owner_id <> n.owner_id OR n.mime <> 'image/jpeg'",
    "dup_pair": "SELECT count(*) FROM dup_pair WHERE a >= b",
    # De backfills bij het starten hebben niets te doen
    "backfill": "SELECT count(*) FROM blob WHERE preview IS NULL OR preview = 'pending' "
    f"OR render_version < {previews.RENDER_VERSION} OR meta_version < {render_meta.VERSION}",
    # Ook die van de tekst (Z2): elke inhoud een rij voor de stap text, niets open, done precies
    # bij een inhoud met tekst (bench.analyse_klaar)
    "analysis": "SELECT count(*) FROM blob b WHERE NOT EXISTS (SELECT 1 FROM analysis a "
    "WHERE a.blob_sha = b.sha256 AND a.step = 'text' AND a.version = "
    f"{analysis.VERSIONS[analysis.Step.TEXT]} AND a.state IN ('done', 'none', 'skip') AND "
    "(a.state = 'done') = EXISTS (SELECT 1 FROM blob_text t WHERE t.blob_sha = b.sha256))",
    # En die van ocr (X1): elke inhoud een rij voor de stap ocr, none bij een PDF, anders skip
    "ocr": "SELECT count(*) FROM blob b WHERE NOT EXISTS (SELECT 1 FROM analysis a "
    "WHERE a.blob_sha = b.sha256 AND a.step = 'ocr' AND a.version = "
    f"{analysis.VERSIONS[analysis.Step.OCR]} AND a.state = CASE WHEN b.mime = "
    "'application/pdf' THEN 'none' ELSE 'skip' END)",
}


def test_wat_er_geschreven_is(boom):
    data, uit = boom
    t = uit.tellingen
    with _con(data) as con:

        def een(sql: str) -> int:
            return con.execute(sql).fetchone()[0]

        for naam, sql in NUL.items():
            assert een(sql) == 0, naam
        assert een("SELECT count(*) FROM node WHERE kind = 'file'") == KLEIN.n == t["bestanden"]
        mappen = een("SELECT count(*) FROM node WHERE kind = 'dir' AND parent_id IS NOT NULL")
        assert mappen == t["mappen"]
        assert een("SELECT count(*) FROM blob_text") == t["documenten"] > 0
        assert een("SELECT sum(length(body)) FROM blob_text") == t["tekens"]
        assert een("SELECT count(*) FROM photo_geo") == t["gps"] > 0
        # Kopieën bij een ander: dezelfde inhoud bij twee eigenaren
        gedeeld = een(
            "SELECT count(*) FROM (SELECT blob_id FROM node WHERE kind = 'file' "
            "GROUP BY blob_id HAVING count(DISTINCT owner_id) > 1)"
        )
        assert gedeeld > 0
        # dead volgt de prullenbak: ook een bestand onder een weggegooide map is dood
        dood = een("SELECT count(*) FROM node WHERE dead = 1")
        assert dood > t["prullenbak_mappen"] + t["prullenbak_bestanden"]


@pytest.mark.anyio
async def test_de_backfill_van_z2_heeft_niets_te_doen(boom):
    """De echte backfill van Z2 (analysis.backfill_job, zoals bij het starten) zet in de boom
    niets klaar: de bestanden staan niet op schijf, dus anders las de stap text (of de stap ocr
    van X1) bij een meting duizenden documenten die er niet zijn (§4.4)."""
    data, _ = boom
    settings = Settings(data_dir=data)
    engine = get_engine(settings)

    async def stand() -> tuple:
        async with engine.connect() as conn:
            rijen = (
                await conn.exec_driver_sql("SELECT state, count(*) FROM analysis GROUP BY state")
            ).all()
            werk = (
                await conn.exec_driver_sql(
                    "SELECT count(*) FROM job WHERE type LIKE 'analysis.%' AND status = 'queued'"
                )
            ).scalar()
        return sorted(rijen), werk

    try:
        voor = await stand()
        assert {staat for staat, _ in voor[0]} <= {"done", "none", "skip"}
        await analysis.backfill_job(settings, {"after": ""})
        assert await stand() == voor
        assert voor[1] == 0
    finally:
        await engine.dispose()


def test_kort_voor_het_einde_van_een_log(boom):
    _, uit = boom
    regels = uit.kort().splitlines()
    assert regels[0] == "=== kort ==="
    assert f"bestanden {KLEIN.n}" in regels[2]
    assert '"rollen"' in uit.json()


def test_iedereen_kan_inloggen(boom):
    data, uit = boom
    with _con(data) as con:
        hashes = dict(con.execute('SELECT email, password_hash FROM "user"').fetchall())
    assert set(hashes) == {g["email"] for g in uit.gebruikers.values()}
    assert verify_password(hashes[uit.gebruikers["b00"]["email"]], bench.WACHTWOORD)


@pytest.mark.anyio
async def test_de_rollen_van_4_4(boom):
    """Hoeveel bovenste gedeelde mappen elke rol ziet, met de echte access.shared_roots."""
    data, uit = boom
    settings = Settings(data_dir=data, jwt_secret="test" * 8)
    MADE.append(settings)
    verwacht = {
        "eigenaar": 0,
        "ontvanger5": 5,
        "ontvanger20": 20,
        "groepslid": 6,
        "gast": 2,
        "alles": KLEIN.gebruikers,
    }
    async with AsyncSession(get_engine(settings)) as session:
        for rol, aantal in verwacht.items():
            gebruiker = uit.gebruikers[uit.rollen[rol]]
            wortels = await access.shared_roots(session, uuid.UUID(gebruiker["id"]))
            assert len(wortels) == aantal, rol
        gast = await session.get(User, uuid.UUID(uit.gebruikers["gast"]["id"]))
        assert gast is not None and gast.role == "guest"


def test_de_opdrachtregel(tmp_path, capsys):
    uitkomst = tmp_path / "uitkomst.json"
    data = tmp_path / "data"
    code = bench.main(["--data", str(data), "--n", "300", "--uitkomst", str(uitkomst)])
    assert code == 0
    assert capsys.readouterr().out.startswith("=== kort ===\n")
    assert '"n": 300' in uitkomst.read_text()


def test_zoek_binnen():
    woorden = sorted(["factuur", "facturen", "fiets", "offerte"])
    assert bench.zoek_binnen(woorden, "fact") == ["facturen", "factuur"]
    assert bench.zoek_binnen(woorden, "x") == []
