"""python -m app.cli beeld-status, beeld-bench en beeld-kwaliteit (bouwontwerp fase 6, X3; BZ6,
BZ9, BZ10; §3.5; X3b).

- Met de analyse uit (een ontbrekende ANALYSE-regel is uit): alle drie code 1, een regel op
  stderr, niets op stdout en geen bestand onder de datamap.
- Met de analyse aan zonder container: beeld-status zegt down met de reden (code 0), de bench
  en de kwaliteit zeggen dat de container niet klaar is (code 1, geen bestand).
- Met de nep: de bench schrijft beeld-bench.json ({at, p95_ms, photos_per_minute}) atomisch,
  in batches van 16 en met een eigen eigenaar die daarna weg is; /beheer leest het. De
  kwaliteit met --personen schrijft {at, ok}; zonder --personen code 2.
- Nooit een vraag, een vector, een naam van een persoon of van een bestand op het scherm.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import uuid
from pathlib import Path

import anyio
import pytest

from app.beeld import admin as badmin
from app.beeld import cli as bcli
from app.models import Face, FacePerson, Person
from app.search.types import AnalyseQuality, BeeldBench
from tests.beeld import nep
from tests.beeld.nep import foto

pytestmark = pytest.mark.anyio

API_DIR = Path(__file__).resolve().parents[2]


async def _cli(settings, *args: str, analyse: bool) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_ANALYSE")}
    env.update(PLATFORM_DATA_DIR=str(settings.data_dir), PYTHONDONTWRITEBYTECODE="1")
    # Op PostgreSQL de database van deze test, en de analyse daar aan (ANALYSE_POSTGRES)
    pg = settings.database_url and not settings.db_url.startswith("sqlite")
    if pg:
        env.update(PLATFORM_DATABASE_URL=settings.database_url)
    if analyse:
        env.update(
            PLATFORM_ANALYSE_ENABLED="true",
            PLATFORM_ANALYSE_WORKER=str(settings.data_dir / "geen" / "worker.sock"),
            PLATFORM_ANALYSE_WORKER_KEY=secrets.token_hex(32),
        )
        if pg:
            env.update(PLATFORM_ANALYSE_POSTGRES="true")
    result = await anyio.run_process(
        [sys.executable, "-m", "app.cli", *args], cwd=API_DIR, env=env, check=False
    )
    return result.returncode, result.stdout.decode(), result.stderr.decode()


def _bestanden(root: Path) -> set[Path]:
    return {p.relative_to(root) for p in root.rglob("*")}


async def test_met_de_analyse_uit_niets(settings):
    voor = _bestanden(settings.data_dir)
    for args in (
        ("beeld-status",),
        ("beeld-bench", "--fotos", "10"),
        ("beeld-kwaliteit",),
        ("beeld-kwaliteit", "--personen"),
    ):
        code, out, err = await _cli(settings, *args, analyse=False)
        assert code == 1, (args, err)
        assert out == "", args
        assert "de analyse staat uit" in err, args
    na = _bestanden(settings.data_dir)
    assert na == voor, na - voor
    assert not badmin.results_dir(settings).exists() or not any(
        (badmin.results_dir(settings) / name).exists()
        for name in (badmin.BENCH_FILE, badmin.QUALITY_FILE)
    )


async def test_met_de_analyse_aan_zonder_container(settings):
    code, out, err = await _cli(settings, "beeld-status", analyse=True)
    assert code == 0, err
    regels = out.splitlines()
    assert regels[0].startswith("analyse: down ("), out
    assert "model: missing" in regels
    assert "foto's met een vector: 0 van 0" in regels
    for args in (("beeld-bench", "--fotos", "10"), ("beeld-kwaliteit", "--personen")):
        code, out, err = await _cli(settings, *args, analyse=True)
        assert (code, out) == (1, ""), (args, err)
        assert "De container is niet klaar voor beeld (down)." in err
    for name in (badmin.BENCH_FILE, badmin.QUALITY_FILE):
        assert not (badmin.results_dir(settings) / name).exists()


async def test_beeld_status_alleen_getallen(client, mensen, beeld, capsys):
    (eva, _), _ = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    for i, kleur in enumerate(("rood", "blauw", "groen")):
        await foto(client, eva, settings, f"vakantie-{kleur}.jpg", kleur, seed=i)
    fake.codes = {2: "image_bad"}
    await nep.embed_all(settings)
    fake.codes = {}
    capsys.readouterr()
    assert await bcli.status(settings) == 0
    out, err = capsys.readouterr()
    regels = out.splitlines()
    assert regels[:4] == [
        "analyse: ok",
        "model: ok",
        "beheerder: aan",
        "foto's met een vector: 2 van 3",
    ], out
    assert "mislukt (image_bad): 1" in regels
    assert "vakantie" not in out + err


async def test_beeld_bench_schrijft_het_bestand(client, beeld, capsys):
    fake, settings = beeld
    capsys.readouterr()
    assert await bcli.bench(settings, 30, 5, 20) == 0
    out, err = capsys.readouterr()
    # embed_image in batches van 16; dan opwarmen (k = 0) en 5 vragen met k = 1000
    assert fake.embeds == [16, 4]
    warm, *vragen = fake.ranks
    assert (warm.k, warm.text, warm.num, warm.gen) == (0, None, None, 1)
    assert [(a.k, a.num, a.owner) for a in vragen] == [(1000, None, warm.owner)] * 5
    assert {a.text for a in vragen} <= set(bcli.BENCH_QUERIES)
    # een eigen eigenaar: geen gebruiker, daarna vergeten, en geen bestand in de cache
    assert fake.forgets == [warm.owner]
    assert not (settings.beeld_dir / f"{warm.owner}.vec").exists()
    # het bestand, atomisch: alleen beeld-bench.json, geen tijdelijk bestand ernaast
    target = badmin.results_dir(settings) / badmin.BENCH_FILE
    data = json.loads(target.read_text())
    assert set(data) == {"at", "p95_ms", "photos_per_minute"}
    bench = BeeldBench.model_validate(data)
    assert bench.p95_ms >= 0 and bench.photos_per_minute > 0
    assert [p.name for p in badmin.results_dir(settings).iterdir() if p.is_file()] == [
        badmin.BENCH_FILE
    ]
    async with nep.maker(settings)() as db:
        block = await badmin.block(db)
    assert block.bench == bench
    # geen vraag op het scherm
    for vraag in bcli.BENCH_QUERIES:
        assert vraag not in out + err
    assert "over 5 vragen en 30 foto's" in out


async def test_write_result_atomisch(settings, monkeypatch):
    target = bcli.write_result(settings, badmin.BENCH_FILE, {"at": "oud"})
    assert json.loads(target.read_text()) == {"at": "oud"}
    assert target.stat().st_mode & 0o777 == 0o600

    def kapot(src, dst):
        raise OSError("schijf vol")

    monkeypatch.setattr(bcli.os, "replace", kapot)
    with pytest.raises(OSError):
        bcli.write_result(settings, badmin.BENCH_FILE, {"at": "nieuw"})
    assert json.loads(target.read_text()) == {"at": "oud"}
    assert [p.name for p in target.parent.iterdir() if p.is_file()] == [badmin.BENCH_FILE]


async def test_beeld_kwaliteit_zonder_personen(beeld, capsys):
    fake, settings = beeld
    assert await bcli.quality(settings, False, None) == 2
    _, err = capsys.readouterr()
    assert "beeld-model" in err
    assert not (badmin.results_dir(settings) / badmin.QUALITY_FILE).exists()
    assert fake.ranks == []


async def _personen(settings, eigenaar: str, groepen: dict[str, list[str]]) -> None:
    """Per persoon (naam) een gezicht met verdict ja op elk van deze nodes."""
    owner = uuid.UUID(eigenaar)
    async with nep.maker(settings)() as db:
        for naam, nodes in groepen.items():
            person = Person(owner_id=owner, name=naam, search_name=naam.lower())
            db.add(person)
            await db.flush()
            for node in nodes:
                face = Face(
                    node_id=uuid.UUID(node),
                    owner_id=owner,
                    x=0.1,
                    y=0.1,
                    w=0.2,
                    h=0.2,
                    score=0.9,
                    px=64,
                    version=1,
                )
                db.add(face)
                await db.flush()
                db.add(
                    FacePerson(face_id=face.id, person_id=person.id, owner_id=owner, verdict="ja")
                )
        await db.commit()


async def test_beeld_kwaliteit_met_personen(client, mensen, beeld, capsys):
    """Anna op de twee rode foto's, Bert op de twee blauwe, vier groene ernaast: "meer zoals
    deze" vindt de andere foto van dezelfde persoon altijd in de top 5 (toeval: 5/7)."""
    (eva, eva_id), (bram, _) = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    nodes = {}
    for i, (naam, kleur) in enumerate(
        (("r1", "rood"), ("r2", "rood"), ("b1", "blauw"), ("b2", "blauw"))
        + tuple((f"g{j}", "groen") for j in range(4))
    ):
        nodes[naam] = (await foto(client, eva, settings, f"strand-{naam}.jpg", kleur, seed=i))["id"]
    await foto(client, bram, settings, "van-bram.jpg", "rood", seed=50)
    await nep.embed_all(settings)
    await _personen(
        settings,
        eva_id,
        {
            "Anna": [nodes["r1"], nodes["r2"]],
            "Bert": [nodes["b1"], nodes["b2"]],
            "Carla": [nodes["g0"]],
        },
    )
    capsys.readouterr()
    assert await bcli.quality(settings, True, "eva@example.nl") == 0
    out, err = capsys.readouterr()
    data = json.loads((badmin.results_dir(settings) / badmin.QUALITY_FILE).read_text())
    assert set(data) == {"at", "ok"}
    assert AnalyseQuality.model_validate(data).ok is True
    # alleen met de num van de eigen foto's (Keuze 79), nooit een tekst
    asked = [a for a in fake.ranks if a.k > 0]
    assert len(asked) == 4
    assert {(a.text, a.k, a.owner) for a in asked} == {(None, bcli.TOP + 1, uuid.UUID(eva_id).hex)}
    assert "2 personen, 4 vragen" in out
    assert "1.000, bij toeval 0.714 (boven toeval)" in out
    for woord in ("Anna", "Bert", "Carla", "strand", "van-bram"):
        assert woord not in out + err
    async with nep.maker(settings)() as db:
        block = await badmin.block(db)
    assert block.quality is not None and block.quality.ok is True


