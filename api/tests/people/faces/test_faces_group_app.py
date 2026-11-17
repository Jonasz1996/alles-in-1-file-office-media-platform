"""Groeperen en voorstellen, de kant van de app (fase 6, Keuze 62; X2b), met de nep van group (de
referentie in puur Python, tests/analyse/groep_ref.py).

- na een batch: voorstellen voor de nieuwe gezichten (suggest, alleen die);
- full: face_owner.run één omhoog, de groepen heten "<run>:<cluster>"; een oude run is 409
  faces_gone; hooguit één keer per uur vanzelf, en de ronde van elk uur haalt in wat binnen het
  uur kwam;
- een afwijzing (nee) gaat mee: nooit dat voorstel;
- de uitkomst klopt niet (grootte): bad_output, niets geschreven, geen nieuwe poging;
- schrijven in stukken (CHUNK): dezelfde uitkomst als in één keer.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.analyse import wire
from app.models import Face, FaceOwner, Job
from app.people.faces import group
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"


@pytest.fixture
async def eva(client, settings, nep):
    """Eva: vier foto's met persoon 1 (en op twee ook persoon 2), en een met persoon 3."""
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        await session.commit()
    ids = {"id": user.id}
    ids["fotos"] = [
        await foto(settings, nep, user.id, f"een-{i}.jpg", [(A, 1)] + ([(B, 2)] if i < 2 else []))
        for i in range(4)
    ]
    ids["ander"] = await foto(settings, nep, user.id, "ander.jpg", [(A, 3)])
    await aan(settings, user.id)
    await draai(settings)
    ids["h"] = await login(client, "eva@example.nl")
    return ids


async def run_of(settings, owner) -> int:
    async with maker(settings)() as session:
        row = await session.get(FaceOwner, owner)
        return 0 if row is None else row.run


async def test_full_maakt_groepen_met_de_run_in_de_sleutel(client, settings, nep, eva):
    before = await run_of(settings, eva["id"])
    assert await group.run_group(settings, eva["id"], "full") == 7
    assert await run_of(settings, eva["id"]) == before + 1
    r = await client.get(f"{F}/groups", headers=eva["h"])
    assert r.status_code == 200
    groups = r.json()["groups"]
    # Persoon 1 (vier keer) is een groep; persoon 2 (twee) en 3 (een) zijn te klein
    assert [g["count"] for g in groups] == [4]
    key = groups[0]["key"]
    run, cluster = key.split(":")
    assert int(run) == before + 1
    r = await client.get(f"{F}/groups/{key}", headers=eva["h"])
    assert r.status_code == 200 and len(r.json()["faces"]) == 4
    assert {f["node_id"] for f in r.json()["faces"]} == {str(n) for n in eva["fotos"]}
    status = (await client.get(f"{F}/status", headers=eva["h"])).json()
    assert status["faces"]["groups"] == 1
    # Een nieuwe ronde: de oude sleutel is weg
    await group.run_group(settings, eva["id"], "full")
    for old in (key, "x", "1", f"{run}:abc", f"{int(run) + 5}:{cluster}"):
        r = await client.get(f"{F}/groups/{old}", headers=eva["h"])
        assert r.status_code == 409, old
        assert r.json()["detail"]["code"] == "faces_gone"


