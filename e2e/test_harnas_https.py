"""Het harnas en https: trusting_browser (conftest.py) laat Chromium het zelfgetekende
certificaat van een testserver vertrouwen, zoals een echte browser Let's Encrypt vertrouwt. Dan
is er geen certificaatfout en dus ook geen herstart van een verzoek. Zonder server van de app:
kleine TLS-servers hier.

Waarom: met ignore_https_errors (hoe de https-tests van office het eerst deden) ziet Chromium op
elke nieuwe verbinding eerst een certificaatfout. DevTools staat hem toe en het verzoek begint
opnieuw; na 32 herstarts geeft Chromium het op (net::ERR_TOO_MANY_RETRIES). De socket pool geeft
de fout van een verbinding aan het wachtende verzoek met de hoogste prioriteit, ook als die
verbinding voor een ander verzoek werd geopend (late binding). Vermoedelijk faalde zo in CI (job
office, op 1a6dac0) een stylesheet: "Unable to preload CSS for …"; bewezen is het niet, zie
docs/fase-4/o1.md.

- test_trage_handshakes_…: precies dat mechanisme, met één certificaat zoals de server van de
  app. Deze server rondt de handshakes één voor één af, PACE uit elkaar. Zonder vertrouwen, met
  ignore_https_errors, faalt de stylesheet hier elke keer op net::ERR_TOO_MANY_RETRIES terwijl
  de fetches ernaast laden (handmatig nagegaan, o1.md). Met trusting_browser: niets afgewezen.
- test_een_browser_die_…: elke verbinding een nieuw certificaat (dezelfde sleutel): het
  vertrouwen hangt aan de sleutel, niet aan één certificaat.
- De controle zonder vertrouwen laat zien dat de server echt een onvertrouwd certificaat heeft."""

from __future__ import annotations

import queue
import socket
import ssl
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.x509.oid import NameOID

from conftest import trusting_browser

STYLESHEETS = 6
# Elke verbinding een certificaat dat nog niet voorkwam. Zonder trusting_browser, met
# ignore_https_errors (de oude opzet), faalt zo elke stylesheet: na 32 herstarts per verzoek,
# dus ruim meer dan STYLESHEETS * 32 certificaten
CERTS = 256
# Naast de stylesheet zoveel fetches tegelijk, zoals de chunks van Bestanden na Sluiten (in de
# netlog van de echte test kregen 13 tot 23 verzoeken een certificaatfout)
FETCHES = 20
# Seconden tussen twee handshakes. Zonder vertrouwen (ignore_https_errors) faalde de stylesheet
# bij 0,002 tot 0,05 s elke keer, in Chromium 141 en Chrome Headless Shell 141; met de handshakes
# wel één voor één maar zonder wachten 1 en 3 van de 5 keer (o1.md). Met vertrouwen duurt de
# test zo ongeveer een seconde.
PACE = 0.05

# Zoals de preload van Vite (shell-*.js): een link rel=stylesheet met crossOrigin, en bij
# error "Unable to preload CSS for …". Geeft per stylesheet "ok" of die melding.
PRELOAD_JS = """
async (urls) => Promise.all(urls.map((href) => new Promise((resolve) => {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.crossOrigin = "";
  link.href = href;
  link.addEventListener("load", () => resolve("ok"));
  link.addEventListener("error", () => resolve(`Unable to preload CSS for ${href}`));
  document.head.appendChild(link);
})))
"""

# Eén stylesheet (prioriteit HIGHEST) en de fetches (HIGH) tegelijk. Geeft [stylesheet, fetches].
SHEET_AND_FETCHES_JS = """
async ([href, urls]) => {
  const sheet = new Promise((resolve) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.crossOrigin = "";
    link.href = href;
    link.addEventListener("load", () => resolve("ok"));
    link.addEventListener("error", () => resolve(`Unable to preload CSS for ${href}`));
    document.head.appendChild(link);
  });
  const others = urls.map((u) => fetch(u, {cache: "no-store"}).then((r) => r.text()).then(
    () => "ok", (e) => String(e)));
  return [await sheet, await Promise.all(others)];
}
"""


