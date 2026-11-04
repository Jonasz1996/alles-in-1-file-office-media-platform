"""Gezichten automatisch (bouwontwerp fase 6, X2; de API van X2). Stubs van Z0b (Keuze 55): 501
not_built("X2"), na de inlog. De vormen staan in app/people/faces_types.py.

Deze router komt in main.py vóór routers/people.py, zodat /people/faces/... nooit bij
/people/{person_id} belandt. Alleen de eigenaar ziet gezichten; een ander die de foto mag zien
krijgt bij GET /files/{id}/faces [], anders 404. Aan- en uitzetten gaat via PATCH
/me/photo-settings (routers/photo_prefs.py).

GET /people/faces/suggestions heeft twee vormen: zonder person per persoon
(SuggestionsByPerson), met person de gezichten van die persoon (FacePage).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, Response

from app.people.faces_types import (
    FACES_PAGE,
    CropSize,
    FaceGroups,
    FaceOut,
    FacePage,
    FacesConfirmIn,
    FacesIn,
    FacesQueued,
    FacesRejectIn,
    FacesStatus,
    SuggestionsByPerson,
)
from app.routers.stubs import QueryInt, not_built
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])

Cursor = Annotated[str | None, Query(max_length=200)]
# ?size=96 komt als tekst binnen; CropSize (Literal[96, 192]) alleen neemt geen "96" (dan altijd
# 422, ook voor faceCrop van de web)
Size = Annotated[CropSize, QueryInt]


@router.get("/people/faces/status")
async def faces_status(user: CurrentUser) -> FacesStatus:
    raise not_built("X2")


@router.get("/people/faces/suggestions")
async def face_suggestions(
    user: CurrentUser,
    person: uuid.UUID | None = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=FACES_PAGE)] = FACES_PAGE,
) -> SuggestionsByPerson | FacePage:
    raise not_built("X2")


@router.get("/people/faces/groups")
async def face_groups(user: CurrentUser, cursor: Cursor = None) -> FaceGroups:
    raise not_built("X2")


@router.get("/people/faces/groups/{key}")
async def face_group(
    key: Annotated[str, Path(max_length=100)], user: CurrentUser, cursor: Cursor = None
) -> FacePage:
    """409 faces_gone na een nieuwe ronde."""
    raise not_built("X2")


@router.post("/people/faces/confirm", status_code=204)
async def confirm_faces(body: FacesConfirmIn, user: CurrentUser) -> None:
    """422 faces_too_many, 409 faces_gone."""
    raise not_built("X2")


@router.post("/people/faces/reject", status_code=204)
async def reject_faces(body: FacesRejectIn, user: CurrentUser) -> None:
    raise not_built("X2")


@router.post("/people/faces/hide", status_code=204)
async def hide_faces(body: FacesIn, user: CurrentUser) -> None:
    raise not_built("X2")


@router.post("/people/faces/reset", status_code=204)
async def reset_faces(body: FacesIn, user: CurrentUser) -> None:
    """Ongedaan maken, hooguit 10 minuten na de keuze."""
    raise not_built("X2")


@router.post("/people/faces/wipe-unnamed", status_code=202)
async def wipe_unnamed_faces(user: CurrentUser) -> FacesQueued:
    """faces.wipe met scope unnamed."""
    raise not_built("X2")


@router.get("/people/faces/{face_id}/crop", response_class=Response)
async def face_crop(face_id: uuid.UUID, user: CurrentUser, size: Size = 96) -> Response:
    """WebP, Cache-Control: no-store; niets op schijf. Met de inlog als Bearer-header, zoals elke
    route hier: de web haalt hem met een fetch (faceCrop), niet als adres in een <img>; geen
    token of handtekening in de query (review Z0b, veilig V2 en contract F2)."""
    raise not_built("X2")


@router.get("/files/{node_id}/faces")
async def file_faces(node_id: uuid.UUID, user: CurrentUser) -> list[FaceOut]:
    raise not_built("X2")


@router.post("/files/{node_id}/faces/rescan", status_code=202)
async def rescan_faces(node_id: uuid.UUID, user: CurrentUser) -> None:
    """Opnieuw zoeken, vooraan in de rij; hooguit 20 per uur."""
    raise not_built("X2")
