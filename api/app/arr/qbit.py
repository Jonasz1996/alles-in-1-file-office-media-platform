"""qBittorrent aansturen via zijn web-API v2 (keuze van Jonas, 15-11-2026).

qBittorrent draait in een eigen container ernaast; de app is de enige die het aanstuurt.
`QBit(client, base, username, password)`:

- `login()` (POST `/api/v2/auth/login`, cookie `SID`); elke andere aanroep logt bij een 403 één
  keer opnieuw in;
- `version()` voor de testknop op `/beheer`;
- `ensure_category(name, save_path)`: de categorie van het platform met zijn downloadmap;
- `add(url_or_magnet, tag=…, category=…)`: een torrent toevoegen. qBittorrent geeft geen hash
  terug, dus elke download krijgt een eigen tag (`platform-<id>`) waarmee we hem terugvinden;
- `torrents(category=…, tag=…, hashes=…)` -> `Torrent`s met een eenvoudige `stage`: wachten,
  metadata, downloaden, vastgelopen, klaar, gestopt, fout;
- `delete(hashes, files=…)`.

De fouten zijn `QBitError` met een vaste reden; het wachtwoord komt nooit in de log of een fout.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_BODY = 8 * 1024 * 1024
_BTIH = re.compile(r"xt=urn:btih:([0-9a-zA-Z]{32,40})", re.IGNORECASE)

# De toestanden van qBittorrent (4.x en 5.x: paused heet sinds 5.0 stopped)
_STAGES = {
    "metaDL": "metadata",
    "forcedMetaDL": "metadata",
    "queuedDL": "wachten",
    "checkingDL": "wachten",
    "checkingResumeData": "wachten",
    "allocating": "wachten",
    "moving": "wachten",
    "downloading": "downloaden",
    "forcedDL": "downloaden",
    "stalledDL": "vastgelopen",
    "pausedDL": "gestopt",
    "stoppedDL": "gestopt",
    "uploading": "klaar",
    "forcedUP": "klaar",
    "stalledUP": "klaar",
    "queuedUP": "klaar",
    "checkingUP": "klaar",
    "pausedUP": "klaar",
    "stoppedUP": "klaar",
    "error": "fout",
    "missingFiles": "fout",
}


class QBitError(Exception):
    """Een vaste reden, nooit het wachtwoord of tekst van een uitzondering."""


@dataclass(frozen=True)
class Torrent:
    hash: str
    name: str
    state: str
    stage: str
    progress: float  # 0..1
    size: int
    downloaded: int
    eta: int  # seconden, 8640000 = onbekend
    seeds: int
    content_path: str
    save_path: str
    category: str
    tags: tuple[str, ...]
    added_on: int
    completion_on: int
    last_activity: int


def magnet_hash(magnet: str) -> str | None:
    """De infohash uit een magnet (hex, of base32 omgezet naar hex), kleine letters."""
    match = _BTIH.search(magnet)
    if not match:
        return None
    value = match.group(1)
    if len(value) == 40 and re.fullmatch(r"[0-9a-fA-F]{40}", value):
        return value.lower()
    if len(value) == 32:
        try:
            return base64.b32decode(value.upper()).hex()
        except (binascii.Error, ValueError):
            return None
    return None


def to_torrent(raw: dict) -> Torrent:
    state = str(raw.get("state", ""))
    tags = tuple(t.strip() for t in str(raw.get("tags", "")).split(",") if t.strip())
    return Torrent(
        hash=str(raw.get("hash", "")).lower(),
        name=str(raw.get("name", "")),
        state=state,
        stage=_STAGES.get(state, "wachten"),
        progress=float(raw.get("progress", 0) or 0),
        size=int(raw.get("size", 0) or 0),
        downloaded=int(raw.get("downloaded", 0) or 0),
        eta=int(raw.get("eta", 8640000) or 0),
        seeds=int(raw.get("num_seeds", 0) or 0),
        content_path=str(raw.get("content_path", "")),
        save_path=str(raw.get("save_path", "")),
        category=str(raw.get("category", "")),
        tags=tags,
        added_on=int(raw.get("added_on", 0) or 0),
        completion_on=int(raw.get("completion_on", 0) or 0),
        last_activity=int(raw.get("last_activity", 0) or 0),
    )


class QBit:
    def __init__(self, client: httpx.AsyncClient, base: str, username: str, password: str):
        parts = urlsplit(base)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise QBitError("ongeldig adres van qBittorrent")
        self._client = client
        self._base = base.rstrip("/")
        self._username = username
        self._password = password
        self._sid: str | None = None

    async def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        # De cookie als kop: geen cookie-pot van httpx die tussen bronnen gedeeld kan worden
        headers = {"Cookie": f"SID={self._sid}"} if self._sid else {}
        try:
            resp = await self._client.request(
                method,
                self._base + path,
                headers=headers,
                timeout=TIMEOUT,
                follow_redirects=False,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise QBitError("qBittorrent antwoordt niet op tijd") from exc
        except httpx.HTTPError as exc:
            raise QBitError("qBittorrent is niet bereikbaar") from exc
        if len(resp.content) > MAX_BODY:
            raise QBitError("antwoord van qBittorrent te groot")
        return resp

    async def login(self) -> None:
        resp = await self._send(
            "POST",
            "/api/v2/auth/login",
            data={"username": self._username, "password": self._password},
        )
        if resp.status_code == 403:
            raise QBitError("qBittorrent heeft dit adres tijdelijk geblokkeerd")
        sid = resp.cookies.get("SID")
        if resp.status_code != 200 or resp.text.strip() != "Ok." or not sid:
            raise QBitError("qBittorrent weigert de gebruikersnaam of het wachtwoord")
        self._sid = sid

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._sid is None:
            await self.login()
        resp = await self._send(method, path, **kwargs)
        if resp.status_code == 403:
            await self.login()
            resp = await self._send(method, path, **kwargs)
        if resp.status_code == 403:
            raise QBitError("qBittorrent weigert de toegang")
        return resp

    async def version(self) -> str:
        resp = await self._call("GET", "/api/v2/app/version")
        if resp.status_code != 200:
            raise QBitError(f"qBittorrent antwoordt {resp.status_code}")
        return resp.text.strip()

    async def ensure_category(self, name: str, save_path: str) -> None:
        data = {"category": name, "savePath": save_path}
        resp = await self._call("POST", "/api/v2/torrents/createCategory", data=data)
        if resp.status_code == 409:  # bestaat al: de map gelijk trekken
            resp = await self._call("POST", "/api/v2/torrents/editCategory", data=data)
        if resp.status_code != 200:
            raise QBitError(f"categorie maken mislukt ({resp.status_code})")

    async def add(
        self,
        url_or_magnet: str | None = None,
        *,
        torrent_file: bytes | None = None,
        tag: str,
        category: str,
        stopped: bool = False,
    ) -> None:
        if (url_or_magnet is None) == (torrent_file is None):
            raise ValueError("geef een adres of een .torrent, niet allebei")
        data = {
            "category": category,
            "tags": tag,
            "paused": "true" if stopped else "false",  # 4.x
            "stopped": "true" if stopped else "false",  # 5.x
            "contentLayout": "Original",
        }
        if url_or_magnet is not None:
            data["urls"] = url_or_magnet
            resp = await self._call("POST", "/api/v2/torrents/add", data=data)
        else:
            files = {"torrents": ("release.torrent", torrent_file, "application/x-bittorrent")}
            resp = await self._call("POST", "/api/v2/torrents/add", data=data, files=files)
        if resp.status_code == 415 or resp.text.strip() == "Fails.":
            raise QBitError("qBittorrent weigert de torrent")
        if resp.status_code != 200:
            raise QBitError(f"toevoegen mislukt ({resp.status_code})")

    async def torrents(
        self,
        *,
        category: str | None = None,
        tag: str | None = None,
        hashes: Iterable[str] = (),
    ) -> list[Torrent]:
        params: dict[str, str] = {}
        if category is not None:
            params["category"] = category
        if tag is not None:
            params["tag"] = tag
        wanted = [h.lower() for h in hashes]
        if wanted:
            params["hashes"] = "|".join(wanted)
        resp = await self._call("GET", "/api/v2/torrents/info", params=params)
        if resp.status_code != 200:
            raise QBitError(f"lijst ophalen mislukt ({resp.status_code})")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise QBitError("qBittorrent geeft geen geldige lijst") from exc
        if not isinstance(raw, list):
            raise QBitError("qBittorrent geeft geen geldige lijst")
        return [to_torrent(item) for item in raw if isinstance(item, dict)]

    async def delete(self, hashes: Iterable[str], *, files: bool) -> None:
        wanted = [h.lower() for h in hashes]
        if not wanted:
            return
        data = {"hashes": "|".join(wanted), "deleteFiles": "true" if files else "false"}
        resp = await self._call("POST", "/api/v2/torrents/delete", data=data)
        if resp.status_code != 200:
            raise QBitError(f"verwijderen mislukt ({resp.status_code})")
