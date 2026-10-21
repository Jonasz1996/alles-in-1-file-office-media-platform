"""De metadata van bestaande foto's en video's (alsnog) lezen: een achtergrondtaak die in
stukken van 200 blobs door alles met een oude Blob.meta_version loopt.

Stub van P0; P5 bouwt de taak (job-type "photometa", key "photometa:backfill", een keten via
jobs.chain). Tot dan zet het starten niets klaar.
"""

from app.config import Settings

TYPE = "photometa"
KEY = "photometa:backfill"


async def enqueue_backfill(settings: Settings) -> int:
    """Bij het starten: de backfill klaarzetten als er iets te doen is. Geeft het aantal nieuwe
    taken (0 of 1)."""
    return 0
