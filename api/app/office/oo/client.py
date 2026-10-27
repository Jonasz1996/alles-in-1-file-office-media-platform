"""De enige weg van de app naar de document server (regel 7 van §2.0).

Eén httpx-client per proces: trust_env=False (geen proxy uit de omgeving), geen omleidingen,
tijdslimieten op alles. Een adres uit een antwoord van DS volgt hij nooit: fetch neemt alleen
een adres dat al herschreven is naar PLATFORM_OFFICE_URL (urls.rewrite).

Tests zetten `transport` (een httpx.MockTransport of de nep-DS via ASGITransport) vóór de
eerste get(), of maken zelf een DocServer met een transport.
"""

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import anyio
import httpx

from app.config import Settings
from app.office import runtime
from app.office.oo import tokens

# Voor tests: het transport van de volgende client
transport: httpx.AsyncBaseTransport | None = None

TIMEOUT = httpx.Timeout(10.0, read=30.0)
HEALTH_TIMEOUT = httpx.Timeout(5.0)
CHUNK = 64 * 1024


class DocServerError(Exception):
    """DS antwoordde niet, niet met 200, of niet met wat we verwachtten."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TooLarge(DocServerError):
    """Een download werd groter dan de grens; er is niets bewaard."""


def _json(response: httpx.Response) -> dict:
    # DS geeft een fout soms als kale tekst ('{"error":6}'), zonder JSON-type
    try:
        data = json.loads(response.content)
    except ValueError as exc:
        raise DocServerError("geen JSON", response.status_code) from exc
    if not isinstance(data, dict):
        raise DocServerError("geen object", response.status_code)
    return data


def _sharded(path: str, params: dict) -> str:
    """?shardkey=<key> erbij als er een key is: zo blijft een document op één DS (A18)."""
    if isinstance(params.get("key"), str):
        return path + "?shardkey=" + quote(params["key"], safe="")
    return path


async def _remove(path: Path) -> None:
    with anyio.CancelScope(shield=True):
        await anyio.Path(path).unlink(missing_ok=True)


class DocServer:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.base = settings.office_url.rstrip("/")
        self._base_path = httpx.URL(self.base).raw_path.rstrip(b"/")
        self.client = httpx.AsyncClient(
            base_url=self.base,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=TIMEOUT,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def healthy(self) -> bool:
        """GET /healthcheck geeft precies "true"."""
        try:
            r = await self.client.get("/healthcheck", timeout=HEALTH_TIMEOUT)
        except httpx.HTTPError:
            return False
        return r.status_code == 200 and r.text.strip() == "true"

    async def command(self, c: str, *, signed: bool = True, **params) -> dict:
        """POST /command?shardkey=<key>. Het antwoord als dict ({"error": 0, ...})."""
        body = {"c": c, **params}
        if signed:
            body = tokens.command_body(self.settings, body)
        try:
            r = await self.client.post(_sharded("/command", params), json=body)
        except httpx.HTTPError as exc:
            raise DocServerError(f"command {c}: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise DocServerError(f"command {c}: HTTP {r.status_code}", r.status_code)
        return _json(r)

    async def convert(self, *, signed: bool = True, **params) -> dict:
        """POST /converter?shardkey=<key> met Accept: application/json (anders antwoordt DS in
        XML). Zonder shardkey gaf DS soms de shardkey van een ander verzoek in fileUrl (A18)."""
        body = tokens.command_body(self.settings, params) if signed else dict(params)
        try:
            r = await self.client.post(
                _sharded("/converter", params), json=body, headers={"Accept": "application/json"}
            )
        except httpx.HTTPError as exc:
            raise DocServerError(f"converter: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise DocServerError(f"converter: HTTP {r.status_code}", r.status_code)
        return _json(r)

    def _own(self, url: str) -> None:
        if not url.startswith(self.base + "/"):
            raise ValueError("adres niet bij PLATFORM_OFFICE_URL (eerst urls.rewrite)")

    async def fetch(self, url: str, dest: Path, limit: int) -> tuple[str, int]:
        """Een al herschreven adres ophalen naar dest, hooguit limit bytes: (sha, grootte).
        Te groot: TooLarge; een fout: DocServerError. Bij een fout blijft dest niet staan."""
        self._own(url)
        digest = hashlib.sha256()
        size = 0
        try:
            # Zonder compressie: we bewaren de bytes zoals ze binnenkomen (aiter_raw), en een
            # ingepakt antwoord zou anders ingepakt in de blob belanden
            headers = {"Accept-Encoding": "identity"}
            async with self.client.stream("GET", url, headers=headers) as r:
                if r.status_code != 200:
                    raise DocServerError(f"ophalen: HTTP {r.status_code}", r.status_code)
                encoding = r.headers.get("content-encoding", "identity").strip().lower()
                if encoding not in ("", "identity"):
                    raise DocServerError(f"ophalen: onverwachte codering {encoding}")
                async with await anyio.open_file(dest, "wb") as out:
                    async for chunk in r.aiter_raw(CHUNK):
                        size += len(chunk)
                        if size > limit:
                            raise TooLarge(f"groter dan {limit} bytes")
                        digest.update(chunk)
                        await out.write(chunk)
        except httpx.HTTPError as exc:
            await _remove(dest)
            raise DocServerError(f"ophalen: {type(exc).__name__}") from exc
        except BaseException:
            await _remove(dest)
            raise
        return digest.hexdigest(), size

    async def open(
        self, method: str, path: str, headers: list[tuple[str, str]], content=None
    ) -> httpx.Response:
        """Een verzoek voor de proxy, met een gestreamd antwoord (de aanroeper sluit het).
        path is pad en query aan de kant van DS.

        DS krijgt precies dat pad, byte voor byte, want daarop beslist de proxy. Kan dat niet,
        dan ValueError en gaat er niets weg: een pad dat niet met precies één '/' begint (httpx
        leest '//x/…' als host en laat die weg), en een pad dat httpx anders zou sturen ('.' en
        '..' weggewerkt, opnieuw gecodeerd, een '#'). De query mag httpx wel coderen: die
        betekent daarna hetzelfde."""
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("pad zonder precies één '/' vooraan")
        url = httpx.URL(self.base + path)
        wanted = self._base_path + path.partition("?")[0].encode()
        if url.raw_path.partition(b"?")[0] != wanted:
            raise ValueError("httpx zou DS een ander pad sturen")
        request = self.client.build_request(method, url, headers=headers, content=content)
        return await self.client.send(request, stream=True)


_current: DocServer | None = None


def get() -> DocServer:
    """De DocServer van dit proces, voor de instellingen van nu."""
    global _current
    settings = runtime.settings()
    if _current is None or _current.settings is not settings:
        old, _current = _current, DocServer(settings, transport)
        if old is not None:
            _orphans.append(old)
    return _current


_orphans: list[DocServer] = []


async def close() -> None:
    """Alle clients dicht (bij het stoppen van de app)."""
    global _current
    clients = [*_orphans, *([_current] if _current else [])]
    _orphans.clear()
    _current = None
    for client in clients:
        await client.aclose()
