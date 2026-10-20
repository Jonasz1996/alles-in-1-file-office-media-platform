"""Live gebeurtenissen bij delen: wie het via een deling ziet, hoort het ook, maar nooit iets
over wat erboven ligt."""

import uuid

import pytest

from app import events, jobs
from app.events import Hub
from tests.test_files import mkdir, put, root
from tests.test_files_events import drain
from tests.test_previews import picture
from tests.test_shares import group_of, person, share_with


@pytest.fixture
def hub(monkeypatch):
    fresh = Hub()
    monkeypatch.setattr(events, "hub", fresh)
    return fresh


@pytest.mark.anyio
async def test_wijzigingen_naar_wie_het_ziet(client, admin, hub):
    jonas, tokens = admin
    jonas_id = tokens["user"]["id"]
    root_id = (await root(client, jonas))["folder"]["id"]
    werk = await mkdir(client, jonas, root_id, "Werk")
    gedeeld = await mkdir(client, jonas, werk, "Gedeeld")
    los = (await put(client, jonas, werk, "los.txt", b"l")).json()["id"]
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    bert, bert_id = await person(client, jonas, "bert@example.nl")

    with (
        hub.subscribe(uuid.UUID(jonas_id)) as j,
        hub.subscribe(uuid.UUID(anna_id)) as a,
        hub.subscribe(uuid.UUID(bert_id)) as b,
    ):
        share = await share_with(client, jonas, gedeeld, user_id=anna_id, permission="write")
        assert drain(a) == [("share.created", {"share_id": share["id"], "node_id": gedeeld})]
        await client.patch(
            f"/api/v1/shares/{share['id']}", json={"permission": "write"}, headers=jonas
        )
        assert drain(a) == [("share.changed", {"share_id": share["id"], "node_id": gedeeld})]
        assert drain(j) == []

        # Anna maakt iets in de gedeelde map: Jonas en zij horen het
        f = (await put(client, anna, gedeeld, "f.txt", b"f")).json()["id"]
        assert drain(j) == [("node.created", {"id": f, "parent_id": gedeeld})]
        assert drain(a) == [("node.created", {"id": f, "parent_id": gedeeld})]

        # De gedeelde map zelf hernoemd: zij ziet niet waar hij in staat
        await client.patch(f"/api/v1/files/{gedeeld}", json={"name": "Samen"}, headers=jonas)
        assert drain(j) == [("node.changed", {"id": gedeeld, "parent_id": werk})]
        assert drain(a) == [("node.changed", {"id": gedeeld, "parent_id": None})]

        # Eruit verplaatst: zij hoort waar het wegging, niet waarheen
        await client.patch(f"/api/v1/files/{f}", json={"parent_id": werk}, headers=jonas)
        assert drain(j) == [
            ("node.changed", {"id": f, "parent_id": werk, "old_parent_id": gedeeld})
        ]
        assert drain(a) == [
            ("node.changed", {"id": f, "parent_id": None, "old_parent_id": gedeeld})
        ]
        # Erin verplaatst: zij hoort niet waar het vandaan kwam
        await client.patch(f"/api/v1/files/{los}", json={"parent_id": gedeeld}, headers=jonas)
        assert drain(a) == [("node.changed", {"id": los, "parent_id": gedeeld})]
        drain(j)
        # Iets buiten de gedeelde map: alleen Jonas
        await client.patch(f"/api/v1/files/{f}", json={"name": "g.txt"}, headers=jonas)
        assert drain(a) == []
        assert len(drain(j)) == 1

        # Weggooien en definitief verwijderen (ook van de gedeelde map zelf)
        await client.delete(f"/api/v1/files/{los}", headers=anna)
        assert drain(a) == [("node.deleted", {"id": los, "parent_id": gedeeld})]
        assert drain(j) == [("node.deleted", {"id": los, "parent_id": gedeeld})]
        await client.delete(f"/api/v1/trash/{los}", headers=jonas)
        assert drain(a) == [("node.deleted", {"id": los, "parent_id": gedeeld})]
        drain(j)
        await client.delete(f"/api/v1/files/{gedeeld}", headers=jonas)
        await client.delete(f"/api/v1/trash/{gedeeld}", headers=jonas)
        assert drain(a) == [
            ("node.deleted", {"id": gedeeld, "parent_id": None}),
            ("node.deleted", {"id": gedeeld, "parent_id": None}),
        ]
        assert drain(j) == [
            ("node.deleted", {"id": gedeeld, "parent_id": werk}),
            ("node.deleted", {"id": gedeeld, "parent_id": werk}),
        ]
        # Bert ziet niets en hoort niets
        assert drain(b) == []


