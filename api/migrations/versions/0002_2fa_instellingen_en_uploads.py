"""2fa, instellingen en uploads

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20 16:10:48.727493
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setting",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "recovery_code",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recovery_code_user_id", "recovery_code", ["user_id"])
    op.create_table(
        "upload",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("conflict", sa.String(length=8), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["node.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_upload_expires_at", "upload", ["expires_at"])
    # Gewone ADD COLUMN, geen batch: op SQLite zou een batch de tabel user opnieuw
    # aanmaken, en met foreign keys aan neemt DROP TABLE dan alle bestanden mee.
    op.add_column("user", sa.Column("totp_secret", sa.String(length=64), nullable=True))
    op.add_column("user", sa.Column("totp_pending", sa.String(length=64), nullable=True))
    op.add_column(
        "user",
        sa.Column("totp_last_step", sa.BigInteger(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("user", "totp_last_step")
    op.drop_column("user", "totp_pending")
    op.drop_column("user", "totp_secret")
    op.drop_index("ix_upload_expires_at", table_name="upload")
    op.drop_table("upload")
    op.drop_index("ix_recovery_code_user_id", table_name="recovery_code")
    op.drop_table("recovery_code")
    op.drop_table("setting")
