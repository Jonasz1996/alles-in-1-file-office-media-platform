"""Online opzoeken (lookup.py, Keuze 39–44, M8) tegen de nep-providers in het proces.

- Zonder sleutel, of met `online` uit: niets naar buiten, alles blijft lokaal.
- Met een sleutel: films gekoppeld (of naar Nakijken), met hun plaatjes via ons eigen adres;
  series per seizoen één verzoek; muziek via MusicBrainz met de hoes uit de CAA.
- Onbereikbaar of een 429: `jobs.Defer`, de items blijven lokaal; pas na 24 uur een melding (met
  een nepklok). Een 401: de sleutel dicht en de melding `media_provider_auth`.
- Wanneer: de haak na een scan (`scanner.after_scan`, integratie punt 6) en de housekeeping; in
  stukken van BATCH.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid

import pytest
from sqlalchemy import func, select

from app import jobs
from app.config import MediaConfig
from app.main import app
from app.media import lookup
from app.media.providers import art, http, secrets
from app.media.state import MediaPolicy
from app.models import Job, MediaAlbum, MediaItem, MediaShow
from tests.media.fake_providers import FakeConfig, create_app, kit, load
from tests.media.fake_providers.app import transport
from tests.test_files import mkdir, root
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_scanner import items, scan, upload
from tests.test_media_scanner import world as world  # noqa: F401
from tests.test_media_sessions import maker

pytestmark = pytest.mark.anyio

START = dt.datetime(2026, 5, 1, 12, 0, tzinfo=dt.UTC)


class Wall:
    """De klok van http.py voor `failing_since` en `last_ok`."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> dt.datetime:
        return self.now


class Net:
    def __init__(self, settings, headers, fake, outbound, clock, wall):
        self.settings = settings
        self.headers = headers
        self.fake = fake
        self.outbound = outbound
        self.clock = clock
        self.wall = wall


@pytest.fixture
async def net(world, monkeypatch):  # noqa: F811
    """De nep-providers als de enige uitgaande client van het proces, met een nepklok (geen echte
    pauzes) en een eigen wandklok."""
    settings, _fake_worker, headers = world
    secrets.reset()
    lookup.reset()
    clock, wall = kit.Clock(), Wall()
    providers = create_app(FakeConfig(tmdb_key=kit.KEY, data=load(), clock=clock))
    out = http.Outbound(
        settings,
        resolver=kit.Resolver(),
        transport=transport(providers),
        clock=clock,
        wall=wall,
        sleep=clock.sleep,
    )
    previous = http.use(out)
    yield Net(settings, headers, providers.state.fake, out, clock, wall)
    http.use(previous)
    await out.aclose()
    lookup.reset()
    secrets.reset()


async def save_key(net: Net) -> None:
    async with maker(net.settings)() as session:
        await secrets.save_tmdb(session, net.settings, kit.KEY)
    net.fake.log.clear()


async def make_library(client, headers, name="Films", kind="movies", online=True) -> tuple:
    parent = (await root(client, headers))["folder"]["id"]
    folder = await mkdir(client, headers, parent, name)
    r = await client.post(
        "/api/v1/media/libraries",
        json={"root_id": folder, "kind": kind, "online": online},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"], folder


async def films(client, net: Net, names: list[str], online=True) -> str:
    library_id, folder = await make_library(client, net.headers, online=online)
    for name in names:
        await upload(client, net.headers, folder, name)
    await scan(net.settings)
    return library_id


async def run(settings) -> int:
    """Wat de housekeeping doet, en dan de rij media_net leeg."""
    await lookup.queue_all(settings)
    return await jobs.run_pending(settings, lane="media_net")


async def lookup_jobs(settings, status: str | None = None) -> list[Job]:
    async with maker(settings)() as session:
        query = select(Job).where(Job.type == lookup.JOB)
        if status:
            query = query.where(Job.status == status)
        return list((await session.scalars(query)).all())


NEPHAVEN = "De.Nephaven.2004.1080p.BluRay.x264-NEP.mkv"
LANTERNS = "Paper.Lanterns.1999.720p.WEB-DL.AAC2.0.H.264-NEP.mkv"
STORM = "The.Fake.Storm.720p.HDTV.x264-NEP.mkv"


# --- zonder provider: niets naar buiten -----------------------------------------------------------


async def test_zonder_sleutel_gaat_er_niets_naar_buiten(net, client):
    library_id = await films(client, net, [NEPHAVEN, STORM])
    assert await lookup.queue_all(net.settings) == 0
    # Ook een taak die er toch staat, doet niets
    await lookup.run(net.settings, {"library_id": library_id})
    assert net.fake.log == []
    found = await items(net.settings, library_id)
    assert {i.match_state for i in found.values()} == {"local"}
    assert found[NEPHAVEN].title == "De Nephaven"


async def test_een_bibliotheek_zonder_online_blijft_lokaal(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN], online=False)
    assert await lookup.queue_all(net.settings) == 0
    await lookup.run(net.settings, {"library_id": library_id})
    assert net.fake.log == []
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "local"


