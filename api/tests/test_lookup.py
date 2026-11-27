"""POST /files/lookup: mappen en bestanden zoals de vrager ze nu ziet (voor live bijwerken
van een open map), met de toegang van delingen en zonder wat in de prullenbak ligt."""

import uuid

import pytest

from tests.test_files import mkdir, put, root
from tests.test_shares import person, share_with


async def lookup(client, headers, *ids, expect=200) -> list[dict]:
    r = await client.post("/api/v1/files/lookup", json={"ids": list(ids)}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()["nodes"] if expect == 200 else []


@pytest.mark.anyio
async def test_eigen_nodes_in_volgorde_zonder_prullenbak(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = await mkdir(client, headers, root_id, "A")
    b = (await put(client, headers, a, "b.txt", b"b")).json()["id"]
    c = (await put(client, headers, root_id, "c.txt", b"c")).json()["id"]
    weg = await mkdir(client, headers, root_id, "Weg")
    onder = (await put(client, headers, weg, "onder.txt", b"o")).json()["id"]
    los = (await put(client, headers, root_id, "los.txt", b"l")).json()["id"]

    found = await lookup(client, headers, c, a, str(uuid.uuid4()), b, c)
    assert [n["id"] for n in found] == [c, a, b]
    single = (await client.get(f"/api/v1/files/{b}", headers=headers)).json()
    assert found[2] == single

    await client.delete(f"/api/v1/files/{weg}", headers=headers)
    await client.delete(f"/api/v1/files/{los}", headers=headers)
    assert await lookup(client, headers, weg, onder, los) == []
    assert await lookup(client, headers) == []
    await lookup(client, headers, *[str(uuid.uuid4()) for _ in range(501)], expect=422)


@pytest.mark.anyio
async def test_volgt_delingen(client, admin):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    gedeeld = await mkdir(client, jonas, root_id, "Gedeeld")
    binnen = (await put(client, jonas, gedeeld, "binnen.txt", b"x")).json()["id"]
    buiten = (await put(client, jonas, root_id, "buiten.txt", b"y")).json()["id"]
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    share = await share_with(client, jonas, gedeeld, user_id=anna_id, permission="read")

    found = await lookup(client, anna, gedeeld, binnen, buiten)
    assert [n["id"] for n in found] == [gedeeld, binnen]
    by_id = {n["id"]: n for n in found}
    # De gedeelde map is zijn bovenste map: geen ouder; daarbinnen wel, met het recht
    assert by_id[gedeeld]["parent_id"] is None
    assert by_id[binnen]["parent_id"] == gedeeld
    assert {n["permission"] for n in found} == {"read"}
    assert found == [
        (await client.get(f"/api/v1/files/{i}", headers=anna)).json() for i in (gedeeld, binnen)
    ]

    # In de prullenbak van de eigenaar: ook voor haar weg
    await client.delete(f"/api/v1/files/{binnen}", headers=jonas)
    assert [n["id"] for n in await lookup(client, anna, gedeeld, binnen)] == [gedeeld]
    # Deling ingetrokken: niets meer
    r = await client.delete(f"/api/v1/shares/{share['id']}", headers=jonas)
    assert r.status_code == 204, r.text
    assert await lookup(client, anna, gedeeld) == []
