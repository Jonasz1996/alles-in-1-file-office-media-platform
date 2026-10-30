"""De scanner (Keuze 5): gebeurtenissen via hooks.register_touched, plus een ronde bij de start en
elke 6 uur (jobs media.scan en media.reconcile in de rij media_scan).

De haak voor de lus (review K1): runtime.start roept start(app, settings) aan, alleen met media
aan, en runtime.stop roept stop(handle) aan met wat start teruggaf. Hier draait een eigen lus van
Settings.media_scan_tick (30 s, tests 0,5 s; niet de housekeeping, die tikt per 60 s); bij de
start en elke 6 uur zet hij media.reconcile klaar.

Hoe een wijziging een item wordt:
1. files.notify roept na de commit hooks.touched(ids, type) aan, en die touched() hieronder: de
   ids gaan in een set in het geheugen (hooguit MAX_TOUCHED; daarboven een volledige ronde van
   elke bibliotheek). Geen I/O. Zolang er geen bibliotheek is, doet hij niets (een vlag).
2. Elke tik zoekt bij de ids de bibliotheken (de mappen erboven, de items die er al waren, en
   bij een map wat eronder ligt) en zet per bibliotheek één job media.scan klaar met key
   media.scan:<id>. Staat er al een klaar, dan komen de ids erbij (de payload van die job).
3. De job loopt in stukken van hooguit CHUNK bestanden (jobs.chain met dezelfde key): elk stuk
   leest eerst (een korte sessie), vraagt dan de NFO's aan de worker zonder databaseverbinding
   (regel 8), en schrijft daarna alleen wat veranderde (een tweede korte sessie).

Een stuk is een lijst mappen, of een deel van een grote map: een map met meer dan CHUNK
bestanden gaat in delen, maar elk deel ziet de namen van de hele map (voor ondertitels, art en
delen).

Wat een scan doet (Keuze 5–9): classificeren (classify), ontleden (names), groeperen in series
(items.Shows), de externe ondertitels koppelen (subtitles.pair, M5), lokale art zoeken, de NFO
lezen (via de worker, met een cache per sha), en de analyse klaarzetten voor nieuwe inhoud (de
rij media; de stappen subs, sprites en keyframes zien de bibliotheek). In de prullenbak (zelf of
een map erboven): present = false, de rij blijft. Naar buiten de bibliotheek: de rij weg.

De worker delen met afspelen: NFO's lezen neemt een plaats van de soort short. De scanner vraagt
eerst caps en leest alleen als er minstens twee short-plaatsen vrij zijn, zodat hij nooit de
laatste neemt; anders (of als de worker druk of weg is) komen de items er nu met de naam, en
probeert hij de NFO's na NFO_RETRY seconden opnieuw.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
from sqlalchemy import Integer, delete, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app import access, events, jobs
from app.config import Settings
from app.db import get_engine
from app.media import analysis, classify, client, hooks, names, subtitles
from app.media import items as media_items
from app.media.types import EVENT_ITEM, EVENT_LIBRARY, ItemEvent, LibraryEvent
from app.media.wire import Code, Op
from app.media.worker.args import NfoArgs
from app.models import Job, MediaItem, MediaLibrary, MediaProbe, MediaShow, Node
from app.storage import BlobStore

log = logging.getLogger("platform.media")

JOB = "media.scan"
RECONCILE = "media.reconcile"
LANE = "media_scan"
SCAN_TIMEOUT = 300.0
RECONCILE_TIMEOUT = 1800.0
CHUNK = 500  # bestanden per stuk (Keuze 5)
MAX_TOUCHED = 50_000  # de set in het geheugen; daarboven een volledige ronde
MAX_NODES = 5_000  # ids in één payload; daarboven wordt het een volledige ronde
RECONCILE_EVERY = 6 * 3600.0
NFO_RETRY = 60.0  # worker druk of weg: de NFO's na zoveel seconden opnieuw
NFO_MAX_BYTES = 1024 * 1024  # Keuze 9
# Een fout van de NFO zelf: in de cache, en geen herkansing. Ook timeout (review golf 3, S1): de
# opdracht leest hooguit 1 MiB in milliseconden; duurt het 10 s, dan ligt het aan de bytes, en
# zou de herkansing na NFO_RETRY dezelfde NFO steeds opnieuw sturen.
NFO_FILE_ERRORS = analysis.FILE_ERRORS | {Code.TIMEOUT.value}
MAX_EVENTS = 20  # meer veranderde items in één stuk: alleen media.library
MEDIA_KINDS = ("movie", "episode", "video", "extra", "track")
SEP, END = "\x1f", "\x1e"  # in de sorteersleutel van een map (namen hebben geen stuurtekens)

_pending: set[uuid.UUID] = set()
_overflow = False
_active = False
# Mappen waarvan de NFO's nog gelezen moeten worden: bibliotheek → (wanneer, mappen)
_retry: dict[uuid.UUID, tuple[float, set[uuid.UUID]]] = {}


def activate(on: bool = True) -> None:
    """Er is (of er is geen) bibliotheek: touched verzamelt alleen als er een is."""
    global _active
    _active = on
    if not on:
        _pending.clear()


def active() -> bool:
    return _active


def reset() -> None:
    """Voor tests: de set, de vlag en de herkansingen leeg."""
    global _overflow, _active
    _pending.clear()
    _retry.clear()
    _overflow = False
    _active = False


def touched(node_ids: list[uuid.UUID], type: str) -> None:
    """De luisteraar voor hooks.touched: alleen in het geheugen, nooit I/O."""
    global _overflow
    if not _active or _overflow:
        return
    _pending.update(node_ids)
    if len(_pending) > MAX_TOUCHED:
        _pending.clear()
        _overflow = True


hooks.register_touched(touched)


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def _now() -> datetime:
    return datetime.now(UTC)


def _key(library_id: uuid.UUID) -> str:
    return f"{JOB}:{library_id}"


# --- klaarzetten -------------------------------------------------------------------------------


def _earliest(a: Any, b: Any) -> list[Any] | None:
    """De vroegste van twee cursors van een volledige ronde ([sleutel, al gedaan]); None is
    het begin."""
    if not (isinstance(a, list) and len(a) == 2 and isinstance(b, list) and len(b) == 2):
        return None
    return min([str(a[0]), int(a[1])], [str(b[0]), int(b[1])])


def _merge(
    old: dict[str, Any],
    *,
    nodes: Iterable[uuid.UUID] = (),
    dirs: Iterable[uuid.UUID] = (),
    full: bool = False,
    after: list[Any] | None = None,
    parts: Iterable[list[Any]] = (),
) -> dict[str, Any]:
    """De payload van een klaarstaande scan met wat erbij komt. full met after: de rest van een
    volledige ronde vanaf die cursor (review golf 3, C1); parts: mappen vanaf een plek
    ([map, al gedaan], de rest van een stuk)."""
    out = dict(old)
    if full:
        if not out.get("full"):
            out["full"] = True
            out["after"] = after
        else:
            out["after"] = _earliest(out.get("after"), after)
    merged = list(dict.fromkeys([*(out.get("nodes") or []), *(str(n) for n in nodes)]))
    if len(merged) > MAX_NODES:
        out["full"], out["after"], merged = True, None, []
    if merged:
        out["nodes"] = merged
    else:
        out.pop("nodes", None)
    wanted: dict[str, int] = {}
    extra = [*([str(d), 0] for d in dirs), *([str(d), int(o)] for d, o in parts)]
    for entry in [*(out.get("dirs") or []), *extra]:
        dir_id, offset = str(entry[0]), int(entry[1])
        wanted[dir_id] = min(offset, wanted.get(dir_id, offset))
    if wanted:
        out["dirs"] = [[d, o] for d, o in wanted.items()]
    return out


async def _queue(
    session: AsyncSession,
    library_id: uuid.UUID,
    *,
    nodes: Iterable[uuid.UUID] = (),
    dirs: Iterable[uuid.UUID] = (),
    full: bool = False,
    after: list[Any] | None = None,
    parts: Iterable[list[Any]] = (),
) -> None:
    """Eén scan voor deze bibliotheek klaarzetten, of wat erbij komt in de payload van de scan
    die al klaarstaat (alleen status queued: een lopende scan pakt zijn opvolger zelf op).
    In de transactie van de aanroeper. Ook de rest van een lopend stuk gaat zo (after en
    parts): een scan die intussen klaarstaat, neemt die rest mee in plaats van hem weg te
    laten vallen."""
    nodes, dirs, parts = list(nodes), list(dirs), list(parts)
    key = _key(library_id)
    for _ in range(3):
        job = await session.scalar(
            select(Job).where(Job.key == key, Job.status == "queued").order_by(Job.created_at)
        )
        if job is not None:
            payload = _merge(
                job.payload or {}, nodes=nodes, dirs=dirs, full=full, after=after, parts=parts
            )
            result = await session.execute(
                update(Job)
                .where(Job.id == job.id, Job.status == "queued")
                .values(payload=payload)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount:
                break
            continue  # net geclaimd: dan een nieuwe
        payload = _merge(
            {"library_id": str(library_id)},
            nodes=nodes,
            dirs=dirs,
            full=full,
            after=after,
            parts=parts,
        )
        if await jobs.chain(session, JOB, payload, key) is not None:
            break
    await session.execute(
        update(MediaLibrary)
        .where(MediaLibrary.id == library_id, MediaLibrary.scan_state.in_(("idle", "failed")))
        .values(scan_state="queued")
        .execution_options(synchronize_session=False)
    )


async def queue_full(session: AsyncSession, library_id: uuid.UUID) -> None:
    """Een volledige ronde van deze bibliotheek (nieuw, opnieuw scannen, reconcile)."""
    await _queue(session, library_id, full=True)


async def queue_nodes(
    session: AsyncSession, library_id: uuid.UUID, node_ids: Iterable[uuid.UUID]
) -> None:
    await _queue(session, library_id, nodes=node_ids)


async def _resolve(
    session: AsyncSession, ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, set[uuid.UUID]], bool]:
    """Bij welke bibliotheken horen deze nodes (nu, of tot nu toe)? Geeft per bibliotheek de
    ids, en of er ids waren die niet meer bestaan (definitief weg: de rijen gingen met
    CASCADE; dan alleen opnieuw tellen)."""
    plan: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    gone = False
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        up = access.ancestry(Node.id.in_(chunk))
        rows = await session.execute(
            select(up.c.origin, MediaLibrary.id).join(MediaLibrary, MediaLibrary.root_id == up.c.id)
        )
        for origin, library_id in rows:
            plan[library_id].add(origin)
        rows = await session.execute(
            select(MediaItem.node_id, MediaItem.library_id).where(MediaItem.node_id.in_(chunk))
        )
        for node_id, library_id in rows:
            plan[library_id].add(node_id)
        kinds = dict(
            (await session.execute(select(Node.id, Node.kind).where(Node.id.in_(chunk)))).all()
        )
        if len(kinds) < len(chunk):
            gone = True
        dirs = [i for i, kind in kinds.items() if kind == "dir"]
        if dirs:
            # Wat eronder ligt: items (een map die naar buiten ging) en bibliotheken (een map
            # boven een bibliotheek ging naar de prullenbak of terug)
            top = select(Node.id.label("origin"), Node.id.label("id")).where(Node.id.in_(dirs))
            down = top.cte("scan_below", recursive=True)
            child = aliased(Node)
            down = down.union_all(
                select(down.c.origin, child.id).where(child.parent_id == down.c.id)
            )
            rows = await session.execute(
                select(down.c.origin, MediaItem.library_id)
                .join(MediaItem, MediaItem.node_id == down.c.id)
                .distinct()
            )
            for origin, library_id in rows:
                plan[library_id].add(origin)
            rows = await session.execute(
                select(down.c.origin, MediaLibrary.id).join(
                    MediaLibrary, MediaLibrary.root_id == down.c.id
                )
            )
            for origin, library_id in rows:
                plan[library_id].add(origin)
    return plan, gone


async def recount(session: AsyncSession, library_ids: Iterable[uuid.UUID] | None = None) -> None:
    """item_count opnieuw tellen (aanwezig, geen extra's)."""
    count = (
        select(func.count())
        .where(
            MediaItem.library_id == MediaLibrary.id,
            MediaItem.present.is_(True),
            MediaItem.kind.in_(("movie", "episode", "video", "track")),
        )
        .scalar_subquery()
    )
    query = update(MediaLibrary).values(item_count=count)
    if library_ids is not None:
        query = query.where(MediaLibrary.id.in_(list(library_ids)))
    await session.execute(query.execution_options(synchronize_session=False))


async def tick(settings: Settings) -> int:
    """Eén ronde van de lus: de set leeg en per bibliotheek een scan klaarzetten. Geeft het
    aantal bibliotheken. Gaat het mis, dan komen de ids terug in de set."""
    global _overflow
    if not _active and not _retry:
        return 0
    ids = list(_pending)
    _pending.clear()
    overflow, _overflow = _overflow, False
    due = {lib: dirs for lib, (at, dirs) in _retry.items() if at <= time.monotonic()}
    for lib in due:
        _retry.pop(lib, None)
    if not ids and not overflow and not due:
        return 0
    try:
        async with _maker(settings)() as session:
            if overflow:
                libraries = list((await session.scalars(select(MediaLibrary.id))).all())
                for library_id in libraries:
                    await queue_full(session, library_id)
                await session.commit()
                return len(libraries)
            plan, gone = await _resolve(session, ids) if ids else ({}, False)
            known = set((await session.scalars(select(MediaLibrary.id))).all())
            for library_id, nodes in plan.items():
                if library_id in known:
                    await queue_nodes(session, library_id, nodes)
            for library_id, dirs in due.items():
                if library_id in known:
                    await _queue(session, library_id, dirs=dirs)
            if gone:
                await recount(session)
            await session.commit()
            return len(set(plan) | set(due))
    except Exception:
        _pending.update(ids)
        if overflow:
            _overflow = True
        for library_id, dirs in due.items():
            _retry.setdefault(library_id, (0.0, set()))[1].update(dirs)
        raise


# --- de boom van een bibliotheek ---------------------------------------------------------------


@dataclass
class Dir:
    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    parts: tuple[str, ...]  # de namen vanaf de map van de bibliotheek (die zelf: ())
    trashed: bool
    key: str  # sorteersleutel: ouders voor kinderen, op naam

    def path(self, name: str) -> list[str]:
        return [*self.parts, name]


@dataclass
class Tree:
    root: Dir
    dirs: dict[uuid.UUID, Dir]
    children: dict[uuid.UUID, list[uuid.UUID]]

    @classmethod
    async def load(cls, session: AsyncSession, library: MediaLibrary) -> Tree:
        """Alle mappen onder de bibliotheek (ook in de prullenbak), in één recursieve query,
        plus of er boven de map van de bibliotheek iets in de prullenbak ligt."""
        top = select(Node.id, Node.parent_id, Node.name, Node.deleted_at).where(
            Node.id == library.root_id
        )
        found = top.cte("library_dirs", recursive=True)
        child = aliased(Node)
        found = found.union_all(
            select(child.id, child.parent_id, child.name, child.deleted_at).where(
                child.parent_id == found.c.id, child.kind == "dir"
            )
        )
        rows = (await session.execute(select(found))).all()
        up = access.ancestry(Node.id == library.root_id)
        above = await session.scalar(
            select(func.count()).select_from(up).where(up.c.depth > 0, up.c.deleted_at.is_not(None))
        )
        info = {r.id: r for r in rows}
        children: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for r in rows:
            if r.id != library.root_id and r.parent_id in info:
                children[r.parent_id].append(r.id)
        dirs: dict[uuid.UUID, Dir] = {}
        root_row = info.get(library.root_id)
        if root_row is None:
            root = Dir(library.root_id, None, "", (), True, END + library.root_id.hex)
            return cls(root, {root.id: root}, {})
        root = Dir(
            root_row.id, root_row.parent_id, root_row.name, (),
            bool(above) or root_row.deleted_at is not None, END + root_row.id.hex,
        )  # fmt: skip
        dirs[root.id] = root
        stack = [root.id]
        while stack:
            parent = dirs[stack.pop()]
            for child_id in children.get(parent.id, []):
                r = info[child_id]
                parts = (*parent.parts, r.name)
                dirs[child_id] = Dir(
                    r.id, r.parent_id, r.name, parts,
                    parent.trashed or r.deleted_at is not None,
                    SEP.join(p.casefold() for p in parts) + END + r.id.hex,
                )  # fmt: skip
                stack.append(child_id)
        return cls(root, dirs, children)

    def below(self, dir_id: uuid.UUID) -> list[uuid.UUID]:
        """Deze map en alles eronder."""
        out, stack = [], [dir_id]
        while stack:
            current = stack.pop()
            out.append(current)
            stack.extend(self.children.get(current, []))
        return out

    def chain(self, dir_id: uuid.UUID) -> list[Dir]:
        """De mappen van onder de bibliotheek tot en met deze (index = plek in parts)."""
        out: list[Dir] = []
        current = self.dirs.get(dir_id)
        while current is not None and current.id != self.root.id:
            out.append(current)
            current = self.dirs.get(current.parent_id) if current.parent_id else None
        return out[::-1]

    def ordered(self) -> list[Dir]:
        return sorted(self.dirs.values(), key=lambda d: d.key)


# --- een stuk plannen ---------------------------------------------------------------------------


@dataclass
class Unit:
    dir_id: uuid.UUID
    start: int  # bestanden [start:end] van de map, op (naam, id)
    end: int


@dataclass
class Plan:
    units: list[Unit]
    rest: dict[str, Any] | None  # de payload van de opvolger, of None als dit het laatste is
    full_done: bool  # een volledige ronde is met dit stuk klaar


def plan(
    tree: Tree,
    counts: dict[uuid.UUID, int],
    explicit: list[tuple[uuid.UUID, int]],
    full: bool,
    after: tuple[str, int] | None,
    library_id: uuid.UUID,
    limit: int = CHUNK,
) -> Plan:
    """Welke mappen (of delen van een map) dit stuk doet: eerst de genoemde mappen, dan bij een
    volledige ronde de rest op volgorde van de sleutel vanaf after (sleutel, al gedaan). Hooguit
    limit bestanden, en altijd iets. Pure functie."""
    wanted: dict[uuid.UUID, int] = {}
    for dir_id, offset in explicit:
        if dir_id in tree.dirs:
            wanted[dir_id] = min(offset, wanted.get(dir_id, offset))
    queue: list[tuple[uuid.UUID, int, bool]] = [
        (d, o, False) for d, o in sorted(wanted.items(), key=lambda e: tree.dirs[e[0]].key)
    ]
    if full:
        for d in tree.ordered():
            if d.id in wanted:
                continue
            if after is not None:
                if d.key < after[0]:
                    continue
                if d.key == after[0]:
                    queue.append((d.id, after[1], True))
                    continue
            queue.append((d.id, 0, True))
    units: list[Unit] = []
    budget = limit
    rest_dirs: list[list[Any]] = []
    next_after: tuple[str, int] | None = None
    for dir_id, offset, in_full in queue:
        total = counts.get(dir_id, 0)
        if budget <= 0 and units:
            if in_full:
                next_after = (tree.dirs[dir_id].key, offset)
                break
            rest_dirs.append([str(dir_id), offset])
            continue
        end = offset + max(0, min(total - offset, budget))
        units.append(Unit(dir_id, offset, end))
        budget -= end - offset
        if end < total:
            if in_full:
                next_after = (tree.dirs[dir_id].key, end)
                break
            rest_dirs.append([str(dir_id), end])
    rest: dict[str, Any] | None = None
    if rest_dirs or next_after is not None:
        rest = {"library_id": str(library_id)}
        if rest_dirs:
            rest["dirs"] = rest_dirs
        if next_after is not None:
            rest["full"] = True
            rest["after"] = list(next_after)
    return Plan(units, rest, full and next_after is None)


# --- de scan zelf -------------------------------------------------------------------------------


@dataclass
class FileRow:
    id: uuid.UUID
    parent_id: uuid.UUID
    name: str
    mime: str | None
    blob_id: str | None
    deleted: bool


async def _files(session: AsyncSession, dir_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[FileRow]]:
    """De bestanden van deze mappen, per map op (naam, id)."""
    out: dict[uuid.UUID, list[FileRow]] = defaultdict(list)
    for start in range(0, len(dir_ids), 500):
        rows = await session.execute(
            select(
                Node.id, Node.parent_id, Node.name, Node.mime, Node.blob_id, Node.deleted_at
            ).where(Node.parent_id.in_(dir_ids[start : start + 500]), Node.kind == "file")
        )
        for r in rows:
            out[r.parent_id].append(
                FileRow(r.id, r.parent_id, r.name, r.mime, r.blob_id, r.deleted_at is not None)
            )
    for files in out.values():
        files.sort(key=lambda f: (f.name.casefold(), f.name, f.id.hex))
    return out


async def _counts(session: AsyncSession, tree: Tree) -> dict[uuid.UUID, int]:
    ids = list(tree.dirs)
    out: dict[uuid.UUID, int] = {}
    for start in range(0, len(ids), 500):
        rows = await session.execute(
            select(Node.parent_id, func.count())
            .where(Node.parent_id.in_(ids[start : start + 500]), Node.kind == "file")
            .group_by(Node.parent_id)
        )
        out.update(dict(rows.all()))
    return out


def _context(tree: Tree, dir_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """De mappen boven deze mappen binnen de bibliotheek (voor tvshow.nfo en de art van de
    serie), zonder de map van de bibliotheek zelf."""
    out: set[uuid.UUID] = set()
    for dir_id in dir_ids:
        out.update(d.id for d in tree.chain(dir_id))
    return out


def _nfo_dir() -> str:
    return "nfo"


def nfo_cache(settings: Settings, sha: str) -> Path:
    return settings.data_dir / "media" / _nfo_dir() / f"{sha}.json"


def _read_cache(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


async def discard_nfo(sha: str) -> None:
    """Een blob gaat weg (hooks.on_discard): zijn NFO uit de cache."""
    from app.media import runtime

    path = nfo_cache(runtime.settings(), sha)
    with contextlib.suppress(OSError):
        await anyio.to_thread.run_sync(path.unlink)


hooks.register_discard(discard_nfo)


async def polite() -> bool:
    """Mag de scanner nu een plaats van de soort short nemen? Alleen als er minstens twee vrij
    zijn: afspelen (de probe van play) houdt er dan altijd een."""
    caps = await client.caps()
    places = caps.places.get("short", 0)
    return places - caps.in_use.get("short", 0) >= 2


async def read_nfos(settings: Settings, shas: list[str]) -> tuple[dict[str, dict], bool]:
    """De NFO's van deze inhoud: uit de cache, of via de worker (opdracht nfo, Keuze 9).
    Geeft (sha → antwoord van de worker, later opnieuw proberen). Geen databaseverbinding."""
    found: dict[str, dict] = {}
    retry = False
    worker_ok = True
    blobs = BlobStore(settings.blob_dir)
    for sha in dict.fromkeys(shas):
        path = nfo_cache(settings, sha)
        cached = await anyio.to_thread.run_sync(_read_cache, path)
        if cached is not None:
            found[sha] = cached
            continue
        if not worker_ok:
            retry = True
            continue
        try:
            if not await polite():
                worker_ok, retry = False, True
                continue
            job, _ = await client.call(
                Op.NFO, NfoArgs(max_bytes=NFO_MAX_BYTES), source=blobs.path(sha)
            )
            async with job:
                data = await job.result()
        except (client.WorkerBusy, client.WorkerDown, TimeoutError):
            worker_ok, retry = False, True
            continue
        except client.WorkerFailed as exc:
            if exc.code in NFO_FILE_ERRORS:
                data = {"root": None, "fields": {}, "error": exc.code}
            else:
                worker_ok = False  # not_built, internal: de ronde van 6 uur probeert het
                continue
        except OSError:
            continue  # de blob is weg
        if not isinstance(data, dict):
            continue
        found[sha] = data
        with contextlib.suppress(OSError):
            await anyio.to_thread.run_sync(_write_cache, path, data)
    return found, retry


@dataclass
class Entry:
    """Wat de scan van één mediabestand vindt."""

    node: FileRow
    kind: str
    extra: str | None
    present: bool
    fields: dict[str, Any]
    source: str
    confidence: float
    subs: list[dict[str, Any]]
    show: tuple[uuid.UUID | None, dict[str, Any], str] | None = None
    extra_dir: uuid.UUID | None = None  # de map van de film bij een extra
    extra_stem: str | None = None  # de naam van de film bij "<film>-trailer"


@dataclass
class Context:
    """Wat een stuk weet: de boom, de bestanden per map, de NFO's en de probes."""

    library: MediaLibrary
    tree: Tree
    files: dict[uuid.UUID, list[FileRow]]
    nfo: dict[str, dict]
    durations: dict[str, int | None] = field(default_factory=dict)

    def nfo_of(self, dir_id: uuid.UUID, *names_: str) -> dict | None:
        """De velden van de eerste NFO met een van deze namen in deze map (zonder hoofdletters)."""
        wanted = [n.casefold() for n in names_]
        files = {f.name.casefold(): f for f in self.files.get(dir_id, []) if not f.deleted}
        for name in wanted:
            f = files.get(name)
            if f is not None and f.blob_id and f.blob_id in self.nfo:
                data = self.nfo[f.blob_id]
                if not data.get("error") and isinstance(data.get("fields"), dict):
                    return data
        return None


def _nfo_fields(data: dict | None, episode: bool = False) -> dict[str, Any]:
    """De velden van een NFO in de vorm van media_item."""
    if not data:
        return {}
    raw = data.get("fields") or {}
    if episode and data.get("episodes"):
        first = data["episodes"][0]
        if isinstance(first, dict):
            raw = {**raw, **(first.get("fields") or first)}
    out: dict[str, Any] = {}
    if raw.get("title"):
        out["title"] = raw["title"]
    if raw.get("sorttitle"):
        out["sort_title"] = names.sort_title(str(raw["sorttitle"]))
    if raw.get("year"):
        out["year"] = raw["year"]
    if raw.get("plot"):
        out["overview"] = raw["plot"]
    if isinstance(raw.get("ids"), dict) and raw["ids"]:
        out["ids"] = raw["ids"]
    if episode:
        for name in ("season", "episode"):
            if isinstance(raw.get(name), int):
                out[name] = raw[name]
        if raw.get("aired") or raw.get("premiered"):
            out["aired"] = raw.get("aired") or raw.get("premiered")
        eps = [e for e in data.get("episodes") or [] if isinstance(e, dict)]
        last = eps[-1].get("episode") if len(eps) > 1 else None
        if isinstance(last, int):
            out["episode_end"] = last
    return out


def _art(images: list[FileRow], video_stems: list[str], single: bool) -> dict[str, dict]:
    """Lokale art in een map (Keuze 9): per video-stem (of None voor de hele map) de rollen."""
    out: dict[str | None, dict[str, dict]] = defaultdict(dict)
    for f in images:
        if f.deleted:
            continue
        found = classify.art_role(f.name, video_stems)
        if found is None:
            continue
        role, stem = found
        if stem is None and not single:
            continue  # een poster.jpg in een map met meer films hoort bij geen ervan
        out[stem].setdefault(role, {"node": str(f.id)})
    return out  # type: ignore[return-value]


def _show_art(images: list[FileRow]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in images:
        if f.deleted:
            continue
        season = classify.season_art(f.name)
        if season is not None:
            number, role = season
            out.setdefault(
                f"season:{'all' if number is None else number}:{role}", {"node": str(f.id)}
            )
            continue
        found = classify.art_role(f.name)
        if found is not None:
            out.setdefault(found[0], {"node": str(f.id)})
    return out


def describe(
    ctx: Context, dir_id: uuid.UUID, start: int, end: int
) -> list[tuple[FileRow, Entry | None]]:
    """Wat de bestanden [start:end] van deze map zijn. Pure functie (geen I/O): per bestand een
    Entry, of None als het (nu) geen item is."""
    library, tree = ctx.library, ctx.tree
    folder = tree.dirs[dir_id]
    files = ctx.files.get(dir_id, [])
    live = [f for f in files if not f.deleted]
    kinds = {f.id: classify.file_kind(f.name, f.mime) for f in files}
    videos = [f for f in live if kinds[f.id] == "video"]
    images = [f for f in live if kinds[f.id] == "image"]
    sub_files = [f for f in live if kinds[f.id] == "subtitle"]
    by_name = {f.name: f for f in sub_files}
    paired = subtitles.pair([v.name for v in videos], list(by_name)) if videos else {}
    stems = [classify.stem(v.name) for v in videos]
    main = [v for v in videos if classify.extra_of(folder.path(v.name)) is None]
    titles = {names.sort_title(names.parse(folder.path(v.name), library.kind).title) for v in main}
    single = len(main) == 1 or (len(main) > 1 and len(titles) == 1)
    art = _art(images, stems, single) if library.kind in ("movies", "videos", "shows") else {}
    chain = tree.chain(dir_id)
    out: list[tuple[FileRow, Entry | None]] = []
    for f in files[start:end]:
        kind = classify.classify(library.kind, folder.path(f.name), f.mime)
        if kind == "other":
            out.append((f, None))
            continue
        parts = folder.path(f.name)
        parsed = names.parse(parts, library.kind)
        extra: str | None = None
        if kind == "extra":
            extra = classify.extra_of(parts) or "other"
            duration = ctx.durations.get(f.blob_id or "")
            if (
                extra == "sample"
                and library.kind == "movies"
                and duration is not None
                and duration >= classify.SAMPLE_MAX_MS
            ):
                kind, extra = "movie", None  # een lange "sample" is gewoon een film (Keuze 5)
        stem = classify.stem(f.name)
        fields: dict[str, Any] = {
            "title": parsed.title or stem,
            "year": parsed.year,
            "edition": parsed.edition,
            "part": parsed.part,
            "ids": dict(parsed.ids),
            "season": None,
            "episode": None,
            "episode_end": None,
            "absolute": None,
            "aired": None,
            "artist": None,
            "disc": None,
            "track": None,
        }
        source = "name"
        show = None
        if kind == "episode":
            fields.update(
                title=parsed.episode_title or parsed.show or parsed.title or stem,
                season=parsed.season,
                episode=parsed.episode,
                episode_end=parsed.episode_end,
                absolute=parsed.absolute,
                aired=parsed.date,
            )
            nfo = _nfo_fields(ctx.nfo_of(dir_id, f"{stem}.nfo"), episode=True)
            if nfo:
                fields.update(nfo)
                source = "nfo"
            folder_id = None
            if parsed.show_index is not None and parsed.show_index < len(chain):
                folder_id = chain[parsed.show_index].id
            show_fields: dict[str, Any] = {
                "title": parsed.show or parsed.title or stem,
                "year": parsed.show_year,
                "ids": dict(parsed.show_ids),
            }
            show_source = "name"
            if folder_id is not None:
                tv = _nfo_fields(ctx.nfo_of(folder_id, "tvshow.nfo"))
                if tv:
                    show_fields.update(tv)
                    show_source = "nfo"
                show_images = [
                    g
                    for g in ctx.files.get(folder_id, [])
                    if kinds.get(g.id, classify.file_kind(g.name)) == "image"
                ]
                season_dir = (
                    chain[parsed.show_index + 1] if parsed.show_index + 1 < len(chain) else None
                )
                if season_dir is not None:
                    show_images += [
                        g for g in ctx.files.get(season_dir.id, [])
                        if classify.season_art(g.name) is not None
                    ]  # fmt: skip
                show_fields["art"] = _show_art(show_images)
            show = (folder_id, show_fields, show_source)
        elif kind in ("movie", "video", "extra"):
            names_ = (
                (f"{stem}.nfo",) if kind != "movie" or not single else (f"{stem}.nfo", "movie.nfo")
            )
            nfo = _nfo_fields(ctx.nfo_of(dir_id, *names_))
            if nfo and kind != "extra":
                fields.update(nfo)
                source = "nfo"
        elif kind == "track":
            fields.update(
                title=parsed.title or stem,
                artist=parsed.artist,
                disc=parsed.disc,
                track=parsed.track,
            )
        item_art = {**art.get(None, {}), **art.get(stem, {})} if kind != "extra" else {}
        if kind == "episode":
            item_art = dict(art.get(stem, {}))  # de art van de map hoort bij de serie
        fields["art"] = item_art
        subs = [
            {
                "node": str(by_name[m["name"]].id),
                "lang": m["lang"],
                "forced": m["forced"],
                "sdh": m["sdh"],
                "default": m["default"],
            }
            for m in paired.get(f.name, [])
            if m["name"] in by_name
        ]
        entry = Entry(
            node=f,
            kind=kind,
            extra=extra,
            present=not (folder.trashed or f.deleted),
            fields=fields,
            source=source,
            confidence=1.0 if source == "nfo" else parsed.confidence,
            subs=subs if kind in ("movie", "episode", "video", "extra") else [],
            show=show,
        )
        if kind == "extra":
            dir_parts = list(folder.parts)
            own_dir = chain[-1].id if chain else tree.root.id
            if dir_parts and classify.extra_dir(dir_parts[-1]) is not None:
                parent = chain[-2].id if len(chain) >= 2 else tree.root.id
                entry.extra_dir = parent
            else:
                entry.extra_dir = own_dir
                suffix_kind, rest = classify.extra_suffix(stem)
                entry.extra_stem = rest.strip() or None if suffix_kind else None
        out.append((f, entry))
    return out


async def _probes(session: AsyncSession, shas: list[str]) -> dict[str, tuple[int, int | None]]:
    """Per sha (versie, duur) van de opgeslagen probe."""
    out: dict[str, tuple[int, int | None]] = {}
    for start in range(0, len(shas), 500):
        rows = await session.execute(
            select(MediaProbe.blob_sha, MediaProbe.version, MediaProbe.duration_ms).where(
                MediaProbe.blob_sha.in_(shas[start : start + 500])
            )
        )
        for sha, version, duration in rows:
            out[sha] = (version, duration)
    return out


async def _queue_analysis(session: AsyncSession, library_id: uuid.UUID, shas: list[str]) -> None:
    """De analyse van deze inhoud in de rij media, met de bibliotheek erbij (de stappen subs,
    sprites en keyframes zien dan het soort). Staat hij al klaar zonder bibliotheek (play zette
    hem), dan komt die er alsnog bij."""
    if not shas:
        return
    shas = list(dict.fromkeys(shas))
    payload = {"library_id": str(library_id)}
    await jobs.enqueue_many(
        session,
        analysis.JOB,
        [({"sha": sha, **payload}, f"{analysis.JOB}:{sha}") for sha in shas],
    )
    keys = [f"{analysis.JOB}:{sha}" for sha in shas]
    for start in range(0, len(keys), 500):
        rows = await session.execute(
            select(Job.id, Job.payload).where(
                Job.key.in_(keys[start : start + 500]), Job.status == "queued"
            )
        )
        for job_id, data in rows:
            if isinstance(data, dict) and not data.get("library_id"):
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "queued")
                    .values(payload={**data, **payload})
                    .execution_options(synchronize_session=False)
                )


@dataclass
class Result:
    changed: list[uuid.UUID] = field(default_factory=list)
    retry_dirs: set[uuid.UUID] = field(default_factory=set)


async def _write(
    session: AsyncSession,
    ctx: Context,
    units: list[Unit],
    removals: list[uuid.UUID],
    removal_dirs: list[uuid.UUID],
) -> list[uuid.UUID]:
    """Fase 3: de rijen bijwerken. Alleen wat veranderde wordt geschreven."""
    library = ctx.library
    changed: list[uuid.UUID] = []
    if removals:
        for start in range(0, len(removals), 500):
            result = await session.execute(
                delete(MediaItem)
                .where(
                    MediaItem.library_id == library.id,
                    MediaItem.node_id.in_(removals[start : start + 500]),
                )
                .returning(MediaItem.node_id)
            )
            changed += list(result.scalars())
    for dir_id in removal_dirs:
        top = select(Node.id, literal(0, Integer).label("depth")).where(Node.id == dir_id)
        down = top.cte("scan_out", recursive=True)
        down = down.union_all(select(Node.id, down.c.depth + 1).where(Node.parent_id == down.c.id))
        result = await session.execute(
            delete(MediaItem)
            .where(MediaItem.library_id == library.id, MediaItem.node_id.in_(select(down.c.id)))
            .returning(MediaItem.node_id)
        )
        changed += list(result.scalars())
    described: list[tuple[FileRow, Entry | None]] = []
    for unit in units:
        if unit.dir_id in ctx.tree.dirs:
            described += describe(ctx, unit.dir_id, unit.start, unit.end)
    if not described:
        return changed
    node_ids = [f.id for f, _ in described]
    existing: dict[uuid.UUID, MediaItem] = {}
    for start in range(0, len(node_ids), 500):
        rows = await session.scalars(
            select(MediaItem).where(MediaItem.node_id.in_(node_ids[start : start + 500]))
        )
        existing.update({i.node_id: i for i in rows})
    # Eerst de series (nieuwe eerst geschreven: media_item.show_id wijst ernaar en er is geen
    # relationship die SQLAlchemy de volgorde laat kiezen)
    show_of: dict[uuid.UUID, MediaShow] = {}
    if any(e is not None and e.show is not None for _, e in described):
        shows = await media_items.Shows.load(session, library.id)
        for f, entry in described:
            if entry is not None and entry.show is not None:
                folder_id, show_fields, show_source = entry.show
                show_of[f.id] = shows.get(session, folder_id, show_fields, show_source)
        await session.flush()
    analyse: list[str] = []
    # Inhoud die nieuw is in deze bibliotheek (een nieuw item, of verhuisd uit een andere): die
    # krijgt de analyse ook als de probe er al is. Play (de viewer van Bestanden, M6) probet een
    # video buiten een bibliotheek; zonder dit kwamen de stappen van de bibliotheek (subs,
    # sprites) er voor zo'n video nooit. De stappen slaan over wat al klaar is.
    fresh: set[str] = set()
    extras: list[tuple[MediaItem, Entry]] = []
    now = _now()
    for f, entry in described:
        item = existing.get(f.id)
        if entry is None:
            if item is not None and item.library_id == library.id:
                await session.delete(item)
                changed.append(f.id)
            continue
        new = item is None
        if item is None:
            item = MediaItem(
                node_id=f.id,
                library_id=library.id,
                kind=entry.kind,
                extra=entry.extra,
                present=entry.present,
                title=entry.fields.get("title") or f.name,
                sort_title="",
                ids={},
                art={},
                subs=[],
                source=entry.source,
                confidence=0.0,
                names_version=0,
                locked=[],
                match_state="local",
                candidates=[],
                added_at=now,
                updated_at=now,
            )
            session.add(item)
        touched_item = new
        moved = item.library_id != library.id
        if moved:  # verhuisd uit een andere bibliotheek
            item.library_id = library.id
            item.show_id = None
            item.parent_id = None
            touched_item = True
        for name, value in (
            ("kind", entry.kind),
            ("extra", entry.extra),
            ("present", entry.present),
            ("subs", entry.subs),
            ("confidence", round(entry.confidence, 3)),
            ("names_version", names.NAMES_VERSION),
        ):
            if getattr(item, name) != value:
                setattr(item, name, value)
                touched_item = True
        fields = dict(entry.fields)
        if new or not item.sort_title:
            fields.setdefault("sort_title", None)
        if media_items.merge(item, fields, entry.source):
            touched_item = True
        if not item.sort_title:
            item.sort_title = names.sort_title(item.title) or item.title.casefold()[:255]
        show = show_of.get(f.id)
        if show is not None:
            if item.show_id != show.id:
                item.show_id = show.id
                touched_item = True
        elif item.show_id is not None and entry.kind != "episode":
            item.show_id = None
            touched_item = True
        if entry.kind == "extra":
            extras.append((item, entry))
        elif item.parent_id is not None:
            item.parent_id = None
            touched_item = True
        if touched_item:
            item.updated_at = now
            changed.append(f.id)
        if f.blob_id and entry.present:
            analyse.append(f.blob_id)
            if new or moved:
                fresh.add(f.blob_id)
    await session.flush()
    probes = await _probes(session, list(dict.fromkeys(analyse)))
    await _queue_analysis(
        session,
        library.id,
        [
            s
            for s in analyse
            if s in fresh or s not in probes or probes[s][0] < analysis.PROBE_VERSION
        ],
    )
    if extras:
        await _link_extras(session, library.id, extras, changed)
    await _link_orphans(session, ctx, units, changed)
    return changed


async def _movies_in(
    session: AsyncSession, library_id: uuid.UUID, dir_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[uuid.UUID, str, int | None]]]:
    """De films per map: (node, stem, part)."""
    out: dict[uuid.UUID, list[tuple[uuid.UUID, str, int | None]]] = defaultdict(list)
    if not dir_ids:
        return out
    rows = await session.execute(
        select(MediaItem.node_id, Node.parent_id, Node.name, MediaItem.part)
        .join(Node, Node.id == MediaItem.node_id)
        .where(
            MediaItem.library_id == library_id,
            MediaItem.kind == "movie",
            Node.parent_id.in_(dir_ids),
        )
    )
    for node_id, parent_id, name, part in rows:
        out[parent_id].append((node_id, classify.stem(name), part))
    for movies in out.values():
        movies.sort(key=lambda m: (m[2] or 0, m[1].casefold(), m[0].hex))
    return out


def _pick(movies: list[tuple[uuid.UUID, str, int | None]], stem: str | None) -> uuid.UUID | None:
    if not movies:
        return None
    if stem:
        for node_id, name, _ in movies:
            if name.casefold() == stem.casefold():
                return node_id
    return movies[0][0]


async def _link_extras(
    session: AsyncSession,
    library_id: uuid.UUID,
    extras: list[tuple[MediaItem, Entry]],
    changed: list[uuid.UUID],
) -> None:
    """Een extra hangt aan zijn film: in de map erboven (Extras/), of in dezelfde map
    ("<film>-trailer"; anders de eerste film daar)."""
    movies = await _movies_in(
        session, library_id, list({e.extra_dir for _, e in extras if e.extra_dir})
    )
    for item, entry in extras:
        parent = (
            _pick(movies.get(entry.extra_dir, []), entry.extra_stem) if entry.extra_dir else None
        )
        if parent == item.node_id:
            parent = None
        if item.parent_id != parent:
            item.parent_id = parent
            if item.node_id not in changed:
                changed.append(item.node_id)


async def _link_orphans(
    session: AsyncSession, ctx: Context, units: list[Unit], changed: list[uuid.UUID]
) -> None:
    """Een film die er net bij kwam: de extra's in de map Extras eronder die nog geen film
    hadden, hangen er nu aan."""
    tree = ctx.tree
    extra_dirs: dict[uuid.UUID, uuid.UUID] = {}
    for unit in units:
        for child in tree.children.get(unit.dir_id, []):
            if classify.extra_dir(tree.dirs[child].name) is not None:
                extra_dirs[child] = unit.dir_id
    if not extra_dirs:
        return
    rows = await session.execute(
        select(MediaItem)
        .join(Node, Node.id == MediaItem.node_id)
        .where(
            MediaItem.library_id == ctx.library.id,
            MediaItem.kind == "extra",
            MediaItem.parent_id.is_(None),
            Node.parent_id.in_(list(extra_dirs)),
        )
        .add_columns(Node.parent_id)
    )
    found = rows.all()
    if not found:
        return
    movies = await _movies_in(session, ctx.library.id, list(set(extra_dirs.values())))
    for item, parent_dir in found:
        parent = _pick(movies.get(extra_dirs[parent_dir], []), None)
        if parent is not None:
            item.parent_id = parent
            changed.append(item.node_id)


async def _affected(
    session: AsyncSession, library: MediaLibrary, tree: Tree, nodes: list[uuid.UUID]
) -> tuple[set[uuid.UUID], list[uuid.UUID], list[uuid.UUID]]:
    """Wat de geraakte nodes betekenen voor deze bibliotheek: (mappen om te scannen, items om
    weg te halen, mappen buiten de bibliotheek waarvan de items weg moeten)."""
    dirs: set[uuid.UUID] = set()
    removals: list[uuid.UUID] = []
    out_dirs: list[uuid.UUID] = []
    if not nodes:
        return dirs, removals, out_dirs
    up = access.ancestry(Node.id == library.root_id)
    above = set((await session.scalars(select(up.c.id).where(up.c.depth > 0))).all())
    info: dict[uuid.UUID, tuple[str, uuid.UUID | None]] = {}
    for start in range(0, len(nodes), 500):
        rows = await session.execute(
            select(Node.id, Node.kind, Node.parent_id).where(
                Node.id.in_(nodes[start : start + 500])
            )
        )
        info.update({r.id: (r.kind, r.parent_id) for r in rows})
    for node_id in nodes:
        found = info.get(node_id)
        if found is None:
            continue  # definitief weg: CASCADE
        kind, parent_id = found
        if node_id in tree.dirs:
            dirs.update(tree.below(node_id))
        elif kind == "file" and parent_id in tree.dirs:
            dirs.add(parent_id)
        elif node_id in above:
            dirs.update(tree.dirs)  # een map boven de bibliotheek: alles
        elif kind == "file":
            removals.append(node_id)
        else:
            out_dirs.append(node_id)
    return dirs, removals, out_dirs


async def _finish_full(session: AsyncSession, library: MediaLibrary) -> list[uuid.UUID]:
    """Het eind van een volledige ronde: items van nodes die niet meer onder de map liggen
    weg (ook als een gebeurtenis verloren ging)."""
    top = select(Node.id, literal(0, Integer).label("depth")).where(Node.id == library.root_id)
    down = top.cte("scan_all", recursive=True)
    down = down.union_all(select(Node.id, down.c.depth + 1).where(Node.parent_id == down.c.id))
    result = await session.execute(
        delete(MediaItem)
        .where(MediaItem.library_id == library.id, MediaItem.node_id.not_in(select(down.c.id)))
        .returning(MediaItem.node_id)
    )
    return list(result.scalars())


async def _cleanup_shows(session: AsyncSession, library_id: uuid.UUID) -> None:
    """Series zonder afleveringen weg."""
    used = select(MediaItem.show_id).where(MediaItem.show_id.is_not(None))
    await session.execute(
        delete(MediaShow)
        .where(MediaShow.library_id == library_id, MediaShow.id.not_in(used))
        .execution_options(synchronize_session=False)
    )


async def run(settings: Settings, payload: dict[str, Any]) -> None:
    """De job media.scan: één stuk, en de opvolger klaarzetten."""
    library_id = uuid.UUID(str(payload["library_id"]))
    maker = _maker(settings)
    full = bool(payload.get("full"))
    after = payload.get("after")
    cursor = (str(after[0]), int(after[1])) if isinstance(after, list) and len(after) == 2 else None
    nodes = [uuid.UUID(str(n)) for n in payload.get("nodes") or []]
    explicit = [(uuid.UUID(str(d)), int(o)) for d, o in payload.get("dirs") or []]
    # Fase 1: lezen, en wat de NFO's zijn
    async with maker() as session:
        library = await session.get(MediaLibrary, library_id)
        if library is None:
            return
        tree = await Tree.load(session, library)
        dirs, removals, out_dirs = await _affected(session, library, tree, nodes)
        explicit += [(d, 0) for d in dirs]
        counts = await _counts(session, tree)
        work = plan(tree, counts, explicit, full, cursor, library.id)
        scan_dirs = [u.dir_id for u in work.units]
        files = await _files(session, list({*scan_dirs, *_context(tree, scan_dirs)}))
        nfo_shas = [
            f.blob_id
            for rows in files.values()
            for f in rows
            if f.blob_id and not f.deleted and classify.file_kind(f.name) == "nfo"
        ]
        if library.scan_state != "running":
            library.scan_state = "running"
            await session.commit()
            await _publish(session, library)
    # Fase 2: de NFO's, zonder databaseverbinding (regel 8)
    nfo, retry = await read_nfos(settings, nfo_shas)
    # Fase 3: schrijven (de boom en de bestanden opnieuw: ze kunnen intussen veranderd zijn)
    async with maker() as session:
        library = await session.get(MediaLibrary, library_id)
        if library is None:
            return
        tree = await Tree.load(session, library)
        files = await _files(session, list({*scan_dirs, *_context(tree, scan_dirs)}))
        shas = [f.blob_id for rows in files.values() for f in rows if f.blob_id]
        probes = await _probes(session, shas)
        ctx = Context(library, tree, files, nfo, {s: d for s, (_, d) in probes.items()})
        changed = await _write(session, ctx, work.units, removals, out_dirs)
        if work.full_done:
            changed += await _finish_full(session, library)
        if changed:
            await _cleanup_shows(session, library.id)
        if work.rest is not None:
            # Niet jobs.chain alleen: staat er intussen al een scan klaar (een upload, een tik),
            # dan won die de ontdubbeling en viel de rest van de ronde weg (review golf 3, C1)
            await _queue(
                session,
                library.id,
                full=bool(work.rest.get("full")),
                after=work.rest.get("after"),
                parts=work.rest.get("dirs") or [],
            )
        else:
            waiting = await session.scalar(
                select(Job.id).where(Job.key == _key(library.id), Job.status == "queued").limit(1)
            )
            library.scan_state = "queued" if waiting else "idle"
            library.scanned_at = _now()
        await recount(session, [library.id])
        await session.commit()
        await session.refresh(library)
        await _publish(session, library, changed)
    if retry:
        when = time.monotonic() + NFO_RETRY
        _, dirs_before = _retry.get(library_id, (when, set()))
        _retry[library_id] = (when, dirs_before | set(scan_dirs))


async def _publish(
    session: AsyncSession, library: MediaLibrary, changed: list[uuid.UUID] | None = None
) -> None:
    """media.library (en bij een paar veranderde items media.item) naar wie hem ziet."""
    from app.media import library as media_library

    try:
        users = await media_library.watchers(session, library)
    except Exception:  # een event mag de scan niet laten mislukken
        log.exception("ontvangers van media.library")
        return
    events.publish(
        users,
        EVENT_LIBRARY,
        LibraryEvent(
            library_id=library.id,
            scan_state=library.scan_state,  # type: ignore[arg-type]
            item_count=library.item_count,
        ).model_dump(mode="json"),
    )
    if changed and len(changed) <= MAX_EVENTS:
        for node_id in dict.fromkeys(changed):
            events.publish(
                users,
                EVENT_ITEM,
                ItemEvent(node_id=node_id, library_id=library.id).model_dump(mode="json"),
            )


async def failed(settings: Settings, payload: dict[str, Any], error: str) -> None:
    """Een scan mislukte definitief: de staat zegt het (op /media en /beheer)."""
    with contextlib.suppress(Exception):
        library_id = uuid.UUID(str(payload["library_id"]))
        async with _maker(settings)() as session:
            library = await session.get(MediaLibrary, library_id)
            if library is not None:
                library.scan_state = "failed"
                await session.commit()
                await _publish(session, library)


async def reconcile(settings: Settings, payload: dict[str, Any]) -> None:
    """De job media.reconcile: een volledige ronde van elke bibliotheek klaarzetten. Vangt wat
    een crash tussen de commit en de lus verloor (Keuze 5)."""
    async with _maker(settings)() as session:
        libraries = list((await session.scalars(select(MediaLibrary.id))).all())
        for library_id in libraries:
            await queue_full(session, library_id)
        await session.commit()
    activate(bool(libraries) or _active)


jobs.register(JOB, run, on_dead=failed, lane=LANE, timeout=SCAN_TIMEOUT)
jobs.register(RECONCILE, reconcile, lane=LANE, timeout=RECONCILE_TIMEOUT)


# --- de lus ------------------------------------------------------------------------------------


async def queue_reconcile(settings: Settings) -> bool:
    """media.reconcile klaarzetten als er bibliotheken zijn (bij de start en elke 6 uur)."""
    async with _maker(settings)() as session:
        any_library = await session.scalar(select(MediaLibrary.id).limit(1))
        activate(any_library is not None)
        if any_library is None:
            return False
        await jobs.enqueue(session, RECONCILE, {}, key=RECONCILE)
        await session.commit()
    return True


async def _loop(settings: Settings) -> None:
    last = None
    while True:
        try:
            if last is None or time.monotonic() - last >= RECONCILE_EVERY:
                await queue_reconcile(settings)
                last = time.monotonic()
            await tick(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("de lus van de scanner")
        await asyncio.sleep(max(0.05, settings.media_scan_tick))


def start(app, settings: Settings) -> asyncio.Task | None:
    """De lus van de scanner starten (runtime.start, alleen met media aan); geeft de taak voor
    stop. Zonder settings niets."""
    if settings is None:
        return None
    hooks.register_touched(touched)
    activate()  # tot de database zegt dat er geen bibliotheek is: niets missen bij de start
    return asyncio.create_task(_loop(settings), name="media scanner")


async def stop(handle: asyncio.Task | None) -> None:
    """De lus stoppen; None is niets."""
    if handle is None:
        return
    handle.cancel()
    try:
        await handle
    except asyncio.CancelledError:
        pass
