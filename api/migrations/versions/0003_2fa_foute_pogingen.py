"""2fa: foute pogingen tellen en tijdelijk blokkeren

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20 18:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Gewone ADD COLUMN, geen batch: zie 0002 (een batch maakt de tabel user opnieuw aan).
    op.add_column(
        "user", sa.Column("totp_failures", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("user", sa.Column("totp_locked_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "totp_locked_until")
    op.drop_column("user", "totp_failures")
