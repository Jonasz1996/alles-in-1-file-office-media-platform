"""Het e2e- en meetharnas (P0, frozen na P0; zie docs/fase-3/e2e.md).

Draait de echte server (`python -m app.serve`) als subproces met een eigen datamap, de
gebouwde web-app uit web/dist, vrije poorten en zonder rate limits, en een Chromium via
Playwright (Python). Starten vanuit api/:

    cd api && uv run --with playwright pytest ../e2e -m "not perf"

Wat hier staat, gebruiken ook de meetscripts in e2e/perf/ (die als los script draaien):
Server, Account, Browsers, standalone() en de hulpjes voor 2FA en CDP.

Bewaking: elke browsercontext krijgt een init-script dat CSP-schendingen
(securitypolicyviolation), console.error-regels die met "i18n:" beginnen, gelogde fouten (een
Error-object of een React-fout, zoals een foutgrens die logt) en onafgevangen fouten doorgeeft.
Eén treffer en de test faalt; een test die er een verwacht, haalt hem weg met Guard.take().
"""

from __future__ import annotations

import contextlib
import itertools
import math
import os
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

E2E_DIR = Path(__file__).resolve().parent
REPO = E2E_DIR.parent
API_DIR = REPO / "api"
WEB_DIST = Path(os.environ.get("E2E_WEB_DIST") or REPO / "web" / "dist").resolve()
# Chromium in de sandbox (rev 1194, Chromium 141); elders die van Playwright zelf
SANDBOX_CHROMIUM = Path("/opt/pw-browsers/chromium")
PASSWORD = "e2e-wachtwoord-lang-genoeg"
# Hoe lang een access-token hier meegaat voordat we opnieuw inloggen (de server: 15 min)
TOKEN_SECONDS = 10 * 60

# app.* (totp, models, previews) importeren zoals de server dat doet
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))


def _loopback_without_proxy() -> None:
    """Verzoeken naar de testserver nooit via een proxy (de sandbox heeft er een)."""
    for key in ("NO_PROXY", "no_proxy"):
        parts = [p for p in os.environ.get(key, "").split(",") if p]
        for host in ("127.0.0.1", "localhost"):
            if host not in parts:
                parts.append(host)
        os.environ[key] = ",".join(parts)


_loopback_without_proxy()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- accounts ---


@dataclass
class Account:
    id: str
    email: str
    password: str
    display_name: str
    role: str
    root_id: str | None = None
    token: str | None = field(default=None, repr=False)
    token_at: float = field(default=0.0, repr=False)


_counter = itertools.count(1)


def unique_email(prefix: str = "e2e") -> str:
    return f"{prefix}-{next(_counter)}-{uuid.uuid4().hex[:8]}@voorbeeld.nl"


# --- de server ---


