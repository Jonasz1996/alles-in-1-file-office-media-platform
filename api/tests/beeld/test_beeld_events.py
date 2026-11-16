"""De gebeurtenissen van §3.9 voor blob_embed, beeld_owner en de bestanden per eigenaar
(bouwontwerp fase 6, X3; Keuzes 69, 76, 82; X3b). Elke test heet naar zijn rij.

| gebeurtenis | test |
|---|---|
| upload, daarna te vinden | test_beeld_upload_then_found |
| upload door een ander (Keuze 69) | test_beeld_other_uploader |
| kopie bij dezelfde eigenaar | test_beeld_copy_same_owner |
| kopie naar een andere eigenaar | test_beeld_copy_other_owner |
| andere eigenaar (owner_id) | test_beeld_owner_change |
| nieuwe inhoud (versie) | test_beeld_replace |
| prullenbak en terug | test_beeld_trash |
| definitief weg (purge) | test_beeld_purge |
| gebruiker weg | test_beeld_user_delete |
| beeld uit (K1-08) | test_beeld_off |
| beeld weer aan | test_beeld_on_again |
| de beheerder zet beeld uit | test_beeld_admin_off |
| de beheerder wist de beeldindex | test_beeld_wipe |
| een nieuwe versie van de stap | test_beeld_version |
| te laat, weg, geen model | test_beeld_fallback |
| een openbare link zoekt nooit | test_public_never_search |
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app import analysis, jobs
from app.analyse import monitor
from app.analysis import Step
from app.beeld import jobs as bjobs
from app.beeld import vecfile
from app.models import Node
from app.routers.public import router as public_router
from tests.beeld import nep
from tests.beeld.nep import foto
from tests.test_files import mkdir, root
from tests.test_public import bearer, new_link, open_link, tus_create, tus_send
from tests.test_shares import share_with
from tests.test_user_delete import block_and_delete

pytestmark = pytest.mark.anyio

ADMIN_SEARCH = "/api/v1/admin/search"


async def aan(client, settings, *headers) -> None:
    for h in headers:
        await nep.beeld_aan(client, h)
    await nep.run_jobs(settings)


def file_of(settings, user_id: str):
    return vecfile.path_for(settings.beeld_dir, uuid.UUID(user_id))


async def test_beeld_upload_then_found(client, mensen, beeld):
    """Beeld aan, een foto erbij: de backfill zet hem in de rij, de pomp maakt de vector, het
    bestand van Eva komt er, en "rood" vindt de rode foto eerst."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await aan(client, settings, eva)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    blauw = await foto(client, eva, settings, "blauw.jpg", "blauw", seed=1)
    assert await nep.embed_all(settings) == 2
    assert await nep.row(settings, rood["sha"]) == ("done", None)
    assert await nep.vectors(settings) == {rood["sha"], blauw["sha"]}
    found = await nep.owner(settings, eva_id)
    assert found is not None and found.built_rows == 2 and found.built_gen == found.gen
    assert file_of(settings, eva_id).exists()
    assert await nep.ids(client, eva, "rood") == [rood["id"], blauw["id"]]
    assert await nep.ids(client, eva, "blauw") == [blauw["id"], rood["id"]]


