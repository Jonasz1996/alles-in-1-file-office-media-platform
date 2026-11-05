"""Afspeelsessies in het geheugen (Keuze 21, 34). M3.

De signaturen zijn frozen (§3.3); wat erbij kwam, is additief (zie docs/fase-5/m3.md).

Een sessie pint de inhoud: bij het maken onthoudt hij (sha, grootte) van de bron. Alles wat de
app voor de sessie opent, gebruikt die gepinde sha. Elk verzoek onder /api/v1/stream/ gaat langs
guard:

1. de sid opzoeken. Onbekend of verlopen (12 uur, of 30 minuten zonder verzoek): 410
   media_session_gone. Is de sessie door de app gesloten, dan de grafsteen: 30 minuten 403
   media_access_gone met de reden (stopped, revoked, blocked), of 410 met reden replaced;
2. de toegang van nu, met een cache van hooguit settings.media_access_cache (15 s): de
   gebruiker is actief, de login (family) is niet ingetrokken, het bestand is nog leesbaar en
   niet in de prullenbak; bij een publieke link leeft de link, ligt het bestand erin, en is de
   vingerafdruk van het wachtwoord gelijk. Daarna: wijst de node nog naar de gepinde sha? Zo
   niet, 410 met reden replaced;
3. mislukt 2, dan gaat de sessie dicht (met grafsteen).

create doet stap 2 ook meteen bij het maken: play keek de toegang na vóór het wachten op de
probe (tot 5 s), en een haak die toen kwam, zag de nieuwe sessie nog niet (review golf 1, S2).

Een lange respons (/file, en voor M9 audio.mp4) loopt door guarded_body: na elk stuk van
hooguit 4 MB, of na 5 s, kijkt die naar session.closed en doet hij stap 2 opnieuw (met de
cache). Intrekken (close_user, close_login), Stop en close_sha zetten de vlag closed meteen;
close_node laat de sessies op die node meteen opnieuw controleren (een nieuwe versie geeft
replaced, de prullenbak revoked).

Geen databaseverbinding blijft open: elke controle gebruikt een eigen korte sessie (regel 8),
en elk wachten op de database onder /api/v1/stream/ gaat via runtime.until_stopped.

Een sid staat nooit in een log (Keuze 35): het log noemt alleen de eigen uuid van de sessie.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import logging
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import access, errors
from app.config import Settings
from app.db import get_engine
from app.media import errors as media_errors
from app.media import runtime
from app.media.state import media_state
from app.media.types import ClientProfile, Grant, LoadtestFile, Plan, Probe, Rung, SessionView
from app.models import Node, Share, User
from app.security import family_revoked, password_print
from app.storage import BlobStore

log = logging.getLogger("platform.media")

SessionSource = Node | LoadtestFile

# De grenzen van Keuze 21 en 34 (seconden en aantallen)
MAX_AGE = 12 * 3600
IDLE = 30 * 60
TOMBSTONE = 30 * 60
PER_USER = 8
PER_LINK = 2
TOTAL = 64
TOMBSTONES_MAX = 4096  # zo groeit het geheugen niet met elke gesloten sessie
SID_MAX = 200  # langer is nooit een sid van ons (PlayIn.replace heeft dezelfde grens)
TOTAL_RETRY = 30  # Retry-After als alle 64 plekken bezet zijn

# guarded_body: na hoeveel bytes of seconden de controle opnieuw (Keuze 21)
CHECK_BYTES = 4 * 1024 * 1024
CHECK_SECONDS = 5.0

# Waarom de app een sessie sloot (de grafsteen): replaced geeft 410, de rest 403. GONE is
# intern: dicht zonder grafsteen (de speler sloot hem, verlopen, replace, de app stopt)
Closed = Literal["stopped", "revoked", "blocked", "replaced"]
GONE = "gone"

NOT_FOUND = {"message": "Niet gevonden", "code": "not_found"}

_NODE_NAME = re.compile(r"[^a-z0-9-]+")


def clock() -> float:
    """De klok van de sessies (monotoon, in seconden). Tests zetten een nepklok."""
    return time.monotonic()


def node_name(value: str | None) -> str:
    """De naam van de node voor in de sid: alleen [a-z0-9-], hooguit 63 tekens, anders "node".
    Zo laat _hide in main.py die naam staan en verbergt het de rest (review M0, S5);
    PLATFORM_NODE_NAME zelf valideert M0 niet."""
    name = _NODE_NAME.sub("-", (value or "").lower()).strip("-")[:63].strip("-")
    return name or "node"


@dataclass
class Session:
    """Eén sessie. sid staat alleen in het pad, nooit in een log (Keuze 35), dus ook niet in
    repr: een logregel of een fout die de sessie noemt, toont hem niet (review golf 1, S4).

    De velden na closed zijn van M3 (additief): wat play vastlegde, en de staat van de
    controle."""

    id: uuid.UUID
    sid: str = field(repr=False)
    user_id: uuid.UUID
    sha: str
    size: int
    plan: Plan
    node_id: uuid.UUID | None = None
    link_id: uuid.UUID | None = None
    family: uuid.UUID | None = None
    grant: Grant | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed: str | None = None  # de reden als de app hem sloot (de grafsteen)
    # --- M3 ---
    path: Path | None = None  # de gepinde inhoud: BlobStore.path(sha), of het loadtest-bestand
    settings: Settings | None = field(default=None, repr=False)
    probe: Probe | None = field(default=None, repr=False)
    mime: str = ""  # wat /file als Content-Type geeft (zonder codecs)
    start_ms: int = 0
    # De losse ondertitels die play vastlegde (Keuze 29): x<i> is plek i in deze lijst,
    # [{node, lang, forced, sdh, default, label}], al gefilterd op toegang
    subs: list[dict[str, Any]] = field(default_factory=list)
    link_print: str | None = None  # vingerafdruk van het wachtwoord van de link bij play
    loadtest: bool = False
    info: Any = None  # StreamInfo van streams.open (M4)
    born: float = field(default_factory=lambda: clock())
    seen: float = field(default_factory=lambda: clock())
    closed_at: float | None = None
    checked: float | None = None  # wanneer de toegang voor het laatst klopte
    stale: bool = False  # nu opnieuw controleren, ook binnen de cache (close_node)
    refine: bool = False  # revoked kan bij het volgende verzoek blocked worden (close_user)
    app: Any = field(default=None, repr=False)  # de app van het laatste verzoek
    _gen: int = field(default=0, repr=False)
    _task: asyncio.Future | None = field(default=None, repr=False)
    _task_gen: int = field(default=-1, repr=False)
    # --- M4 (additief): wat play koos, voor streams.open (het profiel en het audiospoor) ---
    profile: ClientProfile | None = field(default=None, repr=False)
    audio_index: int | None = None

    @property
    def public(self) -> bool:
        return self.link_id is not None

    def view(self) -> SessionView:
        rung = self.plan.video if isinstance(self.plan.video, Rung) else None
        return SessionView(
            id=self.id,
            user_id=self.user_id,
            node_id=self.node_id,
            link_id=self.link_id,
            mode=self.plan.mode,
            rung=rung,
            created_at=self.created_at,
            last_seen=self.last_seen,
            reasons=builtins.list(self.plan.reasons),
        )


class SessionTable:
    """Op MediaState.sessions (M3). Eén tabel per proces: de haken (files.notify, auth,
    previews) hebben geen app bij zich."""

    def __init__(self) -> None:
        self.by_sid: dict[str, Session] = {}

    def open(self) -> builtins.list[Session]:
        return [s for s in self.by_sid.values() if s.closed is None]

    def by_id(self, session_id: uuid.UUID) -> Session | None:
        return next((s for s in self.by_sid.values() if s.id == session_id), None)

    def prune(self, now: float) -> None:
        """Verlopen sessies (zonder grafsteen) en oude grafstenen weg."""
        for s in builtins.list(self.by_sid.values()):
            if s.closed is not None:
                if s.closed_at is None or now - s.closed_at >= TOMBSTONE:
                    self.by_sid.pop(s.sid, None)
            elif expired(s, now):
                _close(s, None)
        stones = sorted(
            (s for s in self.by_sid.values() if s.closed is not None),
            key=lambda s: s.closed_at or 0,
        )
        for s in stones[: max(0, len(stones) - TOMBSTONES_MAX)]:
            self.by_sid.pop(s.sid, None)


_table = SessionTable()
_tasks: set[asyncio.Task] = set()


def table(app=None) -> SessionTable:
    """De tabel van dit proces; met een app staat hij ook op MediaState.sessions."""
    if app is not None:
        state = media_state(app)
        if state.sessions is not _table:
            state.sessions = _table
    return _table


def reset() -> None:
    """Alleen voor tests: alle sessies en grafstenen weg, zonder iets te sluiten."""
    _table.by_sid.clear()


def expired(s: Session, now: float | None = None) -> bool:
    now = clock() if now is None else now
    return now - s.born >= MAX_AGE or now - s.seen >= IDLE


def _settings(s: Session) -> Settings:
    return s.settings if s.settings is not None else runtime.settings()


def _maker(s: Session) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(_settings(s)), expire_on_commit=False)


def _spawn(coro: Awaitable[Any]) -> None:
    """Op de achtergrond (sluiten en opnieuw controleren); zonder lopende loop niets."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        if asyncio.iscoroutine(coro):
            coro.close()
        return
    task = loop.create_task(coro)  # type: ignore[arg-type]
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _release(s: Session) -> None:
    from app.media import admission, streams

    if s.grant is not None:
        with contextlib.suppress(Exception):
            admission.release(s.grant)
        s.grant = None
    if s.plan.mode != "direct":
        try:
            await streams.close(s)
        except Exception:
            log.exception("de stroom van mediasessie %s sluiten", s.id)


