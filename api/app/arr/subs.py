"""Ondertitels zoeken bij OpenSubtitles (fase 11, stap 4; zoals Bazarr, eigen code).

Elke `ROUND` (vanuit de lus van mediabeheer) bekijkt de app een stuk van de films en
afleveringen in de bibliotheken met films en series, de nieuwste eerst; de volgende ronde het
volgende stuk, en na het laatste weer van voren. Per video en per gewenste taal (/beheer →
Mediabeheer → OpenSubtitles, standaard nl en en):

- **Is er al een?** Een ingebed ondertitelspoor of een los bestand in die taal telt, behalve een
  geforceerde (die vertaalt alleen anderstalige stukjes).
- **Zoeken** op de hash van het bestand (een treffer daarop past precies in de tijd) en op het
  id van de film of de serie (TMDB of IMDb, uit de bibliotheek). Zonder id zoekt de app niet:
  op titel zijn de treffers te vaak van een andere film.
- **Gevonden:** de ondertitel komt als los bestand naast de video, `<naam van de video>.<taal>.srt`,
  als de eigenaar van de bibliotheek (`place_file`, zoals een upload). De scanner van fase 5
  koppelt hem dan aan de video, en de speler toont hem.
- **Niets gevonden:** opnieuw na 6 uur, dan na een dag, 3 dagen, en daarna elke week (er komen
  vaak later ondertitels bij).

Per ronde hooguit `MAX_PER_ROUND` video's. Meldt OpenSubtitles dat de downloads van vandaag op
zijn, dan pauzeert de app tot de grens terugkomt (of een dag). Zonder sleutel doet een ronde niets.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.arr import opensubs
from app.arr import settings as arr_settings
from app.config import Settings
from app.db import get_engine
from app.media.subtitles import lang_code
from app.models import (
    ArrSubtitle,
    MediaItem,
    MediaLibrary,
    MediaProbe,
    MediaShow,
    Node,
    User,
)
from app.storage import BlobStore

log = logging.getLogger("platform.arr")

ROUND = timedelta(minutes=10)
MAX_PER_ROUND = 4
SCAN = 300
RETRY = (timedelta(hours=6), timedelta(days=1), timedelta(days=3), timedelta(days=7))


@dataclass
class _State:
    last: datetime | None = None
    paused_until: datetime | None = None
    offset: int = 0


STATE = _State()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _digits(value: Any) -> int | None:
    m = re.search(r"(\d+)$", str(value or ""))
    return int(m.group(1)) if m else None


def _imdb(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if re.fullmatch(r"tt\d{7,10}", text) else None


def retry_after(tries: int) -> timedelta:
    return RETRY[min(max(tries, 1), len(RETRY)) - 1]


def have(item: MediaItem, probe: MediaProbe | None) -> set[str]:
    """De talen (ISO 639-2/T) waarvan er al een niet-geforceerde ondertitel is."""
    out: set[str] = set()
    for sub in item.subs or []:
        if isinstance(sub, dict) and not sub.get("forced") and (code := lang_code(sub.get("lang"))):
            out.add(code)
    for stream in (probe.streams if probe is not None else None) or []:
        if (
            isinstance(stream, dict)
            and stream.get("type") == "subtitle"
            and not stream.get("forced")
            and (code := lang_code(stream.get("lang")))
        ):
            out.add(code)
    return out


@dataclass
class Todo:
    item: MediaItem
    node: Node
    show: MediaShow | None
    langs: list[str]


async def todo(
    session: AsyncSession, languages: tuple[str, ...], limit: int, now: datetime
) -> list[Todo]:
    """Een stuk van de bibliotheek (vanaf STATE.offset): de video's met een taal die ontbreekt
    en nu aan de beurt is."""
    rows = list(
        await session.execute(
            select(MediaItem, Node)
            .join(Node, Node.id == MediaItem.node_id)
            .join(MediaLibrary, MediaLibrary.id == MediaItem.library_id)
            .where(
                MediaItem.kind.in_(("movie", "episode")),
                MediaItem.present.is_(True),
                MediaItem.extra.is_(None),
                MediaLibrary.kind.in_(("movies", "shows")),
                Node.deleted_at.is_(None),
                Node.blob_id.is_not(None),
            )
            .order_by(MediaItem.added_at.desc(), MediaItem.node_id)
            .offset(STATE.offset)
            .limit(SCAN)
        )
    )
    STATE.offset = 0 if len(rows) < SCAN else STATE.offset + SCAN
    if not rows:
        return []
    node_ids = [item.node_id for item, _ in rows]
    done: dict[tuple[uuid.UUID, str], ArrSubtitle] = {
        (r.node_id, r.lang): r
        for r in await session.scalars(select(ArrSubtitle).where(ArrSubtitle.node_id.in_(node_ids)))
    }
    probes = {
        p.blob_sha: p
        for p in await session.scalars(
            select(MediaProbe).where(MediaProbe.blob_sha.in_({n.blob_id for _, n in rows}))
        )
    }
    out: list[Todo] = []
    for item, node in rows:
        present = have(item, probes.get(node.blob_id or ""))
        langs = []
        for lang in languages:
            if lang_code(lang) in present:
                continue
            row = done.get((item.node_id, lang))
            if row is not None and (
                row.state == "found" or now - _aware(row.tried_at) < retry_after(row.tries)
            ):
                continue
            langs.append(lang)
        if not langs:
            continue
        show = await session.get(MediaShow, item.show_id) if item.show_id else None
        out.append(Todo(item, node, show, langs))
        if len(out) >= limit:
            break
    return out


def query_of(t: Todo, moviehash: str | None) -> dict[str, Any] | None:
    """De zoekvraag voor OpenSubtitles, of None zonder bruikbaar id."""
    ids = t.item.ids or {}
    if t.item.kind == "movie":
        imdb, tmdb = _imdb(ids.get("imdb")), _digits(ids.get("tmdb"))
        if not imdb and not tmdb:
            return None
        return {"imdb": imdb, "tmdb": tmdb, "moviehash": moviehash}
    show_ids = (t.show.ids if t.show is not None else None) or {}
    imdb, tmdb = _imdb(show_ids.get("imdb")), _digits(show_ids.get("tmdb"))
    if (not imdb and not tmdb) or t.item.season is None or t.item.episode is None:
        return None
    return {
        "show_imdb": imdb,
        "show_tmdb": tmdb,
        "season": t.item.season,
        "episode": t.item.episode,
        "moviehash": moviehash,
    }


def sub_name(video: str, lang: str) -> str:
    stem = video.rsplit(".", 1)[0] if "." in video else video
    return f"{stem}.{lang.split('-')[0].lower()}.srt"


async def _once(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def save(
    session: AsyncSession, settings: Settings, node: Node, lang: str, data: bytes
) -> uuid.UUID | None:
    """De ondertitel als los bestand naast de video, als de eigenaar van de video."""
    from app.routers.files import place_file

    parent = await session.get(Node, node.parent_id) if node.parent_id else None
    owner = await session.get(User, node.owner_id)
    if parent is None or owner is None or parent.deleted_at is not None:
        return None
    blobs = BlobStore(settings.blob_dir)
    tmp, sha, size = await blobs.receive(_once(data), opensubs.MAX_SUB_BYTES)
    try:
        placed = await place_file(
            session,
            blobs,
            owner,
            parent,
            sub_name(node.name, lang),
            "rename",
            sha,
            size,
            tmp,
            actor=owner,
            tz=settings.timezone,
        )
    finally:
        tmp.unlink(missing_ok=True)
    return placed.id


async def _remember(
    session: AsyncSession,
    node_id: uuid.UUID,
    lang: str,
    found: opensubs.Found | None,
    sub_node: uuid.UUID | None,
    now: datetime,
) -> None:
    row = await session.get(ArrSubtitle, (node_id, lang))
    if row is None:
        row = ArrSubtitle(node_id=node_id, lang=lang, tries=0)
        session.add(row)
    row.tries = (row.tries or 0) + 1
    row.tried_at = now
    row.state = "found" if sub_node is not None else "none"
    row.file_id = found.file_id if found is not None else None
    row.release = found.release if found is not None else None
    row.sub_node_id = sub_node
    await session.commit()


async def fetch(
    session: AsyncSession, settings: Settings, api: opensubs.Session, t: Todo, now: datetime
) -> int:
    """Zoeken en zetten voor één video; geeft het aantal gezette ondertitels."""
    path = BlobStore(settings.blob_dir).path(t.node.blob_id or "")
    moviehash = await asyncio.to_thread(opensubs.moviehash, path)
    query = query_of(t, moviehash)
    if query is None:
        for lang in t.langs:
            await _remember(session, t.node.id, lang, None, None, now)
        return 0
    found = await api.search(t.langs, **query)
    placed = 0
    for lang in t.langs:
        choice = opensubs.best(found, lang)
        sub_node = None
        if choice is not None:
            data = await api.download(choice.file_id)
            sub_node = await save(session, settings, t.node, lang, data)
            placed += sub_node is not None
            log.info("arr: ondertitel %s voor %s", lang, t.node.name)
        await _remember(session, t.node.id, lang, choice, sub_node, now)
    return placed


async def tick(settings: Settings, *, force: bool = False) -> None:
    now = _now()
    if not force:
        if STATE.paused_until is not None and now < STATE.paused_until:
            return
        if STATE.last is not None and now - STATE.last < ROUND:
            return
    STATE.last = now
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        conf = await arr_settings.load_opensubs(session, settings)
        if conf is None:
            return
        work = await todo(session, conf.languages, MAX_PER_ROUND, now)
        if not work:
            return
        async with arr_settings.http_client() as client:
            api = arr_settings.opensubs_session(client, conf)
            for t in work:
                try:
                    await fetch(session, settings, api, t, now)
                except opensubs.OpenSubsQuota as exc:
                    STATE.paused_until = exc.reset or now + timedelta(days=1)
                    log.info("arr: OpenSubtitles: de downloads van vandaag zijn op")
                    return
                except opensubs.OpenSubsAuth as exc:
                    log.warning("arr: OpenSubtitles: %s", exc)
                    return
                except opensubs.OpenSubsError as exc:
                    log.warning("arr: OpenSubtitles bij %s: %s", t.node.name, exc)
                    continue
                if api.remaining == 0:
                    STATE.paused_until = now + timedelta(days=1)
                    return
