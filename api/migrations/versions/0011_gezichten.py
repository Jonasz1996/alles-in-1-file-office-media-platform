"""gezichten: de tabellen van X2, en wie een bestand uploadde

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-11 12:00:00.000000

Bouwontwerp fase 6, §3.1a (Keuze 61, 64 en 69). Vijf nieuwe tabellen (face, face_person,
face_scan, face_owner en node_origin), zeven indexen en drie triggers. Geen bestaande tabel
verandert, en niets herschrijft node.

De SQL hieronder is een bevroren kopie van app/search/schema.py (TRIGGERS_0011, INDEXES_0011 en
TABLES_0011); tests/search/test_schema_triggers.py eist dat ze gelijk zijn. Geen van de triggers
raakt een FTS-tabel: met en zonder FTS5 zijn ze gelijk.

Op PostgreSQL alleen de tabellen en de indexen, geen triggers (Keuze 10). Met de hand
nagelopen op PostgreSQL 16 (heen, terug naar 0010 en 0009, weer heen); geen vaste test.

De downgrade wist eerst alle rijen met secure_delete aan (de gezichten zijn biometrische
gegevens), dan de triggers en de tabellen. app.migrate en app.cli doen na de transactie een
wal_checkpoint(TRUNCATE), zodat er niets van in .db of -wal achterblijft. Personen en wie er op
een foto staat (photo_person, van 0010) blijven; met node_origin weg telt elk bestand daarna
als door de eigenaar geüpload, zoals in 0.8.0 (Vraag 9).
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# --- Bevroren kopie van app/search/schema.py (revisie 0011) -------------------------------

INDEXES = {
    "ix_face_node": "CREATE INDEX ix_face_node ON face (node_id)",
    "ix_face_open": (
        "CREATE INDEX ix_face_open ON face (owner_id, suggest_id, suggest_score DESC) "
        "WHERE suggest_id IS NOT NULL AND hidden = 0"
    ),
    "ix_face_cluster": (
        "CREATE INDEX ix_face_cluster ON face (owner_id, cluster) "
        "WHERE cluster IS NOT NULL AND hidden = 0"
    ),
    "ix_face_new": (
        "CREATE INDEX ix_face_new ON face (owner_id, created_at DESC) WHERE hidden = 0"
    ),
    "ux_fp_ja": "CREATE UNIQUE INDEX ux_fp_ja ON face_person (face_id) WHERE verdict = 'ja'",
    "ix_fp_person": "CREATE INDEX ix_fp_person ON face_person (owner_id, person_id, verdict)",
    "ix_face_scan_todo": (
        "CREATE INDEX ix_face_scan_todo ON face_scan (state, owner_id, prio DESC)"
    ),
}

TABLES = ("face_person", "face", "face_scan", "face_owner", "node_origin")


def _trigger(head: str, *body: str) -> str:
    lines = [f"CREATE TRIGGER {head} BEGIN"]
    lines += ["  " + line.replace("\n", "\n  ") for line in body if line]
    lines.append("END")
    return "\n".join(lines)


def triggers() -> dict[str, str]:
    t: dict[str, str] = {}
    t["node_au_owner_face"] = _trigger(
        "node_au_owner_face AFTER UPDATE OF owner_id ON node\n"
        "WHEN OLD.owner_id IS NOT NEW.owner_id",
        "DELETE FROM face WHERE node_id = NEW.id;",
        "DELETE FROM face_scan WHERE node_id = NEW.id;",
    )
    t["node_au_blob_face"] = _trigger(
        "node_au_blob_face AFTER UPDATE OF blob_id ON node\nWHEN OLD.blob_id IS NOT NEW.blob_id",
        "DELETE FROM face WHERE node_id = NEW.id;",
        "DELETE FROM face_scan WHERE node_id = NEW.id;",
        "UPDATE photo_person SET x = NULL, y = NULL, w = NULL, h = NULL WHERE node_id = NEW.id;",
    )
    t["person_bd_face"] = _trigger(
        "person_bd_face BEFORE DELETE ON person",
        "UPDATE face SET vec = NULL, hidden = 1, suggest_id = NULL, suggest_score = NULL,\n"
        "                suggest_why = NULL, cluster = NULL\n"
        "WHERE id IN (SELECT face_id FROM face_person"
        " WHERE person_id = OLD.id AND verdict = 'ja');",
    )
    return t


TRIGGERS = triggers()

# --- De migratie -----------------------------------------------------------------------------


def _ref(column: str, target: str, ondelete: str = "CASCADE", **kw) -> sa.Column:
    kw.setdefault("nullable", False)
    return sa.Column(column, sa.Uuid(), sa.ForeignKey(f"{target}.id", ondelete=ondelete), **kw)


def _at(column: str, nullable: bool = False) -> sa.Column:
    return sa.Column(column, sa.DateTime(timezone=True), nullable=nullable)


def _int(column: str) -> sa.Column:
    return sa.Column(column, sa.Integer(), server_default="0", nullable=False)


def _tables() -> None:
    op.create_table(
        "face",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ref("node_id", "node"),
        _ref("owner_id", "user"),
        *(sa.Column(c, sa.Float(), nullable=False) for c in ("x", "y", "w", "h", "score")),
        sa.Column("px", sa.Integer(), nullable=False),
        sa.Column("vec", sa.LargeBinary(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("cluster", sa.Integer(), nullable=True),
        _ref("suggest_id", "person", "SET NULL", nullable=True),
        sa.Column("suggest_score", sa.Float(), nullable=True),
        sa.Column("suggest_why", sa.Text(), nullable=True),
        _int("hidden"),
        _at("created_at"),
    )
    op.create_table(
        "face_person",
        _ref("face_id", "face", primary_key=True),
        _ref("person_id", "person", primary_key=True),
        _ref("owner_id", "user"),
        sa.Column("verdict", sa.String(length=8), nullable=False),
        _at("created_at"),
    )
    op.create_table(
        "face_scan",
        _ref("node_id", "node", primary_key=True),
        _ref("owner_id", "user"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=8), nullable=False),
        _int("faces"),
        _int("tries"),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("prio", sa.Text(), nullable=False),
        _at("queued_at"),
        _at("updated_at"),
    )
    op.create_table(
        "face_owner",
        _ref("owner_id", "user", primary_key=True),
        _int("run"),
        _at("grouped_at", nullable=True),
        _at("suggested_at", nullable=True),
        _at("verdict_at", nullable=True),
        _at("served_at", nullable=True),
        _int("wiping"),
    )
    op.create_table(
        "node_origin",
        _ref("node_id", "node", primary_key=True),
        _ref("created_by", "user", "SET NULL", nullable=True),
        _int("via_link"),
    )


def upgrade() -> None:
    _tables()
    for sql in INDEXES.values():
        op.execute(sql)
    if op.get_bind().dialect.name != "sqlite":
        return
    for sql in TRIGGERS.values():
        op.execute(sql)


def downgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        # De connect-haak zet hem al (app/db.py); hier nog eens, want dit zijn biometrische
        # gegevens: SQLite zet de gewiste cellen en vrije pagina's op nul (Keuze 64)
        op.execute("PRAGMA secure_delete = ON")
    for table in TABLES:
        op.execute(f"DELETE FROM {table}")
    if sqlite:
        for name in reversed(TRIGGERS):
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
    for table in TABLES:
        op.drop_table(table)
