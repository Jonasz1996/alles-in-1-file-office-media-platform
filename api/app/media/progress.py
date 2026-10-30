"""Kijkvoortgang (Keuze 36, M7): de regels, het geheugen met gebundeld schrijven, en de rijen
van /media/home.

Eerst het event, dan de database. De app houdt de laatste stand per gebruiker en bestand in het
geheugen (Store) en publiceert elke stand die telt meteen als playback.progress, alleen naar de
gebruiker zelf. Naar playback_state schrijft hij hooguit eens per 10 s per gebruiker (alles van
die gebruiker in één transactie), en meteen bij pauze, stoppen, het einde, gezien en een batch.
Een crash verliest dus hooguit 10 s; netjes stoppen schrijft alles weg (stop, vanuit
runtime.stop).

De volgorde (een PUT kan laat komen):
- een (origin, seq) die niet nieuwer is dan de laatste van die origin telt niet;
- een PUT van een sessie die begon vóór watched_at (de laatste overgang naar gezien, of het
  laatste end) telt niet;
- gezien gaat alleen weg door "Niet gezien" (batch) of door een nieuwe sessie die verder komt
  dan 60 s.
Wat niet telt, geeft 204 zonder event. Ticks hooguit één per 5 s per gebruiker en bestand (429).

Gezien: positie ≥ 90 %, of voorbij het begin van de aftiteling (chapters.credits_ms van de
probe), of minder dan 3 minuten van het eind (alleen bij items langer dan 10 minuten). Dan
watched, play_count + 1 (één keer per sessie) en positie 0. Een vervangen bestand (blob_sha
anders én de duur meer dan 5 % anders): positie terug naar 0.

Sloten: per gebruiker een asyncio.Lock. Wie hem neemt, heeft geen transactie open (de router
commit eerst), en binnen het slot gebruikt de Store alleen zijn eigen sessies; er wordt nooit op
een segment, de worker of een stroom gewacht.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime as dt
import hashlib
import logging
import math
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import UTC
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import events
from app.config import Settings
from app.db import get_engine
from app.media import analysis, chapters, runtime
from app.media import items as media_items
from app.media.types import (
    EVENT_PROGRESS,
    Card,
    CardProgress,
    PlaybackProgressEvent,
    ProgressIn,
    event_data,
)
from app.models import MediaItem, MediaProbe, Node, PlaybackState, User

log = logging.getLogger("platform.media")

ROWS = ("continue", "next_up", "recent")

PROFILE = ""  # fase 5: altijd de gebruiker zelf (Vraag 6)
WATCHED_SHARE = 0.9  # gezien vanaf 90 %
END_MARGIN_MS = 3 * 60_000  # minder dan 3 minuten van het eind ...
LONG_MS = 10 * 60_000  # ... alleen bij items langer dan 10 minuten
RESUME_MS = 60_000  # Verder kijken vanaf 60 s; een nieuwe sessie daarna haalt gezien weg
REPLACED_SHARE = 0.05  # een ander bestand met meer dan 5 % andere duur
TICK_EVERY = 5.0  # seconden tussen twee ticks per gebruiker en bestand
WRITE_EVERY = 10.0  # seconden tussen twee schrijfacties per gebruiker
CONTINUE_MAX = 20
NEXT_UP_MAX = 20
ACTIVE_DAYS = 90  # Volgende aflevering: alleen series met activiteit in deze dagen
SCAN_MAX = 200  # zoveel kandidaten bekijkt Verder kijken hooguit
SHOWS_MAX = 50  # zoveel series bekijkt Volgende aflevering hooguit
KINDS = ("movie", "episode", "video")
ORIGINS_MAX = 16  # de laatste seq van zoveel tabbladen en apparaten per bestand
SESSIONS_MAX = 8  # de eerste positie van zoveel sessies per bestand
IDLE_S = 3600.0  # een schone stand blijft zo lang in het geheugen
ENTRIES_MAX = 20_000  # daarboven gaan de oudste schone standen eruit
INFO_MAX = 1024  # duur en aftiteling van zoveel blobs bewaard
BATCH_EVENTS = 20  # meer nodes in één batch: alleen de eerste zoveel als event
BATCH_ORIGIN = "batch"  # de origin van een event uit een batch
CHUNK = 500  # ids per IN (...)
RETRY_S = 1.0  # de schrijver op de achtergrond na een fout
# De grenzen van een PUT en een batch (review golf 4, S2): wat erboven ligt, kan SQLite niet
# bewaren (INTEGER is 64 bits) of is onzin; de router geeft 422 invalid_input, apply() knipt af
MS_MAX = 10**10  # positie en duur: ruim 115 dagen
SEQ_MAX = 2**62
AUDIO_MAX = 9999  # de index van een audiospoor
SEASON_MAX = 9999
REPLACED_POLL_S = 5.0  # een nieuwe versie: zo vaak kijken of de probe er is (K2)
REPLACED_WAIT_S = 1800.0  # en hooguit zo lang

Outcome = Literal["ok", "ignored", "rate_limited"]
EPOCH = dt.datetime(1970, 1, 1, tzinfo=UTC)


# De regels (puur)


def is_watched(position_ms: int, duration_ms: int, credits_ms: int | None) -> bool:
    """Gezien volgens Keuze 36: 90 %, de aftiteling, of 3 minuten van het eind (lange items)."""
    if duration_ms <= 0:
        return False
    if position_ms >= WATCHED_SHARE * duration_ms:
        return True
    if credits_ms is not None and 0 < credits_ms < duration_ms and position_ms >= credits_ms:
        return True
    return duration_ms > LONG_MS and duration_ms - position_ms < END_MARGIN_MS


def replaced(old_sha: str | None, old_ms: int, new_sha: str | None, new_ms: int | None) -> bool:
    """Een ander bestand (andere blob) met een duur die meer dan 5 % afwijkt. Zonder bekende
    duur aan een van beide kanten: niet vervangen (de positie blijft)."""
    if not old_sha or not new_sha or old_sha == new_sha:
        return False
    if old_ms <= 0 or not new_ms or new_ms <= 0:
        return False
    return abs(new_ms - old_ms) > REPLACED_SHARE * old_ms


def origin_key(origin: str) -> str:
    """De origin zoals hij in playback_state past (36 tekens). Een langere (de API laat er 64
    toe) wordt een vaste hash, zodat twee lange origins nooit samenvallen."""
    if len(origin) <= 36:
        return origin
    return "h" + hashlib.sha256(origin.encode()).hexdigest()[:35]


def aware(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite geeft tijden zonder zone terug; alles hier is UTC. Een tijd die in UTC buiten het
    bereik van datetime valt (jaar 1 met +01:00): EPOCH, een heel oude sessie (review golf 4,
    S4)."""
    if value is None:
        return None
    try:
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    except (OverflowError, ValueError):
        return EPOCH


