"""versies, delen, tags, achtergrondwerk en zoeken op naam

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20 20:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

from app.text import fold

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Gewone ADD COLUMN, geen batch: zie 0002 (een batch maakt de tabel opnieuw aan, en
    # met foreign keys aan neemt DROP TABLE node dan via CASCADE alle bestanden mee).
    op.add_column("node", sa.Column("search_name", sa.Text(), server_default="", nullable=False))
    op.add_column("blob", sa.Column("mime", sa.String(length=255), nullable=True))
    op.add_column("blob", sa.Column("preview", sa.String(length=16), nullable=True))
    op.add_column("blob", sa.Column("preview_meta", sa.JSON(), nullable=True))
    op.add_column("refresh_token", sa.Column("ip", sa.String(length=45), nullable=True))

    op.create_table(
        "version",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("blob_id", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replaced_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["blob_id"], ["blob.sha256"]),
        sa.ForeignKeyConstraint(["replaced_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_version_node_id", "version", ["node_id"])

    op.create_table(
        "share",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("grantee_id", sa.Uuid(), nullable=True),
        sa.Column("token", sa.String(length=64), nullable=True),
        sa.Column("permission", sa.String(length=8), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["grantee_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "grantee_id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index("ix_share_node_id", "share", ["node_id"])
    op.create_index("ix_share_grantee_id", "share", ["grantee_id"])

    op.create_table(
        "tag",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name"),
    )
    op.create_table(
        "node_tag",
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("node_id", "tag_id"),
    )
    op.create_index("ix_node_tag_tag_id", "node_tag", ["tag_id"])

    op.create_table(
        "job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_type", "job", ["type"])
    op.create_index("ix_job_key", "job", ["key"])
    op.create_index("ix_job_status", "job", ["status"])
    op.create_index("ix_job_run_after", "job", ["run_after"])

    # Bestaande namen doorzoekbaar maken
    bind = op.get_bind()
    node = sa.table("node", sa.column("id", sa.Uuid()), sa.column("name"), sa.column("search_name"))
    for node_id, name in bind.execute(sa.select(node.c.id, node.c.name)).all():
        bind.execute(node.update().where(node.c.id == node_id).values(search_name=fold(name)))


def downgrade() -> None:
    op.drop_table("job")
    op.drop_table("node_tag")
    op.drop_table("tag")
    op.drop_table("share")
    op.drop_table("version")
    op.drop_column("refresh_token", "ip")
    op.drop_column("blob", "preview_meta")
    op.drop_column("blob", "preview")
    op.drop_column("blob", "mime")
    op.drop_column("node", "search_name")
