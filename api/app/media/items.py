"""Items van een bibliotheek (Keuze 6–9): metadata zetten met locked, series groeperen, en wat de
web-app te zien krijgt (kaarten, de pagina van een item en van een serie).

Metadata (apply_metadata, ook voor M8):
- velden in `locked` blijven altijd staan (Keuze 43);
- lokale bronnen (name, nfo) vervangen elkaar: een NFO die erbij komt gaat voor de naam, en een
  NFO die weggaat geeft de naam terug. Een lokale bron overschrijft nooit de beschrijvende
  velden (titel, sorteertitel, jaar, samenvatting) van een provider of van tags; ids vult hij
  alleen aan;
- art per rol: lokaal is {"node": id} (een plaatje naast het bestand, Keuze 9), van een provider
  iets anders (M8). Een lokale ronde vervangt alleen lokale rollen.

Groeperen (Keuze 8): een aflevering hangt aan een serie via de map van de serie, anders via de
genormaliseerde titel. Delen (cd1, cd2) zijn items met part in dezelfde map met dezelfde titel en
jaar; de pagina van het item noemt ze allemaal.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, errors, previews
from app.config import Settings
from app.media import analysis, names, subtitles
from app.media import chapters as media_chapters
from app.media.library import Seen, get_seen
from app.media.types import (
    Card,
    CardPage,
    CardProgress,
    Item,
    ItemPart,
    Probe,
    Show,
    ShowDetail,
    ShowSeason,
    StreamBrief,
    SubtitleBrief,
)
from app.models import (
    Blob,
    Job,
    MediaItem,
    MediaLibrary,
    MediaProbe,
    MediaShow,
    Node,
    PlaybackState,
    User,
)

LOCAL = frozenset({"name", "nfo"})
# Beschrijvend: een lokale bron overschrijft die van een provider of van tags niet
DESCRIPTIVE = ("title", "sort_title", "year", "overview")
STRUCTURE = (
    "season",
    "episode",
    "episode_end",
    "absolute",
    "aired",
    "part",
    "edition",
    "artist",
    "disc",
    "track",
)
FIELDS = (*DESCRIPTIVE, "ids", "art", *STRUCTURE)
MATCH_STATES = ("local", "matched", "review", "manual")
MAX_OVERVIEW = 10_000
YEAR_RANGE = (1800, 2200)
LISTED = {"movie": ("movie",), "video": ("video",), "track": ("track",)}
RECENT = 20
ANALYSIS_STATES = frozenset({"none", "pending", "ready", "sparse", "failed", "skip"})


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def is_local_art(entry: Any) -> bool:
    return isinstance(entry, dict) and "node" in entry


def _int(value: Any, low: int = 0, high: int = 99_999) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if low <= value <= high else None


def _clean(name: str, value: Any) -> Any:
    """Een veld in de vorm van de kolom; wat niet past wordt None (of valt weg bij title)."""
    if value is None:
        return None
    if name in ("title", "sort_title"):
        text = " ".join(str(value).split())[:255]
        return text or None
    if name == "overview":
        text = str(value).strip()[:MAX_OVERVIEW]
        return text or None
    if name == "year":
        return _int(value, *YEAR_RANGE)
    if name in ("season", "episode", "episode_end", "absolute", "part", "disc", "track"):
        return _int(value)
    if name == "edition":
        return str(value).strip()[:64] or None
    if name == "artist":
        return str(value).strip()[:255] or None
    if name == "aired":
        if isinstance(value, dt.date):
            return value
        try:
            return dt.date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    if name == "ids":
        if not isinstance(value, dict):
            return {}
        return {
            str(k)[:16]: str(v)[:64]
            for k, v in value.items()
            if isinstance(v, (str, int)) and str(v)
        }
    if name == "art":
        return dict(value) if isinstance(value, dict) else {}
    return value


def merge_art(old: dict | None, new: dict, source: str) -> dict:
    """Art per rol. Lokaal: de lokale rollen worden precies `new`, die van een provider blijven.
    Van een provider: zijn rollen gaan voor (een lokale rol die hij niet noemt, blijft)."""
    old = dict(old or {})
    if source in LOCAL:
        kept = {role: entry for role, entry in old.items() if not is_local_art(entry)}
        return {**{r: e for r, e in new.items() if r not in kept}, **kept}
    return {**old, **new}


def merge(item: MediaItem | MediaShow, fields: dict[str, Any], source: str) -> list[str]:
    """Velden op een item of serie zetten (in het geheugen; de aanroeper commit). Geeft de
    namen van wat veranderde."""
    locked = set(item.locked or [])
    current = getattr(item, "source", None) or "name"
    behind = source in LOCAL and current not in LOCAL  # lokaal onder een provider of tags
    changed: list[str] = []
    wrote = False
    for name in FIELDS:
        if name not in fields or name in locked or not hasattr(item, name):
            continue
        if behind and name in DESCRIPTIVE:
            continue
        value = _clean(name, fields[name])
        if name == "title" and value is None:
            continue  # een titel is nooit leeg
        if name == "ids":
            value = {**value, **(item.ids or {})} if behind else value
        elif name == "art":
            value = merge_art(item.art, value, source)
        if name in DESCRIPTIVE:
            wrote = True
        if getattr(item, name) != value:
            setattr(item, name, value)
            changed.append(name)
    if (
        "title" in changed
        and "sort_title" not in fields
        and "sort_title" not in locked
        and item.title
    ):
        sort = names.sort_title(item.title) or item.title.casefold()[:255]
        if item.sort_title != sort:
            item.sort_title = sort
            changed.append("sort_title")
    state = fields.get("match_state")
    if state in MATCH_STATES and "match_state" not in locked and item.match_state != state:
        item.match_state = state
        changed.append("match_state")
    if isinstance(fields.get("candidates"), list):
        item.candidates = fields["candidates"][:5]
        changed.append("candidates")
    if wrote and hasattr(item, "source") and getattr(item, "source", None) != source:
        item.source = source  # type: ignore[union-attr]
        changed.append("source")
    if changed and hasattr(item, "updated_at"):
        item.updated_at = now()  # type: ignore[union-attr]
    return changed


async def apply_metadata(
    session: AsyncSession, node_id: uuid.UUID, fields: dict[str, Any], source: str
) -> None:
    """Metadata op een item zetten; velden in locked blijven staan (Keuze 43). Bekende velden:
    title, sort_title, year, overview, ids, art, season, episode, episode_end, absolute, aired,
    part, edition, artist, disc, track, match_state en candidates; de rest telt niet. source:
    name | nfo | tags | provider. De aanroeper commit (en stuurt media.item)."""
    item = await session.get(MediaItem, node_id)
    if item is None:
        return
    merge(item, fields, source)
    await session.flush()


# --- series -----------------------------------------------------------------------------------


class Shows:
    """De series van één bibliotheek in het geheugen, voor de scanner: zoeken op de map van de
    serie, of zonder map op de genormaliseerde titel; anders een nieuwe."""

    def __init__(self, library_id: uuid.UUID, rows: Iterable[MediaShow]):
        self.library_id = library_id
        self.by_folder: dict[uuid.UUID, MediaShow] = {}
        self.by_title: dict[str, MediaShow] = {}
        for show in rows:
            if show.folder_id is not None:
                self.by_folder[show.folder_id] = show
            else:
                self.by_title.setdefault(show.sort_title, show)

    @classmethod
    async def load(cls, session: AsyncSession, library_id: uuid.UUID) -> Shows:
        rows = await session.scalars(select(MediaShow).where(MediaShow.library_id == library_id))
        return cls(library_id, rows.all())

    def get(
        self,
        session: AsyncSession,
        folder_id: uuid.UUID | None,
        fields: dict[str, Any],
        source: str,
    ) -> MediaShow:
        title = _clean("title", fields.get("title")) or "?"
        sort = names.sort_title(title) or title.casefold()[:255]
        show = self.by_folder.get(folder_id) if folder_id else self.by_title.get(sort)
        if show is None:
            show = MediaShow(
                id=uuid.uuid4(),
                library_id=self.library_id,
                folder_id=folder_id,
                title=title,
                sort_title=sort,
                ids={},
                art={},
                locked=[],
                match_state="local",
                candidates=[],
            )
            session.add(show)
            if folder_id:
                self.by_folder[folder_id] = show
            else:
                self.by_title[sort] = show
        if show.match_state in ("local", "review"):
            merge(show, fields, source)
        else:  # gekoppeld: alleen lokale art en ids aanvullen
            merge(show, {k: v for k, v in fields.items() if k in ("art", "ids")}, source)
        return show


# --- lezen: kaarten, items en series ----------------------------------------------------------


def invalid_cursor():
    return errors.fail(422, "invalid_cursor", "Ongeldige cursor voor de lijst")


def encode_cursor(values: list[Any]) -> str:
    raw = json.dumps(values, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str, kinds: str) -> list[Any]:
    """kinds per waarde: s (tekst), i (getal), d (tijd) en u (uuid)."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) != len(kinds):
            raise ValueError
        out: list[Any] = []
        for kind, value in zip(kinds, values, strict=True):
            if kind == "s" and isinstance(value, str):
                out.append(value)
            elif kind == "i" and isinstance(value, int) and not isinstance(value, bool):
                out.append(value)
            elif kind == "d" and isinstance(value, str):
                out.append(dt.datetime.fromisoformat(value))
            elif kind == "u" and isinstance(value, str):
                out.append(uuid.UUID(value))
            else:
                raise ValueError
        return out
    except (ValueError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        raise invalid_cursor() from None


def after(columns: list[tuple[Any, bool]], values: list[Any]):
    """De rijen na deze cursor bij deze volgorde (kolom, aflopend): de eerste kolom die
    verschilt, beslist."""
    clauses = []
    for i, (column, desc) in enumerate(columns):
        same = [c == v for (c, _), v in zip(columns[:i], values[:i], strict=True)]
        clauses.append(and_(*same, column < values[i] if desc else column > values[i]))
    return or_(*clauses)


class Reader:
    """Wat één vraag nodig heeft om kaarten te maken: de gebruiker en de sleutel van de
    previewadressen."""

    def __init__(self, session: AsyncSession, settings: Settings, user: User):
        self.session = session
        self.settings = settings
        self.user = user
        self.secret = previews.url_key(settings)
        self.exp = previews.expiry()

    async def blobs_of(self, node_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Blob]:
        """De blob per node (levend), voor de previews van plaatjes en video's."""
        ids = list(dict.fromkeys(i for i in node_ids if i is not None))
        if not ids:
            return {}
        rows = await self.session.execute(
            select(Node.id, Blob).join(Blob, Blob.sha256 == Node.blob_id).where(Node.id.in_(ids))
        )
        return {node_id: blob for node_id, blob in rows}

    def image(self, blob: Blob | None, large: bool = False) -> str | None:
        if blob is None:
            return None
        info = previews.info(self.secret, blob, self.exp)
        return (info["large"] if large else info["thumb"]) or info["thumb"] or info["large"]

    @staticmethod
    def art_node(art: dict | None, role: str) -> uuid.UUID | None:
        entry = (art or {}).get(role)
        if not is_local_art(entry):
            return None
        try:
            return uuid.UUID(str(entry["node"]))
        except ValueError:
            return None

    @staticmethod
    def art_url(art: dict | None, role: str) -> str | None:
        """Een plaatje van een provider met een eigen adres (M8 zet het als {"url": ...})."""
        entry = (art or {}).get(role)
        if isinstance(entry, dict) and isinstance(entry.get("url"), str):
            url = entry["url"]
            return url if url.startswith("/api/") else None
        return None

    async def progress(self, node_ids: list[uuid.UUID]) -> dict[uuid.UUID, PlaybackState]:
        if not node_ids:
            return {}
        rows = await self.session.scalars(
            select(PlaybackState).where(
                PlaybackState.user_id == self.user.id,
                PlaybackState.profile == "",
                PlaybackState.node_id.in_(node_ids),
            )
        )
        return {p.node_id: p for p in rows}

    async def cards(self, items: list[MediaItem]) -> list[Card]:
        """Kaarten voor deze items, met poster, voortgang en gezien: drie queries."""
        posters = {i.node_id: self.art_node(i.art, "poster") or self.art_node(i.art, "thumb")
                   for i in items}  # fmt: skip
        blobs = await self.blobs_of([*posters.values(), *(i.node_id for i in items)])
        states = await self.progress([i.node_id for i in items])
        out = []
        for i in items:
            poster = self.art_url(i.art, "poster")
            if poster is None:
                art = posters.get(i.node_id)
                poster = self.image(blobs.get(art)) if art else None
            if poster is None:
                poster = self.image(blobs.get(i.node_id))
            out.append(self.card(i, poster, states.get(i.node_id)))
        return out

    @staticmethod
    def card(item: MediaItem, poster: str | None, state: PlaybackState | None) -> Card:
        progress = None
        if state is not None and not state.watched and state.position_ms > 0:
            progress = CardProgress(position_ms=state.position_ms, duration_ms=state.duration_ms)
        return Card(
            node_id=item.node_id,
            library_id=item.library_id,
            title=item.title,
            year=item.year,
            kind=item.kind,  # type: ignore[arg-type]
            poster=poster,
            progress=progress,
            watched=bool(state and state.watched),
            show_id=item.show_id,
            season=item.season,
            episode=item.episode,
        )

    async def show_cards(self, shows: list[MediaShow]) -> list[Card]:
        arts = {s.id: self.art_node(s.art, "poster") for s in shows}
        blobs = await self.blobs_of(arts.values())
        # Zonder poster: het eerste plaatje van een aflevering
        missing = [s.id for s in shows if not arts.get(s.id) and not self.art_url(s.art, "poster")]
        first: dict[uuid.UUID, uuid.UUID] = {}
        if missing:
            rows = await self.session.execute(
                select(MediaItem.show_id, func.min(MediaItem.node_id))
                .where(MediaItem.show_id.in_(missing), MediaItem.present.is_(True))
                .group_by(MediaItem.show_id)
            )
            first = {show_id: node_id for show_id, node_id in rows}
            blobs.update(await self.blobs_of(first.values()))
        out = []
        for s in shows:
            poster = self.art_url(s.art, "poster")
            if poster is None and arts.get(s.id):
                poster = self.image(blobs.get(arts[s.id]))
            if poster is None and s.id in first:
                poster = self.image(blobs.get(first[s.id]))
            out.append(
                Card(
                    node_id=s.folder_id,
                    library_id=s.library_id,
                    title=s.title,
                    year=s.year,
                    kind="show",
                    poster=poster,
                    show_id=s.id,
                )
            )
        return out


def default_type(kind: str) -> str:
    return {"movies": "movie", "shows": "show", "videos": "video", "music": "track"}.get(
        kind, "movie"
    )


async def page(
    reader: Reader,
    library: MediaLibrary,
    type: str | None,
    sort: str,
    cursor: str | None,
    limit: int,
) -> CardPage:
    """Eén pagina van de lijst van een bibliotheek, met een cursor (keyset): zonder extra's en
    zonder wat in de prullenbak ligt."""
    wanted = type or default_type(library.kind)
    if wanted == "show":
        return await _show_page(reader, library, sort, cursor, limit)
    kinds = LISTED.get(wanted, (wanted,))
    year_key = literal(0) - func.coalesce(MediaItem.year, 0)  # zonder jaar achteraan
    if sort == "added":
        columns = [(MediaItem.added_at, True), (MediaItem.node_id, True)]
        types = "du"
    elif sort == "year":
        columns = [(year_key, False), (MediaItem.sort_title, False), (MediaItem.node_id, False)]
        types = "isu"
    else:
        columns = [(MediaItem.sort_title, False), (MediaItem.node_id, False)]
        types = "su"
    query = select(MediaItem).where(
        MediaItem.library_id == library.id,
        MediaItem.present.is_(True),
        MediaItem.kind.in_(kinds),
    )
    if cursor:
        query = query.where(after(columns, decode_cursor(cursor, types)))
    query = query.order_by(*(c.desc() if d else c.asc() for c, d in columns)).limit(limit + 1)
    rows = list((await reader.session.scalars(query)).all())
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more and rows:
        last = rows[-1]
        if sort == "added":
            values: list[Any] = [last.added_at.isoformat(), str(last.node_id)]
        elif sort == "year":
            values = [-(last.year or 0), last.sort_title, str(last.node_id)]
        else:
            values = [last.sort_title, str(last.node_id)]
        next_cursor = encode_cursor(values)
    return CardPage(items=await reader.cards(rows), next=next_cursor)


async def _show_page(
    reader: Reader, library: MediaLibrary, sort: str, cursor: str | None, limit: int
) -> CardPage:
    latest = (
        select(MediaItem.show_id.label("show_id"), func.max(MediaItem.added_at).label("added"))
        .where(MediaItem.library_id == library.id, MediaItem.present.is_(True))
        .where(MediaItem.show_id.is_not(None))
        .group_by(MediaItem.show_id)
        .subquery()
    )
    year_key = literal(0) - func.coalesce(MediaShow.year, 0)
    if sort == "added":
        columns = [(latest.c.added, True), (MediaShow.id, True)]
        types = "du"
    elif sort == "year":
        columns = [(year_key, False), (MediaShow.sort_title, False), (MediaShow.id, False)]
        types = "isu"
    else:
        columns = [(MediaShow.sort_title, False), (MediaShow.id, False)]
        types = "su"
    query = (
        select(MediaShow, latest.c.added)
        .join(latest, latest.c.show_id == MediaShow.id)
        .where(MediaShow.library_id == library.id)
    )
    if cursor:
        query = query.where(after(columns, decode_cursor(cursor, types)))
    query = query.order_by(*(c.desc() if d else c.asc() for c, d in columns)).limit(limit + 1)
    rows = list((await reader.session.execute(query)).all())
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more and rows:
        last, added = rows[-1]
        if sort == "added":
            values: list[Any] = [_iso(added), str(last.id)]
        elif sort == "year":
            values = [-(last.year or 0), last.sort_title, str(last.id)]
        else:
            values = [last.sort_title, str(last.id)]
        next_cursor = encode_cursor(values)
    return CardPage(items=await reader.show_cards([s for s, _ in rows]), next=next_cursor)


def _iso(value: Any) -> str:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return dt.datetime.fromisoformat(str(value)).isoformat()


async def recent(reader: Reader, library_ids: list[uuid.UUID], limit: int = RECENT) -> list[Card]:
    """Net toegevoegd in deze bibliotheken (films, afleveringen en video's)."""
    if not library_ids:
        return []
    rows = await reader.session.scalars(
        select(MediaItem)
        .where(
            MediaItem.library_id.in_(library_ids),
            MediaItem.present.is_(True),
            MediaItem.kind.in_(("movie", "episode", "video")),
        )
        .order_by(MediaItem.added_at.desc(), MediaItem.node_id.desc())
        .limit(limit)
    )
    return await reader.cards(list(rows.all()))


async def can_write(session: AsyncSession, user: User, node: Node) -> bool:
    """Mag deze gebruiker het bestand bewerken (de eigenaar, of een deling met schrijven)?"""
    if node.owner_id == user.id:
        return user.role != "guest"
    grant = (await access.resolve(session, user.id, [node.id])).get(node.id)
    return grant is not None and grant.permission == "write"


async def visible_item(reader: Reader, node_id: uuid.UUID) -> tuple[MediaItem, Node, Seen]:
    """Een item uit een bibliotheek die de vrager ziet, niet in de prullenbak; anders 404."""
    session = reader.session
    item = await session.get(MediaItem, node_id)
    node = await session.get(Node, node_id)
    if item is None or node is None or not item.present or node.kind != "file":
        raise errors.fail(404, "not_found", "Niet gevonden")
    seen = await get_seen(session, reader.user, item.library_id)
    if node.owner_id == reader.user.id and await access.trashed(session, node_id):
        raise errors.fail(404, "not_found", "Niet gevonden")
    return item, node, seen


async def _probe(session: AsyncSession, sha: str | None) -> Probe | None:
    if not sha:
        return None
    row = await session.get(MediaProbe, sha)
    if row is None or row.version < analysis.PROBE_VERSION:
        return None
    return Probe.model_validate(row, from_attributes=True)


def _analysis(probe: Probe | None, queued: bool, kind: str | None) -> dict[str, str]:
    steps = analysis.steps()
    if probe is None:
        return {"probe": "pending" if queued else "none"}
    out: dict[str, str] = {"probe": probe.state}
    if probe.state != "ready":
        return out
    states = {
        "keyframes": probe.keyframes_state,
        "subs": probe.subs_state,
        "sprites": probe.trick_state,
        "audio_info": probe.audio_state,
    }
    for step in steps:
        if step.name == "probe":
            continue
        if not step.applies(probe, kind):
            out[step.name] = "skip"
            continue
        value = states.get(step.name, "none")
        out[step.name] = value if value in ANALYSIS_STATES else "none"
    return out


def _streams(probe: Probe | None) -> list[StreamBrief]:
    out = []
    for s in probe.streams if probe else []:
        if s.type not in ("video", "audio", "subtitle") or s.attached_pic:
            continue
        title = " ".join((s.title or "").split())[:100] or None
        out.append(
            StreamBrief(
                index=s.index,
                type=s.type,
                codec=s.codec or "?",
                lang=subtitles.lang_code(s.lang),
                title=title,
            )  # fmt: skip
        )
    return out


def _embedded(probe: Probe | None) -> list[SubtitleBrief]:
    if probe is None:
        return []
    kinds = {f"e{s.index}": "image" if subtitles.is_image(s) else "text" for s in probe.streams}
    out = []
    for entry in subtitles.tracks(probe):
        if entry["id"] not in kinds or len(entry["id"]) > 4:
            continue
        out.append(
            SubtitleBrief(
                id=entry["id"],
                lang=entry.get("lang"),
                label=entry["label"] or entry["id"],
                forced=entry["forced"],
                sdh=entry["sdh"],
                kind=kinds[entry["id"]],
            )  # fmt: skip
        )
    return out


async def detail(reader: Reader, node_id: uuid.UUID) -> Item:
    """De pagina van een film, aflevering, video of extra."""
    from app.routers import media_play

    session = reader.session
    item, node, seen = await visible_item(reader, node_id)
    probe = await _probe(session, node.blob_id)
    queued = False
    if probe is None and node.blob_id:
        queued = (
            await session.scalar(
                select(Job.id)
                .where(
                    Job.key == f"{analysis.JOB}:{node.blob_id}",
                    Job.status.in_(("queued", "running")),
                )
                .limit(1)
            )
        ) is not None
    card = (await reader.cards([item]))[0]
    backdrop = reader.art_url(item.art, "backdrop")
    art = reader.art_node(item.art, "backdrop")
    if backdrop is None and art is not None:
        backdrop = reader.image((await reader.blobs_of([art])).get(art), large=True)
    loose = await media_play.allowed_subs(
        session, media_play.Viewer(user=reader.user), item.subs or []
    )
    subs = _embedded(probe)
    for i, sub in enumerate(loose[:1000]):
        lang = sub.get("lang")
        subs.append(
            SubtitleBrief(
                id=f"x{i}",
                lang=lang,
                label=(lang or "").upper() or f"#{i + 1}",
                forced=sub["forced"],
                sdh=sub["sdh"],
                kind="text",
            )  # fmt: skip
        )
    parts: list[ItemPart] = []
    if item.part is not None and node.parent_id is not None:
        rows = await session.execute(
            select(MediaItem.node_id, MediaItem.part)
            .join(Node, Node.id == MediaItem.node_id)
            .where(
                Node.parent_id == node.parent_id,
                MediaItem.library_id == item.library_id,
                MediaItem.present.is_(True),
                MediaItem.part.is_not(None),
                MediaItem.sort_title == item.sort_title,
                MediaItem.year.is_(None) if item.year is None else MediaItem.year == item.year,
            )
            .order_by(MediaItem.part, MediaItem.node_id)
        )
        parts = [ItemPart(node_id=n, part=p) for n, p in rows]
    return Item(
        **card.model_dump(exclude={"node_id", "library_id"}),
        node_id=item.node_id,
        library_id=item.library_id,
        overview=item.overview,
        backdrop=backdrop,
        duration_ms=probe.duration_ms if probe else None,
        streams=_streams(probe),
        subtitles=subs,
        chapters=media_chapters.items(probe) if probe and probe.state == "ready" else [],
        match_state=item.match_state,  # type: ignore[arg-type]
        can_edit=await can_write(session, reader.user, node),
        parts=parts,
        analysis=_analysis(probe, queued, seen.library.kind),  # type: ignore[arg-type]
        extra=item.extra,  # type: ignore[arg-type]
        error=probe.error if probe and probe.state == "failed" else None,
    )


SEASON_LAST = 10**6  # specials (seizoen 0) achteraan, zoals Jellyfin


async def show_detail(reader: Reader, show_id: uuid.UUID) -> ShowDetail:
    """Een serie met zijn seizoenen en afleveringen; absolute nummering staat in één groep
    zonder seizoen (Keuze 8)."""
    session = reader.session
    show = await session.get(MediaShow, show_id)
    if show is None:
        raise errors.fail(404, "not_found", "Niet gevonden")
    seen = await get_seen(session, reader.user, show.library_id)
    rows = await session.scalars(
        select(MediaItem).where(
            MediaItem.show_id == show.id,
            MediaItem.present.is_(True),
            MediaItem.kind == "episode",
        )
    )
    episodes = sorted(
        rows.all(),
        key=lambda i: (
            i.episode if i.episode is not None else (i.absolute or 0),
            i.aired or dt.date.min,
            i.sort_title,
            str(i.node_id),
        ),
    )
    if not episodes:
        raise errors.fail(404, "not_found", "Niet gevonden")
    cards = await reader.cards(episodes)
    groups: dict[int | None, list[Card]] = {}
    for item, card in zip(episodes, cards, strict=True):
        groups.setdefault(item.season, []).append(card)
    order = sorted(groups, key=lambda s: -1 if s is None else SEASON_LAST if s == 0 else s)
    [show_card] = await reader.show_cards([show])
    backdrop = reader.art_url(show.art, "backdrop")
    art = reader.art_node(show.art, "backdrop")
    if backdrop is None and art is not None:
        backdrop = reader.image((await reader.blobs_of([art])).get(art), large=True)
    return ShowDetail(
        show=Show(
            id=show.id,
            library_id=show.library_id,
            title=show.title,
            year=show.year,
            overview=show.overview,
            poster=show_card.poster,
            backdrop=backdrop,
            match_state=show.match_state,  # type: ignore[arg-type]
            can_edit=seen.can_edit,
        ),
        seasons=[ShowSeason(season=s, episodes=groups[s]) for s in order],
    )
