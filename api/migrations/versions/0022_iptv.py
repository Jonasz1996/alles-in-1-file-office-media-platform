"""iptv: live tv met een kanaallijst (m3u) en een gids (XMLTV)

Revision ID: 0022
Revises: 0019
Create Date: 2026-11-26 20:00:00.000000

Drie nieuwe tabellen, op SQLite en PostgreSQL gelijk: iptv_channel (app.models.IptvChannel, de
kanalen uit de m3u, met hun stroomadres versleuteld), iptv_programme (app.models.IptvProgramme,
de gids uit XMLTV) en iptv_favorite (app.models.IptvFavorite, de favorieten per gebruiker, op de
vaste sleutel van een kanaal zodat ze een nieuwe lijst overleven). De instellingen (het adres
van de m3u, ook versleuteld, en dat van de gids) staan in de tabel setting, rij
`iptv_settings`. Niets bestaands verandert; de downgrade haalt de drie tabellen weg.

Nummering: 0020 is gereserveerd (rangvector, fase 6) en 0021 is Sport (PR #13). Beide volgen
nu, net als deze, op 0019. Wie als laatste samenvoegt, zet down_revision op de vorige.
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0019"
branch_labels = None
depends_on = None

BIGINT_PG = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "iptv_channel",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("group_title", sa.String(length=200), nullable=True),
        sa.Column("xmltv_id", sa.String(length=200), nullable=True),
        sa.Column("logo", sa.String(length=1000), nullable=True),
        sa.Column("logo_key", sa.String(length=32), nullable=True),
        sa.Column("stream", sa.JSON(), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", name="uq_iptv_channel_key"),
    )
    op.create_index("ix_iptv_channel_xmltv_id", "iptv_channel", ["xmltv_id"])
    op.create_index("ix_iptv_channel_logo_key", "iptv_channel", ["logo_key"])
    op.create_table(
        "iptv_programme",
        sa.Column("id", BIGINT_PG, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("xmltv_id", sa.String(length=200), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stop", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("subtitle", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("episode", sa.String(length=100), nullable=True),
    )
    op.create_index("ix_iptv_programme_channel_start", "iptv_programme", ["xmltv_id", "start"])
    op.create_index("ix_iptv_programme_stop", "iptv_programme", ["stop"])
    op.create_table(
        "iptv_favorite",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("channel_key", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("iptv_favorite")
    op.drop_index("ix_iptv_programme_stop", table_name="iptv_programme")
    op.drop_index("ix_iptv_programme_channel_start", table_name="iptv_programme")
    op.drop_table("iptv_programme")
    op.drop_index("ix_iptv_channel_logo_key", table_name="iptv_channel")
    op.drop_index("ix_iptv_channel_xmltv_id", table_name="iptv_channel")
    op.drop_table("iptv_channel")
