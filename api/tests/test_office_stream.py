"""De StreamGuard (oo/stream.py, spike stap 12, A1): het CONNECT-pakket en het eerste
auth-bericht van de browser, als gemaskeerde WebSocket-frames, eventueel in stukken, en daarna
de rest van de stroom.

Een token van key A op het pad van A gaat door; een token van key B op het pad van A, of een
token met een ander geheim, gaat dicht; het sessietoken van DS (de afgeleide sleutel) gaat door;
te groot of te traag dicht; frames in stukken en in fragmenten gaan door. Na de opening leest
hij mee: gewoon verkeer gaat door, ook groot en binair, maar een nieuw auth-bericht of CONNECT
over een andere key gaat dicht (DS verhuist een levende verbinding anders naar die key)."""

import base64
import json
import os
import secrets
import struct
import time

import jwt
import pytest

from app.config import Settings
from app.office.oo import stream, tokens
from tests.fake_docserver.client import EditorClient

KEY = "4f3c2a1b0e9d8c7b6a5f4e3d2c1b0a99-0123456789abcdef"
OTHER = "aaaabbbbccccddddeeeeffff00001111-fedcba9876543210"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        jwt_secret="app-" + secrets.token_hex(16),
        office_url="http://office",
        office_app_url="http://platform:8081",
        office_secret=secrets.token_urlsafe(32),
    )


def frame(
    payload: bytes | str,
    opcode: int = 0x1,
    *,
    fin: bool = True,
    masked: bool = True,
    rsv: int = 0,
) -> bytes:
    """Eén WebSocket-frame van de browser (gemaskeerd, zoals RFC 6455 eist)."""
    data = payload.encode() if isinstance(payload, str) else payload
    head = bytes([(0x80 if fin else 0) | rsv | opcode])
    n = len(data)
    bit = 0x80 if masked else 0
    if n < 126:
        head += bytes([bit | n])
    elif n < 65536:
        head += bytes([bit | 126]) + struct.pack(">H", n)
    else:
        head += bytes([bit | 127]) + struct.pack(">Q", n)
    if not masked:
        return head + data
    mask = os.urandom(4)
    return head + mask + xor(data, mask)


def xor(data: bytes, mask: bytes) -> bytes:
    """Het masker over de inhoud (RFC 6455, 5.3), los van de code van de wacht; groot in één
    keer als getal."""
    n = len(data)
    if n < 4096:
        return bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    pad = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "little") ^ int.from_bytes(pad, "little")).to_bytes(n, "little")


def config(settings: Settings, key: str = KEY, **extra) -> dict:
    body = {
        "document": {"fileType": "docx", "key": key, "title": "a.docx", "url": "http://x/doc"},
        "editorConfig": {"user": {"id": "u1", "name": "Anna"}, "callbackUrl": "http://x/cb"},
        **extra,
    }
    return {**body, "token": tokens.browser_token(settings, body)}


def session_token(settings: Settings, key: str = KEY, secret: str | None = None, **extra) -> str:
    now = int(time.time())
    claims = {"document": {"key": key}, "editorConfig": {"user": {"id": "u1"}}, **extra}
    claims.setdefault("iat", now)
    claims.setdefault("exp", now + 30 * 24 * 3600)
    return jwt.encode(claims, secret or tokens.session_secret(settings), algorithm="HS256")


def opening(cfg: dict, *, session: str | None = None) -> tuple[str, str]:
    """CONNECT en auth zoals de editor ze stuurt (de vorm van de nep-editor, gelijk aan de
    opname in recorded/socket.json)."""
    editor = EditorClient("ws://x", cfg)
    connect = {"data": editor._auth(True)}
    if session:
        connect["session"] = session
        editor.session = session
        auth = editor._auth(False)
    else:
        connect["token"] = cfg["token"]
        auth = editor._auth(True)
    return "40" + json.dumps(connect), '42["message",' + json.dumps(auth) + "]"


