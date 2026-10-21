"""Een geblokkeerd account (User.disabled_at, hier rechtstreeks in de database gezet): kan niet
meer inloggen of verversen, een uitgegeven token werkt niet meer, een downloadlink en zijn
publieke links ook niet, en een open stroom met gebeurtenissen stopt. Zonder het juiste
wachtwoord (en de code) is niet te zien dat het geblokkeerd is."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from app import events, totp
from app.models import User
from app.routers import events as live
from app.routers import files, public, uploads
from app.routers.auth import revoke_other_logins
from tests.factories import code_of, maker
from tests.test_2fa import enable, login
from tests.test_events_stream import Stream
from tests.test_files import mkdir, put, root
from tests.test_public import bearer, new_link, open_link, token_of, tus_create
from tests.test_shares import person

EMAIL = "anna@example.nl"
PASSWORD = "lang-genoeg-123"


async def block(settings, email=EMAIL, at=None):
    async with maker(settings)() as session:
        await session.execute(
            update(User).where(User.email == email).values(disabled_at=at or datetime.now(UTC))
        )
        await session.commit()


async def unblock(settings, email=EMAIL):
    async with maker(settings)() as session:
        await session.execute(update(User).where(User.email == email).values(disabled_at=None))
        await session.commit()


@pytest.mark.anyio
async def test_inloggen_pas_na_wachtwoord_geweigerd(client, admin, settings):
    headers, _ = admin
    await person(client, headers, EMAIL)
    await block(settings)

    r = await login(client, {"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 403
    assert code_of(r) == "account_disabled"

    # Met een fout wachtwoord de gewone 401: niets te zien
    r = await login(client, {"email": EMAIL, "password": "verkeerd-wachtwoord"})
    assert r.status_code == 401
    assert code_of(r) == "bad_credentials"


@pytest.mark.anyio
async def test_met_2fa_pas_na_de_code_geweigerd(client, admin, settings):
    headers, _ = admin
    anna, _ = await person(client, headers, EMAIL)
    secret, _ = await enable(client, anna, password=PASSWORD)
    await block(settings)

    r = await login(client, {"email": EMAIL, "password": PASSWORD})
    assert (r.status_code, code_of(r)) == (401, "totp_required")
    code = totp.code_at(secret, totp.current_step() + 1)  # een stap die nog niet gebruikt is
    r = await login(client, {"email": EMAIL, "password": PASSWORD}, code=code)
    assert (r.status_code, code_of(r)) == (403, "account_disabled")


@pytest.mark.anyio
async def test_verversen_geweigerd_en_sessie_ingetrokken(client, admin, settings):
    headers, _ = admin
    await person(client, headers, EMAIL)
    tokens = (await login(client, {"email": EMAIL, "password": PASSWORD})).json()
    await block(settings)

    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert (r.status_code, code_of(r)) == (403, "account_disabled")

    # Ook na het deblokkeren werkt die sessie niet meer
    await unblock(settings)
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_uitgegeven_token_en_downloadlink_werken_niet_meer(client, admin, settings):
    headers, _ = admin
    anna, _ = await person(client, headers, EMAIL)
    root_id = (await root(client, anna))["folder"]["id"]
    node = (await put(client, anna, root_id, "a.txt", b"a")).json()
    link = (await client.post(f"/api/v1/files/{node['id']}/download-link", headers=anna)).json()
    assert (await client.get(link["url"])).status_code == 200
    await block(settings)

    r = await client.get("/api/v1/auth/me", headers=anna)
    assert (r.status_code, code_of(r)) == (401, "account_disabled")
    r = await client.get(link["url"])
    assert (r.status_code, code_of(r)) == (404, "not_found")

    await unblock(settings)
    assert (await client.get(link["url"])).status_code == 200
    me = (await client.get("/api/v1/auth/me", headers=anna)).json()
    assert me["disabled"] is False


@pytest.mark.anyio
async def test_blokkeren_met_ingetrokken_logins_geeft_account_disabled(client, admin, settings):
    """Zoals het blokkeren in beheer: disabled_at plus alle logins ingetrokken. Toch krijgt
    de web-app account_disabled (en niet not_logged_in of logged_out)."""
    headers, _ = admin
    await person(client, headers, EMAIL)
    tokens = (await login(client, {"email": EMAIL, "password": PASSWORD})).json()
    async with maker(settings)() as session:
        await session.execute(
            update(User).where(User.email == EMAIL).values(disabled_at=datetime.now(UTC))
        )
        await revoke_other_logins(session, uuid.UUID(tokens["user"]["id"]), None)
        await session.commit()

    me = await client.get("/api/v1/auth/me", headers=bearer(tokens["access_token"]))
    assert (me.status_code, code_of(me)) == (401, "account_disabled")
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert (r.status_code, code_of(r)) == (403, "account_disabled")

    # Gedeblokkeerd blijft de login ingetrokken: dan weer de gewone 401
    await unblock(settings)
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert (r.status_code, code_of(r)) == (401, "not_logged_in")
    me = await client.get("/api/v1/auth/me", headers=bearer(tokens["access_token"]))
    assert (me.status_code, code_of(me)) == (401, "logged_out")


@pytest.fixture
def fresh_limits():
    for limiter in (public.open_limiter, public.upload_limiter, public.password_limiter):
        limiter.reset()
    yield
    for limiter in (public.open_limiter, public.upload_limiter, public.password_limiter):
        limiter.reset()


@pytest.mark.anyio
async def test_publieke_links_van_een_geblokkeerde_eigenaar(client, admin, settings, fresh_limits):
    """Openen, kijken, downloaden en uploaden via een link van een geblokkeerde eigenaar: 404
    link_not_found, net als een link die niet bestaat. Ook met een al uitgegeven sleutel."""
    headers, _ = admin
    anna, _ = await person(client, headers, EMAIL)
    root_id = (await root(client, anna))["folder"]["id"]
    folder = await mkdir(client, anna, root_id, "Map")
    f = (await put(client, anna, folder, "a.txt", b"geheim")).json()
    read = await new_link(client, anna, folder, permission="read")
    upload = await new_link(client, anna, folder, permission="upload")
    read_key = (await open_link(client, read))["key"]
    upload_key = (await open_link(client, upload))["key"]
    await block(settings)

    for link in (read, upload):
        r = await client.post("/api/v1/public/open", json={"token": token_of(link)})
        assert (r.status_code, code_of(r)) == (404, "link_not_found")
    r = await client.get("/api/v1/public/children", headers=bearer(read_key))
    assert (r.status_code, code_of(r)) == (404, "link_not_found")
    r = await client.get(f"/api/v1/public/download/{f['id']}", params={"key": read_key})
    assert (r.status_code, code_of(r)) == (404, "link_not_found")
    r = await tus_create(client, upload_key, "x.txt", 3)
    assert (r.status_code, code_of(r)) == (404, "link_not_found")

    await unblock(settings)
    r = await client.get(f"/api/v1/public/download/{f['id']}", params={"key": read_key})
    assert (r.status_code, r.content) == (200, b"geheim")
    assert (await tus_create(client, upload_key, "x.txt", 3)).status_code == 201


@pytest.mark.anyio
async def test_linkupload_geblokkeerd_tijdens_de_laatste_bytes(
    client, admin, settings, fresh_limits
):
    """De controle onder de sloten vlak voor het neerzetten (link_open) kijkt ook naar de
    eigenaar: wordt die geblokkeerd terwijl de laatste PATCH al loopt, dan komt er niets bij."""
    headers, _ = admin
    anna, _ = await person(client, headers, EMAIL)
    root_id = (await root(client, anna))["folder"]["id"]
    folder = await mkdir(client, anna, root_id, "Map")
    link = await new_link(client, anna, folder, permission="upload")
    check = uploads.link_open(uuid.UUID(link["id"]), uuid.UUID(folder))
    async with maker(settings)() as session:
        await check(session)  # alles in orde: geen fout
    await block(settings)
    async with maker(settings)() as session:
        with pytest.raises(files.Gone):
            await check(session)


@pytest.fixture
def hub(monkeypatch):
    fresh = events.Hub()
    monkeypatch.setattr(events, "hub", fresh)
    return fresh


@pytest.mark.anyio
async def test_stroom_stopt_als_het_account_geblokkeerd_is(
    client, admin, settings, hub, monkeypatch
):
    """Geblokkeerd zonder de logins in te trekken: binnen één ping geen gebeurtenissen meer,
    niet pas als het access-token verloopt."""
    monkeypatch.setattr(live, "PING", 0.1)
    headers, _ = admin
    anna, anna_id = await person(client, headers, EMAIL)
    stream = Stream(anna)
    await stream.open()
    await stream.until("retry: 5000\n\n")
    assert await stream.until("\n\n") == ": ping\n\n"  # nog niet geblokkeerd: de stroom loopt
    await block(settings)
    await stream.end(seconds=2)
    assert hub.listeners(uuid.UUID(anna_id)) == 0
    hub.publish([uuid.UUID(anna_id)], "node.created", {"id": "te-laat"})
    assert "te-laat" not in stream.text
