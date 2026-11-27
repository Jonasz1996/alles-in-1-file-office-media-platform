"""De triggers van migratie 0013 op PostgreSQL (release 0.8.2, P2; docs/fase-6/i3/pg-0013.md).

Ze doen wat de triggers van 0010-0012 zonder FTS op SQLite doen. Hier:

- elke trigger van schema.TRIGGERS heeft een regel in TWINS (en dezelfde in pg-0013.md);
- op PostgreSQL na elke stap de boom (num, mpath, dead) en photo_geo gelijk aan de
  Python-referentie check_tree (dezelfde als bij SQLite), met dezelfde stappen als
  tests/search/test_triggers_geo.py, test_triggers_gezichten.py en test_triggers_beeld.py;
- twee transacties tegelijk: een nieuwe node onder een map terwijl een map erboven verhuist of
  naar de prullenbak gaat, in beide volgordes; en een stresstest met vier verbindingen;
- een database met een andere collatie (ICU) dan "C": de bereiken op mpath kloppen toch;
- wat fase6_fill gebruikt: de wekelijkse controle en de knop "Index herbouwen" (409).
"""

from __future__ import annotations

import asyncio
import importlib.util
import random
import re
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import db as app_db
from app import photodate
from app.config import Settings
from app.db import get_engine
from app.migrate import upgrade
from app.models import (
    Face,
    FacePerson,
    FaceScan,
    Node,
    Person,
    PhotoGeo,
    PhotoOcr,
    PhotoPerson,
    User,
)
from app.search import purge, rebuild, schema, weekcheck
from tests.conftest import PG_URL
from tests.factories import create_file, create_folder, create_user, maker
from tests.pgboom import ZERO, boom, dsn, fill, nalopen, rows, run

pytestmark = pytest.mark.anyio

API = Path(__file__).resolve().parents[1]
DOC = API.parent / "docs" / "fase-6" / "i3" / "pg-0013.md"
FTS = "FTS -> 0014"
DAG = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)
NU = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


