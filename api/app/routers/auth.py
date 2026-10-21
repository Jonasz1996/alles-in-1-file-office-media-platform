import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors, totp
from app.config import Settings, get_settings
from app.db import Session
from app.models import Node, RecoveryCode, RefreshToken, User, Version
from app.security import (
    ACCOUNT_DISABLED,
    AdminUser,
    CurrentUser,
    LoggedInUser,
    Policy,
    SecurityConfig,
    TokenFamily,
    access_token,
    check_password,
    client_ip,
    family_revoked,
    hash_token,
    login_limiter,
    new_password_hash,
    new_refresh_token,
    security_config,
)
from app.useragent import device_label, shown_device

log = logging.getLogger("platform.auth")
router = APIRouter(prefix="/api/v1", tags=["login"])
REFRESH_COOKIE = "platform_refresh"
ISSUER = "Platform"  # naam in de authenticator-app; wordt de projectnaam zodra die er is
# Foute pogingen voor de tweede factor: de eerste 5 op rij mogen direct achter elkaar. Na
# de 5e is de gebruiker 1 minuut geblokkeerd, na elke volgende fout dubbel zo lang, tot
# hooguit een dag. Raden komt zo al snel neer op één poging per dag.
# De keerzijde is bewust gekozen: er is één teller per gebruiker en een blokkade geldt ook
# voor de goede code. Wie het wachtwoord kent (foute codes bij /auth/login), of alleen een
# geldige sessie heeft (een fout wachtwoord bij /auth/2fa/disable, een foute code bij
# /auth/2fa/recovery-codes), kan de tweede factor zo met één foute poging per dag
# geblokkeerd houden, ook voor inloggen. Dan helpt de beheerder (DELETE /users/{id}/2fa)
# of op de server: python -m app.cli reset-2fa <e-mailadres>.
FREE_ATTEMPTS = 5
MAX_LOCK = timedelta(hours=24)
# Tabbladen van één browser delen het refresh-cookie. Gaan ze tegelijk open (bijv. bij het
# herstellen van de vorige sessie), dan verversen ze met hetzelfde token en wint er één.
# Wie dat token binnen deze marge nog eens via het cookie aanbiedt, krijgt refresh_raced en
# probeert het daarna opnieuw met het nieuwe cookie (zie renew() in web/src/api.ts); de
# login blijft dan bestaan. Later, of via de body (een app houdt zijn eigen token bij), is
# het hergebruik en eindigt de hele login. De web-app probeert het tot net na deze marge,
# zodat ook een gestolen token dat eerder werd gebruikt de login alsnog laat intrekken.
REFRESH_RACE = timedelta(seconds=5)


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
    # Taal van de web-app voor dit account ("nl" of "en"); null = wat het apparaat kiest
    locale: str | None = None
    # Geblokkeerd door een beheerder
    disabled: bool = False


class MeChange(BaseModel):
    # null: geen voorkeur meer (dan kiest het apparaat); weglaten laat hem staan
    locale: Literal["nl", "en"] | None = None


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
    """Bestanden (ook in de prullenbak) plus hun eerdere versies, in één query."""
    files = (
        select(func.coalesce(func.sum(Node.size), 0))
        .where(Node.owner_id == user_id, Node.kind == "file")
        .scalar_subquery()
    )
    versions = (
        select(func.coalesce(func.sum(Version.size), 0))
        .join(Node, Node.id == Version.node_id)
        .where(Node.owner_id == user_id)
        .scalar_subquery()
    )
    total = await session.scalar(select(files + versions))
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
        locale=user.locale,
        disabled=user.disabled_at is not None,
    )


