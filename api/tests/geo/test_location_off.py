"""Locatie "uit" en weer "mij" (bouwontwerp fase 6, Keuze 28; §3.9). "uit": de routes kijken
naar photo_pref, dus de kaart, de plaatsen en /location zijn meteen leeg; één job (geo.off) wist
in stukken alle rijen van die gebruiker; zolang hij "uit" heeft, schrijft niets er een (de
controle zit in dezelfde statement als het schrijven). blob_meta.gps blijft (de waarschuwing bij
delen). "mij": de keten van photometa leest zijn foto's opnieuw (fase nodes)."""

import logging
import uuid

import pytest
from sqlalchemy import delete, func, select

from app import jobs, photo_rows
from app.geo import places
from app.models import Job, PhotoPref
from tests.factories import maker
from tests.geo.hulp import (
    AMSTERDAM,
    VENSTER,
    gps_van,
    kaart_ids,
    met_gps,
    plek,
    rijen,
    uid,
    verwerk,
)
from tests.test_previews import upload
from tests.test_shares import person, share_with

pytestmark = pytest.mark.anyio


async def zet(client, headers, waarde: str) -> None:
    r = await client.patch("/api/v1/me/photo-settings", json={"location": waarde}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["location"] == waarde


async def klaar(settings, type_: str) -> int:
    async with maker(settings)() as db:
        return await db.scalar(
            select(func.count()).select_from(Job).where(Job.type == type_, Job.status == "queued")
        )


async def test_uit_maakt_alles_meteen_leeg_en_de_job_wist_in_stukken(
    client, admin, settings, plaatsen, monkeypatch, caplog
):
    h, tokens = admin
    eva = uid(tokens)
    fotos = [(await upload(client, h, f"{i}.jpg", met_gps(c)))["id"]
             for i, c in enumerate(("red", "green", "white", "black", "yellow"))]  # fmt: skip
    # Bram met een eigen foto: van hem blijft alles
    bram_h, bram = await person(client, h, "bram@example.com")
    van_bram = (await upload(client, bram_h, "b.jpg", met_gps("purple")))["id"]
    await verwerk(settings)
    assert len(await rijen(settings)) == 6

    await zet(client, h, "uit")
    # De routes kijken naar photo_pref: meteen leeg, ook voordat de job iets wist
    assert len(await rijen(settings)) == 6
    assert await kaart_ids(client, h) == set()
    r = await client.get("/api/v1/photos/map", params={"bbox": VENSTER, "z": 8}, headers=h)
    assert r.status_code == 200 and r.json()["cells"] == [], r.text
    assert (await client.get("/api/v1/photos/places", headers=h)).json() == {"places": []}
    for foto in fotos:
        assert await plek(client, h, foto) == 404
    assert await klaar(settings, photo_rows.OFF_TYPE) == 1
    # Twee keer "uit" (of uit, mij, uit) geeft één job
    await zet(client, h, "uit")
    assert await klaar(settings, photo_rows.OFF_TYPE) == 1

    # De job wist in stukken (hier van 2) tot alles van Eva weg is; Bram houdt het zijne
    monkeypatch.setattr(photo_rows, "CHUNK", 2)
    stukken = []
    echt = photo_rows.wipe_chunk

    async def tel(db, user_id):
        n = await echt(db, user_id)
        stukken.append(n)
        return n

    monkeypatch.setattr(photo_rows, "wipe_chunk", tel)
    with caplog.at_level(logging.INFO, logger="platform.geo"):
        await jobs.run_pending(settings)
    assert stukken == [2, 2, 1]
    assert await rijen(settings) == {van_bram: (str(uuid.UUID(bram)), 0, AMSTERDAM, "exif")}
    assert await kaart_ids(client, bram_h) == {van_bram}
    # gps per inhoud blijft (de waarschuwing bij delen), alleen óf er een plek is
    assert sorted((await gps_van(settings)).values()) == [1] * 6
    # In het log alleen een aantal: geen gebruiker, geen plek
    tekst = "\n".join(r.getMessage() for r in caplog.records)
    assert "geo_off_done rows=5" in tekst
    for geheim in (eva, eva.replace("-", ""), "52.36", "Amsterdam"):
        assert geheim not in tekst

    # Zolang "uit": een nieuwe foto, een kopie en de keten schrijven niets voor Eva
    nieuw = (await upload(client, h, "n.jpg", met_gps("orange")))["id"]
    await verwerk(settings)
    r = await client.get("/api/v1/files/root", headers=h)
    root = r.json()["folder"]["id"]
    r = await client.post(f"/api/v1/files/{nieuw}/copy", json={"parent_id": root}, headers=h)
    assert r.status_code == 201, r.text
    await jobs.run_pending(settings)
    assert set(await rijen(settings)) == {van_bram}

    # Weer "mij": de keten in fase nodes leest alles van Eva opnieuw
    await zet(client, h, "mij")
    await jobs.run_pending(settings)
    terug = await rijen(settings)
    assert set(terug) == {*fotos, nieuw, r.json()["id"], van_bram}
    assert all(v == (eva, 0, AMSTERDAM, "exif") for k, v in terug.items() if k != van_bram)
    assert await kaart_ids(client, h) == {*fotos, nieuw, r.json()["id"]}


async def test_de_schrijver_kijkt_in_dezelfde_statement(client, admin, settings, plaatsen):
    """photo_rows.write met een eigenaar die op "uit" staat: geen rij, ook niet als photometa de
    plek al gelezen had voordat de schakelaar omging (de controle zit in de INSERT zelf)."""
    h, tokens = admin
    foto = await upload(client, h, "a.jpg", met_gps())
    await jobs.run_pending(settings)  # alleen de preview; photometa wacht nog
    sha = foto["etag"].strip('"')
    await zet(client, h, "uit")
    spot = photo_rows.Spot(52.3669444, 4.8838889, None, "exif")
    place = places.get(settings).lookup(spot.lat, spot.lon)
    async with maker(settings)() as db:
        await photo_rows.write(db, [(sha, spot, place)], replace=True)
        await db.commit()
    assert await rijen(settings) == {}
    await zet(client, h, "mij")
    async with maker(settings)() as db:
        await photo_rows.write(db, [(sha, spot, place)], replace=True)
        await db.commit()
    assert await rijen(settings) == {foto["id"]: (uid(tokens), 0, AMSTERDAM, "exif")}


async def test_weer_mij_voordat_de_job_draait_laat_alles_staan(client, admin, settings, plaatsen):
    h, tokens = admin
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    await verwerk(settings)
    await zet(client, h, "uit")
    await zet(client, h, "mij")
    await jobs.run_pending(settings)
    assert await rijen(settings) == {foto: (uid(tokens), 0, AMSTERDAM, "exif")}
    assert await kaart_ids(client, h) == {foto}


async def test_brams_uit_geldt_voor_zijn_kopie(client, admin, settings, plaatsen):
    """Een kopie door Bram is van Bram: met zijn "uit" krijgt hij geen plek, ook al heeft Eva
    "mij" (Keuze 25a en 28)."""
    h, _tokens = admin
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    await verwerk(settings)
    bram_h, bram = await person(client, h, "bram@example.com")
    await share_with(client, h, foto, user_id=bram, permission="read")
    await zet(client, bram_h, "uit")
    await jobs.run_pending(settings)
    r = await client.get("/api/v1/files/root", headers=bram_h)
    r = await client.post(
        f"/api/v1/files/{foto}/copy", json={"parent_id": r.json()["folder"]["id"]}, headers=bram_h
    )
    assert r.status_code == 201, r.text
    await jobs.run_pending(settings)
    assert set(await rijen(settings)) == {foto}  # alleen Eva's eigen foto
    assert await kaart_ids(client, bram_h) == set()


async def test_uit_mij_uit_terwijl_de_job_loopt(client, admin, settings, plaatsen, monkeypatch):
    """Review golf 3 (veilig F2): "uit" zet geo.off klaar; terwijl die loopt "mij" (de job ziet
    het en stopt) en weer "uit". Die tweede "uit" moet een nieuwe job geven, ook al loopt de
    eerste nog: anders bleven alle rijen staan terwijl locatie "uit" is."""
    h, _tokens = admin
    for i, kleur in enumerate(("red", "green", "white")):
        await upload(client, h, f"{i}.jpg", met_gps(kleur))
    await verwerk(settings)
    assert len(await rijen(settings)) == 3
    await zet(client, h, "uit")
    echt = photo_rows.location_on
    gezien: list[bool] = []

    async def tussendoor(db, user_id):
        if gezien:
            return await echt(db, user_id)
        await zet(client, h, "mij")
        gezien.append(await echt(db, user_id))
        await zet(client, h, "uit")
        return gezien[0]

    monkeypatch.setattr(photo_rows, "location_on", tussendoor)
    await jobs.run_pending(settings)
    assert gezien == [True]
    assert await rijen(settings) == {}
    async with maker(settings)() as db:
        rij = await db.execute(select(Job.status).where(Job.type == photo_rows.OFF_TYPE))
        assert sorted(s for (s,) in rij) == ["done", "done"]


async def test_de_opruimronde_zet_geo_off_klaar_als_er_nog_rijen_zijn(
    client, admin, settings, plaatsen
):
    """Een geo.off die definitief mislukte (of nooit klaarstond) laat rijen staan; de opruimronde
    (bij de start en daarna elk uur) zet geo.off klaar voor wie "uit" heeft en nog rijen heeft,
    en voor niemand anders: ook niet voor Bram, die rijen en een rij in photo_pref heeft (hij
    zette herinneringen uit), met locatie "mij" (nacontrole golf 3)."""
    h, tokens = admin
    await upload(client, h, "a.jpg", met_gps())
    bram_h, _bram = await person(client, h, "bram@example.com")
    van_bram = (await upload(client, bram_h, "b.jpg", met_gps("purple")))["id"]
    await verwerk(settings)
    r = await client.patch("/api/v1/me/photo-settings", json={"memories": False}, headers=bram_h)
    assert (r.status_code, r.json()["location"]) == (200, "mij"), r.text
    await zet(client, h, "uit")
    async with maker(settings)() as db:
        await db.execute(delete(Job).where(Job.type == photo_rows.OFF_TYPE))
        await db.commit()
        assert len((await db.execute(select(PhotoPref.user_id))).all()) == 2
    assert len(await rijen(settings)) == 2
    assert photo_rows.SWEEP in jobs._housekeeping
    assert await photo_rows.sweep_off(settings) == 1
    async with maker(settings)() as db:
        klaar_voor = await db.scalars(select(Job.payload).where(Job.type == photo_rows.OFF_TYPE))
        assert [p["user"] for p in klaar_voor] == [str(uid(tokens))]
    assert await photo_rows.sweep_off(settings) == 0  # staat al klaar
    await jobs.run_pending(settings)
    assert set(await rijen(settings)) == {van_bram}
    assert await photo_rows.sweep_off(settings) == 0  # niets meer te wissen
    await zet(client, bram_h, "uit")
    async with maker(settings)() as db:
        await db.execute(delete(Job).where(Job.type == photo_rows.OFF_TYPE))
        await db.commit()
    assert await photo_rows.sweep_off(settings) == 1
