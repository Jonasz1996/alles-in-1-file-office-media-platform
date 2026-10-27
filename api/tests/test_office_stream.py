"""De StreamGuard (oo/stream.py, spike stap 12, A1): het CONNECT-pakket en het eerste
auth-bericht van de browser, als gemaskeerde WebSocket-frames, eventueel in stukken.

Een token van key A op het pad van A gaat door; een token van key B op het pad van A, of een
token met een ander geheim, gaat dicht; het sessietoken van DS (de afgeleide sleutel) gaat door;
te groot of te traag dicht; frames in stukken en in fragmenten gaan door."""

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
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data))


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
    # Daarna leest hij niets meer: wat er ook komt, het gaat door
    assert guard.feed(b"\x00\x01kapot") == "ok"


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
