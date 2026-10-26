"""De nep-DS eerlijk houden (§3.6, de kern). De veldnamen en de vorm van de tokens van zijn
callbacks, downloads, commando's en eerste socketpakketten moeten gelijk zijn aan die in
tests/fake_docserver/recorded/ (opgenomen met DS 10.0.0.1). Hij draait hier in pytest (de
socket via een echte uvicorn in dit proces) en als proces.

Eén bewuste afwijking: de echte DS 10.0.0.1 zet history ook in het header-token van een
callback; de nep laat hem daar weg (§3.6), zodat de app history alleen uit de body haalt.
"""

import asyncio
import contextlib
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
import jwt
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from websockets.asyncio.client import connect

from tests.fake_docserver import FakeConfig, create_app, docx, protocol
from tests.fake_docserver.client import EditorClient, Refused

API = Path(__file__).resolve().parents[1]
RECORDED = Path(__file__).parent / "fake_docserver" / "recorded"
APP = "http://app.intern:8081"


def recorded(name: str) -> dict:
    return json.loads((RECORDED / f"{name}.json").read_text())


def keys_of(value) -> object:
    """De vorm van een JSON-waarde: de sleutels van elk object, recursief (lijsten: het
    eerste element)."""
    if isinstance(value, dict):
        return {k: keys_of(v) for k, v in value.items()}
    if isinstance(value, list):
        return [keys_of(value[0])] if value else []
    return type(value).__name__


def secret() -> str:
    return secrets.token_urlsafe(32)


class AppSide:
    """De kant van de app: de interne routes die DS aanroept (document ophalen en callback),
    met een instelbaar antwoord."""

    def __init__(self) -> None:
        self.callbacks: list[dict] = []
        self.downloads: list[dict] = []
        self.files: dict[str, bytes] = {}
        self.digest: dict[str, str] = {}
        self.answers: list[tuple[int, dict]] = []  # de volgende antwoorden op callbacks
        self.app = FastAPI()
        self.app.add_api_route("/internal/office/doc/{name}", self.doc, methods=["GET"])
        self.app.add_api_route("/internal/office/callback", self.callback, methods=["POST"])

    async def doc(self, name: str, request: Request) -> Response:
        self.downloads.append({"name": name, "headers": dict(request.headers)})
        if name not in self.files:
            return Response(status_code=404)
        headers = {}
        if name in self.digest:
            headers["Repr-Digest"] = self.digest[name]
        return Response(self.files[name], headers=headers)

    async def callback(self, request: Request) -> Response:
        body = json.loads(await request.body())
        self.callbacks.append({"body": body, "headers": dict(request.headers)})
        if self.answers:
            status, answer = self.answers.pop(0)
            return JSONResponse(answer, status_code=status)
        return JSONResponse({"error": 0})

    def bodies(self, status: int | None = None) -> list[dict]:
        return [
            c["body"] for c in self.callbacks if status is None or c["body"]["status"] == status
        ]

    async def wait(self, status: int, count: int = 1, seconds: float = 5.0) -> list[dict]:
        deadline = time.monotonic() + seconds
        while len(self.bodies(status)) < count:
            assert time.monotonic() < deadline, f"geen status {status}: {self.bodies()}"
            await asyncio.sleep(0.02)
        return self.bodies(status)


class World:
    def __init__(self, side: AppSide, config: FakeConfig, port: int) -> None:
        self.side = side
        self.config = config
        self.port = port
        self.http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False)

    @property
    def fake(self):
        return self.app.state.fake

    def doc_config(self, key: str, name: str, user: str, uname: str, **extra) -> dict:
        """Een config zoals O2 hem maakt, getekend met het office-geheim."""
        config = {
            "documentType": "word",
            "type": "desktop",
            "width": "100%",
            "height": "100%",
            "document": {
                "fileType": name.rsplit(".", 1)[1],
                "key": key,
                "title": name,
                "url": f"{APP}/internal/office/doc/{name}",
                "permissions": dict.fromkeys(
                    ("edit", "download", "print", "comment", "review", "copy", "fillForms"), True
                ),
            },
            "editorConfig": {
                "mode": "edit",
                "lang": "nl",
                "callbackUrl": f"{APP}/internal/office/callback",
                "user": {"id": user, "name": uname},
            },
        }
        config.update(extra)
        token = protocol.sign(self.config.secret, config, 7200)
        return {**config, "token": token}

    def editor(self, config: dict, **headers: str) -> EditorClient:
        return EditorClient(f"ws://127.0.0.1:{self.port}", config, headers=headers, http=self.http)

    def direct(self, url: str) -> str:
        """Een link van DS (op de host van de browser) naar de nep zelf, zoals urls.rewrite."""
        parsed = httpx.URL(url)
        path = parsed.path.removeprefix("/office")
        return f"http://127.0.0.1:{self.port}{path}?{parsed.query.decode()}"

    async def command(self, c: str, **params) -> dict:
        body = {"c": c, **params}
        body["token"] = protocol.sign(self.config.secret, body, 300)
        r = await self.http.post("/command", json=body)
        return r.json()


