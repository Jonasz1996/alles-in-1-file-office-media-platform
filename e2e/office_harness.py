"""Het harnas voor de e2e van office (fase 4, §3.6 en §4.4-§4.5 van docs/fase-4/bouwontwerp.md).

Uitleg en gebruik: docs/fase-4/e2e.md. Kort:

- FakeDS: de nep-DS (`python -m tests.fake_docserver`) als eigen proces, met een vrije poort,
  geheimen in een tijdelijke map (600) en stoppen op PID;
- DocStub: een kleine luisteraar die voor DS de documenten serveert (met het download-JWT) en
  de callbacks aanneemt (met het callback-JWT) en de opgeslagen bestanden ophaalt. Zolang de
  interne routes van de app O0-stubs zijn (503 office_off), speelt die de kant van de app. O2
  vervangt hem door de echte interne luisteraar;
- Office: een eigen Server met office aan (PLATFORM_OFFICE_*), plus DS (nep of echt) en de
  DocStub. Gemaakt door de fixtures office en real_office in conftest.py;
- de gastpagina: een pagina op de oorsprong van de app (via page.route) met precies de CSP
  van de app, die api.js via /office/ laadt en een editor opent. Zo gaat alles door de echte
  proxy en tunnel, zoals straks de web-app;
- hulpjes: editor_config, open_editor, frame_editor, type_in_editor, wait_callback,
  fake_info en docx_text.

conftest importeert deze module pas in een fixture (deze module importeert conftest).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx

from conftest import API_DIR, E2E_DIR, Account, Server, free_port, make_admin

FIXTURES = E2E_DIR / "fixtures" / "office"
# De nep-DS in de e2e: pogingen 1, 2 en 4 s maal deze factor; status 2 zo lang na de laatste
# sluiter (§3.6: "in tests 0,2 s")
FAKE_TIME_SCALE = 0.05
FAKE_SAVE_DELAY = 0.2
# Een nieuwe DS is binnen 6 minuten gezond (§4.5 stap 3)
REAL_DS_SECONDS = 360.0
# Zo lang wacht de app hooguit op de font-herstart van DS voordat hij ok zegt
# (health.SETTLING_SECONDS); office_ds.sh wait wacht daar meestal al op
REAL_READY_SECONDS = 900.0
API_JS = "/office/web-apps/apps/api/documents/api.js"
HOST_PREFIX = "/__e2e/office"
DOC_TYPES = {
    "docx": "word",
    "doc": "word",
    "odt": "word",
    "rtf": "word",
    "xlsx": "cell",
    "xls": "cell",
    "ods": "cell",
    "pptx": "slide",
    "ppt": "slide",
    "odp": "slide",
}


def _write_secret(path: Path, value: str) -> None:
    """Een geheim naar een bestand met 600-rechten (nooit naar de log)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)


def _kill(proc: subprocess.Popen | None) -> None:
    """Alleen dit proces, via zijn PID: SIGTERM, dan SIGKILL."""
    if proc is None or proc.poll() is not None:
        return
    os.kill(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)


def run_async[T](fn: Callable[[], Awaitable[T]]) -> T:
    """Async code van de app (DocServer, ...) vanuit een synchrone test: in een eigen draad met
    een eigen lus, los van die van Playwright."""
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(lambda: asyncio.run(fn())).result()


# --- de nep-DS als proces ---


