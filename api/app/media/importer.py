"""De import uit /data/import (Keuze 2), als job media.import in de rij import (M2).

Een bestaande verzameling komt zo de boom in, alsof hij geüpload werd: elk bestand gaat de
BlobStore in (ontdubbeld, met quotum, versies en prullenbak), en daarna doet de scanner de rest.

- **Kopiëren** (standaard): de bytes gaan via een tijdelijk bestand de BlobStore in; de bron blijft
  staan.
- **Verplaatsen:** de sha wordt op de plek geteld; daarna krijgt het bestand met `linkat` een
  tweede naam in de werkmap van de BlobStore (`blobs/.tmp`), die `adopt` op zijn plek zet. Pas
  als place_file klaar is (de commit), gaat de naam in de bron weg. Dat kost geen extra ruimte,
  en wie de app midden in een bestand stopt (OOM, een herstart), verliest niets: de bron staat er
  dan nog (review golf 3, C7; een `rename` naar de werkmap liet het bestand daar achter). Een
  link die zo achterbleef, ruimt de volgende import op. De blob krijgt modus 0600 en geen ACL
  (S2: de worker mag een blob nooit kunnen schrijven). Een bestand van een andere gebruiker, of
  `EXDEV` (een ander bestandssysteem): dat ene bestand wordt gekopieerd en daarna de bron
  weggehaald.
- **Veilig:** er wordt nooit een symlink gevolgd. De bronmap gaat open met `O_DIRECTORY |
  O_NOFOLLOW`, elke stap eronder met `openat(dirfd, naam, O_NOFOLLOW)`; een pad als tekst wordt
  daarna nooit meer geopend. `realpath` van de bronmap ligt onder die van de importmap, op
  hetzelfde bestandssysteem; anders `media_import_path`. Speciale bestanden worden overgeslagen.
- **Wat nog geschreven wordt, komt er niet in** (per bestand, op de fd van de import zelf):
  `fstat` vóór het tellen (een gewoon bestand, minstens MIN_AGE_S oud, bij verplaatsen
  `st_nlink == 1`), de sha uit die fd, opnieuw `fstat` (grootte, `mtime_ns` en `ctime_ns`
  gelijk), en bij verplaatsen na de `linkat` hetzelfde `st_ino` met precies twee namen. Klopt
  iets niet, dan komt het in "overgeslagen" met de reden "in gebruik"; een volgende import pakt
  het op.
- **Hervatbaar en idempotent:** een map die er al is, wordt hergebruikt; dezelfde naam met dezelfde
  sha wordt overgeslagen (ook bij verplaatsen: de bron blijft dan staan, behalve als hij al de
  blob zelf is: dan was alleen het weghalen nog niet gebeurd); een andere sha wordt een nieuwe
  versie, zoals bij uploaden.
- **Quotum:** dat van de eigenaar, zoals een upload (place_file). Vol: de import stopt (staat
  stopped, reden no_room); wat binnen is, blijft.
- Hooguit één import tegelijk (409 media_import_busy). De voortgang staat in de payload van de
  job (view() geeft ImportView) en gaat als event media.import naar de beheerders.

De bronmap is een pad onder de importmap: `settings.data_dir / "import"` (in de container
`/data/import`, IMPORT_ROOT). Een absoluut pad mag met een van beide beginnen.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import logging
import os
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import anyio
import anyio.to_thread
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import errors, events, jobs
from app.config import Settings
from app.db import get_engine
from app.media import errors as media_errors
from app.media import runtime
from app.media.types import EVENT_IMPORT, ImportEvent, ImportSource, ImportView, SkippedItem
from app.models import Job, Node, User
from app.storage import BlobStore

log = logging.getLogger("platform.media.import")

IMPORT_ROOT = "/data/import"
JOB = "media.import"
KEY = "media.import"  # één tegelijk
LANE = "import"
TIMEOUT = 86_400
MIN_AGE_S = 60  # jonger: misschien wordt hij nog geschreven
CHUNK = 1024 * 1024
MAX_LISTED = 1000  # zoveel overgeslagen bestanden staan met naam in de payload
MAX_DEPTH = 64
SAVE_EVERY_S = 1.0

LINK_PREFIX = "import-link-"  # een tweede naam van een bron in blobs/.tmp (verplaatsen)
BLOB_MODE = 0o600  # zoals mkstemp: alleen de app (S2)

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# O_NONBLOCK: een fifo die tussen lstat en open op de plek van een bestand komt, blokkeert niet
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

# De redenen bij "overgeslagen" (tekst voor /beheer)
IN_USE = "in gebruik"
UNREADABLE = "niet leesbaar"
UNMOVABLE = "niet te verplaatsen"
NOT_REMOVED = "binnen, maar de bron is niet weg te halen"
SYMLINK = "symlink"
SPECIAL = "geen gewoon bestand"
HARD_LINK = "harde link"
BAD_NAME = "ongeldige naam"
NAME_TAKEN = "de naam is al van een map of bestand"
OTHER_FS = "ander bestandssysteem"
TOO_DEEP = "te diep"


def import_root(settings: Settings) -> Path:
    return settings.data_dir / "import"


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


# --- de bronmap ----------------------------------------------------------------------------------


def parts_of(settings: Settings, source: str) -> list[str]:
    """De bronmap als stappen onder de importmap. Een absoluut pad mag, onder IMPORT_ROOT of
    onder de echte importmap. `..` of `.` als stap: media_import_path."""
    text = (source or "").strip()
    if not text or "\x00" in text or any(p in (".", "..") for p in text.split("/")):
        raise media_errors.import_path()
    path = PurePosixPath(text)
    if not path.is_absolute():
        return list(path.parts)
    for base in (PurePosixPath(IMPORT_ROOT), PurePosixPath(str(import_root(settings)))):
        with contextlib.suppress(ValueError):
            return list(path.relative_to(base).parts)
    raise media_errors.import_path()


def open_source(settings: Settings, parts: list[str]) -> int:
    """Opent de bronmap zonder ooit een symlink te volgen; geeft de fd (de aanroeper sluit
    hem). Anders media_import_path. Blokkeert: in een thread."""
    root = import_root(settings)
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise media_errors.import_path() from None
    fd = os.dup(root_fd)
    try:
        for part in parts:
            try:
                nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            except OSError:
                raise media_errors.import_path() from None
            os.close(fd)
            fd = nxt
        # Nog eens langs de echte paden: onder de importmap, en op hetzelfde bestandssysteem
        real = os.path.realpath(f"/proc/self/fd/{fd}")
        base = os.path.realpath(root)
        inside = real == base or real.startswith(base.rstrip("/") + "/")
        if not inside or os.fstat(fd).st_dev != os.fstat(root_fd).st_dev:
            raise media_errors.import_path()
    except BaseException:
        os.close(fd)
        raise
    finally:
        os.close(root_fd)
    return fd


def _valid(name: str) -> bool:
    """Een naam die in de boom kan (zoals files.clean_name), en geldige UTF-8."""
    from app.routers.files import clean_name

    try:
        name.encode("utf-8")
        return clean_name(name) == name
    except (UnicodeEncodeError, HTTPException):
        return False


async def sources() -> list[str]:
    """De mappen direct onder de importmap die een beheerder kan kiezen (geen symlinks)."""
    root = import_root(runtime.settings())

    def scan() -> list[str]:
        try:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError:
            return []
        try:
            with os.scandir(fd) as found:
                names = [e.name for e in found if e.is_dir(follow_symlinks=False)]
        finally:
            os.close(fd)
        return sorted((n for n in names if _valid(n)), key=lambda n: (n.casefold(), n))

    return await anyio.to_thread.run_sync(scan)


@dataclass
class Survey:
    files: int = 0
    bytes: int = 0
    unreadable: int = 0
    unmovable: int = 0


def _survey(fd: int, move: bool, depth: int = 0, out: Survey | None = None) -> Survey:
    """Vooraf kijken: de bronmap nalopen zonder te tellen (Keuze 2). Blokkeert."""
    out = out if out is not None else Survey()
    try:
        with os.scandir(fd) as found:
            names = sorted(e.name for e in found)
    except OSError:
        out.unreadable += 1
        return out
    for name in names:
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError:
            out.unreadable += 1
            continue
        if stat.S_ISDIR(st.st_mode) and depth < MAX_DEPTH:
            try:
                sub = os.open(name, DIR_FLAGS, dir_fd=fd)
            except OSError:
                out.unreadable += 1
                continue
            try:
                _survey(sub, move, depth + 1, out)
            finally:
                os.close(sub)
        elif stat.S_ISREG(st.st_mode):
            out.files += 1
            out.bytes += st.st_size
            if not os.access(name, os.R_OK, dir_fd=fd):
                out.unreadable += 1
            elif move and (st.st_nlink != 1 or not os.access(".", os.W_OK | os.X_OK, dir_fd=fd)):
                out.unmovable += 1
    return out


async def survey(settings: Settings, source: str, *, move: bool = False) -> ImportSource:
    """Hoeveel er in een bronmap staat, en hoeveel de app niet kan lezen of (bij verplaatsen)
    niet kan verplaatsen; /beheer stelt dan kopiëren voor (Keuze 2)."""
    parts = parts_of(settings, source)

    def walk() -> Survey:
        fd = open_source(settings, parts)
        try:
            return _survey(fd, move)
        finally:
            os.close(fd)

    found = await anyio.to_thread.run_sync(walk)
    return ImportSource(path="/".join(parts), **vars(found))


# --- starten -------------------------------------------------------------------------------------


async def _enqueue(
    session: AsyncSession, parts: list[str], target_id: uuid.UUID, owner_id: uuid.UUID, move: bool
) -> uuid.UUID:
    from app.routers.files import trashed_among

    owner = await session.get(User, owner_id)
    if owner is None or owner.disabled_at is not None or owner.role == "guest":
        raise errors.fail(404, "not_found", "Gebruiker niet gevonden")
    target = await session.get(Node, target_id)
    if (
        target is None
        or target.kind != "dir"
        or target.owner_id != owner.id
        or await trashed_among(session, [target.id])
    ):
        raise errors.fail(404, "folder_not_found", "Map niet gevonden")
    payload: dict[str, Any] = {
        "source": "/".join(parts),
        "target_id": str(target.id),
        "owner_id": str(owner.id),
        "move": bool(move),
        "state": "queued",
    }
    job_id = await jobs.enqueue(session, JOB, payload, key=KEY)
    if job_id is None:
        raise media_errors.import_busy()
    await session.flush()
    job = await session.get(Job, job_id)
    assert job is not None
    job.payload = {**payload, "job": str(job_id)}
    await session.commit()
    return job_id


async def start(
    source: str, target_id: uuid.UUID, owner_id: uuid.UUID, *, move: bool = False
) -> uuid.UUID:
    """Zet de import klaar en geeft het id van de job. 503 media_off, 422 media_import_path,
    404 als de eigenaar of de doelmap (een levende map van de eigenaar) er niet is, 409
    media_import_busy als er al een klaarstaat of loopt."""
    settings = runtime.settings()
    if not settings.media_enabled:
        raise media_errors.off()
    parts = parts_of(settings, source)
    os.close(await anyio.to_thread.run_sync(open_source, settings, parts))
    async with _maker(settings)() as session:
        return await _enqueue(session, parts, target_id, owner_id, move)


async def _target_of(session: AsyncSession, owner: User, target: str) -> Node:
    """Een pad als /Films in de boom van de eigenaar (levende mappen, zonder op hoofdletters te
    letten). Anders 404 folder_not_found."""
    from app.routers.files import sibling

    node = await session.get(Node, owner.root_id)
    for part in (p for p in target.split("/") if p):
        found = await sibling(session, node.id, part) if node is not None else None
        if found is None or found.kind != "dir":
            raise errors.fail(404, "folder_not_found", "Map niet gevonden")
        node = found
    if node is None:
        raise errors.fail(404, "folder_not_found", "Map niet gevonden")
    return node


async def start_cli(
    settings: Settings, source: str, target: str, owner_email: str, *, move: bool = False
) -> uuid.UUID:
    """Voor app.cli media-import: de doelmap als pad (/Films) en de eigenaar als e-mailadres;
    zet dezelfde import klaar als /beheer. Fouten als die van de API (HTTPException)."""
    if not settings.media_enabled:
        raise media_errors.off()
    parts = parts_of(settings, source)
    os.close(await anyio.to_thread.run_sync(open_source, settings, parts))
    async with _maker(settings)() as session:
        owner = await session.scalar(
            select(User).where(func.lower(User.email) == owner_email.strip().lower())
        )
        if owner is None:
            raise errors.fail(404, "not_found", "Gebruiker niet gevonden")
        node = await _target_of(session, owner, target)
        return await _enqueue(session, parts, node.id, owner.id, move)


# --- de voortgang --------------------------------------------------------------------------------


@dataclass
class Progress:
    state: str = "running"
    total: int = 0
    done: int = 0
    same: int = 0  # stond er al, met dezelfde inhoud
    skipped: int = 0
    bytes: int = 0
    reason: str | None = None
    items: list[dict[str, str]] = field(default_factory=list)

    def skip(self, path: str, reason: str) -> None:
        self.skipped += 1
        if len(self.items) < MAX_LISTED:
            self.items.append({"path": path, "reason": reason})

    def fields(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "total": self.total,
            "done": self.done,
            "same": self.same,
            "skipped": self.skipped,
            "bytes": self.bytes,
            "reason": self.reason,
            "skipped_items": list(self.items),
        }


def view(job: Job) -> ImportView:
    """De import van deze job, voor /beheer (M10)."""
    p = job.payload or {}
    state = p.get("state") or "queued"
    if job.status == "failed":
        state = "failed"
    elif job.status == "queued":
        state = "queued"
    elif job.status == "running" and state == "queued":
        state = "running"
    return ImportView(
        job=job.id,
        state=state,
        source=p.get("source", ""),
        target_id=uuid.UUID(p["target_id"]),
        owner_id=uuid.UUID(p["owner_id"]),
        move=bool(p.get("move")),
        done=int(p.get("done", 0)),
        total=int(p.get("total", 0)),
        skipped=int(p.get("skipped", 0)),
        bytes=int(p.get("bytes", 0)),
        skipped_items=[SkippedItem(**i) for i in p.get("skipped_items", [])],
    )


async def current(session: AsyncSession) -> ImportView | None:
    """De laatste import (klaar, lopend of wachtend), of None."""
    job = await session.scalar(
        select(Job).where(Job.type == JOB).order_by(Job.created_at.desc()).limit(1)
    )
    return view(job) if job is not None else None


async def _admins(session: AsyncSession) -> list[uuid.UUID]:
    rows = await session.scalars(select(User.id).where(User.role == "admin"))
    return list(rows.all())


# --- lezen en tellen (in een thread) -------------------------------------------------------------


def _hash(fd: int) -> tuple[str, int]:
    """De sha van wat achter deze fd staat, vanaf het begin."""
    digest = hashlib.sha256()
    offset = 0
    while chunk := os.pread(fd, CHUNK, offset):
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest(), offset


def _copy(fd: int, tmp_dir: Path) -> tuple[Path, str, int]:
    """Kopieert wat achter deze fd staat naar een tijdelijk bestand in de werkmap van de
    BlobStore, en telt intussen de sha."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out, name = tempfile.mkstemp(dir=tmp_dir, prefix="import-")
    digest = hashlib.sha256()
    offset = 0
    try:
        while chunk := os.pread(fd, CHUNK, offset):
            digest.update(chunk)
            rest = memoryview(chunk)
            while rest:
                rest = rest[os.write(out, rest) :]
            offset += len(chunk)
    except BaseException:
        os.close(out)
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise
    os.close(out)
    return Path(name), digest.hexdigest(), offset


