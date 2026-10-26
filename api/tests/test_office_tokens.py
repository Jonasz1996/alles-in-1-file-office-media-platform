"""oo/tokens.py (§3.3, §3.9, §4.2): elke vorm heen en terug, en elke weigering. Een
office-token werkt niet als app-token en omgekeerd; document_key leest een config- en een
sessietoken (dat DS 10.0 met een afgeleide sleutel tekent, gemeten in de spike)."""

import base64
import json
import secrets
import time
import uuid
import warnings

import jwt
import pytest
from fastapi import HTTPException

from app import security
from app.config import Settings
from app.office.engine import TokenError
from app.office.oo import tokens
from tests.fake_docserver import protocol


@pytest.fixture
def office(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        jwt_secret="app-geheim-" + secrets.token_hex(16),
        office_url="http://office",
        office_app_url="http://platform:8081",
        office_secret=secrets.token_urlsafe(32),
    )


def raw(claims: dict, key: str, alg: str = "HS256") -> str:
    with warnings.catch_warnings():  # HS512 met een sleutel van 43 bytes: alleen een waarschuwing
        warnings.simplefilter("ignore", jwt.warnings.InsecureKeyLengthWarning)
        return jwt.encode(claims, key, algorithm=alg)


def future(seconds: int = 300) -> int:
    return int(time.time()) + seconds


def unsigned(claims: dict) -> str:
    """Een token met alg none (zonder handtekening)."""

    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."


CONFIG = {
    "document": {"fileType": "docx", "key": "k-1", "url": "http://platform:8081/x"},
    "editorConfig": {"callbackUrl": "http://platform:8081/cb", "user": {"id": "u1"}},
    "events": {"onAppReady": "functie"},
}


def test_sign_en_decode(office):
    token = tokens.sign(office, {"c": "version"}, minutes=5)
    claims = tokens.decode(office, token)
    assert claims["c"] == "version"
    assert claims["exp"] - claims["iat"] == 300
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_browser_token_is_de_hele_config_zonder_events(office):
    token = tokens.browser_token(office, {**CONFIG, "token": "oud"})
    claims = tokens.decode(office, token)
    assert "events" not in claims and "token" not in claims
    assert claims["document"] == CONFIG["document"]
    assert claims["exp"] - claims["iat"] == 120 * 60


def test_command_body(office):
    body = tokens.command_body(office, {"c": "info", "key": "k-1"})
    assert body["c"] == "info" and body["key"] == "k-1"
    claims = tokens.decode(office, body["token"])
    assert claims["c"] == "info" and claims["key"] == "k-1"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "geen.jwt",
        "a.b.c",
    ],
)
def test_decode_weigert_rommel(office, token):
    with pytest.raises(TokenError):
        tokens.decode(office, token)


def test_decode_weigert_elke_vreemde_vorm(office):
    secret = office.office_secret
    cases = {
        "alg none": unsigned({"c": "version", "exp": future()}),
        "HS512": raw({"c": "version", "exp": future()}, secret, "HS512"),
        "ander geheim": raw({"c": "version", "exp": future()}, "ander" * 8),
        "geen exp": raw({"c": "version"}, secret),
        "verlopen": raw({"c": "version", "exp": int(time.time()) - 120}, secret),
        "app-token met office-geheim": raw({"typ": "access", "exp": future()}, secret),
        "app-geheim": raw({"c": "version", "exp": future()}, office.secret()),
    }
    for name, token in cases.items():
        with pytest.raises(TokenError):
            tokens.decode(office, token)
        assert tokens.document_key(office, token) is None, name


def test_speling_van_een_minuut(office):
    secret = office.office_secret
    tokens.decode(office, raw({"c": "x", "exp": int(time.time()) - 30}, secret))
    with pytest.raises(TokenError):
        tokens.decode(office, raw({"c": "x", "exp": int(time.time()) - 90}, secret))


def test_office_token_werkt_niet_als_app_token_en_omgekeerd(office):
    user_id, family = uuid.uuid4(), uuid.uuid4()

    class User:
        id = user_id
        role = "user"

    app_token = security.access_token(office, User, family)
    with pytest.raises(TokenError):
        tokens.decode(office, app_token)  # ander geheim
    # Ook als iemand het office-geheim zou gebruiken met de vorm van een app-token: typ
    forged = raw({"sub": str(user_id), "typ": "access", "exp": future()}, office.office_secret)
    with pytest.raises(TokenError):
        tokens.decode(office, forged)
    # En een office-token geeft geen toegang tot de app
    office_token = tokens.browser_token(office, CONFIG)
    with pytest.raises(HTTPException) as caught:
        security._decode(office, office_token, "access")
    assert caught.value.status_code == 401


def test_callback_uit_de_body_en_uit_de_header(office):
    fields = protocol.editing("k-1", ["u1"], (protocol.CONNECT, "u1"))
    body, authorization = protocol.callback_request(office.office_secret, fields)
    # Body-token eerst: de platte velden, zonder iat, exp en operation
    assert tokens.callback_data(office, body, authorization) == fields
    assert tokens.callback_data(office, body, None) == fields
    # Alleen de header: de velden onder payload
    bare = {k: v for k, v in body.items() if k != "token"}
    assert tokens.callback_data(office, bare, authorization) == fields
    # Losse velden in de body tellen nooit: een andere status zonder token
    forged = {**bare, "status": 2, "url": "http://evil/x"}
    assert tokens.callback_data(office, forged, authorization) == fields