def _close(s: Session, reason: str | None) -> bool:
    """Dicht. Met een reden blijft de sid 30 minuten staan als grafsteen; zonder reden is hij
    meteen onbekend (410). False als hij al dicht was."""
    if s.closed is not None:
        return False
    s.closed = reason or GONE
    s.closed_at = clock()
    if reason is None:
        _table.by_sid.pop(s.sid, None)
    _spawn(_release(s))
    log.info("mediasessie %s dicht (%s)", s.id, reason or "weg")
    return True


def _stone(s: Session) -> HTTPException:
    if s.closed in (None, GONE):
        return media_errors.session_gone()
    if s.closed == "replaced":
        return media_errors.session_gone("replaced")
    return media_errors.access_gone(s.closed)


# --- de controle --------------------------------------------------------------------------------


async def _verify(s: Session) -> str | None:
    """De toegang van nu (stap 2 van de guard): None als alles klopt, anders de reden om te
    sluiten (revoked, blocked of replaced). Eén korte databasesessie. De toegang gaat vóór de
    sha: wie geen toegang meer heeft, hoort niet dat er een nieuwe versie is."""
    async with _maker(s)() as db:
        if s.loadtest:
            return await _verify_loadtest(db, s)
        if s.link_id is not None:
            return await _verify_link(db, s)
        user = await db.get(User, s.user_id, populate_existing=True)
        if user is None or user.disabled_at is not None:
            return "blocked"
        if s.family is not None and await family_revoked(db, s.family):
            return "revoked"
        node = await db.get(Node, s.node_id, populate_existing=True) if s.node_id else None
        if node is None or node.kind != "file":
            return "revoked"
        if node.owner_id == user.id:
            if user.role == "guest" or await access.trashed(db, node.id):
                return "revoked"
        elif node.id not in await access.resolve(db, user.id, [node.id]):
            return "revoked"
        if node.blob_id != s.sha:
            return "replaced"
        return None