class FakeDS:
    """`python -m tests.fake_docserver` als subproces (§3.6). Stoppen op PID; een herstart
    houdt poort en geheimen."""

    def __init__(
        self,
        workdir: Path,
        *,
        time_scale: float = FAKE_TIME_SCALE,
        save_delay: float = FAKE_SAVE_DELAY,
    ) -> None:
        self.workdir = Path(workdir)
        self.time_scale = time_scale
        self.save_delay = save_delay
        self.secret = secrets.token_hex(32)
        self.secret_file = self.workdir / "office.secret"
        self.link_secret_file = self.workdir / "link.secret"
        self.log_path = self.workdir / "fake-ds.log"
        self.port: int | None = None
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        assert self.port is not None, "de nep-DS is nog niet gestart"
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if not self.secret_file.exists():
            _write_secret(self.secret_file, self.secret)
            _write_secret(self.link_secret_file, secrets.token_hex(32))
        self.port = self.port or free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_")}
        command = [
            sys.executable,
            "-m",
            "tests.fake_docserver",
            "--port",
            str(self.port),
            "--secret-file",
            str(self.secret_file),
            "--link-secret-file",
            str(self.link_secret_file),
            "--time-scale",
            str(self.time_scale),
            "--save-delay",
            str(self.save_delay),
        ]
        log = open(self.log_path, "ab")
        try:
            self.proc = subprocess.Popen(
                command, cwd=API_DIR, env=env, stdout=log, stderr=subprocess.STDOUT
            )
        finally:
            log.close()
        try:
            self._wait_ready()
        except BaseException:
            self.stop()
            raise

    def _wait_ready(self, seconds: float = 30) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"De nep-DS stopte bij het starten:\n{self.log_tail()}")
            if self.healthy():
                return
            time.sleep(0.1)
        raise RuntimeError(f"De nep-DS kwam niet op binnen {seconds} s:\n{self.log_tail()}")

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        _kill(proc)

    def restart(self) -> None:
        self.stop()
        self.start()

    def log_tail(self, lines: int = 40) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return "(geen log)"

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, trust_env=False, timeout=10)

    def healthy(self) -> bool:
        try:
            with self._client() as c:
                r = c.get("/healthcheck", timeout=2)
        except httpx.HTTPError:
            return False
        return r.status_code == 200 and r.text.strip() == "true"

    def control(self, name: str, body: dict | None = None) -> dict:
        """POST /__fake/<name>: kill, stop, restart, health, license of jwt-off."""
        with self._client() as c:
            r = c.post(f"/__fake/{name}", json=body or {})
        r.raise_for_status()
        return r.json()

    def info(self, key: str) -> dict:
        """{key, open, users, names, dirty, saved}: wie de nep-DS op deze key kent."""
        with self._client() as c:
            r = c.get(f"/__fake/info/{key}")
        r.raise_for_status()
        return r.json()

    def log(self) -> list[dict]:
        with self._client() as c:
            r = c.get("/__fake/log")
        r.raise_for_status()
        return r.json()["entries"]

    def callbacks(self, key: str | None = None) -> list[dict]:
        """De callbacks die de nep-DS verstuurde (status, http, answer, fields)."""
        return [
            e
            for e in self.log()
            if e.get("kind") == "callback" and (key is None or e.get("key") == key)
        ]


# --- de kant van de app voor DS (documenten en callbacks) ---


class DocStub:
    """GET /doc/<naam> en /src/<naam>: het bestand, alleen met een geldig download-JWT
    (operation download, payload.url precies dit adres), met Repr-Digest. POST /callback: alleen
    met een geldig callback-JWT; status 2 en 6 met url: het bestand ophalen bij
    PLATFORM_OFFICE_URL (urls.rewrite, zoals de app) en bewaren. Wat binnenkwam staat in
    callbacks() en downloads, zonder tokens. answer: "ok" (200 {"error": 0}), "503" of
    "error1" (200 {"error": 1})."""

    def __init__(self, settings: Any, *, bind: str = "127.0.0.1", host: str = "127.0.0.1"):
        self.settings = settings
        self.bind = bind
        self.host = host
        self.files: dict[str, bytes] = {}
        self.saved: dict[str, list[bytes]] = {}
        self.downloads: list[dict] = []
        self.answer = "ok"
        self._callbacks: list[dict] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._port: int | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        assert self._port is not None, "de stub is nog niet gestart"
        return self._port

    @property
    def base(self) -> str:
        """Het adres zoals DS het ziet."""
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        """Starten; na een stop weer op dezelfde poort (zoals de app na een herstart)."""
        port = self._port or 0
        self._server = ThreadingHTTPServer((self.bind, port), _stub_handler(self))
        self._port = self._server.server_address[1]
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="office-stub", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()

    def put(self, name: str, data: bytes) -> str:
        """Een document voor DS; geeft zijn document.url."""
        with self._lock:
            self.files[name] = data
        return f"{self.base}/doc/{name}"

    def source(self, name: str, data: bytes) -> str:
        """Een bron voor een conversie; geeft zijn url."""
        with self._lock:
            self.files[name] = data
        return f"{self.base}/src/{name}"

    def callback_url(self, tag: str = "") -> str:
        return f"{self.base}/callback" + (f"?f={tag}" if tag else "")

    def callbacks(self, key: str | None = None) -> list[dict]:
        with self._lock:
            return [dict(c) for c in self._callbacks if key is None or c.get("key") == key]

    def last_saved(self, key: str) -> bytes | None:
        with self._lock:
            versions = self.saved.get(key) or []
            return versions[-1] if versions else None

    # --- voor de handler ---

    def _record(self, entry: dict) -> None:
        entry["t"] = time.time()
        with self._lock:
            self._callbacks.append(entry)

    def _store(self, key: str, data: bytes) -> None:
        with self._lock:
            self.saved.setdefault(key, []).append(data)


