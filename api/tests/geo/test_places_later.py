"""Foto's met een plek die photometa las vóór places.sqlite er was (I1, A4).

Bij de upgrade naar 0.8.0 bouwt de app places.sqlite op de achtergrond (load.start), terwijl
photometa v2 alle foto's opnieuw leest. Wat in die tijd gelezen werd, kreeg de plek zonder plaats
en land (places.get() gaf None, dus places.NIETS). Niets las die rijen daarna opnieuw (z3.md,
Open: "Rijen van voor places.sqlite houden place_id NULL tot ze opnieuw gelezen worden"). Nu zet
een nieuwe bouw (bij de start of met geo-load) de job geo.places klaar (app/photo_rows.py): die
vult plaats, land en provincie in uit de lat en lon die er al staan, in stukken, met dezelfde
controle als het gewone schrijven (locatie "uit", de eigenaar van de node), en wacht op een run
van photometa die vóór de bouw begon.
"""

import json
import logging
import sqlite3
import sys
import uuid
from datetime import UTC, timedelta
from pathlib import Path

import anyio
import pytest
from sqlalchemy import delete, func, select, update

from app import jobs, photo_rows, photometa
from app.geo import load, places
from app.models import Job, PhotoGeo, PhotoPref
from tests.factories import maker
from tests.geo import bronnen
from tests.geo.hulp import AMSTERDAM, met_gps, rijen, uid, verwerk
from tests.test_previews import upload
from tests.test_shares import person

pytestmark = pytest.mark.anyio

API_DIR = Path(__file__).resolve().parents[2]
GRENSDORP = load.place_id("NL", "Limburg", "Grensdorp", 50.8, 5.0)


@pytest.fixture(autouse=True)
def _vergeten():
    places.forget()
    yield
    places.forget()


async def plaatsen_van(settings) -> dict[str, tuple[int | None, str | None, str | None]]:
    """Per node: (place_id, country, admin1)."""
    async with maker(settings)() as db:
        rows = await db.execute(
            select(PhotoGeo.node_id, PhotoGeo.place_id, PhotoGeo.country, PhotoGeo.admin1)
        )
        return {str(n): (p, c, a) for n, p, c, a in rows}


async def zet_plek(settings, node_id: str, lat: float, lon: float) -> None:
    async with maker(settings)() as db:
        await db.execute(
            update(PhotoGeo).where(PhotoGeo.node_id == _uuid(node_id)).values(lat=lat, lon=lon)
        )
        await db.commit()


def _uuid(node_id: str) -> uuid.UUID:
    return uuid.UUID(node_id)


def bouw(settings) -> str | None:
    """Het id van de bouw van places.sqlite (meta "bouw")."""
    return load.bouw_id(Path(settings.places_db))


async def gestart(settings) -> None:
    """load.start, en wachten tot zijn taak op de achtergrond klaar is."""
    await load.start(settings)
    while load._taken:
        for taak in list(load._taken):
            await taak


async def jobs_van(settings, type_: str) -> list[str]:
    async with maker(settings)() as db:
        rows = await db.scalars(
            select(Job.status).where(Job.type == type_).order_by(Job.created_at)
        )
        return list(rows)


async def bouw_bij_de_start(settings, monkeypatch) -> None:
    """Zoals de app bij de start: load.start (de haak van de backfill) bouwt op de achtergrond;
    wachten tot die klaar is, dan de jobs."""
    bronnen.gebruik(monkeypatch, bronnen.maak(settings.geo_src))
    await gestart(settings)
    await jobs.run_pending(settings)


async def test_fotos_van_voor_places_sqlite_krijgen_na_de_bouw_hun_plaats(
    client, admin, settings, monkeypatch
):
    h, tokens = admin
    eva = uid(tokens)
    assert places.get(settings) is None  # de bouw loopt nog
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    await verwerk(settings)
    assert await rijen(settings) == {foto: (eva, 0, None, "exif")}
    assert await plaatsen_van(settings) == {foto: (None, None, None)}

    await bouw_bij_de_start(settings, monkeypatch)
    assert await plaatsen_van(settings) == {foto: (AMSTERDAM, "NL", "North Holland")}
    assert await rijen(settings) == {foto: (eva, 0, AMSTERDAM, "exif")}
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == ["done"]
    # Een start zonder nieuwe bouw ("klaar") zet niets klaar: één keer per bouw
    await gestart(settings)
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == ["done"]


