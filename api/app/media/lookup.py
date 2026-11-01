"""Online opzoeken (Keuze 39–44, M8): de taken `media.lookup` en `media.art` in de rij
`media_net` (1 worker), en de opruimronde van de art.

**Wat wordt opgezocht.** Alleen in een bibliotheek met `online` aan, en alleen als er een
provider is: TMDB met een werkende sleutel voor films en series, MusicBrainz als het aan staat op
/beheer voor muziek. Zonder provider gebeurt er niets en gaat er niets naar buiten: alles blijft
lokaal (de standaard: geen sleutel).
- films: items `match_state = local`;
- series: eerst de serie (`media_show`), daarna per seizoen de afleveringen, één verzoek per
  seizoen (`/tv/{id}/season/{n}`);
- muziek: albums (`media_album`), met de MBID uit de tags of door te zoeken; de hoes uit de CAA.
Een item of serie met de verborgen vergrendeling `match_state` (na "Ontkoppelen") slaat het
opzoeken over.

**Hoe een taak loopt.** Eén taak per bibliotheek (key `media.lookup:<id>`): hooguit `BATCH`
dingen per keer, daarna zet hij zijn opvolger klaar (`jobs.chain`). Tijdens het verkeer naar
buiten staat er geen databaseverbinding open (regel 8): eerst lezen, dan de provider, dan per
ding een korte transactie om te schrijven. Een 429 of geen netwerk: `jobs.Defer` (de poging telt
niet); de stroomonderbreker van http.py houdt het tempo laag. Een 401 van TMDB: de sleutel gaat
op `ok: false` (secrets.mark_tmdb) en TMDB gaat dicht tot er een nieuwe is.

**Wanneer.** De scanner (M2) weet niet van M8. Daarom: (1) een haak op `hooks.touched` (een
bestand erbij of veranderd) zet na `delay()` seconden (minstens twee tikken van de scanner) een
taak klaar voor elke online bibliotheek met werk of met een scan die klaarstaat of loopt; loopt
de scan nog, dan wacht de taak (`WAIT_SCAN`) en kijkt opnieuw; (2) de
housekeeping kijkt elke 5 minuten (ook na "Opnieuw scannen", een herstart of een nieuwe
sleutel). Zie m8.md: een duw van de scanner na een scan zou (1) overbodig maken; die haak is
van M2.

**Art** (`media.art`, per rol): het plaatje via http.py, omzetten in het renderproces
(art.store), en `{"art": sha, "url": …}` in `art` van het item, de serie of het album. Een
lokale rol (een poster naast de film) gaat voor: die wordt niet vervangen.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import events, jobs
from app.config import Settings
from app.db import get_engine
from app.media import hooks, runtime
from app.media import items as media_items
from app.media.providers import art, caa, http, match, musicbrainz, secrets, tmdb
from app.media.providers.base import (
    Candidate,
    Details,
    EpisodeDetails,
    MetadataProvider,
    ProviderAuth,
    ProviderRate,
    ProviderUnavailable,
)
from app.media.types import EVENT_ITEM, EVENT_LIBRARY, ItemEvent, LibraryEvent
from app.models import Job, MediaAlbum, MediaItem, MediaLibrary, MediaShow

log = logging.getLogger("platform.media.lookup")

JOB = "media.lookup"
ART_JOB = "media.art"
LANE = "media_net"
TIMEOUT = 300.0
ART_TIMEOUT = 120.0
BATCH = 20
SWEEP = "media.lookup.sweep"
SWEEP_EVERY = 300.0
CLEAN = "media.art.cleanup"
SOON = 10.0  # seconden na een wijziging aan bestanden
WAIT_SCAN = 15.0  # zolang de scan van de bibliotheek nog loopt
OFFLINE = 300.0  # zo lang als de stroomonderbreker dicht blijft
EPISODE_ROWS = 500
PENDING_ROWS = 200
FALLBACK_LANG = tmdb.FALLBACK_LANG
HIDDEN_LOCK = "match_state"
ONLINE_KINDS = ("movies", "shows", "music")
# Eén ding dat steeds faalt terwijl de provider zelf werkt (review golf 5, C2): na zoveel
# pogingen naar Nakijken; tot dan komt de volgende ronde pas na UNIT_RETRY seconden
UNIT_TRIES = 3
UNIT_RETRY = 120.0


def key(library_id: uuid.UUID | str) -> str:
    return f"{JOB}:{library_id}"


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def _now() -> datetime:
    return datetime.now(UTC)


def _active() -> bool:
    bound = runtime.app()
    return bound is not None and runtime.enabled(bound)


def provider_for(kind: str) -> MetadataProvider | None:
    """De provider voor dit soort bibliotheek, of None (geen sleutel, MusicBrainz uit, of eigen
    video's): dan alleen lokale metadata."""
    if kind in ("movies", "shows"):
        return tmdb.provider()
    if kind == "music":
        return musicbrainz.provider()
    return None


def _free(column):
    """Niet vergrendeld met de verborgen `match_state` (na Ontkoppelen)."""
    return cast(column, String).not_like(f'%"{HIDDEN_LOCK}"%')


# --- herkennen: van titel en jaar naar een beslissing ------------------------------------------


def candidate_of(details: Details) -> Candidate:
    poster = next((i.path for i in details.images if i.kind in ("poster", "cover")), None)
    return Candidate(
        provider=details.provider,
        id=details.id,
        kind=details.kind,
        title=details.title,
        year=details.year,
        original_title=details.original_title,
        overview=(details.overview or "")[:300] or None,
        poster=poster,
    )


def _sure(choice: Candidate) -> match.Decision:
    return match.Decision("matched", choice, 1.0, [match.Scored(choice, 1.0)])


async def search(
    provider: MetadataProvider, kind: str, title: str, year: int | None, lang: str
) -> list[Candidate]:
    """Zoeken op titel en jaar. Het jaar is bij TMDB een filter: een scene-naam heeft soms het
    jaar van de uitgave of het festival, niet van de film (een jaar ernaast). Geeft het zoeken
    met jaar geen zekere keuze, dan nog eens zonder jaar, en tellen beide (match.rank telt een
    kandidaat één keer); zo kan de regel van ±1 jaar van Keuze 42 werken (review golf 5, C5)."""
    if not " ".join((title or "").split()):
        return []
    found = await provider.search(kind, title, year, lang)
    if year is not None and match.decide(title, year, found).state != "matched":
        found = found + await provider.search(kind, title, None, lang)
    return found


async def identify(
    provider: MetadataProvider,
    kind: str,
    title: str,
    year: int | None,
    ids: dict[str, Any] | None,
    lang: str,
) -> match.Decision:
    """Eerst een id uit de naam of de NFO (Keuze 42): `/movie/{id}` of `/find`. Anders zoeken in
    de taal van de bibliotheek; zonder automatische keuze nog eens in en-US (de titel van een
    scene-naam is vaak Engels), en de beste titel per kandidaat telt."""
    direct = match.direct(ids, kind)
    seed: list[Candidate] = []
    if direct is not None:
        source, value = direct
        try:
            if source == "tmdb":
                return _sure(candidate_of(await provider.details(kind, value, lang)))
            find = getattr(provider, "find", None)
            if find is not None:
                seed = await find(value, kind, lang)
                if len(seed) == 1:
                    return _sure(seed[0])
        except http.NotFound:
            pass
    found = seed + await search(provider, kind, title, year, lang)
    decision = match.decide(title, year, found)
    if decision.state != "matched" and lang != FALLBACK_LANG and provider.name == tmdb.NAME:
        more = await search(provider, kind, title, year, FALLBACK_LANG)
        if more:
            decision = match.decide(title, year, found + more)
    return decision


def stored_candidates(decision: match.Decision) -> list[dict[str, Any]]:
    """De 5 beste voor "Nakijken" (zoals Candidate, met het pad van de poster bij de
    provider)."""
    return [s.candidate.model_dump(mode="json") for s in decision.top[: match.TOP]]


# --- schrijven ---------------------------------------------------------------------------------


def detail_fields(details: Details, ids: dict[str, Any] | None, state: str) -> dict[str, Any]:
    """De velden van de provider voor items.merge (bron "provider"): wat in `locked` staat,
    slaat merge over."""
    fields: dict[str, Any] = {
        "title": details.title,
        "year": details.year,
        "ids": {**(ids or {}), **details.ids},
        "match_state": state,
        "candidates": [],
    }
    if details.overview:
        fields["overview"] = details.overview
    return fields


async def publish_item(session: AsyncSession, library_id: uuid.UUID, node_ids: list[uuid.UUID]):
    """media.item naar wie de bibliotheek ziet (na de commit). Faalt nooit."""
    from app.media import library as media_library

    try:
        library = await session.get(MediaLibrary, library_id)
        if library is None:
            return
        users = await media_library.watchers(session, library)
        for node_id in dict.fromkeys(node_ids):
            data = ItemEvent(node_id=node_id, library_id=library_id).model_dump(mode="json")
            events.publish(users, EVENT_ITEM, data)
    except Exception:  # noqa: BLE001 - een event mag het opzoeken niet laten mislukken
        log.exception("media.item na het opzoeken")


async def publish_library(session: AsyncSession, library_id: uuid.UUID) -> None:
    """media.library (een serie of album veranderde: de lijst opnieuw laden). Faalt nooit."""
    from app.media import library as media_library

    try:
        library = await session.get(MediaLibrary, library_id)
        if library is None:
            return
        users = await media_library.watchers(session, library)
        data = LibraryEvent(
            library_id=library.id,
            scan_state=library.scan_state,  # type: ignore[arg-type]
            item_count=library.item_count,
        ).model_dump(mode="json")
        events.publish(users, EVENT_LIBRARY, data)
    except Exception:  # noqa: BLE001
        log.exception("media.library na het opzoeken")


def art_ref(target: str, row: MediaItem | MediaShow | MediaAlbum) -> str | None:
    """Bij welke koppeling de art van dit ding hoort: het id van TMDB (een film, een serie),
    het nummer (een aflevering), of het album. None zonder koppeling (lokaal, Nakijken, of
    ontkoppeld): dan hoort er geen art van een provider bij (review golf 5, C1)."""
    locked = getattr(row, "locked", None) or []
    if row.match_state not in ("matched", "manual") or HIDDEN_LOCK in locked:
        return None
    if target == "album":
        return "album"
    if target == "item" and getattr(row, "kind", None) == "episode":
        return f"aflevering:{row.season}:{row.episode}"
    number = str((row.ids or {}).get("tmdb") or "")
    return f"tmdb:{number}" if number.isdigit() else None


def _art_key(target: str, target_id: uuid.UUID | str) -> str:
    return f"{ART_JOB}:{target}:{target_id}:"


async def drop_art(session: AsyncSession, target: str, target_id: uuid.UUID) -> None:
    """De art-taken die nog wachten voor dit ding weg (ontkoppelen, opnieuw koppelen), in de
    transactie van de aanroeper. Een taak die al loopt, schrijft niet meer: run_art kijkt of
    de koppeling nog dezelfde is (art_ref)."""
    await session.execute(
        delete(Job)
        .where(
            Job.type == ART_JOB,
            Job.status == "queued",
            Job.key.like(_art_key(target, target_id) + "%"),
        )
        .execution_options(synchronize_session=False)
    )


async def queue_art(
    session: AsyncSession,
    target: str,
    row: MediaItem | MediaShow | MediaAlbum,
    details_images: list[Any],
) -> None:
    """Een taak media.art per rol (poster, achtergrond, hoes), in de transactie van de
    aanroeper, na het schrijven van de koppeling. De taak draagt de koppeling (`ref`, ook in de
    key): een andere koppeling geeft een eigen taak, en een oude schrijft niets meer."""
    target_id = row.node_id if target == "item" else row.id  # type: ignore[union-attr]
    ref = art_ref(target, row)
    if ref is None:
        return
    seen: set[str] = set()
    for image in details_images:
        role = "poster" if image.kind == "still" else image.kind
        if role in seen or role not in ("poster", "backdrop", "cover"):
            continue
        seen.add(role)
        payload = {
            "target": target,
            "id": str(target_id),
            "role": role,
            "provider": image.provider,
            "kind": image.kind,
            "path": image.path,
            "ref": ref,
        }
        key = f"{_art_key(target, target_id)}{role}:{ref}"
        await jobs.enqueue(session, ART_JOB, payload, key=key)


def _open(row: MediaItem | MediaShow) -> bool:
    return row.match_state == "local" and HIDDEN_LOCK not in (row.locked or [])


async def write_item(
    settings: Settings,
    library_id: uuid.UUID,
    node_id: uuid.UUID,
    decision: match.Decision,
    details: Details | None,
    *,
    manual: bool = False,
) -> bool:
    """De uitkomst voor een film op het item zetten. Automatisch alleen als het item nog lokaal
    is (wie intussen zelf koppelde, wint). Geeft of er iets veranderde."""
    async with _maker(settings)() as session:
        item = await session.get(MediaItem, node_id)
        if item is None or item.library_id != library_id:
            return False
        if not manual and not _open(item):
            return False
        if details is not None:
            state = "manual" if manual else "matched"
            fields = detail_fields(details, item.ids, state)
            if manual:
                item.locked = [f for f in (item.locked or []) if f != HIDDEN_LOCK]
                await drop_art(session, "item", item.node_id)  # de art van een vorige koppeling
            media_items.merge(item, fields, "provider")
            await queue_art(session, "item", item, details.images)
        else:
            media_items.merge(
                item,
                {"match_state": "review", "candidates": stored_candidates(decision)},
                "provider",
            )
        await session.commit()
        await publish_item(session, library_id, [node_id])
    return True


async def write_show(
    settings: Settings,
    library_id: uuid.UUID,
    show_id: uuid.UUID,
    decision: match.Decision,
    details: Details | None,
    *,
    manual: bool = False,
) -> bool:
    async with _maker(settings)() as session:
        show = await session.get(MediaShow, show_id)
        if show is None or show.library_id != library_id:
            return False
        if not manual and not _open(show):
            return False
        if details is not None:
            state = "manual" if manual else "matched"
            if manual:
                show.locked = [f for f in (show.locked or []) if f != HIDDEN_LOCK]
                # Een andere serie: de afleveringen opnieuw, met de seizoenen van deze
                await _reset_episodes(session, show.id)
                await drop_art(session, "show", show.id)
            media_items.merge(show, detail_fields(details, show.ids, state), "provider")
            await queue_art(session, "show", show, details.images)
        else:
            media_items.merge(
                show,
                {"match_state": "review", "candidates": stored_candidates(decision)},
                "provider",
            )
        await session.commit()
        await publish_library(session, library_id)
    return True


async def _reset_episodes(session: AsyncSession, show_id: uuid.UUID) -> None:
    rows = await session.scalars(
        select(MediaItem).where(MediaItem.show_id == show_id, MediaItem.kind == "episode")
    )
    for item in rows:
        if item.match_state in ("matched", "review") and HIDDEN_LOCK not in (item.locked or []):
            item.match_state = "local"
            # Een still van de vorige serie die nog wacht, weg: hij heeft dezelfde koppeling
            # (aflevering:S:E) en key als de still van de nieuwe, en zou die tegenhouden
            # (nacontrole golf 5, C1)
            await drop_art(session, "item", item.node_id)


async def write_season(
    settings: Settings,
    library_id: uuid.UUID,
    show_id: uuid.UUID,
    season: int,
    episodes: list[EpisodeDetails],
) -> None:
    """De afleveringen van één seizoen: titel, samenvatting, datum en een still als poster.
    Een aflevering die de provider niet kent, gaat naar Nakijken."""
    by_number = {e.episode: e for e in episodes}
    changed: list[uuid.UUID] = []
    async with _maker(settings)() as session:
        rows = await session.scalars(
            select(MediaItem).where(
                MediaItem.show_id == show_id,
                MediaItem.kind == "episode",
                MediaItem.season == season,
                MediaItem.match_state == "local",
                _free(MediaItem.locked),
            )
        )
        for item in rows:
            found = by_number.get(item.episode) if item.episode is not None else None
            if found is None:
                media_items.merge(item, {"match_state": "review", "candidates": []}, "provider")
                changed.append(item.node_id)
                continue
            fields: dict[str, Any] = {"match_state": "matched", "aired": found.aired}
            if found.title:
                fields["title"] = found.title
            if found.overview:
                fields["overview"] = found.overview
            media_items.merge(item, fields, "provider")
            changed.append(item.node_id)
            if found.still:
                still = [_Image(found.provider, "still", found.still)]
                await queue_art(session, "item", item, still)
        await session.commit()
        await publish_item(session, library_id, changed)


@dataclass
class _Image:
    provider: str
    kind: str
    path: str


async def write_album(
    settings: Settings,
    library_id: uuid.UUID,
    album_id: uuid.UUID,
    state: str,
    release: musicbrainz.Release | None,
) -> None:
    async with _maker(settings)() as session:
        album = await session.get(MediaAlbum, album_id)
        if album is None or album.library_id != library_id or album.match_state != "local":
            return
        album.match_state = state
        if release is not None and release.cover:
            await queue_art(session, "album", album, [_Image(caa.NAME, "cover", release.id)])
        await session.commit()
        await publish_library(session, library_id)


# --- de taak media.lookup ----------------------------------------------------------------------


@dataclass
class Unit:
    kind: str  # movie | show | season | album
    id: uuid.UUID
    title: str = ""
    year: int | None = None
    ids: dict[str, Any] = field(default_factory=dict)
    artist: str | None = None
    tracks: int | None = None
    mbid: str | None = None
    show_ref: str | None = None  # tv:<n> voor een seizoen
    season: int | None = None


async def pending(session: AsyncSession, library: MediaLibrary, limit: int = BATCH) -> list[Unit]:
    """Wat er in deze bibliotheek nog opgezocht moet worden (hooguit limit)."""
    out: list[Unit] = []
    if library.kind == "movies":
        rows = await session.scalars(
            select(MediaItem)
            .where(
                MediaItem.library_id == library.id,
                MediaItem.kind == "movie",
                MediaItem.present.is_(True),
                MediaItem.match_state == "local",
                _free(MediaItem.locked),
            )
            .order_by(MediaItem.added_at, MediaItem.node_id)
            .limit(limit)
        )
        out = [Unit("movie", i.node_id, i.title, i.year, dict(i.ids or {})) for i in rows]
    elif library.kind == "shows":
        shows = await session.scalars(
            select(MediaShow)
            .where(
                MediaShow.library_id == library.id,
                MediaShow.match_state == "local",
                _free(MediaShow.locked),
            )
            .order_by(MediaShow.sort_title, MediaShow.id)
            .limit(limit)
        )
        out = [Unit("show", s.id, s.title, s.year, dict(s.ids or {})) for s in shows]
        if len(out) < limit:
            out += await _seasons(session, library, limit - len(out))
    elif library.kind == "music":
        albums = await session.scalars(
            select(MediaAlbum)
            .where(MediaAlbum.library_id == library.id, MediaAlbum.match_state == "local")
            .order_by(MediaAlbum.sort_title, MediaAlbum.id)
            .limit(limit)
        )
        out = [
            Unit(
                "album",
                a.id,
                a.title,
                a.year,
                artist=None if a.compilation else a.album_artist,
                tracks=a.track_count or None,
                mbid=a.mbid,
            )
            for a in albums
        ]
    return out


async def _seasons(session: AsyncSession, library: MediaLibrary, limit: int) -> list[Unit]:
    """De seizoenen met lokale afleveringen van gekoppelde series (één verzoek per seizoen)."""
    matched = select(MediaShow.id).where(
        MediaShow.library_id == library.id, MediaShow.match_state.in_(("matched", "manual"))
    )
    rows = await session.execute(
        select(MediaItem.show_id, MediaItem.season)
        .where(
            MediaItem.library_id == library.id,
            MediaItem.kind == "episode",
            MediaItem.present.is_(True),
            MediaItem.match_state == "local",
            MediaItem.season.is_not(None),
            MediaItem.show_id.in_(matched),
            _free(MediaItem.locked),
        )
        .group_by(MediaItem.show_id, MediaItem.season)
        .order_by(MediaItem.show_id, MediaItem.season)
        .limit(limit)
    )
    pairs = [(s, n) for s, n in rows.all() if s is not None]
    if not pairs:
        return []
    shows = {
        s.id: s
        for s in await session.scalars(
            select(MediaShow).where(MediaShow.id.in_({s for s, _ in pairs}))
        )
    }
    out = []
    for show_id, season in pairs:
        show = shows.get(show_id)
        number = match.direct((show.ids or {}) if show else {}, "show")
        if show is None or number is None or number[0] != "tmdb":
            continue
        out.append(Unit("season", show_id, show.title, show_ref=number[1], season=season))
    return out


async def has_pending(session: AsyncSession, library: MediaLibrary) -> bool:
    return bool(await pending(session, library, limit=1))


async def _movie(settings, provider, library_id, lang, unit: Unit) -> None:
    decision = await identify(provider, "movie", unit.title, unit.year, unit.ids, lang)
    details = None
    if decision.state == "matched" and decision.choice is not None:
        try:
            details = await provider.details("movie", decision.choice.id, lang)
        except http.NotFound:
            decision = match.Decision("review", None, decision.confidence, decision.top)
    await write_item(settings, library_id, unit.id, decision, details)


async def _show(settings, provider, library_id, lang, unit: Unit) -> None:
    decision = await identify(provider, "show", unit.title, unit.year, unit.ids, lang)
    details = None
    if decision.state == "matched" and decision.choice is not None:
        try:
            details = await provider.details("show", decision.choice.id, lang)
        except http.NotFound:
            decision = match.Decision("review", None, decision.confidence, decision.top)
    await write_show(settings, library_id, unit.id, decision, details)


async def _season(settings, provider, library_id, lang, unit: Unit) -> None:
    assert unit.show_ref is not None and unit.season is not None
    try:
        episodes = await provider.season(unit.show_ref, unit.season, lang)
    except http.NotFound:
        episodes = []
    await write_season(settings, library_id, unit.id, unit.season, episodes)


async def _album(settings, provider, library_id, lang, unit: Unit) -> None:
    mb = provider if isinstance(provider, musicbrainz.MusicBrainz) else None
    if mb is None:
        return
    release = None
    if unit.mbid and musicbrainz.mbid(unit.mbid):
        try:
            release = await mb.release(unit.mbid)
        except http.NotFound:
            release = None
    if release is not None:
        await write_album(settings, library_id, unit.id, "matched", release)
        return
    found = await mb.search_album(unit.title, unit.artist, None)
    albums = [match.Album(r.id, r.title, r.artist, r.track_count, r.year) for r in found]
    choice, _sure_of = match.decide_album(unit.title, unit.artist, unit.tracks, albums, unit.year)
    if choice is None:
        await write_album(settings, library_id, unit.id, "review", None)
        return
    picked = next(r for r in found if r.id == choice.id)
    # Een zoekresultaat zegt niet of er een voorkant is: de release zelf wel
    try:
        picked = await mb.release(picked.id)
    except http.NotFound:
        pass
    await write_album(settings, library_id, unit.id, "matched", picked)


STEPS = {"movie": _movie, "show": _show, "season": _season, "album": _album}

# Per ding: hoe vaak het faalde terwijl de provider zelf werkte (in het geheugen; na een
# herstart begint het opnieuw, wat hooguit een paar pogingen extra kost)
_failures: dict[tuple[str, uuid.UUID, int | None], int] = {}


def _unit_key(unit: Unit) -> tuple[str, uuid.UUID, int | None]:
    return unit.kind, unit.id, unit.season


async def _reachable(provider: MetadataProvider) -> bool:
    """Werkt de provider zelf? Eén lichte vraag (check, de knop Testen). Staat de
    stroomonderbreker open, dan gaat er niets naar buiten en is het antwoord nee. Een 429 of
    een 401 gaan door naar run (Defer, of de sleutel dicht)."""
    try:
        await provider.check()
    except ProviderUnavailable:
        return False
    return True


async def _give_up(settings: Settings, library_id: uuid.UUID, unit: Unit) -> None:
    """Dit ding naar Nakijken: de provider werkt, maar het antwoord voor dit ding niet."""
    nothing = match.Decision("review", None, 0.0, [])
    if unit.kind == "movie":
        await write_item(settings, library_id, unit.id, nothing, None)
    elif unit.kind == "show":
        await write_show(settings, library_id, unit.id, nothing, None)
    elif unit.kind == "season" and unit.season is not None:
        await write_season(settings, library_id, unit.id, unit.season, [])
    elif unit.kind == "album":
        await write_album(settings, library_id, unit.id, "review", None)


async def _unit_failed(settings: Settings, library_id: uuid.UUID, unit: Unit) -> bool:
    """Telt een fout van één ding. Geeft True als het nu naar Nakijken ging."""
    key = _unit_key(unit)
    _failures[key] = _failures.get(key, 0) + 1
    if _failures[key] < UNIT_TRIES:
        return False
    _failures.pop(key, None)
    log.warning(
        "media.lookup: %s %s faalt steeds bij de provider; naar Nakijken", unit.kind, unit.id
    )
    await _give_up(settings, library_id, unit)
    return True


async def run(settings: Settings, payload: dict[str, Any]) -> None:
    """De taak media.lookup voor één bibliotheek.

    Een fout bij één ding (een kapot of te groot antwoord, een 5xx voor één id) houdt de rest
    niet tegen (review golf 5, C2): werkt de provider zelf nog (`_reachable`), dan telt de fout
    voor dat ding en gaat de taak door met het volgende; na UNIT_TRIES keer gaat het naar
    Nakijken. Alleen als de provider zelf weg is (geen verbinding, de stroomonderbreker open):
    `Defer` voor de hele bibliotheek, en dan telt niets."""
    if not _active():
        return
    library_id = uuid.UUID(str(payload["library_id"]))
    maker = _maker(settings)
    async with maker() as session:
        library = await session.get(MediaLibrary, library_id)
        if library is None or not library.online or library.kind not in ONLINE_KINDS:
            return
        await secrets.load(session, settings)
        provider = provider_for(library.kind)
        if provider is None:
            return  # geen sleutel of uit: alleen lokaal, niets naar buiten
        units = await pending(session, library)
        scanning = library.scan_state in ("queued", "running")
        lang = library.language or "nl-NL"
    # Geen verbinding open tijdens het verkeer naar buiten (regel 8)
    failed = 0
    try:
        for unit in units:
            try:
                await STEPS[unit.kind](settings, provider, library_id, lang, unit)
            except ProviderUnavailable:
                if not await _reachable(provider):
                    raise
                if not await _unit_failed(settings, library_id, unit):
                    failed += 1
            else:
                _failures.pop(_unit_key(unit), None)
    except ProviderRate as e:
        raise jobs.Defer(max(1.0, e.retry_after)) from None
    except ProviderUnavailable:
        raise jobs.Defer(OFFLINE) from None
    except ProviderAuth:
        if isinstance(provider, tmdb.Tmdb):
            async with maker() as session:
                await secrets.mark_tmdb(session, settings, False, used=provider.used_key)
        log.warning("%s: de sleutel werkt niet (meer); alleen lokale metadata", provider.name)
        return
    # Meer werk: een volle ronde, een serie die nu gekoppeld is (haar seizoenen), of een scan
    # die nog loopt (dan even wachten). Faalde er een ding (de provider werkt wel): de volgende
    # ronde pas na UNIT_RETRY, dan nog eens
    more = len(units) >= BATCH or any(u.kind == "show" for u in units)
    if more or scanning or failed:
        later = None if more else _now() + timedelta(seconds=WAIT_SCAN)
        if failed:
            later = _now() + timedelta(seconds=UNIT_RETRY)
        async with maker() as session:
            await jobs.chain(session, JOB, {"library_id": str(library_id)}, key(library_id),
                             run_after=later)  # fmt: skip
            await session.commit()


# --- de taak media.art -------------------------------------------------------------------------


async def download(settings: Settings, provider: str, kind: str, path: str) -> bytes:
    """Het bronplaatje via http.py. http.NotFound als het er niet is."""
    if provider == tmdb.NAME:
        address = tmdb.image_url(settings, kind, path)
        if address is None:
            raise http.NotFound("tmdb: geen geldig pad")
        return (await http.get_image(tmdb.NAME, address)).body
    if provider == caa.NAME:
        return (await caa.CoverArt(settings).front(path)).body
    raise http.NotFound("onbekende provider")


def _local_role(row_art: dict | None, role: str, target: str) -> bool:
    """Staat er een lokaal plaatje in deze rol (of bij een item in poster of thumb)? Dan blijft
    dat: een provider vervangt geen eigen poster."""
    art_map = row_art or {}
    roles = ("poster", "thumb") if target == "item" and role == "poster" else (role,)
    return any(media_items.is_local_art(art_map.get(r)) for r in roles)


async def _row(session: AsyncSession, target: str, target_id: uuid.UUID):
    model = {"item": MediaItem, "show": MediaShow, "album": MediaAlbum}.get(target)
    return None if model is None else await session.get(model, target_id)


def _same_match(target: str, row: Any, payload: dict[str, Any]) -> bool:
    """Hoort de taak nog bij de koppeling van nu? (Een taak van vóór deze regel heeft geen
    ref: dan alleen als er een koppeling is.)"""
    ref = art_ref(target, row)
    wanted = payload.get("ref")
    return ref is not None and (wanted is None or wanted == ref)


async def _still_online(session: AsyncSession, settings: Settings, row: Any) -> bool:
    """Mag er (nog) verkeer naar buiten voor dit ding? De bibliotheek online, en de provider
    nog ingesteld (de sleutel van TMDB, MusicBrainz aan). Anders gaat er niets naar buiten,
    ook niet voor een taak die eerder klaargezet werd (review golf 5, C4)."""
    library = await session.get(MediaLibrary, row.library_id)
    if library is None or not library.online or library.kind not in ONLINE_KINDS:
        return False
    await secrets.load(session, settings)
    return provider_for(library.kind) is not None


async def run_art(settings: Settings, payload: dict[str, Any]) -> None:
    """De taak media.art: één plaatje voor één rol. Alleen als het ding nog dezelfde koppeling
    heeft als toen de taak klaargezet werd (anders is het plaatje van een andere film, of het
    ding is ontkoppeld: review golf 5, C1), en als er nog verkeer naar buiten mag (C4)."""
    if not _active():
        return
    target = str(payload.get("target"))
    target_id = uuid.UUID(str(payload["id"]))
    role = str(payload.get("role"))
    maker = _maker(settings)
    async with maker() as session:
        row = await _row(session, target, target_id)
        if row is None or _local_role(row.art, role, target):
            return
        if not _same_match(target, row, payload):
            return
        if not await _still_online(session, settings, row):
            return
    try:
        body = await download(
            settings, str(payload.get("provider")), str(payload.get("kind")), str(payload["path"])
        )
    except http.NotFound:
        return
    except ProviderRate as e:
        raise jobs.Defer(max(1.0, e.retry_after)) from None
    except ProviderUnavailable:
        raise jobs.Defer(OFFLINE) from None
    try:
        sha = (await art.store(settings, [body]))[0]
    except art.Refused:
        sha = None
    if sha is None:
        log.info("media.art: plaatje geweigerd (%s %s)", payload.get("provider"), role)
        return
    value = art.entry(art.url_key(settings), sha, role)
    async with maker() as session:
        row = await _row(session, target, target_id)
        if row is None or _local_role(row.art, role, target):
            return
        if not _same_match(target, row, payload):
            return  # intussen ontkoppeld of anders gekoppeld
        if target == "item":
            media_items.merge(row, {"art": {role: value}}, "provider")
        else:
            row.art = {**(row.art or {}), role: value}
        await session.commit()
        if target == "item":
            await publish_item(session, row.library_id, [row.node_id])
        else:
            await publish_library(session, row.library_id)


# --- wanneer: de haak, de housekeeping ---------------------------------------------------------


async def queue_all(settings: Settings) -> int:
    """Een taak klaarzetten voor elke online bibliotheek met werk (en een provider). Geeft
    hoeveel er bij kwamen."""
    if not _active():
        return 0
    added = 0
    async with _maker(settings)() as session:
        await secrets.load(session, settings)
        libraries = (
            await session.scalars(
                select(MediaLibrary).where(
                    MediaLibrary.online.is_(True), MediaLibrary.kind.in_(ONLINE_KINDS)
                )
            )
        ).all()
        for library in libraries:
            if provider_for(library.kind) is None:
                continue
            # Ook als de scan nog loopt (of klaarstaat): de taak wacht er dan op (WAIT_SCAN)
            scanning = library.scan_state in ("queued", "running")
            if not scanning and not await has_pending(session, library):
                continue
            payload = {"library_id": str(library.id)}
            if await jobs.enqueue(session, JOB, payload, key=key(library.id)) is not None:
                added += 1
        await session.commit()
    return added


async def queue_library(session: AsyncSession, library_id: uuid.UUID) -> None:
    """Voor de router: een taak voor deze bibliotheek (in de transactie van de aanroeper)."""
    await jobs.enqueue(session, JOB, {"library_id": str(library_id)}, key=key(library_id))


async def sweep(settings: Settings) -> None:
    """Housekeeping (elke 5 minuten): wat de haak miste."""
    await queue_all(settings)


async def clean(settings: Settings) -> None:
    """Housekeeping (eens per dag): art zonder verwijzing en de tijdelijke map."""
    if not _active():
        return
    gone = await art.cleanup(settings)
    if any(gone.values()):
        log.info("art opgeruimd: %s", gone)


_soon: tuple[asyncio.AbstractEventLoop, asyncio.TimerHandle] | None = None
_tasks: set[asyncio.Task] = set()


def delay() -> float:
    """Zo lang na een wijziging: SOON, en minstens twee tikken van de scanner (die zet de scan
    pas bij zijn volgende tik klaar; daarna ziet queue_all hem als queued of running)."""
    tick = float(getattr(runtime.settings(), "media_scan_tick", 0.0) or 0.0)
    return max(SOON, 2 * tick)


def touched(node_ids: list[uuid.UUID], type: str) -> None:
    """De haak op hooks.touched: geen I/O hier, alleen over `delay()` seconden één ronde
    queue_all (meer wijzigingen in die tijd: één ronde)."""
    global _soon
    if not _active():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _soon is not None and _soon[0] is loop and not _soon[1].cancelled():
        return  # er staat er al een klaar in deze lus
    _soon = (loop, loop.call_later(delay(), _fire))


def _fire() -> None:
    global _soon
    _soon = None
    if not _active():
        return
    task = asyncio.get_running_loop().create_task(_kick(runtime.settings()))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _kick(settings: Settings) -> None:
    try:
        await queue_all(settings)
    except Exception:  # noqa: BLE001 - de housekeeping komt er later toch langs
        log.exception("media.lookup klaarzetten na een wijziging")


def reset() -> None:
    """Tests: de haak en de tellers per ding vergeten."""
    global _soon
    if _soon is not None:
        _soon[1].cancel()
    _soon = None
    _failures.clear()


async def counts(session: AsyncSession, library_id: uuid.UUID) -> dict[str, int]:
    """Hoeveel er per staat is (voor de lijst Nakijken en de tests)."""
    rows = await session.execute(
        select(MediaItem.match_state, func.count())
        .where(MediaItem.library_id == library_id, MediaItem.present.is_(True))
        .group_by(MediaItem.match_state)
    )
    return {state: n for state, n in rows.all()}


jobs.register(JOB, run, lane=LANE, timeout=TIMEOUT)
jobs.register(ART_JOB, run_art, lane=LANE, timeout=ART_TIMEOUT)
jobs.housekeeping(SWEEP, sweep, SWEEP_EVERY)
jobs.housekeeping(CLEAN, clean, art.CLEAN_EVERY)
hooks.register_touched(touched)
