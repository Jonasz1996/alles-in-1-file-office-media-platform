"""Een album (node met kind "album", parent_id NULL) staat buiten de boom: de endpoints van de
bestandsbrowser weigeren het met 422 album, zoeken vindt het niet en tags tellen het niet."""

import uuid

import pytest

from app.models import NodeTag
from tests.factories import code_of, create_album, create_file, maker, user_by_email
from tests.test_files import root


@pytest.fixture
async def album(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    tag = (await client.post("/api/v1/tags", json={"name": "vakantie"}, headers=headers)).json()
    async with maker(settings)() as session:
        jonas = await user_by_email(session, "jonas@example.nl")
        foto = await create_file(session, jonas, name="foto.jpg", mime="image/jpeg")
        album = await create_album(session, jonas, name="Vakantie", items=[foto])
        session.add(NodeTag(node_id=album.id, tag_id=uuid.UUID(tag["id"])))
        await session.commit()
    return {"id": str(album.id), "root": root_id, "tag": tag["id"]}


@pytest.mark.anyio
async def test_losse_endpoints_weigeren_een_album(client, admin, album):
    headers, _ = admin
    a, root_id = album["id"], album["root"]
    calls = [
        ("POST", f"/api/v1/files/{a}/copy", {"parent_id": root_id}),
        ("PATCH", f"/api/v1/files/{a}", {"name": "Anders"}),
        ("PATCH", f"/api/v1/files/{a}", {"parent_id": root_id}),
        ("DELETE", f"/api/v1/files/{a}", None),
        ("PUT", f"/api/v1/files/{a}/tags", {"tag_ids": []}),
        ("GET", f"/api/v1/files/{a}/versions", None),
        ("GET", f"/api/v1/files/{a}/content", None),
        ("POST", f"/api/v1/files/{a}/download-link", None),
        ("POST", "/api/v1/archive/link", {"ids": [a]}),
    ]
    for method, url, body in calls:
        r = await client.request(method, url, json=body, headers=headers)
        assert (r.status_code, code_of(r)) == (422, "album"), (method, url, r.text)
    r = await client.put(f"/api/v1/files/{a}/content", content=b"x", headers=headers)
    assert (r.status_code, code_of(r)) == (422, "album")


@pytest.mark.anyio
async def test_bulkacties_weigeren_een_album(client, admin, album):
    headers, _ = admin
    a, root_id = album["id"], album["root"]
    for action, extra in [
        ("copy", {"parent_id": root_id}),
        ("move", {"parent_id": root_id}),
        ("trash", {}),
    ]:
        r = await client.post(
            f"/api/v1/batch/{action}", json={"ids": [a], **extra}, headers=headers
        )
        assert r.status_code == 200, r.text
        assert [(x["ok"], x["code"]) for x in r.json()["results"]] == [(False, "album")], action
    # Als doelmap is een album ook niets
    r = await client.post("/api/v1/batch/copy", json={"ids": [], "parent_id": a}, headers=headers)
    assert r.status_code in (404, 422)


@pytest.mark.anyio
async def test_niet_in_zoeken_en_tags(client, admin, album):
    headers, _ = admin
    r = await client.get("/api/v1/search", params={"q": "vakantie"}, headers=headers)
    assert [n["name"] for n in r.json()["results"]] == []
    r = await client.get("/api/v1/search", params={"tag": album["tag"]}, headers=headers)
    assert r.json()["results"] == []
    tags = (await client.get("/api/v1/tags", headers=headers)).json()
    assert [(t["name"], t["count"]) for t in tags] == [("vakantie", 0)]
