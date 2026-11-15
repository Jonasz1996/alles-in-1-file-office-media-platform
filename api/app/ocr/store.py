"""Waar de woorden van OCR blijven (bouwontwerp fase 6, X1; Keuze 23 en 25a; §3.9).

- **ocr_page** (blob_sha, page): de woorden van één gescande PDF-pagina met hun plek, als zlib van
  JSON [[t, x, y, w, h], …] (x, y, w, h als deel van de pagina, 0..1, zoals de viewer de pagina
  toont). Hoort bij de inhoud: wie het document mag lezen, mag ook de woorden zien (de tekst zelf
  staat in blob_text.ocr). Weg met de blob (cascade).
- **photo_ocr** (node_id): de tekst en de woorden van één foto, per node van één eigenaar. Alleen
  in de index bij die node (de kolom pocr, de triggers po_*). Een andere eigenaar (node_au_owner)
  of nieuwe inhoud (blob_id) wist hem; de node weg wist hem ook (cascade).

Een rij in photo_ocr met words = b"" is een **verzoek** dat nog loopt: de eigenaar drukte op
"Tekst herkennen" (of de knop van een eerdere tekst). De tekst die er al stond, blijft tot de
nieuwe er is. Een rij met woorden [] betekent: gekeken, niets gevonden.

Schrijven gaat zoals overal bij de index: eerst SELECT, dan een gewone INSERT of één UPDATE,
nooit INSERT OR REPLACE of een upsert (de triggers van app/search/schema.py). Geen tekst van een
foto of pagina in een log of een fout.
"""

from __future__ import annotations

import json
import uuid
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Blob, Node, OcrPage, PhotoOcr, PhotoPref, now
from app.ocr import geometry

PENDING = b""  # words van een verzoek dat nog loopt
MAX_TEXT = 200_000  # tekens per foto in photo_ocr.text (de worker geeft hooguit 20.000 woorden)


def pack(words: Sequence[Sequence]) -> bytes:
    return zlib.compress(
        json.dumps([list(w) for w in words], separators=(",", ":"), ensure_ascii=False).encode()
    )


def unpack(data: bytes | None) -> list[list] | None:
    """De woorden, of None voor een verzoek dat nog loopt (of onzin in de kolom)."""
    if not data:
        return None
    try:
        value = json.loads(zlib.decompress(data))
    except (zlib.error, ValueError):
        return None
    return value if isinstance(value, list) else None


def _clamp(v: float) -> float:
    return min(1.0, max(0.0, v))


def placed(words: Iterable[Sequence], summary: dict) -> list[list]:
    """De woorden van de worker ([tekst, links, boven, breedte, hoogte, regel] in pixels van het
    rechte beeld van kind 1) als [t, x, y, w, h], deel van de bron zoals de viewer hem toont: het
    midden van elk vak gaat terug met de hoek van de deskew (geometry.centre), de maat blijft."""
    width, height = int(summary.get("width") or 0), int(summary.get("height") or 0)
    source = summary.get("source") or [width, height]
    sw, sh = int(source[0] or 0), int(source[1] or 0)
    if min(width, height, sw, sh) <= 0:
        return []
    skew = float(summary.get("skew") or 0.0)
    out: list[list] = []
    for word in words:
        text, left, top, w, h = word[0], *(float(v) for v in word[1:5])
        cx, cy = geometry.centre((left, top, w, h), skew, (width, height), (sw, sh))
        fw, fh = min(1.0, w / sw), min(1.0, h / sh)
        out.append(
            [
                str(text),
                round(_clamp(cx / sw - fw / 2), 5),
                round(_clamp(cy / sh - fh / 2), 5),
                round(fw, 5),
                round(fh, 5),
            ]
        )
    return out


def text_of(words: Iterable[Sequence]) -> str:
    """De tekst van de woorden van de worker: per regel met een spatie, regels met een
    regeleinde (zoals ops/ocr.text_of)."""
    lines: dict[int, list[str]] = {}
    for word in words:
        lines.setdefault(int(word[5]), []).append(str(word[0]))
    return "\n".join(" ".join(lines[n]) for n in sorted(lines))


# --- ocr_page ------------------------------------------------------------------------------------


async def done_pages(db: AsyncSession, sha: str) -> set[int]:
    return set((await db.scalars(select(OcrPage.page).where(OcrPage.blob_sha == sha))).all())


async def put_page(db: AsyncSession, sha: str, page: int, angle: float, words: list[list]) -> None:
    """De woorden van één pagina, in één korte transactie (met commit)."""
    mine = (OcrPage.blob_sha == sha, OcrPage.page == page)
    data = pack(words)
    if await db.scalar(select(OcrPage.page).where(*mine)) is None:
        await db.execute(insert(OcrPage).values(blob_sha=sha, page=page, angle=angle, words=data))
    else:
        await db.execute(update(OcrPage).where(*mine).values(angle=angle, words=data))
    await db.commit()


async def page_words(db: AsyncSession, sha: str, page: int) -> list[list] | None:
    """De woorden van een gescande pagina, of None als OCR hem (nog) niet las."""
    data = await db.scalar(
        select(OcrPage.words).where(OcrPage.blob_sha == sha, OcrPage.page == page)
    )
    return unpack(data)


