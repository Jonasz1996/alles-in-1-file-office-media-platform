import asyncio
import json
import ssl
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app import acme
from app.certs import HINTS, MANUAL_RETRY, RETRY_START, CertManager, HttpsConfig, TooSoon, _hint
from app.main import app
from app.routers.admin import DOMAIN as DOMAIN_RE
from app.routers.admin import HttpsIn

DOMAIN = "app.example.nl"


def write_cert(certs: CertManager, domain: str, days: int = 90, age: int = 0) -> None:
    key = acme.new_key()
    start = datetime.now(UTC) - timedelta(days=age)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    certs.dir.mkdir(parents=True, exist_ok=True)
    (certs.dir / "key.pem").write_bytes(acme.key_to_pem(key))
    (certs.dir / "cert.pem").write_bytes(cert.public_bytes(Encoding.PEM))
    (certs.dir / "cert.json").write_text(json.dumps({"directory": certs.directory}))


@pytest.mark.anyio
async def test_actief_en_vernieuwen(certs):
    certs.config = HttpsConfig(domain=DOMAIN)
    assert not certs.active and certs.needs_certificate()
    write_cert(certs, DOMAIN, days=90, age=10)
    assert certs.load_files()
    assert certs.active and certs.has_cert.is_set()
    assert not certs.needs_certificate()
    assert certs.renew_at == certs.not_after - timedelta(days=30)
    assert certs.needs_certificate(now=certs.not_after - timedelta(days=29))
    # Ander domein of andere CA (staging -> productie): opnieuw aanvragen
    certs.config = HttpsConfig(domain="ander.example.nl")
    assert not certs.active
    certs.config = HttpsConfig(domain=DOMAIN, staging=True)
    assert not certs.active


@pytest.mark.anyio
async def test_vernieuwd_certificaat_in_dezelfde_context(certs):
    certs.config = HttpsConfig(domain=DOMAIN)
    write_cert(certs, DOMAIN)
    certs.load_files()
    context = certs.context
    write_cert(certs, DOMAIN, days=60)
    certs.load_files()
    assert certs.context is context
    assert certs.not_after - certs.not_before == timedelta(days=60)


@pytest.mark.anyio
async def test_kapotte_sleutel_laat_oud_certificaat_staan(certs):
    certs.config = HttpsConfig(domain=DOMAIN)
    write_cert(certs, DOMAIN)
    certs.load_files()
    before = certs.not_after
    good_key = (certs.dir / "key.pem").read_bytes()
    write_cert(certs, DOMAIN, days=30)
    (certs.dir / "key.pem").write_bytes(good_key)  # past niet bij het nieuwe certificaat
    with pytest.raises(ssl.SSLError):
        certs.load_files()
    assert certs.not_after == before


@pytest.mark.anyio
async def test_check_fout_met_uitleg_en_wachttijd(certs, monkeypatch):
    assert await certs.check() > 0 and certs.state == "uit"
    certs.config = HttpsConfig(domain=DOMAIN)

    async def faal():
        raise acme.AcmeError("DNS problem: NXDOMAIN", {"type": "urn:ietf:params:acme:error:dns"})

    monkeypatch.setattr(certs, "obtain", faal)
    assert await certs.check() == RETRY_START
    assert certs.state == "fout" and "DNS" in certs.error
    assert await certs.check() == RETRY_START * 2
    # Opslaan zonder wijziging: de wachttijd loopt door en er komt geen extra poging
    certs.configure(HttpsConfig(domain=DOMAIN))
    assert not certs._wake.is_set() and certs.error
    assert await certs.check() == RETRY_START * 4
    certs.configure(HttpsConfig(domain=DOMAIN, email="ik@example.nl"))  # wel nieuw
    assert certs._wake.is_set() and certs.error is None
    assert await certs.check() == RETRY_START


@pytest.mark.anyio
async def test_doorsturen_naar_https_en_hsts(client, certs, settings):
    certs.config = HttpsConfig(domain=DOMAIN)
    write_cert(certs, DOMAIN)
    certs.load_files()
    settings.web_dist.mkdir(parents=True)
    (settings.web_dist / "index.html").write_text("<html>app</html>")

    # Nog geen https-listener: niet doorsturen
    r = await client.get("/beheer", headers={"Host": DOMAIN})
    assert r.status_code == 200

    certs.serving = True
    r = await client.get("/map/a%20b?x=1", headers={"Host": f"{DOMAIN}:80"})
    assert r.status_code == 308
    assert r.headers["location"] == f"https://{DOMAIN}/map/a%20b?x=1"
    # Via het IP-adres in je LAN blijft http werken
    r = await client.get("/beheer", headers={"Host": "192.168.1.20"})
    assert r.status_code == 200 and "strict-transport-security" not in r.headers
    # Challenges van Let's Encrypt gaan nooit naar https
    certs.challenges["tok"] = "tok.abc"
    r = await client.get("/.well-known/acme-challenge/tok", headers={"Host": DOMAIN})
    assert r.status_code == 200 and r.text == "tok.abc"
    assert (await client.get("/.well-known/acme-challenge/x")).status_code == 404

    async with AsyncClient(transport=ASGITransport(app=app), base_url=f"https://{DOMAIN}") as c:
        r = await c.get("/beheer")
        assert r.status_code == 200
        assert r.headers["strict-transport-security"] == "max-age=31536000"


