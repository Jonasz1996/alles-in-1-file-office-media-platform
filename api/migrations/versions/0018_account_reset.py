"""account resetten: tijdelijk wachtwoord, reset-verzoeken met een code en een logboek

Revision ID: 0018
Revises: 0016
Create Date: 2026-11-23 20:00:00.000000

0017 is gereserveerd voor fase 11. Komt die er, of een andere migratie op 0016, zet dan
down_revision hier op de nieuwste head (net als 0015/0016 bij het samenvoegen).
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Gewone ADD COLUMN, geen batch: zie 0002 (een batch maakt de tabel user opnieuw aan).
    op.add_column(
        "user", sa.Column("password_temp_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "account_reset",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_account_reset_user_id", "account_reset", ["user_id"])
    op.create_table(
        "reset_request",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("login", sa.String(length=320), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_reset_request_expires_at", "reset_request", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_reset_request_expires_at", table_name="reset_request")
    op.drop_table("reset_request")
    op.drop_index("ix_account_reset_user_id", table_name="account_reset")
    op.drop_table("account_reset")
    op.drop_column("user", "password_temp_until")
