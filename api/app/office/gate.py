"""Het poort-cookie van /office/ (Keuze 7).

Het cookie heet "office": een JWT met het app-geheim (nooit het office-geheim, en DS ziet het
nooit: de proxy stuurt geen Cookie door), typ "office_gate", de gebruiker en zijn login
(family), 2 uur geldig. HttpOnly, SameSite=Strict, Path=/office/, Secure over https. issue()
zet het (POST …/session en POST /api/v1/office/gate).

check(headers, key=None) -> GateUser | None, bij elk verzoek naar DS:
- het cookie: handtekening met het app-geheim, exp, typ office_gate;
- de gebruiker bestaat en is niet geblokkeerd, zijn login is niet ingetrokken (uitgelogd), en
  eist het beleid 2FA, dan heeft hij 2FA;
- met een key: de sessie van die key bestaat en is niet dicht, de gebruiker is lid (members) en
  niet banned, het bestand bestaat, en hij heeft nu schrijfrecht (sessions.can_write: eigenaar
  die geen gast is, of een deling met write via access.resolve);
- zonder key (de editorpagina, themes.json): lid van minstens één sessie die niet dicht is,
  zo (niet banned, en met schrijfrecht van nu).

Een ja staat hooguit office_gate_cache_seconds (15 s) in het geheugen, met als sleutel de
sha-256 van het cookie plus de key, en nooit langer dan het cookie geldig is. Een nee wordt
niet bewaard. forget_user(user_id) vergeet alles van één gebruiker (de ronde, na banned).

still(user, key): dezelfde controle voor een open tunnel, op de gebruiker en login van toen
(niet op het cookie: dat vernieuwt de pagina elke 30 minuten, de tunnel blijft). Ook 15 s in
het geheugen.
"""

import hashlib
import time
import uuid
from dataclasses import dataclass

import jwt
from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app import security
from app.config import Settings
from app.models import Node, OfficeSession, User
from app.office import runtime, sessions
from app.security import _encode

GATE_COOKIE = "office"
GATE_TYPE = "office_gate"
GATE_MINUTES = 120
CACHE_LIMIT = 10_000

# De klok van de cache (de tests zetten hem vooruit)
clock = time.monotonic


@dataclass(frozen=True)
class GateUser:
    user_id: uuid.UUID
    family: uuid.UUID | None


@dataclass(frozen=True)
class _Entry:
    until: float
    user: GateUser


# (sha-256 van het cookie, key of "") -> wie, tot wanneer (clock)
_by_cookie: dict[tuple[str, str], _Entry] = {}
# (gebruiker, login, key of "") -> tot wanneer; voor still()
_by_user: dict[tuple[uuid.UUID, uuid.UUID | None, str], _Entry] = {}


def gate_token(settings: Settings, user: User, family: uuid.UUID | None) -> str:
    claims = {"sub": str(user.id), "typ": GATE_TYPE}
    if family is not None:
        claims["fam"] = str(family)
    return _encode(settings, claims, GATE_MINUTES)


def issue(
    response: Response, settings: Settings, user: User, family: uuid.UUID | None, https: bool
) -> None:
    response.set_cookie(
        GATE_COOKIE,
        gate_token(settings, user, family),
        max_age=GATE_MINUTES * 60,
        httponly=True,
        secure=https or settings.secure_cookies,
        samesite="strict",
        path="/office/",
    )


def cookies(headers: Headers) -> list[str]:
    """Elke waarde van het cookie office in de koppen (meestal één)."""
    found = []
    for raw in headers.getlist("cookie"):
        for part in raw.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name.strip() == GATE_COOKIE and value.strip():
                found.append(value.strip())
    return found


def read(settings: Settings, token: str) -> tuple[GateUser, float] | None:
    """(wie, exp) uit een poort-cookie, of None als het niet klopt."""
    try:
        claims = jwt.decode(
            token, settings.secret(), algorithms=["HS256"], options={"require": ["exp", "sub"]}
        )
    except jwt.PyJWTError:
        return None
    if not isinstance(claims, dict) or claims.get("typ") != GATE_TYPE:
        return None
    try:
        user_id = uuid.UUID(str(claims["sub"]))
        family = uuid.UUID(str(claims["fam"])) if claims.get("fam") else None
        exp = float(claims["exp"])
    except (TypeError, ValueError):
        return None
    return GateUser(user_id, family), exp


