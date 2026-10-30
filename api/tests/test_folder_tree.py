"""POST /files/{id}/folders/tree: de mappen van een mapupload aanmaken of hergebruiken (P3)."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app import events
from app.events import Hub
from app.models import Node
from tests.factories import code_of, maker, params_of
from tests.test_files import mkdir, put, root
from tests.test_files_events import drain
from tests.test_shares import person, share_with


async def tree(client, headers, folder, paths, expect=200):
    r = await client.post(
        f"/api/v1/files/{folder}/folders/tree", json={"paths": paths}, headers=headers
    )
    assert r.status_code == expect, r.text
    return r.json()


async def names(client, headers, folder) -> list[str]:
    r = await client.get(f"/api/v1/files/{folder}/children", headers=headers)
    return sorted(c["name"] for c in r.json()["children"])


@pytest.mark.anyio
async def test_maakt_de_boom_en_hergebruikt_zonder_op_hoofdletters_te_letten(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    vakantie = await mkdir(client, headers, root_id, "Vakantie")

    out = await tree(
        client, headers, root_id, ["vakantie", "VAKANTIE/2024", "Vakantie/2024/Strand", "Werk"]
    )
    assert out["errors"] == {}
    assert out["folders"]["vakantie"] == vakantie  # hergebruikt: samenvoegen
    assert set(out["created"]) == {"VAKANTIE/2024", "Vakantie/2024/Strand", "Werk"}
    assert out["folders"]["VAKANTIE/2024"] != vakantie
    assert await names(client, headers, root_id) == ["Vakantie", "Werk"]
    assert await names(client, headers, vakantie) == ["2024"]  # de eerste schrijfwijze
    strand = out["folders"]["Vakantie/2024/Strand"]
    info = (await client.get(f"/api/v1/files/{strand}", headers=headers)).json()
    assert info["parent_id"] == out["folders"]["VAKANTIE/2024"]

    # Nog eens: niets nieuws, dezelfde ids
    again = await tree(client, headers, root_id, ["Vakantie/2024/strand", "werk"])
    assert again["created"] == []
    assert again["folders"]["Vakantie/2024/strand"] == strand
    assert again["folders"]["werk"] == out["folders"]["Werk"]


@pytest.mark.anyio
async def test_twee_gelijktijdige_verzoeken_maken_geen_dubbele_mappen(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    paths = [f"Foto's/{i}/sub" for i in range(20)]
    first, second = await asyncio.gather(
        tree(client, headers, root_id, paths), tree(client, headers, root_id, paths)
    )
    assert first["folders"] == second["folders"]
    assert len(first["created"]) + len(second["created"]) == len(paths)
    async with maker(settings)() as session:
        count = await session.scalar(
            select(func.count()).select_from(Node).where(Node.kind == "dir", Node.name != "")
        )
    assert count == 1 + 20 + 20


@pytest.mark.anyio
async def test_een_bestand_met_de_naam_van_een_map(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    await put(client, headers, root_id, "Foto", b"ik ben een bestand")
    out = await tree(client, headers, root_id, ["foto", "Foto/2024", "Foto/2024/x", "Anders/y"])
    assert out["errors"] == {
        "foto": "name_taken",
        "Foto/2024": "name_taken",
        "Foto/2024/x": "name_taken",
    }
    assert set(out["folders"]) == {"Anders/y"}
    assert out["created"] == ["Anders/y"]
    assert await names(client, headers, root_id) == ["Anders", "Foto"]


@pytest.mark.anyio
async def test_ongeldige_namen_diepte_en_aantal(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    deep = "/".join(f"d{i}" for i in range(33))
    ok = "/".join(f"d{i}" for i in range(32))
    out = await tree(client, headers, root_id, ["a/..", "a//b", "a/b\x00c", deep, ok, "/goed/"])
    assert out["errors"] == {
        "a/..": "invalid_name",
        "a//b": "invalid_name",
        "a/b\x00c": "invalid_name",
        deep: "too_deep",
    }
    assert set(out["folders"]) == {ok, "/goed/"}
    assert "a" not in await names(client, headers, root_id)  # een fout pad maakt niets

    r = await client.post(
        f"/api/v1/files/{root_id}/folders/tree",
        json={"paths": [f"p{i}" for i in range(1001)]},
        headers=headers,
    )
    assert r.status_code == 422 and code_of(r) == "too_many"
    assert params_of(r) == {"max": 1000}


@pytest.mark.anyio
async def test_schrijfrecht_in_een_gedeelde_map(client, admin, settings):
    jonas, tokens = admin
    jonas_id = tokens["user"]["id"]
    root_id = (await root(client, jonas))["folder"]["id"]
    lezen = await mkdir(client, jonas, root_id, "Lezen")
    schrijven = await mkdir(client, jonas, root_id, "Schrijven")
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    await share_with(client, jonas, lezen, user_id=anna_id, permission="read")
    await share_with(client, jonas, schrijven, user_id=anna_id, permission="write")

    r = await client.post(
        f"/api/v1/files/{lezen}/folders/tree", json={"paths": ["x"]}, headers=anna
    )
    assert r.status_code == 403 and code_of(r) == "read_only"
    out = await tree(client, anna, schrijven, ["Van Anna/sub"])
    new = uuid.UUID(out["folders"]["Van Anna/sub"])
    async with maker(settings)() as session:
        folder = await session.get(Node, new)
        assert str(folder.owner_id) == jonas_id  # van de eigenaar van de map, niet van Anna
    assert await names(client, jonas, schrijven) == ["Van Anna"]

    # Niet zichtbaar (een id van een ander) is 404, net als elders
    r = await client.post(
        f"/api/v1/files/{uuid.uuid4()}/folders/tree", json={"paths": ["x"]}, headers=anna
    )
    assert r.status_code == 404


@pytest.mark.anyio
async def test_in_de_prullenbak_kan_niet(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    weg = await mkdir(client, headers, root_id, "Weg")
    sub = await mkdir(client, headers, weg, "Sub")
    assert (await client.delete(f"/api/v1/files/{weg}", headers=headers)).status_code == 204
    r = await client.post(
        f"/api/v1/files/{sub}/folders/tree", json={"paths": ["x"]}, headers=headers
    )
    assert r.status_code == 404 and code_of(r) == "folder_not_found"


@pytest.mark.anyio
async def test_meldt_alleen_de_bovenste_nieuwe_mappen(client, admin, monkeypatch):
    headers, tokens = admin
    monkeypatch.setattr(events, "hub", Hub())
    root_id = (await root(client, headers))["folder"]["id"]
    bestaand = await mkdir(client, headers, root_id, "Bestaand")
    owner = uuid.UUID(tokens["user"]["id"])
    with events.hub.subscribe(owner) as sub:
        out = await tree(
            client, headers, root_id, ["A", "A/b/c", "Bestaand/d", "Bestaand/d/e", "F"]
        )
        seen = drain(sub)
    assert {t for t, _ in seen} == {"node.created"}
    announced = set()
    for _, data in seen:
        announced |= set(data.get("ids") or [data["id"]])
    assert announced == {out["folders"]["A"], out["folders"]["Bestaand/d"], out["folders"]["F"]}
    assert {data["parent_id"] for _, data in seen} == {root_id, bestaand}
