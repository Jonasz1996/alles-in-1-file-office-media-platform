"""Een slim album delen (bouwontwerp fase 6, Keuze 42; Z6): wat de regel erbij zet, ziet niemand
anders tot de eigenaar "ja" zegt.

- Bram (een deling) en een publieke link zien een nieuw 'rule'-item niet: niet in het album, niet
  als bestand, niet als download of preview; na confirm wel (access._via_albums en
  public._in_album geven alleen shared 1);
- "nee" houdt ze voor de eigenaar; needs_confirm alleen als het verzoek de vraag niet beantwoordt;
- een nieuwe deling of link op een album met een regel en share_new 0 zegt hoeveel items alleen
  voor de eigenaar zijn (rule_items_private);
- alleen de eigenaar zet, beantwoordt en ververst; album.changed alleen naar wie het verschil ziet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import albums_rule as R
from app import events
from app.models import Node, User
from app.people import search as people_search
from app.routers import albums_rule as rule_router
from tests.factories import code_of, create_album, create_folder, create_user, maker
from tests.test_photos import login, photo
from tests.test_public import bearer, fresh_limits, new_link, open_link  # noqa: F401

pytestmark = [pytest.mark.anyio, pytest.mark.zoek]

BASE = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _filters():
    people_search.register()
    rule_router.register()


@pytest.fixture
async def huis(client, settings):
    """Eva met strand-1, strand-2 en berg (berg met de hand in het album Zomer); Bram. Het album
    is gedeeld met Bram en heeft een publieke leeslink."""
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        ids = {"eva": eva.id, "bram": bram.id}
        for key, name, dag in (("een", "strand-1.jpg", 0), ("twee", "strand-2.jpg", 1),
                               ("berg", "berg.jpg", 2)):  # fmt: skip
            ids[key] = (await photo(session, eva, name, BASE + timedelta(days=dag))).id
        berg = await session.get(Node, ids["berg"])
        ids["album"] = (await create_album(session, eva, "Zomer", items=[berg])).id
        await session.commit()
    ids["h_eva"] = await login(client, "eva@example.nl")
    ids["h_bram"] = await login(client, "bram@example.nl")
    r = await client.post(
        f"/api/v1/files/{ids['album']}/shares",
        json={"user_id": str(ids["bram"]), "permission": "read"},
        headers=ids["h_eva"],
    )
    assert r.status_code == 201, r.text
    assert r.json()["rule_items_private"] is None  # nog geen regel
    link = await new_link(client, ids["h_eva"], ids["album"], permission="read")
    assert link["rule_items_private"] is None
    ids["h_link"] = bearer((await open_link(client, link))["key"])
    return ids


async def regel(client, headers, album, rule: dict, expect=200, **body) -> dict:
    r = await client.put(
        f"/api/v1/albums/{album}/rule", json={"rule": rule, **body}, headers=headers
    )
    assert r.status_code == expect, r.text
    return r.json()


async def confirm(client, headers, album, share_new: bool, expect=200) -> dict:
    r = await client.post(
        f"/api/v1/albums/{album}/rule/confirm", json={"share_new": share_new}, headers=headers
    )
    assert r.status_code == expect, r.text
    return r.json()


async def eigenaar(client, huis) -> list[tuple[str, str, bool]]:
    r = await client.get(f"/api/v1/albums/{huis['album']}/items", headers=huis["h_eva"])
    assert r.status_code == 200, r.text
    return [(i["name"], i["album"]["source"], i["album"]["shared"]) for i in r.json()["items"]]


async def telling(client, huis) -> tuple[int, int]:
    """Wat de eigenaar over de regel hoort (items uit de regel, waarvan alleen voor hem), in
    GET /albums/{id} en in GET /albums hetzelfde."""
    h, album = huis["h_eva"], str(huis["album"])
    rule = (await client.get(f"/api/v1/albums/{album}", headers=h)).json()["rule"]
    [listed] = [a for a in (await client.get("/api/v1/albums", headers=h)).json()["albums"]
                if a["id"] == album]  # fmt: skip
    assert listed["rule"] == rule
    return rule["items"], rule["private"]


async def ziet_bram(client, huis) -> list[str]:
    """Wat Bram van het album ziet, langs elke weg; allemaal hetzelfde."""
    album, h = huis["album"], huis["h_bram"]
    r = await client.get(f"/api/v1/albums/{album}/items", headers=h)
    assert r.status_code == 200, r.text
    page = r.json()["items"]
    assert all(i["album"] is None for i in page)  # bron en shared alleen voor de eigenaar
    names = [i["name"] for i in page]
    ids = {i["id"] for i in page}
    r = await client.get(f"/api/v1/albums/{album}/ids", headers=h)
    assert set(r.json()["ids"]) == ids
    info = (await client.get(f"/api/v1/albums/{album}", headers=h)).json()
    assert info["count"] == len(names) and info["rule"] is None
    [listed] = [a for a in (await client.get("/api/v1/albums", headers=h)).json()["albums"]
                if a["id"] == str(album)]  # fmt: skip
    assert listed["count"] == len(names)
    for key in ("een", "twee", "berg"):
        r = await client.get(f"/api/v1/files/{huis[key]}", headers=h)
        assert r.status_code == (200 if str(huis[key]) in ids else 404), (key, r.text)
    return names


async def ziet_de_link(client, huis) -> list[str]:
    h = huis["h_link"]
    r = await client.get("/api/v1/public/album/items", headers=h)
    assert r.status_code == 200, r.text
    names = [i["name"] for i in r.json()["items"]]
    ids = {i["id"] for i in r.json()["items"]}
    r = await client.post(
        "/api/v1/public/previews",
        json={"node_ids": sorted(ids | {str(huis[k]) for k in ("een", "twee", "berg")})},
        headers=h,
    )
    assert set(r.json()) == ids
    for key in ("een", "twee"):
        if str(huis[key]) not in ids:
            r = await client.get(f"/api/v1/public/download/{huis[key]}", headers=h)
            assert r.status_code == 404
            r = await client.get(f"/api/v1/public/preview/{huis[key]}", headers=h)
            assert r.status_code == 404
    return names


async def test_bram_en_de_link_zien_een_nieuw_rule_item_pas_na_ja(client, huis, settings):
    album = huis["album"]
    assert await ziet_bram(client, huis) == ["berg.jpg"]
    # De regel op een gedeeld album: de vraag, en de nieuwe items zijn alleen voor Eva
    assert await regel(client, huis["h_eva"], album, {"q": "strand"}) == {"needs_confirm": True}
    assert await eigenaar(client, huis) == [
        ("berg.jpg", "hand", True),
        ("strand-2.jpg", "rule", False),
        ("strand-1.jpg", "rule", False),
    ]
    assert await ziet_bram(client, huis) == ["berg.jpg"]
    assert await ziet_de_link(client, huis) == ["berg.jpg"]
    assert await telling(client, huis) == (2, 2)
    # Ja: alle 'rule'-items gedeeld, en wat er later bij komt ook
    assert await confirm(client, huis["h_eva"], album, True) == {"needs_confirm": False}
    assert [s for _, _, s in await eigenaar(client, huis)] == [True, True, True]
    assert await telling(client, huis) == (2, 0)
    alle = ["berg.jpg", "strand-2.jpg", "strand-1.jpg"]
    assert await ziet_bram(client, huis) == alle
    assert await ziet_de_link(client, huis) == alle
    rule = (await client.get(f"/api/v1/albums/{album}", headers=huis["h_eva"])).json()["rule"]
    assert rule["share_new"] is True
    async with maker(settings)() as session:
        eva = await session.get(User, huis["eva"])
        await photo(session, eva, "strand-3.jpg", BASE + timedelta(days=3))
        await session.commit()
    await rule_router.run(settings, {"album": str(album)})
    assert await ziet_bram(client, huis) == ["strand-3.jpg", *alle]
    assert await ziet_de_link(client, huis) == ["strand-3.jpg", *alle]
    assert await telling(client, huis) == (3, 0)
    # Al gedeeld: geen vraag meer
    assert await regel(client, huis["h_eva"], album, {"q": "strand"}) == {"needs_confirm": False}


async def test_nee_houdt_ze_voor_de_eigenaar(client, huis, settings):
    album = huis["album"]
    await regel(client, huis["h_eva"], album, {"q": "strand"})
    assert await confirm(client, huis["h_eva"], album, False) == {"needs_confirm": False}
    assert await ziet_bram(client, huis) == ["berg.jpg"]
    assert await ziet_de_link(client, huis) == ["berg.jpg"]
    async with maker(settings)() as session:
        eva = await session.get(User, huis["eva"])
        await photo(session, eva, "strand-3.jpg", BASE + timedelta(days=3))
        await session.commit()
    await rule_router.run(settings, {"album": str(album)})
    assert ("strand-3.jpg", "rule", False) in await eigenaar(client, huis)
    assert await ziet_bram(client, huis) == ["berg.jpg"]
    assert await telling(client, huis) == (3, 3)
    # Een nieuwe regel zonder antwoord: de vraag opnieuw; met share_new het antwoord meteen
    assert await regel(client, huis["h_eva"], album, {"q": "strand"}) == {"needs_confirm": True}
    assert await regel(client, huis["h_eva"], album, {"q": "strand"}, share_new=False) == {
        "needs_confirm": False}  # fmt: skip
    assert await ziet_bram(client, huis) == ["berg.jpg"]
    assert await regel(client, huis["h_eva"], album, {"q": "strand"}, share_new=True) == {
        "needs_confirm": False}  # fmt: skip
    assert await ziet_bram(client, huis) == [
        "strand-3.jpg", "berg.jpg", "strand-2.jpg", "strand-1.jpg"]  # fmt: skip
    # Weer nee: wat al gedeeld is, blijft gedeeld; wat er nieuw bij komt niet
    await confirm(client, huis["h_eva"], album, False)
    async with maker(settings)() as session:
        eva = await session.get(User, huis["eva"])
        await photo(session, eva, "strand-4.jpg", BASE + timedelta(days=4))
        await session.commit()
    await rule_router.run(settings, {"album": str(album)})
    assert (await eigenaar(client, huis))[0] == ("strand-4.jpg", "rule", False)
    assert "strand-4.jpg" not in await ziet_bram(client, huis)
    assert await telling(client, huis) == (4, 1)


async def test_met_de_hand_toevoegen_deelt_een_rule_item(client, huis):
    album = huis["album"]
    await regel(client, huis["h_eva"], album, {"q": "strand"})
    r = await client.post(
        f"/api/v1/albums/{album}/items",
        json={"node_ids": [str(huis["een"])]},
        headers=huis["h_eva"],
    )
    assert r.json() == {"added": 1, "skipped": []}
    assert await ziet_bram(client, huis) == ["berg.jpg", "strand-1.jpg"]
    assert await telling(client, huis) == (1, 1)  # strand-1 is nu een item met de hand
    assert await ziet_de_link(client, huis) == ["berg.jpg", "strand-1.jpg"]


async def test_een_nieuwe_deling_of_link_zegt_hoeveel_items_prive_zijn(client, huis, settings):
    album, eva = huis["album"], huis["h_eva"]
    await regel(client, eva, album, {"q": "strand"})
    async with maker(settings)() as session:
        mia = await create_user(session, "mia@example.nl")
        mia_id = mia.id
        await session.commit()
    r = await client.post(
        f"/api/v1/files/{album}/shares",
        json={"user_id": str(mia_id), "permission": "read"},
        headers=eva,
    )
    assert r.status_code == 201, r.text
    assert r.json()["rule_items_private"] == 2
    link = await new_link(client, eva, album, permission="read")
    assert link["rule_items_private"] == 2
    # In de lijst van delingen staat het niet (alleen bij een nieuwe)
    r = await client.get(f"/api/v1/files/{album}/shares", headers=eva)
    assert len(r.json()) == 4
    assert {s["rule_items_private"] for s in r.json()} == {None}
    # Na ja: niets meer alleen voor de eigenaar
    await confirm(client, eva, album, True)
    assert (await new_link(client, eva, album, permission="read"))["rule_items_private"] is None
    # Een link op een map: nooit
    async with maker(settings)() as session:
        folder = (await create_folder(session, await session.get(User, huis["eva"]))).id
        await session.commit()
    assert (await new_link(client, eva, folder, permission="read"))["rule_items_private"] is None


async def test_alleen_de_eigenaar(client, huis):
    album, bram = huis["album"], huis["h_bram"]
    await regel(client, huis["h_eva"], album, {"q": "strand"})
    for r in (
        await client.put(
            f"/api/v1/albums/{album}/rule", json={"rule": {"q": "berg"}}, headers=bram
        ),  # fmt: skip
        await client.post(
            f"/api/v1/albums/{album}/rule/confirm", json={"share_new": True}, headers=bram
        ),  # fmt: skip
        await client.delete(f"/api/v1/albums/{album}/rule", headers=bram),
        await client.post(f"/api/v1/albums/{album}/refresh", headers=bram),
    ):
        assert r.status_code == 403 and code_of(r) == "owner_only", r.text
    assert await ziet_bram(client, huis) == ["berg.jpg"]


async def test_album_changed_alleen_naar_wie_het_verschil_ziet(client, huis, settings, monkeypatch):
    album = huis["album"]
    gemeld: list[tuple] = []
    monkeypatch.setattr(events, "publish",
                        lambda ids, type, data: gemeld.append((set(ids), type)))  # fmt: skip

    def album_events() -> list[tuple]:
        found = [g for g in gemeld if g[1].startswith("album.")]
        gemeld.clear()
        return found

    eva, beide = {huis["eva"]}, {huis["eva"], huis["bram"]}
    # Er komen alleen items voor Eva bij: alleen Eva
    await regel(client, huis["h_eva"], album, {"q": "strand"})
    assert album_events() == [(eva, "album.changed")]
    # Ja: nu ziet Bram ze ook
    await confirm(client, huis["h_eva"], album, True)
    assert album_events() == [(beide, "album.changed")]
    # Een andere regel haalt gedeelde items weg: ook Bram
    await regel(client, huis["h_eva"], album, {"q": "niets"})
    assert album_events() == [(beide, "album.changed")]
    # Niets veranderd (een verversing zonder verschil, of nee zonder iets te sluiten): niemand
    await rule_router.run(settings, {"album": str(album)})
    await confirm(client, huis["h_eva"], album, False)
    assert album_events() == []
    # De regel weg: alleen Eva (de items blijven)
    r = await client.delete(f"/api/v1/albums/{album}/rule", headers=huis["h_eva"])
    assert r.status_code == 204
    assert album_events() == [(eva, "album.changed")]


async def test_een_andere_regel_na_ja_vraagt_opnieuw(client, huis, settings):
    """Review golf 2, veilig V1: "ja" gold voor wat de regel van toen erbij zet. Een andere regel
    vindt andere foto's die er al zijn: die zijn alleen voor Eva tot ze weer "ja" zegt."""
    album, eva = huis["album"], huis["h_eva"]
    async with maker(settings)() as session:
        user = await session.get(User, huis["eva"])
        await photo(session, user, "kinderen-badkamer.jpg", BASE + timedelta(days=5))
        await session.commit()
    assert await regel(client, eva, album, {"q": "strand"}) == {"needs_confirm": True}
    await confirm(client, eva, album, True)
    assert await ziet_bram(client, huis) == ["berg.jpg", "strand-2.jpg", "strand-1.jpg"]
    # Een andere regel zonder antwoord: de vraag, en wat hij vindt is alleen voor Eva
    assert await regel(client, eva, album, {"q": "kinderen"}) == {"needs_confirm": True}
    assert await eigenaar(client, huis) == [
        ("kinderen-badkamer.jpg", "rule", False),
        ("berg.jpg", "hand", True),
    ]
    assert await ziet_bram(client, huis) == ["berg.jpg"]
    assert await ziet_de_link(client, huis) == ["berg.jpg"]
    rule = (await client.get(f"/api/v1/albums/{album}", headers=eva)).json()["rule"]
    assert (rule["share_new"], rule["items"], rule["private"]) == (False, 1, 1)
    # Dezelfde regel nog eens zonder antwoord: nog steeds de vraag
    assert await regel(client, eva, album, {"q": "kinderen"}) == {"needs_confirm": True}
    assert await ziet_bram(client, huis) == ["berg.jpg"]
    # Ja: nu wel, en dan geen vraag meer voor dezelfde regel
    await confirm(client, eva, album, True)
    assert await ziet_bram(client, huis) == ["kinderen-badkamer.jpg", "berg.jpg"]
    assert await ziet_de_link(client, huis) == ["kinderen-badkamer.jpg", "berg.jpg"]
    assert await regel(client, eva, album, {"q": "kinderen"}) == {"needs_confirm": False}
    # Een andere regel met het antwoord erbij: meteen gedeeld, zonder vraag
    assert await regel(client, eva, album, {"q": "strand"}, share_new=True) == {
        "needs_confirm": False}  # fmt: skip
    assert await ziet_bram(client, huis) == ["berg.jpg", "strand-2.jpg", "strand-1.jpg"]


