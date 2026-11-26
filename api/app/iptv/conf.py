"""De instellingen van live tv: tabel setting, rij `iptv_settings` (geen migratie nodig).

    {"playlist": {"kind": "url" | "file", "secret": <geheim>},
     "guide_url": "https://epgiptv.example/guide.xml" | null,
     "max_streams": 2, "video": "auto" | "transcode",
     "last": {"at": "...", "ok": true, "error": null, "channels": 392, "programmes": 51234,
              "skipped": 0, "guide_error": null}}

De m3u (het adres, of bij een geüpload bestand de inhoud) is een geheim: versleuteld zoals de
sleutel van TMDB (`media.providers.secrets.seal`, naam `iptv.playlist`). Hij staat nooit in een
antwoord; view() zegt alleen of hij er is en welke soort. Het adres van de gids is geen geheim
(een openbare XMLTV) en staat er gewoon in.

max_streams: hoeveel kanalen tegelijk (de grens van de aanbieder op verbindingen; elk kanaal is
één verbinding, hoeveel mensen er ook naar kijken). video: auto kopieert H.264 en zet alleen
ander beeld om; transcode zet altijd om (meer CPU, maar elke browser speelt het).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.media.providers.secrets import seal, unseal
from app.models import Setting

SETTING = "iptv_settings"
SECRET = "iptv.playlist"
DEFAULT_MAX_STREAMS = 2
MAX_FILE_BYTES = 10 * 1024 * 1024

Video = Literal["auto", "transcode"]


@dataclass(frozen=True)
class Conf:
    playlist: str | None  # het adres of de inhoud (ontsleuteld)
    playlist_kind: Literal["url", "file"] | None
    guide_url: str | None
    max_streams: int = DEFAULT_MAX_STREAMS
    video: Video = "auto"


async def raw(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(Setting, SETTING)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


async def write(session: AsyncSession, value: dict[str, Any]) -> None:
    row = await session.get(Setting, SETTING)
    if row is None:
        session.add(Setting(key=SETTING, value=value))
    else:
        row.value = value
    await session.commit()


def _max_streams(value: Any) -> int:
    return value if isinstance(value, int) and 1 <= value <= 20 else DEFAULT_MAX_STREAMS


def _video(value: Any) -> Video:
    return "transcode" if value == "transcode" else "auto"


def from_raw(settings: Settings, value: dict[str, Any]) -> Conf:
    entry = value.get("playlist") if isinstance(value.get("playlist"), dict) else {}
    kind = entry.get("kind") if entry.get("kind") in ("url", "file") else None
    playlist = unseal(settings, SECRET, entry.get("secret")) if kind else None
    guide = value.get("guide_url")
    return Conf(
        playlist=playlist,
        playlist_kind=kind if playlist else None,
        guide_url=guide if isinstance(guide, str) and guide else None,
        max_streams=_max_streams(value.get("max_streams")),
        video=_video(value.get("video")),
    )


async def load(session: AsyncSession, settings: Settings) -> Conf:
    return from_raw(settings, await raw(session))


async def view(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    """Wat /beheer toont: nooit de m3u zelf."""
    value = await raw(session)
    conf = from_raw(settings, value)
    last = value.get("last") if isinstance(value.get("last"), dict) else None
    return {
        "playlist_set": conf.playlist is not None,
        "playlist_kind": conf.playlist_kind,
        "guide_url": conf.guide_url,
        "max_streams": conf.max_streams,
        "video": conf.video,
        "last": last,
    }


async def store(
    session: AsyncSession,
    settings: Settings,
    *,
    playlist: str | None,
    playlist_kind: Literal["url", "file"] | None,
    guide_url: str | None,
    max_streams: int,
    video: Video,
) -> None:
    """Bewaren. playlist None: de oude m3u blijft."""
    value = await raw(session)
    if playlist is not None and playlist_kind is not None:
        value["playlist"] = {"kind": playlist_kind, "secret": seal(settings, SECRET, playlist)}
    value["guide_url"] = guide_url
    value["max_streams"] = _max_streams(max_streams)
    value["video"] = _video(video)
    await write(session, value)


async def store_playlist(
    session: AsyncSession, settings: Settings, content: str, kind: Literal["url", "file"]
) -> None:
    value = await raw(session)
    value["playlist"] = {"kind": kind, "secret": seal(settings, SECRET, content)}
    await write(session, value)


async def store_last(session: AsyncSession, last: dict[str, Any]) -> None:
    value = await raw(session)
    value["last"] = last
    await write(session, value)


async def forget(session: AsyncSession) -> None:
    row = await session.get(Setting, SETTING)
    if row is not None:
        await session.delete(row)
        await session.commit()
