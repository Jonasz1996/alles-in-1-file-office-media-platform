"""Afspeellijsten (Keuze 37), hooguit 5.000 nummers. M9.

- **Alleen van jezelf.** Elke vraag zoekt de lijst met owner_id = de vrager: de lijst van een
  ander is 404, net als een die niet bestaat (geen IDOR). Delen bouwen we niet.
- **Wat je niet meer mag lezen, staat niet in een antwoord** (ook niet in de tellingen): geen
  deling meer, in de prullenbak, of het bestand weg. De regel blijft staan, want de deling kan
  terugkomen (Keuze 37). Lezen mag wie het bestand bezit (geen gast, niet in de prullenbak, zelf of
  een map erboven) of het via access.resolve krijgt: dezelfde regel als een afspeelsessie
  (sessions._verify).
- **Toevoegen** kan alleen een nummer dat je nu mag lezen en dat een nummer in een bibliotheek is
  (een item van soort track); anders 404 voor de hele vraag. Hetzelfde nummer twee keer mag.
- **Weg met het bestand:** media_playlist_item.node_id heeft ON DELETE CASCADE.
- **De volgorde** is position (oplopend; gaten mogen). Toevoegen op plek p (van wat je ziet)
  schuift wat erna komt op. Ordenen geeft de zichtbare regels de plekken die de zichtbare regels
  al hadden, in de nieuwe volgorde: een regel die je niet ziet, blijft waar hij was.
- **De grens van 5.000** telt alle regels, ook die je nu niet ziet (ze staan er nog). Schrijven gaat
  achter één slot in dit proces, zodat twee vragen tegelijk niet samen over de grens gaan. Eerst
  gaat de transactie van de vraag dicht (commit), dan pas wachten op het slot.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, errors
from app.media import errors as media_errors
from app.media import library as media_library
from app.media import music
from app.media.types import Playlist, PlaylistDetail, PlaylistItem, PlaylistItemsOut, Track
from app.models import MediaItem, MediaPlaylist, MediaPlaylistItem, MediaProbe, Node, User

MAX_ITEMS = 5000
CHUNK = 500

_lock = asyncio.Lock()


def now() -> datetime:
    return datetime.now(UTC)


def not_found():
    return errors.fail(404, "not_found", "Niet gevonden")


def _chunks[T](items: Sequence[T], size: int = CHUNK) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _name(value: str) -> str:
    name = " ".join(value.split())
    if not name:
        raise errors.fail(422, "invalid_input", "Geef de afspeellijst een naam")
    return name[:200]


async def readable(
    session: AsyncSession, user: User, node_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Welke van deze bestanden deze gebruiker nu mag lezen (de regel van een sessie)."""
    ids = list(dict.fromkeys(node_ids))
    out: set[uuid.UUID] = set()
    for chunk in _chunks(ids):
        rows = (
            await session.execute(
                select(Node.id, Node.owner_id).where(Node.id.in_(chunk), Node.kind == "file")
            )
        ).all()
        own = [n for n, owner in rows if owner == user.id and user.role != "guest"]
        others = [n for n, owner in rows if owner != user.id]
        gone = await media_library.trashed_roots(session, own)
        out.update(n for n in own if n not in gone)
        if others:
            out.update((await access.resolve(session, user.id, others)).keys())
    return out


async def tracks(session: AsyncSession, node_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Track]:
    """Een Track per bestand: van het item in een bibliotheek (met de tags), anders de naam."""
    ids = list(dict.fromkeys(node_ids))
    out: dict[uuid.UUID, Track] = {}
    for chunk in _chunks(ids):
        rows = await session.execute(
            select(Node.id, Node.name, MediaItem, MediaProbe.tags, MediaProbe.duration_ms)
            .outerjoin(MediaItem, MediaItem.node_id == Node.id)
            .outerjoin(MediaProbe, MediaProbe.blob_sha == Node.blob_id)
            .where(Node.id.in_(chunk))
        )
        for node_id, name, item, tags, duration in rows:
            if item is not None:
                out[node_id] = music.track_of(item, tags, duration)
            else:
                title = music.tag(tags, music.TITLE) or name.rsplit(".", 1)[0] or name
                artist = music.tag(tags, music.ARTIST)
                out[node_id] = Track(
                    node_id=node_id, title=title, artist=artist, duration_ms=duration
                )
    return out