# --- films ---------------------------------------------------------------------------------------


async def test_films_gekoppeld_met_plaatjes_via_ons_eigen_adres(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN, LANTERNS, STORM])
    assert await run(net.settings) >= 1
    found = await items(net.settings, library_id)
    nephaven, storm = found[NEPHAVEN], found[STORM]
    assert nephaven.match_state == "matched" and nephaven.source == "provider"
    assert (nephaven.title, nephaven.year) == ("De Nephaven", 2004)
    assert nephaven.ids["tmdb"] in (900001, "900001")
    assert nephaven.overview
    assert found[LANTERNS].match_state == "matched"
    # Twee films met die titel en geen jaar: Nakijken, met de kandidaten
    assert storm.match_state == "review" and storm.title == "The Fake Storm"
    assert 2 <= len(storm.candidates) <= 5
    assert {c["provider"] for c in storm.candidates} == {"tmdb"}
    # De plaatjes: per rol, ondertekend, alleen via ons eigen adres; nooit een adres van TMDB
    for role, size in (("poster", "w512"), ("backdrop", "w1280")):
        entry = nephaven.art[role]
        assert entry["url"].startswith(f"/api/v1/media/art/{entry['art']}/{size}.webp?sig=")
        assert art.find(net.settings, entry["art"], "w256") is not None
        r = await client.get(entry["url"])
        assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
        assert art.sniff(r.content) == "image/webp"
    # Het item zoals de API het geeft: poster en achtergrond van ons
    r = await client.get(f"/api/v1/media/items/{nephaven.node_id}", headers=net.headers)
    item = r.json()
    assert item["match_state"] == "matched"
    assert item["poster"] == nephaven.art["poster"]["url"]
    assert item["backdrop"] == nephaven.art["backdrop"]["url"]
    # Alle verzoeken met de sleutel in de kop, nooit in het adres; de plaatjeshost zonder
    api = [e for e in net.fake.log if e["service"] == "tmdb"]
    images = [e for e in net.fake.log if e["service"] == "image"]
    assert api and images
    assert all(e["auth"] == "bearer" for e in api)
    assert all(e["auth"] is None for e in images)
    assert all("api_key" not in e["query"] for e in net.fake.log)
    # Niets meer te doen: geen taak meer
    assert await lookup.queue_all(net.settings) == 0


