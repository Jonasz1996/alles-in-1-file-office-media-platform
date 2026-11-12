"""node_origin: wie een bestand neerzette (bouwontwerp fase 6, Keuze 69, K1-02, Vraag 9; A0).

Jonas deelt een map met Anna (schrijven) en heeft een uploadlink. Per weg waarlangs een bestand
ergens komt, de rij in node_origin (created_by, via_link):

- een upload in één keer en via tus: wie het deed, ook in de map van een ander;
- via een uploadlink: NULL en 1;
- nieuwe inhoud van een ander (PUT content): de rij wordt van hem; dezelfde inhoud: niets;
- een versie terugzetten: van wie terugzet;
- kopiëren (één en in bulk): wie kopieert, tenzij het origineel van een ander kwam: dan gaat
  die herkomst mee (ook via een link, en van een gebruiker die weg is); een map krijgt geen rij;
- een bestand van vóór 0011 heeft geen rij (Vraag 9).
"""

import re
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_engine
from app.models import NodeOrigin, User
from tests.test_files import mkdir, put, root
from tests.test_public import new_link, open_link, tus_create, tus_send
from tests.test_shares import person, share_with
from tests.test_uploads import create, send

pytestmark = pytest.mark.anyio


async def origins(settings) -> dict[str, tuple[str | None, int]]:
    async with async_sessionmaker(get_engine(settings))() as session:
        rows = (await session.execute(select(NodeOrigin))).scalars().all()
    return {str(r.node_id): (str(r.created_by) if r.created_by else None, r.via_link) for r in rows}


async def children(client, headers, folder) -> dict[str, str]:
    r = await client.get(f"/api/v1/files/{folder}/children", headers=headers)
    assert r.status_code == 200, r.text
    return {c["name"]: c["id"] for c in r.json()["children"]}


@pytest.fixture
async def world(client, admin):
    jonas, tokens = admin
    anna, anna_id = await person(client, jonas, "anna@example.com")
    jonas_root = (await root(client, jonas))["folder"]["id"]
    anna_root = (await root(client, anna))["folder"]["id"]
    gedeeld = await mkdir(client, jonas, jonas_root, "Gedeeld")
    await share_with(client, jonas, gedeeld, user_id=anna_id, permission="write")
    return {
        "jonas": jonas,
        "jonas_id": tokens["user"]["id"],
        "anna": anna,
        "anna_id": anna_id,
        "jonas_root": jonas_root,
        "anna_root": anna_root,
        "gedeeld": gedeeld,
    }


async def test_uploads_in_een_keer_en_via_tus(client, world, settings):
    w = world
    eigen = (await put(client, w["jonas"], w["jonas_root"], "eigen.jpg", b"\xff\xd8eigen")).json()
    van_anna = (await put(client, w["anna"], w["gedeeld"], "anna.jpg", b"\xff\xd8anna")).json()
    assert van_anna["owner"]["id"] == w["jonas_id"]  # van Jonas, door Anna neergezet
    r = await create(client, w["anna"], w["gedeeld"], "tus.jpg", 6)
    assert r.status_code == 201, r.text
    r = await send(client, w["anna"], r.headers["location"], 0, b"\xff\xd8tus1")
    assert r.status_code == 204, r.text
    tus = (await children(client, w["jonas"], w["gedeeld"]))["tus.jpg"]
    map_id = await mkdir(client, w["anna"], w["gedeeld"], "map van anna")

    assert await origins(settings) == {
        eigen["id"]: (w["jonas_id"], 0),
        van_anna["id"]: (w["anna_id"], 0),
        tus: (w["anna_id"], 0),
    }
    assert map_id not in await origins(settings)  # een map krijgt geen rij


async def test_via_een_uploadlink(client, world, settings):
    w = world
    link = await new_link(client, w["jonas"], w["gedeeld"], permission="upload")
    key = (await open_link(client, link))["key"]
    r = await tus_create(client, key, "gast.jpg", 6)
    assert r.status_code == 201, r.text
    r = await tus_send(client, key, r.headers["location"], 0, b"\xff\xd8gast")
    assert r.status_code == 204, r.text
    gast = (await children(client, w["jonas"], w["gedeeld"]))["gast.jpg"]
    assert await origins(settings) == {gast: (None, 1)}


