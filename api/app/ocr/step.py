"""De stap ocr van de analyse, aan de kant van de app (bouwontwerp fase 6, X1; Keuzes 20, 22–25a).

analysis.register_step(Step.OCR, "ocr", ...) bij de import (main.py importeert dit bestand), met
de haken erbij (register()). De herkenning zelf doet alleen de mediaworker (Op.OCR); de app
stuurt de blob en krijgt woorden terug. De rij ocr draait alleen met media aan en
PLATFORM_OCR_ENABLED (jobs.active_lanes), één opdracht tegelijk, om de beurt per eigenaar
(analysis.pick, Keuze 20).

- **Een PDF** (eligible: altijd). Eerst kijkt de worker welke pagina's minder dan 20 tekens tekst
  hebben (Op.TEXT, de lezer van Z2: scan_pages). Al ≥ HAS_TEXT tekens tekst: skip met code
  ocr_has_text (C11). Dan per gescande pagina Op.OCR, hooguit ocr_max_pages (200) per document
  (daarboven code pages_limit); de woorden van elke pagina gaan meteen naar ocr_page (store.py),
  in een eigen korte transactie. Na RUN_BUDGET_S geeft een PDF zijn beurt weg (geen uitkomst:
  terug op wait, zonder poging) en gaat de volgende keer verder waar hij was: een PDF van 200
  pagina's houdt de rij zo niet een kwartier bezet. Net zo na elke pagina als de rij op pauze
  staat, de app wijkt (analysis.busy_elsewhere), OCR uit staat op /beheer, of de index een batch
  heeft (review golf 4, uitrol F3 en F1). Alle pagina's klaar: hun tekst naar
  blob_text.ocr (analysis.write_text), en search.ready (step ocr) naar wie de PDF ziet. Een pagina
  die te traag is (too_slow) of een kapot beeld geeft (ocr_bad_image), krijgt een lege rij en de
  code; een nieuw verzoek leest hem opnieuw (store.forget_failed_pages).
- **Een foto** (eligible: alleen als iemand erom vraagt, _wanted). Alleen voor de nodes die het
  willen (store.photo_targets: een verzoek met "Tekst herkennen", of een eigenaar met "Tekst in
  foto's herkennen" aan). Eén keer Op.OCR; de tekst gaat naar photo_ocr van elk van die nodes
  (Keuze 25a), en search.ready alleen naar die eigenaar. Niemand wil hem (meer): skip.
- **Geen OCR in de worker** (caps ocr false, een ouder image): none met code ocr_missing, en de job
  ocr.recheck kijkt elke RECHECK_S of caps OCR meldt; dan gaan die rijen terug op wait (Keuze 24).
  Bij het starten kijkt hij ook (on_start).
- **Uit op /beheer** (Setting "ocr" {"on": false}, het deel ocr van /admin/search): de pomp wijkt
  (jobs.Defer), de rijen blijven op wait.
- **De index gaat vóór** (review golf 4, uitrol F1): Op.TEXT en Op.OCR delen de ene plaats
  background van de worker. Heeft de stap text een batch met de worker (batch.index_first), dan
  begint OCR geen nieuwe opdracht (jobs.Defer INDEX_DEFER_S, of midden in een PDF de beurt weg);
  de index wacht zo hooguit de pagina die OCR nog leest.
- **"Tekst in foto's herkennen" aan** (prefs.register_ocr_photos): de job ocr.photos zet de foto's
  van die eigenaar zonder tekst in de rij, per PHOTOS_CHUNK. Bij het starten ook, voor iedereen met
  het aan (een foto met een inhoud die er al was, meldt files.add_ref niet). Uit: de rij leest
  geen foto meer die niemand anders vroeg; de tekst die er is, blijft bij de eigen node.

Wat er nooit in een log of fout komt: de tekst van een pagina of foto, en de naam van een bestand
(alleen de eerste 12 tekens van de sha).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import access, analysis, events, jobs, prefs, render
from app.analysis import Outcome, Step
from app.config import Settings
from app.db import get_engine
from app.extract import MAX_BYTES, batch
from app.media import client
from app.media.wire import Op
from app.media.worker.args import OcrArgs
from app.models import Analysis, Blob, Node, PhotoPref, Setting, now
from app.ocr import store
from app.search import hooks as search_hooks
from app.search.types import EVENT_SEARCH_READY, AdminOcr, SearchReadyEvent, event_data
from app.storage import BlobStore

log = logging.getLogger("platform.ocr")

LANE = "ocr"
PDF = "application/pdf"
# Foto's: wat de viewer als afbeelding toont, zonder pictogrammen
PHOTO_MIMES = frozenset(render.IMAGE_MIMES - {"image/x-icon", "image/vnd.microsoft.icon"})
HAS_TEXT = 200_000  # een PDF met zoveel tekens tekst krijgt geen OCR (ocr_has_text, C11)
RUN_BUDGET_S = 90.0  # daarna geeft een PDF zijn beurt weg (de rest later)
# Eén pagina: kind 1 (hooguit 300 s), twee herkenningen van 60 s en de OSD, met wat ruimte
PAGE_LIMIT_S = 480.0
WORKER_DEFER_S = 30.0  # de worker is druk of weg
INDEX_DEFER_S = 5.0  # de index heeft een batch met de worker: die gaat vóór (uitrol F1)
OFF_DEFER_S = 300.0  # OCR staat uit op /beheer
RECHECK = "ocr.recheck"
RECHECK_S = 600.0
PHOTOS = "ocr.photos"
PHOTOS_CHUNK = 500
SETTING = "ocr"  # Setting: {"on": bool}, de schakelaar van /beheer
SURVEY_KEEP = 32  # zoveel PDF's onthoudt de stap welke pagina's scans zijn
INSTALLED_TTL = 60.0
# De grenzen van OcrArgs die de app meestuurt (child_cpu_s, ocr_s); leeg: de standaard. Tests
# maken ze klein.
LIMITS: dict[str, Any] = {}
FINAL_CODES = ("too_slow", "ocr_bad_image")

# Foto's die nu in de rij mogen (eligible ziet alleen de sha): queue_photo zet ze er even in
_wanted: set[str] = set()


@dataclass(frozen=True)
class Survey:
    scan_pages: tuple[int, ...]
    has_text: bool


_surveys: OrderedDict[str, Survey] = OrderedDict()
_installed: tuple[float, bool] | None = None  # (wanneer, caps.ocr)
_enabled = False  # media en PLATFORM_OCR_ENABLED, gezet bij het starten (on_start)


def kind_of(mime: str | None) -> str | None:
    """pdf, image of None (doet niet mee)."""
    base = (mime or "").split(";")[0].strip().lower()
    if base == PDF:
        return "pdf"
    if base in PHOTO_MIMES:
        return "image"
    return None


def eligible(sha: str, mime: str | None) -> bool:
    kind = kind_of(mime)
    return kind == "pdf" or (kind == "image" and sha in _wanted)


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


# --- aan of uit ----------------------------------------------------------------------------------


async def switched_on(db: AsyncSession) -> bool:
    """De schakelaar van /beheer (standaard aan)."""
    row = await db.get(Setting, SETTING, populate_existing=True)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    return bool(value.get("on", True))


def installed() -> bool | None:
    """Kan de worker OCR? (caps.ocr, onthouden; None: nog niet gevraagd)"""
    return None if _installed is None else _installed[1]


def _set_installed(value: bool) -> None:
    global _installed
    _installed = (time.monotonic(), bool(value))


def runs(settings: Settings) -> bool:
    """Draait de rij ocr in dit proces? Alleen met media en PLATFORM_OCR_ENABLED
    (jobs.active_lanes)."""
    return bool(settings.media_enabled and settings.ocr_enabled)


async def available(db: AsyncSession, settings: Settings) -> bool:
    """Draait OCR nu? media en PLATFORM_OCR_ENABLED, de schakelaar van /beheer, en geen worker
    waarvan bekend is dat hij geen OCR kan (caps). Anders 503 ocr_off op POST."""
    if not runs(settings):
        return False
    if installed() is False:
        return False
    return await switched_on(db)


# --- de wachtrij ---------------------------------------------------------------------------------


async def queue_photo(
    db: AsyncSession, sha: str, owner: uuid.UUID | None, *, first: bool = False
) -> None:
    """Een foto in de rij ocr (ook als hij al gelezen was: een ander wil hem nu), in de
    transactie van de aanroeper. owner: voor wie (om de beurt per eigenaar, Keuze 20)."""
    _wanted.add(sha)
    try:
        await analysis.queue(db, sha, [Step.OCR], owner=owner, first=first, again=True)
    finally:
        _wanted.discard(sha)
    if owner is not None:
        await db.execute(
            update(Analysis)
            .where(
                Analysis.blob_sha == sha,
                Analysis.step == Step.OCR.value,
                Analysis.state == "wait",
            )
            .values(owner_id=owner)
        )


async def queue_pdf(db: AsyncSession, sha: str, owner: uuid.UUID, *, first: bool = True) -> None:
    """Een PDF opnieuw in de rij (een verzoek): de pagina's die mislukten opnieuw, de rest blijft.
    In de transactie van de aanroeper."""
    await store.forget_failed_pages(db, sha)
    _surveys.pop(sha, None)
    await analysis.queue(db, sha, [Step.OCR], owner=owner, first=first, again=True)
    await db.execute(
        update(Analysis)
        .where(Analysis.blob_sha == sha, Analysis.step == Step.OCR.value, Analysis.state == "wait")
        .values(owner_id=owner)
    )


# --- de pomp -------------------------------------------------------------------------------------


def batch_size(settings: Settings) -> int:
    return 1  # één opdracht tegelijk (Keuze 23)


async def run_one(settings: Settings, sha: str) -> Outcome | None:
    return (await run_batch(settings, [sha])).get(sha)


async def run_batch(settings: Settings, shas: list[str]) -> dict[str, Outcome]:
    """De batch van de pomp (één inhoud). Geen uitkomst: terug op wait, zonder poging (de beurt
    weggegeven midden in een PDF)."""
    maker = _maker(settings)
    async with maker() as db:
        if not await switched_on(db):
            raise jobs.Defer(OFF_DEFER_S)
        rows = {
            row.sha256: row
            for row in await db.execute(
                select(Blob.sha256, Blob.mime, Blob.size).where(Blob.sha256.in_(shas))
            )
        }
    try:
        caps = await client.caps()
    except (client.WorkerBusy, client.WorkerDown, TimeoutError, OSError) as exc:
        raise jobs.Defer(WORKER_DEFER_S) from exc
    _set_installed(caps.ocr)
    out: dict[str, Outcome] = {}
    blobs = BlobStore(settings.blob_dir)
    for sha in shas:
        row = rows.get(sha)
        kind = kind_of(row.mime) if row is not None else None
        if row is None or kind is None or row.size > MAX_BYTES:
            out[sha] = Outcome("skip")
            continue
        if not caps.ocr:
            out[sha] = Outcome("none", "ocr_missing")
            continue
        try:
            if kind == "pdf":
                found = await _run_pdf(settings, sha, blobs.path(sha))
            else:
                found = await _run_photo(settings, sha, blobs.path(sha))
        except (client.WorkerBusy, client.WorkerDown, TimeoutError) as exc:
            # De worker is weg of druk: later verder, zonder poging (de pagina's die klaar zijn,
            # blijven in ocr_page)
            log.info("ocr van %s: %s; later verder", sha[:12], type(exc).__name__)
            raise jobs.Defer(WORKER_DEFER_S) from exc
        if found is not None:
            out[sha] = found
    if any(o.code == "ocr_missing" for o in out.values()):
        async with maker() as db:
            await recheck_later(db)
            await db.commit()
    return out


async def _ocr(settings: Settings, path: Path, kind: str, page: int) -> tuple[dict, list[list]]:
    """Eén pagina of foto door Op.OCR: de samenvatting en de woorden (zoals ops/ocr.py)."""
    args = OcrArgs(kind=kind, page=page, max_pixels=settings.preview_max_pixels, **LIMITS)
    words: list[list] = []
    summary: dict = {}
    _index_first()
    with batch.ocr_call():
        async with asyncio.timeout(PAGE_LIMIT_S):
            job, _ = await client.call(Op.OCR, args, source=path)
            async with job:
                async for event in job.events():
                    data = event.get("data")
                    if event.get("ev") != "result" or not isinstance(data, dict):
                        continue
                    if "part" in data:
                        words.extend(w for w in data.get("words") or [] if _word(w))
                    else:
                        summary = data
    return summary, words


def _index_first() -> None:
    """Heeft de index (de stap text) een batch met de worker, dan begint OCR niets nieuws: de
    plaats background is van hen samen, en de index gaat vóór (review golf 4, uitrol F1)."""
    if batch.index_running():
        raise jobs.Defer(INDEX_DEFER_S)


async def _must_yield(settings: Settings) -> bool:
    """Tussen twee pagina's van een PDF: moet de pomp nu wijken? De index heeft een batch (die
    gaat vóór, uitrol F1), de rij staat op pauze, de app wijkt voor omzettingen of OnlyOffice
    (analysis.busy_elsewhere), of OCR staat uit op /beheer (uitrol F3). analysis.pump en
    run_batch kijken dat alleen bij het begin van een batch."""
    if batch.index_running() or LANE in analysis.PAUSED or analysis.busy_elsewhere(settings):
        return True
    async with _maker(settings)() as db:
        return not await switched_on(db)


def _word(w: Any) -> bool:
    return (
        isinstance(w, list)
        and len(w) == 6
        and isinstance(w[0], str)
        and all(isinstance(v, int | float) for v in w[1:])
    )


async def _survey(settings: Settings, sha: str, path: Path) -> Survey | Outcome:
    """Welke pagina's scans zijn (Op.TEXT, de lezer van Z2), en of er al genoeg tekst is."""
    known = _surveys.get(sha)
    if known is not None:
        _surveys.move_to_end(sha)
        return known
    try:
        fd = batch.open_blob(path)
    except OSError:
        log.warning("ocr: de inhoud %s is niet te openen", sha[:12])
        return Outcome("failed")
    try:
        _index_first()
        with batch.ocr_call():
            ran = await batch.via_worker([fd], ["pdf"], max_chars=HAS_TEXT)
    except client.WorkerFailed as exc:
        log.warning("ocr: de tekst van %s lezen mislukte (%s)", sha[:12], exc.args[0])
        return Outcome("failed")
    finally:
        os.close(fd)
    if not ran.found:
        return Outcome("failed", "too_slow", retry=False) if ran.too_slow else Outcome("failed")
    found = ran.found[0]
    if found.state == "failed":
        return Outcome("failed", found.code, retry=found.code != "too_slow")
    survey = Survey(
        tuple(sorted(set(found.scan_pages))),
        bool(found.truncated) or len(found.text) >= HAS_TEXT,
    )
    _surveys[sha] = survey
    while len(_surveys) > SURVEY_KEEP:
        _surveys.popitem(last=False)
    return survey


