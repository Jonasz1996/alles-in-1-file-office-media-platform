"""De stap embed en write_embed() (bouwontwerp fase 6, X3; Keuzes 69, 76, 77; K2-07; X3b).

- write_embed() is de enige schrijver van blob_embed (een AST- en tekstscan van app/, zoals die
  van write_text in tests/search/test_search_no_replace.py), eerst een SELECT, dan een gewone
  INSERT of één UPDATE, en zet gen + 1 voor elke eigenaar van een beeldbestand met die inhoud;
- eligible: alleen beelden; run_batch: alleen een eigenaar met beeld aan die hem zelf uploadde,
  of door iemand met beeld aan (Keuze 69; de link: test_beeld_events.test_beeld_other_uploader);
- pas na de backfill van de index (K2-07), niet bij ≥ 2 omzettingen (Keuze 50), niet zonder
  container of model (Defer, de batch terug op wait zonder poging);
- de preview van 256 als fd, nooit een pad (EmbedImageArgs is leeg);
- batches van 16, om de beurt per eigenaar (Keuze 20);
- no_preview, en na preview.ready weer op wait;
- wat de container per foto zegt (image_bad, unreadable, too_big: meteen failed; een lege
  vector: een nieuwe poging).

Een nieuwe versie van de stap zet alles terug: test_beeld_events.test_beeld_version.
"""

from __future__ import annotations

import ast
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from app import analysis, jobs, previews
from app.analyse import monitor, wire
from app.analyse.wire import Op
from app.analysis import Step
from app.beeld import jobs as bjobs
from app.beeld import types as vt
from app.models import Analysis, BeeldOwner, BlobEmbed, Job
from app.search.types import IndexProgress
from app.storage import BlobStore
from tests.analyse.fake_worker import FakeRequest
from tests.beeld import nep
from tests.beeld.nep import foto
from tests.search.test_search_no_replace import _functies, _naam, _tabel

pytestmark = pytest.mark.anyio

APP = Path(__file__).resolve().parents[2] / "app"
SCHRIJVER = ("beeld/jobs.py", "write_embed")
SQL_SCHRIJFT = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE(?:\s+OR\s+\w+)?|REPLACE\s+INTO)\s+"
    r"""(?:main\.)?["'`\[]?blob_embed\b""",
    re.I,
)
SCHRIJFT = {"insert", "update", "sqlite_insert", "pg_insert"}


# --- write_embed is de enige schrijver ---------------------------------------------------------


def _schrijft_blob_embed(n: ast.Call) -> bool:
    """insert(BlobEmbed), update(BlobEmbed.__table__), BlobEmbed.__table__.insert(),
    BlobEmbed(…) en table("blob_embed").update()."""
    naam = _naam(n.func)
    if naam == "BlobEmbed":
        return True
    if naam not in SCHRIJFT:
        return False
    if n.args and _tabel(n.args[0]) == "blob_embed":
        return True
    if isinstance(n.func, ast.Attribute):
        return _tabel(n.func.value) == "blob_embed"
    return False


def scan(bron: str, pad: str) -> list[str]:
    """Wie blob_embed schrijft buiten write_embed (pad: relatief aan app/)."""
    fouten: list[str] = []
    tree = ast.parse(bron)
    in_functie = _functies(tree)

    def mag(n: ast.AST) -> bool:
        return pad == SCHRIJVER[0] and in_functie.get(n) == SCHRIJVER[1]

    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if SQL_SCHRIJFT.search(n.value) and not mag(n):
                fouten.append(f"{pad}:{n.lineno}: blob_embed buiten write_embed (SQL)")
        if isinstance(n, ast.Call) and _schrijft_blob_embed(n) and not mag(n):
            fouten.append(f"{pad}:{n.lineno}: blob_embed buiten write_embed")
    return sorted(set(fouten))