def valid(body: ProgressIn) -> bool:
    """Past deze PUT binnen de grenzen (review golf 4, S2)? Zo niet: 422 in de router."""
    if body.position_ms > MS_MAX or body.duration_ms > MS_MAX or body.seq > SEQ_MAX:
        return False
    if body.audio is not None and not 0 <= body.audio <= AUDIO_MAX:
        return False
    try:
        body.started_at.astimezone(UTC)
    except (OverflowError, ValueError):
        return False
    return True


def _bad_value(exc: BaseException) -> bool:
    """Een fout in de waarden van één stand (te groot voor SQLite), niet van de database."""
    bad = (OverflowError, ValueError, TypeError)
    return isinstance(exc, bad) or isinstance(getattr(exc, "orig", None), bad)


def _remember(store: OrderedDict, key, value, limit: int) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > limit:
        store.popitem(last=False)


@dataclasses.dataclass
class Entry:
    """De stand van één gebruiker en bestand in het geheugen, plus wat alleen het geheugen
    weet: de laatste seq per origin, de eerste positie per sessie, welke sessies al telden, en
    de laatste tick (voor de grens)."""

    user_id: uuid.UUID
    node_id: uuid.UUID
    position_ms: int = 0
    duration_ms: int = 0
    watched: bool = False
    play_count: int = 0
    audio: int | None = None
    subtitle: str | None = None
    blob_sha: str | None = None
    origin: str | None = None
    seq: int = 0
    play_started_at: dt.datetime | None = None
    watched_at: dt.datetime | None = None
    updated_at: dt.datetime = EPOCH
    origins: OrderedDict[str, int] = dataclasses.field(default_factory=OrderedDict)
    sessions: OrderedDict[dt.datetime, int] = dataclasses.field(default_factory=OrderedDict)
    counted: OrderedDict[dt.datetime, None] = dataclasses.field(default_factory=OrderedDict)
    last_tick: float = -math.inf
    touched: float = 0.0
    dirty: bool = False

    @classmethod
    def from_row(cls, row: PlaybackState) -> Entry:
        e = cls(
            user_id=row.user_id,
            node_id=row.node_id,
            position_ms=row.position_ms or 0,
            duration_ms=row.duration_ms or 0,
            watched=bool(row.watched),
            play_count=row.play_count or 0,
            audio=row.audio,
            subtitle=row.subtitle,
            blob_sha=row.blob_sha,
            origin=row.origin,
            seq=row.seq or 0,
            play_started_at=aware(row.play_started_at),
            watched_at=aware(row.watched_at),
            updated_at=aware(row.updated_at) or EPOCH,
        )
        if row.origin:
            e.origins[row.origin] = row.seq or 0
        return e

    def fill(self, row: PlaybackState) -> None:
        row.position_ms = self.position_ms
        row.duration_ms = self.duration_ms
        row.watched = self.watched
        row.play_count = self.play_count
        row.audio = self.audio
        row.subtitle = self.subtitle
        row.blob_sha = self.blob_sha
        row.origin = self.origin
        row.seq = self.seq
        row.play_started_at = self.play_started_at
        row.watched_at = self.watched_at
        row.updated_at = self.updated_at