class Server:
    """`python -m app.serve` als subproces. Stoppen gaat via zijn PID (SIGTERM, dan SIGKILL)."""

    def __init__(
        self, data_dir: Path, web_dist: Path = WEB_DIST, env: dict[str, str] | None = None
    ):
        self.data_dir = Path(data_dir)
        self.web_dist = Path(web_dist)
        self.extra_env = dict(env or {})
        self.port: int | None = None
        self.https_port: int | None = None
        self.proc: subprocess.Popen | None = None
        self.log_path = self.data_dir.parent / f"{self.data_dir.name}-server.log"
        self._http: httpx.Client | None = None

    @property
    def url(self) -> str:
        assert self.port is not None, "de server is nog niet gestart"
        return f"http://127.0.0.1:{self.port}"

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            # trust_env=False: geen proxy uit de omgeving
            self._http = httpx.Client(base_url=self.url, trust_env=False, timeout=60)
        return self._http

    def start(self) -> None:
        if not (self.web_dist / "index.html").is_file():
            raise RuntimeError(
                f"Geen gebouwde web-app in {self.web_dist}: eerst `cd web && npm run build`"
            )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Dezelfde poort na een herstart (seeded): open contexts en adressen blijven kloppen
        self.port = self.port or free_port()
        self.https_port = self.https_port or free_port()
        # Niets van een PLATFORM_* uit de omgeving van de ontwikkelaar; leeg overschrijft ook
        # wat een api/.env zou zetten (database_url leeg = SQLite in de datamap)
        inherited = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_")}
        env = {
            **inherited,
            "PLATFORM_MODE": "standalone",
            "PLATFORM_DATABASE_URL": "",
            "PLATFORM_JWT_SECRET": "",
            "PLATFORM_CLUSTER_NODES": "",
            "PLATFORM_DATA_DIR": str(self.data_dir),
            "PLATFORM_WEB_DIST": str(self.web_dist),
            "PLATFORM_HOST": "127.0.0.1",
            "PLATFORM_HTTP_PORT": str(self.port),
            "PLATFORM_HTTPS_PORT": str(self.https_port),
            "PLATFORM_RATE_LIMITS": "false",
            "PLATFORM_ENVIRONMENT": "e2e",
            **self.extra_env,
        }
        log = open(self.log_path, "ab")
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "app.serve"],
                cwd=API_DIR,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        finally:
            log.close()  # het subproces heeft zijn eigen kopie
        try:
            self._wait_ready()
        except BaseException:
            self.stop()
            raise

    def _wait_ready(self, seconds: float = 60) -> None:
        with httpx.Client(trust_env=False, timeout=2) as probe:
            for _ in range(int(seconds / 0.1)):
                if self.proc is not None and self.proc.poll() is not None:
                    raise RuntimeError(f"De server stopte bij het starten:\n{self.log_tail()}")
                with contextlib.suppress(httpx.HTTPError):
                    if probe.get(f"{self.url}/health").status_code == 200:
                        return
                time.sleep(0.1)
        raise RuntimeError(f"De server kwam niet op binnen {seconds} s:\n{self.log_tail()}")

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if self._http is not None:
            self._http.close()
            self._http = None
        if proc is None or proc.poll() is not None:
            return
        # Alleen dit proces, via zijn PID (nooit pkill)
        os.kill(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)

    def restart(self) -> None:
        self.stop()
        self.start()

    def seeded(self, fn: Callable[[Path], Any]) -> Any:
        """Server stoppen → fn(data_dir) (schrijft rechtstreeks in SQLite) → weer starten."""
        self.stop()
        try:
            return fn(self.data_dir)
        finally:
            self.start()

    def log_tail(self, lines: int = 60) -> str:
        try:
            text = self.log_path.read_text(errors="replace")
        except OSError:
            return "(geen log)"
        return "\n".join(text.splitlines()[-lines:])

    # --- API ---

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        expect: int | tuple[int, ...] | None = None,
        **kw: Any,
    ) -> httpx.Response:
        headers = dict(kw.pop("headers", None) or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = self.http.request(method, path, headers=headers, **kw)
        wanted = (expect,) if isinstance(expect, int) else expect
        if wanted is not None and r.status_code not in wanted:
            raise AssertionError(f"{method} {path} gaf {r.status_code}: {r.text[:500]}")
        return r

    def token(self, account: Account) -> str:
        """Een geldig access-token voor dit account (inloggen zonder 2FA-code)."""
        if account.token is None or time.monotonic() - account.token_at > TOKEN_SECONDS:
            r = self.request(
                "POST",
                "/api/v1/auth/login",
                json={"email": account.email, "password": account.password},
                expect=200,
            )
            account.token = r.json()["access_token"]
            account.token_at = time.monotonic()
        return account.token

    def call(self, account: Account, method: str, path: str, **kw: Any) -> httpx.Response:
        return self.request(method, path, token=self.token(account), **kw)


# --- 2FA (voor tests met een eigen gebruiker per geval) ---


def totp_code(secret: str, after_step: int | None = None) -> tuple[str, int]:
    """(code, stap) voor nu. Met after_step: wacht eerst op een nieuwere stap, want de server
    neemt dezelfde stap geen tweede keer aan."""
    from app import totp

    for _ in range(int(totp.STEP / 0.2) + 10):
        step = totp.current_step()
        if after_step is None or step > after_step:
            return totp.code_at(secret, step), step
        time.sleep(0.2)
    raise AssertionError("geen nieuwe TOTP-stap")


def enable_2fa(server: Server, account: Account, token: str | None = None) -> dict[str, Any]:
    """2FA aanzetten: {"secret", "step", "recovery_codes"}."""
    token = token or server.token(account)
    secret = server.request("POST", "/api/v1/auth/2fa/setup", token=token, expect=200).json()[
        "secret"
    ]
    code, step = totp_code(secret)
    codes = server.request(
        "POST",
        "/api/v1/auth/2fa/enable",
        token=token,
        json={"code": code, "password": account.password},
        expect=200,
    ).json()["recovery_codes"]
    return {"secret": secret, "step": step, "recovery_codes": codes}


def make_admin(server: Server, email: str | None = None, name: str = "Beheerder") -> Account:
    """De eerste beheerder, zoals de echte eerste start: /auth/setup → 2FA aan (met een code
    uit app.totp) → 2FA niet meer verplicht → eigen 2FA weer uit met een herstelcode. Daarna
    logt hij in zonder code."""
    email = email or unique_email("beheer")
    body = {"email": email, "display_name": name, "password": PASSWORD}
    tokens = server.request("POST", "/api/v1/auth/setup", json=body, expect=201).json()
    token, user = tokens["access_token"], tokens["user"]
    account = Account(user["id"], user["email"], PASSWORD, name, "admin", user["root_id"])
    two = enable_2fa(server, account, token)
    server.request(
        "PUT", "/api/v1/admin/security", token=token, json={"require_2fa": False}, expect=200
    )
    server.request(
        "POST",
        "/api/v1/auth/2fa/disable",
        token=token,
        json={"code": two["recovery_codes"][0], "password": PASSWORD},
        expect=204,
    )
    account.token, account.token_at = token, time.monotonic()
    return account


def create_user(
    server: Server,
    admin: Account,
    role: str = "user",
    *,
    name: str | None = None,
    email: str | None = None,
    password: str = PASSWORD,
    quota_bytes: int | None = None,
) -> Account:
    name = name or f"Gebruiker {next(_counter)}"
    body: dict[str, Any] = {
        "email": email or unique_email(role),
        "display_name": name,
        "password": password,
        "role": role,
    }
    if quota_bytes is not None:
        body["quota_bytes"] = quota_bytes
    out = server.call(admin, "POST", "/api/v1/users", json=body, expect=201).json()
    return Account(out["id"], out["email"], password, name, role, out["root_id"])


# --- de browser ---

# Bewaking in elke context: CSP-schendingen, ontbrekende i18n-gebieden (console.error "i18n:")
# en fouten
GUARD_JS = r"""
(() => {
  const report = (kind, text) => {
    try { window.__e2eReport(kind, String(text)); } catch (e) { /* binding (nog) weg */ }
  };
  window.addEventListener("securitypolicyviolation", (e) => {
    report("csp", `${e.violatedDirective} geblokkeerd: ${e.blockedURI || "inline"} ` +
      `(${e.sourceFile || location.href}:${e.lineNumber || 0})`);
  }, true);
  const original = console.error;
  console.error = function (...args) {
    const first = args.length ? String(args[0]) : "";
    if (first.startsWith("i18n:")) {
      report("i18n", args.map((a) => String(a)).join(" ").replace(/^i18n:\s*/, ""));
    } else if (args.some((a) => a instanceof Error) || first.includes("React error")) {
      // Een foutgrens (ChunkBoundary, PageBoundary) vangt de fout en logt hem; anders ziet
      // niemand hem
      const text = args.map((a) => (a instanceof Error ? a.stack || String(a) : String(a)));
      report("fout", text.join(" "));
    }
    return original.apply(this, args);
  };
  window.addEventListener("error", (e) => {
    report("fout", `${e.message} (${e.filename || location.href}:${e.lineno || 0})`);
  });
  window.addEventListener("unhandledrejection", (e) => {
    report("fout", `onafgehandelde belofte: ${e.reason && (e.reason.stack || e.reason)}`);
  });
})();
"""


def chromium_path() -> str | None:
    """E2E_CHROMIUM, anders de Chromium van de sandbox als die er is, anders None (dan kiest
    Playwright zijn eigen, na `playwright install chromium`)."""
    configured = os.environ.get("E2E_CHROMIUM")
    if configured:
        return configured
    return str(SANDBOX_CHROMIUM) if SANDBOX_CHROMIUM.exists() else None


def launch_browser(pw: Any) -> Any:
    from playwright.sync_api import expect

    expect.set_options(timeout=15_000)  # de standaard (5 s) is krap op een drukke runner
    return launch_chromium(pw.chromium)


def launch_chromium(browser_type: Any, *, scrollbars: bool = False) -> Any:
    """Chromium zoals de e2e hem gebruiken. scrollbars: schuifbalken die ruimte innemen, zoals
    in Chrome op een desktop en in Lighthouse (Playwright verbergt ze standaard,
    --hide-scrollbars)."""
    return browser_type.launch(
        executable_path=chromium_path(),
        # Zonder dit gaat Chromium via de proxy van de sandbox, ook voor 127.0.0.1
        args=["--no-proxy-server"],
        headless=os.environ.get("E2E_HEADED") != "1",
        ignore_default_args=["--hide-scrollbars"] if scrollbars else None,
    )


class Guard:
    """Wat de bewaking in één context zag."""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def attach(self, context: Any) -> None:
        context.expose_binding("__e2eReport", self._report)
        context.add_init_script(GUARD_JS)

    def _report(self, source: dict, kind: str, text: str) -> None:
        page = source.get("page")
        where = page.url if page is not None else "?"
        self.problems.append(f"{kind}: {text} [{where}]")

    def take(self) -> list[str]:
        """De problemen tot nu toe, en daarna weer leeg (voor een test die er een verwacht)."""
        problems, self.problems = self.problems, []
        return problems


class Browsers:
    """Contexts op de testserver, elk met bewaking. close_all() geeft de problemen terug."""

    def __init__(self, browser: Any, server: Server):
        self.browser = browser
        self.server = server
        self._open: list[tuple[Any, Guard]] = []

    def new_context(self, **options: Any) -> Any:
        opts = {
            "base_url": self.server.url,
            "locale": "nl-NL",
            "timezone_id": "Europe/Amsterdam",
            "viewport": {"width": 1280, "height": 800},
            **options,
        }
        context = self.browser.new_context(**opts)
        context.set_default_timeout(20_000)
        guard = Guard()
        guard.attach(context)
        self._open.append((context, guard))
        return context

    def guard(self, context: Any) -> Guard:
        for ctx, guard in self._open:
            if ctx is context:
                return guard
        raise KeyError("onbekende context")

    def context_as(self, account: Account, **options: Any) -> Any:
        """Een context die als account is ingelogd: inloggen via context.request, zodat het
        refresh-cookie in de context zit (geen 2FA-code; die staat hier uit)."""
        context = self.new_context(**options)
        r = context.request.post(
            f"{self.server.url}/api/v1/auth/login",
            data={"email": account.email, "password": account.password},
        )
        if not r.ok:
            raise AssertionError(f"inloggen als {account.email} gaf {r.status}: {r.text()}")
        return context

    def close(self, context: Any) -> list[str]:
        for i, (ctx, guard) in enumerate(self._open):
            if ctx is context:
                del self._open[i]
                with contextlib.suppress(Exception):
                    ctx.close()
                return guard.take()
        return []

    def close_all(self) -> list[str]:
        problems: list[str] = []
        for ctx, _guard in list(self._open):
            problems.extend(self.close(ctx))
        return problems


# --- meten (gedeeld door e2e/perf) ---


def cdp_profile(
    context: Any,
    page: Any,
    *,
    latency_ms: float,
    down_mbit: float,
    up_mbit: float | None = None,
    cpu: float = 1,
) -> dict[str, Any]:
    """Netwerk- en CPU-vertraging via CDP. Geeft terug wat er echt is ingesteld."""
    session = context.new_cdp_session(page)
    session.send("Network.enable")
    up = down_mbit if up_mbit is None else up_mbit
    applied: dict[str, Any] = {"latency_ms": latency_ms, "down_mbit": down_mbit, "up_mbit": up}
    try:
        session.send(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": latency_ms,
                "downloadThroughput": down_mbit * 1_000_000 / 8,
                "uploadThroughput": up * 1_000_000 / 8,
            },
        )
        applied["network"] = True
    except Exception as e:  # noqa: BLE001 - een Chromium zonder dit commando meet toch
        applied["network"] = f"niet ingesteld: {e}"
    session.send("Emulation.setCPUThrottlingRate", {"rate": cpu})
    applied["cpu"] = cpu
    applied["session"] = session
    return applied


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank: bij 5 waarden is p95 de hoogste."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def report_only() -> bool:
    """E2E_PERF_REPORT_ONLY=1: meten en rapporteren, nooit falen op een poort (CI)."""
    return os.environ.get("E2E_PERF_REPORT_ONLY") == "1"


