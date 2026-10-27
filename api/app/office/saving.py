"""Opslaan uit een callback (Keuze 15, 16 en 17), los van OnlyOffice.

handle(event) -> uitkomst: de hele callback. De adapter leest hem (engine.read_callback) en
maakt van de uitkomst een antwoord (engine.callback_reply):
- "saved", "same", "copy", "ignored": {"error": 0};
- "refused": {"error": 1}, een blijvende fout (DS bewaart het dan als forgotten file, Keuze 21);
- "retry": 503, een tijdelijke fout (DS probeert het opnieuw, tot 8 keer).

store(row, tmp, sha, size, event) -> uitkomst: stap 5 tot en met 9 van het recept, als de
inhoud al gedownload is. De aanroeper houdt het slot van het bestand vast.

Het recept (Keuze 16): het merk en de regels voor een dichte sessie eerst; dan het slot van het
bestand (altijd vóór user_lock en blobs.lock); bij status 2 of 3 de sessie op saving en
committen; meteen downloaden, hooguit min(office_max_save_bytes, de ruimte van de eigenaar plus
de grootte van wat er nu staat: een squash geeft die vrij); het bestand vers laden (weg: niets
schrijven); wie slaat op (policy.choose_actor); dan place_file(..., "replace", exact=True,
only=node.id, squash=...) of een herstelkopie bij de eigenaar; tot slot merk, tussenstand, tijd
en versies bijwerken en committen, en pas dan het antwoord. Gebeurtenissen gaan pas na de
commit.
"""

import asyncio
import base64
import hashlib
import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
import httpx
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Node, OfficeSession, User
from app.office import engine, policy, runtime, sessions, urls
from app.office.engine import CallbackEvent
from app.office.state import OfficeConfig, office_policy
from app.routers.files import is_trashed, place_file, room_left, writable
from app.storage import BlobStore

log = logging.getLogger("platform.office")

SAVES = ("final", "failed", "checkpoint")
CHUNK = 64 * 1024

# Lopende drops (status 1 op een dichte sessie), zodat ze niet weg zijn voor ze klaar zijn
_background: set[asyncio.Task] = set()


class DownloadError(Exception):
    """Het ophalen bij DS mislukte (tijdelijk: DS probeert het opnieuw)."""


class DownloadTooLarge(DownloadError):
    """Groter dan de grens: niets bewaard."""


def repr_digest(sha: str) -> str:
    """Repr-Digest voor een blob: sha-256=:<base64>: (RFC 9530), zoals DS hem controleert."""
    return "sha-256=:" + base64.b64encode(bytes.fromhex(sha)).decode() + ":"


async def download(settings: Settings, key: str, url: str, limit: int) -> tuple[Path, str, int]:
    """Een adres uit een callback ophalen naar blobs.root/.tmp: (pad, sha, grootte).

    Alleen pad en query tellen, en het pad moet bij deze key passen (urls.rewrite, Keuze 13):
    anders ValueError en gaat er niets weg. Het verzoek gaat via de http-client van de adapter
    (engine.upstream, regel 7 van §2.0). Groter dan limit: DownloadTooLarge; een andere fout:
    DownloadError. Bij een fout blijft er niets staan."""
    target = urls.rewrite(settings, url, key=key, kind="data")
    base = settings.office_url.rstrip("/")
    path = target[len(base) :]
    upstream = engine.upstream()
    if upstream is None:
        raise DownloadError("office staat uit")
    blobs = BlobStore(settings.blob_dir)
    await anyio.Path(blobs.tmp).mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=blobs.tmp)
    os.close(fd)
    tmp = Path(name)
    digest = hashlib.sha256()
    size = 0
    try:
        try:
            response = await upstream.open("GET", path, [("accept-encoding", "identity")])
        except httpx.HTTPError as exc:
            raise DownloadError(type(exc).__name__) from exc
        try:
            if response.status_code != 200:
                raise DownloadError(f"HTTP {response.status_code}")
            encoding = response.headers.get("content-encoding", "identity").strip().lower()
            if encoding not in ("", "identity"):
                raise DownloadError(f"onverwachte codering {encoding}")
            async with await anyio.open_file(tmp, "wb") as out:
                async for chunk in response.aiter_raw(CHUNK):
                    size += len(chunk)
                    if size > limit:
                        raise DownloadTooLarge(f"groter dan {limit} bytes")
                    digest.update(chunk)
                    await out.write(chunk)
        except (httpx.HTTPError, httpx.StreamError) as exc:
            raise DownloadError(type(exc).__name__) from exc
        finally:
            await response.aclose()
    except BaseException:
        with anyio.CancelScope(shield=True):
            await anyio.Path(tmp).unlink(missing_ok=True)
        raise
    return tmp, digest.hexdigest(), size