@dataclasses.dataclass(frozen=True)
class View:
    """Wat een rij van een stand nodig heeft (uit de database of het geheugen)."""

    position_ms: int
    duration_ms: int
    watched: bool
    watched_at: dt.datetime | None
    updated_at: dt.datetime
    blob_sha: str | None

    @classmethod
    def of(cls, e: Entry | PlaybackState) -> View:
        return cls(
            position_ms=e.position_ms or 0,
            duration_ms=e.duration_ms or 0,
            watched=bool(e.watched),
            watched_at=aware(e.watched_at),
            updated_at=aware(e.updated_at) or EPOCH,
            blob_sha=e.blob_sha,
        )


def apply(
    e: Entry,
    body: ProgressIn,
    sha: str | None,
    info: tuple[int | None, int | None],
    started: dt.datetime,
    now: dt.datetime,
) -> tuple[bool, bool]:
    """Een PUT die door de volgorde kwam op de stand toepassen: (telt, meteen schrijven). Een
    vervangen bestand hoeft hier niets: de positie van de PUT gaat over het nieuwe bestand (de
    regel geldt bij het lezen, in Verder kijken)."""
    known, credits = info
    duration = min(body.duration_ms or known or e.duration_ms, MS_MAX)
    position = min(body.position_ms, duration) if duration > 0 else min(body.position_ms, MS_MAX)
    if started not in e.sessions:
        _remember(e.sessions, started, position, SESSIONS_MAX)
    first = e.sessions[started]
    done = body.event == "end" or is_watched(position, duration, credits)
    urgent = body.event in ("pause", "stop", "end")
    if done:
        # Eén keer per sessie. Was het al gezien, dan telt alleen een nieuwe kijkbeurt van voren
        # af (een kort item opnieuw), niet een nieuwe sessie die bij de aftiteling instapt.
        count = started not in e.counted and (not e.watched or first <= RESUME_MS)
        if count:
            e.play_count += 1
            _remember(e.counted, started, None, SESSIONS_MAX)
        if not e.watched or count or body.event == "end":
            e.watched_at = now
        elif e.position_ms == 0:
            return False, False  # al gezien, en deze sessie verandert niets
        urgent = urgent or not e.watched or count
        e.watched = True
        e.position_ms = 0
    elif e.watched:
        if position <= RESUME_MS:
            return False, False  # gezien blijft tot een nieuwe sessie verder komt dan 60 s
        e.watched = False
        e.position_ms = position
        urgent = True
    else:
        e.position_ms = position
    e.duration_ms = duration
    e.blob_sha = sha
    e.audio = body.audio if body.audio is None or 0 <= body.audio <= AUDIO_MAX else None
    e.subtitle = body.subtitle
    e.origin = origin_key(body.origin)
    e.seq = min(body.seq, SEQ_MAX)
    e.play_started_at = started
    e.updated_at = now
    e.dirty = True
    return True, urgent


def apply_batch(e: Entry, watched: bool, now: dt.datetime) -> bool:
    """Gezien markeren of "Niet gezien": positie 0. Gezien zet watched_at, zodat een tik van
    een sessie die nog liep niets terugzet; niet gezien laat watched_at staan."""
    if watched:
        if e.watched:
            return False
        e.watched = True
        e.play_count += 1
        e.watched_at = now
    else:
        if not e.watched and e.position_ms == 0:
            return False
        e.watched = False
    e.position_ms = 0
    e.updated_at = now
    e.dirty = True
    return True