FOUT = [
    "db.execute(insert(BlobEmbed).values(blob_sha=sha))",
    "db.execute(BlobEmbed.__table__.insert().values(blob_sha=sha))",
    "db.execute(update(BlobEmbed).where(BlobEmbed.blob_sha == sha).values(vec=b''))",
    "db.execute(BlobEmbed.__table__.update().values(vec=b''))",
    "db.execute(sqlite_insert(BlobEmbed.__table__).values(v))",
    "db.add(BlobEmbed(blob_sha=sha, vec=b''))",
    'db.execute(text("INSERT INTO blob_embed (blob_sha) VALUES (:s)"))',
    'db.execute(text("update blob_embed set vec = :v"))',
    'db.execute(table("blob_embed").update().values(vec=b""))',
]
GOED = [
    "db.execute(BlobEmbed.__table__.delete().where(BlobEmbed.blob_sha.in_(x)))",
    "db.execute(select(BlobEmbed.vec).where(BlobEmbed.blob_sha == sha))",
    'db.execute(text("DELETE FROM blob_embed WHERE blob_sha = :s"))',
    'db.execute(text("SELECT n.num, e.vec FROM node AS n JOIN blob_embed AS e ON 1"))',
    '"""write_embed() is de enige schrijver van blob_embed."""',
    "db.execute(BlobText.__table__.insert().values(blob_sha=sha))",
]


@pytest.mark.parametrize("code", FOUT)
def test_de_scanner_ziet_het(code):
    assert scan(code, "x.py"), code


@pytest.mark.parametrize("code", GOED)
def test_de_scanner_laat_dit_staan(code):
    assert scan(code, "x.py") == [], code


def test_alleen_write_embed_schrijft_blob_embed():
    code = (
        "async def write_embed(db, sha):\n"
        "    await db.execute(BlobEmbed.__table__.insert().values(blob_sha=sha))\n"
        "async def iets_anders(db, sha):\n"
        "    await db.execute(BlobEmbed.__table__.update().values(vec=b''))\n"
    )
    assert [f.split(": ", 1)[1] for f in scan(code, SCHRIJVER[0])] == [
        "blob_embed buiten write_embed"
    ]
    assert len(scan(code, "beeld/search.py")) == 2


def test_niets_in_app():
    bestanden = sorted(APP.rglob("*.py"))
    assert len(bestanden) > 100
    fouten = []
    for pad in bestanden:
        fouten += scan(pad.read_text(encoding="utf-8"), str(pad.relative_to(APP)))
    assert fouten == []


def test_write_embed_bestaat_en_schrijft():
    """De regel heeft zin: write_embed staat er, en schrijft blob_embed echt."""
    tree = ast.parse((APP / SCHRIJVER[0]).read_text(encoding="utf-8"))
    functies = [
        f
        for f in ast.walk(tree)
        if isinstance(f, ast.AsyncFunctionDef | ast.FunctionDef) and f.name == SCHRIJVER[1]
    ]
    assert len(functies) == 1
    namen = {_naam(n.func) for n in ast.walk(functies[0]) if isinstance(n, ast.Call)}
    assert {"insert", "update", "scalar"} <= namen
    assert scan(ast.unparse(functies[0]), SCHRIJVER[0]) == []


async def _gens(settings, *user_ids: str) -> list[int]:
    async with nep.maker(settings)() as db:
        out = []
        for user_id in user_ids:
            row = await db.get(BeeldOwner, uuid.UUID(user_id), populate_existing=True)
            out.append(-1 if row is None else row.gen)
        return out


async def test_write_embed_een_rij_en_gen_van_elke_eigenaar(client, mensen, beeld):
    """Eva en Bram hebben dezelfde foto (Bram met beeld uit): write_embed zet één rij, een
    tweede keer dezelfde rij met de nieuwe vector, en elke keer gen + 1 voor allebei."""
    (eva, eva_id), (bram, bram_id) = mensen
    fake, settings = beeld
    data = nep.photo(nep.KLEUREN["rood"])
    await nep.upload(client, eva, "rood.jpg", data)
    node = await nep.upload(client, bram, "ook rood.jpg", data)
    sha = await nep.sha_of(settings, node["id"])
    voor = await _gens(settings, eva_id, bram_id)
    eerste = nep.pack(nep.vector((1, 0, 0)))
    tweede = nep.pack(nep.vector((0, 1, 0)))
    async with nep.maker(settings)() as db:
        await bjobs.write_embed(db, sha, eerste)
        await db.commit()
        rij = await db.scalar(sa.select(BlobEmbed.id).where(BlobEmbed.blob_sha == sha))
    assert await _gens(settings, eva_id, bram_id) == [g + 1 for g in voor]
    async with nep.maker(settings)() as db:
        await bjobs.write_embed(db, sha, tweede)
        await db.commit()
        rijen = (await db.execute(sa.select(BlobEmbed.id, BlobEmbed.vec, BlobEmbed.version))).all()
    assert rijen == [(rij, tweede, analysis.VERSIONS[Step.EMBED])]
    assert await _gens(settings, eva_id, bram_id) == [g + 2 for g in voor]
    async with nep.maker(settings)() as db:
        with pytest.raises(ValueError):
            await bjobs.write_embed(db, sha, eerste[:-2])


