"""Uit is onzichtbaar (fase 6, X2b; FEITEN van golf 6): zonder PLATFORM_ANALYSE_ENABLED (zoals
0.8.0 en de .env van Jonas), of zonder de map PLATFORM_FACES_KEYS, is gezichten er niet.

- elke route van gezichten geeft na de inlog wat de stub van Z0b in 0.8.0 gaf: 501
  not_built("X2"), ook aan een gast (de samenvoeging van golf 6; tests/uit080/ vergelijkt elke
  route met 0.8.0); met de analyse aan maar zonder de map met sleutels 409 faces_unavailable;
- GET en PATCH /me/photo-settings antwoorden precies als 0.8.0 (zonder aanmelding van X2);
- /admin/search precies als 0.8.0: geen blok, faces_allowed en "opnieuw" 501 not_built vóór er
  iets verandert (ook niet de pauze uit dezelfde PATCH);
- geen haak (grote preview, kopie, personen, de start, het uur) zet een job klaar, niets in de
  rij analyse, geen bestand onder DATA_ROOT en geen people.changed die 0.8.0 niet gaf.

Deze module zet de fixture settings van de conftest terug naar die van 0.8.0 (geen analyse).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import db, jobs, prefs
from app.config import Settings, get_settings
from app.main import app as fastapi_app
from app.migrate import upgrade
from app.models import FaceOwner, FaceScan, Job, PhotoPref
from app.people import people as P
from app.people.faces import register, state
from app.search import hooks as search_hooks
from tests.factories import create_user, maker
from tests.people.faces.conftest import BASE
from tests.test_photos import login, photo

pytestmark = pytest.mark.anyio

ID = "00000000-0000-4000-8000-000000000001"
ADMIN = "/api/v1/admin/search"
NOT_BUILT = {"message": "Nog niet gebouwd (X2)", "code": "not_built", "params": {"package": "X2"}}
UNAVAILABLE = {
    "message": "Gezichtsherkenning is op deze server niet geïnstalleerd",
    "code": "faces_unavailable",
}


def routes(node: str) -> list[tuple[str, str, dict | None]]:
    """De twaalf routes van X2 (de stubs van Z0b), met een geldige vraag."""
    return [
        ("GET", "/api/v1/people/faces/status", None),
        ("GET", "/api/v1/people/faces/suggestions", None),
        ("GET", f"/api/v1/people/faces/suggestions?person={ID}", None),
        ("GET", "/api/v1/people/faces/groups", None),
        ("GET", "/api/v1/people/faces/groups/1:2", None),
        ("POST", "/api/v1/people/faces/confirm", {"faces": [ID], "name": "Oma"}),
        ("POST", "/api/v1/people/faces/reject", {"faces": [ID], "person_id": ID}),
        ("POST", "/api/v1/people/faces/hide", {"faces": [ID]}),
        ("POST", "/api/v1/people/faces/reset", {"faces": [ID]}),
        ("POST", "/api/v1/people/faces/wipe-unnamed", None),
        ("GET", f"/api/v1/people/faces/{ID}/crop?size=96", None),
        ("GET", f"/api/v1/files/{node}/faces", None),
        ("POST", f"/api/v1/files/{node}/faces/rescan", None),
    ]


@pytest.fixture
async def settings(tmp_path):
    """Zoals 0.8.0: geen analyse, geen sleutels (de fixture van tests/conftest.py)."""
    s = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8)
    await upgrade(s)
    state.reset()
    state.configure(s)
    yield s
    await db.get_engine(s).dispose()
    db._engine.cache_clear()


@pytest.fixture
def events(monkeypatch) -> list[uuid.UUID]:
    """Elke people.changed die de app stuurt."""
    seen: list[uuid.UUID] = []
    real = P.changed

    def spy(user_id: uuid.UUID) -> None:
        seen.append(user_id)
        real(user_id)

    monkeypatch.setattr(P, "changed", spy)
    return seen


@pytest.fixture
async def huis(client, settings):
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        gast = await create_user(session, "gijs@example.nl", role="guest")
        foto = await photo(session, eva, "strand.jpg", BASE)
        ids = {"eva": eva.id, "gast": gast.id, "foto": foto.id, "root": eva.root_id}
        await session.commit()
    ids["h_eva"] = await login(client, "eva@example.nl")
    ids["h_gast"] = await login(client, "gijs@example.nl")
    return ids


def files_under(root: Path) -> set[str]:
    """Alle bestanden onder root, zonder de database (die verandert altijd)."""
    found = set()
    for folder, _dirs, names in os.walk(root):
        for name in names:
            rel = os.path.relpath(os.path.join(folder, name), root)
            if not rel.startswith("platform.db"):
                found.add(rel)
    return found


async def jobs_of(settings) -> list[tuple[str, str]]:
    async with maker(settings)() as session:
        return [(t, jobs.lane_of(t)) for t in await session.scalars(select(Job.type))]


async def face_rows(settings) -> int:
    async with maker(settings)() as session:
        a = await session.scalar(select(func.count()).select_from(FaceScan))
        b = await session.scalar(select(func.count()).select_from(FaceOwner))
        return int(a or 0) + int(b or 0)


async def test_elke_route_van_gezichten_antwoordt_als_in_080(client, settings, huis):
    """Eva en de gast Gijs, ook met een eigen foto: 501 not_built("X2") zoals de stubs van
    0.8.0, geen job."""
    assert not settings.analyse_on and not state.running(settings)
    for who in ("h_eva", "h_gast"):
        for method, path, body in routes(str(huis["foto"])):
            r = await client.request(method, path, json=body, headers=huis[who])
            assert r.status_code == 501, (who, path, r.text)
            assert r.json()["detail"] == NOT_BUILT, (who, path)
    assert await jobs_of(settings) == []
    assert await face_rows(settings) == 0


async def test_zonder_inlog_eerst_401(client, huis):
    for method, path, body in routes(str(huis["foto"])):
        r = await client.request(method, path, json=body)
        assert r.status_code == 401, path


async def test_fotoinstellingen_antwoorden_als_in_080(client, settings, huis, events):
    """Dezelfde vragen met en zonder de aanmelding van X2 (zonder: 0.8.0) geven dezelfde
    antwoorden, en zetten niets klaar."""

    async def vragen() -> list[tuple[int, object]]:
        out = []
        for method, body in (
            ("GET", None),
            ("PATCH", {"faces": "aan"}),
            ("PATCH", {"faces": "uit"}),
            ("PATCH", {"faces": "aan", "location": "uit"}),
            ("PATCH", {"location": "aan"}),
            ("GET", None),
        ):
            for who in ("h_eva", "h_gast"):
                r = await client.request(
                    method, "/api/v1/me/photo-settings", json=body, headers=huis[who]
                )
                out.append((r.status_code, r.json()))
        return out

    met = await vragen()
    prefs.forget_switches()
    try:
        zonder = await vragen()
    finally:
        register.register()
    assert met == zonder
    assert met[0] == (
        200,
        {**met[0][1], "faces": "uit", "faces_available": False, "faces_forbidden": False},
    )
    assert met[2] == (409, {"detail": UNAVAILABLE})
    assert events == []
    assert await jobs_of(settings) == []
    async with maker(settings)() as session:
        row = await session.get(PhotoPref, huis["eva"])
        assert row is None or row.faces == "uit"


async def test_beheer_is_dat_van_080(client, settings, admin, monkeypatch):
    """De start meldt het blok niet aan; GET zegt faces None; PATCH met faces_allowed geeft 501
    en laat ook de pauze uit dezelfde vraag staan; "opnieuw" voor faces 501."""
    monkeypatch.delitem(search_hooks._admin, "faces", raising=False)
    await register.on_start(settings)
    assert "faces" not in search_hooks._admin
    assert register.register_admin(settings) is False
    headers, _ = admin
    r = await client.get(ADMIN, headers=headers)
    assert r.status_code == 200 and r.json()["faces"] is None
    for value in (True, False):
        r = await client.patch(
            ADMIN, json={"paused": True, "faces_allowed": value}, headers=headers
        )
        assert r.status_code == 501
        assert r.json()["detail"] == {
            "message": "Nog niet gebouwd (X2)",
            "code": "not_built",
            "params": {"package": "X2"},
        }
    assert (await client.get(ADMIN, headers=headers)).json()["paused"] is False
    r = await client.post(f"{ADMIN}/rebuild", json={"what": "faces"}, headers=headers)
    assert r.status_code == 501 and r.json()["detail"]["params"] == {"package": "X2"}
    assert await jobs_of(settings) == []


async def test_geen_haak_zet_iets_klaar_of_op_schijf(client, settings, huis, events):
    """De haken van gezichten met analyse uit: de grote preview, een kopie, personen op een foto
    (erbij, eraf, samenvoegen, wissen), de start en het uur. Geen job, geen rij, geen bestand
    onder DATA_ROOT, en alleen de people.changed van Z6 (die 0.8.0 ook gaf)."""
    before = files_under(settings.data_dir)
    h, foto = huis["h_eva"], str(huis["foto"])
    await register.after_large(maker(settings), "a" * 64)
    await register.after_ready(maker(settings), "a" * 64, True)
    r = await client.post(
        f"/api/v1/files/{foto}/copy", json={"parent_id": str(huis["root"])}, headers=h
    )
    assert r.status_code == 201, r.text
    for name in ("Anna", "Bram"):
        r = await client.post(f"/api/v1/files/{foto}/people", json={"name": name}, headers=h)
        assert r.status_code == 201, r.text
    people = {
        p["name"]: p["id"] for p in (await client.get("/api/v1/people", headers=h)).json()["people"]
    }
    r = await client.post(
        f"/api/v1/files/{foto}/people",
        json={"person_id": people["Anna"], "box": [0.1, 0.1, 0.2, 0.2]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    r = await client.delete(f"/api/v1/files/{foto}/people/{people['Anna']}", headers=h)
    assert r.status_code == 204, r.text
    r = await client.post(
        f"/api/v1/people/{people['Bram']}/merge", json={"into": people["Anna"]}, headers=h
    )
    assert r.status_code == 200, r.text
    r = await client.delete(f"/api/v1/people/{people['Anna']}", headers=h)
    assert r.status_code == 204, r.text
    await register.on_start(settings)
    await register.hourly(settings)
    await jobs.run_housekeeping(settings)
    assert {t for t, _lane in await jobs_of(settings)} & {
        "faces.plan",
        "faces.scan",
        "faces.group",
        "faces.wipe",
    } == set()
    assert [lane for _t, lane in await jobs_of(settings) if lane == "analyse"] == []
    assert await face_rows(settings) == 0
    assert files_under(settings.data_dir) - before == set()
    # Alleen Z6 stuurde people.changed (erbij drie keer, eraf, samenvoegen, wissen): niets van X2
    assert events == [huis["eva"]] * len(events)
    assert len(events) == 6


async def test_analyse_aan_zonder_sleutelmap_is_ook_uit(client, tmp_path, fake):
    """PLATFORM_ANALYSE_ENABLED aan, maar PLATFORM_FACES_KEYS leeg of een map die er niet is:
    niet geïnstalleerd (Keuze 68), niet een job die elke keer faalt. Er komt ook geen map."""
    for n, keys in enumerate(("", str(tmp_path / "bestaat-niet"))):
        (tmp_path / f"data-{n}").mkdir()
        s = fake.settings(tmp_path / f"data-{n}", faces_keys=keys)
        assert s.analyse_on and not state.running(s)
        assert register.register_admin(s) is False
        await upgrade(s)
        state.configure(s)
        fastapi_app.dependency_overrides[get_settings] = lambda s=s: s
        try:
            async with maker(s)() as session:
                await create_user(session, f"zonder{n}@example.nl")
                await session.commit()
            h = await login(client, f"zonder{n}@example.nl")
            r = await client.get("/api/v1/people/faces/status", headers=h)
            assert r.status_code == 409 and r.json()["detail"] == UNAVAILABLE
            r = await client.get("/api/v1/me/photo-settings", headers=h)
            assert r.json()["faces_available"] is False
            r = await client.patch("/api/v1/me/photo-settings", json={"faces": "aan"}, headers=h)
            assert r.status_code == 409 and r.json()["detail"] == UNAVAILABLE
            await register.on_start(s)
            assert await jobs_of(s) == []
        finally:
            await db.get_engine(s).dispose()
            db._engine.cache_clear()
    assert "faces" not in search_hooks._admin
    assert not (tmp_path / "bestaat-niet").exists()