async def test_nieuwe_inhoud_en_een_versie_terugzetten(client, world, settings):
    w = world
    foto = (await put(client, w["jonas"], w["gedeeld"], "foto.jpg", b"\xff\xd8jonas")).json()["id"]
    assert await origins(settings) == {foto: (w["jonas_id"], 0)}
    # Dezelfde inhoud van Anna: geen nieuwe inhoud, dus niets
    r = await client.put(
        f"/api/v1/files/{foto}/content", content=b"\xff\xd8jonas", headers=w["anna"]
    )
    assert r.status_code == 200, r.text
    assert await origins(settings) == {foto: (w["jonas_id"], 0)}
    # Nieuwe inhoud van Anna: de foto is nu door Anna neergezet
    r = await client.put(
        f"/api/v1/files/{foto}/content", content=b"\xff\xd8anna", headers=w["anna"]
    )
    assert r.status_code == 200, r.text
    assert await origins(settings) == {foto: (w["anna_id"], 0)}
    # Vervangen bij een upload met dezelfde naam: idem, nu weer Jonas
    r = await put(
        client, w["jonas"], w["gedeeld"], "foto.jpg", b"\xff\xd8jonas2", conflict="replace"
    )
    assert r.status_code == 201, r.text
    assert await origins(settings) == {foto: (w["jonas_id"], 0)}
    # Anna zet de versie van Anna terug: van haar
    r = await client.get(f"/api/v1/files/{foto}/versions", headers=w["anna"])
    van_anna = next(v for v in r.json() if v["size"] == len(b"\xff\xd8anna"))
    r = await client.post(
        f"/api/v1/files/{foto}/versions/{van_anna['id']}/restore", headers=w["anna"]
    )
    assert r.status_code == 200, r.text
    assert await origins(settings) == {foto: (w["anna_id"], 0)}


async def test_een_versie_van_een_link_terugzetten(client, world, settings):
    """Review A0, veilig F4 (nit, niet veranderd): wie een versie maakte, staat nergens (a0.md,
    Open 8, voor X2). Zet de eigenaar de inhoud van een uploadlink terug, dan is die van hem: hij
    kiest die inhoud zelf, zoals downloaden en opnieuw uploaden. Deze test legt dat vast; wil X2
    het strenger, dan faalt hij en hoort er een kolom op version bij (een eigen migratie)."""
    w = world
    link = await new_link(client, w["jonas"], w["gedeeld"], permission="upload")
    key = (await open_link(client, link))["key"]
    r = await tus_create(client, key, "gast.jpg", 6)
    await tus_send(client, key, r.headers["location"], 0, b"\xff\xd8gast")
    gast = (await children(client, w["jonas"], w["gedeeld"]))["gast.jpg"]
    assert await origins(settings) == {gast: (None, 1)}
    r = await client.put(
        f"/api/v1/files/{gast}/content", content=b"\xff\xd8eigen", headers=w["jonas"]
    )
    assert r.status_code == 200, r.text
    assert await origins(settings) == {gast: (w["jonas_id"], 0)}
    r = await client.get(f"/api/v1/files/{gast}/versions", headers=w["jonas"])
    (van_de_link,) = r.json()
    r = await client.post(
        f"/api/v1/files/{gast}/versions/{van_de_link['id']}/restore", headers=w["jonas"]
    )
    assert r.status_code == 200, r.text
    assert await origins(settings) == {gast: (w["jonas_id"], 0)}


async def test_kopieren(client, world, settings):
    w = world
    link = await new_link(client, w["jonas"], w["gedeeld"], permission="upload")
    key = (await open_link(client, link))["key"]
    r = await tus_create(client, key, "gast.jpg", 6)
    await tus_send(client, key, r.headers["location"], 0, b"\xff\xd8gast")
    eigen = (await put(client, w["jonas"], w["gedeeld"], "eigen.jpg", b"\xff\xd8eigen")).json()
    van_anna = (await put(client, w["anna"], w["gedeeld"], "anna.jpg", b"\xff\xd8anna")).json()
    sub = await mkdir(client, w["jonas"], w["gedeeld"], "sub")
    diep = (await put(client, w["jonas"], sub, "diep.jpg", b"\xff\xd8diep")).json()
    in_gedeeld = await children(client, w["jonas"], w["gedeeld"])
    gast = in_gedeeld["gast.jpg"]
    voor = await origins(settings)

    # Jonas kopieert de map in zijn eigen hoofdmap: zijn eigen foto's worden van hem (hij
    # kopieert), die van Anna en van de gast houden hun herkomst
    r = await client.post(
        f"/api/v1/files/{w['gedeeld']}/copy",
        json={"parent_id": w["jonas_root"], "name": "Kopie"},
        headers=w["jonas"],
    )
    assert r.status_code == 201, r.text
    kopie = await children(client, w["jonas"], r.json()["id"])
    kopie_diep = (await children(client, w["jonas"], kopie["sub"]))["diep.jpg"]
    na = await origins(settings)
    assert {k: v for k, v in na.items() if k not in voor} == {
        kopie["eigen.jpg"]: (w["jonas_id"], 0),
        kopie["anna.jpg"]: (w["anna_id"], 0),
        kopie["gast.jpg"]: (None, 1),
        kopie_diep: (w["jonas_id"], 0),
    }
    assert kopie["sub"] not in na

    # Anna kopieert in bulk naar haar eigen map: de foto van Jonas wordt van haar (zij kopieert
    # en is daar de eigenaar); haar eigen foto ook; die van de gast blijft van de link
    r = await client.post(
        "/api/v1/batch/copy",
        json={"ids": [eigen["id"], van_anna["id"], gast], "parent_id": w["anna_root"]},
        headers=w["anna"],
    )
    assert r.status_code == 200, r.text
    bij_anna = await children(client, w["anna"], w["anna_root"])
    nieuw = {k: v for k, v in (await origins(settings)).items() if k not in na}
    assert nieuw == {
        bij_anna["eigen.jpg"]: (w["anna_id"], 0),
        bij_anna["anna.jpg"]: (w["anna_id"], 0),
        bij_anna["gast.jpg"]: (None, 1),
    }
    assert diep["id"] in voor and kopie_diep not in voor


