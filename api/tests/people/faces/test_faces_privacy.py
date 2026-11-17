"""De privacy van gezichten (fase 6, Keuze 64, 65 en 69; X2b): de wereld met Eva, Bram, Carla,
een gast, de beheerder en een uploadlink.

- een foto die een ander in Eva's map zette, telt alleen als die ander zelf gezichten aan heeft;
  via een uploadlink nooit (skip met not_own_upload, 0 rijen in face);
- Bram ziet een gedeelde foto van Eva: [] voor de gezichten en 404 voor een uitsnede; een foto die
  hij niet mag zien 404;
- een gast: [] voor een gedeelde foto, verder 403 guest, ook bij de schakelaar;
- geen route voor gezichten onder de publieke routes;
- de beheerder ziet alleen aantallen voor de hele server;
- een verbod zet iedereen uit, wist de sleutels en de rijen, en PATCH aan geeft 403
  faces_forbidden; weer toestaan zet niemand aan.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from sqlalchemy import func, select

from app import origin
from app.main import app as fastapi_app
from app.models import Face, FaceOwner, FaceScan, Job, PhotoPref, Share
from app.people.faces import crypto, wipe
from tests.factories import create_folder, create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"
ADMIN = "/api/v1/admin/search"
SETTINGS = "/api/v1/me/photo-settings"
UUID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)


@pytest.fixture
async def wereld(client, settings, nep, admin):
    """Eva (aan) met een eigen foto en een map Gedeeld (lezen voor Bram en de gast Gijs); Bram
    (aan) met een eigen foto en een upload in Gedeeld; Carla (uit) met een upload in Gedeeld; een
    upload via een link in Gedeeld."""
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        carla = await create_user(session, "carla@example.nl")
        gijs = await create_user(session, "gijs@example.nl", role="guest")
        gedeeld = await create_folder(session, eva, name="Gedeeld")
        for grantee in (bram, gijs):
            session.add(
                Share(node_id=gedeeld.id, owner_id=eva.id, grantee_id=grantee.id, permission="read")
            )
        await session.commit()
    ids = {"eva": eva.id, "bram": bram.id, "carla": carla.id, "gedeeld": gedeeld.id}
    ids["eigen"] = await foto(settings, nep, eva.id, "eigen.jpg", [(A, 1), (B, 2)])
    ids["samen"] = await foto(settings, nep, eva.id, "samen.jpg", [(A, 1)], parent=gedeeld.id)
    ids["van_bram"] = await foto(settings, nep, eva.id, "bram.jpg", [(A, 3)], parent=gedeeld.id)
    ids["van_carla"] = await foto(settings, nep, eva.id, "carla.jpg", [(A, 4)], parent=gedeeld.id)
    ids["via_link"] = await foto(settings, nep, eva.id, "link.jpg", [(A, 5)], parent=gedeeld.id)
    ids["bram_foto"] = await foto(settings, nep, bram.id, "eigen.jpg", [(A, 6)])
    async with maker(settings)() as session:
        await origin.placed(session, ids["van_bram"], bram.id)
        await origin.placed(session, ids["van_carla"], carla.id)
        await origin.placed(session, ids["via_link"], None)
        await session.commit()
    await aan(settings, bram.id)
    await aan(settings, eva.id)
    await draai(settings)
    ids["h"] = await login(client, "eva@example.nl")
    ids["h_bram"] = await login(client, "bram@example.nl")
    ids["h_gijs"] = await login(client, "gijs@example.nl")
    ids["h_admin"] = admin[0]
    return ids


async def scans(settings, nodes) -> dict[uuid.UUID, tuple[str, str | None, int]]:
    async with maker(settings)() as session:
        out = {}
        for node in nodes:
            row = await session.get(FaceScan, node)
            n = await session.scalar(
                select(func.count()).select_from(Face).where(Face.node_id == node)
            )
            out[node] = (row.state, row.code, n) if row is not None else (None, None, n)
        return out


async def test_keuze_69_een_upload_van_een_ander(settings, wereld):
    """Bram heeft gezichten aan: zijn upload in Eva's map telt. Carla niet, en een uploadlink
    nooit: skip met not_own_upload en 0 gezichten."""
    w = wereld
    found = await scans(
        settings, [w["eigen"], w["samen"], w["van_bram"], w["van_carla"], w["via_link"]]
    )
    assert found == {
        w["eigen"]: ("done", None, 2),
        w["samen"]: ("done", None, 1),
        w["van_bram"]: ("done", None, 1),
        w["van_carla"]: ("skip", "not_own_upload", 0),
        w["via_link"]: ("skip", "not_own_upload", 0),
    }
    # De gezichten van Bram's upload horen bij Eva, de eigenaar van de foto
    async with maker(settings)() as session:
        owners = set(
            await session.scalars(select(Face.owner_id).where(Face.node_id == w["van_bram"]))
        )
    assert owners == {w["eva"]}


async def test_een_deling_geeft_bram_niets(client, settings, wereld):
    w = wereld
    # Eva ziet haar eigen gezichten op de gedeelde foto
    r = await client.get(f"/api/v1/files/{w['samen']}/faces", headers=w["h"])
    assert r.status_code == 200 and len(r.json()) == 1
    face = r.json()[0]["id"]
    # Bram (aan) mag de foto zien: [] voor de gezichten, 404 voor de uitsnede
    r = await client.get(f"/api/v1/files/{w['samen']}/faces", headers=w["h_bram"])
    assert r.status_code == 200 and r.json() == []
    r = await client.get(f"{F}/{face}/crop", headers=w["h_bram"])
    assert r.status_code == 404
    # Een foto die hij niet mag zien: 404
    r = await client.get(f"/api/v1/files/{w['eigen']}/faces", headers=w["h_bram"])
    assert r.status_code == 404
    # De gast: de gedeelde foto [] (zoals Bram); verder 403 guest, ook voor de schakelaar
    r = await client.get(f"/api/v1/files/{w['samen']}/faces", headers=w["h_gijs"])
    assert r.status_code == 200 and r.json() == []
    for method, path, body in (
        ("GET", f"{F}/status", None),
        ("GET", f"{F}/{face}/crop", None),
        ("PATCH", SETTINGS, {"faces": "aan"}),
    ):
        r = await client.request(method, path, json=body, headers=w["h_gijs"])
        assert r.status_code == 403, (path, r.text)
        assert r.json()["detail"]["code"] == "guest", path


def test_geen_publieke_route_voor_gezichten():
    """Onder /api/v1/public (de links /s/...) en /s/ zelf staat niets van gezichten."""
    paths = list(fastapi_app.openapi()["paths"])
    public = [p for p in paths if p.startswith(("/api/v1/public", "/s/"))]
    assert public, "geen publieke routes gevonden"
    assert [p for p in public if "face" in p or "people" in p or "person" in p] == []
    faces = sorted(p for p in paths if "faces" in p)
    assert faces == [
        "/api/v1/files/{node_id}/faces",
        "/api/v1/files/{node_id}/faces/rescan",
        "/api/v1/people/faces/confirm",
        "/api/v1/people/faces/groups",
        "/api/v1/people/faces/groups/{key}",
        "/api/v1/people/faces/hide",
        "/api/v1/people/faces/reject",
        "/api/v1/people/faces/reset",
        "/api/v1/people/faces/status",
        "/api/v1/people/faces/suggestions",
        "/api/v1/people/faces/wipe-unnamed",
        "/api/v1/people/faces/{face_id}/crop",
    ]


async def test_de_beheerder_ziet_alleen_aantallen(client, settings, wereld):
    w = wereld
    r = await client.get(ADMIN, headers=w["h_admin"])
    assert r.status_code == 200
    block = r.json()["faces"]
    assert sorted(block) == [
        "allowed",
        "analyse",
        "bench",
        "db_bytes",
        "eta_s",
        "faces",
        "failed_by_code",
        "limits",
        "models",
        "named",
        "quality",
        "queue",
        "reason",
        "users_on",
        "wipe_waiting",
    ]
    # Eva: 2 + 1 + 1 (de upload van Bram), Bram: 1
    assert (block["users_on"], block["faces"], block["named"]) == (2, 5, 0)
    assert block["queue"]["waiting"] == 0
    text = json.dumps(block)
    assert not UUID.search(text), text
    assert "@" not in text and "eva" not in text.lower() and "bram" not in text.lower()
    # Een uitsnede kan de beheerder niet halen (hij heeft geen eigen gezichten)
    async with maker(settings)() as session:
        face = await session.scalar(select(Face.id).where(Face.node_id == w["eigen"]))
    r = await client.get(f"{F}/{face}/crop", headers=w["h_admin"])
    assert r.status_code == 404


async def test_een_verbod_wist_iedereen(client, settings, wereld):
    w = wereld
    owners = (w["eva"], w["bram"])
    for owner in owners:
        assert crypto.load_key(settings, owner) is not None
    r = await client.patch(ADMIN, json={"faces_allowed": False}, headers=w["h_admin"])
    assert r.status_code == 200, r.text
    # Meteen: iedereen uit, de sleutels weg, per eigenaar een wisronde
    async with maker(settings)() as session:
        prefs = {p.user_id: p.faces for p in await session.scalars(select(PhotoPref))}
        keys = sorted(
            j.key for j in await session.scalars(select(Job).where(Job.type == wipe.WIPE))
        )
    assert prefs == {w["eva"]: "uit", w["bram"]: "uit"}
    assert keys == sorted(f"{wipe.WIPE}:all:{o.hex}" for o in owners)
    for owner in owners:
        assert crypto.load_key(settings, owner) is None
        assert not crypto.key_path(settings, owner).exists()
    await draai(settings)
    async with maker(settings)() as session:
        for table in (Face, FaceScan, FaceOwner):
            assert await session.scalar(select(func.count()).select_from(table)) == 0, table
    # Aanzetten mag niet meer; de stand zegt het
    r = await client.patch(SETTINGS, json={"faces": "aan"}, headers=w["h"])
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "faces_forbidden"
    r = await client.get(SETTINGS, headers=w["h"])
    assert r.json()["faces"] == "uit" and r.json()["faces_forbidden"] is True
    r = await client.get(f"{F}/status", headers=w["h"])
    assert r.status_code == 200
    assert (r.json()["setting"], r.json()["forbidden"]) == ("uit", True)
    assert (await client.get(ADMIN, headers=w["h_admin"])).json()["faces"]["allowed"] is False
    # Weer toestaan: niemand staat vanzelf aan, en geen nieuwe scan
    r = await client.patch(ADMIN, json={"faces_allowed": True}, headers=w["h_admin"])
    assert r.status_code == 200, r.text
    await draai(settings)
    async with maker(settings)() as session:
        prefs = {p.user_id: p.faces for p in await session.scalars(select(PhotoPref))}
        assert await session.scalar(select(func.count()).select_from(FaceScan)) == 0
    assert prefs == {w["eva"]: "uit", w["bram"]: "uit"}
    r = await client.patch(SETTINGS, json={"faces": "aan"}, headers=w["h"])
    assert r.status_code == 200, r.text
