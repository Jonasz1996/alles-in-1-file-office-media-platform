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

import logging
import sys
import uuid
from datetime import UTC, timedelta
from pathlib import Path

import anyio
import pytest
from sqlalchemy import delete, func, select, update

from app import jobs, photo_rows, photometa
from app.geo import load, places
from app.models import Job, PhotoGeo
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
    await load.start(settings)
    while load._taken:
        for taak in list(load._taken):
            await taak
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
    await load.start(settings)
    while load._taken:
        for taak in list(load._taken):
            await taak
    assert await jobs_van(settings, photo_rows.PLACES_TYPE) == ["done"]


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
    assert await photo_rows.enqueue_places(settings)
    assert not await photo_rows.enqueue_places(settings)  # staat al klaar: geen tweede
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

    # Nog een keer (een volgende bouw): wat al een plaats heeft blijft, zee blijft NULL
    assert await photo_rows.enqueue_places(settings)
    await jobs.run_pending(settings)
    assert (await plaatsen_van(settings))[zee] == (None, None, None)
    assert (await plaatsen_van(settings))[grens] == (GRENSDORP, "NL", "Limburg")


async def test_wacht_op_photometa_van_voor_de_bouw(client, admin, settings, monkeypatch):
    """Een run van photometa die vóór de bouw begon, kan zijn plaatsen nog zonder places.sqlite
    opgezocht hebben en zijn rijen pas na de vulling schrijven. Zolang hij loopt, wacht geo.places
    (Defer, de poging telt niet); daarna vult hij alles."""
    h, _tokens = admin
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    await verwerk(settings)
    # Zonder places.sqlite doet de job niets (en faalt niet)
    assert await photo_rows.enqueue_places(settings)
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
    assert await photo_rows.enqueue_places(settings)
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
    await photo_rows.run_places(settings, {"after": "", "built": built.isoformat(), "rows": 0})
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