class Store:
    """Het geheugen van de voortgang, met een eigen sessiemaker en een schrijver op de
    achtergrond (background=False: tests roepen flush_due zelf aan, met een eigen klok)."""

    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        *,
        url: str = "",
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], dt.datetime] = lambda: dt.datetime.now(UTC),
        background: bool = True,
    ):
        self.maker = maker
        self.url = url
        self.clock = clock
        self.wall = wall
        self.background = background
        self.tick_every = TICK_EVERY
        self.write_every = WRITE_EVERY
        self.entries: dict[uuid.UUID, dict[uuid.UUID, Entry]] = {}
        self.locks: dict[uuid.UUID, asyncio.Lock] = {}
        self.last_write: dict[uuid.UUID, float] = {}
        self.due: dict[uuid.UUID, float] = {}
        self.info: dict[str, tuple[int | None, int | None]] = {}
        self.writes = 0  # schrijfacties (transacties), voor de tests
        self.closed = False
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None

    # In het geheugen

    def lock(self, user_id: uuid.UUID) -> asyncio.Lock:
        return self.locks.setdefault(user_id, asyncio.Lock())

    def size(self) -> int:
        return sum(len(by) for by in self.entries.values())

    def snapshot(self, user_id: uuid.UUID) -> dict[uuid.UUID, View]:
        """De standen van deze gebruiker in het geheugen (nieuwer dan of gelijk aan de
        database). Zonder await, dus in één keer."""
        return {n: View.of(e) for n, e in self.entries.get(user_id, {}).items()}

    async def _entries(self, user_id: uuid.UUID, node_ids: list[uuid.UUID]) -> list[Entry]:
        """Met het slot: de standen van deze bestanden, wat ontbreekt uit de database (één
        query per 500)."""
        by = self.entries.setdefault(user_id, {})
        missing = [n for n in dict.fromkeys(node_ids) if n not in by]
        rows: dict[uuid.UUID, PlaybackState] = {}
        if missing:
            async with self.maker() as session:
                rows = await _rows(session, user_id, missing)
        now = self.clock()
        out = []
        for n in dict.fromkeys(node_ids):
            e = by.get(n)
            if e is None:
                row = rows.get(n)
                e = Entry.from_row(row) if row is not None else Entry(user_id, n)
                by[n] = e
            e.touched = now
            out.append(e)
        return out

    async def media_info(self, sha: str | None) -> tuple[int | None, int | None]:
        """(duur, begin van de aftiteling) van een blob uit zijn probe, bewaard. Zonder probe
        (nog niet geanalyseerd) niets bewaren: straks is hij er misschien wel."""
        if not sha:
            return None, None
        if sha in self.info:
            return self.info[sha]
        probe = await analysis.load(self.maker, sha)
        if probe is None or probe.state != "ready":
            return None, None
        info = (probe.duration_ms, chapters.credits_ms(probe))
        if len(self.info) >= INFO_MAX:
            self.info.clear()
        self.info[sha] = info
        return info

    # De vragen

    async def put(
        self, user_id: uuid.UUID, node_id: uuid.UUID, sha: str | None, body: ProgressIn
    ) -> Outcome:
        """Een PUT van de speler. De aanroeper heeft geen transactie open."""
        info = await self.media_info(sha)
        async with self.lock(user_id):
            [e] = await self._entries(user_id, [node_id])
            if body.event == "tick":
                mono = self.clock()
                if mono - e.last_tick < self.tick_every:
                    return "rate_limited"
                e.last_tick = mono
            key = origin_key(body.origin)
            last = e.origins.get(key)
            if last is not None and body.seq <= last:
                return "ignored"
            _remember(e.origins, key, body.seq, ORIGINS_MAX)
            now = self.wall()
            started = min(aware(body.started_at) or EPOCH, now)
            if e.watched_at is not None and started < e.watched_at:
                return "ignored"
            counts, urgent = apply(e, body, sha, info, started, now)
            if not counts:
                return "ignored"
            self._publish(e, body.origin)
            await self._after_change(user_id, urgent)
        self._evict_if_full()
        return "ok"

    async def batch(self, user_id: uuid.UUID, node_ids: list[uuid.UUID], watched: bool) -> int:
        """Gezien markeren of niet gezien: hoeveel standen veranderden. Schrijft meteen."""
        if not node_ids:
            return 0
        async with self.lock(user_id):
            now = self.wall()
            found = await self._entries(user_id, node_ids)
            changed = [e for e in found if apply_batch(e, watched, now)]
            for e in changed[:BATCH_EVENTS]:
                self._publish(e, BATCH_ORIGIN)
            if changed:
                await self._write(user_id)
        self._evict_if_full()
        return len(changed)

    def _publish(self, e: Entry, origin: str) -> None:
        data = PlaybackProgressEvent(
            node_id=e.node_id,
            position_ms=e.position_ms,
            duration_ms=e.duration_ms,
            watched=e.watched,
            origin=origin,
            updated_at=e.updated_at,
        )
        events.publish([e.user_id], EVENT_PROGRESS, event_data(EVENT_PROGRESS, data))

    async def _after_change(self, user_id: uuid.UUID, urgent: bool) -> None:
        """Met het slot: meteen schrijven (pauze, stoppen, einde, gezien, of de vorige keer is
        10 s geleden), of de schrijver op de achtergrond laten wachten tot het mag."""
        last = self.last_write.get(user_id, -math.inf)
        if urgent or self.clock() - last >= self.write_every:
            await self._write(user_id)
            return
        self.due[user_id] = min(self.due.get(user_id, math.inf), last + self.write_every)
        self._kick()

    # Schrijven

    async def _write(self, user_id: uuid.UUID) -> None:
        """Met het slot: alles wat van deze gebruiker nog niet in de database staat, in één
        transactie. Een bestand (of gebruiker) dat intussen weg is, valt eruit. Faalt de
        transactie anders, dan elke stand apart (review golf 4, S2): een stand die ook alleen
        niet te bewaren is (een getal te groot voor SQLite), gaat uit het geheugen, zodat hij de
        rest van die gebruiker niet blokkeert. Faalt de database zelf, dan blijven de standen
        staan voor de volgende keer (over write_every, ook als er intussen een PUT komt)."""
        self.due.pop(user_id, None)
        dirty = [e for e in self.entries.get(user_id, {}).values() if e.dirty]
        if not dirty:
            return
        try:
            await self._save(user_id, dirty)
        except Exception:
            log.exception("voortgang schrijven")
            try:
                dirty = await self._one_by_one(user_id, dirty)
            except Exception:
                self.last_write[user_id] = self.clock()  # niet bij elke PUT opnieuw
                self.due[user_id] = self.clock() + self.write_every
                self._kick()
                return
        for e in dirty:
            e.dirty = False
        self.last_write[user_id] = self.clock()
        self.writes += 1

    async def _save(self, user_id: uuid.UUID, dirty: list[Entry]) -> None:
        try:
            await self._upsert(user_id, dirty)
        except IntegrityError:
            dirty = await self._still_there(user_id, dirty)
            if dirty:
                await self._upsert(user_id, dirty)

    async def _one_by_one(self, user_id: uuid.UUID, dirty: list[Entry]) -> list[Entry]:
        """Na een fout van de hele transactie: elke stand apart. Een fout in de waarden van één
        stand: die stand weg uit het geheugen. Een andere fout (de database) gaat omhoog."""
        by = self.entries.get(user_id, {})
        done = []
        for e in dirty:
            try:
                await self._save(user_id, [e])
            except Exception as exc:
                if not _bad_value(exc):
                    raise
                log.warning("voortgang van één bestand niet te bewaren: weggelaten (%s)", exc)
                if by.get(e.node_id) is e:
                    del by[e.node_id]
                continue
            done.append(e)
        return done

    async def _upsert(self, user_id: uuid.UUID, dirty: list[Entry]) -> None:
        async with self.maker() as session:
            rows = await _rows(session, user_id, [e.node_id for e in dirty])
            for e in dirty:
                row = rows.get(e.node_id)
                if row is None:
                    row = PlaybackState(user_id=user_id, profile=PROFILE, node_id=e.node_id)
                    session.add(row)
                e.fill(row)
            await session.commit()

    async def _still_there(self, user_id: uuid.UUID, dirty: list[Entry]) -> list[Entry]:
        """Na een IntegrityError: welke bestanden en de gebruiker er nog zijn. De rest gaat uit
        het geheugen (hun rijen ruimt de database zelf op, ON DELETE CASCADE)."""
        async with self.maker() as session:
            if await session.get(User, user_id) is None:
                self.entries.pop(user_id, None)
                return []
            ids = [e.node_id for e in dirty]
            alive: set[uuid.UUID] = set()
            for start in range(0, len(ids), CHUNK):
                found = await session.scalars(
                    select(Node.id).where(Node.id.in_(ids[start : start + CHUNK]))
                )
                alive.update(found)
        by = self.entries.get(user_id, {})
        for e in dirty:
            if e.node_id not in alive:
                by.pop(e.node_id, None)
        return [e for e in dirty if e.node_id in alive]

    async def flush_user(self, user_id: uuid.UUID) -> None:
        async with self.lock(user_id):
            await self._write(user_id)

    async def flush_due(self) -> None:
        """Iedereen wiens beurt het is. De schrijver op de achtergrond roept dit aan; tests met
        een eigen klok ook."""
        now = self.clock()
        for user_id in [u for u, at in self.due.items() if at <= now]:
            await self.flush_user(user_id)
        self._evict_idle()

    async def flush_all(self) -> None:
        for user_id, by in list(self.entries.items()):
            if any(e.dirty for e in by.values()):
                await self.flush_user(user_id)

    # Opruimen

    def _evict_idle(self) -> None:
        cutoff = self.clock() - IDLE_S
        for user_id in list(self.entries):
            by = self.entries[user_id]
            for node_id in [n for n, e in by.items() if not e.dirty and e.touched < cutoff]:
                del by[node_id]
            if not by:
                del self.entries[user_id]

    def _evict_if_full(self) -> None:
        if self.size() <= ENTRIES_MAX:
            return
        self._evict_idle()
        over = self.size() - ENTRIES_MAX
        if over <= 0:
            return
        clean = sorted(
            ((e.touched, str(u), u, n) for u, by in self.entries.items()
             for n, e in by.items() if not e.dirty),
        )  # fmt: skip
        for _, _, user_id, node_id in clean[:over]:
            self.entries[user_id].pop(node_id, None)

    # De schrijver op de achtergrond

    def _kick(self) -> None:
        if not self.background or self.closed:
            return
        if self._wake is None:
            self._wake = asyncio.Event()
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="voortgang schrijven"
            )
        self._wake.set()

    async def _run(self) -> None:
        assert self._wake is not None
        while not self.closed:
            self._wake.clear()
            delay = min(self.due.values()) - self.clock() if self.due else None
            if delay is None or delay > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), delay)
                continue
            try:
                await self.flush_due()
            except Exception:
                log.exception("voortgang schrijven op de achtergrond")
                await asyncio.sleep(RETRY_S)

    async def close(self) -> None:
        """Netjes stoppen: de schrijver weg en alles naar de database."""
        self.closed = True
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.flush_all()


