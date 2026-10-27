"""De socketstroom van de browser in de tunnel (Keuze 6, StreamGuard; spike stap 12, A1 en
open punt 2).

Guard(settings, key).feed(data) krijgt de bytes van de browser na de upgrade, in de stukken
waarin ze binnenkomen, zolang de tunnel open is. Hij leest de WebSocket-frames (gemaskeerd,
eventueel over stukken en fragmenten verdeeld). Eerst de opening: het CONNECT-pakket van
socket.io en het eerste auth-bericht:

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

Daarna leest hij mee, zonder tijd- of totaalgrens, want DS verhuist een levende verbinding
naar de key van elk nieuw auth-bericht [gemeten met DS 10.0.0.1: daarna leest en schrijft die
verbinding in het andere document]. Een nieuw CONNECT (na 41) en elk event "message" met type
auth krijgen dezelfde controle als de opening; een event met een namespace, een "message" dat
geen object is, of een event dat geen JSON is, is dicht. De rest gaat door, ook binaire frames
(alleen bijlagen van een event dat al gelezen is). Een bericht boven LIMIT bewaart hij niet
(saveChanges kan groot zijn; DS neemt tot 100 MB): dat moet als event beginnen, en mag geen
"auth" en geen \\u bevatten, want zonder die twee staat er in de JSON nergens de string auth.

feed geeft "more" (doorgeven, de opening is nog niet compleet), "ok" (doorgeven; de opening is
goed, blijf voeren) of "deny" (dit stuk niet doorgeven, de tunnel sluit). Een bericht is pas
gelezen als zijn laatste byte er is; zolang komt DS er ook niet aan toe, dus alles vóór die
byte mag al door.
"""

import json
import re
import time
from typing import Any, Literal

import jwt

from app.config import Settings
from app.office.oo import tokens

LIMIT = 64 * 1024
SECONDS = 10.0
# Een event van socket.io zonder namespace: 42[, 42<ack-id>[, 45<bijlagen>-[ (binair)
EVENT = re.compile(rb"4(?:2|5[0-9]+-)[0-9]*\[")
# Zonder een van deze komt de string auth nergens uit de JSON (de enige andere schrijfwijze
# is met \u-escapes)
SUSPECT = (b'"auth"', b"\\u")

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


def _unmask(data: bytes, mask: bytes, offset: int = 0) -> bytes:
    """data met het masker eraf; offset is waar data in de inhoud van het frame begint."""
    n = len(data)
    if n == 0:
        return b""
    turn = offset % 4
    mask = mask[turn:] + mask[:turn]
    pad = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(pad, "big")).to_bytes(n, "big")


