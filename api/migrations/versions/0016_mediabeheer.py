"""mediabeheer: de films en series die de app volgt (fase 11)

Revision ID: 0016
Revises: 0013
Create Date: 2026-11-22 12:00:00.000000

Twee nieuwe tabellen, op SQLite en PostgreSQL gelijk: arr_wanted (app.models.ArrWanted, wat de
app volgt) en arr_grab (app.models.ArrGrab, wat hij aan qBittorrent gaf, met de zwarte lijst).
Niets bestaands verandert. De downgrade haalt ze weg: de lijst van gevolgde titels en de
geschiedenis zijn dan weg, de bibliotheken en de bestanden blijven.

Nummering: 0014 en 0015 zijn gereserveerd voor fase 6 (0014_pg_zoeken) en Nextcloud
(0015_nextcloud). Zolang die er op deze branch niet zijn, volgt 0016 op 0013; bij het
samenvoegen gaat down_revision naar de laatste van die twee.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "arr_wanted",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("imdb_id", sa.String(length=16), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("original_title", sa.String(length=500), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("runtime_min", sa.Integer(), nullable=True),
        sa.Column(
            "library_id",
            sa.Uuid(),
            sa.ForeignKey("media_library.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile", sa.String(length=32), nullable=False),
        sa.Column("seasons", sa.JSON(), nullable=True),
        sa.Column("monitored", sa.Boolean(), nullable=False),
        sa.Column(
            "added_by", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("searched_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("library_id", "kind", "tmdb_id", name="uq_arr_wanted_title"),
        sa.CheckConstraint("kind IN ('movie', 'show')", name="ck_arr_wanted_kind"),
    )
    op.create_index("ix_arr_wanted_library_id", "arr_wanted", ["library_id"])
    op.create_table(
        "arr_grab",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "wanted_id",
            sa.Uuid(),
            sa.ForeignKey("arr_wanted.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("release", sa.String(length=500), nullable=False),
        sa.Column("indexer", sa.String(length=200), nullable=False),
        sa.Column("guid", sa.String(length=1000), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("quality", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('downloading', 'importing', 'imported', 'failed')",
            name="ck_arr_grab_state",
        ),
    )
    op.create_index("ix_arr_grab_wanted_id", "arr_grab", ["wanted_id"])


def downgrade() -> None:
    op.drop_index("ix_arr_grab_wanted_id", table_name="arr_grab")
    op.drop_table("arr_grab")
    op.drop_index("ix_arr_wanted_library_id", table_name="arr_wanted")
    op.drop_table("arr_wanted")
