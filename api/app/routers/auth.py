import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import totp
from app.config import Settings, get_settings
from app.db import Session
from app.models import Node, RecoveryCode, RefreshToken, User
from app.security import (
    AdminUser,
    CurrentUser,
    LoggedInUser,
    Policy,
    SecurityConfig,
    TokenFamily,
    access_token,
    check_password,
    client_ip,
    hash_token,
    login_limiter,
    new_password_hash,
    new_refresh_token,
    security_config,
)

log = logging.getLogger("platform.auth")
router = APIRouter(prefix="/api/v1", tags=["login"])
REFRESH_COOKIE = "platform_refresh"
ISSUER = "Platform"  # naam in de authenticator-app; wordt de projectnaam zodra die er is
# Foute pogingen voor de tweede factor: de eerste 5 op rij mogen direct achter elkaar. Na
# de 5e is de gebruiker 1 minuut geblokkeerd, na elke volgende fout dubbel zo lang, tot
# hooguit een dag. Raden komt zo al snel neer op één poging per dag.
FREE_ATTEMPTS = 5
MAX_LOCK = timedelta(hours=24)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    quota_bytes: int
    used_bytes: int = 0
    root_id: uuid.UUID | None
    totp_enabled: bool = False
    # 2FA is verplicht en staat voor deze gebruiker nog niet aan: alleen 2FA aanzetten werkt
    totp_setup_required: bool = False
    # Na te veel foute codes: tot wanneer de tweede factor geblokkeerd is
    totp_locked_until: datetime | None = None


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


class CodeAndPassword(Code):
    password: str = Field(max_length=200)


class RecoveryCodes(BaseModel):
    recovery_codes: list[str]


async def used_bytes(session: Session, user_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(Node.size), 0)).where(
            Node.owner_id == user_id, Node.kind == "file"
        )
    )
    return int(total or 0)


def aware(value: datetime | None) -> datetime | None:
    """SQLite geeft tijden zonder tijdzone terug; ze staan altijd in UTC opgeslagen."""
    return value.replace(tzinfo=value.tzinfo or UTC) if value else None


def locked_until(user: User) -> datetime | None:
    until = aware(user.totp_locked_until)
    return until if until and until > datetime.now(UTC) else None


async def user_out(session: Session, user: User, policy: SecurityConfig) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        quota_bytes=user.quota_bytes,
        used_bytes=await used_bytes(session, user.id),
        root_id=user.root_id,
        totp_enabled=user.totp_secret is not None,
        totp_setup_required=policy.require_2fa and user.totp_secret is None,
        totp_locked_until=locked_until(user),
    )


