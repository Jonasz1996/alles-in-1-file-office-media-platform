"""nextcloud-clients: app-wachtwoorden en de inlogflow v2

Revision ID: 0015
Revises: 0012
Create Date: 2026-11-17 06:30:00.000000

Twee nieuwe tabellen, verder niets. app_password: één rij per gekoppeld apparaat (Nextcloud
desktop, Android, iOS, rclone); alleen de SHA-256 van het wachtwoord staat erin. nc_login_flow:
een lopende inlogflow v2 (hooguit 20 minuten). Gewone tabellen, dus gelijk op SQLite en
PostgreSQL.

LET OP bij het samenvoegen met fase 6: 0013 en 0014 zijn van de thread "Stappenplan uitwerken"
(PostgreSQL). Zet down_revision dan op hun laatste head ("0014"), zodat er één head blijft. Deze
migratie is nog nergens uitgerold, dus dat mag.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_password",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_app_password_user", "app_password", ["user_id"])
    op.create_table(
        "nc_login_flow",
        sa.Column("poll_hash", sa.String(length=64), nullable=False),
        sa.Column("login_hash", sa.String(length=64), nullable=False),
        sa.Column("client", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("login_name", sa.String(length=320), nullable=True),
        sa.Column("secret", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("poll_hash"),
        sa.UniqueConstraint("login_hash"),
    )


def downgrade() -> None:
    op.drop_table("nc_login_flow")
    op.drop_index("ix_app_password_user", table_name="app_password")
    op.drop_table("app_password")
