import asyncio
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import totp
from app.db import get_engine
from app.main import app
from app.models import RefreshToken, User
from app.routers import auth
from app.security import SecurityPolicy, login_limiter
from app.useragent import device_label, shown_device
from tests.conftest import ADMIN
from tests.test_2fa import (
    AN,
    LOGIN,
    bearer,
    enable,
    login,
    refresh,
    user_row,
    wait_is_over,
    wrong_code,
)

FIREFOX_WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Mobile Safari/537.36"
)
EDGE_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)
SAFARI_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.0 Safari/605.1.15"
)
NEW = "nieuw-wachtwoord-789"
SESSIONS = "/api/v1/auth/sessions"
PASSWORD = "/api/v1/auth/password"


async def login_with(client, agent: str, user=LOGIN, **extra):
    """Inloggen vanuit een bepaalde browser (User-Agent)."""
    login_limiter.reset()
    return await client.post(
        "/api/v1/auth/login", json={**user, **extra}, headers={"User-Agent": agent}
    )


def family(tokens) -> str:
    """De login (refresh-family) waar deze tokens bij horen."""
    return jwt.decode(tokens["access_token"], options={"verify_signature": False})["fam"]


async def sessions(client, tokens) -> list[dict]:
    r = await client.get(SESSIONS, headers=bearer(tokens))
    assert r.status_code == 200, r.text
    return r.json()


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.fixture
async def elsewhere(client):
    """Een tweede client vanaf een ander IP-adres."""
    transport = ASGITransport(app=app, client=("192.0.2.7", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def add_an(client, headers) -> str:
    r = await client.post("/api/v1/users", json=AN, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_apparaatlabel_uit_user_agent():
    assert device_label(FIREFOX_WINDOWS) == "Firefox op Windows"
    assert device_label(SAFARI_IPHONE) == "Safari op iPhone"
    assert device_label(CHROME_ANDROID) == "Chrome op Android"
    assert device_label(EDGE_WINDOWS) == "Edge op Windows"
    assert device_label(SAFARI_MAC) == "Safari op Mac"
    assert device_label(SAFARI_IPHONE.replace("Version/18.0", "CriOS/130.0")) == "Chrome op iPhone"
    assert device_label(SAFARI_IPHONE.replace("Version/18.0", "FxiOS/131.0")) == "Firefox op iPhone"
    assert device_label(SAFARI_IPHONE.replace("iPhone; CPU iPhone OS", "iPad; CPU OS")) == (
        "Safari op iPad"
    )
    assert device_label(CHROME_ANDROID.replace("Chrome/", "SamsungBrowser/26.0 Chrome/")) == (
        "Samsung Internet op Android"
    )
    assert device_label(EDGE_WINDOWS.replace("Edg/", "OPR/")) == "Opera op Windows"
    assert device_label(
        "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
    ) == ("Firefox op Linux")
    assert device_label(
        "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ) == ("Chrome op ChromeOS")
    # Alleen het systeem bekend (een app), of niets
    assert device_label("Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/AP2A)") == "Android"
    for agent in ("curl/8.5.0", "python-httpx/0.28.1", "", None, "x" * 5000):
        assert device_label(agent) == "Onbekend apparaat"


def test_oude_sessies_krijgen_alsnog_een_label():
    # Van vóór deze versie: de hele User-Agent, of "setup" bij de eerste beheerder
    assert shown_device(FIREFOX_WINDOWS) == "Firefox op Windows"
    assert shown_device("curl/8.5.0") == "Onbekend apparaat"
    assert shown_device("setup") == "Onbekend apparaat"
    assert shown_device("  ") == "Onbekend apparaat"
    assert shown_device("Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/AP2A)") == "Android"
    # Een naam die de app zelf meegaf, blijft, ook als die op een User-Agent lijkt
    for name in ("Werklaptop", "Platform-app op Pixel 8", "PlatformApp/1.0", "Nextcloud-iOS/4.0"):
        assert shown_device(name) == name


async def stored_devices(settings, email=ADMIN["email"]) -> list[str]:
    """Wat er per uitgegeven refresh-token als apparaat is opgeslagen, oudste eerst."""
    async with async_sessionmaker(get_engine(settings))() as session:
        return list(
            await session.scalars(
                select(RefreshToken.device)
                .join(User, User.id == RefreshToken.user_id)
                .where(User.email == email)
                .order_by(RefreshToken.created_at)
            )
        )


@pytest.mark.anyio
async def test_setup_bewaart_een_apparaatlabel(client, settings):
    r = await client.post("/api/v1/auth/setup", json=ADMIN, headers={"User-Agent": SAFARI_MAC})
    assert r.status_code == 201, r.text
    assert await stored_devices(settings) == ["Safari op Mac"]


@pytest.mark.anyio
async def test_sessies_per_apparaat(client, admin, elsewhere, settings):
    headers, tokens = admin
    firefox = (await login_with(client, FIREFOX_WINDOWS)).json()
    laptop = (await login_with(client, FIREFOX_WINDOWS, device="  Werklaptop ")).json()
    iphone = (await login_with(elsewhere, SAFARI_IPHONE)).json()
    await add_an(client, headers)
    an = (await login(client, AN)).json()

    rows = await sessions(client, tokens)
    assert [s["device"] for s in rows] == [
        "Safari op iPhone",
        "Werklaptop",
        "Firefox op Windows",
        "Onbekend apparaat",  # de eerste beheerder, via httpx zonder browser
    ]
    assert [s["id"] for s in rows] == [family(t) for t in (iphone, laptop, firefox, tokens)]
    assert [s["ip"] for s in rows] == ["192.0.2.7", "127.0.0.1", "127.0.0.1", "127.0.0.1"]
    assert [s["current"] for s in rows] == [False, False, False, True]
    # Opgeslagen wordt het label (of de naam van de app), niet de hele User-Agent
    assert await stored_devices(settings) == [
        "Onbekend apparaat",
        "Firefox op Windows",
        "Werklaptop",
        "Safari op iPhone",
    ]
    # Van een ander apparaat bekeken staat de markering daar
    assert [s["current"] for s in await sessions(client, firefox)] == [False, False, True, False]
    for s in rows:
        assert parse(s["created_at"]).tzinfo is not None
        assert s["created_at"] == s["last_active_at"]
    started = rows[2]["created_at"]

    # Verversen (vanaf een ander IP-adres): laatst actief en IP gaan mee, de eerste login niet
    r = await elsewhere.post(
        "/api/v1/auth/refresh", json={"refresh_token": firefox["refresh_token"]}
    )
    assert r.status_code == 200, r.text
    firefox = r.json()
    rows = await sessions(client, tokens)
    assert rows[0]["device"] == "Firefox op Windows"
    assert rows[0]["ip"] == "192.0.2.7"
    assert rows[0]["created_at"] == started
    assert parse(rows[0]["last_active_at"]) > parse(started)
    async with async_sessionmaker(get_engine(settings))() as session:
        ips = (
            await session.scalars(
                select(RefreshToken.ip)
                .where(RefreshToken.family == uuid.UUID(family(firefox)))
                .order_by(RefreshToken.created_at)
            )
        ).all()
    assert ips == ["127.0.0.1", "192.0.2.7"]

    # Uitgelogd of verlopen: niet meer in de lijst
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": laptop["refresh_token"]})
    assert r.status_code == 204
    async with async_sessionmaker(get_engine(settings))() as session:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family == uuid.UUID(family(iphone)))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    rows = await sessions(client, tokens)
    assert [s["id"] for s in rows] == [family(firefox), family(tokens)]
    # Alleen je eigen sessies
    assert [s["id"] for s in await sessions(client, an)] == [family(an)]


@pytest.mark.anyio
async def test_eerste_login_blijft_de_begintijd(client, admin):
    headers, tokens = admin
    before = (await sessions(client, tokens))[0]
    for _ in range(3):
        r = await refresh(client, tokens)
        assert r.status_code == 200
        tokens = r.json()
    after = await sessions(client, tokens)
    assert len(after) == 1 and after[0]["id"] == before["id"] and after[0]["current"]
    assert after[0]["created_at"] == before["created_at"]
    assert parse(after[0]["last_active_at"]) > parse(before["last_active_at"])


@pytest.mark.anyio
async def test_sessie_uitloggen(client, admin):
    headers, tokens = admin
    other = (await login(client)).json()
    await add_an(client, headers)
    an = (await login(client, AN)).json()

    # Niet van jou, of onbekend: 404, en die sessie blijft werken
    for target in (family(an), str(uuid.uuid4())):
        r = await client.delete(f"{SESSIONS}/{target}", headers=headers)
        assert r.status_code == 404
        assert r.json()["detail"] == "Sessie niet gevonden"
    assert (await client.get("/api/v1/auth/me", headers=bearer(an))).status_code == 200

    r = await client.delete(f"{SESSIONS}/{family(other)}", headers=headers)
    assert r.status_code == 204
    # Ook het access-token van dat apparaat werkt meteen niet meer
    r = await client.get("/api/v1/auth/me", headers=bearer(other))
    assert r.status_code == 401
    assert r.json()["detail"] == "Je bent op dit apparaat uitgelogd; log opnieuw in"
    assert (await refresh(client, other)).status_code == 401
    assert [s["id"] for s in await sessions(client, tokens)] == [family(tokens)]
    # Nog een keer (dubbelklik) is geen fout
    assert (await client.delete(f"{SESSIONS}/{family(other)}", headers=headers)).status_code == 204

    # De eigen sessie mag ook: dan ben je hier uitgelogd
    r = await client.delete(f"{SESSIONS}/{family(tokens)}", headers=headers)
    assert r.status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    assert (await refresh(client, tokens)).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=bearer(an))).status_code == 200


@pytest.mark.anyio
async def test_overal_anders_uitloggen(client, admin):
    headers, tokens = admin
    a = (await login(client)).json()
    b = (await login_with(client, SAFARI_MAC)).json()
    r = await client.post(f"{SESSIONS}/revoke-others", headers=headers)
    assert r.status_code == 204
    for t in (a, b):
        assert (await client.get("/api/v1/auth/me", headers=bearer(t))).status_code == 401
        assert (await refresh(client, t)).status_code == 401
    r = await refresh(client, tokens)
    assert r.status_code == 200
    rows = await sessions(client, r.json())
    assert [(s["id"], s["current"]) for s in rows] == [(family(tokens), True)]


@pytest.mark.anyio
async def test_uitloggen_maakt_ook_het_access_token_ongeldig(client, admin):
    headers, tokens = admin
    assert (await client.get("/api/v1/files/root", headers=headers)).status_code == 200
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 204
    assert (await client.get("/api/v1/files/root", headers=headers)).status_code == 401


@pytest.mark.anyio
async def test_wachtwoord_wijzigen(client, admin, settings):
    headers, tokens = admin
    other = (await login(client)).json()

    body = {"current_password": "fout-wachtwoord", "new_password": NEW}
    r = await client.post(PASSWORD, json=body, headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "Je huidige wachtwoord klopt niet"
    assert (await user_row(settings)).totp_failures == 1

    for new, message in (
        ("kort", "Kies een wachtwoord van minstens 10 tekens"),
        (ADMIN["password"], "Kies een ander wachtwoord dan je huidige"),
    ):
        body = {"current_password": ADMIN["password"], "new_password": new}
        r = await client.post(PASSWORD, json=body, headers=headers)
        assert r.status_code == 422
        assert r.json()["detail"] == message
    assert (await user_row(settings)).totp_failures == 1  # dat telde niet als poging

    body = {"current_password": ADMIN["password"], "new_password": NEW}
    r = await client.post(PASSWORD, json=body, headers=headers)
    assert r.status_code == 204, r.text
    assert (await user_row(settings)).totp_failures == 0

    assert (await login(client)).status_code == 401
    assert (await login(client, {**LOGIN, "password": NEW})).status_code == 200
    # De andere sessie is uitgelogd, deze niet
    assert (await client.get("/api/v1/auth/me", headers=bearer(other))).status_code == 401
    assert (await refresh(client, other)).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    assert (await refresh(client, tokens)).status_code == 200


@pytest.mark.anyio
async def test_wachtwoord_raden_wordt_geblokkeerd(client, admin, settings, monkeypatch):
    """Ook zonder 2FA: wie alleen een sessie heeft, kan het wachtwoord niet blijven raden."""
    headers, _ = admin
    for i in range(5):
        body = {"current_password": f"fout-wachtwoord-{i}", "new_password": NEW}
        r = await client.post(PASSWORD, json=body, headers=headers)
        assert r.status_code == 400, i

    async def no_argon2(*args):
        raise AssertionError("argon2 terwijl de gebruiker geblokkeerd is")

    monkeypatch.setattr(auth, "check_password", no_argon2)
    ok = {"current_password": ADMIN["password"], "new_password": NEW}
    r = await client.post(PASSWORD, json=ok, headers=headers)
    assert r.status_code == 429
    assert r.json()["detail"] == {
        "message": "Te veel foute pogingen. Probeer het over 1 minuut opnieuw.",
        "code": "totp_locked",
    }
    assert 55 <= int(r.headers["retry-after"]) <= 60
    monkeypatch.undo()
    # Zonder 2FA blokkeert dit het inloggen niet
    assert (await login(client)).status_code == 200

    await wait_is_over(settings)
    r = await client.post(PASSWORD, json=ok, headers=headers)
    assert r.status_code == 204, r.text


@pytest.mark.anyio
async def test_gelijktijdig_raden_omzeilt_de_blokkade_niet(client, admin, settings, monkeypatch):
    headers, _ = admin
    checked = []
    real = auth.check_password

    async def counting(password_hash, password):
        checked.append(password)
        return await real(password_hash, password)

    monkeypatch.setattr(auth, "check_password", counting)
    results = await asyncio.gather(
        *(
            client.post(
                PASSWORD,
                json={"current_password": f"fout-{i}", "new_password": NEW},
                headers=headers,
            )
            for i in range(20)
        )
    )
    statuses = sorted(r.status_code for r in results)
    assert len(checked) <= 5
    assert statuses.count(400) == len(checked)
    assert statuses.count(429) == 20 - len(checked)
    assert (await user_row(settings)).totp_failures <= 5


@pytest.mark.anyio
async def test_wachtwoord_wijzigen_met_2fa(client, admin, settings):
    headers, tokens = admin
    secret, recovery = await enable(client, headers)

    body = {"current_password": ADMIN["password"], "new_password": NEW}
    r = await client.post(PASSWORD, json=body, headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == {
        "message": "Vul ook de code uit je authenticator-app in",
        "code": "totp_required",
    }
    assert (await user_row(settings)).totp_failures == 0

    # Fout wachtwoord of foute code: dezelfde melding, en beide tellen mee
    good = totp.code_at(secret, totp.current_step() + 1)
    for bad in (
        {**body, "code": wrong_code(secret)},
        {**body, "current_password": "fout-wachtwoord", "code": good},
    ):
        r = await client.post(PASSWORD, json=bad, headers=headers)
        assert r.status_code == 400
        assert r.json()["detail"] == "Wachtwoord of code klopt niet"
    assert (await user_row(settings)).totp_failures == 2

    r = await client.post(PASSWORD, json={**body, "code": good}, headers=headers)
    assert r.status_code == 204, r.text
    # Dezelfde code werkt geen tweede keer; een herstelcode wel, ook maar één keer
    again = {"current_password": NEW, "new_password": "nog-een-wachtwoord", "code": good}
    r = await client.post(PASSWORD, json=again, headers=headers)
    assert r.status_code == 400
    r = await client.post(PASSWORD, json={**again, "code": recovery[0]}, headers=headers)
    assert r.status_code == 204, r.text
    login_new = {**LOGIN, "password": "nog-een-wachtwoord"}
    assert (await login(client, login_new, code=recovery[0])).status_code == 401
    assert (await login(client, login_new, code=recovery[1])).status_code == 200


@pytest.mark.anyio
async def test_blokkade_gedeeld_met_2fa_uitzetten(client, admin):
    headers, _ = admin
    secret, recovery = await enable(client, headers)
    for i in range(3):
        body = {"current_password": f"fout-{i}", "new_password": NEW, "code": recovery[0]}
        assert (await client.post(PASSWORD, json=body, headers=headers)).status_code == 400
    for i in range(2):
        body = {"password": f"fout-{i}", "code": recovery[0]}
        r = await client.post("/api/v1/auth/2fa/disable", json=body, headers=headers)
        assert r.status_code == 400
    ok = {"current_password": ADMIN["password"], "new_password": NEW, "code": recovery[0]}
    r = await client.post(PASSWORD, json=ok, headers=headers)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "totp_locked"
    assert "Te veel foute codes" in r.json()["detail"]["message"]
    # En andersom: ook inloggen is nu geblokkeerd
    r = await login(client, code=recovery[0])
    assert r.status_code == 429


def pause_hashing(monkeypatch, password: str):
    """Laat het verzoek dat `password` als nieuw wachtwoord instelt wachten vlak vóór het
    rekenen aan de hash: het huidige wachtwoord is dan al gecontroleerd."""
    reached, release = asyncio.Event(), asyncio.Event()
    real = auth.new_password_hash

    async def paused(value):
        if value == password:
            reached.set()
            async with asyncio.timeout(10):
                await release.wait()
        return await real(value)

    monkeypatch.setattr(auth, "new_password_hash", paused)
    return reached, release


@pytest.mark.anyio
async def test_gelijktijdig_wachtwoord_wijzigen_wint_er_een(client, admin, monkeypatch):
    headers, _ = admin
    barrier = asyncio.Barrier(2)
    real = auth.new_password_hash

    async def together(value):
        # Beide verzoeken hebben het huidige wachtwoord al goed bevonden
        async with asyncio.timeout(10):
            await barrier.wait()
        return await real(value)

    monkeypatch.setattr(auth, "new_password_hash", together)
    passwords = ["eerste-nieuwe-ww", "tweede-nieuwe-ww"]
    results = await asyncio.gather(
        *(
            client.post(
                PASSWORD,
                json={"current_password": ADMIN["password"], "new_password": p},
                headers=headers,
            )
            for p in passwords
        )
    )
    assert sorted(r.status_code for r in results) == [204, 409]
    loser = next(r for r in results if r.status_code == 409)
    assert loser.json()["detail"] == "Je wachtwoord is net ergens anders gewijzigd; log opnieuw in"
    winner = passwords[[r.status_code for r in results].index(204)]
    monkeypatch.undo()
    for p in passwords:
        expected = 200 if p == winner else 401
        assert (await login(client, {**LOGIN, "password": p})).status_code == expected
    assert (await login(client)).status_code == 401


@pytest.mark.anyio
async def test_beheerder_stelt_wachtwoord_in_terwijl_gebruiker_wijzigt(client, admin, monkeypatch):
    headers, _ = admin
    an_id = await add_an(client, headers)
    an = (await login(client, AN)).json()
    reached, release = pause_hashing(monkeypatch, "van-an-zelf-123")
    body = {"current_password": AN["password"], "new_password": "van-an-zelf-123"}
    change = asyncio.create_task(client.post(PASSWORD, json=body, headers=bearer(an)))
    async with asyncio.timeout(10):
        await reached.wait()
    r = await client.put(
        f"/api/v1/users/{an_id}/password",
        json={"new_password": "van-de-beheerder"},
        headers=headers,
    )
    assert r.status_code == 204
    release.set()
    r = await change
    assert r.status_code == 409
    an_login = {"email": AN["email"], "password": "van-de-beheerder"}
    assert (await login(client, an_login)).status_code == 200
    assert (await login(client, {**an_login, "password": "van-an-zelf-123"})).status_code == 401


@pytest.mark.anyio
async def test_beheerder_stelt_wachtwoord_in(client, admin):
    headers, tokens = admin
    an_id = await add_an(client, headers)
    first = (await login(client, AN)).json()
    second = (await login_with(client, CHROME_ANDROID, AN)).json()
    url = f"/api/v1/users/{an_id}/password"
    own = f"/api/v1/users/{tokens['user']['id']}/password"
    new = {"new_password": NEW}

    assert (await client.put(own, json=new, headers=bearer(first))).status_code == 403
    r = await client.put(own, json=new, headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"] == "Gebruik je accountpagina om je eigen wachtwoord te wijzigen"
    r = await client.put(f"/api/v1/users/{uuid.uuid4()}/password", json=new, headers=headers)
    assert r.status_code == 404
    r = await client.put(url, json={"new_password": "kort"}, headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"] == "Kies een wachtwoord van minstens 10 tekens"
    assert (await client.get("/api/v1/auth/me", headers=bearer(first))).status_code == 200

    r = await client.put(url, json={"new_password": NEW}, headers=headers)
    assert r.status_code == 204, r.text
    # Alle sessies van An zijn meteen voorbij; die van de beheerder niet
    for t in (first, second):
        assert (await client.get("/api/v1/auth/me", headers=bearer(t))).status_code == 401
        assert (await refresh(client, t)).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    assert (await login(client, AN)).status_code == 401
    an_new = (await login(client, {**AN, "password": NEW})).json()
    assert [s["device"] for s in await sessions(client, an_new)] == ["Onbekend apparaat"]


@pytest.mark.anyio
async def test_refresh_tegelijk_geeft_geen_tweede_tak(client, admin, monkeypatch):
    """Hetzelfde refresh-token in drie verzoeken tegelijk: er komt er hooguit één nieuw
    token uit, en omdat dat op diefstal lijkt is de login daarna voorbij. Zonder dat liep
    de login in meerdere takken verder, en zag je hergebruik nooit meer."""
    headers, tokens = admin
    barrier = asyncio.Barrier(3)
    real = auth.family_revoked

    async def together(session, fam):
        # Alle drie hebben het token al gelezen (nog ongebruikt) voordat er één schrijft
        async with asyncio.timeout(10):
            await barrier.wait()
        return await real(session, fam)

    monkeypatch.setattr(auth, "family_revoked", together)
    body = {"refresh_token": tokens["refresh_token"]}
    results = await asyncio.gather(
        *(client.post("/api/v1/auth/refresh", json=body) for _ in range(3))
    )
    assert sorted(r.status_code for r in results) == [200, 401, 401]
    monkeypatch.undo()
    new = next(r for r in results if r.status_code == 200).json()
    assert (await refresh(client, new)).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=bearer(new))).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


@pytest.mark.anyio
async def test_ingetrokken_login_ververst_ook_met_een_niet_ingetrokken_token(
    client, admin, settings
):
    """Wordt een login ingetrokken terwijl er net een nieuw token werd uitgegeven, dan kan
    dat nieuwe token (in PostgreSQL) buiten het intrekken vallen. Eén ingetrokken token
    is genoeg: de hele login is voorbij."""
    headers, tokens = admin
    r = await refresh(client, tokens)
    newest = r.json()
    async with async_sessionmaker(get_engine(settings))() as session:
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family == uuid.UUID(family(tokens)),
                RefreshToken.used_at.is_not(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()
    assert (await refresh(client, newest)).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=bearer(newest))).status_code == 401
    other = (await login(client)).json()
    assert [s["id"] for s in await sessions(client, other)] == [family(other)]


@pytest.mark.anyio
async def test_eerst_2fa_aanzetten(client):
    """Is 2FA verplicht en staat het nog niet aan, dan eerst 2FA; ook hier."""
    app.state.security = SecurityPolicy()
    tokens = (await client.post("/api/v1/auth/setup", json=ADMIN)).json()
    body = {"current_password": ADMIN["password"], "new_password": NEW}
    for method, path, json in (
        ("GET", SESSIONS, None),
        ("POST", PASSWORD, body),
        ("POST", f"{SESSIONS}/revoke-others", None),
        ("DELETE", f"{SESSIONS}/{family(tokens)}", None),
    ):
        r = await client.request(method, path, json=json, headers=bearer(tokens))
        assert r.status_code == 403, path
        assert r.json()["detail"]["code"] == "totp_setup_required"


@pytest.mark.anyio
async def test_argon2_draait_niet_op_de_event_loop(client, admin, monkeypatch):
    headers, _ = admin
    an_id = await add_an(client, headers)
    loop_thread = threading.current_thread()
    threads = []

    def spy(fn):
        def wrapper(*args):
            threads.append(threading.current_thread())
            return fn(*args)

        return wrapper

    monkeypatch.setattr(PasswordHasher, "hash", spy(PasswordHasher.hash))
    monkeypatch.setattr(PasswordHasher, "verify", spy(PasswordHasher.verify))
    body = {"current_password": ADMIN["password"], "new_password": NEW}
    assert (await client.post(PASSWORD, json=body, headers=headers)).status_code == 204
    r = await client.put(
        f"/api/v1/users/{an_id}/password", json={"new_password": NEW}, headers=headers
    )
    assert r.status_code == 204
    assert len(threads) == 3  # verify + hash, hash
    assert all(t is not loop_thread for t in threads)


@pytest.mark.anyio
async def test_geen_wachtwoorden_of_tokens_in_het_log(client, admin, caplog):
    caplog.set_level(logging.DEBUG)
    headers, tokens = admin
    an_id = await add_an(client, headers)
    other = (await login(client)).json()
    hidden = [ADMIN["password"], NEW, "fout-wachtwoord", "van-de-beheerder"]
    hidden += [tokens["access_token"], tokens["refresh_token"]]
    hidden += [other["access_token"], other["refresh_token"]]

    bad = {"current_password": "fout-wachtwoord", "new_password": NEW}
    await client.post(PASSWORD, json=bad, headers=headers)
    ok = {"current_password": ADMIN["password"], "new_password": NEW}
    assert (await client.post(PASSWORD, json=ok, headers=headers)).status_code == 204
    r = await client.put(
        f"/api/v1/users/{an_id}/password",
        json={"new_password": "van-de-beheerder"},
        headers=headers,
    )
    assert r.status_code == 204
    await refresh(client, other)
    await client.delete(f"{SESSIONS}/{family(other)}", headers=headers)

    assert "heeft het wachtwoord gewijzigd" in caplog.text
    assert "heeft een nieuw wachtwoord ingesteld" in caplog.text
    # Zonder 2FA is een fout wachtwoord hier geen foute 2FA-poging
    assert "Fout wachtwoord bij wachtwoord wijzigen van gebruiker" in caplog.text
    assert "2FA-poging" not in caplog.text
    for secret in hidden:
        assert secret not in caplog.text


def pause_after_login_check(monkeypatch):
    """Laat de eerste wachtwoordcontrole (die van de login) wachten nadat het wachtwoord
    goed is bevonden: vlak vóór het uitgeven van de tokens. Latere controles (bijv. bij
    2FA aanzetten) lopen gewoon door."""
    reached, release = asyncio.Event(), asyncio.Event()
    real = auth.check_password
    calls = 0

    async def paused(password_hash, password):
        nonlocal calls
        calls += 1
        ok = await real(password_hash, password)
        if calls == 1:
            reached.set()
            async with asyncio.timeout(10):
                await release.wait()
        return ok

    monkeypatch.setattr(auth, "check_password", paused)
    return reached, release


async def live_tokens(settings, email) -> list[RefreshToken]:
    async with async_sessionmaker(get_engine(settings))() as session:
        return list(
            await session.scalars(
                select(RefreshToken)
                .join(User, User.id == RefreshToken.user_id)
                .where(User.email == email, RefreshToken.revoked_at.is_(None))
            )
        )


@pytest.mark.anyio
@pytest.mark.parametrize("by", ["beheerder", "zelf"])
async def test_login_tijdens_nieuw_wachtwoord_overleeft_het_niet(
    client, admin, settings, monkeypatch, by
):
    """Een login met het oude wachtwoord die al voorbij de controle is terwijl het
    wachtwoord wordt gewijzigd: daar mag geen sessie uit komen die het nieuwe wachtwoord
    overleeft (bijv. als de beheerder een overgenomen account herstelt)."""
    headers, _ = admin
    an_id = await add_an(client, headers)
    an = (await login(client, AN)).json()
    reached, release = pause_after_login_check(monkeypatch)
    pending = asyncio.create_task(login(client, AN))
    async with asyncio.timeout(10):
        await reached.wait()
    if by == "beheerder":
        r = await client.put(
            f"/api/v1/users/{an_id}/password", json={"new_password": NEW}, headers=headers
        )
    else:
        body = {"current_password": AN["password"], "new_password": NEW}
        r = await client.post(PASSWORD, json=body, headers=bearer(an))
    assert r.status_code == 204, r.text
    release.set()
    r = await pending
    assert r.status_code == 401
    assert r.json()["detail"] == "E-mailadres of wachtwoord klopt niet"
    expected = [] if by == "beheerder" else [uuid.UUID(family(an))]
    assert [t.family for t in await live_tokens(settings, AN["email"])] == expected


@pytest.mark.anyio
async def test_login_vlak_voor_nieuw_wachtwoord_wordt_ingetrokken(client, admin, monkeypatch):
    """Andersom: komt de login met het oude wachtwoord net vóór het nieuwe wachtwoord in de
    database, dan trekt het nieuwe wachtwoord ook die login in."""
    headers, _ = admin
    an_id = await add_an(client, headers)
    checked, release_login = pause_after_login_check(monkeypatch)
    pending = asyncio.create_task(login(client, AN))
    async with asyncio.timeout(10):
        await checked.wait()
    hashing, release_reset = pause_hashing(monkeypatch, NEW)
    reset = asyncio.create_task(
        client.put(f"/api/v1/users/{an_id}/password", json={"new_password": NEW}, headers=headers)
    )
    async with asyncio.timeout(10):
        await hashing.wait()
    release_login.set()
    r = await pending
    assert r.status_code == 200, r.text
    tokens = r.json()
    release_reset.set()
    assert (await reset).status_code == 204
    monkeypatch.undo()
    assert (await client.get("/api/v1/auth/me", headers=bearer(tokens))).status_code == 401
    assert (await refresh(client, tokens)).status_code == 401


@pytest.mark.anyio
async def test_login_tijdens_2fa_aanzetten_overleeft_het_niet(client, admin, settings, monkeypatch):
    """Ook 2FA aanzetten trekt alle andere logins in, ook een die er net tussendoor kwam
    zonder code."""
    headers, tokens = admin
    reached, release = pause_after_login_check(monkeypatch)
    pending = asyncio.create_task(login(client))
    async with asyncio.timeout(10):
        await reached.wait()
    await enable(client, headers)
    release.set()
    r = await pending
    assert r.status_code == 401
    assert [t.family for t in await live_tokens(settings, ADMIN["email"])] == [
        uuid.UUID(family(tokens))
    ]


def cookie(tokens) -> dict:
    """Zoals een browser het refresh-token meestuurt: als cookie, niet in de body."""
    return {"Cookie": f"{auth.REFRESH_COOKIE}={tokens['refresh_token']}"}


@pytest.mark.anyio
async def test_twee_tabbladen_verversen_tegelijk(client, admin, monkeypatch):
    """Twee tabbladen van één browser gaan tegelijk open en verversen met hetzelfde cookie.
    Eén krijgt nieuwe tokens; de ander refresh_raced, en de login blijft bestaan (vroeger
    werd hij ingetrokken en was je in beide tabbladen uitgelogd)."""
    headers, tokens = admin
    client.cookies.clear()
    barrier = asyncio.Barrier(2)
    real = auth.family_revoked

    async def together(session, fam):
        # Beide hebben het token al gelezen (nog ongebruikt) voordat er één schrijft
        async with asyncio.timeout(10):
            await barrier.wait()
        return await real(session, fam)

    monkeypatch.setattr(auth, "family_revoked", together)
    results = await asyncio.gather(
        *(client.post("/api/v1/auth/refresh", headers=cookie(tokens)) for _ in range(2))
    )
    monkeypatch.undo()
    assert sorted(r.status_code for r in results) == [200, 401]
    lost = next(r for r in results if r.status_code == 401)
    assert lost.json()["detail"] == {
        "message": "Je sessie is net in een ander tabblad vernieuwd; probeer het opnieuw",
        "code": "refresh_raced",
    }
    won = next(r for r in results if r.status_code == 200).json()
    # Het winnende tabblad blijft ingelogd, en het andere lukt het met het nieuwe cookie
    assert (await client.get("/api/v1/auth/me", headers=bearer(won))).status_code == 200
    r = await client.post("/api/v1/auth/refresh", headers=cookie(won))
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/auth/me", headers=bearer(won))).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=bearer(r.json()))).status_code == 200


@pytest.mark.anyio
async def test_tabblad_net_te_laat_en_echt_hergebruik(client, admin, settings):
    """Komt het tweede tabblad pas als het eerste al klaar is, dan ook refresh_raced. Wordt
    hetzelfde token na de marge nog eens aangeboden, dan is dat hergebruik: de hele login
    eindigt. Zo blijft een gestolen token opvallen."""
    _, tokens = admin
    client.cookies.clear()
    r = await client.post("/api/v1/auth/refresh", headers=cookie(tokens))
    assert r.status_code == 200
    newest = r.json()
    for _ in range(2):
        r = await client.post("/api/v1/auth/refresh", headers=cookie(tokens))
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "refresh_raced"
    assert (await client.get("/api/v1/auth/me", headers=bearer(newest))).status_code == 200

    async with async_sessionmaker(get_engine(settings))() as session:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.used_at.is_not(None))
            .values(used_at=datetime.now(UTC) - auth.REFRESH_RACE - timedelta(seconds=1))
        )
        await session.commit()
    r = await client.post("/api/v1/auth/refresh", headers=cookie(tokens))
    assert r.status_code == 401
    assert r.json()["detail"] == "Refresh-token is al gebruikt; log opnieuw in"
    assert (await client.get("/api/v1/auth/me", headers=bearer(newest))).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", headers=cookie(newest))).status_code == 401


@pytest.mark.anyio
async def test_app_die_een_token_dubbel_gebruikt_is_hergebruik(client, admin):
    """Een app stuurt het token in de body en houdt het zelf bij; daar is er geen marge."""
    _, tokens = admin
    client.cookies.clear()
    r = await refresh(client, tokens)
    assert r.status_code == 200
    newest = r.json()
    r = await refresh(client, tokens)
    assert r.status_code == 401
    assert r.json()["detail"] == "Refresh-token is al gebruikt; log opnieuw in"
    assert (await client.get("/api/v1/auth/me", headers=bearer(newest))).status_code == 401
