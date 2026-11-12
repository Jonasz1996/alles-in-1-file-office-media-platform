import pytest

from tests.conftest import ADMIN


@pytest.mark.anyio
async def test_setup_alleen_eerste_keer(client):
    assert (await client.get("/api/v1/auth/status")).json() == {"setup_required": True}
    r = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "admin"
    assert (await client.get("/api/v1/auth/status")).json() == {"setup_required": False}
    r = await client.post("/api/v1/auth/setup", json={**ADMIN, "email": "ander@example.nl"})
    assert r.status_code == 409


@pytest.mark.anyio
async def test_kort_wachtwoord_geweigerd(client):
    r = await client.post("/api/v1/auth/setup", json={**ADMIN, "password": "kort"})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_login_en_me(client, admin):
    r = await client.post(
        "/api/v1/auth/login", json={"email": "JONAS@example.nl", "password": ADMIN["password"]}
    )
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert me["email"] == "jonas@example.nl"
    assert me["root_id"]


@pytest.mark.anyio
async def test_fout_wachtwoord_en_rate_limit(client, admin):
    for _ in range(9):
        r = await client.post(
            "/api/v1/auth/login", json={"email": ADMIN["email"], "password": "verkeerd-wachtwoord"}
        )
        assert r.status_code == 401
    r = await client.post("/api/v1/auth/login", json={"email": ADMIN["email"], "password": "x"})
    assert r.status_code == 429


@pytest.mark.anyio
async def test_zonder_token_geen_toegang(client, admin):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    bad = {"Authorization": "Bearer onzin"}
    assert (await client.get("/api/v1/auth/me", headers=bad)).status_code == 401


@pytest.mark.anyio
async def test_refresh_roteert_en_detecteert_hergebruik(client, admin):
    _, tokens = admin
    first = tokens["refresh_token"]
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert r.status_code == 200
    second = r.json()["refresh_token"]
    assert second != first
    # Het oude token opnieuw gebruiken trekt de hele login in
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    ).status_code == 401
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": second})
    ).status_code == 401


@pytest.mark.anyio
async def test_refresh_via_cookie_en_logout(client, admin):
    client.cookies.clear()
    r = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]}
    )
    assert "platform_refresh" in r.cookies
    client.cookies.set("platform_refresh", r.cookies["platform_refresh"], path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    client.cookies.clear()
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


@pytest.mark.anyio
async def test_beheerder_maakt_gebruiker(client, admin):
    headers, _ = admin
    new = {
        "email": "kind@example.nl",
        "display_name": "Kind",
        "password": "ook-lang-genoeg",
        "quota_bytes": 1000,
    }
    r = await client.post("/api/v1/users", json=new, headers=headers)
    assert r.status_code == 201
    assert r.json()["quota_bytes"] == 1000
    r = await client.post(
        "/api/v1/auth/login", json={"email": new["email"], "password": new["password"]}
    )
    kind = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get("/api/v1/users", headers=kind)).status_code == 403
    assert len((await client.get("/api/v1/users", headers=headers)).json()) == 2