async def test_mislukt_klaarzetten_doet_de_volgende_start(
    client, admin, settings, monkeypatch, caplog
):
    """Mislukt het klaarzetten van geo.places na de bouw (database is locked na de busy_timeout
    tijdens de upgrade, of de app stopt tussen os.replace en de commit), dan doet de volgende
    start het, ook al is places.sqlite dan "klaar": Setting "geo.places" komt in dezelfde
    transactie als de job, en elke start kijkt hem na (review I1, F2)."""
    h, _tokens = admin
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    await verwerk(settings)
    echt = jobs.enqueue

    async def op_slot(db, type_, *args, **kwargs):
        if type_ == photo_rows.PLACES_TYPE:
            raise sqlite3.OperationalError("database is locked")
        return await echt(db, type_, *args, **kwargs)

    monkeypatch.setattr(jobs, "enqueue", op_slot)
    with caplog.at_level(logging.WARNING, logger="platform.geo"):
        await bouw_bij_de_start(settings, monkeypatch)
    assert "geo.places niet klaargezet (OperationalError)" in caplog.text
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == []
    assert await plaatsen_van(settings) == {foto: (None, None, None)}

    monkeypatch.setattr(jobs, "enqueue", echt)
    await gestart(settings)  # places.sqlite is er al: "klaar"
    await jobs.run_pending(settings)
    assert await plaatsen_van(settings) == {foto: (AMSTERDAM, "NL", "North Holland")}
    await gestart(settings)
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == ["done"]


async def test_een_nieuwe_bouw_begint_vooraan(client, admin, settings, monkeypatch, caplog):
    """Komt er een nieuwe bouw terwijl de keten van de vorige nog loopt (geo-load --force), dan
    krijgt die bouw een eigen keten die vooraan begint: ook de rijen vóór de cursor van de oude
    keten worden met de nieuwe places.sqlite opgezocht. De oude keten stopt bij zijn volgende
    stuk."""
    h, _tokens = admin
    fotos = sorted(
        [(await upload(client, h, f"{k}.jpg", met_gps(k)))["id"] for k in ("red", "green")],
        key=_uuid,
    )
    await verwerk(settings)
    monkeypatch.setattr(photo_rows, "PLACES_CHUNK", 1)
    bronnen.gebruik(monkeypatch, bronnen.maak(settings.geo_src))
    assert load.ensure(settings) == "gebouwd"
    assert await photo_rows.enqueue_places(settings, bouw(settings))
    assert await jobs.run_one(settings)  # het eerste stuk; zijn opvolger staat klaar
    amsterdam = (AMSTERDAM, "NL", "North Holland")
    assert await plaatsen_van(settings) == {fotos[0]: amsterdam, fotos[1]: (None, None, None)}
    # Alsof de vorige bouw de eerste foto niet kon plaatsen
    async with maker(settings)() as db:
        await db.execute(
            update(PhotoGeo)
            .where(PhotoGeo.node_id == _uuid(fotos[0]))
            .values(place_id=None, country=None, admin1=None)
        )
        await db.commit()

    assert load.ensure(settings, force=True) == "gebouwd"
    assert await photo_rows.enqueue_places(settings, bouw(settings))
    with caplog.at_level(logging.INFO, logger="platform.geo"):
        await jobs.run_pending(settings)
    assert await plaatsen_van(settings) == {fotos[0]: amsterdam, fotos[1]: amsterdam}
    assert "geo_places_stop rows=1" in "\n".join(r.getMessage() for r in caplog.records)