async def test_beeld_other_uploader(client, mensen, beeld):
    """Keuze 69: Bram (beeld uit) zet een foto in de map van Eva, en een gast via een
    uploadlink: geen vector, niet in het bestand van Eva. Zet Bram beeld aan, dan telt zijn foto
    wel (de job aan zet hem terug in de rij, het bestand van Eva opnieuw); die via de link nooit.
    Zet Bram beeld weer uit, dan is zijn foto meteen weg bij Eva, al vóór de jobs. Gewoon zoeken
    vindt ze allebei wel."""
    (eva, eva_id), (bram, bram_id) = mensen
    fake, settings = beeld
    await aan(client, settings, eva)
    eigen = await foto(client, eva, settings, "rood.jpg", "rood")
    eva_root = (await root(client, eva))["folder"]["id"]
    gedeeld = await mkdir(client, eva, eva_root, "Gedeeld")
    await share_with(client, eva, gedeeld, user_id=bram_id, permission="write")
    groen = await nep.upload(client, bram, "groen.jpg", nep.photo(nep.KLEUREN["groen"]), gedeeld)
    groen["sha"] = await nep.sha_of(settings, groen["id"])
    nep.preview(settings, groen["sha"], nep.KLEUREN["groen"])
    link = await new_link(client, eva, gedeeld, permission="upload")
    key = (await open_link(client, link))["key"]
    data = nep.photo(nep.KLEUREN["blauw"])
    r = await tus_create(client, key, "gast.jpg", len(data))
    assert r.status_code == 201, r.text
    r = await tus_send(client, key, r.headers["location"], 0, data)
    assert r.status_code == 204, r.text
    async with nep.maker(settings)() as db:
        gast_sha = await db.scalar(
            sa.select(Node.blob_id).where(Node.name == "gast.jpg", Node.dead.is_(False))
        )
    nep.preview(settings, gast_sha, nep.KLEUREN["blauw"])

    await nep.embed_all(settings)
    assert await nep.vectors(settings) == {eigen["sha"]}
    assert await nep.row(settings, groen["sha"]) == ("skip", None)
    assert await nep.row(settings, gast_sha) == ("skip", None)
    assert await nep.ids(client, eva, "groen") == [eigen["id"]]
    r = await client.get("/api/v1/search", params={"q": "groen"}, headers=eva)
    assert [h["node"]["id"] for h in r.json()["results"]] == [groen["id"]]

    await aan(client, settings, bram)
    await nep.embed_all(settings)
    assert await nep.vectors(settings) == {eigen["sha"], groen["sha"]}
    assert await nep.row(settings, gast_sha) == ("skip", None)
    assert await nep.ids(client, eva, "groen") == [groen["id"], eigen["id"]]
    assert gast_sha not in await nep.vectors(settings)

    # Bram zet beeld weer uit: zijn foto valt meteen weg bij Eva, ook nu haar bestand en de cache
    # van 60 s hem nog hebben (Keuze 69 bij het zoeken, beeld_rows.origin_ok in _select; review
    # golf 6, G6-4); en ook nadat de jobs het bestand van Eva opnieuw maakten.
    ranks = len(fake.ranks)
    oud = file_of(settings, eva_id).read_bytes()
    await nep.beeld_aan(client, bram, "uit")
    assert await nep.ids(client, eva, "groen") == [eigen["id"]]
    assert len(fake.ranks) == ranks  # uit de cache, niet opnieuw gerangschikt
    assert file_of(settings, eva_id).read_bytes() == oud
    await nep.run_jobs(settings)
    await nep.bestanden(settings)
    assert file_of(settings, eva_id).read_bytes() != oud
    assert await nep.ids(client, eva, "groen") == [eigen["id"]]


