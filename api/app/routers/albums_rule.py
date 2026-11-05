"""De regel van een slim album (bouwontwerp fase 6, §3.4; Z6, Keuze 42). Stubs van Z0: 501
not_built. Codes: album_rule_invalid, album_rule_too_many.
"""

import uuid

from fastapi import APIRouter

from app.people.types import AlbumRuleIn, AlbumRuleSaved, RuleConfirmIn
from app.routers.stubs import not_built
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["albums"])


@router.put("/albums/{album_id}/rule")
async def set_rule(album_id: uuid.UUID, body: AlbumRuleIn, user: CurrentUser) -> AlbumRuleSaved:
    raise not_built("Z6")


@router.post("/albums/{album_id}/rule/confirm")
async def confirm_rule(
    album_id: uuid.UUID, body: RuleConfirmIn, user: CurrentUser
) -> AlbumRuleSaved:
    raise not_built("Z6")


@router.delete("/albums/{album_id}/rule", status_code=204)
async def delete_rule(album_id: uuid.UUID, user: CurrentUser) -> None:
    """De regel weg; de items van de regel blijven, met hun shared."""
    raise not_built("Z6")


@router.post("/albums/{album_id}/refresh", status_code=202)
async def refresh(album_id: uuid.UUID, user: CurrentUser) -> None:
    raise not_built("Z6")
