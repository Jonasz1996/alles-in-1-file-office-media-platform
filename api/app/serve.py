"""Start het platform: http op PLATFORM_HTTP_PORT en, zodra er een certificaat is,
https op PLATFORM_HTTPS_PORT. Beide in één proces met dezelfde app.

    python -m app.serve

De http-server draait de lifespan (migraties, certificaten, opruimen); de
https-server deelt die en start pas als CertManager een certificaat heeft.
"""

import asyncio
import contextlib
import logging
import signal

import uvicorn

from app.config import Settings, get_settings
from app.main import app

log = logging.getLogger("platform")


class Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        # Twee servers in één proces: de signalen vangt run() zelf op.
        yield


def _config(settings: Settings, port: int, **extra) -> uvicorn.Config:
    return uvicorn.Config(
        app,
        host=settings.host,
        port=port,
        log_level=settings.log_level,
        proxy_headers=False,  # er zit geen proxy voor; X-Forwarded-For niet geloven
        server_header=False,
        **extra,
    )


async def run(settings: Settings, stop: asyncio.Event) -> None:
    http = Server(_config(settings, settings.http_port, lifespan="on"))
    https: Server | None = None
    http_task = asyncio.create_task(http.serve(), name="http")

    async def serve_https() -> None:
        nonlocal https
        while not http.started:
            if http_task.done():
                return
            await asyncio.sleep(0.05)
        certs = app.state.certs
        await certs.has_cert.wait()
        https = Server(
            _config(
                settings,
                settings.https_port,
                lifespan="off",
                ssl_context_factory=lambda _config, _default: certs.server_context(),
            )
        )
        certs.serving = True
        log.info("https op poort %s voor %s", settings.https_port, certs.cert_domain)
        try:
            await https.serve()
        finally:
            certs.serving = False

    https_task = asyncio.create_task(serve_https(), name="https")
    stop_task = asyncio.create_task(stop.wait())
    await asyncio.wait({http_task, https_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)

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


def main() -> None:
    settings = get_settings()

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await run(settings, stop)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
