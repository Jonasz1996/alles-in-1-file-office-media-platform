"""photo_geo houdt de stand van zijn node bij (review Z0, haalbaar F1 en F2).

De kaart en de plaatsen lezen photo_geo zonder join op node (live.own_live_geo). Dat mag alleen
als photo_geo precies own_live is: een rij hoort bij een bestand van de eigenaar van de node
(geo_bi), node_id en owner_id veranderen niet (geo_bu; een andere eigenaar: de rij gaat weg),
en dead en taken_at zijn altijd die van de node (geo_ai, node_au_taken en DEAD_SUBTREE; geo_bu
weigert een andere waarde). Hier op een echte database met de triggers van 0010, met en zonder
FTS5, via het ORM zoals Z3 schrijft.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_engine
from app.models import Node, PhotoGeo
from app.search import schema
from app.search.live import OWN_LIVE_GEO_SQL, own_live, own_live_geo
from tests.search import spike
from tests.search.migratie import NOW, check_tree, fill_0009, migrate, settings_at
from tests.search.plans import plan_van

pytestmark = pytest.mark.anyio

DAG = datetime(2026, 7, 14, 15, 30, tzinfo=UTC).isoformat()


async def _head(tmp_path, fts5: bool = True):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "head", fts5=fts5)
    return settings, ids


def _plek(node: uuid.UUID, owner: uuid.UUID, **extra) -> PhotoGeo:
    return PhotoGeo(node_id=node, owner_id=owner, lat=52.37, lon=4.89, x16=33_650, y16=21_530,
                    source="exif", **extra)  # fmt: skip


async def _rijen(settings) -> dict[str, tuple[int, str | None]]:
    """naam -> (dead, taken_at) van photo_geo, en meteen check_tree."""
    async with get_engine(settings).connect() as conn:
        await conn.run_sync(check_tree)
        rows = await conn.exec_driver_sql(
            "SELECT n.name, g.dead, g.taken_at FROM photo_geo g JOIN node n ON n.id = g.node_id"
        )
        return {name: (dead, taken) for name, dead, taken in rows.all()}


async def _kaart(settings, me: uuid.UUID) -> tuple[set, set, set]:
    """Welke plekken de kaart toont: own_live_geo, OWN_LIVE_GEO_SQL, en own_live met de join."""
    async with get_engine(settings).connect() as conn:
        geo = set(
            (
                await conn.execute(sa.select(PhotoGeo.node_id).where(own_live_geo(PhotoGeo, me)))
            ).scalars()
        )
        raw = set(
            uuid.UUID(r)
            for r in (
                await conn.execute(
                    sa.text(
                        "SELECT g.node_id FROM photo_geo g WHERE " + OWN_LIVE_GEO_SQL.format(g="g")
                    ),
                    {"me": me.hex},
                )
            ).scalars()
        )
        join = set(
            (
                await conn.execute(
                    sa.select(PhotoGeo.node_id)
                    .join(Node, Node.id == PhotoGeo.node_id)
                    .where(own_live(Node, me))
                )
            ).scalars()
        )
    return geo, raw, join


async def _run(settings, sql: str, **params) -> None:
    async with get_engine(settings).begin() as conn:
        await conn.execute(sa.text(sql), {k: v.hex if isinstance(v, uuid.UUID) else v
                                          for k, v in params.items()})  # fmt: skip


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_photo_geo_volgt_de_node(tmp_path, fts5):
    """Prullenbak in en uit (ook via een map erboven), verplaatsen, een andere opnamedatum: na
    elke stap zijn dead en taken_at die van de node, en toont de kaart zonder join precies wat
    own_live met de join toont."""
    settings, ids = await _head(tmp_path, fts5)
    eva = ids["eva"]
    await _run(settings, "UPDATE node SET taken_at = :t WHERE id = :id", id=ids["strand"], t=DAG)
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        # strand en schelp liggen in vakantie (in de prullenbak), notities niet. Het ORM schrijft
        # dead = 0 en taken_at = NULL; geo_ai zet ze op die van de node
        session.add_all([_plek(ids[n], eva) for n in ("strand", "schelp", "notities")])
        await session.commit()
    assert await _rijen(settings) == {
        "strand.x": (1, await _taken(settings, "strand")),
        "schelp.x": (1, None),
        "notities.x": (0, None),
    }

    async def kaart(*names: str) -> None:
        geo, raw, join = await _kaart(settings, eva)
        assert geo == raw == join == {ids[n] for n in names}

    await kaart("notities")
    stappen = [
        # vakantie terug: strand en (in diep) schelp leven weer
        ("UPDATE node SET deleted_at = NULL WHERE id = :id", {"id": ids["vakantie"]},
         ("strand", "schelp", "notities")),
        # diep in de prullenbak: schelp weg
        ("UPDATE node SET deleted_at = :t WHERE id = :id", {"id": ids["diep"], "t": NOW},
         ("strand", "notities")),
        # schelp uit diep naar werk: weer levend, al ligt diep nog in de prullenbak
        ("UPDATE node SET parent_id = :p WHERE id = :id", {"id": ids["schelp"], "p": ids["werk"]},
         ("strand", "schelp", "notities")),
        # werk in de prullenbak: schelp (en verslag, zonder plek) weg via de map erboven
        ("UPDATE node SET deleted_at = :t WHERE id = :id", {"id": ids["werk"], "t": NOW},
         ("strand", "notities")),
        # werk naar vakantie verplaatsen (levend): schelp blijft weg, werk ligt er zelf in
        ("UPDATE node SET parent_id = :p WHERE id = :id", {"id": ids["werk"], "p": ids["vakantie"]},
         ("strand", "notities")),
        ("UPDATE node SET deleted_at = NULL WHERE id = :id", {"id": ids["werk"]},
         ("strand", "schelp", "notities")),
        # notities zelf in de prullenbak, en vakantie (met werk en schelp erin)
        ("UPDATE node SET deleted_at = :t WHERE id = :id", {"id": ids["notities"], "t": NOW},
         ("strand", "schelp")),
        ("UPDATE node SET deleted_at = :t WHERE id = :id", {"id": ids["vakantie"], "t": NOW}, ()),
        ("UPDATE node SET deleted_at = NULL WHERE id = :id", {"id": ids["vakantie"]},
         ("strand", "schelp")),
    ]  # fmt: skip
    for sql, params, levend in stappen:
        await _run(settings, sql, **params)
        rijen = await _rijen(settings)
        assert {n.removesuffix(".x") for n, (dead, _) in rijen.items() if not dead} == set(levend)
        await kaart(*levend)

    # Een andere opnamedatum: photo_geo volgt (de kaart sorteert op de nieuwste)
    await _run(settings, "UPDATE node SET taken_at = :t WHERE id = :id", id=ids["schelp"], t=DAG)
    await _run(settings, "UPDATE node SET taken_at = NULL WHERE id = :id", id=ids["strand"])
    rijen = await _rijen(settings)
    assert rijen["schelp.x"][1] == await _taken(settings, "schelp") is not None
    assert rijen["strand.x"][1] is None

    # Een gewone wijziging van de plek zelf mag (geo_bu kijkt alleen naar de vier kolommen)
    async with maker() as session:
        plek = await session.get(PhotoGeo, ids["schelp"])
        assert plek is not None
        plek.place_id, plek.country = 2_759_794, "NL"
        await session.commit()
    await _rijen(settings)

    # Bram kopieert de foto: zijn kopie krijgt een eigen plek, voor hem; Eva's kaart blijft
    copy = uuid.uuid4()
    await _run(
        settings,
        "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, created_at, "
        "modified_at, search_name) VALUES (:id, :parent, :owner, 'kopie.x', 'file', "
        "'image/jpeg', 1, :blob, :t, :t, 'kopie.x')",
        id=copy, parent=ids["bram_root"], owner=ids["bram"], blob="e" * 64, t=NOW,
    )  # fmt: skip
    async with maker() as session:
        session.add(_plek(copy, ids["bram"]))
        await session.commit()
    await kaart("strand", "schelp")
    geo, raw, join = await _kaart(settings, ids["bram"])
    assert geo == raw == join == {copy}
    # Een andere eigenaar van de node: de plek gaat weg (node_au_owner), niets blijft hangen
    await _run(settings, "UPDATE node SET owner_id = :o WHERE id = :id", id=copy, o=eva)
    assert "kopie.x" not in await _rijen(settings)
    await kaart("strand", "schelp")


async def _taken(settings, name: str) -> str | None:
    async with get_engine(settings).connect() as conn:
        return (
            await conn.exec_driver_sql("SELECT taken_at FROM node WHERE name = ?", (f"{name}.x",))
        ).scalar()


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_photo_geo_alleen_bij_een_bestand_van_de_eigenaar(tmp_path, fts5):
    settings, ids = await _head(tmp_path, fts5)
    eva, bram = ids["eva"], ids["bram"]
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)

    async def weigert(rij: PhotoGeo) -> None:
        async with maker() as session:
            session.add(rij)
            with pytest.raises(sa.exc.DBAPIError, match="alleen bij een bestand"):
                await session.commit()

    # Eva's foto met Bram als eigenaar (een plek op de kaart van een ander); een map; een album;
    # een node die niet bestaat
    await weigert(_plek(ids["notities"], bram))
    await weigert(_plek(ids["werk"], eva))
    await weigert(_plek(ids["album"], eva))
    await weigert(_plek(uuid.uuid4(), eva))
    async with get_engine(settings).connect() as conn:
        assert (await conn.exec_driver_sql("SELECT count(*) FROM photo_geo")).scalar() == 0

    async with maker() as session:
        session.add(_plek(ids["notities"], eva))
        await session.commit()
    # Zelf dead, taken_at, owner_id of node_id zetten: geweigerd, ook met het ORM
    for sql, params in (
        ("UPDATE photo_geo SET dead = 1 WHERE node_id = :id", {}),
        ("UPDATE photo_geo SET taken_at = :t WHERE node_id = :id", {"t": DAG}),
        ("UPDATE photo_geo SET owner_id = :o WHERE node_id = :id", {"o": bram}),
        ("UPDATE photo_geo SET node_id = :n WHERE node_id = :id", {"n": ids["strand"]}),
    ):
        with pytest.raises(sa.exc.DBAPIError, match="volgen de node"):
            await _run(settings, sql, id=ids["notities"], **params)
    async with maker() as session:
        plek = await session.get(PhotoGeo, ids["notities"])
        assert plek is not None
        plek.dead = 1
        with pytest.raises(sa.exc.DBAPIError, match="volgen de node"):
            await session.commit()
    assert await _rijen(settings) == {"notities.x": (0, None)}


def test_de_stap_in_photo_geo_zoekt_per_node(tmp_path):
    """De derde stap van DEAD_SUBTREE: de submap via ix_node_mpath, dan per node één zoekstap op
    de sleutel van photo_geo; nooit photo_geo helemaal langs."""
    path = tmp_path / "h.db"
    spike.head_db(path, fts5=True)
    stap = schema.dead_subtree().split(";\n")[-1].rstrip(";").replace("NEW.id", ":id")
    assert stap.startswith("UPDATE photo_geo")
    with sqlite3.connect(path) as con:
        plan = plan_van(con, stap, {"id": "x"})
    assert "SEARCH photo_geo USING INDEX sqlite_autoindex_photo_geo_1 (node_id=?)" in plan, plan
    assert "ix_node_mpath (mpath>? AND mpath<?)" in plan, plan
    assert "SCAN photo_geo" not in plan, plan
    assert "SCAN node" not in plan, plan


@pytest.mark.zoek_perf
async def test_een_map_met_10000_fotos_met_plek(tmp_path):
    """Wat het bijhouden van photo_geo kost: een map met 10.000 foto's met plek, waarvan 2.000
    in de prullenbak, in de prullenbak, terug en verplaatst. Gemeten (3.53.1): 0,37, 0,31-0,34
    en 0,23 s; zonder plekken 0,17-0,22, 0,13-0,19 en 0,20-0,23 s. Elk < 1 s (Keuze 11)."""
    from tests.search.test_triggers_dead import _boom, _gebruiker

    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "head", fts5=True)
    await get_engine(settings).dispose()
    path = str(settings.data_dir / "platform.db")
    with sqlite3.connect(path, isolation_level=None) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN")
        user, root = _gebruiker(con.execute)
        elders = _boom(con.execute, user, root, "elders", "dir")
        folder = _boom(con.execute, user, root, "groot", "dir")
        kids = [_boom(con.execute, user, folder, f"f{i}", "file") for i in range(10_000)]
        con.executemany(
            "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source) "
            "VALUES (?, ?, 52.37, 4.89, 33650, 21530, 'exif')",
            [(kid, user) for kid in kids],
        )
        for kid in kids[:2000]:
            con.execute("UPDATE node SET deleted_at = ? WHERE id = ?", (NOW, kid))
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
        levend = con.execute("SELECT count(*) FROM photo_geo WHERE dead = 0").fetchone()[0]
    con.close()
    print(f"\nphoto_geo bij 10.000 foto's ({sqlite3.sqlite_version}): {tijd}")
    assert levend == 8000
    assert all(t < 1.0 for t in tijd.values()), tijd
    async with get_engine(settings).connect() as conn:
        assert await conn.run_sync(check_tree) == 3 + 10_000
