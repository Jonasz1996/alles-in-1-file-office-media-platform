"""photo_rows.write en clear naast een verplaatsing of prullenbak erboven (0.8.2, P4; golf 1).

photometa schrijft de plek (photo_geo) voor veel bestanden in één transactie: per inhoud een
statement (executemany), en geo_biu zet bij elke nieuwe rij de node vast (FOR SHARE). Een
verplaatsing of prullenbak van een map erboven zet de submap vast op volgorde van mpath. Zonder
het slot van de boom in photometa kwamen ze elkaar tegen in een andere volgorde, en brak
PostgreSQL er een af (40P01), net als bij purge en retime (review PG13-1). Nu nemen write en
clear eerst het slot van de boom (db.lock_tree): wie als tweede komt, wacht.

T1 is de app (photo_rows.write of clear), eerst voor het bestand met het hoogste pad onder werk,
dan voor dat met het laagste. In beide volgordes: T1 eerst (T2 wacht op T1), of T2 eerst (T1
wacht op T2). Daarna klopt de boom, en photo_geo heeft de dead van de node.

De job geo.places (photo_rows.run_places) werkt tot PLACES_CHUNK rijen van photo_geo bij in één
transactie, op volgorde van node_id. Een prullenbak of verplaatsing erboven werkt photo_geo van
de submap bij in een andere volgorde. Zonder het slot van de boom in run_places brak PostgreSQL
er een af (40P01; w2.md, open punt 10); nu neemt run_places het eerst, net als write().

De job geo.off (photo_rows.run_off, na locatie "uit") wist per stuk tot CHUNK rijen van
photo_geo in één DELETE, in de volgorde van zijn plan (hier: zoals de rijen op schijf staan).
Een prullenbak of verplaatsing erboven werkt dezelfde rijen bij op volgorde van mpath. Zonder
het slot van de boom in elk stuk van run_off zetten ze de rijen andersom vast, en brak
PostgreSQL er een af (40P01; w4.md, open punt). Nu neemt elk stuk het slot eerst.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import photo_rows
from app.db import get_engine
from app.geo import load, places
from app.geo.types import Place
from tests.factories import maker
from tests.geo import bronnen
from tests.geo.hulp import AMSTERDAM
from tests.pgboom import ZERO, boom, dsn, fill, nalopen, rows

pytestmark = [pytest.mark.anyio, pytest.mark.pg]

SPOT = photo_rows.Spot(52.0907, 5.1214, None, "exif")
PLACE = Place(id=1, name="Utrecht", admin1="Utrecht", cc="NL", km=0.5)
INSERT = (
    "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, modified_at, "
    "search_name, blob_id) VALUES ($1, $2, $3, $4, $5, 0, now(), now(), $6, $7)"
)


async def _new(conn, parent: uuid.UUID, owner: uuid.UUID, kind: str, sha=None) -> uuid.UUID:
    node = uuid.uuid4()
    name = f"{kind}-{node.hex[:12]}"
    await conn.execute(INSERT, node, parent, owner, name, kind, name, sha)
    return node


async def _blocks(task: asyncio.Task) -> None:
    """De taak wacht op de andere transactie (een slot), en is dus na een halve seconde niet
    klaar."""
    await asyncio.sleep(0.5)
    assert not task.done(), "wacht niet op de andere transactie"


async def _map(settings, ids, geo: bool) -> list:
    """Een map onder werk met zes bestanden, elk met een eigen inhoud; geo: met een rij in
    photo_geo. Geeft (id, blob_id) op volgorde van mpath."""
    import asyncpg

    conn = await asyncpg.connect(dsn(settings))
    try:
        map_ = await _new(conn, ids["werk"], ids["eva"], "dir")
        kids = []
        for _ in range(6):
            sha = uuid.uuid4().hex * 2
            await conn.execute(
                "INSERT INTO blob (sha256, size, ref_count, created_at) VALUES ($1, 1, 1, now())",
                sha,
            )
            kids.append(await _new(conn, map_, ids["eva"], "file", sha))
        order = await conn.fetch(
            "SELECT id, blob_id FROM node WHERE id = ANY($1) ORDER BY mpath", kids
        )
    finally:
        await conn.close()
    if geo:
        async with maker(settings)() as session:
            await photo_rows.write(
                session, [(r["blob_id"], SPOT, PLACE) for r in order], replace=False
            )
            await session.commit()
    return order


@pytest.mark.parametrize("wat", ["schrijven", "vervangen", "weghalen"])
@pytest.mark.parametrize("erboven", ["verplaatsen", "prullenbak"])
@pytest.mark.parametrize("eerst", ["app", "map"])
async def test_de_plek_terwijl_een_map_erboven_verandert(settings, wat, erboven, eerst):
    import asyncpg

    ids = await boom(settings)
    order = await _map(settings, ids, geo=wat != "schrijven")
    first, last = order[-1], order[0]

    async def raak(session, row) -> None:
        if wat == "weghalen":
            await photo_rows.clear(session, [row["blob_id"]])
        else:
            found = [(row["blob_id"], SPOT, PLACE)]
            await photo_rows.write(session, found, replace=wat == "vervangen")

    if erboven == "verplaatsen":
        statement = ("UPDATE node SET parent_id = $1 WHERE id = $2", ids["diep"], ids["werk"])
    else:
        statement = ("UPDATE node SET deleted_at = now() WHERE id = $1", ids["werk"])
    t2 = await asyncpg.connect(dsn(settings))
    try:
        if eerst == "app":
            async with maker(settings)() as session:
                await raak(session, first)
                task = asyncio.create_task(t2.execute(*statement))
                await _blocks(task)
                await raak(session, last)
                await session.commit()
            await task
        else:
            tx = t2.transaction()
            await tx.start()
            await t2.execute(*statement)

            async def app() -> None:
                async with maker(settings)() as session:
                    await raak(session, first)
                    await raak(session, last)
                    await session.commit()

            task = asyncio.create_task(app())
            await _blocks(task)
            await tx.commit()
            await task
    finally:
        await t2.close()
    await nalopen(settings)
    assert await fill(settings, repair=False) == ZERO
    touched = [first["id"], last["id"]]
    found = await rows(
        settings,
        "SELECT g.dead, n.dead FROM photo_geo g JOIN node n ON n.id = g.node_id "
        "WHERE g.node_id = ANY(:t)",
        t=touched,
    )
    if wat == "weghalen":
        assert found == []
    else:
        # vakantie/diep en de prullenbak: beide dood, en photo_geo volgt de node
        assert found == [(1, 1), (1, 1)]


async def _map_zonder_plaats(settings, ids, count: int) -> list[uuid.UUID]:
    """Een map onder werk met count foto's, elk met een plek in Amsterdam zonder plaats en land
    (gelezen vóór places.sqlite er was). Het bestand met de hoogste node_id komt eerst: zo staan
    de nodes (op mpath) en de rijen van photo_geo (op schijf) andersom dan run_places ze doet
    (op node_id). Geeft de ids, oplopend."""
    import asyncpg

    kids = sorted((uuid.uuid4() for _ in range(count)), reverse=True)
    conn = await asyncpg.connect(dsn(settings))
    try:
        map_ = await _new(conn, ids["werk"], ids["eva"], "dir")
        for node in kids:
            sha = node.hex * 2
            await conn.execute(
                "INSERT INTO blob (sha256, size, ref_count, created_at) VALUES ($1, 1, 1, now())",
                sha,
            )
            name = f"file-{node.hex[:12]}"
            await conn.execute(INSERT, node, map_, ids["eva"], name, "file", name, sha)
            await conn.execute(
                "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source) "
                "VALUES ($1, $2, 52.37403, 4.88969, 1, 2, 'exif')",
                node,
                ids["eva"],
            )
    finally:
        await conn.close()
    return sorted(kids)


class _NaDeEersteRij(AsyncSession):
    """Een sessie van run_places die na de eerste rij van een stuk (_FILL) eerst HAAK afwacht."""

    async def execute(self, statement, params=None, **kw):  # type: ignore[override]
        result = await super().execute(statement, params, **kw)
        if statement is photo_rows._FILL and HAAK:
            await HAAK.pop()()
        return result


HAAK: list = []


@pytest.mark.parametrize("erboven", ["verplaatsen", "prullenbak"])
@pytest.mark.parametrize("eerst", ["app", "map"])
async def test_de_plaatsen_terwijl_een_map_erboven_verandert(settings, monkeypatch, erboven, eerst):
    import asyncpg

    ids = await boom(settings)
    kids = await _map_zonder_plaats(settings, ids, 6)
    bronnen.gebruik(monkeypatch, bronnen.maak(settings.geo_src))
    places.forget()
    assert load.ensure(settings) == "gebouwd"
    monkeypatch.setattr(
        photo_rows,
        "_maker",
        lambda s: async_sessionmaker(get_engine(s), expire_on_commit=False, class_=_NaDeEersteRij),
    )
    payload = {"after": "", "rows": 0, "bouw": ""}
    if erboven == "verplaatsen":
        statement = ("UPDATE node SET parent_id = $1 WHERE id = $2", ids["diep"], ids["werk"])
    else:
        statement = ("UPDATE node SET deleted_at = now() WHERE id = $1", ids["werk"])
    t2 = await asyncpg.connect(dsn(settings))
    try:
        if eerst == "app":
            started: list[asyncio.Task] = []

            async def map_erboven() -> None:
                started.append(asyncio.create_task(t2.execute(*statement)))
                await _blocks(started[0])

            HAAK.append(map_erboven)
            await photo_rows.run_places(settings, payload)
            await started[0]
        else:
            tx = t2.transaction()
            await tx.start()
            await t2.execute(*statement)
            task = asyncio.create_task(photo_rows.run_places(settings, payload))
            await _blocks(task)
            await tx.commit()
            await task
    finally:
        HAAK.clear()
        places.forget()
        await t2.close()
    await nalopen(settings)
    assert await fill(settings, repair=False) == ZERO
    found = await rows(
        settings,
        "SELECT g.node_id, g.place_id, g.country, g.dead, n.dead FROM photo_geo g "
        "JOIN node n ON n.id = g.node_id WHERE g.node_id = ANY(:t) ORDER BY g.node_id",
        t=kids,
    )
    # Elke rij heeft zijn plaats, en vakantie/diep en de prullenbak: dood, zoals de node
    assert found == [(kid, AMSTERDAM, "NL", 1, 1) for kid in kids]


async def _map_met_plek(settings, ids, count: int) -> list[uuid.UUID]:
    """Een map onder werk met count foto's van Eva, elk met een rij in photo_geo. De rijen van
    photo_geo komen andersom op schijf: eerst die van het laatste pad. Zo gaat de DELETE van
    wipe_chunk (op volgorde van schijf) andersom door de rijen dan de prullenbak of verplaatsing
    erboven (op mpath). Geeft de ids op volgorde van mpath."""
    import asyncpg

    conn = await asyncpg.connect(dsn(settings))
    try:
        map_ = await _new(conn, ids["werk"], ids["eva"], "dir")
        kids = []
        for _ in range(count):
            sha = uuid.uuid4().hex * 2
            await conn.execute(
                "INSERT INTO blob (sha256, size, ref_count, created_at) VALUES ($1, 1, 1, now())",
                sha,
            )
            kids.append(await _new(conn, map_, ids["eva"], "file", sha))
        kids = [r["id"] for r in await conn.fetch(
            "SELECT id FROM node WHERE id = ANY($1) ORDER BY mpath", kids
        )]  # fmt: skip
        for node in reversed(kids):
            await conn.execute(
                "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source) "
                "VALUES ($1, $2, 52.37403, 4.88969, 1, 2, 'exif')",
                node,
                ids["eva"],
            )
        await conn.execute(
            "INSERT INTO photo_pref (user_id, location, updated_at) VALUES ($1, 'uit', now())",
            ids["eva"],
        )
    finally:
        await conn.close()
    return kids


@pytest.mark.parametrize("erboven", ["verplaatsen", "prullenbak"])
@pytest.mark.parametrize("eerst", ["app", "map"])
async def test_locatie_uit_wissen_terwijl_een_map_erboven_verandert(settings, erboven, eerst):
    """geo.off (run_off) naast een prullenbak of verplaatsing erboven. Met eerst "app" houdt een
    derde verbinding de tweede rij op schijf vast: de DELETE van run_off heeft dan de eerste rij
    en wacht halverwege, zoals hij dat in een groot stuk kan doen. Zonder het slot van de boom
    zette de map erboven intussen de andere rijen vast (op mpath, van voren af) tot hij bij de
    rij van de DELETE kwam; liet de derde los, dan wachtten ze op elkaar en brak PostgreSQL de
    DELETE af (40P01). Nu wacht de map erboven op het slot van run_off. Met eerst "map" wacht
    run_off op het slot van de map."""
    import asyncpg

    ids = await boom(settings)
    kids = await _map_met_plek(settings, ids, 6)
    payload = {"user": str(ids["eva"])}
    if erboven == "verplaatsen":
        statement = ("UPDATE node SET parent_id = $1 WHERE id = $2", ids["diep"], ids["werk"])
    else:
        statement = ("UPDATE node SET deleted_at = now() WHERE id = $1", ids["werk"])
    t2 = await asyncpg.connect(dsn(settings))
    t3 = await asyncpg.connect(dsn(settings))
    try:
        if eerst == "app":
            hold = t3.transaction()
            await hold.start()
            # De tweede rij op schijf (de rijen staan andersom: het een-na-laatste pad)
            await t3.execute("SELECT 1 FROM photo_geo WHERE node_id = $1 FOR UPDATE", kids[-2])
            off = asyncio.create_task(photo_rows.run_off(settings, payload))
            await _blocks(off)
            task = asyncio.create_task(t2.execute(*statement))
            await _blocks(task)
            await hold.rollback()
            await off
            await task
        else:
            tx = t2.transaction()
            await tx.start()
            await t2.execute(*statement)
            off = asyncio.create_task(photo_rows.run_off(settings, payload))
            await _blocks(off)
            await tx.commit()
            await off
    finally:
        await t3.close()
        await t2.close()
    await nalopen(settings)
    assert await fill(settings, repair=False) == ZERO
    # De rijen van Eva zijn weg, en vakantie/diep en de prullenbak: de nodes zijn dood
    left = await rows(settings, "SELECT node_id FROM photo_geo WHERE node_id = ANY(:t)", t=kids)
    assert left == []
    dead = await rows(settings, "SELECT dead FROM node WHERE id = ANY(:t)", t=kids)
    assert dead == [(1,)] * len(kids)
