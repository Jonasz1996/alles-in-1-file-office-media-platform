"""Lidarr voor muziek en albums (Jonas, fixlijst 26-11-2026, punt 8).

Lidarr draait in een eigen container naast qBittorrent en Prowlarr (compose.arr.yaml). De
beheerder volgt er artiesten en albums in (zijn eigen webinterface op poort 8686); Lidarr zoekt
via Prowlarr, laat qBittorrent downloaden (categorie `lidarr`) en zet een klaar album in zijn
hoofdmap `/music` (voor de app: `<data>/import/lidarr`). De app doet de rest:

1. **Koppelen** (`ensure_links`, bij de start en daarna elk uur tot het lukt): in Prowlarr de
   app Lidarr (Prowlarr zet zijn torrentsites dan zelf in Lidarr), in Lidarr qBittorrent als
   downloader (categorie `lidarr`) en de hoofdmap `/music`. Wat er al staat, blijft staan.
2. **Overnemen** (`tick`, elke minuut): een album met bestanden in Lidarr dat compleet is, of
   waar al `PARTIAL_AFTER` niets meer bij kwam, wordt in Lidarr niet meer gevolgd (anders zoekt
   Lidarr het opnieuw zodra de bestanden weg zijn), de bestanden komen met een harde link in
   `<data>/import/arr/lidarr-<album>/<Artiest>/<Album>/`, de namen in de hoofdmap van Lidarr gaan
   weg, en de import van fase 5 (verplaatsen) zet ze in de gekozen muziekbibliotheek. Daar
   indexeert de scanner ze meteen, zoals elk nieuw bestand.
3. **Opruimen:** een torrent van de categorie `lidarr` die al `SEED_DAYS` klaar is, gaat met zijn
   bestanden weg uit qBittorrent (Lidarr heeft het album dan allang overgenomen; zo blijft er
   geen tweede kopie op de schijf staan).

Fouten zijn `LidarrError` met een vaste reden; de sleutel komt nooit in een fout of log.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("platform.arr.lidarr")

TIMEOUT = httpx.Timeout(20.0, connect=5.0)
MAX_BODY = 16 * 1024 * 1024
CATEGORY = "lidarr"
ROOT = "/music"  # de hoofdmap in de container van Lidarr
PARTIAL_AFTER = timedelta(days=1)
SEED_DAYS = 3
LINK_RETRY_S = 3600
IMPORT_RETRY_S = 3600
PREFIX = "lidarr-"
# Muziek met de categorieën van Newznab (3000 = Audio en de soorten eronder)
SYNC_CATEGORIES = [3000, 3010, 3020, 3030, 3040, 3050, 3060]


class LidarrError(Exception):
    """Een vaste reden, nooit de sleutel."""


def check_base(base: str) -> str:
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.query:
        raise LidarrError("ongeldig adres van Lidarr")
    return base.rstrip("/")


@dataclass(frozen=True)
class Album:
    id: int
    artist_id: int
    title: str
    artist: str
    monitored: bool
    files: int
    tracks: int


@dataclass(frozen=True)
class TrackFile:
    id: int
    path: str
    added: datetime | None


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=UTC)


class Lidarr:
    def __init__(self, client: httpx.AsyncClient, base: str, key: str):
        self._client = client
        self._base = check_base(base)
        self._key = key

    async def _call(self, method: str, path: str, **kwargs) -> Any:
        try:
            resp = await self._client.request(
                method,
                self._base + path,
                headers={"X-Api-Key": self._key},
                timeout=TIMEOUT,
                follow_redirects=False,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise LidarrError("Lidarr antwoordt niet op tijd") from exc
        except httpx.HTTPError as exc:
            raise LidarrError("Lidarr is niet bereikbaar") from exc
        if resp.status_code in (401, 403):
            raise LidarrError("Lidarr weigert de API-sleutel")
        if resp.status_code >= 300:
            raise LidarrError(f"Lidarr antwoordt {resp.status_code}")
        if len(resp.content) > MAX_BODY:
            raise LidarrError("antwoord van Lidarr te groot")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise LidarrError("Lidarr geeft geen geldig antwoord") from exc

    async def version(self) -> str:
        raw = await self._call("GET", "/api/v1/system/status")
        if not isinstance(raw, dict):
            raise LidarrError("Lidarr geeft geen geldig antwoord")
        return str(raw.get("version", ""))

    async def _list(self, path: str, **params) -> list[dict]:
        raw = await self._call("GET", path, params=params or None)
        if not isinstance(raw, list):
            raise LidarrError("Lidarr geeft geen geldige lijst")
        return [x for x in raw if isinstance(x, dict)]

    async def albums(self) -> list[Album]:
        out = []
        for raw in await self._list("/api/v1/album"):
            stats = raw.get("statistics") if isinstance(raw.get("statistics"), dict) else {}
            artist = raw.get("artist") if isinstance(raw.get("artist"), dict) else {}
            try:
                out.append(
                    Album(
                        id=int(raw["id"]),
                        artist_id=int(raw.get("artistId") or 0),
                        title=str(raw.get("title") or ""),
                        artist=str(artist.get("artistName") or ""),
                        monitored=bool(raw.get("monitored")),
                        files=int(stats.get("trackFileCount") or 0),
                        tracks=int(stats.get("trackCount") or stats.get("totalTrackCount") or 0),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    async def track_files(self, album_id: int) -> list[TrackFile]:
        out = []
        for raw in await self._list("/api/v1/trackfile", albumId=int(album_id)):
            try:
                out.append(TrackFile(int(raw["id"]), str(raw["path"]), _when(raw.get("dateAdded"))))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    async def unmonitor(self, album_ids: list[int]) -> None:
        body = {"albumIds": [int(x) for x in album_ids], "monitored": False}
        await self._call("PUT", "/api/v1/album/monitor", json=body)

    async def refresh(self, artist_id: int) -> None:
        body = {"name": "RefreshArtist", "artistId": int(artist_id)}
        await self._call("POST", "/api/v1/command", json=body)

    async def ensure_root(self) -> bool:
        roots = await self._list("/api/v1/rootfolder")
        if any(str(r.get("path", "")).rstrip("/") == ROOT for r in roots):
            return False
        quality = await self._list("/api/v1/qualityprofile")
        metadata = await self._list("/api/v1/metadataprofile")
        if not quality or not metadata:
            raise LidarrError("Lidarr heeft nog geen profielen")
        body = {
            "name": "Muziek",
            "path": ROOT,
            "defaultQualityProfileId": int(quality[0]["id"]),
            "defaultMetadataProfileId": int(metadata[0]["id"]),
            "defaultMonitorOption": "all",
            "defaultNewItemMonitorOption": "all",
            "defaultTags": [],
        }
        await self._call("POST", "/api/v1/rootfolder", json=body)
        return True

    async def ensure_qbit(self, host: str, port: int, username: str, password: str) -> bool:
        for raw in await self._list("/api/v1/downloadclient"):
            if raw.get("implementation") == "QBittorrent":
                return False
        fields = [
            {"name": "host", "value": host},
            {"name": "port", "value": port},
            {"name": "useSsl", "value": False},
            {"name": "urlBase", "value": ""},
            {"name": "username", "value": username},
            {"name": "password", "value": password},
            {"name": "musicCategory", "value": CATEGORY},
        ]
        body = {
            "enable": True,
            "protocol": "torrent",
            "priority": 1,
            "name": "qBittorrent (platform)",
            "implementation": "QBittorrent",
            "configContract": "QBittorrentSettings",
            "fields": fields,
            "tags": [],
            "removeCompletedDownloads": True,
            "removeFailedDownloads": True,
        }
        await self._call("POST", "/api/v1/downloadclient", json=body)
        return True


async def ensure_prowlarr_app(
    client: httpx.AsyncClient, prowlarr_url: str, prowlarr_key: str, lidarr_url: str, key: str
) -> bool:
    """De app Lidarr in Prowlarr (volledige synchronisatie); False als hij er al is."""
    base = prowlarr_url.rstrip("/")
    headers = {"X-Api-Key": prowlarr_key}
    try:
        resp = await client.get(
            base + "/api/v1/applications", headers=headers, timeout=TIMEOUT, follow_redirects=False
        )
        if resp.status_code != 200:
            raise LidarrError(f"Prowlarr antwoordt {resp.status_code}")
        existing = resp.json()
        if any(isinstance(a, dict) and a.get("implementation") == "Lidarr" for a in existing):
            return False
        body = {
            "name": "Lidarr",
            "syncLevel": "fullSync",
            "implementation": "Lidarr",
            "configContract": "LidarrSettings",
            "fields": [
                {"name": "prowlarrUrl", "value": base},
                {"name": "baseUrl", "value": lidarr_url.rstrip("/")},
                {"name": "apiKey", "value": key},
                {"name": "syncCategories", "value": SYNC_CATEGORIES},
            ],
            "tags": [],
        }
        resp = await client.post(
            base + "/api/v1/applications",
            headers=headers,
            json=body,
            timeout=TIMEOUT,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise LidarrError("Prowlarr is niet bereikbaar") from exc
    except ValueError as exc:
        raise LidarrError("Prowlarr geeft geen geldige lijst") from exc
    if resp.status_code >= 300:
        raise LidarrError(f"Prowlarr weigert de app Lidarr ({resp.status_code})")
    return True


# --- bestanden --------------------------------------------------------------------------------


def music_dir(data_dir: str | Path) -> Path:
    """De hoofdmap van Lidarr zoals de app hem ziet (in Lidarr `/music`)."""
    return Path(data_dir) / "import" / "lidarr"


def staging_dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / "import" / "arr"


def local_path(data_dir: str | Path, inner: str) -> Path | None:
    """Een pad van Lidarr (onder /music) als pad van de app; None daarbuiten of met `..`."""
    try:
        rest = PurePosixPath(inner).relative_to(ROOT)
    except ValueError:
        return None
    if not rest.parts or any(part in ("..", "", ".") for part in rest.parts):
        return None
    return music_dir(data_dir).joinpath(*rest.parts)


def _link_or_copy(src: Path, dest: Path) -> None:
    """Een harde link als het bestand nog maar één naam heeft; anders (nog een naam in de
    downloads van qBittorrent, of een ander bestandssysteem) een kopie: de import verplaatst
    alleen een bestand met precies één naam."""
    if src.stat().st_nlink == 1:
        try:
            os.link(src, dest)
            return
        except OSError as exc:
            if exc.errno not in (errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EMLINK):
                raise
    shutil.copy2(src, dest)


def stage_album(data_dir: str | Path, album_id: int, files: list[Path]) -> Path | None:
    """De bestanden van een album in <staging>/lidarr-<album>/<Artiest>/<Album>/ (de structuur
    van Lidarr blijft), daarna de namen bij Lidarr weg. Geeft de bronmap voor de import
    (relatief aan de importmap) of None als er niets was."""
    root = music_dir(data_dir)
    top = staging_dir(data_dir) / f"{PREFIX}{int(album_id)}"
    placed: list[Path] = []
    for src in files:
        if src.is_symlink() or not src.is_file():
            continue
        try:
            rest = src.relative_to(root)
        except ValueError:
            continue
        dest = top.joinpath(*rest.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            _link_or_copy(src, dest)
        placed.append(src)
    if not placed:
        return None
    for src in placed:
        with contextlib.suppress(FileNotFoundError):
            src.unlink()
    # Lege mappen bij Lidarr weg (album, en de artiest als die leeg is)
    for src in placed:
        for parent in (src.parent, src.parent.parent):
            if parent != root and root in parent.parents:
                with contextlib.suppress(OSError):
                    parent.rmdir()
    return top.relative_to(staging_dir(data_dir).parent)


def _has_files(path: Path) -> bool:
    for _dirpath, _dirs, names in os.walk(path, followlinks=False):
        if names:
            return True
    return False


def staged_dirs(data_dir: str | Path) -> list[Path]:
    root = staging_dir(data_dir)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.name.startswith(PREFIX) or d.is_symlink() or not d.is_dir():
            continue
        if _has_files(d):
            out.append(d)
        else:
            shutil.rmtree(d, ignore_errors=True)
    return out


# --- de ronde ---------------------------------------------------------------------------------

_state: dict[str, float] = {}
_tried: dict[str, float] = {}


def reset() -> None:
    _state.clear()
    _tried.clear()


def ready(album: Album, files: list[TrackFile], now: datetime) -> bool:
    if not files:
        return False
    if album.tracks and len(files) >= album.tracks:
        return True
    newest = max((f.added for f in files if f.added), default=None)
    return newest is not None and now - newest >= PARTIAL_AFTER


async def _library(session, conf):
    """De muziekbibliotheek voor Lidarr: de gekozen, anders de oudste."""
    import uuid

    from sqlalchemy import select

    from app.models import MediaLibrary

    if conf.library:
        with contextlib.suppress(ValueError):
            lib = await session.get(MediaLibrary, uuid.UUID(conf.library))
            if lib is not None and lib.kind == "music":
                return lib
    return await session.scalar(
        select(MediaLibrary)
        .where(MediaLibrary.kind == "music")
        .order_by(MediaLibrary.created_at)
        .limit(1)
    )


async def start_import(source: str, library) -> None:
    """De import van fase 5 (verplaatsen). Tests vervangen deze functie."""
    from app.media import importer

    await importer.start(source, library.root_id, library.owner_id, move=True)


async def _import_staged(data_dir: str, library) -> None:
    """Hooguit één import per ronde; een map die al geprobeerd is, pas na IMPORT_RETRY_S
    opnieuw (wat de import overslaat, zoals "in gebruik", blijft zo niet elke minuut lopen)."""
    from fastapi import HTTPException

    now = time.monotonic()
    for d in await asyncio.to_thread(staged_dirs, data_dir):
        last = _tried.get(d.name)
        if last is not None and now - last < IMPORT_RETRY_S:
            continue
        try:
            await start_import(str(d.relative_to(staging_dir(data_dir).parent)), library)
        except HTTPException:
            return  # er loopt al een import: volgende ronde
        _tried[d.name] = now
        log.info("lidarr: %s naar de import (%s)", d.name, library.name)
        return


async def ensure_links(client: httpx.AsyncClient, session, settings, conf) -> None:
    from app.arr import settings as arr_settings

    lid = Lidarr(client, conf.url, conf.key)
    q = await arr_settings.load_qbit(session, settings)
    if q is not None:
        parts = urlsplit(q.url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if await lid.ensure_qbit(parts.hostname or "qbittorrent", port, q.username, q.password):
            log.info("lidarr: qBittorrent als downloader gezet")
    if await lid.ensure_root():
        log.info("lidarr: hoofdmap %s gezet", ROOT)
    p = await arr_settings.load_prowlarr(session, settings)
    if p is not None and await ensure_prowlarr_app(client, p.url, p.key, conf.url, conf.key):
        log.info("lidarr: Lidarr als app in Prowlarr gezet")


async def _clean_torrents(client: httpx.AsyncClient, session, settings) -> None:
    from app.arr import qbit
    from app.arr import settings as arr_settings

    q = await arr_settings.load_qbit(session, settings)
    if q is None:
        return
    api = qbit.QBit(client, q.url, q.username, q.password)
    limit = time.time() - SEED_DAYS * 86400
    old = [
        t.hash
        for t in await api.torrents(category=CATEGORY)
        if t.progress >= 1.0 and t.completion_on and t.completion_on < limit
    ]
    if old:
        await api.delete(old, files=True)
        log.info("lidarr: %d oude torrents weg", len(old))


async def take_over(client: httpx.AsyncClient, data_dir: str, conf) -> int:
    """Stap 2: klare albums uit Lidarr naar de staging; geeft het aantal."""
    lid = Lidarr(client, conf.url, conf.key)
    now = datetime.now(UTC)
    done = 0
    for album in await lid.albums():
        if album.files <= 0:
            continue
        files = await lid.track_files(album.id)
        if not ready(album, files, now):
            continue
        paths = [p for p in (local_path(data_dir, f.path) for f in files) if p is not None]
        if album.monitored:
            await lid.unmonitor([album.id])
        staged = await asyncio.to_thread(stage_album, data_dir, album.id, paths)
        with contextlib.suppress(LidarrError):
            await lid.refresh(album.artist_id)
        if staged is not None:
            done += 1
            log.info("lidarr: album %s – %s klaar voor de import", album.artist, album.title)
    return done


async def tick(settings) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.arr import settings as arr_settings
    from app.db import get_engine

    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session, arr_settings.http_client() as client:
        conf = await arr_settings.load_lidarr(session, settings)
        if conf is None:
            return
        now = time.monotonic()
        if now - _state.get("links", -LINK_RETRY_S) >= LINK_RETRY_S:
            _state["links"] = now
            try:
                await ensure_links(client, session, settings, conf)
                _state["links"] = float("inf")  # gelukt: niet meer nodig tot een herstart
            except LidarrError as exc:
                log.warning("lidarr: koppelen: %s", exc)
        library = await _library(session, conf)
        if library is None:
            return  # nog geen muziekbibliotheek: Lidarr houdt de albums zolang zelf
        data_dir = str(settings.data_dir)
        try:
            await take_over(client, data_dir, conf)
        except LidarrError as exc:
            log.warning("lidarr: %s", exc)
        await _import_staged(data_dir, library)
        if now - _state.get("clean", -3600) >= 3600:
            _state["clean"] = now
            try:
                await _clean_torrents(client, session, settings)
            except Exception as exc:  # noqa: BLE001 - qBittorrent kan even weg zijn
                log.warning("lidarr: torrents opruimen: %s", exc)
