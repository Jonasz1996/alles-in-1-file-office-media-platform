"""GET /shares: alles wat je zelf deelt, met de node en het pad erboven; niet wat in de
prullenbak ligt; gasten 403."""

import pytest

from tests.factories import code_of
from tests.test_files import mkdir, put, root
from tests.test_public import new_link
from tests.test_shares import group_of, person, share_with


@pytest.mark.anyio
async def test_mijn_delingen(client, admin):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    work = await mkdir(client, jonas, root_id, "Werk")
    sub = await mkdir(client, jonas, work, "Plannen")
    doc = (await put(client, jonas, sub, "plan.txt", b"plan")).json()["id"]
    gone = await mkdir(client, jonas, root_id, "Weg")
    _, anna = await person(client, jonas, "anna@example.nl")
    _, piet = await person(client, jonas, "piet@example.nl")
    group = await group_of(client, jonas, "Team", anna, piet)
    s1 = await share_with(client, jonas, work, user_id=anna, permission="write")
    s2 = await share_with(client, jonas, doc, group_id=group, permission="read")
    link = await new_link(client, jonas, sub, permission="read", label="voor de klant")
    await share_with(client, jonas, gone, user_id=piet, permission="read")
    assert (await client.delete(f"/api/v1/files/{gone}", headers=jonas)).status_code == 204
    # Van Anna zelf: niet van Jonas
    anna_h, _ = await person(client, jonas, "an@example.nl")
    hers = await mkdir(client, anna_h, (await root(client, anna_h))["folder"]["id"], "Van An")
    await share_with(client, anna_h, hers, user_id=piet, permission="read")

    r = await client.get("/api/v1/shares", headers=jonas)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [row["share"]["id"] for row in rows] == [link["id"], s2["id"], s1["id"]]
    by_id = {row["share"]["id"]: row for row in rows}
    assert by_id[s1["id"]]["node"]["name"] == "Werk"
    assert by_id[s1["id"]]["path"] == []
    assert by_id[s1["id"]]["share"]["user"]["display_name"] == "Anna"
    assert by_id[s2["id"]]["path"] == [
        {"id": work, "name": "Werk"},
        {"id": sub, "name": "Plannen"},
    ]
    assert by_id[s2["id"]]["share"]["group"]["member_count"] == 2
    assert by_id[link["id"]]["share"]["link"]["label"] == "voor de klant"
    assert by_id[link["id"]]["node"]["shared"] is True


@pytest.mark.anyio
async def test_leeg_en_gasten(client, admin):
    jonas, _ = admin
    assert (await client.get("/api/v1/shares", headers=jonas)).json() == []
    guest, _ = await person(client, jonas, "gast@example.nl", role="guest")
    r = await client.get("/api/v1/shares", headers=guest)
    assert r.status_code == 403 and code_of(r) == "guest"
