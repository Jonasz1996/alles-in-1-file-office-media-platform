"""De editor openen (app/office/sessions.py en routers/office.py): de rechten van Keuze 22 rij
voor rij, de invoercontrole van Keuze 23, wie welke key krijgt (de tabel van Keuze 14), de
getekende config, de grenzen, stale, banned, en de knoppen Nu opslaan en het poort-cookie."""

import asyncio
import uuid
from datetime import timedelta

import jwt
import pytest
from sqlalchemy import select, update

from app.main import app
from app.models import Blob, Node, OfficeSession, User
from app.office import sessions
from app.office.oo import tokens
from app.office.state import OfficeConfig, office_policy
from app.routers.office_contracts import MAX_EDIT_BYTES
from app.security import SecurityConfig, SecurityPolicy
from tests.factories import code_of, create_album, create_file, maker, params_of
from tests.fake_docserver import docx
from tests.office_world import office_world, wait_until

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        yield w


async def a_docx(world, owner, folder=None, name="verslag.docx", text=("Begin",)):
    return await world.docx(owner, folder, name, list(text))


async def guest(world, email: str):
    return await world.user(email, role="guest")


async def opened(world, user, node, **body):
    r = await world.open_config(user, node, **body)
    assert r.status_code == 200, r.text
    return r.json()


def uid(world, user) -> str:
    return sessions.ds_uid(world.settings, user.id)


# --- Keuze 22: wie mag de editor openen ---


async def test_eigenaar_mag(world):
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    out = await opened(world, anna, node)
    assert out["api_url"] == "/office/web-apps/apps/api/documents/api.js"
    assert out["gate_seconds"] == 7200 and out["stale"] is False
    assert out["key"].startswith(node.hex + "-")
    row = await world.row(out["key"])
    assert row.state == "pending" and row.members == {uid(world, anna): str(anna.id)}


