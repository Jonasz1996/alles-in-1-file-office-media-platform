"""Een kapotte places.sqlite (review golf 3, veilig F1): de meta klopt nog, maar de pagina's van
de plaatsen en de vlakken zijn beschadigd (een schijffout, een afgebroken kopie). Dan zijn er
geen plaatsnamen, zoals zonder places.sqlite: /location en /photos/places geven 200 zonder
plaats, en een nieuwe upload houdt zijn plek en zijn pHash (photometa faalt niet, dus on_dead zet
gps niet op NULL en blob_hash niet op "none"). Eén regel in het log, zonder plek. Bij de
volgende start (of geo-load) ziet ensure() het met quick_check en bouwt hem opnieuw."""

import logging
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from app.geo import load, places
from app.models import BlobHash, Job
from tests.factories import maker
from tests.geo.hulp import AMSTERDAM, gps_van, met_gps, rijen, uid, verwerk
from tests.test_previews import upload

pytestmark = pytest.mark.anyio

AMSTERDAM_NAAM = {"name": "Amsterdam", "admin1": "North Holland", "cc": "NL"}


def beschadig(pad: Path, tabellen=("place", "country_part")) -> None:
    """De eerste pagina van deze tabellen overschrijven; de meta blijft heel."""
    db = sqlite3.connect(pad)
    grootte = db.execute("PRAGMA page_size").fetchone()[0]
    wortels = dict(db.execute("SELECT name, rootpage FROM sqlite_master WHERE type = 'table'"))
    db.close()
    with open(pad, "r+b") as f:
        for naam in tabellen:
            f.seek((wortels[naam] - 1) * grootte)
            f.write(bytes(range(7, 256)) * (grootte // 249) + b"\x05" * (grootte % 249))


async def test_een_kapotte_places_sqlite_geeft_geen_plaatsen_en_geen_fout(
    client, admin, settings, plaatsen, caplog
):
    h, tokens = admin
    a = (await upload(client, h, "a.jpg", met_gps("red")))["id"]
    await verwerk(settings)
    r = await client.get(f"/api/v1/files/{a}/location", headers=h)
    assert r.status_code == 200 and r.json()["place"] == AMSTERDAM_NAAM, r.text

    pad = Path(settings.places_db)
    beschadig(pad)
    places.forget()
    # De meta klopt nog: alleen naar de meta kijken ziet het niet
    assert load.current(pad)
    caplog.set_level(logging.INFO, "platform.geo")

    r = await client.get(f"/api/v1/files/{a}/location", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"lat": 52.3669444, "lon": 4.8838889, "alt": None, "place": None}
    r = await client.get("/api/v1/photos/places", headers=h)
    assert (r.status_code, r.json()) == (200, {"places": []}), r.text
    # Daarna opent de app hem niet opnieuw zolang het hetzelfde bestand is
    assert places.get(settings) is None

    # Een nieuwe foto met GPS: zijn plek (zonder plaats) en zijn hash blijven
    b = await upload(client, h, "b.jpg", met_gps("green"))
    await verwerk(settings)
    sha = b["etag"].strip('"')
    assert (await gps_van(settings))[sha] == 1
    assert (await rijen(settings))[b["id"]] == (uid(tokens), 0, None, "exif")
    async with maker(settings)() as db:
        assert await db.scalar(select(BlobHash.state).where(BlobHash.blob_sha == sha)) == "flat"
        vraag = select(Job.status, Job.error).where(Job.type == "photometa")
        keten = (await db.execute(vraag)).all()
    assert {status for status, _fout in keten} == {"done"}, keten
    # Eén regel in het log, zonder plek of plaats
    regels = [r.getMessage() for r in caplog.records if r.name == "platform.geo"]
    kapot = [r for r in regels if "places.sqlite is niet te lezen" in r]
    assert len(kapot) == 1, regels
    for geheim in ("52.3", "4.88", "Amsterdam"):
        assert all(geheim not in r for r in regels), (geheim, regels)

    # De volgende start ziet het (quick_check) en bouwt hem opnieuw; de oude rijen houden hun
    # plaats, de nieuwe krijgt hem als photometa hem opnieuw leest (z3.md, Open)
    assert load.ensure(settings) == "gebouwd"
    assert places.get(settings) is not None
    r = await client.get(f"/api/v1/files/{a}/location", headers=h)
    assert r.json()["place"] == AMSTERDAM_NAAM, r.text
    assert (await rijen(settings))[a] == (uid(tokens), 0, AMSTERDAM, "exif")
    assert load.ensure(settings) == "klaar"


async def test_kapot_zonder_bronnen_wordt_opzij_gezet(settings, plaatsen, caplog):
    """Kan hij niet opnieuw gebouwd worden (de bronnen zijn weg), dan zet ensure() hem opzij:
    geen plaatsnamen, en de volgende start probeert het niet steeds opnieuw."""
    pad = Path(settings.places_db)
    beschadig(pad, ("place",))
    places.forget()
    for bron in (load.LANDEN.naam, load.PLAATSEN.naam):
        (Path(settings.geo_src) / bron).unlink()
    caplog.set_level(logging.INFO, "platform.geo")
    assert load.ensure(settings) == "geen_bron"
    assert sorted(p.name for p in pad.parent.glob("places.sqlite*")) == ["places.sqlite.kapot"]
    assert places.get(settings) is None
    regels = [r.getMessage() for r in caplog.records if r.name == "platform.geo"]
    assert any("places.sqlite is kapot" in r for r in regels), regels


async def test_een_vlak_dat_niet_uit_te_pakken_is(settings, plaatsen, caplog):
    """De pagina's zijn heel, maar de ringen van een vlak niet (zlib of struct): geen land."""
    pad = Path(settings.places_db)
    db = sqlite3.connect(pad)
    db.execute("UPDATE country_part SET rings = x'78da0000'")
    db.commit()
    db.close()
    places.forget()
    caplog.set_level(logging.INFO, "platform.geo")
    p = places.get(settings)
    assert p.lookup(52.0907, 5.1214) == places.NIETS
    assert places.get(settings) is None
    assert sum("places.sqlite is niet te lezen" in r.getMessage() for r in caplog.records) == 1


async def test_photometa_faalt_niet_op_een_fout_in_de_plaatsen(
    client, admin, settings, plaatsen, monkeypatch, caplog
):
    """Ook een fout die Places niet kent, laat het stuk van photometa niet mislukken: de plek
    blijft, zonder plaats, en de pHash ook."""
    h, tokens = admin

    def stuk(self, lat, lon):
        raise RuntimeError(f"stuk bij {lat} {lon}")

    monkeypatch.setattr(places.Places, "lookup", stuk)
    caplog.set_level(logging.INFO, "platform")
    a = await upload(client, h, "a.jpg", met_gps())
    await verwerk(settings)
    assert (await rijen(settings)) == {a["id"]: (uid(tokens), 0, None, "exif")}
    sha = a["etag"].strip('"')
    assert (await gps_van(settings))[sha] == 1
    async with maker(settings)() as db:
        assert await db.scalar(select(BlobHash.state).where(BlobHash.blob_sha == sha)) == "flat"
        keten = (await db.execute(select(Job.status).where(Job.type == "photometa"))).all()
    assert {status for (status,) in keten} == {"done"}, keten
    tekst = "\n".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in tekst and "52.3" not in tekst and "stuk bij" not in tekst
