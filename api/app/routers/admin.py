import re
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__, errors, jobs, usage
from app.certs import CertManager, HttpsConfig, TooSoon, save_config
from app.cluster import (
    ClusterStatus,
    Disk,
    NodeStatus,
    Notice,
    cluster_status,
    disk_notice,
    notice,
)
from app.config import Settings, get_settings
from app.db import Session
from app.models import Blob, Node, Upload, User, Version
from app.routers import trash
from app.security import AdminUser, Policy, SecurityConfig, security_policy
from app.usage import NodeStorage

router = APIRouter(prefix="/api/v1/admin", tags=["beheer"])

# Zo kort voor het verlopen van het certificaat wordt de status "degraded"
HTTPS_WARN_DAYS = 14
# Zo lang mag de oudste taak die aan de beurt is wachten voor er een melding komt
QUEUE_WARN = timedelta(minutes=15)


async def http_client():
    async with httpx.AsyncClient() as client:
        yield client


async def standalone_status(session: Session, settings: Settings) -> ClusterStatus:
    notices: list[Notice] = []
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001 - elke databasefout telt als storing
        db_ok = False
        notices.append(notice("db_unreachable", "De database is niet bereikbaar."))

    settings.blob_dir.mkdir(parents=True, exist_ok=True)
    probe = settings.blob_dir / ".schrijftest"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
        storage_ok = True
    except OSError:
        storage_ok = False
        notices.append(notice("storage_not_writable", "De opslagmap is niet schrijfbaar."))

    found = shutil.disk_usage(settings.data_dir)
    disk = Disk(total_bytes=found.total, used_bytes=found.used, free_bytes=found.free)
    if full := disk_notice(settings.node_name, disk):
        notices.append(full)

    node = NodeStatus(
        name=settings.node_name,
        ip="127.0.0.1",
        checks={"database": db_ok, "storage": storage_ok, "api": True},
        db_role="sqlite" if settings.db_url.startswith("sqlite") else "postgresql",
        disk=disk,
    )
    status = "down" if not (db_ok and storage_ok) else ("degraded" if notices else "ok")
    return ClusterStatus(
        status=status,
        nodes=[node],
        database=[],
        messages=[n.text for n in notices],
        notices=notices,
    )


class JobsSummary(BaseModel):
    queued: int
    running: int
    failed: int
    # Sinds wanneer de oudste taak die aan de beurt is, wacht
    oldest_queued_at: datetime | None
    # Draaien de workers in dit proces?
    runner_alive: bool


class HttpsSummary(BaseModel):
    # uit, bezig, actief of fout; null als het certificatenbeheer niet draait
    state: str | None
    expires_at: datetime | None
    days_left: int | None


class SystemInfo(BaseModel):
    version: str
    mode: str
    started_at: datetime | None
    uptime_s: int | None
    db_kind: Literal["sqlite", "postgresql"]
    db_bytes: int
    jobs: JobsSummary
    https: HttpsSummary


class Counts(ClusterStatus):
    users: int = 0
    files: int = 0
    stored_bytes: int = 0
    logical_bytes: int = 0
    system: SystemInfo | None = None