class Guard:
    def __init__(self, settings: Settings, key: str) -> None:
        self.settings = settings
        self.key = key
        self.started = clock()
        self.seen = 0  # bytes van de opening
        self.buffer = bytearray()  # wat er nog niet gelezen is (hooguit een halve kop)
        # het frame dat binnenkomt: None tussen twee frames, anders de bytes die nog komen
        self.left: int | None = None
        self.opcode = 0
        self.fin = True
        self.mask = b""
        self.offset = 0
        # het bericht, eventueel in fragmenten
        self.message = bytearray()  # de tekst, tot LIMIT
        self.fragmented = False
        self.binary = False  # een binair bericht (alleen na de opening): niet gelezen
        self.big = False  # boven LIMIT (alleen na de opening): niet bewaard, alleen doorzocht
        self.tail = b""  # het eind van het vorige stuk van een groot bericht
        self.closed = False  # de browser stuurde een close-frame
        self.stage = "connect"  # connect -> auth -> open (de opening is goed: meelezen)
        self.verdict: Verdict = "more"

    def feed(self, data: bytes) -> Verdict:
        if self.verdict == "deny":
            return "deny"
        try:
            if self.stage != "open" and clock() - self.started > SECONDS:
                raise Denied("te traag")
            self.buffer += data
            self._frames()
        except Denied:
            self.verdict = "deny"
            self.buffer.clear()
            self.message.clear()
            return "deny"
        if self.stage == "open":
            self.verdict = "ok"
        return self.verdict

    # --- frames ---

    def _frames(self) -> None:
        """Alles in de buffer lezen; een kop die nog niet compleet is, blijft staan."""
        buf = self.buffer
        while True:
            if self.left is None and not self._head():
                return
            assert self.left is not None
            if self.left:
                take = min(self.left, len(buf))
                if not take:
                    return
                chunk = _unmask(bytes(buf[:take]), self.mask, self.offset)
                self._take(take)
                self.offset += take
                self.left -= take
                self._payload(chunk)
            if self.left == 0:
                self.left = None
                self._end()

    def _take(self, n: int) -> None:
        del self.buffer[:n]
        if self.stage != "open":
            self.seen += n
            if self.seen > LIMIT:
                raise Denied("te groot")

    def _head(self) -> bool:
        """De kop van het volgende frame, als die compleet is."""
        buf = self.buffer
        if len(buf) < 2:
            return False
        if self.closed:
            raise Denied("na close")
        b0, b1 = buf[0], buf[1]
        fin, rsv, opcode = bool(b0 & 0x80), b0 & 0x70, b0 & 0x0F
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
        if self.stage != "open" and length > LIMIT:
            raise Denied("frame te groot")
        if len(buf) < pos + 4:
            return False
        if opcode >= 0x8:  # besturing: close, ping, pong
            if not fin or length > 125 or opcode not in (0x8, 0x9, 0xA):
                raise Denied("besturingsframe")
        elif opcode == 0x1 or (opcode == 0x2 and self.stage == "open"):
            if self.fragmented:
                raise Denied("nieuw bericht in een gefragmenteerd bericht")
            self.message = bytearray()
            self.binary, self.big, self.tail = opcode == 0x2, False, b""
        elif opcode == 0x0:
            if not self.fragmented:
                raise Denied("vervolg zonder begin")
        else:
            raise Denied("binair of onbekend")
        self.opcode, self.fin, self.left, self.offset = opcode, fin, length, 0
        self.mask = bytes(buf[pos : pos + 4])
        self._take(pos + 4)
        return True

    def _payload(self, chunk: bytes) -> None:
        if self.opcode >= 0x8 or self.binary:
            return  # de inhoud van besturing en van een bijlage telt niet
        if self.big:
            self._scan(chunk)
            return
        self.message += chunk
        if len(self.message) > LIMIT:
            if self.stage != "open":
                raise Denied("bericht te groot")
            # Te groot om te bewaren: alleen nog doorzoeken
            if not EVENT.match(self.message):
                raise Denied("groot bericht dat geen event is")
            self.big = True
            self._scan(bytes(self.message))
            self.message = bytearray()

    def _scan(self, chunk: bytes) -> None:
        window = self.tail + chunk
        if any(word in window for word in SUSPECT):
            raise Denied("groot bericht dat een auth kan zijn")
        self.tail = window[-5:]

    def _end(self) -> None:
        """Het frame is compleet."""
        if self.opcode >= 0x8:
            if self.opcode == 0x8:
                self.closed = True
            return
        self.fragmented = not self.fin
        if self.fragmented:
            return
        skip = self.binary or self.big
        self.binary = self.big = False
        self.tail = b""
        if skip:
            return
        try:
            text = bytes(self.message).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise Denied("geen utf-8") from exc
        self.message = bytearray()
        self._packet(text)

    # --- socket.io ---

    def _packet(self, text: str) -> None:
        if self.stage == "open":
            self._watch(text)
            return
        if text in ("2", "3"):  # ping, pong van engine.io
            return
        if self.stage == "connect":
            if not text.startswith("40{"):
                raise Denied("eerst CONNECT")
            self._connect(_object(text[2:]))
            self.stage = "auth"
            return
        if text == "41":
            return
        if not text.startswith('42["message",'):
            raise Denied("eerst auth")
        packet = _json(text[2:])
        if not (isinstance(packet, list) and len(packet) == 2 and packet[0] == "message"):
            raise Denied("geen bericht")
        message = packet[1]
        if not isinstance(message, dict) or message.get("type") != "auth":
            raise Denied("eerst auth")
        self._auth(message)
        self.stage = "open"

    def _watch(self, text: str) -> None:
        """Na de opening: alleen wat DS als een (nieuwe) aanmelding leest, telt."""
        if text.startswith("40"):  # een nieuw CONNECT (na 41): zoals het eerste
            if not text.startswith("40{"):
                raise Denied("CONNECT")
            self._connect(_object(text[2:]))
            return
        if not text.startswith(("42", "45")):
            return  # engine.io, 41, ack: daar leest DS geen aanmelding uit
        found = EVENT.match(text.encode())
        if found is None:
            raise Denied("event met een namespace of een vreemde kop")
        packet = _json(text[found.end() - 1 :])
        if not isinstance(packet, list) or not packet:
            raise Denied("geen event")
        if packet[0] != "message":
            return  # DS luistert alleen naar "message"
        if len(packet) < 2 or not isinstance(packet[1], dict):
            raise Denied("geen bericht")
        if packet[1].get("type") == "auth":
            self._auth(packet[1])

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
    except (ValueError, RecursionError) as exc:
        raise Denied("geen json") from exc


def _object(text: str) -> dict:
    value = _json(text)
    if not isinstance(value, dict):
        raise Denied("geen object")
    return value
