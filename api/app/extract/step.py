"""De stap text in de analyse, aan de kant van de app (bouwontwerp fase 6, Z2; Keuze 17 en 20).

analysis.register_step(Step.TEXT, "index", ...) bij de import (main.py importeert dit bestand).

- eligible: de inhoud heeft een lezer (app.extract.kind_for op Blob.mime).
- run_batch: per inhoud de soort lezer en het bestand. Groter dan 512 MB: skip (alleen de naam).
  doc, xls en ppt: de PDF die OnlyOffice ervan maakte (fase 4), als soort pdf; zonder die PDF
  skip (de haak na de omzetting zet hem opnieuw klaar, Z2 stap 2).
- Met media aan gaat de batch door de mediaworker (batch.via_worker), met per eigenaar een eigen
  kind: een kind dat een kwaadaardig bestand las, krijgt zo nooit de bestanden van een ander
  (de batch zelf is om de beurt per eigenaar, Keuze 20). Met media uit: later (Z2 stap 2 bouwt
  de terugval via previews.render).
- Crasht het kind (een segfault, RLIMIT_CPU, onzin op de pipe), dan halveert run_batch de rest
  zonder uitkomst en probeert beide helften, tot de fout bij één blob ligt. Die wordt failed:
  met code too_slow en zonder nieuwe poging als RLIMIT_CPU hem stopte (S15), anders zonder code
  en met een nieuwe poging later.
- Elke blob met tekst gaat in een eigen korte transactie naar blob_text (analysis.write_text,
  Keuze 20). scan_pages (pagina's zonder tekst) bewaart X1 (OCR); hier nog niet.

De app leest hier geen enkel document zelf: geen pypdfium2 en geen kantoorlezer
(test_extract_no_pdfium_in_app.py).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import analysis, jobs
from app.analysis import Outcome, Step
from app.config import Settings
from app.db import get_engine
from app.extract import LEGACY, MAX_BYTES, batch, kind_for
from app.extract.frame import Found
from app.media import client
from app.models import Analysis, Blob
from app.storage import BlobStore

log = logging.getLogger("platform.extract")

LANE = "index"
WORKER_DEFER_S = 30.0  # de worker is druk of weg
# Na een kind dat de app zelf stopte (onzin op de pipe): de worker geeft zijn plek pas vrij als
# hij het kind heeft opgeruimd; zo lang mag de volgende helft op busy wachten
SETTLE_S = 5.0
MEDIA_OFF_DEFER_S = 600.0  # zonder media: nog geen terugval (Z2 stap 2)
# De grenzen van TextArgs die de app meestuurt; leeg: de standaard van de worker. Tests maken
# ze klein.
LIMITS: dict[str, Any] = {}
# Codes waarbij een nieuwe poging niets verandert: de inhoud zelf is te traag of geweigerd
FINAL_CODES = ("too_slow", "zip_limit", "xml_refused")


@dataclass(frozen=True)
class Item:
    sha: str
    path: Path
    kind: str
    owner: uuid.UUID | None


def eligible(sha: str, mime: str | None) -> bool:
    return kind_for(mime) is not None


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
    if not settings.media_enabled:
        raise jobs.Defer(MEDIA_OFF_DEFER_S)
    items, out = await _items(settings, shas)
    # Per eigenaar een eigen kind, in de volgorde van de batch
    order = {item.sha: i for i, item in enumerate(items)}
    by_owner = sorted(items, key=lambda item: (str(item.owner), order[item.sha]))
    groups = [list(group) for _, group in groupby(by_owner, key=lambda item: item.owner)]
    try:
        for group in groups:
            await _run_group(settings, group, out)
    except (client.WorkerBusy, client.WorkerDown) as exc:
        if not any(item.sha in out for item in items):
            raise jobs.Defer(WORKER_DEFER_S) from exc
        log.info("text: de worker is %s; de rest van de batch later", type(exc).__name__)
    return out


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


async def _run_group(
    settings: Settings, items: list[Item], out: dict[str, Outcome], *, settle: bool = False
) -> None:
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
            return
        ran = await _via_worker(settings, fds, [item.kind for item in ready], settle=settle)
    finally:
        for fd in fds:
            os.close(fd)
    for found in ran.found:
        item = ready[found.index]
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
    await _run_group(settings, rest[:half], out, settle=True)
    await _run_group(settings, rest[half:], out, settle=True)


async def _store(settings: Settings, item: Item, found: Found) -> Outcome:
    if found.state == "done":
        async with _maker(settings)() as db:
            await analysis.write_text(
                db, item.sha, body=found.text, meta=found.meta, truncated=found.truncated
            )
        return Outcome("done", found.code)
    if found.state in ("none", "skip"):
        return Outcome(found.state, found.code)  # type: ignore[arg-type]
    return Outcome("failed", found.code, retry=found.code not in FINAL_CODES)


def register() -> None:
    """De stap aanmelden (één keer; tests die analysis.reset() deden, roepen hem opnieuw aan)."""
    if Step.TEXT not in analysis.registered():
        analysis.register_step(
            Step.TEXT, LANE, eligible, run_one, batch=run_batch, size=lambda s: s.text_batch
        )


register()
