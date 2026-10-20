import asyncio
import gc
import uuid
from collections import Counter
from datetime import UTC, datetime

import anyio
import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import events
from app.db import get_engine
from app.events import Hub
from app.models import Blob, Node
from app.routers import files, uploads
from app.text import fold
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
    """Twee kopieën gaan al na elkaar door het blob-slot; dit blijft staan als vangnet. Dat
    het gebruikersslot werkt, tonen de tests met een reservering en een upload ernaast."""
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
async def test_kopie_en_upload_tegelijk(client, admin, monkeypatch):
    headers, _ = admin
    q = await new_user(client, headers, "u@example.nl", quota_bytes=20)
    qroot = (await root(client, q))["folder"]["id"]
    acht = (await put(client, q, qroot, "acht", b"12345678")).json()["id"]
    meet_up(monkeypatch, files)
    # Elk past op zich (8 + 8), samen niet (8 + 8 + 8 = 24); de upload controleert het nog
    # eens als de inhoud binnen is
    r1, r2 = await asyncio.gather(
        copy(client, q, acht, qroot), put(client, q, qroot, "nog acht", b"abcdefgh")
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 413]
    assert (await client.get("/api/v1/auth/me", headers=q)).json()["used_bytes"] == 16


@pytest.mark.anyio
async def test_te_veel_in_een_keer(client, admin, monkeypatch):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = await mkdir(client, headers, root_id, "A")
    await mkdir(client, headers, a, "B")
    await put(client, headers, a, "c.txt", b"c")
    monkeypatch.setattr(files, "MAX_COPY", 3)
    assert (await copy(client, headers, a, root_id)).status_code == 201  # precies 3
    await put(client, headers, a, "d.txt", b"d")
    r = await copy(client, headers, a, root_id)
    assert r.status_code == 422 and "Te veel" in r.json()["detail"]
    assert sorted(await children(client, headers, root_id)) == ["A", "A (1)"]


@pytest.mark.anyio
async def test_vrije_naam_blijft_binnen_de_grens(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    name = "x" * 251 + ".txt"
    f = (await put(client, headers, root_id, name, b"x")).json()["id"]
    r = await copy(client, headers, f, root_id)
    assert r.status_code == 201
    assert r.json()["name"] == "x" * 247 + " (1).txt"
    r = await put(client, headers, root_id, name, b"y")
    assert r.json()["name"] == "x" * 247 + " (2).txt"
    folder = await mkdir(client, headers, root_id, "m" * 255)
    r = await copy(client, headers, folder, root_id)
    assert r.json()["name"] == "m" * 251 + " (1)"


def test_genummerde_naam():
    assert files.numbered("a.txt", 1) == "a (1).txt"
    assert files.numbered(".bashrc", 2) == ".bashrc (2)"
    assert files.numbered("map", 12) == "map (12)"
    # Te lang: de naam wordt korter, de extensie en het nummer blijven
    assert files.numbered("a" * 250 + ".jpeg", 3) == "a" * 246 + " (3).jpeg"
    # Een "extensie" die bijna de hele naam is, hoort bij de naam
    long_ext = "a." + "b" * 253
    assert files.numbered(long_ext, 1) == long_ext[:251] + " (1)"
    assert all(len(files.numbered("x" * 255, i)) <= 255 for i in (1, 99, 9999))


async def worst_stall(coro):
    """Voert coro uit en geeft (uitkomst, langste tijd dat de event loop intussen niets anders
    kon doen). Zonder garbage collection: die duurt af en toe wat langer, wat de code ook doet."""
    loop = asyncio.get_running_loop()
    worst, done = 0.0, asyncio.Event()

    async def tick():
        nonlocal worst
        while not done.is_set():
            started = loop.time()
            await asyncio.sleep(0.005)
            worst = max(worst, loop.time() - started - 0.005)

    gc.collect()
    gc.disable()
    ticker = asyncio.create_task(tick())
    try:
        await asyncio.sleep(0.02)
        return await coro, worst
    finally:
        done.set()
        await ticker
        gc.enable()


@pytest.mark.anyio
async def test_grote_map_kopieren_houdt_de_rest_niet_op(client, admin, settings):
    """Duizenden bestanden kopiëren en weggooien laat de event loop nooit lang stilstaan:
    andere verzoeken gaan intussen gewoon door."""
    headers, tokens = admin
    owner = uuid.UUID(tokens["user"]["id"])
    root_id = (await root(client, headers))["folder"]["id"]
    top = await mkdir(client, headers, root_id, "Groot")
    now, rows, refs = datetime.now(UTC), [], Counter()

    def node(parent, name, kind, sha=None):
        rows.append(
            {
                "id": uuid.uuid4(),
                "parent_id": parent,
                "owner_id": owner,
                "name": name,
                "search_name": fold(name),
                "kind": kind,
                "size": 1 if sha else 0,
                "blob_id": sha,
                "created_at": now,
                "modified_at": now,
            }
        )
        if sha:
            refs[sha] += 1
        return rows[-1]["id"]

    for i in range(50):
        sub = node(uuid.UUID(top), f"Map {i}", "dir")
        for j in range(100):
            node(sub, f"Foto {j}.jpg", "file", f"{(i * 100 + j) % 700:064x}")
    maker = async_sessionmaker(get_engine(settings))
    async with maker() as session:
        await session.execute(
            insert(Blob), [{"sha256": s, "size": 1, "ref_count": n} for s, n in refs.items()]
        )
        await session.execute(insert(Node), rows)
        await session.commit()

    async def ref_counts():
        async with maker() as session:
            return dict((await session.execute(select(Blob.sha256, Blob.ref_count))).all())

    r, stall = await worst_stall(copy(client, headers, top, root_id))
    assert r.status_code == 201, r.text
    assert stall < 0.1, f"kopiëren hield de event loop {stall:.2f} s vast"
    kopie = r.json()
    assert kopie["name"] == "Groot (1)"
    assert await ref_counts() == {sha: 2 * n for sha, n in refs.items()}
    # Alles staat erin, ook vindbaar op naam (zoeknaam gezet zonder ORM-objecten)
    params = {"q": "foto 42", "in": kopie["id"], "limit": 200}
    found = (await client.get("/api/v1/search", params=params, headers=headers)).json()
    assert len(found["results"]) == 50
    assert {hit["path"][1]["name"] for hit in found["results"]} == {"Groot (1)"}

    await client.delete(f"/api/v1/files/{kopie['id']}", headers=headers)
    r, stall = await worst_stall(client.delete(f"/api/v1/trash/{kopie['id']}", headers=headers))
    assert r.status_code == 204
    assert stall < 0.1, f"definitief verwijderen hield de event loop {stall:.2f} s vast"
    assert await ref_counts() == dict(refs)


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
