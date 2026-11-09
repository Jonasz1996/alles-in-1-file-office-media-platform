"""De tekst van een bestand voor de viewer (bouwontwerp fase 6, §3.4; Z2): de woorden per pagina
met hun plek (de tekstlaag, B20, Keuze 18) en de stand van de analyse.

De woorden gaan met het download-token van de viewer, zoals de pagina's zelf (viewer.page):
het token hoort bij dit bestand en bij de versie die je bekijkt, en de toegang wordt bij elk
verzoek opnieuw gecontroleerd. Een publieke link heeft geen woorden (er is geen route onder
/public: 404).

De woorden komen uit dezelfde render als de pagina (render.pages met words): viewer._render_pages
zet ze in previews/<sha>/pages/words/<n>.json, naast de pagina's en met dezelfde opruimregel.
Staat de pagina al in de cache maar de woorden niet (gerenderd vóór Z2), dan rendert deze route
alleen de woorden (only_words), met dezelfde limiter (page_render_workers): nooit een eigen
proces per verzoek. Een pagina zonder woorden is 404 not_found.

X1 (OCR) zette er twee bronnen bij, met source "ocr":
- een gescande pagina van een PDF: de woorden uit ocr_page (wie de PDF mag lezen, ziet ze);
- een foto: de woorden uit photo_ocr van die node (n is 1), alleen voor de eigenaar; een ander
  (ook via een deling) krijgt 404, zoals voor een foto zonder tekst. Een eerdere versie heeft
  geen woorden.
"""

import contextlib
import json
import os
import shutil
import uuid
import zlib
from pathlib import Path
from typing import Annotated, Any, get_args

import anyio
import anyio.to_thread
import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, Response
from fastapi import Path as PathParam
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis as analysis_steps
from app import errors, photo_hash, render
from app.config import Settings, get_settings
from app.db import Session
from app.models import Analysis, Blob, BlobText, Node, OcrPage
from app.ocr import step as ocr_step
from app.ocr import store as ocr_store
from app.routers import files, viewer
from app.search.types import AnalysisCode, AnalysisOut, PageWord, PageWords, StepState
from app.security import CurrentUser, RateLimiter, active_user, read_download_token

router = APIRouter(prefix="/api/v1", tags=["zoeken"])

# Per gebruiker hooguit 20 per seconde (§3.4)
WORDS_PER_SECOND = 20
words_limiter = RateLimiter(
    WORDS_PER_SECOND,
    1,
    {"message": "Te veel verzoeken; probeer het zo opnieuw", "code": "rate_limited"},
)
NO_WORDS = {"code": "not_found", "message": "Deze pagina heeft geen tekst"}
_WORDS = TypeAdapter(list[PageWord])


def _etag(sha: str, n: int, v: int) -> str:
    """Op (sha, pagina, de versie van de woorden) en de pdf_version van een omgezette PDF."""
    return f'"w{render.WORDS_VERSION}-{sha}-{n}-p{v}"'


async def _load(path: Path) -> list[PageWord] | None:
    """De woorden uit de cache, of None als ze er (in deze versie) niet zijn. Het renderproces
    schreef ze: elk woord gaat door het contract."""

    def load() -> bytes | None:
        try:
            data = path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return None
        with contextlib.suppress(FileNotFoundError):
            os.utime(path)  # net gebruikt: als laatste weg bij het opruimen
        return data

    data = await anyio.to_thread.run_sync(load)
    if data is None:
        return None
    try:
        value = json.loads(data)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("v") != render.WORDS_VERSION:
        return None
    try:
        return _WORDS.validate_python(value.get("words"))
    except ValidationError:
        return None


