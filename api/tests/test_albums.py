"""Albums (routers/albums.py): maken, vullen met eigen foto's en video's, omslag, pagina's,
verwijderen en de events."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import events
from app.events import Hub
from app.models import AlbumItem
from tests.factories import code_of, create_file, create_folder, create_user, maker
from tests.test_files_events import drain
from tests.test_photos import login, photo

pytestmark = pytest.mark.anyio
BASE = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
async def mine(client, settings, monkeypatch):
    """Fien met vier foto's, een tekstbestand, een map en een foto in de prullenbak; Bas met
    een eigen foto."""
    monkeypatch.setattr(events, "hub", Hub())
    async with maker(settings)() as session:
        fien = await create_user(session, "fien@example.nl")
        bas = await create_user(session, "bas@example.nl")
        fotos = [
            (await photo(session, fien, f"f{i}.jpg", BASE + timedelta(days=i))).id for i in range(4)
        ]
        tekst = (await create_file(session, fien, name="a.txt")).id
        map_ = (await create_folder(session, fien, name="Map")).id
        weg = (await photo(session, fien, "weg.jpg", BASE, deleted=True)).id
        van_bas = (await photo(session, bas, "bas.jpg", BASE)).id
        await session.commit()
        ids = {"fien": fien.id, "bas": bas.id}
    return {
        "fien": await login(client, "fien@example.nl"),
        "bas": await login(client, "bas@example.nl"),
        "fotos": [str(i) for i in fotos],
        "tekst": str(tekst),
        "map": str(map_),
        "weg": str(weg),
        "van_bas": str(van_bas),
        "ids": ids,
    }


async def new_album(client, headers, name="Vakantie", **body):
    r = await client.post("/api/v1/albums", json={"name": name, **body}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def add(client, headers, album_id, ids, expect=200):
    r = await client.post(
        f"/api/v1/albums/{album_id}/items", json={"node_ids": ids}, headers=headers
    )
    assert r.status_code == expect, r.text
    return r.json()


async def test_maken_en_vullen(client, mine):
    fien = mine["fien"]
    album = await new_album(client, fien, "  Vakantie ", description="Zon")
    assert album["name"] == "Vakantie" and album["description"] == "Zon"
    assert album["permission"] == "owner" and album["count"] == 0 and album["cover"] is None
    assert album["owner"]["id"] == str(mine["ids"]["fien"]) and album["shared"] is False
    assert album["first_taken_at"] is None

    result = await add(
        client,
        fien,
        album["id"],
        [
            *mine["fotos"],
            mine["tekst"],
            mine["map"],
            mine["weg"],
            mine["van_bas"],
            str(uuid.uuid4()),
        ],
    )
    assert result["added"] == 4
    codes = {s["id"]: s["code"] for s in result["skipped"]}
    assert codes[mine["tekst"]] == "not_media" and codes[mine["map"]] == "not_media"
    assert codes[mine["weg"]] == "trashed" and codes[mine["van_bas"]] == "not_own"
    assert sorted(codes.values()).count("not_found") == 1
    again = await add(client, fien, album["id"], mine["fotos"][:1])
    assert again == {"added": 0, "skipped": [{**again["skipped"][0], "code": "already"}]}

    info = (await client.get(f"/api/v1/albums/{album['id']}", headers=fien)).json()
    assert info["count"] == 4
    assert info["first_taken_at"] == "2024-07-01T12:00:00Z"
    assert info["last_taken_at"] == "2024-07-04T12:00:00Z"
    assert info["modified_at"] >= album["modified_at"]

    items = (await client.get(f"/api/v1/albums/{album['id']}/items", headers=fien)).json()
    assert [i["id"] for i in items["items"]] == list(reversed(mine["fotos"]))
    page = (
        await client.get(f"/api/v1/albums/{album['id']}/items", params={"limit": 3}, headers=fien)
    ).json()
    rest = (
        await client.get(
            f"/api/v1/albums/{album['id']}/items",
            params={"limit": 3, "after": page["next"]},
            headers=fien,
        )
    ).json()
    assert [i["id"] for i in page["items"] + rest["items"]] == list(reversed(mine["fotos"]))
    assert rest["next"] is None
    ids = (await client.get(f"/api/v1/albums/{album['id']}/ids", headers=fien)).json()
    assert ids == {"ids": list(reversed(mine["fotos"])), "truncated": False}


async def test_omslag_naam_en_weghalen(client, mine, settings):
    fien = mine["fien"]
    album = await new_album(client, fien)
    await add(client, fien, album["id"], mine["fotos"])
    url = f"/api/v1/albums/{album['id']}"
    r = await client.patch(url, json={"cover_id": mine["van_bas"]}, headers=fien)
    assert r.status_code == 422 and code_of(r) == "cover_not_in_album"
    r = await client.patch(url, json={"cover_id": mine["fotos"][0], "name": "Zomer"}, headers=fien)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Zomer"
    r = await client.patch(url, json={"name": "a/b"}, headers=fien)
    assert r.status_code == 422 and code_of(r) == "invalid_name"

    r = await client.post(
        f"{url}/items/remove", json={"node_ids": [mine["fotos"][0], mine["van_bas"]]}, headers=fien
    )
    assert r.json() == {"removed": 1}
    info = (await client.get(url, headers=fien)).json()
    assert info["count"] == 3
    async with maker(settings)() as session:
        from app.models import Album

        assert (await session.get(Album, uuid.UUID(album["id"]))).cover_id is None
        left = await session.get(AlbumItem, (uuid.UUID(album["id"]), uuid.UUID(mine["fotos"][1])))
        assert left is not None
    # Het bestand zelf staat er nog
    r = await client.get(f"/api/v1/files/{mine['fotos'][0]}", headers=fien)
    assert r.status_code == 200


async def test_prullenbak_en_definitief_weg(client, mine):
    fien = mine["fien"]
    album = await new_album(client, fien)
    await add(client, fien, album["id"], mine["fotos"])
    url = f"/api/v1/albums/{album['id']}"
    r = await client.delete(f"/api/v1/files/{mine['fotos'][3]}", headers=fien)
    assert r.status_code == 204
    info = (await client.get(url, headers=fien)).json()
    assert info["count"] == 3
    items = (await client.get(f"{url}/items", headers=fien)).json()["items"]
    assert mine["fotos"][3] not in [i["id"] for i in items]
    # Terugzetten: weer in het album
    r = await client.post("/api/v1/trash/restore", json={"ids": [mine["fotos"][3]]}, headers=fien)
    assert r.status_code == 200
    assert (await client.get(url, headers=fien)).json()["count"] == 4
    # Definitief weg: ook uit het album
    await client.delete(f"/api/v1/files/{mine['fotos'][3]}", headers=fien)
    r = await client.delete(f"/api/v1/trash/{mine['fotos'][3]}", headers=fien)
    assert r.status_code == 204, r.text
    assert (await client.get(url, headers=fien)).json()["count"] == 3


async def test_lijst_en_verwijderen_met_events(client, mine):
    fien, bas = mine["fien"], mine["bas"]
    a = await new_album(client, fien, "A")
    b = await new_album(client, fien, "B")
    r = await client.post(
        f"/api/v1/files/{a['id']}/shares",
        json={"user_id": str(mine["ids"]["bas"]), "permission": "read"},
        headers=fien,
    )
    assert r.status_code == 201, r.text
    share_id = r.json()["id"]
    listed = (await client.get("/api/v1/albums", headers=fien)).json()["albums"]
    assert [x["name"] for x in listed] == ["B", "A"]
    assert {x["name"]: x["shared"] for x in listed} == {"A": True, "B": False}
    theirs = (await client.get("/api/v1/albums", headers=bas)).json()["albums"]
    assert [(x["name"], x["permission"], x["shared"]) for x in theirs] == [("A", "read", False)]
    assert theirs[0]["owner"]["display_name"] == "Fien"

    with (
        events.hub.subscribe(mine["ids"]["fien"]) as owner,
        events.hub.subscribe(mine["ids"]["bas"]) as viewer,
    ):
        await add(client, fien, a["id"], mine["fotos"][:1])
        assert drain(owner) == [("album.changed", {"id": a["id"]})]
        assert drain(viewer) == [("album.changed", {"id": a["id"]})]
        await add(client, fien, b["id"], mine["fotos"][:1])
        assert drain(viewer) == []  # B is niet met Bas gedeeld
        assert drain(owner) == [("album.changed", {"id": b["id"]})]
        r = await client.delete(f"/api/v1/albums/{a['id']}", headers=fien)
        assert r.status_code == 204
        assert drain(viewer) == [
            ("share.deleted", {"share_id": share_id, "node_id": a["id"]}),
            ("album.deleted", {"id": a["id"]}),
        ]
        assert drain(owner) == [("album.deleted", {"id": a["id"]})]
    assert (await client.get(f"/api/v1/albums/{a['id']}", headers=fien)).status_code == 404
    assert (await client.get("/api/v1/albums", headers=bas)).json()["albums"] == []
    # De foto zelf staat er nog
    assert (await client.get(f"/api/v1/files/{mine['fotos'][0]}", headers=fien)).status_code == 200


async def test_geen_album_en_gasten(client, mine, settings):
    fien = mine["fien"]
    for path in (f"/api/v1/albums/{mine['map']}", f"/api/v1/albums/{uuid.uuid4()}"):
        r = await client.get(path, headers=fien)
        assert r.status_code == 404 and code_of(r) == "not_found"
    async with maker(settings)() as session:
        await create_user(session, "gast@example.nl", role="guest")
        await session.commit()
    gast = await login(client, "gast@example.nl")
    r = await client.post("/api/v1/albums", json={"name": "X"}, headers=gast)
    assert r.status_code == 403 and code_of(r) == "guest"
    assert (await client.get("/api/v1/albums", headers=gast)).json() == {"albums": []}