def run(guard: stream.Guard, *chunks: bytes) -> list[str]:
    return [guard.feed(chunk) for chunk in chunks]


def test_eerste_verbinding_met_het_config_token(settings):
    cfg = config(settings)
    connect, auth = opening(cfg)
    guard = stream.Guard(settings, KEY)
    assert run(guard, frame(connect), frame("3"), frame(auth)) == ["more", "more", "ok"]
    # Daarna leest hij mee: gewoon verkeer gaat door, iets dat geen frame is niet
    assert guard.feed(frame('42["message",{"type":"cursor","cursor":"1"}]')) == "ok"
    assert guard.feed(b"\x00\x01kapot") == "deny"


def test_herverbinden_met_het_sessietoken_van_ds(settings):
    """A1: het sessietoken is getekend met HMAC-SHA256(office-geheim, "editor-session")."""
    cfg = config(settings)
    connect, auth = opening(cfg, session=session_token(settings))
    assert '"jwtSession"' in auth and '"session"' in connect
    guard = stream.Guard(settings, KEY)
    assert run(guard, frame(connect), frame(auth)) == ["more", "ok"]


def test_een_verlopen_config_token_telt_niet_als_fout(settings):
    """exp controleert DS zelf (spike, open punt 1); de wacht kijkt naar handtekening en key."""
    body = {"document": {"key": KEY}}
    old = jwt.encode(
        {**body, "iat": int(time.time()) - 9000, "exp": int(time.time()) - 1800},
        settings.office_secret,
        algorithm="HS256",
    )
    cfg = {**body, "token": old}
    connect, auth = opening(cfg)
    assert run(stream.Guard(settings, KEY), frame(connect), frame(auth)) == ["more", "ok"]


def swap(packet: str, path: list, value) -> str:
    """Het pakket met één veld vervangen (path: sleutels vanaf de JSON)."""
    head, body = packet[:2], json.loads(packet[2:])
    target = body
    for step in path[:-1]:
        target = target[step]
    if value is None:
        target.pop(path[-1], None)
    else:
        target[path[-1]] = value
    return head + json.dumps(body)


@pytest.mark.parametrize(
    ("which", "path", "kind"),
    [
        ("connect", ["token"], "config"),
        ("connect", ["data", "jwtOpen"], "config"),
        ("connect", ["data", "docid"], "key"),
        ("connect", ["data", "openCmd", "id"], "key"),
        ("connect", ["session"], "session"),
        ("connect", ["data", "token"], "config"),  # de nepwaarde, maar nu een JWT van B
        ("auth", [1, "jwtOpen"], "config"),
        ("auth", [1, "docid"], "key"),
        ("auth", [1, "jwtSession"], "session"),
    ],
)
def test_iets_van_een_andere_key_gaat_dicht(settings, which, path, kind):
    """M15: wie lid is van A en een token van B heeft, komt via het pad van A niet bij B."""
    connect, auth = opening(config(settings))
    value = {
        "config": config(settings, OTHER)["token"],
        "session": session_token(settings, OTHER),
        "key": OTHER,
    }[kind]
    if which == "connect":
        connect = swap(connect, path, value)
        assert run(stream.Guard(settings, KEY), frame(connect)) == ["deny"]
    else:
        auth = swap(auth, path, value)
        assert run(stream.Guard(settings, KEY), frame(connect), frame(auth)) == ["more", "deny"]


@pytest.mark.parametrize(
    "token",
    [
        "kapot",
        "a.b.c",
        "",
        12,
        {"key": KEY},
    ],
)
def test_een_kapot_token_gaat_dicht(settings, token):
    connect, _ = opening(config(settings))
    assert run(stream.Guard(settings, KEY), frame(swap(connect, ["token"], token))) == ["deny"]


