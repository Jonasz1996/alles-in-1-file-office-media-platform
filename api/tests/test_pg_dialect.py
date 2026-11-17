"""Fase 6 op PostgreSQL (0.8.2, P1 pg-sql): elke vraag die P1 een eigen vorm gaf.

Drie soorten tests:
- De tekst op SQLite is byte voor byte die van vóór P1 (aa6fa8b): tests/fixtures/
  pg_dialect_sqlite.json is met dezelfde bouwers (_hinted, _built) op die code gemaakt. Zo blijven
  de plannen van SQLite gelijk, ook voor de vragen met een hint (+kolom, rowid, CROSS JOIN).
- Dezelfde vragen compileren voor PostgreSQL en hebben dan niets van SQLite: geen rowid,
  json_each, group_concat, instr, unaire +, PRAGMA, sqlite_master, CROSS JOIN of IN gone.
- Op de database van de test (SQLite, of PostgreSQL met PLATFORM_TEST_DATABASE_URL, zie
  conftest.py) doet elke vraag wat hij moet: elke upsert voegt in en werkt dan bij, de kaart
  kiest de nieuwste foto, de tellers van /admin/jobs kloppen, een serie begint bij zijn eerste
  item, de bereiken van de toegang (0, 1 en 70 wortels), een venster met backslashes en tekens
  van meer bytes, een stand buiten bereik, en de gezichten. Wat alleen op PostgreSQL kan, staat
  onder requires_pg.

face.seq (de volgorde van invoegen van een gezicht op PostgreSQL) maakt migratie 0013 van P2;
zolang die er niet is, zet _face_seq hem hier zelf neer, in dezelfde vorm.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import BigInteger, Integer, bindparam, cast, func, select, text, update
from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg
from sqlalchemy.dialects.sqlite import aiosqlite
from sqlalchemy.exc import DBAPIError

from app import dbdialect, face_rows, memories, origin, photo_hash, photo_rows, previews
from app.albums_rule import skip as album_skip
from app.geo.types import Place
from app.media import progress
from app.media.items import _first_node
from app.models import (
    AlbumRule,
    AlbumSkip,
    BlobHash,
    BlobMeta,
    BlobText,
    DupPair,
    Face,
    FaceOwner,
    FacePerson,
    FaceScan,
    MediaItem,
    MediaLibrary,
    MediaShow,
    MemoryHide,
    Node,
    NodeOrigin,
    Person,
    PhotoGeo,
)
from app.people.faces import read
from app.people.faces import scan as face_scan
from app.routers import photos_map
from app.search import access, snippets
from tests.factories import (
    create_album,
    create_blob,
    create_file,
    create_folder,
    create_user,
    maker,
)

requires_pg = pytest.mark.skipif(
    not os.environ.get("PLATFORM_TEST_DATABASE_URL"), reason="needs PostgreSQL"
)

API_DIR = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((API_DIR / "tests" / "fixtures" / "pg_dialect_sqlite.json").read_text())

OWNER = uuid.UUID("11111111-2222-3333-4444-555555555555")
NODE = uuid.UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
FACE = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
SHA = "ab" * 32


# --- De vragen zelf, gebouwd met de code van de app (zoals voor de vaste teksten) -------------


class _Result:
    rowcount = 0

    def __iter__(self):
        return iter(())

    def all(self):
        return []

    def first(self):
        return None

    def scalars(self):
        return self

    def scalar(self):
        return None

    def scalar_one(self):
        return 0


class Recorder:
    """Een AsyncSession die elke vraag bewaart en niets terugzegt."""

    def __init__(self, dialect: Any) -> None:
        self.dialect = dialect
        self.statements: list[Any] = []

    def get_bind(self) -> Any:
        return SimpleNamespace(dialect=self.dialect)

    async def execute(self, stmt: Any, *args: Any, **kw: Any) -> _Result:
        self.statements.append(stmt)
        return _Result()

    async def scalar(self, stmt: Any, *args: Any, **kw: Any) -> Any:
        self.statements.append(stmt)
        return None


SQLITE = aiosqlite.dialect()
PG = pg_asyncpg.dialect()


def _text_of(stmt: Any, dialect: Any) -> str:
    if isinstance(stmt, str):
        return stmt
    return str(stmt.compile(dialect=dialect))


def _recorded(dialect: Any, fn) -> list[Any]:
    rec = Recorder(dialect)

    async def go() -> None:
        await fn(rec)

    asyncio.run(go())
    return rec.statements


def _hinted(dialect: Any) -> dict[str, list[Any]]:
    """De vragen met een hint voor SQLite (+kolom, rowid, CROSS JOIN, IN gone)."""
    from app.beeld import search as beeld_search
    from app.search.live import own_live
    from app.search.types import SearchParams

    out: dict[str, list[Any]] = {}
    out["verdicts_faces"] = [
        select(Face.id, Face.node_id)
        .join(Node, Node.id == Face.node_id)
        .where(
            Face.id.in_([FACE]), face_rows.no_index(Face.owner_id) == OWNER, own_live(Node, OWNER)
        )
        .where(Face.hidden == 0)
    ]
    out["links_boxes"] = [
        select(Face.id, Face.x, Face.y, Face.w, Face.h).where(
            Face.node_id == NODE, face_rows.no_index(Face.owner_id) == OWNER, Face.hidden == 0
        )
    ]
    out["scan_take"] = [
        update(FaceScan)
        .where(FaceScan.node_id.in_([NODE]), face_rows.no_index(FaceScan.state) == "wait")
        .values(state="busy", updated_at=AT)
    ]
    ja = select(FacePerson.face_id).where(FacePerson.verdict == "ja")
    out["group_only"] = [
        select(Face.id, Face.vec)
        .join(Node, Node.id == Face.node_id)
        .where(
            own_live(Node, OWNER),
            face_rows.no_index(Face.owner_id) == OWNER,
            Face.hidden == 0,
            Face.vec.is_not(None),
            Face.id.not_in(ja),
        )
        .order_by(Face.created_at.desc(), Face.id)
        .where(Face.id.in_([FACE]))
    ]
    out["read_file_faces"] = [
        read._face_cols(OWNER, own_live, by_node=True)
        .where(Face.node_id == NODE)
        .order_by(Face.x, Face.id)
        .limit(200)
    ]
    out["read_face_cols"] = [read._face_cols(OWNER)]
    out["read_gone_by"] = _recorded(
        dialect,
        lambda db: asyncio.gather(
            read._gone_by(db, OWNER, "cluster"), read._gone_by(db, OWNER, "suggest_id")
        ),
    )
    out["read_firsts"] = _recorded(
        dialect,
        lambda db: asyncio.gather(
            read._firsts(db, OWNER, "suggest_id", [FACE, OWNER]),
            read._firsts(db, OWNER, "cluster", [3, 4, 5]),
        ),
    )
    out["read_counts"] = _recorded(dialect, lambda db: read.counts(db, OWNER))
    out["photo_hash_scan"] = _recorded(
        dialect,
        lambda db: asyncio.gather(
            photo_hash._scan(db, 0x0123456789ABCDEF), photo_hash._scan(db, 42, OWNER)
        ),
    )
    user = SimpleNamespace(id=OWNER, role="user", root_id=NODE)
    out["beeld_select"] = [beeld_search._select(SearchParams(), user, dialect.name)]
    return out


def _built(dialect: Any) -> dict[str, list[Any]]:
    """De andere vragen die P1 een eigen vorm gaf."""
    out: dict[str, list[Any]] = {}
    user = SimpleNamespace(id=OWNER, role="user", root_id=NODE)
    out["photos_map_cells"] = _recorded(
        dialect, lambda db: photos_map._cells(db, user, 3, [(10, 20)], 30, 40, None)
    )
    out["photo_counts"] = _recorded(dialect, lambda db: previews.photo_counts(db))
    out["snippets_sources"] = [snippets._sources(dialect.name)]
    out["photo_rows_meta"] = _recorded(
        dialect,
        lambda db: photo_rows.set_meta(
            db, [{"blob_sha": SHA, "camera": "c", "lens": None, "gps": 1}]
        ),
    )
    spot = photo_rows.Spot(52.0, 5.0, None, "exif")
    place = Place(id=150904566956734, name="Utrecht", admin1="Utrecht", cc="NL", km=1.0)
    out["photo_rows_write"] = _recorded(
        dialect,
        lambda db: asyncio.gather(
            photo_rows.write(db, [(SHA, spot, place)], replace=True),
            photo_rows.write(db, [(SHA, spot, place)], replace=False),
        ),
    )
    return out


def _upserts(dialect: Any) -> dict[str, list[Any]]:
    """Alleen voor PostgreSQL: de upserts die een insert van SQLite hadden (die compileert daar
    niet), en de eerste van een serie."""
    hidden_user = SimpleNamespace(id=OWNER)
    out: dict[str, list[Any]] = {}
    out["photo_hash_write"] = _recorded(
        dialect,
        lambda db: photo_hash.write(
            db,
            [photo_hash.Hashed(SHA, "ok", 0x0123456789ABCDEF)],
            [{"p_a": SHA, "p_b": "cd" * 32, "p_dist": 3, "p_mad": 1.0, "p_level": "near"}],
        ),
    )
    out["photo_hash_none"] = _recorded(dialect, lambda db: photo_hash.mark_none(db, [SHA]))
    out["memories_hide"] = _recorded(
        dialect, lambda db: memories.hide(db, hidden_user, "day", "2026-01-02")
    )
    out["face_owner"] = _recorded(
        dialect,
        lambda db: face_rows.write_group(db, OWNER, [], full=False, first=False, at=AT),
    )
    out["origin_placed"] = [origin.placed_stmt(dialect.name, NODE, OWNER)]
    out["show_first"] = [
        select(MediaItem.show_id, _first_node(dialect.name)).group_by(MediaItem.show_id)
    ]
    return out


def _all(dialect: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for group in (_hinted, _built):
        for name, stmts in group(dialect).items():
            out[name] = [_text_of(s, dialect) for s in stmts]
    return out


def test_de_tekst_op_sqlite_is_die_van_ervoor():
    """Byte voor byte: dezelfde tekst geeft dezelfde plannen (de snelheidstests en de plannen
    van tests/search/test_search_plans.py rekenen erop)."""
    found = _all(SQLITE)
    assert sorted(found) == sorted(GOLDEN)
    for name, texts in GOLDEN.items():
        assert found[name] == texts, name


# Wat alleen SQLite kent. Een unaire + staat na een haakje, een komma of een sleutelwoord; een
# + tussen twee waarden (a + b) telt niet.
SQLITE_ONLY = {
    "rowid": re.compile(r"\browid\b", re.I),
    "json_each": re.compile(r"\bjson_each\b", re.I),
    "json-pad": re.compile(r"->>\s*'\$"),
    "group_concat": re.compile(r"\bgroup_concat\b", re.I),
    "instr": re.compile(r"\binstr\s*\(", re.I),
    "PRAGMA": re.compile(r"\bPRAGMA\b", re.I),
    "sqlite_master": re.compile(r"\bsqlite_master\b", re.I),
    "CROSS JOIN": re.compile(r"\bCROSS\s+JOIN\b", re.I),
    "IN gone": re.compile(r"\bIN\s+gone\b", re.I),
    "BLOB": re.compile(r"\bBLOB\b"),
    "unaire +": re.compile(
        r"(?:^|[(,]|\b(?:AND|OR|WHERE|BY|ON|NOT|SELECT|WHEN|THEN|ELSE))\s*\+\s*[A-Za-z_\"(]", re.I
    ),
}


def _sqlite_only(sql: str) -> list[str]:
    return [name for name, pattern in SQLITE_ONLY.items() if pattern.search(sql)]


def test_de_zoeker_vindt_wat_alleen_sqlite_kent():
    """De zoeker zelf: in de teksten van SQLite vindt hij elke soort hint die er staat."""
    seen = {name for texts in GOLDEN.values() for t in texts for name in _sqlite_only(t)}
    assert {"rowid", "json_each", "group_concat", "CROSS JOIN", "IN gone", "unaire +"} <= seen
    assert _sqlite_only("SELECT a + b, (x+1) FROM t WHERE c = d + 2") == []
    assert _sqlite_only("WHERE x = 1 AND +t.owner_id = ?") == ["unaire +"]
    assert _sqlite_only("ORDER BY +blob_hash.blob_sha") == ["unaire +"]
    assert _sqlite_only("AND (+ face.owner_id) = ?") == ["unaire +"]


def test_op_postgresql_compileert_alles_zonder_sqlite():
    found = _all(PG)
    found.update({k: [_text_of(s, PG) for s in v] for k, v in _upserts(PG).items()})
    assert set(GOLDEN) <= set(found)
    for name, texts in found.items():
        assert texts, name
        for sql in texts:
            assert _sqlite_only(sql) == [], (name, _sqlite_only(sql), sql)
    # Een paar vormen die er op PostgreSQL wel horen te staan
    joined = "\n".join(t for texts in found.values() for t in texts)
    for wanted in ("face.seq", "f.seq", "NULLS LAST", "string_agg", "array_agg", "unnest"):
        assert wanted in joined, wanted
    assert "ON CONFLICT" in "\n".join(found["memories_hide"] + found["photo_hash_write"])


def test_de_vormen_van_dbdialect():
    col = Face.__table__.c.owner_id
    assert _text_of(dbdialect.NoIndex(col), SQLITE) == "+ face.owner_id"
    assert _text_of(dbdialect.NoIndex(col, tight=True), SQLITE) == "+face.owner_id"
    assert _text_of(dbdialect.NoIndex(col), PG) == "face.owner_id"
    # tight en alias horen bij de sleutel van de cache: anders hergebruikt SQLAlchemy de tekst
    # van de ene vorm voor de andere
    assert dbdialect.NoIndex(col)._generate_cache_key() != (
        dbdialect.NoIndex(col, tight=True)._generate_cache_key()
    )
    assert dbdialect.FaceSeq("f")._generate_cache_key() != dbdialect.FaceSeq()._generate_cache_key()
    assert _text_of(dbdialect.FaceSeq("f"), SQLITE) == "f.rowid"
    assert _text_of(dbdialect.FaceSeq("f"), PG) == "f.seq"
    assert dbdialect.face_seq_sql("sqlite") == "face.rowid"
    assert dbdialect.face_seq_sql("postgresql", "f") == "f.seq"
    assert type(dbdialect.insert_for("postgresql", Face)).__module__.startswith(
        "sqlalchemy.dialects.postgresql"
    )
    assert dbdialect.module_for("postgresql").__name__.endswith("postgresql")
    assert dbdialect.module_for("sqlite").__name__.endswith("sqlite")


def test_serie_en_bereiken_op_sqlite_zoals_ervoor():
    """De vormen die niet in de vaste teksten staan: min(node_id) en ranges_sql."""
    by_show = select(MediaItem.show_id, _first_node("sqlite")).group_by(MediaItem.show_id)
    old = select(MediaItem.show_id, func.min(MediaItem.node_id)).group_by(MediaItem.show_id)
    assert _text_of(by_show, SQLITE) == _text_of(old, SQLITE)
    assert access.ranges_sql([]) == ("0", {})
    few = [("/1/", "/10"), ("/2/", "/20")]
    assert access.ranges_sql(few) == (
        "((n.mpath >= :rp0 AND n.mpath < :re0) OR (n.mpath >= :rp1 AND n.mpath < :re1))",
        {"rp0": "/1/", "re0": "/10", "rp1": "/2/", "re1": "/20"},
    )
    many = [(f"/{i}/", f"/{i}0") for i in range(access.MAX_OR + 1)]
    sql, params = access.ranges_sql(many, alias="d")
    assert sql == (
        "EXISTS (SELECT 1 FROM json_each(:ranges) AS r "
        "WHERE d.mpath >= r.value ->> '$[0]' AND d.mpath < r.value ->> '$[1]')"
    )
    assert json.loads(params["ranges"]) == [list(r) for r in many]
    assert access.ranges_sql(many, "d", "sqlite") == (sql, params)
    # PostgreSQL
    assert access.ranges_sql([], dialect="postgresql") == ("FALSE", {})
    assert access.ranges_sql(few, dialect="postgresql") == access.ranges_sql(few)
    pg_sql, pg_params = access.ranges_sql(many, "d", "postgresql")
    assert "jsonb_array_elements(CAST(:ranges AS jsonb))" in pg_sql
    assert _sqlite_only(pg_sql) == [] and pg_params == params


# --- Op de database van de test -------------------------------------------------------------


def _pg(db: Any) -> bool:
    return dbdialect.is_pg(db)


async def _face_seq(db: Any) -> None:
    """face.seq op PostgreSQL, zoals migratie 0013 van P2 hem maakt (niet in het model)."""
    if _pg(db):
        await db.execute(
            text(
                "ALTER TABLE face ADD COLUMN IF NOT EXISTS"
                " seq bigint GENERATED BY DEFAULT AS IDENTITY"
            )
        )


async def _paths(db: Any, rows: list[tuple[uuid.UUID, str, int]]) -> None:
    """mpath en dead op PostgreSQL: op SQLite houden de triggers van 0010 ze bij, op PostgreSQL
    straks die van P2; zolang die er niet zijn, zetten we ze hier zelf."""
    if _pg(db):
        for node_id, mpath, dead in rows:
            await db.execute(update(Node).where(Node.id == node_id).values(mpath=mpath, dead=dead))


async def _image(db: Any, user: Any, name: str, **kw: Any) -> Node:
    """Een foto (ftype image: een berekende kolom uit mime, op beide databases)."""
    return await create_file(db, user, name=name, data=name.encode(), mime="image/jpeg", **kw)


@pytest.mark.anyio
async def test_elke_upsert_voegt_in_en_werkt_bij(settings):
    async with maker(settings)() as db:
        user = await create_user(db, "eva@example.nl")
        foto = await _image(db, user, "a.jpg")
        b1 = await create_blob(db, b"een", mime="image/jpeg")
        b2 = await create_blob(db, b"twee", mime="image/jpeg")
        b3 = await create_blob(db, b"drie", mime="image/png")
        b4 = await create_blob(db, b"vier", mime="image/png")
        await db.commit()
        sha = foto.blob_id
        a, b = sorted([b1.sha256, b2.sha256])

        # blob_meta (photo_rows.set_meta): erin, dan de nieuwe waarden
        await photo_rows.set_meta(db, [{"blob_sha": sha, "camera": "c1", "lens": None, "gps": 0}])
        await photo_rows.set_meta(db, [{"blob_sha": sha, "camera": "c2", "lens": "l", "gps": 1}])
        meta = (await db.execute(select(BlobMeta).where(BlobMeta.blob_sha == sha))).scalar_one()
        assert (meta.camera, meta.lens, meta.gps) == ("c2", "l", 1)

        # photo_geo (photo_rows.write): zonder replace blijft wat er is, met replace niet
        place = Place(id=42, name="Utrecht", admin1="Utrecht", cc="NL", km=1.0)
        here, there = (
            photo_rows.Spot(52.0, 5.0, None, "exif"),
            photo_rows.Spot(51.0, 4.0, 3.0, "exif"),
        )
        await photo_rows.write(db, [(sha, here, place)], replace=False)
        await photo_rows.write(db, [(sha, there, place)], replace=False)
        geo = (await db.execute(select(PhotoGeo.lat, PhotoGeo.alt, PhotoGeo.place_id))).all()
        assert [tuple(r) for r in geo] == [(52.0, None, 42)]
        await photo_rows.write(db, [(sha, there, place)], replace=True)
        geo = (await db.execute(select(PhotoGeo.lat, PhotoGeo.alt))).all()
        assert [tuple(r) for r in geo] == [(51.0, 3.0)]

        # blob_hash en dup_pair (photo_hash.write), daarna mark_none
        await photo_hash.write(
            db,
            [photo_hash.Hashed(a, "ok", 0x0123456789ABCDEF), photo_hash.Hashed(b, "flat", None)],
            [{"p_a": a, "p_b": b, "p_dist": 3, "p_mad": 1.5, "p_level": "near"}],
        )
        await photo_hash.write(
            db,
            [photo_hash.Hashed(b, "ok", 0x0123456789ABCDEE)],
            [{"p_a": a, "p_b": b, "p_dist": 5, "p_mad": 2.5, "p_level": "same"}],
        )
        await photo_hash.write(
            db, [], [{"p_a": a, "p_b": b, "p_dist": 7, "p_mad": 3.5, "p_level": "near"}]
        )
        hashes = dict((await db.execute(select(BlobHash.blob_sha, BlobHash.state))).all())
        assert hashes == {a: "ok", b: "ok"}
        pair = (await db.execute(select(DupPair.dist, DupPair.mad, DupPair.level))).all()
        assert [tuple(r) for r in pair] == [(7, 3.5, "near")]
        db.add(BlobHash(blob_sha=b4.sha256, state="ok", phash=1, version=photo_hash.VERSION - 1))
        await db.flush()
        await photo_hash.mark_none(db, [a, b3.sha256, b4.sha256])
        rows = (
            await db.execute(
                select(BlobHash.blob_sha, BlobHash.state, BlobHash.phash, BlobHash.version)
            )
        ).all()
        found = {r.blob_sha: (r.state, r.phash, r.version) for r in rows}
        assert found[a][0] == "ok"  # al een hash van deze VERSION: blijft
        assert found[b3.sha256] == ("none", None, photo_hash.VERSION)  # nieuw
        assert found[b4.sha256] == ("none", None, photo_hash.VERSION)  # oud: bijgewerkt

        # memory_hide: twee keer dezelfde is één regel
        await memories.hide(db, user, "day", "2026-01-02")
        await memories.hide(db, user, "day", "2026-01-02")
        assert await db.scalar(select(func.count()).select_from(MemoryHide)) == 1

        # face_owner (face_rows.write_group): eerst erin, de tweede keer niet nog eens
        await face_rows.write_group(db, user.id, [], full=False, first=True, at=AT)
        later = AT + timedelta(hours=1)
        await face_rows.write_group(db, user.id, [], full=False, first=True, at=later)
        owners = (await db.execute(select(FaceOwner.owner_id, FaceOwner.suggested_at))).all()
        assert [(o, at.astimezone(UTC)) for o, at in owners] == [(user.id, later)]

        # node_origin (origin.placed): de tweede vervangt de eerste
        await origin.placed(db, foto.id, user.id)
        await origin.placed(db, foto.id, None)
        row = (await db.execute(select(NodeOrigin.created_by, NodeOrigin.via_link))).one()
        assert tuple(row) == (None, 1)

        # face_scan (scan.rescan): erin, en na done weer wait
        assert await face_scan.rescan(db, user.id, foto.id)
        await db.execute(update(FaceScan).values(state="done", tries=2))
        assert await face_scan.rescan(db, user.id, foto.id)
        scans = (await db.execute(select(FaceScan.state, FaceScan.tries))).all()
        assert [tuple(r) for r in scans] == [("wait", 0)]

        # album_skip (albums_rule.skip): twee keer dezelfde is één regel
        album = await create_album(db, user, items=[foto])
        db.add(AlbumRule(album_id=album.id, rule="{}"))
        await db.flush()
        await album_skip(db, album.id, [foto.id])
        await album_skip(db, album.id, [foto.id])
        assert await db.scalar(select(func.count()).select_from(AlbumSkip)) == 1
        await db.commit()


@pytest.mark.anyio
async def test_de_kaart_kiest_de_nieuwste_foto(settings):
    """De cover van een cel is de foto met de nieuwste taken_at; een foto zonder datum telt mee
    maar wordt geen cover (SQLite: de kale kolom bij max(); PostgreSQL: array_agg op volgorde)."""
    t0 = datetime(2024, 5, 1, 12, tzinfo=UTC)
    async with maker(settings)() as db:
        user = await create_user(db, "eva@example.nl")
        nodes = []
        for i, at in enumerate([t0, t0 + timedelta(days=30), t0 + timedelta(days=2), None]):
            node = await _image(db, user, f"{i}.jpg", taken_at=at)
            nodes.append(node)
            db.add(
                PhotoGeo(
                    node_id=node.id,
                    owner_id=user.id,
                    lat=52.0,
                    lon=5.0,
                    x16=100 + i,
                    y16=200 + i,
                    source="exif",
                    taken_at=at,
                )
            )
        await db.flush()
        found = await photos_map._cells(db, user, 4, [(0, 1000)], 0, 1000, None)
        assert list(found) == [(100 >> 4, 200 >> 4)]
        n, newest, cover, sx, sy = found[(6, 12)]
        assert n == 4
        assert uuid.UUID(str(cover)) == nodes[1].id
        assert (sx, sy) == pytest.approx((406.0, 806.0))
        assert isinstance(sx, float)
        await db.rollback()


@pytest.mark.anyio
async def test_de_tellers_van_admin_jobs(client, admin, settings):
    headers, _ = admin
    rows = [
        ("image/jpeg", "ready", {"partial": True}),
        ("image/jpeg", "ready", {"partial": True, "large_failed": True}),
        ("image/png", "ready", None),
        ("image/jpeg", None, None),
        ("video/mp4", "none", {"later": True}),
        ("image/jpeg", "failed", None),
        ("text/plain", "ready", None),
    ]
    async with maker(settings)() as db:
        for i, (mime, state, meta) in enumerate(rows):
            blob = await create_blob(db, f"blob {i}".encode(), mime=mime)
            blob.preview, blob.preview_meta = state, meta
        await db.commit()
        want = {"total": 6, "ready": 1, "partial": 1, "large_failed": 1, "waiting": 1,
                "failed": 1, "none": 0, "later": 1}  # fmt: skip
        assert await previews.photo_counts(db) == want
    r = await client.get("/api/v1/admin/jobs", headers=headers)
    assert r.status_code == 200, r.text
    assert {k: r.json()["photos"][k] for k in want} == want


@pytest.mark.anyio
async def test_een_serie_begint_bij_de_kleinste_node(settings):
    """min(node_id) per serie: op SQLite over de hex, op PostgreSQL over de tekst (COLLATE "C")
    en terug als uuid; dezelfde volgorde als uuid.UUID zelf."""
    ids = [
        uuid.UUID("f0000000-0000-4000-8000-000000000001"),
        uuid.UUID("0a000000-0000-4000-8000-000000000002"),
        uuid.UUID("9b000000-0000-4000-8000-000000000003"),
        uuid.UUID("00000000-0000-4000-8000-000000000004"),  # niet meer aanwezig
        uuid.UUID("0a000000-0000-4000-8000-000000000001"),  # een andere serie
    ]
    async with maker(settings)() as db:
        user = await create_user(db, "eva@example.nl")
        lib = MediaLibrary(owner_id=user.id, root_id=user.root_id, kind="shows", name="Series")
        db.add(lib)
        await db.flush()
        shows = [MediaShow(library_id=lib.id, title=t, sort_title=t) for t in ("A", "B")]
        db.add_all(shows)
        await db.flush()
        for i, node_id in enumerate(ids):
            db.add(Node(id=node_id, parent_id=user.root_id, owner_id=user.id, name=f"{i}.mkv",
                        kind="file"))  # fmt: skip
            await db.flush()
            db.add(
                MediaItem(
                    node_id=node_id,
                    library_id=lib.id,
                    kind="episode",
                    title=str(i),
                    sort_title=str(i),
                    show_id=shows[1 if i == 4 else 0].id,
                    present=i != 3,
                )
            )
        await db.flush()
        rows = await db.execute(
            select(MediaItem.show_id, _first_node(dbdialect.name_of(db)))
            .where(MediaItem.present.is_(True))
            .group_by(MediaItem.show_id)
        )
        first = {show: uuid.UUID(str(node)) for show, node in rows}
        assert first == {shows[0].id: min(ids[:3]), shows[1].id: ids[4]}
        await db.rollback()


@pytest.mark.anyio
@pytest.mark.parametrize("roots", [0, 1, 70])
async def test_de_bereiken_van_de_toegang(settings, roots):
    """ranges_sql met 0, 1 en 70 wortels (boven MAX_OR: één parameter met JSON) geeft dezelfde
    paden als de vergelijking in Python (op bytes, zoals COLLATE "C" van mpath)."""
    paths = [f"/1/{i}/" for i in range(1, 90)] + [f"/1/{i}/7/" for i in range(1, 90)]
    paths += ["/1/", "/2/", "/1/10/", "/1/100/"]
    ranges = [(f"/1/{i}/", f"/1/{i}0") for i in range(3, 3 + roots)]
    want = sorted(p for p in paths if any(s <= p < e for s, e in ranges))
    async with maker(settings)() as db:
        dialect = dbdialect.name_of(db)
        coll = ' COLLATE "C"' if dialect == dbdialect.POSTGRESQL else ""
        values = ", ".join(f"('{p}'{coll})" for p in paths)
        cond, params = access.ranges_sql(ranges, alias="n", dialect=dialect)
        found = await db.scalars(
            text(f"WITH n(mpath) AS (VALUES {values}) SELECT n.mpath FROM n WHERE {cond}"), params
        )
        assert sorted(found) == want
    if roots:
        assert want  # de test vergelijkt iets


@pytest.mark.anyio
async def test_een_venster_met_backslashes_en_tekens_van_meer_bytes(settings):
    piece = "C:\\map\\bestand \\x00 \\\\ é € 😀 ő \n"
    body = piece * (snippets.WINDOW // len(piece) + 50)
    async with maker(settings)() as db:
        user = await create_user(db, "eva@example.nl")
        node = await create_file(db, user, name="tekst.txt", data=b"tekst")
        db.add(BlobText(blob_sha=node.blob_id, body=body, ocr="\\ ocr ÿ", meta=""))
        await db.commit()
        [src] = await snippets.sources(db, user, [node.id])
        assert src.body == body[: snippets.WINDOW]
        assert src.ocr == "\\ ocr ÿ"
        # Leeg: None op SQLite (substr van een lege blob), "" op PostgreSQL; _one() slaat beide
        # over
        assert src.meta in (None, "")


@pytest.mark.anyio
async def test_een_stand_buiten_bereik_is_een_fout_in_de_waarde(settings):
    """progress laat één stand weg als zijn waarde niet past (_bad_value), en geeft elke andere
    fout door: zo op beide databases."""
    async with maker(settings)() as db:
        with pytest.raises(Exception) as caught:  # noqa: B017 - elke database zijn eigen soort
            await db.execute(select(bindparam("v", 2**70, type_=Integer)))
        assert progress._bad_value(caught.value)
        await db.rollback()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(text("SELECT x FROM bestaat_niet"))
        assert not progress._bad_value(caught.value)


@requires_pg
@pytest.mark.anyio
async def test_buiten_bereik_in_postgresql_zelf(settings):
    """De server zegt 22003 (integer out of range): dat is ook een fout in de waarde."""
    async with maker(settings)() as db:
        with pytest.raises(DBAPIError) as caught:
            await db.execute(select(cast(bindparam("v", 2**40, type_=BigInteger), Integer)))
        assert getattr(caught.value.orig, "sqlstate", "") == "22003"
        assert progress._bad_value(caught.value)


@pytest.mark.anyio
async def test_de_gezichten_lezen(settings):
    """read.counts, _firsts en _gone_by: een foto in de prullenbak (via zijn map) valt weg; de
    volgorde van invoegen is rowid (SQLite) of seq (PostgreSQL); een score zonder waarde komt
    achteraan; wiping komt terug als waar."""
    async with maker(settings)() as db:
        await _face_seq(db)
        user = await create_user(db, "eva@example.nl")
        folder = await create_folder(db, user, name="Weg")
        live = await _image(db, user, "live.jpg")
        gone = await _image(db, user, "weg.jpg", parent_id=folder.id)
        person = Person(owner_id=user.id, name="Bram", search_name="bram")
        db.add(person)
        await db.flush()
        faces = []
        for node, score in ((live, 0.9), (live, None), (live, 0.3), (gone, 0.5)):
            face = Face(
                node_id=node.id,
                owner_id=user.id,
                x=0.1,
                y=0.1,
                w=0.2,
                h=0.2,
                score=0.99,
                px=80,
                version=1,
                cluster=7,
                suggest_id=person.id,
                suggest_score=score,
            )
            db.add(face)
            await db.flush()  # één voor één: de volgorde van invoegen telt
            faces.append(face)
        db.add(FacePerson(face_id=faces[0].id, person_id=person.id, owner_id=user.id, verdict="ja"))
        for node in (live, gone):
            db.add(FaceScan(node_id=node.id, owner_id=user.id, version=1, state="done", prio="p"))
        db.add(FaceOwner(owner_id=user.id, wiping=1))
        await db.flush()
        folder.deleted_at = datetime.now(UTC)
        await db.flush()
        await _paths(
            db,
            [(user.root_id, "/1/", 0), (folder.id, "/1/2/", 1), (live.id, "/1/3/", 0),
             (gone.id, "/1/2/4/", 1)],
        )  # fmt: skip
        await db.commit()

        photos, counted, wiping = await read.counts(db, user.id)
        assert (photos.total, photos.done, photos.waiting) == (1, 1, 0)
        assert (counted.found, counted.named, counted.open, counted.groups) == (3, 1, 2, 1)
        assert wiping is True

        newest_first = [r.id for r in await read._firsts(db, user.id, "cluster", [7])]
        assert newest_first == [faces[2].id, faces[1].id, faces[0].id]
        best_first = [r.id for r in await read._firsts(db, user.id, "suggest_id", [person.id])]
        assert best_first == [faces[0].id, faces[2].id, faces[1].id]

        assert await read._gone_by(db, user.id, "cluster") == {7: 1}
        assert await read._gone_by(db, user.id, "suggest_id") == {person.id: 1}


def test_db_backup_op_postgresql_verwijst_naar_pg_dump(tmp_path):
    """Op PostgreSQL maakt pg_dump de kopie: het commando zegt dat, met exitcode 2, zonder de
    database te openen (de poort hieronder luistert niet)."""
    env = {
        **os.environ,
        "PLATFORM_DATA_DIR": str(tmp_path),
        "PLATFORM_DATABASE_URL": "postgresql+asyncpg://platform@127.0.0.1:9/platform",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "db-backup"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 2, result.stderr.decode()
    assert "gebruik pg_dump (cluster: platform-ctl.sh db-backup)" in result.stderr.decode()
    assert not (tmp_path / "backup").exists()
