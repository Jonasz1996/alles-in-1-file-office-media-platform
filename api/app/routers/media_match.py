"""Koppelen en corrigeren (M8, Keuze 42–44).

GET    /api/v1/media/items/{node_id}/candidates?q=&year=  → [MatchCandidate] (hooguit 10, met
                                             een poster via ons eigen adres)
POST   /api/v1/media/items/{node_id}/match   MatchIn → Item | 409 media_provider_off
                                             | 422 media_provider_auth
                                             | 503 media_provider_unavailable
                                             | 503 media_provider_rate (Retry-After)
DELETE /api/v1/media/items/{node_id}/match   → Item (alleen lokaal)
PATCH  /api/v1/media/items/{node_id}         ItemPatch → Item
POST   /api/v1/media/shows/{show_id}/match   MatchIn → ShowDetail
GET    /api/v1/media/libraries/{library_id}/review?cursor=&limit=  → CardPage ("Nakijken")
GET    /api/v1/media/art/{sha}/{size}.webp?exp=&sig=   ondertekend adres (zonder login)

**Wie.** Lezen wat je ziet (de toegang van M2: 404 als je het item niet ziet). Koppelen, verbeteren
en kandidaten zoeken alleen met schrijfrecht op het bestand (eigenaar of een deling met schrijven):
anders 403 `read_only`. Een serie: schrijfrecht op de bibliotheek of op de map van de serie.

**Wanneer er iets naar buiten gaat.** Alleen als de bibliotheek `online` aan heeft en er een
provider is (een werkende sleutel van TMDB). Anders 409 `media_provider_off`, zonder verkeer.
Tijdens het verkeer naar buiten staat er geen databaseverbinding open (regel 8): eerst lezen en
committen, dan de provider, dan schrijven (lookup.write_item, een eigen korte transactie).

**Ontkoppelen** zet het item terug op lokaal: de titel uit de naam (de scanner leest daarna ook de
NFO weer), geen plaatjes van de provider (ook niet wat nog in de rij stond), wat met de hand
vaststond weer los (Keuze 43: `locked` leeg), en alleen de verborgen vergrendeling `match_state`,
zodat het automatisch opzoeken hem niet meteen weer koppelt (afwijking 3 in m8.md).

Elke wijziging stuurt `media.item` (of `media.library` bij een serie) naar wie de bibliotheek ziet.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import Integer, literal, select
from sqlalchemy.orm import aliased

from app import errors
from app.config import Settings, get_settings
from app.db import Session
from app.media import errors as media_errors
from app.media import items as media_items
from app.media import library as media_library
from app.media import lookup, names, runtime, scanner
from app.media.providers import art, http, match, secrets, tmdb
from app.media.providers.base import (
    Candidate,
    ProviderAuth,
    ProviderRate,
    ProviderUnavailable,
)
from app.media.types import (
    ITEMS_PAGE,
    CardPage,
    Item,
    ItemPatch,
    MatchCandidate,
    MatchIn,
    ShowDetail,
)
from app.models import MediaItem, MediaLibrary, MediaShow, Node
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media", tags=["media"])

Config = Annotated[Settings, Depends(get_settings)]

CANDIDATES = 10  # hooguit zoveel in het venster
CANDIDATE_SECONDS = 8.0  # zoeken plus de posters; wat dan nog niet binnen is, komt zonder poster
POSTER_PARALLEL = 4
EDITABLE = ("title", "sort_title", "year", "overview", "season", "episode")
ITEM_KINDS = ("movie", "episode")


def read_only():
    return errors.fail(403, "read_only", "Je mag dit alleen bekijken")


def not_found():
    return errors.fail(404, "not_found", "Niet gevonden")


def invalid(message: str = "Ongeldige invoer"):
    return errors.fail(422, "invalid_input", message)


async def outbound[T](
    settings: Settings, provider: tmdb.Tmdb, call: Callable[[], Awaitable[T]]
) -> T:
    """Een aanroep naar een provider; de fouten als API-fout uit de catalogus, nooit met de
    tekst van de fout. Een 401 zet de sleutel op ok false (dicht tot "Testen" of een nieuwe),
    maar alleen als het nog dezelfde sleutel is (review golf 5, C7)."""
    try:
        return await call()
    except ProviderRate as e:
        raise media_errors.provider_rate(max(1, round(e.retry_after))) from None
    except ProviderUnavailable:
        raise media_errors.provider_unavailable() from None
    except ProviderAuth:
        async with lookup._maker(settings)() as session:
            await secrets.mark_tmdb(session, settings, False, used=provider.used_key)
        raise media_errors.provider_auth() from None


async def provider_of(session, settings: Settings, library: MediaLibrary) -> tmdb.Tmdb:
    """TMDB voor deze bibliotheek, of 409 media_provider_off (bibliotheek niet online, geen
    sleutel, of de sleutel werkt niet meer). Er gaat dan niets naar buiten."""
    if not library.online or library.kind not in ("movies", "shows"):
        raise media_errors.provider_off()
    await secrets.load(session, settings)
    provider = tmdb.provider()
    if not isinstance(provider, tmdb.Tmdb):
        raise media_errors.provider_off()
    return provider


async def writable_item(session, settings: Settings, user, node_id: uuid.UUID):
    """Een item dat de vrager ziet (anders 404) en mag bewerken (anders 403 read_only)."""
    reader = media_items.Reader(session, settings, user)
    item, node, seen = await media_items.visible_item(reader, node_id)
    if not await media_items.can_write(session, user, node):
        raise read_only()
    return item, node, seen


def fresh(session) -> None:
    """Wat een andere sessie (lookup.write_item) schreef, opnieuw lezen: de media-rijen in deze
    sessie vergeten. Niet expire_all: de gebruiker van het verzoek moet blijven (een async
    sessie laadt niets lui)."""
    for obj in list(session.identity_map.values()):
        if isinstance(obj, (MediaItem, MediaShow, MediaLibrary)):
            session.expire(obj)


async def item_out(session, settings: Settings, user, node_id: uuid.UUID) -> Item:
    """Het Item zoals GET …/items/{id}, plus `locked` en `sort_title` (M0-hotfix in types.py)
    voor het venster Bewerken. `locked` zonder de verborgen vergrendeling."""
    fresh(session)
    found = await media_items.detail(media_items.Reader(session, settings, user), node_id)
    item = await session.get(MediaItem, node_id)
    if item is not None:
        found.locked = [f for f in (item.locked or []) if f in EDITABLE]
        found.sort_title = item.sort_title
    return found


# --- kandidaten -----------------------------------------------------------------------------------


async def posters(settings: Settings, found: list[Candidate], deadline: float) -> dict[str, str]:
    """De posters van de kandidaten in de tijdelijke map (art-tmp, 24 uur), als ons eigen adres
    met exp. Wat niet op tijd binnen is, of geweigerd, heeft geen poster."""
    bodies: dict[str, bytes] = {}
    limiter = anyio.Semaphore(POSTER_PARALLEL)

    async def fetch(c: Candidate) -> None:
        address = tmdb.image_url(settings, "poster", c.poster or "")
        if address is None:
            return
        async with limiter:
            with contextlib.suppress(http.NotFound, ProviderRate, ProviderUnavailable):
                bodies[c.id] = (await http.get_image(tmdb.NAME, address)).body

    with anyio.move_on_after(max(0.0, deadline - time.monotonic())):
        async with anyio.create_task_group() as group:
            for c in found:
                if c.poster:
                    group.start_soon(fetch, c)
    ids = [c.id for c in found if c.id in bodies]
    if not ids:
        return {}
    try:
        shas = await art.store(settings, [bodies[i] for i in ids], tmp=True)
    except art.Refused:
        return {}
    key, exp = art.url_key(settings), art.expiry()
    return {i: art.url(key, sha, "w256", exp) for i, sha in zip(ids, shas, strict=True) if sha}


@router.get("/items/{node_id}/candidates", response_model=list[MatchCandidate])
async def candidates(
    node_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Config,
    q: Annotated[str, Query(max_length=300)] = "",
    year: Annotated[int | None, Query(ge=1800, le=2200)] = None,
):
    """Zoeken voor het venster Koppelen: zonder q de titel en het jaar van het item (bij een
    aflevering die van de serie: een serie koppel je als geheel)."""
    runtime.require(request)
    item, _node, seen = await writable_item(session, settings, user, node_id)
    library = seen.library
    if item.kind not in ITEM_KINDS:
        return []
    provider = await provider_of(session, settings, library)
    kind = "movie" if item.kind == "movie" else "show"
    title, wanted_year = item.title, item.year
    if kind == "show":
        show = await session.get(MediaShow, item.show_id) if item.show_id else None
        title, wanted_year = (show.title, show.year) if show else (item.title, None)
    if q.strip():
        title, wanted_year = " ".join(q.split()), year
    elif year is not None:
        wanted_year = year
    lang = library.language or "nl-NL"
    await session.commit()  # geen verbinding open tijdens het verkeer naar buiten (regel 8)
    # Keuze 39: het venster wacht hooguit 8 s, voor het zoeken (ook zonder jaar) én de
    # posters samen (review golf 5, C6). Is het zoeken dan niet klaar: 503, zoals onbereikbaar
    deadline = time.monotonic() + CANDIDATE_SECONDS
    found: list[Candidate] | None = None
    with anyio.move_on_after(CANDIDATE_SECONDS):
        found = await outbound(
            settings, provider, lambda: lookup.search(provider, kind, title, wanted_year, lang)
        )
    if found is None:
        raise media_errors.provider_unavailable()
    ranked = [s.candidate for s in match.rank(title, wanted_year, found)][:CANDIDATES]
    pictures = await posters(settings, ranked, deadline)
    return [
        MatchCandidate(
            provider="tmdb",
            id=c.id,
            title=c.title,
            year=c.year,
            original_title=c.original_title,
            overview=(c.overview or "")[:300] or None,
            poster=pictures.get(c.id),
        )
        for c in ranked
    ]


# --- koppelen -------------------------------------------------------------------------------------


def tmdb_id(body: MatchIn, kind: str) -> str:
    """Het id van TMDB voor deze soort (movie:N of tv:N), anders 422 invalid_input."""
    if body.provider != "tmdb":
        raise invalid("Films en series koppel je met TMDB")
    try:
        tmdb_kind, number = tmdb.parse_id(body.id)
    except ValueError:
        raise invalid() from None
    if tmdb_kind != ("movie" if kind == "movie" else "tv"):
        raise invalid()
    return f"{tmdb_kind}:{number}"


async def details_of(settings: Settings, provider: tmdb.Tmdb, kind: str, wanted: str, lang: str):
    try:
        return await outbound(settings, provider, lambda: provider.details(kind, wanted, lang))
    except http.NotFound:
        raise not_found() from None


@router.post("/items/{node_id}/match", response_model=Item)
async def match_item(
    node_id: uuid.UUID,
    body: MatchIn,
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Config,
):
    runtime.require(request)
    item, _node, seen = await writable_item(session, settings, user, node_id)
    if item.kind != "movie":
        # Een aflevering koppel je via haar serie (POST …/shows/{id}/match)
        raise invalid("Koppel een aflevering via de serie")
    wanted = tmdb_id(body, "movie")
    library = seen.library
    provider = await provider_of(session, settings, library)
    library_id, lang = library.id, library.language or "nl-NL"
    await session.commit()  # regel 8
    details = await details_of(settings, provider, "movie", wanted, lang)
    decision = match.Decision("matched", lookup.candidate_of(details), 1.0)
    await lookup.write_item(settings, library_id, node_id, decision, details, manual=True)
    return await item_out(session, settings, user, node_id)


@router.post("/shows/{show_id}/match", response_model=ShowDetail)
async def match_show(
    show_id: uuid.UUID,
    body: MatchIn,
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Config,
):
    runtime.require(request)
    show = await session.get(MediaShow, show_id)
    if show is None:
        raise not_found()
    seen = await media_library.get_seen(session, user, show.library_id)
    reader = media_items.Reader(session, settings, user)
    if not await reader.visible_shows([show]):
        raise not_found()
    allowed = seen.can_edit
    if not allowed and show.folder_id is not None:
        folder = await session.get(Node, show.folder_id)
        allowed = folder is not None and await media_items.can_write(session, user, folder)
    if not allowed:
        raise read_only()
    wanted = tmdb_id(body, "show")
    library = seen.library
    provider = await provider_of(session, settings, library)
    library_id, lang = library.id, library.language or "nl-NL"
    await session.commit()  # regel 8
    details = await details_of(settings, provider, "show", wanted, lang)
    decision = match.Decision("matched", lookup.candidate_of(details), 1.0)
    await lookup.write_show(settings, library_id, show_id, decision, details, manual=True)
    # De afleveringen bij de nieuwe serie: de taak van de bibliotheek (één verzoek per seizoen)
    await lookup.queue_library(session, library_id)
    await session.commit()
    fresh(session)
    return await media_items.show_detail(media_items.Reader(session, settings, user), show_id)


# --- ontkoppelen ----------------------------------------------------------------------------------


async def relative_parts(session, node_id: uuid.UUID, root_id: uuid.UUID) -> list[str] | None:
    """Het pad van een bestand onder de map van zijn bibliotheek (zoals de scanner het aan de
    parser geeft), of None als het er niet (meer) onder ligt."""
    up = aliased(Node)
    top = select(
        Node.id.label("id"), Node.parent_id.label("parent_id"), Node.name.label("name"),
        literal(0, Integer).label("depth"),
    ).where(Node.id == node_id)  # fmt: skip
    chain = top.cte("chain", recursive=True)
    chain = chain.union_all(
        select(up.id, up.parent_id, up.name, chain.c.depth + 1).where(
            up.id == chain.c.parent_id, chain.c.id != root_id, chain.c.depth < 64
        )
    )
    rows = (await session.execute(select(chain.c.id, chain.c.name, chain.c.depth))).all()
    rows = sorted(rows, key=lambda r: -r.depth)
    if not rows or rows[0].id != root_id:
        return None
    return [r.name for r in rows[1:]]


def local_fields(parsed: Any, kind: str, stem: str) -> dict[str, Any]:
    """Wat de naam zegt (zoals scanner.describe): de beschrijving zonder provider."""
    title = parsed.title or stem
    if kind == "episode":
        title = parsed.episode_title or parsed.show or parsed.title or stem
    return {"title": title, "year": parsed.year, "ids": dict(parsed.ids)}


@router.delete("/items/{node_id}/match", response_model=Item)
async def unmatch(
    node_id: uuid.UUID, user: CurrentUser, request: Request, session: Session, settings: Config
):
    runtime.require(request)
    item, node, seen = await writable_item(session, settings, user, node_id)
    library = seen.library
    parts = await relative_parts(session, node_id, library.root_id) or [node.name]
    parsed = names.parse(parts, library.kind)
    stem = node.name.rsplit(".", 1)[0] if "." in node.name else node.name
    # Keuze 43: "terug naar alleen lokaal; locked leeg". Ook wat met de hand vaststond, komt
    # weer uit de naam en de NFO (review golf 5, C8)
    item.locked = []
    item.source = "name"  # de naam is weer de bron (anders blijft ze achter de provider)
    keep = {r: e for r, e in (item.art or {}).items() if media_items.is_local_art(e)}
    fields = local_fields(parsed, item.kind, stem)
    media_items.merge(item, {**fields, "match_state": "local", "candidates": []}, "name")
    item.art = keep
    # Niet meteen weer automatisch koppelen: de verborgen vergrendeling (zie de kop)
    item.locked = [lookup.HIDDEN_LOCK]
    item.candidates = []
    item.updated_at = media_items.now()
    # Een plaatje van de oude koppeling dat nog wacht, komt er niet meer bij (C1)
    await lookup.drop_art(session, "item", node_id)
    # De scanner leest de naam en de NFO opnieuw: wat de NFO zegt, komt zo terug
    await scanner.queue_nodes(session, library.id, [node_id])
    await session.commit()
    await lookup.publish_item(session, library.id, [node_id])
    return await item_out(session, settings, user, node_id)


# --- verbeteren -----------------------------------------------------------------------------------


def _sort_of(title: str) -> str:
    return names.sort_title(title) or title.casefold()[:255]


def typed(name: str, value: Any) -> Any:
    """Wat iemand typte, zonder stuurtekens (ESC, BEL, C1): een titel komt ook in een terminal
    terecht (`media-status --sessions`, review golf 5, S1). De samenvatting houdt haar regels."""
    if not isinstance(value, str):
        return value
    return http.printable(value, lines=name == "overview")


@router.patch("/items/{node_id}", response_model=Item)
async def edit(
    node_id: uuid.UUID,
    body: ItemPatch,
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Config,
):
    """De velden zetten en vastzetten (`locked`); `unlock` geeft velden terug aan de scanner en
    de provider. Een lege body verandert niets en geeft het Item met `locked`: zo opent het
    venster Bewerken, zolang GET …/items/{id} van M2 `locked` niet vult (open punt in m8.md)."""
    runtime.require(request)
    item, _node, seen = await writable_item(session, settings, user, node_id)
    given = [k for k in EDITABLE if k in body.model_fields_set]
    unlock = list(dict.fromkeys(body.unlock or []))
    if any(f not in EDITABLE for f in unlock) or set(given) & set(unlock):
        raise invalid()
    if item.kind != "episode" and {"season", "episode"} & set(given):
        raise invalid()
    locked = [f for f in (item.locked or []) if f not in unlock]
    changed = False
    renumbered = False
    for name in given:
        value = media_items._clean(name, typed(name, getattr(body, name)))
        if name == "title" and value is None:
            raise invalid()
        if getattr(item, name) != value:
            renumbered = renumbered or name in ("season", "episode")
            setattr(item, name, value)
            changed = True
        if name not in locked:
            locked.append(name)
    if "sort_title" in given and not item.sort_title:
        # Leeg: weer automatisch, uit de titel
        item.sort_title = _sort_of(item.title)
        locked = [f for f in locked if f != "sort_title"]
    elif "title" in given and "sort_title" not in locked:
        if item.sort_title != _sort_of(item.title):
            item.sort_title = _sort_of(item.title)
            changed = True
    if renumbered and item.match_state in ("matched", "review"):
        item.match_state = "local"  # de aflevering opnieuw opzoeken bij haar nieuwe nummer
    library = seen.library
    refetch: str | None = None
    if unlock:
        # Terug naar wat de naam, de NFO of de provider zegt
        if item.kind == "episode" and item.match_state == "matched":
            item.match_state = "local"  # de taak media.lookup haalt het seizoen opnieuw op
        elif item.kind == "movie" and item.match_state in ("matched", "manual"):
            number = (item.ids or {}).get("tmdb")
            refetch = f"movie:{number}" if str(number or "").isdigit() else None
        if item.match_state == "local" or item.source in media_items.LOCAL:
            await scanner.queue_nodes(session, library.id, [node_id])
    if locked != list(item.locked or []):
        item.locked = locked
        changed = True
    if changed or unlock:
        item.updated_at = media_items.now()
    state, library_id, lang = item.match_state, library.id, library.language or "nl-NL"
    await session.commit()
    if refetch is not None:
        await refresh(session, settings, library, node_id, refetch, state, lang)
    if changed or unlock:
        await lookup.publish_item(session, library_id, [node_id])
    return await item_out(session, settings, user, node_id)


async def refresh(session, settings, library, node_id, wanted, state, lang) -> None:
    """Na unlock op een gekoppelde film: de details nog eens, zodat het veld zijn waarde van de
    provider terugkrijgt. Lukt dat niet (geen sleutel, offline, te traag): het veld houdt zijn
    waarde tot de volgende keer koppelen; het ontgrendelen zelf is gelukt."""
    try:
        provider = await provider_of(session, settings, library)
        await session.commit()  # regel 8
        with anyio.move_on_after(CANDIDATE_SECONDS):
            details = await provider.details("movie", wanted, lang)
            decision = match.Decision("matched", lookup.candidate_of(details), 1.0)
            await lookup.write_item(
                settings, library.id, node_id, decision, details, manual=state == "manual"
            )
    except (errors.HTTPException, http.NotFound, ProviderAuth, ProviderRate, ProviderUnavailable):
        return


# --- Nakijken -------------------------------------------------------------------------------------


@router.get("/libraries/{library_id}/review", response_model=CardPage)
async def review(
    library_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Config,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = ITEMS_PAGE,
):
    """De lijst "Nakijken": series, films en afleveringen met `match_state = review`, op titel.
    Alleen voor wie de bibliotheek mag bewerken (anders leeg: koppelen kan hij toch niet).
    De series alleen op de eerste pagina; de cursor loopt over de items."""
    runtime.require(request)
    seen = await media_library.get_seen(session, user, library_id)
    if not seen.can_edit:
        return CardPage()
    reader = media_items.Reader(session, settings, user)
    out = []
    if cursor is None:
        shows = await session.scalars(
            select(MediaShow)
            .where(MediaShow.library_id == library_id, MediaShow.match_state == "review")
            .order_by(MediaShow.sort_title, MediaShow.id)
            .limit(limit)
        )
        out += await reader.show_cards(await reader.visible_shows(list(shows.all())))
    columns = [(MediaItem.sort_title, False), (MediaItem.node_id, False)]
    query = select(MediaItem).where(
        MediaItem.library_id == library_id,
        MediaItem.present.is_(True),
        MediaItem.match_state == "review",
        MediaItem.kind.in_(ITEM_KINDS),
    )
    if cursor:
        query = query.where(media_items.after(columns, media_items.decode_cursor(cursor, "su")))
    query = query.order_by(MediaItem.sort_title, MediaItem.node_id).limit(limit + 1)
    rows = list((await session.scalars(query)).all())
    more = len(rows) > limit
    rows = rows[:limit]
    following = None
    if more and rows:
        following = media_items.encode_cursor([rows[-1].sort_title, str(rows[-1].node_id)])
    out += await reader.cards(await reader.visible(rows))
    return CardPage(items=out, next=following)


# --- de plaatjes ----------------------------------------------------------------------------------


@router.get("/art/{sha}/{size}.webp")
async def art_file(
    request: Request,
    settings: Config,
    sha: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    size: Annotated[str, Path(pattern=r"^w[0-9]{3,4}$")],
    exp: int = 0,
    sig: Annotated[str, Query(max_length=200)] = "",
):
    """Een plaatje van een provider, alleen met een geldige handtekening (geen login: een <img>
    stuurt geen kop). Zonder exp: art bij een item (geldig zolang het daar staat; de opruimronde
    haalt het weg als niemand er meer naar verwijst)."""
    runtime.require(request)
    if size not in art.SIZES or not art.valid(art.url_key(settings), sha, size, exp, sig):
        raise not_found()
    path = await anyio.to_thread.run_sync(art.find, settings, sha, size)
    if path is None:
        raise not_found()
    age = max(0, min(exp - int(time.time()), art.URL_HOURS * 3600)) if exp else 7 * 24 * 3600
    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": f"private, max-age={age}", "X-Content-Type-Options": "nosniff"},
    )
