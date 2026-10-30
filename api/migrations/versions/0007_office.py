"""fase 4: bewerksessies van office en de PDF van een kantoordocument

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-26 09:00:00.000000

Een nieuwe tabel office_session (één rij per document.key, Keuze 14) en twee kolommen op blob
voor de PDF via de document server (Keuze 25). Gewone ADD COLUMN, geen batch: zie 0002 en 0006
(een batch maakt de tabel opnieuw aan, en met foreign keys aan neemt dat via CASCADE te veel
mee). De nieuwe foreign keys staan alleen in de nieuwe tabel.

Hooguit één niet-dichte sessie per bestand: een gedeeltelijke unieke index (SQLite en
PostgreSQL kennen allebei WHERE op een index).

De downgrade haalt eerst ix_blob_pdf weg (SQLite laat geen kolom vallen die in een index
staat), dan de kolommen, dan de tabel. Daarna is de database gelijk aan 0006, zodat v0.5.0
weer start (M19); app.cli db-downgrade 0006 doet dat.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

OPEN = "state <> 'closed' AND node_id IS NOT NULL"


def upgrade() -> None:
    op.create_table(
        "office_session",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=8), nullable=False),
        sa.Column("blob_id", sa.String(length=64), nullable=False),
        sa.Column("checkpoint", sa.String(length=64), nullable=True),
        sa.Column("versioned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("versions_made", sa.Integer(), server_default="0", nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_mark", sa.String(length=40), nullable=True),
        sa.Column("state", sa.String(length=8), server_default="pending", nullable=False),
        sa.Column("members", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("banned", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("connected", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("last_error", sa.String(length=32), nullable=True),
        sa.Column("closed_reason", sa.String(length=16), nullable=True),
        sa.Column("node_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_office_session_node", "office_session", ["node_id"])
    op.create_index("ix_office_session_state", "office_session", ["state"])
    op.create_index(
        "ux_office_session_open",
        "office_session",
        ["node_id"],
        unique=True,
        sqlite_where=sa.text(OPEN),
        postgresql_where=sa.text(OPEN),
    )

    op.add_column("blob", sa.Column("pdf_state", sa.String(length=8), nullable=True))
    op.add_column(
        "blob", sa.Column("pdf_version", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_index("ix_blob_pdf", "blob", ["pdf_version"])


def downgrade() -> None:
    op.drop_index("ix_blob_pdf", table_name="blob")
    op.drop_column("blob", "pdf_version")
    op.drop_column("blob", "pdf_state")
    op.drop_index("ux_office_session_open", table_name="office_session")
    op.drop_index("ix_office_session_state", table_name="office_session")
    op.drop_index("ix_office_session_node", table_name="office_session")
    op.drop_table("office_session")