async def test_een_lokale_poster_gaat_voor(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    node = (await items(net.settings, library_id))[NEPHAVEN].node_id
    local = {"node": str(uuid.uuid4())}
    async with maker(net.settings)() as session:
        item = await session.get(MediaItem, node)
        item.art = {"poster": local}
        await session.commit()
    await run(net.settings)
    item = (await items(net.settings, library_id))[NEPHAVEN]
    assert item.match_state == "matched"
    assert item.art["poster"] == local  # niet vervangen
    assert "url" in item.art["backdrop"]  # de achtergrond wel van de provider


async def test_wie_met_de_hand_koppelde_wint(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    node = (await items(net.settings, library_id))[NEPHAVEN].node_id
    async with maker(net.settings)() as session:
        item = await session.get(MediaItem, node)
        item.match_state = "manual"
        await session.commit()
    decision = await lookup.identify(
        lookup.tmdb.provider(), "movie", "De Nephaven", 2004, {}, "nl-NL"
    )
    assert decision.state == "matched"
    assert not await lookup.write_item(net.settings, uuid.UUID(library_id), node, decision, None)
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "manual"


async def test_de_verborgen_vergrendeling_slaat_het_opzoeken_over(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN, LANTERNS])
    node = (await items(net.settings, library_id))[NEPHAVEN].node_id
    async with maker(net.settings)() as session:
        item = await session.get(MediaItem, node)
        item.locked = ["title", lookup.HIDDEN_LOCK]
        await session.commit()
    await run(net.settings)
    found = await items(net.settings, library_id)
    assert found[NEPHAVEN].match_state == "local"
    assert found[LANTERNS].match_state == "matched"


async def test_in_stukken_met_een_opvolger(net, client, monkeypatch):
    await save_key(net)
    monkeypatch.setattr(lookup, "BATCH", 2)
    library_id = await films(client, net, [NEPHAVEN, LANTERNS, STORM])
    await run(net.settings)
    found = await items(net.settings, library_id)
    assert {i.match_state for i in found.values()} == {"matched", "review"}
    # Twee rondes: 2 + 1 (de tweede zette geen derde klaar)
    assert len(await lookup_jobs(net.settings, "done")) == 2
    assert await lookup_jobs(net.settings, "queued") == []


async def test_tijdens_de_scan_wacht_de_taak(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    async with maker(net.settings)() as session:
        from app.models import MediaLibrary

        library = await session.get(MediaLibrary, uuid.UUID(library_id))
        library.scan_state = "running"
        await session.commit()
    await run(net.settings)
    # Wat er was, is gedaan; er staat een opvolger klaar voor na de scan
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "matched"
    [later] = await lookup_jobs(net.settings, "queued")
    assert later.run_after.replace(tzinfo=dt.UTC) > dt.datetime.now(dt.UTC)


# --- onbereikbaar, afgeremd, een foute sleutel ---------------------------------------------------


async def test_onbereikbaar_blijft_lokaal_en_meldt_pas_na_24_uur(net, client):
    from app.media import providers

    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    net.fake.fault("500", host="tmdb", times=None)
    with pytest.raises(jobs.Defer) as defer:
        await lookup.run(net.settings, {"library_id": library_id})
    assert defer.value.seconds == lookup.OFFLINE
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "local"
    since = providers.unreachable_since("tmdb")
    assert since == START
    status = providers.status(now=START)["tmdb"]
    assert status["configured"] and status["failing_since"] == START
    assert status["last_error"]
    assert providers.notices(now=START + dt.timedelta(hours=23, minutes=59)) == []
    assert providers.notices(now=START + dt.timedelta(hours=24)) == ["media_provider_failing"]
    # Via de rij: terug in de rij, de poging telt niet
    await lookup.queue_all(net.settings)
    await jobs.run_pending(net.settings, lane="media_net")
    [job] = await lookup_jobs(net.settings, "queued")
    assert job.attempts == 0
    # Weer bereikbaar (na de stroomonderbreker): gekoppeld, en de melding is weg
    net.fake.clear_faults()
    net.clock.now += http.BREAKER_OPEN + 1
    net.wall.now = START + dt.timedelta(hours=25)
    await lookup.run(net.settings, {"library_id": library_id})
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "matched"
    assert providers.unreachable_since("tmdb") is None
    assert providers.notices(now=START + dt.timedelta(hours=26)) == []


async def test_een_lange_429_is_defer(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    net.fake.fault("429", "/3/search/movie", host="tmdb", retry_after=30, times=None)
    with pytest.raises(jobs.Defer) as defer:
        await lookup.run(net.settings, {"library_id": library_id})
    assert defer.value.seconds == pytest.approx(30, abs=1)
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "local"


async def test_een_401_zet_de_sleutel_dicht_en_meldt(net, client):
    from app.media import providers

    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    assert providers.notices() == []
    net.fake.config.tmdb_key = "een-andere-sleutel-" + "x" * 20  # ingetrokken bij TMDB
    await lookup.run(net.settings, {"library_id": library_id})  # geen fout: alleen lokaal
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "local"
    async with maker(net.settings)() as session:
        assert (await secrets.status(session, net.settings))["ok"] is False
    assert providers.notices() == ["media_provider_auth"]
    assert providers.status()["tmdb"]["auth"] is True
    [view] = [v for v in providers.views() if v.name == "tmdb"]
    assert view.key_set and view.last_error == "media_provider_auth"
    # Dicht: geen nieuwe taak, geen verkeer meer
    net.fake.log.clear()
    assert await lookup.queue_all(net.settings) == 0
    await lookup.run(net.settings, {"library_id": library_id})
    assert net.fake.log == []
    # Een nieuwe sleutel: weer open, de melding weg
    net.fake.config.tmdb_key = kit.KEY
    await save_key(net)
    assert providers.notices() == []
    await run(net.settings)
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "matched"


NEW_KEY = "nieuwe-sleutel-" + "n" * 30


def swap_key_during_search(net: Net, monkeypatch) -> None:
    """Terwijl een verzoek met de oude sleutel loopt, bewaart Jonas een nieuwe; TMDB weigert de
    oude daarna (401). Zo komt de 401 pas binnen als de nieuwe er al staat."""
    from app.media.providers import tmdb

    search = tmdb.Tmdb.search

    async def works(key: str) -> None:
        return None

    async def swap(self, *args, **kwargs):
        async with maker(net.settings)() as session:
            await secrets.save_tmdb(session, net.settings, NEW_KEY, check=works)
        net.fake.config.tmdb_key = NEW_KEY
        return await search(self, *args, **kwargs)

    monkeypatch.setattr(tmdb.Tmdb, "search", swap)


async def test_een_late_401_bij_het_opzoeken_laat_de_nieuwe_sleutel_open(net, client, monkeypatch):
    """Nacontrole golf 5 (C7, de aanroep in run): de 401 van een ronde die nog met de oude
    sleutel liep, zet de nieuwe sleutel niet dicht."""
    from app.media import providers

    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    swap_key_during_search(net, monkeypatch)
    await lookup.run(net.settings, {"library_id": library_id})  # 401: deze ronde stopt
    async with maker(net.settings)() as session:
        assert (await secrets.status(session, net.settings))["ok"] is True
    assert secrets.usable() and secrets.tmdb_key() == NEW_KEY
    assert providers.notices() == []


async def test_de_sleutel_nooit_in_de_log(net, client, caplog):
    caplog.set_level("DEBUG")
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN, STORM])
    net.fake.fault("500", "/3/search/movie", host="tmdb", times=1)
    await run(net.settings)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    await jobs.run_pending(net.settings, lane="media_net", at=later)
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "matched"
    assert kit.KEY not in caplog.text
    assert kit.KEY[-12:] not in caplog.text


async def test_een_ding_dat_steeds_faalt_houdt_de_rest_niet_tegen(net, client):
    """Review golf 5, C2: een kapot antwoord voor één film (de provider werkt verder) was een
    Defer voor de hele bibliotheek, elke ronde opnieuw, zonder einde. Nu: de rest wordt
    gekoppeld, de ronde erna komt pas na UNIT_RETRY, en na UNIT_TRIES keer naar Nakijken."""
    await save_key(net)
    library_id = await films(client, net, [LANTERNS, NEPHAVEN])
    net.fake.fault("garbage", "/3/movie/900002", host="tmdb", times=None)
    await run(net.settings)
    found = await items(net.settings, library_id)
    assert found[NEPHAVEN].match_state == "matched"
    assert found[LANTERNS].match_state == "local"
    [job] = await lookup_jobs(net.settings, "queued")
    after = job.run_after.replace(tzinfo=job.run_after.tzinfo or dt.UTC)
    wait = (after - dt.datetime.now(dt.UTC)).total_seconds()
    assert lookup.UNIT_RETRY - 10 < wait <= lookup.UNIT_RETRY
    at = dt.datetime.now(dt.UTC)
    for _ in range(lookup.UNIT_TRIES - 1):
        at += dt.timedelta(seconds=lookup.UNIT_RETRY + 1)
        await jobs.run_pending(net.settings, lane="media_net", at=at)
    found = await items(net.settings, library_id)
    assert found[LANTERNS].match_state == "review"
    assert found[NEPHAVEN].match_state == "matched"
    # Klaar: geen ronde meer in de rij, en de provider zelf staat niet als kapot
    assert await lookup_jobs(net.settings, "queued") == []
    from app.media import providers

    assert providers.unreachable_since("tmdb") is None


async def test_een_provider_die_weg_is_telt_niet_per_ding(net, client):
    """Is de provider zelf weg, dan is het een Defer en telt er niets per ding (anders zou een
    lange storing alles naar Nakijken sturen)."""
    await save_key(net)
    library_id = await films(client, net, [LANTERNS])
    net.fake.fault("500", host="tmdb", times=None)
    for _ in range(lookup.UNIT_TRIES + 1):
        with pytest.raises(jobs.Defer):
            await lookup.run(net.settings, {"library_id": library_id})
    assert lookup._failures == {}
    net.fake.clear_faults()
    net.clock.now += http.BREAKER_OPEN + 1
    await lookup.run(net.settings, {"library_id": library_id})
    assert (await items(net.settings, library_id))[LANTERNS].match_state == "matched"


# --- wanneer: de haak en de housekeeping ---------------------------------------------------------


async def test_na_een_scan_zet_de_haak_een_taak_klaar(net, client):
    """Integratie, punt 6 (open punt 1 van M8): de scanner roept na een scan
    scanner.after_scan aan, en M8 zet dan meteen het opzoeken klaar voor die bibliotheek. Tot
    de integratie was dat een haak op hooks.touched met een wachttijd van minstens twee tikken;
    zonder scan (een map erbij) staat er nu niets klaar."""
    from app.media import scanner

    await save_key(net)
    assert lookup.scanned in scanner._after_scan
    library_id, folder = await make_library(client, net.headers)
    other, _ = await make_library(client, net.headers, "Leeg")
    await upload(client, net.headers, folder, NEPHAVEN)
    assert await lookup_jobs(net.settings) == []  # nog geen scan: niets
    await scanner.tick(net.settings)
    await jobs.run_pending(net.settings, lane="media_scan")
    # Meteen na de scan, alleen voor de bibliotheek met iets op te zoeken
    [job] = await lookup_jobs(net.settings, "queued")
    assert job.payload == {"library_id": library_id} and other != library_id
    await jobs.run_pending(net.settings, lane="media_net")
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "matched"


async def test_zonder_sleutel_doet_de_haak_niets(net, client):
    library_id, folder = await make_library(client, net.headers)
    await upload(client, net.headers, folder, NEPHAVEN)
    from app.media import scanner

    await scanner.tick(net.settings)
    await jobs.run_pending(net.settings, lane="media_scan")
    assert await lookup_jobs(net.settings) == []
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "local"


async def test_een_fout_in_de_haak_houdt_de_scan_niet_tegen(net, client, monkeypatch, caplog):
    from app.media import scanner

    async def kapot(settings, library_id):
        raise RuntimeError("kapot")

    monkeypatch.setattr(scanner, "_after_scan", [kapot, *scanner._after_scan])
    await save_key(net)
    library_id, folder = await make_library(client, net.headers)
    await upload(client, net.headers, folder, NEPHAVEN)
    await scanner.tick(net.settings)
    await jobs.run_pending(net.settings, lane="media_scan")
    assert NEPHAVEN in await items(net.settings, library_id)
    assert "haak na de scan" in caplog.text
    [job] = await lookup_jobs(net.settings, "queued")  # de haak van M8 liep toch
    assert job.payload == {"library_id": library_id}


async def test_de_housekeeping_kijkt_rond(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    await lookup.sweep(net.settings)
    [job] = await lookup_jobs(net.settings, "queued")
    assert job.key == lookup.key(library_id)
    await lookup.sweep(net.settings)  # niet nog een
    assert len(await lookup_jobs(net.settings)) == 1


async def test_opruimen_houdt_wat_gebruikt_wordt(net, client):
    await save_key(net)
    library_id = await films(client, net, [NEPHAVEN])
    await run(net.settings)
    item = (await items(net.settings, library_id))[NEPHAVEN]
    sha = item.art["poster"]["art"]
    later = time.time() + 2 * 3600
    assert (await art.cleanup(net.settings, later))["art"] == 0
    assert art.find(net.settings, sha, "w512") is not None
    async with maker(net.settings)() as session:
        row = await session.get(MediaItem, item.node_id)
        row.art = {}
        await session.commit()
    assert (await art.cleanup(net.settings, later))["art"] == 2
    assert art.find(net.settings, sha, "w512") is None


# --- series --------------------------------------------------------------------------------------


async def test_series_eerst_de_serie_dan_per_seizoen_een_verzoek(net, client):
    await save_key(net)
    library_id, folder = await make_library(client, net.headers, "Series", "shows")
    show = await mkdir(client, net.headers, folder, "The Fake Coast (2010)")
    s1 = await mkdir(client, net.headers, show, "Season 1")
    s2 = await mkdir(client, net.headers, show, "Season 2")
    for parent, name in (
        (s1, "The Fake Coast S01E01.mkv"),
        (s1, "The Fake Coast S01E02.mkv"),
        (s1, "The Fake Coast S01E09.mkv"),  # kent de provider niet
        (s2, "The Fake Coast S02E01.mkv"),
    ):
        await upload(client, net.headers, parent, name)
    await scan(net.settings)
    await run(net.settings)
    async with maker(net.settings)() as session:
        [row] = (await session.scalars(select(MediaShow))).all()
    assert row.match_state == "matched" and row.ids["tmdb"] in (900101, "900101")
    assert "url" in row.art["poster"]
    found = await items(net.settings, library_id)
    first = found["The Fake Coast S01E01.mkv"]
    assert (first.match_state, first.title) == ("matched", "Aflevering 1")
    assert first.overview and first.aired
    assert "url" in first.art["poster"]  # de still
    assert found["The Fake Coast S02E01.mkv"].match_state == "matched"
    assert found["The Fake Coast S01E09.mkv"].match_state == "review"
    # Per seizoen één verzoek per taal (en-US alleen om lege titels of samenvattingen aan te
    # vullen), nooit per aflevering
    seasons = [(e["inner"], e["query"]) for e in net.fake.log if "/season/" in e["inner"]]
    assert len(seasons) == len(set(seasons))
    assert {path for path, _ in seasons} == {"/3/tv/900101/season/1", "/3/tv/900101/season/2"}
    assert not any("/episode/" in e["inner"] for e in net.fake.log)
    # De lijst Nakijken van de bibliotheek: de onbekende aflevering
    r = await client.get(f"/api/v1/media/libraries/{library_id}/review", headers=net.headers)
    assert r.status_code == 200, r.text
    page = r.json()
    assert [(c["kind"], c["season"], c["episode"]) for c in page["items"]] == [("episode", 1, 9)]
    assert page["items"][0]["node_id"] == str(found["The Fake Coast S01E09.mkv"].node_id)


# --- muziek --------------------------------------------------------------------------------------


async def test_muziek_via_musicbrainz_met_de_hoes_uit_de_caa(net, client, monkeypatch):
    monkeypatch.setattr(
        app.state, "media_policy", MediaPolicy(MediaConfig(musicbrainz=True)), raising=False
    )
    library_id, _folder = await make_library(client, net.headers, "Muziek", "music")
    lib = uuid.UUID(library_id)
    async with maker(net.settings)() as session:
        rows = [
            MediaAlbum(library_id=lib, key="nep orkest|zeegezichten", title="Zeegezichten",
                       sort_title="zeegezichten", album_artist="Nep Orkest", track_count=10),
            MediaAlbum(library_id=lib, key="quiet engines|static", title="Statisch (tags)",
                       sort_title="static", album_artist="Quiet Engines", track_count=9,
                       mbid="0c242e53-4fce-5f1f-a2f6-1a6a2e8bb8d5"),
            MediaAlbum(library_id=lib, key="niemand|bestaat niet", title="Bestaat Niet",
                       sort_title="bestaat niet", album_artist="Niemand", track_count=3),
        ]  # fmt: skip
        session.add_all(rows)
        await session.commit()
    await run(net.settings)
    async with maker(net.settings)() as session:
        albums = {a.album_artist: a for a in (await session.scalars(select(MediaAlbum))).all()}
    assert albums["Nep Orkest"].match_state == "matched"
    assert albums["Quiet Engines"].match_state == "matched"
    assert albums["Niemand"].match_state == "review"
    cover = albums["Nep Orkest"].art["cover"]
    assert cover["url"].startswith(f"/api/v1/media/art/{cover['art']}/w512.webp?sig=")
    r = await client.get(cover["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert "cover" in albums["Quiet Engines"].art
    # MusicBrainz: met onze User-Agent, JSON, en nooit meer dan 1 per seconde
    mb = [e for e in net.fake.log if e["service"] == "musicbrainz"]
    assert mb and all("fmt=json" in e["query"] for e in mb)
    assert all(e["headers"].get("user-agent") == http.USER_AGENT for e in mb)
    assert min(net.clock.slept, default=1.0) > 0  # de token bucket hield het tempo laag
    assert {e["status"] for e in mb} == {200}
    # De MBID uit de tags: meteen de release, niet zoeken
    assert any("0c242e53" in e["inner"] for e in mb)


async def test_muziek_uit_op_beheer_gaat_niet_naar_buiten(net, client):
    library_id, _folder = await make_library(client, net.headers, "Muziek", "music")
    async with maker(net.settings)() as session:
        session.add(
            MediaAlbum(library_id=uuid.UUID(library_id), key="a|b", title="Zeegezichten",
                       sort_title="zeegezichten", album_artist="Nep Orkest", track_count=10)
        )  # fmt: skip
        await session.commit()
    assert await lookup.queue_all(net.settings) == 0
    await lookup.run(net.settings, {"library_id": library_id})
    assert net.fake.log == []
    async with maker(net.settings)() as session:
        assert await session.scalar(select(func.count()).select_from(MediaAlbum).where(
            MediaAlbum.match_state == "local")) == 1  # fmt: skip
