"""Een nep van zoeken op beeld voor de tests van de app (fase 6, X3b), zonder numpy.

De nep-worker (tests/analyse/fake_worker.py) krijgt hier embed_image, rank en forget, met een
"model" van drie kleuren: een preview is één kleur, zijn vector is die kleur (r, g, b) op de
eerste drie plekken, genormaliseerd, als float16. De vraag "rood", "groen" of "blauw" is die
kleur; rank leest het bestand van de eigenaar uit de fd (het formaat van app/beeld/types.py) en
rekent de cosinus uit. Zo kan een test zeggen welke foto's een vraag vindt, en in welke volgorde.

Verder de stappen van de app: een foto neerzetten met zijn preview, de backfill en de pomp, de
jobs, en zoeken via de API.
"""

from __future__ import annotations

import io
import math
import os
import struct
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import analysis, jobs, previews
from app.analyse import wire
from app.analyse.wire import Code, Op
from app.analysis import Step
from app.beeld import jobs as bjobs
from app.beeld import types as vt
from app.db import get_engine
from app.models import Analysis, BeeldOwner, BlobEmbed, Node
from app.storage import BlobStore
from tests.analyse.fake_worker import FakeAnalyseWorker, FakeFail, FakeRequest
from tests.test_files import put, root

KLEUREN: dict[str, tuple[int, int, int]] = {
    "rood": (255, 0, 0),
    "groen": (0, 255, 0),
    "blauw": (0, 0, 255),
    "oranje": (255, 128, 0),
    "paars": (128, 0, 255),
}
SCALE = 117.33
BIAS = -12.9


def maker(settings) -> async_sessionmaker:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def vector(rgb: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(c * c for c in rgb)) or 1.0
    return [c / norm for c in rgb] + [0.0] * (vt.DIM - 3)


def pack(values: Sequence[float]) -> bytes:
    return struct.pack(f"<{vt.DIM}e", *values)


def unpack(raw: bytes) -> list[float]:
    return list(struct.unpack(f"<{vt.DIM}e", raw))


