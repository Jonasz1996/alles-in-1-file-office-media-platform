"""Profielen per account (fase 11, stap 7): eigen voortgang, kiezen met een pincode, beheren."""

from __future__ import annotations

import uuid

import pytest

from app.media import viewer
from app.models import MediaFacts, MediaItem
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_progress_api import MIN, Seq, films, home, save, titles, w  # noqa: F401
from tests.test_media_scanner import world  # noqa: F401
from tests.test_media_sessions import maker, profile_json
from tests.test_shares import person

pytestmark = pytest.mark.anyio

URL = "/api/v1/media/profiles"


@pytest.fixture(autouse=True)
def _pins():
    viewer.pin_reset()
    yield
    viewer.pin_reset()


async def select(client, headers, pid, pin=None) -> dict:
    r = await client.post(f"{URL}/{pid}/select", json={"pin": pin}, headers=headers)
    assert r.status_code == 200, r.text
    return {**headers, viewer.HEADER: r.json()["token"]}


async def rate(settings, ages: dict[str, int]) -> None:
    """Een TMDB-id en een kijkwijzer voor deze films (zoals facts.tick ze zou vinden)."""
    async with maker(settings)() as session:
        for n, (node, age) in enumerate(ages.items(), start=1):
            item = await session.get(MediaItem, uuid.UUID(node))
            item.ids = {"tmdb": str(n)}
            session.add(MediaFacts(kind="movie", tmdb_id=str(n), age=age))
        await session.commit()


async def test_profielen_hebben_hun_eigen_voortgang(w, client):  # noqa: F811
    settings, jonas = w
    r = await client.get(URL, headers=jonas)
    assert r.status_code == 200, r.text
    start = r.json()
    assert start["current"] == "self" and start["can_manage"] is True
    assert [p["id"] for p in start["profiles"]] == ["self"]

    r = await client.post(URL, json={"name": "Kinderen", "max_age": 9}, headers=jonas)
    assert r.status_code == 201, r.text
    kids_id = r.json()["id"]
    r = await client.post(URL, json={"name": "Papa", "pin": "1234"}, headers=jonas)
    papa_id = r.json()["id"]
    assert r.json()["has_pin"] is True

    kids = await select(client, jonas, kids_id)
    now = (await client.get(URL, headers=kids)).json()
    assert now["current"] == kids_id and now["max_age"] == 9 and now["can_manage"] is False
    r = await client.post(URL, json={"name": "Stiekem"}, headers=kids)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "media_profile_locked"

    _, nodes = await films(client, jonas, settings, ["Een (2001)", "Twee (2002)"])
    await rate(settings, {nodes["Een (2001)"]: 0, nodes["Twee (2002)"]: 0})
    tab = Seq()
    assert (await save(client, kids, nodes["Een (2001)"], 5 * MIN, seq=tab())).status_code == 204
    assert titles((await home(client, kids))["continue"]) == ["Een"]
    assert (await home(client, jonas))["continue"] == []  # het account zelf: niets begonnen

    # Een pincode: fout is 403, en na 5 keer even niet
    r = await client.post(f"{URL}/{papa_id}/select", json={"pin": "0000"}, headers=jonas)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "media_profile_pin"
    papa = await select(client, jonas, papa_id, "1234")
    assert (await client.get(URL, headers=papa)).json()["current"] == papa_id
    for _ in range(5):
        await client.post(f"{URL}/{papa_id}/select", json={"pin": "0000"}, headers=jonas)
    r = await client.post(f"{URL}/{papa_id}/select", json={"pin": "1234"}, headers=jonas)
    assert r.status_code == 429
    viewer.pin_reset()

    # Een vervalst token telt niet: het account zelf
    fake = {**jonas, viewer.HEADER: f"{papa_id}.{'0' * 32}"}
    assert (await client.get(URL, headers=fake)).json()["current"] == "self"

    # Een pincode op het account zelf: zonder token het strengste profiel
    r = await client.patch(f"{URL}/self", json={"pin": "9999"}, headers=jonas)
    assert r.status_code == 200, r.text
    assert (await client.get(URL, headers=jonas)).json()["current"] == kids_id
    me = await select(client, jonas, "self", "9999")
    assert (await client.get(URL, headers=me)).json()["can_manage"] is True
    # Een nieuwe pincode maakt oude tokens ongeldig
    r = await client.patch(f"{URL}/{papa_id}", json={"pin": "5678"}, headers=me)
    assert r.status_code == 200
    assert (await client.get(URL, headers=papa)).json()["current"] == kids_id

    # Weghalen: met zijn voortgang
    assert (await client.delete(f"{URL}/{kids_id}", headers=me)).status_code == 204
    assert (await client.delete(f"{URL}/self", headers=me)).status_code == 422