async def _verify_link(db: AsyncSession, s: Session) -> str | None:
    from app.routers import public

    share = await db.get(Share, s.link_id, populate_existing=True)
    if share is None or share.token is None or public.expired(share):
        return "revoked"
    if share.permission != "read" or password_print(share.password_hash) != s.link_print:
        return "revoked"
    owner = await db.get(User, share.owner_id, populate_existing=True)
    if owner is None or owner.disabled_at is not None:
        return "revoked"
    root = await db.get(Node, share.node_id, populate_existing=True)
    if root is None or s.node_id is None or await access.trashed(db, root.id):
        return "revoked"
    if s.node_id not in await public.inside(db, public.Link(share, root), [s.node_id]):
        return "revoked"
    node = await db.get(Node, s.node_id, populate_existing=True)
    if node is None or node.kind != "file":
        return "revoked"
    if node.blob_id != s.sha:
        return "replaced"
    return None


async def _verify_loadtest(db: AsyncSession, s: Session) -> str | None:
    user = await db.get(User, s.user_id, populate_existing=True)
    if user is None or user.disabled_at is not None:
        return "blocked"
    if user.role != "admin" or not _settings(s).media_loadtest:
        return "revoked"
    if s.family is not None and await family_revoked(db, s.family):
        return "revoked"
    return None


