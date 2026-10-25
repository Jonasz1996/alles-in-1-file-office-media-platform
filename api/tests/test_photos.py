"""De fototijdlijn (routers/photos.py): eigen foto's en video's op opnamedatum, zonder wat
in de prullenbak ligt, in pagina's met een cursor."""

import base64
from datetime import UTC, datetime, timedelta

import pytest

from app.routers import photos
from tests.factories import code_of, create_file, create_folder, create_user, maker

pytestmark = pytest.mark.anyio
BASE = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)


async def login(client, email: str) -> dict:
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "lang-genoeg-123"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def photo(session, owner, name, taken_at, parent_id=None, *, mime="image/jpeg", **kw):
    return await create_file(
        session,
        owner,
        parent_id,
        name,
        data=f"{owner.id}-{name}".encode(),
        mime=mime,
        taken_at=taken_at,
        **kw,
    )


def cursor(item: dict) -> str:
    """Zoals de web-app hem maakt: base64url("<dag>|<taken_at>|<id>")."""
    raw = f"{item['taken_day']}|{item['taken_at']}|{item['id']}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


@pytest.fixture
async def library(client, settings):
    """Fien met foto's in juli en augustus 2024, een video, een tekstbestand, een foto in de
    prullenbak en een onder een map in de prullenbak; Bas met een eigen foto."""
    async with maker(settings)() as session:
        fien = await create_user(session, "fien@example.nl")
        bas = await create_user(session, "bas@example.nl")
        reis = await create_folder(session, fien, name="Reis")
        weg = await create_folder(session, fien, name="Weg", deleted=True)
        diep = await create_folder(session, fien, weg.id, name="Diep")
        ids = {}
        for i in range(5):
            node = await photo(session, fien, f"juli-{i}.jpg", BASE + timedelta(hours=i), reis.id)
            ids[f"juli-{i}"] = node.id
        ids["aug"] = (await photo(session, fien, "aug.jpg", BASE + timedelta(days=40))).id
        ids["film"] = (
            await photo(session, fien, "film.mp4", BASE + timedelta(days=41), mime="video/mp4")
        ).id
        await create_file(session, fien, name="notities.txt")
        await photo(session, fien, "prullenbak.jpg", BASE, deleted=True)
        await photo(session, fien, "onder-weg.jpg", BASE, diep.id)
        await photo(session, bas, "van-bas.jpg", BASE)
        await session.commit()
    return {"fien": await login(client, "fien@example.nl"), "ids": ids}


async def test_maanden_nieuwste_eerst(client, library):
    r = await client.get("/api/v1/photos/months", headers=library["fien"])
    assert r.status_code == 200, r.text
    assert r.json() == {
        "months": [{"month": "2024-08", "count": 2}, {"month": "2024-07", "count": 5}]
    }


async def test_tijdlijn_alleen_eigen_en_levend(client, library):
    r = await client.get("/api/v1/photos", headers=library["fien"])
    items = r.json()["items"]
    names = [i["name"] for i in items]
    assert names == ["film.mp4", "aug.jpg", "juli-4.jpg", "juli-3.jpg", "juli-2.jpg",
                     "juli-1.jpg", "juli-0.jpg"]  # fmt: skip
    assert r.json()["next"] is None
    film = items[0]
    assert film["media"] == "video" and items[1]["media"] == "image"
    assert film["size"] > 0 and film["modified_at"].endswith("Z")
    assert film["taken_at"] == "2024-08-11T12:00:00Z" and film["taken_day"] == 20240811
    assert film["etag"].startswith('"') and film["shared"] is False
    assert film["thumb"] is None  # nog geen preview


async def test_pagina_s_met_cursor(client, library):
    headers = library["fien"]
    full = (await client.get("/api/v1/photos", headers=headers)).json()["items"]
    seen, after = [], None
    for _ in range(10):
        params = {"limit": 2, **({"after": after} if after else {})}
        page = (await client.get("/api/v1/photos", params=params, headers=headers)).json()
        seen += page["items"]
        after = page["next"]
        if after is None:
            break
    assert [i["id"] for i in seen] == [i["id"] for i in full]

    # Per maand, ook in pagina's
    page = (
        await client.get("/api/v1/photos", params={"month": "2024-07", "limit": 3}, headers=headers)
    ).json()
    assert [i["name"] for i in page["items"]] == ["juli-4.jpg", "juli-3.jpg", "juli-2.jpg"]
    page = (
        await client.get(
            "/api/v1/photos",
            params={"month": "2024-07", "limit": 3, "after": page["next"]},
            headers=headers,
        )
    ).json()
    assert [i["name"] for i in page["items"]] == ["juli-1.jpg", "juli-0.jpg"]
    assert page["next"] is None


async def test_ongeldige_invoer(client, library):
    headers = library["fien"]
    for params in ({"month": "2024-13"}, {"month": "juli"}, {"after": "!!!"}, {"limit": 501}):
        r = await client.get("/api/v1/photos", params=params, headers=headers)
        assert r.status_code == 422 and code_of(r) == "invalid_input", (params, r.text)


async def test_reeks_van_ids(client, library, monkeypatch):
    headers = library["fien"]
    items = (await client.get("/api/v1/photos", headers=headers)).json()["items"]
    # Van aug.jpg tot en met juli-1.jpg, in welke volgorde ook
    for a, b in ((items[1], items[5]), (items[5], items[1])):
        r = await client.get(
            "/api/v1/photos/ids", params={"from": cursor(a), "to": cursor(b)}, headers=headers
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"ids": [i["id"] for i in items[1:6]], "truncated": False}
    monkeypatch.setattr(photos, "MAX_IDS", 2)
    r = await client.get(
        "/api/v1/photos/ids", params={"from": cursor(items[0]), "to": cursor(items[-1])},
        headers=headers,
    )  # fmt: skip
    assert r.json() == {"ids": [items[0]["id"], items[1]["id"]], "truncated": True}


async def test_gast_krijgt_403(client, admin, settings):
    async with maker(settings)() as session:
        await create_user(session, "gast@example.nl", role="guest")
        await session.commit()
    headers = await login(client, "gast@example.nl")
    for path in ("/api/v1/photos", "/api/v1/photos/months"):
        r = await client.get(path, headers=headers)
        assert r.status_code == 403 and code_of(r) == "guest", r.text


async def test_teruggezet_staat_er_weer(client, library, settings):
    """Een map uit de prullenbak terugzetten zet zijn foto's terug op de tijdlijn."""
    headers = library["fien"]
    r = await client.get("/api/v1/files/root", headers=headers)
    reis = next(c for c in r.json()["children"] if c["name"] == "Reis")
    r = await client.delete(f"/api/v1/files/{reis['id']}", headers=headers)
    assert r.status_code == 204, r.text
    months = (await client.get("/api/v1/photos/months", headers=headers)).json()["months"]
    assert months == [{"month": "2024-08", "count": 2}]
    r = await client.post("/api/v1/trash/restore", json={"ids": [reis["id"]]}, headers=headers)
    assert r.status_code == 200, r.text
    months = (await client.get("/api/v1/photos/months", headers=headers)).json()["months"]
    assert months[1] == {"month": "2024-07", "count": 5}
