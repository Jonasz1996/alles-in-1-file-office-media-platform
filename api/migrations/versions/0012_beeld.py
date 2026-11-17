"""zoeken op beeld: een vector per inhoud en een bestand per eigenaar

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-11 12:00:00.000000

Bouwontwerp fase 6, §3.1a en Keuze 76. Twee nieuwe tabellen (blob_embed en beeld_owner), de
kolom photo_pref.beeld (TEXT NULL: NULL is de standaard uit PHOTO_PREFS, Vraag 11), en drie
triggers die beeld_owner.gen ophogen als het bestand per eigenaar verandert: een nieuw
beeldbestand, een andere eigenaar of inhoud, en definitief wissen (K1-08). Alleen een gewone
INSERT … WHERE NOT EXISTS en een UPDATE, geen upsert. Niets herschrijft node.

De SQL hieronder is een bevroren kopie van app/search/schema.py (TRIGGERS_0012, INDEXES_0012 en
TABLES_0012); tests/search/test_schema_triggers.py eist dat ze gelijk zijn. Met en zonder FTS5
zijn ze gelijk.

Op PostgreSQL alleen de tabellen, de index en de kolom, geen triggers: beeld staat daar uit,
zoals FTS (Keuze 10). Met de hand nagelopen op PostgreSQL 16, net als 0011; geen vaste test.

De downgrade haalt eerst de triggers weg, dan de twee tabellen, dan de kolom (gewone DROP
COLUMN, geen kopie van photo_pref). De beeldindex is daarna weg; de foto's blijven. De cachemap
met de bestanden per eigenaar blijft staan; het runbook zegt hoe je hem wist.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# --- Bevroren kopie van app/search/schema.py (revisie 0012) -------------------------------

INDEXES = {
    "ux_blob_embed_sha": "CREATE UNIQUE INDEX ux_blob_embed_sha ON blob_embed (blob_sha)",
}

TABLES = ("blob_embed", "beeld_owner")


def _trigger(head: str, *body: str) -> str:
    lines = [f"CREATE TRIGGER {head} BEGIN"]
    lines += ["  " + line.replace("\n", "\n  ") for line in body if line]
    lines.append("END")
    return "\n".join(lines)


def bump(user: str) -> tuple[str, str]:
    return (
        f"INSERT INTO beeld_owner (user_id, gen) SELECT {user}, 0\n"
        f"  WHERE NOT EXISTS (SELECT 1 FROM beeld_owner WHERE user_id = {user})\n"
        f'    AND EXISTS (SELECT 1 FROM "user" WHERE id = {user});',
        f"UPDATE beeld_owner SET gen = gen + 1 WHERE user_id = {user};",
    )


def triggers() -> dict[str, str]:
    t: dict[str, str] = {}
    t["node_ai_beeld"] = _trigger(
        "node_ai_beeld AFTER INSERT ON node\nWHEN NEW.kind = 'file' AND NEW.ftype = 'image'",
        *bump("NEW.owner_id"),
    )
    t["node_au_beeld"] = _trigger(
        "node_au_beeld AFTER UPDATE OF owner_id, blob_id ON node\n"
        "WHEN NEW.kind = 'file' AND (OLD.ftype = 'image' OR NEW.ftype = 'image')\n"
        " AND (OLD.owner_id IS NOT NEW.owner_id OR OLD.blob_id IS NOT NEW.blob_id)",
        *bump("OLD.owner_id"),
        *bump("NEW.owner_id"),
    )
    t["node_ad_beeld"] = _trigger(
        "node_ad_beeld AFTER DELETE ON node\nWHEN OLD.kind = 'file' AND OLD.ftype = 'image'",
        *bump("OLD.owner_id"),
    )
    return t


TRIGGERS = triggers()

# --- De migratie -----------------------------------------------------------------------------


def _int(column: str) -> sa.Column:
    return sa.Column(column, sa.Integer(), server_default="0", nullable=False)


def upgrade() -> None:
    op.create_table(
        "blob_embed",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "blob_sha",
            sa.String(length=64),
            sa.ForeignKey("blob.sha256", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("vec", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "beeld_owner",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        _int("gen"),
        _int("built_gen"),
        _int("built_rows"),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=True),
    )
    for sql in INDEXES.values():
        op.execute(sql)
    op.add_column("photo_pref", sa.Column("beeld", sa.Text(), nullable=True))
    if op.get_bind().dialect.name != "sqlite":
        return
    for sql in TRIGGERS.values():
        op.execute(sql)


def downgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        for name in reversed(TRIGGERS):
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
    for table in TABLES:
        op.drop_table(table)
    if sqlite:
        # Gewone DROP COLUMN (SQLite ≥ 3.35), geen batch: die maakt de tabel opnieuw
        op.execute("ALTER TABLE photo_pref DROP COLUMN beeld")
    else:
        op.drop_column("photo_pref", "beeld")
