"""De routes van gezichten (fase 6, X2; gebouwd in X2b), met gezichten aan op het platform.

- de stand (GET /people/faces/status) met de tellers;
- de uitsnede: WebP, Cache-Control no-store, 96 of 192 px, alleen eigen gezichten, 20 per
  seconde (429), de inlog als header (geen token in de query);
- opnieuw zoeken: vooraan, oude gezichten weg, hooguit 20 per uur (429);
- GET /files/{id}/faces: de eigenaar zijn gezichten; een ander die de foto mag zien []; anders 404;
  een foto die wacht gaat vooraan;
- een gast 403 guest; gezichten uit voor de eigenaar: 409 faces_off (behalve de stand, de
  uitsnede en wipe-unnamed);
- de volgorde van de routers: /people/faces/... komt nooit bij /people/{person_id};
- wipe-unnamed: de open gezichten weg, benoemde blijven.
"""

from __future__ import annotations

import io
import uuid

import pytest
from sqlalchemy import select

from app.models import Face, FaceScan, Share
from app.people.faces import scan
from app.routers import people_faces
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"


@pytest.fixture(autouse=True)
def _vrije_grenzen(monkeypatch):
    monkeypatch.setattr(people_faces, "_crops", {})
    monkeypatch.setattr(people_faces, "_rescans", {})


@pytest.fixture
async def huis(client, settings, nep):
    """Eva (gezichten aan, twee foto's, een map Gedeeld met Bram), Bram (eigen foto), de gast
    Gijs met leesrecht op Gedeeld."""
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        gijs = await create_user(session, "gijs@example.nl", role="guest")
        from tests.factories import create_folder

        gedeeld = await create_folder(session, eva, name="Gedeeld")
        for grantee in (bram, gijs):
            session.add(
                Share(node_id=gedeeld.id, owner_id=eva.id, grantee_id=grantee.id, permission="read")
            )
        await session.commit()
    ids = {"eva": eva.id, "bram": bram.id, "gedeeld": gedeeld.id}
    ids["een"] = await foto(settings, nep, eva.id, "een.jpg", [(A, 1), (B, 2)])
    ids["samen"] = await foto(settings, nep, eva.id, "samen.jpg", [(A, 1)], parent=gedeeld.id)
    ids["bram_foto"] = await foto(settings, nep, bram.id, "bram.jpg", [(A, 3)])
    await aan(settings, eva.id, bram.id)
    await draai(settings)
    ids["h"] = await login(client, "eva@example.nl")
    ids["h_bram"] = await login(client, "bram@example.nl")
    ids["h_gijs"] = await login(client, "gijs@example.nl")
    return ids


async def face_ids(settings, node) -> list[uuid.UUID]:
    async with maker(settings)() as session:
        return list(
            await session.scalars(select(Face.id).where(Face.node_id == node).order_by(Face.x))
        )


async def test_de_stand(client, huis):
    r = await client.get(f"{F}/status", headers=huis["h"])
    assert r.status_code == 200
    assert r.json() == {
        "setting": "aan",
        "forbidden": False,
        "analyse": "ok",
        "photos": {"total": 2, "done": 2, "waiting": 0, "failed": 0, "skipped": 0},
        "faces": {"found": 3, "named": 0, "open": 3, "groups": 0},
        "wiping": False,
    }


async def test_de_uitsnede(client, settings, huis):
    from PIL import Image

    face = (await face_ids(settings, huis["een"]))[0]
    for size in (96, 192):
        r = await client.get(f"{F}/{face}/crop", params={"size": size}, headers=huis["h"])
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/webp"
        assert r.headers["cache-control"] == "no-store"
        with Image.open(io.BytesIO(r.content)) as img:
            assert img.format == "WEBP" and img.size == (size, size)
    r = await client.get(f"{F}/{face}/crop", params={"size": 128}, headers=huis["h"])
    assert r.status_code == 422
    # Zonder inlog 401, ook niet met een token in de query
    token = huis["h"]["Authorization"].split()[1]
    r = await client.get(f"{F}/{face}/crop", params={"size": 96, "token": token})
    assert r.status_code == 401
    # Een gezicht van een ander, of onbekend: 404
    bram = (await face_ids(settings, huis["bram_foto"]))[0]
    for other in (bram, uuid.uuid4()):
        r = await client.get(f"{F}/{other}/crop", headers=huis["h"])
        assert r.status_code == 404


