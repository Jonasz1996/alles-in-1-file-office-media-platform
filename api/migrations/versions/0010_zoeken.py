"""zoeken en foto's: de zoekindex, de foto-gegevens en slimme albums

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-03 12:00:00.000000

Bouwontwerp fase 6, §3.1. Op node vier kolommen die triggers bijhouden (num, mpath, dead, en
ftype als virtuele kolom) met zes indexen; op album_item source en shared; zeventien nieuwe
tabellen; de view search_src met de FTS5-tabellen fts_node en fts_name; en de triggers die de
index in dezelfde transactie gelijk houden. Alles met ALTER TABLE en ruwe SQL: geen kopie van
node (een batch maakt de tabel opnieuw, en met foreign keys aan neemt DROP TABLE dan via
CASCADE alles mee wat ernaar verwijst).

De SQL hieronder is een bevroren kopie van app/search/schema.py; tests/search/
test_schema_triggers.py eist dat ze gelijk zijn. Zonder FTS5 (een SQLite zonder die module, of
de vlag search_fts5 = False in de config van Alembic, voor de test) geen view, geen
FTS-tabellen en alleen de triggers voor nummer, pad, dead en de foto-rijen: de app zoekt dan
met LikeFallback.

Op PostgreSQL alleen de gewone tabellen en kolommen (Keuze 10; niet getest).

De downgrade haalt alles weer weg, ook zonder kopie van node (ALTER TABLE DROP COLUMN, SQLite ≥
3.35); daarna is de database gelijk aan 0009 en start v0.7.1 weer: app.cli db-downgrade 0009.
Bestanden, delingen en albums blijven; de index, de plekken, de hashes, de personen en de
regels van albums gaan weg. Een regel-item dat niet gedeeld was (shared = 0), is daarna weer
gedeeld zoals in 0.7.x; db-downgrade vraagt daarom eerst of die items uit het album moeten.
"""

import sqlalchemy as sa
from alembic import context, op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# --- Bevroren kopie van app/search/schema.py (revisie 0010) -------------------------------

FTYPE_SQL = (
    "CASE WHEN kind = 'dir' THEN 'folder'"
    " WHEN kind <> 'file' OR mime IS NULL OR mime = '' THEN 'other'"
    " WHEN mime IN ('application/vnd.ms-excel', 'application/vnd.ms-excel.sheet.macroenabled.12',"
    " 'application/vnd.oasis.opendocument.spreadsheet',"
    " 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'text/csv',"
    " 'text/tab-separated-values') THEN 'spreadsheet'"
    " WHEN mime IN ('application/vnd.ms-powerpoint',"
    " 'application/vnd.ms-powerpoint.presentation.macroenabled.12',"
    " 'application/vnd.oasis.opendocument.presentation',"
    " 'application/vnd.openxmlformats-officedocument.presentationml.presentation')"
    " THEN 'presentation'"
    " WHEN mime IN ('application/gzip', 'application/java-archive', 'application/vnd.rar',"
    " 'application/x-7z-compressed', 'application/x-bzip2', 'application/x-tar',"
    " 'application/x-xz', 'application/zip', 'application/zstd') THEN 'other'"
    " WHEN mime = 'application/pdf' THEN 'pdf'"
    " WHEN mime LIKE 'image/%' THEN 'image'"
    " WHEN mime LIKE 'video/%' THEN 'video'"
    " WHEN mime LIKE 'audio/%' THEN 'audio'"
    " WHEN mime LIKE 'text/%' OR mime IN ('application/epub+zip', 'application/javascript',"
    " 'application/json', 'application/msword', 'application/rtf', 'application/sql',"
    " 'application/toml', 'application/vnd.ms-outlook',"
    " 'application/vnd.ms-word.document.macroenabled.12',"
    " 'application/vnd.oasis.opendocument.graphics', 'application/vnd.oasis.opendocument.text',"
    " 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',"
    " 'application/vnd.openxmlformats-officedocument.wordprocessingml.template',"
    " 'application/x-sh', 'application/xhtml+xml', 'application/xml', 'application/yaml')"
    " THEN 'document'"
    " ELSE 'other' END"
)