async def test_een_nee_tijdens_het_verversen_telt(client, huis, settings, monkeypatch):
    """Review golf 2, veilig V2: de verversing leest share_new pas als ze schrijft. Een "nee"
    dat binnenkomt terwijl de regel nog zoekt, geldt voor wat deze verversing erbij zet."""
    album, eva = huis["album"], huis["h_eva"]
    await regel(client, eva, album, {"q": "strand"}, share_new=True)
    async with maker(settings)() as session:
        user = await session.get(User, huis["eva"])
        await photo(session, user, "strand-nieuw.jpg", BASE + timedelta(days=6))
        await session.commit()
    echt = R.matches

    async def tussendoor(*args, **kwargs):
        found = await echt(*args, **kwargs)
        assert await confirm(client, eva, album, False) == {"needs_confirm": False}
        return found

    monkeypatch.setattr(R, "matches", tussendoor)
    await rule_router.run(settings, {"album": str(album)})
    assert (await eigenaar(client, huis))[0] == ("strand-nieuw.jpg", "rule", False)
    assert await ziet_bram(client, huis) == ["berg.jpg", "strand-2.jpg", "strand-1.jpg"]
    assert "strand-nieuw.jpg" not in await ziet_de_link(client, huis)


async def test_een_nieuwe_regel_tijdens_het_verversen_wint(client, huis, settings, monkeypatch):
    """Zelfde venster als V2: zet Eva een andere regel terwijl de verversing nog met de oude
    zoekt, dan zet die verversing de treffers van de oude regel er niet meer bij."""
    album, eva = huis["album"], huis["h_eva"]
    await regel(client, eva, album, {"q": "strand"}, share_new=True)
    async with maker(settings)() as session:
        user = await session.get(User, huis["eva"])
        await photo(session, user, "strand-nieuw.jpg", BASE + timedelta(days=6))
        await session.commit()
    echt = R.matches

    async def tussendoor(*args, **kwargs):
        found = await echt(*args, **kwargs)
        monkeypatch.setattr(R, "matches", echt)
        await regel(client, eva, album, {"q": "berg"}, share_new=False)
        return found

    monkeypatch.setattr(R, "matches", tussendoor)
    await rule_router.run(settings, {"album": str(album)})
    assert await eigenaar(client, huis) == [("berg.jpg", "hand", True)]
    rule = (await client.get(f"/api/v1/albums/{album}", headers=eva)).json()["rule"]
    assert (rule["rule"], rule["count"], rule["items"]) == ({"q": "berg"}, 1, 0)
