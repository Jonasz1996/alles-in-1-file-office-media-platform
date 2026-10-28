"""De API van een geïnstalleerd platform, voor infra/single/tests/test_install_update.sh.

Alleen de standaardbibliotheek: draait met python3 op de host, naast de echte installatie. De
login staat in het bestand INSTALL_API_STATE (rechten 600, in de werkmap van de test):
e-mailadres, wachtwoord, het TOTP-geheim, de laatst gebruikte stap en de tokens. Niets daarvan
komt op stdout of in een log. INSTALL_API_URL is het adres van de app (http://127.0.0.1:<poort>).

    python3 install_api.py setup                 eerste beheerder (/auth/setup), dan 2FA aan
    python3 install_api.py login                 inloggen met wachtwoord en een TOTP-code
    python3 install_api.py login-zonder-code     slaagt als de app 401 totp_required geeft
    python3 install_api.py get <pad> [veld]      GET; één veld (a.0.b) of de hele JSON
    python3 install_api.py count <pad>           het aantal in een lijst (GET)
    python3 install_api.py post <pad> <json> [veld]
    python3 install_api.py upload <map> <naam> <bestand> [conflict]   geeft het id
    python3 install_api.py sha <bestand> [versie]    sha256 van de inhoud
    python3 install_api.py text <bestand> [versie]   de tekst van een docx
    python3 install_api.py wait <toestand> <s>   tot GET /office/status die toestand zegt
    python3 install_api.py watch <s> <uitvoer>   elke seconde /office/status, tot ok
    python3 install_api.py notice <code>         slaagt als /admin/status die melding heeft
    python3 install_api.py font-end <log> <+hhmm>    het einde van de font-herstart (epoch)

TOTP zoals app/totp.py (RFC 6238: HMAC-SHA1, 6 cijfers, 30 s). De server neemt een stap maar
één keer aan; een nieuwe login wacht dus zo nodig op de volgende stap, zoals totp_code in
e2e/conftest.py. Een access-token dat verlopen is, wordt met het refresh-token vernieuwd.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree

STEP = 30
DIGITS = 6
EMAIL = "beheer@example.com"
NAME = "Beheer"
# Het platform draait op 127.0.0.1: nooit via een proxy uit de omgeving
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ApiError(Exception):
    def __init__(self, method: str, path: str, status: int, body: bytes):
        super().__init__(f"{method} {path} gaf {status}: {body[:300]!r}")
        self.status = status


def base() -> str:
    return os.environ["INSTALL_API_URL"].rstrip("/")


def load() -> dict[str, Any]:
    with open(os.environ["INSTALL_API_STATE"]) as f:
        return json.load(f)


def save(state: dict[str, Any]) -> None:
    path = os.environ["INSTALL_API_STATE"]
    tmp = path + ".nieuw"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def next_code(state: dict[str, Any]) -> str:
    """Een code voor een stap die de server nog niet zag (wacht zo nodig op de volgende)."""
    target = max(int(time.time() // STEP), state.get("step", -1) + 1)
    while int(time.time() // STEP) < target:
        time.sleep(0.5)
    state["step"] = target
    save(state)
    return code_at(state["secret"], target)


def request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: Any = None,
    raw: bytes | None = None,
) -> tuple[int, bytes]:
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        data = raw
        headers["Content-Type"] = "application/octet-stream"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base() + path, data=data, method=method, headers=headers)
    try:
        with OPENER.open(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def keep_tokens(state: dict[str, Any], answer: bytes) -> None:
    tokens = json.loads(answer)
    state["access"] = tokens["access_token"]
    state["refresh"] = tokens["refresh_token"]
    save(state)


def call(
    method: str,
    path: str,
    *,
    body: Any = None,
    raw: bytes | None = None,
    expect: tuple[int, ...] = (200,),
) -> bytes:
    """Met het access-token; is dat verlopen (401), dan eerst één keer vernieuwen."""
    state = load()
    status, answer = request(method, path, token=state.get("access"), body=body, raw=raw)
    if status == 401 and state.get("refresh"):
        renewed, tokens = request(
            "POST", "/api/v1/auth/refresh", body={"refresh_token": state["refresh"]}
        )
        if renewed == 200:
            keep_tokens(state, tokens)
            status, answer = request(method, path, token=state["access"], body=body, raw=raw)
    if status not in expect:
        raise ApiError(method, path, status, answer)
    return answer


def pick(value: Any, field: str | None) -> Any:
    for part in field.split(".") if field else []:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def show(value: Any) -> None:
    print(value if isinstance(value, str) else json.dumps(value))


def setup() -> None:
    password = secrets.token_hex(16)
    body = {"email": EMAIL, "display_name": NAME, "password": password}
    status, answer = request("POST", "/api/v1/auth/setup", body=body)
    if status != 201:
        raise ApiError("POST", "/api/v1/auth/setup", status, answer)
    user = json.loads(answer)["user"]
    state = {"email": EMAIL, "password": password, "root": user["root_id"], "user": user["id"]}
    keep_tokens(state, answer)
    # 2FA is verplicht: zonder kan de beheerder verder niets
    secret = json.loads(call("POST", "/api/v1/auth/2fa/setup"))["secret"]
    state = load()
    state["secret"] = secret
    save(state)
    code = next_code(state)
    call("POST", "/api/v1/auth/2fa/enable", body={"code": code, "password": password})


def login(with_code: bool = True) -> bool:
    state = load()
    body = {"email": state["email"], "password": state["password"]}
    if with_code:
        body["code"] = next_code(state)
    status, answer = request("POST", "/api/v1/auth/login", body=body)
    if not with_code:
        # Een fout van de API: {"detail": {"message": …, "code": …}} (app/errors.py)
        return status == 401 and json.loads(answer)["detail"].get("code") == "totp_required"
    if status != 200:
        raise ApiError("POST", "/api/v1/auth/login", status, answer)
    keep_tokens(load(), answer)
    return True


def content(node: str, version: str | None) -> bytes:
    query = f"?{urllib.parse.urlencode({'version': version})}" if version else ""
    return call("GET", f"/api/v1/files/{node}/content{query}")


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    return "\n".join("".join(t.text or "" for t in p.iter(f"{W}t")) for p in root.iter(f"{W}p"))


def office_state() -> tuple[str, bool]:
    status = json.loads(call("GET", "/api/v1/office/status"))
    return status["state"], bool(status["editing"])


def wait(wanted: str, seconds: float) -> bool:
    end = time.monotonic() + seconds
    seen = None
    while time.monotonic() < end:
        try:
            seen = office_state()[0]
        except (ApiError, OSError) as exc:  # de app start misschien net opnieuw
            seen = f"fout ({exc})"
        if seen == wanted:
            return True
        time.sleep(2)
    print(f"  /api/v1/office/status zegt na {seconds:.0f} s nog {seen}", file=sys.stderr)
    return False


def watch(seconds: float, out: str) -> bool:
    """Elke seconde de toestand, met de tijd (epoch), tot de eerste ok."""
    end = time.monotonic() + seconds
    with open(out, "w") as f:
        while time.monotonic() < end:
            try:
                state, editing = office_state()
            except (ApiError, OSError) as exc:
                state, editing = f"fout:{type(exc).__name__}", False
            f.write(f"{time.time():.3f} {state} {str(editing).lower()}\n")
            f.flush()
            if state == "ok":
                return True
            time.sleep(1)
    return False


SUPERVISOR = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) \w+ (stopped|success): (?:ds:)?converter\b"
)


def font_end(log: str, offset: str) -> float | None:
    """Het einde van de font-herstart volgens supervisord in DS (runbook "DS bijwerken"): de
    eerste "success: converter" na "stopped: converter". offset is `date +%z` in DS."""
    sign = -1 if offset.startswith("-") else 1
    tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
    stopped = False
    with open(log, errors="replace") as f:
        for line in f:
            m = SUPERVISOR.match(line)
            if not m:
                continue
            if m.group(3) == "stopped":
                stopped = True
            elif stopped:
                at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
                return at.timestamp() + int(m.group(2)) / 1000
    return None


def main(argv: list[str]) -> int:
    command, args = argv[0], argv[1:]
    if command == "setup":
        setup()
    elif command == "login":
        login()
    elif command == "login-zonder-code":
        return 0 if login(with_code=False) else 1
    elif command == "get":
        show(pick(json.loads(call("GET", args[0])), args[1] if len(args) > 1 else None))
    elif command == "count":
        print(len(json.loads(call("GET", args[0]))))
    elif command == "post":
        answer = call("POST", args[0], body=json.loads(args[1]), expect=(200, 201, 202, 204))
        if len(args) > 2:
            show(pick(json.loads(answer), args[2]))
    elif command == "upload":
        query = urllib.parse.urlencode(
            {"name": args[1], "conflict": args[3] if len(args) > 3 else "rename"}
        )
        with open(args[2], "rb") as f:
            data = f.read()
        node = call("PUT", f"/api/v1/files/{args[0]}/upload?{query}", raw=data, expect=(201,))
        print(json.loads(node)["id"])
    elif command == "sha":
        print(hashlib.sha256(content(args[0], args[1] if len(args) > 1 else None)).hexdigest())
    elif command == "text":
        print(docx_text(content(args[0], args[1] if len(args) > 1 else None)))
    elif command == "wait":
        return 0 if wait(args[0], float(args[1])) else 1
    elif command == "watch":
        return 0 if watch(float(args[0]), args[1]) else 1
    elif command == "notice":
        notices = json.loads(call("GET", "/api/v1/admin/status")).get("notices") or []
        return 0 if any(n.get("code") == args[0] for n in notices) else 1
    elif command == "font-end":
        end = font_end(args[0], args[1])
        if end is None:
            return 1
        print(f"{end:.3f}")
    else:
        print(f"onbekend commando: {command}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ApiError as exc:
        print(f"  {exc}", file=sys.stderr)
        sys.exit(1)
