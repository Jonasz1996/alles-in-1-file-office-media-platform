"""Het protocol van DS 10.0 zoals gemeten, voor de nep-DS (§3.6). Puur: geen I/O.

Bronnen: de proef (stub.log, cmd-results.jsonl) en de spike van O0 (ws.log, stub.log, met
DS 10.0.0.1, build 10.0.0.169). De vormen staan ook in recorded/*.json; test_fake_docserver.py
houdt deze module daaraan.

- JWT: HS256 met het office-geheim. Het sessietoken dat DS aan de browser geeft, tekent DS met
  HMAC-SHA256(office-geheim, "editor-session") als hex-tekst [gemeten].
- Callbacks: het token in de body heeft de velden plat, plus operation, iat en exp (+300 s);
  dat in de header heeft ze onder "payload". De echte DS 10.0.0.1 zet history ook in de header;
  de nep laat hem daar weg (zoals §3.6 zegt), zodat de app history alleen uit de body haalt.
- Secure links (nginx secure_link): md5 = base64url(md5(expires + pad + linkgeheim)) zonder
  "=", over het pad zoals DS het ziet (zonder het prefix van de proxy).
- Socket: engine.io 4 met socket.io erin: "0" open, "2"/"3" ping/pong, "40" CONNECT, "41"
  disconnect, "44" connect-fout, "42" een gebeurtenis ["message", {...}].
"""

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any

import jwt

ALGORITHM = "HS256"
VERSION = "10.0.0"
BUILD_NUMBER = 169
BUILD = f"{VERSION}.{BUILD_NUMBER}"  # wat command version geeft
# Het versiesegment in de adressen van de editor: 10.0.0-<32 hex>, vast voor de nep
V = f"{VERSION}-{hashlib.md5(b'nep-ds 10.0.0').hexdigest()}"
UNLIMITED = 2147483647
CALLBACK_SECONDS = 300
DOWNLOAD_SECONDS = 300
SESSION_SECONDS = 30 * 24 * 3600  # het sessietoken van DS: 30 dagen [gemeten]
LINK_SECONDS = 900
PING_INTERVAL = 25000
PING_TIMEOUT = 20000
MAX_PAYLOAD = 100000000

# Callbackstatussen en soorten acties (zoals de documentatie van DS en gemeten)
EDITING, SAVE, SAVE_ERROR, CLOSED, FORCESAVE, FORCESAVE_ERROR = 1, 2, 3, 4, 6, 7
DISCONNECT, CONNECT, FORCESAVE_BUTTON = 0, 1, 2
# forcesavetype: 0 commando, 1 knop, 2 timer
BY_COMMAND, BY_BUTTON, BY_TIMER = 0, 1, 2

# Foutcodes van /command
CMD_OK, CMD_KEY, CMD_NO_CHANGES, CMD_UNKNOWN, CMD_TOKEN = 0, 1, 4, 5, 6
# Foutcodes van de editor (onError): -4 downloaden mislukt, -20 token klopt niet, -21 verlopen
ERR_DOWNLOAD, ERR_TOKEN, ERR_TOKEN_EXPIRED = -4, -20, -21
WARN_CONNECTION = -18  # de verbinding met DS is weg


def session_secret(secret: str) -> str:
    return hmac.new(secret.encode(), b"editor-session", hashlib.sha256).hexdigest()


def sign(secret: str, claims: dict, seconds: int, now: float | None = None) -> str:
    t = int(time.time() if now is None else now)
    return jwt.encode({**claims, "iat": t, "exp": t + seconds}, secret, algorithm=ALGORITHM)


def decode(secret: str, token: str) -> dict:
    """De claims; een jwt.PyJWTError bij elke fout (ook zonder exp)."""
    claims = jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": ["exp"]})
    if not isinstance(claims, dict):
        raise jwt.InvalidTokenError("geen object")
    return claims


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def callback_request(secret: str, fields: dict, now: float | None = None) -> tuple[dict, str]:
    """(body, Authorization) van een callback: body met het token plat, header onder payload."""
    flat = sign(secret, {**fields, "operation": "callback"}, CALLBACK_SECONDS, now)
    body = {**fields, "token": flat}
    header = {k: v for k, v in fields.items() if k != "history"}
    token = sign(secret, {"payload": header, "operation": "callback"}, CALLBACK_SECONDS, now)
    return body, f"Bearer {token}"


