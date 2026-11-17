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
- admin (Z0b, review contract F4): wat X2 (faces) en X3 (beeld) op /admin/search doen: het blok
  in GET (AdminSearch.faces en .beeld), de schakelaar in PATCH (faces_allowed en beeld) en
  POST rebuild {what: faces | beeld}. Z7 roept admin_block, admin_switch en admin_rebuild aan,
  zodat X2 en X3 app/search/admin.py en routers/admin_search.py niet open hoeven. Zonder
  aanmelding is het blok None (de standaard van AdminSearch), en geven de schakelaar en het
  herbouwen 501 not_built("X2") of not_built("X3"), net als de stubs.
  Z7 (golf 4) zette OCR (X1) erbij, op dezelfde manier: het blok AdminSearch.ocr (AdminOcr), de
  schakelaar ocr in PATCH en POST rebuild {what: ocr}. Zonder aanmelding toont /beheer "OCR niet
  geïnstalleerd" (app/search/admin.py) en geven schakelaar en herbouwen 501 not_built("X1").
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import event
from sqlalchemy.orm import Session as OrmSession

from app.routers.stubs import not_built

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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


# De delen van /admin/search die van X1, X2 en X3 zijn, met hun pakket
ADMIN_PARTS: dict[str, str] = {"faces": "X2", "beeld": "X3", "ocr": "X1"}
# Welk veld van AdminSearchPatch bij welk deel hoort
ADMIN_SWITCHES: dict[str, str] = {"faces_allowed": "faces", "beeld": "beeld", "ocr": "ocr"}


@dataclass(frozen=True)
class AdminPart:
    """De aanmelding van X1 (ocr), X2 (faces) of X3 (beeld) voor /admin/search. block: AdminOcr,
    AdminFaces of AdminBeeld; switch: ocr, faces_allowed of beeld uit de PATCH (X2: een verbod
    zet iedereen uit en wist, Keuze 64; X3: Keuze 82); rebuild: POST rebuild met what ocr, faces
    of beeld."""

    block: Callable[[AsyncSession], Awaitable[Any]]
    switch: Callable[[AsyncSession, bool], Awaitable[None]]
    rebuild: Callable[[AsyncSession], Awaitable[None]]


_admin: dict[str, AdminPart] = {}


def register_admin(name: Literal["faces", "beeld", "ocr"], part: AdminPart) -> None:
    if name not in ADMIN_PARTS:
        raise ValueError(name)
    _admin[name] = part


def admin_registered(name: str) -> bool:
    """Heeft het pakket van dit deel zich aangemeld? (PATCH kijkt dit vóór het iets verandert.)"""
    return name in _admin


async def admin_block(name: str, db: AsyncSession) -> Any:
    """Het blok voor GET /admin/search; None zonder aanmelding."""
    part = _admin.get(name)
    return None if part is None else await part.block(db)


async def admin_switch(name: str, db: AsyncSession, value: bool) -> None:
    """De schakelaar uit PATCH /admin/search; zonder aanmelding 501 not_built."""
    part = _admin.get(name)
    if part is None:
        raise not_built(ADMIN_PARTS[name])
    await part.switch(db, value)


async def admin_rebuild(name: str, db: AsyncSession) -> None:
    """POST /admin/search/rebuild met what faces of beeld; zonder aanmelding 501 not_built."""
    part = _admin.get(name)
    if part is None:
        raise not_built(ADMIN_PARTS[name])
    await part.rebuild(db)


def reset() -> None:
    """Alleen voor tests: alle aanmeldingen weg."""
    for registry in (_blob_added, _photo_copy, _groups, _suggest, _backfill):
        registry.clear()
    _admin.clear()


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