@contextlib.asynccontextmanager
async def served(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    config = uvicorn.Config(app, ws="websockets-sansio", log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.ensure_future(server.serve(sockets=[sock]))
    deadline = time.monotonic() + 5
    while not server.started:
        assert time.monotonic() < deadline and not task.done()
        await asyncio.sleep(0.01)
    try:
        yield sock.getsockname()[1]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


@pytest.fixture
async def world():
    side = AppSide()
    side.files["proef.docx"] = docx.make_docx(["Eerste alinea"])
    config = FakeConfig(
        secret=secret(),
        save_delay=0.2,
        time_scale=0.01,
        transport=httpx.ASGITransport(app=side.app),
    )
    app = create_app(config)
    async with served(app) as port:
        w = World(side, config, port)
        w.app = app
        try:
            yield w
        finally:
            await w.http.aclose()


def asgi_client(config: FakeConfig) -> tuple[httpx.AsyncClient, FastAPI]:
    app = create_app(config)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ds")
    return client, app


# --- protocol (puur) ---


def test_secure_link_volgens_nginx():
    """md5 = base64url(md5(expires + pad + geheim)) zonder "=", zoals secure_link_md5."""
    import base64
    import hashlib

    path = "/cache/files/data/k_1/output.docx/output.docx"
    raw = hashlib.md5(f"1800000000{path}geheim".encode()).digest()
    want = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    assert protocol.link_md5("geheim", path, 1800000000) == want
    assert protocol.link_ok("geheim", path, want, "1800000000", now=1700000000)
    assert not protocol.link_ok("geheim", path, want, "1800000000", now=1900000000)  # verlopen
    assert not protocol.link_ok("ander", path, want, "1800000000", now=1700000000)
    assert not protocol.link_ok("geheim", path + "x", want, "1800000000", now=1700000000)
    assert not protocol.link_ok("geheim", path, want, "morgen", now=1700000000)


def test_frames_heen_en_terug():
    assert protocol.parse(protocol.eio_open("abc"))[1]["sid"] == "abc"
    assert protocol.parse("2") == ("ping", "")
    assert protocol.parse("3") == ("pong", "")
    assert protocol.parse('40{"token":"t"}') == ("connect", {"token": "t"})
    assert protocol.parse("40") == ("connect", {})
    assert protocol.parse("41") == ("disconnect", None)
    assert protocol.parse(protocol.sio_error("nee", -20)) == (
        "connect_error",
        {"message": "nee", "data": {"code": -20}},
    )
    assert protocol.parse(protocol.message({"type": "x"})) == ("message", {"type": "x"})
    assert protocol.parse('42["anders",1]') == ("event", ["anders", 1])
    assert protocol.parse("42{kapot")[0] == "event"
    assert protocol.parse("") == ("unknown", None)
    assert protocol.parse("9") == ("unknown", "9")


def test_versiesegment_heeft_de_vorm_van_de_echte():
    import re

    assert re.fullmatch(r"10\.0\.0-[0-9a-f]{32}", protocol.V)


def test_docx_heen_en_terug():
    data = docx.make_docx(["Een", "Twee & <drie>"])
    assert docx.paragraphs(data) == ["Een", "Twee & <drie>"]
    assert docx.paragraphs(b"geen zip") == []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
    xlsx = docx.with_text(data, "tekst")
    assert docx.fake_text(xlsx) == "tekst"
    assert docx.fake_text(docx.with_text(xlsx, "nieuw")) == "nieuw"  # vervangen, niet dubbel
    assert docx.fake_text(data) is None


def test_repr_digest():
    import base64
    import hashlib

    good = "sha-256=:" + base64.b64encode(hashlib.sha256(b"abc").digest()).decode() + ":"
    assert protocol.repr_digest_ok(good, b"abc")
    assert not protocol.repr_digest_ok(good, b"abd")
    assert protocol.repr_digest_ok(None, b"abc")


def test_callbackvelden_hebben_de_vorm_van_de_opname():
    rec = recorded("callbacks")
    body, authorization = protocol.callback_request(
        "s" * 32,
        protocol.editing("k", ["u1"], (protocol.CONNECT, "u1")),
    )
    assert set(body) == set(rec["status_1_open"]["body_keys"])
    claims = jwt.decode(body["token"], "s" * 32, algorithms=["HS256"])
    want = rec["status_1_open"]["body_token"]["claims"]
    assert set(claims) - {"iat", "exp"} == set(want) - {"exp_minus_iat"}
    assert claims["exp"] - claims["iat"] == want["exp_minus_iat"] == 300
    header = jwt.decode(authorization.split(" ", 1)[1], "s" * 32, algorithms=["HS256"])
    assert set(header) - {"iat", "exp"} == {"payload", "operation"}
    assert header["operation"] == "callback"


# --- HTTP via ASGI ---


@pytest.mark.anyio
async def test_healthcheck_en_api_js():
    client, app = asgi_client(FakeConfig(secret=secret()))
    async with client:
        r = await client.get("/healthcheck")
        assert (r.status_code, r.text) == (200, "true")
        r = await client.get(
            "/web-apps/apps/api/documents/api.js", headers={"X-Forwarded-Prefix": "/office"}
        )
        assert r.status_code == 200
        assert 'var PREFIX = "/office";' in r.text
        assert f'var V = "{protocol.V}";' in r.text
        assert "var JWT = true;" in r.text
        r = await client.get("/web-apps/apps/api/documents/api.js")
        assert 'var PREFIX = "";' in r.text  # zonder de kop klopt het adres niet: onError -4
        r = await client.get(
            "/web-apps/apps/api/documents/api.js",
            headers={"X-Forwarded-Prefix": '/office";alert(1)//'},
        )
        assert 'var PREFIX = "";' in r.text
        r = await client.get(f"/{protocol.V}/web-apps/apps/api/documents/api.js")
        assert r.status_code == 200
        r = await client.get("/10.0.0-0000/web-apps/apps/api/documents/api.js")
        assert r.status_code == 404
        await client.post("/__fake/health", json={"starting": True})
        assert (await client.get("/healthcheck")).text == "false"
        await client.post("/__fake/health", json={"ok": True})
        assert (await client.get("/healthcheck")).text == "true"


@pytest.mark.anyio
async def test_editorpagina_zonder_inline_script():
    from html.parser import HTMLParser

    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.scripts: list[dict] = []
            self.inline = False
            self.labels: list[str] = []
            self._in_script = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "script":
                self.scripts.append(attrs)
                self._in_script = True
            if tag == "textarea":
                self.labels.append(attrs.get("aria-label", ""))
            if any(k.startswith("on") for k in attrs):
                self.inline = True

        def handle_endtag(self, tag):
            if tag == "script":
                self._in_script = False

        def handle_data(self, data):
            if self._in_script and data.strip():
                self.inline = True

    client, _ = asgi_client(FakeConfig(secret=secret()))
    async with client:
        for editor in ("documenteditor", "spreadsheeteditor", "presentationeditor"):
            r = await client.get(f"/{protocol.V}/web-apps/apps/{editor}/main/index.html")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/html")
            parser = Scripts()
            parser.feed(r.text)
            assert parser.scripts == [{"src": "editor.js"}]
            assert not parser.inline
            assert parser.labels == ["Nep-editor"]
            js = await client.get(f"/{protocol.V}/web-apps/apps/{editor}/main/editor.js")
            assert js.status_code == 200
            assert "javascript" in js.headers["content-type"]
        r = await client.get(f"/{protocol.V}/web-apps/apps/onbekend/main/index.html")
        assert r.status_code == 404
        r = await client.get("/10.0.0-0000/web-apps/apps/documenteditor/main/index.html")
        assert r.status_code == 404


@pytest.mark.anyio
async def test_commandos_zoals_gemeten():
    rec = recorded("commands")
    config = FakeConfig(secret=secret(), connections=20)
    client, _ = asgi_client(config)

    def signed(body: dict, key: str | None = None) -> dict:
        return {**body, "token": protocol.sign(key or config.secret, body, 300)}

    async with client:
        # Zonder token: kale tekst {"error":6}, ook niet als JSON-type [gemeten]
        r = await client.post("/command", json={"c": "version"})
        assert r.text == rec["version zonder token"]["answer"] == '{"error":6}'
        assert "json" not in r.headers["content-type"]
        r = await client.post("/command", json=signed({"c": "version"}))
        assert r.json() == rec["version token alleen in body"]["answer"]
        token = protocol.sign(config.secret, {"payload": {"c": "version"}}, 300)
        r = await client.post(
            "/coauthoring/CommandService.ashx",
            json={"c": "version"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.json() == rec["version token alleen in header"]["answer"]
        # Een ander geheim, een config-token, een verlopen token: error 6
        r = await client.post("/command", json=signed({"c": "version"}, "ander" * 8))
        assert r.text == '{"error":6}'
        config_token = protocol.sign(config.secret, {"document": {"key": "k"}}, 300)
        r = await client.post("/command", json={"c": "version", "token": config_token})
        assert r.text == '{"error":6}'
        old = jwt.encode({"c": "version", "exp": 1}, config.secret, algorithm="HS256")
        r = await client.post("/command", json={"c": "version", "token": old})
        assert r.text == '{"error":6}'
        # De parameters komen uit het token, niet uit de body
        body = {"c": "version", "token": protocol.sign(config.secret, {"c": "onzin"}, 300)}
        assert (await client.post("/command", json=body)).json() == {"error": 5}
        for c in ("info", "forcesave", "drop"):
            r = await client.post("/command", json=signed({"c": c, "key": "bestaat-niet"}))
            assert r.json() == rec[c]["answer"], c
        r = await client.post("/command", json=signed({"c": "license"}))
        answer = r.json()
        assert keys_of(answer) == keys_of(rec["license"]["answer"])
        assert answer["license"]["connections"] == 20
        await client.post("/__fake/license", json={"connections": 5})
        r = await client.post("/command", json=signed({"c": "license"}))
        assert r.json()["license"]["connections"] == 5
        # jwt-off: alles zonder token (voor de tokencontrole van Keuze 28)
        await client.post("/__fake/jwt-off", json={"off": True})
        r = await client.post("/command", json={"c": "version"})
        assert r.json() == rec["version"]["answer"]
        await client.post("/__fake/jwt-off", json={"off": False})
        r = await client.post("/command", json={"c": "version"})
        assert r.text == '{"error":6}'


@pytest.mark.anyio
async def test_conversie_zonder_token_geeft_min_8():
    config = FakeConfig(secret=secret())
    client, _ = asgi_client(config)
    async with client:
        params = {"async": False, "filetype": "docx", "outputtype": "pdf", "key": "k", "url": "x"}
        r = await client.post("/converter", json=params, headers={"Accept": "application/json"})
        assert r.json() == {"error": -8}
        r = await client.post("/ConvertService.ashx", json=params)
        assert "<Error>-8</Error>" in r.text  # zonder Accept: application/json geen JSON
        body = {**params, "token": protocol.sign(config.secret, params, 300)}
        r = await client.post("/converter", json=body, headers={"Accept": "application/json"})
        assert r.json()["error"] != -8
        await client.post("/__fake/jwt-off", json={})
        r = await client.post("/converter", json=params, headers={"Accept": "application/json"})
        assert r.json()["error"] != -8


@pytest.mark.anyio
async def test_kill_en_restart_zonder_socket():
    client, app = asgi_client(FakeConfig(secret=secret()))
    fake = app.state.fake
    async with client:
        old = fake.link_secret
        await client.post("/__fake/kill")
        assert (await client.get("/healthcheck")).status_code == 503
        assert (await client.post("/command", json={})).status_code == 503
        await client.post("/__fake/restart")
        assert (await client.get("/healthcheck")).text == "true"
        assert fake.link_secret != old  # oude links geven 403
    client, app = asgi_client(FakeConfig(secret=secret(), link_secret="vast"))
    async with client:
        await client.post("/__fake/restart")
        assert app.state.fake.link_secret == "vast"


# --- de socket, callbacks en downloads (een echte server in dit proces) ---


@pytest.mark.anyio
async def test_eerste_pakketten_zoals_gemeten(world):
    rec = recorded("socket")["tunnels"][0]["frames"]
    config = world.doc_config("k1-abc", "proef.docx", "u1", "Anna")
    anna = world.editor(
        config,
        **{"X-Forwarded-Host": "platform.test", "X-Forwarded-Proto": "https"},
        **{"X-Forwarded-Prefix": "/office"},
    )
    reply = await anna.open(load=False)
    s2c = [f for d, f in anna.frames if d == "s2c"]
    c2s = [f for d, f in anna.frames if d == "c2s"]
    # 1. open van engine.io
    assert set(protocol.parse(s2c[0])[1]) == set(rec[0]["body"])
    # 2. CONNECT van de browser: token bovenaan, de data met jwtOpen
    kind, packet = protocol.parse(c2s[0])
    assert kind == "connect"
    assert set(packet) == set(rec[1]["body"])
    assert set(packet["data"]) == set(rec[1]["body"]["data"])
    # 3. 40 met sid, 4. license
    assert set(protocol.parse(s2c[1])[1]) == set(rec[2]["body"])
    license = protocol.parse(s2c[2])[1]
    assert keys_of(license) == keys_of(rec[3]["body"][1])
    # 5. auth van de browser, 6. auth van DS met het sessietoken
    assert set(protocol.parse(c2s[1])[1]) == set(rec[4]["body"][1])
    want = rec[5]["body"][1]
    assert set(reply) == set(want)
    assert set(reply["participants"][0]) == set(want["participants"][0])
    assert reply["participants"][0]["id"] == "u1" + str(reply["indexUser"])
    session_rec = rec[5]["tokens"][0]
    assert session_rec["controle"]["hmac(office-geheim,'editor-session').hex"] == "klopt"
    claims = jwt.decode(
        reply["jwt"], protocol.session_secret(world.config.secret), algorithms=["HS256"]
    )
    assert keys_of({k: v for k, v in claims.items() if k not in ("iat", "exp")}) == keys_of(
        {k: v for k, v in session_rec["claims"].items() if k != "exp_minus_iat"}
    )
    assert claims["exp"] - claims["iat"] == session_rec["claims"]["exp_minus_iat"]
    assert claims["document"]["key"] == "k1-abc"
    with pytest.raises(jwt.InvalidSignatureError):  # niet met het office-geheim zelf
        jwt.decode(reply["jwt"], world.config.secret, algorithms=["HS256"])
    # documentOpen met Editor.bin op de host en het prefix van de browser
    opened = await anna.wait_for("documentOpen")
    link = opened["data"]["data"]["Editor.bin"]
    assert link.startswith("https://platform.test/office/cache/files/data/k1-abc/Editor.bin/")
    r = await world.http.get(world.direct(link))
    assert r.json()["paragraphs"] == ["Eerste alinea"]
    await anna.close()


@pytest.mark.anyio
async def test_herverbinden_met_het_sessietoken(world):
    rec = recorded("socket")["tunnels"][1]["frames"]
    anna = world.editor(world.doc_config("k2-abc", "proef.docx", "u1", "Anna"))
    await anna.open()
    first = len(anna.frames)
    before = len(anna.messages)
    reply = await anna.reconnect()
    frames = anna.frames[first:]
    c2s = [f for d, f in frames if d == "c2s"]
    kind, packet = protocol.parse(c2s[0])
    assert set(packet) == set(rec[1]["body"]) == {"data", "session"}
    assert set(protocol.parse(c2s[1])[1]) == set(rec[4]["body"][1])  # jwtSession, geen openCmd
    assert "jwt" not in reply
    assert set(reply) == set(rec[5]["body"][1])
    await asyncio.sleep(0.1)
    assert not [m for m in anna.messages[before:] if m.get("type") == "documentOpen"]
    await anna.close()


@pytest.mark.anyio
async def test_samen_bewerken_opslaan_en_sluiten(world):
    rec = recorded("callbacks")
    side = world.side
    anna = world.editor(world.doc_config("k3-abc", "proef.docx", "u1", "Anna"))
    bram = world.editor(world.doc_config("k3-abc", "proef.docx", "u2", "Bram"))
    await anna.open()
    # Eerst status 1, dan het document ophalen [gemeten]
    open_cb = (await side.wait(1))[0]
    assert set(open_cb) == set(rec["status_1_open"]["body_keys"])
    assert open_cb["actions"] == [{"type": 1, "userid": "u1"}]
    headers = side.callbacks[0]["headers"]
    assert headers["user-agent"] == rec["status_1_open"]["user_agent"]
    header = jwt.decode(
        headers["authorization"].split(" ", 1)[1], world.config.secret, algorithms=["HS256"]
    )
    want = rec["status_1_open"]["header_token"]["claims"]
    assert set(header) - {"iat", "exp"} == set(want) - {"exp_minus_iat"}
    assert set(header["payload"]) == set(want["payload"])
    assert header["exp"] - header["iat"] == want["exp_minus_iat"]
    # Het ophalen: de koppen en het token zoals gemeten
    download = recorded("download")
    got = side.downloads[0]["headers"]
    for name, value in download["headers"].items():
        if name != "Connection":
            assert got[name.lower()] == value, name
    claims = jwt.decode(
        got["authorization"].split(" ", 1)[1], world.config.secret, algorithms=["HS256"]
    )
    assert claims["payload"] == {"url": f"{APP}/internal/office/doc/proef.docx"}
    assert claims["operation"] == "download"
    assert claims["exp"] - claims["iat"] == download["token"]["claims"]["exp_minus_iat"]

    await bram.open()
    joined = (await side.wait(1, 2))[1]
    assert joined["users"] == ["u1", "u2"] and joined["actions"] == [{"type": 1, "userid": "u2"}]
    assert len(side.downloads) == 1  # de tweede haalt niet opnieuw op

    await anna.type("Anna was hier")
    await bram.wait_for("saveChanges")
    assert bram.texts_of_others() == {"u1": "Anna was hier"}

    # De knop Opslaan: status 6 met forcesavetype 1
    answer = await anna.save()
    assert answer["messages"]["success"] is True
    six = (await side.wait(6))[0]
    want = rec["status_6_button"]
    assert set(six) == set(want["body_keys"])
    assert keys_of(six["history"]) == keys_of(want["body"]["history"])
    assert six["forcesavetype"] == 1 and six["actions"] == [{"type": 2, "userid": "u1"}]
    assert six["users"] == ["u1"]
    header = jwt.decode(
        side.callbacks[-1]["headers"]["authorization"].split(" ", 1)[1],
        world.config.secret,
        algorithms=["HS256"],
    )
    # zonder history in de header (de enige afwijking van de opname, §3.6)
    assert set(header["payload"]) == set(want["header_token"]["claims"]["payload"]) - {"history"}
    r = await world.http.get(world.direct(six["url"]))
    assert docx.paragraphs(r.content) == ["Eerste alinea", "Anna was hier"]
    r = await world.http.get(world.direct(six["changesurl"]))
    assert r.status_code == 200
    await anna.wait_for("documentSaved")
    await bram.wait_for("documentSaved")

    # forcesave via het commando: error 4 zonder wijzigingen, anders status 6 type 0
    assert (await world.command("forcesave", key="k3-abc"))["error"] == 4
    await bram.type("Bram ook")
    answer = await world.command("forcesave", key="k3-abc", userdata="merk-1")
    assert answer == {"key": "k3-abc", "error": 0}
    cmd = (await side.wait(6, 2))[1]
    assert set(cmd) == set(rec["status_6_command"]["body_keys"])
    assert cmd["forcesavetype"] == 0 and cmd["userdata"] == "merk-1"
    assert cmd["users"] == ["u2"]
    assert len(cmd["history"]["changes"]) == 2

    # info: de gebruikers, en een status 1 zonder actions
    info = await world.command("info", key="k3-abc")
    assert info == {"key": "k3-abc", "error": 0, "users": ["u1", "u2"]}
    await asyncio.sleep(0.1)
    assert set(side.bodies(1)[-1]) == set(rec["status_1_info"]["body_keys"])

    # Bram weg: status 1 met actions type 0; Anna weg: status 2 na save_delay
    await bram.close()
    closing = (await side.wait(1, 4))[3]
    assert set(closing) == set(rec["status_1_close"]["body_keys"])
    assert closing["users"] == ["u1"] and closing["actions"] == [{"type": 0, "userid": "u2"}]
    await anna.type("Anna was hier, nog eens")
    await anna.close()
    two = (await side.wait(2))[0]
    assert set(two) == set(rec["status_2"]["body_keys"])
    assert two["notmodified"] is False and two["actions"] == [{"type": 0, "userid": "u1"}]
    assert two["lastsave"].endswith(".000Z")
    r = await world.http.get(world.direct(two["url"]))
    assert docx.paragraphs(r.content) == ["Eerste alinea", "Anna was hier, nog eens", "Bram ook"]

    # De key is opgeslagen: wie er nu mee opent, krijgt refreshFile (M17)
    late = world.editor(world.doc_config("k3-abc", "proef.docx", "u3", "Cor"))
    with pytest.raises((Refused, ConnectionError)):
        await late.open()
    assert any(m.get("type") == "refreshFile" for m in late.messages)


@pytest.mark.anyio
async def test_status_4_en_drop(world):
    rec = recorded("callbacks")
    side = world.side
    anna = world.editor(world.doc_config("k4-abc", "proef.docx", "u1", "Anna"))
    await anna.open()
    await anna.close()
    four = (await side.wait(4))[0]
    assert set(four) == set(rec["status_4"]["body_keys"])
    # drop zonder users: iedereen eruit en de key dicht (status 4 zonder actions)
    bram = world.editor(world.doc_config("k5-abc", "proef.docx", "u2", "Bram"))
    await bram.open()
    assert (await world.command("drop", key="k5-abc")) == {"key": "k5-abc", "error": 0}
    await bram.wait_for("drop")
    drop = (await side.wait(4, 2))[1]
    assert set(drop) == set(rec["status_4_drop"]["body_keys"])
    # drop met users: alleen die eruit; hij kan opnieuw verbinden (M15)
    cor = world.editor(world.doc_config("k6-abc", "proef.docx", "u3", "Cor"))
    dirk = world.editor(world.doc_config("k6-abc", "proef.docx", "u4", "Dirk"))
    await cor.open()
    await dirk.open()
    await cor.type("iets")
    assert (await world.command("drop", key="k6-abc", users=["u4"]))["error"] == 0
    await dirk.wait_for("drop")
    info = await world.command("info", key="k6-abc")
    deadline = time.monotonic() + 5
    while info["users"] != ["u3"]:
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)
        info = await world.command("info", key="k6-abc")
    await dirk.reconnect()
    assert (await world.command("info", key="k6-abc"))["users"] == ["u3", "u4"]
    await cor.close()
    await dirk.close()
    two = (await side.wait(2))[0]
    assert two["key"] == "k6-abc"


@pytest.mark.anyio
async def test_commandos_op_een_open_key_zoals_gemeten(world):
    """info, forcesave en drop op een open key, en info na de drop, met de antwoorden die de
    spike van de echte DS opnam (recorded/commands.json)."""
    rec = recorded("commands")

    def want(name: str, key: str) -> dict:
        return json.loads(json.dumps(rec[name]["answer"]).replace("<key>", key))

    key = "k9-abc"
    anna = world.editor(world.doc_config(key, "proef.docx", "u1", "Anna"))
    await anna.open()
    assert await world.command("info", key=key) == want("info open key", key)
    answer = await world.command("forcesave", key=key, userdata="timer")
    assert answer == want("forcesave zonder wijzigingen", key)
    await anna.type("iets")
    answer = await world.command("forcesave", key=key, userdata="timer")
    assert answer == want("forcesave met wijzigingen", key)
    six = (await world.side.wait(6))[0]
    measured = rec["forcesave callback"]["body"]
    assert set(measured) <= set(six)
    assert (six["userdata"], six["forcesavetype"]) == (
        measured["userdata"],
        measured["forcesavetype"],
    )
    # Na drop van de enige gebruiker kent DS de key nog even, zonder gebruikers [gemeten]
    world.config.save_delay = 5.0
    assert await world.command("drop", key=key, users=["u1"]) == want("drop open key", key)
    await anna.wait_for("drop")
    deadline = time.monotonic() + 5
    info = await world.command("info", key=key)
    while info.get("users"):
        assert time.monotonic() < deadline, info
        await asyncio.sleep(0.02)
        info = await world.command("info", key=key)
    assert info == want("info na drop", key)


@pytest.mark.anyio
async def test_tokens_bij_het_verbinden(world):
    # Een token van een andere key, een ander geheim, verlopen: geweigerd na het eerste pakket
    other = world.doc_config("kB-abc", "proef.docx", "u1", "Anna")
    wrong = dict(other, document={**other["document"], "key": "kA-abc"})
    with pytest.raises(Refused) as caught:
        await world.editor(wrong).open()
    assert caught.value.code == protocol.ERR_TOKEN
    bad = dict(world.doc_config("kA-abc", "proef.docx", "u1", "Anna"))
    bad["token"] = protocol.sign("ander" * 8, {"document": {"key": "kA-abc"}}, 300)
    with pytest.raises(Refused) as caught:
        await world.editor(bad).open()
    assert caught.value.code == protocol.ERR_TOKEN
    old = dict(world.doc_config("kA-abc", "proef.docx", "u1", "Anna"))
    old["token"] = jwt.encode(
        {"document": {"key": "kA-abc"}, "exp": int(time.time()) - 100},
        world.config.secret,
        algorithm="HS256",
    )
    with pytest.raises(Refused) as caught:
        await world.editor(old).open()
    assert caught.value.code == protocol.ERR_TOKEN_EXPIRED
    # Het sessietoken van key A werkt niet op key B
    anna = world.editor(world.doc_config("kA-abc", "proef.docx", "u1", "Anna"))
    await anna.open()
    thief = world.editor(world.doc_config("kB-abc", "proef.docx", "u9", "Iemand"))
    thief.session = anna.session
    with pytest.raises(Refused):
        await thief._connect(session=True)
    # Een upgrade zonder EIO=4 of met een ander versiesegment: geweigerd
    with pytest.raises(Exception):  # noqa: B017 - elke weigering van de handshake
        await connect(f"ws://127.0.0.1:{world.port}/{protocol.V}/doc/kA-abc/c/")
    await anna.close()
    assert not world.side.bodies(2)


@pytest.mark.anyio
async def test_ophalen_mislukt_of_digest_klopt_niet(world):
    world.side.digest["proef.docx"] = "sha-256=:" + "A" * 43 + "=:"
    anna = world.editor(world.doc_config("k7-abc", "proef.docx", "u1", "Anna"))
    with pytest.raises(Refused) as caught:
        await anna.open()
    assert caught.value.code == protocol.ERR_DOWNLOAD
    bram = world.editor(world.doc_config("k8-abc", "weg.docx", "u2", "Bram"))
    with pytest.raises(Refused) as caught:
        await bram.open()
    assert caught.value.code == protocol.ERR_DOWNLOAD
    await anna.close()
    await bram.close()
    assert [b["status"] for b in await world.side.wait(4, 2)] == [4, 4]  # nooit iets veranderd


@pytest.mark.anyio
async def test_opnieuw_proberen_en_forgotten(world):
    side = world.side
    lost: list[dict] = []

    async def hook(doc, fields):
        lost.append(fields)

    world.fake.on_lost.append(hook)
    anna = world.editor(world.doc_config("k9-abc", "proef.docx", "u1", "Anna"))
    await anna.open()
    await anna.type("tekst")
    # 503, 503 en dan goed: drie keer dezelfde status 6
    side.answers = [(503, {}), (429, {}), (200, {"error": 0})]
    await anna.save()
    sixes = await side.wait(6, 3)
    assert len({json.dumps(b, sort_keys=True) for b in sixes}) == 1
    # {"error": 1} op status 2: niet opnieuw, en naar de forgotten files
    await anna.type("meer tekst")
    side.answers = [(200, {"error": 1})]
    await anna.close()
    await side.wait(2)
    await asyncio.sleep(0.1)
    assert len(side.bodies(2)) == 1
    assert lost and lost[0]["status"] == 2
    # Na alle pogingen (1, 2 en 4 s maal time_scale) ook
    bram = world.editor(world.doc_config("k10-abc", "proef.docx", "u2", "Bram"))
    await bram.open()
    await bram.type("x")
    side.answers = [(500, {})] * 4
    await bram.close()
    await side.wait(2, 5)
    deadline = time.monotonic() + 5
    while len(lost) < 2:
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)
    assert lost[1]["key"] == "k10-abc"


@pytest.mark.anyio
async def test_stop_slaat_alles_op_binnen_een_seconde(world):
    side = world.side
    anna = world.editor(world.doc_config("k11-abc", "proef.docx", "u1", "Anna"))
    await anna.open()
    await anna.type("bijna kwijt")
    start = time.monotonic()
    r = await world.http.post("/__fake/stop")
    assert r.status_code == 200
    two = (await side.wait(2, seconds=1))[0]
    assert time.monotonic() - start < 1
    assert two["key"] == "k11-abc" and "actions" not in two
    assert (await world.http.get("/healthcheck")).status_code == 503
    await world.http.post("/__fake/restart")
    assert (await world.http.get("/healthcheck")).text == "true"
    # kill: geen callbacks
    bram = world.editor(world.doc_config("k12-abc", "proef.docx", "u2", "Bram"))
    await bram.open()
    await bram.type("weg")
    count = len(side.callbacks)
    await world.http.post("/__fake/kill")
    await asyncio.sleep(0.4)
    assert len(side.callbacks) == count
    await world.http.post("/__fake/restart")


@pytest.mark.anyio
async def test_secure_links_en_restart(world):
    anna = world.editor(world.doc_config("k13-abc", "proef.docx", "u1", "Anna"))
    await anna.open()
    opened = await anna.wait_for("documentOpen")
    link = world.direct(opened["data"]["data"]["Editor.bin"])
    assert (await world.http.get(link)).status_code == 200
    assert (await world.http.get(link.replace("md5=", "md5=x"))).status_code == 403
    expired = protocol.link_md5(world.fake.link_secret, "/cache/files/data/k13-abc/x/x", 1)
    r = await world.http.get(f"/cache/files/data/k13-abc/x/x?md5={expired}&expires=1")
    assert r.status_code == 403
    await world.http.post("/__fake/restart")  # nieuw linkgeheim
    assert (await world.http.get(link)).status_code == 403


@pytest.mark.anyio
async def test_log_zonder_geheimen(world):
    anna = world.editor(world.doc_config("k14-abc", "proef.docx", "u1", "Anna"))
    await anna.open()
    await anna.type("x")
    await anna.save()
    await anna.close()
    await world.side.wait(2)
    log = json.dumps((await world.http.get("/__fake/log")).json(), ensure_ascii=False)
    assert "eyJ" not in log  # geen JWT
    assert world.fake.link_secret not in log
    assert "md5=…" in log and "md5=" not in log.replace("md5=…", "")
    kinds = {e["kind"] for e in json.loads(log)["entries"]}
    assert {"socket", "callback", "download", "change", "http"} <= kinds


def test_als_proces(tmp_path):
    """python -m tests.fake_docserver: draait, spreekt het protocol, en stopt op PID."""
    secret_file = tmp_path / "geheim"
    secret_file.write_text(secret())
    link_file = tmp_path / "linkgeheim"
    link_file.write_text(secret())
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.fake_docserver",
            "--port",
            str(port),
            "--secret-file",
            str(secret_file),
            "--link-secret-file",
            str(link_file),
            "--time-scale",
            "0.01",
            "--save-delay",
            "0.2",
        ],
        cwd=API,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as c:
            deadline = time.monotonic() + 20
            while True:
                try:
                    if c.get("/healthcheck").text == "true":
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < deadline and proc.poll() is None
                time.sleep(0.1)
            body = {"c": "version"}
            body["token"] = protocol.sign(secret_file.read_text(), body, 300)
            assert c.post("/command", json=body).json() == recorded("commands")["version"]["answer"]
            assert c.get("/web-apps/apps/api/documents/api.js").status_code == 200
    finally:
        proc.terminate()  # op PID
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)
    out = (proc.stdout.read() + proc.stderr.read()).decode()
    assert secret_file.read_text() not in out
