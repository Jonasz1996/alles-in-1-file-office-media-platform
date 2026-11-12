"""De regel van een slim album (bouwontwerp fase 6, §3.4; Z6, Keuze 42): zetten, de vraag over
delen beantwoorden, weghalen en verversen. Alleen de eigenaar van het album (anders 404 of 403
owner_only, zoals de andere routes van albums). Codes: album_rule_invalid,
album_rule_too_many. Wat er met de database gebeurt: app/albums_rule.py.

Verversen gebeurt (Keuze 42):

- meteen na PUT (in het verzoek: meer dan 10.000 is 400 album_rule_too_many en dan blijft alles
  zoals het was);
- na POST /refresh (202), elke nacht (housekeeping) en 60 s na een reeks uploads (de haak
  previews.register_ready: pas dan heeft een nieuwe foto zijn opnamedag), als taak albums.rule,
  één per album tegelijk (key).

album.changed gaat na een verversing alleen naar wie het verschil kan zien: kwam er alleen iets
bij dat niet gedeeld wordt, dan alleen naar de eigenaar.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import albums_rule as R
from app import errors, events, jobs, previews
from app.config import Settings, get_settings
from app.db import Session, get_engine
from app.models import AlbumRule, Node, User
from app.people.types import AlbumRuleIn, AlbumRuleSaved, RuleConfirmIn
from app.routers.albums import changed, get_album, now
from app.routers.files import shared_ids
from app.security import CurrentUser

log = logging.getLogger("platform.albums")
router = APIRouter(prefix="/api/v1", tags=["albums"])

JOB = "albums.rule"
NIGHTLY = "albums.rule.nacht"
EVERY_NIGHT = 24 * 3600.0
AFTER_UPLOAD = timedelta(seconds=60)


def _key(album_id: uuid.UUID) -> str:
    return f"albums.rule:{album_id}"


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def _tell(session: AsyncSession, album: Node, done: R.Refreshed | None) -> None:
    """album.changed na de commit: naar iedereen die het album ziet als er iets gedeelds
    veranderde, anders alleen naar de eigenaar."""
    if done is None or not (done.added or done.removed):
        return
    if done.shared_changed:
        await changed(session, album)
    else:
        events.publish({album.owner_id}, "album.changed", {"id": str(album.id)})


@router.put("/albums/{album_id}/rule")
async def set_rule(
    album_id: uuid.UUID,
    body: AlbumRuleIn,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AlbumRuleSaved:
    """De regel van je album zetten of vervangen; het album wordt meteen ververst. Met
    share_new beantwoord je ook de vraag of nieuwe foto's gedeeld worden; zonder, terwijl nieuwe
    items nog niet gedeeld worden en het album gedeeld is of een publieke link heeft, dan
    needs_confirm (de vraag van Keuze 42)."""
    album = await get_album(session, user, album_id, "owner")
    await R.check(session, user, body.rule)
    row = await R.save(session, album.id, body.rule, body.share_new)
    done = await R.refresh(session, settings, user, album.id)
    if done is not None and done.too_many:
        await session.rollback()
        raise R.too_many()
    # De vraag alleen als dit verzoek hem niet beantwoordde en nieuwe items nog niet gedeeld
    # worden (een eerder "nee" vragen we bij een nieuwe regel opnieuw; een eerder "ja" gold
    # alleen voor dezelfde regel, R.save)
    needs = (
        body.share_new is None
        and not row.share_new
        and album.id in await shared_ids(session, [album.id])
    )
    album.modified_at = now()
    await session.commit()
    if body.share_new:
        await changed(session, album)
    else:
        await _tell(session, album, done)
    return AlbumRuleSaved(needs_confirm=needs)


@router.post("/albums/{album_id}/rule/confirm")
async def confirm_rule(
    album_id: uuid.UUID, body: RuleConfirmIn, user: CurrentUser, session: Session
) -> AlbumRuleSaved:
    """Het antwoord op de vraag: true deelt alle items van de regel (ook wat er later bij
    komt), false houdt nieuwe items voor jou. 404 als het album geen regel heeft."""
    album = await get_album(session, user, album_id, "owner")
    row = await session.get(AlbumRule, album.id, populate_existing=True)
    if row is None:
        raise errors.fail(404, "not_found", "Niet gevonden")
    opened = await R.confirm(session, row, body.share_new)
    if opened:
        album.modified_at = now()
    await session.commit()
    if opened:
        await changed(session, album)
    return AlbumRuleSaved(needs_confirm=False)


@router.delete("/albums/{album_id}/rule", status_code=204)
async def delete_rule(album_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    """De regel weg; de items van de regel blijven, met hun shared."""
    album = await get_album(session, user, album_id, "owner")
    if await R.remove(session, album.id):
        album.modified_at = now()
        await session.commit()
        events.publish({album.owner_id}, "album.changed", {"id": str(album.id)})


@router.post("/albums/{album_id}/refresh", status_code=202, response_class=Response)
async def refresh(album_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    """Het album opnieuw bij zijn regel brengen, op de achtergrond (taak albums.rule). Zonder
    regel gebeurt er niets."""
    album = await get_album(session, user, album_id, "owner")
    if await session.get(AlbumRule, album.id) is not None:
        await jobs.enqueue(session, JOB, {"album": str(album.id)}, key=_key(album.id))
        await session.commit()


# --- de taak, de nacht en na uploads -------------------------------------------------------


async def run(settings: Settings, payload: dict[str, Any]) -> None:
    """De taak albums.rule: één album verversen, als zijn eigenaar."""
    album_id = uuid.UUID(payload["album"])
    async with _maker(settings)() as session:
        album = await session.get(Node, album_id)
        if album is None or album.kind != "album":
            return
        owner = await session.get(User, album.owner_id)
        if owner is None:
            return
        done = await R.refresh(session, settings, owner, album.id)
        if done is not None and (done.added or done.removed):
            album.modified_at = now()
        await session.commit()
        await _tell(session, album, done)


async def _enqueue(
    session: AsyncSession, album_ids: list[uuid.UUID], after: timedelta | None = None
) -> int:
    """Een taak per album. Zonder after (de nacht): niet als er voor dat album al een klaarstaat
    of loopt. Met after (na uploads): alleen niet als er al een klaarstaat; een taak die nu
    loopt, kan de foto van zojuist al gemist hebben (jobs.chain)."""
    queued = 0
    for album_id in album_ids:
        payload, key = {"album": str(album_id)}, _key(album_id)
        if after is None:
            added = await jobs.enqueue(session, JOB, payload, key=key)
        else:
            added = await jobs.chain(session, JOB, payload, key, run_after=now() + after)
        queued += added is not None
    return queued


async def nightly(settings: Settings) -> None:
    """Elke nacht (housekeeping, de eerste keer kort na het starten): een taak per album met
    een regel."""
    async with _maker(settings)() as session:
        ids = list((await session.scalars(select(AlbumRule.album_id))).all())
        if ids and await _enqueue(session, ids):
            await session.commit()


async def after_upload(maker: async_sessionmaker[AsyncSession], sha: str, dated: bool) -> None:
    """previews.register_ready: een foto of video kreeg zijn opnamedag. De albums met een regel
    van wie deze inhoud heeft, worden over 60 s ververst; komt er intussen meer, dan blijft het
    bij die ene taak per album (key)."""
    if not dated:
        return
    async with maker() as session:
        owners = select(Node.owner_id).where(Node.blob_id == sha, Node.kind == "file")
        albums = select(Node.id).where(Node.kind == "album", Node.owner_id.in_(owners))
        ids = list(
            (
                await session.scalars(
                    select(AlbumRule.album_id).where(AlbumRule.album_id.in_(albums))
                )
            ).all()
        )
        if ids and await _enqueue(session, ids, AFTER_UPLOAD):
            await session.commit()


def register() -> None:
    """De taak, de nacht en de haak na uploads; bij het importeren, en opnieuw in tests die de
    haken van previews leegmaakten."""
    jobs.register(JOB, run)
    jobs.housekeeping(NIGHTLY, nightly, EVERY_NIGHT)
    previews.register_ready(after_upload)


register()