async def test_deling_met_write_als_gebruiker_en_als_gast(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    gast = await guest(world, "gast@example.nl")
    folder = await world.folder(anna, "Samen")
    await world.share(anna, folder, bram, write=True)
    await world.share(anna, folder, gast, write=True)
    node = await a_docx(world, anna, folder)
    first = await opened(world, bram, node)
    second = await opened(world, gast, node)
    assert first["key"] == second["key"]


async def test_deling_met_read_geeft_403_read_only(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Lezen")
    await world.share(anna, folder, bram, write=False)
    node = await a_docx(world, anna, folder)
    r = await world.open_config(bram, node)
    assert r.status_code == 403 and code_of(r) == "read_only"


async def test_album_prullenbak_map_en_onbekend(world):
    anna = await world.user("anna@example.nl")
    async with maker(world.base_settings)() as session:
        owner = await session.get(User, anna.id)
        album = await create_album(session, owner)
        await session.commit()
    r = await world.open_config(anna, album.id)
    assert r.status_code == 422 and code_of(r) == "album"
    node = await a_docx(world, anna)
    await world.trash(anna, node)
    r = await world.open_config(anna, node)
    assert r.status_code == 422 and code_of(r) == "trashed"
    folder = await world.folder(anna, "Map")
    r = await world.open_config(anna, folder)
    assert r.status_code == 422 and code_of(r) == "not_a_file"
    r = await world.open_config(anna, uuid.uuid4())
    assert r.status_code == 404 and code_of(r) == "not_found"
    # Van een ander, zonder deling: 404, net als iets dat niet bestaat
    bram = await world.user("bram@example.nl")
    other = await a_docx(world, bram)
    r = await world.open_config(anna, other)
    assert r.status_code == 404 and code_of(r) == "not_found"


async def test_in_een_map_in_de_prullenbak(world):
    anna = await world.user("anna@example.nl")
    folder = await world.folder(anna, "Weg")
    node = await a_docx(world, anna, folder)
    await world.trash(anna, folder)
    r = await world.open_config(anna, node)
    assert r.status_code == 422 and code_of(r) == "trashed"


async def test_zonder_2fa_geblokkeerd_en_gast_eigenaar(world):
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    previous = app.state.security
    app.state.security = SecurityPolicy(SecurityConfig(require_2fa=True))
    try:
        r = await world.open_config(anna, node)
        assert r.status_code == 403 and code_of(r) == "totp_setup_required"
    finally:
        app.state.security = previous
    # Een gast die toch een eigen bestand heeft (een oude rij): 403 guest
    gast = await guest(world, "gast@example.nl")
    async with maker(world.base_settings)() as session:
        owner = await session.get(User, gast.id)
        own = await create_file(
            session, owner, name="eigen.docx", data=docx.make_docx(["x"]), mime=None
        )
        await session.commit()
    r = await world.open_config(gast, own.id)
    assert r.status_code == 403 and code_of(r) == "guest"
    # Geblokkeerd: 401
    async with maker(world.base_settings)() as session:
        await session.execute(
            update(User).where(User.id == anna.id).values(disabled_at=world.now())
        )
        await session.commit()
    r = await world.open_config(anna, node)
    assert r.status_code == 401


# --- Keuze 23: formaat, grootte en de invoercontrole ---


async def test_formaat_grootte_en_invoercontrole(world):
    anna = await world.user("anna@example.nl")
    txt = await world.put_file(anna, None, "notities.txt", b"gewone tekst")
    r = await world.open_config(anna, txt)
    assert r.status_code == 422 and code_of(r) == "office_format"
    odt = await world.put_file(anna, None, "oud.odt", b"PK\x03\x04rest")
    r = await world.open_config(anna, odt)
    assert r.status_code == 422 and code_of(r) == "office_format"
    # Geen zip met de naam .docx: de invoercontrole weigert hem
    fake = await world.put_file(anna, None, "nep.docx", b"dit is geen zip")
    r = await world.open_config(anna, fake)
    assert r.status_code == 422 and code_of(r) == "office_unsafe"
    assert await world.row_count() == 0  # er ging niets naar DS en er is geen sessie
    big = await a_docx(world, anna, name="groot.docx")
    async with maker(world.base_settings)() as session:
        await session.execute(update(Node).where(Node.id == big).values(size=MAX_EDIT_BYTES + 1))
        await session.commit()
    r = await world.open_config(anna, big)
    assert r.status_code == 422 and code_of(r) == "office_too_large"


async def test_office_uit_weg_of_verkeerd_ingesteld(world):
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    office_policy(app).config = OfficeConfig(enabled=False)
    r = await world.open_config(anna, node)
    assert r.status_code == 503 and code_of(r) == "office_off"
    office_policy(app).config = OfficeConfig()
    world.state.health = "down"
    r = await world.open_config(anna, node)
    assert r.status_code == 503 and code_of(r) == "office_unavailable"
    world.state.health = "starting"
    r = await world.open_config(anna, node)
    assert r.status_code == 503 and code_of(r) == "office_unavailable"
    world.state.health = "misconfigured"
    r = await world.open_config(anna, node)
    assert r.status_code == 503 and code_of(r) == "office_misconfigured"


async def test_te_vaak_openen(world):
    anna = await world.user("anna@example.nl")
    folder = await world.folder(anna, "Map")
    for _ in range(30):
        r = await world.open_config(anna, folder)
        assert r.status_code == 422
    r = await world.open_config(anna, folder)
    assert r.status_code == 429 and code_of(r) == "rate_limited"


# --- de config ---


async def test_config_is_getekend_en_compleet(world):
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna, name="Plan & zo.docx")
    r = await world.open_config(anna, node, type="mobile", theme="dark")
    assert r.status_code == 200
    out = r.json()
    config = out["config"]
    claims = jwt.decode(config["token"], world.settings.office_secret, algorithms=["HS256"])
    assert "exp" in claims and claims["exp"] - claims["iat"] == 7200
    unsigned = {k: v for k, v in config.items() if k != "token"}
    assert {k: v for k, v in claims.items() if k not in ("exp", "iat")} == unsigned
    key = out["key"]
    assert config["document"]["url"] == f"http://platform:8081/internal/office/doc/{key}"
    assert config["editorConfig"]["callbackUrl"] == "http://platform:8081/internal/office/callback"
    assert config["document"]["permissions"]["edit"] is True
    assert config["editorConfig"]["mode"] == "edit"
    assert config["document"]["title"] == "Plan & zo.docx"
    assert config["documentType"] == "word" and config["type"] == "mobile"
    custom = config["editorConfig"]["customization"]
    assert custom["uiTheme"] == "theme-dark" and custom["macros"] is False
    assert custom["plugins"] is False and custom["macrosMode"] == "disable"
    assert custom["forcesave"] is True and custom["close"] == {"visible": True}
    assert config["editorConfig"]["user"] == {"id": uid(world, anna), "name": anna.name}
    assert config["editorConfig"]["lang"] == "nl" and config["editorConfig"]["region"] == "nl-NL"
    # Het poort-cookie van /office/
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("office=") and "Path=/office/" in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")


async def test_taal_en_licht_thema(world):
    anna = await world.user("anna@example.nl")
    async with maker(world.base_settings)() as session:
        await session.execute(update(User).where(User.id == anna.id).values(locale="en"))
        await session.commit()
    node = await a_docx(world, anna, name="sheet.xlsx")
    out = await opened(world, anna, node)
    config = out["config"]
    assert config["documentType"] == "cell" and config["type"] == "desktop"
    assert config["editorConfig"]["lang"] == "en" and config["editorConfig"]["region"] == "en-GB"
    assert "uiTheme" not in config["editorConfig"]["customization"]


async def test_ds_uid_is_die_van_de_adapter(settings):
    from tests.office_world import office_settings

    office = office_settings(settings)
    user_id = uuid.uuid4()
    assert sessions.ds_uid(office, user_id) == tokens.ds_user_id(office, user_id)
    assert len(sessions.ds_uid(office, user_id)) == 24


# --- Keuze 14: wie welke key krijgt ---


async def test_tegelijk_openen_geeft_een_key(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    await world.share(anna, folder, bram, write=True)
    node = await a_docx(world, anna, folder)
    first, second = await asyncio.gather(
        world.open_config(anna, node), world.open_config(bram, node)
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["key"] == second.json()["key"]
    row = await world.row(first.json()["key"])
    assert set(row.members) == {uid(world, anna), uid(world, bram)}


async def test_pending_jong_dezelfde_oud_een_nieuwe_in_een_transactie(world):
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    first = (await opened(world, anna, node))["key"]
    await world.advance(4)
    assert (await opened(world, anna, node))["key"] == first  # jonger dan 5 minuten
    await world.advance(6)
    second = (await opened(world, anna, node))["key"]
    assert second != first
    old = await world.row(first)
    assert old.state == "closed" and old.closed_reason == "lost"
    assert (await world.row(second)).state == "pending"


async def test_open_met_en_zonder_iemand(world):
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    key = (await opened(world, anna, node))["key"]
    await world.set_session(key, state="open", connected=[uid(world, anna)])
    assert (await opened(world, anna, node))["key"] == key
    await world.set_session(key, state="open", connected=[])
    r = await world.open_config(anna, node)
    assert r.status_code == 409 and code_of(r) == "office_busy"


async def test_dicht_geeft_een_nieuwe_key(world):
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    key = (await opened(world, anna, node))["key"]
    await world.set_session(key, state="closed", closed_reason="saved")
    assert (await opened(world, anna, node))["key"] != key


async def test_saving_geeft_office_busy(world):
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    key = (await opened(world, anna, node))["key"]
    await world.set_session(key, state="saving")
    r = await world.open_config(anna, node)
    assert r.status_code == 409 and code_of(r) == "office_busy"


async def test_wachten_op_het_slot_en_dan_office_busy(world, monkeypatch):
    """Het slot van het bestand is bezet (er wordt opgeslagen): de opener wacht, en na
    LOCK_SECONDS (15 s; hier korter) 409 office_busy."""
    monkeypatch.setattr(sessions, "LOCK_SECONDS", 0.3)
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    lock = world.state.node_lock(node)
    async with lock:
        r = await world.open_config(anna, node)
    assert r.status_code == 409 and code_of(r) == "office_busy"


async def test_wachten_op_het_slot_en_dan_een_nieuwe_key(world):
    """Na het slot (de opslag is klaar, de sessie dicht) krijgt de opener gewoon een nieuwe
    key."""
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    key = (await opened(world, anna, node))["key"]
    await world.set_session(key, state="saving")
    lock = world.state.node_lock(node)
    await lock.acquire()
    try:
        task = asyncio.ensure_future(world.open_config(anna, node))
        await wait_until(lambda: len(lock._waiters or ()) >= 1)
        await world.set_session(key, state="closed", closed_reason="saved")
    finally:
        lock.release()
    r = await task
    assert r.status_code == 200 and r.json()["key"] != key


async def test_een_kapotte_unieke_index_gebeurt_nooit(world):
    """Vijf openers tegelijk op een oude pending: één nieuwe rij, nooit een IntegrityError."""
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    first = (await opened(world, anna, node))["key"]
    await world.advance(10)
    results = await asyncio.gather(*(world.open_config(anna, node) for _ in range(5)))
    keys = {r.json()["key"] for r in results if r.status_code == 200}
    assert all(r.status_code == 200 for r in results)
    assert len(keys) == 1 and first not in keys
    async with maker(world.base_settings)() as session:
        live = await session.scalars(
            select(OfficeSession).where(
                OfficeSession.node_id == node, OfficeSession.state != "closed"
            )
        )
        assert len(list(live)) == 1


# --- grenzen, stale en banned ---


async def test_te_veel_editors_per_document(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    await world.share(anna, folder, bram, write=True)
    node = await a_docx(world, anna, folder)
    key = (await opened(world, anna, node))["key"]
    await world.set_session(key, state="open", connected=[uid(world, anna)])
    office_policy(app).config = OfficeConfig(max_editors=1)
    r = await world.open_config(bram, node)
    assert r.status_code == 429 and code_of(r) == "office_too_many"
    assert params_of(r) == {"max": 1}
    # Wie er al in zit, mag opnieuw laden
    assert (await opened(world, anna, node))["key"] == key


async def test_licentie_van_ds_vol(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    node = await a_docx(world, anna)
    other = await a_docx(world, bram, name="ander.docx")
    key = (await opened(world, anna, node))["key"]
    await world.set_session(key, state="open", connected=[uid(world, anna)])
    world.state.license_connections = 1
    r = await world.open_config(bram, other)
    assert r.status_code == 429 and code_of(r) == "office_too_many"
    assert params_of(r) == {"max": 1}
    world.state.license_connections = 2_147_483_647  # onbeperkt
    assert (await world.open_config(bram, other)).status_code == 200


async def test_stale_na_een_upload(world):
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    key = (await opened(world, anna, node))["key"]
    await world.upload(anna, node, ["van buiten"])
    again = await opened(world, anna, node)
    assert again["key"] == key and again["stale"] is True


async def test_banned_met_weer_recht_doet_weer_mee(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    await world.share(anna, folder, bram, write=True)
    node = await a_docx(world, anna, folder)
    key = (await opened(world, anna, node))["key"]
    await world.set_session(
        key,
        state="open",
        connected=[uid(world, anna)],
        members={uid(world, anna): str(anna.id)},
        banned=[uid(world, bram)],
    )
    assert (await opened(world, bram, node))["key"] == key
    row = await world.row(key)
    assert uid(world, bram) in row.members and uid(world, bram) not in row.banned


# --- GET files/{id} ---


async def test_files_zegt_waarom_niet(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Lezen")
    await world.share(anna, folder, bram, write=False)
    node = await a_docx(world, anna, folder)

    async def reason(user, node_id):
        r = await world.client.get(f"/api/v1/office/files/{node_id}", headers=user.headers)
        assert r.status_code == 200, r.text
        return r.json()

    out = await reason(anna, node)
    assert out == {"can_edit": True, "reason": None, "open": None, "pdf_state": None}
    assert (await reason(bram, node))["reason"] == "read_only"
    txt = await world.put_file(anna, None, "a.txt", b"tekst")
    assert (await reason(anna, txt))["reason"] == "format"
    unsafe = await world.put_file(anna, None, "nep.docx", b"geen zip")
    assert (await reason(anna, unsafe))["reason"] == "unsafe"
    office_policy(app).config = OfficeConfig(enabled=False)
    assert (await reason(anna, node))["reason"] == "office_off"
    office_policy(app).config = OfficeConfig(max_editors=1)
    world.state.health = "down"
    assert (await reason(anna, node))["reason"] == "office_down"
    world.state.health = "ok"
    # Open, met één editor erin: Bram (read) ziet dat, en een tweede schrijver zit vol
    key = (await opened(world, anna, node))["key"]
    await world.set_session(key, state="open", connected=[uid(world, anna)])
    assert (await reason(anna, node))["open"] == {"editors": 1}
    assert (await reason(bram, node))["open"] == {"editors": 1}
    async with maker(world.base_settings)() as session:
        sha = (await session.get(Node, node)).blob_id
        await session.execute(update(Blob).where(Blob.sha256 == sha).values(pdf_state="ready"))
        await session.commit()
    assert (await reason(anna, node))["pdf_state"] == "ready"
    await world.trash(anna, node)
    assert (await reason(anna, node))["reason"] == "trashed"


async def test_files_te_veel(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    await world.share(anna, folder, bram, write=True)
    node = await a_docx(world, anna, folder)
    key = (await opened(world, anna, node))["key"]
    await world.set_session(key, state="open", connected=[uid(world, anna)])
    office_policy(app).config = OfficeConfig(max_editors=1)
    r = await world.client.get(f"/api/v1/office/files/{node}", headers=bram.headers)
    assert r.json()["reason"] == "too_many" and r.json()["can_edit"] is False


# --- Nu opslaan en het poort-cookie ---


async def test_nu_opslaan(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    node = await a_docx(world, anna)
    h = await world.open(anna, node)
    await world.settle()
    await world.type(h, "nu opslaan")
    r = await world.client.post(f"/api/v1/office/files/{node}/save", headers=anna.headers)
    assert r.status_code == 202 and r.json() == {}
    await world.settle()
    assert "nu opslaan" in await world.paragraphs(node)
    commands = [e for e in world.fake.entries if e["kind"] == "command"]
    assert commands[-1]["c"] == "forcesave"
    # Geen lid, of geen open sessie: 404
    r = await world.client.post(f"/api/v1/office/files/{node}/save", headers=bram.headers)
    assert r.status_code == 404
    other = await a_docx(world, anna, name="ander.docx")
    r = await world.client.post(f"/api/v1/office/files/{other}/save", headers=anna.headers)
    assert r.status_code == 404 and code_of(r) == "not_found"
    # DS weg: 503 office_unavailable
    world.fake.up = False
    r = await world.client.post(f"/api/v1/office/files/{node}/save", headers=anna.headers)
    assert r.status_code == 503 and code_of(r) == "office_unavailable"
    world.fake.up = True


async def test_poort_cookie_vernieuwen(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    share = await world.share(anna, folder, bram, write=True)
    node = await a_docx(world, anna, folder)
    await opened(world, anna, node)
    await opened(world, bram, node)
    r = await world.client.post("/api/v1/office/gate", headers=bram.headers)
    assert r.status_code == 204 and r.headers["set-cookie"].startswith("office=")
    await world.revoke(anna, share)
    r = await world.client.post("/api/v1/office/gate", headers=bram.headers)
    assert r.status_code == 404 and "set-cookie" not in r.headers
    carl = await world.user("carl@example.nl")
    r = await world.client.post("/api/v1/office/gate", headers=carl.headers)
    assert r.status_code == 404
    r = await world.client.post("/api/v1/office/gate", headers=anna.headers)
    assert r.status_code == 204


async def test_pending_ouder_dan_vijf_minuten_volgens_updated_at(world):
    """De leeftijd van pending telt vanaf de laatste config die uitging (updated_at)."""
    anna = await world.user("anna@example.nl")
    node = await a_docx(world, anna)
    key = (await opened(world, anna, node))["key"]
    row = await world.row(key)
    await world.set_session(key, updated_at=row.updated_at - timedelta(minutes=6))
    assert (await opened(world, anna, node))["key"] != key