async def _run_pdf(settings: Settings, sha: str, path: Path) -> Outcome | None:
    survey = await _survey(settings, sha, path)
    if isinstance(survey, Outcome):
        return survey
    if survey.has_text:
        _surveys.pop(sha, None)
        return Outcome("skip", "ocr_has_text")
    limit = max(1, settings.ocr_max_pages)
    pages = survey.scan_pages[:limit]
    code = "pages_limit" if len(survey.scan_pages) > limit else None
    maker = _maker(settings)
    async with maker() as db:
        done = await store.done_pages(db, sha)
    start = time.monotonic()
    read = 0
    for n in pages:
        if n in done:
            continue
        if read and (time.monotonic() - start > RUN_BUDGET_S or await _must_yield(settings)):
            return None  # de beurt weg (na minstens één pagina): de volgende keer verder
        read += 1
        summary, words = await _ocr(settings, path, "pdf", n)
        state, found_code = summary.get("state"), summary.get("code")
        if found_code == "ocr_missing":
            _set_installed(False)
            return Outcome("none", "ocr_missing")
        if state == "failed" and found_code not in FINAL_CODES:
            log.warning(
                "ocr van %s, pagina %d mislukt (%s)", sha[:12], n, summary.get("error") or "?"
            )
            return Outcome("failed")
        if state == "failed":
            code = code or found_code
            placed: list[list] = []
        else:
            placed = store.placed(words, summary)
        async with maker() as db:
            await store.put_page(db, sha, n, float(summary.get("skew") or 0.0), placed)
    _surveys.pop(sha, None)
    async with maker() as db:
        text = await store.pdf_text(db, sha)
        await db.commit()
        await analysis.write_text(db, sha, ocr=text)
    await announce_pdf(settings, sha)
    return Outcome("done" if text else "none", code)