NODE_COLUMNS = (
    "ALTER TABLE node ADD COLUMN num INTEGER",
    "ALTER TABLE node ADD COLUMN mpath TEXT",
    "ALTER TABLE node ADD COLUMN dead INTEGER NOT NULL DEFAULT 0",
    f"ALTER TABLE node ADD COLUMN ftype TEXT GENERATED ALWAYS AS ({FTYPE_SQL}) VIRTUAL",
)
NODE_INDEXES = {
    "ix_node_num": "CREATE UNIQUE INDEX ix_node_num ON node (num)",
    "ix_node_mpath": "CREATE INDEX ix_node_mpath ON node (mpath)",
    "ix_node_trash": "CREATE INDEX ix_node_trash ON node (mpath) WHERE deleted_at IS NOT NULL",
    "ix_node_live_mod": (
        "CREATE INDEX ix_node_live_mod ON node (modified_at DESC, num DESC) WHERE dead = 0"
    ),
    "ix_node_ftype_mod": (
        "CREATE INDEX ix_node_ftype_mod ON node (ftype, modified_at DESC, num DESC) WHERE dead = 0"
    ),
    "ix_node_owner_blob": (
        "CREATE INDEX ix_node_owner_blob ON node (owner_id, blob_id) WHERE dead = 0"
    ),
}
ALBUM_ITEM_COLUMNS = (
    "ALTER TABLE album_item ADD COLUMN source TEXT NOT NULL DEFAULT 'hand'",
    "ALTER TABLE album_item ADD COLUMN shared INTEGER NOT NULL DEFAULT 1",
)
SEQ_TABLES = {
    "search_seq": (
        "CREATE TABLE search_seq (id INTEGER PRIMARY KEY CHECK (id = 1), next INTEGER NOT NULL)"
    ),
    "search_state": (
        "CREATE TABLE search_state (id INTEGER PRIMARY KEY CHECK (id = 1), cursor INTEGER, "
        "started_at TEXT, what TEXT)"
    ),
}


def ss(x: str) -> str:
    return f"replace(replace({x}, 'ß', 'ss'), 'ẞ', 'ss')"


_BODY = ss("coalesce(t.body, '')")
_OCR = ss("coalesce(t.ocr, '')")
_POCR = ss("coalesce(po.text, '')")
_META = ss("coalesce(t.meta, '')")
_EXTRA = ss(
    "coalesce((SELECT group_concat(m.title, ' ') FROM media_item m WHERE m.node_id = n.id), '')"
)
VIEW_SQL = (
    "CREATE VIEW search_src AS\n"
    "  SELECT n.num AS num, n.blob_id AS blob_id, n.id AS node_id, n.search_name AS name,\n"
    f"         {_EXTRA} AS extra,\n"
    f"         {_BODY} AS body,\n"
    f"         {_OCR} AS ocr,\n"
    f"         {_META} AS meta,\n"
    f"         {_POCR} AS pocr\n"
    "  FROM node n\n"
    "  LEFT JOIN blob_text t ON t.blob_sha = n.blob_id\n"
    "  LEFT JOIN photo_ocr po ON po.node_id = n.id"
)
FTS_TABLES = {
    "fts_node": (
        "CREATE VIRTUAL TABLE fts_node USING fts5(name, extra, body, ocr, meta, pocr, "
        "content='search_src', content_rowid='num', tokenize='unicode61 remove_diacritics 2')"
    ),
    "fts_name": (
        "CREATE VIRTUAL TABLE fts_name USING fts5(search_name, "
        "content='node', content_rowid='num', tokenize='trigram')"
    ),
}

_CURSOR = "(SELECT cursor FROM search_state WHERE id = 1)"
_COLS = "name, extra, body, ocr, meta, pocr"


def in_(n: str) -> str:
    return f"({_CURSOR} IS NULL OR {n} <= {_CURSOR})"


def del_(w: str) -> str:
    return (
        f"INSERT INTO fts_node(fts_node, rowid, {_COLS}) "
        f"SELECT 'delete', num, {_COLS} FROM search_src WHERE ({w}) AND {in_('num')};"
    )