def test_een_ander_geheim_of_soort_token_gaat_dicht(settings):
    other = Settings(**{**settings.model_dump(), "office_secret": secrets.token_urlsafe(32)})
    bad = [
        config(other)["token"],  # een ander office-geheim
        session_token(settings, secret=settings.office_secret + "x"),
        session_token(settings, secret=settings.secret()),  # het app-geheim
        jwt.encode({"document": {"key": KEY}}, settings.office_secret),  # zonder exp
        session_token(settings, operation="download"),  # een token van DS naar de app
        session_token(settings, typ="office_gate"),  # een token van de app zelf
        jwt.encode(
            {"document": {"key": KEY}, "exp": int(time.time()) + 60},
            settings.office_secret,
            algorithm="HS512",
        ),
    ]
    for token in bad:
        connect, _ = opening(config(settings))
        assert run(stream.Guard(settings, KEY), frame(swap(connect, ["token"], token))) == [
            "deny"
        ], token


def test_zonder_token_dicht(settings):
    connect, auth = opening(config(settings))
    connect = swap(swap(connect, ["token"], None), ["data", "jwtOpen"], None)
    assert run(stream.Guard(settings, KEY), frame(connect)) == ["deny"]
    connect, auth = opening(config(settings))
    auth = swap(auth, [1, "jwtOpen"], None)
    assert run(stream.Guard(settings, KEY), frame(connect), frame(auth)) == ["more", "deny"]


def test_de_nepwaarde_in_data_token_telt_niet(settings):
    connect, auth = opening(config(settings))
    assert '"fghhfgsjdgfjs"' in connect
    assert run(stream.Guard(settings, KEY), frame(connect), frame(auth)) == ["more", "ok"]


def test_frames_in_stukken_en_fragmenten(settings):
    connect, auth = opening(config(settings))
    data = frame(connect) + frame("2") + frame(b"", 0x9) + frame(auth)
    guard = stream.Guard(settings, KEY)
    verdicts = [guard.feed(data[i : i + 1]) for i in range(len(data))]
    assert verdicts[:-1] == ["more"] * (len(data) - 1) and verdicts[-1] == "ok"
    # Een bericht in fragmenten (tekst, vervolg, vervolg), met een ping ertussen
    raw = auth.encode()
    third = len(raw) // 3
    parts = (
        frame(raw[:third], 0x1, fin=False)
        + frame(b"x", 0x9)
        + frame(raw[third : 2 * third], 0x0, fin=False)
        + frame(raw[2 * third :], 0x0)
    )
    guard = stream.Guard(settings, KEY)
    assert run(guard, frame(connect), parts[:100], parts[100:]) == ["more", "more", "ok"]


def test_een_verkeerd_bericht_komt_nooit_compleet_bij_ds(settings):
    """Tot de laatste byte van een bericht mag alles door; het stuk met die byte niet."""
    connect, _ = opening(config(settings))
    bad = frame(swap(connect, ["token"], config(settings, OTHER)["token"]))
    guard = stream.Guard(settings, KEY)
    assert run(guard, bad[:-1], bad[-1:]) == ["more", "deny"]
    assert guard.feed(frame("3")) == "deny"  # blijft dicht


@pytest.mark.parametrize(
    "first",
    [
        frame('40{"data":{}}', masked=False),  # geen masker
        frame("40{}", rsv=0x40),  # gecomprimeerd (RSV1)
        frame(b"40{}", 0x2),  # binair
        frame(b"\xff\xfe", 0x1),  # geen utf-8
        frame('42["message",{"type":"auth"}]'),  # eerst CONNECT
        frame('40/editor,{"data":{}}'),  # een namespace
        frame("4{}"),
        frame("1"),
        frame("x", 0x0),  # vervolg zonder begin
        frame("x", 0x3),  # gereserveerd
        frame("x", 0xB),  # gereserveerde besturing
        frame(b"x" * 126, 0x9),  # besturing te lang
        frame("2", 0x9, fin=False),  # besturing in fragmenten
    ],
)
def test_vreemde_frames_dicht(settings, first):
    assert stream.Guard(settings, KEY).feed(first) == "deny"


