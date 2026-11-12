"""Namen met hoofdletters buiten ASCII ("Übersicht.txt"): lower() in SQLite kent alleen ASCII,
dus de vergelijking zonder hoofdletters (sibling(), names/check) mag daar niet op leunen."""

import pytest

from tests.factories import code_of
from tests.test_files import mkdir, put, root
from tests.test_folder_tree import names, tree
from tests.test_names_check import check
from tests.test_uploads import create


@pytest.mark.anyio
async def test_uploaden_en_hernoemen_zien_de_botsing(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    first = await put(client, headers, root_id, "Übersicht.txt", b"1")
    assert first.status_code == 201
    first = first.json()

    for name in ("Übersicht.txt", "übersicht.TXT"):
        r = await put(client, headers, root_id, name, b"2", conflict="fail")
        assert r.status_code == 409 and code_of(r) == "name_taken", name
        r = await create(client, headers, root_id, name, 1, conflict="fail")
        assert r.status_code == 409 and code_of(r) == "name_taken", name
    # Accenten tellen wel: dat is een andere naam
    assert (await put(client, headers, root_id, "Ubersicht.txt", b"3")).status_code == 201
    renamed = (await put(client, headers, root_id, "ÜBERSICHT.txt", b"4")).json()
    assert renamed["name"] == "ÜBERSICHT (1).txt"

    # Hernoemen naar de naam van een ander: nee; alleen de hoofdletters van jezelf: ja
    r = await client.patch(
        f"/api/v1/files/{renamed['id']}", json={"name": "übersicht.txt"}, headers=headers
    )
    assert r.status_code == 409 and code_of(r) == "name_taken"
    r = await client.patch(
        f"/api/v1/files/{first['id']}", json={"name": "ÜBERSICHT.txt"}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert await names(client, headers, root_id) == [
        "Ubersicht.txt",
        "ÜBERSICHT (1).txt",
        "ÜBERSICHT.txt",
    ]


@pytest.mark.anyio
async def test_nieuwe_versie_van_een_bestand_met_een_umlaut(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "Ökonomie.pdf", b"een")).json()
    r = await client.put(f"/api/v1/files/{node['id']}/content", content=b"twee", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == node["id"]
    r = await client.get(f"/api/v1/files/{node['id']}/content", headers=headers)
    assert r.content == b"twee"


@pytest.mark.anyio
async def test_names_check_en_folders_tree(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    file_id = (await put(client, headers, root_id, "Übersicht.txt", b"1")).json()["id"]
    map_id = await mkdir(client, headers, root_id, "École")

    asked = ["Übersicht.txt", "ÜBERSICHT.TXT", "ecole", "école"]
    out = await check(client, headers, root_id, asked)
    assert out == {
        "taken": [
            {"name": "Übersicht.txt", "id": file_id, "kind": "file"},
            {"name": "ÜBERSICHT.TXT", "id": file_id, "kind": "file"},
            {"name": "école", "id": map_id, "kind": "dir"},
        ]
    }

    once = await tree(client, headers, root_id, ["Österreich", "Österreich/Wien"])
    again = await tree(client, headers, root_id, ["ÖSTERREICH", "österreich/Wien", "ÉCOLE/a"])
    assert again["errors"] == {} and again["created"] == ["ÉCOLE/a"]
    assert again["folders"]["ÖSTERREICH"] == once["folders"]["Österreich"]
    assert again["folders"]["österreich/Wien"] == once["folders"]["Österreich/Wien"]
    assert await names(client, headers, root_id) == ["École", "Österreich", "Übersicht.txt"]
    r = await tree(client, headers, root_id, ["übersicht.txt/x"])
    assert r["errors"] == {"übersicht.txt/x": "name_taken"}
