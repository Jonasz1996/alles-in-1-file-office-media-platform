"""De API van bibliotheken en items (Keuze 4–9, M2): maken, nesting, wie wat ziet en mag, de
lijst met cursor, het item en de serie. De scan zelf staat in test_media_scanner.py."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app import jobs
from app.media import items as media_items
from app.media import library as media_library
from app.media import scanner
from app.models import Job, MediaItem, MediaLibrary, Node
from tests.test_files import mkdir, root
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_scanner import library, scan, upload, world  # noqa: F401
from tests.test_media_sessions import maker
from tests.test_shares import person, share_with

pytestmark = pytest.mark.anyio

URL = "/api/v1/media/libraries"


def code(r) -> str | None:
    detail = r.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


async def make(client, headers, root_id, kind="movies", **extra):
    return await client.post(URL, json={"root_id": root_id, "kind": kind, **extra}, headers=headers)


@pytest.fixture
async def people(world, client):  # noqa: F811
    """Jonas (beheerder, eigenaar van /Films met /Films/Sub), Bas (gebruiker) en Gijs (gast)."""
    settings, fake, jonas = world
    top = (await root(client, jonas))["folder"]["id"]
    films = await mkdir(client, jonas, top, "Films")
    sub = await mkdir(client, jonas, films, "Sub")
    bas, bas_id = await person(client, jonas, "bas@example.nl")
    gijs, gijs_id = await person(client, jonas, "gijs@example.nl", role="guest")
    return {
        "settings": settings,
        "jonas": jonas,
        "top": top,
        "films": films,
        "sub": sub,
        "bas": bas,
        "bas_id": bas_id,
        "gijs": gijs,
        "gijs_id": gijs_id,
    }


async def test_maken_zet_de_eerste_scan_klaar(people, client):
    p = people
    r = await make(client, p["jonas"], p["films"], name="Mijn films")
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["name"], body["kind"], body["mine"], body["scan_state"]) == (
        "Mijn films", "movies", True, "queued",
    )  # fmt: skip
    assert body["item_count"] == 0 and body["online"] is False
    async with maker(p["settings"])() as session:
        job = await session.scalar(select(Job).where(Job.key == f"media.scan:{body['id']}"))
    assert job is not None and job.status == "queued" and job.payload["full"] is True
    assert jobs.lane_of(scanner.JOB) == "media_scan"
    # Zonder naam: de naam van de map
    other = await mkdir(client, p["jonas"], p["top"], "Series")
    r = await make(client, p["jonas"], other, kind="shows")
    assert r.status_code == 201 and r.json()["name"] == "Series"


async def test_geen_nesting_boven_op_of_onder(people, client):
    p = people
    first = (await make(client, p["jonas"], p["sub"])).json()["id"]
    # Erboven
    r = await make(client, p["jonas"], p["films"])
    assert r.status_code == 409 and code(r) == "media_library_nested"
    assert r.json()["detail"]["params"]["library_id"] == first
    # Erop
    r = await make(client, p["jonas"], p["sub"], kind="videos")
    assert r.status_code == 409 and code(r) == "media_library_nested"
    # Eronder
    deeper = await mkdir(client, p["jonas"], p["sub"], "Dieper")
    r = await make(client, p["jonas"], deeper)
    assert r.status_code == 409 and r.json()["detail"]["params"]["library_id"] == first
    # Ernaast mag
    other = await mkdir(client, p["jonas"], p["films"], "Ander")
    assert (await make(client, p["jonas"], other)).status_code == 201


async def test_twee_tegelijk_in_elkaar_geeft_een_409(people, client):
    p = people
    a, b = await asyncio.gather(
        make(client, p["jonas"], p["films"]), make(client, p["jonas"], p["sub"])
    )
    assert sorted([a.status_code, b.status_code]) == [201, 409]


async def test_geen_map_of_in_de_prullenbak(people, client):
    p = people
    node = await upload(client, p["jonas"], p["films"], "los.mkv")
    r = await make(client, p["jonas"], node)
    assert r.status_code == 422 and code(r) == "media_library_not_folder"
    assert (await client.delete(f"/api/v1/files/{p['films']}", headers=p["jonas"])).status_code
    for folder in (p["films"], p["sub"]):  # zelf in de prullenbak, of een map erboven
        r = await make(client, p["jonas"], folder)
        assert r.status_code == 422 and code(r) == "media_library_not_folder"
    r = await make(client, p["jonas"], str(uuid.uuid4()))
    assert r.status_code == 404


async def test_alleen_de_eigenaar_van_de_map(people, client):
    p = people
    # Bas ziet de map niet: 404, zoals overal
    r = await make(client, p["bas"], p["films"])
    assert r.status_code == 404
    # Met schrijven gedeeld: hij ziet hem, maar hij is niet de eigenaar
    await share_with(client, p["jonas"], p["films"], user_id=p["bas_id"], permission="write")
    r = await make(client, p["bas"], p["films"])
    assert r.status_code == 403 and code(r) == "media_not_owner"
    # Een gast maakt nooit een bibliotheek, ook niet van een gedeelde map
    await share_with(client, p["jonas"], p["films"], user_id=p["gijs_id"], permission="read")
    r = await make(client, p["gijs"], p["films"])
    assert r.status_code == 403 and code(r) == "media_not_owner"


async def test_zichtbaar_via_een_deling_van_de_map_maar_niet_van_een_submap(people, client):
    p = people
    lib = (await make(client, p["jonas"], p["films"])).json()["id"]
    await share_with(client, p["jonas"], p["sub"], user_id=p["bas_id"], permission="read")
    assert (await client.get(URL, headers=p["bas"])).json() == []
    r = await client.get(f"{URL}/{lib}/items", headers=p["bas"])
    assert r.status_code == 404 and code(r) == "not_found"
    await share_with(client, p["jonas"], p["films"], user_id=p["bas_id"], permission="read")
    seen = (await client.get(URL, headers=p["bas"])).json()
    assert [(x["id"], x["mine"]) for x in seen] == [(lib, False)]
    assert (await client.get(f"{URL}/{lib}/items", headers=p["bas"])).status_code == 200
    # Een gast ziet een gedeelde bibliotheek ook
    await share_with(client, p["jonas"], p["films"], user_id=p["gijs_id"], permission="read")
    assert [x["id"] for x in (await client.get(URL, headers=p["gijs"])).json()] == [lib]


async def test_een_deling_van_een_map_erboven_toont_hem_ook(people, client):
    p = people
    lib = (await make(client, p["jonas"], p["sub"])).json()["id"]
    await share_with(client, p["jonas"], p["films"], user_id=p["bas_id"], permission="read")
    assert [x["id"] for x in (await client.get(URL, headers=p["bas"])).json()] == [lib]


async def test_weghalen_laat_de_bestanden_staan(people, client):
    p = people
    lib = (await make(client, p["jonas"], p["films"])).json()["id"]
    node = await upload(client, p["jonas"], p["films"], "Film (2010).mkv")
    await scan(p["settings"])
    async with maker(p["settings"])() as session:
        assert await session.get(MediaItem, uuid.UUID(node)) is not None
    r = await client.delete(f"{URL}/{lib}", headers=p["jonas"])
    assert r.status_code == 204
    async with maker(p["settings"])() as session:
        assert await session.get(MediaLibrary, uuid.UUID(lib)) is None
        assert await session.get(MediaItem, uuid.UUID(node)) is None
        assert await session.get(Node, uuid.UUID(node)) is not None
        assert await session.get(Node, uuid.UUID(p["films"])) is not None
    assert (await client.get(f"/api/v1/files/{node}", headers=p["jonas"])).status_code == 200
    # En de map kan weer een bibliotheek worden
    assert (await make(client, p["jonas"], p["films"])).status_code == 201


async def test_hooguit_twintig(people, client):
    p = people
    for i in range(media_library.MAX_LIBRARIES):
        folder = await mkdir(client, p["jonas"], p["top"], f"Map {i}")
        assert (await make(client, p["jonas"], folder)).status_code == 201
    folder = await mkdir(client, p["jonas"], p["top"], "Nog een")
    r = await make(client, p["jonas"], folder)
    assert r.status_code == 422 and code(r) == "media_library_limit"
    assert r.json()["detail"]["params"]["max"] == media_library.MAX_LIBRARIES


async def test_wijzigen_en_weghalen_alleen_de_eigenaar(people, client):
    p = people
    lib = (await make(client, p["jonas"], p["films"])).json()["id"]
    await share_with(client, p["jonas"], p["films"], user_id=p["bas_id"], permission="write")
    r = await client.patch(f"{URL}/{lib}", json={"name": "Van Bas"}, headers=p["bas"])
    assert r.status_code == 403 and code(r) == "media_not_owner"
    r = await client.delete(f"{URL}/{lib}", headers=p["bas"])
    assert r.status_code == 403 and code(r) == "media_not_owner"
    r = await client.patch(
        f"{URL}/{lib}", json={"name": "Nieuw", "online": True, "language": "nl"}, headers=p["jonas"]
    )
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["online"]) == ("Nieuw", True)
    async with maker(p["settings"])() as session:
        assert (await session.get(MediaLibrary, uuid.UUID(lib))).language == "nl"
    r = await client.patch(f"{URL}/{lib}", json={"language": "Nederlands"}, headers=p["jonas"])
    assert r.status_code == 422
    # Wie hem niet ziet: 404
    other, _ = await person(client, p["jonas"], "els@example.nl")
    assert (
        await client.patch(f"{URL}/{lib}", json={"name": "x"}, headers=other)
    ).status_code == 404
    assert (await client.delete(f"{URL}/{uuid.uuid4()}", headers=p["jonas"])).status_code == 404


async def test_opnieuw_scannen_wie_mag_schrijven(people, client):
    p = people
    lib = (await make(client, p["jonas"], p["films"])).json()["id"]
    await jobs.run_pending(p["settings"], lane="media_scan")
    await share_with(client, p["jonas"], p["films"], user_id=p["bas_id"], permission="read")
    r = await client.post(f"{URL}/{lib}/scan", headers=p["bas"])
    assert r.status_code == 403 and code(r) == "media_not_owner"
    await share_with(client, p["jonas"], p["films"], user_id=p["gijs_id"], permission="write")
    assert (await client.post(f"{URL}/{lib}/scan", headers=p["gijs"])).status_code == 202
    r = await client.post(f"{URL}/{lib}/scan", headers=p["jonas"])
    assert r.status_code == 202 and r.json() == {"library_id": lib}
    async with maker(p["settings"])() as session:
        queued = await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.key == f"media.scan:{lib}", Job.status == "queued")
        )
    assert queued == 1  # twee keer gevraagd, één ronde


async def test_items_van_een_onzichtbare_bibliotheek_zijn_404(people, client):
    p = people
    lib = (await make(client, p["jonas"], p["films"])).json()["id"]
    node = await upload(client, p["jonas"], p["films"], "Film (2010).mkv")
    await scan(p["settings"])
    assert (await client.get(f"/api/v1/media/items/{node}", headers=p["jonas"])).status_code == 200
    for url in (
        f"{URL}/{lib}/items",
        f"/api/v1/media/items/{node}",
        f"/api/v1/media/items/{uuid.uuid4()}",
        f"/api/v1/media/shows/{uuid.uuid4()}",
        f"{URL}/{uuid.uuid4()}/items",
    ):
        r = await client.get(url, headers=p["bas"])
        assert r.status_code == 404 and code(r) == "not_found", url


async def test_de_lijst_met_cursor_en_sorteringen(people, client):
    p = people
    lib = (await make(client, p["jonas"], p["films"])).json()["id"]
    names = ["Zebra (1990).mkv", "Appel (2010).mkv", "Midden (2000).mkv", "The Beer (1980).mkv"]
    nodes = {}
    for name in names:
        nodes[name] = await upload(client, p["jonas"], p["films"], name)
    await scan(p["settings"])

    async def walk(sort: str) -> list[str]:
        out: list[str] = []
        cursor = None
        for _ in range(10):
            params = {"sort": sort, "limit": 3, **({"cursor": cursor} if cursor else {})}
            r = await client.get(f"{URL}/{lib}/items", params=params, headers=p["jonas"])
            assert r.status_code == 200, r.text
            page = r.json()
            out += [c["title"] for c in page["items"]]
            cursor = page.get("next")
            if not cursor:
                return out
        raise AssertionError("de cursor houdt niet op")

    assert await walk("title") == ["Appel", "The Beer", "Midden", "Zebra"]
    assert await walk("year") == ["Appel", "Midden", "Zebra", "The Beer"]
    assert sorted(await walk("added")) == sorted(["Appel", "The Beer", "Midden", "Zebra"])
    r = await client.get(f"{URL}/{lib}/items", params={"cursor": "kapot!"}, headers=p["jonas"])
    assert r.status_code == 422 and code(r) == "invalid_cursor"
    r = await client.get(f"{URL}/{lib}/items", params={"type": "show"}, headers=p["jonas"])
    assert r.status_code == 200 and r.json()["items"] == []


def test_een_cursor_die_niet_past_is_ongeldig():
    good = media_items.encode_cursor(["a", str(uuid.uuid4())])
    assert media_items.decode_cursor(good, "su")[0] == "a"
    for bad in (good, media_items.encode_cursor(["a"]), media_items.encode_cursor({"a": 1})):
        with pytest.raises(Exception) as caught:
            media_items.decode_cursor(bad, "iu")
        assert getattr(caught.value, "status_code", None) == 422


async def test_home_met_net_toegevoegd(people, client):
    p = people
    lib = (await make(client, p["jonas"], p["films"])).json()["id"]
    await upload(client, p["jonas"], p["films"], "Een (2001).mkv")
    await upload(client, p["jonas"], p["films"], "Twee (2002).mkv")
    await scan(p["settings"])
    r = await client.get("/api/v1/media/home", headers=p["jonas"])
    assert r.status_code == 200, r.text
    home = r.json()
    assert [(x["id"], x["item_count"]) for x in home["libraries"]] == [(lib, 2)]
    assert sorted(c["title"] for c in home["recent"]) == ["Een", "Twee"]
    # Bas ziet niets
    r = await client.get("/api/v1/media/home", headers=p["bas"])
    assert r.json()["libraries"] == [] and r.json()["recent"] == []


async def test_het_item_met_wat_de_analyse_al_weet(people, client):
    p = people
    await make(client, p["jonas"], p["films"])
    node = await upload(client, p["jonas"], p["films"], "Film (2010).mkv")
    await scan(p["settings"])
    r = await client.get(f"/api/v1/media/items/{node}", headers=p["jonas"])
    assert r.status_code == 200, r.text
    item = r.json()
    assert (item["title"], item["year"], item["kind"], item["can_edit"]) == (
        "Film", 2010, "movie", True,
    )  # fmt: skip
    assert item["analysis"]["probe"] == "pending"  # de analyse staat klaar
    await share_with(client, p["jonas"], p["films"], user_id=p["bas_id"], permission="read")
    r = await client.get(f"/api/v1/media/items/{node}", headers=p["bas"])
    assert r.status_code == 200 and r.json()["can_edit"] is False


async def test_de_prullenbak_telt_meteen_ook_voor_wie_via_een_deling_kijkt(people, client):
    """Review golf 3, S4 (a): Jonas zet een film in de prullenbak. Vóór de volgende scan kreeg
    Bas (lezen via de deling) het item nog met titel en al, terwijl /files hem al 404 gaf. Nu
    kijkt elke vraag zelf: weg uit het item, de lijst en Net toegevoegd, voor iedereen."""
    p = people
    lib = (await make(client, p["jonas"], p["films"])).json()["id"]
    await share_with(client, p["jonas"], p["films"], user_id=p["bas_id"], permission="read")
    node = await upload(client, p["jonas"], p["films"], "Weg (2001).mkv")
    await upload(client, p["jonas"], p["films"], "Blijft (2002).mkv")
    await scan(p["settings"])
    assert (await client.get(f"/api/v1/media/items/{node}", headers=p["bas"])).status_code == 200
    r = await client.delete(f"/api/v1/files/{node}", headers=p["jonas"])
    assert r.status_code == 204
    # Geen scan: de rij staat nog op present
    async with maker(p["settings"])() as session:
        assert (await session.get(MediaItem, uuid.UUID(node))).present is True
    for who in ("bas", "jonas"):
        r = await client.get(f"/api/v1/media/items/{node}", headers=p[who])
        assert r.status_code == 404 and code(r) == "not_found", who
        r = await client.get(f"{URL}/{lib}/items", headers=p[who])
        assert [c["title"] for c in r.json()["items"]] == ["Blijft"], who
        home = (await client.get("/api/v1/media/home", headers=p[who])).json()
        assert [c["title"] for c in home["recent"]] == ["Blijft"], who
    assert (await client.get(f"/api/v1/files/{node}", headers=p["bas"])).status_code == 404


async def test_een_poster_die_wegging_geeft_geen_adres_meer(people, client):
    """S4 (b): Jonas verhuist poster.jpg uit de gedeelde filmmap naar een eigen map. De scanner
    zag dat niet (de poster hoort bij geen bibliotheek meer), en Bas kreeg nog een ondertekend
    adres van de preview van een bestand waar /files hem 404 op gaf. Nu telt een plaatje alleen
    als het nog in de bibliotheek ligt."""
    from app.models import Blob
    from tests.test_office import jpeg

    p = people
    await make(client, p["jonas"], p["films"])
    await share_with(client, p["jonas"], p["films"], user_id=p["bas_id"], permission="read")
    folder = await mkdir(client, p["jonas"], p["films"], "Film (2010)")
    node = await upload(client, p["jonas"], folder, "Film (2010).mkv")
    poster = await upload(client, p["jonas"], folder, "poster.jpg", jpeg((90, 120)))
    backdrop = await upload(client, p["jonas"], folder, "fanart.jpg", jpeg((160, 90)))
    await scan(p["settings"])
    async with maker(p["settings"])() as session:
        item = await session.get(MediaItem, uuid.UUID(node))
        assert item.art == {"poster": {"node": poster}, "backdrop": {"node": backdrop}}
        shas = {}
        for art in (poster, backdrop):
            sha = (await session.get(Node, uuid.UUID(art))).blob_id
            blob = await session.get(Blob, sha)
            blob.preview, blob.preview_meta = "ready", {"sizes": [256, 1024]}
            shas[art] = sha
        await session.commit()
    r = await client.get(f"/api/v1/media/items/{node}", headers=p["bas"])
    assert shas[poster] in r.json()["poster"] and shas[backdrop] in r.json()["backdrop"]
    eigen = await mkdir(client, p["jonas"], p["top"], "Eigen")
    for art in (poster, backdrop):
        r = await client.patch(
            f"/api/v1/files/{art}", json={"parent_id": eigen}, headers=p["jonas"]
        )
        assert r.status_code == 200, r.text
    await scan(p["settings"])  # de scanner ziet het niet: de art blijft in de rij staan
    async with maker(p["settings"])() as session:
        assert (await session.get(MediaItem, uuid.UUID(node))).art["poster"] == {"node": poster}
    assert (await client.get(f"/api/v1/files/{poster}", headers=p["bas"])).status_code == 404
    for who in ("bas", "jonas"):
        item = (await client.get(f"/api/v1/media/items/{node}", headers=p[who])).json()
        assert shas[poster] not in (item["poster"] or ""), who
        assert item["backdrop"] is None, who
        lib_items = (await client.get(f"{URL}/{item['library_id']}/items", headers=p[who])).json()
        assert all(shas[poster] not in (c["poster"] or "") for c in lib_items["items"]), who
