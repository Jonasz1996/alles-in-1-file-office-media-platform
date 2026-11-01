"""De enige weg naar buiten voor media (regel 9, Keuze 39): een eigen client met grenzen, een
token bucket per dienst, een stroomonderbreker per provider en geen privé-adressen.

- **Alleen de diensten uit de instellingen** (`PLATFORM_MEDIA_TMDB_URL`, `…_TMDB_IMAGE_URL`,
  `…_MUSICBRAINZ_URL`, `…_CAA_URL`), alleen https. Een adres buiten die basis weigert de client
  zonder verbinding.
- **Het adres zit in het verbinden.** De client zoekt de naam één keer op (`getaddrinfo` in een
  thread), controleert elk adres met `ipaddress` (ook een IPv4 in IPv6, 6to4 en Teredo), en
  verbindt met dát adres: het verzoek gaat naar `https://<ip>/…` met de kop `Host: <naam>` en de
  extensie `sni_hostname` van httpcore, zodat TLS het certificaat tegen de naam controleert.
  httpx zoekt dus zelf niets op. Is één adres privé, loopback, link-local, ULA, multicast of
  gereserveerd, dan geen verbinding. Alleen de tests zetten `PLATFORM_MEDIA_PROVIDER_PRIVATE_OK`
  (dan mag 127.0.0.1, en http).
- **Een client per hostnaam** (afwijking van "één client"): de pool van httpcore deelt
  verbindingen per adres. Twee namen op hetzelfde adres (een CDN) zouden anders een
  TLS-verbinding delen die voor de andere naam gecontroleerd is. Elke client: `trust_env=False`,
  geen eigen omleidingen, geen nieuwe pogingen.
- **Omleidingen** volgt de client zelf, hooguit 3, alleen naar `coverartarchive.org`,
  `archive.org` en `*.archive.org` (de CAA leidt om naar archive.org), met een nieuwe controle
  van het adres voor de nieuwe host. Bij een andere host gaat `Authorization` niet mee.
- **Grenzen:** JSON hooguit 2 MB, een plaatje hooguit 10 MB (eerst `Content-Length`, dan tellend
  tijdens het lezen, ook na gzip), een plaatje alleen als `image/jpeg`, `image/png` of
  `image/webp`. 10 s om te verbinden, 20 s in totaal (met de omleidingen).
- **429 en 503:** `Retry-After` (seconden of een datum) met een plafond van 60 s. Tot die tijd
  gaat er niets naar die dienst. Is het hooguit 2 s, dan wacht de client één keer zelf; anders
  `ProviderRate` (de jobs worden `Defer`, het koppelvenster 503 media_provider_rate).
  MusicBrainz geeft 503 zonder `Retry-After` als het te snel gaat: dan 2 s.
- **De stroomonderbreker:** na 5 fouten op rij (geen verbinding, time-out, 5xx, een kapot of te
  groot antwoord) 5 minuten dicht, dan één proef. 404, 401 en 429 tellen niet.
- **De log** toont alleen de dienst, de host, het pad zonder query, de status en de tijd. Ook
  de regel van httpx zelf ("HTTP Request: …") verliest zijn query (een filter op de logger
  `httpx`). Een fout zegt nooit meer dan de dienst en een vaste reden: nooit de tekst van een
  uitzondering van httpx, nooit een kop.

`get_json` en `get_image` gebruiken de client van het proces (gemaakt met de instellingen van de
app); tests maken een eigen `Outbound` met een nep-resolver, een transport en een nepklok.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import ipaddress
import json
import logging
import socket
import time
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio
import httpx

from app import __version__
from app.config import Settings
from app.media.providers.base import ProviderAuth, ProviderRate, ProviderUnavailable

log = logging.getLogger("platform.media.providers")

USER_AGENT = (
    f"alles-in-1-platform/{__version__} "
    "( https://github.com/Jonasz1996/alles-in-1-file-office-media-platform )"
)
MAX_JSON = 2 * 1024 * 1024
MAX_IMAGE = 10 * 1024 * 1024
CONNECT_TIMEOUT = 10.0
TOTAL_TIMEOUT = 20.0
MAX_REDIRECTS = 3
RETRY_CEILING = 60.0  # plafond van Retry-After
RETRY_DEFAULT = 2.0  # 429 of 503 zonder (bruikbare) Retry-After
INLINE_RETRY = 2.0  # zo lang wacht de client zelf, één keer
BREAKER_FAILURES = 5
BREAKER_OPEN = 300.0
DNS_TTL = 30.0
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
REDIRECT_HOSTS = ("coverartarchive.org", "archive.org")  # en *.archive.org
REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
PROVIDERS = ("tmdb", "musicbrainz", "caa")
ULA = ipaddress.ip_network("fc00::/7")
# Verzoeken per seconde per dienst (Keuze 39), en de burst. MusicBrainz: 1 per seconde, met een
# marge, zodat ook met wat vertraging onderweg nooit twee binnen een seconde aankomen.
RATES: dict[str, tuple[float, float]] = {
    "tmdb": (10.0, 10.0),
    "tmdb_image": (10.0, 10.0),
    "musicbrainz": (1 / 1.1, 1.0),
    "caa": (5.0, 5.0),
    "archive": (5.0, 5.0),
}

Resolver = Callable[[str, int], Awaitable[list[str]]]
Kind = Literal["json", "image"]
Breaker = Literal["closed", "open", "half_open"]


class NotFound(Exception):  # noqa: N818 - een uitkomst, geen fout van de provider
    """404 bij de provider: dit id of dit plaatje bestaat daar niet."""


class _Rate(Exception):  # noqa: N818
    def __init__(self, seconds: float):
        super().__init__(f"{seconds:g} s")
        self.seconds = seconds


class _Failure(Exception):  # noqa: N818
    """Telt mee voor de stroomonderbreker; `reason` is een vaste, veilige tekst."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _Refused(Exception):  # noqa: N818
    """Wij weigeren (adres, omleiding, host, status): telt niet mee."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Service:
    """Eén dienst uit de instellingen: waar hij is, en bij welke provider hij hoort."""

    name: str
    provider: str
    scheme: str
    host: str
    port: int
    prefix: str

    @classmethod
    def parse(cls, name: str, provider: str, base: str) -> Service | None:
        try:
            url = httpx.URL(base)
        except (httpx.InvalidURL, TypeError):
            return None
        if url.scheme not in ("http", "https") or not url.host:
            return None
        port = url.port or default_port(url.scheme)
        return cls(name, provider, url.scheme, url.host.lower(), port, url.path.rstrip("/"))

    def owns(self, url: httpx.URL) -> bool:
        if url.scheme != self.scheme or (url.host or "").lower() != self.host:
            return False
        if (url.port or default_port(url.scheme)) != self.port:
            return False
        path = url.path
        return not self.prefix or path == self.prefix or path.startswith(self.prefix + "/")


@dataclass
class Fetched:
    """Een antwoord: de body (binnen de grens), het type, het adres na de omleidingen, en bij
    JSON het object."""

    status: int
    body: bytes
    content_type: str
    url: str
    data: dict[str, Any] | None = None


@dataclass
class Health:
    """De staat van een provider, voor /beheer en de melding na 24 uur (M10)."""

    failures: int = 0
    breaker: Breaker = "closed"
    opened_at: float | None = None
    trial: bool = False
    last_ok: dt.datetime | None = None
    last_error: str | None = None
    last_error_at: dt.datetime | None = None
    failing_since: dt.datetime | None = None
    auth_failed: bool = False

    def view(self) -> dict[str, Any]:
        return {
            "breaker": self.breaker,
            "failures": self.failures,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "failing_since": self.failing_since,
            "auth_failed": self.auth_failed,
        }


@dataclass
class _Bucket:
    rate: float
    capacity: float
    tokens: float
    updated: float
    blocked_until: float = 0.0
    lock: anyio.Lock = field(default_factory=anyio.Lock)


def default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


# --- adressen ---------------------------------------------------------------------------------


def _unwrap(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> list[Any]:
    """Het adres en wat erin verpakt zit (IPv4 in IPv6, 6to4, Teredo)."""
    out: list[Any] = [address]
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            out.append(address.ipv4_mapped)
        if address.sixtofour is not None:
            out.append(address.sixtofour)
        if address.teredo is not None:
            out.extend(address.teredo)
    return out


def address_ok(text: str, *, private_ok: bool = False) -> bool:
    """Mag de client met dit adres verbinden? Alleen een publiek unicast-adres; met private_ok
    (alleen tests) ook privé en loopback, maar nooit 0.0.0.0, multicast of een scope-id."""
    if "%" in text:
        return False
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    for a in _unwrap(address):
        if a.is_unspecified or a.is_multicast:
            return False
        if private_ok:
            continue
        if not a.is_global or a.is_private or a.is_loopback or a.is_link_local or a.is_reserved:
            return False
        if isinstance(a, ipaddress.IPv6Address) and a in ULA:
            return False
    return True


async def system_resolve(host: str, port: int) -> list[str]:
    """getaddrinfo in een thread; de adressen in de volgorde van het systeem, zonder dubbele."""

    def lookup() -> list[str]:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return list(dict.fromkeys(str(info[4][0]) for info in infos))

    return await anyio.to_thread.run_sync(lookup)


def redirect_host_ok(host: str) -> bool:
    host = host.lower().rstrip(".")
    return host in REDIRECT_HOSTS or host.endswith(".archive.org")


def retry_after(value: str | None) -> float:
    """Retry-After in seconden (een getal of een HTTP-datum), tussen 0 en het plafond; zonder of
    onleesbaar: RETRY_DEFAULT."""
    if not value:
        return RETRY_DEFAULT
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return RETRY_DEFAULT
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.UTC)
        seconds = (when - dt.datetime.now(dt.UTC)).total_seconds()
    if seconds != seconds:  # NaN
        return RETRY_DEFAULT
    return min(max(seconds, 0.0), RETRY_CEILING)


# --- de log van httpx -------------------------------------------------------------------------


class NoQuery(logging.Filter):
    """De regel "HTTP Request: GET <url> …" van httpx zonder query (Keuze 39)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 2:
            url = record.args[1]
            if isinstance(url, httpx.URL) and url.query:
                args = list(record.args)
                args[1] = url.copy_with(query=None)
                record.args = tuple(args)
        return True


