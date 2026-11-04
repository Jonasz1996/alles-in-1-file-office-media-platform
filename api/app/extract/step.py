"""De stap text in de analyse, aan de kant van de app (bouwontwerp fase 6, Z2; Keuze 17 en 20).

analysis.register_step(Step.TEXT, "index", ...) bij de import (main.py importeert dit bestand),
met de haken van Z2 erbij (register()).

- eligible: de inhoud heeft een lezer (app.extract.kind_for op Blob.mime).
- run_batch: per inhoud de soort lezer en het bestand. Groter dan 512 MB: skip (alleen de naam).
  doc, xls en ppt: de PDF die OnlyOffice ervan maakte (fase 4), als soort pdf; zonder die PDF
  skip (after_ready zet hem opnieuw klaar als de PDF er is, de backfill bij het starten ook).
- Met media aan gaat de batch door de mediaworker (batch.via_worker), met per eigenaar een eigen
  kind: een kind dat een kwaadaardig bestand las, krijgt zo nooit de bestanden van een ander
  (de batch zelf is om de beurt per eigenaar, Keuze 20).
- Met media uit (de terugval): het renderproces van de app (previews.render_text, een kind van
  de forkserver of een eigen proces), met batches tot FALLBACK_BATCH, RLIMIT_AS 1 GiB,
  RLIMIT_CPU en nice 19. Dat is geen zandbak: /beheer zegt het (in_sandbox(), Z7 toont het).
- Crasht het kind (een segfault, RLIMIT_CPU, onzin op de pipe), dan halveert run_batch de rest
  zonder uitkomst en probeert beide helften, tot de fout bij één blob ligt. Die wordt failed:
  met code too_slow en zonder nieuwe poging als RLIMIT_CPU hem stopte (S15), anders zonder code
  en met een nieuwe poging later.
- Elke blob met tekst gaat in een eigen korte transactie naar blob_text (analysis.write_text,
  Keuze 20), meteen als hij binnen is. Daarna search.ready naar wie de bestanden ziet.
  scan_pages (pagina's zonder tekst) bewaart X1 (OCR); hier nog niet.

De haken (register()):

- search.hooks.blob_added (files.add_ref, een nieuwe inhoud): analysis.on_blob_added;
- search.hooks.backfill (het starten): analysis.start_backfill, om de beurt per eigenaar;
- previews.register_ready: na de thumbnails van een PDF van OnlyOffice (doc, xls, ppt) de stap
  text opnieuw (after_ready);
- media (de titels): vond mutagen in het kind geen titel, artiest of album (een formaat dat
  mutagen niet kent, zoals mkv), dan die uit de probe van de worker (media_probe.tags), als die
  er al is. De app leest daarvoor alleen de database.

De app leest hier geen enkel document zelf: geen pypdfium2 en geen kantoorlezer
(test_extract_no_pdfium_in_app.py).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import access, analysis, events, jobs, previews
from app.analysis import Outcome, Step
from app.config import Settings
from app.db import get_engine
from app.extract import LEGACY, LEGACY_MIMES, MAX_BYTES, batch, frame, kind_for
from app.extract.frame import Found
from app.extract.normalize import fold_meta
from app.media import client
from app.models import Analysis, Blob, MediaProbe, Node
from app.search import hooks as search_hooks
from app.search.types import EVENT_SEARCH_READY, SearchReadyEvent, event_data
from app.storage import BlobStore

log = logging.getLogger("platform.extract")

LANE = "index"
WORKER_DEFER_S = 30.0  # de worker is druk of weg
# Na een kind dat de app zelf stopte (onzin op de pipe): de worker geeft zijn plek pas vrij als
# hij het kind heeft opgeruimd; zo lang mag de volgende helft op busy wachten
SETTLE_S = 5.0
# De terugval zonder media: hooguit zoveel inhouden per renderproces, en zo lang mag één
# renderproces duren (de klok van de batch, batch_cpu_s, stopt het meestal veel eerder)
FALLBACK_BATCH = 50
FALLBACK_WALL_S = 600.0
TEXT_OUT = "text.out"
# De grenzen van TextArgs die de app meestuurt (blob_cpu_s, batch_cpu_s, child_cpu_s); leeg:
# de standaard van de worker en van previews.render_text. Tests maken ze klein.
LIMITS: dict[str, Any] = {}
# Codes waarbij een nieuwe poging niets verandert: de inhoud zelf is te traag of geweigerd
FINAL_CODES = ("too_slow", "zip_limit", "xml_refused")
# De titels van audio en video uit de probe (zoals app/extract/media.py ze uit mutagen haalt)
MEDIA_FIELDS = ("title", "artist", "album")


@dataclass(frozen=True)
class Item:
    sha: str
    path: Path
    kind: str
    owner: uuid.UUID | None


def eligible(sha: str, mime: str | None) -> bool:
    return kind_for(mime) is not None


def batch_size(settings: Settings) -> int:
    """De worker: text_batch (16); de terugval: FALLBACK_BATCH (Keuze 17)."""
    return settings.text_batch if settings.media_enabled else FALLBACK_BATCH


def in_sandbox(settings: Settings) -> bool:
    """Leest de stap text in de zandbak van de worker? Nee met media uit: dan de melding op
    /beheer (AdminSearch.extract_in_sandbox, Z7)."""
    return bool(settings.media_enabled)


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def run_one(settings: Settings, sha: str) -> Outcome:
    """Eén inhoud (de pomp gebruikt run_batch; dit is voor wie er één wil)."""
    return (await run_batch(settings, [sha])).get(sha) or Outcome("failed")


async def _items(settings: Settings, shas: Sequence[str]) -> tuple[list[Item], dict[str, Outcome]]:
    """Wat er te lezen is, en de uitkomst van wat al vaststaat (skip)."""
    from app.office.convert import pdf_path  # alleen het pad; leest zelf niets

    blobs = BlobStore(settings.blob_dir)
    async with _maker(settings)() as db:
        rows = {
            row.sha256: row
            for row in await db.execute(
                select(Blob.sha256, Blob.mime, Blob.size, Blob.pdf_state, Blob.pdf_version).where(
                    Blob.sha256.in_(list(shas))
                )
            )
        }
        owners = dict(
            (
                await db.execute(
                    select(Analysis.blob_sha, Analysis.owner_id).where(
                        Analysis.blob_sha.in_(list(shas)), Analysis.step == Step.TEXT.value
                    )
                )
            ).all()
        )
    items: list[Item] = []
    done: dict[str, Outcome] = {}
    for sha in shas:
        row = rows.get(sha)
        kind = kind_for(row.mime) if row is not None else None
        if row is None or kind is None or row.size > MAX_BYTES:
            done[sha] = Outcome("skip")
            continue
        path = blobs.path(sha)
        if kind == LEGACY:
            path = pdf_path(blobs, sha)
            if row.pdf_state != "ready" or not row.pdf_version or not path.is_file():
                done[sha] = Outcome("skip")
                continue
            kind = "pdf"
        items.append(Item(sha, path, kind, owners.get(sha)))
    return items, done


async def run_batch(settings: Settings, shas: list[str]) -> dict[str, Outcome]:
    """De batch van de pomp. Een inhoud zonder uitkomst gaat terug op wait."""
    items, out = await _items(settings, shas)
    try:
        if not settings.media_enabled:
            if items:
                await _run_group(settings, items, out, _via_render)
        else:
            # Per eigenaar een eigen kind, in de volgorde van de batch
            order = {item.sha: i for i, item in enumerate(items)}
            by_owner = sorted(items, key=lambda item: (str(item.owner), order[item.sha]))
            for _, group in groupby(by_owner, key=lambda item: item.owner):
                await _run_group(settings, list(group), out, _via_worker_group)
    except (client.WorkerBusy, client.WorkerDown, jobs.JobError) as exc:
        if not any(item.sha in out for item in items):
            raise jobs.Defer(WORKER_DEFER_S) from exc
        log.info("text: %s; de rest van de batch later", type(exc).__name__)
    await announce(settings, [sha for sha, o in out.items() if o.state == "done"])
    return out


# Eén kind voor deze inhouden: (settings, items, out, settle) → (Ran, de items in de volgorde
# van Ran.found). Een runner mag uitkomsten zelf al in out zetten (de terugval doet dat per
# blob, zodat de tekst niet in het geheugen blijft staan); die slaat _run_group dan over.
Run = Callable[
    [Settings, list[Item], dict[str, Outcome], bool], Awaitable[tuple[batch.Ran, list[Item]]]
]


async def _run_group(
    settings: Settings,
    items: list[Item],
    out: dict[str, Outcome],
    run: Run,
    *,
    settle: bool = False,
) -> None:
    ran, ready = await run(settings, items, out, settle)
    if not ready:
        return
    for found in ran.found:
        item = ready[found.index]
        if item.sha not in out:
            out[item.sha] = await _store(settings, item, found)
    rest = ready[len(ran.found) :]
    if not rest or ran.clean:
        return  # klaar, of de klok van de batch was op: de rest komt later
    if len(rest) == 1:
        sha = rest[0].sha
        if ran.too_slow:
            out[sha] = Outcome("failed", "too_slow", retry=False)
        else:
            out[sha] = Outcome("failed")
        log.warning("text: het kind stopte bij %s (rc %s)", sha[:12], ran.rc)
        return
    half = len(rest) // 2
    log.info("text: het kind stopte (rc %s); de rest van %d in twee helften", ran.rc, len(rest))
    await _run_group(settings, rest[:half], out, run, settle=True)
    await _run_group(settings, rest[half:], out, run, settle=True)


# --- met media aan: de worker --------------------------------------------------------------------


async def _via_worker(settings: Settings, fds: list[int], kinds: list[str], *, settle: bool):
    """batch.via_worker; met settle wacht hij even op busy (de plek van het vorige kind)."""
    end = time.monotonic() + (SETTLE_S if settle else 0.0)
    pause = 0.05
    while True:
        try:
            return await batch.via_worker(fds, kinds, max_chars=settings.search_max_text, **LIMITS)
        except client.WorkerBusy:
            if time.monotonic() + pause > end:
                raise
            await asyncio.sleep(pause)
            pause = min(pause * 2, 0.5)


async def _via_worker_group(
    settings: Settings, items: list[Item], out: dict[str, Outcome], settle: bool
) -> tuple[batch.Ran, list[Item]]:
    fds: list[int] = []
    ready: list[Item] = []
    try:
        for item in items:
            try:
                fds.append(batch.open_blob(item.path))
            except OSError:
                log.warning("text: de inhoud %s is niet te openen", item.sha[:12])
                out[item.sha] = Outcome("failed")
                continue
            ready.append(item)
        if not ready:
            return batch.Ran(), []
        ran = await _via_worker(settings, fds, [item.kind for item in ready], settle=settle)
    finally:
        for fd in fds:
            os.close(fd)
    return ran, ready


# --- met media uit: het renderproces van de app (de terugval) ------------------------------------


async def _via_render(
    settings: Settings, items: list[Item], out: dict[str, Outcome], settle: bool
) -> tuple[batch.Ran, list[Item]]:
    """De batch door previews.render_text. De stukken komen in een bestand in een eigen
    werkmap; elke uitkomst gaat meteen naar blob_text (en de tekst dan uit het geheugen)."""
    tmp = previews.root(BlobStore(settings.blob_dir)) / ".tmp"
    await anyio.Path(tmp).mkdir(parents=True, exist_ok=True)
    work = Path(await anyio.to_thread.run_sync(lambda: tempfile.mkdtemp(dir=tmp)))
    ran = batch.Ran()
    try:
        request: dict[str, Any] = {
            "items": [{"path": str(item.path), "kind": item.kind} for item in items],
            "out": str(work / TEXT_OUT),
            "max_chars": settings.search_max_text,
        }
        for name in ("blob_cpu_s", "batch_cpu_s"):
            if LIMITS.get(name) is not None:
                request[name] = LIMITS[name]
        cpu = int(LIMITS.get("child_cpu_s") or previews.TEXT_CPU_S)
        result: dict[str, Any] | None = None
        with anyio.move_on_after(FALLBACK_WALL_S) as scope:
            ran.rc, result = await previews.render_text(settings, request, work, cpu_s=cpu)
        if scope.cancelled_caught:
            ran.clean, ran.too_slow, ran.error = False, True, "wall"
        else:
            ran.too_slow = batch._too_slow(ran.rc)
            ran.clean = ran.rc == 0 and result is not None
            if not ran.clean:
                ran.error = f"rc {ran.rc}"
        await _read_file(settings, work / TEXT_OUT, items, out, ran)
        if ran.clean and (result or {}).get("done") != len(ran.found):
            ran.clean, ran.error = False, "done klopt niet"
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(shutil.rmtree, work, True)
    return ran, items


async def _read_file(
    settings: Settings, path: Path, items: list[Item], out: dict[str, Outcome], ran: batch.Ran
) -> None:
    """De uitkomsten uit het bestand van het renderproces, één voor één: lezen, opslaan, en de
    tekst los laten. Onzin in het bestand: niet netjes (dan halveert _run_group de rest)."""
    try:
        f = await anyio.to_thread.run_sync(open, path, "rb")
    except OSError:
        return  # het proces kwam niet zo ver: geen uitkomsten
    try:
        for index in range(len(items)):
            try:
                found = await anyio.to_thread.run_sync(
                    frame.read_file, f, index, settings.search_max_text
                )
            except frame.Broken as exc:
                ran.clean, ran.error = False, f"bestand: {exc}"
                return
            if found is None:
                return
            out[items[index].sha] = await _store(settings, items[index], found)
            ran.found.append(dataclasses.replace(found, text=""))
        if await anyio.to_thread.run_sync(f.read, 1):
            ran.clean, ran.error = False, "bestand: meer uitkomsten dan blobs"
    finally:
        f.close()


# --- opslaan en melden ---------------------------------------------------------------------------


async def _store(settings: Settings, item: Item, found: Found) -> Outcome:
    meta = found.meta
    if item.kind == "media" and found.state in ("done", "none") and not meta:
        meta = await _probe_meta(settings, item.sha)
    if found.state == "done" or (found.state == "none" and meta):
        async with _maker(settings)() as db:
            await analysis.write_text(
                db, item.sha, body=found.text, meta=meta, truncated=found.truncated
            )
        return Outcome("done", found.code)
    if found.state in ("none", "skip"):
        return Outcome(found.state, found.code)  # type: ignore[arg-type]
    return Outcome("failed", found.code, retry=found.code not in FINAL_CODES)


async def _probe_meta(settings: Settings, sha: str) -> str:
    """Titel, artiest en album uit de opgeslagen probe (media), of ""."""
    async with _maker(settings)() as db:
        tags = await db.scalar(select(MediaProbe.tags).where(MediaProbe.blob_sha == sha))
    if not isinstance(tags, dict):
        return ""
    return fold_meta(*(tags.get(key) for key in MEDIA_FIELDS))


async def announce(settings: Settings, shas: Sequence[str]) -> None:
    """search.ready (step text) naar de eigenaren van bestanden met deze inhoud en naar wie er
    een via een deling kan zien, elk met alleen de bestanden die hij ziet (zoals
    previews.announce). Faalt nooit: de tekst staat er al."""
    if not shas:
        return
    try:
        async with _maker(settings)() as db:
            mine = Node.blob_id.in_(list(shas))
            rows = (await db.execute(select(Node.owner_id, Node.id).where(mine))).all()
            shared = await access.viewers_of(db, mine)
    except Exception:  # noqa: BLE001 - de melding is bijzaak
        log.warning("text: search.ready niet verstuurd", exc_info=True)
        return
    by_user: dict[uuid.UUID, dict[uuid.UUID, None]] = {}
    for owner_id, node_id in rows:
        by_user.setdefault(owner_id, {})[node_id] = None
    for user_id, node_ids in shared.items():
        by_user.setdefault(user_id, {}).update(dict.fromkeys(node_ids))
    for user_id, node_ids in by_user.items():
        data = SearchReadyEvent(nodes=list(node_ids), step="text")
        events.publish([user_id], EVENT_SEARCH_READY, event_data(EVENT_SEARCH_READY, data))


# --- de haken ------------------------------------------------------------------------------------


async def after_ready(
    maker: async_sessionmaker[AsyncSession], sha: str, _dated: bool = False
) -> None:
    """previews.register_ready: na de thumbnails van een PDF van OnlyOffice. Een doc, xls of
    ppt heeft dan een PDF om te lezen: de stap text opnieuw (skip, failed of nog nooit)."""
    async with maker() as db:
        blob = (
            await db.execute(select(Blob.mime, Blob.pdf_state).where(Blob.sha256 == sha))
        ).first()
        if blob is None or blob.pdf_state != "ready":
            return
        if (blob.mime or "").split(";")[0].strip().lower() not in LEGACY_MIMES:
            return
        state = await db.scalar(
            select(Analysis.state).where(Analysis.blob_sha == sha, Analysis.step == Step.TEXT.value)
        )
        if state in ("wait", "busy", "done", "none"):
            return
        await analysis.queue(db, sha, [Step.TEXT], owner=None, again=True)
        await db.commit()


def register() -> None:
    """De stap en de haken aanmelden (één keer; tests die analysis.reset() of
    search.hooks.reset() deden, roepen hem opnieuw aan)."""
    if Step.TEXT not in analysis.registered():
        analysis.register_step(Step.TEXT, LANE, eligible, run_one, batch=run_batch, size=batch_size)
    search_hooks.register_blob_added(analysis.on_blob_added)
    search_hooks.register_backfill(analysis.start_backfill)
    previews.register_ready(after_ready)


register()
