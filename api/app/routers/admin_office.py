"""/api/v1/admin/office: office op /beheer (Keuze 27 en 28). O0-stubs die 503 office_off
geven; O5 maakt ze af. Alleen voor beheerders.
"""

from fastapi import APIRouter

from app import errors
from app.routers.files import NodeOut
from app.routers.office_contracts import CheckpointOut, OfficeAdmin, OfficeAdminIn, RetryOut
from app.security import AdminUser

router = APIRouter(prefix="/api/v1/admin/office", tags=["beheer"])

OFF = "Bewerken van documenten staat op deze server niet aan"


@router.get("")
async def office_admin(_: AdminUser) -> OfficeAdmin:
    raise errors.fail(503, "office_off", OFF)


@router.put("")
async def save_office_admin(body: OfficeAdminIn, _: AdminUser) -> OfficeAdmin:
    raise errors.fail(503, "office_off", OFF)


@router.post("/checkpoint")
async def checkpoint(_: AdminUser) -> CheckpointOut:
    raise errors.fail(503, "office_off", OFF)


@router.post("/conversions/retry")
async def retry_conversions(_: AdminUser) -> RetryOut:
    raise errors.fail(503, "office_off", OFF)


@router.post("/unsaved/{key}/restore")
async def restore_unsaved(key: str, _: AdminUser) -> NodeOut:
    raise errors.fail(503, "office_off", OFF)


@router.delete("/unsaved/{key}", status_code=204)
async def delete_unsaved(key: str, _: AdminUser) -> None:
    raise errors.fail(503, "office_off", OFF)