async def alive(session: AsyncSession, row: OfficeSession) -> tuple[Node | None, User | None]:
    """Het bestand en de eigenaar, vers uit de database; None als ze definitief weg zijn."""
    node = None
    if row.node_id is not None:
        node = await session.get(Node, row.node_id, populate_existing=True)
    owner = None
    owner_id = node.owner_id if node is not None else row.owner_id
    if owner_id is not None:
        owner = await session.get(User, owner_id, populate_existing=True)
    if node is not None and node.kind != "file":
        node = None
    return node, owner


async def limit_for(
    session: AsyncSession, settings: Settings, owner: User, current: int = 0
) -> tuple[int, bool]:
    """(grens, door het quotum): de kleinste van office_max_save_bytes en de ruimte van de
    eigenaar plus current, de grootte van wat er nu staat (Keuze 16, stap 4). Vervangt de opslag
    de vorige tussenstand (squash), dan komt die ruimte vrij; of dat zo is, en wat het precies
    kost, rekent place_file onder de sloten."""
    room = await room_left(session, owner)
    cap = settings.office_max_save_bytes
    if room is not None and room + current < cap:
        return room + current, True
    return cap, False


def _mark(row: OfficeSession, event: CallbackEvent) -> None:
    if event.mark and (row.applied_mark is None or event.mark > row.applied_mark):
        row.applied_mark = event.mark


def _old_mark(row: OfficeSession, event: CallbackEvent) -> bool:
    """Een opslag met een lager merk dan wat al is toegepast: een late herhaling (A9: gelijk
    mag, dan beslist de sha)."""
    return bool(row.applied_mark and event.mark and event.mark < row.applied_mark)


# --- de callback ---


async def handle(event: CallbackEvent) -> str:
    """Eén callback van de editor; de uitkomst voor callback_reply."""
    try:
        async with sessions.maker()() as session:
            row = await sessions.find_by_key(session, event.key)
            if row is None:
                # Een onbekende key: status 1 en 4 zijn goed, een opslag niet (Keuze 15)
                if event.kind in SAVES:
                    log.warning("office: opslag voor een onbekende key")
                    return "refused"
                return "ignored"
            if event.kind == "presence":
                return await _presence(session, row, event)
            if event.kind == "closed":
                return await _closed(session, row)
            if event.kind in SAVES:
                return await _save(session, row, event)
            if event.kind == "checkpoint_failed":
                log.warning("office: tussentijds opslaan mislukt in de editor (status 7)")
            return "ignored"
    except DBAPIError:
        log.exception("office: database bij een callback")
        return "retry"


def _lock(row: OfficeSession) -> asyncio.Lock:
    if row.node_id is None:
        return asyncio.Lock()  # het bestand is weg: niemand anders schrijft erin
    return sessions.office().node_lock(row.node_id)


async def _presence(session: AsyncSession, row: OfficeSession, event: CallbackEvent) -> str:
    """Status 1: wie er nu in zit. Op een dichte sessie: die gebruikers eruit (drop), zodat hun
    editor opnieuw laadt met de key van nu."""
    lock = _lock(row)
    await session.commit()
    async with lock:
        row = await sessions.find_by_key(session, event.key)
        if row is None:
            return "ignored"
        if row.state == "closed":
            if policy.late(row, event) == "drop":
                await session.commit()
                _drop_stale(row, event.connected)
            return "ignored"
        unknown = [uid for uid in event.connected if uid not in (row.members or {})]
        if unknown:
            log.info("office: %d onbekende gebruiker(s) in de editor", len(unknown))
        row.connected = list(event.connected)
        if row.state == "pending" or (row.state == "saving" and event.connected):
            row.state = "open"
            row.updated_at = sessions.now()
        await session.commit()
    node, _owner = await alive(session, row)
    if node is not None:
        await sessions.publish_presence(session, row, node)
    await session.commit()
    return "ignored"


