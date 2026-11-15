"""app.cli analyse-bench en analyse-kwaliteit met de nep-worker (fase 6, X2b; meten.py).

De bench stuurt gemaakte foto's door faces (eerst één batch om op te warmen), schrijft alleen
getallen naar setting en /beheer toont ze, met de geschatte tijd voor wat wacht. De kwaliteit
houdt per deel de gezichten met een ja open en kijkt of group de goede persoon voorstelt; een
open gezicht met een nee telt als fout als group de afgewezen persoon weer voorstelt. Niets
daarvan zet een id of naam op stdout. Met de analyse uit: tests/test_cli_db.py.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.models import Face, Setting
from app.people.faces import meten, register
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"
UUID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}")


async def test_de_bench_meet_en_beheer_toont_het(settings, nep, capsys):
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        await session.commit()
    await foto(settings, nep, user.id, "wacht.jpg", [(A, 1)])
    await aan(settings, user.id)  # faces.plan staat klaar, de foto wacht nog

    assert await meten._run_bench(settings, 32) == 0
    out = capsys.readouterr().out
    assert nep.seen == 16 + 32  # één batch opwarmen, dan 32 foto's
    assert "analyse-bench: 32 gemaakte foto's in batches van 16:" in out
    assert "per extra gezicht: niet gemeten (gemaakte foto's hebben geen gezicht)" in out
    assert "user" not in out and not UUID.search(out)
    async with maker(settings)() as session:
        found = await meten.stored(session, meten.BENCH)
    assert set(found) == {
        "at", "version", "photos", "batch", "ms_per_photo", "photos_per_minute", "faces"
    }  # fmt: skip
    assert (found["photos"], found["batch"], found["faces"]) == (32, 16, 0)
    assert found["photos_per_minute"] > 0

    await draai(settings)  # plan en scan: de foto wacht dan niet meer
    async with maker(settings)() as session:
        block = await register.admin_block(session)
    assert block.bench is not None and block.bench.photos_per_minute == found["photos_per_minute"]
    assert block.queue.waiting == 0 and block.eta_s is None
    assert block.quality is None  # "niet gemeten"
    assert meten.eta_s(found, 30) == round(30 * 60 / found["photos_per_minute"])


@pytest.fixture
async def eva(client, settings, nep):
    """Eva met twaalf foto's: persoon 1 op elke foto, persoon 2 op acht, persoon 3 op vier, allemaal
    met een ja (24 ≥ MIN_JA); en één open foto met persoon 1, die ze bij Oma afwees."""
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        await session.commit()
    fotos = []
    for i in range(12):
        other = 2 if i < 8 else 3
        fotos.append(await foto(settings, nep, user.id, f"{i}.jpg", [(A, 1), (B, other)]))
    open_ = await foto(settings, nep, user.id, "open.jpg", [(A, 1)])
    await aan(settings, user.id)
    await draai(settings)
    h = await login(client, "eva@example.nl")
    async with maker(settings)() as session:
        rows = list(await session.execute(select(Face.id, Face.node_id, Face.x)))
    left = [r.id for r in rows if r.node_id in fotos and r.x == A[0]]
    right = {r.node_id: r.id for r in rows if r.node_id in fotos and r.x == B[0]}
    for name, faces in (
        ("Oma", left),
        ("Opa", [right[n] for n in fotos[:8]]),
        ("Tante", [right[n] for n in fotos[8:]]),
    ):
        r = await client.post(
            f"{F}/confirm", json={"faces": [str(f) for f in faces], "name": name}, headers=h
        )
        assert r.status_code == 204, r.text
    people = (await client.get("/api/v1/people", headers=h)).json()
    oma = next(p["id"] for p in people["people"] if p["name"] == "Oma")
    nee = next(r.id for r in rows if r.node_id == open_)
    r = await client.post(f"{F}/reject", json={"faces": [str(nee)], "person_id": oma}, headers=h)
    assert r.status_code == 204, r.text
    return user


async def test_de_kwaliteit_tegen_de_eigen_ja_en_nee(settings, nep, eva, capsys):
    before = nep.groups
    code = await meten._run_quality(settings)
    out = capsys.readouterr().out
    assert nep.groups - before == meten.VOUWEN + 2  # de delen, de nee, de groepen
    async with maker(settings)() as session:
        found = await meten.stored(session, meten.QUALITY)
    assert (found["owners"], found["tested"]) == (1, 24)
    # De nep geeft per persoon vectoren dicht bij elkaar: elk deel krijgt de goede persoon
    assert (found["precision"], found["coverage"]) == (round(24 / 25, 4), 1.0)
    assert (found["nee_tested"], found["nee_wrong"]) == (1, 1)  # zonder de afwijzing: weer Oma
    assert (found["purity"], found["recall"]) == (1.0, 1.0)
    assert found["ok"] is True and code == 0
    assert "precisie 96.0% (poort 95%), dekking 100.0%" in out
    assert "met een nee: 1 van 1 opnieuw de afgewezen persoon" in out
    assert "poorten: gehaald" in out
    for word in ("Oma", "Opa", "Tante", "eva"):
        assert word not in out
    assert not UUID.search(out)
    async with maker(settings)() as session:
        block = await register.admin_block(session)
    assert block.quality is not None and block.quality.ok is True


async def test_te_weinig_ja_meet_niets(settings, nep):
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        await session.commit()
    await foto(settings, nep, user.id, "1.jpg", [(A, 1)])
    await aan(settings, user.id)
    await draai(settings)
    with pytest.raises(meten.NotMeasured) as found:
        await meten.quality(settings)
    assert str(found.value) == (
        f"te weinig om te meten: niemand met gezichten aan heeft {meten.MIN_JA} gezichten met"
        " een ja"
    )
    async with maker(settings)() as session:
        assert await session.get(Setting, meten.QUALITY) is None