async def test_voorstellen_voor_wie_al_bevestigd_is(client, settings, nep, eva):
    """Eén gezicht van persoon 1 als Oma: de andere drie krijgen Oma als voorstel (sim, zeker);
    persoon 2 en 3 niet. Afgewezen: nooit meer dat voorstel."""
    eerste = await gezichten_van(settings, eva["fotos"][0])
    oma_face = eerste[0]
    r = await client.post(
        f"{F}/confirm", json={"faces": [str(oma_face.id)], "name": "Oma"}, headers=eva["h"]
    )
    assert r.status_code == 204
    oma = (await client.get("/api/v1/people", headers=eva["h"])).json()["people"][0]["id"]
    await group.run_group(settings, eva["id"], "suggest")
    r = await client.get(f"{F}/suggestions", headers=eva["h"])
    people = r.json()["people"]
    assert [(p["person"]["name"], p["count"]) for p in people] == [("Oma", 3)]
    assert all(f["suggest"]["sure"] and f["suggest"]["why"] == "sim" for f in people[0]["sample"])
    r = await client.get(f"{F}/suggestions", params={"person": oma, "limit": 2}, headers=eva["h"])
    page = r.json()
    assert len(page["faces"]) == 2 and page["cursor"] == "2"
    r = await client.get(
        f"{F}/suggestions", params={"person": oma, "cursor": "2"}, headers=eva["h"]
    )
    assert len(r.json()["faces"]) == 1 and r.json()["cursor"] is None
    # Nee voor één ervan: hij krijgt Oma nooit meer
    nee = page["faces"][0]["id"]
    r = await client.post(f"{F}/reject", json={"faces": [nee], "person_id": oma}, headers=eva["h"])
    assert r.status_code == 204
    await group.run_group(settings, eva["id"], "full")
    async with maker(settings)() as session:
        row = await session.get(Face, uuid.UUID(nee))
        assert row.suggest_id != uuid.UUID(oma)
    r = await client.get(f"{F}/suggestions", headers=eva["h"])
    assert [p["count"] for p in r.json()["people"]] == [2]


async def gezichten_van(settings, node) -> list[Face]:
    async with maker(settings)() as session:
        return list(
            await session.scalars(select(Face).where(Face.node_id == node).order_by(Face.x))
        )


async def test_na_een_batch_alleen_de_nieuwe_gezichten(settings, nep, eva, fake):
    """De scan stuurt na een batch alleen de nieuwe gezichten naar group (suggest)."""
    seen: list[tuple[str, int]] = []
    real = nep.group

    async def spy(req):
        data = os.pread(req.extra["in"], wire.GROUP_HEAD.size, 0)
        head = wire.GROUP_HEAD.unpack(data)
        seen.append((req.args.mode, head[1] - head[2]))
        return await real(req)

    fake.on(wire.Op.GROUP, spy)
    await foto(settings, nep, eva["id"], "nieuw.jpg", [(A, 1), (B, 3)])
    await aan(settings, eva["id"])
    await draai(settings)
    assert seen and seen[0] == ("suggest", 2)


async def test_full_hooguit_een_keer_per_uur(settings, nep, eva):
    async with maker(settings)() as session:
        row = await session.get(FaceOwner, eva["id"])
        row.grouped_at = None
        await session.execute(Job.__table__.delete().where(Job.type == group.GROUP))
        await session.commit()
    await group.maybe_full(settings, eva["id"])
    async with maker(settings)() as session:
        modes = [
            p["mode"]
            for p in await session.scalars(select(Job.payload).where(Job.type == group.GROUP))
        ]
    assert modes == ["full"]
    await draai(settings)
    async with maker(settings)() as session:
        await session.execute(Job.__table__.delete().where(Job.type == group.GROUP))
        await session.commit()
    await group.maybe_full(settings, eva["id"])
    async with maker(settings)() as session:
        assert list(await session.scalars(select(Job.payload).where(Job.type == group.GROUP))) == []


async def group_modes(settings) -> list[str]:
    """De rondes van group die klaarstaan."""
    async with maker(settings)() as session:
        waiting = select(Job.payload).where(Job.type == group.GROUP, Job.status == "queued")
        return [p["mode"] for p in await session.scalars(waiting)]