def test_na_connect_eerst_auth(settings):
    connect, auth = opening(config(settings))
    for between in (
        '42["message",{"type":"saveChanges","text":"x"}]',
        '421["message",{"type":"auth"}]',  # met een ack-id
        "43[]",
        '42["other",{"type":"auth"}]',
        '42["message","auth"]',
        "45-[]",
    ):
        guard = stream.Guard(settings, KEY)
        assert run(guard, frame(connect), frame(between)) == ["more", "deny"], between
    # 41 (weg) en ping/pong mogen
    guard = stream.Guard(settings, KEY)
    assert run(guard, frame(connect), frame("3"), frame("41")) == ["more", "more", "more"]


def test_na_close_niets_meer(settings):
    connect, _ = opening(config(settings))
    guard = stream.Guard(settings, KEY)
    assert run(guard, frame(b"\x03\xe8", 0x8), frame(connect)) == ["more", "deny"]


def test_te_groot_dicht(settings):
    connect, auth = opening(config(settings))
    guard = stream.Guard(settings, KEY)
    # Veel pings: geen bericht te groot, wel samen boven de grens
    ping = frame("2")
    count = stream.LIMIT // len(ping) + 1
    assert guard.feed(ping * count) == "deny"
    # Eén frame boven de grens (de kop zegt het al)
    big = frame("40" + "x" * (stream.LIMIT + 10))
    assert stream.Guard(settings, KEY).feed(big[:20]) == "deny"
    # Een bericht in fragmenten boven de grens
    guard = stream.Guard(settings, KEY)
    piece = frame("x" * 40000, 0x1, fin=False) + frame("x" * 40000, 0x0, fin=False)
    assert guard.feed(piece) == "deny"


