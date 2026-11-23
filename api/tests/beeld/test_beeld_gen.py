"""beeld_owner.gen via de routes van de app (bouwontwerp fase 6, X3; Keuze 76; K1-08; BZ8; X3b).

De triggers van 0012 zelf staan in tests/search/test_triggers_beeld.py (op een database zonder
app). Hier: wat een gebruiker doet, en of gen dan omhoog gaat (het bestand loopt dan achter en
wordt opnieuw gemaakt; hoeveel hoger doet er niet toe):

- omhoog: een nieuwe foto, een kopie, een andere eigenaar, nieuwe inhoud, definitief wissen
  (node_ad_beeld), en een gebruiker wissen met zijn foto's geeft geen fout;
- gelijk: prullenbak in en uit, een andere naam of map, een document, een map;
- BZ8: NEW.ftype in WHEN werkt op deze SQLite (CI: 3.46.1, hier 3.53.1), en een import van 500
  foto's wordt met de drie triggers hooguit 10% trager dan zonder.
"""

from __future__ import annotations

import gc
import sqlite3
import time
import uuid

import pytest
import sqlalchemy as sa

from app.db import get_engine
from app.models import Node
from app.search import schema
from tests.beeld import nep
from tests.beeld.nep import foto
from tests.test_files import mkdir, put, root
from tests.test_user_delete import block_and_delete

pytestmark = pytest.mark.anyio

BEELD_TRIGGERS = ("node_ai_beeld", "node_au_beeld", "node_ad_beeld")


async def _gen(settings, user_id: str) -> int:
    row = await nep.owner(settings, user_id)
    return -1 if row is None else row.gen


async def test_gen_bij_wat_het_bestand_raakt(client, admin, mensen, beeld):
    jonas, _ = admin
    (eva, eva_id), (bram, bram_id) = mensen
    fake, settings = beeld
    eva_root = (await root(client, eva))["folder"]["id"]
    album = await mkdir(client, eva, eva_root, "Album")
    stappen: list[tuple[str, bool]] = []

    async def stap(naam: str, omhoog: bool, user_id: str = eva_id) -> None:
        stappen.append((naam, omhoog))
        assert (await _gen(settings, user_id) > voor[0]) is omhoog, naam

    voor = [await _gen(settings, eva_id)]
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    await stap("nieuwe foto", True)

    voor = [await _gen(settings, eva_id)]
    r = await client.post(
        f"/api/v1/files/{rood['id']}/copy",
        json={"parent_id": eva_root, "name": "kopie.jpg"},
        headers=eva,
    )
    assert r.status_code == 201, r.text
    kopie = r.json()["id"]
    await stap("kopie", True)

    for naam, doe in (
        ("prullenbak", lambda: client.delete(f"/api/v1/files/{kopie}", headers=eva)),
        ("terugzetten", lambda: client.post(f"/api/v1/files/{kopie}/restore", headers=eva)),
        (
            "andere naam",
            lambda: client.patch(f"/api/v1/files/{kopie}", json={"name": "k.jpg"}, headers=eva),
        ),
        (
            "andere map",
            lambda: client.patch(f"/api/v1/files/{kopie}", json={"parent_id": album}, headers=eva),
        ),
        ("een document", lambda: nep.upload(client, eva, "brief.txt", b"rood")),
        ("een map", lambda: mkdir(client, eva, eva_root, "Nog een map")),
    ):
        voor = [await _gen(settings, eva_id)]
        r = await doe()
        if hasattr(r, "status_code"):
            assert r.status_code in (200, 204), (naam, r.text)
        await stap(naam, False)

    voor = [await _gen(settings, eva_id)]
    data = nep.photo(nep.KLEUREN["blauw"], seed=9)
    r = await client.put(f"/api/v1/files/{rood['id']}/content", content=data, headers=eva)
    assert r.status_code == 200, r.text
    await stap("nieuwe inhoud", True)

    voor = [await _gen(settings, eva_id)]
    bram_voor = await _gen(settings, bram_id)
    async with nep.maker(settings)() as db:
        await db.execute(
            sa.update(Node).where(Node.id == uuid.UUID(kopie)).values(owner_id=uuid.UUID(bram_id))
        )
        await db.commit()
    await stap("andere eigenaar (de oude)", True)
    assert await _gen(settings, bram_id) > bram_voor

    voor = [await _gen(settings, eva_id)]
    assert (await client.delete(f"/api/v1/files/{rood['id']}", headers=eva)).status_code == 204
    await stap("prullenbak", False)
    r = await client.delete(f"/api/v1/trash/{rood['id']}", headers=eva)
    assert r.status_code == 204, r.text
    await stap("definitief wissen", True)

    # een gebruiker wissen met zijn foto's: geen fout, en zijn rij komt niet terug
    await foto(client, bram, settings, "eigen.jpg", "groen", seed=3)
    assert await _gen(settings, bram_id) >= 0
    await block_and_delete(client, jonas, bram_id)
    await nep.run_jobs(settings)
    assert await nep.owner(settings, bram_id) is None
    assert len(stappen) == 12