async def test_een_beheerder_zet_een_grens_op_een_account(w, client):  # noqa: F811
    _, jonas = w
    bas, bas_id = await person(client, jonas, "bas@example.nl")
    r = await client.put(f"/api/v1/admin/media/profiles/{bas_id}", json={"max_age": 12},
                         headers=jonas)  # fmt: skip
    assert r.status_code == 204, r.text
    now = (await client.get(URL, headers=bas)).json()
    assert now["max_age"] == 12 and now["can_manage"] is False
    r = await client.get("/api/v1/admin/media/profiles", headers=jonas)
    assert r.json() == [{"user_id": bas_id, "max_age": 12}]
    # Bas mag de grens niet zelf weghalen
    r = await client.put(f"/api/v1/admin/media/profiles/{bas_id}", json={"max_age": None},
                         headers=bas)  # fmt: skip
    assert r.status_code == 403
    r = await client.put(f"/api/v1/admin/media/profiles/{bas_id}", json={"max_age": None},
                         headers=jonas)  # fmt: skip
    assert (await client.get(URL, headers=bas)).json()["max_age"] is None


async def test_kinderslot(w, client):  # noqa: F811
    settings, jonas = w
    library_id, nodes = await films(
        client, jonas, settings, ["Nemo (2003)", "Alien (1979)", "Onbekend (2001)"]
    )
    async with maker(settings)() as session:
        for name, number in (("Nemo (2003)", "12"), ("Alien (1979)", "348"),
                             ("Onbekend (2001)", "999")):  # fmt: skip
            item = await session.get(MediaItem, uuid.UUID(nodes[name]))
            item.ids = {"tmdb": number}
        session.add(MediaFacts(kind="movie", tmdb_id="12", age=0))
        session.add(MediaFacts(kind="movie", tmdb_id="348", age=16))
        await session.commit()
    r = await client.post(URL, json={"name": "Kinderen", "max_age": 12}, headers=jonas)
    kids = await select(client, jonas, r.json()["id"])

    async def listed(headers) -> list[str]:
        r = await client.get(f"/api/v1/media/libraries/{library_id}/items", headers=headers)
        assert r.status_code == 200, r.text
        return sorted(c["title"] for c in r.json()["items"])

    assert await listed(jonas) == ["Alien", "Nemo", "Onbekend"]
    assert await listed(kids) == ["Nemo"]  # zonder kijkwijzer: niet met een grens
    assert titles((await home(client, kids))["recent"]) == ["Nemo"]
    alien = nodes["Alien (1979)"]
    assert (await client.get(f"/api/v1/media/items/{alien}", headers=kids)).status_code == 404
    assert (await client.get(f"/api/v1/media/items/{alien}", headers=jonas)).status_code == 200
    body = {"profile": profile_json()}
    r = await client.post(f"/api/v1/media/{alien}/play", json=body, headers=kids)
    assert r.status_code == 404, r.text
    r = await client.post(f"/api/v1/media/{alien}/play", json=body, headers=jonas)
    assert r.status_code != 404, r.text  # zonder grens wel


async def test_statistieken(w, client):  # noqa: F811
    settings, jonas = w
    _, nodes = await films(client, jonas, settings, ["Een (2001)", "Twee (2002)"])
    tab = Seq()
    assert (await save(client, jonas, nodes["Een (2001)"], 30 * MIN, seq=tab())).status_code == 204
    r = await client.get("/api/v1/admin/media/stats", headers=jonas)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [w["title"] for w in body["recent"]] == ["Een"]
    assert body["users"][0]["hours"] == 0.5 and body["users"][0]["titles"] == 1
    bas, _ = await person(client, jonas, "bas@example.nl")
    assert (await client.get("/api/v1/admin/media/stats", headers=bas)).status_code == 403