@pytest.mark.anyio
async def test_delingen_en_groepen_worden_gemeld(client, admin, hub):
    jonas, tokens = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, root_id, "Team")
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    bert, bert_id = await person(client, jonas, "bert@example.nl")
    group = await group_of(client, jonas, "Team", anna_id)

    with hub.subscribe(uuid.UUID(anna_id)) as a, hub.subscribe(uuid.UUID(bert_id)) as b:
        share = await share_with(client, jonas, folder, group_id=group, permission="read")
        expected = ("share.created", {"share_id": share["id"], "node_id": folder})
        assert drain(a) == [expected]
        assert drain(b) == []
        # Bert erbij, Anna eruit
        r = await client.put(
            f"/api/v1/groups/{group}/members", json={"user_ids": [bert_id]}, headers=jonas
        )
        assert r.status_code == 200
        assert drain(b) == [expected]
        assert drain(a) == [("share.deleted", {"share_id": share["id"], "node_id": folder})]
        # Links: niemand om het te vertellen
        r = await client.post(
            f"/api/v1/files/{folder}/links", json={"permission": "read"}, headers=jonas
        )
        await client.delete(f"/api/v1/shares/{r.json()['id']}", headers=jonas)
        assert drain(a) == [] and drain(b) == []
        # De groep weg: de deling ook
        assert (await client.delete(f"/api/v1/groups/{group}", headers=jonas)).status_code == 204
        assert drain(b) == [("share.deleted", {"share_id": share["id"], "node_id": folder})]
        r = await client.get(f"/api/v1/files/{folder}", headers=bert)
        assert r.status_code == 404
        # Een deling met één persoon intrekken
        share = await share_with(client, jonas, folder, user_id=anna_id, permission="read")
        drain(a)
        await client.delete(f"/api/v1/shares/{share['id']}", headers=jonas)
        assert drain(a) == [("share.deleted", {"share_id": share["id"], "node_id": folder})]


@pytest.mark.anyio
async def test_preview_klaar_ook_voor_wie_het_ziet(client, admin, settings, hub):
    jonas, tokens = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, root_id, "Foto's")
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    bert, bert_id = await person(client, jonas, "bert@example.nl")
    await share_with(client, jonas, folder, user_id=anna_id, permission="read")
    data = picture("PNG", (300, 300))
    inside = (await put(client, jonas, folder, "a.png", data)).json()["id"]
    outside = (await put(client, jonas, root_id, "b.png", data)).json()["id"]
    weg = await mkdir(client, jonas, folder, "Weg")
    trashed = (await put(client, jonas, weg, "c.png", data)).json()["id"]
    await client.delete(f"/api/v1/files/{weg}", headers=jonas)
    with (
        hub.subscribe(uuid.UUID(tokens["user"]["id"])) as j,
        hub.subscribe(uuid.UUID(anna_id)) as a,
        hub.subscribe(uuid.UUID(bert_id)) as b,
    ):
        assert await jobs.run_pending(settings) == 1
        [(type_a, data_a)] = drain(a)
        [(_, data_j)] = drain(j)
    assert type_a == "preview.ready" and data_a["node_ids"] == [inside]
    assert sorted(data_j["node_ids"]) == sorted([inside, outside, trashed])
    assert drain(b) == []
    r = await client.post("/api/v1/previews", json={"node_ids": [inside, outside]}, headers=anna)
    assert list(r.json()) == [inside] and r.json()[inside]["state"] == "ready"