def ins(w: str) -> str:
    return (
        f"INSERT INTO fts_node(rowid, {_COLS}) "
        f"SELECT num, {_COLS} FROM search_src WHERE ({w}) AND {in_('num')};"
    )


def ndel(n: str, s: str) -> str:
    return (
        f"INSERT INTO fts_name(fts_name, rowid, search_name) SELECT 'delete', {n}, {s} "
        f"WHERE {in_(n)};"
    )


def nins(n: str, s: str) -> str:
    return f"INSERT INTO fts_name(rowid, search_name) SELECT {n}, {s} WHERE {in_(n)};"


def end(p: str) -> str:
    return f"substr({p}, 1, length({p}) - 1) || '0'"


def dead_subtree() -> str:
    cur = "(SELECT mpath FROM node WHERE id = NEW.id)"
    return (
        "UPDATE node SET dead =\n"
        "      coalesce((SELECT p.dead FROM node p WHERE p.id = NEW.parent_id), 0)\n"
        f"   WHERE mpath >= {cur} AND mpath < {end(cur)};\n"
        "UPDATE node SET dead = 1\n"
        "  FROM (SELECT mpath AS tp FROM node WHERE deleted_at IS NOT NULL\n"
        f"          AND mpath >= {cur} AND mpath < {end(cur)}) AS t\n"
        f"   WHERE node.mpath >= t.tp AND node.mpath < {end('t.tp')};\n"
        "UPDATE photo_geo SET dead = (SELECT n.dead FROM node n WHERE n.id = photo_geo.node_id)\n"
        f"   WHERE node_id IN (SELECT id FROM node WHERE mpath >= {cur} AND mpath < {end(cur)})\n"
        "     AND dead IS NOT (SELECT n.dead FROM node n WHERE n.id = photo_geo.node_id);"
    )


_NEW_NUM = "(SELECT num FROM node WHERE id = NEW.id)"
_TEXT_CHANGED = "(OLD.search_name IS NOT NEW.search_name OR OLD.blob_id IS NOT NEW.blob_id)"


def _trigger(head: str, *body: str) -> str:
    lines = [f"CREATE TRIGGER {head} BEGIN"]
    lines += ["  " + line.replace("\n", "\n  ") for line in body if line]
    lines.append("END")
    return "\n".join(lines)


