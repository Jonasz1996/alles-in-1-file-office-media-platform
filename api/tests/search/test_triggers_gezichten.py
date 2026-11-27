"""De triggers van 0011 en de foreign keys van de tabellen van gezichten (bouwontwerp fase 6,
§3.1a; Keuze 25a, 61, 63 en 69), op een echte database met en zonder FTS5. De levensloop van een
foto met gezichten:

- een andere eigenaar: de gezichten en de scan van die node weg (node_au_owner_face); de rest
  van de eigenaar blijft;
- nieuwe inhoud: idem, en het kader van een persoon op die foto weg, de persoon zelf blijft
  (node_au_blob_face); dezelfde inhoud nog eens schrijven doet niets;
- een persoon wissen: zijn bevestigde gezichten worden "niet tonen" zonder vector (person_bd_face),
  een voorstel voor hem valt weg via SET NULL, face_person via de cascade;
- prullenbak in en uit: niets (dat filtert own_live bij het lezen);
- definitief wissen van de node: alles van die node weg via de cascade, ook node_origin;
- een gebruiker wissen: al zijn rijen weg; created_by van wat hij bij een ander uploadde wordt
  NULL.
"""

import uuid

import pytest
import sqlalchemy as sa
from alembic import command

from app.db import get_engine
from tests.search.gezichten import fill_faces
from tests.search.migratie import NOW, SHA_DOC, fill_0009, migrate, settings_at

pytestmark = pytest.mark.anyio


async def _q(settings, sql: str, **params) -> list[tuple]:
    async with get_engine(settings).connect() as conn:
        return [tuple(r) for r in (await conn.execute(sa.text(sql), params)).all()]


async def _do(settings, sql: str, **params) -> None:
    async with get_engine(settings).begin() as conn:
        await conn.execute(sa.text(sql), params)


async def _count(settings, table: str, where: str = "1", **params) -> int:
    return (await _q(settings, f"SELECT count(*) FROM {table} WHERE {where}", **params))[0][0]


async def _world(tmp_path, fts5: bool):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "0011", fts5=fts5)
    made = await fill_faces(settings, ids)
    # Een tweede foto van Eva met een gezicht en een scan: die mag nooit meegaan
    other = uuid.uuid4()
    await _do(
        settings,
        "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, created_at, "
        "modified_at, search_name) VALUES (:id, :root, :eva, 'ander.jpg', 'file', 'image/jpeg', "
        f"1, '{'e' * 64}', :t, :t, 'ander.jpg')",
        id=other.hex,
        root=ids["root"].hex,
        eva=ids["eva"].hex,
        t=NOW,
    )
    await _do(
        settings,
        "INSERT INTO face (id, node_id, owner_id, x, y, w, h, score, px, vec, version, hidden, "
        "created_at) VALUES (:id, :node, :eva, 0, 0, 1, 1, 1, 80, x'00', 1, 0, :t)",
        id=uuid.uuid4().hex,
        node=other.hex,
        eva=ids["eva"].hex,
        t=NOW,
    )
    await _do(
        settings,
        "INSERT INTO face_scan (node_id, owner_id, version, state, prio, queued_at, updated_at) "
        "VALUES (:node, :eva, 1, 'done', :t, :t, :t)",
        node=other.hex,
        eva=ids["eva"].hex,
        t=NOW,
    )
    made["ander"] = other
    return settings, ids, made


FTS = pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])


@FTS
async def test_een_andere_eigenaar(tmp_path, fts5):
    settings, ids, made = await _world(tmp_path, fts5)
    foto = made["foto"].hex
    assert await _count(settings, "face", "node_id = :n", n=foto) == 4
    await _do(
        settings,
        "UPDATE node SET owner_id = :bram, parent_id = :root WHERE id = :n",
        bram=ids["bram"].hex,
        root=ids["bram_root"].hex,
        n=foto,
    )
    assert await _count(settings, "face", "node_id = :n", n=foto) == 0
    assert await _count(settings, "face_scan", "node_id = :n", n=foto) == 0
    assert await _count(settings, "face_person") == 0  # via de cascade van face
    # De andere foto van Eva, haar persoon en face_owner blijven
    assert await _count(settings, "face", "node_id = :n", n=made["ander"].hex) == 1
    assert await _count(settings, "face_scan", "node_id = :n", n=made["ander"].hex) == 1
    assert await _count(settings, "person") == 1
    assert await _count(settings, "face_owner") == 1
    # Wie uploadde, verandert niet met de eigenaar
    assert await _q(settings, "SELECT created_by FROM node_origin WHERE node_id = :n", n=foto) == [
        (ids["eva"].hex,)
    ]


