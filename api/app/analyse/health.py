"""Leeft de worker van de analyse? De healthcheck van compose.analyse.yaml (fase 6, §3.11; X2a).

    python -I -c BOOT /srv --check /run/analyse/worker.sock

Een hello zonder sleutel: de healthcheck heeft hem niet (`env -i`), en het bewijs kan alleen de
app nakijken. Een hello terug (ev hello, PROTOCOL) is een levende worker: 0. Een weigering (de
zelftest faalde: done met sandbox) of geen antwoord binnen LIMIT_S is 1. Zo doet de mediaworker
het ook (app/media/worker/__main__.py, alive).

Wat er op stdout komt, is kort en zonder waarden van de worker: de code van een weigering, nooit
zijn detail.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
from pathlib import Path

from app.analyse import wire

LIMIT_S = 4.0  # binnen de timeout van 5 s van de healthcheck


async def alive(path: Path, *, limit_s: float = LIMIT_S) -> int:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    sock.setblocking(False)
    try:
        async with asyncio.timeout(limit_s):
            with wire.socket_address(path) as address:
                await loop.sock_connect(sock, address)
            await wire.send(sock, wire.hello(secrets.token_hex(16)))
            reply, fds = await wire.receive(sock, max_fds=0)
        wire.close_all(fds)
    except (OSError, TimeoutError, wire.WireError) as exc:
        print(f"geen analyse-worker op {path}: {exc.__class__.__name__}")
        return 1
    finally:
        sock.close()
    if reply.get("ev") == "hello" and reply.get("v") == wire.PROTOCOL:
        print("de analyse-worker leeft")
        return 0
    code = reply.get("code")
    print(f"de analyse-worker weigert: {code if isinstance(code, str) else '?'}"[:200])
    return 1
