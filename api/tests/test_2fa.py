import asyncio
import os
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from argon2 import PasswordHasher
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import security, totp
from app.db import get_engine
from app.main import app
from app.models import User
from app.routers import auth
from app.security import SecurityPolicy, login_limiter
from tests.conftest import ADMIN
from tests.factories import message_of

LOGIN = {"email": ADMIN["email"], "password": ADMIN["password"]}
AN = {"email": "an@example.nl", "display_name": "An", "password": "lang-genoeg-456"}
API_DIR = Path(__file__).resolve().parent.parent


async def enable(client, headers, password=ADMIN["password"]):
    r = await client.post("/api/v1/auth/2fa/setup", headers=headers)
    assert r.status_code == 200, r.text
    setup = r.json()
    assert setup["qr"].startswith("data:image/svg+xml")
    code = totp.code_at(setup["secret"], totp.current_step())
    body = {"code": code, "password": password}
    r = await client.post("/api/v1/auth/2fa/enable", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return setup["secret"], r.json()["recovery_codes"]


def wrong_code(secret):
    """Een code die nu (en de komende stap) zeker niet klopt."""
    valid = {totp.code_at(secret, totp.current_step() + d) for d in (-1, 0, 1, 2)}
    return next(c for c in ("000000", "111111", "222222") if c not in valid)


async def login(client, user=LOGIN, **extra):
    login_limiter.reset()  # alsof elke poging van een ander IP-adres komt
    return await client.post("/api/v1/auth/login", json={**user, **extra})


async def refresh(client, tokens):
    body = {"refresh_token": tokens["refresh_token"]}
    return await client.post("/api/v1/auth/refresh", json=body)


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def user_row(settings, email=ADMIN["email"]) -> User:
    async with async_sessionmaker(get_engine(settings))() as session:
        return await session.scalar(select(User).where(User.email == email))


async def wait_is_over(settings, email=ADMIN["email"]):
    """Alsof de wachttijd van de blokkade voorbij is."""
    async with async_sessionmaker(get_engine(settings))() as session:
        await session.execute(
            update(User)
            .where(User.email == email)
            .values(totp_locked_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()


@pytest.mark.anyio
async def test_2fa_aanzetten_en_inloggen(client, admin):
    headers, _ = admin
    secret, recovery = await enable(client, headers)
    assert len(recovery) == 10
    assert (await client.get("/api/v1/auth/me", headers=headers)).json()["totp_enabled"]

    r = await client.post("/api/v1/auth/login", json=LOGIN)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "totp_required"

    r = await client.post("/api/v1/auth/login", json={**LOGIN, "code": wrong_code(secret)})
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
async def test_dezelfde_code_tegelijk_werkt_maar_een_keer(client, admin):
    headers, _ = admin
    secret, recovery = await enable(client, headers)
    for code in (totp.code_at(secret, totp.current_step() + 1), recovery[0]):
        login_limiter.reset()
        results = await asyncio.gather(
            *(client.post("/api/v1/auth/login", json={**LOGIN, "code": code}) for _ in range(3))
        )
        assert sorted(r.status_code for r in results) == [200, 401, 401]


@pytest.mark.anyio
async def test_2fa_aanzetten_vraagt_wachtwoord_en_code(client, admin, settings):
    headers, _ = admin
    body = {"code": "123456", "password": ADMIN["password"]}
    assert (
        await client.post("/api/v1/auth/2fa/enable", json=body, headers=headers)
    ).status_code == 409
    secret = (await client.post("/api/v1/auth/2fa/setup", headers=headers)).json()["secret"]
    code = totp.code_at(secret, totp.current_step())
    r = await client.post("/api/v1/auth/2fa/enable", json={"code": code}, headers=headers)
    assert r.status_code == 422
    # Fout wachtwoord of foute code: dezelfde melding, en beide tellen als foute poging
    for i, body in enumerate(
        [
            {"code": code, "password": "fout-wachtwoord"},
            {"code": wrong_code(secret), "password": ADMIN["password"]},
            *({"code": code, "password": f"fout-wachtwoord-{j}"} for j in range(3)),
        ]
    ):
        r = await client.post("/api/v1/auth/2fa/enable", json=body, headers=headers)
        assert r.status_code == 400, i
        assert message_of(r) == "Wachtwoord of code klopt niet"
    assert not (await client.get("/api/v1/auth/me", headers=headers)).json()["totp_enabled"]
    ok = {"code": code, "password": ADMIN["password"]}
    r = await client.post("/api/v1/auth/2fa/enable", json=ok, headers=headers)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "totp_locked"
    await wait_is_over(settings)
    r = await client.post("/api/v1/auth/2fa/enable", json=ok, headers=headers)
    assert r.status_code == 200, r.text
    assert (await user_row(settings)).totp_failures == 0


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
    r = await client.post("/api/v1/auth/2fa/disable", json=bad, headers=headers)
    assert r.status_code == 400
    assert message_of(r) == "Wachtwoord of code klopt niet"
    # Een fout wachtwoord geeft dezelfde melding: niet te zien wat er fout was
    bad = {"password": "fout-wachtwoord", "code": nieuw[0]}
    r = await client.post("/api/v1/auth/2fa/disable", json=bad, headers=headers)
    assert r.status_code == 400
    assert message_of(r) == "Wachtwoord of code klopt niet"
    ok = {"password": ADMIN["password"], "code": nieuw[0]}
    assert (
        await client.post("/api/v1/auth/2fa/disable", json=ok, headers=headers)
    ).status_code == 204
    assert (await client.post("/api/v1/auth/login", json=LOGIN)).status_code == 200


@pytest.mark.anyio
async def test_beheerder_zet_2fa_uit(client, admin, settings):
    headers, tokens = admin
    await client.post("/api/v1/users", json=AN, headers=headers)
    first = (await login(client, AN)).json()
    an = bearer(first)
    secret, recovery = await enable(client, an, AN["password"])
    second = (await login(client, AN, code=recovery[0])).json()
    for _ in range(5):
        await login(client, AN, code=wrong_code(secret))
    an_id = first["user"]["id"]
    assert (await client.delete(f"/api/v1/users/{an_id}/2fa", headers=an)).status_code == 403
    assert (await client.delete(f"/api/v1/users/{an_id}/2fa", headers=headers)).status_code == 204
    users = (await client.get("/api/v1/users", headers=headers)).json()
    assert not next(u for u in users if u["id"] == an_id)["totp_enabled"]
    # De blokkade is weg en alle logins van An zijn ingetrokken; die van de beheerder niet
    row = await user_row(settings, AN["email"])
    assert row.totp_failures == 0 and row.totp_locked_until is None
    assert (await refresh(client, first)).status_code == 401
    assert (await refresh(client, second)).status_code == 401
    assert (await refresh(client, tokens)).status_code == 200
    assert (await login(client, AN)).status_code == 200


def test_wachttijd_verdubbelt_tot_hooguit_een_dag():
    assert auth.lock_time(5) == timedelta(minutes=1)
    assert auth.lock_time(6) == timedelta(minutes=2)
    assert auth.lock_time(15) == timedelta(minutes=1024)
    assert auth.lock_time(16) == timedelta(hours=24)
    assert auth.lock_time(10_000) == timedelta(hours=24)


@pytest.mark.anyio
async def test_na_5_foute_codes_geblokkeerd(client, admin, settings):
    headers, _ = admin
    secret, _ = await enable(client, headers)
    bad = wrong_code(secret)
    for _ in range(5):
        r = await login(client, code=bad)
        assert r.json()["detail"]["code"] == "totp_invalid"
    # Geblokkeerd: ook de goede code werkt nu niet, en de poging telt niet mee
    good = totp.code_at(secret, totp.current_step() + 1)
    r = await login(client, code=good)
    assert r.status_code == 429
    assert message_of(r) == "Te veel foute codes. Probeer het over 1 minuut opnieuw."
    assert r.json()["detail"]["code"] == "totp_locked"
    assert 55 <= int(r.headers["retry-after"]) <= 60
    assert (await client.get("/api/v1/auth/me", headers=headers)).json()["totp_locked_until"]
    # Met alleen het wachtwoord hoor je het al vóór het intypen van een code; met een fout
    # wachtwoord niet (dat verraadt niets over het account)
    r = await login(client)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "totp_locked"
    assert 55 <= int(r.headers["retry-after"]) <= 60
    r = await login(client, {**LOGIN, "password": "fout-wachtwoord"})
    assert r.status_code == 401 and message_of(r) == "E-mailadres of wachtwoord klopt niet"
    assert (await user_row(settings)).totp_failures == 5

    # Na de wachttijd weer een fout: nu 2 minuten
    await wait_is_over(settings)
    assert (await login(client, code=bad)).status_code == 401
    r = await login(client, code=good)
    assert r.status_code == 429
    assert "over 2 minuten" in r.json()["detail"]["message"]
    assert 115 <= int(r.headers["retry-after"]) <= 120

    # De goede code na de wachttijd zet de teller op nul
    await wait_is_over(settings)
    assert (await login(client, code=good)).status_code == 200
    row = await user_row(settings)
    assert row.totp_failures == 0 and row.totp_locked_until is None
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert me["totp_locked_until"] is None


@pytest.mark.anyio
async def test_gelijktijdig_raden_omzeilt_de_blokkade_niet(client, admin, settings):
    headers, _ = admin
    secret, _ = await enable(client, headers)
    codes = [f"{i:06d}" for i in range(20)]
    login_limiter.reset()
    results = await asyncio.gather(
        *(client.post("/api/v1/auth/login", json={**LOGIN, "code": c}) for c in codes)
    )
    statuses = [r.status_code for r in results]
    # Hooguit 5 pogingen zijn echt gecontroleerd; de rest kreeg 429 (of de IP-limiet)
    assert len([s for s in statuses if s in (200, 401)]) <= 5
    assert (await user_row(settings)).totp_failures <= 5


@pytest.mark.anyio
async def test_alleen_foute_pogingen_tellen(client, admin):
    headers, _ = admin
    secret, recovery = await enable(client, headers)
    bad = wrong_code(secret)
    for code in recovery[:6]:
        assert (await login(client, code=bad)).status_code == 401
        assert (await login(client, code=code)).status_code == 200
    # Na een geslaagde login begint de teller opnieuw
    for _ in range(4):
        assert (await login(client, code=bad)).status_code == 401
    assert (await login(client, code=recovery[6])).status_code == 200


@pytest.mark.anyio
async def test_blokkade_geldt_voor_elke_controle(client, admin, settings, monkeypatch):
    headers, _ = admin
    secret, recovery = await enable(client, headers)
    # Foute wachtwoorden bij het uitzetten tellen ook als foute poging
    for i in range(5):
        body = {"password": f"fout-wachtwoord-{i}", "code": recovery[0]}
        r = await client.post("/api/v1/auth/2fa/disable", json=body, headers=headers)
        assert r.status_code == 400
    r = await login(client, code=recovery[0])
    assert r.status_code == 429 and r.json()["detail"]["code"] == "totp_locked"

    async def no_argon2(*args):
        raise AssertionError("argon2 terwijl de gebruiker geblokkeerd is")

    monkeypatch.setattr(auth, "check_password", no_argon2)
    ok = {"password": ADMIN["password"], "code": recovery[0]}
    r = await client.post("/api/v1/auth/2fa/disable", json=ok, headers=headers)
    assert r.status_code == 429 and "retry-after" in r.headers
    r = await client.post(
        "/api/v1/auth/2fa/recovery-codes", json={"code": recovery[0]}, headers=headers
    )
    assert r.status_code == 429
    monkeypatch.undo()

    # Tijdens de blokkade is er niets gecontroleerd of verbruikt
    await wait_is_over(settings)
    r = await client.post("/api/v1/auth/2fa/disable", json=ok, headers=headers)
    assert r.status_code == 204


@pytest.mark.anyio
async def test_met_alleen_een_sessie_blijft_inloggen_geblokkeerd(client, admin, settings):
    """Bewust zo gekozen (zie FREE_ATTEMPTS): wie alleen een sessie heeft, houdt met één foute
    poging per dag ook het inloggen geblokkeerd. De beheerder of de CLI heft het op."""
    headers, _ = admin
    secret, recovery = await enable(client, headers)
    async with async_sessionmaker(get_engine(settings))() as session:
        await session.execute(
            update(User).where(User.email == ADMIN["email"]).values(totp_failures=15)
        )
        await session.commit()
    r = await client.post(
        "/api/v1/auth/2fa/recovery-codes", json={"code": wrong_code(secret)}, headers=headers
    )
    assert r.status_code == 400
    r = await login(client, code=recovery[0])
    assert r.status_code == 429
    assert "over 1440 minuten" in r.json()["detail"]["message"]
    assert 86_390 <= int(r.headers["retry-after"]) <= 86_400

    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]
    r = await client.delete(f"/api/v1/users/{user_id}/2fa", headers=headers)
    assert r.status_code == 204
    row = await user_row(settings)
    assert row.totp_failures == 0 and row.totp_locked_until is None
    assert (await login(client)).status_code == 200


@pytest.mark.anyio
async def test_code_met_andere_cijfers_geeft_geen_500(client, admin):
    headers, _ = admin
    secret, _ = await enable(client, headers)
    code = totp.code_at(secret, totp.current_step() + 1)
    wide = code.translate({ord(c): ord(c) + 0xFEE0 for c in "0123456789"})  # '１２３４５６'
    r = await login(client, code=wide)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "totp_invalid"


@pytest.mark.anyio
async def test_argon2_draait_niet_op_de_event_loop(client, monkeypatch):
    loop_thread = threading.current_thread()
    threads = []

    def spy(fn):
        def wrapper(*args):
            threads.append(threading.current_thread())
            return fn(*args)

        return wrapper

    monkeypatch.setattr(PasswordHasher, "hash", spy(PasswordHasher.hash))
    monkeypatch.setattr(PasswordHasher, "verify", spy(PasswordHasher.verify))
    tokens = (await client.post("/api/v1/auth/setup", json=ADMIN)).json()  # hash
    headers = bearer(tokens)
    await client.post("/api/v1/users", json=AN, headers=headers)  # hash
    assert (await login(client, AN)).status_code == 200  # verify
    _, recovery = await enable(client, headers)  # verify
    body = {"password": ADMIN["password"], "code": recovery[0]}
    await client.post("/api/v1/auth/2fa/disable", json=body, headers=headers)  # verify
    assert all(t is not loop_thread for t in threads)
    assert len(threads) == 5


@pytest.mark.anyio
async def test_onbekend_adres_draait_ook_argon2(client, admin, monkeypatch):
    """Anders zie je aan de tijd (~100 ms verschil) of een account bestaat."""
    hashes = []
    verify = PasswordHasher.verify

    def spy(hasher, password_hash, password):
        hashes.append(password_hash)
        return verify(hasher, password_hash, password)

    monkeypatch.setattr(PasswordHasher, "verify", spy)
    r = await login(client, {"email": "niemand@example.nl", "password": ADMIN["password"]})
    assert r.status_code == 401
    assert message_of(r) == "E-mailadres of wachtwoord klopt niet"
    assert len(hashes) == 1 and hashes[0].startswith("$argon2id$")
    assert not await security.check_password(None, ADMIN["password"])


@pytest.mark.anyio
async def test_2fa_aan_of_uit_trekt_andere_logins_in(client, admin):
    headers, tokens = admin
    other = (await login(client)).json()
    _, recovery = await enable(client, headers)
    assert (await refresh(client, other)).status_code == 401
    r = await refresh(client, tokens)  # de login waarmee 2FA aanging blijft
    assert r.status_code == 200
    current = r.json()

    other = (await login(client, code=recovery[0])).json()
    body = {"password": ADMIN["password"], "code": recovery[1]}
    r = await client.post("/api/v1/auth/2fa/disable", json=body, headers=bearer(current))
    assert r.status_code == 204
    assert (await refresh(client, other)).status_code == 401
    assert (await refresh(client, current)).status_code == 200


@pytest.fixture
def verplicht(client):
    """2FA verplicht, zoals na een installatie: de instelling komt uit de (lege) database."""
    app.state.security = SecurityPolicy()


@pytest.mark.anyio
async def test_eerste_beheerder_moet_eerst_2fa_aanzetten(client, verplicht):
    r = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert r.status_code == 201
    tokens = r.json()
    assert tokens["user"]["totp_setup_required"] is True
    headers = bearer(tokens)
    for path in ("/api/v1/files/root", "/api/v1/users", "/api/v1/admin/security"):
        r = await client.get(path, headers=headers)
        assert r.status_code == 403, path
        assert r.json()["detail"] == {
            "message": "Zet eerst 2FA aan voor je account.",
            "code": "totp_setup_required",
        }
    # Wel: inloggen met alleen het wachtwoord, /auth/me, verversen en 2FA aanzetten
    r = await login(client)
    assert r.status_code == 200 and r.json()["user"]["totp_setup_required"]
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert me["totp_setup_required"] and not me["totp_enabled"]
    r = await refresh(client, tokens)
    assert r.status_code == 200
    tokens = r.json()
    await enable(client, headers)
    assert (await client.get("/api/v1/files/root", headers=headers)).status_code == 200
    assert not (await client.get("/api/v1/auth/me", headers=headers)).json()["totp_setup_required"]
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 204


@pytest.mark.anyio
async def test_beheerder_zet_verplichte_2fa_uit(client, verplicht):
    headers = bearer((await client.post("/api/v1/auth/setup", json=ADMIN)).json())
    await enable(client, headers)
    r = await client.get("/api/v1/admin/security", headers=headers)
    assert r.status_code == 200 and r.json() == {"require_2fa": True}
    r = await client.post("/api/v1/users", json=AN, headers=headers)
    assert r.json()["totp_setup_required"]
    an = bearer((await login(client, AN)).json())
    assert (await client.get("/api/v1/files/root", headers=an)).status_code == 403

    r = await client.put("/api/v1/admin/security", json={"require_2fa": False}, headers=headers)
    assert r.status_code == 200 and r.json() == {"require_2fa": False}
    assert (await client.get("/api/v1/files/root", headers=an)).status_code == 200
    # Bewaard in de database: ook na een herstart
    app.state.security = SecurityPolicy()
    r = await client.get("/api/v1/admin/security", headers=headers)
    assert r.json() == {"require_2fa": False}
    assert (await client.get("/api/v1/files/root", headers=an)).status_code == 200
    # Alleen een beheerder mag het aanpassen
    r = await client.put("/api/v1/admin/security", json={"require_2fa": True}, headers=an)
    assert r.status_code == 403

    r = await client.put("/api/v1/admin/security", json={"require_2fa": True}, headers=headers)
    assert r.status_code == 200
    assert (await client.get("/api/v1/files/root", headers=an)).status_code == 403
    # Wie 2FA heeft, merkt er niets van
    assert (await client.get("/api/v1/files/root", headers=headers)).status_code == 200


@pytest.mark.anyio
async def test_gelijktijdig_aanpassen_houdt_geheugen_en_database_gelijk(client, admin):
    headers, _ = admin
    results = await asyncio.gather(
        *(
            client.put("/api/v1/admin/security", json={"require_2fa": v}, headers=headers)
            for v in (False, True, False, True, False)
        )
    )
    assert all(r.status_code == 200 for r in results)
    cached = app.state.security.config
    app.state.security = SecurityPolicy()  # opnieuw uit de database
    await client.get("/api/v1/auth/me", headers=headers)
    assert app.state.security.config == cached


async def cli(settings, *args, python=()):
    env = {
        **os.environ,
        "PLATFORM_DATA_DIR": str(settings.data_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return await anyio.run_process(
        [sys.executable, *python, "-m", "app.cli", *args], cwd=API_DIR, env=env, check=False
    )


@pytest.mark.anyio
async def test_cli_zet_2fa_uit_voor_de_laatste_beheerder(client, admin, settings):
    headers, tokens = admin
    secret, _ = await enable(client, headers)
    for _ in range(5):
        await login(client, code=wrong_code(secret))

    result = await cli(settings, "reset-2fa", "JONAS@example.nl")
    assert result.returncode == 0, result.stderr.decode()
    assert "2FA staat uit voor JONAS@example.nl" in result.stdout.decode()
    row = await user_row(settings)
    assert row.totp_secret is None and row.totp_failures == 0 and row.totp_locked_until is None
    # Alle logins zijn ingetrokken; inloggen gaat weer met alleen het wachtwoord
    assert (await refresh(client, tokens)).status_code == 401
    assert (await login(client)).status_code == 200

    result = await cli(settings, "reset-2fa", "niemand@example.nl")
    assert result.returncode == 1
    assert "geen account" in result.stderr.decode()
    # Ook onder python -OO (zonder docstrings)
    result = await cli(settings, "reset-2fa", "niemand@example.nl", python=["-OO"])
    assert result.returncode == 1
    assert "geen account" in result.stderr.decode()
