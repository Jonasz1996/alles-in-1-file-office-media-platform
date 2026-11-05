"""Definitief wissen in stukken (bouwontwerp fase 6, Keuze 11; review Z0, haalbaar F3).

routers/trash.py hield BlobStore.lock en de schrijver vast voor de hele boom: een map van
10.911 nodes definitief wissen hield een upload ernaast seconden op (en met 100.000 langer dan
busy_timeout). Nu gaat het per purge.CHUNK nodes, elk stuk een eigen transactie onder het slot,
zoals userdelete._drop_nodes; delete_nodes zelf commit nooit.

- een upload tijdens het wissen van 10.911 nodes komt tussen twee stukken door, en is binnen 1 s
  klaar, ook bij de prullenbak legen (purge_ids). Hier (3.53.1) kostte de hele boom in één
  transactie 0,78 s; de grens van 1 s alleen zegt dus te weinig;
- geen aanroep van delete_nodes met meer dan CHUNK nodes, en samen precies de boom;
- daarna klopt de boekhouding (check_refs), de boom (check_tree) en de index (check_fts);
- terugzetten tijdens het wissen: wat nog niet weg was, komt terug (heel), en het wissen
  antwoordt not_trashed;
- tussen twee stukken: wat er onder de map bij kwam gaat mee, wat eruit verhuisde blijft;
- zonder de triggers van 0010 (zoals op PostgreSQL) werkt definitief wissen ook, kinderen vóór
  hun ouders (nacontrole).
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_engine
from app.models import Node
from app.routers import trash
from app.search import purge
from app.storage import BlobStore
from tests.search.migratie import NOW, check_fts, check_tree
from tests.test_files import check_refs, mkdir, put, root

pytestmark = pytest.mark.anyio

MAPPEN = 10
PER_MAP = 1090
TOTAAL = 1 + MAPPEN + MAPPEN * PER_MAP  # 10.911


async def _grote_map(client, headers, settings) -> tuple[str, str]:
    """Een map "groot" in de hoofdmap met 10 mappen van elk 1.090 bestanden (10.911 nodes),
    allemaal met dezelfde inhoud als "inhoud.txt" (ref_count telt mee). Geeft (hoofdmap,
    groot)."""
    top = (await root(client, headers))["folder"]["id"]
    r = await put(client, headers, top, "inhoud.txt", b"dezelfde inhoud")
    assert r.status_code == 201, r.text
    groot = await mkdir(client, headers, top, "groot")

    def bouw(conn) -> None:
        owner, blob = conn.execute(
            sa.select(Node.owner_id, Node.blob_id).where(Node.name == "inhoud.txt")
        ).one()
        mappen, bestanden = [], []
        for i in range(MAPPEN):
            folder = uuid.uuid4()
            mappen.append((folder.hex, uuid.UUID(groot).hex, owner.hex, f"map-{i}", "dir",
                           None, None, 0, NOW, NOW, f"map-{i}"))  # fmt: skip
            for j in range(PER_MAP):
                name = f"bestand-{i}-{j}.txt"
                bestanden.append((uuid.uuid4().hex, folder.hex, owner.hex, name, "file",
                                  "text/plain", blob, 15, NOW, NOW, name))  # fmt: skip
        sql = (
            "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, blob_id, size, "
            "created_at, modified_at, search_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        conn.exec_driver_sql(sql, mappen)
        conn.exec_driver_sql(sql, bestanden)
        conn.exec_driver_sql(
            "UPDATE blob SET ref_count = ref_count + ? WHERE sha256 = ?", (len(bestanden), blob)
        )

    async with get_engine(settings).begin() as conn:
        await conn.run_sync(bouw)
    r = await client.delete(f"/api/v1/files/{groot}", headers=headers)
    assert r.status_code == 204, r.text
    return top, groot


class _Teller:
    """Telt de aanroepen van delete_nodes en hoeveel nodes er al weg zijn; bezig gaat bij de
    eerste aanroep aan (het wissen is dan bezig, met het slot)."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[int] = []
        self.weg = 0
        self.bezig = asyncio.Event()
        echt = purge.delete_nodes

        async def tellen(db, ids):
            self.calls.append(len(ids))
            self.bezig.set()
            n = await echt(db, ids)
            self.weg += len(ids)
            return n

        monkeypatch.setattr(purge, "delete_nodes", tellen)