async def create_user(
    session: Session, email: str, name: str, password: str, role: str, quota: int
) -> User:
    # Eerst de hash (in een thread, ~100 ms): de tijd tussen de controle hieronder en het
    # aanmaken blijft zo kort.
    password_hash = await new_password_hash(password)
    if await session.scalar(select(User).where(func.lower(User.email) == email.lower())):
        raise HTTPException(409, "Er bestaat al een account met dit e-mailadres")
    user = User(
        email=email.lower(),
        display_name=name,
        password_hash=password_hash,
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
    family = family or uuid.uuid4()
    session.add(
        RefreshToken(
            user_id=user.id,
            family=family,
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
        access_token=access_token(settings, user, family),
        refresh_token=refresh,
        expires_in=settings.access_token_minutes * 60,
        user=await user_out(session, user, await security_config(request, session)),
    )


def lock_time(failures: int) -> timedelta:
    """Hoe lang de gebruiker na zoveel fouten op rij geblokkeerd is (vanaf de 5e)."""
    # De teller loopt door; zonder min() past 2**n minuten op den duur niet in een timedelta
    return min(timedelta(minutes=2 ** min(failures - FREE_ATTEMPTS, 11)), MAX_LOCK)


def locked_error(until: datetime | None) -> HTTPException:
    seconds = max(1, math.ceil((until - datetime.now(UTC)).total_seconds())) if until else 1
    minutes = math.ceil(seconds / 60)
    wait = "1 minuut" if minutes == 1 else f"{minutes} minuten"
    return HTTPException(
        429,
        {
            "message": f"Te veel foute codes. Probeer het over {wait} opnieuw.",
            "code": "totp_locked",
        },
        headers={"Retry-After": str(seconds)},
    )


async def start_attempt(session: AsyncSession, user: User) -> int:
    """Legt de poging vast vóórdat er iets gecontroleerd wordt: hij telt als fout tot blijkt
    dat hij klopt. Zo kunnen ook gelijktijdige verzoeken samen niet vaker raden dan de
    blokkade toelaat. Geeft het aantal fouten op rij (deze meegeteld) terug. Zolang de
    gebruiker geblokkeerd is: 429, ook als de code wel klopt."""
    now = datetime.now(UTC)
    failures = await session.scalar(
        update(User)
        .where(
            User.id == user.id,
            or_(User.totp_locked_until.is_(None), User.totp_locked_until <= now),
        )
        .values(totp_failures=User.totp_failures + 1)
        .returning(User.totp_failures)
        .execution_options(synchronize_session=False)
    )
    if failures is None:
        until = await session.scalar(select(User.totp_locked_until).where(User.id == user.id))
        await session.commit()
        raise locked_error(aware(until))
    if failures >= FREE_ATTEMPTS:
        await session.execute(
            update(User)
            .where(User.id == user.id)
            .values(totp_locked_until=now + lock_time(failures))
            .execution_options(synchronize_session=False)
        )
    await session.commit()
    return failures


async def end_attempt(
    session: AsyncSession, user: User, request: Request, ok: bool, failures: int
) -> None:
    """Bij succes de teller op nul. Een fout telde al mee; die wordt alleen gelogd."""
    if ok:
        await session.execute(
            update(User)
            .where(User.id == user.id)
            .values(totp_failures=0, totp_locked_until=None)
            .execution_options(synchronize_session=False)
        )
    else:
        ip = client_ip(request)
        log.warning("Foute 2FA-poging van gebruiker %s vanaf %s (%d op rij)", user.id, ip, failures)
        if failures >= FREE_ATTEMPTS:
            log.error(
                "2FA van gebruiker %s %d minuten geblokkeerd na %d foute pogingen op rij "
                "(de laatste vanaf %s)",
                user.id,
                lock_time(failures) // timedelta(minutes=1),
                failures,
                ip,
            )
    await session.commit()
    # De UPDATE's gaan buiten het object om
    await session.refresh(user)


async def use_code(session: AsyncSession, user: User, code: str) -> bool:
    """Verbruikt een TOTP-code of herstelcode met een voorwaardelijke UPDATE: van
    gelijktijdige verzoeken met dezelfde code slaagt er precies één."""
    if user.totp_secret is None:
        return False
    step = totp.verify(user.totp_secret, code, user.totp_last_step)
    if step is not None:
        result = await session.execute(
            update(User)
            .where(
                User.id == user.id,
                User.totp_secret == user.totp_secret,
                User.totp_last_step < step,
            )
            .values(totp_last_step=step)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1
    result = await session.execute(
        update(RecoveryCode)
        .where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash == totp.hash_recovery_code(code),
            RecoveryCode.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def use_pending_code(session: AsyncSession, user: User, code: str) -> bool:
    """Tijdens het aanzetten: een code bij het nieuwe geheim zet 2FA aan, ook hier precies
    één keer (en niet als er intussen een ander geheim is aangevraagd)."""
    if user.totp_pending is None:
        return False
    step = totp.verify(user.totp_pending, code, 0)
    if step is None:
        return False
    result = await session.execute(
        update(User)
        .where(
            User.id == user.id,
            User.totp_secret.is_(None),
            User.totp_pending == user.totp_pending,
        )
        .values(totp_secret=User.totp_pending, totp_pending=None, totp_last_step=step)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def second_factor(
    session: AsyncSession,
    user: User,
    request: Request,
    code: str,
    password: str | None = None,
    enabling: bool = False,
) -> bool:
    """Eén poging voor de tweede factor: een code uit de app of een herstelcode, en waar
    gevraagd ook het wachtwoord. Een fout telt mee voor de blokkade (zie FREE_ATTEMPTS);
    wie geblokkeerd is krijgt 429 voordat er iets gecontroleerd wordt. Commit zelf."""
    failures = await start_attempt(session, user)
    ok = password is None or await check_password(user.password_hash, password)
    if ok:
        ok = await (use_pending_code if enabling else use_code)(session, user, code)
    await end_attempt(session, user, request, ok, failures)
    return ok


async def revoke_other_logins(
    session: AsyncSession, user_id: uuid.UUID, keep: uuid.UUID | None
) -> None:
    """Trekt alle logins (refresh-families) van de gebruiker in, behalve keep. Na het aan-
    of uitzetten van 2FA moet wie al een sessie had dus opnieuw inloggen."""
    query = update(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    )
    if keep is not None:
        query = query.where(RefreshToken.family != keep)
    await session.execute(
        query.values(revoked_at=datetime.now(UTC)).execution_options(synchronize_session=False)
    )


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
    # Ook zonder account argon2 draaien: aan de tijd zie je dan niet of het adres bestaat
    password_ok = await check_password(user.password_hash if user else None, body.password)
    if user is None or not password_ok:
        raise HTTPException(401, "E-mailadres of wachtwoord klopt niet")
    if user.totp_secret is not None:
        if not body.code:
            raise HTTPException(
                401,
                {"message": "Vul de code uit je authenticator-app in", "code": "totp_required"},
            )
        if not await second_factor(session, user, request, body.code):
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
    # Voorwaardelijk: is de login intussen ingetrokken (bijv. omdat net 2FA aanging), dan
    # werkt het token niet meer, ook al leek het hierboven nog geldig.
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.id == row.id, RefreshToken.revoked_at.is_(None))
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.commit()
        raise HTTPException(401, "Ongeldig refresh-token")
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
async def me(user: LoggedInUser, session: Session, policy: Policy) -> UserOut:
    return await user_out(session, user, policy)


@router.get("/users", tags=["beheer"])
async def list_users(_: AdminUser, session: Session, policy: Policy) -> list[UserOut]:
    users = (await session.scalars(select(User).order_by(User.created_at))).all()
    return [await user_out(session, u, policy) for u in users]


@router.post("/users", tags=["beheer"], status_code=201)
async def add_user(
    body: NewUser,
    _: AdminUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    policy: Policy,
) -> UserOut:
    quota = settings.default_quota_bytes if body.quota_bytes is None else body.quota_bytes
    user = await create_user(
        session, body.email, body.display_name, body.password, body.role, quota
    )
    await session.commit()
    return await user_out(session, user, policy)


@router.post("/auth/2fa/setup", tags=["2fa"])
async def totp_setup(user: LoggedInUser, session: Session) -> TotpSetup:
    """Stap 1: nieuw geheim voor de authenticator-app (als QR-code). 2FA staat pas aan
    na /auth/2fa/enable met een code die klopt."""
    if user.totp_secret is not None:
        raise HTTPException(409, "2FA staat al aan")
    user.totp_pending = totp.new_secret()
    await session.commit()
    uri = totp.provisioning_uri(user.totp_pending, user.email, ISSUER)
    return TotpSetup(secret=user.totp_pending, uri=uri, qr=totp.qr_data_uri(uri))


@router.post("/auth/2fa/enable", tags=["2fa"])
async def totp_enable(
    body: CodeAndPassword,
    request: Request,
    user: LoggedInUser,
    family: TokenFamily,
    session: Session,
) -> RecoveryCodes:
    """Stap 2: met het wachtwoord en een code uit de app 2FA aanzetten. Geeft eenmalig de
    herstelcodes. Andere logins van deze gebruiker worden ingetrokken."""
    if user.totp_secret is not None:
        raise HTTPException(409, "2FA staat al aan")
    if user.totp_pending is None:
        raise HTTPException(409, "Begin eerst met /auth/2fa/setup")
    if not await second_factor(session, user, request, body.code, body.password, enabling=True):
        raise HTTPException(400, "Wachtwoord of code klopt niet")
    codes = await new_recovery_codes(session, user)
    await revoke_other_logins(session, user.id, family)
    await session.commit()
    return RecoveryCodes(recovery_codes=codes)


@router.post("/auth/2fa/recovery-codes", tags=["2fa"])
async def totp_recovery_codes(
    body: Code, request: Request, user: CurrentUser, session: Session
) -> RecoveryCodes:
    """Nieuwe herstelcodes; de oude werken daarna niet meer."""
    if user.totp_secret is None:
        raise HTTPException(409, "2FA staat niet aan")
    if not await second_factor(session, user, request, body.code):
        raise HTTPException(400, "De code klopt niet")
    codes = await new_recovery_codes(session, user)
    await session.commit()
    return RecoveryCodes(recovery_codes=codes)


@router.post("/auth/2fa/disable", tags=["2fa"], status_code=204)
async def totp_disable(
    body: CodeAndPassword,
    request: Request,
    user: CurrentUser,
    family: TokenFamily,
    session: Session,
) -> None:
    """2FA uitzetten; vraagt het wachtwoord en een code. Een fout wachtwoord telt als foute
    poging, net als een foute code, en de melding zegt niet welke van de twee."""
    if user.totp_secret is None:
        raise HTTPException(409, "2FA staat niet aan")
    if not await second_factor(session, user, request, body.code, body.password):
        raise HTTPException(400, "Wachtwoord of code klopt niet")
    await reset_totp(session, user, family)


async def reset_totp(session: AsyncSession, user: User, keep: uuid.UUID | None) -> None:
    """2FA uit, herstelcodes weg, blokkade opgeheven, en alle logins behalve keep ingetrokken
    (keep=None: allemaal)."""
    await session.execute(
        update(User)
        .where(User.id == user.id)
        .values(
            totp_secret=None,
            totp_pending=None,
            totp_last_step=0,
            totp_failures=0,
            totp_locked_until=None,
        )
        .execution_options(synchronize_session=False)
    )
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    await revoke_other_logins(session, user.id, keep)
    await session.commit()
    await session.refresh(user)


@router.delete("/users/{user_id}/2fa", tags=["beheer"], status_code=204)
async def admin_reset_totp(
    user_id: uuid.UUID, _: AdminUser, family: TokenFamily, session: Session
) -> None:
    """Voor wie de telefoon en de herstelcodes kwijt is: de beheerder zet 2FA uit. Dat heft
    ook een blokkade op en trekt de logins van die gebruiker in."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Gebruiker niet gevonden")
    await reset_totp(session, user, family)
