"""Hulp voor de tests van OCR aan de kant van de app (bouwontwerp fase 6, X1).

De nep-worker (tests/media/fake_worker.py) spreekt ocr al (register_ocr). Hier komt de opdracht
text erbij, zoals de stap ocr die gebruikt om te zien welke pagina's van een PDF scans zijn:
de test zegt per inhoud welke pagina's scans zijn en welke tekst er al op staat (`pdf`), en de
nep schrijft de uitkomst op de pipe zoals het kind van ops/text.py (frame.encode).

Verder: kleine foto's en de stappen van de app (de pomp van de rij ocr, de rij van een inhoud
in analysis, de tekst in blob_text en photo_ocr) zonder dat elke test ze zelf opzoekt.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import random
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
from PIL import Image, ImageDraw
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import analysis
from app.analysis import Step
from app.db import get_engine
from app.extract import frame
from app.media.wire import Op
from app.models import Analysis, BlobText, OcrPage, PhotoOcr
from app.ocr import store
from tests.media import fake_worker
from tests.media.fake_worker import FakeWorker, Job, running

WIDTH, HEIGHT = 600, 400


def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def photo(seed: str) -> bytes:
    """Een kleine JPEG, per seed anders (een andere inhoud, een andere sha)."""
    rng = random.Random(seed)
    image = Image.new("RGB", (WIDTH, HEIGHT), (rng.randrange(200, 250),) * 3)
    draw = ImageDraw.Draw(image)
    for _ in range(12):
        x, y = rng.randrange(WIDTH - 40), rng.randrange(HEIGHT - 20)
        draw.rectangle((x, y, x + 30, y + 8), fill=(rng.randrange(60),) * 3)
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def words(*texts: str, line: int = 1) -> list[list]:
    """Woorden zoals de worker ze geeft ([tekst, links, boven, breedte, hoogte, regel], pixels
    van een beeld van WIDTH × HEIGHT), naast elkaar op één regel."""
    return [[t, 20 + 110 * i, 40 * line, 100, 30, line] for i, t in enumerate(texts)]


@dataclass
class Nep:
    """De nep-worker met de opdracht text: pdf() zegt wat een PDF is."""

    fake: FakeWorker
    scans: dict[str, tuple[list[int], str, bool]] = field(default_factory=dict)
    surveys: int = 0

    def pdf(self, data: bytes, pages: list[int], text: str = "", *, truncated=False) -> None:
        """Deze PDF heeft scans op pages (vanaf 1) en text als tekstlaag."""
        self.scans[sha(data)] = (list(pages), text, truncated)

    def page(self, data: bytes, n: int, *texts: str) -> None:
        """Pagina n van deze PDF (of de foto, n = 1) geeft bij ocr deze woorden."""
        self.fake.register_ocr(data, words(*texts), page=n, width=WIDTH, height=HEIGHT)

    def ocr_calls(self) -> list[dict]:
        """De vragen ocr die de nep kreeg (hun args: kind, page, ...)."""
        return [r.args for r in self.fake.requests if r.op == Op.OCR.value]

    async def _text(self, job: Job) -> dict:
        self.surveys += 1
        out = job.outputs[0]
        for i, fd in enumerate(job.inputs):
            pages, text, truncated = self.scans.get(fake_worker._sha_fd(fd) or "", ([], "", False))
            fields = {
                "state": "done" if text.strip() else "none",
                "truncated": truncated,
                "scan_pages": pages,
                "meta": "",
                "code": None,
            }
            await fake_worker._write(out, frame.encode(i, fields, text))
        fake_worker._close_output(job, out)
        return {"rc": 0, "cpu_s": 0.0}


@contextlib.asynccontextmanager
async def worker(tmp_path: Path, *, ocr: bool = True) -> AsyncIterator[Nep]:
    """Een nep-worker met tesseract (ocr) of zonder, en de client erop gebonden."""
    async with running(tmp_path / "w" / "nep.sock") as fake:
        fake.ocr = ocr
        nep = Nep(fake)
        fake.on(Op.TEXT, nep._text)
        yield nep


# --- de stand in de database ---------------------------------------------------------------------


async def pump(settings) -> int:
    """Eén beurt van de pomp in de rij ocr."""
    return await analysis.pump(settings, "ocr")


async def row(settings, blob_sha: str) -> tuple[str, str | None, int] | None:
    """(state, code, tries) van de rij ocr van deze inhoud, of None."""
    async with maker(settings)() as db:
        found = (
            await db.execute(
                sa.select(Analysis.state, Analysis.code, Analysis.tries).where(
                    Analysis.blob_sha == blob_sha, Analysis.step == Step.OCR.value
                )
            )
        ).first()
    return tuple(found) if found is not None else None  # type: ignore[return-value]


async def set_row(settings, blob_sha: str, state: str, code: str | None = None) -> None:
    async with maker(settings)() as db:
        await db.execute(
            sa.update(Analysis)
            .where(Analysis.blob_sha == blob_sha, Analysis.step == Step.OCR.value)
            .values(state=state, code=code)
        )
        await db.commit()


async def pdf_ocr(settings, blob_sha: str) -> str | None:
    """blob_text.ocr van deze inhoud."""
    async with maker(settings)() as db:
        return await db.scalar(sa.select(BlobText.ocr).where(BlobText.blob_sha == blob_sha))


async def pages(settings, blob_sha: str) -> dict[int, list[list] | None]:
    """De pagina's in ocr_page met hun woorden."""
    async with maker(settings)() as db:
        rows = await db.execute(
            sa.select(OcrPage.page, OcrPage.words).where(OcrPage.blob_sha == blob_sha)
        )
        return {n: store.unpack(data) for n, data in rows.all()}


async def photo_text(settings, node_id: str | uuid.UUID) -> tuple[str, bytes] | None:
    """(text, words) van photo_ocr bij deze node, of None."""
    async with maker(settings)() as db:
        found = (
            await db.execute(
                sa.select(PhotoOcr.text, PhotoOcr.words).where(
                    PhotoOcr.node_id == uuid.UUID(str(node_id))
                )
            )
        ).first()
    return tuple(found) if found is not None else None  # type: ignore[return-value]


async def fts(settings, column: str, word: str) -> set[str]:
    """De nodes (id) waar dit woord in deze kolom van fts_node staat."""
    async with maker(settings)() as db:
        rows = await db.execute(
            sa.text(
                "SELECT n.id FROM fts_node JOIN node n ON n.num = fts_node.rowid "
                "WHERE fts_node MATCH :q"
            ),
            {"q": f'{column}:"{word}"'},
        )
        return {str(uuid.UUID(str(r[0]))) for r in rows.all()}


async def search(client, headers, q: str) -> dict[str, dict]:
    """GET /search: per node (id) de treffer (match, snippet)."""
    r = await client.get("/api/v1/search", params={"q": q}, headers=headers)
    assert r.status_code == 200, r.text
    return {hit["node"]["id"]: hit for hit in r.json()["results"]}