async def _rows(
    session: AsyncSession, user_id: uuid.UUID, node_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, PlaybackState]:
    ids = list(dict.fromkeys(node_ids))
    out: dict[uuid.UUID, PlaybackState] = {}
    for start in range(0, len(ids), CHUNK):
        found = await session.scalars(
            select(PlaybackState).where(
                PlaybackState.user_id == user_id,
                PlaybackState.profile == PROFILE,
                PlaybackState.node_id.in_(ids[start : start + CHUNK]),
            )
        )
        out.update({r.node_id: r for r in found})
    return out


_store: Store | None = None


def store(settings: Settings) -> Store:
    """De Store van deze database: één per proces (een andere database is een andere test)."""
    global _store
    url = settings.db_url
    if _store is None or _store.url != url or _store.closed:
        maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
        _store = Store(maker, url=url)
    return _store


def current() -> Store | None:
    return _store


def reset() -> None:
    """Alleen voor tests: het geheugen weg zonder te schrijven (zoals een crash)."""
    global _store
    old, _store = _store, None
    if old is not None:
        old.closed = True
        if old._task is not None and not old._task.done():
            old._task.cancel()


async def stop() -> None:
    """Netjes stoppen (vanuit runtime.stop, M0-hotfix van M7): alles uit het geheugen naar de
    database. Faalt nooit."""
    global _store
    old, _store = _store, None
    if old is None:
        return
    try:
        await old.close()
    except Exception:
        log.exception("voortgang wegschrijven bij het stoppen")