async def owned(session: AsyncSession, user: User, playlist_id: uuid.UUID) -> MediaPlaylist:
    """Een lijst van deze gebruiker, anders 404 (ook als hij van een ander is)."""
    found = await session.get(MediaPlaylist, playlist_id)
    if found is None or found.owner_id != user.id:
        raise not_found()
    return found


async def _rows(session: AsyncSession, playlist_id: uuid.UUID) -> list[MediaPlaylistItem]:
    found = await session.scalars(
        select(MediaPlaylistItem)
        .where(MediaPlaylistItem.playlist_id == playlist_id)
        .order_by(MediaPlaylistItem.position, MediaPlaylistItem.added_at, MediaPlaylistItem.id)
    )
    return list(found.all())


def _view(
    playlist: MediaPlaylist, shown: list[uuid.UUID], found: dict[uuid.UUID, Track]
) -> Playlist:
    duration = sum(max(0, found[n].duration_ms or 0) for n in shown if n in found)
    return Playlist(id=playlist.id, name=playlist.name, item_count=len(shown), duration_ms=duration)


async def summaries(session: AsyncSession, user: User) -> list[Playlist]:
    """De lijsten van deze gebruiker op naam, met wat hij nu mag lezen geteld."""
    lists = list(
        (
            await session.scalars(select(MediaPlaylist).where(MediaPlaylist.owner_id == user.id))
        ).all()
    )
    if not lists:
        return []
    by_list: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for chunk in _chunks([p.id for p in lists]):
        rows = await session.execute(
            select(MediaPlaylistItem.playlist_id, MediaPlaylistItem.node_id).where(
                MediaPlaylistItem.playlist_id.in_(chunk)
            )
        )
        for playlist_id, node_id in rows:
            by_list[playlist_id].append(node_id)
    every = {n for nodes in by_list.values() for n in nodes}
    ok = await readable(session, user, every)
    found = await tracks(session, ok)
    lists.sort(key=lambda p: (p.name.casefold(), p.created_at, str(p.id)))
    return [_view(p, [n for n in by_list.get(p.id, []) if n in ok], found) for p in lists]


async def summary(session: AsyncSession, user: User, playlist: MediaPlaylist) -> Playlist:
    nodes = [row.node_id for row in await _rows(session, playlist.id)]
    ok = await readable(session, user, nodes)
    found = await tracks(session, ok)
    return _view(playlist, [n for n in nodes if n in ok], found)


async def detail(session: AsyncSession, user: User, playlist_id: uuid.UUID) -> PlaylistDetail:
    playlist = await owned(session, user, playlist_id)
    rows = await _rows(session, playlist.id)
    ok = await readable(session, user, [row.node_id for row in rows])
    shown = [row for row in rows if row.node_id in ok]
    found = await tracks(session, ok)
    items = [
        PlaylistItem(id=row.id, node_id=row.node_id, track=found[row.node_id])
        for row in shown
        if row.node_id in found
    ]
    return PlaylistDetail(
        playlist=_view(playlist, [row.node_id for row in shown], found), items=items
    )


async def create(session: AsyncSession, user: User, name: str) -> Playlist:
    playlist = MediaPlaylist(id=uuid.uuid4(), owner_id=user.id, name=_name(name))
    session.add(playlist)
    await session.commit()
    return Playlist(id=playlist.id, name=playlist.name)


async def rename(session: AsyncSession, user: User, playlist_id: uuid.UUID, name: str) -> Playlist:
    playlist = await owned(session, user, playlist_id)
    playlist.name = _name(name)
    playlist.updated_at = now()
    await session.commit()
    return await summary(session, user, playlist)


async def remove(session: AsyncSession, user: User, playlist_id: uuid.UUID) -> None:
    playlist = await owned(session, user, playlist_id)
    await session.delete(playlist)
    await session.commit()