async def test_hooguit_20_uitsneden_per_seconde(client, settings, huis):
    face = (await face_ids(settings, huis["een"]))[0]
    codes = []
    for _ in range(people_faces.CROP_PER_S + 1):
        r = await client.get(f"{F}/{face}/crop", headers=huis["h"])
        codes.append(r.status_code)
    assert codes[: people_faces.CROP_PER_S] == [200] * people_faces.CROP_PER_S
    assert codes[-1] == 429
    # Bram heeft zijn eigen grens
    bram = (await face_ids(settings, huis["bram_foto"]))[0]
    r = await client.get(f"{F}/{bram}/crop", headers=huis["h_bram"])
    assert r.status_code == 200


async def test_opnieuw_zoeken(client, settings, nep, huis):
    oud = await face_ids(settings, huis["een"])
    r = await client.post(f"/api/v1/files/{huis['een']}/faces/rescan", headers=huis["h"])
    assert r.status_code == 202
    async with maker(settings)() as session:
        row = await session.get(FaceScan, huis["een"])
        assert row.state == "wait" and row.prio.startswith(scan.FRONT)
    assert await face_ids(settings, huis["een"]) == []
    await draai(settings)
    nieuw = await face_ids(settings, huis["een"])
    assert len(nieuw) == 2 and not set(nieuw) & set(oud)
    # Van een ander, of geen foto: 404
    r = await client.post(f"/api/v1/files/{huis['bram_foto']}/faces/rescan", headers=huis["h"])
    assert r.status_code == 404
    r = await client.post(f"/api/v1/files/{huis['gedeeld']}/faces/rescan", headers=huis["h"])
    assert r.status_code == 404


async def test_hooguit_20_keer_opnieuw_per_uur(client, huis):
    url = f"/api/v1/files/{huis['een']}/faces/rescan"
    codes = [
        (await client.post(url, headers=huis["h"])).status_code
        for _ in range(people_faces.RESCAN_PER_HOUR + 1)
    ]
    assert codes == [202] * people_faces.RESCAN_PER_HOUR + [429]


async def test_gezichten_van_een_foto(client, settings, huis):
    r = await client.get(f"/api/v1/files/{huis['een']}/faces", headers=huis["h"])
    assert r.status_code == 200
    out = r.json()
    assert [tuple(f["box"]) for f in out] == [A, B]
    assert all(set(f) == {"id", "node_id", "box", "person", "suggest"} for f in out)
    # Bram en de gast Gijs mogen samen.jpg zien: []; een.jpg niet: 404
    for h in (huis["h_bram"], huis["h_gijs"]):
        r = await client.get(f"/api/v1/files/{huis['samen']}/faces", headers=h)
        assert (r.status_code, r.json()) == (200, [])
        r = await client.get(f"/api/v1/files/{huis['een']}/faces", headers=h)
        assert r.status_code == 404
    r = await client.get(f"/api/v1/files/{uuid.uuid4()}/faces", headers=huis["h"])
    assert r.status_code == 404


async def test_een_foto_die_wacht_gaat_vooraan(client, settings, nep, huis):
    nieuw = await foto(settings, nep, huis["eva"], "nieuw.jpg", [(A, 1)])
    await aan(settings, huis["eva"])
    async with maker(settings)() as session:
        await scan.plan_nodes(session, huis["eva"])
        await session.commit()
        row = await session.get(FaceScan, nieuw)
        assert row.state == "wait" and not row.prio.startswith(scan.FRONT)
    r = await client.get(f"/api/v1/files/{nieuw}/faces", headers=huis["h"])
    assert (r.status_code, r.json()) == (200, [])
    async with maker(settings)() as session:
        row = await session.get(FaceScan, nieuw)
        assert row.prio.startswith(scan.FRONT)