def _migration_0014():
    spec = importlib.util.spec_from_file_location(
        "m0014", API / "migrations/versions/0014_zoekindex_postgres.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration():
    spec = importlib.util.spec_from_file_location(
        "m0013", API / "migrations/versions/0013_postgres.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doc_table() -> dict[str, str]:
    """De tabel "SQLite-trigger | PostgreSQL" in pg-0013.md."""
    found = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = cells[0].strip("`")
        if name in schema.TRIGGERS:
            found[name] = cells[1].replace("`", "")
    return found


def test_elke_sqlite_trigger_heeft_een_regel():
    """Geen trigger van 0010-0012 zonder tweeling, zonder "FTS -> 0014" of zonder reden; de
    FTS-regels zijn precies de triggers die zonder FTS5 niet bestaan."""
    m = _migration()
    assert set(m.TWINS) == set(schema.TRIGGERS)
    fts = {k for k, v in m.TWINS.items() if v == FTS}
    assert fts == set(schema.TRIGGERS) - set(schema.TRIGGERS_NO_FTS)
    ours = {name for name, _table in m.TRIGGERS}
    for name, twin in m.TWINS.items():
        assert twin == FTS or twin in ours or twin.startswith("niet nodig"), (name, twin)
    # Elke trigger van 0013 komt van een trigger van SQLite, behalve het slot van de boom
    assert ours - set(m.TWINS.values()) == {"node_bs_tree"}
    assert set(m.TRIGGER_SQL) == ours
    assert _doc_table() == m.TWINS


# --- Op PostgreSQL ----------------------------------------------------------------------------


@pytest.mark.pg
async def test_de_triggers_functies_en_kolommen_staan_er(settings):
    m = _migration()
    found = await rows(settings, "SELECT tgname, tgrelid::regclass::text FROM pg_trigger "
                                 "WHERE NOT tgisinternal")  # fmt: skip
    # Met de triggers van de zoekindex (0014)
    zoek = {(name, table) for name, table, _when, _func in _migration_0014().TRIGGERS}
    # En de rangvector van zoeken (0020)
    zoek.add(("fts_node_rank", "fts_node"))
    assert set(found) == set(m.TRIGGERS) | zoek
    for signature in m.FUNCTIONS:
        [(ok,)] = await rows(settings, "SELECT to_regprocedure(:s) IS NOT NULL", s=signature)
        assert ok, signature
    [(collation,)] = await rows(
        settings,
        "SELECT collation_name FROM information_schema.columns "
        "WHERE table_name = 'node' AND column_name = 'mpath'",
    )
    assert collation == "C"
    [(identity,)] = await rows(
        settings,
        "SELECT is_identity FROM information_schema.columns "
        "WHERE table_name = 'face' AND column_name = 'seq'",
    )
    assert identity == "YES"
    for table, column in m.BIGINT:
        [(kind,)] = await rows(
            settings,
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c",
            t=table,
            c=column,
        )
        assert kind == "bigint", (table, column)
    indexes = {r[0] for r in await rows(settings, "SELECT indexname FROM pg_indexes")}
    assert {"ix_node_timeline", *m.FACE_INDEXES} <= indexes


async def _levend(settings, owner: uuid.UUID) -> set[str]:
    """De namen van de bestanden van owner die niet in de prullenbak liggen (ook niet via een map
    erboven), na check_tree."""
    await nalopen(settings)
    found = await rows(
        settings,
        "SELECT name FROM node WHERE owner_id = :o AND kind = 'file' AND dead = 0",
        o=owner,
    )
    return {r[0].split(".")[0] for r in found}


def _plek(node: uuid.UUID, owner: uuid.UUID, **extra) -> PhotoGeo:
    return PhotoGeo(node_id=node, owner_id=owner, lat=52.37, lon=4.89, x16=33_650, y16=21_530,
                    source="exif", **extra)  # fmt: skip


@pytest.mark.pg
async def test_de_boom_bij_elke_stap(settings):
    """Prullenbak in en uit (ook via een map erboven), verplaatsen, terugzetten met een nieuwe
    ouder in één UPDATE, meer rijen in één UPDATE en een map in zichzelf: na elke stap gelijk aan
    check_tree, en photo_geo volgt."""
    ids = await boom(settings)
    eva = ids["eva"]
    await run(settings, "UPDATE node SET taken_at = :t WHERE id = :id", id=ids["strand"], t=DAG)
    async with maker(settings)() as session:
        # Het ORM schrijft dead = 0 en taken_at = NULL; geo_biu zet die van de node
        session.add_all([_plek(ids[n], eva) for n in ("strand", "schelp", "notities")])
        await session.commit()
    assert await _levend(settings, eva) == {"verslag", "notities"}
    assert set(await rows(settings, "SELECT dead, taken_at FROM photo_geo")) == {
        (0, None), (1, None), (1, DAG)
    }  # fmt: skip

    stappen = [
        ("UPDATE node SET deleted_at = NULL WHERE id = :id", {"id": ids["vakantie"]},
         {"verslag", "notities", "strand", "schelp"}),
        ("UPDATE node SET deleted_at = :t WHERE id = :id", {"id": ids["diep"], "t": NU},
         {"verslag", "notities", "strand"}),
        ("UPDATE node SET parent_id = :p WHERE id = :id", {"id": ids["schelp"], "p": ids["werk"]},
         {"verslag", "notities", "strand", "schelp"}),
        ("UPDATE node SET deleted_at = :t WHERE id = :id", {"id": ids["werk"], "t": NU},
         {"notities", "strand"}),
        ("UPDATE node SET parent_id = :p WHERE id = :id", {"id": ids["werk"], "p": ids["vakantie"]},
         {"notities", "strand"}),
        ("UPDATE node SET deleted_at = NULL WHERE id = :id", {"id": ids["werk"]},
         {"notities", "strand", "verslag", "schelp"}),
        ("UPDATE node SET deleted_at = :t WHERE id = :id", {"id": ids["notities"], "t": NU},
         {"strand", "verslag", "schelp"}),
        ("UPDATE node SET deleted_at = :t WHERE id = :id", {"id": ids["vakantie"], "t": NU},
         set()),
        ("UPDATE node SET deleted_at = NULL WHERE id = :id", {"id": ids["vakantie"]},
         {"strand", "verslag", "schelp"}),
        # Terugzetten zoals trash.restore_node: ouder, naam en prullenbak in één UPDATE (beide
        # triggers in één statement)
        ("UPDATE node SET parent_id = :p, name = 'notities 2.txt', deleted_at = NULL "
         "WHERE id = :id", {"id": ids["notities"], "p": ids["verslagen"]},
         {"strand", "verslag", "schelp", "notities 2"}),
        # Twee rijen in één UPDATE, de een onder de ander
        ("UPDATE node SET deleted_at = :t WHERE id IN (:a, :b)",
         {"a": ids["werk"], "b": ids["verslagen"], "t": NU}, {"strand"}),
        ("UPDATE node SET deleted_at = NULL WHERE id IN (:a, :b)",
         {"a": ids["werk"], "b": ids["verslagen"]}, {"strand", "verslag", "schelp", "notities 2"}),
        ("UPDATE node SET parent_id = :p WHERE id IN (:a, :b)",
         {"a": ids["verslagen"], "b": ids["diep"], "p": ids["root"]},
         {"strand", "verslag", "schelp", "notities 2"}),
        ("UPDATE node SET deleted_at = :t WHERE id = :id", {"id": ids["diep"], "t": NU},
         {"strand", "verslag", "schelp", "notities 2"}),
        ("UPDATE node SET parent_id = :p WHERE id = :id",
         {"id": ids["verslagen"], "p": ids["diep"]}, {"strand", "schelp"}),
    ]  # fmt: skip
    for sql, params, levend in stappen:
        await run(settings, sql, **params)
        assert await _levend(settings, eva) == levend, sql

    # Een map in zichzelf of in een map eronder: een fout, en niets veranderd
    voor = await rows(settings, "SELECT id, num, mpath, dead FROM node ORDER BY id")
    for map_id, onder in ((ids["vakantie"], ids["werk"]), (ids["werk"], ids["werk"])):
        with pytest.raises(sa.exc.DBAPIError, match="kan niet in zichzelf"):
            await run(settings, "UPDATE node SET parent_id = :p WHERE id = :id", id=map_id, p=onder)
    assert await rows(settings, "SELECT id, num, mpath, dead FROM node ORDER BY id") == voor

    # Een nieuwe node krijgt een nieuw num, achter het hoogste, en zijn pad onder zijn ouder
    async with maker(settings)() as session:
        eva_user = await _user(session, eva)
        nieuw = await create_folder(session, eva_user, ids["werk"], "nieuw")
        await session.commit()
    [(num, mpath, top)] = await rows(
        settings,
        "SELECT n.num, n.mpath, (SELECT max(num) FROM node WHERE id <> n.id) FROM node n "
        "WHERE n.id = :id",
        id=nieuw.id,
    )
    [(werk_pad,)] = await rows(settings, "SELECT mpath FROM node WHERE id = :id", id=ids["werk"])
    assert num == top + 1 and mpath == f"{werk_pad}{num}/"
    await nalopen(settings)


async def _user(session, user_id: uuid.UUID) -> User:
    found = await session.get(User, user_id)
    assert found is not None
    return found


@pytest.mark.pg
async def test_photo_geo_alleen_bij_een_bestand_van_de_eigenaar(settings):
    """geo_biu: zoals geo_bi, geo_ai en geo_bu van SQLite, met dezelfde meldingen."""
    ids = await boom(settings)
    eva, bram = ids["eva"], ids["bram"]
    sessions = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with sessions() as session:
        album = Node(owner_id=eva, name="zomer", kind="album")
        session.add(album)
        await session.commit()

    async def weigert(rij: PhotoGeo) -> None:
        async with sessions() as session:
            session.add(rij)
            with pytest.raises(sa.exc.DBAPIError, match="alleen bij een bestand"):
                await session.commit()

    await weigert(_plek(ids["notities"], bram))
    await weigert(_plek(ids["werk"], eva))
    await weigert(_plek(album.id, eva))
    await weigert(_plek(uuid.uuid4(), eva))
    assert await rows(settings, "SELECT count(*) FROM photo_geo") == [(0,)]

    async with sessions() as session:
        session.add(_plek(ids["notities"], eva))
        await session.commit()
    for sql, params in (
        ("UPDATE photo_geo SET dead = 1 WHERE node_id = :id", {}),
        ("UPDATE photo_geo SET taken_at = :t WHERE node_id = :id", {"t": DAG}),
        ("UPDATE photo_geo SET owner_id = :o WHERE node_id = :id", {"o": bram}),
        ("UPDATE photo_geo SET node_id = :n WHERE node_id = :id", {"n": ids["strand"]}),
    ):
        with pytest.raises(sa.exc.DBAPIError, match="volgen de node"):
            await run(settings, sql, id=ids["notities"], **params)
    async with sessions() as session:
        plek = await session.get(PhotoGeo, ids["notities"])
        assert plek is not None
        plek.dead = 1
        with pytest.raises(sa.exc.DBAPIError, match="volgen de node"):
            await session.commit()
    # De plek zelf mag veranderen, ook een place_id boven int32 (BIGINT sinds 0013)
    async with sessions() as session:
        plek = await session.get(PhotoGeo, ids["notities"])
        assert plek is not None
        plek.place_id, plek.country = 3_000_000_000, "NL"
        await session.commit()
    # taken_at volgt de node (node_au_taken), ook terug naar NULL
    for value in (DAG, None):
        await run(settings, "UPDATE node SET taken_at = :t WHERE id = :id", id=ids["notities"],
                  t=value)  # fmt: skip
        assert await rows(settings, "SELECT taken_at, place_id FROM photo_geo") == [
            (value, 3_000_000_000)
        ]
    # Bram krijgt de node: de plek gaat weg
    await run(settings, "UPDATE node SET owner_id = :o, parent_id = :p WHERE id = :id",
              o=bram, p=ids["bram_root"], id=ids["notities"])  # fmt: skip
    assert await rows(settings, "SELECT count(*) FROM photo_geo") == [(0,)]
    await nalopen(settings)


async def _count(settings, table: str, node: uuid.UUID) -> int:
    [(n,)] = await rows(settings, f"SELECT count(*) FROM {table} WHERE node_id = :n", n=node)
    return n


async def _gen(settings) -> dict[uuid.UUID, int]:
    return dict(await rows(settings, "SELECT user_id, gen FROM beeld_owner"))


async def _gezichten(settings, ids) -> dict[str, uuid.UUID]:
    """Een foto van Eva met vier gezichten (bevestigd, voorstel, groep, verborgen), Oma, een scan,
    tekst, een plek en Oma op de foto; en een tweede foto met een gezicht die nooit meegaat."""
    eva = ids["eva"]
    out: dict[str, uuid.UUID] = {}
    async with maker(settings)() as session:
        user = await _user(session, eva)
        for key in ("foto", "ander"):
            out[key] = (await create_file(session, user, None, f"{key}.jpg", data=key.encode(),
                                          mime="image/jpeg")).id  # fmt: skip
        oma = Person(owner_id=eva, name="Oma", search_name="oma")
        session.add(oma)
        await session.flush()
        out["person"] = oma.id
        for key, cluster, hidden in (
            ("ja", None, 0),
            ("voorstel", None, 0),
            ("groep", 7, 0),
            ("verborgen", None, 1),
        ):
            face = Face(node_id=out["foto"], owner_id=eva, x=0.1, y=0.1, w=0.2, h=0.2, score=0.9,
                        px=80, vec=None if hidden else b"\x01" * 16, version=1, cluster=cluster,
                        suggest_id=oma.id if key == "voorstel" else None,
                        suggest_score=0.8 if key == "voorstel" else None,
                        suggest_why="x" if key == "voorstel" else None, hidden=hidden)  # fmt: skip
            session.add(face)
            await session.flush()
            out[key] = face.id
        session.add(FacePerson(face_id=out["ja"], person_id=oma.id, owner_id=eva, verdict="ja"))
        session.add(Face(node_id=out["ander"], owner_id=eva, x=0, y=0, w=1, h=1, score=1, px=80,
                         vec=b"\x00", version=1, hidden=0))  # fmt: skip
        for node in (out["foto"], out["ander"]):
            session.add(FaceScan(node_id=node, owner_id=eva, version=1, state="done", prio="x"))
        session.add(PhotoOcr(node_id=out["foto"], owner_id=eva, text="tekst", words=b"\x00"))
        session.add(_plek(out["foto"], eva))
        session.add(PhotoPerson(person_id=oma.id, node_id=out["foto"], owner_id=eva, x=0.1,
                                y=0.1, w=0.2, h=0.2))  # fmt: skip
        await session.commit()
    return out


@pytest.mark.pg
async def test_nieuwe_inhoud_en_een_andere_eigenaar(settings):
    """node_au_content: zoals node_bu_text (zonder FTS) en node_au_owner van 0010,
    node_au_blob_face en node_au_owner_face van 0011, node_au_beeld van 0012."""
    ids = await boom(settings)
    eva, bram = ids["eva"], ids["bram"]
    made = await _gezichten(settings, ids)
    foto, ander = made["foto"], made["ander"]
    [(jpg,)] = await rows(settings, "SELECT blob_id FROM node WHERE id = :n", n=ids["strand"])
    [(doc,)] = await rows(settings, "SELECT blob_id FROM node WHERE id = :n", n=ids["verslag"])
    [(txt,)] = await rows(settings, "SELECT blob_id FROM node WHERE id = :n", n=ids["notities"])
    gen = (await _gen(settings))[eva]
    assert bram not in await _gen(settings)

    # Dezelfde inhoud, een andere naam, een andere map, de prullenbak in en uit: niets
    await run(
        settings, "UPDATE node SET blob_id = blob_id, name = 'oma2.jpg' WHERE id = :n", n=foto
    )
    await run(settings, "UPDATE node SET parent_id = :p WHERE id = :n", p=ids["werk"], n=foto)
    for value in (NU, None):
        await run(settings, "UPDATE node SET deleted_at = :v WHERE id = :n", v=value, n=foto)
    for table in ("face", "face_scan", "photo_ocr", "photo_geo", "photo_person"):
        assert await _count(settings, table, foto) > 0, table
    assert (await _gen(settings))[eva] == gen
    # Nieuwe inhoud van een tekstbestand: niets voor beeld
    await run(settings, "UPDATE node SET blob_id = :b WHERE id = :n", b=doc, n=ids["notities"])
    assert (await _gen(settings))[eva] == gen

    # Nieuwe inhoud van de foto: gezichten, scan, tekst en plek weg; Oma blijft, zonder kader;
    # beeld twee keer voor Eva (oude en nieuwe eigenaar)
    await run(settings, "UPDATE node SET blob_id = :b WHERE id = :n", b=jpg, n=foto)
    for table in ("face", "face_scan", "photo_ocr", "photo_geo"):
        assert await _count(settings, table, foto) == 0, table
    assert await rows(settings, "SELECT x, y, w, h FROM photo_person WHERE node_id = :n",
                      n=foto) == [(None, None, None, None)]  # fmt: skip
    assert await _count(settings, "face", ander) == 1
    assert await _count(settings, "face_scan", ander) == 1
    assert (await _gen(settings))[eva] == gen + 2
    # Nieuwe inhoud die geen beeld meer is: ook; daarna niet meer
    await run(settings, "UPDATE node SET blob_id = :b, mime = 'text/plain' WHERE id = :n",
              b=txt, n=foto)  # fmt: skip
    assert (await _gen(settings))[eva] == gen + 4
    await run(settings, "UPDATE node SET blob_id = :b WHERE id = :n", b=doc, n=foto)
    assert (await _gen(settings))[eva] == gen + 4
    await run(settings, "UPDATE node SET blob_id = :b, mime = 'image/jpeg' WHERE id = :n",
              b=jpg, n=foto)  # fmt: skip
    assert (await _gen(settings))[eva] == gen + 6

    # Een andere eigenaar: alles van de foto weg (ook Oma op de foto), allebei één hoger
    await run(settings, "UPDATE node SET owner_id = :o, parent_id = :p WHERE id = :n",
              o=bram, p=ids["bram_root"], n=ander)  # fmt: skip
    for table in ("face", "face_scan", "photo_ocr", "photo_geo", "photo_person"):
        assert await _count(settings, table, ander) == 0, table
    assert await _gen(settings) == {eva: gen + 7, bram: 1}
    await nalopen(settings)

    # Definitief wissen: één hoger; een gebruiker wissen met zijn foto's: geen fout
    await run(settings, "DELETE FROM node WHERE id = :n", n=foto)
    assert (await _gen(settings))[eva] == gen + 8
    await run(settings, 'DELETE FROM "user" WHERE id = :u', u=bram)
    assert bram not in await _gen(settings)
    assert await rows(settings, "SELECT count(*) FROM node WHERE owner_id = :u", u=bram) == [(0,)]


@pytest.mark.pg
async def test_nieuw_beeld_en_niet_nieuw(settings):
    """node_ai_beeld: alleen een nieuw beeld telt."""
    ids = await boom(settings)
    eva = ids["eva"]
    gen = (await _gen(settings))[eva]
    async with maker(settings)() as session:
        user = await _user(session, eva)
        await create_folder(session, user, None, "map")
        for name, mime in (("brief.txt", "text/plain"), ("v.pdf", "application/pdf"),
                           ("film.mp4", "video/mp4")):  # fmt: skip
            await create_file(session, user, None, name, data=name.encode(), mime=mime)
        await session.commit()
    assert (await _gen(settings))[eva] == gen
    async with maker(settings)() as session:
        user = await _user(session, eva)
        await create_file(session, user, None, "a.jpg", data=b"a", mime="image/jpeg")
        await create_file(session, user, None, "b.png", data=b"b", mime="image/png")
        await session.commit()
    assert (await _gen(settings))[eva] == gen + 2


@pytest.mark.pg
async def test_een_persoon_wissen(settings):
    ids = await boom(settings)
    made = await _gezichten(settings, ids)
    await run(settings, "DELETE FROM person WHERE id = :p", p=made["person"])
    found = {
        r[0]: r[1:]
        for r in await rows(
            settings,
            "SELECT id, vec IS NULL, hidden, suggest_id, suggest_score, suggest_why, cluster "
            "FROM face",
        )
    }
    # Het bevestigde gezicht: "niet tonen", zonder vector (Keuze 63)
    assert found[made["ja"]] == (True, 1, None, None, None, None)
    # Het voorstel: suggest_id weg (SET NULL), de vector blijft
    assert found[made["voorstel"]][:3] == (False, 0, None)
    assert found[made["groep"]] == (False, 0, None, None, None, 7)
    assert found[made["verborgen"]] == (True, 1, None, None, None, None)
    assert await rows(settings, "SELECT count(*) FROM face_person") == [(0,)]


# --- Twee transacties tegelijk ----------------------------------------------------------------

INSERT = (
    "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, modified_at, "
    "search_name) VALUES ($1, $2, $3, $4, $5, 0, now(), now(), $6)"
)


async def _new(conn, parent: uuid.UUID, owner: uuid.UUID, kind: str = "file") -> uuid.UUID:
    node = uuid.uuid4()
    name = f"{kind}-{node.hex[:12]}"
    await conn.execute(INSERT, node, parent, owner, name, kind, name)
    return node


async def _blocks(task: asyncio.Task) -> None:
    """De taak wacht op de andere transactie (een slot), en is dus na een halve seconde niet
    klaar."""
    await asyncio.sleep(0.5)
    assert not task.done(), "wacht niet op de andere transactie"


@pytest.mark.pg
@pytest.mark.parametrize("wat", ["verplaatsen", "prullenbak"])
@pytest.mark.parametrize("eerst", ["nieuw", "map"])
async def test_nieuw_onder_een_map_terwijl_een_map_erboven_verandert(settings, wat, eerst):
    """T1 zet een bestand in werk/verslagen; T2 verplaatst werk naar vakantie/diep (in de
    prullenbak) of zet werk in de prullenbak. Wie als tweede komt, wacht op de eerste; daarna
    heeft het nieuwe bestand het pad en de dead van zijn ouder, in beide volgordes."""
    import asyncpg

    ids = await boom(settings)
    if wat == "verplaatsen":
        statement = ("UPDATE node SET parent_id = $1 WHERE id = $2", ids["diep"], ids["werk"])
    else:
        statement = ("UPDATE node SET deleted_at = now() WHERE id = $1", ids["werk"])
    t1 = await asyncpg.connect(dsn(settings))
    t2 = await asyncpg.connect(dsn(settings))
    try:
        if eerst == "nieuw":
            tx = t1.transaction()
            await tx.start()
            nieuw = await _new(t1, ids["verslagen"], ids["eva"])
            task = asyncio.create_task(t2.execute(*statement))
            await _blocks(task)
            await tx.commit()
            await task
        else:
            tx = t2.transaction()
            await tx.start()
            await t2.execute(*statement)
            nieuw_task = asyncio.create_task(_new(t1, ids["verslagen"], ids["eva"]))
            await _blocks(nieuw_task)
            await tx.commit()
            nieuw = await nieuw_task
    finally:
        await t1.close()
        await t2.close()
    await nalopen(settings)
    [(pad, dead)] = await rows(settings, "SELECT mpath, dead FROM node WHERE id = :n", n=nieuw)
    [(ouder,)] = await rows(settings, "SELECT mpath FROM node WHERE id = :n", n=ids["verslagen"])
    assert pad.startswith(ouder) and dead == 1


async def _blobs(conn, n: int) -> list[str]:
    """n inhouden (rijen van blob), voor bestanden met een opnamedatum."""
    shas = [uuid.uuid4().hex * 2 for _ in range(n)]
    for sha in shas:
        await conn.execute(
            "INSERT INTO blob (sha256, size, ref_count, created_at) VALUES ($1, 1, 1, now())", sha
        )
    return shas


async def _met_inhoud(conn, node: uuid.UUID, sha: str) -> None:
    """Het bestand krijgt inhoud sha en staat op de tijdlijn (taken_day)."""
    await conn.execute(
        "UPDATE node SET blob_id = $1, taken_at = now(), taken_day = 20260714 WHERE id = $2",
        sha,
        node,
    )


@pytest.mark.pg
@pytest.mark.parametrize(
    ("wat", "erboven"),
    [("wissen", "verplaatsen"), ("datum", "verplaatsen"), ("datum", "prullenbak")],
)
async def test_wissen_en_een_datum_terwijl_een_map_erboven_verandert(settings, wat, erboven):
    """Review PG13-1. T1 is de app in één transactie met meer dan één statement:
    purge.delete_nodes (definitief wissen) of photodate.retime_nodes (een nieuwe opnamedatum),
    eerst voor het bestand met het hoogste pad onder werk, dan voor dat met het laagste. Tussen
    die twee verplaatst T2 werk of zet hem in de prullenbak; T2 zet de submap op volgorde van
    mpath vast. Zonder het slot van de boom in T1 wachtten ze op elkaar en brak PostgreSQL er
    een af (40P01). Nu wacht T2 op T1, en daarna klopt de boom."""
    import asyncpg

    ids = await boom(settings)
    conn = await asyncpg.connect(dsn(settings))
    try:
        map_ = await _new(conn, ids["werk"], ids["eva"], "dir")
        kids = [await _new(conn, map_, ids["eva"]) for _ in range(6)]
        shas = await _blobs(conn, len(kids))
        for kid, sha in zip(kids, shas, strict=True):
            if wat == "wissen":
                await conn.execute("UPDATE node SET deleted_at = now() WHERE id = $1", kid)
            else:
                await _met_inhoud(conn, kid, sha)
        order = await conn.fetch(
            "SELECT id, blob_id FROM node WHERE id = ANY($1) ORDER BY mpath", kids
        )
    finally:
        await conn.close()
    first, last = order[-1], order[0]
    at = datetime(2001, 2, 3, 4, 5, tzinfo=UTC)

    async def raak(session, row) -> None:
        if wat == "wissen":
            await purge.delete_nodes(session, [row["id"]])
        else:
            await photodate.retime_nodes(session, [(row["blob_id"], at)])

    if erboven == "verplaatsen":
        statement = ("UPDATE node SET parent_id = $1 WHERE id = $2", ids["diep"], ids["werk"])
    else:
        statement = ("UPDATE node SET deleted_at = now() WHERE id = $1", ids["werk"])
    t2 = await asyncpg.connect(dsn(settings))
    try:
        async with maker(settings)() as session:
            await raak(session, first)
            task = asyncio.create_task(t2.execute(*statement))
            await _blocks(task)
            await raak(session, last)
            await session.commit()
        await task
    finally:
        await t2.close()
    await nalopen(settings)
    assert await fill(settings, repair=False) == ZERO
    touched = [first["id"], last["id"]]
    if wat == "wissen":
        assert await rows(settings, "SELECT id FROM node WHERE id = ANY(:t)", t=touched) == []
    else:
        found = await rows(settings, "SELECT taken_at FROM node WHERE id = ANY(:t)", t=touched)
        assert found == [(at,), (at,)]


OPS = ("map", "bestand", "bestand", "kopie", "verplaats", "verplaats", "prullenbak", "terug",
       "herstel", "taken", "plek", "wis", "datum")  # fmt: skip


async def _werker(
    settings: Settings, owners: list[uuid.UUID], shas: list[str], seed: int, n: int
) -> Counter:
    """n willekeurige bewerkingen op de boom, elk in een eigen transactie (zoals de app). wis en
    datum gaan door de app zelf (purge.delete_nodes en photodate.retime_nodes), in een
    transactie met meer dan één statement (review PG13-1)."""
    import asyncpg

    rnd = random.Random(seed)
    done: Counter = Counter()
    conn = await asyncpg.connect(dsn(settings))
    try:
        for _ in range(n):
            owner = rnd.choice(owners)
            nodes = await conn.fetch(
                "SELECT id, kind, parent_id, deleted_at FROM node WHERE owner_id = $1", owner
            )
            dirs = [r["id"] for r in nodes if r["kind"] == "dir"]
            files = [r["id"] for r in nodes if r["kind"] == "file"]
            movable = [r["id"] for r in nodes if r["parent_id"] is not None]
            trashed = [r["id"] for r in nodes if r["kind"] == "file" and r["deleted_at"]]
            op = rnd.choice(OPS)
            if not movable and op in ("verplaats", "prullenbak", "terug", "herstel"):
                op = "map"
            if not trashed and op == "wis":
                op = "datum"
            if not files and op in ("taken", "plek"):
                op = "bestand"
            target = rnd.choice(files) if files else None
            try:
                if op == "map":
                    await _new(conn, rnd.choice(dirs), owner, "dir")
                elif op == "bestand":  # met inhoud en een opnamedatum, voor datum
                    async with conn.transaction():
                        node = await _new(conn, rnd.choice(dirs), owner)
                        await _met_inhoud(conn, node, rnd.choice(shas))
                elif op == "wis":  # definitief wissen: elk bestand een eigen DELETE
                    gone = rnd.sample(trashed, min(3, len(trashed)))
                    async with maker(settings)() as session:
                        await purge.delete_nodes(session, gone)
                        await session.commit()
                elif op == "datum":  # een nieuwe opnamedatum voor drie inhouden
                    hours = rnd.randint(0, 9999)
                    at = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=hours)
                    async with maker(settings)() as session:
                        await photodate.retime_nodes(
                            session, [(s, at) for s in rnd.sample(shas, 3)]
                        )
                        await session.commit()
                elif op == "kopie":  # een map en een bestand erin, in één transactie
                    async with conn.transaction():
                        folder = await _new(conn, rnd.choice(dirs), owner, "dir")
                        await _new(conn, folder, owner, "file")
                elif op == "verplaats":
                    await conn.execute("UPDATE node SET parent_id = $1 WHERE id = $2",
                                       rnd.choice(dirs), rnd.choice(movable))  # fmt: skip
                elif op == "prullenbak":
                    await conn.execute("UPDATE node SET deleted_at = now() WHERE id = $1",
                                       rnd.choice(movable))  # fmt: skip
                elif op == "terug":
                    await conn.execute("UPDATE node SET deleted_at = NULL WHERE id = $1",
                                       rnd.choice(movable))  # fmt: skip
                elif op == "herstel":  # trash.restore_node: ouder en prullenbak in één UPDATE
                    await conn.execute(
                        "UPDATE node SET parent_id = $1, deleted_at = NULL WHERE id = $2",
                        rnd.choice(dirs),
                        rnd.choice(movable),
                    )
                elif op == "taken":
                    await conn.execute(
                        "UPDATE node SET taken_at = now() - $1 * interval '1 hour' WHERE id = $2",
                        rnd.randint(0, 9999),
                        rnd.choice(files),
                    )
                elif op == "plek":
                    await conn.execute(
                        "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source) "
                        "VALUES ($1, $2, 52.0, 4.0, 1, 2, 'exif') ON CONFLICT (node_id) DO NOTHING",
                        target,
                        owner,
                    )
                done[op] += 1
            except asyncpg.RaiseError as e:
                if "in zichzelf" in str(e):
                    done["kring"] += 1
                    continue
                # Een plek bij een bestand dat een andere verbinding net definitief wiste (wis)
                assert op == "plek" and "photo_geo" in str(e), e
                assert not await conn.fetchval("SELECT count(*) FROM node WHERE id = $1", target)
                done["weg"] += 1
    finally:
        await conn.close()
    return done


@pytest.mark.pg
async def test_vier_verbindingen_door_elkaar(settings, monkeypatch):
    """4 verbindingen x 300 bewerkingen op de bomen van twee eigenaars: nooit 40P01 (een
    DeadlockDetectedError zou de test laten falen), en daarna gelijk aan check_tree. Wissen
    doet één DELETE per node (purge.IN_LIMIT 1): zoveel mogelijk statements in één transactie.
    300 en niet 200 sinds wis en datum erbij kwamen (PG13-1): wis haalt nodes weg, en de boom
    moet groot blijven (n > 300)."""
    import asyncpg

    monkeypatch.setattr(purge, "IN_LIMIT", 1)
    ids = await boom(settings)
    owners = [ids["eva"], ids["bram"]]
    conn = await asyncpg.connect(dsn(settings))
    try:
        shas = await _blobs(conn, 8)
    finally:
        await conn.close()
    start = time.monotonic()
    found = await asyncio.gather(*(_werker(settings, owners, shas, seed, 300) for seed in range(4)))
    took = time.monotonic() - start
    total = sum(found, Counter())
    print(f"\n4 x 300 bewerkingen in {took:.1f} s: {dict(sorted(total.items()))}")
    assert sum(total.values()) == 1200
    assert total["verplaats"] > 50 and total["prullenbak"] > 0 and total["kopie"] > 0, total
    assert total["wis"] > 0 and total["datum"] > 0, total
    n = await nalopen(settings)
    assert n > 300
    counts = await fill(settings, repair=False)
    assert {k: counts[k] for k in weekcheck.DRIFT} == {k: 0 for k in weekcheck.DRIFT}


# --- Een andere collatie ----------------------------------------------------------------------

ICU = "en-US-u-ka-shifted"


@pytest.mark.pg
async def test_een_database_met_een_icu_collatie(tmp_path):
    """Met ICU (leestekens tellen pas als laatste) klopt het bereik mpath >= p AND mpath <
    mpath_end(p) niet met de collatie van de database; met COLLATE "C" op de kolom wel."""
    import asyncpg

    name = "t_icu_" + uuid.uuid4().hex
    admin = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        try:
            await admin.execute(
                f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8' "
                f"LOCALE_PROVIDER icu ICU_LOCALE '{ICU}' LOCALE 'C.UTF-8'"
            )
        except asyncpg.PostgresError as e:
            pytest.skip(f"geen ICU-collatie {ICU} op deze server: {e}")
        url = PG_URL.rsplit("/", 1)[0] + "/" + name
        settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8,
                            database_url=url)  # fmt: skip
        try:
            await upgrade(settings)
            await _icu_boom(settings)
        finally:
            await app_db.get_engine(settings).dispose()
            app_db._engine.cache_clear()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


