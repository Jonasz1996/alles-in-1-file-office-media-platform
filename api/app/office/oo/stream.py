"""Het eerste socketpakket in de tunnel (Keuze 6, StreamGuard; spike stap 12, A1).

Guard(settings, key).feed(data) krijgt de bytes van de browser na de upgrade, in de stukken
waarin ze binnenkomen. Hij leest de WebSocket-frames (gemaskeerd, eventueel over stukken en
fragmenten verdeeld) tot het CONNECT-pakket van socket.io en het eerste auth-bericht:

    40{"data":{"type":"auth","docid":<key>,"openCmd":{"id":<key>,…},"jwtOpen":<config>,…},
       "token":<config>}                      eerste verbinding
    40{"data":{…,"jwtOpen":<config>},"session":<sessietoken>}           herverbinden
    42["message",{"type":"auth","docid":<key>,…,"jwtOpen"|"jwtSession":…}]

Elke key (docid, openCmd.id) moet de key van het pad zijn, en elk token daarin (token,
session, jwtOpen, jwtSession; data.token alleen als het een JWT is, het is een vaste nepwaarde)
moet kloppen: HS256 met het office-geheim (de config is van ons) of met de afgeleide sleutel
HMAC-SHA256(office-geheim, "editor-session") als hex (het sessietoken van DS, A1), zonder
operation of typ, en met document.key gelijk aan de key van het pad. exp telt hier niet: dat
controleert DS zelf, en de toegang komt van de poort (gate.check, elke 15 s opnieuw); zo breekt
een herverbinding na de 2 uur van het config-token niet op de wacht (spike, open punt 1).

Vóór het CONNECT-pakket mogen alleen ping en pong van engine.io ("2", "3"), daarna tot het
auth-bericht ook "41" (weg). Al het andere is dicht: een binair frame, een gecomprimeerd frame
(RSV-bit; DS onderhandelt geen permessage-deflate en de tunnel geeft de kop niet door), een
frame zonder masker, een ongeldige UTF-8-tekst, een namespace of een ander bericht. Hooguit
LIMIT bytes en SECONDS seconden vanaf het maken van de wacht.

feed geeft "more" (doorgeven, verder lezen), "ok" (doorgeven; vanaf nu alleen pompen) of
"deny" (dit stuk niet doorgeven, de tunnel sluit). Een bericht is pas gelezen als zijn laatste
byte er is; zolang komt DS er ook niet aan toe, dus alles vóór die byte mag al door.
"""

import json
import time
from typing import Any, Literal

import jwt

from app.config import Settings
from app.office.oo import tokens

LIMIT = 64 * 1024
SECONDS = 10.0

# De klok van de wacht (de tests zetten hem vooruit)
clock = time.monotonic

Verdict = Literal["more", "ok", "deny"]


class Denied(Exception):
    pass


def _claims(token: str, secret: str, check_exp: bool) -> dict | None:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[tokens.ALGORITHM],
            leeway=tokens.LEEWAY,
            options={"require": ["exp"], "verify_exp": check_exp},
        )
    except jwt.PyJWTError:
        return None
    return claims if isinstance(claims, dict) else None


def token_key(
    settings: Settings, token: Any, *, session_only: bool = False, check_exp: bool = False
) -> str | None:
    """document.key van een config-token (office-geheim) of een sessietoken van DS (afgeleide
    sleutel), of None als het token niet klopt. session_only: alleen het sessietoken."""
    if not isinstance(token, str) or not token or len(token) > LIMIT:
        return None
    secrets = [tokens.session_secret(settings)]
    if not session_only:
        secrets.insert(0, settings.office_secret)
    for secret in secrets:
        claims = _claims(token, secret, check_exp)
        if claims is None:
            continue
        if "operation" in claims or "typ" in claims:
            return None
        document = claims.get("document")
        if not isinstance(document, dict):
            return None
        key = document.get("key")
        return key if isinstance(key, str) and key else None
    return None


def _jwt_shaped(value: Any) -> bool:
    return isinstance(value, str) and value.count(".") == 2


def _unmask(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    if n == 0:
        return b""
    pad = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(pad, "big")).to_bytes(n, "big")


