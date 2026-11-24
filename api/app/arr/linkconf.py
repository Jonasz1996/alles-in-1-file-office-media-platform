"""De config van qBittorrent en Prowlarr vooraf schrijven (fase 11, stap 2b).

- `qbit_hash(password, salt)`: het formaat van qBittorrent (sinds 4.2) voor
  `WebUI\\Password_PBKDF2`: `@ByteArray(<salt b64>:<sleutel b64>)`, PBKDF2-HMAC-SHA512,
  100.000 rondes, 16 bytes zout, 64 bytes sleutel.
- `qbit_conf(text, …)`: zet de nodige sleutels in `qBittorrent.conf` en laat de rest staan
  (qBittorrent schrijft het bestand zelf bij het stoppen; dit gebeurt dus vóór de start).
- `prowlarr_conf(text, api_key)`: zet `<ApiKey>` in `config.xml` (en maakt het bestand als het er
  niet is); de rest vult Prowlarr zelf aan. Het eerste bezoek aan Prowlarr vraagt de beheerder een
  eigen login te kiezen.

Pure functies op tekst; de cli (`arr-koppel`) leest en schrijft de bestanden.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from xml.sax.saxutils import escape

ITERATIONS = 100_000


def qbit_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, ITERATIONS, dklen=64)
    return f"@ByteArray({base64.b64encode(salt).decode()}:{base64.b64encode(key).decode()})"


def qbit_check(password: str, stored: str) -> bool:
    match = re.fullmatch(r"@ByteArray\(([A-Za-z0-9+/=]+):([A-Za-z0-9+/=]+)\)", stored.strip('"'))
    if not match:
        return False
    salt = base64.b64decode(match.group(1))
    return qbit_hash(password, salt) == f"@ByteArray({match.group(1)}:{match.group(2)})"


def _set(text: str, section: str, values: dict[str, str]) -> str:
    """Zet sleutels in een sectie van een ini-bestand van Qt; de rest blijft zoals het was."""
    lines = text.splitlines()
    header = f"[{section}]"
    if header not in (line.strip() for line in lines):
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(header)
    start = next(i for i, line in enumerate(lines) if line.strip() == header)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    todo = dict(values)
    for i in range(start + 1, end):
        key = lines[i].split("=", 1)[0]
        if key in todo:
            lines[i] = f"{key}={todo.pop(key)}"
    insert = [f"{k}={v}" for k, v in todo.items()]
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines[end:end] = insert
    return "\n".join(lines) + "\n"


def qbit_conf(
    text: str, *, username: str, password: str, save_path: str, salt: bytes | None = None
) -> str:
    text = _set(text, "LegalNotice", {"Accepted": "true"})
    text = _set(
        text,
        "BitTorrent",
        {
            "Session\\DefaultSavePath": save_path,
            "Session\\TempPathEnabled": "true",
            "Session\\TempPath": save_path.rstrip("/") + "/bezig",
        },
    )
    return _set(
        text,
        "Preferences",
        {
            "WebUI\\Username": username,
            "WebUI\\Password_PBKDF2": f'"{qbit_hash(password, salt)}"',
            "WebUI\\Port": "8080",
            "WebUI\\LocalHostAuth": "true",
            "WebUI\\AuthSubnetWhitelistEnabled": "false",
        },
    )


def prowlarr_conf(text: str, api_key: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", api_key):
        raise ValueError("de API-sleutel van Prowlarr is 32 hex-tekens")
    if not text.strip():
        return f"<Config>\n  <ApiKey>{escape(api_key)}</ApiKey>\n</Config>\n"
    if re.search(r"<ApiKey>[^<]*</ApiKey>", text):
        return re.sub(r"<ApiKey>[^<]*</ApiKey>", f"<ApiKey>{api_key}</ApiKey>", text, count=1)
    return text.replace("</Config>", f"  <ApiKey>{api_key}</ApiKey>\n</Config>", 1)


def read_prowlarr_key(text: str) -> str | None:
    match = re.search(r"<ApiKey>([0-9a-f]{32})</ApiKey>", text)
    return match.group(1) if match else None