async def _run_photo(settings: Settings, sha: str, path: Path) -> Outcome | None:
    maker = _maker(settings)
    async with maker() as db:
        if not await store.photo_targets(db, sha):
            return Outcome("skip")
    summary, words = await _ocr(settings, path, "image", 1)
    state, code = summary.get("state"), summary.get("code")
    if code == "ocr_missing":
        _set_installed(False)
        return Outcome("none", "ocr_missing")
    if state == "failed":
        if code in FINAL_CODES:
            return Outcome("failed", code, retry=False)
        log.warning("ocr van foto %s mislukt (%s)", sha[:12], summary.get("error") or "?")
        return Outcome("failed")
    placed = store.placed(words, summary)
    text = store.text_of(words)
    async with maker() as db:
        written = await store.put_photo(db, sha, placed, text, float(summary.get("skew") or 0.0))
    for owner, nodes in written.items():
        _publish([owner], nodes)
    if not written:
        return Outcome("skip")
    return Outcome("done" if placed else "none")


# --- melden --------------------------------------------------------------------------------------


def _publish(users: Sequence[uuid.UUID], nodes: Sequence[uuid.UUID]) -> None:
    data = SearchReadyEvent(nodes=list(nodes), step="ocr")
    events.publish(list(users), EVENT_SEARCH_READY, event_data(EVENT_SEARCH_READY, data))