@dataclass
class TlsServer:
    """Een https-server op 127.0.0.1 (certificaten voor platform.test, alle met dezelfde
    sleutel) die elk verzoek met een stylesheet beantwoordt. Verbinding n krijgt certificaat
    n % het aantal."""

    port: int
    first_cert: bytes
    # Handshakes die Chromium afbrak met een alert over het certificaat (hij wees het af)
    rejected: int = 0
    # Andere mislukte handshakes (de reden), voor de melding
    other_failures: list[str] = field(default_factory=list)
    requests: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def url(self, path: str) -> str:
        return f"https://127.0.0.1:{self.port}{path}"


def _contexts(directory: Path, count: int) -> tuple[list[ssl.SSLContext], bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    key_path = directory / "key.pem"
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "platform.test")])
    now = datetime.now(UTC)
    contexts, pems = [], []
    for i in range(count):
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("platform.test")]), False)
            .sign(key, hashes.SHA256())
        )
        pem = cert.public_bytes(Encoding.PEM)
        cert_path = directory / f"cert{i}.pem"
        cert_path.write_bytes(pem)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        contexts.append(context)
        pems.append(pem)
    return contexts, pems[0]


def _answer(
    server: TlsServer,
    conn: socket.socket,
    context: ssl.SSLContext,
    turn: threading.Event | None,
) -> None:
    with conn:
        # Met PACE: de handshake pas als deze verbinding aan de beurt is (de ClientHello
        # wacht zolang in de socket)
        if turn is not None and not turn.wait(30):
            return
        try:
            tls = context.wrap_socket(conn, server_side=True)
        except ssl.SSLError as e:
            # Wijst Chromium het certificaat af, dan stuurt hij het alert certificate_unknown
            with server.lock:
                if "CERTIFICATE_UNKNOWN" in str(e.reason):
                    server.rejected += 1
                else:
                    server.other_failures.append(f"{e.reason}")
            return
        except OSError as e:
            with server.lock:
                server.other_failures.append(repr(e))
            return
        with tls:
            reader = tls.makefile("rb")
            if not reader.readline():
                return
            while reader.readline() not in (b"\r\n", b"\n", b""):
                pass
            with server.lock:
                server.requests += 1
            body = b"body { color: rgb(1, 2, 3) }"
            head = (
                "HTTP/1.1 200 OK\r\nContent-Type: text/css\r\n"
                "Access-Control-Allow-Origin: *\r\nCache-Control: no-store\r\n"
                f"Connection: close\r\nContent-Length: {len(body)}\r\n\r\n"
            )
            tls.sendall(head.encode() + body)


def _tls_server(directory: Path, certs: int, pace: float | None) -> Iterator[TlsServer]:
    """De server, tot het eind van de fixture. pace: de handshakes één voor één, in de volgorde
    waarin de verbindingen binnenkwamen, met pace seconden ertussen."""
    contexts, first_cert = _contexts(directory, certs)
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.2)
    server = TlsServer(port=listener.getsockname()[1], first_cert=first_cert)
    stop = threading.Event()
    turns: queue.Queue[threading.Event] = queue.Queue()
    workers: list[threading.Thread] = []

    def accept() -> None:
        n = 0
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            conn.settimeout(10)
            turn = None
            if pace is not None:
                turn = threading.Event()
                turns.put(turn)
            worker = threading.Thread(
                target=_answer, args=(server, conn, contexts[n % certs], turn), daemon=True
            )
            n += 1
            worker.start()
            workers.append(worker)

    def pacer() -> None:
        while not stop.is_set():
            try:
                turn = turns.get(timeout=0.2)
            except queue.Empty:
                continue
            turn.set()
            stop.wait(pace)

    helpers = [threading.Thread(target=accept, daemon=True)]
    if pace is not None:
        helpers.append(threading.Thread(target=pacer, daemon=True))
    for helper in helpers:
        helper.start()
    try:
        yield server
    finally:
        stop.set()
        for helper in helpers:  # binnen 0,2 s klaar; pas daarna de listener dicht
            helper.join(5)
        listener.close()
        while not turns.empty():  # wachtende verbindingen niet laten hangen
            turns.get_nowait().set()
        for worker in workers:
            worker.join(5)


