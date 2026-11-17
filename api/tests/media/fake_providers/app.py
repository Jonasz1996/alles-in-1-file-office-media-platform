"""De kern van de nep-providers (§3.6, §4.4): één Starlette-app voor vijf hosts.

- **Hosts.** Op de kop `Host`: `api.themoviedb.org` (TMDB), `image.tmdb.org` (plaatjes),
  `musicbrainz.org`, `coverartarchive.org` (CAA) en `archive.org` (ook `*.archive.org`, waar de
  CAA naar omleidt). Is de host onbekend (zoals `127.0.0.1:P` in de e2e), dan beslist het
  voorvoegsel van het pad: `/tmdb`, `/tmdb-img`, `/mb`, `/caa` en `/archive`.
- **Fouten op bestelling:** `POST /__fault {path, kind, times, host?, …}` (zie `Fault`).
- **Logboek:** `GET /__log`: methode, host, pad, query (een `api_key` als `***`), koppen zonder
  `Authorization` en `Cookie` (wel of er een Bearer was, en of hij klopte), de status en de tijd.
  Om de User-Agent en het tempo te controleren. `DELETE /__log` maakt het leeg.
- **Data** uit `data.py` (M8 vult hem).

Twee standen: in het proces (`httpx.ASGITransport(app=create_app(...))`, voor de API-tests) en
als proces (`python -m tests.media.fake_providers`, zie `process.py`, voor de e2e).
"""

from __future__ import annotations

import asyncio
import fnmatch
import hmac
import itertools
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from . import musicbrainz, tmdb
from .data import Data, load
from .images import Images

SERVICES = ("tmdb", "image", "musicbrainz", "caa", "archive")
HOSTS = {
    "api.themoviedb.org": "tmdb",
    "image.tmdb.org": "image",
    "musicbrainz.org": "musicbrainz",
    "coverartarchive.org": "caa",
    "archive.org": "archive",
}
PREFIXES = {
    "/tmdb-img": "image",
    "/tmdb": "tmdb",
    "/mb": "musicbrainz",
    "/caa": "caa",
    "/archive": "archive",
}
# De echte adressen per dienst, voor in het proces (ASGITransport kiest de app op de host)
BASE_URLS = {
    "tmdb": "https://api.themoviedb.org",
    "image": "https://image.tmdb.org",
    "musicbrainz": "https://musicbrainz.org",
    "caa": "https://coverartarchive.org",
    "archive": "https://archive.org",
}
FAULTS = ("500", "429", "slow", "garbage", "huge", "wrongtype", "redirect_evil", "drop")
HIDDEN_HEADERS = {"authorization", "cookie", "proxy-authorization"}
SECRET_QUERY = {"api_key"}
JSON_SERVICES = {"tmdb", "musicbrainz"}


@dataclass
class FakeConfig:
    """- `tmdb_key`: het "API Read Access Token" dat de nep aanneemt; None: elke Bearer is fout;
    - `data`: None leest `fake_providers/data/`;
    - `image_url`: de basis van de plaatjes in `/3/configuration` (`…/t/p/`);
    - `archive_url`: waar de CAA naar omleidt (een tweede hostnaam);
    - `evil_url`: het doel van de fout `redirect_evil`;
    - `tmdb_rate` en `musicbrainz_rate`: verzoeken per seconde, daarboven 429 (TMDB) of 503
      (MusicBrainz); `rate_limits=False` zet beide uit;
    - `clock`: de klok van de grenzen (tests geven een nepklok); `sleep`: de pauze van `slow`.
    """

    tmdb_key: str | None = None
    data: Data | None = None
    image_url: str = "https://image.tmdb.org"
    archive_url: str = "https://archive.org"
    evil_url: str = "https://evil.example/steal"
    tmdb_rate: int = 20
    musicbrainz_rate: int = 1
    rate_limits: bool = True
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep


@dataclass
class Fault:
    """Een fout op bestelling. `path` is een patroon (fnmatch) op het pad bij de host, dus
    zonder het voorvoegsel: `/3/search/movie`, `/3/movie/*`, `/t/p/w500/*`. `host` beperkt hem tot
    één dienst (`tmdb`, `image`, `musicbrainz`, `caa`, `archive`, of hun hostnaam). `times`: zo
    vaak, daarna is hij weg; None tot `DELETE /__fault`.

    - `500`: een 500 met een JSON-body;
    - `429`: 429 met `Retry-After: retry_after`;
    - `slow`: `seconds` (standaard 10) wachten, dan het gewone antwoord;
    - `garbage`: 200 met een body die geen JSON (of geen plaatje) is;
    - `huge`: 200, `image/jpeg`, `size_mb` (standaard 50) MB;
    - `wrongtype`: 200 met `text/html`;
    - `redirect_evil`: 302 naar `location` (standaard `evil_url`);
    - `drop`: de verbinding gaat dicht midden in het antwoord. In het proces geeft httpx dan
      `RemoteProtocolError`; in de stand met ASGITransport dezelfde uitzondering.
    """

    kind: str
    path: str = "*"
    host: str | None = None
    times: int | None = 1
    seconds: float = 10.0
    retry_after: float = 1.0
    size_mb: int = 50
    location: str | None = None
    id: int = 0
    hits: int = 0

    def matches(self, service: str, path: str) -> bool:
        return (self.host is None or self.host == service) and fnmatch.fnmatchcase(path, self.path)