async def _heel(settings) -> int:
    """Boekhouding, boom en index kloppen; geeft het aantal nodes."""
    await check_refs(settings)
    async with get_engine(settings).begin() as conn:
        n = await conn.run_sync(check_tree)
        await conn.run_sync(check_fts)
    return n


@pytest.mark.parametrize("hoe", ["los", "legen"])
async def test_een_upload_wacht_niet_op_het_hele_wissen(client, admin, settings, monkeypatch, hoe):
    headers, _ = admin
    top, groot = await _grote_map(client, headers, settings)
    teller = _Teller(monkeypatch)

    url = f"/api/v1/trash/{groot}" if hoe == "los" else "/api/v1/trash"
    wissen = asyncio.create_task(client.delete(url, headers=headers))
    await asyncio.wait_for(teller.bezig.wait(), 60)
    start = time.perf_counter()
    r = await put(client, headers, top, "tussendoor.txt", b"tijdens het wissen")
    duur = time.perf_counter() - start
    al_weg = teller.weg
    assert r.status_code == 201, r.text
    r = await wissen
    assert r.status_code == (204 if hoe == "los" else 200), r.text
    if hoe == "legen":
        assert r.json()["purged"] == 1

    # Tussen twee stukken door (asyncio.Lock is eerlijk: wie wacht, is eerst), niet pas na
    # de hele boom; en binnen 1 s
    assert al_weg < TOTAAL, "de upload wachtte op het hele wissen"
    assert duur < 1.0, f"de upload wachtte {duur:.2f} s op het wissen"
    assert max(teller.calls) <= purge.CHUNK, teller.calls
    assert sum(teller.calls) == TOTAAL
    # Over: de hoofdmap, inhoud.txt en tussendoor.txt
    assert await _heel(settings) == 3


async def test_terugzetten_tijdens_het_wissen(client, admin, settings, monkeypatch):
    """Wat nog niet weg was, komt heel terug; het wissen zegt not_trashed."""
    headers, _ = admin
    _top, groot = await _grote_map(client, headers, settings)
    teller = _Teller(monkeypatch)

    wissen = asyncio.create_task(client.delete(f"/api/v1/trash/{groot}", headers=headers))
    await asyncio.wait_for(teller.bezig.wait(), 60)
    r = await client.post(f"/api/v1/files/{groot}/restore", headers=headers)
    assert r.status_code == 200, r.text
    r = await wissen
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "not_trashed"

    weg = teller.weg
    assert 0 < weg < TOTAAL
    assert await _heel(settings) == 2 + TOTAAL - weg  # de hoofdmap en inhoud.txt erbij
    async with get_engine(settings).connect() as conn:
        dood = await conn.scalar(sa.select(sa.func.count()).select_from(Node).where(Node.dead != 0))
    assert dood == 0


async def test_wat_er_tussen_twee_stukken_gebeurt(client, admin, settings):
    """purge_chunks op twee helften van het plan, met ertussen wat een ander kan doen terwijl
    het wissen het slot even loslaat: de map terugzetten, er een nieuw bestand in zetten en hem
    weer weggooien; en een submap eruit verplaatsen naar een map die daarna zelf in de
    prullenbak gaat. Het nieuwe bestand gaat mee (met ref_count omlaag); de verhuisde submap
    blijft in de prullenbak van zijn nieuwe map."""
    headers, _ = admin
    top = (await root(client, headers))["folder"]["id"]
    weg = await mkdir(client, headers, top, "weg")
    a = await mkdir(client, headers, weg, "a")
    b = await mkdir(client, headers, weg, "b")
    ids = {"weg": weg, "a": a, "b": b}
    for parent, name in (("a", "a1"), ("a", "a2"), ("b", "b1"), ("weg", "c")):
        r = await put(client, headers, ids[parent], f"{name}.txt", f"inhoud {name}".encode())
        assert r.status_code == 201, r.text
        ids[name] = r.json()["id"]
    elders = await mkdir(client, headers, top, "elders")
    assert (await client.delete(f"/api/v1/files/{weg}", headers=headers)).status_code == 204

    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    blobs = BlobStore(settings.blob_dir)
    async with maker() as session:
        plan = await trash._subtree(session, [uuid.UUID(weg)])
        assert [node_id for node_id, _ in plan][-1] == uuid.UUID(weg)  # de map als laatste
        eerst = {uuid.UUID(ids["a1"]), uuid.UUID(ids["a2"])}
        removed = await trash.purge_chunks(session, blobs, [p for p in plan if p[0] in eerst])
        assert removed == eerst

        # Terugzetten, er iets in zetten, weer weggooien; b eruit naar elders, elders weg
        r = await client.post(f"/api/v1/files/{weg}/restore", headers=headers)
        assert r.status_code == 200, r.text
        r = await put(client, headers, weg, "nieuw.txt", b"kwam er tussendoor bij")
        assert r.status_code == 201, r.text
        nieuw = uuid.UUID(r.json()["id"])
        r = await client.patch(f"/api/v1/files/{b}", json={"parent_id": elders}, headers=headers)
        assert r.status_code == 200, r.text
        for node in (weg, elders):
            assert (
                await client.delete(f"/api/v1/files/{node}", headers=headers)
            ).status_code == 204

        removed = await trash.purge_chunks(session, blobs, [p for p in plan if p[0] not in eerst])
        await trash.files.collect_garbage(session, blobs)
    assert removed == {uuid.UUID(ids[k]) for k in ("weg", "a", "c")} | {nieuw}
    # Over: de hoofdmap, elders (in de prullenbak) met b en b1
    assert await _heel(settings) == 4
    r = await client.get("/api/v1/trash", headers=headers)
    assert {n["name"] for n in r.json()} == {"elders"}