async def announce_pdf(settings: Settings, sha: str) -> None:
    """search.ready (step ocr) naar de eigenaren van de PDF en naar wie hem via een deling ziet,
    elk met alleen de bestanden die hij ziet. Faalt nooit: de tekst staat er al."""
    try:
        async with _maker(settings)() as db:
            mine = Node.blob_id == sha
            rows = (await db.execute(select(Node.owner_id, Node.id).where(mine))).all()
            shared = await access.viewers_of(db, mine)
    except Exception:  # noqa: BLE001 - de melding is bijzaak
        log.warning("ocr: search.ready niet verstuurd", exc_info=True)
        return
    by_user: dict[uuid.UUID, dict[uuid.UUID, None]] = {}
    for owner_id, node_id in rows:
        by_user.setdefault(owner_id, {})[node_id] = None
    for user_id, node_ids in shared.items():
        by_user.setdefault(user_id, {}).update(dict.fromkeys(node_ids))
    for user_id, node_ids in by_user.items():
        _publish([user_id], list(node_ids))


# --- geen OCR in de worker: later opnieuw kijken (Keuze 24) ---------------------------------------


async def recheck_later(db: AsyncSession, seconds: float = RECHECK_S) -> None:
    """De job ocr.recheck over seconds (één tegelijk), in de transactie van de aanroeper."""
    await jobs.enqueue(db, RECHECK, {}, key=RECHECK, run_after=now() + timedelta(seconds=seconds))