async def put(
    settings: Settings, user_id: uuid.UUID, node_id: uuid.UUID, sha: str | None, body: ProgressIn
) -> Outcome:
    return await store(settings).put(user_id, node_id, sha, body)


async def batch(
    settings: Settings, user_id: uuid.UUID, node_ids: list[uuid.UUID], watched: bool
) -> int:
    return await store(settings).batch(user_id, node_ids, watched)


# De rijen van /media/home


def episode_order(item: MediaItem) -> tuple:
    """De volgorde van de pagina van een serie (items.show_detail): seizoen (zonder seizoen
    eerst, specials achteraan), dan aflevering (of absoluut nummer), uitzenddatum, titel."""
    season = item.season
    return (
        -1 if season is None else media_items.SEASON_LAST if season == 0 else season,
        item.episode if item.episode is not None else (item.absolute or 0),
        item.aired or dt.date.min,
        item.sort_title,
        str(item.node_id),
    )


def _overlay(card: Card, view: View | None) -> Card:
    """De kaart met de stand uit het geheugen (die is nieuwer dan wat de Reader las)."""
    if view is None:
        return card
    progress = None
    if not view.watched and view.position_ms > 0:
        progress = CardProgress(position_ms=view.position_ms, duration_ms=view.duration_ms)
    return card.model_copy(update={"progress": progress, "watched": view.watched})


