"""mediabeheer: de films en series die de app volgt (fase 11)

Revision ID: 0016
Revises: 0013
Create Date: 2026-11-22 12:00:00.000000

Zeven nieuwe tabellen, op SQLite en PostgreSQL gelijk: arr_wanted (app.models.ArrWanted, wat de
app volgt), arr_grab (app.models.ArrGrab, wat hij aan qBittorrent gaf, met de zwarte lijst) en
arr_subtitle (app.models.ArrSubtitle, wat OpenSubtitles per video en taal opleverde) en
media_sub_offset (app.models.SubOffset, de timing van een ondertitel die iemand verschoof) en
arr_request (app.models.ArrRequest, de verzoeken van gezinsleden) en arr_upscale
(app.models.ArrUpscale, de rij van de AI-upscaling) en media_facts (app.models.MediaFacts,
collectie en kijkwijzer uit TMDB).
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
    op.create_table(
        "arr_request",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("seasons", sa.JSON(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column(
            "wanted_id",
            sa.Uuid(),
            sa.ForeignKey("arr_wanted.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decided_by", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'approved', 'declined', 'available')",
            name="ck_arr_request_state",
        ),
        sa.CheckConstraint("kind IN ('movie', 'show')", name="ck_arr_request_kind"),
    )
    op.create_index("ix_arr_request_user_id", "arr_request", ["user_id"])
    op.create_table(
        "arr_upscale",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "node_id", sa.Uuid(), sa.ForeignKey("node.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("scale", sa.Integer(), nullable=False),
        sa.Column("segments_done", sa.Integer(), nullable=False),
        sa.Column("segments_total", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column(
            "result_node_id",
            sa.Uuid(),
            sa.ForeignKey("node.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'done', 'failed', 'cancelled')",
            name="ck_arr_upscale_state",
        ),
    )
    op.create_index("ix_arr_upscale_node_id", "arr_upscale", ["node_id"])
    op.create_table(
        "arr_subtitle",
        sa.Column(
            "node_id",
            sa.Uuid(),
            sa.ForeignKey("node.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("lang", sa.String(length=8), primary_key=True, nullable=False),
        sa.Column("state", sa.String(length=8), nullable=False),
        sa.Column("tries", sa.Integer(), nullable=False),
        sa.Column("tried_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=True),
        sa.Column("release", sa.String(length=200), nullable=True),
        sa.Column(
            "sub_node_id",
            sa.Uuid(),
            sa.ForeignKey("node.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("state IN ('found', 'none')", name="ck_arr_subtitle_state"),
    )
    op.create_table(
        "media_sub_offset",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.Uuid(),
            sa.ForeignKey("node.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("sub_id", sa.String(length=8), primary_key=True, nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("offset_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "media_facts",
        sa.Column("kind", sa.String(length=8), primary_key=True, nullable=False),
        sa.Column("tmdb_id", sa.String(length=16), primary_key=True, nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=True),
        sa.Column("collection_name", sa.String(length=255), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_media_facts_collection", "media_facts", ["collection_id"])


def downgrade() -> None:
    op.drop_index("ix_media_facts_collection", table_name="media_facts")
    op.drop_table("media_facts")
    op.drop_table("media_sub_offset")
    op.drop_index("ix_arr_upscale_node_id", table_name="arr_upscale")
    op.drop_table("arr_upscale")
    op.drop_index("ix_arr_request_user_id", table_name="arr_request")
    op.drop_table("arr_request")
    op.drop_table("arr_subtitle")
    op.drop_index("ix_arr_grab_wanted_id", table_name="arr_grab")
    op.drop_table("arr_grab")
    op.drop_index("ix_arr_wanted_library_id", table_name="arr_wanted")
    op.drop_table("arr_wanted")