def test_callback_met_history_alleen_in_de_body(office):
    fields = protocol.saved(
        "k-1",
        protocol.SAVE,
        url="http://h/office/cache/files/data/k-1_5/output.docx/output.docx?md5=x&expires=1",
        changesurl="http://h/c",
        changes=[{"created": "2026-10-26 10:00:00", "user": {"id": "u1", "name": "A"}}],
        users=["u1"],
        lastsave_at=time.time(),
        filetype="docx",
    )
    body, authorization = protocol.callback_request(office.office_secret, fields)
    assert tokens.callback_data(office, body, authorization)["history"] == fields["history"]
    bare = {"key": "k-1"}
    assert "history" not in tokens.callback_data(office, bare, authorization)


def test_callback_weigeringen(office):
    secret = office.office_secret
    fields = {"key": "k-1", "status": 2}
    weigeren = [
        ({}, None),  # niets
        ({"key": "k-1", "status": 2}, None),  # alleen losse velden
        ({"token": raw({**fields, "exp": future()}, secret)}, None),  # geen operation
        ({"token": raw({**fields, "operation": "download", "exp": future()}, secret)}, None),
        ({"token": raw({**fields, "operation": "callback", "exp": future()}, "x" * 32)}, None),
        ({"token": unsigned({**fields, "operation": "callback", "exp": future()})}, None),
        ({}, "Bearer " + raw({"payload": fields, "exp": future()}, secret)),
        ({}, "Bearer " + raw({"payload": "nee", "operation": "callback", "exp": future()}, secret)),
        ({}, "Basic " + raw({"payload": fields, "operation": "callback", "exp": future()}, secret)),
        ({}, "Bearer "),
        # Een uitgaand token van DS (download) dat als callback terugkomt
        ({}, "Bearer " + protocol.download_token(secret, "http://platform:8081/x")),
        # Een config-token als callback
        ({"token": tokens.browser_token(office, CONFIG)}, None),
    ]
    for body, authorization in weigeren:
        with pytest.raises(TokenError):
            tokens.callback_data(office, body, authorization)
    # Een body-token dat niet klopt valt niet terug op de header
    header = "Bearer " + raw({"payload": fields, "operation": "callback", "exp": future()}, secret)
    with pytest.raises(TokenError):
        tokens.callback_data(office, {"token": "kapot"}, header)
    with pytest.raises(TokenError):
        tokens.callback_data(office, ["geen", "object"], header)


def test_download_token(office):
    url = "http://platform:8081/internal/office/doc/k-1"
    token = protocol.download_token(office.office_secret, url)
    tokens.check_download(office, f"Bearer {token}", url)
    for authorization, target in (
        (None, url),
        (f"Bearer {token}", url + "x"),  # precies dit adres
        (f"Bearer {token}", url.upper()),
        ("Bearer " + raw({"payload": {"url": url}, "exp": future()}, office.office_secret), url),
        (
            "Bearer "
            + raw(
                {"payload": {"url": url}, "operation": "callback", "exp": future()},
                office.office_secret,
            ),
            url,
        ),
        ("Bearer " + protocol.download_token("ander" * 8, url), url),
        (
            "Bearer "
            + raw({"payload": url, "operation": "download", "exp": future()}, office.office_secret),
            url,
        ),  # fmt: skip
    ):
        with pytest.raises(TokenError):
            tokens.check_download(office, authorization, target)


def test_document_key_uit_een_config_en_een_sessietoken(office):
    assert tokens.document_key(office, tokens.browser_token(office, CONFIG)) == "k-1"
    # Het sessietoken van DS 10.0: afgeleide sleutel, 30 dagen [gemeten]
    claims = protocol.session_claims("k-2", {"edit": True}, {"id": "u1", "name": "A"}, 1, "edit")
    session = protocol.sign(
        protocol.session_secret(office.office_secret), claims, protocol.SESSION_SECONDS
    )
    assert tokens.session_secret(office) == protocol.session_secret(office.office_secret)
    assert tokens.document_key(office, session) == "k-2"
    # Met het office-geheim zelf getekend werkt ook (zoals de documentatie zegt)
    same = protocol.sign(office.office_secret, claims, 300)
    assert tokens.document_key(office, same) == "k-2"
    # Callback- en downloadtokens horen hier niet; zonder document ook niet
    fields = protocol.editing("k-1", ["u1"])
    body, authorization = protocol.callback_request(office.office_secret, fields)
    assert tokens.document_key(office, body["token"]) is None
    assert tokens.document_key(office, protocol.download_token(office.office_secret, "x")) is None
    assert tokens.document_key(office, tokens.sign(office, {"document": "k-1"})) is None
    assert tokens.document_key(office, tokens.sign(office, {"document": {"key": ""}})) is None
    other = Settings(**{**office.model_dump(), "office_secret": secrets.token_urlsafe(32)})
    assert tokens.document_key(other, session) is None


def test_ds_user_id(office):
    a, b = uuid.uuid4(), uuid.uuid4()
    first = tokens.ds_user_id(office, a)
    assert first == tokens.ds_user_id(office, a)  # vast per gebruiker
    assert first != tokens.ds_user_id(office, b)
    assert len(first) == 24 and int(first, 16) >= 0
    assert str(a).replace("-", "")[:24] != first  # niet de echte id
    other = Settings(**{**office.model_dump(), "office_secret": secrets.token_urlsafe(32)})
    assert tokens.ds_user_id(other, a) != first
