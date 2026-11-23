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
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app import photo_rows
from app.geo.types import Place
from tests.factories import maker
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
