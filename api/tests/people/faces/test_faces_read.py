"""De stand, voorstellen en groepen uit de indexen (fase 6, X2b; read.py, test_faces_perf.py).

read.py telt uit de indexen van 0011 en haalt eraf wat own_live laat vallen (vanaf de wortels in
de prullenbak). Hier: precies wat de rechtstreekse vraag geeft (join op node met own_live, open
is zonder ja), ook als een map erboven in de prullenbak ligt, en na het terugzetten.

Daarvoor moet een gezicht met een ja nooit een voorstel of een groep hebben: confirm, een kader
met de hand (links) en face_rows.write_group houden dat zo, ook als de ja komt tussen het lezen
en het schrijven van group.
"""

from __future__ import annotations

import uuid
from collections import Counter

import pytest
from sqlalchemy import func, or_, select

from app.models import Face, FacePerson, FaceScan, Node, Person
from app.people.faces import drempels, group, read
from app.search.live import own_live
from tests.factories import create_folder, create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"


@pytest.fixture
async def eva(client, settings, nep):
    """Eva: drie foto's in de hoofdmap, twee in vakantie en twee in vakantie/diep, met persoon 1,
    2 en 3. Oma is het gezicht van persoon 1 op r1; daarna full en suggest."""
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        vakantie = await create_folder(session, user, name="vakantie")
        diep = await create_folder(session, user, vakantie.id, name="diep")
        await session.commit()
    ids = {"id": user.id, "vakantie": vakantie.id}
    plan = {
        "r1": (None, [(A, 1), (B, 2)]),
        "r2": (None, [(A, 1), (B, 2)]),
        "r3": (None, [(A, 1), (B, 3)]),
        "v1": (vakantie.id, [(A, 1), (B, 2)]),
        "v2": (vakantie.id, [(A, 2), (B, 3)]),
        "d1": (diep.id, [(A, 1), (B, 3)]),
        "d2": (diep.id, [(A, 3)]),
    }
    for name, (parent, faces) in plan.items():
        ids[name] = await foto(settings, nep, user.id, f"{name}.jpg", faces, parent=parent)
    await aan(settings, user.id)
    await draai(settings)
    ids["h"] = await login(client, "eva@example.nl")
    async with maker(settings)() as session:
        oma = await session.scalar(
            select(Face.id).where(Face.node_id == ids["r1"]).order_by(Face.x).limit(1)
        )
    r = await client.post(
        f"{F}/confirm", json={"faces": [str(oma)], "name": "Oma"}, headers=ids["h"]
    )
    assert r.status_code == 204, r.text
    await group.run_group(settings, user.id, "full")
    await group.run_group(settings, user.id, "suggest")
    return ids


async def referentie(settings, owner: uuid.UUID) -> dict:
    """De rechtstreekse vraag: elk gezicht met de join op node en own_live; open is zonder ja."""
    async with maker(settings)() as session:
        rows = list(
            await session.execute(
                select(Face.id, Face.suggest_id, Face.cluster)
                .join(Node, Node.id == Face.node_id)
                .where(Face.owner_id == owner, own_live(Node, owner), Face.hidden == 0)
            )
        )
        ja = set(
            await session.scalars(select(FacePerson.face_id).where(FacePerson.verdict == "ja"))
        )
        shown = set(
            await session.scalars(
                select(Person.id).where(Person.owner_id == owner, Person.hidden == 0)
            )
        )
        scans = Counter(
            await session.scalars(
                select(FaceScan.state)
                .join(Node, Node.id == FaceScan.node_id)
                .where(FaceScan.owner_id == owner, own_live(Node, owner))
            )
        )
    open_rows = [r for r in rows if r.id not in ja]
    clusters = Counter(r.cluster for r in open_rows if r.cluster is not None)
    named = len(rows) - len(open_rows)
    return {
        "photos": {
            "total": sum(scans.values()),
            "done": scans["done"],
            "waiting": scans["wait"] + scans["busy"],
            "failed": scans["failed"],
            "skipped": scans["skip"],
        },
        "faces": {
            "found": len(rows),
            "named": named,
            "open": len(open_rows),
            "groups": sum(1 for n in clusters.values() if n >= drempels.MIN_GROUP),
        },
        "suggest": dict(Counter(str(r.suggest_id) for r in open_rows if r.suggest_id in shown)),
        "groups": sorted((n for n in clusters.values() if n >= drempels.MIN_GROUP), reverse=True),
    }