def triggers(fts: bool) -> dict[str, str]:
    def f(*sql: str) -> tuple[str, ...]:
        return sql if fts else ()

    t: dict[str, str] = {}
    t["node_ai"] = _trigger(
        "node_ai AFTER INSERT ON node",
        "UPDATE search_seq SET next = next + 1 WHERE id = 1;",
        "UPDATE node SET num = (SELECT next - 1 FROM search_seq WHERE id = 1) WHERE id = NEW.id;",
        "UPDATE node SET\n"
        "  mpath = coalesce((SELECT p.mpath FROM node p WHERE p.id = NEW.parent_id), '/')"
        " || num || '/',\n"
        "  dead = coalesce((SELECT p.dead FROM node p WHERE p.id = NEW.parent_id), 0)"
        " OR NEW.deleted_at IS NOT NULL\n"
        "WHERE id = NEW.id;",
        *f(ins(f"num = {_NEW_NUM}"), nins(_NEW_NUM, "NEW.search_name")),
    )
    t["node_bu_text"] = _trigger(
        "node_bu_text BEFORE UPDATE OF search_name, blob_id ON node\n"
        f"WHEN OLD.num IS NOT NULL AND {_TEXT_CHANGED}",
        "DELETE FROM photo_ocr WHERE node_id = OLD.id AND OLD.blob_id IS NOT NEW.blob_id;",
        "DELETE FROM photo_geo WHERE node_id = OLD.id AND OLD.blob_id IS NOT NEW.blob_id;",
        *f(del_("num = OLD.num"), ndel("OLD.num", "OLD.search_name")),
    )
    if fts:
        t["node_au_text"] = _trigger(
            "node_au_text AFTER UPDATE OF search_name, blob_id ON node\n"
            f"WHEN NEW.num IS NOT NULL AND {_TEXT_CHANGED}",
            ins("num = NEW.num"),
            nins("NEW.num", "NEW.search_name"),
        )
    t["node_au_owner"] = _trigger(
        "node_au_owner AFTER UPDATE OF owner_id ON node\nWHEN OLD.owner_id IS NOT NEW.owner_id",
        "DELETE FROM photo_ocr WHERE node_id = NEW.id;",
        "DELETE FROM photo_geo WHERE node_id = NEW.id;",
        "DELETE FROM photo_person WHERE node_id = NEW.id;",
    )
    if fts:
        t["node_bd"] = _trigger(
            "node_bd BEFORE DELETE ON node WHEN OLD.num IS NOT NULL",
            del_("num = OLD.num"),
            ndel("OLD.num", "OLD.search_name"),
        )
    t["node_au_move"] = _trigger(
        "node_au_move AFTER UPDATE OF parent_id ON node\n"
        "WHEN OLD.parent_id IS NOT NEW.parent_id AND OLD.mpath IS NOT NULL",
        "UPDATE node SET mpath = coalesce((SELECT p.mpath FROM node p WHERE p.id = NEW.parent_id),"
        " '/') || NEW.num || '/'\n"
        "                        || substr(mpath, length(OLD.mpath) + 1)\n"
        f" WHERE mpath >= OLD.mpath AND mpath < {end('OLD.mpath')};",
        dead_subtree(),
    )
    t["node_au_trash"] = _trigger(
        "node_au_trash AFTER UPDATE OF deleted_at ON node\n"
        "WHEN (OLD.deleted_at IS NULL) <> (NEW.deleted_at IS NULL) AND OLD.mpath IS NOT NULL",
        dead_subtree(),
    )
    t["node_au_taken"] = _trigger(
        "node_au_taken AFTER UPDATE OF taken_at ON node\nWHEN OLD.taken_at IS NOT NEW.taken_at",
        "UPDATE photo_geo SET taken_at = NEW.taken_at WHERE node_id = NEW.id;",
    )
    t["geo_bi"] = _trigger(
        "geo_bi BEFORE INSERT ON photo_geo\n"
        "WHEN NOT EXISTS (SELECT 1 FROM node n WHERE n.id = NEW.node_id"
        " AND n.owner_id = NEW.owner_id AND n.kind = 'file')",
        "SELECT RAISE(ABORT, 'photo_geo: alleen bij een bestand, van de eigenaar van de node');",
    )
    t["geo_ai"] = _trigger(
        "geo_ai AFTER INSERT ON photo_geo",
        "UPDATE photo_geo SET (dead, taken_at) =\n"
        "  (SELECT n.dead, n.taken_at FROM node n WHERE n.id = NEW.node_id)\n"
        "WHERE node_id = NEW.node_id;",
    )
    t["geo_bu"] = _trigger(
        "geo_bu BEFORE UPDATE ON photo_geo\n"
        "WHEN NEW.node_id IS NOT OLD.node_id OR NEW.owner_id IS NOT OLD.owner_id\n"
        "  OR NEW.dead IS NOT (SELECT n.dead FROM node n WHERE n.id = NEW.node_id)\n"
        "  OR NEW.taken_at IS NOT (SELECT n.taken_at FROM node n WHERE n.id = NEW.node_id)",
        "SELECT RAISE(ABORT, 'photo_geo: node_id, owner_id, dead en taken_at volgen de node');",
    )
    if not fts:
        return t
    for table, short, key in (("blob_text", "bt", "blob_id"), ("photo_ocr", "po", "node_id")):
        col = "blob_sha" if table == "blob_text" else "node_id"
        for name, when, row, macro in (
            ("bi", "BEFORE INSERT", "NEW", del_),
            ("ai", "AFTER INSERT", "NEW", ins),
            ("bu", "BEFORE UPDATE", "OLD", del_),
            ("au", "AFTER UPDATE", "NEW", ins),
            ("bd", "BEFORE DELETE", "OLD", del_),
            ("ad", "AFTER DELETE", "OLD", ins),
        ):
            t[f"{short}_{name}"] = (
                f"CREATE TRIGGER {short}_{name} {when} ON {table} BEGIN "
                f"{macro(f'{key} = {row}.{col}')} END"
            )
    for name, when, row, macro in (
        ("bi", "BEFORE INSERT", "NEW", del_),
        ("ai", "AFTER INSERT", "NEW", ins),
        ("bu", "BEFORE UPDATE OF title", "OLD", del_),
        ("au", "AFTER UPDATE OF title", "NEW", ins),
        ("bd", "BEFORE DELETE", "OLD", del_),
        ("ad", "AFTER DELETE", "OLD", ins),
    ):
        t[f"mi_{name}"] = (
            f"CREATE TRIGGER mi_{name} {when} ON media_item BEGIN "
            f"{macro(f'num = (SELECT num FROM node WHERE id = {row}.node_id)')} END"
        )
    return t


