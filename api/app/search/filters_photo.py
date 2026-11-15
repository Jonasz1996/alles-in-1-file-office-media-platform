"""De filters place, country en camera, de suggesties "plaatsen" en de groep plaatsen bovenaan de
resultaten (bouwontwerp fase 6, Keuzes 26, 38 en 43; Z3).

Alleen eigen, levende bestanden (own_live), in beide motoren: FtsEngine via filters.apply_all,
LikeFallback via het register (de filters die niet van Z1 zijn, op de tabel node), en zo ook op
/photos, de kaart en de regel van een slim album.

- place (een id uit places.sqlite) en country (een code van twee letters, hoofdletters of niet):
  eigen, levende bestanden met een plek in photo_geo met die plaats of dat land, en alleen zolang
  de eigenaar locatie niet op "uit" heeft (photo_pref; ook vóór de job geo.off de rijen wist,
  Keuze 28). Een plek is per node en alleen van de eigenaar (Keuze 25a): een gedeelde foto van een
  ander valt dus nooit onder deze filters. Een code die geen twee letters is, geeft niets.
- camera: eigen, levende bestanden waarvan de inhoud in blob_meta die camera heeft. De vraag
  wordt gevouwen zoals render_meta.camera de camera opslaat ("merk model", zonder stuurtekens,
  witruimte samengevoegd). Hij past op precies die camera, of op een camera die met die woorden
  begint: "canon" vindt "canon eos 5d", niet "canonical"; "canon eos" vindt ook "canon eos r5".
  Een vraag van alleen witruimte geeft niets.
- De suggesties "plaatsen" en de groep plaatsen: de plaatsen van je eigen, levende foto's
  (zolang locatie niet "uit" is) waarvan de naam, of een woord in de naam, met de vraag begint
  (gevouwen); de meeste foto's eerst, hooguit 5. Zonder places.sqlite geen namen en dus geen
  plaatsen; een gast heeft geen eigen foto's. De groep alleen bij een vraag en op de eerste
  pagina, zoals die van personen (Z6).

register() meldt de drie filters (place met de suggesties) en de groep aan; twee keer kan.
app.geo.wire roept het aan bij zijn import (de routers van Z3 importeren die), dus na de import van
app.main is alles aangemeld; een test die filters.reset() of search.hooks.reset() deed, roept
register() opnieuw aan (zoals app.people.search.register()).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import Select, and_, exists, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import photo_rows
from app.config import get_settings
from app.geo import places as geo_places
from app.models import BlobMeta, PhotoGeo, User
from app.search import filters, hooks
from app.search.live import own_live, own_live_geo
from app.search.types import GroupItem, Groups, PlaceRef, SearchParams
from app.text import CONTROL, fold

MAX = 5
# Zoals render_meta.TEXT_CHARS: langer slaat blob_meta een camera niet op
CAMERA_CHARS = 200
COUNTRY = re.compile(r"^[A-Z]{2}$")
# Waar een woord in de naam van een plaats begint ("Den Haag", "'s-Hertogenbosch")
_WORD = re.compile(r"[^\w]+")


def camera_key(text: str) -> str:
    """De camera zoals blob_meta hem heeft: gevouwen, zonder stuurtekens, witruimte samen."""
    return fold(" ".join(CONTROL.sub(" ", text).split()))[:CAMERA_CHARS].strip()


def country_code(text: str) -> str | None:
    code = text.strip().upper()
    return code if COUNTRY.match(code) else None


def _with_place(n: Any, user: User, column: str, value: Any) -> Any:
    """n is een eigen, levend bestand met een plek waarvan column (place_id of country) value is,
    en locatie staat niet op "uit"."""
    cols = getattr(n, "c", n)
    g = PhotoGeo.__table__
    geo = g.c
    # De EXISTS vraagt zelf uit photo_geo, ook als de buitenste vraag dat al doet (de kaart en de
    # plaatsen in photos_map.py). Zonder correlate_except koppelt SQLAlchemy hem aan die buitenste
    # photo_geo en blijft er geen FROM over; node koppelt hij wel, zoals bij de andere filters
    has_place = exists().where(
        geo.node_id == cols.id, own_live_geo(g, user.id), geo[column] == value
    )
    return and_(
        own_live(n, user.id),
        photo_rows.not_off(user.id),
        has_place.correlate_except(g),
    )


def place(sel: Select, n: Any, p: SearchParams, user: User) -> Select:
    if p.place is None:
        return sel
    return sel.where(_with_place(n, user, "place_id", p.place))


def country(sel: Select, n: Any, p: SearchParams, user: User) -> Select:
    if not p.country:
        return sel
    code = country_code(p.country)
    if code is None:
        return sel.where(false())
    return sel.where(_with_place(n, user, "country", code))


def camera(sel: Select, n: Any, p: SearchParams, user: User) -> Select:
    if not p.camera:
        return sel
    key = camera_key(p.camera)
    if not key:
        return sel.where(false())
    cols = getattr(n, "c", n)
    # Precies deze camera, of een die met deze woorden begint (" " is 0x20, "!" 0x21)
    same = or_(
        BlobMeta.camera == key, and_(BlobMeta.camera > key + " ", BlobMeta.camera < key + "!")
    )
    return sel.where(own_live(n, user.id), exists().where(BlobMeta.blob_sha == cols.blob_id, same))


def _matches(name: str, key: str) -> bool:
    folded = fold(name)
    return folded.startswith(key) or any(w.startswith(key) for w in _WORD.split(folded) if w)


async def _found(db: AsyncSession, user: User, q: str) -> list[tuple[geo_places.PlaceRow, int]]:
    """De eigen plaatsen waarvan de naam met q begint, met hun aantal eigen, levende foto's; de
    meeste eerst, hooguit MAX."""
    key = " ".join(fold(CONTROL.sub(" ", q)).split())
    if not key or user.role == "guest":
        return []
    db_places = geo_places.get(get_settings())
    if db_places is None or not await photo_rows.location_on(db, user.id):
        return []
    rows = await db.execute(
        select(PhotoGeo.place_id, func.count())
        .where(own_live_geo(PhotoGeo, user.id), PhotoGeo.place_id.is_not(None))
        .group_by(PhotoGeo.place_id)
    )
    counts = {pid: count for pid, count in rows}
    names = db_places.names(counts) if counts else {}
    found = [(row, counts[pid]) for pid, row in names.items() if _matches(row.name, key)]
    found.sort(key=lambda item: (-item[1], item[0].name, item[0].id))
    return found[:MAX]


async def suggest(db: AsyncSession, user: User, q: str) -> list[PlaceRef]:
    return [
        PlaceRef(id=row.id, name=row.name, admin1=row.admin1, country=row.cc)
        for row, _n in await _found(db, user, q)
    ]


async def groups(db: AsyncSession, user: User, p: SearchParams) -> Groups | None:
    """De groep plaatsen bovenaan: dezelfde als de suggesties, met hun aantal. Alleen bij q en op
    de eerste pagina; None als er geen is."""
    if not p.q.strip() or p.offset or p.after is not None:
        return None
    found = await _found(db, user, p.q)
    if not found:
        return None
    return Groups(places=[GroupItem(id=str(row.id), name=row.name, n=n) for row, n in found])


def register() -> None:
    """De filters (place met de suggesties "plaatsen") en de groep aanmelden; twee keer kan."""
    for name, apply, hint in (("place", place, suggest), ("country", country, None),
                              ("camera", camera, None)):  # fmt: skip
        if name not in filters.FILTERS:
            filters.register(name, apply, suggest=hint)
    hooks.register_groups(groups)


register()
