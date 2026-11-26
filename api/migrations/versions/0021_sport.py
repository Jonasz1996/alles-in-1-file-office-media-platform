"""sport: activiteiten uit GPX en TCX (vervangt Endurain)

Revision ID: 0021
Revises: 0019
Create Date: 2026-11-26 17:00:00.000000

Eén nieuwe tabel, sport_activity (app.models.SportActivity). Niets bestaands verandert; de
downgrade haalt de tabel weg (de activiteiten zijn dan weg, de bestanden in Bestanden niet).

Nummering: 0020 is gereserveerd voor de rangvector van zoeken (0.8.3-lijst). Komt die er eerder,
zet down_revision hier dan op "0020" (net als 0016 en 0018 bij het samenvoegen).
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sport_activity",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sport", sa.String(length=8), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("day", sa.Date(), nullable=True),
        sa.Column("distance", sa.Float(), nullable=False),
        sa.Column("elapsed", sa.Float(), nullable=False),
        sa.Column("moving", sa.Float(), nullable=False),
        sa.Column("gain", sa.Float(), nullable=False),
        sa.Column("loss", sa.Float(), nullable=False),
        sa.Column("ele_min", sa.Float(), nullable=True),
        sa.Column("ele_max", sa.Float(), nullable=True),
        sa.Column("max_speed", sa.Float(), nullable=False),
        sa.Column("avg_hr", sa.Float(), nullable=True),
        sa.Column("max_hr", sa.Float(), nullable=True),
        sa.Column("avg_cad", sa.Float(), nullable=True),
        sa.Column("avg_power", sa.Float(), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("fixed", sa.Integer(), nullable=False),
        sa.Column("lat_min", sa.Float(), nullable=False),
        sa.Column("lon_min", sa.Float(), nullable=False),
        sa.Column("lat_max", sa.Float(), nullable=False),
        sa.Column("lon_max", sa.Float(), nullable=False),
        sa.Column("efforts", sa.JSON(), nullable=False),
        sa.Column("preview", sa.Text(), nullable=False),
        sa.Column("ele_filter", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("track", sa.LargeBinary(), nullable=False),
        sa.Column("source", sa.LargeBinary(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("source_fmt", sa.String(length=8), nullable=False),
        sa.Column("source_sha", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "source_sha", name="uq_sport_activity_source"),
    )
    op.create_index("ix_sport_activity_owner_start", "sport_activity", ["owner_id", "start_at"])


def downgrade() -> None:
    op.drop_index("ix_sport_activity_owner_start", table_name="sport_activity")
    op.drop_table("sport_activity")