def _fresh(s: Session, now: float) -> bool:
    if s.stale or s.checked is None:
        return False
    return now - s.checked < max(0.0, _settings(s).media_access_cache)


async def check(s: Session) -> str | None:
    """Stap 2 van de guard met de cache: None als de toegang klopt, anders de reden waarom
    de sessie dicht moet. Gelijktijdige verzoeken delen één controle. Wordt de sessie tijdens
    de controle ongeldig verklaard (close_node, close_user: _gen), dan nog een keer; gaat hij
    tijdens de controle dicht, dan die reden."""
    for _ in range(3):
        if s.closed is not None:
            return s.closed
        if _fresh(s, clock()):
            return None
        gen = s._gen
        task = s._task
        if task is None or task.done() or s._task_gen != gen:
            task = s._task = asyncio.ensure_future(_verify(s))
            s._task_gen = gen
        failure = await asyncio.shield(task)
        if failure is not None:
            return failure
        if s.closed is not None:  # tijdens de controle dicht (uitloggen, blokkeren, Stop)
            return s.closed
        if s._gen == gen:
            s.checked = clock()
            s.stale = False
            return None
    return None


async def _refine(s: Session) -> None:
    """revoked na close_user: was de gebruiker geblokkeerd, dan blocked."""
    s.refine = False
    async with _maker(s)() as db:
        user = await db.get(User, s.user_id, populate_existing=True)
        if user is None or user.disabled_at is not None:
            s.closed = "blocked"


async def _wait(app, awaitable: Awaitable[Any]) -> Any:
    """Wachten onder /api/v1/stream/: altijd via runtime.until_stopped (F3)."""
    if app is None:
        return await awaitable
    return await runtime.until_stopped(app, awaitable)


async def _recheck(s: Session) -> None:
    """Na close_node: meteen controleren, ook als er geen verzoek komt (de producent stopt)."""
    try:
        failure = await _wait(s.app, check(s))
    except HTTPException:
        return  # de app stopt: close_all sluit alles
    except Exception:
        log.exception("mediasessie %s opnieuw controleren", s.id)
        return
    if failure is not None:
        _close(s, failure)


# --- de poort ----------------------------------------------------------------------------------


async def guard(request: Request, sid: str) -> Session:
    """De poort van elk verzoek onder /api/v1/stream/ (regel 6)."""
    if not runtime.enabled(request.app):
        raise media_errors.off()
    found = table(request.app)
    s = found.by_sid.get(sid) if len(sid) <= SID_MAX else None
    if s is None:
        raise media_errors.session_gone()
    now = clock()
    if s.closed is not None:
        if s.closed_at is None or now - s.closed_at >= TOMBSTONE:
            found.by_sid.pop(sid, None)
            raise media_errors.session_gone()
        if s.refine:
            await _wait(request.app, _refine(s))
        raise _stone(s)
    if expired(s, now):
        _close(s, None)
        raise media_errors.session_gone()
    s.app = request.app
    failure = await _wait(request.app, check(s))
    if failure is not None:
        _close(s, failure)
        if s.refine:  # tijdens de controle dicht door close_user: misschien blocked
            await _wait(request.app, _refine(s))
        raise _stone(s)
    s.seen = clock()
    s.last_seen = datetime.now(UTC)
    return s


