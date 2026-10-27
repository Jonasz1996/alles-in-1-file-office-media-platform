"""De ronde (Keuze 20) en het tussentijds opslaan (Keuze 18).

start(app): een eigen taak, elke office_sweep_seconds. run_once(): één ronde.
checkpoint_all(wait): forcesave voor alle open sessies en hooguit wait seconden wachten op de
opslag; het aantal opgeslagen documenten. Gebruikt door het stoppen van de app (wait 5),
app.cli office-checkpoint en de knop op /beheer (O5, die met checkpoint_counts ook het aantal
verstuurde ziet).

Eén ronde, per sessie die niet dicht is, elke stap met een eigen korte transactie onder het
slot van het bestand, en nooit een slot vast tijdens een aanroep naar DS:
1. bestand of eigenaar definitief weg: dicht (gone) en drop voor iedereen;
2. pending ouder dan 5 minuten: dicht (lost);
3. wie vorige ronde eruit werd gezet (banned) en nog steeds verbonden is: forcesave en drop
   voor iedereen; de sessie sluit dan gewoon met status 2;
4. wie zijn recht kwijt is (of geblokkeerd is): uit members, in banned, de poortcache voor hem
   leeg, zijn tunnels op die key dicht, drop met hem, en office.dropped (alleen naar hem);
5. elke 2 minuten info per open key (en per pending ouder dan 1 minuut): error 1 of twee keer
   na elkaar niemand -> dicht (lost); saving ouder dan 6 minuten -> info: error 1 of niemand
   -> lost, wel iemand -> weer open;
6. tussentijds opslaan na checkpoint_minutes (userdata "timer"; error 4 is goed);
7. dichte rijen ouder dan 30 dagen weg.
De klok is sessions.now(): de tests zetten hem vooruit.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.models import Node, OfficeSession, User
from app.office import engine, gate, runtime, saving, sessions, tunnel
from app.office.state import OfficeConfig, office_state

log = logging.getLogger("platform.office")

PENDING_MINUTES = sessions.PENDING_MINUTES
PENDING_INFO_MINUTES = 1
INFO_MINUTES = 2
SAVING_MINUTES = 6
KEEP_DAYS = 30
EMPTY_ROUNDS = 2
POLL_SECONDS = 0.2

# In het geheugen (één proces): wanneer de laatste info per key, hoe vaak na elkaar niemand,
# en wanneer de laatste forcesave van de klok
_info_at: dict[str, datetime] = {}
_empty: dict[str, int] = {}
_timer_at: dict[str, datetime] = {}


def forget() -> None:
    """Het geheugen van de ronde leeg (tests, en na een herstart van DS)."""
    _info_at.clear()
    _empty.clear()
    _timer_at.clear()


async def start(app) -> None:
    """Start de ronde als taak (de lifespan annuleert hem bij het stoppen)."""
    state = office_state(app)
    task = asyncio.create_task(_loop(), name="office ronde")
    state.tasks.append(task)


async def _loop() -> None:
    while True:
        await asyncio.sleep(runtime.settings().office_sweep_seconds)
        try:
            await run_once()
        except Exception:  # noqa: BLE001 - de ronde blijft draaien
            log.exception("office: de ronde mislukte")


async def run_once() -> int:
    """Eén ronde; het aantal sessies dat bekeken is."""
    eng = engine.current()
    if eng is None:
        return 0
    async with sessions.maker()() as session:
        keys = (
            await session.scalars(
                select(OfficeSession.key)
                .where(OfficeSession.state != "closed")
                .order_by(OfficeSession.created_at)
            )
        ).all()
        cfg = await saving.office_config(session)
        await session.commit()
    live = set(keys)
    for gone in [k for k in (*_info_at, *_empty, *_timer_at) if k not in live]:
        _info_at.pop(gone, None)
        _empty.pop(gone, None)
        _timer_at.pop(gone, None)
    for key in keys:
        try:
            await _one(eng, key, cfg)
        except Exception:  # noqa: BLE001 - de andere sessies gaan door
            log.exception("office: de ronde voor één sessie mislukte")
    await _prune()
    return len(keys)


class _Row:
    """Wat de ronde van een rij nodig heeft, gelezen onder het slot (zonder ORM-object)."""

    def __init__(self, row: OfficeSession) -> None:
        self.key = row.key
        self.node_id = row.node_id
        self.state = row.state
        self.updated_at = sessions.utc(row.updated_at)
        self.created_at = sessions.utc(row.created_at)
        self.saved_at = sessions.utc(row.saved_at) if row.saved_at else None
        self.members = sessions.member_ids(row)
        self.banned = list(row.banned or [])
        self.connected = list(row.connected or [])


async def _read(key: str) -> _Row | None:
    async with sessions.maker()() as session:
        row = await sessions.find_by_key(session, key)
        return _Row(row) if row is not None and row.state != "closed" else None


async def _change(key: str, change) -> object:
    """change(session, row) onder het slot van het bestand, in één korte transactie."""
    async with sessions.maker()() as session:
        row = await sessions.find_by_key(session, key)
        if row is None:
            return None
        lock = sessions.office().node_lock(row.node_id) if row.node_id else asyncio.Lock()
        await session.commit()
        async with lock:
            row = await sessions.find_by_key(session, key)
            if row is None or row.state == "closed":
                return None
            result = await change(session, row)
            await session.commit()
            return result


async def _ds(call, what: str):
    """Een aanroep naar DS; een fout is geen reden om de ronde te stoppen (None)."""
    try:
        return await call
    except Exception:  # noqa: BLE001 - DS weg of een vreemd antwoord: de volgende ronde
        log.warning("office: %s mislukt", what)
        return None


def _close_tunnels(key: str, user_id: uuid.UUID) -> None:
    close_for = getattr(tunnel, "close_for", None)  # O1
    if close_for is not None:
        try:
            close_for(key, user_id)
        except Exception:  # noqa: BLE001
            log.exception("office: tunnels sluiten mislukt")


async def _one(eng, key: str, cfg: OfficeConfig) -> None:
    now = sessions.now()
    row = await _read(key)
    if row is None:
        return
    # 1. Bestand of eigenaar definitief weg: niets meer opslaan, iedereen eruit
    if not await _alive(row):

        async def gone(session, r):
            sessions.close(r, "gone")

        await _change(key, gone)
        await _ds(eng.drop(key, None), "drop na weg")
        return
    # 2. pending te oud: niemand opende hem
    if row.state == "pending" and now - row.updated_at >= timedelta(minutes=PENDING_MINUTES):

        async def lost(session, r):
            if r.state == "pending":
                sessions.close(r, "lost")

        await _change(key, lost)
        return
    # 3. Iemand die eruit is gezet en toch nog verbonden is: de hele sessie stopt
    settings = runtime.settings()
    in_tunnel = {sessions.ds_uid(settings, u) for u in sessions.office().tunnels.get(key, {})}
    still = [uid for uid in row.banned if uid in row.connected or uid in in_tunnel]
    if still:
        log.warning("office: iemand zonder recht nog in de editor; de sessie stopt")
        await _ds(eng.checkpoint(key, "stop"), "forcesave bij stoppen")
        await _ds(eng.drop(key, None), "drop bij stoppen")
        return
    # 4. Wie zijn recht kwijt is
    await _rights(eng, row)
    # 5. info
    row = await _read(key)
    if row is None:
        return
    await _info(eng, row, now)
    # 6. Tussentijds opslaan
    row = await _read(key)
    if row is not None:
        await _timer(eng, row, now, cfg)


async def _alive(row: _Row) -> bool:
    async with sessions.maker()() as session:
        node = await session.get(Node, row.node_id) if row.node_id else None
        if node is None or node.kind != "file":
            return False
        owner = await session.get(User, node.owner_id)
        return owner is not None


async def _rights(eng, row: _Row) -> None:
    """Stap 4: per gebruiker in members of connected: actief en nog schrijfrecht?"""
    if row.node_id is None:
        return
    async with sessions.maker()() as session:
        node = await session.get(Node, row.node_id)
        if node is None:
            return
        wanted = {uid: m for uid, m in row.members.items() if uid not in row.banned}
        for uid in row.connected:
            if uid not in row.members:
                log.info("office: een onbekende gebruiker in de editor")
        allowed = await sessions.writers(session, node, wanted.values())
        await session.commit()
    lost = {uid: m for uid, m in wanted.items() if m not in allowed}
    if not lost:
        return

    async def ban(session, r):
        r.members = {uid: m for uid, m in (r.members or {}).items() if uid not in lost}
        r.banned = list(dict.fromkeys([*(r.banned or []), *lost]))
        return r.node_id

    node_id = await _change(row.key, ban)
    if node_id is None:
        return
    for user_id in lost.values():
        gate.forget_user(user_id)
        _close_tunnels(row.key, user_id)
    await _ds(eng.drop(row.key, list(lost)), "drop zonder recht")
    sessions.publish_dropped(set(lost.values()), node_id, row.key, "access")


async def _info(eng, row: _Row, now: datetime) -> None:
    """Stap 5. Let op: elke info laat DS een callback sturen (status 1, of 4 op een dichte key)."""
    age = now - row.updated_at
    last = _info_at.get(row.key)
    due = last is None or now - last >= timedelta(minutes=INFO_MINUTES)
    wanted = {
        "open": due,
        "pending": due and age >= timedelta(minutes=PENDING_INFO_MINUTES),
        "saving": age >= timedelta(minutes=SAVING_MINUTES),
    }
    if not wanted.get(row.state):
        return
    _info_at[row.key] = now
    try:
        users = await eng.present(row.key)
    except Exception:  # noqa: BLE001 - DS weg: geen conclusie
        log.warning("office: info mislukt")
        return
    key = row.key

    if row.state == "pending":
        # Nog niemand opende hem: DS kent een pending key pas na de eerste verbinding. Alleen
        # als DS er wel iemand in ziet, is hij open (de status 1 kwam niet aan)
        if users:

            async def opened(session, r):
                if r.state == "pending":
                    r.state = "open"
                    r.connected = list(users)
                    r.updated_at = sessions.now()

            await _change(key, opened)
        return

    if users is None or (row.state == "saving" and not users):
        # DS kent de key niet meer (een herstart, of een forgotten file), of DS deed hem dicht
        # zonder dat de opslag nog komt: dicht, de volgende opener krijgt een nieuwe key
        await _change(key, _lost_if(row.state))
        _empty.pop(key, None)
        return
    if row.state == "saving":

        async def reopen(session, r):
            if r.state == "saving":
                r.state = "open"
                r.connected = list(users)
                r.updated_at = sessions.now()

        await _change(key, reopen)
        return
    if users:
        _empty.pop(key, None)

        async def present(session, r):
            if r.state == "open":
                r.connected = list(users)

        await _change(key, present)
        return
    _empty[key] = _empty.get(key, 0) + 1
    if _empty[key] >= EMPTY_ROUNDS:
        _empty.pop(key, None)
        await _change(key, _lost_if("open"))


def _lost_if(state: str):
    async def lost(session, r):
        if r.state == state:
            sessions.close(r, "lost")

    return lost


async def _timer(eng, row: _Row, now: datetime, cfg: OfficeConfig) -> None:
    """Stap 6 (Keuze 18): een open sessie met iemand erin, en de laatste opslag ouder dan
    checkpoint_minutes."""
    if row.state != "open" or not row.connected:
        return
    last = max(t for t in (row.saved_at, _timer_at.get(row.key), row.created_at) if t)
    if now - last < timedelta(minutes=cfg.checkpoint_minutes):
        return
    _timer_at[row.key] = now
    await _ds(eng.checkpoint(row.key, "timer"), "forcesave van de klok")


async def _prune() -> None:
    """Stap 7: dichte rijen ouder dan 30 dagen weg."""
    before = sessions.now() - timedelta(days=KEEP_DAYS)
    async with sessions.maker()() as session:
        await session.execute(
            delete(OfficeSession).where(
                OfficeSession.state == "closed", OfficeSession.closed_at < before
            )
        )
        await session.commit()


# --- alles tussentijds opslaan ---


async def checkpoint_counts(wait: float) -> tuple[int, int]:
    """(verstuurd, opgeslagen): forcesave voor elke open sessie, dan hooguit wait seconden
    wachten tot hun opslag binnen is (saved_at verder, of de sessie dicht)."""
    eng = engine.current()
    if eng is None:
        return 0, 0
    async with sessions.maker()() as session:
        rows = (
            await session.scalars(select(OfficeSession).where(OfficeSession.state == "open"))
        ).all()
        before = {row.key: row.saved_at for row in rows}
        await session.commit()
    sent: dict = {}
    for key, saved_at in before.items():
        answer = await _ds(eng.checkpoint(key, "stop"), "forcesave voor alles")
        if answer == "sent":
            sent[key] = saved_at
    saved: set[str] = set()
    rounds = max(1, int(wait / POLL_SECONDS))
    for _ in range(rounds):
        if len(saved) == len(sent):
            break
        await asyncio.sleep(POLL_SECONDS)
        async with sessions.maker()() as session:
            for key in set(sent) - saved:
                row = await sessions.find_by_key(session, key)
                if row is None or row.state == "closed" or row.saved_at != sent[key]:
                    saved.add(key)
            await session.commit()
    return len(sent), len(saved)


async def checkpoint_all(wait: float) -> int:
    """forcesave voor alle open sessies; het aantal dat binnen wait seconden is opgeslagen."""
    _sent, saved = await checkpoint_counts(wait)
    return saved
