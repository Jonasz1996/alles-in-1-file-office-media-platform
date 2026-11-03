"""De analyse van inhoud voor zoeken (bouwontwerp fase 6, §3.3; Keuzes 3, 19, 49, 50).

Z0 legt de vorm vast; Z2 is de eigenaar en bouwt de wachtrij (queue, de rij index, Defer) en
de stappen. Eén stap per inhoud (blob) in de tabel analysis: text (Z2), hash (Z3), ocr (X1).

write_text() is af: het is de enige schrijver van blob_text (Keuze 3). Eerst een SELECT, dan een
gewone INSERT of één UPDATE, in één korte transactie. Nooit INSERT OR REPLACE, OR IGNORE of een
upsert: dan klopt de FTS-index niet meer (de triggers van app/search/schema.py).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import BlobText, now

log = logging.getLogger("platform.analysis")


class Step(StrEnum):
    TEXT = "text"
    HASH = "hash"
    OCR = "ocr"


# Een hogere versie zet de stap voor elke inhoud opnieuw in de wachtrij (zoals PROBE_VERSION)
VERSIONS: dict[Step, int] = {Step.TEXT: 1, Step.HASH: 1, Step.OCR: 1}

# Doet de stap mee voor deze inhoud? (sha, mime) → ja of nee
Eligible = Callable[[str, str | None], bool]
# Voert de stap uit voor één inhoud (sha); de stap schrijft zelf zijn uitkomst
Runner = Callable[[Settings, str], Awaitable[None]]


@dataclass(frozen=True)
class Registered:
    step: Step
    lane: str
    eligible: Eligible
    run: Runner


_steps: dict[Step, Registered] = {}


def register_step(step: Step, lane: str, eligible: Eligible, run: Runner) -> None:
    """Een pakket meldt zijn stap aan (Z2 text, Z3 hash, X1 ocr); één keer per stap."""
    if step in _steps:
        raise ValueError(f"stap {step.value} is al aangemeld")
    _steps[step] = Registered(step=step, lane=lane, eligible=eligible, run=run)


def registered() -> dict[Step, Registered]:
    return dict(_steps)


def reset() -> None:
    """Alleen voor tests."""
    _steps.clear()


async def queue(
    db: AsyncSession,
    sha: str,
    steps: Iterable[Step],
    *,
    owner: uuid.UUID | None,
    first: bool = False,
) -> None:
    """Zet stappen voor een inhoud in de wachtrij; owner: wie hem het eerst nodig had (de
    volgorde per gebruiker, Keuze 20); first: vóór de rest (wat iemand nu bekijkt)."""
    raise NotImplementedError("queue wordt gebouwd in Z2")


async def write_text(
    db: AsyncSession,
    sha: str,
    *,
    body: str | None = None,
    ocr: str | None = None,
    meta: str | None = None,
    truncated: bool = False,
) -> None:
    """Schrijft de tekst van een inhoud en sluit af met een commit: één korte transactie.

    None laat een veld zoals het is (een nieuwe rij krijgt dan ""). truncated hoort bij body:
    het telt alleen als body meekomt. Wat er nog in de sessie stond, gaat mee in dezelfde
    commit; roep hem dus aan met een sessie zonder ander werk.

    Schreef een ander dezelfde inhoud tussen de SELECT en de INSERT, dan geeft de INSERT een
    IntegrityError; dan nog één keer, en de SELECT vindt de rij.
    """
    for attempt in (1, 2):
        exists = await db.scalar(select(BlobText.blob_sha).where(BlobText.blob_sha == sha))
        try:
            if exists is None:
                await db.execute(
                    insert(BlobText).values(
                        blob_sha=sha,
                        body=body or "",
                        ocr=ocr or "",
                        meta=meta or "",
                        truncated=int(bool(truncated)) if body is not None else 0,
                        updated_at=now(),
                    )
                )
            else:
                values: dict[str, object] = {"updated_at": now()}
                if body is not None:
                    values["body"] = body
                    values["truncated"] = int(bool(truncated))
                if ocr is not None:
                    values["ocr"] = ocr
                if meta is not None:
                    values["meta"] = meta
                await db.execute(update(BlobText).where(BlobText.blob_sha == sha).values(values))
            await db.commit()
            return
        except IntegrityError:
            await db.rollback()
            if attempt == 2:
                raise
            log.info("blob_text voor %s kwam er tussendoor bij; opnieuw", sha[:12])