async def test_zee_uit_prullenbak_eigenaar_en_stukken(client, admin, settings, monkeypatch, caplog):
    """Op zee blijft het NULL; wie locatie op "uit" heeft, krijgt niets (ook niet voordat geo.off
    zijn rijen wist); een rij in de prullenbak krijgt zijn plaats wel (Keuze 25a). In stukken van
    PLACES_CHUNK, elk stuk een eigen run; in het log alleen een aantal."""
    h, tokens = admin
    eva = uid(tokens)
    zee, prullenbak, gewoon, grens = [
        (await upload(client, h, f"{k}.jpg", met_gps(k)))["id"]
        for k in ("red", "green", "white", "black")
    ]
    bram_h, bram = await person(client, h, "bram@example.com")
    van_bram = (await upload(client, bram_h, "b.jpg", met_gps("purple")))["id"]
    await verwerk(settings)
    assert set((await plaatsen_van(settings)).values()) == {(None, None, None)}

    await zet_plek(settings, zee, *bronnen.ZEE)
    await zet_plek(settings, grens, *bronnen.GRENS)
    assert (await client.delete(f"/api/v1/files/{prullenbak}", headers=h)).status_code == 204
    # Bram zet locatie op "uit"; geo.off draait hier (nog) niet
    r = await client.patch("/api/v1/me/photo-settings", json={"location": "uit"}, headers=bram_h)
    assert r.status_code == 200, r.text
    async with maker(settings)() as db:
        await db.execute(delete(Job).where(Job.type == photo_rows.OFF_TYPE))
        await db.commit()

    monkeypatch.setattr(photo_rows, "PLACES_CHUNK", 2)
    bronnen.gebruik(monkeypatch, bronnen.maak(settings.geo_src))
    assert load.ensure(settings) == "gebouwd"
    assert await photo_rows.enqueue_places(settings, bouw(settings))
    # Voor deze bouw staat hij al klaar: geen tweede
    assert not await photo_rows.enqueue_places(settings, bouw(settings))
    with caplog.at_level(logging.INFO, logger="platform.geo"):
        await jobs.run_pending(settings)

    assert await plaatsen_van(settings) == {
        zee: (None, None, None),
        prullenbak: (AMSTERDAM, "NL", "North Holland"),
        gewoon: (AMSTERDAM, "NL", "North Holland"),
        grens: (GRENSDORP, "NL", "Limburg"),
        van_bram: (None, None, None),
    }
    assert (await rijen(settings))[prullenbak] == (eva, 1, AMSTERDAM, "exif")
    # Vier rijen zonder plaats (die van Bram niet: "uit") in stukken van 2: twee volle stukken en
    # een laatste run die niets meer vindt; drie gevuld (zee niet)
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == ["done"] * 3
    tekst = "\n".join(r.getMessage() for r in caplog.records)
    assert "geo_places_done rows=3" in tekst
    for geheim in ("52.36", "4.88", "Amsterdam", "Grensdorp", "Limburg", eva, bram):
        assert geheim not in tekst

    # Een volgende bouw (een nieuw id): wat al een plaats heeft blijft, zee blijft NULL
    eerste = bouw(settings)
    assert load.ensure(settings, force=True) == "gebouwd"
    assert bouw(settings) != eerste
    assert await photo_rows.enqueue_places(settings, bouw(settings))
    await jobs.run_pending(settings)
    assert (await plaatsen_van(settings))[zee] == (None, None, None)
    assert (await plaatsen_van(settings))[grens] == (GRENSDORP, "NL", "Limburg")


async def test_wat_tijdens_het_opzoeken_verandert_krijgt_geen_plaats(
    client, admin, settings, monkeypatch, caplog
):
    """Tussen het lezen van een stuk en het schrijven ervan zoekt geo.places op in een thread (±1–3
    s per stuk). In die tijd kan Bram locatie op "uit" zetten (geo.off heeft zijn rijen dan nog
    niet gewist), en kan photometa een foto opnieuw lezen met een andere plek. De UPDATE controleert
    dat zelf, net als write(): Bram krijgt niets, en de verplaatste foto niet de plaats van zijn
    oude plek (review I1, F1)."""
    h, _tokens = admin
    gewoon, verplaatst = [
        (await upload(client, h, f"{k}.jpg", met_gps(k)))["id"] for k in ("red", "green")
    ]
    bram_h, bram = await person(client, h, "bram@example.com")
    van_bram = (await upload(client, bram_h, "b.jpg", met_gps("purple")))["id"]
    await verwerk(settings)
    assert set((await plaatsen_van(settings)).values()) == {(None, None, None)}
    bronnen.gebruik(monkeypatch, bronnen.maak(settings.geo_src))
    assert load.ensure(settings) == "gebouwd"
    echt = places.get(settings)

    async def tussendoor() -> None:
        async with maker(settings)() as db:
            await db.merge(PhotoPref(user_id=_uuid(bram), location="uit"))
            await db.commit()
        await zet_plek(settings, verplaatst, *bronnen.GRENS)

    class Midden:
        """places.get() van run_places: vóór de eerste lookup gebeurt tussendoor()."""

        gedaan = False

        def lookup(self, lat: float, lon: float) -> places.Place:
            if not Midden.gedaan:
                Midden.gedaan = True
                anyio.from_thread.run(tussendoor)
            return echt.lookup(lat, lon)

    monkeypatch.setattr(places, "get", lambda _settings: Midden())
    assert await photo_rows.enqueue_places(settings, bouw(settings))
    with caplog.at_level(logging.INFO, logger="platform.geo"):
        await jobs.run_pending(settings)

    assert Midden.gedaan
    assert await plaatsen_van(settings) == {
        gewoon: (AMSTERDAM, "NL", "North Holland"),
        verplaatst: (None, None, None),
        van_bram: (None, None, None),
    }
    assert "geo_places_done rows=1" in "\n".join(r.getMessage() for r in caplog.records)


