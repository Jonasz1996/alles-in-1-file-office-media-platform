import asyncio
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import anyio
import pytest
from sqlalchemy import event, insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import events
from app.db import get_engine
from app.events import Hub
from app.mime import CATEGORIES, category
from app.models import Node, NodeTag, Tag
from app.routers import search as search_module
from app.text import fold
from tests.test_files import mkdir, new_user, put, root
from tests.test_mime import zip_bytes

PNG = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"


async def find(client, headers, expect=200, **params):
    r = await client.get("/api/v1/search", params=params, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def names(result) -> list[str]:
    return [hit["node"]["name"] for hit in result["results"]]


@contextmanager
def count_queries(settings):
    seen: list[str] = []

    def before(_conn, _cursor, statement, *_args):
        seen.append(statement)

    engine = get_engine(settings).sync_engine
    event.listen(engine, "before_cursor_execute", before)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", before)


@pytest.mark.anyio
async def test_zoeken_op_naam(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    vakantie = await mkdir(client, headers, root_id, "Vakantie Café")
    await put(client, headers, vakantie, "café menu.pdf", b"%PDF-1.4\n%\xe2")
    await put(client, headers, root_id, "CAFE.txt", b"x")
    await put(client, headers, root_id, "Cafeïne.md", b"x")
    await put(client, headers, root_id, "thee.txt", b"x")
    await put(client, headers, root_id, "100% zeker.txt", b"x")
    await put(client, headers, root_id, "1000 dingen.txt", b"x")
    await put(client, headers, root_id, "a_b.txt", b"x")
    await put(client, headers, root_id, "axb.txt", b"x")

    # Hoofdletters en accenten tellen niet; mappen eerst, dan op naam
    result = await find(client, headers, q="cafe")
    assert names(result) == ["Vakantie Café", "café menu.pdf", "CAFE.txt", "Cafeïne.md"]
    assert result["more"] is False
    # Elk woord moet erin staan, in elke volgorde
    assert names(await find(client, headers, q="MENU  café")) == ["café menu.pdf"]
    assert names(await find(client, headers, q="vakantie thee")) == []
    # % en _ zijn gewone tekens
    assert names(await find(client, headers, q="100%")) == ["100% zeker.txt"]
    assert names(await find(client, headers, q="a_b")) == ["a_b.txt"]

    # Het pad: van de hoofdmap tot de map waar het in staat
    [hit] = (await find(client, headers, q="menu"))["results"]
    assert [p["name"] for p in hit["path"]] == ["", "Vakantie Café"]
    assert [p["id"] for p in hit["path"]] == [root_id, vakantie]
    assert hit["node"]["tags"] == []
    [hit] = (await find(client, headers, q="thee"))["results"]
    assert [p["id"] for p in hit["path"]] == [root_id]


@pytest.mark.anyio
async def test_niets_uit_de_prullenbak(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    weg = await mkdir(client, headers, root_id, "rapport map")
    diep = await mkdir(client, headers, weg, "sub")
    await put(client, headers, diep, "rapport diep.txt", b"x")
    los = (await put(client, headers, root_id, "rapport los.txt", b"x")).json()["id"]
    await put(client, headers, root_id, "rapport blijft.txt", b"x")
    await client.delete(f"/api/v1/files/{weg}", headers=headers)
    await client.delete(f"/api/v1/files/{los}", headers=headers)
    assert names(await find(client, headers, q="rapport")) == ["rapport blijft.txt"]
    # Ook niet door in die map te zoeken
    await find(client, headers, expect=404, q="rapport", **{"in": weg})
    await find(client, headers, expect=404, q="rapport", **{"in": diep})


@pytest.mark.anyio
async def test_zoeken_op_soort(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    await mkdir(client, headers, root_id, "item map")
    samples = {
        "item foto.png": PNG,
        "item scan.pdf": b"%PDF-1.4\n%\xe2",
        "item brief.docx": zip_bytes([("[Content_Types].xml", b"<T/>"), ("word/d.xml", b"<w/>")]),
        "item notitie.txt": b"tekst",
        "item archief.zip": zip_bytes([("a.txt", b"a")]),
        "item film.mp4": b"\0\0\0\x18ftypisom\0\0\0\0isomavc1",
        "item liedje.mp3": b"ID3\x04\0\0\0\0\0\0",
        "item onbekend.bin": b"\0\x01\x02",
        "item leeg": b"",
    }
    for name, data in samples.items():
        assert (await put(client, headers, root_id, name, data)).status_code == 201
    everything = (await find(client, headers, q="item"))["results"]
    assert len(everything) == len(samples) + 1
    expected = {c: [] for c in CATEGORIES}
    for hit in everything:
        node = hit["node"]
        expected[category(node["mime"], node["kind"])].append(node["name"])
    assert all(expected[c] for c in CATEGORIES)  # elke soort komt voor
    # De SQL-voorwaarde zegt hetzelfde als mime.category()
    for c in CATEGORIES:
        assert names(await find(client, headers, q="item", type=c)) == expected[c], c
    assert expected["document"] == ["item brief.docx", "item notitie.txt"]
    assert expected["other"] == ["item leeg", "item onbekend.bin"]
    await find(client, headers, expect=422, type="foto")


@pytest.mark.anyio
async def test_in_map_limiet_en_andermans_bestanden(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = await mkdir(client, headers, root_id, "A")
    b = await mkdir(client, headers, a, "B")
    for i in range(3):
        await put(client, headers, b, f"doc {i}.txt", b"x")
        await put(client, headers, root_id, f"doc buiten {i}.txt", b"x")
    assert len(names(await find(client, headers, q="doc", **{"in": a}))) == 3
    assert names(await find(client, headers, q="", **{"in": a})) == [
        "B",
        "doc 0.txt",
        "doc 1.txt",
        "doc 2.txt",
    ]
    result = await find(client, headers, q="doc", limit=4)
    assert len(result["results"]) == 4 and result["more"] is True
    assert (await find(client, headers, q="doc", limit=6))["more"] is False
    await find(client, headers, expect=422, limit=0)
    await find(client, headers, expect=422, limit=201)
    f = (await put(client, headers, root_id, "f.txt", b"x")).json()["id"]
    await find(client, headers, expect=404, **{"in": f})

    z = await new_user(client, headers, "z@example.nl")
    assert names(await find(client, z, q="doc")) == []
    await find(client, z, expect=404, **{"in": a})


@pytest.mark.anyio
async def test_tags_beheren(client, admin):
    headers, _ = admin
    r = await client.post(
        "/api/v1/tags", json={"name": " Werk ", "color": "#1D5FD1"}, headers=headers
    )
    assert r.status_code == 201, r.text
    werk = r.json()
    assert (werk["name"], werk["color"], werk["count"]) == ("Werk", "#1d5fd1", 0)
    # Hoofdletters en accenten tellen niet: dat is dezelfde naam
    for name in ("werk", "WÉRK"):
        r = await client.post("/api/v1/tags", json={"name": name}, headers=headers)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "name_taken"
    for bad in ({"name": ""}, {"name": "x" * 51}, {"name": "a", "color": "rood"}, {"name": "\x07"}):
        assert (await client.post("/api/v1/tags", json=bad, headers=headers)).status_code == 422
    # Ook geen C1-stuurtekens en niets dat de schrijfrichting omdraait
    for bad in ("a\u0085b", "\u202ekrow", "a\u2066b", "\u200fx"):
        r = await client.post("/api/v1/tags", json={"name": bad}, headers=headers)
        assert r.status_code == 422, bad
    # De lengte telt zonder spaties eromheen; samengestelde emoji mogen
    for good in (" " + "y" * 50 + " ", "\U0001f468\u200d\U0001f469\u200d\U0001f467 Familie"):
        r = await client.post("/api/v1/tags", json={"name": good}, headers=headers)
        assert r.status_code == 201, r.text
        assert r.json()["name"] == good.strip()
        await client.delete(f"/api/v1/tags/{r.json()['id']}", headers=headers)
    r = await client.patch(f"/api/v1/tags/{werk['id']}", json={"name": "z" * 51}, headers=headers)
    assert r.status_code == 422
    prive = (await client.post("/api/v1/tags", json={"name": "Privé"}, headers=headers)).json()
    assert prive["color"] == ""

    r = await client.patch(f"/api/v1/tags/{prive['id']}", json={"name": "werk"}, headers=headers)
    assert r.status_code == 409
    r = await client.patch(
        f"/api/v1/tags/{prive['id']}", json={"name": "Thuis", "color": "#00aa00"}, headers=headers
    )
    assert (r.json()["name"], r.json()["color"]) == ("Thuis", "#00aa00")
    r = await client.patch(f"/api/v1/tags/{prive['id']}", json={"color": ""}, headers=headers)
    assert (r.json()["name"], r.json()["color"]) == ("Thuis", "")
    # Zelfde naam, andere hoofdletters, voor dezelfde tag mag
    r = await client.patch(f"/api/v1/tags/{werk['id']}", json={"name": "WERK"}, headers=headers)
    assert r.status_code == 200 and r.json()["name"] == "WERK"
    tags = (await client.get("/api/v1/tags", headers=headers)).json()
    assert [t["name"] for t in tags] == ["Thuis", "WERK"]

    # Tags zijn per gebruiker
    z = await new_user(client, headers, "z@example.nl")
    assert (await client.get("/api/v1/tags", headers=z)).json() == []
    assert (await client.post("/api/v1/tags", json={"name": "Werk"}, headers=z)).status_code == 201
    assert (await client.patch(f"/api/v1/tags/{werk['id']}", json={}, headers=z)).status_code == 404
    assert (await client.delete(f"/api/v1/tags/{werk['id']}", headers=z)).status_code == 404
    assert (await client.delete(f"/api/v1/tags/{werk['id']}", headers=headers)).status_code == 204
    assert [t["name"] for t in (await client.get("/api/v1/tags", headers=headers)).json()] == [
        "Thuis"
    ]


@pytest.mark.anyio
async def test_tags_op_bestanden(client, admin, monkeypatch, settings):
    headers, tokens = admin
    monkeypatch.setattr(events, "hub", Hub())
    root_id = (await root(client, headers))["folder"]["id"]
    folder = await mkdir(client, headers, root_id, "Map")
    a = (await put(client, headers, folder, "a.txt", b"a")).json()["id"]
    b = (await put(client, headers, root_id, "b.txt", b"b")).json()["id"]
    werk = (await client.post("/api/v1/tags", json={"name": "Werk"}, headers=headers)).json()
    alfa = (await client.post("/api/v1/tags", json={"name": "alfa"}, headers=headers)).json()

    with events.hub.subscribe(uuid.UUID(tokens["user"]["id"])) as sub:
        r = await client.put(
            f"/api/v1/files/{a}/tags", json={"tag_ids": [werk["id"], alfa["id"]]}, headers=headers
        )
        assert sub.queue.get_nowait() == {
            "type": "node.changed",
            "data": {"id": a, "parent_id": folder},
        }
    assert r.status_code == 200
    assert [t["name"] for t in r.json()] == ["alfa", "Werk"]
    await client.put(f"/api/v1/files/{b}/tags", json={"tag_ids": [werk["id"]]}, headers=headers)
    await client.put(
        f"/api/v1/files/{folder}/tags", json={"tag_ids": [werk["id"]]}, headers=headers
    )

    # In de lijst, bij info en bij zoeken
    listing = (await client.get(f"/api/v1/files/{folder}/children", headers=headers)).json()
    assert [t["name"] for t in listing["children"][0]["tags"]] == ["alfa", "Werk"]
    assert [t["name"] for t in listing["folder"]["tags"]] == ["Werk"]
    info = (await client.get(f"/api/v1/files/{b}", headers=headers)).json()
    assert info["tags"] == [{"id": werk["id"], "name": "Werk", "color": ""}]
    assert names(await find(client, headers, tag=werk["id"])) == ["Map", "a.txt", "b.txt"]
    assert names(await find(client, headers, tag=werk["id"], q="a")) == ["Map", "a.txt"]
    assert names(await find(client, headers, tag=alfa["id"])) == ["a.txt"]
    counts = {
        t["name"]: t["count"] for t in (await client.get("/api/v1/tags", headers=headers)).json()
    }
    assert counts == {"alfa": 1, "Werk": 3}

    # Vervangen, niet erbij
    r = await client.put(f"/api/v1/files/{a}/tags", json={"tag_ids": [alfa["id"]]}, headers=headers)
    assert [t["name"] for t in r.json()] == ["alfa"]
    r = await client.put(f"/api/v1/files/{a}/tags", json={"tag_ids": []}, headers=headers)
    assert r.json() == []
    # In de prullenbak telt niet mee (ook niet wat in een weggegooide map staat)
    await client.put(f"/api/v1/files/{a}/tags", json={"tag_ids": [werk["id"]]}, headers=headers)
    await client.delete(f"/api/v1/files/{folder}", headers=headers)
    # De prullenbak laat de tags ook zien
    trash = (await client.get("/api/v1/trash", headers=headers)).json()
    assert [(n["name"], [t["name"] for t in n["tags"]]) for n in trash] == [("Map", ["Werk"])]
    counts = {
        t["name"]: t["count"] for t in (await client.get("/api/v1/tags", headers=headers)).json()
    }
    assert counts == {"alfa": 0, "Werk": 1}
    assert names(await find(client, headers, tag=werk["id"])) == ["b.txt"]

    # Alleen eigen tags en eigen bestanden
    z = await new_user(client, headers, "z@example.nl")
    eigen = (await client.post("/api/v1/tags", json={"name": "Z"}, headers=z)).json()
    r = await client.put(
        f"/api/v1/files/{b}/tags", json={"tag_ids": [eigen["id"]]}, headers=headers
    )
    assert r.status_code == 404
    r = await client.put(f"/api/v1/files/{b}/tags", json={"tag_ids": [eigen["id"]]}, headers=z)
    assert r.status_code == 404
    await find(client, headers, expect=404, tag=eigen["id"])
    await find(client, headers, expect=404, tag=str(uuid.uuid4()))
    # Een tag van een ander op hetzelfde bestand (straks via delen) ziet alleen die ander
    async with async_sessionmaker(get_engine(settings))() as session:
        session.add(NodeTag(node_id=uuid.UUID(b), tag_id=uuid.UUID(eigen["id"])))
        await session.commit()
    info = (await client.get(f"/api/v1/files/{b}", headers=headers)).json()
    assert [t["name"] for t in info["tags"]] == ["Werk"]
    # Weg met de tag: weg van de bestanden
    await client.delete(f"/api/v1/tags/{werk['id']}", headers=headers)
    assert (await client.get(f"/api/v1/files/{b}", headers=headers)).json()["tags"] == []


@pytest.mark.anyio
async def test_hooguit_zoveel_tags(client, admin, monkeypatch):
    headers, _ = admin
    monkeypatch.setattr(search_module, "MAX_TAGS", 2)
    for name in ("a", "b"):
        r = await client.post("/api/v1/tags", json={"name": name}, headers=headers)
        assert r.status_code == 201
    r = await client.post("/api/v1/tags", json={"name": "c"}, headers=headers)
    assert r.status_code == 422 and r.json()["detail"] == "Je kunt hooguit 2 tags hebben"
    # Andere gebruikers hebben hun eigen ruimte
    z = await new_user(client, headers, "z@example.nl")
    assert (await client.post("/api/v1/tags", json={"name": "c"}, headers=z)).status_code == 201


@pytest.mark.anyio
async def test_tegelijk_dezelfde_tag_aanmaken(client, admin, monkeypatch):
    headers, _ = admin
    arrived, both = [], asyncio.Event()
    real = search_module.check_free

    async def waiting(*args, **kwargs):
        # Beide verzoeken zouden tegelijk controleren of de naam vrij is
        arrived.append(1)
        if len(arrived) >= 2:
            both.set()
        with anyio.move_on_after(0.3):
            await both.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(search_module, "check_free", waiting)
    r1, r2 = await asyncio.gather(
        client.post("/api/v1/tags", json={"name": "Foto"}, headers=headers),
        client.post("/api/v1/tags", json={"name": "foto"}, headers=headers),
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 409]
    assert len((await client.get("/api/v1/tags", headers=headers)).json()) == 1


@pytest.mark.anyio
async def test_tegelijk_tags_zetten(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    f = (await put(client, headers, root_id, "f.txt", b"f")).json()["id"]
    ids = [
        (await client.post("/api/v1/tags", json={"name": n}, headers=headers)).json()["id"]
        for n in "abcd"
    ]
    sets = [ids[:2], ids[2:], ids[1:3], []]
    await asyncio.gather(
        *(client.put(f"/api/v1/files/{f}/tags", json={"tag_ids": s}, headers=headers) for s in sets)
    )
    # Precies één van de sets, nooit een mengsel
    final = {
        t["id"] for t in (await client.get(f"/api/v1/files/{f}", headers=headers)).json()["tags"]
    }
    assert final in [set(s) for s in sets]


@pytest.mark.anyio
async def test_lijst_en_zoeken_zonder_query_per_bestand(client, admin, settings):
    """Tags en paden kosten één query per verzoek, niet één per bestand."""
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    tag = (await client.post("/api/v1/tags", json={"name": "t"}, headers=headers)).json()["id"]
    few, many = (
        await mkdir(client, headers, root_id, "few"),
        await mkdir(client, headers, root_id, "many"),
    )
    deep = many
    for i in range(4):
        deep = await mkdir(client, headers, deep, f"diep {i}")
    for folder, count in ((few, 2), (many, 30), (deep, 30)):
        for i in range(count):
            node = (await put(client, headers, folder, f"bestand {i}.txt", b"x")).json()["id"]
            await client.put(f"/api/v1/files/{node}/tags", json={"tag_ids": [tag]}, headers=headers)

    async def statements(url, **params):
        with count_queries(settings) as seen:
            r = await client.get(url, params=params, headers=headers)
        assert r.status_code == 200
        return len(seen), r.json()

    small, _ = await statements(f"/api/v1/files/{few}/children")
    large, listing = await statements(f"/api/v1/files/{many}/children")
    assert small == large
    assert all(c["tags"] for c in listing["children"] if c["kind"] == "file")
    small, _ = await statements("/api/v1/search", q="bestand", limit=2)
    large, result = await statements("/api/v1/search", q="bestand", limit=200)
    assert small == large
    assert len(result["results"]) == 62
    assert all(hit["node"]["tags"] for hit in result["results"])
    assert max(len(hit["path"]) for hit in result["results"]) == 6


@pytest.mark.anyio
async def test_snel_genoeg_met_twintigduizend_bestanden(client, admin, settings):
    headers, tokens = admin
    owner = uuid.UUID(tokens["user"]["id"])
    root_id = uuid.UUID((await root(client, headers))["folder"]["id"])
    now = datetime.now(UTC)
    rows, weg = [], None

    def node(parent, name, kind="file", **extra):
        row = {
            "id": uuid.uuid4(),
            "parent_id": parent,
            "owner_id": owner,
            "name": name,
            "search_name": fold(name),
            "kind": kind,
            "mime": "image/jpeg" if kind == "file" else None,
            "size": 1,
            "created_at": now,
            "modified_at": now,
            **extra,
        }
        rows.append(row)
        return row["id"]

    for i in range(20):
        top = node(root_id, f"Map {i:02d}", "dir", deleted_at=now if i == 0 else None)
        weg = weg or top
        for j in range(10):
            sub = node(top, f"Sub {i:02d}-{j}", "dir")
            for k in range(100):
                node(sub, f"Foto {i:02d}-{j}-{k}.jpg")
    assert len(rows) > 20_000
    async with async_sessionmaker(get_engine(settings))() as session:
        await session.execute(insert(Node), rows)
        tag = Tag(owner_id=owner, name="mooi")
        session.add(tag)
        await session.flush()
        tag_id = tag.id
        session.add_all(NodeTag(node_id=r["id"], tag_id=tag_id) for r in rows[::50])
        await session.commit()

    cases = [
        ({"q": "foto 07", "limit": 200}, 200, True),
        ({"q": "foto 13-3-42"}, 1, False),
        ({"q": "07-3-4"}, 11, False),
        ({"q": "foto 00-3-42"}, 0, False),  # in de prullenbak
        ({"q": "sub", "type": "folder", "limit": 200}, 190, False),
        ({"tag": str(tag_id), "limit": 200}, 200, True),
        ({"q": "bestaat niet"}, 0, False),
    ]
    for params, count, more in cases:
        started = time.perf_counter()
        result = await find(client, headers, **params)
        elapsed = time.perf_counter() - started
        assert (len(result["results"]), result["more"]) == (count, more), params
        assert elapsed < 1.0, f"{params}: {elapsed:.2f} s"
    started = time.perf_counter()
    tags = (await client.get("/api/v1/tags", headers=headers)).json()
    assert time.perf_counter() - started < 1.0
    assert tags[0]["count"] == len(rows[::50]) - sum(
        1 for r in rows[::50] if r["name"].startswith(("Map 00", "Sub 00-", "Foto 00-"))
    )
    assert weg is not None
