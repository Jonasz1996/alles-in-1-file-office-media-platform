"""De triggers van 0012 (bouwontwerp fase 6, §3.1a en Keuze 76; K1-08, BZ8): beeld_owner.gen gaat
omhoog als het bestand met beelden van een eigenaar verandert, en alleen dan. X3 kijkt alleen of
gen > built_gen; hoeveel hij omhoog gaat, doet er niet toe. Op een echte database, met en zonder
FTS5:

- een nieuw beeldbestand: de rij van de eigenaar erbij (gen 1), daarna steeds één hoger; een map,
  een ander bestand of een album: niets;
- nieuwe inhoud: twee hoger (BUMP(OLD.owner_id) en BUMP(NEW.owner_id), zoals Keuze 76 hem
  schrijft), ook als de nieuwe inhoud geen beeld meer is (OLD.ftype); dezelfde inhoud nog eens:
  niets;
- een andere eigenaar: allebei één hoger;
- prullenbak in en uit, een andere naam of map: niets (dat filtert X3 bij het lezen);
- definitief wissen: één hoger;
- een gebruiker wissen met zijn foto's: geen fout van de foreign key, en zijn rij komt niet
  terug (de cascade wist eerst de gebruiker, dan zijn nodes);
- blob_embed gaat weg met zijn blob (cascade).
"""

import uuid

import pytest
import sqlalchemy as sa
from alembic import command

from app.db import get_engine
from tests.search.migratie import NOW, SHA_DOC, SHA_JPG, SHA_TXT, fill_0009, migrate, settings_at

pytestmark = pytest.mark.anyio
FTS = pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])


async def _do(settings, sql: str, **params) -> None:
    async with get_engine(settings).begin() as conn:
        await conn.execute(sa.text(sql), params)


async def _gen(settings) -> dict[str, int]:
    async with get_engine(settings).connect() as conn:
        rows = await conn.exec_driver_sql("SELECT user_id, gen FROM beeld_owner")
        return {r[0]: r[1] for r in rows.all()}


async def _world(tmp_path, fts5: bool):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "0012", fts5=fts5)
    assert await _gen(settings) == {}
    return settings, ids


async def _new(settings, ids, owner: str, name: str, kind: str, mime: str | None, sha: str | None):
    node = uuid.uuid4()
    root = ids["root" if owner == "eva" else "bram_root"]
    await _do(
        settings,
        "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, created_at, "
        "modified_at, search_name) VALUES (:id, :root, :owner, :name, :kind, :mime, 1, :sha, :t, "
        ":t, :name)",
        id=node.hex,
        root=root.hex,
        owner=ids[owner].hex,
        name=name,
        kind=kind,
        mime=mime,
        sha=sha,
        t=NOW,
    )
    return node.hex


@FTS
async def test_nieuw_en_niet_nieuw(tmp_path, fts5):
    settings, ids = await _world(tmp_path, fts5)
    eva = ids["eva"].hex
    await _new(settings, ids, "eva", "map", "dir", None, None)
    await _new(settings, ids, "eva", "brief.txt", "file", "text/plain", SHA_TXT)
    await _new(settings, ids, "eva", "verslag.pdf", "file", "application/pdf", SHA_DOC)
    await _new(settings, ids, "eva", "film.mp4", "file", "video/mp4", SHA_DOC)
    assert await _gen(settings) == {}
    await _new(settings, ids, "eva", "a.jpg", "file", "image/jpeg", SHA_JPG)
    assert await _gen(settings) == {eva: 1}
    await _new(settings, ids, "eva", "b.png", "file", "image/png", SHA_JPG)
    assert await _gen(settings) == {eva: 2}