async def create_user(
    session: Session, email: str, name: str, password: str, role: str, quota: int
) -> User:
    # Eerst de hash (in een thread, ~100 ms): de tijd tussen de controle hieronder en het
    # aanmaken blijft zo kort.
    password_hash = await new_password_hash(password)
    if await session.scalar(select(User).where(func.lower(User.email) == email.lower())):
        raise errors.fail(409, "email_taken", "Er bestaat al een account met dit e-mailadres")
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
    verified: tuple[str, str | None] | None = None,
) -> Tokens:
    """verified: de wachtwoord-hash en het 2FA-geheim waarmee net is ingelogd. Zijn die
    intussen gewijzigd, dan komt er geen login bij (401)."""
    if verified is not None and not await credentials_unchanged(session, user.id, *verified):
        await session.rollback()
        raise errors.fail(401, "bad_credentials", "E-mailadres of wachtwoord klopt niet")
    refresh = new_refresh_token()
    family = family or uuid.uuid4()
    session.add(
        RefreshToken(
            user_id=user.id,
            family=family,
            token_hash=hash_token(refresh),
            device=device,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
            ip=client_ip(request)[:45],
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


async def credentials_unchanged(
    session: AsyncSession, user_id: uuid.UUID, password_hash: str, totp_secret: str | None
) -> bool:
    """Of het wachtwoord en de 2FA waarmee net is ingelogd nog gelden. Een UPDATE die niets
    verandert, zodat de gebruiker tot de commit vastligt (schrijfslot in SQLite, rijslot in
    PostgreSQL). Een nieuw wachtwoord of een andere 2FA (die de logins in dezelfde of een
    latere transactie intrekt) komt zo óf eerder, en dan klopt het hier niet meer, óf later,
    en dan trekt die ook de login in die hier wordt uitgegeven."""
    secret = User.totp_secret.is_(None) if totp_secret is None else User.totp_secret == totp_secret
    result = await session.execute(
        update(User)
        .where(User.id == user_id, User.password_hash == password_hash, secret)
        .values(password_hash=password_hash)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def lock_time(failures: int) -> timedelta:
    """Hoe lang de gebruiker na zoveel fouten op rij geblokkeerd is (vanaf de 5e)."""
    # De teller loopt door; zonder min() past 2**n minuten op den duur niet in een timedelta
    return min(timedelta(minutes=2 ** min(failures - FREE_ATTEMPTS, 11)), MAX_LOCK)


def locked_error(until: datetime | None, what: str = "codes") -> HTTPException:
    """429 totp_locked; params until (ISO-tijd, als bekend) en seconds (hoe lang nog)."""
    now = datetime.now(UTC)
    seconds = max(1, math.ceil((until - now).total_seconds())) if until else 1
    minutes = math.ceil(seconds / 60)
    wait = "1 minuut" if minutes == 1 else f"{minutes} minuten"
    return errors.fail(
        429,
        "totp_locked",
        f"Te veel foute {what}. Probeer het over {wait} opnieuw.",
        headers={"Retry-After": str(seconds)},
        until=(until or now + timedelta(seconds=seconds)).isoformat(),
        seconds=seconds,
    )


async def start_attempt(session: AsyncSession, user: User, what: str = "codes") -> int:
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
        raise locked_error(aware(until), what)
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
    session: AsyncSession,
    user: User,
    request: Request,
    ok: bool,
    failures: int,
    label: str = "Foute 2FA-poging",
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
        log.warning("%s van gebruiker %s vanaf %s (%d op rij)", label, user.id, ip, failures)
        if failures >= FREE_ATTEMPTS:
            log.error(
                "2FA van gebruiker %s geblokkeerd voor %d min na %d foute pogingen op rij "
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
    of uitzetten van 2FA of een nieuw wachtwoord moet wie al een sessie had dus opnieuw
    inloggen; ook zijn access-token werkt dan niet meer (zie security.family_revoked)."""
    query = update(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    )
    if keep is not None:
        query = query.where(RefreshToken.family != keep)
    await session.execute(
        query.values(revoked_at=datetime.now(UTC)).execution_options(synchronize_session=False)
    )


async def revoke_family(session: AsyncSession, family: uuid.UUID, now: datetime) -> None:
    """Eén login intrekken (uitloggen, of hergebruik van een token)."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family == family, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
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
        raise errors.fail(409, "setup_done", "Er is al een beheerder")
    user = await create_user(session, body.email, body.display_name, body.password, "admin", 0)
    device = device_label(request.headers.get("user-agent"))
    return await issue_tokens(session, settings, request, response, user, device)


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
        raise errors.fail(401, "bad_credentials", "E-mailadres of wachtwoord klopt niet")
    # Wat hier klopte, moet nog gelden als de tokens worden uitgegeven (zie issue_tokens):
    # een beheerder die intussen een nieuw wachtwoord instelde, logt deze login ook uit.
    verified = (user.password_hash, user.totp_secret)
    if user.totp_secret is not None:
        if not body.code:
            # Het wachtwoord klopt al, dus de blokkade melden verraadt niets; zo hoort de
            # gebruiker het vóór het intypen van een code. Telt niet als poging.
            if until := locked_until(user):
                raise locked_error(until)
            raise errors.fail(401, "totp_required", "Vul de code uit je authenticator-app in")
        if not await second_factor(session, user, request, body.code):
            raise errors.fail(401, "totp_invalid", "De code klopt niet")
    # Geblokkeerd: pas na het wachtwoord en de tweede factor, zodat je zonder die twee niet
    # kunt aftasten of een account geblokkeerd is
    if user.disabled_at is not None:
        raise errors.fail(403, "account_disabled", ACCOUNT_DISABLED)
    # Zonder naam van de app: een label als "Firefox op Windows" voor het sessie-overzicht
    device = body.device.strip() or device_label(request.headers.get("user-agent"))
    return await issue_tokens(session, settings, request, response, user, device, verified=verified)


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
        raise await not_logged_in(session, "Geen refresh-token")
    shared = not refresh_token  # het cookie, dat de tabbladen van een browser delen
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw))
    )
    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None or await family_revoked(session, row.family):
        raise await not_logged_in(session, "Ongeldig refresh-token")
    if row.used_at is not None:
        if shared and now - aware(row.used_at) <= REFRESH_RACE:
            raise refresh_raced()
        # Hergebruik: iemand anders heeft dit token mogelijk ook. Trek de hele login in.
        await revoke_family(session, row.family, now)
        await session.commit()
        raise await not_logged_in(session, "Refresh-token is al gebruikt; log opnieuw in")
    if row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC) < now:
        raise await not_logged_in(session, "Refresh-token is verlopen")
    # Voorwaardelijk: is de login intussen ingetrokken (bijv. omdat net 2FA aanging), dan
    # werkt het token niet meer, ook al leek het hierboven nog geldig. En is het intussen
    # door een ander verzoek gebruikt, dan krijgt alleen dat een nieuw token: zonder deze
    # voorwaarde kregen beide er een en liep de login in twee takken verder, waarvan je de
    # tweede nooit meer als hergebruik zou zien. Via het cookie is dat een ander tabblad
    # (refresh_raced, zie REFRESH_RACE); via de body is het hergebruik zoals hierboven.
    result = await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.id == row.id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.used_at.is_(None),
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        revoked = await session.scalar(
            select(RefreshToken.revoked_at).where(RefreshToken.id == row.id)
        )
        if shared and revoked is None:
            await session.rollback()
            raise refresh_raced()
        await revoke_family(session, row.family, now)
        await session.commit()
        raise await not_logged_in(session, "Ongeldig refresh-token")
    user = await session.get(User, row.user_id)
    if user is None:
        raise await not_logged_in(session, "Gebruiker bestaat niet meer")
    if user.disabled_at is not None:
        # Geblokkeerd: deze login is voorbij (ook voor een ander tabblad met hetzelfde cookie)
        await revoke_family(session, row.family, now)
        await session.commit()
        raise errors.fail(403, "account_disabled", ACCOUNT_DISABLED)
    return await issue_tokens(session, settings, request, response, user, row.device, row.family)