# --- BZ8 -----------------------------------------------------------------------------------------

N = 500
KEER = 2  # elke kant twee keer een import van N foto's
BLOK = 25  # foto's per blok; de blokken gaan om de beurt
VOLGORDE = ("met", "zonder", "zonder", "met")  # ABBA


async def _triggers(engine, aan: bool) -> None:
    async with engine.begin() as conn:
        for naam in BEELD_TRIGGERS:
            await conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {naam}")
            if aan:
                await conn.exec_driver_sql(schema.TRIGGERS[naam])


async def _import(client, headers, parent: str, fotos: list[tuple[str, bytes]]) -> float:
    """Deze foto's uploaden, één voor één zoals de app een import doet; de tijd in s."""
    begin = time.perf_counter()
    for name, data in fotos:
        r = await put(client, headers, parent, name, data)
        assert r.status_code == 201, r.text
    return time.perf_counter() - begin


@pytest.fixture
def frozen_heap():
    """De heap van dit testproces gaat in gc.freeze zolang de test loopt, zoals python -m
    app.serve dat na de start doet (serve.freeze_heap) en zoals frozen_heap in
    test_media_loadtest en tests_analyse/test_beeld_perf. In de volle suite telt de heap
    miljoenen objecten van eerdere tests; een volle ronde van de gc daarover kost tot ±0,6 s
    (gemeten met een spion op gc.callbacks, met 3,3 miljoen objecten) en valt dan in één kant.
    Wat de uploads zelf aan afval maken, ruimt de gc nog steeds op."""
    gc.collect()
    gc.freeze()
    yield
    gc.unfreeze()


@pytest.mark.sqlite_only("BZ8 meet de SQLite-triggers van beeld; op PostgreSQL staan ze in 0013")
async def test_bz8_where_met_ftype_en_de_kosten_van_een_import(client, mensen, beeld, frozen_heap):
    """Een import van 500 foto's met en zonder de drie triggers van beeld, twee keer per kant,
    in blokken van 25 om de beurt (ABBA); de som per kant telt. Met: hooguit 10% trager. De
    triggers doen het op deze SQLite (met: gen + 25 per blok, zonder: niets).

    Blokken van 25 en niet vier rondes van 500: wat de rest van de machine intussen doet (een
    build, een andere test op dezelfde kernen) duurt seconden en raakt dan beide kanten. Met
    rondes van 500 (±14 s elk) viel zoiets in één kant: in de volle suite eens 1,101, terwijl
    deze test alleen 0,95–1,00 gaf; twee processen die alleen tijdens de rondes met de triggers
    rekenden, gaven 0,79 (de uploads werden er hier sneller van, niet trager)."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    engine = get_engine(settings)
    async with engine.connect() as conn:
        found = {
            r[0]
            for r in await conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
    assert set(BEELD_TRIGGERS) <= found
    parent = (await root(client, eva))["folder"]["id"]
    kleuren = list(nep.KLEUREN.values())
    await _import(
        client,
        eva,
        parent,
        [(f"warm{i}.jpg", nep.photo(kleuren[i % 5], seed=90_000 + i)) for i in range(50)],
    )
    tijden: dict[str, list[float]] = {"met": [], "zonder": []}
    aan = True
    try:
        for blok in range(2 * KEER * N // BLOK):
            naam = VOLGORDE[blok % len(VOLGORDE)]
            fotos = [
                (f"b{blok}-{i}.jpg", nep.photo(kleuren[i % 5], seed=blok * BLOK + i))
                for i in range(BLOK)
            ]
            if aan != (naam == "met"):
                aan = naam == "met"
                await _triggers(engine, aan)
            voor = await _gen(settings, eva_id)
            tijden[naam].append(await _import(client, eva, parent, fotos))
            na = await _gen(settings, eva_id)
            assert na - voor == (BLOK if aan else 0), (naam, blok, voor, na)
    finally:
        await _triggers(engine, True)
    assert len(tijden["met"]) == len(tijden["zonder"]) == KEER * N // BLOK
    met, zonder = sum(tijden["met"]), sum(tijden["zonder"])
    verhouding = met / zonder
    print(
        f"BZ8 sqlite {sqlite3.sqlite_version}: {KEER} x {N} foto's per kant in blokken van "
        f"{BLOK}, met de triggers {met * 1000:.0f} ms, zonder {zonder * 1000:.0f} ms, "
        f"verhouding {verhouding:.3f}"
    )
    assert verhouding <= 1.10, tijden