async def test_beeld_kwaliteit_met_personen_zonder_paren(client, mensen, beeld, capsys):
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    await _personen(settings, eva_id, {"Anna": [rood["id"]]})
    assert await bcli.quality(settings, True, "eva@example.nl") == 1
    _, err = capsys.readouterr()
    assert "Geen bevestigde persoon met twee foto's of meer." in err
    assert not (badmin.results_dir(settings) / badmin.QUALITY_FILE).exists()
    assert await bcli.quality(settings, True, "niemand@example.nl") == 1


def test_same_person_rate():
    groepen = {"a": {1, 2}, "b": {3, 4}, "c": {5}}
    # perfect: de andere foto van dezelfde persoon staat bovenaan
    asked, rate, chance = bcli.same_person_rate(groepen, {1: [1, 2], 3: [3, 4], 5: [5, 1]}, 8)
    assert (asked, rate) == (2, 1.0)  # 5 telt niet: geen tweede foto
    assert chance == pytest.approx(5 / 7)
    # nooit: alleen andere foto's in de top 5
    asked, rate, _ = bcli.same_person_rate(groepen, {1: [1, 3, 4, 5, 6, 7]}, 8)
    assert (asked, rate) == (1, 0.0)
    # buiten de top 5 telt niet
    asked, rate, _ = bcli.same_person_rate(groepen, {1: [1, 3, 4, 5, 6, 7, 2]}, 20)
    assert (asked, rate) == (1, 0.0)
    assert bcli.same_person_rate(groepen, {}, 8) == (0, 0.0, 0.0)


def test_random_vecfile(tmp_path):
    from tests.beeld.test_vecfile import lees

    path = tmp_path / f"{uuid.uuid4().hex}.vec"
    bcli.random_vecfile(path, 7, 768)
    head, nums, vecs, shas = lees(path)
    assert (head.count, head.dim, head.gen) == (7, 768, 1)
    assert nums == list(range(1, 8))
    for vec in vecs:
        assert sum(x * x for x in nep.unpack(vec)) == pytest.approx(1.0, abs=1e-2)
