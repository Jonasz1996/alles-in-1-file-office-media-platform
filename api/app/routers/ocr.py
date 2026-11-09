"""OCR op verzoek: "Tekst herkennen" (bouwontwerp fase 6, §3.4; X1, Keuze 23).

POST /files/{id}/ocr zet het bestand vooraan in de rij ocr (app/ocr/step.py) en geeft 202.

- Een foto: alleen de eigenaar. De tekst komt in photo_ocr van zijn node (Keuze 25a). Een ander,
  ook met een deling, krijgt 404, net als voor een bestand dat er niet is.
- Een PDF: wie schrijfrecht heeft; wie alleen mag lezen (of niets), krijgt 404. De tekst komt in
  blob_text.ocr: wie de PDF mag lezen, mag ook de tekst zien. De pagina's die mislukten, worden
  opnieuw gelezen.
- Iets anders (een map, een tekstbestand): 422 ocr_unsupported.
- 503 ocr_off: OCR draait niet (media uit, PLATFORM_OCR_ENABLED uit, uit op /beheer, of een
  worker zonder tesseract).
- 409 ocr_running: hij staat al in de rij of wordt nu gelezen. 409 ocr_has_text: een PDF met al
  ≥ 200.000 tekens tekst (C11).
- 429 ocr_limit: per gebruiker hooguit ocr_per_hour (20) verzoeken per uur en ocr_pages_per_day
  (200) pagina's per dag (de laatste 24 uur). Een foto telt als één pagina; een PDF als zijn
  aantal pagina's (uit de preview; hooguit ocr_max_pages, en nooit meer dan op een dag mag: één
  document kan dus altijd). Alleen een verzoek dat doorgaat (202) telt. De tellers staan in het
  geheugen van dit proces, zoals de andere grenzen per gebruiker (security.RateLimiter).
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select

from app import errors, jobs
from app.analysis import OPEN, Step
from app.config import Settings, get_settings
from app.db import Session
from app.models import Analysis, Blob, BlobText
from app.ocr import step as ocr_step
from app.ocr import store as ocr_store
from app.routers import files
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["zoeken"])

HOUR = 3600.0
DAY = 86400.0
NOT_FOUND = {"code": "not_found", "message": "Niet gevonden"}
UNSUPPORTED = {
    "code": "ocr_unsupported",
    "message": "Alleen in een foto of PDF kan tekst worden herkend",
}


class Limits:
    """Per gebruiker de verzoeken van het laatste uur en de pagina's van de laatste 24 uur."""

    def __init__(self) -> None:
        self._requests: dict[str, deque[float]] = {}
        self._pages: dict[str, deque[tuple[float, int]]] = {}

    def _trim(self, user: str, moment: float) -> tuple[deque[float], deque[tuple[float, int]]]:
        requests = self._requests.setdefault(user, deque())
        pages = self._pages.setdefault(user, deque())
        while requests and moment - requests[0] >= HOUR:
            requests.popleft()
        while pages and moment - pages[0][0] >= DAY:
            pages.popleft()
        return requests, pages

    def check(self, user: str, settings: Settings, pages: int) -> None:
        """429 ocr_limit als er dit uur al genoeg verzoeken waren, of als deze pagina's er
        vandaag niet meer bij passen."""
        requests, done = self._trim(user, time.monotonic())
        used = sum(n for _, n in done)
        if len(requests) >= settings.ocr_per_hour or used + pages > settings.ocr_pages_per_day:
            raise errors.fail(
                429,
                "ocr_limit",
                "Je hebt al veel tekst laten herkennen; probeer het later opnieuw",
            )

    def add(self, user: str, pages: int) -> None:
        moment = time.monotonic()
        requests, used = self._trim(user, moment)
        requests.append(moment)
        used.append((moment, max(1, pages)))

    def reset(self) -> None:
        """Alleen voor tests."""
        self._requests.clear()
        self._pages.clear()


limits = Limits()


@router.post("/files/{node_id}/ocr", status_code=202)
async def request_ocr(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Een foto (alleen de eigenaar) of PDF (schrijfrecht) vooraan in de rij ocr: 202."""
    found = await files.get_access(session, user, node_id, "read")
    node = found.node
    if node.kind != "file" or not node.blob_id:
        raise errors.fail(422, **UNSUPPORTED)
    sha = node.blob_id
    blob = await session.get(Blob, sha)
    kind = ocr_step.kind_of(blob.mime if blob is not None else None)
    mine = node.owner_id == user.id
    if kind == "image" and not mine:
        raise errors.fail(404, **NOT_FOUND)
    if kind == "pdf" and not mine and found.permission != "write":
        raise errors.fail(404, **NOT_FOUND)
    if kind is None:
        raise errors.fail(422, **UNSUPPORTED)
    if not await ocr_step.available(session, settings):
        raise errors.fail(503, "ocr_off", "Tekst herkennen staat uit")
    row = (
        await session.execute(
            select(Analysis.state, Analysis.code).where(
                Analysis.blob_sha == sha, Analysis.step == Step.OCR.value
            )
        )
    ).first()
    running = errors.fail(409, "ocr_running", "De tekst wordt al herkend")
    if kind == "pdf":
        if row is not None and row.state in OPEN:
            raise running
        body = await session.scalar(
            select(func.length(BlobText.body)).where(BlobText.blob_sha == sha)
        )
        if (body or 0) >= ocr_step.HAS_TEXT or (row is not None and row.code == "ocr_has_text"):
            raise errors.fail(409, "ocr_has_text", "Dit document heeft al genoeg tekst")
        meta = blob.preview_meta if blob is not None and isinstance(blob.preview_meta, dict) else {}
        count = meta.get("pages") if isinstance(meta.get("pages"), int) else 1
        pages = min(
            max(1, count), max(1, settings.ocr_max_pages), max(1, settings.ocr_pages_per_day)
        )
    else:
        asked = await ocr_store.photo_row(session, node.id)
        if asked is not None and asked.words == ocr_store.PENDING:
            if row is not None and row.state in OPEN:
                raise running
        pages = 1
    limits.check(str(user.id), settings, pages)
    if kind == "pdf":
        await ocr_step.queue_pdf(session, sha, user.id)
    else:
        await ocr_store.request_photo(session, node)
        await ocr_step.queue_photo(session, sha, user.id, first=True)
    await session.commit()
    limits.add(str(user.id), pages)
    jobs.kick([ocr_step.LANE])
    return Response(status_code=202)
