"""foto's: twee indexen voor een grote achterstand

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-03 09:00:00.000000

Alleen indexen, geen kolommen of tabellen (docs/fotos-indexeren.md):

- ix_job_queue (status, type, run_after, created_at): jobs.claim zoekt per rij de eerste
  wachtende taak van zijn soorten op volgorde van run_after. Zonder deze index las SQLite bij
  elke claim alle wachtende taken en sorteerde ze: gemeten 70 ms per claim met 50.000 previews
  in de rij, met de index 14 ms (de vaste kosten van een claim zijn 6 ms).
- ix_blob_ref_count: files.collect_garbage zoekt na elk nieuw bestand de blobs met ref_count 0,
  onder BlobStore.lock. Zonder index een volle scan: gemeten 9-12 ms bij 50.000 blobs.

De downgrade haalt ze weer weg; daarna is de database gelijk aan 0008, zodat v0.7.0 weer
start: app.cli db-downgrade 0008.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_job_queue", "job", ["status", "type", "run_after", "created_at"])
    op.create_index("ix_blob_ref_count", "blob", ["ref_count"])


def downgrade() -> None:
    op.drop_index("ix_blob_ref_count", table_name="blob")
    op.drop_index("ix_job_queue", table_name="job")
