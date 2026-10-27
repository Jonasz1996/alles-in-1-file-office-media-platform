"""Het herstel van forgotten files (Keuze 21), met de nep-DS: dezelfde sha, het origineel (ook
als de sessie nog saving is), een herstelkopie (veranderd, een nieuwere sessie, of geen
schrijver), bestand of eigenaar weg, quotum vol, een onbekende key, bij de start van de app,
en de knoppen Terugzetten en Verwijderen van /beheer (restore en forget)."""

import asyncio

import pytest
from fastapi import HTTPException

from app.main import app
from app.office import recovery, runtime
from tests.fake_docserver import docx
from tests.office_contract import RECOVERED
from tests.office_world import office_world, wait_until

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        yield w


async def lost_with(world, user, node, paragraphs, state="closed"):
    """Een sessie waarvan de laatste opslag de app niet bereikte: de rij lost (of nog
    saving), en DS bewaarde de inhoud als forgotten file."""
    key = await world.begin(user, node)
    fields = {"state": state}
    if state == "closed":
        fields.update(closed_reason="lost", closed_at=world.now())
    await world.set_session(key, **fields)
    world.forgotten()[key] = ("output.docx", docx.make_docx(paragraphs))
    return key


async def test_dezelfde_sha_alleen_weg(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await world.begin(anna, node)
    world.forgotten()[key] = ("output.docx", await world.content(node))
    v0 = await world.version_count(node)
    await world.recover()
    assert key not in world.forgotten()
    assert await world.version_count(node) == v0
    assert world.event_data(anna, "office.recovered") == []
    row = await world.row(key)
    assert row.state == "closed" and row.closed_reason == "saved"


@pytest.mark.parametrize("state", ["closed", "saving", "open"])
async def test_ongewijzigd_bestand_gewoon_op_het_origineel(world, state):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost_with(world, anna, node, ["Begin", "hersteld"], state=state)
    v0 = await world.version_count(node)
    await world.recover()
    assert key not in world.forgotten()
    assert await world.paragraphs(node) == ["Begin", "hersteld"]
    assert await world.version_count(node) == v0 + 1  # wat er stond, blijft als versie
    assert await world.find(anna, "verslag" + RECOVERED) == []
    row = await world.row(key)
    # Een dichte rij houdt zijn reden (hoe de sessie eindigde); een die nog liep, sluit nu
    reason = "lost" if state == "closed" else "saved"
    assert row.state == "closed" and row.closed_reason == reason and row.last_error is None
    assert world.event_data(anna, "office.recovered") == [
        {"node_id": str(node), "copy_id": str(node), "name": "verslag.docx", "reason": "forgotten"}
    ]


async def test_herstel_vervangt_nooit_een_tussenstand(world):
    """Ook als de huidige inhoud de tussenstand van deze sessie is: geen squash (Keuze 17)."""
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await world.begin(anna, node)
    six = world.saved_fields(key, 6, world.stage(key, docx.make_docx(["zes"])), [anna])
    assert (await world.callback(six)).json() == {"error": 0}
    await world.set_session(key, state="closed", closed_reason="lost", closed_at=world.now())
    v0 = await world.version_count(node)
    world.forgotten()[key] = ("output.docx", docx.make_docx(["laatste"]))
    await world.recover()
    assert await world.paragraphs(node) == ["laatste"]
    assert await world.version_count(node) == v0 + 1
    assert ["zes"] in await world.version_paragraphs(node)


async def test_veranderd_bestand_een_herstelkopie(world):
    anna = await world.user("anna@example.nl")
    folder = await world.folder(anna, "Werk")
    node = await world.docx(anna, folder, "verslag.docx", ["Begin"])
    key = await lost_with(world, anna, node, ["uit de editor"])
    await world.upload(anna, node, ["van buiten"])
    await world.recover()
    assert key not in world.forgotten()
    assert await world.paragraphs(node) == ["van buiten"]
    copies = await world.find(anna, "verslag" + RECOVERED)
    assert len(copies) == 1
    assert await world.paragraphs(copies[0]) == ["uit de editor"]
    view = await world.node(copies[0])
    assert view.parent_id == await world.root(anna)  # in de hoofdmap
    row = await world.row(key)
    assert row.state == "closed" and row.closed_reason == "lost"
    assert world.event_data(anna, "office.recovered")[-1] == {
        "node_id": str(node),
        "copy_id": str(copies[0]),
        "name": (await world.node(copies[0])).name,
        "reason": "forgotten",
    }


async def test_niet_de_laatste_sessie_een_herstelkopie(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    old = await lost_with(world, anna, node, ["oud"])
    newer = await world.begin(anna, node)
    assert newer != old
    await world.recover()
    assert await world.paragraphs(node) == ["Begin"]
    copies = await world.find(anna, "verslag" + RECOVERED)
    assert len(copies) == 1 and await world.paragraphs(copies[0]) == ["oud"]
    assert (await world.row(newer)).state == "open"


async def test_zonder_schrijver_een_herstelkopie_en_niets_voor_wie_eruit_ging(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    share = await world.share(anna, folder, bram, write=True)
    node = await world.docx(anna, folder, "verslag.docx", ["Begin"])
    key = await lost_with(world, bram, node, ["van Bram"])
    # Bram mag nog lezen, niet meer schrijven: geen schrijver, wel iemand die het bestand ziet
    r = await world.client.patch(
        f"/api/v1/shares/{share}", json={"permission": "read"}, headers=anna.headers
    )
    assert r.status_code == 200, r.text
    await world.set_session(key, banned=[world.uid(bram)], members={})
    await world.recover()
    assert await world.paragraphs(node) == ["Begin"]
    copies = await world.find(anna, "verslag" + RECOVERED)
    assert len(copies) == 1 and await world.paragraphs(copies[0]) == ["van Bram"]
    assert world.event_data(anna, "office.recovered")
    assert "office.recovered" not in await world.events(bram)


async def test_ook_op_het_origineel_geen_melding_voor_wie_eruit_ging(world):
    """Bram ziet het bestand nog (lezen), maar staat in banned: office.recovered over het
    origineel gaat naar Anna en niet naar hem."""
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    share = await world.share(anna, folder, bram, write=True)
    node = await world.docx(anna, folder, "verslag.docx", ["Begin"])
    key = await world.begin(anna, node)
    joined = await world.open_config(bram, node)
    assert joined.status_code == 200 and joined.json()["key"] == key
    assert (await world.presence(key, [anna, bram])).json() == {"error": 0}
    r = await world.client.patch(
        f"/api/v1/shares/{share}", json={"permission": "read"}, headers=anna.headers
    )
    assert r.status_code == 200, r.text
    await world.sweep()
    assert world.uid(bram) in (await world.row(key)).banned
    await world.set_session(key, state="closed", closed_reason="lost", closed_at=world.now())
    world.forgotten()[key] = ("output.docx", docx.make_docx(["samen"]))
    await world.recover()
    assert await world.paragraphs(node) == ["samen"]
    assert world.event_data(anna, "office.recovered")
    assert "office.recovered" not in await world.events(bram)


async def test_bestand_weg_alleen_weg(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost_with(world, anna, node, ["weg ermee"])
    await world.purge(anna, node)
    await world.recover()
    assert key not in world.forgotten()
    assert await world.find(anna, "verslag") == []
    assert world.event_data(anna, "office.recovered") == []


async def test_quotum_vol_blijft_staan_en_meldt_een_keer(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost_with(world, anna, node, ["past niet"])
    await world.upload(anna, node, ["veranderd"])  # dus een kopie, en die past niet
    await world.room(anna, 0)
    await world.recover()
    await world.recover()
    assert key in world.forgotten()
    assert (await world.row(key)).last_error == "no_room"
    unsaved = world.event_data(anna, "office.unsaved")
    assert unsaved == [
        {"node_id": str(node), "name": "verslag.docx", "reason": "no_room", "final": True}
    ]
    assert await world.find(anna, "verslag" + RECOVERED) == []
    await world.room(anna, None)
    await world.recover()
    assert key not in world.forgotten()
    copies = await world.find(anna, "verslag" + RECOVERED)
    assert len(copies) == 1 and await world.paragraphs(copies[0]) == ["past niet"]
    assert (await world.row(key)).last_error is None


async def test_onbekende_key_blijft_staan(world):
    world.forgotten()["onbekend-abc"] = ("output.docx", docx.make_docx(["?"]))
    assert await recovery.handle("onbekend-abc") == "unknown"
    await world.recover()
    assert "onbekend-abc" in world.forgotten()


async def test_ds_weg_bij_ophalen_blijft_staan(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost_with(world, anna, node, ["later"])
    await world.http.post("/__fake/kill")
    assert await recovery.run_once() == 0
    await world.http.post("/__fake/restart")
    assert key in world.forgotten()
    await world.recover()
    assert key not in world.forgotten()
    assert await world.paragraphs(node) == ["later"]


async def test_bij_de_start_van_de_app_en_na_kick(world):
    """Na een herstart van de app staat een forgotten file meteen weer in de app (de eerste
    ronde draait bij de start, daarna elke office_recovery_seconds, hooguit 10 minuten), en
    kick() (DS weer gezond) haalt de volgende meteen."""
    assert world.settings.office_recovery_seconds <= 600
    anna = await world.user("anna@example.nl")
    first = await world.docx(anna, None, "een.docx", ["een"])
    second = await world.docx(anna, None, "twee.docx", ["twee"])
    k1 = await lost_with(world, anna, first, ["een hersteld"])
    await world.restart_app()
    await recovery.start(app)
    try:
        await wait_until(lambda: k1 not in world.forgotten(), 10)
        await wait_until(world.idle, 10)
        assert await world.paragraphs(first) == ["een hersteld"]
        k2 = await lost_with(world, anna, second, ["twee hersteld"])
        recovery.kick()
        await wait_until(lambda: k2 not in world.forgotten(), 10)
    finally:
        for task in world.state.tasks:
            task.cancel()
        await asyncio.gather(*world.state.tasks, return_exceptions=True)
        world.state.tasks.clear()
    await world.settle()
    assert await world.paragraphs(second) == ["twee hersteld"]
    assert runtime.app() is app


# --- de knoppen van /beheer ---


async def test_terugzetten_maakt_een_kopie(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost_with(world, anna, node, ["terug"])
    await world.room(anna, 0)
    await world.recover()
    assert key in world.forgotten()
    await world.room(anna, None)
    copy = await recovery.restore(key)
    assert key not in world.forgotten()
    assert await world.paragraphs(copy.id) == ["terug"]
    assert await world.paragraphs(node) == ["Begin"]
    row = await world.row(key)
    assert row.state == "closed" and row.closed_reason == "lost" and row.last_error is None
    assert world.event_data(anna, "office.recovered")[-1]["copy_id"] == str(copy.id)


async def test_terugzetten_zonder_rij_of_zonder_ruimte(world):
    with pytest.raises(HTTPException) as caught:
        await recovery.restore("geen-rij")
    assert caught.value.status_code == 404 and caught.value.detail["code"] == "not_found"
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost_with(world, anna, node, ["vol"])
    await world.room(anna, 0)
    with pytest.raises(HTTPException) as caught:
        await recovery.restore(key)
    assert caught.value.status_code == 413
    assert key in world.forgotten()


async def test_terugzetten_als_de_eigenaar_weg_is(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost_with(world, anna, node, ["weg"])
    await world.set_session(key, node_id=None, owner_id=None)
    with pytest.raises(HTTPException) as caught:
        await recovery.restore(key)
    assert caught.value.status_code == 404
    assert caught.value.detail["code"] == "office_owner_gone"


async def test_verwijderen(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost_with(world, anna, node, ["weg ermee"])
    await recovery.forget(key)
    assert key not in world.forgotten()
    assert await world.paragraphs(node) == ["Begin"]
    with pytest.raises(HTTPException) as caught:
        await recovery.forget(key)  # bestaat niet meer: DS geeft error 1
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "office_unavailable"
