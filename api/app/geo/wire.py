"""Z3 bij de rest van de app aanmelden (bouwontwerp fase 6, §3.5).

routers/geo.py en routers/photos_map.py importeren deze module, dus na de import van app.main is
alles aangemeld (zoals app.people.search bij Z6). Een test die search.hooks.reset(),
filters.reset() of previews.forget_ready() deed, roept register() opnieuw aan; twee keer kan.

- search.hooks.register_backfill(load.start): bij de start places.sqlite bouwen als hij ontbreekt
  of bij andere bronnen hoort, op de achtergrond en nooit fataal (Keuze 29).
- previews.register_ready: na preview.ready van een foto of video de keten van photometa, na
  photometa.DELAY (een import geeft zo één keten). Die leest de plek; de preview nooit (P6).
- search.hooks.register_photo_copy: een kopie erft nooit een plek (Keuze 25a). Heeft de inhoud er
  een, dan zet de kopie (in zijn eigen transactie) de keten klaar in fase nodes: photometa leest
  de plek dan voor de eigenaar van de kopie, als die locatie niet op "uit" heeft.
- prefs.register_location: "uit" zet de job geo.off klaar (alle rijen van die gebruiker weg),
  "mij" de keten in fase nodes (zijn foto's opnieuw lezen) (Keuze 28).
- app.search.filters_photo.register: de filters place, country en camera, de suggesties
  "plaatsen" en de groep plaatsen bovenaan de resultaten (Keuze 43).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app import photo_rows, photometa, prefs, previews
from app.geo import load
from app.models import Blob
from app.search import filters_photo, hooks

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models import User

MEDIA = ("image/", "video/")


async def on_ready(maker: async_sessionmaker[AsyncSession], sha: str, _dated: bool) -> None:
    """Na preview.ready: een foto of video krijgt (na DELAY) de keten van photometa."""
    async with maker() as db:
        mime = await db.scalar(select(Blob.mime).where(Blob.sha256 == sha))
        if not mime or not mime.lower().startswith(MEDIA):
            return
        await photometa.kick(db, photometa.BLOBS, delay=photometa.DELAY)
        await db.commit()


async def on_copy(session: Any, pairs: Sequence[tuple[uuid.UUID, uuid.UUID]]) -> None:
    """Bestanden gekopieerd (bron, kopie), in de transactie van de kopie: heeft een van die
    inhouden een plek, dan de keten in fase nodes. Niets gaat mee (Keuze 25a)."""
    if await photo_rows.copied(session, [copy for _source, copy in pairs]):
        await photometa.kick(session, photometa.NODES)


async def on_location(db: AsyncSession, user: User, value: str) -> None:
    """PATCH /me/photo-settings veranderde location, in dezelfde transactie (Keuze 28)."""
    if value == "uit":
        await photo_rows.enqueue_off(db, user.id)
    else:
        await photometa.kick(db, photometa.NODES)


def register() -> None:
    hooks.register_backfill(load.start)
    hooks.register_photo_copy(on_copy)
    previews.register_ready(on_ready)
    prefs.register_location(on_location)
    filters_photo.register()


register()
