"""fase 3: taal en blokkeren van accounts, opnamedatum, albums, previewversies

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-21 09:00:00.000000

Alle schemawijzigingen van fase 3 in één migratie. Gewone ADD COLUMN, geen batch: zie 0002
(een batch maakt de tabel opnieuw aan, en met foreign keys aan neemt DROP TABLE node dan via
CASCADE alle bestanden mee). De nieuwe foreign keys staan alleen in nieuwe tabellen.

Bestaande afbeeldingen en video's krijgen een terugvaldatum op de tijdlijn: hun modified_at als
kloktijd in de tijdzone van de server (settings.timezone). De preview- of backfilltaak zet
daar later de EXIF-datum overheen.
"""

from datetime import UTC, datetime
from itertools import batched

import sqlalchemy as sa
from alembic import op

from app.photodate import day_of, wall_clock

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

BATCH = 500


def _timezone() -> str:
    """De tijdzone die app.migrate meegeeft; anders die uit de omgeving (PLATFORM_TIMEZONE)."""
    config = op.get_context().config
    tz = config.attributes.get("timezone") if config is not None else None
    if tz:
        return tz
    from app.config import Settings

    return Settings().timezone


def upgrade() -> None:
    op.add_column("user", sa.Column("locale", sa.String(length=5), nullable=True))
    op.add_column("user", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("blob", sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "blob", sa.Column("meta_version", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "blob", sa.Column("render_version", sa.Integer(), server_default="0", nullable=False)
    )

    op.add_column("node", sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("node", sa.Column("taken_day", sa.Integer(), nullable=True))
    op.create_index("ix_node_owner_taken", "node", ["owner_id", "taken_day", "taken_at", "id"])
    op.create_index("ix_node_blob", "node", ["blob_id"])
    op.create_index("ix_node_parent_order", "node", ["parent_id", "kind", "search_name", "id"])

    op.add_column("upload", sa.Column("client_mtime", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "album",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("cover_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["id"], ["node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cover_id"], ["node.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "album_item",
        sa.Column("album_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["album_id"], ["node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("album_id", "node_id"),
    )
    op.create_index("ix_album_item_node", "album_item", ["node_id"])

    # Terugvaldatum voor wat er al stond, in stukken van 500
    tz = _timezone()
    bind = op.get_bind()
    node = sa.table(
        "node",
        sa.column("id", sa.Uuid()),
        sa.column("kind", sa.String()),
        sa.column("mime", sa.String()),
        sa.column("modified_at", sa.DateTime(timezone=True)),
        sa.column("taken_at", sa.DateTime(timezone=True)),
        sa.column("taken_day", sa.Integer()),
    )
    rows = bind.execute(
        sa.select(node.c.id, node.c.modified_at).where(
            node.c.kind == "file",
            sa.or_(node.c.mime.like("image/%"), node.c.mime.like("video/%")),
        )
    ).all()
    update = (
        node.update()
        .where(node.c.id == sa.bindparam("node_id"))
        .values(taken_at=sa.bindparam("new_taken_at"), taken_day=sa.bindparam("new_taken_day"))
    )
    for chunk in batched(rows, BATCH):
        values = []
        for node_id, modified_at in chunk:
            taken = wall_clock(modified_at or datetime.now(UTC), tz)
            values.append(
                {"node_id": node_id, "new_taken_at": taken, "new_taken_day": day_of(taken)}
            )
        bind.execute(update, values)


def downgrade() -> None:
    op.drop_index("ix_album_item_node", table_name="album_item")
    op.drop_table("album_item")
    op.drop_table("album")
    op.drop_column("upload", "client_mtime")
    op.drop_index("ix_node_parent_order", table_name="node")
    op.drop_index("ix_node_blob", table_name="node")
    op.drop_index("ix_node_owner_taken", table_name="node")
    op.drop_column("node", "taken_day")
    op.drop_column("node", "taken_at")
    op.drop_column("blob", "render_version")
    op.drop_column("blob", "meta_version")
    op.drop_column("blob", "taken_at")
    op.drop_column("user", "disabled_at")
    op.drop_column("user", "locale")