async def recheck_job(settings: Settings, payload: dict[str, Any]) -> None:
    """Meldt caps nu OCR, dan gaan de rijen met ocr_missing terug op wait; anders later nog eens."""
    try:
        caps = await client.caps()
    except (client.WorkerBusy, client.WorkerDown, TimeoutError, OSError) as exc:
        raise jobs.Defer(WORKER_DEFER_S) from exc
    _set_installed(caps.ocr)
    async with _maker(settings)() as db:
        if caps.ocr:
            await requeue_missing(db)
        else:
            await jobs.chain(
                db, RECHECK, {}, key=RECHECK, run_after=now() + timedelta(seconds=RECHECK_S)
            )
        await db.commit()


async def requeue_missing(db: AsyncSession) -> int:
    """De rijen none met ocr_missing terug op wait, en de pomp wekken. In de transactie van de
    aanroeper."""
    result = await db.execute(
        update(Analysis)
        .where(
            Analysis.step == Step.OCR.value,
            Analysis.state == "none",
            Analysis.code == "ocr_missing",
        )
        .values(state="wait", code=None, tries=0, updated_at=now())
    )
    if result.rowcount:
        await _wake(db)
    return int(result.rowcount or 0)


async def _wake(db: AsyncSession) -> None:
    """De pomp van de rij ocr nu: een nieuwe job, of de job die al klaarstaat naar voren. Na
    OCR uit wachtte die OFF_DEFER_S (jobs.Defer); "OCR aanzetten" op /beheer mag niet vijf
    minuten op hem wachten (golf 4). In de transactie van de aanroeper."""
    await analysis._wake(db, LANE, now())