async def _render_words(
    settings: Settings, sha: str, n: int, count: int, source: Path | None, v: int
) -> None:
    """Alleen de woorden van pagina n (en n+1, n+2, zoals de pagina's), voor een pagina die al
    vóór Z2 in de cache stond. Onder de limiter van de viewer."""
    blobs = viewer._blobs(settings)
    target = viewer.words_path(blobs, sha, n)
    async with viewer.limiter(settings):
        if await anyio.Path(target).is_file():
            return  # een ander verzoek renderde ze net
        wanted = []
        for k in range(n, min(count, n + viewer.AHEAD) + 1):
            if k == n or not await anyio.Path(viewer.words_path(blobs, sha, k)).is_file():
                wanted.append(k)
        pdf = source or blobs.path(sha)
        result, work = await viewer._render(
            settings,
            {
                "mode": "page",
                "source": str(pdf),
                "pages": wanted,
                "width": viewer.WIDTHS[0],
                "only_words": True,
            },
        )
        try:
            done = [int(k) for k in result.get("pages") or [] if int(k) in wanted]
            moves = await viewer.word_moves(blobs, sha, work, done)
            await viewer._place(settings, sha, moves, v)
        finally:
            await anyio.to_thread.run_sync(shutil.rmtree, work, True)
    await viewer.evict(settings, keep={target})


def _ocr_response(words: list[dict], n: int, etag: str, if_none_match: str | None) -> Response:
    """De woorden van OCR (een scan of een foto), met source ocr."""
    headers = {"ETag": etag, "Cache-Control": viewer.PAGE_CACHE}
    if viewer._matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    body = PageWords(page=n, words=_WORDS.validate_python(words), source="ocr").model_dump_json()
    return Response(content=body, media_type="application/json", headers=headers)


async def _photo_words(
    session: AsyncSession, node: Node, user_id: uuid.UUID, sha: str, n: int, inm: str | None
) -> Response:
    """De woorden van een foto (photo_ocr), alleen voor de eigenaar en de huidige inhoud."""
    if node.owner_id != user_id or node.blob_id != sha or n != 1:
        raise errors.fail(404, **NO_WORDS)
    row = await ocr_store.photo_row(session, node.id)
    words = ocr_step.page_words_of(ocr_store.unpack(row.words)) if row is not None else None
    if row is None or not words:
        raise errors.fail(404, **NO_WORDS)
    etag = f'"po{render.WORDS_VERSION}-{node.id}-{zlib.crc32(row.words):08x}"'
    return _ocr_response(words, n, etag, inm)


async def _words(
    session: AsyncSession,
    settings: Settings,
    sha: str,
    name: str,
    n: int,
    if_none_match: str | None,
) -> Response:
    blob = await session.get(Blob, sha)
    kind, source, v = await viewer._source(settings, blob, sha, name)
    if kind != "pdf":
        raise errors.fail(404, **NO_WORDS)
    if blob is not None and source is None and ocr_step.kind_of(blob.mime) == "pdf":
        # Een gescande pagina (X1): de woorden van OCR
        data = await session.scalar(
            sa.select(OcrPage.words).where(OcrPage.blob_sha == sha, OcrPage.page == n)
        )
        found = ocr_step.page_words_of(ocr_store.unpack(data))
        if found:
            await session.commit()
            etag = f'"o{render.WORDS_VERSION}-{sha}-{n}-{zlib.crc32(data):08x}"'
            return _ocr_response(found, n, etag, if_none_match)
    await session.commit()  # niet met een databaseverbinding op het renderproces wachten
    found = await viewer.docinfo(settings, sha, source, v)
    if found is None or not 1 <= n <= found["pages"]:
        raise errors.fail(404, **NO_WORDS)
    etag = _etag(sha, n, v)
    headers = {"ETag": etag, "Cache-Control": viewer.PAGE_CACHE}
    if viewer._matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    path = viewer.words_path(viewer._blobs(settings), sha, n)
    words = await _load(path)
    if words is None:
        await _render_words(settings, sha, n, found["pages"], source, v)
        words = await _load(path)
    if not words:
        raise errors.fail(404, **NO_WORDS)
    body = PageWords(page=n, words=words, source="pdf").model_dump_json()
    return Response(content=body, media_type="application/json", headers=headers)