@dataclass
class FakeProviders:
    config: FakeConfig
    data: Data
    images: Images = field(default_factory=Images)
    faults: list[Fault] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)
    windows: dict[str, deque[float]] = field(default_factory=dict)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    # --- fouten ---

    def fault(self, kind: str, path: str = "*", **options: Any) -> Fault:
        """Een fout op bestelling (zie `Fault`); geeft hem terug, met zijn id."""
        if kind not in FAULTS:
            raise ValueError(f"onbekende fout {kind!r}; kies uit {', '.join(FAULTS)}")
        host = options.pop("host", None)
        if host is not None:
            host = HOSTS.get(host, host)
            if host not in SERVICES:
                raise ValueError(f"onbekende host {host!r}")
        unknown = set(options) - {"times", "seconds", "retry_after", "size_mb", "location"}
        if unknown:
            raise ValueError(f"onbekende opties {sorted(unknown)}")
        times = options.get("times", 1)
        if times is not None and (not isinstance(times, int) or times < 1):
            raise ValueError("times is een getal ≥ 1, of null (tot DELETE /__fault)")
        if not isinstance(path, str) or not path.startswith(("/", "*")):
            raise ValueError("path begint met / (of is *)")
        f = Fault(kind=kind, path=path, host=host, id=next(self._ids), **options)
        self.faults.append(f)
        return f

    def take_fault(self, service: str, path: str) -> Fault | None:
        for f in self.faults:
            if f.matches(service, path):
                f.hits += 1
                if f.times is not None and f.hits >= f.times:
                    self.faults.remove(f)
                return f
        return None

    def clear_faults(self) -> None:
        self.faults.clear()

    # --- het tempo ---

    def over_rate(self, service: str, per_second: int) -> int | None:
        """Telt dit verzoek mee in het venster van 1 s. Geeft het aantal als het boven de grens
        komt, anders None. Ook geweigerde verzoeken tellen (zoals bij de echte diensten)."""
        if not self.config.rate_limits:
            return None
        now = self.config.clock()
        window = self.windows.setdefault(service, deque())
        while window and window[0] <= now - 1.0:
            window.popleft()
        window.append(now)
        return len(window) if len(window) > per_second else None

    def reset(self) -> None:
        self.faults.clear()
        self.log.clear()
        self.windows.clear()

    # --- het logboek ---

    def record(self, request: Request, service: str | None, path: str, status: int, fault):
        headers = {k: v for k, v in request.headers.items() if k not in HIDDEN_HEADERS}
        auth = request.headers.get("authorization")
        bearer = auth[7:] if auth and auth.startswith("Bearer ") else None
        self.log.append(
            {
                "t": time.time(),
                "service": service,
                "host": request.headers.get("host", ""),
                "method": request.method,
                "path": request.url.path,
                "inner": path,
                "query": _mask(request.url.query),
                "headers": headers,
                "auth": None if auth is None else ("bearer" if bearer is not None else "other"),
                "auth_ok": bearer is not None and self.key_ok(bearer),
                "status": status,
                "fault": fault,
            }
        )

    def key_ok(self, token: str) -> bool:
        key = self.config.tmdb_key
        return key is not None and hmac.compare_digest(token.encode(), key.encode())


def _mask(query: str) -> str:
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    return urlencode([(k, "***" if k in SECRET_QUERY else v) for k, v in pairs])


def route(request: Request) -> tuple[str | None, str]:
    """(dienst, pad bij die dienst) uit de host, en anders uit het voorvoegsel."""
    host = request.headers.get("host", "").lower()
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    path = request.url.path
    service = HOSTS.get(name)
    if service is None and name.endswith(".archive.org"):
        service = "archive"
    if service is not None:
        return service, path
    for prefix, service in PREFIXES.items():
        if path == prefix or path.startswith(prefix + "/"):
            return service, path[len(prefix) :] or "/"
    return None, path


class Dropped(httpx.RemoteProtocolError):
    """De fout `drop`: in het proces dezelfde soort als wat httpx dan zelf geeft."""


