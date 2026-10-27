"""Wat er gebeurt als het bestand verandert terwijl het open is (de tabel van Keuze 19), rij
voor rij, plus de volgorde van callbacks (S7), een herstart van de app midden in een opslag, en
DS die het na 8 pogingen opgeeft. Met de nep-DS en echte editors (tests/office_world.py)."""

import asyncio
import time

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from websockets.exceptions import ConnectionClosed

from app import userdelete
from app.models import User
from app.office import saving, sessions
from tests.factories import code_of, maker
from tests.fake_docserver import docx
from tests.office_contract import GIVE_UP, RECOVERED, Refused
from tests.office_world import office_world, wait_until

pytestmark = pytest.mark.anyio
OK = {"error": 0}


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        yield w


async def shared(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    share = await world.share(anna, folder, bram, write=True)
    node = await world.docx(anna, folder, "verslag.docx", ["Begin"])
    return anna, bram, folder, share, node


# --- de rijen van Keuze 19 ---


async def test_hernoemen_en_verplaatsen(world):
    anna, bram, folder, _, node = await shared(world)
    elders = await world.folder(anna, "Elders")
    h = await world.open(bram, node)
    await world.type(h, "Bram schreef")
    await world.rename(anna, node, "nieuw.docx")
    await world.move(anna, node, elders)
    await world.sweep()
    # Bram ziet het bestand niet meer (het staat buiten de gedeelde map): DS zette hem eruit en
    # hij kan niet verder typen
    await wait_until(lambda: not h.state.reading, 5)
    with pytest.raises(ConnectionClosed):
        await world.type(h, "Bram schreef nog")
    await world.close(h)
    await world.settle()
    view = await world.node(node)
    assert view.name == "nieuw.docx" and view.parent_id == elders
    copies = await world.find(anna, "verslag" + RECOVERED) + await world.find(
        anna, "nieuw" + RECOVERED
    )
    # Bram verloor zijn recht door het verplaatsen: de ronde zette hem eruit, zijn werk werd
    # een herstelkopie (er was niemand anders met schrijfrecht)
    assert len(copies) == 1
    assert "office.dropped" in await world.events(bram)


async def test_hernoemen_door_de_eigenaar_zelf(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    h = await world.open(anna, node)
    await world.type(h, "na hernoemen")
    await world.rename(anna, node, "ander.docx")
    await world.close(h)
    await world.settle()
    assert (await world.node(node)).name == "ander.docx"
    assert "na hernoemen" in await world.paragraphs(node)
    assert len(await world.find(anna, "")) == 1


async def test_verplaatsen_binnen_de_deling_slaat_op_in_het_verplaatste_bestand(world):
    anna, bram, folder, _, node = await shared(world)
    sub = await world.folder(anna, "Hoofdstukken", parent=folder)
    h = await world.open(bram, node)
    await world.type(h, "Bram in de submap")
    await world.move(anna, node, sub)
    await world.rename(anna, node, "hoofdstuk.docx")
    await world.sweep()
    assert world.uid(bram) not in (await world.row(h.key)).banned
    await world.close(h)
    await world.settle()
    view = await world.node(node)
    assert view.name == "hoofdstuk.docx" and view.parent_id == sub
    assert "Bram in de submap" in await world.paragraphs(node)
    assert await world.find(anna, "verslag") == [] and len(await world.find(anna, "")) == 1


async def test_prullenbak_de_eigenaar_doet_mee_de_deelnemer_niet(world):
    anna, bram, folder, _, node = await shared(world)
    a = await world.open(anna, node)
    b = await world.open(bram, node)
    assert a.key == b.key
    await world.type(a, "Anna")
    await world.type(b, "Bram")
    await world.trash(anna, node)
    await world.sweep()
    row = await world.row(a.key)
    assert world.uid(bram) in row.banned and world.uid(bram) not in row.members
    assert "office.dropped" in await world.events(bram)
    assert await world.reconnect(b) is False
    await world.close(a)
    await world.settle()
    view = await world.node(node)
    assert view.trashed
    text = await world.paragraphs(node)
    assert "Anna" in text  # in het bestand in de prullenbak
    r = await world.open_config(anna, node)
    assert r.status_code == 422 and code_of(r) == "trashed"


async def test_definitief_verwijderd(world):
    anna, bram, folder, _, node = await shared(world)
    a = await world.open(anna, node)
    b = await world.open(bram, node)
    await world.type(a, "weg")
    await world.purge(anna, node)
    await world.sweep()
    row = await world.row(a.key)
    assert row.state == "closed" and row.closed_reason == "gone"
    assert world.commands("drop")
    await world.settle()
    assert await world.find(anna, "verslag") == []
    # Een callback daarna schrijft niets
    r = await world.callback(world.saved_fields(a.key, 2, world.stage(a.key, b"x"), [anna]))
    assert r.json() == OK and await world.find(anna, "verslag") == []
    # Een forgotten file van die key: alleen weg
    world.forgotten()[a.key] = ("output.docx", docx.make_docx(["weg"]))
    await world.recover()
    assert a.key not in world.forgotten()
    assert await world.find(anna, "verslag") == []
    del b


async def test_upload_ertussen_de_editor_wint_en_stale(world):
    anna, bram, folder, _, node = await shared(world)
    a = await world.open(anna, node)
    await world.type(a, "uit de editor")
    v0 = await world.version_count(node)
    await world.upload(anna, node, ["van buiten"])
    assert await world.version_count(node) == v0 + 1
    r = await world.open_config(bram, node)
    assert r.status_code == 200 and r.json()["stale"] is True and r.json()["key"] == a.key
    await world.close(a)
    await world.settle()
    assert "uit de editor" in await world.paragraphs(node)
    # De upload bleef als versie staan
    assert ["van buiten"] in await world.version_paragraphs(node)


async def test_versie_terugzetten_ertussen(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["een"])
    await world.upload(anna, node, ["twee"])
    first = (await world.versions(node))[0]
    h = await world.open(anna, node)
    await world.type(h, "editor")
    r = await world.client.post(
        f"/api/v1/files/{node}/versions/{first.id}/restore", headers=anna.headers
    )
    assert r.status_code == 200, r.text
    await world.close(h)
    await world.settle()
    text = await world.paragraphs(node)
    assert "editor" in text and text[0] == "twee"  # de editor had "twee" geladen
    # Terugzetten ruilt: "twee" werd een versie en de versie "een" de inhoud. De editor won,
    # dus ook "een" (de inhoud van buiten) bleef als versie staan
    assert await world.version_paragraphs(node) == [["twee"], ["een"]]


async def test_deling_ingetrokken_iemand_anders_slaat_op(world):
    anna, bram, folder, share, node = await shared(world)
    a = await world.open(anna, node)
    b = await world.open(bram, node)
    await world.type(a, "Anna")
    await world.type(b, "Bram")
    await world.revoke(anna, share)
    await world.sweep()
    row = await world.row(a.key)
    assert world.uid(bram) in row.banned
    assert world.event_data(bram, "office.dropped")[-1] == {
        "node_id": str(node),
        "key": a.key,
        "reason": "access",
    }
    assert await world.reconnect(b) is False
    await world.close(a)
    await world.settle()
    text = await world.paragraphs(node)
    assert "Anna" in text and "Bram" in text  # zijn werk ging mee met dat van Anna
    assert await world.find(anna, "verslag" + RECOVERED) == []
    assert "office.recovered" not in await world.events(bram)
    assert "office.unsaved" not in await world.events(bram)


async def test_geblokkeerd_zelfde_als_ingetrokken(world):
    anna, bram, folder, share, node = await shared(world)
    b = await world.open(bram, node)
    await world.type(b, "Bram")
    async with maker(world.base_settings)() as session:
        await session.execute(
            update(User).where(User.id == bram.id).values(disabled_at=world.now())
        )
        await session.commit()
    await world.sweep()
    await world.settle()
    assert "office.dropped" in await world.events(bram)
    copies = await world.find(anna, "verslag" + RECOVERED)
    assert len(copies) == 1 and "Bram" in await world.paragraphs(copies[0])
    assert await world.paragraphs(node) == ["Begin"]


async def test_nog_verbonden_bij_de_volgende_ronde_stopt_de_sessie(world, monkeypatch):
    """De drop haalt hem er niet uit (de terugval van Keuze 20): bij de volgende ronde stopt de
    hele sessie met forcesave en drop voor iedereen."""
    anna, bram, folder, share, node = await shared(world)
    a = await world.open(anna, node)
    b = await world.open(bram, node)
    await world.type(a, "Anna")
    await world.type(b, "Bram")
    eng = world.fake
    original = eng.commands["drop"]

    async def deaf(params):
        if params.get("users"):
            return {"key": params.get("key"), "error": 0}  # doet niets
        return await original(params)

    eng.commands["drop"] = deaf
    await world.revoke(anna, share)
    await world.sweep()
    assert (await world.row(a.key)).state == "open"
    await world.sweep()
    await world.settle()
    row = await world.row(a.key)
    assert row.state == "closed" and row.closed_reason == "saved"
    forcesaves = [c for c in world.commands() if c["c"] in ("forcesave", "drop")]
    assert [c["c"] for c in forcesaves][-2:] == ["forcesave", "drop"]
    text = await world.paragraphs(node)
    assert "Anna" in text
    del b


async def test_quotum_vol_tussenstand_en_laatste_opslag(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    before = await world.content(node)
    h = await world.open(anna, node)
    await world.type(h, "past niet")
    await world.room(anna, 0)
    await world.save(h, explicit=False)
    await world.settle()
    assert await world.content(node) == before
    assert world.event_data(anna, "office.unsaved")[-1]["final"] is False
    await world.close(h)
    await world.settle()
    assert await world.content(node) == before
    assert h.key in world.forgotten()  # ook de kopie lukt niet: een forgotten file
    assert await world.find(anna, "verslag" + RECOVERED) == []
    await world.recover()  # nog steeds vol: blijft staan, met één melding
    await world.recover()
    assert h.key in world.forgotten()
    finals = [e for e in world.event_data(anna, "office.unsaved") if e["final"]]
    assert len(finals) == 2  # één van de callback, één van het herstel
    await world.room(anna, None)
    await world.recover()
    assert h.key not in world.forgotten()
    assert "past niet" in await world.paragraphs(node)


async def test_eigenaar_verwijderd(world):
    anna, bram, folder, _, node = await shared(world)
    b = await world.open(bram, node)
    await world.type(b, "Bram")
    key = b.key
    async with maker(world.base_settings)() as session:
        await session.execute(
            update(User).where(User.id == anna.id).values(disabled_at=world.now())
        )
        await session.commit()
    await userdelete.run(world.base_settings, {"user_id": str(anna.id)})
    await world.sweep()
    row = await world.row(key)
    assert row.state == "closed" and row.closed_reason == "gone"
    await world.settle()
    world.forgotten()[key] = ("output.docx", docx.make_docx(["Bram"]))
    await world.recover()
    assert key not in world.forgotten()  # meteen weg, geen kopie
    async with maker(world.base_settings)() as session:
        assert await session.get(User, anna.id) is None


# --- volgorde en herstarts ---


async def test_s7_oude_tussenstand_na_de_laatste(world, monkeypatch):
    """503 op de eerste status 6, dan status 2, dan de herhaling van die status 6: de inhoud
    van status 2 blijft de huidige."""
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await world.begin(anna, node)
    now = time.time()
    six = world.saved_fields(key, 6, world.stage(key, docx.make_docx(["zes"])), [anna], at=now)
    real = sessions.find_by_key
    calls = {"n": 0}

    async def once_locked(session, k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("SELECT", {}, Exception("database is locked"))
        return await real(session, k)

    monkeypatch.setattr(sessions, "find_by_key", once_locked)
    r = await world.callback(six)
    assert r.status_code == 503
    monkeypatch.setattr(sessions, "find_by_key", real)
    two = world.saved_fields(key, 2, world.stage(key, docx.make_docx(["twee"])), [anna], at=now + 5)
    assert (await world.callback(two)).json() == OK
    assert (await world.callback(six)).json() == OK
    assert await world.paragraphs(node) == ["twee"]


async def test_twee_callbacks_tegelijk_op_het_slot(world):
    """Een status 6 en een status 2 komen tegelijk binnen: het slot van het bestand zet ze na
    elkaar, en de laatste inhoud wint."""
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await world.begin(anna, node)
    now = time.time()
    six = world.saved_fields(key, 6, world.stage(key, docx.make_docx(["zes"])), [anna], at=now)
    two = world.saved_fields(key, 2, world.stage(key, docx.make_docx(["twee"])), [anna], at=now + 1)
    lock = world.state.node_lock(node)
    await lock.acquire()
    try:
        tasks = [asyncio.ensure_future(world.callback(f)) for f in (two, six)]
        await wait_until(lambda: len(lock._waiters or ()) >= 2)
    finally:
        lock.release()
    results = await asyncio.gather(*tasks)
    assert all(r.json() == OK for r in results)
    assert await world.paragraphs(node) == ["twee"]
    assert (await world.row(key)).closed_reason == "saved"


async def test_herstart_van_de_app_tussen_saving_en_het_antwoord(world, monkeypatch):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    v0 = await world.version_count(node)
    key = await world.begin(anna, node)
    fields = world.saved_fields(key, 2, world.stage(key, docx.make_docx(["klaar"])), [anna])

    async def crash(*args, **kwargs):
        raise RuntimeError("de app viel om")

    monkeypatch.setattr(saving, "download", crash)
    with pytest.raises(RuntimeError):
        await world.callback(fields)
    assert (await world.row(key)).state == "saving"
    monkeypatch.undo()
    await world.restart_app()
    # Wie nu opent, krijgt office_busy (de opslag loopt nog)
    r = await world.open_config(anna, node)
    assert r.status_code == 409 and code_of(r) == "office_busy"
    # DS probeert het opnieuw: nu lukt het, één versie
    assert (await world.callback(fields)).json() == OK
    assert await world.paragraphs(node) == ["klaar"]
    assert await world.version_count(node) == v0 + 1
    assert (await world.row(key)).closed_reason == "saved"


async def test_ds_geeft_het_op_na_8_keer(world):
    """8 keer 503 (Keuze 29): de opslag wordt een forgotten file. De ronde sluit de sessie
    (lost), het herstel zet het werk op het origineel, de volgende opener krijgt een nieuwe
    key."""
    world.fake.config.retries = (1, 2, 4, 8, 16, 32, 64, 128)
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    v0 = await world.version_count(node)
    h = await world.open(anna, node)
    await world.type(h, "na 8 pogingen")
    await world.fail_callbacks(GIVE_UP)
    await world.close(h)
    await world.settle(15)
    assert world.failing.refused == 9  # status 1 bij het sluiten niet: alleen de opslag
    assert h.key in world.forgotten()
    await world.restart_app()
    await world.advance(7)
    await world.sweep()
    row = await world.row(h.key)
    assert row.state == "closed" and row.closed_reason == "lost"
    await world.recover()
    assert "na 8 pogingen" in await world.paragraphs(node)
    assert await world.version_count(node) == v0 + 1
    assert h.key not in world.forgotten()
    assert world.event_data(anna, "office.recovered")[-1]["reason"] == "forgotten"
    again = await world.open(anna, node)
    assert again.key != h.key


async def test_een_gast_met_write_kan_niet_meer_na_intrekken(world):
    anna = await world.user("anna@example.nl")
    gast = await world.user("gast@example.nl", role="guest")
    folder = await world.folder(anna, "Samen")
    share = await world.share(anna, folder, gast, write=True)
    node = await world.docx(anna, folder, "verslag.docx", ["Begin"])
    h = await world.open(gast, node)
    await world.revoke(anna, share)
    await world.sweep()
    assert world.uid(gast) in (await world.row(h.key)).banned
    try:
        await world.open(gast, node)
    except Refused as refused:
        assert refused.status == 404
    else:
        raise AssertionError("na intrekken hoort openen geweigerd te worden")