@FTS
async def test_nieuwe_inhoud_en_een_andere_eigenaar(tmp_path, fts5):
    settings, ids = await _world(tmp_path, fts5)
    eva, bram = ids["eva"].hex, ids["bram"].hex
    foto = await _new(settings, ids, "eva", "a.jpg", "file", "image/jpeg", SHA_JPG)
    tekst = await _new(settings, ids, "eva", "c.txt", "file", "text/plain", SHA_TXT)
    assert await _gen(settings) == {eva: 1}

    # Dezelfde inhoud, een andere naam, een andere map: niets
    await _do(settings, "UPDATE node SET blob_id = blob_id WHERE id = :n", n=foto)
    await _do(settings, "UPDATE node SET name = 'z.jpg', search_name = 'z.jpg' WHERE id = :n",
              n=foto)  # fmt: skip
    await _do(settings, "UPDATE node SET parent_id = :p WHERE id = :n", p=ids["werk"].hex, n=foto)
    assert await _gen(settings) == {eva: 1}
    # Nieuwe inhoud van een tekstbestand: niets
    await _do(settings, f"UPDATE node SET blob_id = '{SHA_DOC}' WHERE id = :n", n=tekst)
    assert await _gen(settings) == {eva: 1}

    # Nieuwe inhoud van de foto: de oude en de nieuwe eigenaar, hier twee keer Eva
    await _do(settings, f"UPDATE node SET blob_id = '{SHA_DOC}' WHERE id = :n", n=foto)
    assert await _gen(settings) == {eva: 3}
    # Nieuwe inhoud die geen beeld meer is: ook (hij moet uit het bestand)
    await _do(
        settings,
        f"UPDATE node SET blob_id = '{SHA_TXT}', mime = 'text/plain' WHERE id = :n",
        n=foto,
    )
    assert await _gen(settings) == {eva: 5}
    # Daarna is het geen beeld: nieuwe inhoud zonder beeld doet niets meer
    await _do(settings, f"UPDATE node SET blob_id = '{SHA_DOC}' WHERE id = :n", n=foto)
    await _do(settings, f"UPDATE node SET blob_id = '{SHA_TXT}' WHERE id = :n", n=foto)
    assert await _gen(settings) == {eva: 5}
    # En weer een beeld
    await _do(
        settings,
        f"UPDATE node SET blob_id = '{SHA_JPG}', mime = 'image/jpeg' WHERE id = :n",
        n=foto,
    )
    assert await _gen(settings) == {eva: 7}

    # Een andere eigenaar: allebei, Bram met een nieuwe rij
    await _do(
        settings,
        "UPDATE node SET owner_id = :bram, parent_id = :root WHERE id = :n",
        bram=bram,
        root=ids["bram_root"].hex,
        n=foto,
    )
    assert await _gen(settings) == {eva: 8, bram: 1}


@FTS
async def test_prullenbak_en_definitief_wissen(tmp_path, fts5):
    settings, ids = await _world(tmp_path, fts5)
    eva = ids["eva"].hex
    foto = await _new(settings, ids, "eva", "a.jpg", "file", "image/jpeg", SHA_JPG)
    for value in (NOW, None):
        await _do(settings, "UPDATE node SET deleted_at = :v WHERE id = :n", v=value, n=foto)
    # Een map erboven in de prullenbak (dead via de triggers van 0010): ook niets
    await _do(settings, "UPDATE node SET parent_id = :p WHERE id = :n", p=ids["werk"].hex, n=foto)
    await _do(settings, "UPDATE node SET deleted_at = :t WHERE id = :p", t=NOW, p=ids["werk"].hex)
    assert await _gen(settings) == {eva: 1}
    await _do(settings, "DELETE FROM node WHERE id = :n", n=foto)
    assert await _gen(settings) == {eva: 2}


@FTS
async def test_een_gebruiker_wissen_met_zijn_fotos(tmp_path, fts5):
    """BZ8: de cascade wist eerst de rij in user, dan zijn nodes. Zonder de voorwaarde dat de
    gebruiker nog bestaat, gaf node_ad_beeld daar een fout van de foreign key (of kwam zijn rij
    terug)."""
    settings, ids = await _world(tmp_path, fts5)
    eva, bram = ids["eva"].hex, ids["bram"].hex
    for i in range(3):
        await _new(settings, ids, "bram", f"{i}.jpg", "file", "image/jpeg", SHA_JPG)
    await _new(settings, ids, "eva", "a.jpg", "file", "image/jpeg", SHA_JPG)
    assert await _gen(settings) == {eva: 1, bram: 3}
    await _do(settings, 'DELETE FROM "user" WHERE id = :u', u=bram)
    assert await _gen(settings) == {eva: 1}
    async with get_engine(settings).connect() as conn:
        left = await conn.exec_driver_sql("SELECT count(*) FROM node WHERE owner_id = ?", (bram,))
        assert left.scalar() == 0