async def add(
    session: AsyncSession,
    user: User,
    playlist_id: uuid.UUID,
    node_ids: list[uuid.UUID],
    position: int | None,
) -> PlaylistItemsOut:
    """Nummers toevoegen, achteraan of op plek `position` van wat de gebruiker ziet. Meer dan
    MAX_ITEMS samen: 409 media_playlist_full, en er komt niets bij."""
    await session.commit()  # de transactie van de vraag dicht vóór het wachten op het slot
    async with _lock:
        playlist = await owned(session, user, playlist_id)
        wanted = list(dict.fromkeys(node_ids))
        ok = await readable(session, user, wanted)
        is_track: set[uuid.UUID] = set()
        for chunk in _chunks(wanted):
            found = await session.scalars(
                select(MediaItem.node_id).where(
                    MediaItem.node_id.in_(chunk), MediaItem.kind == "track"
                )
            )
            is_track.update(found.all())
        if any(n not in ok or n not in is_track for n in wanted):
            raise not_found()
        rows = await _rows(session, playlist.id)
        if len(rows) + len(node_ids) > MAX_ITEMS:
            raise media_errors.playlist_full(MAX_ITEMS)
        end = max((row.position for row in rows), default=-1) + 1
        start = end
        if position is not None:
            seen = await readable(session, user, [row.node_id for row in rows])
            shown = [row for row in rows if row.node_id in seen]
            if position < len(shown):
                start = shown[position].position
                await session.execute(
                    update(MediaPlaylistItem)
                    .where(
                        MediaPlaylistItem.playlist_id == playlist.id,
                        MediaPlaylistItem.position >= start,
                    )
                    .values(position=MediaPlaylistItem.position + len(node_ids))
                )
        added = [
            MediaPlaylistItem(
                id=uuid.uuid4(), playlist_id=playlist.id, node_id=node_id, position=start + i
            )
            for i, node_id in enumerate(node_ids)
        ]
        session.add_all(added)
        playlist.updated_at = now()
        await session.commit()
    found_tracks = await tracks(session, wanted)
    return PlaylistItemsOut(
        items=[
            PlaylistItem(id=row.id, node_id=row.node_id, track=found_tracks[row.node_id])
            for row in added
            if row.node_id in found_tracks
        ]
    )


async def reorder(
    session: AsyncSession, user: User, playlist_id: uuid.UUID, item_ids: list[uuid.UUID]
) -> None:
    """De nieuwe volgorde van de regels die de gebruiker ziet: precies die regels, elk één keer
    (anders 422 invalid_input)."""
    await session.commit()
    async with _lock:
        playlist = await owned(session, user, playlist_id)
        rows = await _rows(session, playlist.id)
        seen = await readable(session, user, [row.node_id for row in rows])
        shown = [row for row in rows if row.node_id in seen]
        if len(item_ids) != len(set(item_ids)) or set(item_ids) != {row.id for row in shown}:
            raise errors.fail(422, "invalid_input", "De volgorde past niet bij de afspeellijst")
        slots = [row.position for row in shown]
        by_id = {row.id: row for row in shown}
        for slot, item_id in zip(slots, item_ids, strict=True):
            by_id[item_id].position = slot
        playlist.updated_at = now()
        await session.commit()


async def remove_item(
    session: AsyncSession, user: User, playlist_id: uuid.UUID, item_id: uuid.UUID
) -> None:
    await session.commit()
    async with _lock:
        playlist = await owned(session, user, playlist_id)
        result = await session.execute(
            delete(MediaPlaylistItem).where(
                MediaPlaylistItem.id == item_id, MediaPlaylistItem.playlist_id == playlist.id
            )
        )
        if not result.rowcount:  # type: ignore[attr-defined]
            await session.rollback()
            raise not_found()
        playlist.updated_at = now()
        await session.commit()


async def count(session: AsyncSession, playlist_id: uuid.UUID) -> int:
    """Alle regels van een lijst, ook die de eigenaar nu niet ziet (tests)."""
    found = await session.scalar(
        select(func.count())
        .select_from(MediaPlaylistItem)
        .where(MediaPlaylistItem.playlist_id == playlist_id)
    )
    return int(found or 0)
