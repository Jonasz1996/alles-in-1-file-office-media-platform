"""De kant van de app van de YouTube-module: de client naar de helper en de taak `youtube.mp3`.

De taak (rij default, één poging): de helper downloadt en zet om naar
`<data>/import/youtube/<job>/<naam>.mp3`; de app zet dat bestand met place_file in de gekozen map,
als iemand die het zelf uploadt (rechten, quotum, versies; in een gedeelde map met schrijfrecht
van de eigenaar van die map). Daarna doet de scanner van media de rest: staat de map in een
muziekbibliotheek, dan is het nummer meteen geïndexeerd (place_file meldt het nieuwe bestand bij
media.hooks, zoals elke upload). Wie het vroeg, krijgt de gebeurtenis `youtube.mp3` (klaar of
mislukt).
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import events, jobs
from app.config import Settings
from app.db import get_engine
from app.models import User
from app.storage import BlobStore

log = logging.getLogger("platform.youtube")

TYPE = "youtube.mp3"
EVENT = "youtube.mp3"
SEARCH_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
FETCH_TIMEOUT = httpx.Timeout(3600.0, connect=5.0)
CHUNK = 1024 * 1024


class YoutubeError(Exception):
    """Een vaste reden voor de gebruiker."""


def enabled(settings: Settings) -> bool:
    return bool(settings.youtube_url)


def out_dir(settings: Settings) -> Path:
    return Path(settings.data_dir) / "import" / "youtube"


def http_client() -> httpx.AsyncClient:
    """De client naar de helper (tests vervangen deze functie)."""
    return httpx.AsyncClient(trust_env=False)


def _headers(settings: Settings) -> dict[str, str]:
    return {"X-Key": settings.youtube_key} if settings.youtube_key else {}


async def _call(settings: Settings, method: str, path: str, limit: httpx.Timeout, **kwargs) -> Any:
    if not enabled(settings):
        raise YoutubeError("de YouTube-module staat niet aan")
    try:
        async with http_client() as client:
            resp = await client.request(
                method,
                settings.youtube_url.rstrip("/") + path,
                headers=_headers(settings),
                timeout=limit,
                follow_redirects=False,
                **kwargs,
            )
    except httpx.TimeoutException as exc:
        raise YoutubeError("de YouTube-helper antwoordt niet op tijd") from exc
    except httpx.HTTPError as exc:
        raise YoutubeError("de YouTube-helper is niet bereikbaar") from exc
    if resp.status_code == 401:
        raise YoutubeError("de YouTube-helper weigert de sleutel")
    if resp.status_code >= 300:
        try:
            detail = str(resp.json().get("detail", ""))[:200]
        except (ValueError, AttributeError):
            detail = ""
        raise YoutubeError(detail or f"de YouTube-helper antwoordt {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise YoutubeError("de YouTube-helper geeft geen geldig antwoord") from exc


async def search(settings: Settings, query: str, n: int = 20) -> list[dict[str, Any]]:
    raw = await _call(settings, "GET", "/zoek", SEARCH_TIMEOUT, params={"q": query, "n": n})
    return raw if isinstance(raw, list) else []


async def helper_version(settings: Settings) -> str | None:
    try:
        raw = await _call(settings, "GET", "/health", SEARCH_TIMEOUT)
    except YoutubeError:
        return None
    return str(raw.get("yt_dlp")) if isinstance(raw, dict) else None


async def channel(settings: Settings, ref: str, n: int = 15) -> dict[str, Any]:
    """{id, title, videos: [{id, title, published}]} van een kanaal (UC… of @naam)."""
    raw = await _call(settings, "GET", "/kanaal", SEARCH_TIMEOUT, params={"c": ref, "n": n})
    if not isinstance(raw, dict) or not isinstance(raw.get("videos"), list):
        raise YoutubeError("de YouTube-helper geeft geen geldig antwoord")
    return raw


async def enqueue(
    session: AsyncSession,
    user: User,
    video: str,
    folder: uuid.UUID,
    title: str,
    kind: str = "mp3",
    sponsor: bool = False,
    commit: bool = True,
) -> uuid.UUID:
    """Eén taak: video als mp3 of als mp4 (kind) naar folder; sponsor knipt SponsorBlock-stukken
    eruit. Het type blijft youtube.mp3 (ook voor mp4), zodat lopende taken van vóór mp4 blijven
    werken."""
    job = uuid.uuid4()
    payload = {
        "job": str(job),
        "user": str(user.id),
        "video": video,
        "folder": str(folder),
        "title": title[:300],
        "kind": kind,
        "sponsor": sponsor,
    }
    await jobs.enqueue(session, TYPE, payload, key=f"{TYPE}:{user.id}:{video}:{folder}:{kind}")
    if commit:
        await session.commit()
    return job


async def _chunks(path: Path) -> AsyncIterator[bytes]:
    async with await anyio.open_file(path, "rb") as f:
        while chunk := await f.read(CHUNK):
            yield chunk


def _publish(payload: dict[str, Any], state: str, **extra: Any) -> None:
    with_user = uuid.UUID(payload["user"])
    data = {
        "job": payload["job"],
        "video": payload["video"],
        "title": payload.get("title", ""),
        "state": state,
        **extra,
    }
    events.publish([with_user], EVENT, data)


async def run(settings: Settings, payload: dict[str, Any]) -> None:
    from app.routers.files import place_file
    from app.routers.office_new import target_folder

    job = str(uuid.UUID(payload["job"]))
    folder_dir = out_dir(settings) / job
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    try:
        async with maker() as session:
            user = await session.get(User, uuid.UUID(payload["user"]))
            if user is None or user.disabled_at is not None:
                return
            try:
                target = await target_folder(session, user, uuid.UUID(payload["folder"]))
            except HTTPException as exc:
                raise jobs.JobError("de map is er niet meer of je mag er niet schrijven") from exc
            await session.commit()  # geen open transactie tijdens het downloaden
            kind = "mp4" if payload.get("kind") == "mp4" else "mp3"
            body = {
                "id": payload["video"],
                "job": job,
                "kind": kind,
                "sponsor": bool(payload.get("sponsor")),
            }
            try:
                made = await _call(settings, "POST", "/mp3", FETCH_TIMEOUT, json=body)
            except YoutubeError as exc:
                raise jobs.JobError(str(exc)) from exc
            name = str(made.get("name", "")) if isinstance(made, dict) else ""
            if not name or "/" in name or name.startswith(".") or not name.endswith(f".{kind}"):
                raise jobs.JobError("de YouTube-helper gaf geen geldig bestand")
            source = folder_dir / name
            if not source.is_file() or source.is_symlink():
                raise jobs.JobError(f"het {kind}-bestand is niet gevonden")
            blobs = BlobStore(settings.blob_dir)
            tmp, sha, size = await blobs.receive(_chunks(source), 0)
            try:
                node = await place_file(
                    session,
                    blobs,
                    target.owner,
                    target.parent,
                    name,
                    "rename",
                    sha,
                    size,
                    tmp,
                    actor=user,
                    guard=target.guard,
                    tz=settings.timezone,
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                raise jobs.JobError(
                    str(detail.get("message") or detail.get("code") or exc)
                ) from exc
            finally:
                await anyio.Path(tmp).unlink(missing_ok=True)
            _publish(payload, "done", node=str(node.id), name=node.name, kind=kind)
            log.info("youtube: %s als %s bewaard", payload["video"], kind)
    finally:
        await anyio.to_thread.run_sync(lambda: shutil.rmtree(folder_dir, ignore_errors=True))


async def dead(settings: Settings, payload: dict[str, Any], error: str) -> None:
    _publish(payload, "failed", reason=error[:200])


jobs.register(TYPE, run, dead, max_attempts=1, lane="default", timeout=3700.0)
