"""Het protocol van Keuze 12, zo klein als het voor S3 nodig is.

Een boodschap is 4 bytes lengte (big endian) en dan JSON, hooguit 64 KB. De eerste boodschap
draagt de fd's als SCM_RIGHTS. Ontvangen fd's krijgen meteen FD_CLOEXEC (`MSG_CMSG_CLOEXEC`):
`socket.recv_fds` doet dat niet, en dan erft elk kind (ffmpeg) ze.
"""

from __future__ import annotations

import array
import json
import os
import socket
import struct

MAX_BERICHT = 64 * 1024
MAX_FDS = 8


def stuur(sock: socket.socket, bericht: dict, fds: list[int] | tuple[int, ...] = ()) -> None:
    data = json.dumps(bericht, separators=(",", ":")).encode()
    if len(data) > MAX_BERICHT:
        raise ValueError(f"bericht te groot: {len(data)} bytes")
    buf = struct.pack(">I", len(data)) + data
    if fds:
        anc = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds).tobytes())]
        verstuurd = sock.sendmsg([buf], anc)
        buf = buf[verstuurd:]
    if buf:
        sock.sendall(buf)


def ontvang(sock: socket.socket) -> tuple[dict, list[int]]:
    """Eén boodschap en de fd's die erbij zaten. EOFError als de andere kant dicht is."""
    fds: list[int] = []
    kop = b""
    ruimte = socket.CMSG_SPACE(MAX_FDS * array.array("i").itemsize)
    try:
        while len(kop) < 4:
            data, anc, vlaggen, _ = sock.recvmsg(4 - len(kop), ruimte, socket.MSG_CMSG_CLOEXEC)
            for niveau, soort, inhoud in anc:
                if niveau == socket.SOL_SOCKET and soort == socket.SCM_RIGHTS:
                    a = array.array("i")
                    a.frombytes(inhoud[: len(inhoud) - (len(inhoud) % a.itemsize)])
                    fds += list(a)
            if vlaggen & socket.MSG_CTRUNC:
                raise ValueError("te veel fd's")
            if not data:
                raise EOFError("verbinding dicht")
            kop += data
        (lengte,) = struct.unpack(">I", kop)
        if lengte > MAX_BERICHT:
            raise ValueError(f"bericht te groot: {lengte} bytes")
        body = b""
        while len(body) < lengte:
            stuk = sock.recv(lengte - len(body))
            if not stuk:
                raise EOFError("verbinding dicht midden in een bericht")
            body += stuk
        return json.loads(body), fds
    except BaseException:
        for fd in fds:
            os.close(fd)
        raise