def download_token(secret: str, url: str, now: float | None = None) -> str:
    return sign(secret, {"payload": {"url": url}, "operation": "download"}, DOWNLOAD_SECONDS, now)


DOWNLOAD_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "DocumentServer",
    "Want-Repr-Digest": "sha-256=10",
    "Want-Content-Digest": "sha-256=10",
    "Accept-Encoding": "gzip, compress, deflate, br",
}


def repr_digest_ok(header: str | None, content: bytes) -> bool:
    """Klopt Repr-Digest (sha-256=:<base64>:)? Zonder kop: ja (DS controleert dan niets)."""
    if not header:
        return True
    want = base64.b64encode(hashlib.sha256(content).digest()).decode()
    for part in header.split(","):
        name, _, value = part.strip().partition("=")
        if name.strip().lower() == "sha-256":
            return value.strip().strip(":") == want
    return True


# --- secure links ---


def link_md5(link_secret: str, path: str, expires: int) -> str:
    digest = hashlib.md5(f"{expires}{path}{link_secret}".encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def secure_link(
    base: str, link_secret: str, path: str, key: str, filename: str, now: float | None = None
) -> str:
    """Een adres onder /cache/files op base (de host en het prefix van de aanvrager)."""
    expires = int(time.time() if now is None else now) + LINK_SECONDS
    md5 = link_md5(link_secret, path, expires)
    return f"{base}{path}?md5={md5}&expires={expires}&shardkey={key}&filename={filename}"


def link_ok(link_secret: str, path: str, md5: str, expires: str, now: float | None = None) -> bool:
    """403 bij een verkeerde md5, en bij een verlopen link."""
    try:
        when = int(expires)
    except ValueError:
        return False
    if when < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(link_md5(link_secret, path, when), md5)


# --- engine.io en socket.io ---


def eio_open(sid: str) -> str:
    body = {
        "sid": sid,
        "upgrades": [],
        "pingInterval": PING_INTERVAL,
        "pingTimeout": PING_TIMEOUT,
        "maxPayload": MAX_PAYLOAD,
    }
    return "0" + json.dumps(body, separators=(",", ":"))


def sio_connected(sid: str) -> str:
    return "40" + json.dumps({"sid": sid}, separators=(",", ":"))


def sio_error(message: str, code: int | None = None) -> str:
    """CONNECT geweigerd; data.code is de foutcode voor onError (alleen in de nep)."""
    body: dict[str, Any] = {"message": message}
    if code is not None:
        body["data"] = {"code": code}
    return "44" + json.dumps(body, separators=(",", ":"))


def message(data: dict) -> str:
    return "42" + json.dumps(["message", data], separators=(",", ":"))


def parse(frame: str) -> tuple[str, Any]:
    """(soort, inhoud) van een tekstframe: open, close, ping, pong, connect, disconnect,
    connect_error, message (de dict van ["message", {...}]), event (een andere naam) of
    unknown."""
    if not frame:
        return "unknown", None
    eio = frame[0]
    if eio == "0":
        return "open", _json(frame[1:])
    if eio == "1":
        return "close", None
    if eio == "2":
        return "ping", frame[1:]
    if eio == "3":
        return "pong", frame[1:]
    if eio != "4" or len(frame) < 2:
        return "unknown", frame
    sio, rest = frame[1], frame[2:]
    if sio == "0":
        return "connect", _json(rest) if rest else {}
    if sio == "1":
        return "disconnect", None
    if sio == "4":
        return "connect_error", _json(rest)
    if sio == "2":
        data = _json(rest)
        if isinstance(data, list) and data and data[0] == "message" and len(data) > 1:
            return "message", data[1]
        return "event", data
    return "unknown", frame


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def license_message() -> dict:
    """Het eerste bericht na CONNECT [gemeten]."""
    return {
        "type": "license",
        "license": {
            "type": 3,
            "light": False,
            "mode": 0,
            "rights": 1,
            "buildVersion": VERSION,
            "buildNumber": BUILD_NUMBER,
            "protectionSupport": True,
            "isAnonymousSupport": True,
            "liveViewerSupport": True,
            "branding": False,
            "customization": False,
            "advancedApi": False,
        },
        "aiPluginSettingsAllowBrowserProxy": False,
    }


SOCKET_SETTINGS = {
    "reconnection": {"attempts": 50, "delay": 2000},
    "binaryChanges": False,
    "websocketMaxPayloadSize": 1572864,
    "maxChangesSize": 157286400,
    "limits_image_size": 26214400,
    "limits_image_types_upload": (
        "jpg;jpeg;jpe;jfif;jif;png;gif;bmp;svg;tiff;tif;webp;heic;heif;avif"
    ),
}


def session_claims(key: str, permissions: dict, user: dict, index: int, mode: str) -> dict:
    """De inhoud van het sessietoken van DS [gemeten]; exp is 30 dagen."""
    return {
        "document": {"key": key, "permissions": permissions, "ds_encrypted": False},
        "editorConfig": {
            "user": {"id": user.get("id"), "name": user.get("name"), "index": index},
            "mode": mode,
            "ds_view": False,
            "ds_isCloseCoAuthoring": False,
            "ds_sessionTimeConnect": int(time.time() * 1000),
        },
    }


# --- velden van de callbacks ---


def lastsave(t: float) -> str:
    """ "2026-10-26T18:42:56.000Z": op de seconde, zoals DS."""
    return datetime.fromtimestamp(int(t), UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def created(t: float) -> str:
    return datetime.fromtimestamp(int(t), UTC).strftime("%Y-%m-%d %H:%M:%S")


def history(changes: list[dict]) -> dict:
    return {"serverVersion": VERSION, "changes": changes}


def editing(key: str, users: list[str], action: tuple[int, str] | None = None) -> dict:
    """Status 1: bij elke socket die opent of sluit (met actions), en na info (zonder)."""
    fields: dict[str, Any] = {"key": key, "status": EDITING, "users": users}
    if action is not None:
        fields["actions"] = [{"type": action[0], "userid": action[1]}]
    return fields


def closed(key: str, action: tuple[int, str] | None = None) -> dict:
    """Status 4: gesloten zonder wijzigingen."""
    fields: dict[str, Any] = {"key": key, "status": CLOSED}
    if action is not None:
        fields["actions"] = [{"type": action[0], "userid": action[1]}]
    return fields


def saved(
    key: str,
    status: int,
    *,
    url: str,
    changesurl: str,
    changes: list[dict],
    users: list[str],
    lastsave_at: float,
    filetype: str,
    action: tuple[int, str] | None = None,
    forcesavetype: int | None = None,
    userdata: str | None = None,
    notmodified: bool = False,
) -> dict:
    """Status 2 of 6, in de volgorde van de velden zoals gemeten."""
    fields: dict[str, Any] = {
        "key": key,
        "status": status,
        "url": url,
        "changesurl": changesurl,
        "history": history(changes),
        "users": users,
    }
    if userdata is not None:
        fields["userdata"] = userdata
    if action is not None:
        fields["actions"] = [{"type": action[0], "userid": action[1]}]
    fields["lastsave"] = lastsave(lastsave_at)
    if status == SAVE:
        fields["notmodified"] = notmodified
    if status == FORCESAVE:
        fields["forcesavetype"] = BY_COMMAND if forcesavetype is None else forcesavetype
    fields["filetype"] = filetype
    return fields


def version_answer() -> dict:
    return {"error": CMD_OK, "version": BUILD}


def license_answer(connections: int) -> dict:
    """Het antwoord van command license [gemeten], met een instelbaar aantal verbindingen."""
    return {
        "error": CMD_OK,
        "license": {
            "start_date": "2026-10-01T00:00:00.000Z",
            "end_date": None,
            "timelimited": False,
            "trial": False,
            "developer": False,
            "branding": False,
            "customization": False,
            "advanced_api": False,
            "connections": connections,
            "connections_view": connections,
            "users_count": 0,
            "users_view_count": 0,
            "users_expire": 1,
            "customer_id": "",
            "alias": "",
            "multitenancy": False,
        },
        "server": {
            "workersCount": 1,
            "resultType": 3,
            "packageType": 0,
            "buildDate": "2026-10-11T00:00:00.000Z",
            "buildVersion": VERSION,
            "buildNumber": BUILD_NUMBER,
        },
        "quota": {"users": [], "users_view": []},
    }
