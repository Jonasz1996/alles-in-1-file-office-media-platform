"""Personen bij zoeken (bouwontwerp fase 6, Keuze 38 en 43; Z6): het filter person, de
suggesties en de groep personen bovenaan de resultaten.

- Filter person (meer keer is EN): alleen eigen, levende foto's (own_live) waar elk van die
  personen op staat, en alleen eigen personen. Een persoon van een ander geeft dus niets, in
  beide motoren (FtsEngine via filters.apply_all, LikeFallback via het register).
- Suggesties: eigen, niet verborgen personen waarvan de naam met de vraag begint (gevouwen),
  hooguit 5, op naam.
- De groep: dezelfde personen, met het aantal eigen, levende foto's; alleen bij een vraag en op
  de eerste pagina (de router van zoeken vraagt hem via search.hooks.groups()).

register() meldt het filter en de groep aan. app.routers.people importeert deze module, dus na de
import van app.main is alles aangemeld; een test die filters.reset() of search.hooks.reset() deed,
roept register() opnieuw aan (zoals extract.step.register()).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Node, Person, PhotoPerson, User
from app.people.people import copy_people, search_prefix, starts_with
from app.search import filters, hooks
from app.search.live import own_live
from app.search.types import GroupItem, Groups, PersonRef, SearchParams

NAME = "person"
MAX = 5


def apply(sel: Select, n: Any, p: SearchParams, user: User) -> Select:
    """Elke persoon moet op de foto staan; alleen eigen, levende foto's en eigen personen."""
    persons = list(dict.fromkeys(getattr(p, "persons", None) or ()))
    if not persons:
        return sel
    cols = getattr(n, "c", n)
    sel = sel.where(own_live(n, user.id))
    for person_id in persons:
        sel = sel.where(
            exists().where(
                PhotoPerson.node_id == cols.id,
                PhotoPerson.person_id == person_id,
                PhotoPerson.owner_id == user.id,
            )
        )
    return sel


async def suggest(db: AsyncSession, user: User, q: str) -> list[PersonRef]:
    """Eigen, zichtbare personen waarvan de naam met q begint; hooguit 5, op naam."""
    prefix = search_prefix(q)
    if prefix is None or user.role == "guest":
        return []
    rows = await db.execute(
        select(Person.id, Person.name)
        .where(Person.owner_id == user.id, Person.hidden == 0, starts_with(prefix))
        .order_by(Person.search_name, Person.id)
        .limit(MAX)
    )
    return [PersonRef(id=pid, name=name) for pid, name in rows]


async def groups(db: AsyncSession, user: User, p: SearchParams) -> Groups | None:
    """De groep personen bovenaan: dezelfde als de suggesties, met het aantal eigen, levende
    foto's. Alleen bij q en op de eerste pagina; None als er geen is."""
    if not p.q.strip() or p.offset or p.after is not None:
        return None
    found = await suggest(db, user, p.q)
    if not found:
        return None
    ids = [ref.id for ref in found]
    rows = await db.execute(
        select(PhotoPerson.person_id, func.count())
        .join(Node, Node.id == PhotoPerson.node_id)
        .where(
            PhotoPerson.owner_id == user.id,
            PhotoPerson.person_id.in_(ids),
            own_live(Node, user.id),
        )
        .group_by(PhotoPerson.person_id)
    )
    n = {pid: count for pid, count in rows}
    return Groups(people=[GroupItem(id=str(r.id), name=r.name, n=n.get(r.id, 0)) for r in found])


def register() -> None:
    """Het filter (met zijn suggesties), de groep en de haak van een kopie aanmelden; twee keer
    kan."""
    if NAME not in filters.FILTERS:
        filters.register(NAME, apply, suggest=suggest)
    hooks.register_groups(groups)
    hooks.register_photo_copy(copy_people)


register()
