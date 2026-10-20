"""De listeners van serve.py. Er zit geen proxy voor: een verbinding die geen headers
stuurt gaat dicht, en een https-poort die niet open gaat stopt http niet."""

import asyncio
import contextlib
import re
import socket
import ssl
import sys
import time

import httpx
import pytest

from app import serve
from app.certs import CertManager
from app.config import Settings, get_settings
from app.main import app
from tests.test_certs import write_cert

IMPLEMENTATIONS = [serve.GuardedH11]
if serve.HttpToolsProtocol is not None:
    IMPLEMENTATIONS.append(serve.GuardedHttpTools)


async def echo(scope, receive, send):
    """Leest de hele body en antwoordt met het aantal bytes; /vroeg antwoordt meteen,
    zonder de body te lezen (zoals een tus-fout)."""
    size, more = 0, scope["path"] != "/vroeg"
    while more:
        message = await receive()
        size += len(message.get("body", b""))
        more = message.get("more_body", False)
    body = str(size).encode()
    headers = [(b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": body})


@pytest.fixture(params=IMPLEMENTATIONS, ids=lambda impl: impl.__name__)
async def listener(request, tmp_path, monkeypatch):
    """Een server zoals serve.py hem maakt, met een korte header-deadline en echo als
    app. Geeft de poort."""
    monkeypatch.setattr(serve, "HEADER_TIMEOUT", 0.3)
    monkeypatch.setattr(serve, "app", echo)
    config = serve._config(Settings(data_dir=tmp_path), 0, lifespan="off")
    assert config.http is serve.GuardedHttp
    config.http = request.param
    server = serve.Server(config)
    ready = asyncio.Event()
    server.on_started = ready.set
    sock = serve._listen("127.0.0.1", 0)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    await asyncio.wait_for(ready.wait(), 5)
    yield sock.getsockname()[1]
    server.should_exit = True
    await asyncio.wait_for(task, 5)


def request(method: str = "GET", path: str = "/", length: int | None = None) -> bytes:
    extra = "" if length is None else f"Content-Length: {length}\r\n"
    return f"{method} {path} HTTP/1.1\r\nHost: x\r\n{extra}\r\n".encode()


async def response(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    length = int(re.search(rb"content-length: (\d+)", head, re.IGNORECASE).group(1))
    return int(head.split(b" ", 2)[1]), await reader.readexactly(length)


async def closed_within(reader: asyncio.StreamReader, seconds: float) -> bool:
    """True als de server de verbinding binnen `seconds` sluit."""
    try:
        return await asyncio.wait_for(reader.read(), seconds) == b""
    except ConnectionError:
        return True
    except TimeoutError:
        return False


@pytest.mark.anyio
async def test_stille_verbinding_gaat_dicht(listener):
    reader, writer = await asyncio.open_connection("127.0.0.1", listener)
    start = time.monotonic()
    assert await closed_within(reader, 3)
    assert time.monotonic() - start >= 0.2
    writer.close()


@pytest.mark.anyio
async def test_druppelende_headers_gaan_dicht(listener):
    """Elke 0,1 s een headerregel: uvicorns eigen keep-alive-timer begint dan telkens
    opnieuw, de header-deadline niet."""
    reader, writer = await asyncio.open_connection("127.0.0.1", listener)
    writer.write(b"GET / HTTP/1.1\r\nHost: x\r\n")
    for i in range(30):
        try:
            writer.write(f"X-{i}: a\r\n".encode())
            await writer.drain()
        except ConnectionError:
            break
        if await closed_within(reader, 0.1):
            break
    else:
        pytest.fail("De verbinding bleef 3 seconden open")
    writer.close()


@pytest.mark.anyio
async def test_keep_alive_werkt_en_gaat_daarna_dicht(listener):
    reader, writer = await asyncio.open_connection("127.0.0.1", listener)
    for _ in range(3):
        writer.write(request())
        assert await response(reader) == (200, b"0")
        await asyncio.sleep(0.2)  # korter dan de deadline: de verbinding blijft open
    assert await closed_within(reader, 3)
    writer.close()


@pytest.mark.anyio
async def test_langzame_upload_loopt_door(listener):
    """Een upload die veel langer duurt dan de deadline (een tus-PATCH van minuten)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", listener)
    chunks = 20
    writer.write(request("PATCH", "/upload", chunks * 100))
    for _ in range(chunks):  # 2 seconden: bijna 7 keer de deadline
        writer.write(b"x" * 100)
        await writer.drain()
        await asyncio.sleep(0.1)
    assert await response(reader) == (200, str(chunks * 100).encode())
    writer.write(request())
    assert await response(reader) == (200, b"0")
    writer.close()


@pytest.mark.anyio
async def test_vroeg_antwoord_terwijl_de_body_nog_binnenkomt(listener):
    """Antwoordt de app al voordat de body binnen is, dan blijft de verbinding open tot
    de rest van de body er is; daarna kan het volgende request."""
    reader, writer = await asyncio.open_connection("127.0.0.1", listener)
    writer.write(request("PATCH", "/vroeg", 1000))
    assert await response(reader) == (200, b"0")
    for _ in range(10):  # 1 seconde, ruim langer dan de deadline
        writer.write(b"x" * 100)
        await writer.drain()
        await asyncio.sleep(0.1)
    writer.write(request())
    assert await response(reader) == (200, b"0")
    writer.close()


def test_listener_instellingen(tmp_path):
    config = serve._config(Settings(data_dir=tmp_path), 8080)
    assert config.limit_concurrency == serve.LIMIT_CONCURRENCY
    assert config.timeout_keep_alive == serve.KEEP_ALIVE


def test_limiet_open_bestanden_omhoog(monkeypatch):
    calls = []
    monkeypatch.setattr(serve.resource, "getrlimit", lambda _: (1024, 524288))
    monkeypatch.setattr(serve.resource, "setrlimit", lambda _, limits: calls.append(limits))
    serve._raise_fd_limit()
    assert calls == [(524288, 524288)]

    def refuse(_, limits):
        raise ValueError("niet toegestaan")

    monkeypatch.setattr(serve.resource, "setrlimit", refuse)
    serve._raise_fd_limit()  # alleen een waarschuwing in de log


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def until(predicate, seconds: float = 10) -> None:
    for _ in range(int(seconds * 20)):
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("Duurde te lang")


def https_get(port: int, path: str) -> str:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with context.wrap_socket(raw) as tls:
            tls.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
            data = b""
            while chunk := tls.recv(65536):
                data += chunk
    return data.decode(errors="replace")


@contextlib.asynccontextmanager
async def platform(tmp_path, monkeypatch, https_port: int):
    """Het hele platform via serve.run, met al een certificaat in data/tls (zoals na
    een herstart). Geeft een http-client."""
    monkeypatch.setattr(serve, "LISTENER_RETRY", 0.2)
    http_port = free_port()
    for key, value in {
        "PLATFORM_DATA_DIR": str(tmp_path),
        "PLATFORM_WEB_DIST": str(tmp_path / "web"),
        "PLATFORM_HOST": "127.0.0.1",
        "PLATFORM_HTTP_PORT": str(http_port),
        "PLATFORM_HTTPS_PORT": str(https_port),
        "PLATFORM_JWT_SECRET": "test" * 8,
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    settings = get_settings()
    write_cert(CertManager(settings), "platform.test")
    stop = asyncio.Event()
    task = asyncio.create_task(serve.run(settings, stop))
    base = f"http://127.0.0.1:{http_port}"
    try:
        async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=5) as c:
            for _ in range(200):
                with contextlib.suppress(httpx.TransportError):
                    if (await c.get("/health")).status_code == 200:
                        break
                assert not task.done(), "serve.run stopte"
                await asyncio.sleep(0.05)
            yield c
    finally:
        stop.set()
        await asyncio.wait_for(task, 20)
        get_settings.cache_clear()
        with contextlib.suppress(AttributeError):
            del app.state.certs


@pytest.mark.anyio
async def test_https_poort_bezet_http_werkt_door(tmp_path, monkeypatch):
    blocker = socket.create_server(("127.0.0.1", 0))
    port = blocker.getsockname()[1]
    try:
        async with platform(tmp_path, monkeypatch, port) as c:
            certs = app.state.certs
            await until(lambda: certs.listener_error is not None)
            assert str(port) in certs.listener_error and "in gebruik" in certs.listener_error
            assert certs.status()["listener_error"] == certs.listener_error
            assert not certs.serving
            assert (await c.get("/health")).status_code == 200

            # Poort weer vrij: https start alsnog, zonder herstart
            blocker.close()
            await until(lambda: certs.serving)
            assert certs.listener_error is None
            response = await asyncio.to_thread(https_get, port, "/health")
            assert response.startswith("HTTP/1.1 200")
    finally:
        blocker.close()


@pytest.mark.anyio
async def test_systemexit_van_https_stopt_http_niet(tmp_path, monkeypatch):
    """Uvicorn roept bij een startfout sys.exit aan; dat mag alleen https raken."""
    startup = serve.Server.startup

    async def failing(self, sockets=None):
        if self.config.lifespan == "off":  # de https-server
            sys.exit(3)
        await startup(self, sockets=sockets)

    monkeypatch.setattr(serve.Server, "startup", failing)
    async with platform(tmp_path, monkeypatch, free_port()) as c:
        certs = app.state.certs
        await until(lambda: certs.listener_error is not None)
        assert "stopte" in certs.listener_error
        await asyncio.sleep(0.5)  # een paar pogingen verder
        assert (await c.get("/health")).status_code == 200
        assert not certs.serving