async def pdf_text(db: AsyncSession, sha: str) -> str:
    """De tekst van alle gescande pagina's (op volgorde), voor blob_text.ocr: per pagina de
    woorden met een spatie, de pagina's met een regeleinde."""
    pages = []
    for (data,) in await db.execute(
        select(OcrPage.words).where(OcrPage.blob_sha == sha).order_by(OcrPage.page)
    ):
        words = unpack(data) or []
        if words:
            pages.append(" ".join(str(w[0]) for w in words))
    return "\n".join(pages)


async def forget_failed_pages(db: AsyncSession, sha: str) -> None:
    """De pagina's zonder woorden (mislukt of leeg) weg, zodat een nieuw verzoek ze opnieuw
    leest. In de transactie van de aanroeper."""
    empty = pack([])
    await db.execute(delete(OcrPage).where(OcrPage.blob_sha == sha, OcrPage.words == empty))


# --- photo_ocr -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    node_id: uuid.UUID
    owner_id: uuid.UUID


async def photo_targets(db: AsyncSession, sha: str) -> list[Target]:
    """De foto's (nodes) met deze inhoud die OCR willen: een verzoek dat loopt (words = b"") of
    een eigenaar met "Tekst in foto's herkennen" aan. Ook in de prullenbak (terugzetten brengt
    de tekst mee); nooit de node van een eigenaar die het niet vroeg."""
    pending = (
        select(PhotoOcr.node_id)
        .where(PhotoOcr.node_id == Node.id, PhotoOcr.words == PENDING)
        .exists()
    )
    wants = (
        select(PhotoPref.user_id)
        .where(PhotoPref.user_id == Node.owner_id, PhotoPref.ocr_photos == 1)
        .exists()
    )
    rows = await db.execute(
        select(Node.id, Node.owner_id).where(
            Node.blob_id == sha, Node.kind == "file", or_(pending, wants)
        )
    )
    return [Target(node_id, owner_id) for node_id, owner_id in rows.all()]


async def put_photo(
    db: AsyncSession, sha: str, words: list[list], text: str, angle: float
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """De tekst van een foto bij elke node die hem wil (photo_targets, opnieuw gelezen in deze
    transactie: wie intussen uitzette of een andere inhoud kreeg, krijgt niets). Eén korte
    transactie met commit. Geeft per eigenaar zijn nodes."""
    data = pack(words)
    text = text[:MAX_TEXT]
    written: dict[uuid.UUID, list[uuid.UUID]] = {}
    for target in await photo_targets(db, sha):
        mine = PhotoOcr.node_id == target.node_id
        values = {"text": text, "words": data, "angle": angle, "updated_at": now()}
        if await db.scalar(select(PhotoOcr.node_id).where(mine)) is None:
            await db.execute(
                insert(PhotoOcr).values(node_id=target.node_id, owner_id=target.owner_id, **values)
            )
        else:
            await db.execute(update(PhotoOcr).where(mine).values(values))
        written.setdefault(target.owner_id, []).append(target.node_id)
    await db.commit()
    return written


async def request_photo(db: AsyncSession, node: Node) -> None:
    """De eigenaar vraagt OCR van deze foto: een verzoek (words = b""). De tekst die er al
    stond, blijft tot de nieuwe er is. In de transactie van de aanroeper."""
    mine = PhotoOcr.node_id == node.id
    if await db.scalar(select(PhotoOcr.node_id).where(mine)) is None:
        await db.execute(
            insert(PhotoOcr).values(
                node_id=node.id, owner_id=node.owner_id, text="", words=PENDING, updated_at=now()
            )
        )
    else:
        await db.execute(update(PhotoOcr).where(mine).values(words=PENDING, updated_at=now()))


async def photo_row(db: AsyncSession, node_id: uuid.UUID) -> PhotoOcr | None:
    return await db.get(PhotoOcr, node_id, populate_existing=True)


async def copy_photo(
    db: AsyncSession, source: uuid.UUID, copy: uuid.UUID, owner: uuid.UUID
) -> None:
    """Een kopie door dezelfde eigenaar (search.hooks.photo_copy): de tekst gaat mee, het is zijn
    eigen tekst van dezelfde inhoud. Een verzoek dat nog loopt, gaat niet mee. In de transactie
    van de aanroeper."""
    row = await photo_row(db, source)
    if row is None or row.owner_id != owner or row.words == PENDING:
        return
    if await db.scalar(select(PhotoOcr.node_id).where(PhotoOcr.node_id == copy)) is not None:
        return
    await db.execute(
        insert(PhotoOcr).values(
            node_id=copy,
            owner_id=owner,
            text=row.text,
            words=row.words,
            angle=row.angle,
            updated_at=now(),
        )
    )


async def wanting_shas(
    db: AsyncSession, owner: uuid.UUID, mimes: Iterable[str], after: str, limit: int
) -> list[str]:
    """De inhoud van de foto's van deze eigenaar zonder tekst van OCR (geen rij in photo_ocr, of
    een verzoek), op sha na after: wat "Tekst in foto's herkennen" nog moet lezen."""
    done = (
        select(PhotoOcr.node_id)
        .where(PhotoOcr.node_id == Node.id, PhotoOcr.words != PENDING)
        .exists()
    )
    rows = await db.scalars(
        select(Node.blob_id)
        .join(Blob, Blob.sha256 == Node.blob_id)
        .where(
            Node.owner_id == owner,
            Node.kind == "file",
            Node.blob_id > after,
            func.lower(Blob.mime).in_(sorted(mimes)),
            ~done,
        )
        .group_by(Node.blob_id)
        .order_by(Node.blob_id)
        .limit(limit)
    )
    return [sha for sha in rows.all() if sha]