def photo(rgb: tuple[int, int, int], seed: int = 0) -> bytes:
    """Een JPEG van één kleur; seed maakt de inhoud uniek (één pixel in de hoek)."""
    img = Image.new("RGB", (48, 32), rgb)
    img.putpixel((0, 0), (seed % 256, (seed // 256) % 256, 7))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=95)
    return out.getvalue()


def preview(settings, sha: str, rgb: tuple[int, int, int]) -> None:
    """De 256.webp van deze inhoud (zoals de preview-job hem neerzet)."""
    path = previews.directory(BlobStore(settings.blob_dir), sha)
    path.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), rgb).save(path / bjobs.PREVIEW, "WEBP", lossless=True)


def _read_fd(fd: int) -> bytes:
    size = os.fstat(fd).st_size
    return os.pread(fd, size, 0)


@dataclass
class Nep:
    """De handlers van beeld op een FakeAnalyseWorker; onthoudt wat hij kreeg (nooit een vector
    in een bericht: alleen de args)."""

    fake: FakeAnalyseWorker
    codes: dict[int, str] = field(default_factory=dict)  # index in de batch -> code
    ranks: list[wire.RankArgs] = field(default_factory=list)
    forgets: list[str] = field(default_factory=list)
    embeds: list[int] = field(default_factory=list)  # aantal previews per embed_image
    rank_delay: float = 0.0

    def install(self) -> Nep:
        self.fake.beeld = wire.BeeldCaps(model=vt.MODEL, dim=vt.DIM, scale=SCALE, bias=BIAS)
        self.fake.on(Op.EMBED_IMAGE, self._embed)
        self.fake.on(Op.RANK, self._rank)
        self.fake.on(Op.FORGET, self._forget)
        return self

    async def _embed(self, req: FakeRequest) -> dict[str, Any]:
        self.embeds.append(len(req.previews))
        out = req.extra["uit"]
        photos = []
        for i, fd in enumerate(req.previews):
            code = self.codes.get(i, "ok")
            if code == "ok":
                with Image.open(io.BytesIO(_read_fd(fd))) as img:
                    rgb = img.convert("RGB").getpixel((16, 16))
                vec = pack(vector(rgb))
            else:
                vec = bytes(vt.DIM * 2)
            os.pwrite(out, vec, i * vt.DIM * 2)
            photos.append({"code": code})
        return {"photos": photos}

    async def _rank(self, req: FakeRequest) -> dict[str, Any]:
        import anyio

        args = req.args
        assert isinstance(args, wire.RankArgs)
        self.ranks.append(args)
        if self.rank_delay:
            await anyio.sleep(self.rank_delay)
        raw = _read_fd(req.extra["vectoren"])
        head = vt.read_head(raw[: vt.HEAD.size])
        if args.k == 0:
            return {"hits": []}
        nums = [
            vt.NUM.unpack_from(raw, head.nums_at() + i * vt.NUM.size)[0] for i in range(head.count)
        ]
        vecs = [
            unpack(
                raw[head.vectors_at() + i * vt.DIM * 2 : head.vectors_at() + (i + 1) * vt.DIM * 2]
            )
            for i in range(head.count)
        ]
        if args.num is not None:
            if args.num not in nums:
                raise FakeFail(Code.BAD_REQUEST, vt.NO_NUM)
            query = vecs[nums.index(args.num)]
        else:
            query = vector(KLEUREN.get(str(args.text), (1, 1, 1)))
        scored = [
            (num, round(sum(a * b for a, b in zip(query, vec, strict=True)) * 10_000))
            for num, vec in zip(nums, vecs, strict=True)
        ]
        scored.sort(key=lambda h: (-h[1], h[0]))
        return {"hits": [list(h) for h in scored[: args.k]]}

    async def _forget(self, req: FakeRequest) -> dict[str, Any]:
        assert isinstance(req.args, wire.ForgetArgs)
        self.forgets.append(req.args.owner)
        return {"found": False}


# --- de app ----------------------------------------------------------------------------------


async def upload(client, headers, name: str, data: bytes, parent: str | None = None) -> dict:
    parent = parent or (await root(client, headers))["folder"]["id"]
    r = await put(client, headers, parent, name, data)
    assert r.status_code == 201, r.text
    return r.json()


async def sha_of(settings, node_id: str) -> str:
    async with maker(settings)() as db:
        return str(await db.scalar(sa.select(Node.blob_id).where(Node.id == uuid.UUID(node_id))))


async def foto(client, headers, settings, name: str, kleur: str, seed: int = 0) -> dict:
    """Een foto in de hoofdmap met zijn preview; geeft de node (met "sha")."""
    rgb = KLEUREN[kleur]
    node = await upload(client, headers, name, photo(rgb, seed))
    node["sha"] = await sha_of(settings, node["id"])
    preview(settings, node["sha"], rgb)
    return node


async def beeld_aan(client, headers, value: str = "aan") -> None:
    r = await client.patch("/api/v1/me/photo-settings", json={"beeld": value}, headers=headers)
    assert r.status_code == 200, r.text


async def run_jobs(settings, lane: str | None = None) -> int:
    """Wat klaarstaat in de rijen default en index (aan, uit, wissen, het bestand), behalve de
    pomp van de analyse: die draait pump()."""
    count = 0
    for name in ("default", "index") if lane is None else (lane,):
        count += await jobs.run_pending(settings, lane=name, at=_later())
    return count


def _later():
    from datetime import timedelta

    return jobs.now() + timedelta(days=1)


async def backfill(settings) -> None:
    await analysis.backfill_job(settings, {"after": ""})
    async with maker(settings)() as db:
        await db.execute(
            sa.delete(analysis_jobs()).where(analysis_jobs().c.type == analysis.BACKFILL)
        )
        await db.commit()


def analysis_jobs():
    from app.models import Job

    return Job.__table__


async def pump(settings) -> int:
    """Pompen tot de rij analyse leeg is; geeft het aantal inhouden."""
    total = 0
    while True:
        n = await analysis.pump(settings, bjobs.LANE)
        if not n:
            return total
        total += n


async def bestanden(settings) -> None:
    """Wat na een poos gebeurt: het opruimwerk zet de bestanden klaar die achterlopen (ook na een
    trigger), en de jobs maken ze."""
    await bjobs.sweep(settings)
    await run_jobs(settings)


async def embed_all(settings) -> int:
    """Backfill, pomp en dan de bestanden per eigenaar."""
    await backfill(settings)
    n = await pump(settings)
    await bestanden(settings)
    return n


async def row(settings, sha: str) -> tuple[str, str | None] | None:
    async with maker(settings)() as db:
        found = (
            await db.execute(
                sa.select(Analysis.state, Analysis.code).where(
                    Analysis.blob_sha == sha, Analysis.step == Step.EMBED.value
                )
            )
        ).first()
    return None if found is None else (found[0], found[1])


async def vectors(settings) -> set[str]:
    async with maker(settings)() as db:
        return set((await db.scalars(sa.select(BlobEmbed.blob_sha))).all())


async def owner(settings, user_id: str) -> BeeldOwner | None:
    async with maker(settings)() as db:
        return await db.get(BeeldOwner, uuid.UUID(user_id))


def code(r) -> str | None:
    """De code van een fout ({"detail": {"code": …}})."""
    detail = r.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


async def zoek(client, headers, q: str = "", *, like: str | None = None, **params) -> Any:
    query: dict[str, Any] = {"mode": "image", **params}
    if q:
        query["q"] = q
    if like is not None:
        query["like"] = like
    return await client.get("/api/v1/search", params=query, headers=headers)


async def ids(client, headers, q: str = "", **params) -> list[str]:
    r = await zoek(client, headers, q, **params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "image" and body["fallback"] is None, body
    return [hit["node"]["id"] for hit in body["results"]]
