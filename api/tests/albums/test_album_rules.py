"""Slimme albums (bouwontwerp fase 6, Keuze 42; Z6): de regel, de verversing en album_skip.

- de regel past alleen op eigen, levende foto's en video's met een opnamedag, in beide motoren;
- items met de hand blijven; een andere regel haalt alleen 'rule'-items weg;
- wat de eigenaar weghaalt, komt in album_skip en komt niet terug; met de hand toevoegen wel;
- de grens van 10.000 (hier kleiner): 400 album_rule_too_many, of op de achtergrond de code;
- een regel die niet kan: 400 album_rule_invalid, en dan verandert er niets;
- de prullenbak: een item blijft lid en staat er na terugzetten weer;
- verversen: POST /refresh, elke nacht en 60 s na uploads (de haak van previews).

Of anderen een 'rule'-item zien (shared, de vraag en confirm): test_album_share_new.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import albums_rule as R
from app import jobs, previews
from app.models import AlbumItem, AlbumRule, AlbumSkip, Job, Node, Tag, User
from app.people import search as people_search
from app.routers import albums_rule as rule_router
from tests.factories import (
    code_of,
    create_album,
    create_file,
    create_folder,
    create_user,
    maker,
    params_of,
)
from tests.test_photos import login, photo

pytestmark = [pytest.mark.anyio, pytest.mark.zoek]

BASE = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _filters():
    """Een andere test kan filters.reset() of previews.forget_ready() gedaan hebben."""
    people_search.register()
    rule_router.register()


@pytest.fixture(params=["fts", "like"])
def motor(request, settings, monkeypatch) -> str:
    if request.param == "like":
        monkeypatch.setattr(settings, "search_index", "uit")
    return request.param


@pytest.fixture
async def huis(client, settings):
    """Eva met foto's en een video in en buiten de map Vakantie, een foto in de prullenbak, een
    onder een map in de prullenbak, een zonder opnamedag en een tekstbestand, allemaal met
    "strand" in de naam, plus berg.jpg; het album Zomer met berg.jpg (met de hand). Bram met een
    eigen foto "strand", een eigen map en een eigen tag."""
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        vakantie = await create_folder(session, eva, name="Vakantie")
        weg = await create_folder(session, eva, name="Weg", deleted=True)
        ids = {"eva": eva.id, "bram": bram.id, "vakantie": vakantie.id, "weg_map": weg.id}
        for key, name, dag, parent, kw in (
            ("een", "strand-1.jpg", 0, vakantie.id, {}),
            ("twee", "strand-2.jpg", 1, None, {}),
            ("berg", "berg.jpg", 2, None, {}),
            ("film", "strand-film.mp4", 3, None, {"mime": "video/mp4"}),
            ("weg", "strand-weg.jpg", 4, None, {"deleted": True}),
            ("onder_weg", "strand-onder.jpg", 5, weg.id, {}),
        ):
            node = await photo(session, eva, name, BASE + timedelta(days=dag), parent, **kw)
            ids[key] = node.id
        ids["zonder_dag"] = (
            await create_file(
                session, eva, name="strand-zonder.jpg", mime="image/jpeg", data=b"zonder"
            )  # fmt: skip
        ).id
        ids["tekst"] = (await create_file(session, eva, name="strand.txt")).id
        ids["bram_foto"] = (await photo(session, bram, "strand-bram.jpg", BASE)).id
        ids["bram_map"] = (await create_folder(session, bram, name="Brams map")).id
        tag = Tag(owner_id=bram.id, name="Van Bram")
        session.add(tag)
        berg = await session.get(Node, ids["berg"])
        ids["album"] = (await create_album(session, eva, "Zomer", items=[berg])).id
        await session.flush()
        ids["bram_tag"] = tag.id
        await session.commit()
    ids["h_eva"] = await login(client, "eva@example.nl")
    ids["h_bram"] = await login(client, "bram@example.nl")
    return ids


async def regel(client, headers, album, rule: dict, expect=200, **body):
    r = await client.put(
        f"/api/v1/albums/{album}/rule", json={"rule": rule, **body}, headers=headers
    )
    assert r.status_code == expect, r.text
    return r


async def items(client, headers, album) -> list[tuple[str, str, bool]]:
    """De items zoals de eigenaar ze ziet: (naam, bron, shared), nieuwste eerst."""
    r = await client.get(f"/api/v1/albums/{album}/items", headers=headers)
    assert r.status_code == 200, r.text
    return [(i["name"], i["album"]["source"], i["album"]["shared"]) for i in r.json()["items"]]


async def info(client, headers, album) -> dict:
    r = await client.get(f"/api/v1/albums/{album}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def nieuwe_foto(settings, owner_id, name: str, dag: int) -> tuple[uuid.UUID, str]:
    """Een foto erbij met een opnamedag (zoals na photometa), buiten de API om."""
    async with maker(settings)() as session:
        node = await photo(session, await session.get(User, owner_id), name,
                           BASE + timedelta(days=dag))  # fmt: skip
        await session.commit()
        return node.id, node.blob_id


async def skips(settings, album) -> set[uuid.UUID]:
    async with maker(settings)() as session:
        rows = await session.scalars(select(AlbumSkip.node_id).where(AlbumSkip.album_id == album))
        return set(rows.all())


async def leden(settings, album) -> dict[uuid.UUID, tuple[str, int]]:
    """Alle rijen van album_item, ook wat het album niet toont (de prullenbak)."""
    async with maker(settings)() as session:
        rows = await session.execute(
            select(AlbumItem.node_id, AlbumItem.source, AlbumItem.shared).where(
                AlbumItem.album_id == album
            )
        )
        return {node: (source, shared) for node, source, shared in rows}


STRAND = [
    ("strand-film.mp4", "rule", False),
    ("berg.jpg", "hand", True),
    ("strand-2.jpg", "rule", False),
    ("strand-1.jpg", "rule", False),
]


async def test_de_regel_past_op_eigen_levende_fotos_in_beide_motoren(client, huis, motor):
    eva, album = huis["h_eva"], huis["album"]
    r = await regel(client, eva, album, {"q": "strand"})
    assert r.json() == {"needs_confirm": False}
    # Niet: de prullenbak (ook via de map erboven), zonder opnamedag, geen foto, van Bram
    assert await items(client, eva, album) == STRAND
    got = await info(client, eva, album)
    assert got["count"] == 4
    rule = got["rule"]
    assert (rule["rule"], rule["share_new"], rule["count"], rule["code"]) == (
        {"q": "strand"}, False, 3, None)  # fmt: skip
    assert datetime.fromisoformat(rule["refreshed_at"]) > datetime.now(UTC) - timedelta(minutes=1)
    # Een andere regel: filters uit het register (type, in) zonder q; de rest gaat weg
    await regel(client, eva, album, {"type": ["image"], "in": str(huis["vakantie"])})
    assert await items(client, eva, album) == [STRAND[1], STRAND[3]]
    assert (await info(client, eva, album))["rule"]["rule"] == {
        "type": ["image"], "in": str(huis["vakantie"])}  # fmt: skip
    # Met q en een filter samen
    await regel(client, eva, album, {"q": "strand", "type": ["video"]})
    assert await items(client, eva, album) == STRAND[:2]
    # Bram ziet het album niet (niet gedeeld) en kan de regel niet zetten
    r = await client.get(f"/api/v1/albums/{album}", headers=huis["h_bram"])
    assert r.status_code == 404
    await regel(client, huis["h_bram"], album, {"q": "strand"}, expect=404)


async def test_items_met_de_hand_blijven_en_weghalen_gaat_in_album_skip(client, huis, settings):
    eva, album = huis["h_eva"], huis["album"]
    # berg past ook, maar staat er al met de hand: hij blijft een item met de hand
    await regel(client, eva, album, {"q": "berg"})
    assert await items(client, eva, album) == [("berg.jpg", "hand", True)]
    assert (await info(client, eva, album))["rule"]["count"] == 1
    await regel(client, eva, album, {"q": "strand"})
    assert await items(client, eva, album) == STRAND
    # Weghalen: een 'rule'-item en een item met de hand
    r = await client.post(
        f"/api/v1/albums/{album}/items/remove",
        json={"node_ids": [str(huis["een"]), str(huis["berg"])]},
        headers=eva,
    )
    assert r.json() == {"removed": 2}
    assert await skips(settings, album) == {huis["een"], huis["berg"]}
    # Een regel die op beide past, zet ze niet terug
    await regel(client, eva, album, {"type": ["image", "video"]})
    assert await items(client, eva, album) == [STRAND[0], STRAND[2]]
    # Met de hand toevoegen: weer erin, als item met de hand, en uit album_skip
    r = await client.post(
        f"/api/v1/albums/{album}/items", json={"node_ids": [str(huis["een"])]}, headers=eva
    )
    assert r.json() == {"added": 1, "skipped": []}
    assert await skips(settings, album) == {huis["berg"]}
    # Een 'rule'-item met de hand toevoegen: het wordt een item met de hand (en gedeeld)
    r = await client.post(
        f"/api/v1/albums/{album}/items", json={"node_ids": [str(huis["twee"])]}, headers=eva
    )
    assert r.json() == {"added": 1, "skipped": []}
    r = await client.post(
        f"/api/v1/albums/{album}/items", json={"node_ids": [str(huis["twee"])]}, headers=eva
    )
    assert [s["code"] for s in r.json()["skipped"]] == ["already"]
    assert await items(client, eva, album) == [
        ("strand-film.mp4", "rule", False),
        ("strand-2.jpg", "hand", True),
        ("strand-1.jpg", "hand", True),
    ]
    # Een regel waar ze niet meer op passen: de items met de hand blijven
    await regel(client, eva, album, {"q": "niets"})
    assert await items(client, eva, album) == [
        ("strand-2.jpg", "hand", True), ("strand-1.jpg", "hand", True)]  # fmt: skip
    # De regel weg: de items blijven, album_skip ook
    await regel(client, eva, album, {"q": "strand"})
    r = await client.delete(f"/api/v1/albums/{album}/rule", headers=eva)
    assert r.status_code == 204
    assert (await info(client, eva, album))["rule"] is None
    assert [n for n, _, _ in await items(client, eva, album)] == [
        "strand-film.mp4", "strand-2.jpg", "strand-1.jpg"]  # fmt: skip
    assert await skips(settings, album) == {huis["berg"]}
    r = await client.delete(f"/api/v1/albums/{album}/rule", headers=eva)
    assert r.status_code == 204
    # Zonder regel komt er niets in album_skip
    r = await client.post(
        f"/api/v1/albums/{album}/items/remove", json={"node_ids": [str(huis["film"])]},
        headers=eva,
    )  # fmt: skip
    assert r.json() == {"removed": 1}
    assert await skips(settings, album) == {huis["berg"]}


async def test_de_grens_van_het_aantal(client, huis, settings, monkeypatch):
    eva, album = huis["h_eva"], huis["album"]
    monkeypatch.setattr(R, "MAX_ITEMS", 2)
    # Meer dan de grens bij het zetten: 400 en er verandert niets
    r = await regel(client, eva, album, {"type": ["image", "video"]}, expect=400)
    assert code_of(r) == "album_rule_too_many" and params_of(r) == {"max": 2}
    assert (await info(client, eva, album))["rule"] is None
    assert await items(client, eva, album) == [("berg.jpg", "hand", True)]
    # Precies de grens: goed
    await regel(client, eva, album, {"q": "strand", "type": ["image"]})
    assert await items(client, eva, album) == [STRAND[1], STRAND[2], STRAND[3]]
    # Een foto erbij: bij de verversing op de achtergrond te veel; de items blijven zoals ze
    # waren en de eigenaar ziet de code
    nieuw_id, _ = await nieuwe_foto(settings, huis["eva"], "strand-3.jpg", 6)
    r = await client.post(f"/api/v1/albums/{album}/refresh", headers=eva)
    assert r.status_code == 202
    await jobs.run_pending(settings)
    rule = (await info(client, eva, album))["rule"]
    assert (rule["code"], rule["count"]) == ("album_rule_too_many", 2)
    assert await items(client, eva, album) == [STRAND[1], STRAND[2], STRAND[3]]
    # Weer binnen de grens (de nieuwe foto in de prullenbak): de code is weg
    r = await client.delete(f"/api/v1/files/{nieuw_id}", headers=eva)
    assert r.status_code == 204
    await client.post(f"/api/v1/albums/{album}/refresh", headers=eva)
    await jobs.run_pending(settings)
    rule = (await info(client, eva, album))["rule"]
    assert (rule["code"], rule["count"]) == (None, 2)


async def test_een_regel_die_niet_kan(client, huis, settings):
    eva, album = huis["h_eva"], huis["album"]
    for rule in (
        {},  # niets om te zoeken en geen filter
        {"q": "   "},
        {"q": " ".join(["woord"] * 11)},  # meer dan 10 woorden
        {"in": str(huis["bram_map"])},  # een map van Bram
        {"in": str(huis["weg_map"])},  # een map in de prullenbak
        {"in": str(uuid.uuid4())},
        {"in": str(huis["een"])},  # een bestand, geen map
        {"tag": [str(huis["bram_tag"])]},  # een tag van Bram
        {"person": [str(uuid.uuid4())]},  # een persoon die er niet is
    ):
        r = await regel(client, eva, album, rule, expect=400)
        assert code_of(r) == "album_rule_invalid", rule
    # Wat de API niet kent: 422 (een regel zoekt nooit op beeld, en geen pagina)
    for rule in ({"mode": "beeld"}, {"offset": 10}, {"q": "x" * 201}):
        await regel(client, eva, album, rule, expect=422)
    async with maker(settings)() as session:
        assert (await session.scalars(select(AlbumRule))).all() == []
    assert await items(client, eva, album) == [("berg.jpg", "hand", True)]
    # Geen album, of niet van jou: 404
    await regel(client, eva, uuid.uuid4(), {"q": "strand"}, expect=404)
    await regel(client, eva, huis["een"], {"q": "strand"}, expect=404)
    r = await client.post(f"/api/v1/albums/{album}/rule/confirm", json={"share_new": True},
                          headers=eva)  # fmt: skip
    assert r.status_code == 404  # nog geen regel


async def test_de_prullenbak_houdt_een_item_lid(client, huis, settings):
    eva, album = huis["h_eva"], huis["album"]
    await regel(client, eva, album, {"q": "strand"})
    r = await client.delete(f"/api/v1/files/{huis['twee']}", headers=eva)
    assert r.status_code == 204
    r = await client.delete(f"/api/v1/files/{huis['vakantie']}", headers=eva)
    assert r.status_code == 204
    await client.post(f"/api/v1/albums/{album}/refresh", headers=eva)
    await jobs.run_pending(settings)
    # Het album toont ze niet, maar ze blijven lid (de verversing haalt ze niet weg)
    assert await items(client, eva, album) == STRAND[:2]
    assert set(await leden(settings, album)) == {
        huis["berg"], huis["film"], huis["een"], huis["twee"]}  # fmt: skip
    assert (await info(client, eva, album))["rule"]["count"] == 1
    # Terugzetten: ze staan er weer, als 'rule'-item
    for node in (huis["twee"], huis["vakantie"]):
        r = await client.post(f"/api/v1/files/{node}/restore", headers=eva)
        assert r.status_code == 200, r.text
    assert await items(client, eva, album) == STRAND
    # Een foto die al in de prullenbak lag (strand-weg.jpg) is nooit lid geworden; terugzetten
    # en verversen zet hem erbij
    r = await client.post(f"/api/v1/files/{huis['weg']}/restore", headers=eva)
    assert r.status_code == 200, r.text
    assert huis["weg"] not in await leden(settings, album)
    await regel(client, eva, album, {"q": "strand"})
    assert ("strand-weg.jpg", "rule", False) in await items(client, eva, album)
    # Definitief weg: het lid is weg (ON DELETE CASCADE)
    r = await client.delete(f"/api/v1/files/{huis['twee']}", headers=eva)
    r = await client.delete(f"/api/v1/trash/{huis['twee']}", headers=eva)
    assert r.status_code == 204, r.text
    assert huis["twee"] not in await leden(settings, album)


async def test_verversen_met_de_taak_de_nacht_en_na_uploads(client, huis, settings, monkeypatch):
    eva, album = huis["h_eva"], huis["album"]
    await regel(client, eva, album, {"q": "strand"})
    gemeld: list[tuple] = []
    monkeypatch.setattr(rule_router.events, "publish",
                        lambda ids, type, data: gemeld.append((set(ids), type, data)))  # fmt: skip

    async def taken() -> list[Job]:
        async with maker(settings)() as session:
            return list(
                (await session.scalars(select(Job).where(Job.type == rule_router.JOB))).all()
            )

    # Na een upload: de haak van previews zet een taak klaar voor over 60 s, één per album
    _, sha = await nieuwe_foto(settings, huis["eva"], "strand-3.jpg", 6)
    await previews.announce(maker(settings), sha, dated=False)  # nog geen opnamedag: niets
    assert await taken() == []
    begin = datetime.now(UTC)
    await previews.announce(maker(settings), sha, dated=True)
    await previews.announce(maker(settings), sha, dated=True)
    [taak] = await taken()
    assert taak.key == f"albums.rule:{album}" and taak.status == "queued"
    wacht = taak.run_after.replace(tzinfo=taak.run_after.tzinfo or UTC) - begin
    assert timedelta(seconds=55) < wacht < timedelta(seconds=65)
    # Nog niet aan de beurt; na 60 s wel
    await jobs.run_pending(settings)
    assert len(await items(client, eva, album)) == 4
    await jobs.run_pending(settings, jobs.now() + timedelta(seconds=61))
    assert ("strand-3.jpg", "rule", False) == (await items(client, eva, album))[0]
    # Alleen de eigenaar hoort het: wat erbij kwam, wordt niet gedeeld
    assert [g for g in gemeld if g[1].startswith("album.")] == [
        ({huis["eva"]}, "album.changed", {"id": str(album)})]  # fmt: skip
    # Een foto van Bram zet niets klaar voor Eva's album
    async with maker(settings)() as session:
        bram_sha = (await session.get(Node, huis["bram_foto"])).blob_id
    await previews.announce(maker(settings), bram_sha, dated=True)
    assert [t.status for t in await taken()] == ["done"]

    # Elke nacht: een taak per album met een regel
    await nieuwe_foto(settings, huis["eva"], "strand-4.jpg", 7)
    assert "albums.rule.nacht" in jobs._housekeeping
    await rule_router.nightly(settings)
    await rule_router.nightly(settings)  # één taak per album tegelijk
    assert [t.status for t in await taken()].count("queued") == 1
    await jobs.run_pending(settings)
    assert (await items(client, eva, album))[0] == ("strand-4.jpg", "rule", False)

    # POST /refresh: 202 en een taak; zonder regel gebeurt er niets
    r = await client.post(f"/api/v1/albums/{album}/refresh", headers=eva)
    assert r.status_code == 202 and r.content == b""
    assert [t.status for t in await taken()].count("queued") == 1
    await jobs.run_pending(settings)
    await client.delete(f"/api/v1/albums/{album}/rule", headers=eva)
    r = await client.post(f"/api/v1/albums/{album}/refresh", headers=eva)
    assert r.status_code == 202
    assert [t.status for t in await taken()].count("queued") == 0
    r = await client.post(f"/api/v1/albums/{album}/refresh", headers=huis["h_bram"])
    assert r.status_code == 404
    # Een taak voor een album dat intussen weg is, of voor iets dat geen album is: niets
    await rule_router.run(settings, {"album": str(uuid.uuid4())})
    await rule_router.run(settings, {"album": str(huis["een"])})
