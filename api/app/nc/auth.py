"""Inloggen voor WebDAV en de Nextcloud-API's: HTTP Basic met een app-wachtwoord.

Het gewone wachtwoord werkt hier niet: met verplichte 2FA zou dat een achterdeur zijn. Een
app-wachtwoord komt uit de inlogflow v2 (de client opent een pagina waar je inlogt mét
2FA-code) of uit /api/v1/account/app-passwords. De gebruikersnaam is het e-mailadres (of het
id); hij moet bij het app-wachtwoord horen.
"""

import base64
import binascii
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import func, select, update

from app import errors
from app.db import Session
from app.models import AppPassword, User
from app.security import RateLimiter, client_ip, hash_token

REALM = 'Basic realm="Platform", charset="UTF-8"'
# Mislukte pogingen per IP: een client met een oud wachtwoord probeert het steeds opnieuw,
# dus ruim, maar raden blijft onbegonnen werk (72 tekens willekeur)
dav_limiter = RateLimiter(limit=30, window_seconds=300)
# last_used_at niet bij elk verzoek schrijven
TOUCH_EVERY = timedelta(minutes=5)


def new_app_password() -> str:
    """72 tekens, in groepjes zoals Nextcloud ze toont; alleen letters en cijfers."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(72))
    return "-".join(raw[i : i + 6] for i in range(0, 72, 6))


def unauthorized() -> Exception:
    return errors.fail(
        401, "not_logged_in", "Log in met een app-wachtwoord", headers={"WWW-Authenticate": REALM}
    )


def basic_credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic" or not value:
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    login, sep, password = decoded.partition(":")
    return (login, password) if sep else None


def matches(user: User, login: str) -> bool:
    login = login.strip().lower()
    return login in (user.email.lower(), str(user.id), user.id.hex)


async def app_password_user(request: Request, session: Session) -> User:
    """De gebruiker achter Basic-auth met een app-wachtwoord, of 401 met WWW-Authenticate."""
    ip = client_ip(request)
    dav_limiter.refuse(ip)
    creds = basic_credentials(request)
    if creds is None:
        raise unauthorized()
    login, password = creds
    row = await session.scalar(
        select(AppPassword).where(AppPassword.token_hash == hash_token(password.strip()))
    )
    user = await session.get(User, row.user_id) if row is not None else None
    if row is None or user is None or not matches(user, login) or user.disabled_at is not None:
        dav_limiter.add(ip)
        raise unauthorized()
    now = datetime.now(UTC)
    last = row.last_used_at
    if last is None or (last if last.tzinfo else last.replace(tzinfo=UTC)) < now - TOUCH_EVERY:
        await session.execute(
            update(AppPassword).where(AppPassword.id == row.id).values(last_used_at=now)
        )
        await session.commit()
    request.state.app_password_id = row.id
    return user


DavUser = Annotated[User, Depends(app_password_user)]


async def user_by_login(session, login: str) -> User | None:
    login = login.strip()
    try:
        return await session.get(User, uuid.UUID(login))
    except ValueError:
        return await session.scalar(select(User).where(func.lower(User.email) == login.lower()))