@FTS
async def test_blob_embed_gaat_met_zijn_blob(tmp_path, fts5):
    settings, ids = await _world(tmp_path, fts5)
    await _do(
        settings,
        "INSERT INTO blob_embed (blob_sha, model, version, vec, updated_at) "
        "VALUES (:sha, 'clip', 1, x'00', :t)",
        sha=SHA_TXT,
        t=NOW,
    )
    # Eén vector per inhoud (ux_blob_embed_sha)
    with pytest.raises(sa.exc.IntegrityError):
        await _do(
            settings,
            "INSERT INTO blob_embed (blob_sha, model, version, vec, updated_at) "
            "VALUES (:sha, 'clip', 2, x'01', :t)",
            sha=SHA_TXT,
            t=NOW,
        )
    await _do(settings, "UPDATE node SET blob_id = NULL WHERE blob_id = :sha", sha=SHA_TXT)
    await _do(settings, "DELETE FROM blob WHERE sha256 = :sha", sha=SHA_TXT)
    async with get_engine(settings).connect() as conn:
        assert (await conn.exec_driver_sql("SELECT count(*) FROM blob_embed")).scalar() == 0


@FTS
async def test_search_triggers_zet_ook_deze_terug(tmp_path, fts5):
    """app.cli search-triggers (rebuild.restore_triggers) kent de triggers van 0011 en 0012:
    zonder node_au_owner_face en node_au_beeld ging een andere eigenaar langs de gezichten en
    langs gen heen. Terugzetten ruimt de gezichten van de oude eigenaar op en zet gen van elke
    eigenaar omhoog, zodat X3 elk bestand opnieuw maakt."""
    from app.search import rebuild, schema
    from tests.search.gezichten import fill_faces

    settings, ids = await _world(tmp_path, fts5)
    made = await fill_faces(settings, ids)
    eva, bram = ids["eva"].hex, ids["bram"].hex
    assert await _gen(settings) == {eva: 1}
    async with get_engine(settings).begin() as conn:
        for name in ("node_au_owner_face", "node_au_beeld"):
            await conn.exec_driver_sql(f"DROP TRIGGER {name}")
    await _do(
        settings,
        "UPDATE node SET owner_id = :bram, parent_id = :root WHERE id = :n",
        bram=bram,
        root=ids["bram_root"].hex,
        n=made["foto"].hex,
    )
    assert await _gen(settings) == {eva: 1}
    async with get_engine(settings).connect() as conn:
        assert (await conn.exec_driver_sql("SELECT count(*) FROM face")).scalar() == 4

    hersteld = await rebuild.restore_triggers(get_engine(settings))
    assert sorted(hersteld.created) == ["node_au_beeld", "node_au_owner_face"]
    assert hersteld.photo_rows >= 5  # vier gezichten en de scan
    async with get_engine(settings).connect() as conn:
        assert (await conn.exec_driver_sql("SELECT count(*) FROM face")).scalar() == 0
        assert (await conn.exec_driver_sql("SELECT count(*) FROM face_scan")).scalar() == 0
        found = dict(
            (
                await conn.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
                )
            ).all()
        )
    assert found == (schema.TRIGGERS if fts5 else schema.TRIGGERS_NO_FTS)
    assert await _gen(settings) == {eva: 2}  # Bram heeft nog geen rij: X3 begint daar bij 0
    # Nog eens: niets te doen, en gen blijft
    leeg = await rebuild.restore_triggers(get_engine(settings))
    assert (leeg.created, leeg.dropped, leeg.photo_rows) == ([], [], 0)
    assert await _gen(settings) == {eva: 2}