@router.get("/files/{node_id}/pages/{n}/words", response_model=PageWords)
async def page_words(
    node_id: uuid.UUID,
    n: Annotated[int, PathParam(ge=1)],
    token: str,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """{"page", "words": [{t, x, y, w, h}], "source": "pdf" | "ocr"}; ETag, Cache-Control:
    private; 404 not_found als er geen is; 20 per seconde per gebruiker. Het token komt uit
    GET /document (met de versie die je bekijkt), voor een foto uit POST /download-link."""
    user_id, token_node, version_id = read_download_token(settings, token)
    if token_node != node_id:
        raise errors.fail(403, "wrong_token", "Token hoort bij een ander bestand")
    words_limiter.check(str(user_id))
    # Weg of geblokkeerd: 404, ook met een token dat al uit was
    user = await active_user(session, user_id)
    # Opnieuw: een deling die intussen is ingetrokken, stopt ook een token dat al uit was
    node = await files.get_node(session, user, node_id, "read")
    sha, _ = await viewer._content(session, node, version_id)
    mime = await session.scalar(sa.select(Blob.mime).where(Blob.sha256 == sha))
    if ocr_step.kind_of(mime) == "image":
        return await _photo_words(session, node, user.id, sha, n, if_none_match)
    return await _words(session, settings, sha, node.name, n, if_none_match)


# --- de stand van de analyse ----------------------------------------------------------------

# wait en busy in de tabel zijn voor de API "queued"
_STATES: dict[str, StepState] = {
    "wait": "queued",
    "busy": "queued",
    "done": "done",
    "none": "none",
    "skip": "skip",
    "failed": "failed",
}
_CODES = frozenset(get_args(AnalysisCode))
_SHOWN = (analysis_steps.Step.TEXT, analysis_steps.Step.HASH, analysis_steps.Step.OCR)


def _missing(step: analysis_steps.Step, sha: str, mime: str | None) -> StepState:
    """Een stap zonder rij: queued als hij voor deze inhoud geldt (de upload of de backfill
    zet hem klaar), anders skip. Een stap die niet aangemeld is, geldt nergens: skip. hash komt
    niet hier langs: die staat in blob_hash (photo_hash.analysis_state)."""
    found = analysis_steps.registered().get(step)
    return "queued" if found is not None and found.eligible(sha, mime) else "skip"


@router.get("/files/{node_id}/analysis")
async def analysis(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AnalysisOut:
    """De stand van text, hash en ocr voor wie het bestand mag lezen; truncated: de tekst is
    afgekapt (1.048.576 tekens); code: waarom een stap niets gaf of mislukte (text eerst). De
    OCR van een foto is per node: alleen de eigenaar ziet zijn stand (X1). Draait de rij ocr
    niet (media of PLATFORM_OCR_ENABLED uit), dan is wat nog wacht skip: er komt niets."""
    node = await files.get_node(session, user, node_id, "read")
    if node.kind != "file" or not node.blob_id:
        raise errors.fail(422, "not_a_file", "Alleen een bestand heeft een analyse")
    sha = node.blob_id
    rows = {
        row.step: row
        for row in (
            await session.execute(
                sa.select(Analysis.step, Analysis.state, Analysis.code).where(
                    Analysis.blob_sha == sha, Analysis.step.in_([s.value for s in _SHOWN])
                )
            )
        ).all()
    }
    mime = await session.scalar(sa.select(Blob.mime).where(Blob.sha256 == sha))
    truncated = await session.scalar(sa.select(BlobText.truncated).where(BlobText.blob_sha == sha))
    states: dict[str, Any] = {}
    code = None
    photo = ocr_step.kind_of(mime) == "image"
    for step in _SHOWN:
        row = rows.get(step.value)
        states[step.value] = _STATES.get(row.state, "queued") if row else _missing(step, sha, mime)
        if photo and step == analysis_steps.Step.OCR:
            continue  # de OCR van een foto is per node (hieronder)
        if code is None and row is not None and row.code in _CODES:
            code = row.code
    if photo:
        # X1: de OCR van een foto hoort bij de node van één eigenaar (Keuze 25a); een ander ziet
        # skip, ook als de eigenaar tekst heeft
        states["ocr"], ocr_code = await ocr_step.photo_state(
            session, node, user.id, rows.get(analysis_steps.Step.OCR.value)
        )
        code = code or (ocr_code if ocr_code in _CODES else None)
    if states["ocr"] == "queued" and not ocr_step.runs(settings):
        states["ocr"] = "skip"
    # De pHash rekent photometa, niet de pomp (Keuze 35; Z3): zijn stand staat in blob_hash
    states["hash"] = await photo_hash.analysis_state(session, sha, mime)
    return AnalysisOut(**states, truncated=bool(truncated), code=code)
