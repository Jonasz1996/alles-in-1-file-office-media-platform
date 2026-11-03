"""De achtergrondtaken op /beheer: overzicht per soort, wat nu loopt, de mislukte (in
pagina's), opnieuw proberen en weggooien. En hoe ver de foto's zijn (photos: de statusregel)
met "alles nalopen" (previews.recheck)."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app import errors, jobs, previews
from app.db import Session
from app.models import Job
from app.security import AdminUser

router = APIRouter(prefix="/api/v1", tags=["beheer"])

PAGE = 100


class FailedJob(BaseModel):
    id: uuid.UUID
    type: str
    key: str | None
    attempts: int
    error: str | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class TypeCount(BaseModel):
    type: str
    status: str
    count: int


class RunningJob(BaseModel):
    id: uuid.UUID
    type: str
    key: str | None
    attempts: int
    started_at: datetime | None

    model_config = {"from_attributes": True}


class PhotoIndex(BaseModel):
    """Foto's en video's naar de toestand van hun preview (previews.photo_counts)."""

    total: int = 0
    ready: int = 0
    # Alleen de thumbnail; de grote maat komt nog (de tweede ronde)
    partial: int = 0
    waiting: int = 0
    failed: int = 0
    # Geen preview mogelijk (onleesbaar, of een soort zonder)
    none: int = 0
    # Een video die wacht tot de mediaworker ffmpeg heeft
    later: int = 0


class JobsOverview(BaseModel):
    counts: dict[str, int]
    photos: PhotoIndex = PhotoIndex()
    by_type: list[TypeCount] = []
    running: list[RunningJob] = []
    # Sinds wanneer de oudste taak die aan de beurt is, wacht (null: niets wacht)
    oldest_queued_at: datetime | None = None
    failed: list[FailedJob]
    # Voor de volgende pagina mislukte taken: ?before=<dit>; null als dit de laatste is
    next_before: str | None = None


class Retried(BaseModel):
    count: int


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=value.tzinfo or UTC) if value else None


def cursor(job: Job) -> str:
    finished = _aware(job.finished_at) or datetime.fromtimestamp(0, UTC)
    return f"{finished.isoformat()}~{job.id}"


def parse_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    at, sep, job_id = value.rpartition("~")
    try:
        if not sep:
            raise ValueError
        return datetime.fromisoformat(at), uuid.UUID(job_id)
    except ValueError:
        raise errors.fail(422, "invalid_input", "Ongeldige invoer", field="query.before") from None


@router.get("/admin/jobs", tags=["beheer"])
async def admin_jobs(_: AdminUser, session: Session, before: str | None = None) -> JobsOverview:
    """Hoeveel taken er klaarstaan, lopen, klaar en mislukt zijn (ook per soort), wat nu loopt,
    en de mislukte, de laatst mislukte eerst, per 100 (before: next_before van de vorige)."""
    page = parse_cursor(before) if before else None
    failed = await jobs.failed(session, PAGE, page)
    return JobsOverview(
        counts=await jobs.counts(session),
        photos=PhotoIndex(**await previews.photo_counts(session)),
        by_type=[
            TypeCount(type=t, status=s, count=c) for t, s, c in await jobs.counts_by_type(session)
        ],
        running=[RunningJob.model_validate(j) for j in await jobs.running(session)],
        oldest_queued_at=await jobs.oldest_queued(session),
        failed=[FailedJob.model_validate(j) for j in failed],
        next_before=cursor(failed[-1]) if len(failed) == PAGE else None,
    )


@router.post("/admin/jobs/retry-failed", tags=["beheer"])
async def retry_failed(_: AdminUser, session: Session) -> Retried:
    """Alle mislukte taken opnieuw, elk weer met alle pogingen."""
    count = await jobs.retry_failed(session)
    await session.commit()
    return Retried(count=count)


class Recheck(BaseModel):
    # False: alles nalopen liep al (of stond al klaar)
    started: bool


@router.post("/admin/jobs/recheck-previews", tags=["beheer"])
async def recheck_previews(_: AdminUser, session: Session) -> Recheck:
    """Alles nalopen: elke foto, video en ander bestand zonder (complete) preview opnieuw in de
    rij, ook een mislukte, en een preview waarvan het bestand op schijf ontbreekt. Daarna de
    opnamedatums (photometa) en verouderde previews."""
    started = await previews.queue_recheck(session)
    await session.commit()
    return Recheck(started=started)


async def _not_failed(session: Session, job_id: uuid.UUID) -> Exception:
    exists = await session.get(Job, job_id)
    await session.commit()
    if exists is None:
        return errors.fail(404, "job_not_found", "Taak niet gevonden")
    return errors.fail(409, "not_failed", "Deze taak is niet mislukt")


@router.post("/admin/jobs/{job_id}/retry", tags=["beheer"], status_code=204)
async def retry_job(job_id: uuid.UUID, _: AdminUser, session: Session) -> None:
    if not await jobs.retry(session, job_id):
        raise await _not_failed(session, job_id)
    await session.commit()


@router.delete("/admin/jobs/{job_id}", tags=["beheer"], status_code=204)
async def delete_job(job_id: uuid.UUID, _: AdminUser, session: Session) -> None:
    """Een mislukte taak weggooien: hij komt niet meer terug (ook niet bij "alles opnieuw")."""
    if not await jobs.delete_failed(session, job_id):
        raise await _not_failed(session, job_id)
    await session.commit()
