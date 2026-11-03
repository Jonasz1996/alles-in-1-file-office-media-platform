"""Hulpmiddelen voor de tests van migratie 0010: een database op een revisie zetten (met of
zonder FTS5), een boom van 0009 vullen, en controleren dat num, mpath, dead en de FTS-tabellen
kloppen met wat er in node staat."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from app.config import Settings
from app.db import get_engine
from app.migrate import alembic_config

TZ = "Europe/Amsterdam"
NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC).isoformat()
SHA_DOC = "d" * 64
SHA_JPG = "e" * 64
SHA_TXT = "f" * 64
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def settings_at(tmp_path, name: str) -> Settings:
    path = tmp_path / name
    path.mkdir()
    return Settings(data_dir=path, jwt_secret="test" * 8, timezone=TZ)


async def migrate(settings: Settings, fn, revision: str, fts5: bool | None = None) -> None:
    """fn is command.upgrade of command.downgrade. fts5=False: doe alsof SQLite geen FTS5 kent
    (de vlag search_fts5 die 0010 leest)."""
    cfg = alembic_config(settings.db_url)

    def run(connection) -> None:
        cfg.attributes["connection"] = connection
        cfg.attributes["timezone"] = TZ
        if fts5 is not None:
            cfg.attributes["search_fts5"] = fts5
        fn(cfg, revision)

    async with get_engine(settings).begin() as conn:
        await conn.run_sync(run)


def _user(conn, name: str) -> tuple[uuid.UUID, uuid.UUID]:
    user, root = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        sa.text(
            'INSERT INTO "user" (id, email, display_name, password_hash, role, quota_bytes, '
            "root_id, created_at) VALUES (:id, :email, :name, 'x', 'user', 0, :root, :t)"
        ),
        {"id": user.hex, "email": f"{name}@example.nl", "name": name, "root": root.hex, "t": NOW},
    )
    _node(conn, root, None, user, "", "dir")
    return user, root


def _node(
    conn,
    node_id: uuid.UUID,
    parent: uuid.UUID | None,
    owner: uuid.UUID,
    name: str,
    kind: str = "file",
    mime: str | None = None,
    blob: str | None = None,
    deleted: bool = False,
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, "
            "created_at, modified_at, deleted_at, search_name) VALUES (:id, :parent, :owner, "
            ":name, :kind, :mime, 1, :blob, :t, :t, :deleted, :search)"
        ),
        {
            "id": node_id.hex,
            "parent": parent.hex if parent else None,
            "owner": owner.hex,
            "name": name,
            "kind": kind,
            "mime": mime,
            "blob": blob,
            "t": NOW,
            "deleted": NOW if deleted else None,
            "search": name.lower(),
        },
    )


def _fill(conn) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    ids["eva"], ids["root"] = _user(conn, "eva")
    ids["bram"], ids["bram_root"] = _user(conn, "bram")
    for sha, mime in (
        (SHA_DOC, DOCX),
        (SHA_JPG, "image/jpeg"),
        (SHA_TXT, "text/plain"),
    ):
        conn.execute(
            sa.text(
                "INSERT INTO blob (sha256, size, ref_count, mime, created_at) "
                "VALUES (:sha, 1, 1, :mime, :t)"
            ),
            {"sha": sha, "mime": mime, "t": NOW},
        )
    eva = ids["eva"]
    tree = [
        # naam, ouder, soort, mime, blob, prullenbak
        ("werk", "root", "dir", None, None, False),
        ("verslagen", "werk", "dir", None, None, False),
        ("verslag", "verslagen", "file", DOCX, SHA_DOC, False),
        ("vakantie", "root", "dir", None, None, True),
        ("strand", "vakantie", "file", "image/jpeg", SHA_JPG, False),
        ("diep", "vakantie", "dir", None, None, False),
        ("schelp", "diep", "file", "image/jpeg", SHA_JPG, False),
        ("los", "root", "file", "text/plain", SHA_TXT, True),
        ("notities", "root", "file", "text/plain", SHA_TXT, False),
    ]
    for name, parent, kind, mime, blob, deleted in tree:
        ids[name] = uuid.uuid4()
        _node(conn, ids[name], ids[parent], eva, f"{name}.x" if kind == "file" else name,
              kind, mime, blob, deleted)  # fmt: skip
    # Een album van Eva met twee foto's, gedeeld met Bram; en de map werk gedeeld met Bram
    ids["album"] = uuid.uuid4()
    _node(conn, ids["album"], None, eva, "zomer", "album")
    conn.execute(sa.text("INSERT INTO album (id, description) VALUES (:id, '')"),
                 {"id": ids["album"].hex})  # fmt: skip
    for item in ("strand", "notities"):
        conn.execute(
            sa.text(
                "INSERT INTO album_item (album_id, node_id, added_by, added_at) "
                "VALUES (:album, :node, :by, :t)"
            ),
            {"album": ids["album"].hex, "node": ids[item].hex, "by": eva.hex, "t": NOW},
        )
    for node in ("werk", "album"):
        conn.execute(
            sa.text(
                "INSERT INTO share (id, node_id, owner_id, grantee_id, permission, created_at) "
                "VALUES (:id, :node, :owner, :grantee, 'read', :t)"
            ),
            {
                "id": uuid.uuid4().hex,
                "node": ids[node].hex,
                "owner": eva.hex,
                "grantee": ids["bram"].hex,
                "t": NOW,
            },
        )
    # Een bibliotheek met een titel op het verslag (komt als extra in fts_node)
    lib = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO media_library (id, owner_id, root_id, kind, name, created_at) "
            "VALUES (:id, :owner, :root, 'movies', 'Films', :t)"
        ),
        {"id": lib.hex, "owner": eva.hex, "root": ids["root"].hex, "t": NOW},
    )
    conn.execute(
        sa.text(
            "INSERT INTO media_item (node_id, library_id, kind, title, sort_title, added_at, "
            "updated_at) VALUES (:node, :lib, 'movie', 'Jaarverslag Film', 'jaarverslag', :t, :t)"
        ),
        {"node": ids["verslag"].hex, "lib": lib.hex, "t": NOW},
    )
    return ids


async def fill_0009(settings: Settings) -> dict[str, uuid.UUID]:
    """Eva en Bram, een boom van Eva tot diepte 3 met een map in de prullenbak (met een submap)
    en een los bestand in de prullenbak, een album met items, twee delingen en een titel van
    media. Op revisie 0009 (zonder de kolommen van 0010)."""
    async with get_engine(settings).begin() as conn:
        return await conn.run_sync(_fill)


DATA = {
    "node": "SELECT id, parent_id, owner_id, name, kind, mime, blob_id, deleted_at, search_name "
    "FROM node ORDER BY id",
    "share": "SELECT id, node_id, owner_id, grantee_id, permission FROM share ORDER BY id",
    "album": "SELECT id, description FROM album ORDER BY id",
    "album_item": "SELECT album_id, node_id, added_by FROM album_item ORDER BY 1, 2",
    "media_item": "SELECT node_id, title FROM media_item ORDER BY node_id",
    "blob": "SELECT sha256, ref_count FROM blob ORDER BY sha256",
}


async def data(settings: Settings) -> dict[str, list]:
    """De bestanden, delingen, albums en titels zelf: blijven gelijk bij heen en terug."""
    async with get_engine(settings).connect() as conn:
        out = {}
        for key, query in DATA.items():
            out[key] = [tuple(r) for r in (await conn.exec_driver_sql(query)).all()]
        return out


def check_tree(conn) -> int:
    """num uniek en gezet, elk pad = pad van de ouder + num, dead = een node of een map erboven
    in de prullenbak. Een synchrone verbinding; geeft het aantal nodes."""
    rows = conn.exec_driver_sql(
        "SELECT id, parent_id, num, mpath, dead, deleted_at IS NOT NULL FROM node"
    ).all()
    by_id = {r[0]: r for r in rows}
    nums = [r[2] for r in rows]
    assert None not in nums, "een node zonder num"
    assert len(set(nums)) == len(nums), "num is niet uniek"
    next_num = conn.exec_driver_sql("SELECT next FROM search_seq WHERE id = 1").scalar()
    assert next_num > max(nums, default=0)
    for node_id, _parent, _num, mpath, dead, _trashed in rows:
        chain, cur = [], by_id[node_id]
        while cur is not None:
            chain.append(cur)
            cur = by_id.get(cur[1]) if cur[1] else None
        want_path = "/" + "".join(f"{c[2]}/" for c in reversed(chain))
        assert mpath == want_path, (node_id, mpath, want_path)
        assert dead == int(any(c[5] for c in chain)), (node_id, dead)
    return len(rows)


def check_fts(conn) -> None:
    """De FTS-tabellen zijn heel en gelijk aan hun inhoud (view search_src en node)."""
    for table in ("fts_node", "fts_name"):
        conn.exec_driver_sql(f"INSERT INTO {table}({table}, rank) VALUES ('integrity-check', 1)")


def names(conn, pattern: str) -> set[str]:
    """Welke namen fts_name vindt (trigram: minstens drie tekens)."""
    rows = conn.exec_driver_sql(
        "SELECT n.name FROM fts_name f JOIN node n ON n.num = f.rowid WHERE fts_name MATCH ?",
        (f'"{pattern}"',),
    ).all()
    return {r[0] for r in rows}


def words(conn, query: str) -> set[str]:
    """Welke namen fts_node vindt voor een FTS5-vraag."""
    rows = conn.exec_driver_sql(
        "SELECT n.name FROM fts_node f JOIN node n ON n.num = f.rowid WHERE fts_node MATCH ?",
        (query,),
    ).all()
    return {r[0] for r in rows}
