import asyncio
import uuid

import anyio
import pytest

from app import events
from app.events import Hub
from app.routers import files, uploads
from tests.test_files import check_refs, mkdir, new_user, put, root
from tests.test_uploads import create, hold


async def copy(client, headers, node_id, parent_id, name=None):
    body = {"parent_id": parent_id} | ({"name": name} if name else {})
    return await client.post(f"/api/v1/files/{node_id}/copy", json=body, headers=headers)


async def children(client, headers, folder_id) -> dict[str, dict]:
    r = await client.get(f"/api/v1/files/{folder_id}/children", headers=headers)
    return {c["name"]: c for c in r.json()["children"]}


async def content(client, headers, node_id) -> bytes:
    return (await client.get(f"/api/v1/files/{node_id}/content", headers=headers)).content


@pytest.mark.anyio
async def test_bestand_kopieren(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    folder = await mkdir(client, headers, root_id, "Doel")
    original = (await put(client, headers, root_id, "a.txt", b"inhoud")).json()

    r = await copy(client, headers, original["id"], folder)
    assert r.status_code == 201, r.text
    kopie = r.json()
    assert kopie["id"] != original["id"]
    assert (kopie["name"], kopie["parent_id"]) == ("a.txt", folder)
    assert kopie["etag"] == original["etag"] and kopie["mime"] == "text/plain"
    assert await content(client, headers, kopie["id"]) == b"inhoud"

    # Naam bezet: vanzelf een vrije naam, ook als je zelf een naam kiest
    assert (await copy(client, headers, original["id"], root_id)).json()["name"] == "a (1).txt"
    r = await copy(client, headers, original["id"], folder, name="b.txt")
    assert r.json()["name"] == "b.txt"
    r = await copy(client, headers, original["id"], folder, name="B.TXT")
    assert r.json()["name"] == "B (1).TXT"
    assert (await copy(client, headers, original["id"], folder, name="a/b")).status_code == 422

    # Eén keer op schijf, vijf verwijzingen
    assert list((await check_refs(settings)).values()) == [5]


@pytest.mark.anyio
async def test_map_met_alles_erin_kopieren(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = await mkdir(client, headers, root_id, "A")
    b = await mkdir(client, headers, a, "B")
    await put(client, headers, a, "een.txt", b"1")
    await put(client, headers, b, "twee.txt", b"2")
    await mkdir(client, headers, b, "leeg")
    weg = (await put(client, headers, a, "weg.txt", b"weg")).json()["id"]
    weggemap = await mkdir(client, headers, a, "Weg")
    await put(client, headers, weggemap, "diep.txt", b"diep")
    await client.delete(f"/api/v1/files/{weg}", headers=headers)
    await client.delete(f"/api/v1/files/{weggemap}", headers=headers)

    r = await copy(client, headers, a, root_id)
    assert r.status_code == 201
    kopie = r.json()
    assert kopie["name"] == "A (1)" and kopie["kind"] == "dir"
    # Wat in de prullenbak ligt (en wat daaronder zit) gaat niet mee
    top = await children(client, headers, kopie["id"])
    assert sorted(top) == ["B", "een.txt"]
    sub = await children(client, headers, top["B"]["id"])
    assert sorted(sub) == ["leeg", "twee.txt"]
    assert sub["twee.txt"]["id"] != b
    assert await content(client, headers, sub["twee.txt"]["id"]) == b"2"
    await check_refs(settings)

    # De kopie staat los van het origineel
    await client.delete(f"/api/v1/files/{a}", headers=headers)
    assert (await client.delete(f"/api/v1/trash/{a}", headers=headers)).status_code == 204
    assert await content(client, headers, sub["twee.txt"]["id"]) == b"2"
    await check_refs(settings)


@pytest.mark.anyio
async def test_niet_in_zichzelf_en_niet_uit_de_prullenbak(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = await mkdir(client, headers, root_id, "A")
    b = await mkdir(client, headers, a, "B")
    for target in (a, b):
        r = await copy(client, headers, a, target)
        assert r.status_code == 422
    assert (await copy(client, headers, root_id, a)).status_code == 422

    f = (await put(client, headers, a, "f.txt", b"f")).json()["id"]
    await client.delete(f"/api/v1/files/{a}", headers=headers)
    assert (await copy(client, headers, a, root_id)).status_code == 422
    assert (await copy(client, headers, f, root_id)).status_code == 422  # in een weggegooide map
    g = (await put(client, headers, root_id, "g.txt", b"g")).json()["id"]
    assert (await copy(client, headers, g, a)).status_code == 404  # doel in de prullenbak
    assert (await copy(client, headers, g, b)).status_code == 404  # doel daaronder
    assert (await copy(client, headers, g, g)).status_code == 404  # doel is geen map


@pytest.mark.anyio
async def test_quotum_en_andermans_bestanden(client, admin):
    headers, _ = admin
    k = await new_user(client, headers, "k@example.nl", quota_bytes=10)
    kroot = (await root(client, k))["folder"]["id"]
    zes = (await put(client, k, kroot, "zes", b"123456")).json()["id"]
    r = await copy(client, k, zes, kroot)
    assert r.status_code == 413 and r.json()["detail"] == "Onvoldoende quotum"
    vier = (await put(client, k, kroot, "vier", b"1234")).json()["id"]
    assert (await copy(client, k, vier, kroot)).status_code == 413  # 6 + 4 = 10, vol
    me = (await client.get("/api/v1/auth/me", headers=k)).json()
    assert me["used_bytes"] == 10

    root_id = (await root(client, headers))["folder"]["id"]
    mine = (await put(client, headers, root_id, "mijn.txt", b"x")).json()["id"]
    assert (await copy(client, k, mine, kroot)).status_code == 404
    assert (await copy(client, k, zes, root_id)).status_code == 404


def meet_up(monkeypatch, *modules):
    """Laat room_left in deze modules op elkaar wachten (hooguit even), zodat twee
    verzoeken het quotum echt tegelijk zouden controleren. Een slot ertussen houdt de tweede
    tegen; die controleert dan pas na de eerste."""
    arrived, both = [], asyncio.Event()

    def wrap(real):
        async def waiting(*args, **kwargs):
            arrived.append(1)
            if len(arrived) >= 2:
                both.set()
            with anyio.move_on_after(0.3):
                await both.wait()
            return await real(*args, **kwargs)

        return waiting

    for module in modules:
        monkeypatch.setattr(module, "room_left", wrap(module.room_left))


@pytest.mark.anyio
async def test_twee_kopieen_tegelijk_niet_over_het_quotum(client, admin, monkeypatch, settings):
    headers, _ = admin
    q = await new_user(client, headers, "q@example.nl", quota_bytes=20)
    qroot = (await root(client, q))["folder"]["id"]
    acht = (await put(client, q, qroot, "acht", b"12345678")).json()["id"]
    meet_up(monkeypatch, files)
    # Elk past op zich (8 + 8), samen niet (8 + 8 + 8 = 24)
    r1, r2 = await asyncio.gather(copy(client, q, acht, qroot), copy(client, q, acht, qroot))
    assert sorted([r1.status_code, r2.status_code]) == [201, 413]
    await check_refs(settings)


@pytest.mark.anyio
async def test_kopie_en_reservering_tegelijk(client, admin, monkeypatch):
    headers, _ = admin
    q = await new_user(client, headers, "r@example.nl", quota_bytes=20)
    qroot = (await root(client, q))["folder"]["id"]
    acht = (await put(client, q, qroot, "acht", b"12345678")).json()["id"]
    meet_up(monkeypatch, files, uploads)
    # Elk past op zich (8 + 8 of 8 + 10), samen niet (8 + 8 + 10 = 26)
    r1, r2 = await asyncio.gather(copy(client, q, acht, qroot), create(client, q, qroot, "t", 10))
    assert sorted([r1.status_code, r2.status_code]) == [201, 413]


@pytest.mark.anyio
async def test_kopieren_terwijl_het_origineel_definitief_weggaat(
    client, admin, settings, monkeypatch
):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    bron = await mkdir(client, headers, root_id, "Bron")
    await put(client, headers, bron, "x.bin", b"ENIGE-KOPIE")
    entered, release = hold(monkeypatch, files, "room_left")  # midden in het kopiëren

    with anyio.fail_after(5):
        copying = asyncio.create_task(copy(client, headers, bron, root_id, name="Kopie"))
        await entered.wait()
        await client.delete(f"/api/v1/files/{bron}", headers=headers)
        purging = asyncio.create_task(client.delete(f"/api/v1/trash/{bron}", headers=headers))
        await asyncio.sleep(0.05)
        assert not purging.done()  # wacht op het blob-slot
        release.set()
        c, p = await copying, await purging
    assert c.status_code == 201 and p.status_code == 204
    kopie = await children(client, headers, c.json()["id"])
    assert await content(client, headers, kopie["x.bin"]["id"]) == b"ENIGE-KOPIE"
    assert list((await check_refs(settings)).values()) == [1]


@pytest.mark.anyio
async def test_afgebroken_kopie_laat_niets_achter(client, admin, settings, monkeypatch):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    bron = await mkdir(client, headers, root_id, "Bron")
    await put(client, headers, bron, "x.bin", b"x")
    entered, _release = hold(monkeypatch, files, "room_left")

    with anyio.fail_after(5):
        copying = asyncio.create_task(copy(client, headers, bron, root_id))
        await entered.wait()
        copying.cancel()  # de client is weg (of de server stopt)
        with pytest.raises(asyncio.CancelledError):
            await copying
        assert sorted(await children(client, headers, root_id)) == ["Bron"]
        # De sloten zijn vrij: een volgende kopie werkt gewoon
        monkeypatch.undo()
        assert (await copy(client, headers, bron, root_id)).status_code == 201
    assert list((await check_refs(settings)).values()) == [2]


@pytest.mark.anyio
async def test_kopieren_meldt_nieuw_bestand(client, admin, monkeypatch):
    headers, tokens = admin
    monkeypatch.setattr(events, "hub", Hub())
    root_id = (await root(client, headers))["folder"]["id"]
    folder = await mkdir(client, headers, root_id, "Doel")
    f = (await put(client, headers, root_id, "a.txt", b"a")).json()["id"]

    with events.hub.subscribe(uuid.UUID(tokens["user"]["id"])) as sub:
        kopie = (await copy(client, headers, f, folder)).json()
        assert sub.queue.get_nowait() == {
            "type": "node.created",
            "data": {"id": kopie["id"], "parent_id": folder},
        }
        assert sub.queue.empty()