def test_eligible_alleen_beelden():
    assert bjobs.eligible("x", "image/jpeg")
    assert bjobs.eligible("x", "IMAGE/PNG; q=1")
    for mime_type in ("application/pdf", "video/mp4", "text/plain", "", None):
        assert not bjobs.eligible("x", mime_type), mime_type


# --- de stap -----------------------------------------------------------------------------------


async def _state(settings, sha: str) -> tuple[str, str | None, int] | None:
    async with nep.maker(settings)() as db:
        row = (
            await db.execute(
                sa.select(Analysis.state, Analysis.code, Analysis.tries).where(
                    Analysis.blob_sha == sha, Analysis.step == Step.EMBED.value
                )
            )
        ).first()
    return None if row is None else (row[0], row[1], row[2])


async def test_alleen_een_eigenaar_met_beeld_aan_en_een_preview(client, mensen, beeld):
    """Bram heeft beeld uit: skip. Eva's foto zonder preview: none met no_preview, na
    preview.ready terug op wait en dan done. Een document staat meteen op skip."""
    (eva, _), (bram, _) = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    van_bram = await foto(client, bram, settings, "groen.jpg", "groen", seed=1)
    zonder = await nep.upload(client, eva, "blauw.jpg", nep.photo(nep.KLEUREN["blauw"], seed=2))
    zonder["sha"] = await nep.sha_of(settings, zonder["id"])
    doc = await nep.upload(client, eva, "rood.txt", b"rood")
    doc["sha"] = await nep.sha_of(settings, doc["id"])
    await nep.backfill(settings)
    assert await nep.pump(settings) == 3
    assert await _state(settings, rood["sha"]) == ("done", None, 0)
    assert await _state(settings, van_bram["sha"]) == ("skip", None, 0)
    assert await _state(settings, zonder["sha"]) == ("none", "no_preview", 0)
    assert await _state(settings, doc["sha"]) == ("skip", None, 0)  # eligible: geen foto
    assert fake.embeds == [1]
    # de preview komt: preview.ready zet hem terug, en dan heeft hij zijn vector
    nep.preview(settings, zonder["sha"], nep.KLEUREN["blauw"])
    await previews.announce(nep.maker(settings), zonder["sha"])
    assert await _state(settings, zonder["sha"]) == ("wait", None, 0)
    assert await nep.pump(settings) == 1
    assert await _state(settings, zonder["sha"]) == ("done", None, 0)
    assert await nep.vectors(settings) == {rood["sha"], zonder["sha"]}
    # preview.ready van een foto die al klaar is, verandert niets
    await previews.announce(nep.maker(settings), rood["sha"])
    assert await _state(settings, rood["sha"]) == ("done", None, 0)


async def _een_foto_in_de_rij(client, eva, settings) -> dict:
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    await nep.backfill(settings)
    assert await _state(settings, rood["sha"]) == ("wait", None, 0)
    return rood


async def _wijkt(settings, seconds: float, sha: str, fake) -> None:
    voor = len(fake.embeds)
    with pytest.raises(jobs.Defer) as defer:
        await analysis.pump(settings, bjobs.LANE)
    assert defer.value.seconds == seconds
    assert await _state(settings, sha) == ("wait", None, 0)
    assert len(fake.embeds) == voor


