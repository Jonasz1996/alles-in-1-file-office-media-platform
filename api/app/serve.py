"""Start het platform: http op PLATFORM_HTTP_PORT en, zodra er een certificaat is,
https op PLATFORM_HTTPS_PORT. Beide in één proces met dezelfde app.

    python -m app.serve

De http-server draait de lifespan (migraties, certificaten, opruimen); de
https-server deelt die en start pas als CertManager een certificaat heeft. Gaat de
https-poort niet open, dan blijft http gewoon werken en staat de reden op /beheer.

Er zit geen proxy voor: beide poorten staan rechtstreeks open naar internet. Daarom
sluit elke verbinding die niet op tijd de tls-handshake doet of de headers van een
request stuurt.

Met office (fase 4) komt er een derde luisteraar bij: de interne app (main.internal_app) op
PLATFORM_OFFICE_INTERNAL_PORT, alleen voor de document server (Keuze 8). Die poort wordt
niet gepubliceerd; hij start alleen als office ingesteld is en stopt als laatste. De
publieke luisteraars krijgen ws=Tunnel: de enige WebSocket is de tunnel naar office
(Keuze 6), al het andere krijgt 404.

Netjes stoppen (fase 5, F3): eerst media (geen nieuwe verzoeken onder /api/v1/stream/, wie
wacht krijgt 503 met Retry-After: 2, de sessies dicht), dan office, dan https en http tegelijk
met één deadline van GRACEFUL seconden (timeout_graceful_shutdown op beide). Een lange stream
houdt het stoppen zo niet langer op; daarna sluit http de lifespan af, pas als https ook klaar
is. Zonder office geeft Docker 10 s, met media 30 s (stop_grace_period in compose.media.yaml).
"""

import asyncio
import contextlib
import errno
import functools
import logging
import resource
import signal
import socket
from collections.abc import Awaitable, Callable, Sequence

import h11
import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from app import events
from app.config import Settings, get_settings
from app.main import app, internal_app
from app.media import runtime as media_runtime
from app.office import sweep, tunnel
from app.office.tunnel import Tunnel

try:
    from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol
except ImportError:  # zonder httptools gebruikt uvicorn h11
    HttpToolsProtocol = None

log = logging.getLogger("platform")
uvicorn_log = logging.getLogger("uvicorn.error")  # heeft een handler, ook voor info

