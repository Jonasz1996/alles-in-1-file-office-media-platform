import json
import ssl
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from httpx import ASGITransport, AsyncClient

from app import acme
from app.certs import RETRY_START, CertManager, HttpsConfig
from app.main import app

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
    certs.configure(HttpsConfig(domain=DOMAIN))  # nieuwe instelling: teller opnieuw
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
    assert r.json()["domain"] == DOMAIN
    assert (await client.post("/api/v1/admin/https/renew", headers=headers)).status_code == 202