async def via_api(client, h) -> dict:
    status = (await client.get(f"{F}/status", headers=h)).json()
    people = (await client.get(f"{F}/suggestions", headers=h)).json()["people"]
    for p in people:
        assert len(p["sample"]) == min(read.SAMPLE, p["count"])
        assert {f["suggest"]["person_id"] for f in p["sample"]} == {p["person"]["id"]}
        page = (await client.get(f"{F}/suggestions?person={p['person']['id']}", headers=h)).json()
        assert len(page["faces"]) == p["count"]
    groups = (await client.get(f"{F}/groups", headers=h)).json()["groups"]
    for g in groups:
        assert len(g["faces"]) == min(read.GROUP_FACES, g["count"])
        page = (await client.get(f"{F}/groups/{g['key']}", headers=h)).json()
        assert len(page["faces"]) == g["count"]
        assert all(f["person"] is None for f in page["faces"])
    return {
        "photos": status["photos"],
        "faces": status["faces"],
        "suggest": {p["person"]["id"]: p["count"] for p in people},
        "groups": [g["count"] for g in groups],
    }


async def ja_zonder_voorstel(settings) -> int:
    """Hoeveel gezichten met een ja toch een voorstel of een groep hebben (moet 0 zijn)."""
    async with maker(settings)() as session:
        return await session.scalar(
            select(func.count())
            .select_from(Face)
            .join(FacePerson, FacePerson.face_id == Face.id)
            .where(
                FacePerson.verdict == "ja",
                or_(Face.suggest_id.is_not(None), Face.cluster.is_not(None)),
            )
        )


async def test_dezelfde_stand_als_de_join_ook_met_een_map_in_de_prullenbak(client, settings, eva):
    h = eva["h"]
    voor = await referentie(settings, eva["id"])
    # De wereld zegt iets: gezichten, een ja, voorstellen en groepen
    assert voor["faces"]["found"] == 13 and voor["faces"]["named"] == 1
    assert voor["suggest"] and voor["groups"]
    assert await via_api(client, h) == voor
    assert await ja_zonder_voorstel(settings) == 0

    # vakantie naar de prullenbak: ook diep (een map erboven) valt weg
    r = await client.delete(f"/api/v1/files/{eva['vakantie']}", headers=h)
    assert r.status_code == 204, r.text
    weg = await referentie(settings, eva["id"])
    assert weg["faces"]["found"] == 6 and weg["photos"]["total"] == 3
    assert weg != voor
    assert await via_api(client, h) == weg

    # Terug: dezelfde stand als ervoor
    r = await client.post("/api/v1/trash/restore", json={"ids": [str(eva["vakantie"])]}, headers=h)
    assert r.status_code == 200, r.text
    assert await referentie(settings, eva["id"]) == voor
    assert await via_api(client, h) == voor


async def test_een_ja_tussen_lezen_en_schrijven_van_group(client, settings, eva, monkeypatch):
    """Eva bevestigt een gezicht terwijl full rekent: het krijgt geen groep en geen voorstel."""
    async with maker(settings)() as session:
        target = await session.scalar(
            select(Face.id).where(Face.owner_id == eva["id"], Face.cluster.is_not(None)).limit(1)
        )
    assert target is not None
    real = group._read

    async def read_then_confirm(*args, **kwargs):
        out = await real(*args, **kwargs)
        assert target in {face_id for face_id, _vec in out[1]}  # het staat open in de invoer
        r = await client.post(
            f"{F}/confirm", json={"faces": [str(target)], "name": "Tante"}, headers=eva["h"]
        )
        assert r.status_code == 204, r.text
        return out

    monkeypatch.setattr(group, "_read", read_then_confirm)
    await group.run_group(settings, eva["id"], "full")
    async with maker(settings)() as session:
        face = await session.get(Face, target)
    assert (face.suggest_id, face.cluster) == (None, None)
    assert await ja_zonder_voorstel(settings) == 0
    assert await via_api(client, eva["h"]) == await referentie(settings, eva["id"])


async def test_een_kader_met_de_hand_op_een_gezicht_in_een_groep(client, settings, eva):
    """Een kader (IoU ≥ 0,5) maakt het gezicht ja: het verliest zijn groep en zijn voorstel."""
    async with maker(settings)() as session:
        face = (
            await session.scalars(
                select(Face).where(
                    Face.owner_id == eva["id"], Face.cluster.is_not(None), Face.x == A[0]
                )
            )
        ).first()
    assert face is not None
    box = [face.x + 0.02, face.y + 0.01, face.w - 0.01, face.h - 0.01]
    r = await client.post(
        f"/api/v1/files/{face.node_id}/people", json={"name": "Neef", "box": box}, headers=eva["h"]
    )
    assert r.status_code == 201, r.text
    async with maker(settings)() as session:
        after = await session.get(Face, face.id)
        verdict = await session.scalar(
            select(FacePerson.verdict).where(FacePerson.face_id == face.id)
        )
    assert verdict == "ja"
    assert (after.suggest_id, after.cluster) == (None, None)
    assert await ja_zonder_voorstel(settings) == 0
    assert await via_api(client, eva["h"]) == await referentie(settings, eva["id"])
