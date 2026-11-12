"""De haken van personen voor gezichten (bouwontwerp fase 6, Keuze 63 en "De haken"; A0).

Z6 blijft de bron van personen. Drie acties in routers/people.py roepen hier een haak aan, zodat
X2 de gezichten bijwerkt zonder dat bestand open te hoeven:

- person_removed(session, owner_id, node_id, person_id): een persoon van een foto gehaald
  (DELETE /files/{id}/people/{person}), na P.take_off. X2: de ja van die persoon op die node
  wordt nee.
- persons_merged(session, owner_id, source_id, into_id): samenvoegen (POST /people/{id}/merge),
  vóór P.merge, dus vóór de bron weg is. X2: ja en nee van de bron gaan naar het doel, en
  suggest_id ook.
- person_deleted(session, owner_id, person_id): een persoon gewist (DELETE /people/{id}), na de
  flush: de trigger person_bd_face heeft zijn bevestigde gezichten dan al "niet tonen" gemaakt,
  zonder vector. X2: faces.wipe met scope checkpoint.

Alle drie in dezelfde transactie, vóór de commit, onder het slot van de eigenaar (P.lock). Een
fout gaat door: dan mislukt de actie liever dan dat de gezichten half bijgewerkt zijn (zoals
search.hooks.photo_copy). Dit bestand importeert geen pakketten: X2 meldt zich aan. Zonder
aanmelding doen de haken niets, en is de app precies 0.8.0. Ze lezen zelf geen foto's."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

PersonRemoved = Callable[[Any, uuid.UUID, uuid.UUID, uuid.UUID], Awaitable[None]]
PersonsMerged = Callable[[Any, uuid.UUID, uuid.UUID, uuid.UUID], Awaitable[None]]
PersonDeleted = Callable[[Any, uuid.UUID, uuid.UUID], Awaitable[None]]

_removed: list[PersonRemoved] = []
_merged: list[PersonsMerged] = []
_deleted: list[PersonDeleted] = []


def _add(registry: list, fn) -> None:
    if fn not in registry:
        registry.append(fn)


def register_person_removed(fn: PersonRemoved) -> None:
    _add(_removed, fn)


def register_persons_merged(fn: PersonsMerged) -> None:
    _add(_merged, fn)


def register_person_deleted(fn: PersonDeleted) -> None:
    _add(_deleted, fn)


def reset() -> None:
    """Alleen voor tests: alle aanmeldingen weg."""
    for registry in (_removed, _merged, _deleted):
        registry.clear()


async def person_removed(
    session: Any, owner_id: uuid.UUID, node_id: uuid.UUID, person_id: uuid.UUID
) -> None:
    for fn in list(_removed):
        await fn(session, owner_id, node_id, person_id)


async def persons_merged(
    session: Any, owner_id: uuid.UUID, source_id: uuid.UUID, into_id: uuid.UUID
) -> None:
    for fn in list(_merged):
        await fn(session, owner_id, source_id, into_id)


async def person_deleted(session: Any, owner_id: uuid.UUID, person_id: uuid.UUID) -> None:
    for fn in list(_deleted):
        await fn(session, owner_id, person_id)
