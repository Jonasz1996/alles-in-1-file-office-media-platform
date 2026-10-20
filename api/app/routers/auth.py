import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, update

from app.config import Settings, get_settings
from app.db import Session
from app.models import Node, RefreshToken, User
from app.security import (
    AdminUser,
    CurrentUser,
    access_token,
    client_ip,
    hash_password,
    hash_token,
    login_limiter,
    new_refresh_token,
    verify_password,
)

router = APIRouter(prefix="/api/v1", tags=["login"])
REFRESH_COOKIE = "platform_refresh"


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    quota_bytes: int
    used_bytes: int = 0
    root_id: uuid.UUID | None


class Tokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class Setup(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=10, max_length=200)


class NewUser(Setup):
    role: str = Field(default="user", pattern="^(admin|user|guest)$")
    quota_bytes: int | None = Field(default=None, ge=0)


class Login(BaseModel):
    email: str
    password: str
    device: str = Field(default="", max_length=200)


async def used_bytes(session: Session, user_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(Node.size), 0)).where(
            Node.owner_id == user_id, Node.kind == "file"
        )
    )
    return int(total or 0)


async def user_out(session: Session, user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        quota_bytes=user.quota_bytes,
        used_bytes=await used_bytes(session, user.id),
        root_id=user.root_id,
    )


async def create_user(
    session: Session, email: str, name: str, password: str, role: str, quota: int
) -> User:
    if await session.scalar(select(User).where(func.lower(User.email) == email.lower())):
        raise HTTPException(409, "Er bestaat al een account met dit e-mailadres")
    user = User(
        email=email.lower(),
        display_name=name,
        password_hash=hash_password(password),
        role=role,
        quota_bytes=quota,
    )
    session.add(user)
    await session.flush()
    root = Node(owner_id=user.id, name="", kind="dir")
    session.add(root)
    await session.flush()
    user.root_id = root.id
    return user


async def issue_tokens(
    session: Session,
    settings: Settings,
    response: Response,
    user: User,
    device: str,
    family: uuid.UUID | None = None,
) -> Tokens:
    refresh = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            family=family or uuid.uuid4(),
            token_hash=hash_token(refresh),
            device=device,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
        )
    )
    await session.commit()
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        path="/api/v1/auth",
    )
    return Tokens(
        access_token=access_token(settings, user),
        refresh_token=refresh,
        expires_in=settings.access_token_minutes * 60,
        user=await user_out(session, user),
    )


@router.get("/auth/status")
async def status(session: Session) -> dict[str, bool]:
    """Of er nog een eerste beheerder aangemaakt moet worden."""
    count = await session.scalar(select(func.count()).select_from(User))
    return {"setup_required": count == 0}


@router.post("/auth/setup", status_code=201)
async def setup(
    body: Setup,
    request: Request,
    response: Response,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Tokens:
    """Maakt de eerste gebruiker aan als beheerder. Werkt alleen zolang er nog niemand is."""
    login_limiter.check(client_ip(request))
    if await session.scalar(select(func.count()).select_from(User)):
        raise HTTPException(409, "Er is al een beheerder")
    user = await create_user(session, body.email, body.display_name, body.password, "admin", 0)
    return await issue_tokens(session, settings, response, user, "setup")


@router.post("/auth/login")
async def login(
    body: Login,
    request: Request,
    response: Response,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Tokens:
    login_limiter.check(client_ip(request))
    user = await session.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not verify_password(user.password_hash, body.password):
        raise HTTPException(401, "E-mailadres of wachtwoord klopt niet")
    device = body.device or request.headers.get("user-agent", "")[:200]
    return await issue_tokens(session, settings, response, user, device)


@router.post("/auth/refresh")
async def refresh(
    response: Response,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    refresh_token: Annotated[str | None, Body(embed=True)] = None,
    cookie: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> Tokens:
    """Ruilt een refresh-token in voor een nieuw paar. Elk token werkt één keer."""
    raw = refresh_token or cookie
    if not raw:
        raise HTTPException(401, "Geen refresh-token")
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw))
    )
    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None:
        raise HTTPException(401, "Ongeldig refresh-token")
    if row.used_at is not None:
        # Hergebruik: iemand anders heeft dit token mogelijk ook. Trek de hele login in.
        await session.execute(
            update(RefreshToken).where(RefreshToken.family == row.family).values(revoked_at=now)
        )
        await session.commit()
        raise HTTPException(401, "Refresh-token is al gebruikt; log opnieuw in")
    if row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC) < now:
        raise HTTPException(401, "Refresh-token is verlopen")
    row.used_at = now
    user = await session.get(User, row.user_id)
    if user is None:
        raise HTTPException(401, "Gebruiker bestaat niet meer")
    return await issue_tokens(session, settings, response, user, row.device, row.family)


@router.post("/auth/logout", status_code=204)
async def logout(
    response: Response,
    session: Session,
    refresh_token: Annotated[str | None, Body(embed=True)] = None,
    cookie: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> None:
    raw = refresh_token or cookie
    if raw:
        row = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw))
        )
        if row:
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.family == row.family)
                .values(revoked_at=datetime.now(UTC))
            )
            await session.commit()
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")


@router.get("/auth/me")
async def me(user: CurrentUser, session: Session) -> UserOut:
    return await user_out(session, user)


@router.get("/users", tags=["beheer"])
async def list_users(_: AdminUser, session: Session) -> list[UserOut]:
    users = (await session.scalars(select(User).order_by(User.created_at))).all()
    return [await user_out(session, u) for u in users]


@router.post("/users", tags=["beheer"], status_code=201)
async def add_user(
    body: NewUser,
    _: AdminUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserOut:
    quota = settings.default_quota_bytes if body.quota_bytes is None else body.quota_bytes
    user = await create_user(
        session, body.email, body.display_name, body.password, body.role, quota
    )
    await session.commit()
    return await user_out(session, user)
