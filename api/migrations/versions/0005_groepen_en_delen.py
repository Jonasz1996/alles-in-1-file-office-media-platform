"""groepen, delen met een groep, en uploads in gedeelde mappen of via een link

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20 22:15:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def add_reference(table: str, column: str, target: str) -> None:
    """Een nullable kolom met een foreign key (ON DELETE CASCADE) naar target.id. Alembic kan
    op SQLite geen constraint toevoegen, maar SQLite zelf accepteert REFERENCES bij ADD
    COLUMN zolang de standaardwaarde NULL is."""
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            f'ALTER TABLE "{table}" ADD COLUMN {column} CHAR(32) '
            f'REFERENCES "{target}" (id) ON DELETE CASCADE'
        )
    else:
        op.add_column(
            table,
            sa.Column(
                column, sa.Uuid(), sa.ForeignKey(f"{target}.id", ondelete="CASCADE"), nullable=True
            ),
        )


def upgrade() -> None:
    op.create_table(
        "user_group",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "group_member",
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["user_group.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "user_id"),
    )
    op.create_index("ix_group_member_user_id", "group_member", ["user_id"])

    # Gewone ADD COLUMN, geen batch: zie 0002 (een batch maakt de tabel opnieuw aan, en met
    # foreign keys aan neemt DROP TABLE dan via CASCADE alles mee wat ernaar verwijst).
    add_reference("share", "group_id", "user_group")
    op.add_column("share", sa.Column("label", sa.String(length=100), nullable=True))
    op.create_index("ix_share_group_id", "share", ["group_id"])
    op.create_index("uq_share_node_group", "share", ["node_id", "group_id"], unique=True)

    add_reference("upload", "created_by", "user")
    add_reference("upload", "share_id", "share")
    op.create_index("ix_upload_share_id", "upload", ["share_id"])
    # Tot nu toe uploadde iedereen alleen in zijn eigen bestanden
    upload = sa.table(
        "upload", sa.column("owner_id", sa.Uuid()), sa.column("created_by", sa.Uuid())
    )
    op.execute(upload.update().values(created_by=upload.c.owner_id))


def downgrade() -> None:
    op.drop_index("ix_upload_share_id", table_name="upload")
    op.drop_column("upload", "share_id")
    op.drop_column("upload", "created_by")
    op.drop_index("uq_share_node_group", table_name="share")
    op.drop_index("ix_share_group_id", table_name="share")
    op.drop_column("share", "label")
    op.drop_column("share", "group_id")
    op.drop_index("ix_group_member_user_id", table_name="group_member")
    op.drop_table("group_member")
    op.drop_table("user_group")
