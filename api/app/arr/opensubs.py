"""OpenSubtitles (REST-API v1, api.opensubtitles.com): ondertitels zoeken en downloaden (fase 11,
stap 4). Eigen code, geen bibliotheek.

- Elke aanvraag draagt de API-sleutel (`Api-Key`) en een eigen `User-Agent`; met een account
  (gebruiker en wachtwoord) komt er een token bij (`/login`), dat hoort bij een hogere
  dagelijkse grens voor downloads. Zonder account werkt zoeken en downloaden ook, maar met een
  lagere grens.
- Zoeken (`/subtitles`) op IMDb- of TMDB-id (film) of op het id van de serie met seizoen en
  aflevering, en waar het kan op de hash van het bestand (`moviehash`): een treffer op die hash
  past precies op dat bestand, dus ook in de tijd. De API wil de parameters gesorteerd en klein.
- Downloaden is twee stappen: `/download` met het file_id geeft een link en hoeveel downloads er
  vandaag nog over zijn, dan de link zelf. Hooguit 2 MB, alleen tekst (SRT).
- `best()` kiest per taal: hash-treffer eerst, dan vertrouwde uploader, geen machinevertaling of
  AI-vertaling, geen ondertitel voor slechthorenden tenzij er niets anders is, dan het aantal
  downloads.

Fouten: `OpenSubsAuth` (sleutel of account geweigerd), `OpenSubsQuota` (de grens voor vandaag
is op; `reset` zegt wanneer hij terugkomt, als de API dat meldt), anders `OpenSubsError`. Een
fout noemt nooit een sleutel of wachtwoord.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

API = "https://api.opensubtitles.com/api/v1"
USER_AGENT = "AllesInEen v0.9"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
MAX_SUB_BYTES = 2 * 1024 * 1024
HASH_CHUNK = 64 * 1024


class OpenSubsError(Exception):
    pass


class OpenSubsAuth(OpenSubsError):
    pass


class OpenSubsQuota(OpenSubsError):
    def __init__(self, message: str, reset: datetime | None = None) -> None:
        super().__init__(message)
        self.reset = reset


@dataclass(frozen=True)
class Found:
    file_id: int
    lang: str  # zoals OpenSubtitles hem geeft: nl, en, pt-BR
    release: str
    downloads: int
    hash_match: bool
    trusted: bool
    hearing_impaired: bool
    machine: bool  # machine- of AI-vertaling
    fps: float | None = None


@dataclass
class Session:
    """Een verbinding: sleutel, en na login een token en de juiste basis-URL."""

    client: httpx.AsyncClient
    api_key: str
    username: str | None = None
    password: str | None = None
    token: str | None = None
    base: str = API
    remaining: int | None = None
    _logged_in: bool = field(default=False, repr=False)

    def headers(self) -> dict[str, str]:
        h = {"Api-Key": self.api_key, "User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def _call(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = await self.client.request(
                method, self.base + path, headers=self.headers(), timeout=TIMEOUT, **kw
            )
        except httpx.HTTPError:
            raise OpenSubsError("OpenSubtitles is niet bereikbaar") from None
        if r.status_code in (401, 403):
            raise OpenSubsAuth("OpenSubtitles weigert de sleutel of het account")
        if r.status_code == 406 or r.status_code == 429:
            raise OpenSubsQuota("de grens van OpenSubtitles is bereikt", _reset_of(r))
        if r.status_code >= 400:
            raise OpenSubsError(f"OpenSubtitles antwoordt {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise OpenSubsError("OpenSubtitles gaf geen JSON") from None

    async def login(self) -> None:
        """Alleen met een account; zonder doet het niets."""
        if self._logged_in or not (self.username and self.password):
            return
        data = await self._call(
            "POST", "/login", json={"username": self.username, "password": self.password}
        )
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise OpenSubsAuth("OpenSubtitles weigert het account")
        self.token = str(token)
        host = data.get("base_url")
        if isinstance(host, str) and _plain_host(host):
            self.base = f"https://{host}/api/v1"
        user = data.get("user") if isinstance(data.get("user"), dict) else {}
        if isinstance(user.get("allowed_downloads"), int):
            self.remaining = user["allowed_downloads"]
        self._logged_in = True

    async def search(
        self,
        languages: list[str],
        *,
        imdb: str | None = None,
        tmdb: int | None = None,
        show_imdb: str | None = None,
        show_tmdb: int | None = None,
        season: int | None = None,
        episode: int | None = None,
        moviehash: str | None = None,
    ) -> list[Found]:
        await self.login()
        params = search_params(
            languages,
            imdb=imdb,
            tmdb=tmdb,
            show_imdb=show_imdb,
            show_tmdb=show_tmdb,
            season=season,
            episode=episode,
            moviehash=moviehash,
        )
        data = await self._call("GET", "/subtitles?" + params)
        return parse_search(data)

    async def download(self, file_id: int) -> bytes:
        await self.login()
        data = await self._call("POST", "/download", json={"file_id": file_id, "sub_format": "srt"})
        if not isinstance(data, dict) or not isinstance(data.get("link"), str):
            raise OpenSubsError("OpenSubtitles gaf geen downloadlink")
        if isinstance(data.get("remaining"), int):
            self.remaining = data["remaining"]
        link = data["link"]
        if not link.startswith("https://"):
            raise OpenSubsError("OpenSubtitles gaf een vreemde downloadlink")
        try:
            async with self.client.stream(
                "GET", link, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
            ) as r:
                if r.status_code != 200:
                    raise OpenSubsError(f"de download antwoordt {r.status_code}")
                out = bytearray()
                async for chunk in r.aiter_bytes():
                    out += chunk
                    if len(out) > MAX_SUB_BYTES:
                        raise OpenSubsError("de ondertitel is te groot")
        except httpx.HTTPError:
            raise OpenSubsError("de download lukte niet") from None
        return bytes(out)


def _plain_host(host: str) -> bool:
    """base_url van /login: alleen een hostnaam onder opensubtitles.com."""
    host = host.strip().lower()
    return host.replace(".", "").replace("-", "").isalnum() and (
        host == "api.opensubtitles.com" or host.endswith(".opensubtitles.com")
    )


def _reset_of(r: httpx.Response) -> datetime | None:
    try:
        data = r.json()
    except ValueError:
        return None
    value = data.get("reset_time_utc") if isinstance(data, dict) else None
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _imdb_number(value: str | None) -> str | None:
    if not value:
        return None
    digits = value.lower().removeprefix("tt")
    return str(int(digits)) if digits.isdigit() else None


def search_params(
    languages: list[str],
    *,
    imdb: str | None = None,
    tmdb: int | None = None,
    show_imdb: str | None = None,
    show_tmdb: int | None = None,
    season: int | None = None,
    episode: int | None = None,
    moviehash: str | None = None,
) -> str:
    """De query voor /subtitles: gesorteerd op naam en in kleine letters (anders stuurt de API
    door). Bij een aflevering het id van de serie, niet dat van de aflevering."""
    params: dict[str, str] = {"languages": ",".join(sorted({x.lower() for x in languages}))}
    if season is not None or episode is not None:
        if show_imdb and _imdb_number(show_imdb):
            params["parent_imdb_id"] = _imdb_number(show_imdb) or ""
        elif show_tmdb:
            params["parent_tmdb_id"] = str(show_tmdb)
        if season is not None:
            params["season_number"] = str(season)
        if episode is not None:
            params["episode_number"] = str(episode)
    elif imdb and _imdb_number(imdb):
        params["imdb_id"] = _imdb_number(imdb) or ""
    elif tmdb:
        params["tmdb_id"] = str(tmdb)
    if moviehash:
        params["moviehash"] = moviehash.lower()
    return urlencode(sorted(params.items()))


def parse_search(data: Any) -> list[Found]:
    out: list[Found] = []
    rows = data.get("data") if isinstance(data, dict) else None
    for row in rows if isinstance(rows, list) else []:
        a = row.get("attributes") if isinstance(row, dict) else None
        if not isinstance(a, dict):
            continue
        files = a.get("files")
        if not isinstance(files, list) or not files or not isinstance(files[0], dict):
            continue
        file_id = files[0].get("file_id")
        lang = a.get("language")
        if not isinstance(file_id, int) or not isinstance(lang, str):
            continue
        fps = a.get("fps")
        out.append(
            Found(
                file_id=file_id,
                lang=lang,
                release=str(a.get("release") or "")[:200],
                downloads=int(a.get("download_count") or 0),
                hash_match=bool(a.get("moviehash_match")),
                trusted=bool(a.get("from_trusted")),
                hearing_impaired=bool(a.get("hearing_impaired")),
                machine=bool(a.get("machine_translated") or a.get("ai_translated")),
                fps=float(fps) if isinstance(fps, int | float) and fps else None,
            )
        )
    return out


def best(found: list[Found], lang: str) -> Found | None:
    """De beste ondertitel in taal `lang` (nl, en; pt-br telt als pt-br)."""
    lang = lang.lower()
    fitting = [f for f in found if f.lang.lower() == lang and not f.machine]
    if not fitting:
        return None
    return max(
        fitting,
        key=lambda f: (f.hash_match, not f.hearing_impaired, f.trusted, f.downloads),
    )


def moviehash(path: Path) -> str | None:
    """De hash van OpenSubtitles: de grootte plus de som van de 64-bits woorden (little endian)
    van de eerste en de laatste 64 KB, modulo 2^64, als 16 hexcijfers. None bij een bestand
    kleiner dan 128 KB."""
    try:
        size = path.stat().st_size
        if size < 2 * HASH_CHUNK:
            return None
        total = size
        with path.open("rb") as f:
            for offset in (0, size - HASH_CHUNK):
                f.seek(offset, os.SEEK_SET)
                chunk = f.read(HASH_CHUNK)
                if len(chunk) != HASH_CHUNK:
                    return None
                total += sum(struct.unpack(f"<{HASH_CHUNK // 8}Q", chunk))
    except OSError:
        return None
    return f"{total & 0xFFFFFFFFFFFFFFFF:016x}"
