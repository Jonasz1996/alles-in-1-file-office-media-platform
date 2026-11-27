"""De helper van de YouTube-module: yt-dlp en ffmpeg in een eigen container.

    python -m app.youtube.helper          (luistert op 0.0.0.0:8090)

Omgeving: `YOUTUBE_KEY` (de gedeelde sleutel; de app stuurt hem als kop `X-Key`; leeg = geen
controle, alleen voor een test), `YOUTUBE_OUT` (standaard `/out`, in de app
`<data>/import/youtube`), `YOUTUBE_PORT` (8090).

    GET  /health                        → {"ok": true, "yt_dlp": "<versie>"}
    GET  /zoek?q=…&n=20                 → [{id, title, channel, channel_id, duration, live}]
    GET  /kanaal?c=<UC…|@naam>&n=15     → {id, title, videos: [{id, title, published}]}
    POST /mp3 {"id": "<video>", "job": "<uuid>", "kind": "mp3"|"mp4", "sponsor": false}
                                        → {"name": "<bestand>.mp3|.mp4", "title", "artist", "size"}

`/mp3` met kind mp3 downloadt het beste geluid en zet het om naar mp3 (VBR, de beste kwaliteit),
met titel, artiest (de artiest van YouTube Music, anders het kanaal), album ("YouTube" als er
geen is) en de miniatuur als hoes; met kind mp4 de video tot 1080p met het beste geluid erbij,
samengevoegd tot één mp4 (met titel en miniatuur). Met sponsor knipt hij de stukken eruit die
SponsorBlock kent (sponsor, eigen reclame, "abonneer je"). Alles in `/out/<job>/`. Een
livestream of iets langer dan `MAX_SECONDS` weigert hij. Hooguit `PARALLEL` tegelijk.

`/kanaal` geeft de nieuwste video's van een kanaal uit de RSS-feed van YouTube (geen account
nodig); een @naam zoekt hij eerst op met yt-dlp.

Dit pakket importeert niets van de rest van de app: de helper heeft geen database, geen
geheimen van de app en geen bestanden van gebruikers.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_SECONDS = 3 * 3600
MAX_BYTES = 1024 * 1024 * 1024
PARALLEL = 2
SEARCH_MAX = 30
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
HANDLE = re.compile(r"^@[A-Za-z0-9._-]{3,100}$")
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id="
FEED_MAX_BYTES = 2 * 1024 * 1024
SPONSOR = ["sponsor", "selfpromo", "interaction"]
_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

app = FastAPI(title="platform-youtube", docs_url=None, redoc_url=None, openapi_url=None)
_slots = asyncio.Semaphore(PARALLEL)


def _out() -> Path:
    return Path(os.environ.get("YOUTUBE_OUT", "/out"))


def _refused(key: str | None) -> JSONResponse | None:
    """Een antwoord 401 als de sleutel niet klopt (de helper gebruikt geen app.errors: hij
    importeert niets van de rest van de app)."""
    want = os.environ.get("YOUTUBE_KEY", "")
    if want and not hmac.compare_digest(want, key or ""):
        return _error(401, "sleutel")
    return None


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


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
                "channel_id": _channel_id(e.get("channel_id")),
                "duration": int(duration) if isinstance(duration, int | float) else None,
                "live": _live(e),
            }
        )
    return out


def _channel_id(value: Any) -> str | None:
    value = str(value or "")
    return value if CHANNEL_ID.match(value) else None


def resolve(handle: str) -> str:
    """Het kanaal-id (UC…) van een @naam, met yt-dlp (één video is genoeg)."""
    from yt_dlp import YoutubeDL

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": 1,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/{handle}/videos", download=False) or {}
    found = _channel_id(info.get("channel_id")) or _channel_id(info.get("id"))
    if not found:
        raise ValueError("kanaal niet gevonden")
    return found


def channel(ref: str, n: int) -> dict[str, Any]:
    """De nieuwste video's van een kanaal uit zijn RSS-feed (hooguit 15, dat geeft YouTube)."""
    cid = ref if CHANNEL_ID.match(ref) else resolve(ref)
    req = Request(FEED + cid, headers={"User-Agent": "platform-youtube"})  # noqa: S310 - vaste https
    with urlopen(req, timeout=20) as resp:  # noqa: S310
        raw = resp.read(FEED_MAX_BYTES + 1)
    if len(raw) > FEED_MAX_BYTES:
        raise ValueError("de feed is te groot")
    root = ElementTree.fromstring(raw)  # noqa: S314 - van YouTube, begrensd, zonder entiteiten
    title = root.findtext("a:title", default="", namespaces=_NS)
    videos = []
    for entry in root.findall("a:entry", _NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=_NS)
        if not VIDEO_ID.match(vid):
            continue
        videos.append(
            {
                "id": vid,
                "title": entry.findtext("a:title", default="", namespaces=_NS)[:300],
                "published": entry.findtext("a:published", default="", namespaces=_NS)[:40],
            }
        )
        if len(videos) >= n:
            break
    return {"id": cid, "title": title[:200], "videos": videos}