def _unchanged(a: os.stat_result, b: os.stat_result, *, ctime: bool = True) -> bool:
    """Is dit nog hetzelfde bestand, zonder wijziging? (Na een rename verandert ctime.)"""
    keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns") + (("st_ctime_ns",) if ctime else ())
    return all(getattr(a, k) == getattr(b, k) for k in keys)


def _open_file(name: str, dir_fd: int) -> int:
    return os.open(name, FILE_FLAGS, dir_fd=dir_fd)


def _link(name: str, staged: str, dir_fd: int, tmp_fd: int) -> None:
    """Een tweede naam voor de bron in de werkmap (linkat, geen symlink volgen)."""
    os.link(name, staged, src_dir_fd=dir_fd, dst_dir_fd=tmp_fd, follow_symlinks=False)


def _mine(st: os.stat_result) -> bool:
    """Is dit bestand van de app zelf? Alleen dan mag het (met een link) de blob worden: een
    bestand van een ander houdt zijn eigenaar, en wie dat is, kan de blob dan schrijven."""
    return st.st_uid == os.geteuid()


def _seal(fd: int) -> None:
    """Modus 0600 en geen ACL: zoals een blob uit mkstemp (review golf 3, S2). Een bron met
    0666 (een NAS, een share, umask 000) werd anders een blob die de worker (een andere uid)
    via /proc/self/fd met O_WRONLY kon openen en overschrijven."""
    os.fchmod(fd, BLOB_MODE)
    with contextlib.suppress(OSError):  # geen ACL, of het bestandssysteem kent ze niet
        os.removexattr(fd, "system.posix_acl_access")


