"""TOTP (RFC 6238) voor 2FA met een authenticator-app, plus herstelcodes.

Codes van 6 cijfers, elke 30 seconden een nieuwe, HMAC-SHA1: dat is wat elke
authenticator-app (Google Authenticator, Aegis, 1Password, ...) standaard doet.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

import segno

STEP = 30
DIGITS = 6
# Eén stap ervoor en erna telt ook mee, voor een klok die iets afwijkt
WINDOW = 1
# Zonder 0/o en 1/l, zodat een opgeschreven code niet verkeerd overgetypt wordt
RECOVERY_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"


def new_secret() -> str:
    """160 bits willekeur, base32 zonder opvulling (zoals apps het verwachten)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP)


def verify(secret: str, code: str, last_step: int, now: float | None = None) -> int | None:
    """Geeft de stap van de geldige code terug, of None. Een stap die al gebruikt is
    (<= last_step) telt niet: zo werkt een afgeluisterde code geen tweede keer."""
    code = "".join(code.split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    now_step = current_step(now)
    for step in range(now_step - WINDOW, now_step + WINDOW + 1):
        if step > last_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    query = urlencode({"secret": secret, "issuer": issuer, "digits": DIGITS, "period": STEP})
    return f"otpauth://totp/{label}?{query}"


def qr_data_uri(uri: str) -> str:
    """QR-code als SVG in een data-URI; de web-app zet die direct in een <img>."""
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2)


def new_recovery_codes(count: int = 10) -> list[str]:
    """Codes als 'abcde-fghjk' (10 tekens, ~49 bits): makkelijk over te typen."""
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def normalize_recovery_code(code: str) -> str:
    return "".join(c for c in code.lower() if c.isalnum())


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(normalize_recovery_code(code).encode()).hexdigest()