@FTS
async def test_nieuwe_inhoud(tmp_path, fts5):
    settings, ids, made = await _world(tmp_path, fts5)
    foto = made["foto"].hex
    await _do(
        settings,
        "INSERT INTO photo_person (person_id, node_id, owner_id, x, y, w, h, created_at) "
        "VALUES (:p, :n, :eva, 0.1, 0.1, 0.2, 0.2, :t)",
        p=made["person"].hex,
        n=foto,
        eva=ids["eva"].hex,
        t=NOW,
    )
    # Dezelfde inhoud nog eens (en een andere naam): niets
    await _do(
        settings,
        "UPDATE node SET blob_id = blob_id, name = 'oma2.jpg', search_name = 'oma2.jpg' "
        "WHERE id = :n",
        n=foto,
    )
    assert await _count(settings, "face", "node_id = :n", n=foto) == 4
    assert await _q(settings, "SELECT x FROM photo_person WHERE node_id = :n", n=foto) == [(0.1,)]

    await _do(settings, f"UPDATE node SET blob_id = '{SHA_DOC}' WHERE id = :n", n=foto)
    assert await _count(settings, "face", "node_id = :n", n=foto) == 0
    assert await _count(settings, "face_scan", "node_id = :n", n=foto) == 0
    # De persoon blijft op de foto, zonder kader (Keuze 25a)
    assert await _q(settings, "SELECT x, y, w, h FROM photo_person WHERE node_id = :n", n=foto) == [
        (None, None, None, None)
    ]
    assert await _count(settings, "face", "node_id = :n", n=made["ander"].hex) == 1


@FTS
async def test_een_persoon_wissen(tmp_path, fts5):
    settings, ids, made = await _world(tmp_path, fts5)
    await _do(settings, "DELETE FROM person WHERE id = :p", p=made["person"].hex)
    found = dict(
        (r[0], r[1:])
        for r in await _q(
            settings,
            "SELECT id, vec IS NULL, hidden, suggest_id, suggest_score, suggest_why, cluster "
            "FROM face",
        )
    )
    # Het bevestigde gezicht: "niet tonen", zonder vector, zodat hij niet terugkomt (Keuze 63)
    assert found[made["ja"].hex] == (1, 1, None, None, None, None)
    # Het voorstel voor hem: het voorstel weg (SET NULL), de vector blijft. suggest_score en
    # suggest_why ruimt X2 op; de foreign key zet alleen suggest_id op NULL
    assert found[made["voorstel"].hex][:3] == (0, 0, None)
    # De andere gezichten veranderen niet
    assert found[made["groep"].hex] == (0, 0, None, None, None, 7)
    assert found[made["verborgen"].hex] == (1, 1, None, None, None, None)
    assert await _count(settings, "face_person") == 0


@FTS
async def test_prullenbak_in_en_uit_laat_alles_staan(tmp_path, fts5):
    settings, ids, made = await _world(tmp_path, fts5)
    foto = made["foto"].hex
    for value in (NOW, None):
        await _do(settings, "UPDATE node SET deleted_at = :v WHERE id = :n", v=value, n=foto)
        assert await _count(settings, "face", "node_id = :n", n=foto) == 4
        assert await _count(settings, "face_scan", "node_id = :n", n=foto) == 1
        assert await _count(settings, "node_origin", "node_id = :n", n=foto) == 1


@FTS
async def test_definitief_wissen(tmp_path, fts5):
    settings, ids, made = await _world(tmp_path, fts5)
    foto = made["foto"].hex
    await _do(settings, "DELETE FROM node WHERE id = :n", n=foto)
    for table in ("face", "face_scan", "node_origin"):
        assert await _count(settings, table, "node_id = :n", n=foto) == 0
    assert await _count(settings, "face_person") == 0
    assert await _count(settings, "face", "node_id = :n", n=made["ander"].hex) == 1


@FTS
async def test_een_gebruiker_wissen(tmp_path, fts5):
    settings, ids, made = await _world(tmp_path, fts5)
    # Bram uploadde iets via een deling in Eva's boom
    await _do(
        settings,
        "UPDATE node_origin SET created_by = :bram, via_link = 0 WHERE node_id = :n",
        bram=ids["bram"].hex,
        n=ids["notities"].hex,
    )
    await _do(settings, 'DELETE FROM "user" WHERE id = :u', u=ids["bram"].hex)
    assert await _q(
        settings, "SELECT created_by, via_link FROM node_origin WHERE node_id = :n",
        n=ids["notities"].hex,
    ) == [(None, 0)]  # fmt: skip
    await _do(settings, 'DELETE FROM "user" WHERE id = :u', u=ids["eva"].hex)
    for table in ("face", "face_person", "face_scan", "face_owner", "node_origin", "person"):
        assert await _count(settings, table) == 0, table
