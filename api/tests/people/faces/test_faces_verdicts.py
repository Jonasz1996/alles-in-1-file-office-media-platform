"""Bevestigen, afwijzen, niet tonen, ongedaan maken, en de haken van Z6 (fase 6, Keuze 63; X2b).

Z6 blijft de bron van personen: een ja maakt photo_person (source 'auto', met het kader) via
sync_links; een nee of niet tonen haalt die rij weer weg; een rij met de hand (source 'hand')
raakt X2 nooit aan. Een kader met de hand (IoU ≥ 0,5) maakt het gezicht ja; één persoon zonder
kader en één gezicht wordt een voorstel (tag). Na elke keuze: verdict_at en faces.group suggest na
30 s. Eén persoon van de foto halen, samenvoegen en wissen (de haken van Z6).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Face, FaceOwner, FacePerson, Job, Node, PhotoPerson
from app.people.faces import group, state, verdicts, wipe
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"


@pytest.fixture
async def eva(client, settings, nep):
    """Eva met drie foto's: twee met persoon 1 en 2, een met alleen persoon 1. Gezichten aan en
    gescand; Bram met een eigen foto."""
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        await session.commit()
    ids = {"id": user.id, "bram": bram.id, "root": user.root_id}
    ids["een"] = await foto(settings, nep, user.id, "een.jpg", [(A, 1), (B, 2)])
    ids["twee"] = await foto(settings, nep, user.id, "twee.jpg", [(A, 1), (B, 2)])
    ids["drie"] = await foto(settings, nep, user.id, "drie.jpg", [(A, 1)])
    ids["bram_foto"] = await foto(settings, nep, bram.id, "bram.jpg", [(A, 1)])
    await aan(settings, user.id, bram.id)
    await draai(settings)
    ids["h"] = await login(client, "eva@example.nl")
    ids["h_bram"] = await login(client, "bram@example.nl")
    return ids


async def gezichten(settings, node) -> list[Face]:
    async with maker(settings)() as session:
        return list(
            await session.scalars(select(Face).where(Face.node_id == node).order_by(Face.x))
        )


async def oordelen(settings) -> set[tuple[uuid.UUID, uuid.UUID, str]]:
    async with maker(settings)() as session:
        rows = await session.execute(
            select(FacePerson.face_id, FacePerson.person_id, FacePerson.verdict)
        )
        return {(r.face_id, r.person_id, r.verdict) for r in rows}


async def links(settings, node) -> dict[uuid.UUID, tuple]:
    async with maker(settings)() as session:
        rows = await session.execute(select(PhotoPerson).where(PhotoPerson.node_id == node))
        return {
            p.person_id: (p.source, (p.x, p.y, p.w, p.h) if p.x is not None else None)
            for (p,) in rows
        }


async def post(client, h, path, expect=204, **body):
    r = await client.post(f"{F}/{path}", json=body, headers=h)
    assert r.status_code == expect, r.text
    return r


async def groep_jobs(settings) -> list[tuple[dict, datetime]]:
    async with maker(settings)() as session:
        rows = await session.execute(
            select(Job.payload, Job.run_after).where(
                Job.type == group.GROUP, Job.status == "queued"
            )
        )
        return [(r.payload, r.run_after) for r in rows]


async def test_bevestigen_met_een_nieuwe_naam(client, settings, eva):
    een = await gezichten(settings, eva["een"])
    await post(client, eva["h"], "confirm", faces=[str(een[0].id)], name="Oma")
    r = await client.get(f"/api/v1/files/{eva['een']}/faces", headers=eva["h"])
    out = {f["id"]: f for f in r.json()}
    oma = out[str(een[0].id)]["person"]
    assert oma["name"] == "Oma"
    assert out[str(een[0].id)]["suggest"] is None
    assert out[str(een[1].id)]["person"] is None
    # Z6 kent Oma, met deze foto en het kader van het gezicht
    people = (await client.get("/api/v1/people", headers=eva["h"])).json()["people"]
    assert [(p["name"], p["count"]) for p in people] == [("Oma", 1)]
    assert await links(settings, eva["een"]) == {uuid.UUID(oma["id"]): ("auto", A)}
    r = await client.get(f"/api/v1/files/{eva['een']}/people", headers=eva["h"])
    assert [p["person"]["name"] for p in r.json()["people"]] == ["Oma"]
    status = (await client.get(f"{F}/status", headers=eva["h"])).json()
    assert status["faces"] == {
        "found": 5,
        "named": 1,
        "open": 4,
        "groups": 0,
    }
    async with maker(settings)() as session:
        row = await session.get(FaceOwner, eva["id"])
        assert row is not None and row.verdict_at is not None


async def test_na_een_keuze_groeperen_na_30_seconden(client, settings, eva):
    een = await gezichten(settings, eva["een"])
    before = datetime.now(UTC)
    await post(client, eva["h"], "confirm", faces=[str(een[0].id)], name="Oma")
    found = [(p, at) for p, at in await groep_jobs(settings) if p["owner"] == eva["id"].hex]
    assert [p["mode"] for p, _at in found] == ["suggest"]
    at = found[0][1].replace(tzinfo=UTC) if found[0][1].tzinfo is None else found[0][1]
    assert at >= before + verdicts.group.VERDICT_WAIT - timedelta(seconds=1)
    # Twee keuzes kort na elkaar: nog steeds één taak
    await post(client, eva["h"], "confirm", faces=[str(een[1].id)], name="Opa")
    assert len([p for p, _ in await groep_jobs(settings) if p["owner"] == eva["id"].hex]) == 1


async def test_nee_dit_is_bram_in_een_stap(client, settings, eva):
    """Een ja voor Oma, dan bevestigen als Opa: Oma wordt nee, haar rij op de foto gaat weg."""
    een = await gezichten(settings, eva["een"])
    face = str(een[0].id)
    await post(client, eva["h"], "confirm", faces=[face], name="Oma")
    oma = (await client.get("/api/v1/people", headers=eva["h"])).json()["people"][0]["id"]
    await post(client, eva["h"], "confirm", faces=[face], name="Opa")
    people = {
        p["name"]: p["id"]
        for p in (await client.get("/api/v1/people", headers=eva["h"])).json()["people"]
    }
    opa = people["Opa"]
    assert await oordelen(settings) == {
        (een[0].id, uuid.UUID(oma), "nee"),
        (een[0].id, uuid.UUID(opa), "ja"),
    }
    assert await links(settings, eva["een"]) == {uuid.UUID(opa): ("auto", A)}


async def test_afwijzen_haalt_de_ja_en_het_voorstel_weg(client, settings, eva):
    een = await gezichten(settings, eva["een"])
    face = een[0].id
    await post(client, eva["h"], "confirm", faces=[str(face)], name="Oma")
    oma = uuid.UUID(
        (await client.get("/api/v1/people", headers=eva["h"])).json()["people"][0]["id"]
    )
    await post(client, eva["h"], "reject", faces=[str(face)], person_id=str(oma))
    assert await oordelen(settings) == {(face, oma, "nee")}
    assert await links(settings, eva["een"]) == {}
    # Een voorstel voor Oma op een ander gezicht: nee haalt het weg
    twee = await gezichten(settings, eva["twee"])
    async with maker(settings)() as session:
        row = await session.get(Face, twee[0].id)
        row.suggest_id, row.suggest_score, row.suggest_why = oma, 0.9, "sim"
        await session.commit()
    await post(client, eva["h"], "reject", faces=[str(twee[0].id)], person_id=str(oma))
    async with maker(settings)() as session:
        row = await session.get(Face, twee[0].id)
        assert (row.suggest_id, row.suggest_why) == (None, None)


async def test_een_rij_met_de_hand_blijft_altijd(client, settings, eva):
    """Oma met de hand op de foto (zonder kader); bevestigen en afwijzen laten die rij staan."""
    r = await client.post(
        f"/api/v1/files/{eva['een']}/people", json={"name": "Oma"}, headers=eva["h"]
    )
    assert r.status_code == 201
    oma = (await client.get("/api/v1/people", headers=eva["h"])).json()["people"][0]["id"]
    een = await gezichten(settings, eva["een"])
    await post(client, eva["h"], "confirm", faces=[str(een[0].id)], person_id=oma)
    assert await links(settings, eva["een"]) == {uuid.UUID(oma): ("hand", A)}
    await post(client, eva["h"], "reject", faces=[str(een[0].id)], person_id=oma)
    assert await links(settings, eva["een"]) == {uuid.UUID(oma): ("hand", A)}
    await post(client, eva["h"], "hide", faces=[str(een[0].id)])
    assert await links(settings, eva["een"]) == {uuid.UUID(oma): ("hand", A)}


async def test_een_kader_met_de_hand_maakt_het_gezicht_ja(client, settings, eva):
    een = await gezichten(settings, eva["een"])

    async def van_eva() -> list[tuple[dict, datetime]]:
        return [(p, at) for p, at in await groep_jobs(settings) if p["owner"] == eva["id"].hex]

    async def verdict_at() -> datetime | None:
        async with maker(settings)() as session:
            row = await session.get(FaceOwner, eva["id"])
            return None if row is None else row.verdict_at

    # Een kader dat nergens op valt: geen ja, geen groepsronde en geen verdict_at
    r = await client.post(
        f"/api/v1/files/{eva['twee']}/people",
        json={"name": "Opa", "box": [0.40, 0.60, 0.10, 0.10]},
        headers=eva["h"],
    )
    assert r.status_code == 201
    assert await oordelen(settings) == set()
    assert await van_eva() == []
    assert await verdict_at() is None

    box = [0.12, 0.11, 0.19, 0.24]  # IoU met A ruim boven 0,5
    before = datetime.now(UTC)
    r = await client.post(
        f"/api/v1/files/{eva['een']}/people", json={"name": "Oma", "box": box}, headers=eva["h"]
    )
    assert r.status_code == 201, r.text
    people = (await client.get("/api/v1/people", headers=eva["h"])).json()["people"]
    oma = uuid.UUID(next(p["id"] for p in people if p["name"] == "Oma"))
    assert await oordelen(settings) == {(een[0].id, oma, "ja")}
    # Dan komen er ook meteen voorstellen (§3.11, Levensloop): zoals na een keuze verdict_at en
    # faces.group suggest na 30 s, één keer
    found = await van_eva()
    assert [p["mode"] for p, _at in found] == ["suggest"]
    at = found[0][1].replace(tzinfo=UTC) if found[0][1].tzinfo is None else found[0][1]
    assert at >= before + group.VERDICT_WAIT - timedelta(seconds=1)
    gezet = await verdict_at()
    assert gezet is not None and gezet.replace(tzinfo=UTC) >= before - timedelta(seconds=1)
    # Een tweede kader op dezelfde foto voor iemand anders, zonder gezicht eronder: geen tweede
    r = await client.post(
        f"/api/v1/files/{eva['een']}/people",
        json={"name": "Tante", "box": [0.40, 0.60, 0.10, 0.10]},
        headers=eva["h"],
    )
    assert r.status_code == 201
    assert [p["mode"] for p, _at in await van_eva()] == ["suggest"]


async def test_een_persoon_zonder_kader_en_een_gezicht_is_een_tag(client, settings, eva):
    r = await client.post(
        f"/api/v1/files/{eva['drie']}/people", json={"name": "Oma"}, headers=eva["h"]
    )
    assert r.status_code == 201
    out = (await client.get(f"/api/v1/files/{eva['drie']}/faces", headers=eva["h"])).json()
    assert [(f["suggest"]["name"], f["suggest"]["why"], f["suggest"]["sure"]) for f in out] == [
        ("Oma", "tag", False)
    ]
    assert await oordelen(settings) == set()
    # Twee gezichten: geen tag
    r = await client.post(
        f"/api/v1/files/{eva['een']}/people", json={"name": "Opa"}, headers=eva["h"]
    )
    out = (await client.get(f"/api/v1/files/{eva['een']}/faces", headers=eva["h"])).json()
    assert [f["suggest"] for f in out] == [None, None]


async def test_niet_tonen_en_ongedaan_maken(client, settings, eva):
    een = await gezichten(settings, eva["een"])
    face = een[0].id
    await post(client, eva["h"], "confirm", faces=[str(face)], name="Oma")
    await post(client, eva["h"], "hide", faces=[str(face)])
    async with maker(settings)() as session:
        row = await session.get(Face, face)
        assert (row.hidden, row.vec, row.suggest_id, row.cluster) == (1, None, None, None)
        wipes = list(await session.scalars(select(Job.payload).where(Job.type == wipe.WIPE)))
    assert {"owner": eva["id"].hex, "scope": "checkpoint"} in wipes
    assert await oordelen(settings) == set()
    assert await links(settings, eva["een"]) == {}
    out = (await client.get(f"/api/v1/files/{eva['een']}/faces", headers=eva["h"])).json()
    assert [f["id"] for f in out] == [str(een[1].id)]
    # Verborgen kan niet nog eens bevestigd worden
    await post(client, eva["h"], "confirm", 409, faces=[str(face)], name="Oma")
    # Ongedaan maken: weer zichtbaar, zonder vector
    await post(client, eva["h"], "reset", faces=[str(face)])
    async with maker(settings)() as session:
        row = await session.get(Face, face)
        assert (row.hidden, row.vec) == (0, None)
    out = (await client.get(f"/api/v1/files/{eva['een']}/faces", headers=eva["h"])).json()
    assert {f["id"] for f in out} == {str(face), str(een[1].id)}


async def test_ongedaan_maken_hooguit_tien_minuten(client, settings, eva, monkeypatch):
    een = await gezichten(settings, eva["een"])
    await post(client, eva["h"], "confirm", faces=[str(een[0].id)], name="Oma")
    later = datetime.now(UTC) + verdicts.RESET_WINDOW + timedelta(seconds=5)
    monkeypatch.setattr(state, "now", lambda: later)
    r = await post(client, eva["h"], "reset", 409, faces=[str(een[0].id)])
    assert r.json()["detail"]["code"] == "faces_gone"
    assert len(await oordelen(settings)) == 1


async def test_alleen_eigen_levende_gezichten(client, settings, eva):
    """Een gezicht van Bram, een onbekend gezicht, of een foto in de prullenbak: 409 faces_gone,
    en er verandert niets."""
    bram = await gezichten(settings, eva["bram_foto"])
    een = await gezichten(settings, eva["een"])
    for faces in ([bram[0].id], [uuid.uuid4()], [een[0].id, bram[0].id]):
        body = [str(f) for f in faces]
        for path, extra in (("confirm", {"name": "X"}), ("hide", {}), ("reset", {})):
            r = await post(client, eva["h"], path, 409, faces=body, **extra)
            assert r.json()["detail"]["code"] == "faces_gone"
    async with maker(settings)() as session:
        node = await session.get(Node, eva["drie"])
        node.deleted_at = datetime.now(UTC)
        await session.commit()
    drie = await gezichten(settings, eva["drie"])
    await post(client, eva["h"], "confirm", 409, faces=[str(drie[0].id)], name="X")
    assert await oordelen(settings) == set()
    people = (await client.get("/api/v1/people", headers=eva["h"])).json()["people"]
    assert people == []
    # Een persoon van Bram: 404 zoals in Z6
    r = await client.post("/api/v1/people", json={"name": "Brams oma"}, headers=eva["h_bram"])
    brams = r.json()["id"]
    await post(client, eva["h"], "confirm", 404, faces=[str(een[0].id)], person_id=brams)
    await post(client, eva["h"], "reject", 404, faces=[str(een[0].id)], person_id=brams)


async def test_de_haken_van_z6(client, settings, eva):
    """Van de foto halen: ja wordt nee. Samenvoegen: de oordelen gaan mee. Wissen: de gezichten
    worden niet tonen, zonder vector, en faces.wipe checkpoint."""
    een = await gezichten(settings, eva["een"])
    twee = await gezichten(settings, eva["twee"])
    await post(client, eva["h"], "confirm", faces=[str(een[0].id)], name="Oma")
    await post(client, eva["h"], "confirm", faces=[str(twee[0].id)], name="Opa")
    people = {
        p["name"]: p["id"]
        for p in (await client.get("/api/v1/people", headers=eva["h"])).json()["people"]
    }
    oma, opa = uuid.UUID(people["Oma"]), uuid.UUID(people["Opa"])
    await post(client, eva["h"], "reject", faces=[str(een[1].id)], person_id=str(opa))
    # Oma van foto een halen
    r = await client.delete(f"/api/v1/files/{eva['een']}/people/{oma}", headers=eva["h"])
    assert r.status_code == 204
    assert (een[0].id, oma, "nee") in await oordelen(settings)
    # Opa gaat op in Oma: zijn ja en nee horen nu bij Oma
    r = await client.post(f"/api/v1/people/{opa}/merge", json={"into": str(oma)}, headers=eva["h"])
    assert r.status_code == 200, r.text
    assert await oordelen(settings) == {
        (een[0].id, oma, "nee"),
        (twee[0].id, oma, "ja"),
        (een[1].id, oma, "nee"),
    }
    # Oma wissen: haar gezichten niet tonen, zonder vector; faces.wipe checkpoint
    async with maker(settings)() as session:
        await session.execute(Job.__table__.delete().where(Job.type == wipe.WIPE))
        await session.commit()
    r = await client.delete(f"/api/v1/people/{oma}", headers=eva["h"])
    assert r.status_code == 204
    async with maker(settings)() as session:
        row = await session.get(Face, twee[0].id)
        assert (row.hidden, row.vec) == (1, None)
        wipes = list(await session.scalars(select(Job.payload).where(Job.type == wipe.WIPE)))
    assert wipes == [{"owner": eva["id"].hex, "scope": "checkpoint"}]
    assert await oordelen(settings) == set()


async def test_met_gezichten_uit_409_faces_off(client, settings, eva):
    r = await client.patch("/api/v1/me/photo-settings", json={"faces": "uit"}, headers=eva["h"])
    assert r.status_code == 200
    een = await gezichten(settings, eva["een"])
    for path, body in (("confirm", {"name": "X"}), ("hide", {}), ("reset", {})):
        r = await post(
            client,
            eva["h"],
            path,
            409,
            faces=[str(een[0].id)] if een else [str(uuid.uuid4())],
            **body,
        )
        assert r.json()["detail"]["code"] == "faces_off"
