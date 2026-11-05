"""Het register van filters op een zoekvraag (bouwontwerp fase 6, §3.3; Keuze 43). Z0 legt de
vorm vast; Z1 is de eigenaar (met de filters type, from/to, tag, library en in), Z3 meldt place,
country en camera aan (filters_photo.py), Z6 person.

Een filter is een functie die een select over node (alias n) smaller maakt op grond van
SearchParams; suggest geeft suggesties voor het zoekveld. Twee keer dezelfde naam is een fout:
één eigenaar per filter. Een filter voegt alleen voorwaarden toe (where, met EXISTS voor een
andere tabel), nooit een join: zo blijft de vorm van de vraag (Keuze 4 en 4a) die van de motor.

De filters van Z1:

- type: node.ftype; meer keer is OF;
- from en to (één filter, dates): taken_day voor foto en video als die er is, anders
  modified_at, per hele dag in settings.timezone;
- tag: eigen tags; meer keer is EN;
- library: onder de hoofdmap van die mediabibliotheek (via mpath);
- in: onder deze map (via mpath).

Onder is echt eronder: de map van in, en de hoofdmap van library, zijn zelf geen treffer, net als
in LikeFallback en in 0.7.0 (review golf 1, contract N1). De eigen hoofdmap is nooit een treffer
(engine.scoped; contract S2).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Select, and_, case, exists, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import MediaLibrary, Node, NodeTag, Tag, User
from app.photodate import _zone
from app.search.types import SearchParams

ApplyFilter = Callable[[Select, Any, SearchParams, User], Select]
SuggestFn = Callable[[AsyncSession, User, str], Awaitable[list[Any]]]

# Soorten met een opnamedag (taken_day): daarop filtert from/to, als hij er is
DATED = ("image", "video")
_NODE = Node.__table__


@dataclass(frozen=True)
class Filter:
    name: str
    apply: ApplyFilter
    suggest: SuggestFn | None = None


FILTERS: dict[str, Filter] = {}


def register(name: str, apply: ApplyFilter, suggest: SuggestFn | None = None) -> None:
    if name in FILTERS:
        raise ValueError(f"filter {name!r} bestaat al")
    FILTERS[name] = Filter(name, apply, suggest)


def apply_all(sel: Select, n: Any, p: SearchParams, user: User) -> Select:
    """Elk filter uit het register op sel, in de volgorde van aanmelden."""
    for f in FILTERS.values():
        sel = f.apply(sel, n, p, user)
    return sel


# --- De filters van Z1 ----------------------------------------------------------------------


def _type(sel: Select, n: Any, p: SearchParams, user: User) -> Select:
    if not p.types:
        return sel
    types = sorted(set(p.types))
    return sel.where(n.c.ftype == types[0] if len(types) == 1 else n.c.ftype.in_(types))


def _day_start(day: date) -> datetime:
    """Het begin van deze dag in settings.timezone, in UTC (zoals modified_at is opgeslagen)."""
    local = datetime.combine(day, time.min, tzinfo=_zone(get_settings().timezone))
    return local.astimezone(UTC)


def _day_num(day: date) -> int:
    return day.year * 10_000 + day.month * 100 + day.day


def date_where(p: SearchParams, dated: Any, taken_day: Any, modified_at: Any) -> list[Any]:
    """De voorwaarden van from en to: op taken_day waar dated waar is, anders op modified_at.
    Ook voor LikeFallback, met zijn eigen dated (zonder ftype; review golf 1, contract S3)."""
    out = []
    if p.date_from is not None:
        out.append(
            case(
                (dated, taken_day >= _day_num(p.date_from)),
                else_=modified_at >= _day_start(p.date_from),
            )
        )
    if p.date_to is not None:
        out.append(
            case(
                (dated, taken_day <= _day_num(p.date_to)),
                else_=modified_at < _day_start(p.date_to + timedelta(days=1)),
            )
        )
    return out


def _dates(sel: Select, n: Any, p: SearchParams, user: User) -> Select:
    if p.date_from is None and p.date_to is None:
        return sel
    dated = and_(n.c.ftype.in_(DATED), n.c.taken_day.is_not(None))
    return sel.where(*date_where(p, dated, n.c.taken_day, n.c.modified_at))


def _tag(sel: Select, n: Any, p: SearchParams, user: User) -> Select:
    """Elke tag moet erop staan, en alleen eigen tags tellen."""
    for tag_id in dict.fromkeys(p.tags):
        mine = exists().where(Tag.id == tag_id, Tag.owner_id == user.id)
        sel = sel.where(exists().where(NodeTag.node_id == n.c.id, NodeTag.tag_id == tag_id), mine)
    return sel


def _under(n: Any, root: Select) -> Any:
    """n ligt onder de node met dit mpath (root: een select van één mpath), en is hem niet zelf:
    het pad van de map zelf is het begin van het bereik. Elk deel is een losse subquery zonder
    verband met n: SQLite rekent hem één keer uit."""
    start = root.scalar_subquery()
    end = root.with_only_columns(
        literal_column("substr(mpath, 1, length(mpath) - 1) || '0'")
    ).scalar_subquery()
    return and_(n.c.mpath > start, n.c.mpath < end)


def _library(sel: Select, n: Any, p: SearchParams, user: User) -> Select:
    """Wie de bibliotheek niet ziet, krijgt 404 vóór dit filter (like.check_library)."""
    if p.library is None:
        return sel
    root = (
        select(_NODE.c.mpath)
        .select_from(_NODE.join(MediaLibrary.__table__, MediaLibrary.root_id == _NODE.c.id))
        .where(MediaLibrary.id == p.library)
    )
    return sel.where(_under(n, root))


def _in(sel: Select, n: Any, p: SearchParams, user: User) -> Select:
    if p.in_ is None:
        return sel
    return sel.where(_under(n, select(_NODE.c.mpath).where(_NODE.c.id == p.in_)))


BUILTIN: dict[str, ApplyFilter] = {
    "type": _type,
    "dates": _dates,
    "tag": _tag,
    "library": _library,
    "in": _in,
}


def _builtin() -> None:
    for name, apply in BUILTIN.items():
        register(name, apply)


def reset() -> None:
    """Alleen voor tests: alle filters weg, behalve die van Z1 zelf (anders werkt zoeken daarna
    niet meer zoals het hoort)."""
    FILTERS.clear()
    _builtin()


_builtin()