async def test_de_ronde_van_elk_uur_groepeert_wat_binnen_het_uur_kwam(settings, nep, eva):
    """Een batch binnen het uur na een full zet geen full klaar (maybe_full). De ronde van elk uur
    (register.hourly) doet dat wel zodra het uur om is, en alleen voor wie gezichten heeft van na
    zijn laatste full."""
    from app.people.faces import register

    voor = await run_of(settings, eva["id"])
    assert voor >= 1
    await foto(settings, nep, eva["id"], "later.jpg", [(A, 1)])
    await aan(settings, eva["id"])
    await draai(settings)
    assert await run_of(settings, eva["id"]) == voor
    # Binnen het uur: ook de ronde van elk uur wacht
    await register.hourly(settings)
    assert await group_modes(settings) == []
    # Het uur is om: full, met het nieuwe gezicht
    async with maker(settings)() as session:
        row = await session.get(FaceOwner, eva["id"])
        row.grouped_at = datetime.now(UTC) - timedelta(hours=2)
        await session.commit()
    await register.hourly(settings)
    assert await group_modes(settings) == ["full"]
    await draai(settings)
    assert await run_of(settings, eva["id"]) == voor + 1
    # Geen gezicht van na de laatste full: geen full, ook niet na het uur
    async with maker(settings)() as session:
        row = await session.get(FaceOwner, eva["id"])
        row.grouped_at = datetime.now(UTC) - timedelta(hours=2)
        await session.execute(
            update(Face)
            .where(Face.owner_id == eva["id"])
            .values(created_at=datetime.now(UTC) - timedelta(hours=3))
        )
        await session.commit()
    await register.hourly(settings)
    assert await group_modes(settings) == []


async def test_een_uitkomst_die_niet_klopt_schrijft_niets(settings, nep, eva, fake, caplog):
    async def liar(req):
        out = await nep.group(req)
        return wire.GroupData(n=out.n + 1)

    fake.on(wire.Op.GROUP, liar)
    run = await run_of(settings, eva["id"])
    with pytest.raises(group.BadOutput):
        await group.run_group(settings, eva["id"], "full")
    assert await run_of(settings, eva["id"]) == run
    # Als job: geen nieuwe poging, en in het log alleen de mode
    async with maker(settings)() as session:
        await session.execute(Job.__table__.delete())
        await group.queue_group(session, eva["id"], "full")
        await session.commit()
    with caplog.at_level(logging.WARNING, logger="platform.faces"):
        await draai(settings)
    async with maker(settings)() as session:
        rows = list(
            await session.execute(select(Job.status, Job.attempts).where(Job.type == group.GROUP))
        )
    assert [r.status for r in rows] == ["done"]
    assert [r.getMessage() for r in caplog.records if r.name == "platform.faces"] == [
        "faces.group: bad_output (full)"
    ]
    assert await run_of(settings, eva["id"]) == run


async def test_in_stukken_dezelfde_uitkomst(settings, nep, eva, monkeypatch):
    await group.run_group(settings, eva["id"], "full")
    async with maker(settings)() as session:
        heel = {
            f.id: (f.suggest_id, f.cluster is not None) for f in await session.scalars(select(Face))
        }
    monkeypatch.setattr(group, "CHUNK", 2)
    await group.run_group(settings, eva["id"], "full")
    async with maker(settings)() as session:
        stukken = {
            f.id: (f.suggest_id, f.cluster is not None) for f in await session.scalars(select(Face))
        }
    assert stukken == heel
    assert sum(1 for _s, c in heel.values() if c) == 4


async def test_druk_is_defer_zonder_poging(settings, nep, eva, fake):
    """Down of druk is Defer, geen poging; dat geldt ook voor group."""
    async with maker(settings)() as session:
        await session.execute(Job.__table__.delete())
        await group.queue_group(session, eva["id"], "full")
        await session.commit()
    fake.fail(wire.Op.GROUP, wire.Code.BUSY)
    await draai(settings)
    async with maker(settings)() as session:
        row = (
            await session.execute(select(Job.status, Job.attempts).where(Job.type == group.GROUP))
        ).one()
    assert (row.status, row.attempts) == ("queued", 0)