TRIGGERS = triggers(fts=True)
TRIGGERS_NO_FTS = triggers(fts=False)

FILL = (
    "UPDATE node SET num = rowid",
    "INSERT INTO search_seq VALUES (1, coalesce((SELECT max(num) FROM node), 0) + 1)",
    "INSERT INTO search_state VALUES (1, NULL, NULL, NULL)",
    "WITH RECURSIVE t(id, mpath, dead) AS (\n"
    "  SELECT id, '/' || num || '/', deleted_at IS NOT NULL FROM node WHERE parent_id IS NULL\n"
    "  UNION ALL\n"
    "  SELECT n.id, t.mpath || n.num || '/', t.dead OR n.deleted_at IS NOT NULL\n"
    "  FROM node n JOIN t ON n.parent_id = t.id)\n"
    "UPDATE node SET mpath = t.mpath, dead = t.dead FROM t WHERE node.id = t.id",
)
FTS_FILL = (
    "INSERT INTO fts_node(fts_node) VALUES ('rebuild')",
    "INSERT INTO fts_name(fts_name) VALUES ('rebuild')",
)
NEW_TABLES = (
    "album_skip",
    "album_rule",
    "memory_hide",
    "photo_pref",
    "photo_ocr",
    "photo_person",
    "person",
    "dup_ignore",
    "dup_pair",
    "blob_hash",
    "photo_geo",
    "blob_meta",
    "analysis",
    "ocr_page",
    "blob_text",
    "search_state",
    "search_seq",
)

# --- De migratie -----------------------------------------------------------------------------


def _fts5(bind) -> bool:
    """FTS5 aanwezig? De vlag search_fts5 in de config van Alembic gaat voor (tests)."""
    flag = context.config.attributes.get("search_fts5")
    if flag is not None:
        return bool(flag)
    found = bind.exec_driver_sql("SELECT sqlite_compileoption_used('ENABLE_FTS5')").scalar()
    return bool(found)


def _blob(column: str = "blob_sha", primary_key: bool = True) -> sa.Column:
    return sa.Column(
        column,
        sa.String(length=64),
        sa.ForeignKey("blob.sha256", ondelete="CASCADE"),
        primary_key=primary_key,
        nullable=False,
    )


def _ref(column: str, target: str, ondelete: str = "CASCADE", **kw) -> sa.Column:
    kw.setdefault("nullable", False)
    return sa.Column(column, sa.Uuid(), sa.ForeignKey(f"{target}.id", ondelete=ondelete), **kw)


def _at(column: str, nullable: bool = False) -> sa.Column:
    return sa.Column(column, sa.DateTime(timezone=True), nullable=nullable)


