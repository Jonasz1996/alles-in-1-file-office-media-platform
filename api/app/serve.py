"""Start het platform: http op PLATFORM_HTTP_PORT en, zodra er een certificaat is,
https op PLATFORM_HTTPS_PORT. Beide in één proces met dezelfde app.

    python -m app.serve

De http-server draait de lifespan (migraties, certificaten, opruimen); de
https-server deelt die en start pas als CertManager een certificaat heeft. Gaat de
https-poort niet open, dan blijft http gewoon werken en staat de reden op /beheer.

Er zit geen proxy voor: beide poorten staan rechtstreeks open naar internet. Daarom
sluit elke verbinding die niet op tijd de headers van een request stuurt.
"""

import asyncio
import contextlib
import errno
import functools
import logging
import resource
import signal
import socket
from collections.abc import Callable

import h11
import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from app.config import Settings, get_settings
from app.main import app

try:
    from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol
except ImportError:  # zonder httptools gebruikt uvicorn h11
    HttpToolsProtocol = None

log = logging.getLogger("platform")

HEADER_TIMEOUT = 10.0  # seconden voor de headers van een request, ook tussen keep-alive door
KEEP_ALIVE = 5  # seconden dat een stille keep-alive-verbinding open blijft
LIMIT_CONCURRENCY = 1000  # meer verbindingen of requests tegelijk: 503
LISTENER_RETRY = 60.0  # https-poort ging niet open: zo vaak opnieuw proberen

# Leesbare reden bij de fouten die je ziet als de https-poort niet open kan
BIND_ERRORS = {
    errno.EADDRINUSE: "de poort is al in gebruik door een ander programma",
    errno.EACCES: "geen toestemming voor deze poort",
    errno.EADDRNOTAVAIL: "dit adres bestaat niet op deze machine",
}


class HeaderDeadline:
    """Sluit een verbinding die niet binnen HEADER_TIMEOUT de headers van een request
    stuurt: na het openen en na elk antwoord. Uvicorn heeft daar zelf geen timeout
    voor; zijn keep-alive-timer begint pas na een antwoord en stopt bij elke byte die
    binnenkomt. De body van een request (een lange upload) valt er niet onder, en
    zolang een antwoord nog loopt ook niet."""

    _deadline: asyncio.TimerHandle | None = None

    def _waiting_for_headers(self) -> bool:
        raise NotImplementedError

    def _responses_done(self) -> bool:
        return self.cycle is None or self.cycle.response_complete

    def _update_deadline(self) -> None:
        if not self._waiting_for_headers():
            self._cancel_deadline()
        elif self._deadline is None and not self.transport.is_closing():
            # Niet opnieuw beginnen bij elke byte: dan houdt druppelen hem open
            self._deadline = self.loop.call_later(HEADER_TIMEOUT, self._header_timeout)

    def _cancel_deadline(self) -> None:
        if self._deadline is not None:
            self._deadline.cancel()
            self._deadline = None

    def _header_timeout(self) -> None:
        self._deadline = None
        if not self.transport.is_closing():
            self.transport.close()

    def connection_made(self, transport) -> None:
        super().connection_made(transport)
        self._update_deadline()

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self._update_deadline()

    def on_response_complete(self) -> None:
        super().on_response_complete()
        self._update_deadline()

    def connection_lost(self, exc: Exception | None) -> None:
        super().connection_lost(exc)
        self._cancel_deadline()


class GuardedH11(HeaderDeadline, H11Protocol):
    def _waiting_for_headers(self) -> bool:
        # IDLE: nog geen volledige headers; daarna SEND_BODY of DONE
        return self.conn.their_state is h11.IDLE and self._responses_done()


GuardedHttp: type = GuardedH11