# --- "Tekst in foto's herkennen" (photo_pref.ocr_photos) -----------------------------------------


async def ocr_photos_changed(db: AsyncSession, user: Any, value: bool) -> None:
    """prefs.register_ocr_photos: aan zet de job ocr.photos voor deze eigenaar klaar, in de
    transactie van PATCH /me/photo-settings. Uit doet niets: de rij leest geen foto meer die
    niemand vroeg (store.photo_targets), en de tekst die er is, blijft bij de eigen node."""
    if value:
        await queue_photos_of(db, user.id)


async def queue_photos_of(db: AsyncSession, owner: uuid.UUID) -> None:
    await jobs.enqueue(db, PHOTOS, {"user": str(owner), "after": ""}, key=f"{PHOTOS}:{owner}")


async def photos_job(settings: Settings, payload: dict[str, Any]) -> None:
    """Een stuk van de foto's van één eigenaar zonder tekst in de rij (per PHOTOS_CHUNK, één
    korte transactie), zolang hij het aan heeft."""
    owner = uuid.UUID(str(payload["user"]))
    after = str(payload.get("after") or "")
    async with _maker(settings)() as db:
        on = await db.scalar(select(PhotoPref.ocr_photos).where(PhotoPref.user_id == owner))
        if not on:
            return
        shas = await store.wanting_shas(db, owner, PHOTO_MIMES, after, PHOTOS_CHUNK)
        for sha in shas:
            await queue_photo(db, sha, owner)
        if len(shas) == PHOTOS_CHUNK:
            await jobs.chain(
                db, PHOTOS, {"user": str(owner), "after": shas[-1]}, key=f"{PHOTOS}:{owner}"
            )
        await db.commit()


# --- de haken ------------------------------------------------------------------------------------


def on_blob_added(sha: str, owner: uuid.UUID | None) -> None:
    """search.hooks.blob_added: een nieuwe foto van een eigenaar met "Tekst in foto's
    herkennen" aan gaat in de rij (een PDF zet analysis.on_blob_added al klaar)."""
    if owner is None:
        return
    settings = analysis._live_settings()
    if settings is None:
        return
    task = asyncio.get_running_loop().create_task(_new_photo(settings, sha, owner), name="ocr")
    analysis._tasks.add(task)
    task.add_done_callback(analysis._tasks.discard)