class Guard:
    def __init__(self, settings: Settings, key: str) -> None:
        self.settings = settings
        self.key = key
        self.started = clock()
        self.seen = 0
        self.buffer = bytearray()  # bytes van een frame dat nog niet compleet is
        self.message = bytearray()  # de tekst van een bericht in fragmenten
        self.fragmented = False
        self.closed = False  # de browser stuurde een close-frame
        self.stage = "connect"  # connect -> auth -> klaar
        self.verdict: Verdict = "more"

    def feed(self, data: bytes) -> Verdict:
        if self.verdict != "more":
            return self.verdict
        try:
            if clock() - self.started > SECONDS:
                raise Denied("te traag")
            self.seen += len(data)
            self.buffer += data
            if self._frames():
                self.verdict = "ok"
                self.buffer.clear()
                self.message.clear()
                return "ok"
            if self.seen > LIMIT:
                raise Denied("te groot")
        except Denied:
            self.verdict = "deny"
            self.buffer.clear()
            self.message.clear()
        return self.verdict

    # --- frames ---

    def _frames(self) -> bool:
        """Alle complete frames in de buffer; True zodra het auth-bericht er goed door is."""
        buf = self.buffer
        while len(buf) >= 2:
            if self.closed:
                raise Denied("na close")
            b0, b1 = buf[0], buf[1]
            fin, rsv, opcode = b0 & 0x80, b0 & 0x70, b0 & 0x0F
            if rsv or not b1 & 0x80:
                raise Denied("rsv of geen masker")
            length, pos = b1 & 0x7F, 2
            if length == 126:
                if len(buf) < 4:
                    return False
                length, pos = int.from_bytes(buf[2:4], "big"), 4
            elif length == 127:
                if len(buf) < 10:
                    return False
                length, pos = int.from_bytes(buf[2:10], "big"), 10
            if length > LIMIT:
                raise Denied("frame te groot")
            end = pos + 4 + length
            if len(buf) < end:
                return False
            payload = _unmask(bytes(buf[pos + 4 : end]), bytes(buf[pos : pos + 4]))
            del buf[:end]
            if self._frame(bool(fin), opcode, payload):
                return True
        return False

    def _frame(self, fin: bool, opcode: int, payload: bytes) -> bool:
        if opcode >= 0x8:  # besturing: close, ping, pong
            if not fin or len(payload) > 125 or opcode not in (0x8, 0x9, 0xA):
                raise Denied("besturingsframe")
            if opcode == 0x8:
                self.closed = True
            return False
        if opcode == 0x1:
            if self.fragmented:
                raise Denied("nieuw bericht in een gefragmenteerd bericht")
            self.message = bytearray(payload)
        elif opcode == 0x0:
            if not self.fragmented:
                raise Denied("vervolg zonder begin")
            self.message += payload
        else:
            raise Denied("binair of onbekend")
        if len(self.message) > LIMIT:
            raise Denied("bericht te groot")
        self.fragmented = not fin
        if self.fragmented:
            return False
        try:
            text = bytes(self.message).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise Denied("geen utf-8") from exc
        self.message = bytearray()
        return self._packet(text)

    # --- socket.io ---

    def _packet(self, text: str) -> bool:
        if text in ("2", "3"):  # ping, pong van engine.io
            return False
        if self.stage == "connect":
            if not text.startswith("40{"):
                raise Denied("eerst CONNECT")
            self._connect(_object(text[2:]))
            self.stage = "auth"
            return False
        if text == "41":
            return False
        if not text.startswith('42["message",'):
            raise Denied("eerst auth")
        packet = _json(text[2:])
        if not (isinstance(packet, list) and len(packet) == 2 and packet[0] == "message"):
            raise Denied("geen bericht")
        message = packet[1]
        if not isinstance(message, dict) or message.get("type") != "auth":
            raise Denied("eerst auth")
        self._auth(message)
        return True

    def _connect(self, packet: dict) -> None:
        data = packet.get("data")
        if not isinstance(data, dict) or data.get("type") != "auth":
            raise Denied("CONNECT zonder auth")
        self._keys(data)
        found = self._tokens(packet, ("token", "session"))
        found += self._tokens(data, ("jwtOpen", "jwtSession", "session"))
        if not found:
            raise Denied("CONNECT zonder token")

    def _auth(self, message: dict) -> None:
        self._keys(message)
        if not self._tokens(message, ("jwtOpen", "jwtSession", "session")):
            raise Denied("auth zonder token")

    def _keys(self, data: dict) -> None:
        if data.get("docid") != self.key:
            raise Denied("andere docid")
        opened = data.get("openCmd")
        if opened is not None:
            if not isinstance(opened, dict):
                raise Denied("openCmd")
            if opened.get("id") is not None and opened.get("id") != self.key:
                raise Denied("openCmd over een andere key")
        # data.token is een vaste nepwaarde; is het toch een JWT, dan telt hij mee
        if _jwt_shaped(data.get("token")):
            self._check(data["token"])

    def _tokens(self, data: dict, names: tuple[str, ...]) -> int:
        found = 0
        for name in names:
            value = data.get(name)
            if value is None:
                continue
            self._check(value)
            found += 1
        return found

    def _check(self, token: Any) -> None:
        if token_key(self.settings, token) != self.key:
            raise Denied("token van een andere key of met een ander geheim")


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Denied("geen json") from exc


def _object(text: str) -> dict:
    value = _json(text)
    if not isinstance(value, dict):
        raise Denied("geen object")
    return value