def test_te_traag_dicht(settings, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(stream, "clock", lambda: now[0])
    connect, auth = opening(config(settings))
    guard = stream.Guard(settings, KEY)
    assert guard.feed(frame(connect)) == "more"
    now[0] += stream.SECONDS + 0.1
    assert guard.feed(frame(auth)) == "deny"
    # Binnen de tijd: goed
    guard = stream.Guard(settings, KEY)
    now[0] += stream.SECONDS - 0.5
    assert run(guard, frame(connect), frame(auth)) == ["more", "ok"]


def test_lange_frames_met_16_bits_lengte(settings):
    """Een config-token van een paar KB: de lengte staat dan in 16 bits."""
    cfg = config(settings, extra={"pad": "x" * 3000})
    connect, auth = opening(cfg)
    assert len(connect) > 125
    assert run(stream.Guard(settings, KEY), frame(connect), frame(auth)) == ["more", "ok"]


def test_token_key(settings):
    assert stream.token_key(settings, config(settings)["token"]) == KEY
    assert stream.token_key(settings, session_token(settings)) == KEY
    assert stream.token_key(settings, config(settings)["token"], session_only=True) is None
    assert stream.token_key(settings, session_token(settings, exp=1), check_exp=True) is None
    assert stream.token_key(settings, session_token(settings, exp=1)) == KEY
    no_doc = jwt.encode({"exp": int(time.time()) + 60}, settings.office_secret)
    assert stream.token_key(settings, no_doc) is None
    assert stream.token_key(settings, None) is None


# --- na de opening: meelezen (spike, open punt 2) ---


def opened_guard(settings: Settings) -> stream.Guard:
    """Een wacht na een goede opening over KEY (CONNECT en auth met het config-token)."""
    connect, auth = opening(config(settings))
    guard = stream.Guard(settings, KEY)
    assert run(guard, frame(connect), frame(auth)) == ["more", "ok"]
    return guard


def message(data: dict, head: str = "42") -> str:
    return head + '["message",' + json.dumps(data) + "]"


def save_changes(size: int) -> str:
    """Een saveChanges zoals de editor hem stuurt: de wijzigingen als JSON in een string, elk
    "lengte;base64" (zonder aanhalingstekens of \\u)."""
    change = f"{size};" + base64.b64encode(os.urandom(size * 3 // 4)).decode()
    return message(
        {
            "type": "saveChanges",
            "changes": json.dumps([change]),
            "startSaveChanges": True,
            "endSaveChanges": True,
            "isCoAuthoring": True,
            "isExcel": False,
            "deleteIndex": None,
            "excelAdditionalInfo": None,
            "unlock": False,
            "releaseLocks": False,
        }
    )


def chunks(data: bytes, size: int) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


def other_auth(settings: Settings, **swaps) -> dict:
    """Het auth-bericht van een herverbinding over OTHER, met het sessietoken van DS voor OTHER
    (wat iemand overhoudt die eerder in OTHER zat; M14: 30 dagen geldig)."""
    editor = EditorClient("ws://x", config(settings, OTHER))
    editor.session = session_token(settings, OTHER)
    data = editor._auth(False)
    data.update(swaps)
    return data


def test_na_de_opening_gaat_gewoon_verkeer_door(settings, monkeypatch):
    """Alles wat de editor na de opening stuurt, gaat door: ping en pong, berichten, een groot
    saveChanges in stukken, in fragmenten en in één frame, binaire frames, 41, en een nieuwe
    auth over dezelfde key. Zonder tijd- of totaalgrens."""
    now = [1000.0]
    monkeypatch.setattr(stream, "clock", lambda: now[0])
    guard = opened_guard(settings)
    now[0] += stream.SECONDS * 10
    small = [
        "2",
        "3",
        '42["message",{"type":"cursor","cursor":"1;2;3"}]',
        message({"type": "message", "messages": [{"message": 'zei "auth" en \\u0041'}]}),
        message({"type": "getLock", "block": [{"guid": "x", "type": 1}]}),
        message({"type": "isSaveLock"}),
        message({"type": "clientLog", "level": "log", "msg": "author"}),
        save_changes(200),
        '42["other",{"type":"auth","docid":"' + OTHER + '"}]',  # geen "message": DS leest het niet
        "43[]",
    ]
    assert run(guard, *(frame(text) for text in small)) == ["ok"] * len(small)
    assert guard.feed(frame(b"", 0x9) + frame(b"", 0xA)) == "ok"
    # Groot: 1 MB in stukken die niet op 4 bytes vallen (het masker loopt door)
    big = frame(save_changes(1024 * 1024))
    assert set(run(guard, *chunks(big, 60_001))) == {"ok"}
    # 5 MB in één frame, en in fragmenten van 100 KB met een ping ertussen
    assert set(run(guard, *chunks(frame(save_changes(5 * 1024 * 1024)), 256 * 1024))) == {"ok"}
    raw = save_changes(1024 * 1024).encode()
    parts = chunks(raw, 100_000)
    pieces = [frame(parts[0], 0x1, fin=False), frame(b"p", 0x9)]
    pieces += [frame(part, 0x0, fin=False) for part in parts[1:-1]]
    pieces.append(frame(parts[-1], 0x0))
    assert run(guard, *pieces) == ["ok"] * len(pieces)
    # Binair (binaryChanges van DS): het event met een plaatshouder, dan de bijlage, ook in
    # fragmenten; de bijlage leest hij niet
    binary_event = (
        '451-["message",{"type":"saveChanges","changes":[{"_placeholder":true,"num":0}]}]'
    )
    attachment = b'"auth"\\u' + os.urandom(100_000)
    assert (
        run(
            guard,
            frame(binary_event),
            frame(attachment, 0x2),
            frame(attachment[:10], 0x2, fin=False),
            frame(attachment[10:], 0x0),
        )
        == ["ok"] * 4
    )
    # Een nieuwe auth over dezelfde key (met het sessietoken van DS) en 41
    _, again = opening(config(settings), session=session_token(settings))
    assert run(guard, frame(again), frame("41")) == ["ok", "ok"]
    assert guard.feed(frame("2") * 20_000) == "ok"  # samen ver boven LIMIT


@pytest.mark.parametrize(
    "case",
    [
        "andere key met zijn sessietoken",
        "andere key met zijn config-token",
        "eigen docid met het token van een andere key",
        "zonder token",
        "met een ack-id",
        "met een namespace",
        "binair event",
        "type met een escape",
        "type twee keer",
        "bericht als string",
        "nieuwe CONNECT na 41",
        "groot met opvulling",
        "groot met een escape",
        "groot zonder event-kop",
        "geen json",
    ],
)
def test_een_tweede_auth_over_een_andere_key_gaat_dicht(settings, case):
    """Gemeten met de echte DS (spike, open punt 2): een tweede auth-bericht op een levende
    verbinding verhuist die verbinding naar de key van dat bericht. Dus: elk nieuw auth-bericht
    of CONNECT moet over de key van het pad gaan, met kloppende tokens, in welke vorm ook."""
    auth = other_auth(settings)
    text = {
        "andere key met zijn sessietoken": lambda: message(auth),
        "andere key met zijn config-token": lambda: opening(config(settings, OTHER))[1],
        "eigen docid met het token van een andere key": lambda: message(
            other_auth(settings, docid=KEY)
        ),
        "zonder token": lambda: message(
            {k: v for k, v in other_auth(settings, docid=KEY).items() if k != "jwtSession"}
        ),
        "met een ack-id": lambda: message(auth, "4217"),
        "met een namespace": lambda: message(auth, "42/,"),
        "binair event": lambda: message(auth, "451-"),
        "type met een escape": lambda: message(auth).replace('"auth"', '"\\u0061uth"', 1),
        "type twee keer": lambda: (
            '42["message",' + json.dumps({**auth, "type": "cursor"})[:-1] + ', "type": "auth"}]'
        ),
        "bericht als string": lambda: '42["message",' + json.dumps(json.dumps(auth)) + "]",
        "nieuwe CONNECT na 41": lambda: (
            "40" + json.dumps({"data": auth, "session": auth["jwtSession"]})
        ),
        "groot met opvulling": lambda: message({**auth, "pad": "x" * (2 * stream.LIMIT)}),
        "groot met een escape": lambda: message({"pad": "x" * (2 * stream.LIMIT), **auth}).replace(
            '"auth"', '"\\u0061uth"', 1
        ),
        "groot zonder event-kop": lambda: "40" + "x" * (2 * stream.LIMIT),
        "geen json": lambda: '42["message",{"type":"auth"',
    }[case]()
    guard = opened_guard(settings)
    if case == "nieuwe CONNECT na 41":
        assert guard.feed(frame("41")) == "ok"
    data = frame(text)
    # Wat vóór de laatste byte zit, mag door; het stuk met de laatste byte niet (DS krijgt het
    # bericht nooit compleet). Groot: in stukken, de wacht zegt het al eerder
    verdicts = run(guard, *chunks(data[:-1], 50_001), data[-1:])
    assert verdicts[-1] == "deny", (case, verdicts)
    assert guard.feed(frame("2")) == "deny"  # blijft dicht


@pytest.mark.parametrize(("word", "first"), [('"auth"', 5), ("\\u0061", 1)])
def test_een_groot_bericht_met_auth_op_de_grens_van_twee_stukken(settings, word, first):
    """Een groot bericht wordt in stukken doorzocht; wat over de grens van twee stukken valt,
    telt ook (het eind van het vorige stuk gaat mee). first: zoveel bytes van het woord in het
    eerste stuk ("auth" met aanhalingstekens tot op de laatste byte, \\u tussen \\ en u)."""
    text = '42["message",{"pad":"' + "x" * (2 * stream.LIMIT) + '","type":' + word + "}]"
    data = frame(text)
    at = len(data) - len(text) + text.index(word) + first
    guard = opened_guard(settings)
    assert run(guard, data[:at], data[at:-1], data[-1:]) == ["ok", "deny", "deny"]