async def test_kopie_van_wie_weg_is_blijft_van_een_ander(client, world, settings):
    """created_by NULL zonder link: wie het neerzette, is weg. Ook dat is niet de eigenaar."""
    w = world
    jan, jan_id = await person(client, w["jonas"], "jan@example.com")
    await share_with(client, w["jonas"], w["gedeeld"], user_id=jan_id, permission="write")
    van_jan = (await put(client, jan, w["gedeeld"], "jan.jpg", b"\xff\xd8jan")).json()["id"]
    async with async_sessionmaker(get_engine(settings))() as session:
        await session.execute(delete(User).where(User.id == uuid.UUID(jan_id)))
        await session.commit()
    assert (await origins(settings))[van_jan] == (None, 0)
    r = await client.post(
        f"/api/v1/files/{van_jan}/copy", json={"parent_id": w["jonas_root"]}, headers=w["jonas"]
    )
    assert r.status_code == 201, r.text
    assert (await origins(settings))[r.json()["id"]] == (None, 0)


async def test_van_voor_0011_geen_rij(client, world, settings):
    """Een bestand zonder rij (zoals alles van vóór 0011) telt als van de eigenaar (Vraag 9);
    een kopie daarvan krijgt een gewone rij van wie kopieert."""
    w = world
    oud = (await put(client, w["anna"], w["gedeeld"], "oud.jpg", b"\xff\xd8oud")).json()["id"]
    async with async_sessionmaker(get_engine(settings))() as session:
        await session.execute(delete(NodeOrigin))
        await session.commit()
    # Zonder rij weet niemand meer dat Anna hem neerzette: hij telt als van Jonas, en wie hem
    # kopieert, wordt de herkomst van de kopie
    r = await client.post(
        f"/api/v1/files/{oud}/copy", json={"parent_id": w["anna_root"]}, headers=w["anna"]
    )
    assert r.status_code == 201, r.text
    assert await origins(settings) == {r.json()["id"]: (w["anna_id"], 0)}


SQL = (
    "INSERT INTO node_origin (node_id, created_by, via_link) VALUES (?, ?, ?) ON CONFLICT "
    "(node_id) DO UPDATE SET created_by = excluded.created_by, via_link = excluded.via_link"
)


class _Sessie:
    """Genoeg van een AsyncSession voor placed(): de database en wat er uitgevoerd wordt."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.stmts: list = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.name))

    async def execute(self, stmt):
        self.stmts.append(stmt)


@pytest.mark.parametrize("name", ["sqlite", "postgresql"])
async def test_de_upsert_ook_op_postgresql(name):
    """Review A0, contract F2: placed() schrijft bij elke upload met nieuwe inhoud. De vorm van
    SQLite (sqlalchemy.dialects.sqlite.insert) compileert niet op PostgreSQL, en dan faalde elke
    upload daar. placed() kiest de vorm van de database van de sessie; beide geven dezelfde
    upsert op node_id."""
    from sqlalchemy.dialects import postgresql, sqlite

    from app import origin

    dialect = {"sqlite": sqlite.dialect(), "postgresql": postgresql.asyncpg.dialect()}[name]
    node, actor = uuid.uuid4(), uuid.uuid4()
    sessie = _Sessie(name)
    await origin.placed(sessie, node, actor)
    await origin.placed(sessie, node, None)
    eigen, link = sessie.stmts
    sql = " ".join(str(eigen.compile(dialect=dialect)).split())
    assert re.sub(r"\$\d+::\w+", "?", sql) == SQL  # asyncpg: $1::UUID, $2::UUID, $3::INTEGER
    params = eigen.compile(dialect=dialect).params
    assert (params["node_id"], params["created_by"], params["via_link"]) == (node, actor, 0)
    params = link.compile(dialect=dialect).params
    assert (params["created_by"], params["via_link"]) == (None, 1)