async def test_wacht_op_photometa_van_voor_de_bouw(client, admin, settings, monkeypatch):
    """Een run van photometa die vóór de bouw begon, kan zijn plaatsen nog zonder places.sqlite
    opgezocht hebben en zijn rijen pas na de vulling schrijven. Zolang hij loopt, wacht geo.places
    (Defer, de poging telt niet); daarna vult hij alles."""
    h, _tokens = admin
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    await verwerk(settings)
    # Zonder places.sqlite doet de job niets (en faalt niet)
    assert bouw(settings) is None
    assert await photo_rows.enqueue_places(settings, "nog-geen")
    await jobs.run_pending(settings)
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == ["done"]
    assert await plaatsen_van(settings) == {foto: (None, None, None)}

    begon = jobs.now() - timedelta(seconds=30)
    async with maker(settings)() as db:
        lopend = Job(type=photometa.TYPE, key="test:photometa", status="running",
                     payload={}, started_at=begon, attempts=1)  # fmt: skip
        db.add(lopend)
        await db.commit()
        lopend_id = lopend.id
    bronnen.gebruik(monkeypatch, bronnen.maak(settings.geo_src))
    assert load.ensure(settings) == "gebouwd"
    assert await photo_rows.enqueue_places(settings, bouw(settings))
    await jobs.run_pending(settings)
    assert await plaatsen_van(settings) == {foto: (None, None, None)}
    async with maker(settings)() as db:
        job = (
            await db.scalars(
                select(Job).where(Job.type == photo_rows.PLACES_TYPE, Job.status == "queued")
            )
        ).one()
        assert job.attempts == 0 and job.run_after.replace(tzinfo=UTC) > jobs.now()
        # photometa is klaar (zijn rijen staan er)
        await db.execute(update(Job).where(Job.id == lopend_id).values(status="done"))
        await db.commit()
    await jobs.run_pending(settings, at=jobs.now() + timedelta(seconds=photo_rows.PLACES_WAIT * 2))
    assert await plaatsen_van(settings) == {foto: (AMSTERDAM, "NL", "North Holland")}
    # Een run van photometa die ná de bouw begon, telt niet: die vond places.sqlite al
    async with maker(settings)() as db:
        await db.execute(
            update(Job).where(Job.id == lopend_id).values(status="running", started_at=jobs.now())
        )
        await db.execute(update(PhotoGeo).values(place_id=None, country=None, admin1=None))
        await db.commit()
    built = jobs.now() - timedelta(seconds=1)
    await photo_rows.run_places(
        settings, {"after": "", "built": built.isoformat(), "rows": 0, "bouw": bouw(settings)}
    )
    assert await plaatsen_van(settings) == {foto: (AMSTERDAM, "NL", "North Holland")}


