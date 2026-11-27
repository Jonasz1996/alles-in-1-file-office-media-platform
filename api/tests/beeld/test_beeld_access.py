"""Wie wat vindt met zoeken op beeld (bouwontwerp fase 6, X3; Keuze 79; X3b).

De wereld van §4.2 in het klein: Eva deelt een map met Bram. Bram vindt Eva's gedeelde foto niet
via beeld (alleen eigen foto's); zijn eigen kopie wel, zonder iets van Eva; like op een foto van
Eva geeft 404; Eva's foto in de prullenbak niet, na terugzetten wel; een gast krijgt 403; een
openbare link heeft geen route (test_beeld_events.test_public_never_search).
"""

from __future__ import annotations

import uuid

import pytest

from tests.beeld import nep
from tests.beeld.nep import foto
from tests.test_files import mkdir, root
from tests.test_shares import person, share_with

pytestmark = pytest.mark.anyio


async def test_alleen_eigen_fotos_ook_met_een_deling(client, mensen, beeld):
    (eva, _), (bram, bram_id) = mensen
    fake, settings = beeld
    for h in (eva, bram):
        await nep.beeld_aan(client, h)
    await nep.run_jobs(settings)
    eva_root = (await root(client, eva))["folder"]["id"]
    gedeeld = await mkdir(client, eva, eva_root, "Gedeeld")
    rood = await nep.upload(client, eva, "rood.jpg", nep.photo(nep.KLEUREN["rood"]), gedeeld)
    rood["sha"] = await nep.sha_of(settings, rood["id"])
    nep.preview(settings, rood["sha"], nep.KLEUREN["rood"])
    await share_with(client, eva, gedeeld, user_id=bram_id, permission="read")
    eigen = await foto(client, bram, settings, "blauw.jpg", "blauw")
    await nep.embed_all(settings)
    # gewoon zoeken vindt de gedeelde foto wel
    r = await client.get("/api/v1/search", params={"q": "rood"}, headers=bram)
    assert rood["id"] in [h["node"]["id"] for h in r.json()["results"]]
    # zoeken op beeld alleen in het eigen bestand
    assert await nep.ids(client, bram, "rood") == [eigen["id"]]
    r = await nep.zoek(client, bram, like=rood["id"])
    assert (r.status_code, nep.code(r)) == (404, "not_found")
    # in de prullenbak van Eva: weg, terug: weer daar
    assert await nep.ids(client, eva, "rood") == [rood["id"]]
    assert (await client.delete(f"/api/v1/files/{rood['id']}", headers=eva)).status_code == 204
    assert await nep.ids(client, eva, "rood") == []
    r = await nep.zoek(client, eva, like=rood["id"])
    assert r.status_code == 404
    assert (
        await client.post(f"/api/v1/files/{rood['id']}/restore", headers=eva)
    ).status_code == 200
    assert await nep.ids(client, eva, "rood") == [rood["id"]]


async def test_elke_vraag_gaat_naar_het_eigen_bestand(client, mensen, beeld):
    """rank krijgt altijd de eigenaar en de fd van wie vraagt, nooit die van een ander."""
    (eva, eva_id), (bram, bram_id) = mensen
    fake, settings = beeld
    for h in (eva, bram):
        await nep.beeld_aan(client, h)
    await nep.run_jobs(settings)
    await foto(client, eva, settings, "rood.jpg", "rood")
    await foto(client, bram, settings, "groen.jpg", "groen", seed=1)
    await nep.embed_all(settings)
    fake.ranks.clear()
    await nep.ids(client, eva, "rood")
    await nep.ids(client, bram, "rood")
    assert [a.owner for a in fake.ranks if a.k] == [uuid.UUID(eva_id).hex, uuid.UUID(bram_id).hex]


async def test_een_gast_krijgt_403(client, admin, beeld):
    jonas, _ = admin
    fake, settings = beeld
    gast, _ = await person(client, jonas, "gast@example.nl", role="guest")
    r = await nep.zoek(client, gast, "rood")
    assert r.status_code == 403, r.text
    assert fake.ranks == []
    r = await client.delete("/api/v1/admin/search/beeld", headers=gast)
    assert r.status_code == 403


async def test_zonder_inlog_401(client, beeld):
    r = await client.get("/api/v1/search", params={"mode": "image", "q": "rood"})
    assert r.status_code == 401
    r = await client.get("/api/v1/photos/beeld")
    assert r.status_code == 401