@pytest.fixture
def rotating_tls(tmp_path: Path) -> Iterator[TlsServer]:
    yield from _tls_server(tmp_path, CERTS, pace=None)


@pytest.fixture
def paced_tls(tmp_path: Path) -> Iterator[TlsServer]:
    yield from _tls_server(tmp_path, 1, pace=PACE)


def _preload(context: Any, server: TlsServer) -> tuple[list[str], list[str]]:
    """Laadt STYLESHEETS stylesheets tegelijk zoals Vite; geeft de uitkomst per stylesheet en
    de mislukte verzoeken (url en reden)."""
    page = context.new_page()
    failed: list[str] = []
    page.on("requestfailed", lambda r: failed.append(f"{r.url} {r.failure}"))
    page.set_content("<!doctype html><title>https</title>")
    urls = [server.url(f"/assets/{i}.css") for i in range(STYLESHEETS)]
    results = page.evaluate(PRELOAD_JS, urls)
    return results, failed


def test_trage_handshakes_met_een_certificaat_houden_de_stylesheet_niet_tegen(
    browser: Any, paced_tls: TlsServer
) -> None:
    """Het vermoedelijke mechanisme van CI, nagebootst: één certificaat, een server die de
    handshakes één voor één afrondt, en een stylesheet met FETCHES fetches tegelijk. Met
    trusting_browser geen afgewezen handshake, en alles laadt.

    Zonder vertrouwen, met ignore_https_errors, krijgt de stylesheet hier de certificaatfout
    van elke verbinding die voor een fetch werd geopend, tot de 32e herstart:
    net::ERR_TOO_MANY_RETRIES en "Unable to preload CSS", terwijl elke fetch laadt. Dat is
    handmatig nagegaan (o1.md) en geen test: het leunt op de socket pool van Chromium."""
    trusted = trusting_browser(browser, paced_tls.first_cert)
    try:
        context = trusted.new_context()
        page = context.new_page()
        failed: list[str] = []
        page.on("requestfailed", lambda r: failed.append(f"{r.url} {r.failure}"))
        page.set_content("<!doctype html><title>https</title>")
        fetches = [paced_tls.url(f"/api/{i}") for i in range(FETCHES)]
        sheet, others = page.evaluate(
            SHEET_AND_FETCHES_JS, [paced_tls.url("/assets/files.css"), fetches]
        )
        context.close()
    finally:
        trusted.close()
    assert sheet == "ok", (sheet, failed, paced_tls.other_failures)
    assert others == ["ok"] * FETCHES, others
    assert failed == []
    assert paced_tls.rejected == 0
    assert paced_tls.requests == FETCHES + 1


def test_een_browser_die_het_certificaat_vertrouwt_ziet_geen_certificaatfout(
    browser: Any, rotating_tls: TlsServer
) -> None:
    """Zoals de https-tests van office (trusting_browser): elke verbinding een nieuw
    certificaat, en toch geen enkele afgewezen handshake en elke stylesheet geladen."""
    trusted = trusting_browser(browser, rotating_tls.first_cert)
    try:
        context = trusted.new_context()
        results, failed = _preload(context, rotating_tls)
        context.close()
    finally:
        trusted.close()
    assert results == ["ok"] * STYLESHEETS, (results, failed, rotating_tls.other_failures)
    assert failed == []
    assert rotating_tls.rejected == 0
    assert rotating_tls.requests == STYLESHEETS


def test_zonder_vertrouwen_faalt_elke_stylesheet_op_het_certificaat(
    browser: Any, rotating_tls: TlsServer
) -> None:
    """De controle: dezelfde server in een gewone context, zonder trusting_browser en zonder
    ignore_https_errors. Dan is het certificaat echt niet vertrouwd en faalt elke stylesheet
    op een certificaatfout: de test hierboven slaagt door het vertrouwen, niet door de server.
    (Gewoon gedrag van elke browser; de test leunt niet op hoe Chromium herstart.)"""
    context = browser.new_context()
    try:
        results, failed = _preload(context, rotating_tls)
    finally:
        context.close()
    assert all(r.startswith("Unable to preload CSS for https://") for r in results), results
    assert failed and all(" net::ERR_CERT_" in f for f in failed), failed
    assert rotating_tls.requests == 0