async def check(headers: Headers, key: str | None = None) -> GateUser | None:
    settings = runtime.settings()
    if not settings.office_configured:
        return None
    for token in cookies(headers):
        found = read(settings, token)
        if found is None:
            continue
        user, exp = found
        left = exp - time.time()
        if left <= 0:
            continue
        slot = (hashlib.sha256(token.encode()).hexdigest(), key or "")
        now = clock()
        hit = _by_cookie.get(slot)
        if hit is not None and hit.until > now:
            return hit.user
        if await allowed(settings, user, key):
            _remember(_by_cookie, slot, now + min(settings.office_gate_cache_seconds, left), user)
            return user
    return None


async def still(user: GateUser, key: str) -> bool:
    """Mag deze gebruiker (met deze login) nog in de sessie van key? Voor een open tunnel."""
    settings = runtime.settings()
    if not settings.office_configured:
        return False
    slot = (user.user_id, user.family, key)
    now = clock()
    hit = _by_user.get(slot)
    if hit is not None and hit.until > now:
        return True
    if await allowed(settings, user, key):
        _remember(_by_user, slot, now + settings.office_gate_cache_seconds, user)
        return True
    return False


def forget_user(user_id: uuid.UUID | str) -> None:
    """Alles van deze gebruiker uit de cache (ook als id-tekst, zoals de ronde hem heeft)."""
    try:
        wanted = uuid.UUID(str(user_id))
    except ValueError:
        return
    for cache in (_by_cookie, _by_user):
        for slot in [s for s, entry in cache.items() if entry.user.user_id == wanted]:
            cache.pop(slot, None)


def forget() -> None:
    """De hele cache leeg (tests)."""
    _by_cookie.clear()
    _by_user.clear()


def _remember(cache: dict, slot, until: float, user: GateUser) -> None:
    if len(cache) >= CACHE_LIMIT:
        now = clock()
        for old in [s for s, entry in cache.items() if entry.until <= now]:
            del cache[old]
        if len(cache) >= CACHE_LIMIT:
            cache.clear()
    cache[slot] = _Entry(until, user)


# --- de controle zelf (database) ---


async def allowed(settings: Settings, user: GateUser, key: str | None) -> bool:
    async with sessions.maker()() as session:
        try:
            return await _allowed(session, settings, user, key)
        finally:
            await session.commit()


async def _allowed(
    session: AsyncSession, settings: Settings, user: GateUser, key: str | None
) -> bool:
    row = await session.get(User, user.user_id)
    if row is None or row.disabled_at is not None:
        return False
    if user.family is not None and await security.family_revoked(session, user.family):
        return False
    if (await _policy(session)).require_2fa and row.totp_secret is None:
        return False
    uid = sessions.ds_uid(settings, user.user_id)
    if key is not None:
        office = await sessions.find_by_key(session, key)
        return office is not None and await _writes(session, office, uid, user.user_id)
    rows = (
        await session.scalars(select(OfficeSession).where(OfficeSession.state != "closed"))
    ).all()
    for office in rows:
        if uid in (office.members or {}) and await _writes(session, office, uid, user.user_id):
            return True
    return False


async def _writes(
    session: AsyncSession, office: OfficeSession, uid: str, user_id: uuid.UUID
) -> bool:
    """Lid, niet banned, sessie niet dicht, het bestand bestaat, en nu schrijfrecht."""
    if office.state == "closed" or office.node_id is None:
        return False
    if uid not in (office.members or {}) or uid in (office.banned or []):
        return False
    node = await session.get(Node, office.node_id)
    if node is None or node.kind != "file":
        return False
    return await sessions.can_write(session, user_id, node)


async def _policy(session: AsyncSession) -> security.SecurityConfig:
    app = runtime.app()
    policy = security.security_policy(app) if app is not None else security.SecurityPolicy()
    return await policy.get(session)
