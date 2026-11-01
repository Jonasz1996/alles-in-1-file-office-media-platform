"""Koppelen en corrigeren via de API (routers/media_match.py, Keuze 42–44, M8).

- Uit (geen sleutel, of de bibliotheek niet online): 409 `media_provider_off`, niets naar buiten.
- Kandidaten met een poster via ons eigen, ondertekende adres (art-tmp); een ander adres: 404.
- Wie: 404 als je het item niet ziet, 403 `read_only` met alleen lezen.
- Koppelen (manual, vergrendelde velden blijven), ontkoppelen (terug naar de naam, niet meteen
  weer automatisch), bewerken met vastzetten en vrijgeven, een serie koppelen, Nakijken.
- De fouten van de provider als codes uit de catalogus; de sleutel nooit in een antwoord.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app import events, jobs
from app.media import lookup, names
from app.media.providers import art, secrets
from app.models import Job, MediaItem, MediaLibrary, MediaShow
from tests.media.fake_providers import kit
from tests.test_files import mkdir
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_lookup import (
    LANTERNS,
    NEPHAVEN,
    STORM,
    films,
    make_library,
    run,
    save_key,
)
from tests.test_media_lookup import net as net  # noqa: F401
from tests.test_media_scanner import items, scan, upload
from tests.test_media_scanner import world as world  # noqa: F401
from tests.test_media_sessions import maker
from tests.test_shares import person, share_with

pytestmark = pytest.mark.anyio

URL = "/api/v1/media"


def code(r) -> str | None:
    detail = r.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


async def node_of(net, library_id: str, name: str) -> uuid.UUID:
    return (await items(net.settings, library_id))[name].node_id


async def row(net, node_id: uuid.UUID) -> MediaItem:
    async with maker(net.settings)() as session:
        return await session.get(MediaItem, node_id)


def no_key(r) -> None:
    assert kit.KEY not in r.text


# --- uit: niets naar buiten ----------------------------------------------------------------------


async def test_zonder_sleutel_409_en_niets_naar_buiten(net, client):
    library_id = await films(client, net, [NEPHAVEN])
    node = await node_of(net, library_id, NEPHAVEN)
    r = await client.get(f"{URL}/items/{node}/candidates", headers=net.headers)
    assert r.status_code == 409 and code(r) == "media_provider_off"
    r = await client.post(
        f"{URL}/items/{node}/match", json={"provider": "tmdb", "id": "movie:900001"},
        headers=net.headers,
    )  # fmt: skip
    assert r.status_code == 409 and code(r) == "media_provider_off"
    assert net.fake.log == []


async def test_een_bibliotheek_zonder_online_409(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN], online=False)
    node = await node_of(net, library_id, NEPHAVEN)
    r = await client.get(f"{URL}/items/{node}/candidates", headers=net.headers)
    assert r.status_code == 409 and code(r) == "media_provider_off"
    assert net.fake.log == []


# --- kandidaten ----------------------------------------------------------------------------------


async def test_kandidaten_met_een_poster_via_ons_adres(net, client):
    await save_key(net)
    library_id = await films(client, net, [STORM])
    node = await node_of(net, library_id, STORM)
    r = await client.get(f"{URL}/items/{node}/candidates", headers=net.headers)
    assert r.status_code == 200, r.text
    no_key(r)
    found = r.json()
    assert 2 <= len(found) <= 10
    assert {c["provider"] for c in found} == {"tmdb"}
    assert all(c["id"].startswith("movie:") for c in found)
    assert all(c["title"] == "The Fake Storm" for c in found[:2])
    with_poster = [c for c in found if c["poster"]]
    assert with_poster
    for c in with_poster:
        assert c["poster"].startswith("/api/v1/media/art/") and "&sig=" in c["poster"]
        assert "image.tmdb.org" not in c["poster"]
    poster = with_poster[0]["poster"]
    r = await client.get(poster)
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert "max-age=" in r.headers["cache-control"]
    # In de tijdelijke map, niet bij de art van items
    sha = poster.split("/art/")[1].split("/")[0]
    assert (net.settings.media_dir / "art-tmp" / sha / "w256.webp").is_file()
    # Een ander adres: 404
    for bad in (
        poster[:-1] + ("0" if poster[-1] != "0" else "1"),  # de handtekening
        poster.replace("w256", "w512"),  # een andere maat
        poster.replace("exp=", "exp=1"),  # een andere tijd
        poster.split("?")[0],  # zonder
    ):
        assert (await client.get(bad)).status_code == 404, bad
    # Zoeken met een eigen titel en jaar
    r = await client.get(
        f"{URL}/items/{node}/candidates", params={"q": "Copper Sky", "year": 2005},
        headers=net.headers,
    )  # fmt: skip
    assert r.json()[0]["id"] == "movie:900012"


async def test_kandidaten_voor_een_aflevering_zoeken_de_serie(net, client):
    await save_key(net)
    library_id, folder = await make_library(client, net.headers, "Series", "shows")
    show = await mkdir(client, net.headers, folder, "The Fake Coast")
    await upload(client, net.headers, show, "The Fake Coast S01E01.mkv")
    await scan(net.settings)
    node = await node_of(net, library_id, "The Fake Coast S01E01.mkv")
    r = await client.get(f"{URL}/items/{node}/candidates", headers=net.headers)
    assert r.status_code == 200, r.text
    assert r.json()[0]["id"] == "tv:900101"
    assert any(e["inner"] == "/3/search/tv" for e in net.fake.log)


# --- wie -----------------------------------------------------------------------------------------


async def test_alleen_lezen_403_en_niet_zien_404(net, client):
    await save_key(net)
    library_id, folder = await make_library(client, net.headers)
    await upload(client, net.headers, folder, NEPHAVEN)
    await scan(net.settings)
    node = await node_of(net, library_id, NEPHAVEN)
    bas, bas_id = await person(client, net.headers, "bas@example.nl")
    gijs, _ = await person(client, net.headers, "gijs@example.nl")
    await share_with(client, net.headers, folder, user_id=bas_id, permission="read")
    body = {"provider": "tmdb", "id": "movie:900001"}
    calls = [
        ("GET", f"{URL}/items/{node}/candidates", None),
        ("POST", f"{URL}/items/{node}/match", body),
        ("DELETE", f"{URL}/items/{node}/match", None),
        ("PATCH", f"{URL}/items/{node}", {"title": "Anders"}),
    ]
    for method, url, json in calls:
        r = await client.request(method, url, json=json, headers=bas)
        assert r.status_code == 403 and code(r) == "read_only", (method, url, r.text)
        r = await client.request(method, url, json=json, headers=gijs)
        assert r.status_code == 404, (method, url, r.text)
    assert net.fake.log == []
    # Nakijken: leeg voor wie alleen leest
    r = await client.get(f"{URL}/libraries/{library_id}/review", headers=bas)
    assert r.status_code == 200 and r.json()["items"] == []
    assert (await row(net, node)).title == "De Nephaven"


# --- koppelen ------------------------------------------------------------------------------------


async def test_koppelen_met_de_hand(net, client, monkeypatch):
    await save_key(net)
    library_id = await films(client, net, [STORM])
    node = await node_of(net, library_id, STORM)
    # Een vastgezet veld blijft staan
    body = {"overview": "Mijn tekst"}
    r = await client.patch(f"{URL}/items/{node}", json=body, headers=net.headers)
    assert r.status_code == 200, r.text
    assert r.json()["locked"] == ["overview"]
    sent = []
    monkeypatch.setattr(events, "publish", lambda users, kind, data: sent.append((kind, data)))
    r = await client.post(
        f"{URL}/items/{node}/match", json={"provider": "tmdb", "id": "movie:900019"},
        headers=net.headers,
    )  # fmt: skip
    assert r.status_code == 200, r.text
    no_key(r)
    item = r.json()
    assert item["match_state"] == "manual"
    assert item["overview"] == "Mijn tekst"
    assert item["locked"] == ["overview"]
    stored = await row(net, node)
    assert stored.ids["tmdb"] in (900019, "900019") and stored.source == "provider"
    assert ("media.item", {"node_id": str(node), "library_id": library_id}) in [
        (k, {"node_id": d["node_id"], "library_id": d["library_id"]}) for k, d in sent
    ]
    # De plaatjes komen via de taak media.art
    async with maker(net.settings)() as session:
        queued = (await session.scalars(select(Job).where(Job.type == lookup.ART_JOB))).all()
    assert {j.payload["role"] for j in queued} == {"poster", "backdrop"}
    await jobs.run_pending(net.settings, lane="media_net")
    r = await client.get(f"{URL}/items/{node}", headers=net.headers)
    assert r.json()["poster"].startswith("/api/v1/media/art/")
    # Het automatisch opzoeken laat hem met rust
    await run(net.settings)
    assert (await row(net, node)).match_state == "manual"


async def test_koppelen_ongeldig(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    node = await node_of(net, library_id, NEPHAVEN)
    for body in (
        {"provider": "musicbrainz", "id": "movie:900001"},
        {"provider": "tmdb", "id": "tv:900101"},
        {"provider": "tmdb", "id": "movie:abc"},
        {"provider": "tmdb", "id": "900001"},
    ):
        r = await client.post(f"{URL}/items/{node}/match", json=body, headers=net.headers)
        assert r.status_code == 422, (body, r.text)
    r = await client.post(
        f"{URL}/items/{node}/match", json={"provider": "tmdb", "id": "movie:123"},
        headers=net.headers,
    )  # fmt: skip
    assert r.status_code == 404 and code(r) == "not_found"
    assert (await row(net, node)).match_state == "local"
    r = await client.post(
        f"{URL}/items/{uuid.uuid4()}/match", json={"provider": "tmdb", "id": "movie:900001"},
        headers=net.headers,
    )  # fmt: skip
    assert r.status_code == 404


async def test_fouten_van_de_provider(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    node = await node_of(net, library_id, NEPHAVEN)
    url = f"{URL}/items/{node}/candidates"
    net.fake.fault("429", "/3/search/movie", host="tmdb", retry_after=20, times=None)
    r = await client.get(url, headers=net.headers)
    assert r.status_code == 503 and code(r) == "media_provider_rate"
    assert int(r.headers["retry-after"]) >= 1
    net.fake.clear_faults()
    net.clock.now += 60
    net.fake.fault("500", "/3/search/movie", host="tmdb", times=1)
    r = await client.get(url, headers=net.headers)
    assert r.status_code == 503 and code(r) == "media_provider_unavailable"
    no_key(r)
    # Een ingetrokken sleutel: 422 en dicht (daarna 409 zonder verkeer)
    net.fake.config.tmdb_key = "ingetrokken-" + "y" * 24
    r = await client.get(url, headers=net.headers)
    assert r.status_code == 422 and code(r) == "media_provider_auth"
    no_key(r)
    async with maker(net.settings)() as session:
        assert (await secrets.status(session, net.settings))["ok"] is False
    net.fake.log.clear()
    r = await client.get(url, headers=net.headers)
    assert r.status_code == 409 and code(r) == "media_provider_off"
    assert net.fake.log == []


# --- ontkoppelen ---------------------------------------------------------------------------------


async def test_ontkoppelen_terug_naar_de_naam(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN, LANTERNS])
    await run(net.settings)
    node = await node_of(net, library_id, NEPHAVEN)
    before = await row(net, node)
    assert before.match_state == "matched" and "url" in before.art["poster"]
    r = await client.delete(f"{URL}/items/{node}/match", headers=net.headers)
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["match_state"] == "local"
    assert (item["title"], item["year"]) == ("De Nephaven", 2004)
    assert item["poster"] is None or "/art/" not in item["poster"]
    assert item["locked"] == []  # de verborgen vergrendeling telt niet mee
    stored = await row(net, node)
    assert stored.art == {} and stored.source == "name"
    assert lookup.HIDDEN_LOCK in stored.locked
    assert "tmdb" not in (stored.ids or {})
    # De scanner kijkt nog eens (NFO), en het opzoeken koppelt hem niet meteen weer
    await scan(net.settings)
    await run(net.settings)
    assert (await row(net, node)).match_state == "local"
    # Met de hand weer koppelen mag wel, en haalt de verborgen vergrendeling weg
    r = await client.post(
        f"{URL}/items/{node}/match", json={"provider": "tmdb", "id": "movie:900001"},
        headers=net.headers,
    )  # fmt: skip
    assert r.json()["match_state"] == "manual"
    assert lookup.HIDDEN_LOCK not in (await row(net, node)).locked


# --- bewerken ------------------------------------------------------------------------------------


async def test_bewerken_vastzetten_en_vrijgeven(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    await run(net.settings)
    node = await node_of(net, library_id, NEPHAVEN)
    # Een lege body: niets veranderd, wel `locked` voor het venster
    r = await client.patch(f"{URL}/items/{node}", json={}, headers=net.headers)
    assert r.status_code == 200 and r.json()["locked"] == []
    assert r.json()["sort_title"]
    r = await client.patch(
        f"{URL}/items/{node}", json={"title": "Mijn Haven", "year": 2003}, headers=net.headers
    )
    assert r.status_code == 200, r.text
    item = r.json()
    assert (item["title"], item["year"]) == ("Mijn Haven", 2003)
    assert sorted(item["locked"]) == ["title", "year"]
    assert item["sort_title"] == "mijn haven"
    # Een nieuwe ronde van de provider laat ze staan
    await lookup.write_item(
        net.settings, uuid.UUID(library_id), node,
        lookup.match.Decision("matched", None, 1.0),
        await lookup.tmdb.provider().details("movie", "movie:900001", "nl-NL"),
        manual=True,
    )  # fmt: skip
    assert (await row(net, node)).title == "Mijn Haven"
    # Vrijgeven: de titel van de provider komt terug
    r = await client.patch(f"{URL}/items/{node}", json={"unlock": ["title"]}, headers=net.headers)
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "De Nephaven" and r.json()["locked"] == ["year"]
    assert r.json()["year"] == 2003
    # Een eigen sorteertitel, en leeg: weer automatisch
    r = await client.patch(f"{URL}/items/{node}", json={"sort_title": "aaa"}, headers=net.headers)
    assert r.json()["sort_title"] == "aaa" and "sort_title" in r.json()["locked"]
    r = await client.patch(f"{URL}/items/{node}", json={"sort_title": ""}, headers=net.headers)
    assert r.json()["sort_title"] == names.sort_title("De Nephaven")
    assert "sort_title" not in r.json()["locked"]
    # Ongeldig
    for body in (
        {"unlock": ["ids"]},
        {"title": "x", "unlock": ["title"]},
        {"season": 2},  # een film heeft geen seizoen
        {"title": "   "},
    ):
        r = await client.patch(f"{URL}/items/{node}", json=body, headers=net.headers)
        assert r.status_code == 422, (body, r.text)


async def test_een_aflevering_hernummeren(net, client):
    await save_key(net)
    library_id, folder = await make_library(client, net.headers, "Series", "shows")
    show = await mkdir(client, net.headers, folder, "The Fake Coast (2010)")
    await upload(client, net.headers, show, "The Fake Coast S01E01.mkv")
    await upload(client, net.headers, show, "The Fake Coast S01E07.mkv")
    await scan(net.settings)
    await run(net.settings)
    node = await node_of(net, library_id, "The Fake Coast S01E07.mkv")
    assert (await row(net, node)).match_state == "review"
    r = await client.patch(f"{URL}/items/{node}", json={"episode": 3}, headers=net.headers)
    assert r.status_code == 200, r.text
    assert r.json()["episode"] == 3 and r.json()["locked"] == ["episode"]
    assert (await row(net, node)).match_state == "local"
    await run(net.settings)
    stored = await row(net, node)
    assert (stored.match_state, stored.title, stored.episode) == ("matched", "Aflevering 3", 3)


# --- een serie koppelen --------------------------------------------------------------------------


async def test_een_serie_koppelen(net, client):
    await save_key(net)
    library_id, folder = await make_library(client, net.headers, "Series", "shows")
    show = await mkdir(client, net.headers, folder, "Lichten aan de Haven")
    await upload(client, net.headers, show, "Lichten aan de Haven S01E01.mkv")
    await scan(net.settings)
    await run(net.settings)
    async with maker(net.settings)() as session:
        [found] = (await session.scalars(select(MediaShow))).all()
    assert found.match_state in ("review", "local")
    r = await client.post(
        f"{URL}/shows/{found.id}/match", json={"provider": "tmdb", "id": "tv:900102"},
        headers=net.headers,
    )  # fmt: skip
    assert r.status_code == 200, r.text
    detail = r.json()["show"]
    assert detail["title"] == "Havenlichten" and detail["match_state"] == "manual"
    # De afleveringen: de taak van de bibliotheek
    await jobs.run_pending(net.settings, lane="media_net")
    episode = (await items(net.settings, library_id))["Lichten aan de Haven S01E01.mkv"]
    assert episode.match_state == "matched"
    r = await client.post(
        f"{URL}/shows/{found.id}/match", json={"provider": "tmdb", "id": "movie:900001"},
        headers=net.headers,
    )  # fmt: skip
    assert r.status_code == 422
    r = await client.post(
        f"{URL}/shows/{uuid.uuid4()}/match", json={"provider": "tmdb", "id": "tv:900102"},
        headers=net.headers,
    )  # fmt: skip
    assert r.status_code == 404


# --- Nakijken ------------------------------------------------------------------------------------


async def test_nakijken_met_een_cursor(net, client):
    await save_key(net)
    town = "Glass.Town.2009.1080p.BluRay.x264-NEP.mkv"
    library_id = await films(client, net, [STORM, NEPHAVEN, town])
    await run(net.settings)
    r = await client.get(
        f"{URL}/libraries/{library_id}/review", params={"limit": 1}, headers=net.headers
    )
    assert r.status_code == 200, r.text
    first = r.json()
    assert len(first["items"]) == 1 and first["next"]
    r = await client.get(
        f"{URL}/libraries/{library_id}/review", params={"limit": 1, "cursor": first["next"]},
        headers=net.headers,
    )  # fmt: skip
    second = r.json()
    assert len(second["items"]) == 1 and second["next"] is None
    titles = {first["items"][0]["title"], second["items"][0]["title"]}
    assert titles == {"The Fake Storm", "Glass Town"}
    r = await client.get(f"{URL}/libraries/{uuid.uuid4()}/review", headers=net.headers)
    assert r.status_code == 404


async def test_art_zonder_media_of_met_een_kapot_adres(net, client):
    key = art.url_key(net.settings)
    sha = "ab" * 32
    r = await client.get(art.url(key, sha, "w256"))
    assert r.status_code == 404  # goed ondertekend, maar er staat niets
    r = await client.get(f"{URL}/art/{sha}/w999.webp?sig=x")
    assert r.status_code == 404
    r = await client.get(f"{URL}/art/..%2F..%2Fetc/w256.webp?sig=x")
    assert r.status_code in (404, 422)


async def test_de_bibliotheek_weg_tijdens_koppelen(net, client):
    """Een bibliotheek die verdwijnt tussen lezen en schrijven: write_item doet niets."""
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    node = await node_of(net, library_id, NEPHAVEN)
    details = await lookup.tmdb.provider().details("movie", "movie:900001", "nl-NL")
    async with maker(net.settings)() as session:
        library = await session.get(MediaLibrary, uuid.UUID(library_id))
        await session.delete(library)
        await session.commit()
    decision = lookup.match.Decision("matched", None, 1.0)
    assert not await lookup.write_item(
        net.settings, uuid.UUID(library_id), node, decision, details, manual=True
    )


# --- review golf 5 -------------------------------------------------------------------------------


async def art_jobs(net) -> list[Job]:
    async with maker(net.settings)() as session:
        query = select(Job).where(Job.type == lookup.ART_JOB, Job.status == "queued")
        return list((await session.scalars(query)).all())


def image_paths(net) -> list[str]:
    return [e["path"] for e in net.fake.log if e["service"] == "image"]


async def test_ontkoppelen_met_een_wachtende_art_taak(net, client):
    """Review golf 5, C1 (a): de art-taak van de automatische koppeling wacht nog (bijvoorbeeld
    na een 429) als Jonas ontkoppelt. Daarna komt er geen poster van TMDB meer bij, en de taak
    gaat niet naar buiten."""
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    await lookup.run(net.settings, {"library_id": library_id})  # alleen het opzoeken
    node = await node_of(net, library_id, NEPHAVEN)
    assert (await row(net, node)).match_state == "matched"
    old = await art_jobs(net)
    assert {j.payload["role"] for j in old} == {"poster", "backdrop"}
    r = await client.delete(f"{URL}/items/{node}/match", headers=net.headers)
    assert r.status_code == 200, r.text
    assert await art_jobs(net) == []  # de wachtende taken zijn weg
    # Ook een taak die al liep (de payload van toen) schrijft niets en haalt niets op
    net.fake.log.clear()
    for job in old:
        await lookup.run_art(net.settings, job.payload)
    await jobs.run_pending(net.settings, lane="media_net")
    stored = await row(net, node)
    assert stored.match_state == "local" and stored.art == {}
    assert image_paths(net) == []


async def test_een_verkeerde_koppeling_met_de_hand_goed_zetten(net, client):
    """Review golf 5, K6 en C1 (b): een film die automatisch verkeerd gekoppeld is (met art
    die nog wacht) koppelt Jonas met de hand aan een andere. De nieuwe id's, de plaatjes van de
    nieuwe film (nooit die van de oude), en het automatisch opzoeken laat hem daarna staan."""
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    await lookup.run(net.settings, {"library_id": library_id})
    node = await node_of(net, library_id, NEPHAVEN)
    wrong = await row(net, node)
    assert wrong.match_state == "matched" and str(wrong.ids["tmdb"]) == "900001"
    old = await art_jobs(net)
    assert {j.payload["path"] for j in old} == {"/p900001.jpg", "/b900001.jpg"}
    r = await client.post(
        f"{URL}/items/{node}/match", json={"provider": "tmdb", "id": "movie:900019"},
        headers=net.headers,
    )  # fmt: skip
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["match_state"] == "manual" and item["title"] == "The Fake Storm"
    stored = await row(net, node)
    assert str(stored.ids["tmdb"]) == "900019" and stored.year == 1998
    queued = await art_jobs(net)
    assert {j.payload["path"] for j in queued} == {"/p900019.jpg", "/b900019.jpg"}
    assert all(j.payload["ref"] == "tmdb:900019" for j in queued)
    # De oude taak (al bezig toen Jonas koppelde) schrijft niets
    net.fake.log.clear()
    for job in old:
        await lookup.run_art(net.settings, job.payload)
    assert (await row(net, node)).art == {}
    assert image_paths(net) == []
    await jobs.run_pending(net.settings, lane="media_net")
    stored = await row(net, node)
    assert set(stored.art) == {"poster", "backdrop"}
    assert sorted(image_paths(net)) == ["/t/p/w1280/b900019.jpg", "/t/p/w500/p900019.jpg"]
    # Het automatisch opzoeken laat de keuze van Jonas staan
    await run(net.settings)
    stored = await row(net, node)
    assert stored.match_state == "manual" and str(stored.ids["tmdb"]) == "900019"


async def test_art_gaat_niet_naar_buiten_als_het_uit_staat(net, client):
    """Review golf 5, C4: art-taken die nog wachten, gaan niet meer naar buiten als de
    bibliotheek online uit heeft, of als de sleutel weg is."""
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    await lookup.run(net.settings, {"library_id": library_id})
    node = await node_of(net, library_id, NEPHAVEN)
    assert len(await art_jobs(net)) == 2
    r = await client.patch(
        f"{URL}/libraries/{library_id}", json={"online": False}, headers=net.headers
    )
    assert r.status_code == 200, r.text
    net.fake.log.clear()
    await jobs.run_pending(net.settings, lane="media_net")
    assert image_paths(net) == [] and (await row(net, node)).art == {}
    # De sleutel weg (en online weer aan): ook niets
    async with maker(net.settings)() as session:
        library = await session.get(MediaLibrary, uuid.UUID(library_id))
        library.online = True
        await session.commit()
    r = await client.post(
        f"{URL}/items/{node}/match", json={"provider": "tmdb", "id": "movie:900001"},
        headers=net.headers,
    )  # fmt: skip
    assert r.status_code == 200 and len(await art_jobs(net)) == 2
    async with maker(net.settings)() as session:
        await secrets.forget_tmdb(session)
    net.fake.log.clear()
    await jobs.run_pending(net.settings, lane="media_net")
    assert image_paths(net) == [] and (await row(net, node)).art == {}


async def test_ontkoppelen_maakt_locked_leeg(net, client):
    """Review golf 5, C8 (Keuze 43): na ontkoppelen is wat met de hand vaststond weer los;
    de titel en het jaar komen weer uit de naam."""
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    await run(net.settings)
    node = await node_of(net, library_id, NEPHAVEN)
    r = await client.patch(
        f"{URL}/items/{node}", json={"title": "Verkeerde titel", "year": 1990},
        headers=net.headers,
    )  # fmt: skip
    assert sorted(r.json()["locked"]) == ["title", "year"]
    r = await client.delete(f"{URL}/items/{node}/match", headers=net.headers)
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["locked"] == [] and item["match_state"] == "local"
    assert (item["title"], item["year"]) == ("De Nephaven", 2004)
    assert (await row(net, node)).locked == [lookup.HIDDEN_LOCK]


async def test_kandidaten_wachten_hooguit_8_seconden(net, client, monkeypatch):
    """Review golf 5, C6 (Keuze 39): de 8 s gelden voor het zoeken en de posters samen. Is
    TMDB te traag: 503 media_provider_unavailable, niet wachten tot de time-out van 20 s."""
    import time

    import anyio

    from app.routers import media_match

    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    node = await node_of(net, library_id, NEPHAVEN)

    async def slow(*args, **kwargs):
        await anyio.sleep(30)
        return []

    monkeypatch.setattr(media_match, "CANDIDATE_SECONDS", 0.3)
    monkeypatch.setattr(lookup, "search", slow)
    start = time.monotonic()
    r = await client.get(f"{URL}/items/{node}/candidates", headers=net.headers)
    assert time.monotonic() - start < 5
    assert r.status_code == 503 and code(r) == "media_provider_unavailable"
