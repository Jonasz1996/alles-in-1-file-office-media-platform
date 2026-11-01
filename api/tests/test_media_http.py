"""De enige weg naar buiten (providers/http.py, Keuze 39, M8).

- een privé-adres na DNS geweigerd (ook verpakt in IPv6, en een antwoord met één privé-adres);
- DNS-rebinding: een nep-resolver geeft de eerste keer een publiek adres en de tweede keer
  127.0.0.1. De client verbindt met het eerste adres, met Host en `sni_hostname` op de naam, en
  zoekt binnen dat verzoek niet opnieuw op; na de cache weigert hij 127.0.0.1;
- TLS controleert de naam via `sni_hostname`: een echte TLS-server op 127.0.0.1 met een
  certificaat voor de naam werkt, een certificaat voor een andere naam niet;
- een omleiding naar een vreemde host geweigerd (zonder die host op te zoeken); de omleiding van
  de CAA gevolgd, met een nieuwe controle van het adres van archive.org;
- `Retry-After` met het plafond van 60 s, en één keer zelf wachten bij een korte;
- de stroomonderbreker: 5 fouten, 5 minuten dicht, één proef;
- een plaatje van 50 MB afgekapt (met en zonder Content-Length), gzip met een grens;
- de logregel zonder query (ook die van httpx zelf).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import email.utils
import gzip
import ipaddress
import logging
import re
import ssl
import uuid
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.config import Settings
from app.media.providers import http
from app.media.providers.base import ProviderAuth, ProviderRate, ProviderUnavailable
from tests.media.fake_providers import NEP, Data, FakeConfig, create_app, transport

KEY = "nep-sleutel-" + "x" * 24
PUBLIC = "93.184.216.34"
PUBLIC2 = "151.101.1.1"
TMDB = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
CAA = "https://coverartarchive.org"
RELEASE = str(uuid.uuid5(uuid.NAMESPACE_DNS, "http-test.nep"))
ARTIST = str(uuid.uuid5(uuid.NAMESPACE_DNS, "http-artist.nep"))

DOC: dict[str, Any] = {
    "_nep": NEP,
    "movies": [
        {
            "id": 900001,
            "title": "De Nephaven",
            "release_date": "2004-05-01",
            "poster_path": "/p900001.jpg",
        }
    ],
    "releases": [
        {"id": RELEASE, "title": "Nep", "artist": "Nep", "artist_id": ARTIST, "track_count": 3,
         "cover": True}
    ],
}  # fmt: skip


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Clock:
    """Een nepklok: sleep zet de tijd vooruit (en geeft de lus even de beurt)."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


class Resolver:
    """Per naam een rij antwoorden; het laatste blijft. Telt de vragen."""

    def __init__(self, answers: dict[str, list[list[str]]] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[str] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        queue = self.answers.get(host) or [[PUBLIC]]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def settings_for(tmp_path, **extra: Any) -> Settings:
    return Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32, **extra)


def outbound(tmp_path, *, handler=None, app=None, resolver=None, clock=None, **extra) -> Any:
    clock = clock or Clock()
    if app is not None:
        tr = transport(app)
    elif handler is not None:
        tr = httpx.MockTransport(handler)
    else:
        tr = None
    return http.Outbound(
        settings_for(tmp_path, **extra),
        resolver=resolver or Resolver(),
        transport=tr,
        clock=clock,
        sleep=clock.sleep,
    )


def fake(**config: Any):
    return create_app(FakeConfig(tmdb_key=KEY, data=Data.from_documents(DOC), **config))


AUTH = {"Authorization": f"Bearer {KEY}"}


# --- adressen ---


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd12:3456::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "2002:7f00:1::1",  # 6to4 van 127.0.0.1
        "2001:0:4136:e378:8000:63bf:80ff:fffe",  # Teredo met client 127.0.0.1
        "ff02::1",
        "fe80::1%eth0",
    ],
)
def test_geen_privé_of_bijzonder_adres(address):
    assert not http.address_ok(address)


@pytest.mark.parametrize("address", [PUBLIC, PUBLIC2, "2606:2800:220:1:248:1893:25c8:1946"])
def test_een_publiek_adres_mag(address):
    assert http.address_ok(address)


