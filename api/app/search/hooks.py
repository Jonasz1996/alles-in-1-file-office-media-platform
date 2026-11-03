"""De haken van zoeken en foto's in de rest van de app (bouwontwerp fase 6, §3.3 en §3.5). Z0,
frozen; zoals media.hooks.

Dit bestand importeert geen pakketten: Z1, Z2, Z3 en Z6 melden zich aan. Zonder aanmelding doen
de haken niets, en is de app precies 0.7.1.

- blob_added(sha, owner): vanuit files.add_ref, na de commit, voor een nieuwe inhoud (Z2 zet de
  analyse klaar). Geen database: alleen een taak in het geheugen of een job.
- photo_copy(session, pairs): vanuit files.copy en batch.copy, vóór de commit, met (bron, kopie)
  per gekopieerd bestand (Z6: personen gaan mee als de eigenaar gelijk is).
- groups en suggest: extra groepen in de resultaten (Z3 plaatsen, Z6 personen) en suggesties
  voor het zoekveld.
- backfill(settings): bij het starten van de app (main.lifespan), vóór de rijen draaien: elk
  pakket zet zijn achterstand klaar (Z2 tekst, Z3 hash en plaatsen). Faalt nooit.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import event
from sqlalchemy.orm import Session as OrmSession

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger("platform.search")
# In session.info: welke nieuwe inhoud na de commit gemeld wordt (zoals jobs._KICK)
_NEW_BLOBS = "platform.search.new_blobs"

BlobAdded = Callable[[str, uuid.UUID | None], None]
PhotoCopy = Callable[[Any, Sequence[tuple[uuid.UUID, uuid.UUID]]], Awaitable[None]]
GroupsFn = Callable[..., Awaitable[Any]]
SuggestFn = Callable[..., Awaitable[Any]]
Backfill = Callable[["Settings"], Awaitable[None]]

_blob_added: list[BlobAdded] = []
_photo_copy: list[PhotoCopy] = []
_groups: list[GroupsFn] = []
_suggest: list[SuggestFn] = []
_backfill: list[Backfill] = []


def _add(registry: list, fn) -> None:
    if fn not in registry:
        registry.append(fn)


def register_blob_added(fn: BlobAdded) -> None:
    _add(_blob_added, fn)


def register_photo_copy(fn: PhotoCopy) -> None:
    _add(_photo_copy, fn)


def register_groups(fn: GroupsFn) -> None:
    """Z3 plaatsen, Z6 personen."""
    _add(_groups, fn)


def register_suggest(fn: SuggestFn) -> None:
    _add(_suggest, fn)


def register_backfill(fn: Backfill) -> None:
    _add(_backfill, fn)


def groups() -> list[GroupsFn]:
    return list(_groups)


def suggesters() -> list[SuggestFn]:
    return list(_suggest)


def reset() -> None:
    """Alleen voor tests: alle aanmeldingen weg."""
    for registry in (_blob_added, _photo_copy, _groups, _suggest, _backfill):
        registry.clear()


def blob_added(sha: str, owner: uuid.UUID | None) -> None:
    """Een nieuwe inhoud, na de commit. Faalt nooit: een fout komt in het log."""
    for fn in list(_blob_added):
        try:
            fn(sha, owner)
        except Exception:
            log.exception("search.hooks.blob_added")


def blob_added_after_commit(session: Any, sha: str, owner: uuid.UUID | None) -> None:
    """Vanuit files.add_ref, nog in de transactie: na de commit krijgt blob_added deze inhoud.
    Rollt de aanroeper terug, dan gebeurt er niets (de blob is er dan ook niet)."""
    session.info.setdefault(_NEW_BLOBS, []).append((sha, owner))


def pending(session: Any) -> list[tuple[str, uuid.UUID | None]]:
    """Alleen voor tests: wat deze sessie na de commit gaat melden."""
    return list(session.info.get(_NEW_BLOBS, ()))


@event.listens_for(OrmSession, "after_commit")
def _after_commit(session: OrmSession) -> None:
    for sha, owner in session.info.pop(_NEW_BLOBS, ()):
        blob_added(sha, owner)


@event.listens_for(OrmSession, "after_rollback")
def _after_rollback(session: OrmSession) -> None:
    session.info.pop(_NEW_BLOBS, None)


async def photo_copy(session: Any, pairs: Sequence[tuple[uuid.UUID, uuid.UUID]]) -> None:
    """Bestanden gekopieerd (bron, kopie), vóór de commit en in dezelfde transactie. Een fout
    gaat door: dan mislukt de kopie liever dan dat hij half is."""
    if not pairs:
        return
    for fn in list(_photo_copy):
        await fn(session, pairs)


async def backfill(settings: Settings) -> None:
    """De achterstand van elk pakket klaarzetten, bij het starten. Faalt nooit: een fout komt
    in het log en de volgende gaat door."""
    for fn in list(_backfill):
        try:
            await fn(settings)
        except Exception:
            log.exception("search.hooks.backfill")