def _overlay_all(cards: list[Card], views: dict[uuid.UUID, View]) -> list[Card]:
    return [_overlay(c, views.get(c.node_id)) if c.node_id else c for c in cards]


def overlay(user_id: uuid.UUID, cards: list[Card]) -> list[Card]:
    """De kaarten van de Reader van M2 (een bibliotheek, de pagina van een item en van een
    serie) met de stand uit het geheugen erover: die is tot 10 s nieuwer dan de database (een
    tik of een sprong wacht op zijn beurt). Zonder geheugen (media uit, of nog niets gemeld)
    blijven ze zoals ze zijn. Zonder await: één blik op het geheugen. (Bij het samenvoegen van
    golf 4, Keuze 36: "Doel: ≤ 2 s na de PUT" geldt ook voor de balkjes op die pagina's.)"""
    live = current()
    if live is None or not cards:
        return cards
    return _overlay_all(cards, live.snapshot(user_id))


async def _items(
    session: AsyncSession, node_ids: list[uuid.UUID], library_ids: list[uuid.UUID]
) -> dict[uuid.UUID, MediaItem]:
    out: dict[uuid.UUID, MediaItem] = {}
    for start in range(0, len(node_ids), CHUNK):
        rows = await session.scalars(
            select(MediaItem).where(
                MediaItem.node_id.in_(node_ids[start : start + CHUNK]),
                MediaItem.library_id.in_(library_ids),
                MediaItem.present.is_(True),
                MediaItem.kind.in_(KINDS),
            )
        )
        out.update({i.node_id: i for i in rows})
    return out


