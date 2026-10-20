import hashlib
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.db import Session
from app.models import User

_hasher = PasswordHasher()  # argon2id met de aanbevolen standaardparameters
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


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


def access_token(settings: Settings, user: User) -> str:
    return _encode(
        settings,
        {"sub": str(user.id), "role": user.role, "typ": "access"},
        settings.access_token_minutes,
    )


def download_token(settings: Settings, user_id: uuid.UUID, node_id: uuid.UUID) -> str:
    return _encode(
        settings,
        {"sub": str(user_id), "node": str(node_id), "typ": "download"},
        settings.download_token_minutes,
    )


def read_download_token(settings: Settings, token: str) -> tuple[uuid.UUID, uuid.UUID]:
    claims = _decode(settings, token, "download")
    return uuid.UUID(claims["sub"]), uuid.UUID(claims["node"])


async def current_user(
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if creds is None:
        raise HTTPException(401, "Niet ingelogd")
    claims = _decode(settings, creds.credentials, "access")
    user = await session.get(User, uuid.UUID(claims["sub"]))
    if user is None:
        raise HTTPException(401, "Gebruiker bestaat niet meer")
    return user


async def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Alleen voor beheerders")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(admin_user)]


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
# Per gebruiker, zodat 2FA-codes ook vanaf veel IP-adressen niet te raden zijn
totp_limiter = RateLimiter(limit=10, window_seconds=300)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "onbekend"