async def test_een_gast_krijgt_403(client, huis):
    for method, path, body in (
        ("GET", f"{F}/status", None),
        ("GET", f"{F}/groups", None),
        ("POST", f"{F}/confirm", {"faces": [str(uuid.uuid4())], "name": "X"}),
        ("POST", f"{F}/wipe-unnamed", None),
    ):
        r = await client.request(method, path, json=body, headers=huis["h_gijs"])
        assert r.status_code == 403, path
        assert r.json()["detail"]["code"] == "guest"


async def test_gezichten_uit_geeft_409_faces_off(client, settings, huis):
    r = await client.patch("/api/v1/me/photo-settings", json={"faces": "uit"}, headers=huis["h"])
    assert r.status_code == 200
    ID = str(uuid.uuid4())
    for method, path, body in (
        ("GET", f"{F}/suggestions", None),
        ("GET", f"{F}/groups", None),
        ("GET", f"{F}/groups/1:1", None),
        ("POST", f"{F}/confirm", {"faces": [ID], "name": "X"}),
        ("POST", f"{F}/reject", {"faces": [ID], "person_id": ID}),
        ("POST", f"{F}/hide", {"faces": [ID]}),
        ("POST", f"{F}/reset", {"faces": [ID]}),
        ("POST", f"/api/v1/files/{huis['een']}/faces/rescan", None),
    ):
        r = await client.request(method, path, json=body, headers=huis["h"])
        assert r.status_code == 409, path
        assert r.json()["detail"]["code"] == "faces_off", path
    r = await client.get(f"{F}/status", headers=huis["h"])
    assert r.status_code == 200 and r.json()["setting"] == "uit"
    r = await client.get(f"/api/v1/files/{huis['een']}/faces", headers=huis["h"])
    assert (r.status_code, r.json()) == (200, [])
    r = await client.post(f"{F}/wipe-unnamed", headers=huis["h"])
    assert r.status_code == 202


async def test_de_routers_in_de_goede_volgorde(client, huis):
    """/people/faces/... is nooit /people/{person_id}; een persoon blijft gewoon werken."""
    r = await client.post("/api/v1/people", json={"name": "Oma"}, headers=huis["h"])
    oma = r.json()["id"]
    r = await client.patch(f"/api/v1/people/{oma}", json={"name": "Opa"}, headers=huis["h"])
    assert r.status_code == 200 and r.json()["name"] == "Opa"
    r = await client.get(f"/api/v1/people/{oma}/photos", headers=huis["h"])
    assert r.status_code == 200
    r = await client.get(f"{F}/status", headers=huis["h"])
    assert r.status_code == 200 and "setting" in r.json()
    r = await client.get(f"{F}/groups", headers=huis["h"])
    assert r.status_code == 200 and "groups" in r.json()


async def test_wis_wat_niet_benoemd_is(client, settings, huis):
    een = await face_ids(settings, huis["een"])
    r = await client.post(
        f"{F}/confirm", json={"faces": [str(een[0])], "name": "Oma"}, headers=huis["h"]
    )
    assert r.status_code == 204
    r = await client.post(f"{F}/wipe-unnamed", headers=huis["h"])
    assert (r.status_code, r.json()) == (202, {"queued": 2})
    r = await client.get(f"{F}/status", headers=huis["h"])
    assert r.json()["wiping"] is True
    await draai(settings)
    async with maker(settings)() as session:
        rows = {
            f.id: (f.hidden, f.vec is None)
            for f in await session.scalars(select(Face).where(Face.owner_id == huis["eva"]))
        }
    assert rows[een[0]] == (0, False)
    assert sorted(v for k, v in rows.items() if k != een[0]) == [(1, True), (1, True)]
    status = (await client.get(f"{F}/status", headers=huis["h"])).json()
    assert status["wiping"] is False
    assert status["faces"] == {"found": 1, "named": 1, "open": 0, "groups": 0}