def _drop_stale(row: OfficeSession, connected: list[str]) -> None:
    eng = engine.current()
    if eng is None:
        return
    members = sessions.member_ids(row)
    users = [members[uid] for uid in connected if uid in members]
    if row.node_id is not None and users:
        sessions.publish_dropped(users, row.node_id, row.key, "stale")

    async def go() -> None:
        try:
            await eng.drop(row.key, list(connected))
        except Exception:  # noqa: BLE001 - de editor weg: de ronde probeert het later
            log.warning("office: drop op een dichte key mislukt")

    task = asyncio.ensure_future(go())
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _closed(session: AsyncSession, row: OfficeSession) -> str:
    """Status 4: dicht zonder wijzigingen (ook na elke info op een dichte key)."""
    lock = _lock(row)
    key = row.key
    await session.commit()
    async with lock:
        row = await sessions.find_by_key(session, key)
        if row is not None and row.state != "closed":
            sessions.close(row, "no_changes")
        await session.commit()
    return "ignored"


async def _save(session: AsyncSession, row: OfficeSession, event: CallbackEvent) -> str:
    """Status 2, 3 en 6 (Keuze 16)."""
    settings = runtime.settings()
    if _old_mark(row, event):
        return "ignored"
    lock = _lock(row)
    await session.commit()
    async with lock:
        row = await sessions.find_by_key(session, event.key)
        if row is None:
            return "refused"
        late = row.state == "closed"
        if late and policy.late(row, event) != "save":
            return "ignored"
        if _old_mark(row, event):
            return "ignored"
        final = event.kind in ("final", "failed")
        if event.kind == "failed" and not event.url:
            # A8: status 3 heeft geen adres en geen herstelbestand. Dicht als verloren; wat
            # het laatst is opgeslagen, blijft
            if late:
                return "ignored"
            sessions.close(row, "lost", "error_status")
            await session.commit()
            node, _ = await alive(session, row)
            await sessions.publish_unsaved(session, row, node, "error_status", True)
            await session.commit()
            return "ignored"
        if not event.url:
            if final and not late:
                sessions.close(row, "no_changes")
                await session.commit()
            return "ignored"
        if final and not late:
            row.state = "saving"
            row.updated_at = sessions.now()
            await session.commit()
        node, owner = await alive(session, row)
        if node is None or owner is None:
            if not late:
                sessions.close(row, "gone")
                await session.commit()
            return "ignored"
        limit, by_room = await limit_for(session, settings, owner, node.size or 0)
        await session.commit()  # geen transactie open tijdens het downloaden
        try:
            tmp, sha, size = await download(settings, row.key, event.url, limit)
        except ValueError:
            log.warning("office: adres in een callback hoort niet bij de key; niet opgehaald")
            return "refused"
        except DownloadTooLarge:
            return await refuse(session, row.key, event, "no_room" if by_room else "too_large")
        except DownloadError as exc:
            log.warning("office: ophalen van de opslag mislukt (%s); DS probeert opnieuw", exc)
            return "retry"
        try:
            return await store(row, tmp, sha, size, event)
        finally:
            await anyio.Path(tmp).unlink(missing_ok=True)


async def refuse(session: AsyncSession, key: str, event: CallbackEvent, reason: str) -> str:
    """Een blijvende fout (te groot, quotum vol): niets geschreven, {"error": 1}, en een
    melding. Een laatste opslag gaat dan naar de forgotten files van DS (Keuze 21). Op key:
    na een rollback (place_file) zijn de objecten van de sessie verlopen."""
    row = await sessions.find_by_key(session, key)
    if row is None:
        return "refused"
    final = event.kind in ("final", "failed")
    if final and row.state != "closed":
        sessions.close(row, "refused", reason)
    else:
        row.last_error = reason
    await session.commit()
    node, _owner = await alive(session, row)
    await sessions.publish_unsaved(session, row, node, reason, final)
    await session.commit()
    return "refused"