# Seconden voor de headers van een request, ook tussen keep-alive door, en voor de
# tls-handshake. Samen met de limiet op open bestanden is dit de grens: geen
# limit_concurrency, want uvicorn telt daarin ook stille verbindingen en geeft dan
# iedereen 503, ook /health en Let's Encrypt.
HEADER_TIMEOUT = 10.0
KEEP_ALIVE = 5  # seconden dat een stille keep-alive-verbinding open blijft
LISTENER_RETRY = 60.0  # https-poort ging niet open: zo vaak opnieuw proberen
# F3 (fase 5): zo lang mogen lopende antwoorden bij het stoppen nog duren, voor https en http
# samen (ze stoppen tegelijk); daarna breekt uvicorn ze af en sluit de lifespan
GRACEFUL = 10

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
    # Na een upgrade naar de tunnel is het transport van de tunnel: dan geen deadline meer.
    # Zonder deze vlag sloot de deadline een geüpgradede verbinding met httptools na
    # HEADER_TIMEOUT (M1): on_message_complete zette _in_body terug, terwijl de deadline op
    # het gedeelde transport doorliep.
    _upgraded = False

    def _waiting_for_headers(self) -> bool:
        raise NotImplementedError

    def _responses_done(self) -> bool:
        return self.cycle is None or self.cycle.response_complete

    def handle_websocket_upgrade(self, *args) -> None:
        self._upgraded = True
        self._cancel_deadline()
        super().handle_websocket_upgrade(*args)

    def _update_deadline(self) -> None:
        if self._upgraded:
            self._cancel_deadline()
            return
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
    # Alleen http (die draait de lifespan): waar het afsluiten van de lifespan eerst op wacht
    # (dat https ook klaar is; F3)
    before_lifespan: Callable[[], Awaitable[None]] | None = None

    async def shutdown(self, sockets=None) -> None:
        wait = self.before_lifespan
        lifespan = getattr(self, "lifespan", None)
        if wait is not None and lifespan is not None:
            original = lifespan.shutdown

            async def after_wait() -> None:
                try:
                    await wait()
                finally:
                    await original()

            lifespan.shutdown = after_wait
        await super().shutdown(sockets=sockets)

    @contextlib.contextmanager
    def capture_signals(self):
        # Twee servers in één proces: de signalen vangt run() zelf op.
        yield

    async def startup(self, sockets=None) -> None:
        if sockets is not None and self.config.ssl is not None:
            await self._startup_tls(sockets)
        else:
            await super().startup(sockets=sockets)
        if self.started and self.on_started is not None:
            self.on_started()

    async def _startup_tls(self, sockets: Sequence[socket.socket]) -> None:
        """Wat uvicorn doet met sockets=[...], maar met een korte tls-handshake.
        Asyncio wacht daar standaard 60 s op, en de header-deadline begint pas als de
        handshake klaar is: een verbinding die nooit een ClientHello stuurt, hield zo
        een minuut lang een file descriptor bezet."""
        config = self.config
        await self.lifespan.startup()  # https draait zonder lifespan
        loop = asyncio.get_running_loop()

        def create_protocol(_loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Protocol:
            return config.http_protocol_class(
                config=config,
                server_state=self.server_state,
                app_state=self.lifespan.state,
                _loop=_loop,
            )

        self.servers = [
            await loop.create_server(
                create_protocol,
                sock=sock,
                ssl=config.ssl,
                backlog=config.backlog,
                ssl_handshake_timeout=HEADER_TIMEOUT,
            )
            for sock in sockets
        ]
        self.started = True


def _config(settings: Settings, port: int, **extra) -> uvicorn.Config:
    return uvicorn.Config(
        app,
        host=settings.host,
        port=port,
        log_level=settings.log_level,
        proxy_headers=False,  # er zit geen proxy voor; X-Forwarded-For niet geloven
        server_header=False,
        http=GuardedHttp,
        ws=Tunnel,  # alleen de tunnel naar office (Keuze 6); elke andere upgrade: 404
        limit_concurrency=None,  # zie HEADER_TIMEOUT
        timeout_keep_alive=KEEP_ALIVE,
        timeout_graceful_shutdown=GRACEFUL,  # F3: ook voor https (dezelfde config)
        **extra,
    )


def _internal_config(settings: Settings) -> uvicorn.Config:
    """De interne luisteraar voor de document server: alleen http, geen lifespan (de
    publieke app heeft hem al gedraaid; internal_app deelt zijn state), geen WebSocket."""
    return uvicorn.Config(
        internal_app,
        host=settings.office_internal_host or settings.host,
        port=settings.office_internal_port,
        log_level=settings.log_level,
        proxy_headers=False,
        server_header=False,
        http=GuardedHttp,
        ws="none",
        lifespan="off",
        limit_concurrency=None,
        timeout_keep_alive=KEEP_ALIVE,
        timeout_graceful_shutdown=GRACEFUL,  # stopt als laatste; ook hij hangt niet
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
            if certs.listener_error is not None:
                log.warning("De https-poort %s is weer open; https werkt weer.", port)
            certs.listener_error = None
            # Met eigen sockets meldt uvicorn zelf niet dat hij draait
            uvicorn_log.info("Https draait op poort %s voor %s", port, certs.cert_domain)

        while True:
            try:
                sock = _listen(settings.host, port)
            except OSError as e:
                reason = BIND_ERRORS.get(e.errno, e.strerror or str(e))
                error = f"De https-poort {port} gaat niet open: {reason}."
                # Voor /beheer: dezelfde fout als code met params (de web-app vertaalt op code)
                code: tuple[str, dict] = ("listener_bind", {"port": port, "reason": reason})
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
                    code = ("listener_stopped", {"port": port, "detail": detail})
                finally:
                    sock.close()
                    https = None
                    certs.serving = False
            if error != certs.listener_error:
                log.error("%s Http werkt gewoon door.", error)
            certs.listener_error = error
            certs.listener_error_code, certs.listener_error_params = code
            await asyncio.sleep(LISTENER_RETRY)

    internal: Server | None = None
    internal_task: asyncio.Task | None = None

    async def serve_internal() -> None:
        """Pas na de lifespan van http: die maakt OfficeState en de database klaar."""
        nonlocal internal
        while not http.started:
            if http_task.done():
                return
            await asyncio.sleep(0.05)
        try:
            sock = _listen(internal_config.host, internal_config.port)
        except OSError as e:
            reason = BIND_ERRORS.get(e.errno, e.strerror or str(e))
            log.error(
                "De interne poort %s voor office gaat niet open: %s. Office werkt dan niet.",
                internal_config.port,
                reason,
            )
            return
        internal = Server(internal_config)
        try:
            await internal.serve(sockets=[sock])
        finally:
            sock.close()

    if settings.office_configured:
        internal_config = _internal_config(settings)
        internal_task = asyncio.create_task(serve_internal(), name="intern")

    https_task = asyncio.create_task(serve_https(), name="https")

    async def https_stopped() -> None:
        # Https kreeg hetzelfde moment het sein; zijn eigen deadline is GRACEFUL
        await asyncio.wait({https_task}, timeout=GRACEFUL + 5)

    http.before_lifespan = https_stopped
    stop_task = asyncio.create_task(stop.wait())
    # Https mag uitvallen zonder dat http stopt; alleen http of een signaal stopt alles
    await asyncio.wait({http_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)

    # Netjes stoppen. Eerst media (F3): geen nieuwe verzoeken onder /api/v1/stream/, wie op
    # een segment wacht krijgt 503 met Retry-After: 2, en de sessies gaan dicht. Dan office:
    # alle open documenten tussentijds opslaan (hooguit 5 s, de callbacks komen binnen op de
    # interne poort, die nog draait), dan de tunnels dicht. Dan de open gebeurtenisstromen
    # afronden: uvicorn wacht op lopende antwoorden voordat het de lifespan afsluit, en een
    # stroom eindigt anders nooit. Dan https en http tegelijk, met één deadline (GRACEFUL);
    # http sluit de lifespan af als https ook klaar is. De interne luisteraar als laatste.
    media_runtime.begin_stop(app)
    if settings.office_configured:
        try:
            await sweep.checkpoint_all(wait=5)
        except Exception:  # noqa: BLE001 - stoppen gaat altijd door
            log.exception("Tussentijds opslaan van office bij het stoppen mislukt")
    tunnel.close_all()
    events.hub.close()
    stop_task.cancel()
    if https is not None:
        https.should_exit = True
    else:
        https_task.cancel()
    http.should_exit = True
    with contextlib.suppress(asyncio.CancelledError):
        await https_task
    await http_task
    if internal_task is not None:
        if internal is not None:
            internal.should_exit = True
        else:
            internal_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await internal_task


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