async def test_beeld_copy_same_owner(client, mensen, beeld):
    """Een kopie bij dezelfde eigenaar: er is al een vector (per inhoud), dus geen nieuwe
    embed_image; na het bestand vindt "rood" allebei."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await aan(client, settings, eva)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    calls = len(fake.embeds)
    eva_root = (await root(client, eva))["folder"]["id"]
    r = await client.post(
        f"/api/v1/files/{rood['id']}/copy",
        json={"parent_id": eva_root, "name": "kopie.jpg"},
        headers=eva,
    )
    assert r.status_code == 201, r.text
    kopie = r.json()["id"]
    await nep.embed_all(settings)
    assert len(fake.embeds) == calls
    assert set(await nep.ids(client, eva, "rood")) == {rood["id"], kopie}
    assert (await nep.owner(settings, eva_id)).built_rows == 2


async def test_beeld_copy_other_owner(client, mensen, beeld):
    """Bram kopieert een foto van Eva (gedeeld om te lezen) naar zijn eigen map: de vector is er
    al; Bram vindt zijn kopie als hij beeld aan heeft. Niets van Eva gaat mee: haar andere foto
    nooit, en de kopie is van Bram (zijn bestand, zijn keuze)."""
    (eva, _), (bram, bram_id) = mensen
    fake, settings = beeld
    await aan(client, settings, eva, bram)
    eva_root = (await root(client, eva))["folder"]["id"]
    album = await mkdir(client, eva, eva_root, "Vakantie")
    rood = await nep.upload(client, eva, "rood.jpg", nep.photo(nep.KLEUREN["rood"]), album)
    rood["sha"] = await nep.sha_of(settings, rood["id"])
    nep.preview(settings, rood["sha"], nep.KLEUREN["rood"])
    await foto(client, eva, settings, "blauw.jpg", "blauw", seed=1)
    await share_with(client, eva, album, user_id=bram_id, permission="read")
    await nep.embed_all(settings)
    calls = len(fake.embeds)
    bram_root = (await root(client, bram))["folder"]["id"]
    r = await client.post(
        f"/api/v1/files/{rood['id']}/copy", json={"parent_id": bram_root}, headers=bram
    )
    assert r.status_code == 201, r.text
    kopie = r.json()["id"]
    await nep.embed_all(settings)
    assert len(fake.embeds) == calls
    assert await nep.ids(client, bram, "rood") == [kopie]
    assert await nep.ids(client, bram, "blauw") == [kopie]

    await nep.beeld_aan(client, bram, "uit")
    await nep.run_jobs(settings)
    assert nep.code(await nep.zoek(client, bram, "rood")) == "beeld_off"
    assert not file_of(settings, bram_id).exists()
    assert rood["sha"] in await nep.vectors(settings)  # Eva heeft hem nog


async def test_beeld_owner_change(client, mensen, beeld):
    """Een andere eigenaar: de oude vindt de foto meteen niet meer (own_live), de nieuwe na zijn
    bestand (de triggers zetten allebei gen hoger), met zijn eigen keuze."""
    (eva, eva_id), (bram, bram_id) = mensen
    fake, settings = beeld
    await aan(client, settings, eva, bram)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    groen = await foto(client, eva, settings, "groen.jpg", "groen", seed=1)
    await nep.embed_all(settings)
    before = {u: (await nep.owner(settings, u)) for u in (eva_id, bram_id)}
    async with nep.maker(settings)() as db:
        await db.execute(
            sa.update(Node)
            .where(Node.id == uuid.UUID(rood["id"]))
            .values(owner_id=uuid.UUID(bram_id))
        )
        await db.commit()
    assert await nep.ids(client, eva, "rood") == [groen["id"]]
    after = await nep.owner(settings, eva_id)
    assert after.gen > before[eva_id].gen
    bram_before = before[bram_id].gen if before[bram_id] is not None else -1
    assert (await nep.owner(settings, bram_id)).gen > bram_before
    await nep.bestanden(settings)
    assert await nep.ids(client, bram, "rood") == [rood["id"]]
    assert await nep.ids(client, eva, "rood") == [groen["id"]]
    assert (await nep.owner(settings, eva_id)).built_rows == 1


async def test_beeld_replace(client, mensen, beeld):
    """Nieuwe inhoud voor een foto: de oude inhoud nooit meer bij deze node, ook niet zolang het
    bestand nog de oude vector heeft (het begin van de sha per rij); na de stap voor de nieuwe sha
    en het bestand vindt "blauw" hem.

    De eerste zoekvraag na de nieuwe inhoud komt uit de warme cache van 60 s (geen nieuwe rank)
    met het oude bestand: alleen het filter op het begin van de sha (§3.9) houdt de node dan weg
    (review golf 6, G6-5)."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await aan(client, settings, eva)
    node = await foto(client, eva, settings, "foto.jpg", "rood")
    oranje = await foto(client, eva, settings, "oranje.jpg", "oranje", seed=1)
    await nep.embed_all(settings)
    assert await nep.ids(client, eva, "rood") == [node["id"], oranje["id"]]
    ranks = len(fake.ranks)
    oud = file_of(settings, eva_id).read_bytes()
    data = nep.photo(nep.KLEUREN["blauw"], seed=9)
    r = await client.put(f"/api/v1/files/{node['id']}/content", content=data, headers=eva)
    assert r.status_code == 200, r.text
    nieuw = await nep.sha_of(settings, node["id"])
    assert nieuw != node["sha"]
    assert await nep.ids(client, eva, "rood") == [oranje["id"]]  # het oude bestand
    assert len(fake.ranks) == ranks  # uit de cache, niet opnieuw gerangschikt
    assert file_of(settings, eva_id).read_bytes() == oud
    await nep.bestanden(settings)
    assert await nep.ids(client, eva, "rood") == [oranje["id"]]  # nog geen vector
    nep.preview(settings, nieuw, nep.KLEUREN["blauw"])
    await nep.embed_all(settings)
    assert await nep.ids(client, eva, "blauw") == [node["id"], oranje["id"]]
    assert await nep.ids(client, eva, "rood") == [oranje["id"], node["id"]]