def _tables() -> None:
    op.create_table(
        "blob_text",
        _blob(),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column("ocr", sa.Text(), server_default="", nullable=False),
        sa.Column("meta", sa.Text(), server_default="", nullable=False),
        sa.Column("truncated", sa.Integer(), server_default="0", nullable=False),
        _at("updated_at"),
    )
    op.create_table(
        "ocr_page",
        _blob(),
        sa.Column("page", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("angle", sa.Float(), server_default="0", nullable=False),
        sa.Column("words", sa.LargeBinary(), nullable=False),
    )
    op.create_table(
        "analysis",
        _blob(),
        sa.Column("step", sa.String(length=8), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=8), nullable=False),
        sa.Column("tries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("code", sa.String(length=32), nullable=True),
        _ref("owner_id", "user", "SET NULL", nullable=True),
        _at("queued_at"),
        _at("updated_at"),
    )
    op.create_index("ix_analysis_todo", "analysis", ["step", "state", "owner_id", "queued_at"])
    op.create_table(
        "blob_meta",
        _blob(),
        sa.Column("camera", sa.Text(), nullable=True),
        sa.Column("lens", sa.Text(), nullable=True),
        sa.Column("gps", sa.Integer(), nullable=True),
    )
    op.create_index("ix_blob_meta_camera", "blob_meta", ["camera"])
    op.create_table(
        "photo_geo",
        _ref("node_id", "node", primary_key=True),
        _ref("owner_id", "user"),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("alt", sa.Float(), nullable=True),
        sa.Column("x16", sa.Integer(), nullable=False),
        sa.Column("y16", sa.Integer(), nullable=False),
        sa.Column("place_id", sa.Integer(), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("admin1", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("dead", sa.Integer(), server_default="0", nullable=False),
        _at("taken_at", nullable=True),
    )
    op.create_index(
        "ix_photo_geo_tile", "photo_geo", ["owner_id", "dead", "x16", "y16", "taken_at", "node_id"]
    )
    op.create_index("ix_photo_geo_place", "photo_geo", ["owner_id", "dead", "place_id"])
    op.create_index("ix_photo_geo_country", "photo_geo", ["owner_id", "dead", "country"])
    op.create_table(
        "blob_hash",
        _blob(),
        sa.Column("state", sa.String(length=8), nullable=False),
        sa.Column("phash", sa.BigInteger(), nullable=True),
        sa.Column("h0", sa.Integer(), nullable=True),
        sa.Column("h1", sa.Integer(), nullable=True),
        sa.Column("h2", sa.Integer(), nullable=True),
        sa.Column("h3", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    for i in range(4):
        op.create_index(f"ix_hash_{i}", "blob_hash", [f"h{i}"])
    op.create_table(
        "dup_pair",
        _blob("a"),
        _blob("b"),
        sa.Column("dist", sa.Integer(), nullable=False),
        sa.Column("mad", sa.Float(), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.CheckConstraint("a < b"),
        sqlite_with_rowid=False,
    )
    op.create_index("ix_dup_b", "dup_pair", ["b"])
    op.create_table(
        "dup_ignore",
        _ref("user_id", "user", primary_key=True),
        sa.Column("key", sa.String(length=64), primary_key=True),
    )
    op.create_table(
        "person",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ref("owner_id", "user"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("search_name", sa.Text(), nullable=False),
        sa.Column("hidden", sa.Integer(), server_default="0", nullable=False),
        _ref("cover_node", "node", "SET NULL", nullable=True),
        _at("created_at"),
        _at("updated_at"),
    )
    op.create_index("ix_person_owner", "person", ["owner_id", "hidden", "search_name"])
    op.create_table(
        "photo_person",
        _ref("person_id", "person", primary_key=True),
        _ref("node_id", "node", primary_key=True),
        _ref("owner_id", "user"),
        sa.Column("x", sa.Float(), nullable=True),
        sa.Column("y", sa.Float(), nullable=True),
        sa.Column("w", sa.Float(), nullable=True),
        sa.Column("h", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=8), server_default="hand", nullable=False),
        _at("created_at"),
    )
    op.create_index("ix_pp_node", "photo_person", ["node_id"])
    op.create_index("ix_pp_owner", "photo_person", ["owner_id", "person_id"])
    op.create_table(
        "photo_ocr",
        _ref("node_id", "node", primary_key=True),
        _ref("owner_id", "user"),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("words", sa.LargeBinary(), nullable=False),
        sa.Column("angle", sa.Float(), server_default="0", nullable=False),
        _at("updated_at"),
    )
    op.create_table(
        "photo_pref",
        _ref("user_id", "user", primary_key=True),
        sa.Column("location", sa.String(length=8), server_default="mij", nullable=False),
        sa.Column("memories", sa.Integer(), server_default="1", nullable=False),
        sa.Column("ocr_photos", sa.Integer(), server_default="0", nullable=False),
        sa.Column("faces", sa.String(length=8), server_default="uit", nullable=False),
        _at("updated_at"),
    )
    op.create_table(
        "memory_hide",
        _ref("user_id", "user", primary_key=True),
        sa.Column("kind", sa.String(length=8), primary_key=True),
        sa.Column("ref", sa.String(length=255), primary_key=True),
    )
    op.create_table(
        "album_rule",
        _ref("album_id", "node", primary_key=True),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("share_new", sa.Integer(), server_default="0", nullable=False),
        _at("confirmed_at", nullable=True),
        _at("refreshed_at"),
        sa.Column("count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("code", sa.String(length=32), nullable=True),
    )
    op.create_table(
        "album_skip",
        _ref("album_id", "node", primary_key=True),
        _ref("node_id", "node", primary_key=True),
    )


def upgrade() -> None:
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        for sql in (*NODE_COLUMNS, *ALBUM_ITEM_COLUMNS, *SEQ_TABLES.values()):
            op.execute(sql)
    else:
        op.add_column("node", sa.Column("num", sa.Integer(), nullable=True))
        op.add_column("node", sa.Column("mpath", sa.Text(), nullable=True))
        op.add_column("node", sa.Column("dead", sa.Integer(), server_default="0", nullable=False))
        op.add_column("node", sa.Column("ftype", sa.Text(), sa.Computed(FTYPE_SQL, persisted=True)))
        op.add_column(
            "album_item", sa.Column("source", sa.Text(), server_default="hand", nullable=False)
        )
        op.add_column(
            "album_item", sa.Column("shared", sa.Integer(), server_default="1", nullable=False)
        )
        op.create_table(
            "search_seq",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("next", sa.Integer(), nullable=False),
            sa.CheckConstraint("id = 1"),
        )
        op.create_table(
            "search_state",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("cursor", sa.Integer(), nullable=True),
            sa.Column("started_at", sa.Text(), nullable=True),
            sa.Column("what", sa.Text(), nullable=True),
            sa.CheckConstraint("id = 1"),
        )
    _tables()
    for sql in NODE_INDEXES.values():
        op.execute(sql)
    if not sqlite:
        return

    # Vullen [gemeten op 104.696 nodes, SQLite 3.46.1: 2,8 s]; daarna pas de triggers
    for sql in FILL:
        op.execute(sql)
    fts = _fts5(bind)
    if fts:
        op.execute(VIEW_SQL)
        for sql in FTS_TABLES.values():
            op.execute(sql)
        # Alleen namen en titels: blob_text is nog leeg. Een rebuild in één statement mag
        # hier, want de app draait niet; daarna nooit meer (Keuze 12).
        for sql in FTS_FILL:
            op.execute(sql)
    for sql in (TRIGGERS if fts else TRIGGERS_NO_FTS).values():
        op.execute(sql)


def downgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        for name in reversed([*TRIGGERS, *(n for n in TRIGGERS_NO_FTS if n not in TRIGGERS)]):
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
        op.execute("DROP VIEW IF EXISTS search_src")
        for name in reversed(FTS_TABLES):
            op.execute(f"DROP TABLE IF EXISTS {name}")
    for table in NEW_TABLES:
        op.drop_table(table)
    for name in reversed(NODE_INDEXES):
        op.drop_index(name, table_name="node")
    # Gewone DROP COLUMN, geen batch (zie boven); ftype eerst
    for column in ("ftype", "dead", "mpath", "num"):
        op.execute(f"ALTER TABLE node DROP COLUMN {column}")
    for column in ("shared", "source"):
        op.execute(f"ALTER TABLE album_item DROP COLUMN {column}")
