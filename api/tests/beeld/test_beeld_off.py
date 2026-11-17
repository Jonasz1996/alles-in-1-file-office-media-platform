"""Beeld uit (bouwontwerp fase 6, X3; Vraag 11, Keuze 82, K1-08; X3b), en beeld onzichtbaar
zolang de analyse uit staat (FACTS van golf 6: 0.8.0 blijft hetzelfde tot I2).

- zonder keuze (photo_pref.beeld NULL) is beeld uit: geen vector, geen bestand, 409 beeld_off;
- met de analyse uit (geen ANALYSE in .env, de standaard): enable() meldt niets aan, elke route
  van X3 antwoordt zoals in 0.8.0 (501 not_built, 409 beeld_not_installed), geen rij in analysis
  voor embed, geen job in de rij analyse en geen bestand onder DATA_ROOT;
- de job uit doet niets meer als de gebruiker intussen weer aan zette.

De gebeurtenissen zelf (uit, weer aan, de beheerder, wissen) staan in test_beeld_events.py.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app import analysis, jobs, prefs
from app.analysis import Step
from app.beeld import jobs as bjobs
from app.beeld import vecfile
from app.models import Analysis, BlobEmbed, Job, PhotoPref
from tests.beeld import nep
from tests.beeld.nep import foto

pytestmark = pytest.mark.anyio

# De soorten jobs van beeld, met hun rij en grens (de pomp van de rij analyse is van
# analysis.register_step). tests/test_jobs_lanes.py houdt de soorten zonder de analyse vast.
SOORTEN = {
    analysis.pump_type(bjobs.LANE): ("analyse", analysis.PUMP_TIMEOUT),
    bjobs.VEC: ("index", 600.0),
    bjobs.ON: ("default", 600.0),
    bjobs.OFF: ("default", 600.0),
    bjobs.WIPE: ("default", 600.0),
}


async def test_zonder_keuze_is_beeld_uit(client, mensen, beeld):
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    r = await client.get("/api/v1/me/photo-settings", headers=eva)
    assert r.json()["beeld"] == "uit"
    async with nep.maker(settings)() as db:
        assert await db.get(PhotoPref, uuid.UUID(eva_id)) is None
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    assert await nep.row(settings, rood["sha"]) == ("skip", None)
    assert await nep.vectors(settings) == set()
    assert not vecfile.path_for(settings.beeld_dir, uuid.UUID(eva_id)).exists()
    r = await nep.zoek(client, eva, "rood")
    assert (r.status_code, nep.code(r)) == (409, "beeld_off")
    assert fake.embeds == [] and fake.ranks == []


async def test_uit_terwijl_de_job_wacht_en_weer_aan(client, mensen, beeld):
    """Uit en meteen weer aan voordat de job uit draait: de job ziet aan en wist niets."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    await nep.beeld_aan(client, eva, "uit")
    await nep.beeld_aan(client, eva, "aan")
    await nep.run_jobs(settings)
    assert await nep.vectors(settings) == {rood["sha"]}
    await nep.bestanden(settings)
    assert await nep.ids(client, eva, "rood") == [rood["id"]]
    del eva_id


async def test_met_de_analyse_uit_niets_van_beeld(client, admin, settings):
    """De standaard van 0.8.0: geen ANALYSE in .env. Niets van beeld meldt zich aan, elke route
    antwoordt zoals in 0.8.0, en na een upload met preview en de backfill van de index is er
    geen rij voor embed, geen job in de rij analyse en geen map voor beeld onder DATA_ROOT."""
    jonas, _ = admin
    assert not settings.analyse_on
    assert bjobs.enable(settings) is False
    await bjobs.on_start(settings)
    assert bjobs.enabled() is None
    assert Step.EMBED not in analysis.registered()
    assert "beeld" not in prefs._switches
    assert not set(SOORTEN) & set(jobs._kinds)
    await bjobs.sweep(settings)

    for method, path, params in (
        ("GET", "/api/v1/photos/beeld", None),
        ("DELETE", "/api/v1/admin/search/beeld", None),
        ("GET", "/api/v1/search", {"mode": "image", "q": "rood"}),
        ("GET", "/api/v1/search", {"like": str(uuid.uuid4())}),
    ):
        r = await client.request(method, path, params=params, headers=jonas)
        assert r.status_code == 501, (path, r.text)
        assert r.json()["detail"]["code"] == "not_built"
        assert r.json()["detail"]["params"] == {"package": "X3"}
    r = await client.patch("/api/v1/me/photo-settings", json={"beeld": "aan"}, headers=jonas)
    assert (r.status_code, nep.code(r)) == (409, "beeld_not_installed")
    r = await client.get("/api/v1/admin/search", headers=jonas)
    assert r.json()["beeld"] is None
    r = await client.patch("/api/v1/admin/search", json={"beeld": False}, headers=jonas)
    assert r.status_code == 501, r.text

    node = await nep.upload(client, jonas, "rood.jpg", nep.photo(nep.KLEUREN["rood"]))
    sha = await nep.sha_of(settings, node["id"])
    nep.preview(settings, sha, nep.KLEUREN["rood"])
    await analysis.backfill_job(settings, {"after": ""})
    await jobs.run_pending(settings, lane="default")
    await jobs.run_pending(settings, lane="index")
    async with nep.maker(settings)() as db:
        embed = await db.scalar(
            sa.select(sa.func.count())
            .select_from(Analysis)
            .where(Analysis.step == Step.EMBED.value)
        )
        types = (await db.scalars(sa.select(Job.type))).all()
        beeld_jobs = await db.scalar(
            sa.select(sa.func.count())
            .select_from(Job)
            .where(Job.type.in_((bjobs.VEC, bjobs.ON, bjobs.OFF, bjobs.WIPE)))
        )
        vectors = await db.scalar(sa.select(sa.func.count()).select_from(BlobEmbed))
    assert (embed, beeld_jobs, vectors) == (0, 0, 0)
    assert analysis.pump_type(bjobs.LANE) not in types
    assert not [t for t in types if jobs.lane_of(t) == bjobs.LANE]
    assert not settings.beeld_dir.exists()
    assert not any(p.name.startswith("beeld") for p in (settings.data_dir / "cache").glob("*"))


async def test_de_soorten_alleen_zolang_beeld_aangemeld_is(beeld):
    """Met beeld aangemeld: precies deze soorten, in deze rijen, met deze grenzen. disable()
    haalt ze weer weg, zodat een volgende test (ook tests/test_jobs_lanes.py) 0.8.0 ziet: in
    de volle suite bleef beeld.on anders staan na de tests van beeld."""
    gezien = {t: (jobs._kinds[t].lane, jobs._kinds[t].timeout) for t in SOORTEN}
    assert gezien == SOORTEN
    bjobs.disable()
    assert not set(SOORTEN) & set(jobs._kinds)
    assert {jobs.lane_of(t) for t in SOORTEN} == {"default"}
    assert bjobs.enable(beeld[1])
    assert {t: (jobs._kinds[t].lane, jobs._kinds[t].timeout) for t in SOORTEN} == SOORTEN