async def store(row: OfficeSession, tmp: Path, sha: str, size: int, event: CallbackEvent) -> str:
    """Stap 5 tot en met 9 van Keuze 16 met de gedownloade inhoud. Het slot van het bestand
    houdt de aanroeper vast."""
    settings = runtime.settings()
    async with sessions.maker()() as session:
        row = await sessions.find_by_key(session, row.key) or row
        cfg = await office_config(session)
        late = row.state == "closed"
        final = event.kind in ("final", "failed")
        node, owner = await alive(session, row)
        if node is None or owner is None:
            # Definitief weg (ook de eigenaar): niets schrijven, geen kopie (Keuze 19)
            if not late:
                sessions.close(row, "gone")
                await session.commit()
            return "ignored"
        if node.blob_id == sha:
            # Dezelfde inhoud als nu: een herhaling, of er veranderde niets
            _mark(row, event)
            row.blob_id = sha
            row.saved_at = sessions.now()
            if final and not late:
                sessions.close(row, "saved")
            await session.commit()
            return "same"
        if late and node.blob_id != row.blob_id:
            # Een late opslag terwijl het bestand intussen veranderde: nooit eroverheen
            return await save_copy(
                session, settings, row, node, owner, tmp, sha, size, event, "forgotten"
            )
        if event.kind == "failed":
            return await save_copy(
                session, settings, row, node, owner, tmp, sha, size, event, "status3"
            )
        actor = await pick_actor(session, row, node, event.editors)
        where = policy.target(True, True, actor)
        if where == "copy":
            if not final:
                # Een tussenstand zonder schrijver: geen kopie (anders elke 5 minuten één)
                log.info("office: tussenstand zonder iemand met schrijfrecht; niet opgeslagen")
                return "refused"
            return await save_copy(
                session, settings, row, node, owner, tmp, sha, size, event, "no_writer"
            )
        assert actor is not None
        return await save_original(
            session, settings, cfg, row, node, owner, actor, tmp, sha, size, event, squash=not late
        )


async def office_config(session: AsyncSession) -> OfficeConfig:
    """De OfficeConfig van nu (zonder app, zoals in app.cli: de standaard)."""
    app = runtime.app()
    if app is None:
        return OfficeConfig()
    return await office_policy(app).get(session)


async def pick_actor(
    session: AsyncSession, row: OfficeSession, node: Node, editors: list[str]
) -> uuid.UUID | None:
    """Wie slaat op (Keuze 16, stap 6): de laatste bewerker, dan actions, dan de rest van
    members; niet banned, nu nog schrijfrecht, niet geblokkeerd."""
    members = sessions.member_ids(row)
    allowed = await sessions.writers(session, node, members.values())
    by_uid = {uid: str(m) for uid, m in members.items()}
    return policy.choose_actor(editors, [], by_uid, row.banned or [], allowed.__contains__)


async def save_original(
    session: AsyncSession,
    settings: Settings,
    cfg: OfficeConfig,
    row: OfficeSession,
    node: Node,
    owner: User,
    actor_id: uuid.UUID,
    tmp: Path,
    sha: str,
    size: int,
    event: CallbackEvent | None,
    *,
    squash: bool,
) -> str:
    """place_file op het bestand zelf (exact, op id). squash False: wat er nu staat, blijft
    altijd als versie (een late opslag, het herstel)."""
    at = sessions.now()
    key = row.key  # na een mislukte place_file (rollback) zijn de objecten verlopen
    version = not squash or policy.make_version(row, node.blob_id or "", at, cfg)
    actor = await session.get(User, actor_id)
    parent = await session.get(Node, node.parent_id) if node.parent_id else node
    guard = None if actor_id == owner.id else writable(actor_id, node.id)
    final = event is not None and event.kind in ("final", "failed")
    late = row.state == "closed"
    try:
        await place_file(
            session,
            BlobStore(settings.blob_dir),
            owner,
            parent or node,
            node.name,
            "replace",
            sha,
            size,
            tmp,
            actor=actor,
            guard=guard,
            only=node.id,
            exact=True,
            squash=None if version else row.checkpoint,
            tz=settings.timezone,
        )
    except HTTPException as exc:
        return await _placing_failed(session, key, exc, event)
    row = await sessions.find_by_key(session, key) or row
    if event is not None:
        _mark(row, event)
    row.checkpoint = sha
    row.blob_id = sha
    row.saved_at = at
    if version:
        row.versions_made = (row.versions_made or 0) + 1
        row.versioned_at = at
    if final and not late:
        sessions.close(row, "saved")
    await session.commit()
    return "saved"