async def test_pas_na_de_backfill_van_de_index(client, mensen, beeld, monkeypatch):
    """K2-07: zolang de backfill van de analyse klaarstaat, of de index nog veel open heeft,
    wacht beeld (INDEX_DEFER_S); daarna gaat hij."""
    (eva, _), _ = mensen
    fake, settings = beeld
    rood = await _een_foto_in_de_rij(client, eva, settings)
    async with nep.maker(settings)() as db:
        await jobs.enqueue(db, analysis.BACKFILL, {"after": ""})
        await db.commit()
    await _wijkt(settings, bjobs.INDEX_DEFER_S, rood["sha"], fake)
    async with nep.maker(settings)() as db:
        await db.execute(sa.delete(Job).where(Job.type == analysis.BACKFILL))
        await db.commit()

    gevraagd: list[Step] = []
    echt = analysis.indexing

    async def bezig(db, step: Step = Step.TEXT):
        gevraagd.append(step)
        return IndexProgress(done=0.4) if step == Step.TEXT else await echt(db, step)

    monkeypatch.setattr(analysis, "indexing", bezig)
    await _wijkt(settings, bjobs.INDEX_DEFER_S, rood["sha"], fake)
    assert Step.TEXT in gevraagd
    monkeypatch.setattr(analysis, "indexing", echt)
    assert await nep.pump(settings) == 1
    assert await _state(settings, rood["sha"]) == ("done", None, 0)


async def test_wijkt_bij_twee_omzettingen(client, mensen, beeld, monkeypatch):
    """Keuze 50: bij index_defer_transcodes (2) lopende omzettingen wacht de rij analyse
    (analysis.busy_elsewhere, DEFER_S); bij één gaat hij gewoon."""
    from app.media import admin as media_admin

    (eva, _), _ = mensen
    fake, settings = beeld
    rood = await _een_foto_in_de_rij(client, eva, settings)
    met_media = settings.model_copy(update={"media_enabled": True})
    assert met_media.index_defer_transcodes == 2
    monkeypatch.setattr(media_admin, "transcodes_running", lambda: 2)
    await _wijkt(met_media, analysis.DEFER_S, rood["sha"], fake)
    monkeypatch.setattr(media_admin, "transcodes_running", lambda: 1)
    assert await nep.pump(met_media) == 1
    assert await _state(settings, rood["sha"]) == ("done", None, 0)


async def test_wijkt_zonder_container_en_zonder_model(client, mensen, beeld):
    """Zonder container DEFER_DOWN, zonder beeldmodel DEFER_MODEL, met de beheerder die beeld
    uit heeft OFF_DEFER_S: de batch terug op wait, zonder poging en zonder embed_image."""
    (eva, _), _ = mensen
    fake, settings = beeld
    rood = await _een_foto_in_de_rij(client, eva, settings)
    monitor._status = monitor.AnalyseStatus("down", "gone")
    await _wijkt(settings, monitor.DEFER_DOWN, rood["sha"], fake)
    monitor._status = monitor.OFF
    await _wijkt(settings, monitor.DEFER_DOWN, rood["sha"], fake)
    fake.fake.beeld = None
    monitor._status = await monitor.check(settings)
    assert monitor.status().state == "ok"
    await _wijkt(settings, monitor.DEFER_MODEL, rood["sha"], fake)
    fake.install()
    monitor._status = await monitor.check(settings)
    async with nep.maker(settings)() as db:
        await bjobs.set_admin(db, False)
        await db.commit()
    await _wijkt(settings, bjobs.OFF_DEFER_S, rood["sha"], fake)
    async with nep.maker(settings)() as db:
        await bjobs.set_admin(db, True)
        await db.commit()
    assert await nep.pump(settings) == 1
    assert await _state(settings, rood["sha"]) == ("done", None, 0)


