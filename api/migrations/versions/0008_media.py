"""fase 5: media en streaming

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-28 09:00:00.000000

Alleen nieuwe tabellen (bouwontwerp fase 5, §3.1): geen batch, geen backfill, en geen kolom op
een bestaande tabel. Alle tabellen beginnen met media_, behalve playback_state (de naam uit het
stappenplan); album bestaat al voor foto's.

- media_library: een map van de boom die een bibliotheek is (Keuze 1, 4);
- media_probe: de analyse per inhoud (sha), niet per bestand (Keuze 6, 15, 17, 18, 30);
- media_show, media_album: series en albums binnen een bibliotheek (Keuze 8, 37);
- media_item: één rij per bestand in een bibliotheek (per node, Keuze 6);
- playback_state: de voortgang per gebruiker, profiel en bestand (Keuze 36);
- media_playlist, media_playlist_item: afspeellijsten (M9).

De downgrade haalt alles weg in omgekeerde volgorde (eerst de indexen, dan de tabel). Daarna is
de database gelijk aan 0007, zodat v0.6.0 weer start: app.cli db-downgrade 0007 (Keuze 47).
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_library",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("root_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=16), server_default="nl-NL", nullable=False),
        sa.Column("online", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("scan_state", sa.String(length=8), server_default="idle", nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("item_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["root_id"], ["node.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("root_id", name="uq_media_library_root"),
    )
    op.create_index("ix_media_library_owner", "media_library", ["owner_id"])

    op.create_table(
        "media_probe",
        sa.Column("blob_sha", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=8), nullable=False),
        sa.Column("container", sa.String(length=32), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("bit_rate", sa.Integer(), nullable=True),
        sa.Column("start_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("streams", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("chapters", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("tags", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("keyframes", sa.LargeBinary(), nullable=True),
        sa.Column("keyframes_state", sa.String(length=8), server_default="none", nullable=False),
        sa.Column("max_gop_ms", sa.Integer(), nullable=True),
        sa.Column("subs_state", sa.String(length=8), server_default="none", nullable=False),
        sa.Column("trick_state", sa.String(length=8), server_default="none", nullable=False),
        sa.Column("trick", sa.JSON(), nullable=True),
        sa.Column("audio_state", sa.String(length=8), server_default="none", nullable=False),
        sa.Column("audio", sa.JSON(), nullable=True),
        sa.Column("truncated", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("probed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["blob_sha"], ["blob.sha256"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("blob_sha"),
    )

    op.create_table(
        "media_show",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("library_id", sa.Uuid(), nullable=False),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("sort_title", sa.String(length=255), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column("ids", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("art", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("locked", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("match_state", sa.String(length=8), server_default="local", nullable=False),
        sa.Column("candidates", sa.JSON(), server_default="[]", nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["media_library.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["folder_id"], ["node.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("library_id", "folder_id", name="uq_media_show_folder"),
    )

    op.create_table(
        "media_album",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("library_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("sort_title", sa.String(length=255), nullable=False),
        sa.Column("album_artist", sa.String(length=255), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("compilation", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("mbid", sa.String(length=36), nullable=True),
        sa.Column("art", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("match_state", sa.String(length=8), server_default="local", nullable=False),
        sa.Column("track_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["media_library.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("library_id", "key", name="uq_media_album_key"),
    )

    op.create_table(
        "media_item",
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("library_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("extra", sa.String(length=12), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("present", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("sort_title", sa.String(length=255), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("show_id", sa.Uuid(), nullable=True),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("episode_end", sa.Integer(), nullable=True),
        sa.Column("absolute", sa.Integer(), nullable=True),
        sa.Column("aired", sa.Date(), nullable=True),
        sa.Column("album_id", sa.Uuid(), nullable=True),
        sa.Column("disc", sa.Integer(), nullable=True),
        sa.Column("track", sa.Integer(), nullable=True),
        sa.Column("artist", sa.String(length=255), nullable=True),
        sa.Column("part", sa.Integer(), nullable=True),
        sa.Column("edition", sa.String(length=64), nullable=True),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column("ids", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("art", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("subs", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("source", sa.String(length=8), server_default="name", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("names_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("match_state", sa.String(length=8), server_default="local", nullable=False),
        sa.Column("candidates", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["library_id"], ["media_library.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["media_item.node_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["show_id"], ["media_show.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["album_id"], ["media_album.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("node_id"),
    )
    op.create_index(
        "ix_media_item_lib", "media_item", ["library_id", "present", "kind", "sort_title"]
    )
    op.create_index("ix_media_item_added", "media_item", ["library_id", "present", "added_at"])
    op.create_index("ix_media_item_show", "media_item", ["show_id", "season", "episode"])
    op.create_index("ix_media_item_album", "media_item", ["album_id", "disc", "track"])
    op.create_index("ix_media_item_parent", "media_item", ["parent_id"])

    op.create_table(
        "playback_state",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("profile", sa.String(length=36), server_default="", nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("position_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("watched", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("play_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("audio", sa.Integer(), nullable=True),
        sa.Column("subtitle", sa.String(length=48), nullable=True),
        sa.Column("blob_sha", sa.String(length=64), nullable=True),
        sa.Column("origin", sa.String(length=36), nullable=True),
        sa.Column("seq", sa.Integer(), server_default="0", nullable=False),
        sa.Column("play_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "profile", "node_id"),
    )
    op.create_index(
        "ix_playback_recent", "playback_state", ["user_id", "profile", "watched", "updated_at"]
    )

    op.create_table(
        "media_playlist",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_playlist_owner", "media_playlist", ["owner_id"])

    op.create_table(
        "media_playlist_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("playlist_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["playlist_id"], ["media_playlist.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_playlist_item", "media_playlist_item", ["playlist_id", "position"])


def downgrade() -> None:
    op.drop_index("ix_media_playlist_item", table_name="media_playlist_item")
    op.drop_table("media_playlist_item")
    op.drop_index("ix_media_playlist_owner", table_name="media_playlist")
    op.drop_table("media_playlist")
    op.drop_index("ix_playback_recent", table_name="playback_state")
    op.drop_table("playback_state")
    for name in (
        "ix_media_item_parent",
        "ix_media_item_album",
        "ix_media_item_show",
        "ix_media_item_added",
        "ix_media_item_lib",
    ):
        op.drop_index(name, table_name="media_item")
    op.drop_table("media_item")
    op.drop_table("media_album")
    op.drop_table("media_show")
    op.drop_table("media_probe")
    op.drop_index("ix_media_library_owner", table_name="media_library")
    op.drop_table("media_library")
