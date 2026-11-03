"""Het register van filters op een zoekvraag (bouwontwerp fase 6, §3.3). Z0 legt de vorm vast;
Z1 is de eigenaar (met de filters type, from/to, tag, library en in), Z3 meldt place, country
en camera aan (filters_photo.py), Z6 person.

Een filter is een functie die een select over node (alias n) smaller maakt op grond van
SearchParams; suggest geeft suggesties voor het zoekveld. Twee keer dezelfde naam is een fout:
één eigenaar per filter.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.search.types import SearchParams

ApplyFilter = Callable[[Select, Any, SearchParams, User], Select]
SuggestFn = Callable[[AsyncSession, User, str], Awaitable[list[Any]]]


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


def reset() -> None:
    """Alleen voor tests: alle filters weg."""
    FILTERS.clear()