async def _new_photo(settings: Settings, sha: str, owner: uuid.UUID) -> None:
    try:
        async with _maker(settings)() as db:
            mime = await db.scalar(select(Blob.mime).where(Blob.sha256 == sha))
            if kind_of(mime) != "image":
                return
            on = await db.scalar(select(PhotoPref.ocr_photos).where(PhotoPref.user_id == owner))
            if not on:
                return
            for attempt in (1, 2):
                try:
                    await queue_photo(db, sha, owner)
                    await db.commit()
                    return
                except Exception:
                    await db.rollback()
                    if attempt == 2:
                        raise
    except Exception:  # noqa: BLE001 - dan bij het starten (on_start)
        log.exception("ocr van foto %s klaarzetten mislukt", sha[:12])


async def on_photo_copy(db: AsyncSession, pairs: Sequence[tuple[uuid.UUID, uuid.UUID]]) -> None:
    """search.hooks.photo_copy (bron, kopie), in de transactie van de kopie. Dezelfde eigenaar:
    de tekst gaat mee. Een andere eigenaar krijgt niets van de bron; heeft hij "Tekst in foto's
    herkennen" aan, dan leest de rij de foto voor hem."""
    ids = [n for pair in pairs for n in pair]
    nodes = {
        row.id: row
        for row in await db.execute(
            select(Node.id, Node.owner_id, Node.blob_id).where(Node.id.in_(ids))
        )
    }
    for source, copy in pairs:
        a, b = nodes.get(source), nodes.get(copy)
        if a is None or b is None or not b.blob_id:
            continue
        if a.owner_id == b.owner_id:
            await store.copy_photo(db, source, copy, b.owner_id)
            continue
        on = await db.scalar(select(PhotoPref.ocr_photos).where(PhotoPref.user_id == b.owner_id))
        mime = await db.scalar(select(Blob.mime).where(Blob.sha256 == b.blob_id))
        if on and kind_of(mime) == "image":
            await queue_photo(db, b.blob_id, b.owner_id)


async def on_start(settings: Settings) -> None:
    """search.hooks.backfill (bij het starten): onthoudt of OCR aan staat, kijkt opnieuw naar
    ocr_missing, en zet de foto's klaar van wie "Tekst in foto's herkennen" aan heeft."""
    global _enabled
    _enabled = runs(settings)
    async with _maker(settings)() as db:
        missing = await db.scalar(
            select(func.count())
            .select_from(Analysis)
            .where(
                Analysis.step == Step.OCR.value,
                Analysis.state == "none",
                Analysis.code == "ocr_missing",
            )
        )
        if missing:
            await recheck_later(db, 0)
        for owner in (
            await db.scalars(select(PhotoPref.user_id).where(PhotoPref.ocr_photos == 1))
        ).all():
            await queue_photos_of(db, owner)
        await db.commit()


# --- /beheer (het deel ocr van /admin/search, Z7) ------------------------------------------------


async def admin_block(db: AsyncSession) -> AdminOcr:
    """installed: de worker kan OCR (caps, onthouden); on: OCR draait (media, de instelling en de
    schakelaar); pages_queued: hoeveel inhouden (PDF's en foto's) op OCR wachten."""
    known = installed()
    if _enabled and (_installed is None or time.monotonic() - _installed[0] > INSTALLED_TTL):
        try:
            _set_installed((await client.caps(limit_s=2.0)).ocr)
            known = installed()
        except (client.WorkerBusy, client.WorkerDown, client.WorkerFailed, TimeoutError, OSError):
            pass
    queued = await db.scalar(
        select(func.count())
        .select_from(Analysis)
        .where(Analysis.step == Step.OCR.value, Analysis.state.in_(analysis.OPEN))
    )
    return AdminOcr(
        installed=bool(known),
        on=_enabled and bool(known) and await switched_on(db),
        pages_queued=int(queued or 0),
    )


async def admin_switch(db: AsyncSession, value: bool) -> None:
    """PATCH /admin/search {"ocr": …}: de schakelaar in Setting "ocr"; aan wekt de pomp. In de
    transactie van de aanroeper."""
    row = await db.get(Setting, SETTING, populate_existing=True)
    if row is None:
        db.add(Setting(key=SETTING, value={"on": bool(value)}))
    else:
        row.value = {**(row.value if isinstance(row.value, dict) else {}), "on": bool(value)}
    await db.flush()
    if value:
        await _wake(db)


