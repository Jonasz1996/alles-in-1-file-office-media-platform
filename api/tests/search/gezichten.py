"""Hulpmiddelen voor de tests van 0011 en 0012 (bouwontwerp fase 6, §3.1a): rijen van gezichten
en beeld met een merkteken, en tellen hoe vaak dat merkteken nog in platform.db en -wal staat.

Alles hier schrijft met een gewone verbinding uit de pool (dus met de triggers en met
secure_delete), op een database van tests/search/migratie.py (fill_0009 en dan heen)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from app.config import Settings
from app.db import get_engine
from tests.search.migratie import NOW, SHA_JPG

MERK = b"MERKTEKEN-GEZICHT-"
# Groter dan een pagina (4096): ook de overloop-pagina's moeten leeg
VEC = MERK * 300
TEKST = "MERKTEKEN-GEZICHT-TEKST"
# De vectoren van beeld (0012) met een eigen merkteken: zo telt een test van 0012 alleen die
BEELD = b"MERKTEKEN-BEELD-" * 300


def marks(settings: Settings, merk: bytes = b"MERKTEKEN") -> dict[str, int]:
    """Hoe vaak merk in platform.db en in -wal staat (0 als er geen -wal is)."""
    db = settings.data_dir / "platform.db"
    out = {}
    for path in (db, db.with_name(db.name + "-wal")):
        out[path.name] = path.read_bytes().count(merk) if path.exists() else 0
    return out


def _photo(conn, owner: uuid.UUID, parent: uuid.UUID, name: str, sha: str = SHA_JPG) -> uuid.UUID:
    node = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, "
            "created_at, modified_at, search_name) VALUES (:id, :parent, :owner, :name, 'file', "
            "'image/jpeg', 1, :sha, :t, :t, :search)"
        ),
        {
            "id": node.hex,
            "parent": parent.hex,
            "owner": owner.hex,
            "name": name,
            "sha": sha,
            "t": NOW,
            "search": name.lower(),
        },  # fmt: skip
    )
    return node


def _faces(conn, ids: dict[str, uuid.UUID]) -> dict[str, uuid.UUID]:
    eva = ids["eva"]
    out = {"foto": _photo(conn, eva, ids["root"], "oma.jpg"), "person": uuid.uuid4()}
    conn.execute(
        sa.text(
            "INSERT INTO person (id, owner_id, name, search_name, created_at, updated_at) "
            "VALUES (:id, :eva, 'Oma', 'oma', :t, :t)"
        ),
        {"id": out["person"].hex, "eva": eva.hex, "t": NOW},
    )
    face = (
        "INSERT INTO face (id, node_id, owner_id, x, y, w, h, score, px, vec, version, cluster, "
        "suggest_id, suggest_score, suggest_why, hidden, created_at) VALUES (:id, :node, :eva, "
        "0.1, 0.1, 0.2, 0.2, 0.9, 80, :vec, 1, :cluster, :suggest, :score, :why, :hidden, :t)"
    )
    # Een bevestigd gezicht, een voorstel, een in een groep, en een "niet tonen" (zonder vector)
    for key, cluster, suggest, hidden in (
        ("ja", None, None, 0),
        ("voorstel", None, out["person"], 0),
        ("groep", 7, None, 0),
        ("verborgen", None, None, 1),
    ):
        out[key] = uuid.uuid4()
        conn.execute(
            sa.text(face),
            {
                "id": out[key].hex,
                "node": out["foto"].hex,
                "eva": eva.hex,
                "vec": None if hidden else VEC,
                "cluster": cluster,
                "suggest": suggest.hex if suggest else None,
                "score": 0.8 if suggest else None,
                "why": TEKST if suggest else None,
                "hidden": hidden,
                "t": NOW,
            },
        )
    conn.execute(
        sa.text(
            "INSERT INTO face_person (face_id, person_id, owner_id, verdict, created_at) "
            "VALUES (:face, :person, :eva, 'ja', :t)"
        ),
        {"face": out["ja"].hex, "person": out["person"].hex, "eva": eva.hex, "t": NOW},
    )
    conn.execute(
        sa.text(
            "INSERT INTO face_scan (node_id, owner_id, version, state, faces, tries, code, prio, "
            "queued_at, updated_at) VALUES (:node, :eva, 1, 'done', 4, 1, :code, :t, :t, :t)"
        ),
        {"node": out["foto"].hex, "eva": eva.hex, "code": TEKST, "t": NOW},
    )
    conn.execute(
        sa.text(
            "INSERT INTO face_owner (owner_id, run, grouped_at, suggested_at, verdict_at, "
            "served_at, wiping) VALUES (:eva, 3, :t, :t, :t, :t, 0)"
        ),
        {"eva": eva.hex, "t": NOW},
    )
    # Wie uploadde: Eva zelf, en een bestand via een link (created_by NULL, Keuze 69)
    for node, by, link in ((out["foto"], eva, 0), (ids["notities"], None, 1)):
        conn.execute(
            sa.text(
                "INSERT INTO node_origin (node_id, created_by, via_link) VALUES (:n, :by, :link)"
            ),
            {"n": node.hex, "by": by.hex if by else None, "link": link},
        )
    return out


async def fill_faces(settings: Settings, ids: dict[str, uuid.UUID]) -> dict[str, uuid.UUID]:
    """Op 0011 of later: een levende foto van Eva met vier gezichten (bevestigd, voorstel, groep,
    verborgen), Oma, een scan, face_owner, en twee rijen node_origin. Vectoren en tekst met
    merkteken."""
    async with get_engine(settings).begin() as conn:
        return await conn.run_sync(_faces, ids)


def _beeld(conn, ids: dict[str, uuid.UUID]) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO blob_embed (blob_sha, model, version, vec, updated_at) "
            "VALUES (:sha, 'clip', 1, :vec, :t)"
        ),
        {"sha": SHA_JPG, "vec": BEELD, "t": NOW},
    )
    conn.execute(
        sa.text(
            "INSERT INTO photo_pref (user_id, location, memories, ocr_photos, faces, updated_at, "
            "beeld) VALUES (:eva, 'uit', 0, 1, 'uit', :t, 'aan')"
        ),
        {"eva": ids["eva"].hex, "t": NOW},
    )


async def fill_beeld(settings: Settings, ids: dict[str, uuid.UUID]) -> None:
    """Op 0012: een vector met merkteken voor de foto's van Eva, en haar foto-instellingen met
    beeld aan."""
    async with get_engine(settings).begin() as conn:
        await conn.run_sync(_beeld, ids)


async def rows(settings: Settings, table: str) -> list[tuple]:
    async with get_engine(settings).connect() as conn:
        return [tuple(r) for r in (await conn.exec_driver_sql(f"SELECT * FROM {table}")).all()]