async def test_beeld_trash(client, mensen, beeld):
    """Naar de prullenbak: meteen weg (own_live), het bestand blijft; terug: meteen terug."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await aan(client, settings, eva)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    groen = await foto(client, eva, settings, "groen.jpg", "groen", seed=1)
    await nep.embed_all(settings)
    built = (await nep.owner(settings, eva_id)).built_gen
    assert (await client.delete(f"/api/v1/files/{rood['id']}", headers=eva)).status_code == 204
    assert await nep.ids(client, eva, "rood") == [groen["id"]]
    r = await client.post(f"/api/v1/files/{rood['id']}/restore", headers=eva)
    assert r.status_code == 200, r.text
    assert await nep.ids(client, eva, "rood") == [rood["id"], groen["id"]]
    found = await nep.owner(settings, eva_id)
    assert (found.built_gen, found.built_rows) == (built, 2)


async def test_beeld_purge(client, mensen, beeld):
    """Definitief wissen: de vector gaat met de blob (cascade), gen omhoog en het bestand
    opnieuw zonder die foto (K1-08)."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await aan(client, settings, eva)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    groen = await foto(client, eva, settings, "groen.jpg", "groen", seed=1)
    await nep.embed_all(settings)
    assert (await client.delete(f"/api/v1/files/{rood['id']}", headers=eva)).status_code == 204
    r = await client.delete(f"/api/v1/trash/{rood['id']}", headers=eva)
    assert r.status_code == 204, r.text
    assert await nep.ids(client, eva, "rood") == [groen["id"]]  # own_live, tot het bestand
    await nep.bestanden(settings)
    assert await nep.vectors(settings) == {groen["sha"]}
    found = await nep.owner(settings, eva_id)
    assert found.built_rows == 1 and found.built_gen == found.gen
    assert await nep.ids(client, eva, "rood") == [groen["id"]]


