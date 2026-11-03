"""own_live: de ene regel voor "eigen, levende bestanden" (bouwontwerp fase 6, Keuze 25a).

Drie dingen: de vorm ligt vast (de SQLAlchemy-vorm en de SQL-tekst zeggen hetzelfde), hij doet
wat hij zegt op een echte database met de triggers van 0010 (prullenbak in en uit, ook via een
map erboven; een kopie in Brams boom), en de wachter: elke fotofunctie gebruikt hem. De
fotofuncties komen in Z3 en Z6; de wachter kijkt naar elk bestand van die pakketten dat er is.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.orm import aliased

from app.db import get_engine
from app.models import Node
from app.search.live import OWN_LIVE_SQL, own_live
from tests.search.migratie import NOW, SHA_JPG, fill_0009, migrate, settings_at

pytestmark = pytest.mark.anyio

APP = Path(__file__).resolve().parents[2] / "app"

# De fotofuncties van §2.0 en Keuze 25a: kaart, plaatsen, duplicaten, personen, herinneringen,
# de verversing van slimme albums en de foto-filters van zoeken
PHOTO_FUNCTIONS = (
    "memories.py",
    "albums_rule.py",
    "geo",
    "dupes",
    "people",
    "search/filters_photo.py",
    "routers/photos_map.py",
    "routers/duplicates.py",
    "routers/people.py",
    "routers/memories.py",
)
# Wat geen node leest en dus geen own_live hoeft: de vaste typen van Z0
NOT_A_QUERY = {
    "geo/types.py",
    "geo/__init__.py",
    "dupes/__init__.py",
    "people/__init__.py",
    "people/types.py",
}

READS_NODES = re.compile(r"\bNode\b|\bFROM\s+node\b|\bJOIN\s+node\b", re.I)


def needs_own_live(source: str) -> bool:
    """Leest dit bestand nodes zonder own_live? (Dan is het een fout.)"""
    return bool(READS_NODES.search(source)) and not re.search(r"\bown_live\b|\bOWN_LIVE_SQL\b",
                                                              source)  # fmt: skip


def photo_files() -> list[Path]:
    found = []
    for name in PHOTO_FUNCTIONS:
        path = APP / name
        if path.is_dir():
            found += sorted(path.rglob("*.py"))
        elif path.exists():
            found.append(path)
    return [p for p in found if str(p.relative_to(APP)) not in NOT_A_QUERY]


def test_de_vorm_ligt_vast():
    assert OWN_LIVE_SQL == "{n}.owner_id = :me AND {n}.dead = 0 AND {n}.kind = 'file'"
    me = uuid.uuid4()
    compiled = own_live(Node, me).compile()
    assert str(compiled) == (
        "node.owner_id = :owner_id_1 AND node.dead = :dead_1 AND node.kind = :kind_1"
    )
    assert compiled.params == {"owner_id_1": me, "dead_1": 0, "kind_1": "file"}
    # Met een alias van het model en van de tabel: dezelfde drie voorwaarden op die alias
    n = aliased(Node, name="n")
    assert str(own_live(n, me).compile()).startswith("n.owner_id = ")
    t = Node.__table__.alias("t")
    assert str(own_live(t, me).compile()).startswith("t.owner_id = ")


async def _names(settings, me: uuid.UUID) -> tuple[set[str], set[str]]:
    """Wat own_live en OWN_LIVE_SQL voor deze gebruiker laten staan."""
    async with get_engine(settings).connect() as conn:
        orm = set((await conn.execute(sa.select(Node.name).where(own_live(Node, me)))).scalars())
        raw = set(
            (
                await conn.execute(
                    sa.text("SELECT n.name FROM node n WHERE " + OWN_LIVE_SQL.format(n="n")),
                    {"me": me.hex},
                )
            ).scalars()
        )
    return orm, raw


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_alleen_eigen_levende_bestanden_ook_na_prullenbak_en_terug(tmp_path, fts5):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "head", fts5=fts5)
    eva, bram = ids["eva"], ids["bram"]

    # Geen mappen, geen album, niets uit de prullenbak (strand ligt in de map vakantie, los zelf)
    expected = {"verslag.x", "notities.x"}
    assert await _names(settings, eva) == (expected, expected)
    # werk en het album zijn met Bram gedeeld; toch is niets daarvan van hem
    assert await _names(settings, bram) == (set(), set())

    async def run(sql: str, **params) -> None:
        async with get_engine(settings).begin() as conn:
            await conn.execute(sa.text(sql), {k: v.hex if isinstance(v, uuid.UUID) else v
                                              for k, v in params.items()})  # fmt: skip

    # De map vakantie terug uit de prullenbak: strand en (in diep) schelp komen terug
    await run("UPDATE node SET deleted_at = NULL WHERE id = :id", id=ids["vakantie"])
    expected |= {"strand.x", "schelp.x"}
    assert await _names(settings, eva) == (expected, expected)
    # De map erboven (werk) naar de prullenbak: verslag is weg, ook al ligt het er zelf niet in
    await run("UPDATE node SET deleted_at = :t WHERE id = :id", id=ids["werk"], t=NOW)
    expected -= {"verslag.x"}
    assert await _names(settings, eva) == (expected, expected)

    # Bram kopieert Eva's foto naar zijn eigen boom (zelfde inhoud): zijn kopie is van hem
    copy = uuid.uuid4()
    await run(
        "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, created_at, "
        "modified_at, search_name) VALUES (:id, :parent, :owner, 'kopie.x', 'file', "
        "'image/jpeg', 1, :blob, :t, :t, 'kopie.x')",
        id=copy, parent=ids["bram_root"], owner=bram, blob=SHA_JPG, t=NOW,
    )  # fmt: skip
    assert await _names(settings, bram) == ({"kopie.x"}, {"kopie.x"})
    assert await _names(settings, eva) == (expected, expected)


def test_de_wachter_ziet_een_fotofunctie_zonder_own_live():
    zonder = "from app.models import Node\nselect(Node.id).where(Node.owner_id == me)\n"
    met = zonder + "q = q.where(own_live(Node, me))\n"
    rauw = "SELECT n.id FROM node n WHERE " + OWN_LIVE_SQL
    assert needs_own_live(zonder)
    assert not needs_own_live(met)
    assert needs_own_live("SELECT id FROM node WHERE owner_id = :me")
    assert not needs_own_live(f"sql = {rauw!r}\nfrom app.search.live import OWN_LIVE_SQL")
    assert not needs_own_live("def place(lat, lon): ...")  # leest geen nodes


def test_elke_fotofunctie_gebruikt_own_live():
    offenders = [str(p.relative_to(APP)) for p in photo_files() if needs_own_live(p.read_text())]
    assert not offenders, f"Fotofuncties zonder own_live (Keuze 25a): {offenders}"
