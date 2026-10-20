import pytest

from app import totp
from app.security import login_limiter
from tests.conftest import ADMIN

LOGIN = {"email": ADMIN["email"], "password": ADMIN["password"]}


async def enable(client, headers):
    r = await client.post("/api/v1/auth/2fa/setup", headers=headers)
    assert r.status_code == 200, r.text
    setup = r.json()
    assert setup["qr"].startswith("data:image/svg+xml")
    code = totp.code_at(setup["secret"], totp.current_step())
    r = await client.post("/api/v1/auth/2fa/enable", json={"code": code}, headers=headers)
    assert r.status_code == 200, r.text
    return setup["secret"], r.json()["recovery_codes"]


@pytest.mark.anyio
async def test_2fa_aanzetten_en_inloggen(client, admin):
    headers, _ = admin
    secret, recovery = await enable(client, headers)
    assert len(recovery) == 10
    assert (await client.get("/api/v1/auth/me", headers=headers)).json()["totp_enabled"]

    r = await client.post("/api/v1/auth/login", json=LOGIN)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "totp_required"

    r = await client.post("/api/v1/auth/login", json={**LOGIN, "code": "000000"})
    assert r.json()["detail"]["code"] == "totp_invalid"

    # De code van het aanzetten is al gebruikt; de volgende stap werkt wel
    code = totp.code_at(secret, totp.current_step() + 1)
    r = await client.post("/api/v1/auth/login", json={**LOGIN, "code": code})
    assert r.status_code == 200, r.text
    # Dezelfde code nog een keer: geweigerd
    r = await client.post("/api/v1/auth/login", json={**LOGIN, "code": code})
    assert r.status_code == 401

    # Herstelcode werkt precies één keer
    r = await client.post("/api/v1/auth/login", json={**LOGIN, "code": recovery[0].upper()})
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/auth/login", json={**LOGIN, "code": recovery[0]})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_2fa_enable_met_foute_code(client, admin):
    headers, _ = admin
    assert (
        await client.post("/api/v1/auth/2fa/enable", json={"code": "123456"}, headers=headers)
    ).status_code == 409
    await client.post("/api/v1/auth/2fa/setup", headers=headers)
    r = await client.post("/api/v1/auth/2fa/enable", json={"code": "123456"}, headers=headers)
    assert r.status_code == 400
    assert not (await client.get("/api/v1/auth/me", headers=headers)).json()["totp_enabled"]


@pytest.mark.anyio
async def test_2fa_uitzetten_en_nieuwe_herstelcodes(client, admin):
    headers, _ = admin
    secret, recovery = await enable(client, headers)
    r = await client.post(
        "/api/v1/auth/2fa/recovery-codes", json={"code": recovery[1]}, headers=headers
    )
    assert r.status_code == 200
    nieuw = r.json()["recovery_codes"]
    assert set(nieuw).isdisjoint(recovery)
    # Oude herstelcode werkt niet meer
    bad = {"password": ADMIN["password"], "code": recovery[2]}
    assert (
        await client.post("/api/v1/auth/2fa/disable", json=bad, headers=headers)
    ).status_code == 400
    bad = {"password": "fout-wachtwoord", "code": nieuw[0]}
    assert (
        await client.post("/api/v1/auth/2fa/disable", json=bad, headers=headers)
    ).status_code == 400
    ok = {"password": ADMIN["password"], "code": nieuw[0]}
    assert (
        await client.post("/api/v1/auth/2fa/disable", json=ok, headers=headers)
    ).status_code == 204
    assert (await client.post("/api/v1/auth/login", json=LOGIN)).status_code == 200


@pytest.mark.anyio
async def test_beheerder_zet_2fa_uit(client, admin):
    headers, tokens = admin
    await client.post(
        "/api/v1/users",
        json={"email": "an@example.nl", "display_name": "An", "password": "lang-genoeg-456"},
        headers=headers,
    )
    r = await client.post(
        "/api/v1/auth/login", json={"email": "an@example.nl", "password": "lang-genoeg-456"}
    )
    an = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await enable(client, an)
    an_id = r.json()["user"]["id"]
    assert (await client.delete(f"/api/v1/users/{an_id}/2fa", headers=an)).status_code == 403
    assert (await client.delete(f"/api/v1/users/{an_id}/2fa", headers=headers)).status_code == 204
    users = (await client.get("/api/v1/users", headers=headers)).json()
    assert not next(u for u in users if u["id"] == an_id)["totp_enabled"]


@pytest.mark.anyio
async def test_2fa_codes_raden_wordt_afgeremd(client, admin):
    headers, _ = admin
    await enable(client, headers)
    statuses = []
    for i in range(12):
        login_limiter.reset()  # alsof elke poging van een ander IP-adres komt
        r = await client.post("/api/v1/auth/login", json={**LOGIN, "code": f"{i:06d}"})
        statuses.append(r.status_code)
    assert statuses[-1] == 429
