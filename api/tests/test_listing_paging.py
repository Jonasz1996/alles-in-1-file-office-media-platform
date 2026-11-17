"""De lijst van een map in pagina's (?limit=&after=&kind=): de pagina's achter elkaar zijn
precies de lijst in één keer, een cursor blijft kloppen als er intussen iets bij komt, en
describe() (model_construct) geeft hetzelfde als de gevalideerde NodeOut."""

import uuid

import pytest

from app.models import Node
from app.routers import files
from app.routers.files import NodeOut
from tests.factories import code_of, create_file, create_folder, maker, user_by_email
from tests.test_files import mkdir, put, root
from tests.test_shares import person, share_with

NAMES = ["Één", "eend", "Appel", "appel (1)", "Zee", "zee.txt", "ä.txt", "B.txt", "b.TXT~"]


async def pages(client, headers, folder_id, limit, **params) -> tuple[list[dict], list[int]]:
    """Alle kinderen pagina na pagina; plus total van elke pagina."""
    out, totals, after = [], [], None
    while True:
        query = {"limit": limit, **params} | ({"after": after} if after else {})
        r = await client.get(f"/api/v1/files/{folder_id}/children", params=query, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["children"]) <= limit
        out += body["children"]
        totals.append(body["total"])
        after = body["next"]
        if after is None:
            return out, totals


@pytest.fixture
async def folder(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    folder_id = await mkdir(client, headers, root_id, "Groot")
    async with maker(settings)() as session:
        jonas = await user_by_email(session, "jonas@example.nl")
        parent = await session.get(Node, uuid.UUID(folder_id))
        for i in range(25):
            await create_file(session, jonas, parent.id, f"bestand {i:02}.txt", data=b"%d" % i)
        for name in NAMES:
            await create_folder(session, jonas, parent.id, name)
            await create_file(session, jonas, parent.id, f"{name}.dat", data=name.encode())
        await create_file(session, jonas, parent.id, "weg.txt", deleted=True)
        await session.commit()
    return folder_id


@pytest.mark.anyio
async def test_paginas_achter_elkaar_zijn_de_hele_lijst(client, admin, folder):
    headers, _ = admin
    whole = (await client.get(f"/api/v1/files/{folder}/children", headers=headers)).json()
    assert whole["next"] is None
    assert whole["total"] == len(whole["children"]) == 25 + 2 * len(NAMES)
    # Mappen eerst, dan op naam zonder hoofdletters en accenten
    kinds = [c["kind"] for c in whole["children"]]
    assert kinds == sorted(kinds)
    for limit in (1, 7, 200):
        paged, totals = await pages(client, headers, folder, limit)
        assert paged == whole["children"]
        assert set(totals) == {whole["total"]}

    dirs, totals = await pages(client, headers, folder, 4, kind="dir")
    assert [d["name"] for d in dirs] == [c["name"] for c in whole["children"] if c["kind"] == "dir"]
    assert set(totals) == {len(NAMES)}
    only_files, _ = await pages(client, headers, folder, 50, kind="file")
    assert len(only_files) == 25 + len(NAMES)

    # De hoofdmap kan het ook
    r = await client.get("/api/v1/files/root", params={"limit": 1}, headers=headers)
    assert r.json()["total"] == 1 and r.json()["next"] is None


@pytest.mark.anyio
async def test_cursor_blijft_kloppen_als_er_iets_bijkomt(client, admin, folder, settings):
    headers, _ = admin
    first = (
        await client.get(
            f"/api/v1/files/{folder}/children",
            params={"limit": 20},
            headers=headers,
        )
    ).json()
    seen = {c["id"] for c in first["children"]}
    # Iets vóór de cursor (een map: komt bovenaan) en iets erna
    await mkdir(client, headers, folder, "aaa nieuw")
    late = (await put(client, headers, folder, "zzz nieuw.txt", b"z")).json()
    rest, totals = [], []
    after = first["next"]
    while after:
        body = (
            await client.get(
                f"/api/v1/files/{folder}/children",
                params={"limit": 20, "after": after},
                headers=headers,
            )
        ).json()
        rest += body["children"]
        totals.append(body["total"])
        after = body["next"]
    ids = [c["id"] for c in rest]
    assert not seen & set(ids), "geen dubbele"
    assert late["id"] in ids
    assert "aaa nieuw" not in {c["name"] for c in rest}
    assert totals[-1] == first["total"] + 2
    # Weggegooid tussendoor: valt eruit, de cursor erna werkt nog
    victim = rest[0]["id"]
    await client.delete(f"/api/v1/files/{victim}", headers=headers)
    body = (
        await client.get(
            f"/api/v1/files/{folder}/children",
            params={"limit": 20, "after": first["next"]},
            headers=headers,
        )
    ).json()
    assert victim not in {c["id"] for c in body["children"]}


@pytest.mark.anyio
async def test_ongeldige_cursor_en_limiet(client, admin, folder):
    headers, _ = admin
    url = f"/api/v1/files/{folder}/children"
    for bad in ("zomaar", "@@@", "YWJj"):
        r = await client.get(url, params={"limit": 5, "after": bad}, headers=headers)
        assert r.status_code == 422 and code_of(r) == "invalid_cursor", (bad, r.text)
    assert (await client.get(url, params={"limit": 0}, headers=headers)).status_code == 422
    r = await client.get(url, params={"limit": files.MAX_PAGE + 1}, headers=headers)
    assert r.status_code == 422
    assert (await client.get(url, params={"kind": "album"}, headers=headers)).status_code == 422


@pytest.mark.anyio
async def test_describe_geeft_wat_validatie_geeft(client, admin, folder):
    headers, _ = admin
    _, anna = await person(client, headers, "anna@example.nl")
    await share_with(client, headers, folder, user_id=anna, permission="write")
    tag = (await client.post("/api/v1/tags", json={"name": "rood"}, headers=headers)).json()
    whole = (await client.get(f"/api/v1/files/{folder}/children", headers=headers)).json()
    first = whole["children"][0]["id"]
    r = await client.put(
        f"/api/v1/files/{first}/tags", json={"tag_ids": [tag["id"]]}, headers=headers
    )
    assert r.status_code == 200, r.text
    whole = (await client.get(f"/api/v1/files/{folder}/children", headers=headers)).json()
    for child in [whole["folder"], *whole["children"][:5], *whole["path"]]:
        assert NodeOut.model_validate(child).model_dump(mode="json") == child
        single = (await client.get(f"/api/v1/files/{child['id']}", headers=headers)).json()
        assert single == child
    assert whole["children"][0]["tags"][0]["name"] == "rood"
    assert whole["folder"]["shared"] is True
    # Een pagina geeft dezelfde rijen (ook de tags) als de hele lijst
    page = (
        await client.get(f"/api/v1/files/{folder}/children", params={"limit": 3}, headers=headers)
    ).json()
    assert page["children"] == whole["children"][:3]
    assert page["folder"] == whole["folder"] and page["path"] == whole["path"]