if HttpToolsProtocol is not None:

    class GuardedHttpTools(HeaderDeadline, HttpToolsProtocol):
        _in_body = False  # headers binnen, body nog niet helemaal

        def on_headers_complete(self) -> None:
            self._in_body = True
            super().on_headers_complete()

        def on_message_complete(self) -> None:
            self._in_body = False
            super().on_message_complete()

        def _waiting_for_headers(self) -> bool:
            return not self._in_body and self._responses_done()

    GuardedHttp = GuardedHttpTools


class Server(uvicorn.Server):
    on_started: Callable[[], None] | None = None  # zodra de server echt luistert

    @contextlib.contextmanager
    def capture_signals(self):
        # Twee servers in één proces: de signalen vangt run() zelf op.
        yield

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        if self.started and self.on_started is not None:
            self.on_started()


def _config(settings: Settings, port: int, **extra) -> uvicorn.Config:
    return uvicorn.Config(
        app,
        host=settings.host,
        port=port,
        log_level=settings.log_level,
        proxy_headers=False,  # er zit geen proxy voor; X-Forwarded-For niet geloven
        server_header=False,
        http=GuardedHttp,
        ws="none",  # de app heeft geen websockets
        limit_concurrency=LIMIT_CONCURRENCY,
        timeout_keep_alive=KEEP_ALIVE,
        **extra,
    )


def _listen(host: str, port: int) -> socket.socket:
    """Zelf de poort openen: bij een fout roept uvicorn sys.exit aan, en dat stopt het
    hele proces, ook http."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    return socket.create_server((host, port), family=family, backlog=2048)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    http = Server(_config(settings, settings.http_port, lifespan="on"))
    https: Server | None = None  # alleen zolang de https-server echt draait
    http_task = asyncio.create_task(http.serve(), name="http")

    async def serve_https() -> None:
        nonlocal https
        while not http.started:
            if http_task.done():
                return
            await asyncio.sleep(0.05)
        certs = app.state.certs
        await certs.has_cert.wait()
        port = settings.https_port

        def started(server: Server) -> None:
            nonlocal https
            https = server
            certs.serving = True
            certs.listener_error = None
            log.info("https op poort %s voor %s", port, certs.cert_domain)

        while True:
            try:
                sock = _listen(settings.host, port)
            except OSError as e:
                reason = BIND_ERRORS.get(e.errno, e.strerror or str(e))
                error = f"De https-poort {port} gaat niet open: {reason}."
            else:
                server = Server(
                    _config(
                        settings,
                        port,
                        lifespan="off",
                        ssl_context_factory=lambda _config, _default: certs.server_context(),
                    )
                )
                server.on_started = functools.partial(started, server)
                try:
                    await server.serve(sockets=[sock])
                    return  # netjes gestopt door run()
                except (Exception, SystemExit) as e:  # uvicorn stopt met sys.exit
                    detail = "zie de log" if isinstance(e, SystemExit) else str(e)
                    error = f"De https-server op poort {port} stopte: {detail}."
                finally:
                    sock.close()
                    https = None
                    certs.serving = False
            if error != certs.listener_error:
                log.error("%s Http werkt gewoon door.", error)
            certs.listener_error = error
            await asyncio.sleep(LISTENER_RETRY)

    https_task = asyncio.create_task(serve_https(), name="https")
    stop_task = asyncio.create_task(stop.wait())
    # Https mag uitvallen zonder dat http stopt; alleen http of een signaal stopt alles
    await asyncio.wait({http_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)

    # Netjes stoppen: eerst https (zonder lifespan), dan http (die sluit de lifespan af)
    stop_task.cancel()
    if https is not None:
        https.should_exit = True
    else:
        https_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await https_task
    http.should_exit = True
    await http_task


def _raise_fd_limit() -> None:
    """Elke open verbinding kost een file descriptor; Docker geeft er standaard 1024."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft == hard:
        return
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    except (ValueError, OSError) as e:
        log.warning("Limiet op open bestanden blijft %s: %s", soft, e)


def main() -> None:
    settings = get_settings()
    _raise_fd_limit()

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await run(settings, stop)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