async def guarded(request: Request, sid: str) -> Session:
    """De dependency van elke router onder /api/v1/stream/ (regel 6): als dependency van de
    router zelf loopt hij vóór alles van de route, ook vóór de controle van de andere
    parameters. Een route die de sessie nodig heeft, neemt hem als parameter (Guarded); FastAPI
    roept guard dan één keer aan."""
    return await guard(request, sid)


Guarded = Annotated[Session, Depends(guarded)]


class StreamCut(Exception):
    """guarded_body breekt een lange respons af (de sessie is dicht, de toegang is weg, of de
    app stopt). De server sluit dan de verbinding: de respons is korter dan Content-Length, en
    de browser weet dat hij niet compleet is. Het volgende verzoek krijgt de reden (403 of
    410)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _QuietCut(logging.Filter):
    """uvicorn logt een uitzondering uit de app als "Exception in ASGI application" met de
    traceback (ERROR). Voor StreamCut is dat geen fout maar het bedoelde einde: één regel op
    INFO, zonder traceback (en zonder sid: alleen de reden)."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, StreamCut):
            record.msg, record.args = "mediastroom afgebroken (%s)", (exc.reason,)
            record.levelno, record.levelname = logging.INFO, "INFO"
            record.exc_info, record.exc_text = None, None
        return True


logging.getLogger("uvicorn.error").addFilter(_QuietCut())