def _repr_digest(data: bytes) -> str:
    return "sha-256=:" + base64.b64encode(hashlib.sha256(data).digest()).decode() + ":"


def _stub_handler(stub: DocStub) -> type[BaseHTTPRequestHandler]:
    from app.office import urls
    from app.office.engine import TokenError
    from app.office.oo import tokens

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # geen log: in een query kan een token staan

        def _send(self, status: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            if kind == "application/octet-stream":
                self.send_header("Repr-Digest", _repr_digest(body))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            match = re.fullmatch(r"/(doc|src)/([A-Za-z0-9._-]+)", path)
            if match is None:
                return self._send(404, b'{"error":"not_found"}')
            url = stub.base + self.path
            try:
                tokens.check_download(stub.settings, self.headers.get("Authorization"), url)
            except TokenError as exc:
                stub.downloads.append({"path": path, "ok": False, "why": str(exc)})
                return self._send(401, b'{"error":"office_token"}')
            data = stub.files.get(match.group(2))
            stub.downloads.append(
                {
                    "path": path,
                    "ok": data is not None,
                    "user_agent": self.headers.get("User-Agent"),
                }
            )
            if data is None:
                return self._send(404, b'{"error":"not_found"}')
            self._send(200, data, "application/octet-stream")

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/callback":
                return self._send(404, b'{"error":"not_found"}')
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                fields = tokens.callback_data(
                    stub.settings, body, self.headers.get("Authorization")
                )
            except (ValueError, TokenError) as exc:
                stub._record({"key": None, "status": None, "refused": str(exc)})
                return self._send(403, b'{"error":1}')
            entry = {k: v for k, v in fields.items() if k != "token"}
            entry["header_token"] = bool(self.headers.get("Authorization"))
            entry["body_token"] = isinstance(body, dict) and "token" in body
            key = fields.get("key")
            if fields.get("status") in (2, 6) and isinstance(fields.get("url"), str):
                try:
                    target = urls.rewrite(stub.settings, fields["url"], key=key, kind="data")
                    with httpx.Client(trust_env=False, timeout=60) as client:
                        r = client.get(target)
                    entry["fetched"] = r.status_code
                    if r.status_code == 200:
                        stub._store(key, r.content)
                except (ValueError, httpx.HTTPError) as exc:
                    entry["fetched"] = f"fout: {type(exc).__name__}"
            stub._record(entry)
            if stub.answer == "503":
                return self._send(503, b'{"error":1}')
            if stub.answer == "error1":
                return self._send(200, b'{"error":1}')
            self._send(200, b'{"error":0}')

        do_HEAD = do_GET  # noqa: N815

    return Handler


# --- de server met office aan ---


def office_env(
    *, ds_url: str, secret: str, internal_port: int, app_host: str, internal_host: str = ""
) -> dict[str, str]:
    """PLATFORM_OFFICE_* voor een Server (§3.6). De ronde op 1 s, het herstel op 2 s en de
    gezondheid elke seconde, zodat een test DS weg en terug snel ziet. Geen
    PLATFORM_OFFICE_DEV_UNGATED: sinds O1 geldt in elke e2e de echte poort (cookie en recht)."""
    env = {
        "PLATFORM_OFFICE_URL": ds_url,
        "PLATFORM_OFFICE_SECRET": secret,
        "PLATFORM_OFFICE_APP_URL": f"http://{app_host}:{internal_port}",
        "PLATFORM_OFFICE_INTERNAL_PORT": str(internal_port),
        "PLATFORM_OFFICE_SWEEP_SECONDS": "1",
        "PLATFORM_OFFICE_RECOVERY_SECONDS": "2",
        "PLATFORM_OFFICE_HEALTH_SECONDS": "1",
    }
    if internal_host:
        env["PLATFORM_OFFICE_INTERNAL_HOST"] = internal_host
    return env


@dataclass
class Office:
    """Een Server met office aan, met zijn DS (nep of echt) en de DocStub."""

    server: Server
    admin: Account
    ds_url: str
    secret: str = field(repr=False)
    internal_port: int
    stub: DocStub
    fake: FakeDS | None = None
    real: bool = False
    csp: str = ""
    _settings: Any = field(default=None, repr=False)

    def settings(self) -> Any:
        """Settings zoals die van de server (voor tokens, urls en DocServer in een test)."""
        if self._settings is None:
            from app.config import Settings

            self._settings = Settings(
                _env_file=None,
                data_dir=self.server.data_dir,
                office_url=self.ds_url,
                office_secret=self.secret,
                office_app_url=f"http://127.0.0.1:{self.internal_port}",
                office_internal_port=self.internal_port,
            )
        return self._settings

    def status(self) -> dict:
        return self.server.call(self.admin, "GET", "/api/v1/office/status", expect=200).json()

    def wait_state(self, state: str, seconds: float = 30) -> dict:
        """Wacht tot /api/v1/office/status deze toestand noemt."""
        end = time.monotonic() + seconds
        last: dict = {}
        while time.monotonic() < end:
            last = self.status()
            if last.get("state") == state:
                return last
            time.sleep(0.2)
        raise AssertionError(f"office niet {state!r} binnen {seconds} s: {last}")

    def ds[T](self, fn: Callable[[Any], Awaitable[T]]) -> T:
        """fn(DocServer) met de instellingen van deze server; de client gaat daarna dicht."""
        from app.office.oo.client import DocServer

        async def go() -> T:
            client = DocServer(self.settings())
            try:
                return await fn(client)
            finally:
                await client.aclose()

        return run_async(go)

    def stop(self) -> None:
        self.server.stop()
        self.stub.stop()
        if self.fake is not None:
            self.fake.stop()


def _app_csp(server: Server) -> str:
    """De CSP van de web-app, zoals de server hem op / geeft (WEB_CSP)."""
    csp = server.request("GET", "/", expect=200).headers.get("content-security-policy", "")
    assert csp, "de app gaf geen CSP op /"
    return csp


def _start(
    base: Path,
    *,
    ds_url: str,
    secret: str,
    fake: FakeDS | None,
    real: bool,
    app_host: str,
    internal_host: str,
    stub_bind: str,
    stub_host: str,
) -> Office:
    internal_port = free_port()
    env = office_env(
        ds_url=ds_url,
        secret=secret,
        internal_port=internal_port,
        app_host=app_host,
        internal_host=internal_host,
    )
    server = Server(base / "data", env=env)
    server.start()
    try:
        admin = make_admin(server)
        office = Office(
            server=server,
            admin=admin,
            ds_url=ds_url,
            secret=secret,
            internal_port=internal_port,
            stub=DocStub(None, bind=stub_bind, host=stub_host),
            fake=fake,
            real=real,
        )
        office.stub.settings = office.settings()
        office.stub.start()
        office.csp = _app_csp(server)
        office.wait_state("ok", REAL_READY_SECONDS if real else 60)
    except BaseException:
        server.stop()
        raise
    return office


def start_fake_office(base: Path) -> Office:
    """De nep-DS als proces en een Server die hem gebruikt (fixture office)."""
    fake = FakeDS(base / "fake-ds")
    fake.start()
    try:
        return _start(
            base,
            ds_url=fake.url,
            secret=fake.secret,
            fake=fake,
            real=False,
            app_host="127.0.0.1",
            internal_host="",
            stub_bind="127.0.0.1",
            stub_host="127.0.0.1",
        )
    except BaseException:
        fake.stop()
        raise


def wait_real_ds(url: str, seconds: float = REAL_DS_SECONDS) -> None:
    """GET /healthcheck == true, hooguit seconds (§4.5 stap 3)."""
    end = time.monotonic() + seconds
    with httpx.Client(trust_env=False, timeout=5) as c:
        while time.monotonic() < end:
            with contextlib.suppress(httpx.HTTPError):
                r = c.get(url.rstrip("/") + "/healthcheck")
                if r.status_code == 200 and r.text.strip() == "true":
                    return
            time.sleep(1)
    raise AssertionError(f"de echte DS op {url} werd niet gezond binnen {seconds:.0f} s")


def start_real_office(base: Path, url: str, secret: str) -> Office:
    """Een Server tegen een echte DS (fixture real_office). DS bereikt de host als
    host.docker.internal (E2E_REAL_DS_APP_HOST om dat te veranderen): de interne luisteraar en
    de DocStub luisteren daarom op 0.0.0.0."""
    wait_real_ds(url)
    host = os.environ.get("E2E_REAL_DS_APP_HOST") or "host.docker.internal"
    return _start(
        base,
        ds_url=url.rstrip("/"),
        secret=secret,
        fake=None,
        real=True,
        app_host=host,
        internal_host="0.0.0.0",
        stub_bind="0.0.0.0",
        stub_host=host,
    )


# --- de gastpagina en de editor ---

# De gastpagina: geen inline script of stijl (de CSP van de app staat dat niet toe); de config
# staat als JSON in de pagina. Geen onRequestSaveAs: met die handler vraagt de echte editor bij
# "Downloaden als" de gastpagina om de opslag, in plaats van zelf te downloaden (spike).
HOST_JS = r"""
(() => {
  window.__events = [];
  const t0 = performance.now();
  const push = (name, data) => window.__events.push(
    [name, Math.round(performance.now() - t0), data === undefined ? null : data]);
  window.__api = typeof window.DocsAPI === "object" && typeof window.DocsAPI.DocEditor;
  const el = document.getElementById("cfg");
  if (!el) return;
  const config = JSON.parse(el.textContent);
  config.events = {
    onAppReady: () => push("appReady"),
    onDocumentReady: () => { push("documentReady"); window.__ready = true; },
    onError: (e) => push("error", e.data),
    onWarning: (e) => push("warning", e.data),
    onInfo: (e) => push("info", e.data),
    onDocumentStateChange: (e) => { window.__dirty = e.data; },
    onRequestClose: () => push("requestClose"),
    onRequestRefreshFile: () => {
      push("requestRefreshFile");
      window.__refresh = (window.__refresh || 0) + 1;
    },
    onDownloadAs: (e) => push("downloadAs", e.data),
    onOutdatedVersion: () => push("outdatedVersion"),
  };
  window.docEditor = new DocsAPI.DocEditor("ph", config);
})();
"""
HOST_CSS = "html,body{margin:0;height:100%}#ph{height:100%}"


def host_html(config: dict | None, title: str = "Office in de e2e") -> str:
    data = ""
    if config is not None:
        text = json.dumps(config).replace("<", "\\u003c")
        data = f'<script type="application/json" id="cfg">{text}</script>'
    return (
        '<!doctype html><html lang="nl"><head><meta charset="utf-8">'
        f"<title>{title}</title>"
        f'<link rel="stylesheet" href="{HOST_PREFIX}/host.css"></head>'
        f'<body><div id="ph"></div>{data}<script src="{API_JS}"></script>'
        f'<script src="{HOST_PREFIX}/host.js"></script></body></html>'
    )


def serve_host(page: Any, office: Office, config: dict | None = None) -> str:
    """De gastpagina op de oorsprong van de app (page.route), met de CSP van de app. Geeft het
    adres; page.goto doet de test (of open_editor)."""
    base = office.server.url
    name = f"gast-{uuid.uuid4().hex[:10]}.html"
    html = host_html(config)
    headers = {"content-security-policy": office.csp, "cache-control": "no-store"}

    def fulfill(body: str, kind: str) -> Callable[[Any], None]:
        return lambda route: route.fulfill(
            status=200, headers={**headers, "content-type": kind}, body=body
        )

    page.route(f"{base}{HOST_PREFIX}/{name}", fulfill(html, "text/html; charset=utf-8"))
    page.route(f"{base}{HOST_PREFIX}/host.js", fulfill(HOST_JS, "text/javascript"))
    page.route(f"{base}{HOST_PREFIX}/host.css", fulfill(HOST_CSS, "text/css"))
    return f"{base}{HOST_PREFIX}/{name}"


def new_key(tag: str = "e2e") -> str:
    return f"{tag}-{uuid.uuid4().hex[:16]}"


def editor_config(
    office: Office,
    *,
    key: str,
    url: str,
    user_id: str,
    user_name: str,
    file_type: str = "docx",
    title: str | None = None,
    mode: str = "edit",
    lang: str = "nl",
    callback: str | None = None,
    customization: dict | None = None,
) -> dict:
    """Een editorconfig zoals de app hem straks maakt (§3.9), getekend met het office-geheim
    (tokens.browser_token)."""
    from app.office.oo import tokens

    custom = {
        "autosave": True,
        "forcesave": True,
        "feedback": False,
        "help": False,
        "plugins": False,
        "macros": False,
        "macrosMode": "disable",
        **(customization or {}),
    }
    config = {
        "documentType": DOC_TYPES[file_type],
        "type": "desktop",
        "width": "100%",
        "height": "100%",
        "document": {
            "fileType": file_type,
            "key": key,
            "title": title or f"proef.{file_type}",
            "url": url,
            "permissions": {
                "edit": mode == "edit",
                "download": True,
                "print": True,
                "comment": True,
                "review": True,
                "copy": True,
                "fillForms": True,
            },
        },
        "editorConfig": {
            "mode": mode,
            "lang": lang,
            "region": "nl-NL" if lang == "nl" else "en-GB",
            "callbackUrl": callback or office.stub.callback_url(key),
            "user": {"id": user_id, "name": user_name},
            "coEditing": {"mode": "fast", "change": True},
            "customization": custom,
        },
    }
    config["token"] = tokens.browser_token(office.settings(), config)
    return config


def events(page: Any) -> list[list]:
    """Wat de gastpagina van de editor zag: [naam, ms sinds de start, data]."""
    return page.evaluate("() => window.__events || []")


def open_editor(page: Any, office: Office, config: dict, *, timeout: float = 60) -> float:
    """De gastpagina openen en wachten op onDocumentReady. Geeft de seconden tot klaar; bij
    onError of geen klaar binnen timeout een AssertionError met de events."""
    url = serve_host(page, office, config)
    t0 = time.monotonic()
    page.goto(url)
    try:
        page.wait_for_function(
            "() => window.__ready === true || "
            "(window.__events || []).some((e) => e[0] === 'error')",
            timeout=timeout * 1000,
        )
    except Exception as exc:  # noqa: BLE001 - de events zeggen meer dan de time-out
        raise AssertionError(f"editor niet klaar binnen {timeout} s: {events(page)}") from exc
    seen = events(page)
    if any(e[0] == "error" for e in seen):
        raise AssertionError(f"onError bij openen: {seen}")
    return round(time.monotonic() - t0, 2)


def frame_editor(page: Any, timeout: float = 30) -> Any:
    """De iframe van de editor (name frameEditor, zowel bij de echte als bij de nep)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        frame = page.frame(name="frameEditor")
        if frame is not None:
            return frame
        page.wait_for_timeout(100)
    raise AssertionError("geen iframe frameEditor")


def is_fake(page: Any) -> bool:
    return frame_editor(page).locator('textarea[aria-label="Nep-editor"]').count() > 0


def type_in_editor(page: Any, text: str, *, new_paragraph: bool = True) -> None:
    """Tekst typen zoals een gebruiker. Nep: in de textarea. Echt: klik in het documentvlak
    (#id_main_view in de iframe frameEditor [gemeten]), naar het eind, Enter en keyboard.type."""
    frame = frame_editor(page)
    fake = frame.locator('textarea[aria-label="Nep-editor"]')
    if fake.count():
        fake.click()
        page.keyboard.press("Control+End")
        if new_paragraph and fake.input_value():
            page.keyboard.press("Enter")
        page.keyboard.type(text)
        return
    for selector in ("#id_main_view", "#ws-canvas-outer", "#editor_sdk"):
        area = frame.locator(selector)
        if area.count() and area.first.is_visible():
            area.first.click(position={"x": 420, "y": 300}, timeout=30_000)
            break
    else:
        raise AssertionError("geen documentvlak in de editor")
    if new_paragraph:
        page.keyboard.press("Control+End")
        page.keyboard.press("Enter")
    page.keyboard.type(text, delay=25)


def wait_callback(
    where: Any, key: str, status: int, *, timeout: float = 60, after: int = 0
) -> dict:
    """De eerste callback met deze status op deze key (vanaf nummer after), van een DocStub of
    een FakeDS (alles met callbacks(key))."""
    end = time.monotonic() + timeout
    seen: list[dict] = []
    while time.monotonic() < end:
        seen = where.callbacks(key)
        for entry in seen[after:]:
            if entry.get("status") == status:
                return entry
        time.sleep(0.2)
    statuses = [e.get("status") for e in seen]
    raise AssertionError(f"geen callback {status} op {key} binnen {timeout} s (wel: {statuses})")


def fake_info(office: Office, key: str) -> dict:
    """De gebruikers op een key, uit de nep-DS zelf (zonder O5)."""
    assert office.fake is not None, "fake_info kan alleen met de nep-DS"
    return office.fake.info(key)


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(data: bytes) -> str:
    """De tekst van een docx: één regel per alinea."""
    import io

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    lines = []
    for p in root.iter(f"{W}p"):
        lines.append("".join(t.text or "" for t in p.iter(f"{W}t")))
    return "\n".join(lines)


def fixture(name: str) -> bytes:
    """Een bestand uit e2e/fixtures/office/."""
    return (FIXTURES / name).read_bytes()