async def not_logged_in(session: AsyncSession, message: str) -> HTTPException:
    """401 van /auth/refresh: (niet meer) ingelogd. setup_required: er is nog geen enkel
    account, zodat de web-app meteen het formulier voor de eerste beheerder kan tonen."""
    count = await session.scalar(select(func.count()).select_from(User))
    return errors.fail(401, "not_logged_in", message, setup_required=not count)


def refresh_raced() -> HTTPException:
    return errors.fail(
        401,
        "refresh_raced",
        "Je sessie is net in een ander tabblad vernieuwd; probeer het opnieuw",
    )


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
            await revoke_family(session, row.family, datetime.now(UTC))
            await session.commit()
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")


@router.get("/auth/me")
async def me(user: LoggedInUser, session: Session, policy: Policy) -> UserOut:
    return await user_out(session, user, policy)


@router.patch("/auth/me", tags=["account"])
async def change_me(
    body: MeChange, user: LoggedInUser, session: Session, policy: Policy
) -> UserOut:
    """De taal van de web-app voor dit account ("nl", "en", of null voor geen voorkeur)."""
    if "locale" in body.model_fields_set:
        user.locale = body.locale
        await session.commit()
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
        raise errors.fail(409, "totp_already_on", "2FA staat al aan")
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
        raise errors.fail(409, "totp_already_on", "2FA staat al aan")
    if user.totp_pending is None:
        raise errors.fail(409, "totp_setup_first", "Begin eerst met /auth/2fa/setup")
    if not await second_factor(session, user, request, body.code, body.password, enabling=True):
        raise errors.fail(400, "bad_password_or_code", "Wachtwoord of code klopt niet")
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
        raise errors.fail(409, "totp_off", "2FA staat niet aan")
    if not await second_factor(session, user, request, body.code):
        raise errors.fail(400, "totp_invalid", "De code klopt niet")
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
        raise errors.fail(409, "totp_off", "2FA staat niet aan")
    if not await second_factor(session, user, request, body.code, body.password):
        raise errors.fail(400, "bad_password_or_code", "Wachtwoord of code klopt niet")
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
        raise errors.fail(404, "user_not_found", "Gebruiker niet gevonden")
    await reset_totp(session, user, family)