@pytest.mark.anyio
async def test_cookie_secure_over_https(client, settings):
    from tests.conftest import ADMIN

    r = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert "secure" not in r.headers["set-cookie"].lower()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as c:
        r = await c.post("/api/v1/auth/login", json=ADMIN)
        assert "secure" in r.headers["set-cookie"].lower()


@pytest.mark.anyio
async def test_https_instellen_via_beheer(client, admin, certs):
    headers, _ = admin
    r = await client.get("/api/v1/admin/https", headers=headers)
    assert r.json()["domain"] == "" and r.json()["state"] == "uit"
    for bad in ("192.168.1.10", "localhost", "*.example.nl", "a b.nl", "-x.example.nl"):
        r = await client.put("/api/v1/admin/https", json={"domain": bad}, headers=headers)
        assert r.status_code == 422, bad
    assert (await client.post("/api/v1/admin/https/renew", headers=headers)).status_code == 409
    r = await client.put(
        "/api/v1/admin/https",
        json={"domain": " App.Example.NL. ", "email": "", "staging": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["domain"] == DOMAIN and r.json()["staging"]
    assert certs.config.domain == DOMAIN and certs._wake.is_set()
    r = await client.get("/api/v1/admin/https", headers=headers)
    assert r.json()["domain"] == DOMAIN and r.json()["listener_error"] is None
    assert (await client.post("/api/v1/admin/https/renew", headers=headers)).status_code == 202
    # Vlak na een poging: 429 met de reden en hoe lang nog
    certs.last_attempt = datetime.now(UTC)
    r = await client.post("/api/v1/admin/https/renew", headers=headers)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "too_soon" and r.json()["detail"]["message"]
    assert 0 < int(r.headers["retry-after"]) <= MANUAL_RETRY


def test_domein_met_punycode_topdomein():
    assert HttpsIn(domain="xn--80ak6aa92e.xn--p1ai").domain == "xn--80ak6aa92e.xn--p1ai"
    assert HttpsIn(domain="app.example.xn--90ais").domain == "app.example.xn--90ais"
    for bad in ("app.xn--", "app.xn--p1ai-", "app.123", "app.x-y"):
        with pytest.raises(ValidationError):
            HttpsIn(domain=bad)


def test_csr_zonder_cn_bij_lang_domein():
    long = "a" * 60 + ".voorbeeld.nl"
    assert len(long) > 64 and DOMAIN_RE.match(long)
    csr = x509.load_der_x509_csr(acme.csr_der(long, acme.new_key()))
    assert len(csr.subject) == 0
    san = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == [long]
    csr = x509.load_der_x509_csr(acme.csr_der(DOMAIN, acme.new_key()))
    assert csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == DOMAIN


ACME = "https://acme.test"


def fake_acme(*, new_order=None, failed_authz=None):
    """Nep-ACME-server voor httpx.MockTransport, tot en met de controle van het domein.
    new_order: eigen antwoord op newOrder; failed_authz: de authz na de controle."""
    nonce = {"Replay-Nonce": "nonce"}
    pending = {
        "status": "pending",
        "challenges": [{"type": "http-01", "url": f"{ACME}/chall", "token": "tok"}],
    }
    polls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/dir":
            names = ("newNonce", "newAccount", "newOrder")
            return httpx.Response(200, json={name: f"{ACME}/{name}" for name in names})
        if path == "/newNonce":
            return httpx.Response(200, headers=nonce)
        if path == "/newAccount":
            return httpx.Response(201, json={}, headers={**nonce, "Location": f"{ACME}/acct"})
        if path == "/newOrder":
            if new_order is not None:
                return new_order
            order = {"status": "pending", "authorizations": [f"{ACME}/authz"]}
            return httpx.Response(201, json=order, headers={**nonce, "Location": f"{ACME}/order"})
        if path == "/authz":
            polls.append(1)
            body = pending if len(polls) == 1 else failed_authz
            return httpx.Response(200, json=body, headers=nonce)
        if path == "/chall":
            return httpx.Response(200, json={}, headers=nonce)
        return httpx.Response(404, headers=nonce)

    return httpx.MockTransport(handler)


async def issue(transport: httpx.MockTransport) -> acme.AcmeError:
    async with httpx.AsyncClient(transport=transport) as http:
        client = acme.AcmeClient(f"{ACME}/dir", acme.new_key(), http, poll_interval=0)
        await client.register(None)
        with pytest.raises(acme.AcmeError) as info:
            await client.issue(DOMAIN, {})
    return info.value


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["connection", "dns", "unauthorized"])
async def test_uitleg_bij_mislukte_controle(kind):
    """Let's Encrypt en Pebble zetten de reden bij de challenge, niet bij de authz."""
    error = {"type": f"urn:ietf:params:acme:error:{kind}", "detail": "1.2.3.4: Timeout"}
    failed = {
        "status": "invalid",
        "challenges": [{"type": "http-01", "status": "invalid", "token": "tok", "error": error}],
    }
    e = await issue(fake_acme(failed_authz=failed))
    assert e.type == error["type"]
    assert str(e) == f"Controle van het domein mislukt: 1.2.3.4: Timeout ({kind})"
    assert _hint(e) == f"{HINTS[kind]} ({e})"


@pytest.mark.anyio
async def test_retry_after_bij_rate_limit(certs, monkeypatch):
    problem = {"type": "urn:ietf:params:acme:error:rateLimited", "detail": "Te veel"}
    limited = httpx.Response(429, json=problem, headers={"Retry-After": "3600"})
    e = await issue(fake_acme(new_order=limited))
    assert e.type == problem["type"] and e.retry_after == 3600
    later = format_datetime(datetime.now(UTC) + timedelta(hours=2), usegmt=True)
    seconds = acme._retry_after(httpx.Response(429, headers={"Retry-After": later}))
    assert 7190 < seconds <= 7200

    # De volgende poging wacht minstens zo lang, ook als je op de knop drukt
    certs.config = HttpsConfig(domain=DOMAIN)

    async def faal():
        raise acme.AcmeError("Te veel", problem, retry_after=7200)

    monkeypatch.setattr(certs, "obtain", faal)
    assert await certs.check() == 7200
    assert HINTS["rateLimited"] in certs.error
    certs.last_attempt -= timedelta(hours=1)
    with pytest.raises(TooSoon) as info:
        certs.retry_now()
    assert 7100 < info.value.retry_after <= 7200 and "Let's Encrypt" in str(info.value)
    certs.configure(HttpsConfig(domain="ander.example.nl"))  # ander domein: niet wachten
    certs.retry_now()


@pytest.mark.anyio
async def test_knop_hooguit_eens_per_minuut(certs):
    certs.config = HttpsConfig(domain=DOMAIN)
    certs.retry_now()  # nog nooit geprobeerd
    assert certs._wake.is_set()
    certs._wake.clear()
    certs.last_attempt = datetime.now(UTC) - timedelta(seconds=20)
    with pytest.raises(TooSoon) as info:
        certs.retry_now()
    assert info.value.retry_after == 40 and "40 seconden" in str(info.value)
    assert not certs._wake.is_set()
    certs.last_attempt -= timedelta(seconds=41)
    certs.retry_now()
    assert certs._wake.is_set()


@pytest.mark.anyio
async def test_geen_tweede_poging_tijdens_een_lopende(certs, monkeypatch):
    """Opslaan of de knop terwijl Let's Encrypt nog controleert, zet geen poging in de
    rij: elke mislukte controle telt mee voor de limiet (5 per uur)."""
    certs.config = HttpsConfig(domain=DOMAIN)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def obtain():
        calls.append(1)
        started.set()
        await release.wait()
        raise acme.AcmeError("Timeout", {"type": "urn:ietf:params:acme:error:connection"})

    monkeypatch.setattr(certs, "obtain", obtain)
    certs._task = asyncio.create_task(certs._run())
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert certs.state == "bezig"
        certs.configure(HttpsConfig(domain=DOMAIN))
        with pytest.raises(TooSoon):
            certs.retry_now()
        release.set()
        await asyncio.sleep(0.2)
        assert len(calls) == 1 and certs.state == "fout"
        assert certs.next_check > datetime.now(UTC) + timedelta(seconds=RETRY_START - 5)
        with pytest.raises(TooSoon):
            certs.retry_now()  # ook vlak erna nog niet
    finally:
        await certs.stop()


@pytest.mark.anyio
async def test_nieuwe_instelling_tijdens_een_poging(certs, monkeypatch):
    """Die poging was voor de oude instelling: telt niet mee, en er komt meteen een
    nieuwe voor het nieuwe domein."""
    certs.config = HttpsConfig(domain=DOMAIN)
    started, release = asyncio.Event(), asyncio.Event()
    domains = []

    async def obtain():
        domains.append(certs.config.domain)
        if len(domains) == 1:
            started.set()
            await release.wait()
        raise acme.AcmeError("Timeout", {"type": "urn:ietf:params:acme:error:connection"})

    monkeypatch.setattr(certs, "obtain", obtain)
    certs._task = asyncio.create_task(certs._run())
    try:
        await asyncio.wait_for(started.wait(), 5)
        certs.configure(HttpsConfig(domain="ander.example.nl"))
        release.set()
        await asyncio.sleep(0.2)
        assert domains == [DOMAIN, "ander.example.nl"]
        assert certs._failures == 1 and HINTS["connection"] in certs.error
    finally:
        await certs.stop()
