"""De achtergrondtaken op /beheer: overzicht en opnieuw proberen (verhuisd uit
routers/previews.py; P6 breidt dit uit)."""

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app import errors, jobs
from app.db import Session
from app.models import Job
from app.security import AdminUser

router = APIRouter(prefix="/api/v1", tags=["beheer"])


class FailedJob(BaseModel):
    id: uuid.UUID
    type: str
    key: str | None
    attempts: int
    error: str | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class JobsOverview(BaseModel):
    counts: dict[str, int]
    failed: list[FailedJob]


class Retried(BaseModel):
    count: int


@router.get("/admin/jobs", tags=["beheer"])
async def admin_jobs(_: AdminUser, session: Session) -> JobsOverview:
    """Hoeveel taken er klaarstaan, lopen, klaar en mislukt zijn, en de laatste 100 mislukte."""
    counts = await jobs.counts(session)
    failed = [FailedJob.model_validate(j) for j in await jobs.failed(session, 100)]
    return JobsOverview(counts=counts, failed=failed)


@router.post("/admin/jobs/retry-failed", tags=["beheer"])
async def retry_failed(_: AdminUser, session: Session) -> Retried:
    """Alle mislukte taken opnieuw, elk weer met alle pogingen."""
    count = await jobs.retry_failed(session)
    await session.commit()
    return Retried(count=count)


@router.post("/admin/jobs/{job_id}/retry", tags=["beheer"], status_code=204)
async def retry_job(job_id: uuid.UUID, _: AdminUser, session: Session) -> None:
    if not await jobs.retry(session, job_id):
        exists = await session.get(Job, job_id)
        await session.commit()
        if exists is None:
            raise errors.fail(404, "job_not_found", "Taak niet gevonden")
        raise errors.fail(409, "not_failed", "Deze taak is niet mislukt")
    await session.commit()