# --- Account: wachtwoord wijzigen en sessies per apparaat ---

MIN_PASSWORD = 10  # dezelfde regel als bij het aanmaken van een account


class SessionOut(BaseModel):
    id: uuid.UUID  # de login (refresh-family)
    device: str
    ip: str | None
    created_at: datetime  # eerste login van deze sessie
    last_active_at: datetime  # laatst ververst: het nieuwste token
    current: bool  # de sessie van dit verzoek


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)
    # Code uit de authenticator-app of een herstelcode, als 2FA aan staat
    code: str | None = Field(default=None, max_length=32)


class NewPassword(BaseModel):
    new_password: str = Field(max_length=200)


def check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise errors.fail(
            422,
            "password_too_short",
            f"Kies een wachtwoord van minstens {MIN_PASSWORD} tekens",
            min=MIN_PASSWORD,
        )


@router.get("/auth/sessions", tags=["account"])
async def list_sessions(
    user: CurrentUser, family: TokenFamily, session: Session
) -> list[SessionOut]:
    """Waar je nu ingelogd bent: één rij per login, de laatst actieve eerst."""
    now = datetime.now(UTC)
    revoked = select(RefreshToken.family).where(
        RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_not(None)
    )
    # Van elke login die nog werkt is er één token dat nog niet gebruikt is: het nieuwste
    live = (
        await session.execute(
            select(
                RefreshToken.family, RefreshToken.device, RefreshToken.ip, RefreshToken.created_at
            )
            .where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.used_at.is_(None),
                RefreshToken.expires_at > now,
                RefreshToken.family.not_in(revoked),
            )
            .order_by(RefreshToken.created_at)
        )
    ).all()
    newest = {row.family: row for row in live}
    if not newest:
        return []
    first = dict(
        (
            await session.execute(
                select(RefreshToken.family, func.min(RefreshToken.created_at))
                .where(RefreshToken.user_id == user.id, RefreshToken.family.in_(newest))
                .group_by(RefreshToken.family)
            )
        ).all()
    )
    result = [
        SessionOut(
            id=fam,
            device=shown_device(row.device),
            ip=row.ip,
            created_at=aware(first.get(fam, row.created_at)),
            last_active_at=aware(row.created_at),
            current=fam == family,
        )
        for fam, row in newest.items()
    ]
    return sorted(result, key=lambda s: s.last_active_at, reverse=True)