@pytest.mark.parametrize("hoe", ["los", "bulk", "legen"])
async def test_wissen_zonder_de_triggers_van_0010(client, admin, settings, monkeypatch, hoe):
    """Op PostgreSQL maakt 0010 alleen de tabellen en kolommen (§3.1): geen triggers, dus dead
    blijft 0 en mpath leeg. Definitief wissen mag daar niet van afhangen; dood volgt uit de
    keten van ouders, en de volgorde (kinderen vóór hun ouders) uit de diepte in de boom, niet
    uit mpath (nacontrole van de review van Z0, haalbaar F3). Hier nagedaan op SQLite: alle
    triggers weg, en dead en mpath zoals op PostgreSQL."""
    headers, _ = admin
    gewist: list[uuid.UUID] = []
    echt = purge.delete_nodes

    async def opschrijven(db, ids):
        gewist.extend(ids)
        return await echt(db, ids)

    monkeypatch.setattr(purge, "delete_nodes", opschrijven)
    async with get_engine(settings).begin() as conn:
        namen = (
            await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        ).scalars()
        for naam in list(namen):
            await conn.exec_driver_sql(f'DROP TRIGGER "{naam}"')
        await conn.exec_driver_sql("UPDATE node SET mpath = NULL, dead = 0")

    top = (await root(client, headers))["folder"]["id"]
    weg = await mkdir(client, headers, top, "weg")
    sub = await mkdir(client, headers, weg, "sub")
    for parent, name in ((weg, "a.txt"), (sub, "b.txt"), (sub, "c.txt"), (top, "blijft.txt")):
        r = await put(client, headers, parent, name, f"inhoud {name}".encode())
        assert r.status_code == 201, r.text
    assert (await client.delete(f"/api/v1/files/{weg}", headers=headers)).status_code == 204
    async with get_engine(settings).connect() as conn:
        rows = await conn.execute(sa.select(Node.id, Node.parent_id))
        ouder = {node_id: parent_id for node_id, parent_id in rows}

    if hoe == "los":
        r = await client.delete(f"/api/v1/trash/{weg}", headers=headers)
        assert r.status_code == 204, r.text
    elif hoe == "bulk":
        r = await client.post("/api/v1/trash/purge", json={"ids": [weg, sub]}, headers=headers)
        assert r.status_code == 200, r.text
        assert [x["ok"] for x in r.json()["results"]] == [True, True], r.text
    else:
        r = await client.delete("/api/v1/trash", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["purged"] == 1
    async with get_engine(settings).connect() as conn:
        namen = set((await conn.execute(sa.select(Node.name))).scalars())
    assert namen == {"", "blijft.txt"}
    await check_refs(settings)
    # weg, sub, a.txt, b.txt en c.txt, elk kind vóór zijn ouder
    assert len(gewist) == len(set(gewist)) == 5
    plek = {node_id: i for i, node_id in enumerate(gewist)}
    for node_id in gewist:
        if ouder[node_id] in plek:
            assert plek[node_id] < plek[ouder[node_id]], "een map ging vóór wat erin lag"