async def admin_rebuild(db: AsyncSession) -> None:
    """POST /admin/search/rebuild {"what": "ocr"}: alles opnieuw lezen. De pagina's van PDF's
    weg (blob_text.ocr blijft tot de nieuwe tekst er is), elke rij die niet skip is terug op
    wait. Foto's: alleen voor wie het nu wil (store.photo_targets)."""
    from app.models import OcrPage

    await db.execute(OcrPage.__table__.delete())
    _surveys.clear()
    await db.execute(
        update(Analysis)
        .where(Analysis.step == Step.OCR.value, Analysis.state.in_(("done", "none", "failed")))
        .values(state="wait", tries=0, code=None, queued_at=now(), updated_at=now())
    )
    await _wake(db)


def _register_admin() -> None:
    """Het deel ocr van /admin/search (search.hooks, Z7). Alleen als Z7 het deel kent."""
    if "ocr" not in search_hooks.ADMIN_PARTS:
        return
    part = search_hooks.AdminPart(block=admin_block, switch=admin_switch, rebuild=admin_rebuild)
    search_hooks.register_admin("ocr", part)  # type: ignore[arg-type]


def reset() -> None:
    """Alleen voor tests: wat de stap onthoudt (de scans per PDF, caps, aan of uit)."""
    global _installed, _enabled
    _surveys.clear()
    _wanted.clear()
    _installed = None
    _enabled = False


def register() -> None:
    """De stap, de jobs en de haken aanmelden (één keer; tests die analysis.reset() of
    search.hooks.reset() deden, roepen hem opnieuw aan)."""
    if Step.OCR not in analysis.registered():
        analysis.register_step(Step.OCR, LANE, eligible, run_one, batch=run_batch, size=batch_size)
    jobs.register(RECHECK, recheck_job, lane=LANE, timeout=60.0)
    jobs.register(PHOTOS, photos_job, lane=LANE, timeout=600.0)
    search_hooks.register_blob_added(analysis.on_blob_added)
    search_hooks.register_blob_added(on_blob_added)
    search_hooks.register_photo_copy(on_photo_copy)
    search_hooks.register_backfill(analysis.start_backfill)
    search_hooks.register_backfill(on_start)
    prefs.register_ocr_photos(ocr_photos_changed)
    _register_admin()


register()


# --- de stand per bestand (GET /files/{id}/analysis) en de woorden (pages_words) -----------------


async def photo_state(
    db: AsyncSession, node: Node, user_id: uuid.UUID, row: Any | None
) -> tuple[str, str | None]:
    """De stand van ocr voor een foto, per node (Keuze 25a): alleen de eigenaar ziet iets; een
    ander krijgt skip, ook als de eigenaar tekst heeft. row: de rij van de blob in analysis (met
    state en code), of None. Geeft (stand voor de API, code)."""
    if node.owner_id != user_id:
        return "skip", None
    mine = await store.photo_row(db, node.id)
    state = row.state if row is not None else None
    if mine is not None and mine.words != store.PENDING:
        return ("done" if store.unpack(mine.words) else "none"), None
    if mine is None:
        on = await db.scalar(select(PhotoPref.ocr_photos).where(PhotoPref.user_id == user_id))
        return ("queued" if on and state in analysis.OPEN else "skip"), None
    # Een verzoek dat loopt
    if state == "failed":
        return "failed", row.code
    if state == "none" and row.code == "ocr_missing":
        return "none", row.code
    return "queued", None


def page_words_of(words: list[list] | None) -> list[dict] | None:
    """De woorden uit ocr_page of photo_ocr als [{t, x, y, w, h}] (PageWord), of None."""
    if not words:
        return None
    out = []
    for w in words:
        if isinstance(w, list) and len(w) == 5:
            out.append({"t": str(w[0]), "x": w[1], "y": w[2], "w": w[3], "h": w[4]})
    return out or None