@router.delete("/auth/sessions/{session_id}", tags=["account"], status_code=204)
async def end_session(session_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    """Logt die sessie uit, ook meteen voor het access-token. Mag de eigen sessie zijn;
    de app logt dan zelf uit."""
    exists = await session.scalar(
        select(RefreshToken.id)
        .where(RefreshToken.user_id == user.id, RefreshToken.family == session_id)
        .limit(1)
    )
    if exists is None:
        raise errors.fail(404, "session_not_found", "Sessie niet gevonden")
    await revoke_family(session, session_id, datetime.now(UTC))
    await session.commit()


@router.post("/auth/sessions/revoke-others", tags=["account"], status_code=204)
async def end_other_sessions(user: CurrentUser, family: TokenFamily, session: Session) -> None:
    """Overal anders uitloggen; deze sessie blijft."""
    await revoke_other_logins(session, user.id, family)
    await session.commit()


@router.post("/auth/password", tags=["account"], status_code=204)
async def change_password(
    body: PasswordChange,
    request: Request,
    user: CurrentUser,
    family: TokenFamily,
    session: Session,
) -> None:
    """Nieuw wachtwoord, met het huidige en (als 2FA aan staat) een code. Een fout telt mee
    voor dezelfde blokkade als bij 2FA (zie FREE_ATTEMPTS): ook met alleen een sessie kun
    je het wachtwoord zo niet raden. Daarna zijn alle andere sessies uitgelogd."""
    check_new_password(body.new_password)
    if body.new_password == body.current_password:
        raise errors.fail(422, "password_unchanged", "Kies een ander wachtwoord dan je huidige")
    totp_on = user.totp_secret is not None
    if totp_on and not body.code:
        # Telt niet als poging: er is nog niets gecontroleerd
        raise errors.fail(400, "totp_required", "Vul ook de code uit je authenticator-app in")
    old_hash = user.password_hash
    failures = await start_attempt(session, user, "codes" if totp_on else "pogingen")
    ok = await check_password(old_hash, body.current_password)
    if ok and totp_on:
        ok = await use_code(session, user, body.code or "")
    wrong = "Fout wachtwoord of foute code" if totp_on else "Fout wachtwoord"
    await end_attempt(session, user, request, ok, failures, f"{wrong} bij wachtwoord wijzigen")
    if not ok:
        if totp_on:
            raise errors.fail(400, "bad_password_or_code", "Wachtwoord of code klopt niet")
        raise errors.fail(400, "wrong_current_password", "Je huidige wachtwoord klopt niet")
    await session.commit()  # geen databaseverbinding vasthouden terwijl argon2 rekent
    new_hash = await new_password_hash(body.new_password)
    # Alleen als het wachtwoord nog hetzelfde is als wat hierboven klopte: van twee
    # gelijktijdige wijzigingen (of een beheerder die het net instelde) wint er één.
    result = await session.execute(
        update(User)
        .where(User.id == user.id, User.password_hash == old_hash)
        .values(password_hash=new_hash)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise errors.fail(
            409,
            "password_changed_elsewhere",
            "Je wachtwoord is net ergens anders gewijzigd; log opnieuw in",
        )
    await revoke_other_logins(session, user.id, family)
    await session.commit()
    log.info("Gebruiker %s heeft het wachtwoord gewijzigd vanaf %s", user.id, client_ip(request))


@router.put("/users/{user_id}/password", tags=["beheer"], status_code=204)
async def admin_set_password(
    user_id: uuid.UUID,
    body: NewPassword,
    request: Request,
    admin: AdminUser,
    session: Session,
) -> None:
    """Voor wie het wachtwoord kwijt is: de beheerder stelt een nieuw in. Alle sessies van
    die gebruiker worden beëindigd. Je eigen wachtwoord wijzig je op je accountpagina, met
    het huidige wachtwoord en je 2FA."""
    if user_id == admin.id:
        raise errors.fail(
            422,
            "own_password_via_account",
            "Gebruik je accountpagina om je eigen wachtwoord te wijzigen",
        )
    check_new_password(body.new_password)
    if await session.get(User, user_id) is None:
        raise errors.fail(404, "user_not_found", "Gebruiker niet gevonden")
    admin_id = admin.id
    await session.commit()  # geen databaseverbinding vasthouden terwijl argon2 rekent
    password_hash = await new_password_hash(body.new_password)
    result = await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(password_hash=password_hash)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise errors.fail(404, "user_not_found", "Gebruiker niet gevonden")
    await revoke_other_logins(session, user_id, None)
    await session.commit()
    log.warning(
        "Beheerder %s heeft een nieuw wachtwoord ingesteld voor gebruiker %s (vanaf %s)",
        admin_id,
        user_id,
        client_ip(request),
    )