def test_privé_mag_alleen_in_tests_maar_nooit_0000_of_multicast():
    assert http.address_ok("127.0.0.1", private_ok=True)
    assert http.address_ok("::1", private_ok=True)
    assert not http.address_ok("0.0.0.0", private_ok=True)
    assert not http.address_ok("224.0.0.1", private_ok=True)


@pytest.mark.anyio
@pytest.mark.parametrize("answer", [["10.0.0.5"], [PUBLIC, "127.0.0.1"], ["::ffff:192.168.0.1"]])
async def test_privé_adres_na_dns_geweigerd(tmp_path, answer):
    sent = []
    resolver = Resolver({"api.themoviedb.org": [answer]})
    out = outbound(tmp_path, handler=lambda r: sent.append(r), resolver=resolver)
    with pytest.raises(ProviderUnavailable, match="adres niet toegestaan"):
        await out.fetch("tmdb", f"{TMDB}/configuration", headers=AUTH)
    assert sent == []  # nooit verbonden
    assert out.health["tmdb"].failures == 0  # wij weigeren: telt niet voor de onderbreker
    await out.aclose()


@pytest.mark.anyio
async def test_rebinding_verbindt_met_het_eerste_adres(tmp_path):
    """De nep-resolver geeft eerst een publiek adres, dan 127.0.0.1. Het verzoek gaat naar het
    gecontroleerde adres (Host en SNI op de naam); httpx zoekt zelf niets op."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"images": {}})

    clock = Clock()
    resolver = Resolver({"api.themoviedb.org": [[PUBLIC], ["127.0.0.1"]]})
    out = outbound(tmp_path, handler=handler, resolver=resolver, clock=clock)
    await out.fetch("tmdb", f"{TMDB}/configuration", headers=AUTH)
    assert resolver.calls == ["api.themoviedb.org"]
    [request] = seen
    assert request.url.host == PUBLIC
    assert request.url.scheme == "https"
    assert request.headers["host"] == "api.themoviedb.org"
    assert request.extensions["sni_hostname"] == "api.themoviedb.org"
    # binnen de cache: geen nieuwe vraag; daarna de tweede (127.0.0.1): geweigerd
    await out.fetch("tmdb", f"{TMDB}/configuration", headers=AUTH)
    assert resolver.calls == ["api.themoviedb.org"]
    clock.now += http.DNS_TTL + 1
    with pytest.raises(ProviderUnavailable, match="adres niet toegestaan"):
        await out.fetch("tmdb", f"{TMDB}/configuration", headers=AUTH)
    assert resolver.calls == ["api.themoviedb.org", "api.themoviedb.org"]
    assert [r.url.host for r in seen] == [PUBLIC, PUBLIC]
    await out.aclose()


@pytest.mark.anyio
async def test_alleen_de_diensten_uit_de_instellingen_en_https(tmp_path):
    sent = []
    out = outbound(tmp_path, handler=lambda r: sent.append(r))
    for provider, url in [
        ("tmdb", "https://evil.example/3/configuration"),
        ("tmdb", "https://api.themoviedb.org.evil.example/3/configuration"),
        ("tmdb", "http://api.themoviedb.org/3/configuration"),
        ("tmdb", "https://api.themoviedb.org:8443/3/configuration"),
        ("musicbrainz", f"{TMDB}/configuration"),  # een andere provider
        ("caa", "https://archive.org/download/x.jpg"),  # alleen als omleiding
        ("onbekend", f"{TMDB}/configuration"),
    ]:
        with pytest.raises(ProviderUnavailable):
            await out.fetch(provider, url)
    assert sent == []
    await out.aclose()


@pytest.mark.anyio
async def test_een_basis_met_een_pad(tmp_path):
    """Zoals de nep als proces: alles onder het voorvoegsel, niets ernaast."""
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={})

    out = outbound(
        tmp_path,
        handler=handler,
        media_tmdb_url="http://127.0.0.1:9/tmdb",
        media_provider_private_ok=True,
    )
    await out.fetch("tmdb", "http://127.0.0.1:9/tmdb/3/configuration")
    with pytest.raises(ProviderUnavailable, match="host niet toegestaan"):
        await out.fetch("tmdb", "http://127.0.0.1:9/mb/ws/2/release/")
    assert [r.url.path for r in sent] == ["/tmdb/3/configuration"]
    assert sent[0].headers["host"] == "127.0.0.1:9"
    assert "sni_hostname" not in sent[0].extensions  # http: geen TLS
    await out.aclose()


# --- TLS: de naam via sni_hostname ---


def _name(text: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])


def _certs(tmp_path, dns: str) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """Een eigen CA en een certificaat voor `dns`: (de context van de server, die van de
    client met alleen die CA)."""
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("nep-ca"))
        .issuer_name(_name("nep-ca"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(_name(dns))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(dns)]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .sign(ca_key, hashes.SHA256())
    )
    folder = tmp_path / f"tls-{dns}"
    folder.mkdir()
    (folder / "cert.pem").write_bytes(
        leaf.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM)
    )
    (folder / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(folder / "cert.pem", folder / "key.pem")
    client = ssl.create_default_context(cadata=ca.public_bytes(serialization.Encoding.PEM).decode())
    return server, client


async def _tls_server(context: ssl.SSLContext, names: list[str]):
    def sni(sock, name, ctx):
        names.append(name)

    context.sni_callback = sni

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 15\r\n"
                b'Connection: close\r\n\r\n{"images": {}}\n'
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0, ssl=context)


@pytest.mark.anyio
@pytest.mark.parametrize("cert_name, ok", [("api.themoviedb.org", True), ("evil.example", False)])
async def test_tls_controleert_de_naam_via_sni(tmp_path, cert_name, ok):
    """Een echte TLS-server op 127.0.0.1: de client verbindt met het adres, maar SNI en de
    controle van het certificaat gaan op de naam uit de instellingen."""
    server_ctx, client_ctx = _certs(tmp_path, cert_name)
    names: list[str] = []
    server = await _tls_server(server_ctx, names)
    port = server.sockets[0].getsockname()[1]
    try:
        resolver = Resolver({"api.themoviedb.org": [["127.0.0.1"]]})
        out = http.Outbound(
            settings_for(
                tmp_path,
                media_tmdb_url=f"https://api.themoviedb.org:{port}",
                media_provider_private_ok=True,
            ),
            resolver=resolver,
            verify=client_ctx,
        )
        url = f"https://api.themoviedb.org:{port}/3/configuration"
        if ok:
            fetched = await out.fetch("tmdb", url)
            assert fetched.data == {"images": {}}
        else:
            with pytest.raises(ProviderUnavailable, match="geen verbinding"):
                await out.fetch("tmdb", url)
        assert names == ["api.themoviedb.org"]
        await out.aclose()
    finally:
        server.close()
        await server.wait_closed()


# --- omleidingen ---


@pytest.mark.anyio
async def test_omleiding_naar_een_vreemde_host_geweigerd(tmp_path):
    app = fake()
    app.state.fake.fault("redirect_evil", "/release/*", host="caa")
    resolver = Resolver()
    out = outbound(tmp_path, app=app, resolver=resolver)
    with pytest.raises(ProviderUnavailable, match="vreemde host"):
        await out.fetch("caa", f"{CAA}/release/{RELEASE}/front-500", kind="image")
    assert "evil.example" not in resolver.calls
    await out.aclose()


@pytest.mark.anyio
async def test_de_omleiding_van_de_caa_met_een_nieuwe_controle(tmp_path):
    resolver = Resolver({"coverartarchive.org": [[PUBLIC]], "archive.org": [[PUBLIC2]]})
    out = outbound(tmp_path, app=fake(), resolver=resolver)
    fetched = await out.fetch("caa", f"{CAA}/release/{RELEASE}/front-500", kind="image")
    assert fetched.content_type == "image/jpeg"
    assert fetched.body[:3] == b"\xff\xd8\xff"
    assert fetched.url.startswith("https://archive.org/download/")
    assert resolver.calls == ["coverartarchive.org", "archive.org"]
    await out.aclose()
    # archive.org wijst nu naar een privé-adres: geweigerd, ook als omleiding
    resolver = Resolver({"coverartarchive.org": [[PUBLIC]], "archive.org": [["10.0.0.7"]]})
    out = outbound(tmp_path, app=fake(), resolver=resolver)
    with pytest.raises(ProviderUnavailable, match="adres niet toegestaan"):
        await out.fetch("caa", f"{CAA}/release/{RELEASE}/front-500", kind="image")
    await out.aclose()


@pytest.mark.anyio
async def test_hooguit_3_omleidingen_en_geen_sleutel_naar_een_andere_host(tmp_path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        n = len(seen)
        return httpx.Response(302, headers={"Location": f"https://ia{n}.archive.org/x{n}"})

    out = outbound(tmp_path, handler=handler)
    with pytest.raises(ProviderUnavailable, match="te veel omleidingen"):
        await out.fetch("tmdb", f"{TMDB}/configuration", headers=AUTH)
    assert len(seen) == 1 + http.MAX_REDIRECTS
    assert "authorization" in seen[0].headers
    assert all("authorization" not in r.headers for r in seen[1:])
    assert [r.headers["host"] for r in seen[1:]] == ["ia1.archive.org", "ia2.archive.org",
                                                     "ia3.archive.org"]  # fmt: skip
    await out.aclose()


# --- tempo, Retry-After en de stroomonderbreker ---


@pytest.mark.anyio
async def test_retry_after_met_een_plafond(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "3600"})

    clock = Clock()
    out = outbound(tmp_path, handler=handler, clock=clock)
    with pytest.raises(ProviderRate) as caught:
        await out.fetch("tmdb", f"{TMDB}/configuration", headers=AUTH)
    assert caught.value.retry_after == http.RETRY_CEILING
    # tot dan gaat er niets meer naar TMDB
    with pytest.raises(ProviderRate):
        await out.fetch("tmdb", f"{TMDB}/configuration", headers=AUTH)
    assert len(calls) == 1
    assert out.health["tmdb"].failures == 0  # afremmen is geen fout
    clock.now += http.RETRY_CEILING + 1
    with pytest.raises(ProviderRate):
        await out.fetch("tmdb", f"{TMDB}/configuration", headers=AUTH)
    assert len(calls) == 2
    await out.aclose()


@pytest.mark.parametrize(
    "value, seconds",
    [(None, http.RETRY_DEFAULT), ("", http.RETRY_DEFAULT), ("1", 1.0), ("2.5", 2.5),
     ("-5", 0.0), ("99999", 60.0), ("nan", http.RETRY_DEFAULT), ("morgen", http.RETRY_DEFAULT)],
)  # fmt: skip
def test_retry_after_lezen(value, seconds):
    assert http.retry_after(value) == seconds


def test_retry_after_als_datum():
    when = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30)
    seconds = http.retry_after(email.utils.format_datetime(when, usegmt=True))
    assert 25 <= seconds <= 31
    far = dt.datetime.now(dt.UTC) + dt.timedelta(days=2)
    assert http.retry_after(email.utils.format_datetime(far, usegmt=True)) == 60.0


@pytest.mark.anyio
async def test_een_korte_retry_after_wacht_de_client_zelf(tmp_path):
    """De nep-TMDB: 429 met Retry-After: 1; de client wacht 1 s en het lukt."""
    app = fake()
    app.state.fake.fault("429", "/3/search/movie", host="tmdb", retry_after=1)
    clock = Clock()
    out = outbound(tmp_path, app=app, clock=clock)
    fetched = await out.fetch(
        "tmdb", f"{TMDB}/search/movie", params={"query": "nephaven"}, headers=AUTH
    )
    assert fetched.data["results"][0]["id"] == 900001
    assert 1.0 in clock.slept
    await out.aclose()


@pytest.mark.anyio
async def test_musicbrainz_503_zonder_retry_after_is_afremmen(tmp_path):
    def handler(request):
        return httpx.Response(503, json={"error": "rate"})

    out = outbound(tmp_path, handler=handler)
    with pytest.raises(ProviderRate) as caught:
        await out.fetch("musicbrainz", "https://musicbrainz.org/ws/2/release/")
    assert caught.value.retry_after == http.RETRY_DEFAULT
    assert out.health["musicbrainz"].failures == 0
    await out.aclose()


@pytest.mark.anyio
async def test_de_token_bucket_per_dienst(tmp_path):
    clock = Clock()
    out = outbound(tmp_path, handler=lambda r: httpx.Response(200, json={}), clock=clock)
    start = clock.now
    for _ in range(3):
        await out.fetch("musicbrainz", "https://musicbrainz.org/ws/2/release/")
    # 1 per seconde (met een marge): de tweede en de derde wachten elk 1,1 s
    assert clock.now - start == pytest.approx(2.2, abs=0.01)
    start = clock.now
    for _ in range(10):
        await out.fetch("tmdb", f"{TMDB}/configuration")
    assert clock.now - start == 0  # TMDB: een burst van 10
    await out.fetch("tmdb", f"{TMDB}/configuration")
    assert clock.now - start == pytest.approx(0.1, abs=0.01)
    await out.aclose()


@pytest.mark.anyio
async def test_de_stroomonderbreker(tmp_path):
    calls = []
    answer = {"status": 500}

    def handler(request):
        calls.append(request)
        return httpx.Response(answer["status"], json={})

    clock = Clock()
    out = outbound(tmp_path, handler=handler, clock=clock)
    for _ in range(http.BREAKER_FAILURES):
        with pytest.raises(ProviderUnavailable, match="status 500"):
            await out.fetch("tmdb", f"{TMDB}/configuration")
    health = out.health["tmdb"]
    assert health.breaker == "open"
    assert health.last_error == "tmdb: status 500"
    assert health.failing_since is not None
    with pytest.raises(ProviderUnavailable, match="stroomonderbreker"):
        await out.fetch("tmdb", f"{TMDB}/configuration")
    assert len(calls) == 5  # dicht: geen verzoek
    # MusicBrainz staat er los van
    answer["status"] = 200
    await out.fetch("musicbrainz", "https://musicbrainz.org/ws/2/release/")
    # na 5 minuten één proef; die faalt: weer 5 minuten dicht
    answer["status"] = 502
    clock.now += http.BREAKER_OPEN
    with pytest.raises(ProviderUnavailable, match="status 502"):
        await out.fetch("tmdb", f"{TMDB}/configuration")
    assert health.breaker == "open"
    with pytest.raises(ProviderUnavailable, match="stroomonderbreker"):
        await out.fetch("tmdb", f"{TMDB}/configuration")
    assert len(calls) == 7
    # de volgende proef lukt: weer dicht (closed), en de teller op 0
    answer["status"] = 200
    clock.now += http.BREAKER_OPEN
    await out.fetch("tmdb", f"{TMDB}/configuration")
    assert health.breaker == "closed"
    assert health.failures == 0
    assert health.failing_since is None
    assert health.last_ok is not None
    await out.aclose()


@pytest.mark.anyio
async def test_404_en_401_tellen_niet_voor_de_onderbreker(tmp_path):
    status = {"value": 404}
    out = outbound(tmp_path, handler=lambda r: httpx.Response(status["value"], json={}))
    for _ in range(http.BREAKER_FAILURES + 1):
        with pytest.raises(http.NotFound):
            await out.fetch("tmdb", f"{TMDB}/movie/1")
    status["value"] = 401
    for _ in range(http.BREAKER_FAILURES + 1):
        with pytest.raises(ProviderAuth):
            await out.fetch("tmdb", f"{TMDB}/movie/1")
    assert out.health["tmdb"].breaker == "closed"
    assert out.health["tmdb"].auth_failed
    await out.aclose()


# --- grenzen ---


@pytest.mark.anyio
async def test_een_plaatje_van_50_mb_afgekapt(tmp_path):
    app = fake()
    app.state.fake.fault("huge", "/t/p/*", host="image", size_mb=50)
    out = outbound(tmp_path, app=app)
    with pytest.raises(ProviderUnavailable, match="te groot"):
        await out.fetch("tmdb", f"{IMG}/w500/p900001.jpg", kind="image")
    await out.aclose()


@pytest.mark.anyio
async def test_een_plaatje_zonder_lengte_stopt_bij_10_mb(tmp_path):
    produced = []

    async def endless():
        for _ in range(50):
            produced.append(1)
            yield b"\xff" * (1024 * 1024)

    def handler(request):
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=endless())

    out = outbound(tmp_path, handler=handler)
    with pytest.raises(ProviderUnavailable, match="te groot"):
        await out.fetch("tmdb", f"{IMG}/w500/p900001.jpg", kind="image")
    assert len(produced) <= 12  # 10 MB en een stuk, niet 50
    await out.aclose()


@pytest.mark.anyio
async def test_gzip_met_een_grens(tmp_path):
    bomb = gzip.compress(b"{" + b" " * (5 * 1024 * 1024) + b"}")
    small = gzip.compress(b'{"ok": true}')
    body = {"value": bomb}

    def handler(request):
        headers = {"Content-Type": "application/json", "Content-Encoding": "gzip"}
        return httpx.Response(200, headers=headers, content=body["value"])

    out = outbound(tmp_path, handler=handler)
    with pytest.raises(ProviderUnavailable, match="te groot"):
        await out.fetch("tmdb", f"{TMDB}/configuration")
    body["value"] = small
    assert (await out.fetch("tmdb", f"{TMDB}/configuration")).data == {"ok": True}
    await out.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind, reason",
    [
        ("garbage", "kapot antwoord"),
        ("drop", "geen verbinding"),
        ("500", "status 500"),
        ("wrongtype", "kapot antwoord"),
    ],
)
async def test_fouten_van_de_nep_tmdb(tmp_path, kind, reason):
    app = fake()
    app.state.fake.fault(kind, "/3/configuration", host="tmdb")
    out = outbound(tmp_path, app=app)
    with pytest.raises(ProviderUnavailable, match=reason):
        await out.fetch("tmdb", f"{TMDB}/configuration", headers=AUTH)
    assert out.health["tmdb"].failures == 1
    # de volgende keer weer goed
    assert (await out.fetch("tmdb", f"{TMDB}/configuration", headers=AUTH)).data["images"]
    await out.aclose()


@pytest.mark.anyio
async def test_plaatjes_alleen_jpeg_png_of_webp(tmp_path):
    app = fake()
    app.state.fake.fault("wrongtype", "/t/p/*", host="image")
    out = outbound(tmp_path, app=app)
    with pytest.raises(ProviderUnavailable, match="verkeerd type"):
        await out.fetch("tmdb", f"{IMG}/w500/p900001.jpg", kind="image")
    fetched = await out.fetch("tmdb", f"{IMG}/w500/p900001.jpg", kind="image")
    assert fetched.content_type == "image/jpeg"
    await out.aclose()


@pytest.mark.anyio
async def test_traag_is_een_time_out(tmp_path):
    app = fake()
    app.state.fake.fault("slow", "/3/configuration", host="tmdb", seconds=5)
    out = http.Outbound(
        settings_for(tmp_path), resolver=Resolver(), transport=transport(app), total_timeout=0.3
    )
    with pytest.raises(ProviderUnavailable, match="time-out"):
        await out.fetch("tmdb", f"{TMDB}/configuration", headers=AUTH)
    await out.aclose()


@pytest.mark.anyio
async def test_de_user_agent(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={})

    out = outbound(tmp_path, handler=handler)
    await out.fetch("musicbrainz", "https://musicbrainz.org/ws/2/release/")
    [agent] = seen
    assert re.match(r"^\S+/\S+ \( \S.* \)$", agent)
    assert agent.startswith("alles-in-1-platform/")
    assert "@" not in agent
    assert "github.com/Jonasz1996/alles-in-1-file-office-media-platform" in agent
    await out.aclose()


# --- de log ---


@pytest.mark.anyio
async def test_de_logregel_zonder_query_en_zonder_sleutel(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    out = outbound(tmp_path, app=fake())
    await out.fetch(
        "tmdb",
        f"{TMDB}/search/movie",
        params={"query": "GeheimeTitel", "year": 2004},
        headers=AUTH,
    )
    text = caplog.text
    assert "GeheimeTitel" not in text
    assert "query=" not in text
    assert KEY not in text
    assert re.search(r"provider tmdb api\.themoviedb\.org/3/search/movie 200 \d+ ms", text)
    # ook de regel van httpx zelf, zonder query
    assert any(r.name == "httpx" and "HTTP Request" in r.getMessage() for r in caplog.records)
    await out.aclose()


def test_de_fouttekst_zegt_nooit_meer_dan_de_reden():
    """Een header met een regeleinde geeft in httpx een fout met de waarde erin; bij ons komt
    daar nooit iets van door (de sleutel heeft al een vaste vorm, zie secrets)."""
    for text in ("geen verbinding", "time-out", "te groot", "kapot antwoord", "status 500"):
        error = ProviderUnavailable(f"tmdb: {text}")
        assert KEY not in str(error)


def test_ipaddress_kent_de_gevallen():
    """De stdlib-functies waarop address_ok steunt."""
    assert ipaddress.ip_address("2002:7f00:1::1").sixtofour == ipaddress.ip_address("127.0.0.1")
    assert ipaddress.ip_address("::ffff:127.0.0.1").ipv4_mapped is not None
