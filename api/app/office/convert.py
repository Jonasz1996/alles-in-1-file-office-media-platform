"""PDF via conversie (Keuze 25). O0-stub; O3 maakt het af.

on_preview_done(session, blob): de haak in previews._store, vóór de commit. Beslist ask of
een taak office.pdf. on_discard(blobs, sha): de haak in previews.discard; haalt de PDF weg.
on_healthy(): health.py roept het aan bij "weer gezond" (de inhaalronde). De stubs doen niets:
zonder O3 blijft alles zoals in fase 3.
"""

from app.models import Blob


async def on_preview_done(session, blob: Blob) -> None:
    return None


async def on_discard(blobs, sha: str) -> None:
    return None


def on_healthy() -> None:
    return None