def write_result(name: str, result: dict[str, Any]) -> Path | None:
    """Naar $E2E_PERF_OUT/<name>.json als die map is opgegeven."""
    import json

    out = os.environ.get("E2E_PERF_OUT")
    if not out:
        return None
    path = Path(out) / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return path


@dataclass
class PerfData:
    """Wat de metingen gebruiken: een eigenaar met een map van n bestanden en een publieke link."""

    owner: Account
    folder_id: str
    public_url: str
    blobs: list[str]
    n: int


def prepare_perf(server: Server, admin: Account, n: int = 10_000, blobs: int = 2000) -> PerfData:
    import seed

    owner = create_user(server, admin, name="Prestatie")

    def fill(data_dir: Path) -> tuple[str, str, list[str]]:
        shas = seed.make_blobs(data_dir, blobs)
        folder = seed.seed_folder(data_dir, owner, n, shas, name=f"{n} bestanden")
        url = seed.seed_public(data_dir, owner, shas)
        return folder, url, shas

    folder, url, shas = server.seeded(fill)
    return PerfData(owner, folder, url, shas, n)


@contextlib.contextmanager
def standalone() -> Iterator[tuple[Server, Account, Browsers]]:
    """Voor de meetscripts buiten pytest: een eigen server, beheerder en browser."""
    from playwright.sync_api import sync_playwright

    keep = os.environ.get("E2E_KEEP_DATA") == "1"
    base = Path(tempfile.mkdtemp(prefix="platform-e2e-"))
    server = Server(base / "data")
    try:
        server.start()
        admin = make_admin(server)
        with sync_playwright() as pw:
            browser = launch_browser(pw)
            browsers = Browsers(browser, server)
            try:
                yield server, admin, browsers
            finally:
                browsers.close_all()
                browser.close()
    finally:
        server.stop()
        if keep:
            print(f"Data bewaard in {base}", file=sys.stderr)
        else:
            shutil.rmtree(base, ignore_errors=True)