async def guarded_body(session: Session, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Per stuk (≤ 4 MB of 5 s) de sessie en de toegang nakijken (Keuze 21). Is de sessie dicht
    of de toegang weg, dan breekt de iterator af met StreamCut: de respons stopt en de
    verbinding gaat dicht. Wat de browser al had, speelt nog af."""
    sent = 0
    since = clock()
    try:
        async for chunk in chunks:
            if session.closed is not None:
                raise StreamCut(session.closed)
            if session.stale or sent >= CHECK_BYTES or clock() - since >= CHECK_SECONDS:
                try:
                    failure = await _wait(session.app, check(session))
                except HTTPException:
                    raise StreamCut("stopping") from None  # de app stopt (F3)
                if failure is not None:
                    _close(session, failure)
                    raise StreamCut(failure)
                sent = 0
                since = clock()
            if expired(session):
                _close(session, None)
                raise StreamCut(GONE)
            session.seen = clock()
            yield chunk
            sent += len(chunk)
    finally:
        aclose = getattr(chunks, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


# --- maken ---------------------------------------------------------------------------------------


def _new_sid(settings: Settings) -> str:
    return f"{node_name(settings.node_name)}.{secrets.token_urlsafe(24)}"


def _make_room(user_id: uuid.UUID, link_id: uuid.UUID | None) -> None:
    """De oudste gaat dicht boven 8 per gebruiker of 2 per link, met grafsteen stopped: de
    speler daar stopt en zegt waarom, en vraagt niet stil een nieuwe (anders duwen twee
    tabbladen elkaar eindeloos weg). Zijn daarna alle 64 plekken bezet: 503 media_busy."""
    if link_id is not None:
        mine = [s for s in _table.open() if s.link_id == link_id]
        limit = PER_LINK
    else:
        mine = [s for s in _table.open() if s.user_id == user_id and s.link_id is None]
        limit = PER_USER
    mine.sort(key=lambda s: s.born)
    for s in mine[: max(0, len(mine) - limit + 1)]:
        _close(s, "stopped")
    if len(_table.open()) >= TOTAL:
        raise media_errors.busy("cpu", TOTAL_RETRY)


def _replaceable(sid: str | None, user_id: uuid.UUID, link_id: uuid.UUID | None) -> Session | None:
    """De open sessie die replace sluit: dezelfde kijker (gebruiker en link), anders None."""
    old = _table.by_sid.get(sid) if sid and len(sid) <= SID_MAX else None
    if old is None or old.closed is not None:
        return None
    if old.user_id == user_id and old.link_id == link_id:
        return old
    return None


def _replace(sid: str, user_id: uuid.UUID, link_id: uuid.UUID | None) -> None:
    """replace: de oude sessie van dezelfde kijker gaat in dezelfde beurt dicht (410 daarna).
    Een sid van een ander doet niets."""
    old = _replaceable(sid, user_id, link_id)
    if old is not None:
        _close(old, None)


def replaced_grant(sid: str | None, user_id: uuid.UUID, link_id: uuid.UUID | None) -> Grant | None:
    """De grant van de sessie die replace zal sluiten, of None (additief, na-controle golf 2).
    Play telt hem niet mee bij de toelating (admission.replacing): een wissel van spoor of trede
    loopt zo niet tegen de grens, net als bij de sessies."""
    old = _replaceable(sid, user_id, link_id)
    return old.grant if old is not None else None


async def create(
    user: User,
    source: SessionSource,
    probe: Probe,
    plan: Plan,
    grant: Grant | None,
    *,
    family: uuid.UUID | None,
    link_id: uuid.UUID | None = None,
    replace: str | None = None,
    settings: Settings | None = None,
    link_print: str | None = None,
) -> Session:
    """Een nieuwe sessie met de gepinde (sha, grootte) van de bron. settings (additief): de
    instellingen van het verzoek (de database en de blob-opslag); zonder die van runtime.
    link_print (additief): de vingerafdruk van het wachtwoord van de link bij play; de
    controle bij het maken vergelijkt hem al."""
    settings = settings if settings is not None else runtime.settings()
    now = clock()
    _table.prune(now)
    if replace:
        _replace(replace, user.id, link_id)
    if isinstance(source, LoadtestFile):
        sha, size, path, node_id, loadtest = source.sha, source.size, source.path, None, True
    else:
        if source.kind != "file" or not source.blob_id:
            raise errors.fail(404, **NOT_FOUND)
        sha, size, node_id, loadtest = source.blob_id, source.size, source.id, False
        path = BlobStore(settings.blob_dir).path(sha)
    _make_room(user.id, link_id)
    s = Session(
        id=uuid.uuid4(),
        sid=_new_sid(settings),
        user_id=user.id,
        sha=sha,
        size=size,
        plan=plan,
        node_id=node_id,
        link_id=link_id,
        family=family,
        grant=grant,
        path=path,
        settings=settings,
        probe=probe,
        link_print=link_print,
        loadtest=loadtest,
        born=now,
        seen=now,
    )
    _table.by_sid[s.sid] = s
    log.info("mediasessie %s: %s", s.id, plan.mode)
    # De toegang nog een keer, nu de sessie in de tabel staat (review golf 1, S2 en C1). Wat
    # play nakeek, was vóór het wachten op de probe en de index (tot 5 s): een haak die toen
    # kwam (uitloggen, blokkeren, de prullenbak, een nieuwe versie), zag deze sessie nog niet,
    # en de cache had hem anders 15 s vertrouwd. Klopt de toegang niet meer, dan gaat de
    # sessie meteen dicht met de grafsteen: er start geen stroom, en het eerste verzoek krijgt
    # de reden (403, of 410 replaced). Eén korte databasesessie (regel 8).
    try:
        failure = await check(s)
    except BaseException:
        # Gooit create, dan geeft wie create aanriep de plaats terug (play doet admission.release),
        # zoals bij elke andere fout in create; de sessie die dichtgaat dus niet nog een keer
        # (met M4 telt elke release; na-controle golf 1)
        s.grant = None
        _close(s, None)
        raise
    if failure is not None:
        _close(s, failure)
    return s


async def create_audio(
    user: User,
    node: Node,
    probe: Probe,
    *,
    family: uuid.UUID | None,
    settings: Settings | None = None,
) -> Session:
    """Een sessie voor muziek (M9): mode audio, zonder plaats in de toelating. Dezelfde
    grenzen, guard en guarded_body als elke andere sessie."""
    plan = Plan(mode="audio", audio="copy")
    return await create(user, node, probe, plan, None, family=family, settings=settings)


def external(session: Session, track: str) -> dict[str, Any]:
    """De losse ondertitel x<i> uit de lijst die play vastlegde (Keuze 29, voor M5): 404 voor
    alles buiten die lijst. Nooit een node-id uit het adres."""
    match = re.fullmatch(r"x([0-9]{1,3})", track)
    if match is None or int(match.group(1)) >= len(session.subs):
        raise errors.fail(404, **NOT_FOUND)
    return session.subs[int(match.group(1))]


# --- sluiten -------------------------------------------------------------------------------------


def close(session: Session) -> bool:
    """DELETE /api/v1/stream/{sid}: de speler sluit zelf; daarna is de sid onbekend (410)."""
    return _close(session, None)


def close_user(user_id: uuid.UUID, keep_family: uuid.UUID | None = None) -> int:
    """Overal uitloggen, blokkeren, een nieuw wachtwoord of 2FA (na de commit): de sessies van
    die logins dicht (revoked; blocked als hij geblokkeerd bleek). Sessies van zijn publieke
    links blijven, maar het volgende verzoek controleert ze opnieuw."""
    count = 0
    for s in _table.open():
        if s.user_id != user_id:
            continue
        if s.link_id is not None:
            s.checked = None
            s._gen += 1  # een controle die al liep, zet de oude uitkomst niet terug (review C2)
            continue
        if keep_family is not None and s.family == keep_family:
            continue
        if _close(s, "revoked"):
            s.refine = True
            count += 1
    return count


def close_login(family: uuid.UUID) -> int:
    """Eén login ingetrokken (revoke_family, na de commit)."""
    return sum(_close(s, "revoked") for s in _table.open() if s.family == family)


def close_node(node_id: uuid.UUID) -> int:
    """Een nieuwe versie, de prullenbak of weg (files.notify, na de commit). De sessies op deze
    node worden meteen opnieuw gecontroleerd: replaced (410) of revoked (403). Is het een map,
    dan weet de app zonder database niet welke bestanden eronder vallen: dan vervalt de cache
    van elke sessie, en kijkt het volgende verzoek opnieuw. Geeft het aantal sessies op de
    node zelf."""
    hit = [s for s in _table.open() if s.node_id == node_id]
    if not hit:
        for s in _table.open():
            s.checked = None
            s._gen += 1  # een controle die al liep, zet de oude uitkomst niet terug (review C2)
        return 0
    for s in hit:
        s.stale = True
        s._gen += 1
        _spawn(_recheck(s))
    return len(hit)


def close_sha(sha: str) -> int:
    """Een blob gaat weg (on_discard, vóór het weggooien): de sessies op die inhoud dicht, met
    reden replaced."""
    return sum(_close(s, "replaced") for s in _table.open() if s.sha == sha)


def stop(session_id: uuid.UUID, reason: Literal["stopped", "revoked"]) -> bool:
    """Stop op /beheer: dicht met een grafsteen (403 met deze reden, 30 minuten)."""
    s = _table.by_id(session_id)
    return s is not None and _close(s, reason)


def close_all() -> int:
    """Bij het stoppen van de app (F3, runtime.begin_stop): elke sessie dicht, ook de
    producenten (streams.close), zonder grafsteen voor de speler (na de herstart is de sid
    onbekend: 410, en de speler vraagt stil een nieuwe). Geeft het aantal."""
    count = sum(_close(s, None) for s in _table.open())
    _table.by_sid.clear()
    return count


def list() -> builtins.list[SessionView]:
    """De open sessies voor /beheer, de oudste eerst; nooit de sid (Keuze 35)."""
    return [s.view() for s in sorted(_table.open(), key=lambda s: s.born)]
