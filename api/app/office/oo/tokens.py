"""JWT van en voor OnlyOffice (Keuze 11, §3.9): alles HS256 met settings.office_secret, met
60 s speling op de klok.

- app -> browser -> DS: de hele editorconfig (zonder events) in config.token, exp 2 uur;
- DS -> browser: een eigen sessietoken om opnieuw te verbinden (document.key erin). DS 10.0
  tekent dat niet met het office-geheim zelf maar met HMAC-SHA256(office-geheim,
  "editor-session") als hex-tekst [gemeten in de spike, recorded/socket.json];
- DS -> app, document ophalen: Authorization: Bearer {"payload": {"url"}, "operation": "download"};
- DS -> app, callback: token in de body (platte velden) en/of in de header (onder "payload"),
  beide met operation "callback" en 5 minuten geldig;
- app -> DS, /command en /converter: "token" in de body, met de parameters als payload.

Een token met het office-geheim werkt nooit als app-token, en omgekeerd: een andere sleutel,
en operation (of typ) wordt gecontroleerd. Een token zonder exp nemen we niet aan.
"""

import hashlib
import hmac
import time
import uuid

import jwt

from app.config import Settings
from app.office.engine import TokenError

ALGORITHM = "HS256"
LEEWAY = 60
# Velden die DS en wij aan een token toevoegen en die geen deel van de inhoud zijn
CLAIMS = ("iat", "exp", "nbf", "operation")


def sign(settings: Settings, payload: dict, minutes: int = 5) -> str:
    now = int(time.time())
    claims = {**payload, "iat": now, "exp": now + minutes * 60}
    return jwt.encode(claims, settings.office_secret, algorithm=ALGORITHM)


def browser_token(settings: Settings, config: dict, minutes: int = 120) -> str:
    """config.token: de hele config, zonder events (en zonder een eerder token)."""
    body = {k: v for k, v in config.items() if k not in ("events", "token")}
    return sign(settings, body, minutes)


def command_body(settings: Settings, params: dict) -> dict:
    """De body voor /command of /converter: de parameters, plus dezelfde getekend."""
    return {**params, "token": sign(settings, params)}


def session_secret(settings: Settings) -> str:
    """De sleutel van het sessietoken van DS 10.0 (afgeleid van het office-geheim)."""
    return hmac.new(settings.office_secret.encode(), b"editor-session", hashlib.sha256).hexdigest()


def decode(settings: Settings, token: str, key: str | None = None) -> dict:
    """De claims van een token met het office-geheim (of key); TokenError bij elke fout."""
    if not isinstance(token, str) or not token:
        raise TokenError("geen token")
    try:
        claims = jwt.decode(
            token,
            key or settings.office_secret,
            algorithms=[ALGORITHM],
            leeway=LEEWAY,
            options={"require": ["exp"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if not isinstance(claims, dict):
        raise TokenError("geen object")
    if "typ" in claims:  # een token van de app zelf (access, refresh, download, office_gate)
        raise TokenError("token van de app")
    return claims


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def callback_data(settings: Settings, body: dict, authorization: str | None) -> dict:
    """De velden van een callback, alleen uit een geldig token: eerst het token in de body
    (platte velden), anders dat in de header (velden onder "payload"). Losse velden in de
    body tellen nooit. Eist operation == "callback"; TokenError bij elke fout."""
    if not isinstance(body, dict):
        raise TokenError("geen object")
    token = body.get("token")
    if token is not None:
        claims = decode(settings, token)
        if claims.get("operation") != "callback":
            raise TokenError("verkeerde operation")
        return {k: v for k, v in claims.items() if k not in CLAIMS}
    header = _bearer(authorization)
    if header is None:
        raise TokenError("geen token")
    claims = decode(settings, header)
    if claims.get("operation") != "callback":
        raise TokenError("verkeerde operation")
    payload = claims.get("payload")
    if not isinstance(payload, dict):
        raise TokenError("geen payload")
    return dict(payload)


def check_download(settings: Settings, authorization: str | None, url: str) -> None:
    """Het download-token van DS: operation "download" en payload.url precies url."""
    token = _bearer(authorization)
    if token is None:
        raise TokenError("geen token")
    claims = decode(settings, token)
    if claims.get("operation") != "download":
        raise TokenError("verkeerde operation")
    payload = claims.get("payload")
    if not isinstance(payload, dict) or payload.get("url") != url:
        raise TokenError("ander adres")


def document_key(settings: Settings, token: str) -> str | None:
    """document.key uit een config-token (van ons) of een sessietoken (van DS); None als het
    token niet klopt of er geen key in staat."""
    try:
        claims = decode(settings, token)
    except TokenError:
        try:
            claims = decode(settings, token, session_secret(settings))
        except TokenError:
            return None
    if "operation" in claims:  # een callback- of downloadtoken hoort hier niet
        return None
    document = claims.get("document")
    if not isinstance(document, dict):
        return None
    key = document.get("key")
    return key if isinstance(key, str) and key else None


def ds_user_id(settings: Settings, user_id: uuid.UUID) -> str:
    """De gebruiker zoals DS hem kent: vast per gebruiker, zonder de echte id (Keuze 14)."""
    mac = hmac.new(
        settings.office_secret.encode(), f"user:{user_id}".encode(), hashlib.sha256
    ).hexdigest()
    return mac[:24]