def _remove_source(name: str, dir_fd: int, before: os.stat_result, *, ctime: bool) -> None:
    """De bron weg, als het nog hetzelfde bestand is. OSError als dat niet lukt."""
    if not _unchanged(before, os.stat(name, dir_fd=dir_fd, follow_symlinks=False), ctime=ctime):
        raise OSError(errno.ESTALE, "een ander bestand")
    os.unlink(name, dir_fd=dir_fd)


def _sweep_links(tmp_fd: int) -> int:
    """De links die een gestopte import achterliet (LINK_PREFIX): weg. Er is hooguit één import
    tegelijk, en een link is nooit de enige naam (de bron of de blob heeft er ook een); met maar
    één naam blijft hij staan (dan is hij het bestand zelf). Geeft het aantal."""
    removed = 0
    with os.scandir(tmp_fd) as found:
        names = [e.name for e in found if e.name.startswith(LINK_PREFIX)]
    for name in names:
        with contextlib.suppress(OSError):
            if os.stat(name, dir_fd=tmp_fd, follow_symlinks=False).st_nlink > 1:
                os.unlink(name, dir_fd=tmp_fd)
                removed += 1
            else:
                log.error("import: %s in blobs/.tmp heeft geen andere naam; blijft staan", name)
    return removed


# --- de job --------------------------------------------------------------------------------------


