"""De helper van de YouTube-module: yt-dlp en ffmpeg in een eigen container.

    python -m app.youtube.helper          (luistert op 0.0.0.0:8090)

Omgeving: `YOUTUBE_KEY` (de gedeelde sleutel; de app stuurt hem als kop `X-Key`; leeg = geen
controle, alleen voor een test), `YOUTUBE_OUT` (standaard `/out`, in de app
`<data>/import/youtube`), `YOUTUBE_PORT` (8090).

    GET  /health                        → {"ok": true, "yt_dlp": "<versie>"}
    GET  /zoek?q=…&n=20                 → [{id, title, channel, duration, live}]
    POST /mp3 {"id": "<video>", "job": "<uuid>"}
                                        → {"name": "<bestand>.mp3", "title", "artist", "size"}

`/mp3` downloadt het beste geluid, zet het om naar mp3 (VBR, de beste kwaliteit), met titel,
artiest (de artiest van YouTube Music, anders het kanaal), album ("YouTube" als er geen is) en
de miniatuur als hoes, in `/out/<job>/`. Een livestream of iets langer dan `MAX_SECONDS` weigert
hij. Hooguit `PARALLEL` tegelijk. Dit pakket importeert niets van de rest van de app: de helper
heeft geen database, geen geheimen van de app en geen bestanden van gebruikers.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_SECONDS = 3 * 3600
MAX_BYTES = 1024 * 1024 * 1024
PARALLEL = 2
SEARCH_MAX = 30

app = FastAPI(title="platform-youtube", docs_url=None, redoc_url=None, openapi_url=None)
_slots = asyncio.Semaphore(PARALLEL)


def _out() -> Path:
    return Path(os.environ.get("YOUTUBE_OUT", "/out"))


def _check(key: str | None) -> None:
    want = os.environ.get("YOUTUBE_KEY", "")
    if want and not hmac.compare_digest(want, key or ""):
        raise HTTPException(401, "sleutel")


def _live(entry: dict[str, Any]) -> bool:
    return entry.get("live_status") in ("is_live", "is_upcoming") or bool(entry.get("is_live"))


def search(query: str, n: int) -> list[dict[str, Any]]:
    from yt_dlp import YoutubeDL

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{n}:{query}", download=False) or {}
    out = []
    for e in info.get("entries") or []:
        if not isinstance(e, dict) or not VIDEO_ID.match(str(e.get("id", ""))):
            continue
        duration = e.get("duration")
        out.append(
            {
                "id": e["id"],
                "title": str(e.get("title") or "")[:300],
                "channel": str(e.get("channel") or e.get("uploader") or "")[:200],
                "duration": int(duration) if isinstance(duration, int | float) else None,
                "live": _live(e),
            }
        )
    return out


def _refuse(info: dict[str, Any], *, incomplete: bool) -> str | None:
    if _live(info):
        return "een livestream kan niet als mp3"
    duration = info.get("duration")
    if isinstance(duration, int | float) and duration > MAX_SECONDS:
        return "langer dan 3 uur"
    return None


def fetch(video: str, job: str) -> dict[str, Any]:
    from yt_dlp import YoutubeDL
    from yt_dlp.postprocessor.metadataparser import MetadataParserPP

    target = _out() / job
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    interpret = MetadataParserPP.Actions.INTERPRET
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": {"default": str(target / "%(title).150B.%(ext)s")},
        "windowsfilenames": True,
        "max_filesize": MAX_BYTES,
        "match_filter": _refuse,
        "writethumbnail": True,
        "postprocessors": [
            {
                "key": "MetadataParser",
                "when": "pre_process",
                "actions": [
                    (interpret, "%(artist,creator,channel,uploader|)s", "%(meta_artist)s"),
                    (interpret, "%(album|YouTube)s", "%(meta_album)s"),
                ],
            },
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"},
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail"},
        ],
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video}", download=True) or {}
    files = sorted(target.glob("*.mp3"))
    if not files:
        reason = _refuse(info, incomplete=False) or "geen mp3 gemaakt"
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError(reason)
    for extra in target.iterdir():
        if extra != files[0]:
            extra.unlink(missing_ok=True)
    return {
        "name": files[0].name,
        "title": str(info.get("title") or ""),
        "artist": str(info.get("artist") or info.get("channel") or info.get("uploader") or ""),
        "size": files[0].stat().st_size,
    }


class Mp3In(BaseModel):
    id: str = Field(pattern=VIDEO_ID.pattern)
    job: uuid.UUID


@app.get("/health")
async def health() -> dict:
    import yt_dlp.version

    return {"ok": True, "yt_dlp": yt_dlp.version.__version__}


@app.get("/zoek")
async def zoek(
    q: str = Query(min_length=1, max_length=200),
    n: int = Query(default=20, ge=1, le=SEARCH_MAX),
    x_key: str | None = Header(default=None),
) -> list[dict]:
    _check(x_key)
    try:
        return await asyncio.to_thread(search, q, n)
    except Exception as exc:  # noqa: BLE001 - yt-dlp geeft veel soorten fouten
        raise HTTPException(502, f"zoeken mislukt: {str(exc)[:200]}") from None


@app.post("/mp3")
async def mp3(body: Mp3In, x_key: str | None = Header(default=None)) -> dict:
    _check(x_key)
    async with _slots:
        try:
            return await asyncio.to_thread(fetch, body.id, str(body.job))
        except ValueError as exc:
            raise HTTPException(422, str(exc)[:200]) from None
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"downloaden mislukt: {str(exc)[:200]}") from None


@app.delete("/mp3/{job}", status_code=204)
async def forget(job: uuid.UUID, x_key: str | None = Header(default=None)) -> None:
    _check(x_key)
    shutil.rmtree(_out() / str(job), ignore_errors=True)


def main() -> None:
    import uvicorn

    port = int(os.environ.get("YOUTUBE_PORT", "8090"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=False)  # noqa: S104


if __name__ == "__main__":
    main()
