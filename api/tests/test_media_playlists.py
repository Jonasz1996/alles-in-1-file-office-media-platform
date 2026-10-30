"""Afspeellijsten (Keuze 37, M9): app/media/playlists.py via de routes.

- alleen van jezelf: de lijst van een ander is 404 op elke route (geen IDOR);
- toevoegen (alleen een nummer dat je mag lezen), de plek, ordenen en weghalen;
- wat je niet meer mag lezen (deling weg, prullenbak) staat er niet in maar blijft bewaard; het
  bestand definitief weg haalt de regel weg (CASCADE);
- de grens (409 media_playlist_full met max), ook bij twee vragen tegelijk (het slot);
- media uit: 503 media_off.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.main import app
from app.media import music, playlists
from app.media.state import MediaState
from app.models import MediaPlaylistItem
from tests.test_files import mkdir, put
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_music import song
from tests.test_media_scanner import library, scan, upload, world  # noqa: F401
from tests.test_media_sessions import content, maker
from tests.test_shares import person, share_with

PL = "/api/v1/media/playlists"

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def clean():
    music.reset()
    yield
    music.reset()


@pytest.fixture
async def songs(world, client):  # noqa: F811
    """Een muziekbibliotheek van de beheerder met vier nummers."""
    settings, _, headers = world
    library_id, folder = await library(client, headers, name="Muziek", kind="music")
    album = await mkdir(client, headers, folder, "Album")
    nodes = {}
    for i, title in enumerate(("Een", "Twee", "Drie", "Vier"), 1):
        nodes[title], _ = await song(
            client, headers, settings, album, f"0{i}.flac", album="Album", album_artist="Kim",
            title=title, track=str(i), duration_ms=1000 * i,
        )  # fmt: skip
    await scan(settings)
    return {"settings": settings, "headers": headers, "library": library_id, "folder": folder,
            "album": album, "nodes": nodes}  # fmt: skip


async def new_list(client, headers, name: str = "Feest") -> dict:
    r = await client.post(PL, json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def add(client, headers, playlist: str, *nodes: str, position=None, expect=200) -> dict:
    body: dict = {"node_ids": list(nodes)}
    if position is not None:
        body["position"] = position
    r = await client.post(f"{PL}/{playlist}/items", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


async def detail(client, headers, playlist: str) -> dict:
    r = await client.get(f"{PL}/{playlist}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def titles(found: dict) -> list[str]:
    return [i["track"]["title"] for i in found["items"]]


async def rows(settings, playlist: str) -> int:
    async with maker(settings)() as session:
        return await playlists.count(session, uuid.UUID(playlist))


async def test_maken_hernoemen_en_alleen_van_jezelf(songs, client):
    headers = songs["headers"]
    other, _ = await person(client, headers, "bas@example.com")
    created = await new_list(client, headers, "  Mijn   lijst ")
    assert created["name"] == "Mijn lijst" and created["item_count"] == 0
    await new_list(client, headers, "avond")
    r = await client.get(PL, headers=headers)
    assert [p["name"] for p in r.json()] == ["avond", "Mijn lijst"]
    r = await client.patch(f"{PL}/{created['id']}", json={"name": "Zomer"}, headers=headers)
    assert r.status_code == 200 and r.json()["name"] == "Zomer"
    for body in ({"name": "   "}, {"name": ""}, {"name": "x" * 201}, {}):
        r = await client.post(PL, json=body, headers=headers)
        assert r.status_code == 422, body
    r = await client.post(PL, json={"name": " \t "}, headers=headers)
    assert r.json()["detail"]["code"] == "invalid_input"
    # een ander ziet de lijst niet en kan er niets mee: 404, net als een die niet bestaat
    assert (await client.get(PL, headers=other)).json() == []
    item = (await add(client, headers, created["id"], songs["nodes"]["Een"]))["items"][0]
    pid = created["id"]
    for method, url, body in (
        ("GET", f"{PL}/{pid}", None),
        ("PATCH", f"{PL}/{pid}", {"name": "Van mij"}),
        ("POST", f"{PL}/{pid}/items", {"node_ids": [songs["nodes"]["Twee"]]}),
        ("PUT", f"{PL}/{pid}/order", {"item_ids": [item["id"]]}),
        ("DELETE", f"{PL}/{pid}/items/{item['id']}", None),
        ("DELETE", f"{PL}/{pid}", None),
        ("GET", f"{PL}/{uuid.uuid4()}", None),
    ):
        r = await client.request(method, url, json=body, headers=other)
        assert r.status_code == 404, (method, url, r.text)
        assert r.json()["detail"]["code"] == "not_found"
    assert titles(await detail(client, headers, pid)) == ["Een"]
    # weggooien
    assert (await client.delete(f"{PL}/{pid}", headers=headers)).status_code == 204
    assert (await client.get(f"{PL}/{pid}", headers=headers)).status_code == 404
    assert [p["name"] for p in (await client.get(PL, headers=headers)).json()] == ["avond"]
    # zonder login 401
    assert (await client.get(PL)).status_code == 401


async def test_toevoegen_de_plek_ordenen_en_weghalen(songs, client):
    headers, n = songs["headers"], songs["nodes"]
    pid = (await new_list(client, headers))["id"]
    out = await add(client, headers, pid, n["Een"], n["Twee"])
    assert [i["track"]["title"] for i in out["items"]] == ["Een", "Twee"]
    assert out["items"][0]["track"]["artist"] is None  # alleen album_artist als tag
    assert out["items"][0]["track"]["album_id"] is None  # nog geen regroup
    # vooraan, en in het midden
    await add(client, headers, pid, n["Drie"], position=0)
    await add(client, headers, pid, n["Vier"], n["Een"], position=2)
    found = await detail(client, headers, pid)
    assert titles(found) == ["Drie", "Een", "Vier", "Een", "Twee"]
    assert found["playlist"]["item_count"] == 5
    assert found["playlist"]["duration_ms"] == 3000 + 1000 + 4000 + 1000 + 2000
    # een plek voorbij het eind is achteraan
    await add(client, headers, pid, n["Twee"], position=99)
    found = await detail(client, headers, pid)
    assert titles(found)[-1] == "Twee"
    ids = [i["id"] for i in found["items"]]
    # ordenen: precies de regels, elk één keer
    r = await client.put(f"{PL}/{pid}/order", json={"item_ids": ids[::-1]}, headers=headers)
    assert r.status_code == 204, r.text
    assert titles(await detail(client, headers, pid)) == ["Twee", "Twee", "Een", "Vier", "Een",
                                                         "Drie"]  # fmt: skip
    for wrong in (ids[1:], [*ids, ids[0]], [*ids[1:], str(uuid.uuid4())], []):
        r = await client.put(f"{PL}/{pid}/order", json={"item_ids": wrong}, headers=headers)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_input"
    # weghalen
    r = await client.delete(f"{PL}/{pid}/items/{ids[0]}", headers=headers)
    assert r.status_code == 204
    r = await client.delete(f"{PL}/{pid}/items/{ids[0]}", headers=headers)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
    assert len((await detail(client, headers, pid))["items"]) == 5
    # in de lijst van alle lijsten de telling
    [summary] = (await client.get(PL, headers=headers)).json()
    assert summary["item_count"] == 5


async def test_alleen_nummers_die_je_mag_lezen(songs, client):
    headers, settings, n = songs["headers"], songs["settings"], songs["nodes"]
    pid = (await new_list(client, headers))["id"]
    root = (await client.get("/api/v1/files/root", headers=headers)).json()["folder"]["id"]
    loose = (await put(client, headers, root, "los.flac", content("los", 100))).json()["id"]
    text = await upload(client, headers, songs["album"], "tekst.txt", b"hallo")
    await scan(settings)
    other, _ = await person(client, headers, "bas@example.com")
    other_root = (await client.get("/api/v1/files/root", headers=other)).json()["folder"]["id"]
    theirs = (await put(client, other, other_root, "b.flac", content("b", 100))).json()["id"]
    for bad in (loose, text, songs["album"], theirs, str(uuid.uuid4())):
        r = await client.post(
            f"{PL}/{pid}/items", json={"node_ids": [n["Een"], bad]}, headers=headers
        )
        assert r.status_code == 404, (bad, r.text)
        assert r.json()["detail"]["code"] == "not_found"
    assert await rows(settings, pid) == 0  # er kwam niets bij
    r = await client.post(f"{PL}/{pid}/items", json={"node_ids": []}, headers=headers)
    assert r.status_code == 422
    r = await client.post(
        f"{PL}/{pid}/items", json={"node_ids": [n["Een"]], "position": -1}, headers=headers
    )
    assert r.status_code == 422


async def test_wat_je_niet_meer_mag_lezen_blijft_bewaard(songs, client):
    headers, settings, n = songs["headers"], songs["settings"], songs["nodes"]
    other, other_id = await person(client, headers, "bas@example.com")
    share = await share_with(client, headers, songs["folder"], user_id=other_id, permission="read")
    pid = (await new_list(client, other, "Van Bas"))["id"]
    await add(client, other, pid, n["Een"], n["Twee"])
    assert titles(await detail(client, other, pid)) == ["Een", "Twee"]
    # de deling weg: niets te zien, de regels blijven
    assert (
        await client.delete(f"/api/v1/shares/{share['id']}", headers=headers)
    ).status_code == 204
    found = await detail(client, other, pid)
    assert found["items"] == [] and found["playlist"]["item_count"] == 0
    assert (await client.get(PL, headers=other)).json()[0]["item_count"] == 0
    assert await rows(settings, pid) == 2
    # wat je niet ziet, kun je niet ordenen of toevoegen
    r = await client.put(f"{PL}/{pid}/order", json={"item_ids": []}, headers=other)
    assert r.status_code == 204
    await add(client, other, pid, n["Drie"], expect=404)
    # weer gedeeld: terug op hun plek
    await share_with(client, headers, songs["folder"], user_id=other_id, permission="read")
    assert titles(await detail(client, other, pid)) == ["Een", "Twee"]
    # in de prullenbak (de eigenaar ziet het ook niet meer in zijn eigen lijst)
    mine = (await new_list(client, headers))["id"]
    await add(client, headers, mine, n["Een"], n["Drie"])
    assert (await client.delete(f"/api/v1/files/{n['Een']}", headers=headers)).status_code == 204
    assert titles(await detail(client, other, pid)) == ["Twee"]
    assert titles(await detail(client, headers, mine)) == ["Drie"]
    # ordenen met wat je ziet: de regel die je niet ziet, houdt zijn plek
    visible = [i["id"] for i in (await detail(client, headers, mine))["items"]]
    r = await client.put(f"{PL}/{mine}/order", json={"item_ids": visible}, headers=headers)
    assert r.status_code == 204
    r = await client.post(f"/api/v1/files/{n['Een']}/restore", headers=headers)
    assert r.status_code == 200
    assert titles(await detail(client, headers, mine)) == ["Een", "Drie"]
    # definitief weg: de regels gaan mee (CASCADE)
    assert (await client.delete(f"/api/v1/files/{n['Een']}", headers=headers)).status_code == 204
    r = await client.delete(f"/api/v1/trash/{n['Een']}", headers=headers)
    assert r.status_code == 204, r.text
    assert await rows(settings, pid) == 1 and await rows(settings, mine) == 1
    assert titles(await detail(client, headers, mine)) == ["Drie"]


async def test_de_grens(songs, client, monkeypatch):
    headers, settings, n = songs["headers"], songs["settings"], songs["nodes"]
    monkeypatch.setattr(playlists, "MAX_ITEMS", 3)
    pid = (await new_list(client, headers))["id"]
    await add(client, headers, pid, n["Een"], n["Twee"])
    r = await client.post(
        f"{PL}/{pid}/items", json={"node_ids": [n["Drie"], n["Vier"]]}, headers=headers
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "media_playlist_full"
    assert r.json()["detail"]["params"] == {"max": 3}
    assert await rows(settings, pid) == 2
    await add(client, headers, pid, n["Drie"])
    await add(client, headers, pid, n["Vier"], expect=409)
    # wat je niet meer ziet, telt mee (het staat er nog)
    assert (await client.delete(f"/api/v1/files/{n['Een']}", headers=headers)).status_code == 204
    await add(client, headers, pid, n["Vier"], expect=409)


async def test_twee_vragen_tegelijk_gaan_niet_samen_over_de_grens(songs, client, monkeypatch):
    headers, settings, n = songs["headers"], songs["settings"], songs["nodes"]
    monkeypatch.setattr(playlists, "MAX_ITEMS", 3)
    pid = (await new_list(client, headers))["id"]
    body_a = {"node_ids": [n["Een"], n["Twee"]]}
    body_b = {"node_ids": [n["Drie"], n["Vier"]]}
    await playlists._lock.acquire()
    try:
        tasks = [
            asyncio.ensure_future(client.post(f"{PL}/{pid}/items", json=b, headers=headers))
            for b in (body_a, body_b)
        ]
        for _ in range(500):
            if len(playlists._lock._waiters or ()) == 2:
                break
            await asyncio.sleep(0.01)
        assert len(playlists._lock._waiters or ()) == 2
    finally:
        playlists._lock.release()
    results = await asyncio.gather(*tasks)
    assert sorted(r.status_code for r in results) == [200, 409]
    assert await rows(settings, pid) == 2


async def test_media_uit(songs, client, monkeypatch):
    headers = songs["headers"]
    pid = (await new_list(client, headers))["id"]
    monkeypatch.setattr(app.state, "media", MediaState(enabled=False), raising=False)
    for method, url in (("GET", PL), ("GET", f"{PL}/{pid}"), ("DELETE", f"{PL}/{pid}")):
        r = await client.request(method, url, headers=headers)
        assert r.status_code == 503 and r.json()["detail"]["code"] == "media_off"


async def test_een_regel_van_een_andere_lijst(songs, client):
    """Een regel weghalen via een lijst waar hij niet in staat: 404, en hij blijft."""
    headers, settings, n = songs["headers"], songs["settings"], songs["nodes"]
    one = (await new_list(client, headers, "een"))["id"]
    two = (await new_list(client, headers, "twee"))["id"]
    [item] = (await add(client, headers, one, n["Een"]))["items"]
    r = await client.delete(f"{PL}/{two}/items/{item['id']}", headers=headers)
    assert r.status_code == 404
    assert await rows(settings, one) == 1
    async with maker(settings)() as session:
        assert await session.get(MediaPlaylistItem, uuid.UUID(item["id"])) is not None