async def test_beeld_user_delete(client, admin, mensen, beeld):
    """Een gebruiker wissen: beeld_owner weg (cascade), zijn bestand weg bij het opruimwerk en
    forget naar de container."""
    jonas, _ = admin
    _, (bram, bram_id) = mensen
    fake, settings = beeld
    await aan(client, settings, bram)
    await foto(client, bram, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    assert file_of(settings, bram_id).exists()
    await block_and_delete(client, jonas, bram_id)
    await nep.run_jobs(settings)
    assert await nep.owner(settings, bram_id) is None
    await bjobs.sweep(settings)
    assert not file_of(settings, bram_id).exists()
    assert uuid.UUID(bram_id).hex in fake.forgets
    assert await nep.vectors(settings) == set()


async def test_beeld_off(client, mensen, beeld):
    """Beeld uit (K1-08): het bestand weg, forget, de vectoren die niemand met beeld aan nog
    wil weg (die van Bram met dezelfde foto blijft), de stappen op skip, meteen 409."""
    (eva, eva_id), (bram, _) = mensen
    fake, settings = beeld
    await aan(client, settings, eva, bram)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    groen = await foto(client, eva, settings, "groen.jpg", "groen", seed=1)
    van_bram = await foto(client, bram, settings, "ook groen.jpg", "groen", seed=1)
    assert van_bram["sha"] == groen["sha"]
    await nep.embed_all(settings)
    await nep.beeld_aan(client, eva, "uit")
    r = await nep.zoek(client, eva, "rood")
    assert (r.status_code, nep.code(r)) == (409, "beeld_off")
    await nep.run_jobs(settings)
    assert not file_of(settings, eva_id).exists()
    assert fake.forgets == [uuid.UUID(eva_id).hex]
    assert await nep.vectors(settings) == {groen["sha"]}
    assert await nep.row(settings, rood["sha"]) == ("skip", None)
    assert await nep.ids(client, bram, "groen") == [van_bram["id"]]
    found = await nep.owner(settings, eva_id)
    assert (found.built_gen, found.built_rows, found.built_at) == (0, 0, None)


async def test_beeld_on_again(client, mensen, beeld):
    """Weer aan: de stappen terug op wait, GET /photos/beeld zegt bezig met done, en na de
    pomp en het bestand is alles weer te vinden.

    Uit wist ook de gerangschikte lijsten van Eva in de cache van 60 s (K1-08). Het nieuwe
    bestand heeft dezelfde gen als het oude (gen telt nodes, geen vectoren), dus zonder dat gaf
    "rood" vlak na weer aan nog de lijst van vóór uit, uit vectoren die er niet meer zijn
    (nacontrole golf 6)."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await aan(client, settings, eva)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    groen = await foto(client, eva, settings, "groen.jpg", "groen", seed=1)
    await nep.embed_all(settings)
    assert await nep.ids(client, eva, "rood") == [rood["id"], groen["id"]]
    gen = (await nep.owner(settings, eva_id)).built_gen
    await nep.beeld_aan(client, eva, "uit")
    await nep.run_jobs(settings)
    assert await nep.vectors(settings) == set()
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings, lane="default")
    assert await nep.row(settings, rood["sha"]) == ("wait", None)
    r = await client.get("/api/v1/photos/beeld", headers=eva)
    assert r.json() == {"state": "bezig", "done": 0.0}
    await nep.bestanden(settings)
    found = await nep.owner(settings, eva_id)
    assert (found.built_gen, found.built_rows) == (gen, 0)  # dezelfde sleutel in de cache
    assert await nep.ids(client, eva, "rood") == []
    await nep.embed_all(settings)
    assert await nep.ids(client, eva, "rood") == [rood["id"], groen["id"]]
    r = await client.get("/api/v1/photos/beeld", headers=eva)
    assert r.json() == {"state": "ok", "done": None}


async def test_beeld_admin_off(client, admin, mensen, beeld):
    """De beheerder zet beeld uit: 409 beeld_off voor iedereen, aanzetten kan niet, de stap
    wacht (Defer); de vectoren blijven tot wissen. Weer aan: alles werkt weer."""
    jonas, _ = admin
    (eva, _), (bram, _) = mensen
    fake, settings = beeld
    await aan(client, settings, eva)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    r = await client.patch(ADMIN_SEARCH, json={"beeld": False}, headers=jonas)
    assert r.status_code == 200, r.text
    assert r.json()["beeld"]["on"] is False
    r = await nep.zoek(client, eva, "rood")
    assert (r.status_code, nep.code(r)) == (409, "beeld_off")
    r = await client.patch("/api/v1/me/photo-settings", json={"beeld": "aan"}, headers=bram)
    assert (r.status_code, nep.code(r)) == (409, "beeld_off")
    assert (await client.get("/api/v1/photos/beeld", headers=eva)).json()["state"] == "beheer_uit"
    groen = await foto(client, eva, settings, "groen.jpg", "groen", seed=1)
    await nep.backfill(settings)
    with pytest.raises(jobs.Defer):
        await analysis.pump(settings, bjobs.LANE)
    assert await nep.row(settings, groen["sha"]) == ("wait", None)
    assert await nep.vectors(settings) == {rood["sha"]}
    r = await client.patch(ADMIN_SEARCH, json={"beeld": True}, headers=jonas)
    assert r.status_code == 200, r.text
    await nep.embed_all(settings)
    assert await nep.ids(client, eva, "groen") == [groen["id"], rood["id"]]


async def test_beeld_wipe(client, admin, mensen, beeld):
    """De beheerder wist (DELETE /admin/search/beeld, 202; een gebruiker 403): alle vectoren,
    alle bestanden, forget voor elke eigenaar, en de cache van 60 s in de app; de stappen terug
    op wait en de backfill vult alles weer.

    Zonder de cache te wissen gaf "rood" na het nieuwe (lege) bestand met dezelfde gen nog de
    lijst van vóór het wissen, zolang die 60 s niet om waren (nacontrole golf 6)."""
    jonas, _ = admin
    (eva, eva_id), (bram, bram_id) = mensen
    fake, settings = beeld
    await aan(client, settings, eva, bram)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    await foto(client, bram, settings, "groen.jpg", "groen", seed=1)
    await nep.embed_all(settings)
    assert await nep.ids(client, eva, "rood") == [rood["id"]]
    gen = (await nep.owner(settings, eva_id)).built_gen
    assert (await client.delete(f"{ADMIN_SEARCH}/beeld", headers=eva)).status_code == 403
    r = await client.delete(f"{ADMIN_SEARCH}/beeld", headers=jonas)
    assert r.status_code == 202, r.text
    await nep.run_jobs(settings, lane="default")
    assert await nep.vectors(settings) == set()
    assert sorted(settings.beeld_dir.iterdir()) == []
    assert set(fake.forgets) == {uuid.UUID(eva_id).hex, uuid.UUID(bram_id).hex}
    assert await nep.row(settings, rood["sha"]) == ("wait", None)
    r = await nep.zoek(client, eva, "rood")
    assert (r.status_code, r.json()["fallback"]) == (200, "slow")
    await nep.bestanden(settings)
    found = await nep.owner(settings, eva_id)
    assert (found.built_gen, found.built_rows) == (gen, 0)  # dezelfde sleutel in de cache
    assert await nep.ids(client, eva, "rood") == []
    await nep.embed_all(settings)
    assert await nep.ids(client, eva, "rood") == [rood["id"]]


async def test_beeld_version(client, mensen, beeld, monkeypatch):
    """Een nieuwe versie van de stap (een nieuw model): recover zet alles terug op wait, het
    oude bestand telt niet meer (terug naar gewoon zoeken), en wat al opnieuw klaar is, is te
    vinden met indexing."""
    (eva, _), _ = mensen
    fake, settings = beeld
    await aan(client, settings, eva)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    groen = await foto(client, eva, settings, "groen.jpg", "groen", seed=1)
    await nep.embed_all(settings)
    monkeypatch.setitem(analysis.VERSIONS, Step.EMBED, 2)
    await analysis.recover(settings)
    assert await nep.row(settings, rood["sha"]) == ("wait", None)
    r = await nep.zoek(client, eva, "rood")
    assert (r.status_code, r.json()["fallback"]) == (200, "slow")
    await nep.run_jobs(settings)
    body = (await nep.zoek(client, eva, "rood")).json()
    assert (body["results"], body["mode"], body["indexing"]) == ([], "image", {"done": 0.0})
    await nep.embed_all(settings)
    assert await nep.ids(client, eva, "rood") == [rood["id"], groen["id"]]


async def test_beeld_fallback(client, mensen, beeld):
    """Te laat, de container weg, of geen model: dezelfde vraag geeft de treffers van gewoon
    zoeken (hier op de naam), met fallback slow, down of no_model; de stap wacht zonder
    container (Defer 300) en zonder model (Defer 3600)."""
    (eva, _), _ = mensen
    fake, settings = beeld
    await aan(client, settings, eva)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    slow = settings.model_copy(update={"beeld_deadline_ms": 50})
    from app.config import get_settings
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: slow
    fake.rank_delay = 1.0
    r = await nep.zoek(client, eva, "rood")
    body = r.json()
    assert (r.status_code, body["mode"], body["fallback"]) == (200, "text", "slow"), body
    assert [h["node"]["id"] for h in body["results"]] == [rood["id"]]
    fake.rank_delay = 0.0
    app.dependency_overrides[get_settings] = lambda: settings

    groen = await foto(client, eva, settings, "groen.jpg", "groen", seed=1)
    await nep.backfill(settings)
    monitor._status = monitor.AnalyseStatus("down", "gone")
    body = (await nep.zoek(client, eva, "rood")).json()
    assert (body["mode"], body["fallback"]) == ("text", "down")
    with pytest.raises(jobs.Defer) as defer:
        await analysis.pump(settings, bjobs.LANE)
    assert defer.value.seconds == monitor.DEFER_DOWN

    fake.fake.beeld = None
    monitor._status = await monitor.check(settings)
    body = (await nep.zoek(client, eva, "rood")).json()
    assert (body["mode"], body["fallback"]) == ("text", "no_model")
    with pytest.raises(jobs.Defer) as defer:
        await analysis.pump(settings, bjobs.LANE)
    assert defer.value.seconds == monitor.DEFER_MODEL
    assert await nep.row(settings, groen["sha"]) == ("wait", None)
    r = await nep.zoek(client, eva, like=rood["id"])
    assert (r.status_code, r.json()["results"], r.json()["fallback"]) == (200, [], "no_model")


async def test_public_never_search(client, admin, mensen, beeld):
    """Een openbare link zoekt nooit: geen route onder /public met zoeken of beeld, en de
    sleutel van een link mag niet op /search."""
    (eva, _), _ = mensen
    fake, settings = beeld
    await aan(client, settings, eva)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    paths = [getattr(r, "path", "") for r in public_router.routes]
    assert paths and not [p for p in paths if "search" in p or "beeld" in p or "like" in p]
    link = await new_link(client, eva, rood["id"], permission="read")
    key = (await open_link(client, link))["key"]
    for params in ({"mode": "image", "q": "rood"}, {"like": rood["id"]}):
        r = await client.get("/api/v1/search", params=params, headers=bearer(key))
        assert r.status_code == 401, r.text
    assert fake.ranks == []
