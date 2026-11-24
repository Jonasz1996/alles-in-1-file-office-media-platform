"""bibliotheek kopieën: replicatie per bibliotheek in het cluster

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-24 12:00:00.000000

Eén kolom: media_library.copies (app.models.MediaLibrary), het aantal kopieën dat de inhoud van
een bibliotheek in het cluster krijgt (1, 2 of 3; NULL is de standaard van het cluster,
PLATFORM_BLOB_DEFAULT_COPIES). app/blobsplit.py zet de inhoud daarmee in blobs-r1/ tot blobs-r3/
(docs/runbooks/replicatie.md). Bestaande bibliotheken krijgen NULL: er verandert niets.

Gewone ADD COLUMN met de CHECK aan de kolom, geen batch: een batch maakt media_library op SQLite
opnieuw aan, en dan gaan media_item en media_show mee (ON DELETE CASCADE). Op SQLite en
PostgreSQL gelijk. De downgrade haalt de kolom weg (SQLite 3.35 en later kan DROP COLUMN; de CHECK
hoort bij de kolom zelf). Wat al in blobs-r1/ tot blobs-r3/ staat, blijft daar: zet de
bibliotheken eerst op Standaard en laat een ronde lopen (zie het runbook, "Terugzetten").
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_library",
        sa.Column(
            "copies",
            sa.Integer(),
            sa.CheckConstraint(
                "copies IS NULL OR copies IN (1, 2, 3)", name="ck_media_library_copies"
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("media_library", "copies")