async def test_geo_load_naast_de_draaiende_app(client, admin, settings):
    """install.sh: de bronnen kwamen pas bij een volgende run, en de app startte niet opnieuw;
    dan draait geo-load als eigen proces naast de app. Ook die bouw zet geo.places klaar, en de
    app vult de rijen die hij zonder places.sqlite las."""
    h, tokens = admin
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    await verwerk(settings)
    assert await plaatsen_van(settings) == {foto: (None, None, None)}
    b = bronnen.maak(settings.geo_src)
    # Het commando in een eigen proces, met deze bronnen als die van deze versie
    script = (
        "import sys\n"
        "from app.geo import load\n"
        "load.LANDEN = load.Bron(load.LANDEN.naam, '', int(sys.argv[2]), sys.argv[1])\n"
        "load.PLAATSEN = load.Bron(load.PLAATSEN.naam, '', int(sys.argv[4]), sys.argv[3])\n"
        "from app.cli import main\n"
        "sys.exit(main(['geo-load']))\n"
    )
    result = await anyio.run_process(
        [sys.executable, "-c", script, b.landen.sha256, str(b.landen.grootte),
         b.plaatsen.sha256, str(b.plaatsen.grootte)],
        cwd=API_DIR,
        env={"PATH": "/usr/bin:/bin", "PLATFORM_DATA_DIR": str(settings.data_dir),
             "PLATFORM_JWT_SECRET": "test" * 8, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr.decode()
    assert "gebouwd" in result.stdout.decode()
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == ["queued"]
    await jobs.run_pending(settings)
    assert await plaatsen_van(settings) == {foto: (AMSTERDAM, "NL", "North Holland")}
    async with maker(settings)() as db:
        assert await db.scalar(select(func.count()).select_from(PhotoGeo)) == 1


async def test_gelezen_met_plaatsen_uit_krijgt_zijn_plaats_als_ze_weer_aan_gaan(
    client, admin, settings, monkeypatch
):
    """PLAATSEN=uit staat in .env, dus het geldt vanaf een start. places.sqlite blijft staan. Wat
    photometa dan leest, krijgt geen plaats en geen land (places.get() geeft None). Gaat PLAATSEN
    weer aan, dan is het dezelfde bouw en is places.sqlite "klaar". Toch krijgen die foto's hun
    plaats: de start met "uit" wist Setting "geo.places", zodat de start daarna de keten voor deze
    bouw opnieuw klaarzet (I1, C3a)."""
    h, _tokens = admin
    await bouw_bij_de_start(settings, monkeypatch)
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == ["done"]

    uit = settings.model_copy(update={"places": "uit"})
    places.forget()  # een nieuwe start: niets open
    await gestart(uit)
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    await verwerk(uit)
    assert await plaatsen_van(settings) == {foto: (None, None, None)}

    places.forget()
    await gestart(settings)  # weer aan; places.sqlite is "klaar"
    await jobs.run_pending(settings)
    assert await plaatsen_van(settings) == {foto: (AMSTERDAM, "NL", "North Holland")}
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == ["done", "done"]
    # Een volgende start met "aan" zet niets meer klaar; een start met "uit" zonder Setting
    # schrijft niets
    await gestart(settings)
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == ["done", "done"]
    places.forget()
    await gestart(uit)
    assert not await photo_rows.places_off(uit)


async def test_een_keten_die_nog_klaarstaat_bij_plaatsen_uit_begint_daarna_vooraan(
    client, admin, settings, monkeypatch
):
    """De app stopt terwijl de keten nog loopt, start met PLAATSEN=uit en stopt weer voor zijn
    runner die keten oppakt. Dan stond de keten met zijn cursor nog klaar, en de start met "aan"
    daarna zette er geen nieuwe naast (enqueue ontdubbelt op de key): een rij vóór de cursor die
    photometa met "uit" las, bleef zonder plaats. De start met "uit" haalt zo'n keten weg, in de
    transactie die Setting "geo.places" wist (I1, C3a)."""
    h, _tokens = admin
    fotos = sorted(
        [(await upload(client, h, f"{k}.jpg", met_gps(k)))["id"] for k in ("red", "green")],
        key=_uuid,
    )
    await verwerk(settings)
    monkeypatch.setattr(photo_rows, "PLACES_CHUNK", 1)
    bronnen.gebruik(monkeypatch, bronnen.maak(settings.geo_src))
    await gestart(settings)  # de bouw; de keten staat klaar
    assert await jobs.run_one(settings)  # het eerste stuk; zijn opvolger staat klaar
    amsterdam = (AMSTERDAM, "NL", "North Holland")
    leeg = (None, None, None)
    assert await plaatsen_van(settings) == {fotos[0]: amsterdam, fotos[1]: leeg}

    uit = settings.model_copy(update={"places": "uit"})
    places.forget()
    await gestart(uit)  # geen jobs: de app stopt voor zijn runner de keten oppakt
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == ["done"]
    # Alsof photometa de eerste foto met "uit" opnieuw las: geen plaats en geen land
    async with maker(settings)() as db:
        await db.execute(
            update(PhotoGeo)
            .where(PhotoGeo.node_id == _uuid(fotos[0]))
            .values(place_id=None, country=None, admin1=None)
        )
        await db.commit()

    places.forget()
    await gestart(settings)  # weer aan; places.sqlite is "klaar"
    await jobs.run_pending(settings)
    assert await plaatsen_van(settings) == {fotos[0]: amsterdam, fotos[1]: amsterdam}


def _zonder_nederland() -> bytes:
    """De landen van bronnen.geojson() zonder NL: een plek in Nederland ligt dan op zee."""
    data = json.loads(bronnen.geojson())
    data["features"] = [f for f in data["features"] if f["properties"]["ISO_A2"] != "NL"]
    return json.dumps(data).encode()


async def test_geo_load_force_naast_de_app_zoekt_op_in_de_nieuwe_bouw(
    client, admin, settings, monkeypatch, caplog
):
    """geo-load --force draait als eigen proces naast de app (runbook zoeken.md), terwijl de app
    de vorige bouw open heeft en zijn keten nog loopt. De keten van de nieuwe bouw draait in de
    app en zoekt op in de nieuwe places.sqlite, ook de rijen vóór de cursor van de oude keten; de
    oude keten stopt (I1, C3b). Eerst hield places.get() het oude bestand open tot een herstart,
    en na die herstart zette niets de keten opnieuw klaar."""
    h, _tokens = admin
    fotos = sorted(
        [(await upload(client, h, f"{k}.jpg", met_gps(k)))["id"] for k in ("red", "green")],
        key=_uuid,
    )
    await verwerk(settings)
    monkeypatch.setattr(photo_rows, "PLACES_CHUNK", 1)
    # De vorige bouw kent Nederland niet
    bronnen.gebruik(monkeypatch, bronnen.maak(settings.geo_src, landen=_zonder_nederland()))
    assert load.ensure(settings) == "gebouwd"
    assert await photo_rows.enqueue_places(settings, bouw(settings))
    assert await jobs.run_one(settings)  # het eerste stuk; zijn opvolger staat klaar
    leeg = (None, None, None)
    assert await plaatsen_van(settings) == {fotos[0]: leeg, fotos[1]: leeg}
    assert places.get(settings) is not None  # de app heeft de vorige bouw open

    b = bronnen.maak(settings.geo_src)
    script = (
        "import sys\n"
        "from app.geo import load\n"
        "load.LANDEN = load.Bron(load.LANDEN.naam, '', int(sys.argv[2]), sys.argv[1])\n"
        "load.PLAATSEN = load.Bron(load.PLAATSEN.naam, '', int(sys.argv[4]), sys.argv[3])\n"
        "from app.cli import main\n"
        "sys.exit(main(['geo-load', '--force']))\n"
    )
    result = await anyio.run_process(
        [sys.executable, "-c", script, b.landen.sha256, str(b.landen.grootte),
         b.plaatsen.sha256, str(b.plaatsen.grootte)],
        cwd=API_DIR,
        env={"PATH": "/usr/bin:/bin", "PLATFORM_DATA_DIR": str(settings.data_dir),
             "PLATFORM_JWT_SECRET": "test" * 8, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr.decode()
    assert "gebouwd" in result.stdout.decode()

    with caplog.at_level(logging.INFO, logger="platform.geo"):
        await jobs.run_pending(settings)
    amsterdam = (AMSTERDAM, "NL", "North Holland")
    assert await plaatsen_van(settings) == {fotos[0]: amsterdam, fotos[1]: amsterdam}
    assert "geo_places_stop rows=0" in "\n".join(r.getMessage() for r in caplog.records)