RANGE = (
    "SELECT d.id, n.id FROM node d JOIN node n ON n.mpath {c} >= d.mpath {c} "
    "AND n.mpath {c} < mpath_end(d.mpath) {c} WHERE d.kind = 'dir'"
)


async def _icu_boom(settings: Settings) -> None:
    rnd = random.Random(13)
    async with maker(settings)() as session:
        user = await create_user(session, "icu@example.nl")
        dirs = [user.root_id]
        for i in range(60):
            folder = await create_folder(session, user, rnd.choice(dirs), f"m{i}")
            dirs.append(folder.id)
            await create_file(session, user, folder.id, f"f{i}.txt", data=str(i).encode())
        await session.commit()
    for i in range(10):
        a, b = rnd.sample(dirs[1:], 2)
        try:
            await run(settings, "UPDATE node SET parent_id = :p WHERE id = :id", p=a, id=b)
        except sa.exc.DBAPIError as e:
            assert "in zichzelf" in str(e)
        if i % 3 == 0:
            await run(settings, "UPDATE node SET deleted_at = :t WHERE id = :id", t=NU, id=a)
    await nalopen(settings)
    paths = dict(await rows(settings, "SELECT id, mpath FROM node"))
    kinds = dict(await rows(settings, "SELECT id, kind FROM node"))
    want = {
        (d, n) for d, p in paths.items() if kinds[d] == "dir" for n, q in paths.items()
        if q.startswith(p)
    }  # fmt: skip
    assert set(await rows(settings, RANGE.format(c=""))) == want
    # De controle: met de collatie van de database zelf klopt hetzelfde bereik niet
    assert set(await rows(settings, RANGE.format(c='COLLATE "default"'))) != want


