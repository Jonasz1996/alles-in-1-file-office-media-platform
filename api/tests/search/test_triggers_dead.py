"""dead na prullenbak, terugzetten en verplaatsen van een map met kinderen in de prullenbak
(review Z0, veilig F2).

DEAD_SUBTREE (app/search/schema.py) rekende dead eerst met één UPDATE en per rij een EXISTS over
alle nodes in de prullenbak van de submap: O(submap × prullenbak). Een map met 10.000 bestanden
waarvan 2.000 in de prullenbak terugzetten of verplaatsen kostte zo 5 tot 6 s (3.46.1), terwijl
elke andere schrijver op het slot wachtte. Nu twee stappen, elk lineair.

- dead klopt na elke stap met een volledige herberekening (check_tree), op elke diepte;
- het werk (stappen van de SQLite-VM) groeit niet met het aantal in de prullenbak;
- zoek_perf: terugzetten en verplaatsen van 10.000 met 2.000 in de prullenbak < 0,5 s.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import closing

import pytest
import sqlalchemy as sa
from alembic import command

from app.db import get_engine
from tests.search.migratie import NOW, check_tree, migrate, settings_at

pytestmark = pytest.mark.anyio


def _boom(ex, owner: str, parent: str, name: str, kind: str) -> str:
    """ex: execute van sqlite3, of exec_driver_sql van een verbinding van SQLAlchemy."""
    node_id = uuid.uuid4().hex
    ex(
        "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, modified_at, "
        "search_name) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (node_id, parent, owner, name, kind, NOW, NOW, name),
    )
    return node_id


def _gebruiker(ex) -> tuple[str, str]:
    user, root = uuid.uuid4().hex, uuid.uuid4().hex
    ex(
        'INSERT INTO "user" (id, email, display_name, password_hash, role, quota_bytes, root_id, '
        "created_at) VALUES (?, 'eva@example.nl', 'Eva', 'x', 'user', 0, ?, ?)",
        (user, root, NOW),
    )
    ex(
        "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, modified_at, "
        "search_name) VALUES (?, NULL, ?, '', 'dir', 0, ?, ?, '')",
        (root, user, NOW, NOW),
    )
    return user, root


def _map_met_prullenbak(ex, user: str, parent: str, name: str, n: int, k: int) -> str:
    """Een map met n bestanden, waarvan k één voor één in de prullenbak."""
    folder = _boom(ex, user, parent, name, "dir")
    kids = [_boom(ex, user, folder, f"{name}-{i}", "file") for i in range(n)]
    for kid in kids[:k]:
        ex("UPDATE node SET deleted_at = ? WHERE id = ?", (NOW, kid))
    return folder


async def _head(tmp_path, fts5: bool = True):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "head", fts5=fts5)
    await get_engine(settings).dispose()
    return settings


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_dead_klopt_op_elke_diepte(tmp_path, fts5):
    """Een map met kinderen in de prullenbak op diepte 1, 2 en 3, en een submap in de
    prullenbak met een submap erin: na elke stap is dead gelijk aan een herberekening."""
    settings = await _head(tmp_path, fts5)
    ids: dict[str, str] = {}

    def bouw(conn) -> None:
        con = conn.exec_driver_sql
        ids["eva"], ids["root"] = _gebruiker(con)
        ids["elders"] = _boom(con, ids["eva"], ids["root"], "elders", "dir")
        ids["weg"] = _boom(con, ids["eva"], ids["root"], "weg", "dir")
        ids["f"] = _boom(con, ids["eva"], ids["root"], "f", "dir")
        ids["a"] = _boom(con, ids["eva"], ids["f"], "a", "dir")
        ids["b"] = _boom(con, ids["eva"], ids["a"], "b", "dir")
        ids["c"] = _boom(con, ids["eva"], ids["f"], "c", "dir")
        ids["d"] = _boom(con, ids["eva"], ids["c"], "d", "dir")
        for parent in ("f", "a", "b", "c", "d"):
            for i in range(5):
                ids[f"{parent}{i}"] = _boom(con, ids["eva"], ids[parent], f"{parent}{i}", "file")

    async with get_engine(settings).begin() as conn:
        await conn.run_sync(bouw)

    steps = [
        # in de prullenbak op diepte 1, 2 en 3, en een hele submap (c, met d erin)
        ("trash", "f0"), ("trash", "a1"), ("trash", "b2"), ("trash", "c"), ("trash", "d3"),
        ("trash", "weg"),
        # de map zelf erin, eruit, en weer eruit nadat hij verhuisde
        ("trash", "f"), ("restore", "f"),
        ("move", "f", "elders"), ("move", "f", "weg"), ("move", "f", "root"),
        ("move", "a", "c"),  # een levende submap in een submap in de prullenbak
        ("restore", "c"),  # c komt terug, d3 blijft weg
        ("move", "b", "f"), ("trash", "f"), ("move", "c", "elders"), ("restore", "f"),
        ("restore", "weg"), ("restore", "d3"), ("restore", "a1"),
    ]  # fmt: skip
    async with get_engine(settings).begin() as conn:
        for step in steps:
            if step[0] == "trash":
                sql, args = "UPDATE node SET deleted_at = :t WHERE id = :id", {"t": NOW}
            elif step[0] == "restore":
                sql, args = "UPDATE node SET deleted_at = NULL WHERE id = :id", {}
            else:
                sql, args = "UPDATE node SET parent_id = :p WHERE id = :id", {"p": ids[step[2]]}
            await conn.execute(sa.text(sql), {**args, "id": ids[step[1]]})
            await conn.run_sync(check_tree)
        dead = dict((await conn.exec_driver_sql("SELECT name, dead FROM node")).all())
    # Aan het eind: alleen wat zelf in de prullenbak ligt (en wat eronder hangt) is dood
    assert {name for name, d in dead.items() if d} == {"f0", "b2"}


def _werk(path: str, folder: str, *sql: str) -> int:
    """Stappen van de SQLite-VM (per 100) voor deze statements, op een eigen verbinding."""
    stappen = 0

    def tel() -> int:
        nonlocal stappen
        stappen += 1
        return 0

    with closing(sqlite3.connect(path, isolation_level=None)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.set_progress_handler(tel, 100)
        for statement in sql:
            con.execute(statement, {"id": folder, "t": NOW})
        con.set_progress_handler(None, 0)
    return stappen


async def test_het_werk_groeit_niet_met_de_prullenbak(tmp_path):
    """Twee mappen met 2.000 bestanden, de ene met 100 en de andere met 800 in de prullenbak.
    Terugzetten en verplaatsen: de submap één keer, en per node in de prullenbak één stap in de
    index. Gemeten (3.53.1, stappen van 100): terugzetten 1.699 en 2.427, verplaatsen 3.544 en
    4.153. De oude vorm: 20.658 en 219.831, ruim tien keer zoveel bij 800."""
    settings = await _head(tmp_path)
    path = str(settings.data_dir / "platform.db")
    with closing(sqlite3.connect(path, isolation_level=None)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN")
        user, root = _gebruiker(con.execute)
        elders = _boom(con.execute, user, root, "elders", "dir")
        weinig = _map_met_prullenbak(con.execute, user, root, "weinig", 2000, 100)
        veel = _map_met_prullenbak(con.execute, user, root, "veel", 2000, 800)
        con.execute("COMMIT")

    trash = "UPDATE node SET deleted_at = :t WHERE id = :id"
    restore = "UPDATE node SET deleted_at = NULL WHERE id = :id"
    move = f"UPDATE node SET parent_id = '{elders}' WHERE id = :id"
    werk = {}
    for name, folder in (("weinig", weinig), ("veel", veel)):
        _werk(path, folder, trash)
        werk[name] = (_werk(path, folder, restore), _werk(path, folder, move))
    for i, wat in enumerate(("terugzetten", "verplaatsen")):
        assert werk["veel"][i] < 3 * werk["weinig"][i], (wat, werk)

    async with get_engine(settings).connect() as conn:
        assert await conn.run_sync(check_tree) == 4 + 2 * 2000


@pytest.mark.zoek_perf
async def test_terugzetten_en_verplaatsen_van_10000_met_2000_in_de_prullenbak(tmp_path):
    """De meting van de review (3.46.1: terugzetten 5,9 s, verplaatsen 6,2 s): nu < 0,5 s."""
    settings = await _head(tmp_path)
    path = str(settings.data_dir / "platform.db")
    with closing(sqlite3.connect(path, isolation_level=None)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN")
        user, root = _gebruiker(con.execute)
        elders = _boom(con.execute, user, root, "elders", "dir")
        folder = _map_met_prullenbak(con.execute, user, root, "groot", 10_000, 2_000)
        con.execute("COMMIT")
        tijd = {}
        for wat, sql, args in (
            ("prullenbak", "UPDATE node SET deleted_at = ? WHERE id = ?", (NOW, folder)),
            ("terugzetten", "UPDATE node SET deleted_at = NULL WHERE id = ?", (folder,)),
            ("verplaatsen", "UPDATE node SET parent_id = ? WHERE id = ?", (elders, folder)),
        ):
            start = time.perf_counter()
            con.execute(sql, args)
            tijd[wat] = round(time.perf_counter() - start, 3)
    print(f"\ndead in een submap van 10.000 met 2.000 in de prullenbak ({sqlite3.sqlite_version}): "
          f"{tijd}")  # fmt: skip
    assert tijd["terugzetten"] < 0.5, tijd
    assert tijd["verplaatsen"] < 0.5, tijd
    async with get_engine(settings).connect() as conn:
        assert await conn.run_sync(check_tree) == 3 + 10_000
