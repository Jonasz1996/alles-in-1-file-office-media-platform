"""De herbouw in stukken, de triggers terugzetten en de controle (bouwontwerp fase 6, Keuze 12;
app/search/rebuild.py, app.cli search-rebuild, search-triggers en search-check).

Wat hier vastligt:
- een upload tijdens een herbouw wacht hooguit 1 s; tussen de stukken wijzigingen onder en
  boven de cursor; zoeken gaat intussen via LikeFallback; daarna is de index gelijk aan een
  volledige herbouw (integrity-check en elke term, spike.herbouw_verschil);
- een herbouw die stopt, gaat verder waar hij was; twee tegelijk geven dezelfde index;
- search-triggers: een trigger die ontbrak of anders was, staat er weer precies zoals
  schema.TRIGGERS; num, mpath, dead en de foto-rijen kloppen weer met de bron; de index ook;
- search-check vindt een rij die niet klopt, een rij zonder node, een verkeerd pad, en (met
  full) wat de integrity-check zegt; in nums, nooit met een naam of tekst.

"churn" staat niet in de naam: de marker triggers (e2e/slim.sh, 3.46.1) zit op de churn, die
nu ook deze herbouw gebruikt.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import anyio
import pytest
from sqlalchemy import update

from app import analysis
from app.db import get_engine
from app.models import Node
from app.search import rebuild, schema
from tests.factories import maker
from tests.search import wereld as W
from tests.search.test_spike_triggers import klopt, sql, vul_veel, zoek_naam
from tests.test_files import mkdir, put, root

pytestmark = pytest.mark.anyio


async def _vul(client, admin, settings, n: int) -> tuple[dict, str, str]:
    jonas, tokens = admin
    top = (await root(client, jonas))["folder"]["id"]
    owner = uuid.UUID(tokens["user"]["id"]).hex
    await anyio.to_thread.run_sync(vul_veel, settings, owner, uuid.UUID(top).hex, n, 40)
    return jonas, top, owner


async def _triggers(settings) -> dict[str, str]:
    return dict(await sql(settings, "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"))


async def test_een_upload_wacht_hooguit_1_s_en_de_index_klopt(client, admin, settings):
    jonas, top, _ = await _vul(client, admin, settings, 6_000)
    await klopt(settings)
    engine = get_engine(settings)
    map_id = await mkdir(client, jonas, top, "Tijdens")
    gedaan: list[str] = []
    motoren: list[str] = []

    async def na_elk_stuk(cursor: int) -> None:
        """Wijzigingen onder en boven de cursor, via de app; en zoeken gaat via LikeFallback."""
        i = len(gedaan)
        for teken, kant in (("<=", "onder"), (">", "boven")):
            rows = await sql(
                settings,
                f"SELECT id, blob_id FROM node WHERE num {teken} :c AND kind = 'file' "
                "AND dead = 0 AND name LIKE 'stuk %' ORDER BY num LIMIT 1",
                c=cursor,
            )
            if not rows:
                continue
            node_id, sha = str(uuid.UUID(rows[0][0])), rows[0][1]
            r = await client.patch(
                f"/api/v1/files/{node_id}", json={"name": f"hernoemd {kant} {i}.txt"}, headers=jonas
            )
            assert r.status_code == 200, r.text
            async with maker(settings)() as session:
                await analysis.write_text(session, sha, meta=f"bijgewerkt{kant}{i}")
            gedaan.append(kant)
        r = await put(client, jonas, map_id, f"tussendoor {i}.txt", b"tussendoor %d" % i)
        assert r.status_code == 201, r.text
        r = await client.get("/api/v1/search", params={"q": "stuk"}, headers=jonas)
        assert r.status_code == 200, r.text
        motoren.append(r.json()["engine"])

    wachten: list[float] = []
    klaar = asyncio.Event()

    async def uploads() -> None:
        i = 0
        while not klaar.is_set():
            start = time.perf_counter()
            r = await put(client, jonas, map_id, f"tegelijk {i}.txt", b"tegelijk %d" % i)
            wachten.append((time.perf_counter() - start) * 1000)
            assert r.status_code == 201, r.text
            i += 1
            await asyncio.sleep(0.005)

    taak = asyncio.create_task(uploads())
    try:
        stukken = await rebuild.rebuild(engine, chunk=1000, after_chunk=na_elk_stuk)
    finally:
        klaar.set()
        await taak
    print(f"herbouw: {len(stukken)} stukken, langste {max(stukken):.0f} ms, "
          f"{len(wachten)} uploads, langste {max(wachten):.0f} ms")  # fmt: skip
    assert len(stukken) >= 5
    assert max(wachten) <= 1000, max(wachten)
    assert "onder" in gedaan and "boven" in gedaan
    assert set(motoren) == {"like"}
    assert await rebuild.progress(engine) is None
    await klopt(settings)
    assert await zoek_naam(settings, "hernoemd boven") and await zoek_naam(settings, "tegelijk")
    # Na de herbouw weer FtsEngine
    r = await client.get("/api/v1/search", params={"q": "stuk"}, headers=jonas)
    assert r.json()["engine"] == "fts"


class Stop(Exception):
    pass


async def test_een_herbouw_die_stopt_gaat_verder(client, admin, settings):
    await _vul(client, admin, settings, 3_000)
    engine = get_engine(settings)

    async def stop(cursor: int) -> None:
        raise Stop(cursor)

    with pytest.raises(Stop):
        await rebuild.rebuild(engine, chunk=500, after_chunk=stop)
    voortgang = await rebuild.progress(engine)
    assert voortgang is not None and 0 < voortgang[0] < voortgang[1]
    # Verder vanaf de cursor: geen delete-all, de rijen tot de cursor blijven
    # Na het eerste stuk staan precies de nodes tot de cursor in de index
    tot = await sql(settings, "SELECT count(*) FROM node WHERE num <= :c", c=voortgang[0])
    assert await sql(settings, "SELECT count(*) FROM fts_node_docsize") == tot
    verder = await rebuild.rebuild(engine, chunk=500)
    await klopt(settings)
    # Een nieuwe herbouw heeft één stuk meer nodig dan het stuk dat na de stop nog kwam
    helemaal = await rebuild.rebuild(engine, chunk=500)
    assert len(verder) == len(helemaal) - 1
    await klopt(settings)


async def test_twee_tegelijk(client, admin, settings):
    await _vul(client, admin, settings, 2_000)
    engine = get_engine(settings)
    await asyncio.gather(rebuild.rebuild(engine, chunk=300), rebuild.rebuild(engine, chunk=300))
    assert await rebuild.progress(engine) is None
    await klopt(settings)


async def test_de_triggers_terug_en_wat_ze_bijhouden(client, settings):
    """Zonder node_au_text, node_au_move en node_au_owner, en met een andere node_ai: een naam,
    een verplaatsing en een andere eigenaar gaan dan langs de index en de foto-rijen heen.
    search-triggers zet het terug."""
    w = await W.bouw_async(settings)
    engine = get_engine(settings)
    async with engine.begin() as conn:
        for naam in ("node_au_text", "node_au_move", "node_au_owner", "node_ai"):
            await conn.exec_driver_sql(f"DROP TRIGGER {naam}")
        await conn.exec_driver_sql(
            "CREATE TRIGGER node_ai AFTER INSERT ON node BEGIN SELECT 1; END"
        )
        # Een naam, een verplaatsing, een andere eigenaar (de foto met OCR van Eva), en een
        # nieuwe node zonder num
        await conn.execute(
            update(Node)
            .where(Node.id == w.ids["plan"])
            .values(name="Nieuwnaam.txt", search_name="nieuwnaam.txt")
        )
        await conn.execute(
            update(Node).where(Node.id == w.ids["verhuisd"]).values(parent_id=w.roots["eva"])
        )
        await conn.execute(
            update(Node).where(Node.id == w.ids["foto"]).values(owner_id=w.users["bram"])
        )
        await conn.exec_driver_sql(
            "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, modified_at, "
            "search_name) VALUES (?, ?, ?, 'Zonder nummer', 'dir', 0, '2026-11-03', '2026-11-03', "
            "'zonder nummer')",
            (uuid.uuid4().hex, w.roots["eva"].hex, w.users["eva"].hex),
        )
    assert await _triggers(settings) != schema.TRIGGERS
    voor = await rebuild.check(engine, sample=10_000)
    assert not voor.ok

    hersteld = await rebuild.restore_triggers(engine)
    assert set(hersteld.created) == {"node_au_text", "node_au_move", "node_au_owner", "node_ai"}
    assert hersteld.dropped == ["node_ai"]
    assert hersteld.numbered == 1 and hersteld.tree >= 1 and hersteld.photo_rows >= 1
    # De herbouw is begonnen (zoeken via LikeFallback) en loopt daarna af
    assert await rebuild.progress(engine) is not None
    await rebuild.rebuild(engine)
    assert await _triggers(settings) == schema.TRIGGERS
    await klopt(settings)
    assert await zoek_naam(settings, "nieuwnaam") == {"Nieuwnaam.txt"}
    # De OCR van Eva hangt niet meer aan de node van Bram
    assert await sql(settings, "SELECT count(*) FROM photo_ocr") == [(0,)]
    na = await rebuild.check(engine, sample=10_000, full=True)
    assert na.ok, na
    # Nog eens: niets te doen
    leeg = await rebuild.restore_triggers(engine)
    assert (leeg.created, leeg.dropped, leeg.numbered, leeg.tree, leeg.photo_rows) == (
        [], [], 0, 0, 0
    )  # fmt: skip
    await rebuild.rebuild(engine)
    await klopt(settings)


async def test_de_controle_vindt_verschillen(client, settings):
    w = await W.bouw_async(settings)
    engine = get_engine(settings)
    goed = await rebuild.check(engine, sample=10_000, full=True, seed=1)
    assert goed.ok and goed.sampled > 20, goed
    nums = dict(await sql(settings, "SELECT id, num FROM node"))
    num = {k: nums[v.hex] for k, v in w.ids.items() if v.hex in nums}
    async with engine.begin() as conn:
        # Een naam langs de index heen, een rij in de index zonder node, en een pad dat niet
        # klopt met de ouder
        await conn.exec_driver_sql("DROP TRIGGER node_au_text")
        await conn.execute(
            update(Node)
            .where(Node.id == w.ids["brief"])
            .values(name="Ander.txt", search_name="ander.txt")
        )
        await conn.exec_driver_sql("DROP TRIGGER node_bd")
        await conn.exec_driver_sql("DELETE FROM node WHERE id = ?", (w.ids["belasting"].hex,))
        await conn.exec_driver_sql("UPDATE node SET dead = 1 WHERE id = ?", (w.ids["plan"].hex,))
    fout = await rebuild.check(engine, sample=10_000, full=True, seed=1)
    assert not fout.ok
    assert (num["brief"], "fts_name") in fout.bad
    assert (num["brief"], "fts_node") in fout.bad
    assert (num["belasting"], "fts_node_docsize: geen node") in fout.bad
    assert (num["belasting"], "fts_name_docsize: geen node") in fout.bad
    assert (num["plan"], "mpath of dead") in fout.bad
    assert {i.split(":")[0] for i in fout.integrity} == {"fts_node", "fts_name"}
    # Alleen nums en de soort fout, nooit een naam of tekst
    for n, wat in fout.bad:
        assert isinstance(n, int) and "brief" not in wat.lower() and "ander" not in wat.lower()


async def test_de_controle_tijdens_een_herbouw(client, admin, settings):
    await _vul(client, admin, settings, 600)
    engine = get_engine(settings)
    gezien = []

    async def kijk(cursor: int) -> None:
        if not gezien:
            gezien.append(await rebuild.check(engine, sample=50))

    await rebuild.rebuild(engine, chunk=200, after_chunk=kijk)
    assert gezien[0].rebuilding and not gezien[0].ok
    assert (await rebuild.check(engine, sample=50)).ok


async def test_zonder_fts5_niets_te_doen(settings, monkeypatch):
    engine = get_engine(settings)

    async def zonder(conn):
        return False

    monkeypatch.setattr(rebuild, "_has_fts", zonder)
    assert await rebuild.rebuild(engine) == []
    assert (await rebuild.check(engine)).sampled == 0