class _Drop:
    """Een antwoord dat halverwege afbreekt: de kop en een stuk van de body, dan dicht."""

    async def __call__(self, scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json"), (b"content-length", b"4096")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"nep": ', "more_body": True})
        raise Dropped("nep: de verbinding ging dicht")


async def _huge(size_mb: int):
    chunk = b"\0" * (1024 * 1024)
    yield b"\xff\xd8\xff\xe0" + chunk[4:]
    for _ in range(size_mb - 1):
        yield chunk


async def apply_fault(fake: FakeProviders, f: Fault, service: str) -> Response | None:
    """Het antwoord van een fout; None bij `slow` (na de pauze het gewone antwoord)."""
    if f.kind == "500":
        body = (
            {"status_code": 11, "status_message": "Internal error: nep.", "success": False}
            if service == "tmdb"
            else {"error": "nep: 500"}
        )
        return JSONResponse(body, status_code=500)
    if f.kind == "429":
        seconds = f.retry_after
        retry = str(int(seconds)) if float(seconds).is_integer() else str(seconds)
        headers = {"Retry-After": retry}
        return JSONResponse({"error": "nep: te veel"}, status_code=429, headers=headers)
    if f.kind == "slow":
        await fake.config.sleep(f.seconds)
        return None
    if f.kind == "garbage":
        if service in JSON_SERVICES:
            return Response(b'{"nep": geen json', media_type="application/json")
        return Response(os.urandom(512), media_type="image/jpeg")
    if f.kind == "huge":
        return StreamingResponse(
            _huge(f.size_mb),
            media_type="image/jpeg",
            headers={"Content-Length": str(f.size_mb * 1024 * 1024)},
        )
    if f.kind == "wrongtype":
        return Response("<html><body>nep</body></html>", media_type="text/html")
    if f.kind == "redirect_evil":
        return Response(status_code=302, headers={"Location": f.location or fake.config.evil_url})
    if f.kind == "drop":
        return _Drop()  # type: ignore[return-value]
    raise AssertionError(f.kind)


HANDLERS = {
    "tmdb": tmdb.api,
    "image": tmdb.image,
    "musicbrainz": musicbrainz.api,
    "caa": musicbrainz.caa,
    "archive": musicbrainz.archive,
}


async def _control(fake: FakeProviders, request: Request) -> Response:
    path = request.url.path
    if path == "/__health" and request.method == "GET":
        return JSONResponse({"ok": True, **fake.data.counts()})
    if path == "/__log":
        if request.method == "GET":
            return JSONResponse({"entries": fake.log})
        if request.method == "DELETE":
            fake.log.clear()
            return Response(status_code=204)
    if path == "/__reset" and request.method == "POST":
        fake.reset()
        return Response(status_code=204)
    if path == "/__fault":
        if request.method == "GET":
            return JSONResponse({"faults": [asdict(f) for f in fake.faults]})
        if request.method == "DELETE":
            fake.clear_faults()
            return Response(status_code=204)
        if request.method == "POST":
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("verwacht een JSON-object")
                body = dict(body)
                kind = body.pop("kind", None)
                f = fake.fault(str(kind), body.pop("path", "*"), **body)
            except (ValueError, TypeError) as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            return JSONResponse({"id": f.id}, status_code=201)
    if path.startswith("/__fault/") and request.method == "DELETE":
        wanted = path.removeprefix("/__fault/")
        before = len(fake.faults)
        fake.faults[:] = [f for f in fake.faults if str(f.id) != wanted]
        return Response(status_code=204 if len(fake.faults) < before else 404)
    return JSONResponse({"error": "nep: onbekend stuurpad"}, status_code=404)


def create_app(config: FakeConfig | None = None) -> Starlette:
    config = config or FakeConfig()
    fake = FakeProviders(config=config, data=config.data if config.data is not None else load())

    async def dispatch(request: Request) -> Response:
        if request.url.path.startswith("/__"):
            return await _control(fake, request)
        service, path = route(request)
        if service is None:
            response: Response = JSONResponse(
                {"error": "nep: onbekende host en geen voorvoegsel"}, status_code=404
            )
            fake.record(request, None, path, response.status_code, None)
            return response
        f = fake.take_fault(service, path)
        response = None
        if f is not None:
            response = await apply_fault(fake, f, service)
        if response is None:
            response = await HANDLERS[service](fake, request, path)
        status = getattr(response, "status_code", 200)
        fake.record(request, service, path, status, f.kind if f else None)
        return response

    methods = ["GET", "HEAD", "POST", "PUT", "DELETE"]
    app = Starlette(routes=[Route("/{path:path}", dispatch, methods=methods)])
    app.state.fake = fake
    return app


def transport(app: Starlette) -> httpx.ASGITransport:
    """De stand in het proces: elke host gaat naar dezelfde app (op de kop `Host`)."""
    return httpx.ASGITransport(app=app)