if not any(isinstance(f, NoQuery) for f in logging.getLogger("httpx").filters):
    logging.getLogger("httpx").addFilter(NoQuery())


# --- de client --------------------------------------------------------------------------------


def services(settings: Settings) -> list[Service]:
    """De diensten uit de instellingen (een basis die geen http(s)-adres is, telt niet)."""
    wanted = [
        ("tmdb", "tmdb", settings.media_tmdb_url),
        ("tmdb_image", "tmdb", settings.media_tmdb_image_url),
        ("musicbrainz", "musicbrainz", settings.media_musicbrainz_url),
        ("caa", "caa", settings.media_caa_url),
    ]
    out = []
    for name, provider, base in wanted:
        service = Service.parse(name, provider, base)
        if service is not None:
            out.append(service)
    return out


class Outbound:
    """De client van media naar buiten. Eén per proces (zie `client()`); tests maken er een met
    een nep-resolver, een transport en een nepklok."""

    def __init__(
        self,
        settings: Settings,
        *,
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        verify: Any = True,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
        sleep: Callable[[float], Awaitable[Any]] = anyio.sleep,
        total_timeout: float = TOTAL_TIMEOUT,
    ) -> None:
        self.settings = settings
        self.private_ok = bool(settings.media_provider_private_ok)
        self.services = services(settings)
        self._resolver = resolver or system_resolve
        self._transport = transport
        self._verify = verify
        self.clock = clock
        self.wall = wall
        self.sleep = sleep
        self.total_timeout = total_timeout
        self._clients: dict[tuple[str, str], httpx.AsyncClient] = {}
        self._dns: dict[tuple[str, int], tuple[list[str], float]] = {}
        self._buckets: dict[str, _Bucket] = {}
        self.health: dict[str, Health] = {name: Health() for name in PROVIDERS}

    # --- openen en sluiten ---

    def _client(self, scheme: str, host: str) -> httpx.AsyncClient:
        key = ("*", "*") if self._transport is not None else (scheme, host)
        client = self._clients.get(key)
        if client is None:
            transport = self._transport or httpx.AsyncHTTPTransport(verify=self._verify, retries=0)
            client = httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(
                    connect=CONNECT_TIMEOUT,
                    read=self.total_timeout,
                    write=self.total_timeout,
                    pool=CONNECT_TIMEOUT,
                ),
                follow_redirects=False,
                trust_env=False,
            )
            self._clients[key] = client
        return client

    async def aclose(self) -> None:
        clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 - sluiten mag niet falen
                log.debug("client sluiten", exc_info=True)

    # --- diensten en adressen ---

    def service_for(self, provider: str, url: httpx.URL) -> Service:
        for service in self.services:
            if service.provider == provider and service.owns(url):
                if service.scheme != "https" and not self.private_ok:
                    raise _Refused("geen https")
                return service
        raise _Refused("host niet toegestaan")

    async def resolve(self, host: str, port: int) -> str:
        """Eén adres om mee te verbinden, na de controle van elk adres dat de naam geeft."""
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            cached = self._dns.get((host, port))
            if cached is not None and cached[1] > self.clock():
                addresses = cached[0]
            else:
                try:
                    addresses = list(await self._resolver(host, port))
                except (OSError, UnicodeError) as exc:
                    raise _Failure("naam niet gevonden") from exc
                self._dns[(host, port)] = (addresses, self.clock() + DNS_TTL)
        if not addresses:
            raise _Failure("naam niet gevonden")
        if any(not address_ok(a, private_ok=self.private_ok) for a in addresses):
            self._dns.pop((host, port), None)
            raise _Refused("adres niet toegestaan")
        return addresses[0]

    # --- het tempo ---

    def _bucket(self, name: str) -> _Bucket:
        bucket = self._buckets.get(name)
        if bucket is None:
            rate, capacity = RATES.get(name, (1.0, 1.0))
            bucket = self._buckets[name] = _Bucket(rate, capacity, capacity, self.clock())
        return bucket

    def block(self, name: str, seconds: float) -> None:
        """Na een Retry-After: tot dan niets naar deze dienst."""
        bucket = self._bucket(name)
        bucket.blocked_until = max(bucket.blocked_until, self.clock() + seconds)

    async def take(self, name: str) -> None:
        """Een plek in de token bucket van deze dienst (wachten tot er een is). Staat de dienst
        stil na een Retry-After: wachten als dat hooguit INLINE_RETRY duurt, anders
        ProviderRate."""
        bucket = self._bucket(name)
        async with bucket.lock:
            blocked = bucket.blocked_until - self.clock()
            if blocked > 0:
                if blocked > INLINE_RETRY:
                    raise ProviderRate(round(blocked, 1))
                await self.sleep(blocked)
            while True:
                now = self.clock()
                bucket.tokens = min(
                    bucket.capacity, bucket.tokens + (now - bucket.updated) * bucket.rate
                )
                bucket.updated = now
                if bucket.tokens >= 1.0:
                    bucket.tokens -= 1.0
                    return
                await self.sleep((1.0 - bucket.tokens) / bucket.rate)

    # --- de stroomonderbreker ---

    def _admit(self, provider: str) -> None:
        """Mag er een verzoek naar deze provider? Na 5 minuten open: precies één proef."""
        health = self.health[provider]
        if health.breaker == "open":
            if health.opened_at is not None and self.clock() - health.opened_at < BREAKER_OPEN:
                raise ProviderUnavailable(f"{provider}: de stroomonderbreker staat open")
            health.breaker = "half_open"
        if health.breaker == "half_open":
            if health.trial:
                raise ProviderUnavailable(f"{provider}: de stroomonderbreker test nog")
            health.trial = True

    def _ok(self, provider: str) -> None:
        health = self.health[provider]
        health.failures = 0
        health.breaker = "closed"
        health.opened_at = None
        health.trial = False
        health.last_ok = self.wall()
        health.failing_since = None
        health.auth_failed = False

    def _fail(self, provider: str, reason: str) -> None:
        health = self.health[provider]
        health.trial = False
        health.failures += 1
        health.last_error = reason
        health.last_error_at = self.wall()
        if health.failing_since is None:
            health.failing_since = health.last_error_at
        if health.breaker == "half_open" or health.failures >= BREAKER_FAILURES:
            health.breaker = "open"
            health.opened_at = self.clock()

    def _release(self, provider: str) -> None:
        self.health[provider].trial = False

    # --- verzoeken ---

    async def fetch(
        self,
        provider: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        kind: Kind = "json",
    ) -> Fetched:
        """GET met alle regels van boven. ProviderAuth, ProviderRate, ProviderUnavailable of
        NotFound als het niet lukt."""
        if provider not in self.health:
            raise ProviderUnavailable(f"{provider}: onbekende provider")
        try:
            target = httpx.URL(url, params=params) if params else httpx.URL(url)
            service = self.service_for(provider, target)
        except (httpx.InvalidURL, TypeError, ValueError):
            raise ProviderUnavailable(f"{provider}: geen geldig adres") from None
        except _Refused as exc:
            raise ProviderUnavailable(f"{provider}: {exc.reason}") from None
        self._admit(provider)
        try:
            for attempt in (0, 1):
                await self.take(service.name)
                try:
                    fetched = await self._follow(service, target, headers or {}, kind)
                except _Rate as rate:
                    self.block(service.name, rate.seconds)
                    if attempt == 0 and rate.seconds <= INLINE_RETRY:
                        continue
                    raise ProviderRate(round(rate.seconds, 1)) from None
                self._ok(provider)
                return fetched
            raise AssertionError("onbereikbaar")  # pragma: no cover
        except NotFound:
            self._ok(provider)
            raise
        except _Failure as exc:
            self._fail(provider, f"{service.name}: {exc.reason}")
            raise ProviderUnavailable(f"{provider}: {exc.reason}") from None
        except _Refused as exc:
            self._release(provider)
            raise ProviderUnavailable(f"{provider}: {exc.reason}") from None
        except ProviderAuth:
            self._release(provider)
            self.health[provider].auth_failed = True
            raise
        except BaseException:
            self._release(provider)
            raise

    async def _follow(
        self, service: Service, url: httpx.URL, headers: dict[str, str], kind: Kind
    ) -> Fetched:
        """Het verzoek, met de omleidingen, binnen de totale tijd."""
        current = service
        first_host = url.host
        try:
            with anyio.fail_after(self.total_timeout):
                for _ in range(MAX_REDIRECTS + 1):
                    sent = dict(headers)
                    if url.host != first_host:
                        sent.pop("Authorization", None)
                    result = await self._one(current, url, sent, kind)
                    if isinstance(result, Fetched):
                        return result
                    url = result
                    host = (url.host or "").lower()
                    if url.scheme not in ("http", "https"):
                        raise _Refused("omleiding naar een vreemde host")
                    if not (redirect_host_ok(host) or self._test_redirect(url)):
                        raise _Refused("omleiding naar een vreemde host")
                    if url.scheme != "https" and not self.private_ok:
                        raise _Refused("omleiding zonder https")
                    current = Service(
                        "archive" if current.provider == "caa" else current.name,
                        current.provider,
                        url.scheme,
                        host,
                        url.port or default_port(url.scheme),
                        "",
                    )
                    await self.take(current.name)
                raise _Refused("te veel omleidingen")
        except TimeoutError:
            raise _Failure("time-out") from None

    def _test_redirect(self, url: httpx.URL) -> bool:
        """Alleen met PLATFORM_MEDIA_PROVIDER_PRIVATE_OK (de nep als proces): een omleiding naar
        localhost of naar een dienst uit de instellingen."""
        if not self.private_ok:
            return False
        host = (url.host or "").lower()
        return host == "localhost" or any(s.host == host for s in self.services)

    async def _one(
        self, service: Service, url: httpx.URL, headers: dict[str, str], kind: Kind
    ) -> Fetched | httpx.URL:
        """Eén verzoek naar het gecontroleerde adres. Geeft het antwoord, of het volgende adres
        bij een omleiding."""
        host = (url.host or "").lower()
        port = url.port or default_port(url.scheme)
        address = await self.resolve(host, port)
        pinned = url.copy_with(host=address)
        sent = {
            **headers,
            "Host": host if port == default_port(url.scheme) else f"{host}:{port}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json" if kind == "json" else "image/jpeg, image/png, image/webp",
            "Accept-Encoding": "gzip" if kind == "json" else "identity",
        }
        extensions: dict[str, Any] = {}
        if url.scheme == "https":
            extensions["sni_hostname"] = host
        started = time.monotonic()
        status = 0
        client = self._client(url.scheme, host)
        try:
            async with client.stream("GET", pinned, headers=sent, extensions=extensions) as r:
                status = r.status_code
                if status in REDIRECT_STATUS:
                    location = r.headers.get("location")
                    if not location:
                        raise _Failure("omleiding zonder adres")
                    try:
                        return url.join(location)
                    except (httpx.InvalidURL, ValueError):
                        raise _Failure("omleiding naar een ongeldig adres") from None
                self._status(service, r)
                content_type = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind == "image":
                    if content_type not in IMAGE_TYPES:
                        raise _Failure("verkeerd type")
                    body = await read_limited(r, MAX_IMAGE)
                    return Fetched(status, body, content_type, str(url))
                body = await read_limited(r, MAX_JSON)
                return Fetched(status, body, content_type, str(url), parse_json(body))
        except httpx.TimeoutException:
            raise _Failure("time-out") from None
        except (httpx.HTTPError, OSError):
            raise _Failure("geen verbinding") from None
        finally:
            ms = round((time.monotonic() - started) * 1000)
            log.info("provider %s %s%s %s %d ms", service.name, host, url.path[:200], status, ms)

    def _status(self, service: Service, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 401:
            raise ProviderAuth(f"{service.provider}: de sleutel werkt niet")
        if status == 404:
            raise NotFound(f"{service.provider}: niet gevonden")
        if status == 429:
            raise _Rate(retry_after(response.headers.get("retry-after")))
        if status == 503 and (
            service.provider == "musicbrainz" or response.headers.get("retry-after")
        ):
            raise _Rate(retry_after(response.headers.get("retry-after")))
        if status >= 500:
            raise _Failure(f"status {status}")
        raise _Refused(f"status {status}")


async def read_limited(response: httpx.Response, limit: int) -> bytes:
    """De body, nooit meer dan limit bytes (ook niet na gzip). Te groot: _Failure."""
    length = response.headers.get("content-length", "")
    if length.strip().isdigit() and int(length) > limit:
        raise _Failure("te groot")
    if response.is_stream_consumed:
        # Een antwoord dat al in het geheugen staat (httpx.MockTransport in tests).
        if len(response.content) > limit:
            raise _Failure("te groot")
        return response.content
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding in ("", "identity"):
        decoder = None
    elif encoding in ("gzip", "x-gzip"):
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decoder = zlib.decompressobj()
    else:
        raise _Failure("onbekende codering")
    out = bytearray()
    try:
        async for chunk in response.aiter_raw():
            if decoder is not None:
                chunk = decoder.decompress(chunk, limit - len(out) + 1)
            out += chunk
            if len(out) > limit:
                raise _Failure("te groot")
        if decoder is not None:
            out += decoder.flush()
            if len(out) > limit:
                raise _Failure("te groot")
    except zlib.error:
        raise _Failure("kapot antwoord") from None
    return bytes(out)


def parse_json(body: bytes) -> dict[str, Any]:
    """Een JSON-object, of _Failure (geen JSON, te diep, geen object)."""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError, UnicodeDecodeError):
        raise _Failure("kapot antwoord") from None
    if not isinstance(data, dict):
        raise _Failure("kapot antwoord")
    return data


# --- de client van het proces -----------------------------------------------------------------

_outbound: Outbound | None = None


def client() -> Outbound:
    """De client van dit proces, met de instellingen van de app (tenzij een test er een zette
    met `use`)."""
    global _outbound
    if _outbound is None:
        from app.media import runtime

        _outbound = Outbound(runtime.settings())
    return _outbound


def use(outbound: Outbound | None) -> Outbound | None:
    """Tests: deze client gebruiken (None: de volgende keer een nieuwe). Geeft de vorige."""
    global _outbound
    previous, _outbound = _outbound, outbound
    return previous


def current() -> Outbound | None:
    """De client als hij er al is (voor de status op /beheer; maakt er geen)."""
    return _outbound


async def get_json(
    provider: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    """GET van een JSON-object bij een provider (alle regels van boven)."""
    fetched = await client().fetch(provider, url, params=params, headers=headers, kind="json")
    return fetched.data or {}


async def get_image(provider: str, url: str) -> Fetched:
    """GET van een plaatje (jpeg, png of webp, hooguit 10 MB), met de omleidingen van de CAA."""
    return await client().fetch(provider, url, kind="image")