async def test_de_preview_als_fd_nooit_een_pad(client, mensen, beeld):
    """embed_image krijgt de 256.webp als fd (dezelfde inode), nooit de blob; zijn args zijn
    leeg (geen pad, geen naam) en de uitvoer is een memfd."""
    (eva, _), _ = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    gezien: list[tuple[dict[str, Any], list[int], str]] = []

    async def kijk(req: FakeRequest):
        assert isinstance(req.args, wire.EmbedImageArgs)
        gezien.append(
            (
                req.args.model_dump(),
                [os.fstat(fd).st_ino for fd in req.previews],
                os.readlink(f"/proc/self/fd/{req.extra['uit']}"),
            )
        )
        return await fake._embed(req)

    fake.fake.on(Op.EMBED_IMAGE, kijk)
    await nep.backfill(settings)
    assert await nep.pump(settings) == 1
    store = BlobStore(settings.blob_dir)
    preview = previews.directory(store, rood["sha"]) / bjobs.PREVIEW
    assert [(args, inodes) for args, inodes, _ in gezien] == [({}, [preview.stat().st_ino])]
    assert gezien[0][2].startswith("/memfd:uit"), gezien[0][2]
    assert preview.stat().st_ino != store.path(rood["sha"]).stat().st_ino
    assert await nep.vectors(settings) == {rood["sha"]}


async def test_batches_van_16_om_de_beurt_per_eigenaar(client, mensen, beeld):
    """Bram zet eerst 4 foto's neer, daarna Eva 20: de eerste batch van 16 heeft die van Bram
    er allemaal in (om de beurt, Keuze 20), de tweede de rest."""
    (eva, _), (bram, _) = mensen
    fake, settings = beeld
    assert settings.beeld_batch == 16
    for h in (eva, bram):
        await nep.beeld_aan(client, h)
    await nep.run_jobs(settings)
    kleuren = list(nep.KLEUREN)
    van_bram = [
        await foto(client, bram, settings, f"b{i}.jpg", kleuren[i % 5], seed=100 + i)
        for i in range(4)
    ]
    van_eva = [
        await foto(client, eva, settings, f"e{i}.jpg", kleuren[i % 5], seed=i) for i in range(20)
    ]
    await nep.backfill(settings)
    assert await analysis.pump(settings, bjobs.LANE) == 16
    assert fake.embeds == [16]
    for node in van_bram:
        assert await _state(settings, node["sha"]) == ("done", None, 0)
    klaar = [node for node in van_eva if (await _state(settings, node["sha"]))[0] == "done"]
    assert len(klaar) == 12
    assert await nep.pump(settings) == 8
    assert fake.embeds == [16, 8]
    assert len(await nep.vectors(settings)) == 24


async def test_wat_de_container_per_foto_zegt(client, mensen, beeld):
    """image_bad, unreadable en too_big: meteen failed (geen nieuwe poging); ok met een lege
    vector telt niet: failed internal, later opnieuw; de rest krijgt zijn vector."""
    (eva, _), _ = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    kleuren = list(nep.KLEUREN)
    nodes = [await foto(client, eva, settings, f"f{i}.jpg", kleuren[i], seed=i) for i in range(5)]
    fake.codes = {0: "image_bad", 1: "unreadable", 2: "too_big"}
    echt = fake._embed

    async def leeg_op_drie(req: FakeRequest):
        data = await echt(req)
        os.pwrite(req.extra["uit"], bytes(vt.DIM * 2), 3 * vt.DIM * 2)
        return data

    fake.fake.on(Op.EMBED_IMAGE, leeg_op_drie)
    await nep.backfill(settings)
    assert await analysis.pump(settings, bjobs.LANE) == 5
    fake.codes = {}
    states = sorted([await _state(settings, node["sha"]) for node in nodes], key=str)
    assert states == sorted(
        [
            ("failed", "image_bad", 1),
            ("failed", "unreadable", 1),
            ("failed", "too_big", 1),
            ("wait", "internal", 1),
            ("done", None, 0),
        ],
        key=str,
    )
    assert len(await nep.vectors(settings)) == 1
    async with nep.maker(settings)() as db:
        later = await db.scalar(
            sa.select(Analysis.queued_at).where(
                Analysis.step == Step.EMBED.value, Analysis.state == "wait"
            )
        )
    assert later is not None
    assert later.replace(tzinfo=None) > datetime.now(UTC).replace(tzinfo=None)