async def _swapped(
    session: AsyncSession, views: dict[uuid.UUID, View], node_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Welke van deze bestanden intussen een ander bestand zijn (blob en duur, Keuze 36)."""
    out: set[uuid.UUID] = set()
    for start in range(0, len(node_ids), CHUNK):
        rows = await session.execute(
            select(Node.id, Node.blob_id, MediaProbe.duration_ms)
            .outerjoin(MediaProbe, MediaProbe.blob_sha == Node.blob_id)
            .where(Node.id.in_(node_ids[start : start + CHUNK]))
        )
        for node_id, sha, duration in rows.all():
            v = views[node_id]
            if replaced(v.blob_sha, v.duration_ms, sha, duration):
                out.add(node_id)
    return out


async def _continue(
    reader: media_items.Reader,
    user_id: uuid.UUID,
    library_ids: list[uuid.UUID],
    memory: dict[uuid.UUID, View],
) -> list[Card]:
    """Verder kijken: positie > 60 s, niet gezien, de nieuwste eerst, hooguit 20."""
    session = reader.session
    rows = await session.scalars(
        select(PlaybackState)
        .where(
            PlaybackState.user_id == user_id,
            PlaybackState.profile == PROFILE,
            PlaybackState.watched.is_(False),
            PlaybackState.position_ms > RESUME_MS,
        )
        .order_by(PlaybackState.updated_at.desc())
        .limit(SCAN_MAX)
    )
    views = {r.node_id: View.of(r) for r in rows}
    views.update(memory)
    wanted = sorted(
        (n for n, v in views.items() if not v.watched and v.position_ms > RESUME_MS),
        key=lambda n: (views[n].updated_at, str(n)),
        reverse=True,
    )[:SCAN_MAX]
    items = await _items(session, wanted, library_ids)
    swapped = await _swapped(session, views, list(items))
    ordered = [items[n] for n in wanted if n in items and n not in swapped]
    shown = (await reader.visible(ordered))[:CONTINUE_MAX]
    return _overlay_all(await reader.cards(shown), views)


async def _active_shows(
    session: AsyncSession,
    user_id: uuid.UUID,
    library_ids: list[uuid.UUID],
    memory: dict[uuid.UUID, View],
) -> list[uuid.UUID]:
    """De series met activiteit van deze gebruiker in de laatste 90 dagen, de laatste eerst."""
    cutoff = dt.datetime.now(UTC) - dt.timedelta(days=ACTIVE_DAYS)
    episode = (
        MediaItem.show_id.is_not(None),
        MediaItem.library_id.in_(library_ids),
        MediaItem.kind == "episode",
    )
    rows = await session.execute(
        select(MediaItem.show_id, func.max(PlaybackState.updated_at))
        .join(MediaItem, MediaItem.node_id == PlaybackState.node_id)
        .where(
            PlaybackState.user_id == user_id,
            PlaybackState.profile == PROFILE,
            PlaybackState.updated_at >= cutoff,
            *episode,
        )
        .group_by(MediaItem.show_id)
    )
    activity = {show: aware(at) or EPOCH for show, at in rows.all()}
    fresh = [n for n, v in memory.items() if v.updated_at >= cutoff]
    for start in range(0, len(fresh), CHUNK):
        rows = await session.execute(
            select(MediaItem.node_id, MediaItem.show_id).where(
                MediaItem.node_id.in_(fresh[start : start + CHUNK]), *episode
            )
        )
        for node_id, show in rows.all():
            activity[show] = max(activity.get(show, EPOCH), memory[node_id].updated_at)
    return sorted(activity, key=lambda s: (activity[s], str(s)), reverse=True)[:SHOWS_MAX]


async def _next_up(
    reader: media_items.Reader,
    user_id: uuid.UUID,
    library_ids: list[uuid.UUID],
    memory: dict[uuid.UUID, View],
    skip: set[uuid.UUID],
) -> list[Card]:
    """Volgende aflevering: per serie met activiteit in de laatste 90 dagen, na de laatst
    geziene aflevering (de laatste overgang naar gezien) de eerstvolgende ongeziene die de
    gebruiker mag zien. Specials (seizoen 0) tellen niet mee. Staat die aflevering al in
    Verder kijken (skip), dan niet nog eens."""
    session = reader.session
    picked: list[MediaItem] = []
    seen_views: dict[uuid.UUID, View] = {}
    for show_id in await _active_shows(session, user_id, library_ids, memory):
        if len(picked) >= NEXT_UP_MAX:
            break
        found = await session.scalars(
            select(MediaItem).where(
                MediaItem.show_id == show_id,
                MediaItem.present.is_(True),
                MediaItem.kind == "episode",
                MediaItem.library_id.in_(library_ids),
            )
        )
        episodes = sorted((i for i in found.all() if i.season != 0), key=episode_order)
        rows = await _rows(session, user_id, (i.node_id for i in episodes))
        views = {n: View.of(r) for n, r in rows.items()}
        views.update({i.node_id: memory[i.node_id] for i in episodes if i.node_id in memory})
        watched = [
            (views[i.node_id].watched_at or views[i.node_id].updated_at, n)
            for n, i in enumerate(episodes)
            if i.node_id in views and views[i.node_id].watched
        ]
        if not watched:
            continue
        _, last = max(watched)
        rest = [
            i for i in episodes[last + 1 :] if not (i.node_id in views and views[i.node_id].watched)
        ]
        visible = await reader.visible(rest)
        if visible and visible[0].node_id not in skip:
            picked.append(visible[0])
            seen_views.update(views)
    return _overlay_all(await reader.cards(picked), seen_views)


async def home_rows(
    session: AsyncSession, user_id: uuid.UUID, library_ids: list[uuid.UUID]
) -> dict[str, list[Card]]:
    """Verder kijken, Volgende aflevering en Net toegevoegd voor /media/home, met de stand uit
    het geheugen erover (die is tot 10 s nieuwer dan de database). Alleen wat de gebruiker nu
    mag lezen: de bibliotheken die hij ziet (library_ids) en wat nog onder hun map ligt, niet
    in de prullenbak (Reader.visible)."""
    out: dict[str, list[Card]] = {row: [] for row in ROWS}
    user = await session.get(User, user_id)
    if user is None or not library_ids:
        return out
    reader = media_items.Reader(session, runtime.settings(), user)
    live = current()
    memory = live.snapshot(user_id) if live is not None else {}
    out["continue"] = await _continue(reader, user_id, library_ids, memory)
    skip = {c.node_id for c in out["continue"] if c.node_id is not None}
    out["next_up"] = await _next_up(reader, user_id, library_ids, memory, skip)
    out["recent"] = _overlay_all(await media_items.recent(reader, library_ids), memory)
    return out
