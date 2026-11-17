"""Hulpmiddelen voor de tests van migratie 0013 (PostgreSQL, release 0.8.2, P2;
docs/fase-6/i3/pg-0013.md).

- boom(): twee gebruikers en een boom van Eva, via de factories (dus op SQLite en PostgreSQL).
- nalopen(): de Python-referentie check_tree van tests/search/migratie.py (dezelfde als bij de
  triggers van SQLite) op deze database.
- fill(): fase6_fill() van 0013, met de aantallen per soort.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import sqlalchemy as sa

from app.config import Settings
from app.db import get_engine
from tests.factories import create_file, create_folder, create_user, maker
from tests.search.migratie import check_tree

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ZERO = {"num": 0, "tree": 0, "photo_geo": 0, "search_seq": 0, "search_state": 0}

# naam: (ouder, bestandsnaam, inhoud, mime, prullenbak); ouder None is de hoofdmap van Eva
FILES = {
    "verslag": ("verslagen", "verslag.docx", b"verslag", DOCX, False),
    "strand": ("vakantie", "strand.jpg", b"strand", "image/jpeg", False),
    "schelp": ("diep", "schelp.jpg", b"schelp", "image/jpeg", False),
    "los": (None, "los.txt", b"los", "text/plain", True),
    "notities": (None, "notities.txt", b"notities", "text/plain", False),
}


async def boom(settings: Settings) -> dict[str, uuid.UUID]:
    """Eva en Bram, elk met een hoofdmap. Bij Eva: werk/verslagen/verslag.docx, vakantie (in de
    prullenbak) met strand.jpg en diep/schelp.jpg, los.txt (in de prullenbak) en notities.txt.
    Geeft de id's per naam (eva, bram, root, bram_root en de mappen en bestanden)."""
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        assert eva.root_id is not None and bram.root_id is not None
        ids = {"eva": eva.id, "bram": bram.id, "root": eva.root_id, "bram_root": bram.root_id}
        werk = await create_folder(session, eva, name="werk")
        ids["werk"] = werk.id
        ids["verslagen"] = (await create_folder(session, eva, werk.id, "verslagen")).id
        vakantie = await create_folder(session, eva, name="vakantie", deleted=True)
        ids["vakantie"] = vakantie.id
        ids["diep"] = (await create_folder(session, eva, vakantie.id, "diep")).id
        for key, (parent, name, data, mime, deleted) in FILES.items():
            node = await create_file(session, eva, ids[parent] if parent else None, name,
                                     data=data, mime=mime, deleted=deleted)  # fmt: skip
            ids[key] = node.id
        await session.commit()
    return ids


async def run(settings: Settings, query: str, **params: Any) -> None:
    async with get_engine(settings).begin() as conn:
        await conn.execute(sa.text(query), params)


async def rows(settings: Settings, query: str, **params: Any) -> list[tuple]:
    async with get_engine(settings).connect() as conn:
        return [tuple(r) for r in (await conn.execute(sa.text(query), params)).all()]


async def nalopen(settings: Settings) -> int:
    """check_tree: num uniek, elk pad dat van de ouder + num, dead van de node en de mappen
    erboven, photo_geo gelijk aan zijn node. Geeft het aantal nodes."""
    async with get_engine(settings).connect() as conn:
        return await conn.run_sync(check_tree)


async def fill(settings: Settings, repair: bool = True) -> dict[str, int]:
    """fase6_fill(repair): per soort het aantal rijen dat verschilde."""
    value = "true" if repair else "false"
    async with get_engine(settings).begin() as conn:
        found = await conn.exec_driver_sql(f"SELECT fase6_fill(repair => {value})::text")
        return json.loads(found.scalar_one())


def dsn(settings: Settings) -> str:
    """De URL voor asyncpg zelf (twee verbindingen met elk een eigen transactie)."""
    return settings.db_url.replace("postgresql+asyncpg://", "postgresql://")