async def _placing_failed(
    session: AsyncSession, key: str, exc: HTTPException, event: CallbackEvent | None
) -> str:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = detail.get("code")
    await session.rollback()
    if code in ("no_room", "no_room_owner"):
        if event is None:
            raise exc
        return await refuse(session, key, event, "no_room")
    if code == "not_found":  # exact: intussen definitief weg
        row = await sessions.find_by_key(session, key)
        if (
            row is not None
            and row.state != "closed"
            and event is not None
            and event.kind != "checkpoint"
        ):
            sessions.close(row, "gone")
            await session.commit()
        return "ignored"
    if code == "folder_not_found":
        # De guard: de schrijver verloor zijn recht vlak voor het neerzetten. Opnieuw: dan
        # kiest het recept een ander, of maakt het een herstelkopie
        return "retry"
    raise exc


async def save_copy(
    session: AsyncSession,
    settings: Settings,
    row: OfficeSession,
    node: Node | None,
    owner: User,
    tmp: Path,
    sha: str,
    size: int,
    event: CallbackEvent | None,
    reason: str,
) -> str:
    """Een herstelkopie bij de eigenaar (Keuze 16, stap 8), met de eigenaar als actor. Bij
    status 3 naast het origineel (als die map nog leeft), anders in zijn hoofdmap. Nooit
    eroverheen. office.recovered alleen naar wie de kopie nu kan zien."""
    copy = await place_copy(session, settings, row, node, owner, tmp, sha, size, reason, event)
    if isinstance(copy, str):
        return copy
    row = await sessions.find_by_key(session, row.key) or row
    final = event is None or event.kind in ("final", "failed")
    if event is not None:
        _mark(row, event)
        if final and row.state != "closed":
            sessions.close(row, "copy", "no_writer" if reason == "no_writer" else None)
    await session.commit()
    await sessions.publish_recovered(session, row, copy, node.id if node else None, reason)
    await session.commit()
    return "copy"


async def place_copy(
    session: AsyncSession,
    settings: Settings,
    row: OfficeSession,
    node: Node | None,
    owner: User,
    tmp: Path,
    sha: str,
    size: int,
    reason: str,
    event: CallbackEvent | None,
) -> Node | str:
    """De kopie neerzetten; de Node, of de uitkomst als het niet lukt."""
    key = row.key
    folder = None
    if reason == "status3" and node is not None and node.parent_id is not None:
        candidate = await session.get(Node, node.parent_id)
        if candidate is not None and not await is_trashed(session, candidate):
            folder = candidate
    if folder is None:
        folder = await session.get(Node, owner.root_id) if owner.root_id else None
    if folder is None:
        log.error("office: geen hoofdmap voor een herstelkopie")
        return "refused"
    when = _local(sessions.now(), settings.timezone)
    name = policy.recovery_name(node.name if node else row.name, when, owner.locale)
    try:
        return await place_file(
            session,
            BlobStore(settings.blob_dir),
            owner,
            folder,
            name,
            "rename",
            sha,
            size,
            tmp,
            actor=owner,
            tz=settings.timezone,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        await session.rollback()
        if detail.get("code") in ("no_room", "no_room_owner"):
            if event is None:
                raise
            return await refuse(session, key, event, "no_room")
        if detail.get("code") in ("not_found", "folder_not_found"):
            return "retry"
        raise


def _local(moment: datetime, tz: str) -> datetime:
    """De kloktijd in de tijdzone van de server (voor de naam van een herstelkopie)."""
    try:
        return moment.astimezone(ZoneInfo(tz))
    except (ZoneInfoNotFoundError, ValueError):
        return moment
