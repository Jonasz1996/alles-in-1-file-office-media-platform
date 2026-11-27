"""De nep-providers als proces (voor de e2e):

    python -m tests.media.fake_providers [--host 127.0.0.1] [--port 0] [--port-file F]
        [--key-file K] [--data DIR] [--no-rate-limits] [--archive-url URL]

- De sleutel van TMDB alleen uit een bestand, nooit op de opdrachtregel of in de log.
- `--port 0` kiest een vrije poort; `--port-file` krijgt hem (atomair geschreven) zodra de
  socket luistert. Naast 127.0.0.1 luistert de nep ook op ::1 met dezelfde poort, als dat kan,
  zodat `localhost` (de tweede hostnaam, voor de omleiding van de CAA) op beide adressen werkt.
- De diensten op het voorvoegsel: `/tmdb`, `/tmdb-img`, `/mb`, `/caa` en `/archive`.
- Stoppen op PID (SIGTERM).
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from pathlib import Path

import uvicorn

from .app import Dropped, FakeConfig, create_app
from .data import load


def _read(path: str | None) -> str | None:
    if not path:
        return None
    value = Path(path).read_text().strip()
    return value or None


def _bind(host: str, port: int) -> list[socket.socket]:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    first = socket.socket(family, socket.SOCK_STREAM)
    first.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    first.bind((host, port))
    first.listen(128)
    sockets = [first]
    if host == "127.0.0.1" and socket.has_ipv6:
        try:
            extra = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        except OSError:
            return sockets  # geen IPv6 op deze machine
        try:
            extra.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            extra.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            extra.bind(("::1", first.getsockname()[1]))
            extra.listen(128)
            sockets.append(extra)
        except OSError:
            extra.close()
    return sockets


class _QuietDrop(logging.Filter):
    """De fout `drop` is bedoeld: één regel in de log in plaats van een traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and isinstance(record.exc_info[1], Dropped):
            record.msg, record.args, record.exc_info = "nep: drop (verbinding dicht)", (), None
        return True


def _write_port(path: str, port: int) -> None:
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(f"{port}\n")
    os.replace(tmp, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.media.fake_providers")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file", help="hier komt de poort zodra de socket luistert")
    parser.add_argument("--key-file", help="het API Read Access Token dat de nep aanneemt")
    parser.add_argument("--data", help="map met *.json (standaard fake_providers/data/)")
    parser.add_argument("--no-rate-limits", action="store_true", help="geen 429 en 503 op tempo")
    parser.add_argument("--archive-url", help="waar de CAA naar omleidt")
    args = parser.parse_args(argv)
    sockets = _bind(args.host, args.port)
    port = sockets[0].getsockname()[1]
    host = f"[{args.host}]" if ":" in args.host else args.host
    other = "localhost" if args.host in ("127.0.0.1", "::1") else host
    config = FakeConfig(
        tmdb_key=_read(args.key_file),
        data=load(args.data),
        image_url=f"http://{host}:{port}/tmdb-img",
        archive_url=args.archive_url or f"http://{other}:{port}/archive",
        rate_limits=not args.no_rate_limits,
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(config), log_level="warning", access_log=False, lifespan="off")
    )
    logging.getLogger("uvicorn.error").addFilter(_QuietDrop())
    if args.port_file:
        _write_port(args.port_file, port)
    server.run(sockets=sockets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