class Stop(Exception):
    """De import stopt (quotum op); wat binnen is, blijft."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Staged:
    """Het bestand, klaar voor place_file: een link in de werkmap (verplaatsen), of een kopie."""

    path: Path
    linked: bool  # een tweede naam van de bron (anders een kopie)


@dataclass
class Run:
    settings: Settings
    job_id: uuid.UUID
    base: dict[str, Any]
    owner_id: uuid.UUID
    move: bool
    blobs: BlobStore
    tmp_fd: int
    root_dev: int
    admins: list[uuid.UUID]
    progress: Progress = field(default_factory=Progress)
    saved_at: float = 0.0

    async def save(self, force: bool = False) -> None:
        """De voortgang in de payload van de job, en als event naar de beheerders."""
        now = time.monotonic()
        if not force and now - self.saved_at < SAVE_EVERY_S:
            return
        self.saved_at = now
        p = self.progress
        async with _maker(self.settings)() as session:
            await session.execute(
                update(Job).where(Job.id == self.job_id).values(payload={**self.base, **p.fields()})
            )
            await session.commit()
        event = ImportEvent(
            job=self.job_id,
            state=p.state,  # type: ignore[arg-type]
            done=p.done,
            total=p.total,
            skipped=p.skipped,
            bytes=p.bytes,
        )
        events.publish(self.admins, EVENT_IMPORT, event.model_dump(mode="json"))

    async def folder(self, parent_id: uuid.UUID, name: str) -> uuid.UUID | None:
        """De map `name` in de doelmap: hergebruikt als hij er al is, anders nieuw. None als de
        naam al van een bestand is."""
        from app.routers.files import notify, sibling

        async with _maker(self.settings)() as session:
            existing = await sibling(session, parent_id, name)
            if existing is not None:
                return existing.id if existing.kind == "dir" else None
            folder = Node(parent_id=parent_id, owner_id=self.owner_id, name=name, kind="dir")
            session.add(folder)
            try:
                await session.commit()
            except IntegrityError:  # de doelmap is weg, of de naam kwam er net bij
                await session.rollback()
                existing = await sibling(session, parent_id, name)
                if existing is None:
                    raise jobs.JobError("de doelmap is weg") from None
                return existing.id if existing.kind == "dir" else None
            await notify(session, folder, "node.created")
            return folder.id

    async def existing(self, parent_id: uuid.UUID, name: str) -> tuple[bool, str | None]:
        """(de naam is vrij of van een bestand, de sha van dat bestand)."""
        from app.routers.files import sibling

        async with _maker(self.settings)() as session:
            found = await sibling(session, parent_id, name)
        if found is None:
            return True, None
        return found.kind == "file", found.blob_id

    async def place(
        self, parent_id: uuid.UUID, name: str, sha: str, size: int, source: Path, mtime: float
    ) -> None:
        """Het bestand in de boom (place_file: quotum, ontdubbelen, een versie bij een andere
        sha). Mislukt: `source` staat er nog."""
        from app.routers.files import place_file

        async with _maker(self.settings)() as session:
            parent = await session.get(Node, parent_id)
            owner = await session.get(User, self.owner_id)
            if parent is None or owner is None:
                raise jobs.JobError("de doelmap of de eigenaar is weg")
            try:
                await place_file(
                    session,
                    self.blobs,
                    owner,
                    parent,
                    name,
                    "replace",
                    sha,
                    size,
                    source,
                    actor=owner,
                    client_mtime=datetime.fromtimestamp(mtime, UTC),
                    tz=self.settings.timezone,
                )
            except HTTPException as exc:
                code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
                if code in ("no_room", "no_room_owner"):
                    raise Stop("no_room") from None
                if code == "folder_not_found":
                    raise jobs.JobError("de doelmap is weg") from None
                raise

    async def stage(
        self, fd: int, dir_fd: int, name: str, before: os.stat_result, sha: str, path: str
    ) -> Staged | None:
        """Verplaatsen: een link naar de bron in blobs/.tmp, nakijken dat het nog hetzelfde
        bestand is (met precies twee namen), en dan modus 0600. De bron blijft tot na
        place_file. Een bestand van een ander, of EXDEV: een kopie (de bron gaat ook dan weg
        na place_file). None: overgeslagen."""
        p = self.progress
        if not _mine(before):
            return await self.copy_for_move(fd, before, sha, path)
        staged = f"{LINK_PREFIX}{uuid.uuid4().hex}"
        try:
            _link(name, staged, dir_fd, self.tmp_fd)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                p.skip(path, UNMOVABLE)
                return None
            return await self.copy_for_move(fd, before, sha, path)
        try:
            now = os.stat(staged, dir_fd=self.tmp_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(now.st_mode)
                or now.st_nlink != 2
                or not _unchanged(before, now, ctime=False)
            ):
                # Tussen het tellen en de link kwam er iets anders, of een derde naam: overslaan
                raise OSError(errno.ESTALE, "een ander bestand")
            _seal(fd)
        except OSError as exc:
            os.unlink(staged, dir_fd=self.tmp_fd)
            p.skip(path, IN_USE if exc.errno == errno.ESTALE else UNMOVABLE)
            return None
        return Staged(self.blobs.tmp / staged, linked=True)

    async def copy_for_move(
        self, fd: int, before: os.stat_result, sha: str, path: str
    ) -> Staged | None:
        """Verplaatsen met een kopie (een ander bestandssysteem, of een bestand van een ander):
        de kopie gaat de BlobStore in, de bron gaat weg na place_file."""
        tmp, again, _ = await anyio.to_thread.run_sync(_copy, fd, self.blobs.tmp)
        if again != sha or not _unchanged(before, os.fstat(fd)):
            tmp.unlink(missing_ok=True)
            self.progress.skip(path, IN_USE)
            return None
        return Staged(tmp, linked=False)

    async def file(self, dir_fd: int, name: str, parent_id: uuid.UUID, path: str) -> None:
        p = self.progress
        try:
            fd = _open_file(name, dir_fd)
        except OSError as exc:
            p.skip(path, SYMLINK if exc.errno == errno.ELOOP else UNREADABLE)
            return
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                p.skip(path, SPECIAL)
                return
            if time.time() - before.st_mtime < MIN_AGE_S:
                p.skip(path, IN_USE)
                return
            free, existing = await self.existing(parent_id, name)
            if self.move and before.st_nlink != 1:
                if existing is not None and self.is_blob(before, existing):
                    # Een vorige import stopte na place_file en vóór het weghalen: de bron is
                    # de blob al (dezelfde inode). Alleen de naam in de bron moet nog weg.
                    try:
                        _remove_source(name, dir_fd, before, ctime=True)
                    except OSError:
                        p.skip(path, NOT_REMOVED)
                        return
                    p.same += 1
                    p.done += 1
                    return
                p.skip(path, HARD_LINK)
                return
            if not free:
                p.skip(path, NAME_TAKEN)
                return
            await self._file(fd, dir_fd, name, parent_id, path, before, existing)
        finally:
            os.close(fd)

    def is_blob(self, st: os.stat_result, sha: str) -> bool:
        """Is dit bestand de blob van deze sha zelf (dezelfde inode)?"""
        try:
            blob = os.stat(self.blobs.path(sha), follow_symlinks=False)
        except OSError:
            return False
        return (blob.st_dev, blob.st_ino) == (st.st_dev, st.st_ino)

    async def _file(self, fd: int, dir_fd: int, name: str, parent_id: uuid.UUID, path: str,
                    before: os.stat_result, existing: str | None) -> None:  # fmt: skip
        p = self.progress
        copy: Path | None = None
        try:
            try:
                if self.move:
                    sha, size = await anyio.to_thread.run_sync(_hash, fd)
                else:
                    copy, sha, size = await anyio.to_thread.run_sync(_copy, fd, self.blobs.tmp)
            except OSError:
                p.skip(path, UNREADABLE)
                return
            if size != before.st_size or not _unchanged(before, os.fstat(fd)):
                p.skip(path, IN_USE)
                return
            if existing == sha:  # dezelfde naam met dezelfde inhoud: dat was al gedaan
                p.same += 1
                p.done += 1
                return
            staged = Staged(copy, linked=False) if copy is not None else None
            if self.move:
                staged = await self.stage(fd, dir_fd, name, before, sha, path)
                if staged is None:
                    return
            assert staged is not None
            copy = None  # vanaf hier ruimt _place hem op
            await self._place(staged, dir_fd, name, parent_id, sha, size, before, path)
        finally:
            if copy is not None:
                copy.unlink(missing_ok=True)

    async def _place(self, staged: Staged, dir_fd: int, name: str, parent_id: uuid.UUID,
                     sha: str, size: int, before: os.stat_result, path: str) -> None:  # fmt: skip
        """place_file; mislukt het, dan de link of de kopie weg (de bron staat er nog). Bij
        verplaatsen daarna de bron weg, als het nog hetzelfde bestand is: pas na de commit, zodat
        een gestopte app nooit een bestand kwijt is (C7)."""
        p = self.progress
        try:
            await self.place(parent_id, name, sha, size, staged.path, before.st_mtime)
        except BaseException:
            # Synchroon (geen await): ook bij een cancel; de bron is er nog
            staged.path.unlink(missing_ok=True)
            raise
        p.done += 1
        p.bytes += size
        if self.move:
            try:
                # Na een link veranderde ctime (de link en de modus); na een kopie niet
                _remove_source(name, dir_fd, before, ctime=not staged.linked)
            except OSError:
                p.skip(path, NOT_REMOVED)

    async def walk(self, dir_fd: int, parent_id: uuid.UUID, rel: str, depth: int) -> None:
        p = self.progress

        def listing() -> list[str]:
            with os.scandir(dir_fd) as found:
                return sorted((e.name for e in found), key=lambda n: (n.casefold(), n))

        try:
            names = await anyio.to_thread.run_sync(listing)
        except OSError:
            p.skip(rel or ".", UNREADABLE)
            return
        for name in names:
            path = f"{rel}/{name}" if rel else name
            try:
                st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except OSError:
                p.skip(path, UNREADABLE)
                continue
            if stat.S_ISLNK(st.st_mode):
                p.skip(path, SYMLINK)
            elif not _valid(name):
                p.skip(path, BAD_NAME)
            elif stat.S_ISDIR(st.st_mode):
                await self.subdir(dir_fd, name, st, parent_id, path, depth)
            elif stat.S_ISREG(st.st_mode):
                await self.file(dir_fd, name, parent_id, path)
            else:
                p.skip(path, SPECIAL)
            await self.save()

    async def subdir(self, dir_fd: int, name: str, st: os.stat_result, parent_id: uuid.UUID,
                     path: str, depth: int) -> None:  # fmt: skip
        p = self.progress
        if st.st_dev != self.root_dev:
            p.skip(path, OTHER_FS)
            return
        if depth >= MAX_DEPTH:
            p.skip(path, TOO_DEEP)
            return
        try:
            sub = os.open(name, DIR_FLAGS, dir_fd=dir_fd)
        except OSError as exc:
            p.skip(path, SYMLINK if exc.errno == errno.ELOOP else UNREADABLE)
            return
        try:
            folder = await self.folder(parent_id, name)
            if folder is None:
                p.skip(path, NAME_TAKEN)
            else:
                await self.walk(sub, folder, path, depth + 1)
        finally:
            os.close(sub)


async def run(settings: Settings, payload: dict[str, Any]) -> None:
    from app.routers.files import trashed_among

    job_id = uuid.UUID(payload["job"])
    base = {k: payload[k] for k in ("job", "source", "target_id", "owner_id", "move")}
    try:
        parts = parts_of(settings, payload["source"])
        source_fd = await anyio.to_thread.run_sync(open_source, settings, parts)
    except HTTPException:
        raise jobs.JobError("de bronmap ligt niet (meer) in de importmap") from None
    try:
        async with _maker(settings)() as session:
            owner = await session.get(User, uuid.UUID(payload["owner_id"]))
            target = await session.get(Node, uuid.UUID(payload["target_id"]))
            if owner is None or target is None or target.kind != "dir":
                raise jobs.JobError("de doelmap of de eigenaar is weg")
            if await trashed_among(session, [target.id]):
                raise jobs.JobError("de doelmap ligt in de prullenbak")
            admins = await _admins(session)
        blobs = BlobStore(settings.blob_dir)
        await anyio.Path(blobs.tmp).mkdir(parents=True, exist_ok=True)
        tmp_fd = os.open(blobs.tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            swept = await anyio.to_thread.run_sync(_sweep_links, tmp_fd)
            if swept:
                log.info("import %s: %d links van een gestopte import weg", job_id, swept)
            job = Run(
                settings=settings,
                job_id=job_id,
                base=base,
                owner_id=owner.id,
                move=bool(payload.get("move")),
                blobs=blobs,
                tmp_fd=tmp_fd,
                root_dev=os.fstat(source_fd).st_dev,
                admins=admins,
            )
            counted = await anyio.to_thread.run_sync(_survey, source_fd, job.move)
            job.progress.total = counted.files
            await job.save(force=True)
            try:
                await job.walk(source_fd, target.id, "", 0)
                job.progress.state = "done"
            except Stop as stop:
                job.progress.state = "stopped"
                job.progress.reason = stop.reason
            await job.save(force=True)
            p = job.progress
            log.info(
                "import %s: %s, %d van %d binnen (%d al), %d overgeslagen",
                job_id, p.state, p.done, p.total, p.same, p.skipped,
            )  # fmt: skip
        finally:
            os.close(tmp_fd)
    finally:
        os.close(source_fd)


async def failed(settings: Settings, payload: dict[str, Any], error: str) -> None:
    """De import ging mis (na de pogingen): het event naar de beheerders."""
    async with _maker(settings)() as session:
        admins = await _admins(session)
    event = ImportEvent(
        job=uuid.UUID(payload["job"]),
        state="failed",
        done=int(payload.get("done", 0)),
        total=int(payload.get("total", 0)),
        skipped=int(payload.get("skipped", 0)),
        bytes=int(payload.get("bytes", 0)),
    )
    events.publish(admins, EVENT_IMPORT, event.model_dump(mode="json"))


jobs.register(JOB, run, on_dead=failed, max_attempts=2, lane=LANE, timeout=TIMEOUT)