def _refuse(info: dict[str, Any], *, incomplete: bool) -> str | None:
    if _live(info):
        return "een livestream kan niet als mp3"
    duration = info.get("duration")
    if isinstance(duration, int | float) and duration > MAX_SECONDS:
        return "langer dan 3 uur"
    return None


def _postprocessors(kind: str, sponsor: bool) -> list[dict[str, Any]]:
    from yt_dlp.postprocessor.metadataparser import MetadataParserPP

    interpret = MetadataParserPP.Actions.INTERPRET
    out: list[dict[str, Any]] = [
        {
            "key": "MetadataParser",
            "when": "pre_process",
            "actions": [
                (interpret, "%(artist,creator,channel,uploader|)s", "%(meta_artist)s"),
                (interpret, "%(album|YouTube)s", "%(meta_album)s"),
            ],
        },
    ]
    if sponsor:
        out.append({"key": "SponsorBlock", "categories": SPONSOR, "when": "after_filter"})
    out.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
    if kind == "mp3":
        out.append({"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"})
    if sponsor:
        out.append({"key": "ModifyChapters", "remove_sponsor_segments": SPONSOR})
    out += [{"key": "FFmpegMetadata", "add_metadata": True}, {"key": "EmbedThumbnail"}]
    return out


def fetch(video: str, job: str, kind: str = "mp3", sponsor: bool = False) -> dict[str, Any]:
    from yt_dlp import YoutubeDL

    target = _out() / job
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    video_fmt = (
        "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b"
    )
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best" if kind == "mp3" else video_fmt,
        "outtmpl": {"default": str(target / "%(title).150B.%(ext)s")},
        "windowsfilenames": True,
        "max_filesize": MAX_BYTES * (1 if kind == "mp3" else 4),
        "match_filter": _refuse,
        "writethumbnail": True,
        "postprocessors": _postprocessors(kind, sponsor),
    }
    if kind == "mp4":
        opts["merge_output_format"] = "mp4"
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video}", download=True) or {}
    files = sorted(target.glob(f"*.{kind}"))
    if not files:
        reason = _refuse(info, incomplete=False) or f"geen {kind} gemaakt"
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
    kind: Literal["mp3", "mp4"] = "mp3"
    sponsor: bool = False


@app.get("/health")
async def health() -> dict:
    import yt_dlp.version

    return {"ok": True, "yt_dlp": yt_dlp.version.__version__}


@app.get("/zoek")
async def zoek(
    q: str = Query(min_length=1, max_length=200),
    n: int = Query(default=20, ge=1, le=SEARCH_MAX),
    x_key: str | None = Header(default=None),
):
    if (refused := _refused(x_key)) is not None:
        return refused
    try:
        return await asyncio.to_thread(search, q, n)
    except Exception as exc:  # noqa: BLE001 - yt-dlp geeft veel soorten fouten
        return _error(502, f"zoeken mislukt: {str(exc)[:200]}")


@app.get("/kanaal")
async def kanaal(
    c: str = Query(min_length=3, max_length=101),
    n: int = Query(default=15, ge=1, le=15),
    x_key: str | None = Header(default=None),
):
    if (refused := _refused(x_key)) is not None:
        return refused
    if not (CHANNEL_ID.match(c) or HANDLE.match(c)):
        return _error(422, "geen kanaal")
    try:
        return await asyncio.to_thread(channel, c, n)
    except ValueError as exc:
        return _error(404, str(exc)[:200])
    except Exception as exc:  # noqa: BLE001
        return _error(502, f"kanaal ophalen mislukt: {str(exc)[:200]}")


@app.post("/mp3")
async def mp3(body: Mp3In, x_key: str | None = Header(default=None)):
    if (refused := _refused(x_key)) is not None:
        return refused
    async with _slots:
        try:
            return await asyncio.to_thread(fetch, body.id, str(body.job), body.kind, body.sponsor)
        except ValueError as exc:
            return _error(422, str(exc)[:200])
        except Exception as exc:  # noqa: BLE001
            return _error(502, f"downloaden mislukt: {str(exc)[:200]}")


@app.delete("/mp3/{job}", status_code=204)
async def forget(job: uuid.UUID, x_key: str | None = Header(default=None)):
    if (refused := _refused(x_key)) is not None:
        return refused
    shutil.rmtree(_out() / str(job), ignore_errors=True)
    return None


def main() -> None:
    import uvicorn

    port = int(os.environ.get("YOUTUBE_PORT", "8090"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=False)  # noqa: S104


if __name__ == "__main__":
    main()