# --- Wat fase6_fill gebruikt ------------------------------------------------------------------


@pytest.mark.pg
async def test_de_wekelijkse_controle_vindt_en_herstelt(settings, caplog):
    """weekcheck op PostgreSQL: fase6_fill(repair => false) telt, fase6_fill() herstelt; op
    /beheer alleen aantallen."""
    ids = await boom(settings)
    await run(settings, "UPDATE node SET dead = 1 WHERE id = :id", id=ids["werk"])
    await run(settings, "UPDATE node SET mpath = '/kapot/' WHERE id = :id", id=ids["verslag"])
    with caplog.at_level("INFO", logger=weekcheck.log.name):
        found = await weekcheck.run(settings)
    assert found is not None and found.bad == 2
    [(nodes,)] = await rows(settings, "SELECT count(*) FROM node")
    assert found.sampled == nodes
    await nalopen(settings)
    assert re.search(r"2 verschil\(len\) met de bron, hersteld", caplog.text)
    assert "kapot" not in caplog.text
    # Pas na een week weer
    assert await weekcheck.run(settings) is None


@pytest.mark.pg
async def test_search_triggers_herstelt_op_postgresql(settings):
    """Review PG13-4: rebuild.restore_triggers (app.cli search-triggers, ook de bench) op
    PostgreSQL. Geen triggers van SQLite (dropped en created leeg), wel fase6_fill() met herstel,
    met de aantallen per soort: numbered (num), tree (mpath en dead) en photo_rows (photo_geo).
    Elk een ander aantal, zodat een verwisseling opvalt. Daarna klopt alles; een tweede keer
    geeft nullen."""
    ids = await boom(settings)
    # Een node zonder num (dus ook een ander pad), een verkeerde dead en een kapot pad: tree 3
    await run(settings, "UPDATE node SET num = NULL WHERE id = :id", id=ids["notities"])
    await run(settings, "UPDATE node SET dead = 1 - dead WHERE id = :id", id=ids["verslag"])
    await run(settings, "UPDATE node SET mpath = '/kapot/' WHERE id = :id", id=ids["werk"])
    # Twee rijen van photo_geo die niet volgen (de trigger even uit): hun foto's liggen in de
    # prullenbak, de rijen zeggen van niet
    await run(settings, "ALTER TABLE photo_geo DISABLE TRIGGER USER")
    for key in ("strand", "schelp"):
        await run(
            settings,
            "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source, dead) "
            "VALUES (:n, :o, 52, 4, 1, 2, 'exif', 0)",
            n=ids[key],
            o=ids["eva"],
        )
    await run(settings, "ALTER TABLE photo_geo ENABLE TRIGGER USER")

    found = await rebuild.restore_triggers(get_engine(settings))
    assert found == rebuild.Restored(numbered=1, tree=3, photo_rows=2)
    assert await fill(settings, repair=False) == ZERO
    await nalopen(settings)
    assert await rows(settings, "SELECT dead FROM photo_geo") == [(1,), (1,)]
    assert await rebuild.restore_triggers(get_engine(settings)) == rebuild.Restored()


@pytest.mark.pg
async def test_index_herbouwen_kan_op_postgresql(client, admin):
    """Sinds 0014 heeft PostgreSQL een eigen zoekindex: beheer kan hem herbouwen."""
    headers, _tokens = admin
    r = await client.post("/api/v1/admin/search/rebuild", json={"what": "text"}, headers=headers)
    assert r.status_code == 202, r.text
