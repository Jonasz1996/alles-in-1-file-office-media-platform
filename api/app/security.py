import asyncio
import hashlib
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated

import anyio
import anyio.to_thread
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import Session
from app.models import RefreshToken, Setting, User

_hasher = PasswordHasher()  # argon2id met de aanbevolen standaardparameters
_bearer = HTTPBearer(auto_error=False)
# argon2 draait in een thread (het kost ~100 ms CPU en zou de event loop anders zo lang
# stilzetten), maar hooguit 4 tegelijk: elke berekening gebruikt 64 MiB geheugen.
_argon2_slots = anyio.CapacityLimiter(4)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# Voor onbekende e-mailadressen: met dezelfde parameters als echte hashes, zodat een login
# even lang duurt en je aan de tijd niet ziet of een account bestaat.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


async def new_password_hash(password: str) -> str:
    return await anyio.to_thread.run_sync(hash_password, password, limiter=_argon2_slots)


async def check_password(password_hash: str | None, password: str) -> bool:
    """Zonder hash (geen account) wordt tegen een nep-hash gerekend en is het antwoord nee."""
    ok = await anyio.to_thread.run_sync(
        verify_password, password_hash or _DUMMY_HASH, password, limiter=_argon2_slots
    )
    return ok and password_hash is not None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def _encode(settings: Settings, claims: dict, minutes: int) -> str:
    now = datetime.now(UTC)
    payload = {**claims, "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, settings.secret(), algorithm="HS256")


def _decode(settings: Settings, token: str, typ: str) -> dict:
    try:
        claims = jwt.decode(token, settings.secret(), algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(401, "Ongeldig of verlopen token") from e
    if claims.get("typ") != typ:
        raise HTTPException(401, "Verkeerd soort token")
    return claims


def access_token(settings: Settings, user: User, family: uuid.UUID) -> str:
    # fam: de login (refresh-family) waar dit token bij hoort, zodat bijvoorbeeld het
    # aanzetten van 2FA alle andere logins kan intrekken en deze laat staan.
    return _encode(
        settings,
        {"sub": str(user.id), "role": user.role, "typ": "access", "fam": str(family)},
        settings.access_token_minutes,
    )


def download_token(
    settings: Settings, user_id: uuid.UUID, node_id: uuid.UUID, version: uuid.UUID | None = None
) -> str:
    """version: een eerdere inhoud van het bestand; zit in het token, zodat de link alleen
    voor die versie werkt."""
    claims = {"sub": str(user_id), "node": str(node_id), "typ": "download"}
    if version is not None:
        claims["ver"] = str(version)
    return _encode(settings, claims, settings.download_token_minutes)


def read_download_token(
    settings: Settings, token: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]:
    """(gebruiker, bestand, versie of None)"""
    claims = _decode(settings, token, "download")
    version = uuid.UUID(claims["ver"]) if claims.get("ver") else None
    return uuid.UUID(claims["sub"]), uuid.UUID(claims["node"]), version


SECURITY_KEY = "security"


class SecurityConfig(BaseModel):
    # Beslissing B4: 2FA vanaf de eerste login. Wie nog geen 2FA heeft, kan dan alleen
    # inloggen en 2FA aanzetten.
    require_2fa: bool = True


class SecurityPolicy:
    """Beveiligingsinstellingen uit /beheer (tabel setting). Ze staan in het geheugen, zodat
    de controle bij elk verzoek geen query kost; /admin/security werkt ze bij. Er draait één
    proces, dus er is één kopie."""

    def __init__(self, config: SecurityConfig | None = None):
        self.config = config  # None = nog niet uit de database gelezen
        self._lock = asyncio.Lock()

    async def get(self, session: AsyncSession) -> SecurityConfig:
        if self.config is None:
            async with self._lock:
                if self.config is None:
                    row = await session.get(Setting, SECURITY_KEY)
                    self.config = SecurityConfig.model_validate(row.value if row else {})
        return self.config

    async def save(self, session: AsyncSession, config: SecurityConfig) -> None:
        # Onder de lock, zodat bij twee gelijktijdige wijzigingen het geheugen en de
        # database dezelfde waarde houden.
        async with self._lock:
            row = await session.get(Setting, SECURITY_KEY)
            if row is None:
                session.add(Setting(key=SECURITY_KEY, value=config.model_dump()))
            else:
                row.value = config.model_dump()
            await session.commit()
            self.config = config


def security_policy(app: FastAPI) -> SecurityPolicy:
    policy = getattr(app.state, "security", None)
    if policy is None:
        policy = app.state.security = SecurityPolicy()
    return policy


async def security_config(request: Request, session: Session) -> SecurityConfig:
    return await security_policy(request.app).get(session)


Policy = Annotated[SecurityConfig, Depends(security_config)]


async def access_claims(
    settings: Annotated[Settings, Depends(get_settings)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    if creds is None:
        raise HTTPException(401, "Niet ingelogd")
    return _decode(settings, creds.credentials, "access")


async def family_revoked(session: AsyncSession, family: uuid.UUID) -> bool:
    """Een login (refresh-family) is voorbij zodra er één token van is ingetrokken: bij
    uitloggen, hergebruik van een token, of na het wijzigen van het wachtwoord of 2FA.
    Eén ingetrokken token is genoeg, zodat een token dat tegelijk met het intrekken werd
    uitgegeven (en daardoor zelf niet is ingetrokken) toch niet meer werkt."""
    revoked = await session.scalar(
        select(RefreshToken.id)
        .where(RefreshToken.family == family, RefreshToken.revoked_at.is_not(None))
        .limit(1)
    )
    return revoked is not None


async def logged_in_user(session: Session, claims: Annotated[dict, Depends(access_claims)]) -> User:
    """Ingelogd, ook als 2FA nog aangezet moet worden. Alleen voor /auth/me en het aanzetten
    van 2FA; de rest gebruikt CurrentUser."""
    user = await session.get(User, uuid.UUID(claims["sub"]))
    if user is None:
        raise HTTPException(401, "Gebruiker bestaat niet meer")
    # Uitgelogd (ook vanaf een ander apparaat): dan werkt ook het access-token meteen niet
    # meer, niet pas als het na een kwartier verloopt.
    family = claims.get("fam")
    if family and await family_revoked(session, uuid.UUID(family)):
        raise HTTPException(401, "Je bent op dit apparaat uitgelogd; log opnieuw in")
    return user


async def current_user(user: Annotated[User, Depends(logged_in_user)], policy: Policy) -> User:
    if policy.require_2fa and user.totp_secret is None:
        raise HTTPException(
            403, {"message": "Zet eerst 2FA aan voor je account.", "code": "totp_setup_required"}
        )
    return user


def token_family(claims: Annotated[dict, Depends(access_claims)]) -> uuid.UUID | None:
    """De login waar het access-token bij hoort (None bij tokens van vóór deze versie)."""
    family = claims.get("fam")
    return uuid.UUID(family) if family else None


async def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Alleen voor beheerders")
    return user


LoggedInUser = Annotated[User, Depends(logged_in_user)]
CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(admin_user)]
TokenFamily = Annotated[uuid.UUID | None, Depends(token_family)]


class RateLimiter:
    """Eenvoudige limiet per sleutel (bijv. IP) binnen een tijdvenster, in het geheugen."""

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        if len(self._hits) > 10_000:
            self.prune()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            raise HTTPException(429, "Te veel pogingen, probeer het straks opnieuw")
        hits.append(now)

    def reset(self) -> None:
        self._hits.clear()

    def prune(self) -> None:
        """Vergeet sleutels zonder recente pogingen, zodat het geheugen niet groeit."""
        now = time.monotonic()
        for key in [
            k for k, hits in self._hits.items() if not hits or now - hits[-1] > self.window
        ]:
            del self._hits[key]


login_limiter = RateLimiter(limit=10, window_seconds=300)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "onbekend"
