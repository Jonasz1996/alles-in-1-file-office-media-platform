"""Bulkacties (POST /batch/trash, /batch/move, /batch/copy): per id een resultaat met dezelfde
codes als de losse endpoints, wat lukt blijft gelukt, één transactie per BATCH ids."""

import uuid

import pytest

from app import errors
from app.routers import batch, files
from tests.factories import code_of
from tests.test_files import check_refs, mkdir, put, root
from tests.test_shares import person, share_with


async def post(client, headers, action, expect=200, **body) -> dict:
    r = await client.post(f"/api/v1/batch/{action}", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def codes(body) -> dict[str, str | None]:
    return {r["id"]: (None if r["ok"] else r["code"]) for r in body["results"]}


async def names(client, headers, folder) -> list[str]:
    r = await client.get(f"/api/v1/files/{folder}/children", headers=headers)
    return [c["name"] for c in r.json()["children"]]


@pytest.fixture
async def setup(client, admin):
    """Jonas met Werk (schrijven gedeeld met Anna) en Kijk (alleen kijken voor Anna)."""
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    werk = await mkdir(client, jonas, root_id, "Werk")
    kijk = await mkdir(client, jonas, root_id, "Kijk")
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    await share_with(client, jonas, werk, user_id=anna_id, permission="write")
    await share_with(client, jonas, kijk, user_id=anna_id, permission="read")
    files_ = {}
    for folder, name in [(werk, "w1.txt"), (werk, "w2.txt"), (kijk, "k.txt"), (root_id, "r.txt")]:
        files_[name] = (await put(client, jonas, folder, name, name.encode())).json()["id"]
    anna_root = (await root(client, anna))["folder"]["id"]
    return {
        "jonas": jonas,
        "anna": anna,
        "root": root_id,
        "anna_root": anna_root,
        "werk": werk,
        "kijk": kijk,
        **files_,
    }


@pytest.mark.anyio
async def test_weggooien_met_fouten_per_id(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    al_weg = (await put(client, jonas, s["root"], "al-weg.txt", b"x")).json()["id"]
    await client.delete(f"/api/v1/files/{al_weg}", headers=jonas)
    onbekend = str(uuid.uuid4())

    body = await post(
        client, jonas, "trash", ids=[s["r.txt"], s["root"], al_weg, onbekend, s["r.txt"]]
    )
    assert codes(body) == {
        s["r.txt"]: None,
        s["root"]: "root",
        al_weg: "trashed",
        onbekend: "not_found",
    }
    assert body["results"][0]["node"] is None
    assert "r.txt" not in await names(client, jonas, s["root"])

    # Anna: binnen Werk mag het, Werk zelf niet (gedeeld met haar), Kijk is alleen kijken
    body = await post(client, anna, "trash", ids=[s["w1.txt"], s["werk"], s["k.txt"]])
    assert codes(body) == {s["w1.txt"]: None, s["werk"]: "share_root", s["k.txt"]: "read_only"}
    assert await names(client, jonas, s["werk"]) == ["w2.txt"]
    # Een gast heeft geen eigen bestanden; leeg mag
    assert (await post(client, jonas, "trash", ids=[]))["results"] == []
    await post(client, jonas, "trash", expect=422, ids=[onbekend] * (batch.MAX_IDS + 1))


@pytest.mark.anyio
async def test_een_transactie_per_stuk(client, setup, monkeypatch):
    s = setup
    jonas = s["jonas"]
    ids = [
        (await put(client, jonas, s["root"], f"{i}.txt", b"%d" % i)).json()["id"] for i in range(5)
    ]
    calls = []
    real = files.get_tree_accesses

    async def counted(session, user, node_ids, need="owner"):
        calls.append(len(node_ids))
        return await real(session, user, node_ids, need)

    monkeypatch.setattr(batch, "BATCH", 2)
    monkeypatch.setattr(files, "get_tree_accesses", counted)
    body = await post(client, jonas, "move", ids=ids, parent_id=s["werk"])
    assert calls == [2, 2, 1]
    assert set(codes(body).values()) == {None}
    assert [r["node"]["parent_id"] for r in body["results"]] == [s["werk"]] * 5

    # Gaat de doelmap halverwege weg: wat al verplaatst is blijft, de rest faalt per id
    calls.clear()
    real_dir = files.get_dir
    seen = []

    async def vanishing(session, user, node_id, need="read"):
        seen.append(node_id)
        if len(seen) > 1:
            raise errors.fail(404, "folder_not_found", "Map niet gevonden")
        return await real_dir(session, user, node_id, need)

    monkeypatch.setattr(files, "get_dir", vanishing)
    body = await post(client, jonas, "move", ids=ids, parent_id=s["root"])
    assert list(codes(body).values()) == [None, None, *["folder_not_found"] * 3]
    # De eerste keer mag de doelmap niet ontbreken: dan faalt het hele verzoek
    seen.append("al")
    r = await client.post(
        "/api/v1/batch/move", json={"ids": ids, "parent_id": s["root"]}, headers=jonas
    )
    assert r.status_code == 404 and code_of(r) == "folder_not_found"


@pytest.mark.anyio
async def test_verplaatsen(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    sub = await mkdir(client, jonas, s["werk"], "Sub")
    dubbel = (await put(client, jonas, sub, "w1.txt", b"ander")).json()["id"]

    body = await post(
        client,
        jonas,
        "move",
        ids=[dubbel, s["werk"], s["w2.txt"], sub, s["root"]],
        parent_id=s["werk"],
    )
    assert codes(body) == {
        dubbel: "name_taken",
        s["werk"]: "into_itself",
        s["w2.txt"]: None,  # staat er al: gelukt, niets veranderd
        sub: None,
        s["root"]: "root",
    }
    body = await post(client, jonas, "move", ids=[dubbel], parent_id=s["werk"], conflict="rename")
    assert body["results"][0]["node"]["name"] == "w1 (1).txt"
    body = await post(client, jonas, "move", ids=[s["werk"]], parent_id=sub)
    assert codes(body) == {s["werk"]: "into_itself"}

    # Anna: haar eigen bestand kan niet naar de map van Jonas (andere eigenaar)
    eigen = (await put(client, anna, s["anna_root"], "eigen.txt", b"e")).json()["id"]
    body = await post(client, anna, "move", ids=[eigen, s["w1.txt"]], parent_id=s["werk"])
    assert codes(body) == {eigen: "cross_owner", s["w1.txt"]: None}
    # De doelmap moet schrijfbaar zijn: anders faalt het hele verzoek
    r = await client.post(
        "/api/v1/batch/move", json={"ids": [s["w1.txt"]], "parent_id": s["kijk"]}, headers=anna
    )
    assert r.status_code == 403 and code_of(r) == "read_only"
    # Wat zij ziet: de nodes zoals zij ze ziet
    body = await post(client, anna, "move", ids=[s["w1.txt"]], parent_id=sub)
    assert body["results"][0]["node"]["permission"] == "write"
    # Een map in de prullenbak als doel
    await client.delete(f"/api/v1/files/{sub}", headers=jonas)
    r = await client.post(
        "/api/v1/batch/move", json={"ids": [s["w2.txt"]], "parent_id": sub}, headers=jonas
    )
    assert r.status_code == 404


@pytest.mark.anyio
async def test_kopieren(client, setup, settings, monkeypatch):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    sub = await mkdir(client, jonas, s["werk"], "Sub")
    await put(client, jonas, sub, "diep.txt", b"diep")

    body = await post(client, jonas, "copy", ids=[s["w1.txt"], sub, s["werk"]], parent_id=s["werk"])
    assert codes(body) == {s["w1.txt"]: None, sub: None, s["werk"]: "into_itself"}
    kopie = body["results"][0]["node"]
    assert (kopie["name"], kopie["parent_id"]) == ("w1 (1).txt", s["werk"])
    assert kopie["id"] != s["w1.txt"]
    sub_copy = body["results"][1]["node"]["id"]
    assert await names(client, jonas, sub_copy) == ["diep.txt"]
    assert sorted((await check_refs(settings)).values()) == [1, 1, 1, 2, 2]

    # Anna kopieert iets wat zij mag lezen naar haar eigen bestanden: het wordt van haar
    body = await post(client, anna, "copy", ids=[s["k.txt"], s["r.txt"]], parent_id=s["anna_root"])
    assert codes(body) == {s["k.txt"]: None, s["r.txt"]: "not_found"}
    assert body["results"][0]["node"]["permission"] == "owner"

    # Samen meer dan MAX_COPY: wat er niet meer bij past, krijgt too_many
    monkeypatch.setattr(files, "MAX_COPY", 2)
    body = await post(
        client, jonas, "copy", ids=[s["w2.txt"], sub, s["r.txt"]], parent_id=s["root"]
    )
    assert codes(body) == {s["w2.txt"]: None, sub: "too_many", s["r.txt"]: None}


@pytest.mark.anyio
async def test_kopieren_over_het_quotum(client, admin):
    jonas, _ = admin
    anna, _ = await person(client, jonas, "anna@example.nl", quota_bytes=10)
    anna_root = (await root(client, anna))["folder"]["id"]
    a = (await put(client, anna, anna_root, "a.txt", b"123456")).json()["id"]
    r = await client.post(
        "/api/v1/batch/copy", json={"ids": [a], "parent_id": anna_root}, headers=anna
    )
    assert r.status_code == 413 and code_of(r) == "no_room"
    assert await names(client, anna, anna_root) == ["a.txt"]