async def system_info(
    request: Request, session: AsyncSession, settings: Settings
) -> tuple[SystemInfo, list[Notice]]:
    """Het blok system van /admin/status, en de meldingen die daaruit volgen (taken die niet
    draaien of vastzitten, https)."""
    notices: list[Notice] = []
    now = datetime.now(UTC)
    started = getattr(request.app.state, "started_at", None)
    counts = await jobs.counts(session)
    oldest = await jobs.oldest_queued(session)
    alive = jobs.runner_alive()
    if started is not None and not alive:
        notices.append(notice("jobs_stopped", "De achtergrondtaken draaien niet."))
    if oldest is not None and now - oldest > QUEUE_WARN:
        minutes = int((now - oldest).total_seconds() // 60)
        notices.append(
            notice(
                "jobs_waiting",
                f"De oudste achtergrondtaak wacht al {minutes} minuten.",
                minutes=minutes,
            )
        )
    certs: CertManager | None = getattr(request.app.state, "certs", None)
    https = HttpsSummary(state=None, expires_at=None, days_left=None)
    if certs is not None:
        expires = certs.not_after
        days = int((expires - now).total_seconds() // 86400) if expires else None
        https = HttpsSummary(state=certs.state, expires_at=expires, days_left=days)
        # params: error is de code van de fout (zie HttpsStatus), plus zijn eigen params
        if certs.state == "fout" and certs.error:
            params = {**certs.error_params, "error": certs.error_code or "unexpected"}
            notices.append(Notice(code="https_error", params=params, text=f"HTTPS: {certs.error}"))
        if certs.listener_error:
            params = {**certs.listener_error_params, "error": certs.listener_error_code or ""}
            notices.append(
                Notice(code="https_listener", params=params, text=f"HTTPS: {certs.listener_error}")
            )
        if certs.config.domain and days is not None and days < HTTPS_WARN_DAYS:
            notices.append(
                notice(
                    "https_expiring",
                    f"Het certificaat verloopt over {max(days, 0)} dagen.",
                    days=max(days, 0),
                )
            )
    sqlite = settings.db_url.startswith("sqlite")
    info = SystemInfo(
        version=__version__,
        mode=settings.mode,
        started_at=started,
        uptime_s=int((now - started).total_seconds()) if started else None,
        db_kind="sqlite" if sqlite else "postgresql",
        db_bytes=await usage.database_bytes(session, settings),
        jobs=JobsSummary(
            queued=counts["queued"],
            running=counts["running"],
            failed=counts["failed"],
            oldest_queued_at=oldest,
            runner_alive=alive,
        ),
        https=https,
    )
    return info, notices


@router.get("/status")
async def admin_status(
    _: AdminUser,
    request: Request,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(http_client)],
) -> Counts:
    """Systeemstatus: onderdelen, schijfruimte, hoeveel er is opgeslagen, en het systeem
    (versie, uptime, database, wachtrij, https). notices zijn de meldingen met een code (de
    web-app vertaalt die); messages zijn dezelfde meldingen als Nederlandse tekst."""
    if settings.mode == "cluster":
        base = await cluster_status(client, settings.nodes())
    else:
        base = await standalone_status(session, settings)
    system, extra = await system_info(request, session, settings)
    if extra:
        base.notices = [*base.notices, *extra]
        base.messages = [*base.messages, *(n.text for n in extra)]
        if base.status == "ok":
            base.status = "degraded"
    users = await session.scalar(select(func.count()).select_from(User))
    files = await session.scalar(select(func.count()).select_from(Node).where(Node.kind == "file"))
    stored = await session.scalar(select(func.coalesce(func.sum(Blob.size), 0)))
    logical = await session.scalar(
        select(func.coalesce(func.sum(Node.size), 0)).where(Node.kind == "file")
    )
    return Counts(
        **base.model_dump(),
        users=users or 0,
        files=files or 0,
        stored_bytes=int(stored or 0),
        logical_bytes=int(logical or 0),
        system=system,
    )


# --- Opslag ---


class StorageTotals(BaseModel):
    # Wat alle bestanden samen groot zijn (ook in de prullenbak), zonder ontdubbelen
    logical_bytes: int
    # Wat er na ontdubbelen echt in de blobs staat
    stored_bytes: int
    versions_bytes: int
    trash_bytes: int
    previews_bytes: int
    # Lopende uploads op schijf, en hoeveel ruimte ze in quota reserveren
    uploads_bytes: int
    uploads_reserved_bytes: int
    db_bytes: int
    # Hoe vaak elke blob is opgeslagen (standalone: één keer)
    replicas: int


class StorageUser(BaseModel):
    id: uuid.UUID
    display_name: str
    quota_bytes: int
    files_bytes: int
    trash_bytes: int
    versions_bytes: int
    file_count: int


class StorageOverview(BaseModel):
    nodes: list[NodeStorage]
    totals: StorageTotals
    users: list[StorageUser]


@router.get("/storage")
async def admin_storage(
    _: AdminUser, session: Session, settings: Annotated[Settings, Depends(get_settings)]
) -> StorageOverview:
    """Schijfgebruik per node (uit de cache die het opruimwerk elke ~10 minuten ververst),
    totalen en het gebruik per gebruiker (vast aantal query's)."""
    await session.commit()  # de eerste meting kan even duren: geen verbinding vasthouden
    # Het cluster meet later per node; tot dan alleen de node waar deze API draait
    nodes = [await usage.local_storage(settings)]
    logical = await session.scalar(
        select(func.coalesce(func.sum(Node.size), 0)).where(Node.kind == "file")
    )
    stored = await session.scalar(select(func.coalesce(func.sum(Blob.size), 0)))
    versions = await session.scalar(select(func.coalesce(func.sum(Version.size), 0)))
    reserved = await session.scalar(
        select(func.coalesce(func.sum(Upload.size), 0)).where(Upload.node_id.is_(None))
    )
    people = (
        await session.execute(
            select(User.id, User.display_name, User.quota_bytes).order_by(User.display_name)
        )
    ).all()
    per_user = await usage.user_usage(session)
    users = []
    for user_id, name, quota in people:
        found = per_user.get(user_id, usage.UserUsage())
        users.append(
            StorageUser(
                id=user_id,
                display_name=name,
                quota_bytes=quota,
                files_bytes=found.files_bytes,
                trash_bytes=found.trash_bytes,
                versions_bytes=found.versions_bytes,
                file_count=found.file_count,
            )
        )
    users.sort(key=lambda u: u.files_bytes + u.trash_bytes + u.versions_bytes, reverse=True)
    totals = StorageTotals(
        logical_bytes=int(logical or 0),
        stored_bytes=int(stored or 0),
        versions_bytes=int(versions or 0),
        trash_bytes=sum(u.trash_bytes for u in per_user.values()),
        previews_bytes=sum(n.parts.previews for n in nodes),
        uploads_bytes=sum(n.parts.uploads for n in nodes),
        uploads_reserved_bytes=int(reserved or 0),
        db_bytes=max((n.parts.database for n in nodes), default=0),
        replicas=1 if settings.mode == "standalone" else len(settings.nodes()),
    )
    return StorageOverview(nodes=nodes, totals=totals, users=users)


# --- Prullenbak ---


class TrashSettings(BaseModel):
    # Na zoveel dagen gaat wat in een prullenbak ligt vanzelf definitief weg; 0 = nooit
    retention_days: int = Field(ge=0, le=trash.MAX_RETENTION_DAYS)


@router.get("/trash")
async def trash_settings(_: AdminUser, session: Session) -> TrashSettings:
    return TrashSettings(retention_days=await trash.retention_days(session))


@router.put("/trash")
async def trash_settings_put(body: TrashSettings, _: AdminUser, session: Session) -> TrashSettings:
    """De bewaartermijn van de prullenbak, voor iedereen. Het opruimwerk (elk uur) haalt weg
    wat langer dan zoveel dagen geleden is weggegooid."""
    await trash.save_retention(session, body.retention_days)
    await session.commit()
    return body


# --- Beveiliging ---


@router.get("/security")
async def security_get(_: AdminUser, policy: Policy) -> SecurityConfig:
    """Beveiligingsinstellingen, zoals of 2FA verplicht is."""
    return policy


@router.put("/security")
async def security_put(
    body: SecurityConfig, _: AdminUser, request: Request, session: Session
) -> SecurityConfig:
    """2FA verplicht (standaard) of per gebruiker vrij. Geldt meteen, voor elk verzoek."""
    await security_policy(request.app).save(session, body)
    return body


# Gewone hostnaam met minstens één punt; geen IP-adres (Let's Encrypt geeft daar geen
# certificaat voor), geen wildcard. Het topdomein mag ook punycode zijn (xn--p1ai).
DOMAIN = re.compile(
    r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"([a-z]{2,63}|xn--[a-z0-9-]{0,58}[a-z0-9])$"
)


def get_certs(request: Request) -> CertManager:
    certs = getattr(request.app.state, "certs", None)
    if certs is None:
        raise errors.fail(503, "certs_not_running", "Certificatenbeheer draait niet")
    return certs


Certs = Annotated[CertManager, Depends(get_certs)]


class HttpsIn(BaseModel):
    domain: str = ""
    email: EmailStr | None = None
    staging: bool = False

    @field_validator("domain")
    @classmethod
    def valid_domain(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if value and not DOMAIN.match(value):
            raise ValueError("Vul een domeinnaam in, zoals app.voorbeeld.nl (geen IP-adres)")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def empty_email(cls, value):
        return value or None


class HttpsStatus(BaseModel):
    domain: str
    email: str
    staging: bool
    state: str
    error: str | None
    # De fout als code met params, voor de web-app (acme_dns, acme_connection, ...)
    error_code: str | None = None
    error_params: dict[str, str] = {}
    active: bool
    serving: bool
    listener_error: str | None
    # listener_bind of listener_stopped, met port en reason/detail
    listener_error_code: str | None = None
    listener_error_params: dict[str, str | int] = {}
    cert_domain: str | None
    expires_at: datetime | None
    renew_at: datetime | None
    last_attempt: datetime | None
    next_check: datetime | None


@router.get("/https")
async def https_status(_: AdminUser, certs: Certs) -> HttpsStatus:
    """Domein, certificaat en de laatste fout bij het aanvragen."""
    return HttpsStatus(**certs.status())


@router.put("/https")
async def https_configure(
    body: HttpsIn, _: AdminUser, session: Session, certs: Certs
) -> HttpsStatus:
    """Domein instellen; bij een nieuwe instelling wordt het certificaat meteen aangevraagd.
    Leeg domein = alleen http."""
    config = HttpsConfig(domain=body.domain, email=body.email or "", staging=body.staging)
    await save_config(session, config)
    certs.configure(config)
    return HttpsStatus(**certs.status())


@router.post("/https/renew", status_code=202)
async def https_renew(_: AdminUser, certs: Certs) -> HttpsStatus:
    """Nu (opnieuw) proberen, bijvoorbeeld na het aanpassen van de router. Niet tijdens
    een lopende poging en hooguit eens per minuut (429, code too_soon)."""
    if not certs.config.domain:
        raise errors.fail(409, "no_domain", "Stel eerst een domein in")
    try:
        certs.retry_now()
    except TooSoon as e:
        raise errors.fail(
            429, "too_soon", str(e), headers={"Retry-After": str(e.retry_after)}
        ) from None
    return HttpsStatus(**certs.status())
