"""Spike S2 (bouwontwerp fase 6, §3.7; Keuze 3, 12 en 25a): de triggers van 0010 onder elk pad
van de app dat node, blob_text of de foto-rijen verandert, en de herbouw in stukken met uploads
ertussen.

Na elke stap: num, pad en dead kloppen met de boom (migratie.check_tree), FTS5 zegt bij de
integrity-check dat de index bij zijn inhoud past, en de index is term voor term gelijk aan een
volledige herbouw van dezelfde database (spike.herbouw_verschil).

De paden: hernoemen, verplaatsen, prullenbak in en uit, definitief wissen, de batch van 500
(verplaatsen, prullenbak, terugzetten, kopiëren), een map kopiëren, userdelete, purge_expired, de
import van fase 5, een andere eigenaar (daar is geen pad in de app voor: rechtstreeks met de ORM),
en nieuwe inhoud (vervangen, PUT content, een versie terugzetten, tekst schrijven en bijwerken,
OCR van een foto).

herbouw_in_stukken() hieronder is de vorm die de spike bewijst (§3.1, "De herbouw in stukken").
Z1 bouwt hem als app.cli search-rebuild en zet die hier in de plaats; deze tests blijven.

Wat de spike vond (docs/fase-6/spike.md, S2):
- de FK-cascade start node_bd ook voor kleinkinderen: wissen met de cascade laat niets in de
  index staan. Van onder naar boven wissen (purge.delete_nodes) is nodig voor stukken van 2.000,
  niet voor de triggers;
- een stuk van de herbouw moet eerst schrijven en pas dan :hi en search_seq.next lezen. Met
  pysqlite (en dus aiosqlite onder SQLAlchemy) opent een SELECT geen transactie: BEGIN komt pas
  vóór de eerste schrijfactie. Leest het laatste stuk next vóór die tijd, dan mist de index een
  node die er net bij kwam (test_het_laatste_stuk_leest_de_teller_pas_onder_het_slot).
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app import analysis, jobs, userdelete
from app.db import get_engine
from app.models import Node, User
from app.routers import trash
from app.storage import BlobStore
from tests.factories import create_file, create_folder, create_user, maker
from tests.search import spike
from tests.search.migratie import check_tree, names, words
from tests.test_files import mkdir, put, root
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_import import put as put_import
from tests.test_media_import import run_import, world  # noqa: F401
from tests.test_shares import person
from tests.test_user_delete import block_and_delete

pytestmark = pytest.mark.anyio


def pad(settings) -> Path:
    return Path(settings.db_url.split("///", 1)[1])


async def klopt(settings, termen: bool = True) -> None:
    """De boom en de index kloppen, en de index is gelijk aan een volledige herbouw."""
    async with get_engine(settings).connect() as conn:
        await conn.run_sync(check_tree)
    verschil = await anyio.to_thread.run_sync(
        lambda: spike.herbouw_verschil(pad(settings), termen=termen)
    )
    assert verschil == {}, verschil


async def zoek(settings, vraag: str) -> set[str]:
    async with get_engine(settings).connect() as conn:
        return await conn.run_sync(words, vraag)


async def tel(settings, vraag: str) -> int:
    """Hoeveel nodes fts_node vindt (namen kunnen dubbel zijn: een kopie heet hetzelfde)."""
    rows = await sql(settings, "SELECT count(*) FROM fts_node WHERE fts_node MATCH :q", q=vraag)
    return rows[0][0]


async def zoek_naam(settings, stuk: str) -> set[str]:
    async with get_engine(settings).connect() as conn:
        return await conn.run_sync(names, stuk)


async def tekst(settings, data: bytes, body: str | None = None, **velden) -> None:
    """De tekst van een inhoud, zoals de extractie (Z2) hem schrijft: analysis.write_text."""
    async with maker(settings)() as session:
        await analysis.write_text(session, hashlib.sha256(data).hexdigest(), body=body, **velden)


async def sql(settings, query: str, **args) -> list:
    async with get_engine(settings).begin() as conn:
        result = await conn.execute(text(query), args)
        return result.all() if result.returns_rows else []


@pytest.fixture(autouse=True)
def _fts5(settings):
    """Deze tests gaan over de FTS-tabellen: de SQLite van de test moet FTS5 kennen."""
    con = sqlite3.connect(pad(settings))
    try:
        found = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'fts_node'").fetchone()
    finally:
        con.close()
    assert found, "deze SQLite kent geen FTS5"


# --- de paden van de app -----------------------------------------------------------------


async def test_hernoemen_verplaatsen_prullenbak_en_wissen(client, admin, settings):
    jonas, _ = admin
    top = (await root(client, jonas))["folder"]["id"]
    werk = await mkdir(client, jonas, top, "Werk")
    archief = await mkdir(client, jonas, top, "Archief")
    notulen = await mkdir(client, jonas, werk, "Notulen")
    data = b"notulen van de vergadering"
    doc = (await put(client, jonas, notulen, "vergadering.txt", data)).json()["id"]
    await put(client, jonas, werk, "offerte.txt", b"offerte")
    await klopt(settings)
    await tekst(settings, data, body="De begroting voor het kwartaal")
    await klopt(settings)
    assert await zoek(settings, "kwartaal") == {"vergadering.txt"}

    # hernoemen
    r = await client.patch(f"/api/v1/files/{doc}", json={"name": "Bestuur.txt"}, headers=jonas)
    assert r.status_code == 200, r.text
    await klopt(settings)
    assert await zoek_naam(settings, "bestuur") == {"Bestuur.txt"}
    assert await zoek_naam(settings, "vergadering") == set()
    assert await zoek(settings, "kwartaal") == {"Bestuur.txt"}

    # een map verplaatsen: de hele submap krijgt een nieuw pad
    r = await client.patch(f"/api/v1/files/{notulen}", json={"parent_id": archief}, headers=jonas)
    assert r.status_code == 200, r.text
    await klopt(settings)

    # hernoemen en verplaatsen in één keer
    r = await client.patch(
        f"/api/v1/files/{doc}", json={"name": "Raad.txt", "parent_id": werk}, headers=jonas
    )
    assert r.status_code == 200, r.text
    await klopt(settings)

    # prullenbak in en uit: dead voor de hele submap
    assert (await client.delete(f"/api/v1/files/{werk}", headers=jonas)).status_code == 204
    await klopt(settings)
    dead = await sql(settings, "SELECT dead FROM node WHERE id = :id", id=uuid.UUID(doc).hex)
    assert dead == [(1,)]
    r = await client.post(f"/api/v1/files/{werk}/restore", headers=jonas)
    assert r.status_code == 200, r.text
    await klopt(settings)

    # definitief wissen uit de prullenbak (purge.delete_nodes, van onder naar boven)
    assert (await client.delete(f"/api/v1/files/{werk}", headers=jonas)).status_code == 204
    assert (await client.delete(f"/api/v1/trash/{werk}", headers=jonas)).status_code == 204
    await klopt(settings)
    assert await zoek(settings, "kwartaal") == set()
    # de hele prullenbak legen
    assert (await client.delete(f"/api/v1/files/{archief}", headers=jonas)).status_code == 204
    assert (await client.delete("/api/v1/trash", headers=jonas)).status_code == 200
    await klopt(settings)


async def test_wissen_met_de_cascade_raakt_ook_de_kleinkinderen(client, admin, settings):
    """De FK node.parent_id staat op CASCADE. Een map rechtstreeks wissen neemt drie lagen mee,
    en node_bd haalt ze alle drie uit de index (de cascade start de trigger wel)."""
    jonas, _ = admin
    top = (await root(client, jonas))["folder"]["id"]
    a = await mkdir(client, jonas, top, "a")
    b = await mkdir(client, jonas, a, "b")
    c = await mkdir(client, jonas, b, "c")
    data = b"diep in de boom"
    await put(client, jonas, c, "diep.txt", data)
    await tekst(settings, data, body="kleinkind achterkleinkind")
    assert await zoek(settings, "achterkleinkind") == {"diep.txt"}
    await sql(settings, "DELETE FROM node WHERE id = :id", id=uuid.UUID(a).hex)
    assert await sql(settings, "SELECT count(*) FROM node WHERE name IN ('b', 'c')") == [(0,)]
    assert await zoek(settings, "achterkleinkind") == set()
    await klopt(settings)


async def test_een_batch_van_500(client, admin, settings):
    """De bulkacties doen 500 nodes per transactie. Na elke actie is de index gelijk aan een
    herbouw; de tijd per actie wordt gemeld."""
    jonas, tokens = admin
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        owner = await session.get(User, uuid.UUID(tokens["user"]["id"]))
        stapel = await create_folder(session, owner, name="Stapel")
        doel = await create_folder(session, owner, name="Doel")
        ids = []
        for i in range(500):
            node = await create_file(
                session, owner, stapel.id, f"brief {i:03}.txt", data=b"brief %d" % (i % 25),
                blobs=blobs,
            )  # fmt: skip
            ids.append(str(node.id))
        await session.commit()
        stapel_id, doel_id = str(stapel.id), str(doel.id)
    for i in range(25):
        await tekst(settings, b"brief %d" % i, body=f"aanmaning nummer{i}")
    await klopt(settings)
    assert await tel(settings, "aanmaning") == 500

    tijden = {}

    async def bulk(actie: str, **body) -> None:
        start = time.perf_counter()
        r = await client.post(f"/api/v1/{actie}", json=body, headers=jonas)
        tijden[actie] = round((time.perf_counter() - start) * 1000)
        assert r.status_code == 200, r.text
        assert all(x["ok"] for x in r.json()["results"]), r.text
        await klopt(settings)

    await bulk("batch/move", ids=ids, parent_id=doel_id)
    await bulk("batch/trash", ids=ids)
    assert await sql(settings, "SELECT count(*) FROM node WHERE dead = 1") == [(500,)]
    await bulk("trash/restore", ids=ids)
    await bulk("batch/copy", ids=ids, parent_id=stapel_id)
    assert await tel(settings, "aanmaning") == 1000
    await bulk("batch/trash", ids=ids)
    await bulk("trash/purge", ids=ids)
    assert await tel(settings, "aanmaning") == 500
    print(f"S2 batch van 500 (ms per actie): {tijden}")


async def test_een_map_kopieren(client, admin, settings):
    jonas, _ = admin
    top = (await root(client, jonas))["folder"]["id"]
    bron = await mkdir(client, jonas, top, "Bron")
    sub = await mkdir(client, jonas, bron, "Sub")
    doel = await mkdir(client, jonas, top, "Doel")
    await put(client, jonas, sub, "contract.txt", b"contract")
    await tekst(settings, b"contract", body="huurovereenkomst")
    r = await client.post(f"/api/v1/files/{bron}/copy", json={"parent_id": doel}, headers=jonas)
    assert r.status_code == 201, r.text
    await klopt(settings)
    paden = await sql(
        settings,
        "SELECT n.mpath FROM fts_node f JOIN node n ON n.num = f.rowid "
        "WHERE fts_node MATCH 'huurovereenkomst'",
    )
    assert len(paden) == 2


async def test_nieuwe_inhoud(client, admin, settings):
    """Vervangen, PUT content, een versie terugzetten, tekst schrijven en bijwerken (de
    triggers op blob_text), OCR van een foto (photo_ocr), en de ß als ss."""
    jonas, _ = admin
    top = (await root(client, jonas))["folder"]["id"]
    een, twee, drie = b"versie een", b"versie twee", b"versie drie"
    r = await put(client, jonas, top, "plan.txt", een)
    doc = r.json()["id"]
    await tekst(settings, een, body="eerste opzet")
    await klopt(settings)
    assert await zoek(settings, "opzet") == {"plan.txt"}

    # vervangen: andere blob_id, de oude tekst gaat uit de index
    r = await put(client, jonas, top, "plan.txt", twee, conflict="replace")
    assert r.status_code in (200, 201), r.text
    await klopt(settings)
    assert await zoek(settings, "opzet") == set()
    await tekst(settings, twee, body="Die Straße zum Ziel")
    await klopt(settings)
    assert await zoek(settings, "strasse") == {"plan.txt"}
    # dezelfde inhoud bijwerken: UPDATE op blob_text
    await tekst(settings, twee, meta="Jonas")
    await klopt(settings)
    assert await zoek(settings, "meta:jonas") == {"plan.txt"}

    # PUT content (zoals de editor van fase 4 opslaat)
    r = await client.put(f"/api/v1/files/{doc}/content", content=drie, headers=jonas)
    assert r.status_code == 200, r.text
    await klopt(settings)
    assert await zoek(settings, "strasse") == set()

    # een versie terugzetten: de eerste inhoud (met zijn tekst) is weer van plan.txt
    r = await client.get(f"/api/v1/files/{doc}/versions", headers=jonas)
    assert r.status_code == 200, r.text
    vid = next(v["id"] for v in r.json() if v["size"] == len(een))
    r = await client.post(f"/api/v1/files/{doc}/versions/{vid}/restore", headers=jonas)
    assert r.status_code == 200, r.text
    await klopt(settings)
    assert await zoek(settings, "opzet") == {"plan.txt"}

    # OCR van een foto (X1 schrijft photo_ocr), en die rij weer weg
    await sql(
        settings,
        "INSERT INTO photo_ocr (node_id, owner_id, text, words, angle, updated_at) "
        "SELECT id, owner_id, 'kassabon albert', x'00', 0, :t FROM node WHERE id = :id",
        id=uuid.UUID(doc).hex,
        t=spike.NU.isoformat(),
    )
    await klopt(settings)
    assert await zoek(settings, "kassabon") == {"plan.txt"}
    await sql(settings, "UPDATE photo_ocr SET text = 'bonnetje' WHERE node_id = :id",
              id=uuid.UUID(doc).hex)  # fmt: skip
    await klopt(settings)
    assert await zoek(settings, "kassabon") == set()
    # nieuwe inhoud haalt de OCR van de oude weg (node_bu_text)
    r = await client.put(f"/api/v1/files/{doc}/content", content=b"versie vier", headers=jonas)
    assert r.status_code == 200, r.text
    await klopt(settings)
    assert await zoek(settings, "bonnetje") == set()
    assert await sql(settings, "SELECT count(*) FROM photo_ocr") == [(0,)]


async def test_een_andere_eigenaar(settings):
    """Er is geen pad in de app dat owner_id verandert; de ORM rechtstreeks. De foto-rijen van
    de oude eigenaar gaan weg (node_au_owner), en de index blijft gelijk aan een herbouw."""
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        foto = await create_file(session, eva, name="strand.jpg", data=b"jpg", mime="image/jpeg",
                                 blobs=blobs)  # fmt: skip
        await session.commit()
        foto_id, eva_id = foto.id, eva.id
    await sql(
        settings,
        "INSERT INTO photo_ocr (node_id, owner_id, text, words, angle, updated_at) VALUES "
        "(:n, :o, 'zandkasteel', x'00', 0, :t)",
        n=foto_id.hex, o=eva_id.hex, t=spike.NU.isoformat(),
    )  # fmt: skip
    await sql(
        settings,
        "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source) VALUES "
        "(:n, :o, 52.1, 5.1, 1, 2, 'exif')",
        n=foto_id.hex, o=eva_id.hex,
    )  # fmt: skip
    await klopt(settings)
    assert await zoek(settings, "zandkasteel") == {"strand.jpg"}
    async with maker(settings)() as session:
        await session.execute(update(Node).where(Node.id == foto_id).values(owner_id=bram.id))
        await session.commit()
    await klopt(settings)
    assert await zoek(settings, "zandkasteel") == set()
    assert await sql(settings, "SELECT count(*) FROM photo_geo") == [(0,)]


async def test_een_gebruiker_wissen(client, admin, settings):
    jonas, _ = admin
    anna_h, anna = await person(client, jonas, "anna@example.nl")
    top = (await root(client, anna_h))["folder"]["id"]
    werk = await mkdir(client, anna_h, top, "Werk")
    diep = await mkdir(client, anna_h, werk, "Diep")
    await put(client, anna_h, diep, "a.txt", b"van anna")
    await put(client, anna_h, werk, "gedeeld.txt", b"zelfde inhoud")
    jonas_top = (await root(client, jonas))["folder"]["id"]
    await put(client, jonas, jonas_top, "kopie.txt", b"zelfde inhoud")
    await tekst(settings, b"van anna", body="dagboek")
    await tekst(settings, b"zelfde inhoud", body="gezamenlijk")
    await klopt(settings)
    assert await zoek(settings, "gezamenlijk") == {"gedeeld.txt", "kopie.txt"}
    await block_and_delete(client, jonas, anna)
    await jobs.run_pending(settings)
    assert await sql(settings, "SELECT count(*) FROM node WHERE owner_id = :o",
                     o=uuid.UUID(anna).hex) == [(0,)]  # fmt: skip
    await klopt(settings)
    assert await zoek(settings, "gezamenlijk") == {"kopie.txt"}
    assert await zoek(settings, "dagboek") == set()
    assert userdelete  # de job hierboven is userdelete.run


async def test_purge_expired(client, admin, settings):
    jonas, _ = admin
    top = (await root(client, jonas))["folder"]["id"]
    oud = await mkdir(client, jonas, top, "Oud")
    sub = await mkdir(client, jonas, oud, "Sub")
    await put(client, jonas, sub, "oud.txt", b"oud")
    await put(client, jonas, top, "nieuw.txt", b"nieuw")
    await tekst(settings, b"oud", body="verjaard")
    assert (await client.delete(f"/api/v1/files/{oud}", headers=jonas)).status_code == 204
    async with maker(settings)() as session:
        await session.execute(
            update(Node)
            .where(Node.id == uuid.UUID(oud))
            .values(deleted_at=datetime.now(UTC) - timedelta(days=40))
        )
        await session.commit()
    r = await client.put("/api/v1/admin/trash", json={"retention_days": 30}, headers=jonas)
    assert r.status_code == 200, r.text
    assert await trash.purge_expired(settings) >= 1
    await klopt(settings)
    assert await zoek(settings, "verjaard") == set()


async def test_de_import_van_fase_5(world):  # noqa: F811
    w = world
    settings = w["settings"]
    put_import(w["src"], "Bron/Films/Alien (1979)/alien.mkv", b"film")
    put_import(w["src"], "Bron/Films/Heat (1995)/heat.mkv", b"nog een film")
    put_import(w["src"], "Kopie/serie/s01e01.mkv", b"aflevering")
    await run_import(w)
    await klopt(settings)
    assert await zoek_naam(settings, "alien") == {"Alien (1979)", "alien.mkv"}
    # verplaatsen (link en adopt)
    await run_import(w, source="Kopie", move=True)
    await klopt(settings)
    assert await zoek_naam(settings, "s01e01") == {"s01e01.mkv"}


# --- de herbouw in stukken (Keuze 12) ----------------------------------------------------


async def herbouw_in_stukken(
    engine: AsyncEngine, *, stuk: int = 2000, na_elk_stuk=None, slordig=None
) -> list[float]:
    """De herbouw van §3.1: delete-all, dan per stuk van `stuk` nums een korte transactie. Elk
    stuk begint met een schrijfactie, zodat het slot er is vóór :hi en search_seq.next gelezen
    worden. Geeft de duur per stuk in ms.

    na_elk_stuk(cursor): wordt na de commit van elk stuk (behalve het laatste) aangeroepen.
    slordig(): alleen voor de test van de race; leest next zoals een naïeve versie het zou doen
    (een SELECT vóór de eerste schrijfactie, dus buiten de transactie) en roept slordig() aan
    tussen dat lezen en het stuk."""
    nu = spike.NU.isoformat()
    async with engine.begin() as c:
        await c.exec_driver_sql("INSERT INTO fts_node(fts_node) VALUES ('delete-all')")
        await c.exec_driver_sql("INSERT INTO fts_name(fts_name) VALUES ('delete-all')")
        await c.exec_driver_sql(
            "UPDATE search_state SET cursor = 0, started_at = ?, what = 'alles' WHERE id = 1",
            (nu,),
        )
    tijden = []
    while True:
        start = time.perf_counter()
        async with engine.begin() as c:
            if slordig is not None:
                last = (await c.exec_driver_sql("SELECT next - 1 FROM search_seq")).scalar()
                await slordig()
            # eerst schrijven: daarna ziet dit stuk alles wat er gecommit is, en schrijft
            # niemand anders tot de commit
            cur = (
                await c.exec_driver_sql(
                    "UPDATE search_state SET cursor = cursor WHERE id = 1 RETURNING cursor"
                )
            ).scalar()
            if slordig is None:
                last = (await c.exec_driver_sql("SELECT next - 1 FROM search_seq")).scalar()
            hi = (
                await c.exec_driver_sql(
                    "SELECT num FROM node WHERE num > ? ORDER BY num LIMIT 1 OFFSET ?",
                    (cur, stuk - 1),
                )
            ).scalar()
            klaar = hi is None or hi >= last
            if klaar:
                hi = last
            await c.exec_driver_sql(
                "INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) "
                "SELECT num, name, extra, body, ocr, meta FROM search_src "
                "WHERE num > ? AND num <= ?",
                (cur, hi),
            )
            await c.exec_driver_sql(
                "INSERT INTO fts_name(rowid, search_name) SELECT num, search_name FROM node "
                "WHERE num > ? AND num <= ?",
                (cur, hi),
            )
            await c.exec_driver_sql(
                "UPDATE search_state SET cursor = ? WHERE id = 1", (None if klaar else hi,)
            )
        tijden.append((time.perf_counter() - start) * 1000)
        if klaar:
            return tijden
        if na_elk_stuk is not None:
            await na_elk_stuk(hi)


def vul_veel(settings, owner: str, root_id: str, n: int, woorden: int) -> None:
    """n bestanden met tekst in de hoofdmap van owner, rechtstreeks in SQLite (spike.vul_snel)."""
    con = sqlite3.connect(pad(settings))

    def doe(c) -> None:
        nu = spike.NU.isoformat()
        tx = spike.teksten(n, woorden, seed=12)
        shas = [hashlib.sha256(f"veel-{i}".encode()).hexdigest() for i in range(n)]
        c.executemany(
            "INSERT INTO blob (sha256, size, ref_count, created_at) VALUES (?, 1, 1, ?)",
            [(s, nu) for s in shas],
        )
        c.executemany(
            "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, "
            "created_at, modified_at, search_name) VALUES (?, ?, ?, ?, 'file', 'text/plain', "
            "1, ?, ?, ?, ?)",
            [(uuid.uuid4().hex, root_id, owner, f"stuk {i}.txt", shas[i], nu, nu, f"stuk {i}.txt")
             for i in range(n)],
        )  # fmt: skip
        c.executemany(
            "INSERT INTO blob_text (blob_sha, body, ocr, meta, truncated, updated_at) "
            "VALUES (?, ?, '', '', 0, ?)",
            [(shas[i], tx[i], nu) for i in range(n)],
        )

    try:
        spike.vul_snel(con, doe)
    finally:
        con.close()


async def _herbouw_met_uploads(client, admin, settings, n: int, woorden: int) -> dict:
    jonas, tokens = admin
    top = (await root(client, jonas))["folder"]["id"]
    owner = uuid.UUID(tokens["user"]["id"]).hex
    await anyio.to_thread.run_sync(vul_veel, settings, owner, uuid.UUID(top).hex, n, woorden)
    termen = n <= 10_000  # daarboven alleen de integrity-check
    await klopt(settings, termen)
    engine = get_engine(settings)
    map_id = await mkdir(client, jonas, top, "Tijdens")

    async def node_bij(cursor: int, onder: bool) -> str:
        teken = "<=" if onder else ">"
        rows = await sql(
            settings,
            f"SELECT id, blob_id FROM node WHERE num {teken} :c AND kind = 'file' "
            "AND dead = 0 AND name LIKE 'stuk %' ORDER BY num LIMIT 1",
            c=cursor,
        )
        return rows[0] if rows else None

    gedaan: list[str] = []

    async def na_elk_stuk(cursor: int) -> None:
        """Wijzigingen onder en boven de cursor, via de app."""
        i = len(gedaan)
        for onder in (True, False):
            row = await node_bij(cursor, onder)
            if row is None:
                continue
            node_id, sha = str(uuid.UUID(row[0])), row[1]
            kant = "onder" if onder else "boven"
            nieuw = {"name": f"hernoemd {kant} {i}.txt"}
            r = await client.patch(f"/api/v1/files/{node_id}", json=nieuw, headers=jonas)
            assert r.status_code == 200, r.text
            async with maker(settings)() as session:
                await analysis.write_text(session, sha, meta=f"bijgewerkt{kant}{i}")
            assert (
                await client.delete(f"/api/v1/files/{node_id}", headers=jonas)
            ).status_code == 204
            gedaan.append(kant)
        r = await put(client, jonas, map_id, f"tussendoor {i}.txt", b"tussendoor %d" % i)
        assert r.status_code == 201, r.text

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
        stukken = await herbouw_in_stukken(engine, na_elk_stuk=na_elk_stuk)
    finally:
        klaar.set()
        await taak
    await klopt(settings, termen)
    assert await sql(settings, "SELECT cursor FROM search_state") == [(None,)]
    assert "onder" in gedaan and "boven" in gedaan
    return {
        "nodes": n,
        "stukken": len(stukken),
        "langste stuk ms": round(max(stukken)),
        "uploads": len(wachten),
        "upload mediaan ms": round(statistics.median(wachten)),
        "upload max ms": round(max(wachten)),
    }


async def test_herbouw_in_stukken_met_uploads_ertussen(client, admin, settings):
    """Keuze 12: 10.000 nodes met tekst, stukken van 2.000. Tussen de stukken wijzigingen onder
    en boven de cursor; tegelijk uploads. Elke upload wacht hooguit 1 s, en de index is daarna
    gelijk aan een volledige herbouw."""
    uit = await _herbouw_met_uploads(client, admin, settings, 10_000, 60)
    print(f"S2 herbouw: {uit}")
    assert uit["stukken"] >= 5
    assert uit["upload max ms"] <= 1000, uit


@pytest.mark.zoek_perf
async def test_herbouw_in_stukken_op_100000(client, admin, settings):
    """Dezelfde herbouw op 100.000 nodes van 100 woorden (V4 mat 200.000 rijen): het langste
    stuk en de langste upload ernaast."""
    uit = await _herbouw_met_uploads(client, admin, settings, 100_000, 100)
    print(f"S2 herbouw 100.000: {uit}")
    assert uit["upload max ms"] <= 1000, uit


async def test_het_laatste_stuk_leest_de_teller_pas_onder_het_slot(client, admin, settings):
    """De race van het laatste stuk. Een naïeve versie leest search_seq.next met een SELECT
    vóór de eerste schrijfactie: met pysqlite is dat buiten de transactie. Komt er precies dan
    een upload bij, dan krijgt die een num boven de cursor (de trigger schrijft niets), en het
    laatste stuk loopt maar tot de oude next: de index mist de nieuwe node. Begint het stuk met
    een schrijfactie, dan kan dat niet."""
    jonas, _ = admin
    top = (await root(client, jonas))["folder"]["id"]
    for i in range(5):
        await put(client, jonas, top, f"vooraf {i}.txt", b"vooraf %d" % i)
    engine = get_engine(settings)
    gebeurd = []

    async def upload_ertussen() -> None:
        if not gebeurd:
            r = await put(client, jonas, top, "precies ertussen.txt", b"ertussen")
            assert r.status_code == 201, r.text
            gebeurd.append(True)

    await herbouw_in_stukken(engine, slordig=upload_ertussen)
    assert gebeurd
    verschil = await anyio.to_thread.run_sync(spike.herbouw_verschil, pad(settings))
    assert verschil, "de naïeve versie zou hier een node missen"
    print(f"S2 race van het laatste stuk (naïef): {verschil}")
    assert await zoek_naam(settings, "ertussen") == set()
    # Opnieuw in de vorm van herbouw_in_stukken: de index klopt weer
    await herbouw_in_stukken(engine)
    await klopt(settings)
    assert await zoek_naam(settings, "ertussen") == {"precies ertussen.txt"}
