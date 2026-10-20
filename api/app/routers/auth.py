import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select, update

from app import totp
from app.config import Settings, get_settings
from app.db import Session
from app.models import Node, RecoveryCode, RefreshToken, User
from app.security import (
    AdminUser,
    CurrentUser,
    access_token,
    client_ip,
    hash_password,
    hash_token,
    login_limiter,
    new_refresh_token,
    totp_limiter,
    verify_password,
)

router = APIRouter(prefix="/api/v1", tags=["login"])
REFRESH_COOKIE = "platform_refresh"
ISSUER = "Platform"  # naam in de authenticator-app; wordt de projectnaam zodra die er is


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    quota_bytes: int
    used_bytes: int = 0
    root_id: uuid.UUID | None
    totp_enabled: bool = False


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
    # Code uit de authenticator-app of een herstelcode, als 2FA aan staat
    code: str | None = Field(default=None, max_length=32)
    device: str = Field(default="", max_length=200)


class TotpSetup(BaseModel):
    secret: str
    uri: str
    qr: str  # data-URI met de QR-code als SVG


class Code(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class DisableTotp(Code):
    password: str


class RecoveryCodes(BaseModel):
    recovery_codes: list[str]


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
        totp_enabled=user.totp_secret is not None,
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
    request: Request,
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
        secure=settings.secure_cookies or request.url.scheme == "https",
        samesite="strict",
        path="/api/v1/auth",
    )
    return Tokens(
        access_token=access_token(settings, user),
        refresh_token=refresh,
        expires_in=settings.access_token_minutes * 60,
        user=await user_out(session, user),
    )


async def second_factor(session: Session, user: User, code: str) -> bool:
    """Controleert een TOTP-code of herstelcode. Hooguit 10 pogingen per 5 minuten per
    gebruiker, ook vanaf verschillende IP-adressen. Commit gebeurt door de aanroeper."""
    totp_limiter.check(str(user.id))
    if user.totp_secret is None:
        return False
    step = totp.verify(user.totp_secret, code, user.totp_last_step)
    if step is not None:
        user.totp_last_step = step
        return True
    row = await session.scalar(
        select(RecoveryCode).where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash == totp.hash_recovery_code(code),
            RecoveryCode.used_at.is_(None),
        )
    )
    if row is None:
        return False
    row.used_at = datetime.now(UTC)
    return True


async def new_recovery_codes(session: Session, user: User) -> list[str]:
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = totp.new_recovery_codes()
    session.add_all(
        RecoveryCode(user_id=user.id, code_hash=totp.hash_recovery_code(c)) for c in codes
    )
    return codes


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
    return await issue_tokens(session, settings, request, response, user, "setup")


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
    if user.totp_secret is not None:
        if not body.code:
            raise HTTPException(
                401,
                {"message": "Vul de code uit je authenticator-app in", "code": "totp_required"},
            )
        if not await second_factor(session, user, body.code):
            raise HTTPException(401, {"message": "De code klopt niet", "code": "totp_invalid"})
    device = body.device or request.headers.get("user-agent", "")[:200]
    return await issue_tokens(session, settings, request, response, user, device)


@router.post("/auth/refresh")
async def refresh(
    request: Request,
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
    return await issue_tokens(session, settings, request, response, user, row.device, row.family)


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


@router.post("/auth/2fa/setup", tags=["2fa"])
async def totp_setup(user: CurrentUser, session: Session) -> TotpSetup:
    """Stap 1: nieuw geheim voor de authenticator-app (als QR-code). 2FA staat pas aan
    na /auth/2fa/enable met een code die klopt."""
    if user.totp_secret is not None:
        raise HTTPException(409, "2FA staat al aan")
    user.totp_pending = totp.new_secret()
    await session.commit()
    uri = totp.provisioning_uri(user.totp_pending, user.email, ISSUER)
    return TotpSetup(secret=user.totp_pending, uri=uri, qr=totp.qr_data_uri(uri))


@router.post("/auth/2fa/enable", tags=["2fa"])
async def totp_enable(body: Code, user: CurrentUser, session: Session) -> RecoveryCodes:
    """Stap 2: met een code uit de app 2FA aanzetten. Geeft eenmalig de herstelcodes."""
    if user.totp_secret is not None:
        raise HTTPException(409, "2FA staat al aan")
    if user.totp_pending is None:
        raise HTTPException(409, "Begin eerst met /auth/2fa/setup")
    totp_limiter.check(str(user.id))
    step = totp.verify(user.totp_pending, body.code, 0)
    if step is None:
        raise HTTPException(400, "De code klopt niet; controleer de tijd op je telefoon")
    user.totp_secret, user.totp_pending, user.totp_last_step = user.totp_pending, None, step
    codes = await new_recovery_codes(session, user)
    await session.commit()
    return RecoveryCodes(recovery_codes=codes)


@router.post("/auth/2fa/recovery-codes", tags=["2fa"])
async def totp_recovery_codes(body: Code, user: CurrentUser, session: Session) -> RecoveryCodes:
    """Nieuwe herstelcodes; de oude werken daarna niet meer."""
    if user.totp_secret is None:
        raise HTTPException(409, "2FA staat niet aan")
    if not await second_factor(session, user, body.code):
        raise HTTPException(400, "De code klopt niet")
    codes = await new_recovery_codes(session, user)
    await session.commit()
    return RecoveryCodes(recovery_codes=codes)


@router.post("/auth/2fa/disable", tags=["2fa"], status_code=204)
async def totp_disable(body: DisableTotp, user: CurrentUser, session: Session) -> None:
    """2FA uitzetten; vraagt het wachtwoord en een code."""
    if user.totp_secret is None:
        raise HTTPException(409, "2FA staat niet aan")
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(400, "Wachtwoord klopt niet")
    if not await second_factor(session, user, body.code):
        raise HTTPException(400, "De code klopt niet")
    await reset_totp(session, user)


async def reset_totp(session: Session, user: User) -> None:
    user.totp_secret = user.totp_pending = None
    user.totp_last_step = 0
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    await session.commit()


@router.delete("/users/{user_id}/2fa", tags=["beheer"], status_code=204)
async def admin_reset_totp(user_id: uuid.UUID, _: AdminUser, session: Session) -> None:
    """Voor wie de telefoon en de herstelcodes kwijt is: de beheerder zet 2FA uit."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Gebruiker niet gevonden")
    await reset_totp(session, user)