# --- fixtures ---


def pytest_configure(config: pytest.Config) -> None:
    # Ook geregistreerd in pytest.ini; hier voor wie e2e met een andere ini draait
    config.addinivalue_line(
        "markers", "perf: prestatiemeting (e2e/perf); overslaan met -m 'not perf'"
    )


@pytest.fixture(scope="session")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    s = Server(tmp_path_factory.mktemp("platform") / "data")
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.fixture(scope="session")
def admin(server: Server) -> Account:
    return make_admin(server)


@pytest.fixture
def user_factory(server: Server, admin: Account) -> Callable[..., Account]:
    """user_factory(role="user", name=None, ...) → een nieuw Account (via POST /users)."""

    def make(role: str = "user", **kw: Any) -> Account:
        return create_user(server, admin, role, **kw)

    return make


@pytest.fixture(scope="session")
def browser() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = launch_browser(pw)
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def browsers(browser: Any, server: Server) -> Iterator[Browsers]:
    b = Browsers(browser, server)
    yield b
    problems = b.close_all()
    if problems:
        pytest.fail("Problemen in de browser:\n" + "\n".join(problems), pytrace=False)


@pytest.fixture
def new_context(browsers: Browsers) -> Callable[..., Any]:
    """new_context(**opties) → een nieuwe, niet ingelogde context (nl-NL, 1280×800)."""
    return browsers.new_context


@pytest.fixture
def context_as(browsers: Browsers) -> Callable[..., Any]:
    """context_as(account, **opties) → een context die als dat account is ingelogd."""
    return browsers.context_as


@pytest.fixture
def page(new_context: Callable[..., Any]) -> Any:
    return new_context().new_page()


@pytest.fixture
def seeded(server: Server) -> Callable[[Callable[[Path], Any]], Any]:
    """seeded(fn): server stoppen → fn(data_dir) → weer starten; geeft terug wat fn gaf."""
    return server.seeded


@pytest.fixture(scope="session")
def perf_data(server: Server, admin: Account) -> PerfData:
    return prepare_perf(server, admin)
