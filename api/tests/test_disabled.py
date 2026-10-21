"""Een geblokkeerd account (User.disabled_at, hier rechtstreeks in de database gezet): kan niet
meer inloggen of verversen, een uitgegeven token werkt niet meer, en een downloadlink ook
niet. Zonder het juiste wachtwoord (en de code) is niet te zien dat het geblokkeerd is."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from app import totp
from app.models import User
from tests.factories import code_of, maker
from tests.test_2fa import enable, login
from tests.test_files import put, root
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
