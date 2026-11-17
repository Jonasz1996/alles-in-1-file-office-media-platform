"""Suggesties voor het zoekveld (bouwontwerp fase 6, Keuze 46; §3.4 GET /search/suggest). Z1.

    found = await suggest(db, user, q)    # Suggestions: elk ≤ 5

- names: bestanden en mappen die je mag zien, met elk woord van de vraag in de naam. Dezelfde
  bereiken als zoeken (Keuze 14: levend, geen album) en via fts_name (trigram), zoals de
  stukken van namen in engine.names_select. Eerst de namen die met het eerste woord beginnen
  (het voorvoegsel), dan de rest; daarbinnen in de volgorde van de index, zonder te sorteren,
  zodat een veelvoorkomend stuk ("ver") niet alle namen langs hoeft. Alleen als de vraag een
  woord van ≥ 3 tekens heeft (parse().names): zonder trigram is het een scan over alle nodes van
  de wortels, bij elke toets. Bij één of twee tekens zijn er dus alleen tags, albums, personen
  en plaatsen. Onder een tijdsgrens (deadline() van Keuze 9): hooguit NAMES_MS voor de namen,
  en nooit later dan SUGGEST_MS na het begin van suggest() min wat er na de namen nog komt
  (AFTER_NAMES_MS: de tweede controle, tags, albums). Was er vóór de namen al veel tijd nodig
  (de gedeelde wortels), dan krijgen de namen dus minder. Bij de grens geen namen, of alleen die
  met het voorvoegsel; daarna de tweede controle (Keuze 15).
- tags: je eigen tags met elk woord in de naam (gevouwen); eerst wie met het eerste woord
  begint. Hooguit 500 per gebruiker: in Python vergeleken, zoals routers/search.check_free.
- albums: je eigen albums en de albums die met jou (of een groep van jou) gedeeld zijn, zoals
  GET /albums, op naam en zo gesorteerd. De gedeelde wortels (access.shared_roots) leest
  suggest één keer, voor de bereiken van de namen en voor de albums; de eigen albums langs
  (parent_id, kind), want een album staat buiten de boom (engine.top_level).
- people en places: van de haken van Z6 (personen) en Z3 (plaatsen). Een haak meldt zich aan
  met hooks.register_suggest, of als suggest van een filter (filters.register). Elke haak krijgt
  (db, user, q) en geeft een Suggestions of een lijst met PersonRef en PlaceRef; alleen people
  en places tellen, elk hooguit 5, op volgorde van de haken, zonder dubbele. Een haak die faalt,
  geeft niets: één logregel met de naam van de haak, nooit de vraag.

De router laat er één tegelijk per gebruiker door (limits.admit, "suggest": 20 per seconde).
LikeFallback (Keuze 10) geeft geen suggesties.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import access
from app.models import Node, Tag, User
from app.routers.files import TagRef
from app.search import engine as engines
from app.search import filters, hooks
from app.search.access import search_ranges, second_check
from app.search.query import Parsed, parse
from app.search.types import AlbumRef, NameRef, PersonRef, PlaceRef, SearchParams, Suggestions
from app.text import fold

log = logging.getLogger("platform.search")
MAX = 5
# De tijdsgrens voor de namen samen (ms): een suggestie die later komt, helpt niet meer (wie
# typt, typt door), en de poort van §4.4 is p95 ≤ 50 ms voor het hele verzoek
NAMES_MS = 40.0
# Van die 50 ms gaan er ±11-13 naar inloggen, HTTP en JSON (buiten suggest()); SUGGEST_MS is wat
# suggest() zelf mag kosten, AFTER_NAMES_MS wat na de namen nog komt (gemeten op de boom van
# §4.4: de tweede controle tot 8 ms bij p95, tags en albums samen ±5 ms). Met 40 kwam "alles"
# (30 gedeelde hoofdmappen) op een p95 van 49,5
SUGGEST_MS = 35.0
AFTER_NAMES_MS = 10.0


def _rank(name: str, words: Sequence[str]) -> int | None:
    """None: niet elk woord staat in de (gevouwen) naam; 0: hij begint met het eerste woord."""
    if not all(w in name for w in words):
        return None
    return 0 if name.startswith(words[0]) else 1


def _best(items: Iterable[tuple[str, Any]], words: Sequence[str]) -> list[Any]:
    """De eerste MAX van (naam, ding) waar elk woord in de naam staat, het voorvoegsel eerst,
    dan op naam."""
    found = []
    for name, item in items:
        folded = fold(name)
        rank = _rank(folded, words)
        if rank is not None:
            found.append((rank, folded, item))
    found.sort(key=lambda f: (f[0], f[1]))
    return [item for _, _, item in found[:MAX]]


def _like_prefix(word: str) -> str:
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


async def names(
    db: AsyncSession,
    user: User,
    parsed: Parsed,
    q: str,
    shared: Sequence[uuid.UUID] | None = None,
    begin: float | None = None,
) -> list[NameRef]:
    """begin: het begin van suggest() (time.perf_counter); de namen eindigen uiterlijk
    SUGGEST_MS - AFTER_NAMES_MS daarna."""
    if parsed.names is None:
        return []
    roots = await search_ranges(db, user, shared)
    if not roots.ranges:
        return []
    scope = engines.Scope(user, roots, SearchParams(q=q))
    conn = await db.connection()
    start = time.perf_counter()
    end = start + NAMES_MS / 1000
    if begin is not None:
        end = min(end, begin + (SUGGEST_MS - AFTER_NAMES_MS) / 1000)

    def rest() -> float:
        return (end - time.perf_counter()) * 1000

    prefix = engines.names_select(parsed, scope, MAX, 0).where(
        text("n.search_name LIKE :prefix ESCAPE '\\'").bindparams(
            prefix=_like_prefix(parsed.words[0])
        )
    )
    first = await engines.run(conn, prefix, rest(), "names")
    rows = list(first.rows)
    if not first.hit and len(rows) < MAX and rest() > 1:
        sel = engines.names_select(parsed, scope, MAX - len(rows), 0, [r.num for r in rows])
        rows += (await engines.run(conn, sel, rest(), "names")).rows
    if not rows:
        return []
    checked = await second_check(db, user, [(r.id, r.owner_id) for r in rows])
    keep = [r.id for r in rows if r.id in checked.permissions]
    if not keep:
        return []
    loaded = await db.execute(select(Node.id, Node.name, Node.kind).where(Node.id.in_(keep)))
    by_id = {node_id: NameRef(id=node_id, name=name, kind=kind) for node_id, name, kind in loaded}
    return [by_id[i] for i in keep if i in by_id]


async def tags(db: AsyncSession, user: User, words: Sequence[str]) -> list[TagRef]:
    own = await db.execute(select(Tag.id, Tag.name, Tag.color).where(Tag.owner_id == user.id))
    return _best(
        ((name, TagRef(id=tag_id, name=name, color=color)) for tag_id, name, color in own), words
    )


def own_albums(user_id: uuid.UUID) -> Select:
    """(id, naam) van de eigen albums, langs (parent_id, kind): een album staat buiten de boom
    (test_search_plans.py)."""
    n = engines.top_level()
    return select(n.c.id, n.c.name).where(
        n.c.parent_id.is_(None), n.c.kind == "album", n.c.owner_id == user_id
    )


async def albums(
    db: AsyncSession,
    user: User,
    words: Sequence[str],
    shared: Sequence[uuid.UUID] | None = None,
) -> list[AlbumRef]:
    """Zoals GET /albums: je eigen (niet als gast) en wat nu met je gedeeld is. shared: de ids
    van access.shared_roots, als suggest() ze al las."""
    found: list[tuple[Any, str]] = []
    if user.role != "guest":
        found += list(await db.execute(own_albums(user.id)))
    if shared is None:
        shared = [r.node_id for r in await access.shared_roots(db, user.id)]
    if shared:
        others = select(Node.id, Node.name).where(Node.id.in_(shared), Node.kind == "album")
        found += list(await db.execute(others))
    return _best(((name, AlbumRef(id=i, name=name)) for i, name in found), words)


def _refs(found: Any) -> list[Any]:
    if isinstance(found, Suggestions):
        return [*found.people, *found.places]
    return list(found or [])


async def from_hooks(db: AsyncSession, user: User, q: str) -> tuple[list, list]:
    """Personen en plaatsen van Z6 en Z3 (tot dan leeg)."""
    fns = hooks.suggesters() + [f.suggest for f in filters.FILTERS.values() if f.suggest]
    people: dict[Any, PersonRef] = {}
    places: dict[Any, PlaceRef] = {}
    for fn in fns:
        try:
            found = _refs(await fn(db, user, q))
        except Exception:
            log.warning("search_suggest_hook_failed %s", getattr(fn, "__qualname__", "?"))
            continue
        for item in found:
            if isinstance(item, PersonRef) and len(people) < MAX:
                people.setdefault(item.id, item)
            elif isinstance(item, PlaceRef) and len(places) < MAX:
                places.setdefault(item.id, item)
    return list(people.values()), list(places.values())


async def suggest(db: AsyncSession, user: User, q: str) -> Suggestions:
    """QueryTooLong als zoeken (de router geeft 400 search_query_too_long)."""
    begin = time.perf_counter()
    parsed = parse(q)
    if parsed is None:
        return Suggestions()
    people, places = await from_hooks(db, user, q)
    # Eén keer voor de namen en de albums (§4.4: suggesties p95 ≤ 50 ms)
    shared = [r.node_id for r in await access.shared_roots(db, user.id)]
    return Suggestions(
        names=await names(db, user, parsed, q, shared, begin),
        people=people,
        places=places,
        tags=await tags(db, user, parsed.words),
        albums=await albums(db, user, parsed.words, shared),
    )
