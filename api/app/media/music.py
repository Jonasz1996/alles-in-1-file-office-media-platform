"""Albums, artiesten en nummers (Keuze 37), en de analysestap audio_info (Keuze 18). M9.

**Groeperen** (regroup: de job media.music in de rij media_scan, één per bibliotheek; zo loopt hij
nooit tegelijk met de scanner):
- de tags komen uit de probe (media_probe.tags, met kleine letters; bij Ogg die van het
  audiospoor). Zonder tags de terugval van de map (names.parse: Artiest/Album (Jaar)/[CD2/]01 -
  Titel);
- een album is (bibliotheek, albumartiest, of "Diverse artiesten" bij een compilatie, de
  albumtitel genormaliseerd): de sleutel `a:<artiest>|<titel>` of `v:<titel>`;
- zonder albumartiest: de nummers in dezelfde map met dezelfde albumtag (`d:<map>|<titel>`).
  Heeft die map al een album met een albumartiest en dezelfde titel, dan horen ze daarbij;
- schijven: een map `CD1`, `Disc 2` … telt als de map erboven, en de tag `disc` (of het nummer
  van de map) houdt de schijven uit elkaar;
- compilatie: de tag (`compilation`: TCMP, cpil), een albumartiest als "Various Artists" of
  "Diverse artiesten", of zonder albumartiest minstens 3 artiesten waarvan geen de helft haalt;
- zonder albumtag en zonder eigen map (los in de bibliotheek): geen album.
De ids blijven staan zolang de sleutel blijft (upsert op key); een album zonder nummers gaat weg.
regroup schrijft van de items alleen album_id: titel, artiest, disc en nummer zijn van de scanner
(de naam). De tags gelden bij het lezen (track_of).

**De hoes** (art van het album): `{"cover": {"node"}}` voor cover, folder, front of album
(.jpg, .jpeg, .png, .webp) in de map van het album of van een schijf, en `{"thumb": {"node"}}`
voor het eerste nummer: de preview van een nummer is zijn ingebedde hoes (render.py). Een adres
van een provider (M8, `{"url"}`) blijft staan. De scanner zoekt geen art voor muziek; dit doet het.

**Wanneer:** na de analyse van een nummer (de stap audio_info, ook als die niets vond: de tags
komen met de probe), en bij het lezen als er sinds de vorige keer iets in de boom veranderde
(hooks.touched: alleen een teller in het geheugen, geen I/O). Na een herstart één keer per
bibliotheek, bij de eerste vraag. De lijsten tellen de nummers zelf (aanwezig, nu), dus een
nummer in de prullenbak telt meteen niet meer mee.

**Artiesten:** geen tabel. De albumartiesten van de albums met het aantal albums, gesorteerd op
de sorteertag (ALBUMARTISTSORT, TSO2; anders TSOP van de artiest), anders zonder "The", "De" of
"Het" vooraan. De sorteertags komen uit de laatste regroup (in het geheugen per bibliotheek).

**audio_info** (stap 40, muziek of zonder bibliotheek, geen video): de worker leest rate,
samples, delay en padding van het spoor dat play kiest (worker/ops/audio_info.py). Voor een spoor
dat audio.mp4 naar FLAC omzet (alles behalve FLAC, AAC en MP3) is delay 0: de decoder knipt de
voorloop dan zelf (S8). Geen databaseverbinding terwijl de worker leest (regel 8).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app import events, jobs
from app.config import Settings
from app.db import get_engine
from app.media import analysis, client, decide, hooks, names
from app.media import items as media_items
from app.media import library as media_library
from app.media.types import (
    EVENT_LIBRARY,
    AlbumCard,
    AlbumDetail,
    AlbumInfo,
    AlbumPage,
    Artist,
    ArtistDetail,
    LibraryEvent,
    Probe,
    Track,
)
from app.media.wire import Op
from app.media.worker.args import AudioInfoArgs, demuxer_of
from app.models import MediaAlbum, MediaItem, MediaLibrary, MediaProbe, Node, User

log = logging.getLogger("platform.media")

JOB = "media.music"
LANE = "media_scan"
TIMEOUT = 600.0
DELAY_S = 5.0  # een regroup wacht even: zo komen een scan en zijn analyses in één ronde
ALBUMS_PAGE = 60
CHUNK = 500  # ids per IN (...)
KEY_MAX = 255
VARIOUS = "Diverse artiesten"
VARIOUS_NAMES = frozenset(
    {"various artists", "various", "va", "v a", "diverse artiesten", "diverse",
     "verschillende artiesten"}
)  # fmt: skip
COMPILATION_MIN_ARTISTS = 3
COVER_NAMES = ("cover", "folder", "front", "album")
COVER_EXTS = (".jpg", ".jpeg", ".png", ".webp")
# Een map van een schijf, zoals names.py hem herkent
DISC_DIR = re.compile(r"^(?:disc|disk|cd)[\s._\-]*(\d{1,2})(?:\s.*)?$", re.IGNORECASE)
ARTICLES = re.compile(r"^(?:the|de|het)\s+")  # Keuze 37: alleen deze drie
NUMBER = re.compile(r"^\s*(\d{1,6})")
YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")
MBID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# De sleutels van de tags zoals de probe ze geeft (kleine letters; ID3, Vorbis en MP4 via ffmpeg)
ALBUM = ("album", "talb")
TITLE = ("title", "tit2")
ARTIST = ("artist", "tpe1")
ALBUM_ARTIST = ("album_artist", "albumartist", "album artist", "tpe2")
TRACK = ("track", "tracknumber", "trck")
DISC = ("disc", "discnumber", "tpos")
DATE = ("date", "year", "originaldate", "originalyear", "tdrc", "tyer", "tdor")
COMPILATION = ("compilation", "tcmp", "cpil", "itunescompilation")
ALBUM_SORT = ("albumsort", "album-sort", "album_sort", "sort_album", "tsoa")
ALBUM_ARTIST_SORT = (
    "albumartistsort", "album_artist-sort", "album_artist_sort", "album artist sort",
    "sort_album_artist", "tso2",
)  # fmt: skip
ARTIST_SORT = ("artistsort", "artist-sort", "artist_sort", "sort_artist", "tsop")
MBID = ("musicbrainz_albumid", "musicbrainz album id", "musicbrainz_album_id")

# audio_info: wat audio.mp4 (of direct, bij MP3) ongewijzigd doorgeeft; de rest wordt FLAC
KEPT_CODECS = frozenset({"flac", "aac", "mp3"})
AUDIO_DONE = frozenset({"ready", "skip", "failed"})
RATE_MAX = 768_000
SAMPLES_MAX = 2**40


# --- tags en sleutels ----------------------------------------------------------------------------


def tag(tags: dict[str, Any] | None, keys: Sequence[str]) -> str | None:
    """De eerste niet-lege waarde van deze sleutels (hooguit 255 tekens)."""
    if not isinstance(tags, dict):
        return None
    for key in keys:
        value = tags.get(key)
        if isinstance(value, str):
            text = " ".join(value.replace("\x00", " ").split())
            if text:
                return text[:255]
    return None


def number(value: str | None, high: int = 9999) -> int | None:
    """Het nummer vooraan: "3/12" → 3, "03" → 3; 0 of meer dan high is geen nummer."""
    found = NUMBER.match(value or "")
    if found is None:
        return None
    n = int(found.group(1))
    return n if 0 < n <= high else None


def year_of(value: str | None) -> int | None:
    found = YEAR.search(value or "")
    if found is None:
        return None
    year = int(found.group(1))
    low, high = media_items.YEAR_RANGE
    return year if low <= year <= high else None


def truthy(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"1", "true", "yes", "ja", "y"}


def norm(text: str) -> str:
    """Voor het groeperen: zonder hoofdletters, accenten en leestekens (names.normalize). Een
    naam van alleen leestekens ("!!!") houdt zijn tekens."""
    return names.normalize(text) or " ".join(text.casefold().split())


def music_sort(text: str) -> str:
    """De sorteersleutel zonder sorteertag: genormaliseerd en zonder The, De of Het vooraan."""
    base = norm(text)
    stripped = ARTICLES.sub("", base, count=1)
    return (stripped or base)[:255]


def sort_of(name: str, sort_tag: str | None) -> str:
    """De sorteertag als die er is (genormaliseerd), anders music_sort."""
    if sort_tag and norm(sort_tag):
        return norm(sort_tag)[:255]
    return music_sort(name)


def artist_key(name: str) -> str:
    """De sleutel van een artiest in het adres (/artists/{key}): genormaliseerd, dus zonder /,
    punten of andere leestekens. Een naam van alleen leestekens krijgt een hash."""
    key = names.normalize(name)[:255]
    if key:
        return key
    return "~" + hashlib.sha256(name.encode()).hexdigest()[:16]


def album_key(*parts: str) -> str:
    """De sleutel van een album (media_album.key, hooguit 255): te lang wordt kop + hash."""
    text = "|".join(parts)
    if len(text) <= KEY_MAX:
        return text
    digest = hashlib.sha256(text.encode()).hexdigest()[:24]
    return f"{text[: KEY_MAX - 25]}#{digest}"


def _common(values: Iterable[str | None]) -> str | None:
    """De waarde die het vaakst voorkomt; bij gelijkspel de eerste (de volgorde van de nummers)."""
    counted = Counter(v for v in values if v)
    return counted.most_common(1)[0][0] if counted else None


def _chunks[T](items: Sequence[T], size: int = CHUNK) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# --- groeperen (puur) -----------------------------------------------------------------------------


@dataclass
class TrackIn:
    """Eén nummer zoals regroup het leest. dirs: de mappen van de bibliotheek tot het nummer,
    zonder de map van de bibliotheek zelf (hooguit de onderste drie)."""

    node_id: uuid.UUID
    name: str
    dirs: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    tags: dict[str, Any] = field(default_factory=dict)
    duration_ms: int | None = None
    title: str = ""
    artist: str | None = None
    disc: int | None = None
    track: int | None = None


@dataclass
class AlbumOut:
    key: str
    title: str
    sort_title: str
    album_artist: str
    artist_sort: str
    year: int | None
    compilation: bool
    mbid: str | None
    folders: list[uuid.UUID]  # de map van het album eerst, dan die van de schijven
    tracks: list[uuid.UUID]  # op volgorde (schijf, nummer, titel)
    duration_ms: int


@dataclass
class _Seen:
    t: TrackIn
    album: str
    album_norm: str
    folder: uuid.UUID | None  # de map van het album (boven een map van een schijf)
    own: uuid.UUID | None  # de eigen map
    artist: str | None
    album_artist: str | None
    compilation: bool
    year: int | None
    disc: int | None
    track: int | None
    title: str


def _seen(t: TrackIn) -> _Seen | None:
    dirs = t.dirs
    own = dirs[-1][0] if dirs else None
    folder = own
    if len(dirs) >= 2 and DISC_DIR.match(dirs[-1][1].strip()):
        folder = dirs[-2][0]
    parsed = names.parse([*(name for _, name in dirs), t.name], "music")
    album = tag(t.tags, ALBUM) or (parsed.album if folder is not None else None)
    if not album:
        return None
    return _Seen(
        t=t,
        album=album[:255],
        album_norm=norm(album),
        folder=folder,
        own=own,
        artist=tag(t.tags, ARTIST) or t.artist or parsed.artist,
        album_artist=tag(t.tags, ALBUM_ARTIST),
        compilation=truthy(tag(t.tags, COMPILATION)),
        year=year_of(tag(t.tags, DATE)) or parsed.year,
        disc=number(tag(t.tags, DISC), 99) or t.disc or parsed.disc,
        track=number(tag(t.tags, TRACK)) or t.track or parsed.track,
        title=tag(t.tags, TITLE) or t.title or parsed.title or t.name,
    )


def group(tracks: Iterable[TrackIn]) -> list[AlbumOut]:
    """De albums van deze nummers (Keuze 37). Een nummer zonder album komt in geen ervan."""
    seen = [s for s in map(_seen, tracks) if s is not None]
    groups: dict[str, list[_Seen]] = defaultdict(list)
    tagged: dict[tuple[uuid.UUID | None, str], str] = {}  # (map, titel) → album met artiest
    rest: list[_Seen] = []
    for s in seen:
        aa = s.album_artist
        if (aa and norm(aa) in VARIOUS_NAMES) or (s.compilation and not aa):
            key = album_key("v", s.album_norm)
        elif aa:
            key = album_key("a", norm(aa), s.album_norm)
        else:
            rest.append(s)
            continue
        groups[key].append(s)
        tagged.setdefault((s.folder, s.album_norm), key)
    for s in rest:
        key = tagged.get((s.folder, s.album_norm)) or album_key(
            "d", str(s.folder or "-"), s.album_norm
        )
        groups[key].append(s)
    return [_album(key, members) for key, members in groups.items()]


def _order(s: _Seen) -> tuple:
    return (s.disc or 1, s.track or 10**6, norm(s.title), str(s.t.node_id))


def _album(key: str, members: list[_Seen]) -> AlbumOut:
    members.sort(key=_order)
    title = _common(s.album for s in members) or members[0].album
    compilation = any(s.compilation for s in members)
    artist_sort = None
    if key.startswith("v:"):
        album_artist, compilation = VARIOUS, True
    elif key.startswith("a:"):
        album_artist = _common(s.album_artist for s in members) or ""
        artist_sort = _common(tag(s.t.tags, ALBUM_ARTIST_SORT) for s in members)
    else:
        artists = Counter(norm(s.artist) for s in members if s.artist)
        top = artists.most_common(1)[0][1] if artists else 0
        many = len(artists) >= COMPILATION_MIN_ARTISTS and top * 2 < len(members)
        if compilation or many:
            album_artist, compilation = VARIOUS, True
        else:
            album_artist = _common(s.artist for s in members) or ""
    if artist_sort is None and album_artist and album_artist != VARIOUS:
        lead = norm(album_artist)
        artist_sort = _common(
            tag(s.t.tags, ARTIST_SORT) for s in members if s.artist and norm(s.artist) == lead
        )
    years = Counter(s.year for s in members if s.year)
    mbids = (tag(s.t.tags, MBID) for s in members)
    folders = [s.folder for s in members if s.folder is not None]
    folders += [s.own for s in members if s.own is not None]
    return AlbumOut(
        key=key,
        title=title,
        sort_title=sort_of(title, _common(tag(s.t.tags, ALBUM_SORT) for s in members)),
        album_artist=album_artist[:255],
        artist_sort=sort_of(album_artist, artist_sort) if album_artist else "",
        year=years.most_common(1)[0][0] if years else None,
        compilation=compilation,
        mbid=_common(m.lower() for m in mbids if m and MBID_RE.match(m.lower())),
        folders=list(dict.fromkeys(folders)),
        tracks=[s.t.node_id for s in members],
        duration_ms=sum(max(0, s.t.duration_ms or 0) for s in members),
    )


# --- regroup (de database) ------------------------------------------------------------------------

# Na een touched: een teller (geen I/O). Bij het lezen van een bibliotheek die een oudere stand
# zag: een regroup klaarzetten. Leeg na een herstart: dan één keer per bibliotheek.
_generation = 0
_requested: dict[uuid.UUID, int] = {}
# Per bibliotheek de sorteersleutel per artiest (artist_key → sleutel), van de laatste regroup
_sorts: dict[uuid.UUID, dict[str, str]] = {}


def _touched(node_ids: list[uuid.UUID], type: str) -> None:
    global _generation
    _generation += 1


hooks.register_touched(_touched)


def reset() -> None:
    """Voor tests: de tellers en sorteersleutels weg."""
    global _generation
    _generation = 0
    _requested.clear()
    _sorts.clear()


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def schedule(session: AsyncSession, library_id: uuid.UUID, delay: float = DELAY_S) -> None:
    """Een regroup klaarzetten in de transactie van de aanroeper; staat er al een klaar, dan
    niet nog een (die ziet de nieuwe stand ook). Loopt er een, dan komt er een achteraan."""
    await jobs.chain(
        session,
        JOB,
        {"library_id": str(library_id)},
        key=f"{JOB}:{library_id}",
        run_after=jobs.now() + timedelta(seconds=delay),
    )


async def refresh(session: AsyncSession, library_id: uuid.UUID) -> None:
    """Bij het lezen: is de boom veranderd sinds de vorige keer (of is dit de eerste keer na de
    start), dan een regroup klaarzetten. Commit de sessie van de vraag."""
    hooks.register_touched(_touched)  # na hooks.reset (tests) weer aan
    if _requested.get(library_id) == _generation:
        return
    _requested[library_id] = _generation
    await schedule(session, library_id)
    await session.commit()


async def _tracks(session: AsyncSession, library: MediaLibrary) -> list[TrackIn]:
    parent, grand, great = aliased(Node), aliased(Node), aliased(Node)
    rows = await session.execute(
        select(
            MediaItem.node_id,
            MediaItem.title,
            MediaItem.artist,
            MediaItem.disc,
            MediaItem.track,
            Node.name,
            parent.id,
            parent.name,
            grand.id,
            grand.name,
            great.id,
            great.name,
            MediaProbe.tags,
            MediaProbe.duration_ms,
        )  # fmt: skip
        .join(Node, Node.id == MediaItem.node_id)
        .outerjoin(parent, parent.id == Node.parent_id)
        .outerjoin(grand, grand.id == parent.parent_id)
        .outerjoin(great, great.id == grand.parent_id)
        .outerjoin(MediaProbe, MediaProbe.blob_sha == Node.blob_id)
        .where(
            MediaItem.library_id == library.id,
            MediaItem.kind == "track",
            MediaItem.present.is_(True),
        )
    )
    out = []
    for row in rows:
        node_id, title, artist, disc, track, name, *chain_raw, tags, duration = row
        chain: list[tuple[uuid.UUID, str]] = []
        for i in range(0, 6, 2):
            folder_id, folder_name = chain_raw[i], chain_raw[i + 1]
            if folder_id is None or folder_id == library.root_id:
                break
            chain.append((folder_id, folder_name))
        out.append(
            TrackIn(
                node_id=node_id,
                name=name,
                dirs=list(reversed(chain)),
                tags=tags if isinstance(tags, dict) else {},
                duration_ms=duration,
                title=title,
                artist=artist,
                disc=disc,
                track=track,
            )
        )
    return out


async def _covers(session: AsyncSession, folders: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    """Per map de node van zijn hoes (cover voor folder voor front voor album; .jpg eerst)."""
    wanted = [f"{n}{e}" for n in COVER_NAMES for e in COVER_EXTS]
    rank = {name: i for i, name in enumerate(wanted)}
    best: dict[uuid.UUID, tuple[int, str, uuid.UUID]] = {}
    for chunk in _chunks(list(dict.fromkeys(folders))):
        rows = await session.execute(
            select(Node.id, Node.parent_id, Node.name).where(
                Node.parent_id.in_(chunk),
                Node.kind == "file",
                Node.deleted_at.is_(None),
                func.lower(Node.name).in_(wanted),
            )
        )
        for node_id, parent_id, name in rows:
            place = (rank.get(name.lower(), len(rank)), str(node_id), node_id)
            if parent_id not in best or place < best[parent_id]:
                best[parent_id] = place
    return {folder: place[2] for folder, place in best.items()}


async def _store(
    session: AsyncSession,
    library: MediaLibrary,
    albums: list[AlbumOut],
    covers: dict[uuid.UUID, uuid.UUID],
    rows: list[TrackIn],
) -> bool:
    existing = {
        a.key: a
        for a in (
            await session.scalars(select(MediaAlbum).where(MediaAlbum.library_id == library.id))
        ).all()
    }
    changed = False
    wanted: dict[uuid.UUID, uuid.UUID | None] = {t.node_id: None for t in rows}
    keep: set[uuid.UUID] = set()
    for a in albums:
        row = existing.get(a.key)
        if row is None:
            row = MediaAlbum(id=uuid.uuid4(), library_id=library.id, key=a.key, art={})
            session.add(row)
            changed = True
        local: dict[str, dict[str, str]] = {}
        cover = next((covers[f] for f in a.folders if f in covers), None)
        if cover is not None:
            local["cover"] = {"node": str(cover)}
        if a.tracks:
            local["thumb"] = {"node": str(a.tracks[0])}
        art = {k: v for k, v in (row.art or {}).items() if not media_items.is_local_art(v)}
        values = {
            "title": a.title[:255],
            "sort_title": a.sort_title[:255],
            "album_artist": a.album_artist[:255],
            "year": a.year,
            "compilation": a.compilation,
            "mbid": a.mbid,
            "art": {**art, **local},
            "track_count": len(a.tracks),
            "duration_ms": a.duration_ms,
        }
        for name, value in values.items():
            if getattr(row, name, None) != value:
                setattr(row, name, value)
                changed = True
        keep.add(row.id)
        for node_id in a.tracks:
            wanted[node_id] = row.id
    await session.flush()
    current = dict(
        (
            await session.execute(
                select(MediaItem.node_id, MediaItem.album_id).where(
                    MediaItem.library_id == library.id, MediaItem.kind == "track"
                )
            )
        ).all()
    )
    gone = [row for row in existing.values() if row.id not in keep]
    gone_ids = {row.id for row in gone}
    moves: dict[uuid.UUID | None, list[uuid.UUID]] = defaultdict(list)
    for node_id, album_id in current.items():
        if node_id in wanted:
            if album_id != wanted[node_id]:
                moves[wanted[node_id]].append(node_id)
        elif album_id in gone_ids:  # in de prullenbak, en zijn album gaat weg
            moves[None].append(node_id)
    for album_id, node_ids in moves.items():
        for chunk in _chunks(node_ids):
            await session.execute(
                update(MediaItem).where(MediaItem.node_id.in_(chunk)).values(album_id=album_id)
            )
        changed = True
    for row in gone:
        await session.delete(row)
        changed = True
    return changed


async def regroup(settings: Settings, library_id: uuid.UUID) -> bool:
    """De albums van één bibliotheek opnieuw (Keuze 37). True als er iets veranderde."""
    maker = _maker(settings)
    async with maker() as session:
        library = await session.get(MediaLibrary, library_id)
        if library is None or library.kind != "music":
            return False
        rows = await _tracks(session, library)
    albums = group(rows)
    async with maker() as session:
        library = await session.get(MediaLibrary, library_id)
        if library is None:
            return False
        covers = await _covers(session, [f for a in albums for f in a.folders])
        changed = await _store(session, library, albums, covers, rows)
        await session.commit()
        users = await media_library.watchers(session, library) if changed else set()
        event = LibraryEvent(
            library_id=library.id,
            scan_state=library.scan_state,  # type: ignore[arg-type]
            item_count=library.item_count,
        )
    _sorts[library_id] = {
        artist_key(a.album_artist): a.artist_sort for a in albums if a.album_artist
    }
    if users:
        events.publish(users, EVENT_LIBRARY, event.model_dump(mode="json"))
    return changed


async def _run(settings: Settings, payload: dict[str, Any]) -> None:
    await regroup(settings, uuid.UUID(str(payload["library_id"])))


jobs.register(JOB, _run, lane=LANE, timeout=TIMEOUT)


# --- de stap audio_info ---------------------------------------------------------------------------


def audio_position(probe: Probe) -> int:
    """Het hoeveelste audiospoor play kiest (decide.default_audio): N van `-map 0:a:N`."""
    chosen = decide.default_audio(probe)
    if chosen is None:
        return 0
    found = (i for i, s in enumerate(probe.audio_streams()) if s.index == chosen.index)
    return next(found, 0)


def gapless_of(found: Any, codec: str | None) -> tuple[str, dict[str, Any] | None]:
    """Het antwoord van de worker als (audio_state, audio). Alles wordt nagekeken: een getal
    buiten de grenzen is failed, {"skip": …} is skip."""
    if not isinstance(found, dict):
        return "failed", None
    if "skip" in found:
        return "skip", None
    values: dict[str, Any] = {}
    for name in ("rate", "samples", "delay", "padding"):
        value = found.get(name, 0 if name == "padding" else None)
        if not isinstance(value, int) or isinstance(value, bool):
            return "failed", None
        values[name] = value
    rate, samples = values["rate"], values["samples"]
    ok = (
        0 < rate <= RATE_MAX
        and 0 < samples <= SAMPLES_MAX
        and 0 <= values["delay"] <= rate * 10
        and 0 <= values["padding"] <= rate * 10
    )
    if not ok:
        return "failed", None
    if (codec or "") not in KEPT_CODECS:
        values["delay"] = 0  # naar FLAC: de decoder knipt de voorloop zelf (S8)
    values["source"] = str(found.get("source") or "")[:16]
    return "ready", values


async def _set_audio(
    maker: async_sessionmaker[AsyncSession], sha: str, state: str, data: dict | None
) -> None:
    async with maker() as session:
        await session.execute(
            update(MediaProbe)
            .where(MediaProbe.blob_sha == sha)
            .values(audio_state=state, audio=data)
        )
        await session.commit()


async def read_audio(
    maker: async_sessionmaker[AsyncSession], sha: str, path: Path, probe: Probe
) -> str:
    """De gapless-gegevens van het spoor van play, via de worker; geeft de nieuwe audio_state.
    WorkerBusy en WorkerDown gaan door (de analyse wordt Defer), net als een fout van de worker
    die niet aan het bestand ligt; de staat gaat dan terug naar none."""
    streams = probe.audio_streams()
    position = audio_position(probe)
    codec = streams[position].codec if position < len(streams) else None
    args = AudioInfoArgs(demuxer=demuxer_of(probe.container), audio_index=position)  # type: ignore[arg-type]
    await _set_audio(maker, sha, "pending", None)
    state: str = "none"
    data: dict[str, Any] | None = None
    try:
        job, _ = await client.call(Op.AUDIO_INFO, args, source=path)
        async with job:
            found = await job.result()
        state, data = gapless_of(found, codec)
    except client.WorkerFailed as exc:
        if exc.code not in analysis.FILE_ERRORS:
            raise
        state = "failed"
    finally:
        await _set_audio(maker, sha, state, data)
    return state


def _audio_applies(probe: Probe, kind: str | None) -> bool:
    return kind in ("music", None) and probe.video() is None and bool(probe.audio_streams())


async def _after_analysis(maker: async_sessionmaker[AsyncSession], sha: str) -> None:
    """De tags zijn er (de probe): een regroup voor elke bibliotheek met deze inhoud."""
    async with maker() as session:
        found = await session.scalars(
            select(MediaItem.library_id)
            .join(Node, Node.id == MediaItem.node_id)
            .where(Node.blob_id == sha, MediaItem.kind == "track")
            .distinct()
        )
        for library_id in found.all():
            await schedule(session, library_id)
        await session.commit()


async def _audio_step(ctx: analysis.StepContext) -> None:
    probe = ctx.probe
    assert probe is not None
    try:
        if probe.audio_state not in AUDIO_DONE:
            await read_audio(ctx.maker(), ctx.sha, ctx.path, probe)
    finally:
        if ctx.kind == "music":
            await _after_analysis(ctx.maker(), ctx.sha)


analysis.step("audio_info", 40, _audio_applies)(_audio_step)


# --- lezen ----------------------------------------------------------------------------------------


def track_of(item: MediaItem, tags: Any, duration_ms: int | None) -> Track:
    """Een nummer voor de API: de tags als ze er zijn, anders wat de scanner uit de naam haalde."""
    tags = tags if isinstance(tags, dict) else {}
    return Track(
        node_id=item.node_id,
        title=tag(tags, TITLE) or item.title,
        artist=tag(tags, ARTIST) or item.artist,
        album_id=item.album_id,
        disc=number(tag(tags, DISC), 99) or item.disc,
        track=number(tag(tags, TRACK)) or item.track,
        duration_ms=duration_ms,
    )


def track_order(t: Track) -> tuple:
    return (t.disc or 1, t.track or 10**6, norm(t.title), str(t.node_id))


def _live(library_ids: list[uuid.UUID]):
    """Per album het aantal aanwezige nummers en het laatst toegevoegde (nu, niet van de laatste
    regroup)."""
    return (
        select(
            MediaItem.album_id.label("album_id"),
            func.count().label("n"),
            func.max(MediaItem.added_at).label("added"),
        )
        .where(
            MediaItem.library_id.in_(library_ids),
            MediaItem.present.is_(True),
            MediaItem.kind == "track",
            MediaItem.album_id.is_not(None),
        )
        .group_by(MediaItem.album_id)
        .subquery()
    )


def _iso(value: Any) -> str:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return dt.datetime.fromisoformat(str(value)).isoformat()


async def _durations(session: AsyncSession, album_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    out: dict[uuid.UUID, int] = {}
    for chunk in _chunks(album_ids):
        rows = await session.execute(
            select(MediaItem.album_id, func.sum(MediaProbe.duration_ms))
            .join(Node, Node.id == MediaItem.node_id)
            .join(MediaProbe, MediaProbe.blob_sha == Node.blob_id)
            .where(
                MediaItem.album_id.in_(chunk),
                MediaItem.present.is_(True),
                MediaItem.kind == "track",
            )
            .group_by(MediaItem.album_id)
        )
        out.update({album_id: int(total or 0) for album_id, total in rows})
    return out


async def cards(
    reader: media_items.Reader, rows: Sequence[tuple[MediaAlbum, int]]
) -> list[AlbumCard]:
    """Kaarten voor deze albums (met het aantal nummers). De hoes: cover.jpg in de map, dan de
    ingebedde van het eerste nummer, dan een adres van een provider. Een plaatje dat niet meer
    in de bibliotheek ligt, telt niet (Reader.art_nodes)."""
    wanted: dict[uuid.UUID, tuple[uuid.UUID | None, uuid.UUID]] = {}
    for album, _ in rows:
        for role in ("cover", "thumb"):
            node = reader.art_node(album.art, role)
            if node is not None:
                wanted[node] = (node, album.library_id)
    ok = await reader.art_nodes(wanted)
    blobs = await reader.blobs_of(ok.values())
    durations = await _durations(reader.session, [album.id for album, _ in rows])
    out = []
    for album, count in rows:
        cover = None
        for role in ("cover", "thumb"):
            node = reader.art_node(album.art, role)
            if node is not None and node in ok:
                cover = reader.image(blobs.get(node))
                if cover:
                    break
        cover = cover or reader.art_url(album.art, "cover") or reader.art_url(album.art, "poster")
        out.append(
            AlbumCard(
                id=album.id,
                library_id=album.library_id,
                title=album.title,
                album_artist=album.album_artist,
                year=album.year,
                compilation=album.compilation,
                cover=cover,
                track_count=count,
                duration_ms=durations.get(album.id, 0),
            )
        )
    return out


async def album_page(
    reader: media_items.Reader, library: MediaLibrary, sort: str, cursor: str | None
) -> AlbumPage:
    """Eén pagina albums (keyset, ALBUMS_PAGE): op titel, op jaar (nieuwste eerst, zonder jaar
    achteraan) of op toegevoegd (het nieuwste nummer). Alleen albums met een aanwezig nummer."""
    live = _live([library.id])
    year_key = literal(0) - func.coalesce(MediaAlbum.year, 0)
    if sort == "added":
        columns: list[tuple[Any, bool]] = [(live.c.added, True), (MediaAlbum.id, True)]
        types = "du"
    elif sort == "year":
        columns = [(year_key, False), (MediaAlbum.sort_title, False), (MediaAlbum.id, False)]
        types = "isu"
    else:
        columns = [(MediaAlbum.sort_title, False), (MediaAlbum.id, False)]
        types = "su"
    query = (
        select(MediaAlbum, live.c.n, live.c.added)
        .join(live, live.c.album_id == MediaAlbum.id)
        .where(MediaAlbum.library_id == library.id)
    )
    if cursor:
        query = query.where(media_items.after(columns, media_items.decode_cursor(cursor, types)))
    query = query.order_by(*(c.desc() if d else c.asc() for c, d in columns))
    rows = list((await reader.session.execute(query.limit(ALBUMS_PAGE + 1))).all())
    more = len(rows) > ALBUMS_PAGE
    rows = rows[:ALBUMS_PAGE]
    next_cursor = None
    if more and rows:
        last, _, added = rows[-1]
        if sort == "added":
            values: list[Any] = [_iso(added), str(last.id)]
        elif sort == "year":
            values = [-(last.year or 0), last.sort_title, str(last.id)]
        else:
            values = [last.sort_title, str(last.id)]
        next_cursor = media_items.encode_cursor(values)
    found = await cards(reader, [(album, count) for album, count, _ in rows])
    return AlbumPage(items=found, next=next_cursor)


async def album_detail(reader: media_items.Reader, album_id: uuid.UUID) -> AlbumDetail:
    """Een album met zijn nummers, alleen wat nu onder de map van de bibliotheek ligt; een album
    in een bibliotheek die je niet ziet, of zonder zichtbare nummers, is 404."""
    session = reader.session
    album = await session.get(MediaAlbum, album_id)
    if album is None:
        raise media_library.not_found()
    await media_library.get_seen(session, reader.user, album.library_id)
    rows = list(
        (
            await session.execute(
                select(MediaItem, MediaProbe.tags, MediaProbe.duration_ms)
                .join(Node, Node.id == MediaItem.node_id)
                .outerjoin(MediaProbe, MediaProbe.blob_sha == Node.blob_id)
                .where(
                    MediaItem.album_id == album.id,
                    MediaItem.present.is_(True),
                    MediaItem.kind == "track",
                )
            )
        ).all()
    )
    ok = {i.node_id for i in await reader.visible([row[0] for row in rows])}
    tracks = sorted(
        (track_of(item, tags, duration) for item, tags, duration in rows if item.node_id in ok),
        key=track_order,
    )
    if not tracks:
        raise media_library.not_found()
    card = (await cards(reader, [(album, len(tracks))]))[0]
    info = AlbumInfo(
        **card.model_dump(),
        mbid=album.mbid,
        match_state=album.match_state,  # type: ignore[arg-type]
    )
    info.duration_ms = sum(max(0, t.duration_ms or 0) for t in tracks)
    return AlbumDetail(album=info, tracks=tracks)


async def _albums_of(
    session: AsyncSession, library_ids: list[uuid.UUID]
) -> list[tuple[MediaAlbum, int]]:
    if not library_ids:
        return []
    live = _live(library_ids)
    rows = await session.execute(
        select(MediaAlbum, live.c.n)
        .join(live, live.c.album_id == MediaAlbum.id)
        .where(MediaAlbum.library_id.in_(library_ids))
    )
    return [(album, count) for album, count in rows]


async def artists(session: AsyncSession, library: MediaLibrary) -> list[Artist]:
    """De albumartiesten van een bibliotheek met het aantal albums, gesorteerd (Keuze 37)."""
    counts: Counter[str] = Counter()
    shown: dict[str, Counter[str]] = defaultdict(Counter)
    for album, _ in await _albums_of(session, [library.id]):
        if not album.album_artist:
            continue
        key = artist_key(album.album_artist)
        counts[key] += 1
        shown[key][album.album_artist] += 1
    sorts = _sorts.get(library.id, {})
    out = [
        Artist(key=key, name=shown[key].most_common(1)[0][0], album_count=n)
        for key, n in counts.items()
    ]
    out.sort(key=lambda a: (sorts.get(a.key) or music_sort(a.name), a.key))
    return out


async def artist_detail(
    reader: media_items.Reader, key: str, library_id: uuid.UUID | None
) -> ArtistDetail:
    """Een artiest met zijn albums (op jaar, dan titel): in één bibliotheek, of in alle
    muziekbibliotheken die de vrager ziet. Niets gevonden: 404."""
    session = reader.session
    user: User = reader.user
    if library_id is not None:
        libraries = [(await media_library.get_seen(session, user, library_id)).library]
    else:
        libraries = [s.library for s in await media_library.seen(session, user)]
        libraries = [lib for lib in libraries if lib.kind == "music"]
    found = [
        (album, count)
        for album, count in await _albums_of(session, [lib.id for lib in libraries])
        if album.album_artist and artist_key(album.album_artist) == key
    ]
    if not found:
        raise media_library.not_found()
    found.sort(key=lambda r: (r[0].year is None, r[0].year or 0, r[0].sort_title, str(r[0].id)))
    name = Counter(album.album_artist for album, _ in found).most_common(1)[0][0]
    return ArtistDetail(name=name, albums=await cards(reader, found))
