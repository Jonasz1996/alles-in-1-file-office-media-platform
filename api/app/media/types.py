"""De gedeelde typen van media (bouwontwerp fase 5, §3.3 en §3.4). Frozen.

De pakketten praten alleen via deze klassen. web/src/media/types.ts heeft dezelfde vormen voor
wat over de API gaat.

Tijd: alles in mediatijd, in ms vanaf het begin van de bron na het aftrekken van de start S
(start_time van ffprobe). De uitvoer van de worker staat op mediatijd + 1 s (-output_ts_offset
1 − S, §3.3); de app trekt die 1 s er weer af. EXTINF, map.json en de voortgang gebruiken
allemaal mediatijd (spike punt 12, zie SegmentGrid).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.media.worker.args import AudioEncode

StreamType = Literal["video", "audio", "subtitle", "data", "attachment"]
Hdr = Literal["none", "hdr10", "hlg", "dv5"]

# De vaste codes van Keuze 19
Reason = Literal[
    "container",
    "video_codec",
    "audio_codec",
    "audio_track",
    "subtitle_burn",
    "hdr",
    "dolby_vision",
    "interlaced",
    "quality",
    "bitrate",
    "not_smooth",
    "keyframes_missing",
    "gop_too_long",
    "too_large",
    "budget",
    "no_worker",
]
REASONS: tuple[str, ...] = Reason.__args__  # type: ignore[attr-defined]

TEXT_SUBTITLES = frozenset({"subrip", "ass", "ssa", "webvtt", "mov_text", "text"})
IMAGE_SUBTITLES = frozenset({"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"})


class Color(BaseModel):
    primaries: str | None = None
    transfer: str | None = None
    matrix: str | None = None


class Stream(BaseModel):
    """Eén stream uit de probe, opgeschoond (Keuze 15). index is de index van ffprobe (de
    `0:{i}` van -map). Plus attached_pic (een hoes is geen videospoor, Keuze 19), en de duur en
    start per stream: het aantal segmenten komt uit het laatste videobeeld, niet uit de duur van
    het formaat (spike punt 10)."""

    model_config = ConfigDict(extra="ignore")

    index: int
    type: StreamType
    codec: str | None = None
    codec_string: str | None = None  # RFC 6381 (Keuze 16); None als de worker het niet weet
    profile: str | None = None
    level: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    pix_fmt: str | None = None
    bit_depth: int | None = None
    color: Color | None = None
    hdr: Hdr = "none"
    dv_profile: int | None = None  # Dolby Vision: 5, 7, 8 (de basislaag staat in hdr)
    hdr_meta: dict | None = None  # mastering en MaxCLL van het eerste beeld (Keuze 15)
    interlaced: bool = False
    channels: int | None = None
    layout: str | None = None
    sample_rate: int | None = None
    lang: str | None = None
    title: str | None = None
    default: bool = False
    forced: bool = False
    hearing_impaired: bool = False
    bitrate: int | None = None
    attached_pic: bool = False
    duration_ms: int | None = None
    start_ms: int | None = None
    # Video: de tijd van het laatste beeld in mediatijd, als de probe die weet (spike punt 10)
    last_frame_ms: int | None = None


class Chapter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start_ms: int
    end_ms: int
    title: str | None = None


class Probe(BaseModel):
    """De kolommen van media_probe zonder de bytes van de keyframe-index. blob_sha is leeg
    zolang de probe nog niet bij een blob hoort (het antwoord van de worker)."""

    model_config = ConfigDict(extra="ignore")

    blob_sha: str = ""
    version: int = 1
    state: Literal["ready", "failed"] = "ready"
    container: str | None = None
    duration_ms: int | None = None
    bit_rate: int | None = None
    start_ms: int = 0
    streams: list[Stream] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)
    keyframes_state: Literal["none", "ready", "sparse", "failed"] = "none"
    max_gop_ms: int | None = None
    subs_state: str = "none"
    trick_state: str = "none"
    trick: dict | None = None
    audio_state: str = "none"
    audio: dict | None = None
    truncated: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    probed_at: datetime | None = None

    def video(self) -> Stream | None:
        """V: het eerste videospoor dat geen hoes is (Keuze 19)."""
        return next((s for s in self.streams if s.type == "video" and not s.attached_pic), None)

    def audio_streams(self) -> list[Stream]:
        return [s for s in self.streams if s.type == "audio"]

    def subtitles(self) -> list[Stream]:
        return [s for s in self.streams if s.type == "subtitle"]


class WorkerCaps(BaseModel):
    """Het antwoord op caps (Keuze 12), uit de cache van de worker."""

    model_config = ConfigDict(extra="ignore")

    protocol: int
    ffmpeg: str | None = None  # de versie, of None als ffmpeg ontbreekt
    ffprobe: str | None = None
    encoders: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    demuxers: list[str] = Field(default_factory=list)  # van de witte lijst, en lavfi (devices)
    protocols: list[str] = Field(default_factory=list)
    hwaccels: list[str] = Field(default_factory=list)
    dri: bool = False
    missing: list[str] = Field(default_factory=list)  # wat §3.3 nodig heeft en er niet is
    sandbox: Literal["on", "off"] = "off"
    mode: Literal["container", "local"] = "local"
    max_procs: int = 10
    max_transcodes: int = 3
    places: dict[str, int] = Field(default_factory=dict)  # per soort de grens
    in_use: dict[str, int] = Field(default_factory=dict)  # per soort nu bezet
    procs: int = 0  # processen die nu draaien


# Keuze 20: alleen strings die op een bekend patroon passen
_CODEC = re.compile(
    r"^(?:avc1\.[0-9A-Fa-f]{6}"
    r"|hvc1\.[0-9A-Za-z.]{1,40}|hev1\.[0-9A-Za-z.]{1,40}"
    r"|dvh1\.[0-9]{2}\.[0-9]{2}|dvhe\.[0-9]{2}\.[0-9]{2}"
    r"|vp09\.[0-9]{2}\.[0-9]{2}\.[0-9]{2}(?:\.[0-9]{2}){0,5}|vp8|vp9"
    r"|av01\.[0-9]\.[0-9]{2}[MH]\.[0-9]{2}(?:\.[0-9.]{1,20})?"
    r"|mp4a\.40\.[0-9]{1,2}|mp4a\.6[0-9]|ac-3|ec-3|opus|flac|mp3|vorbis|alac)$"
)
_FILE_MIME = re.compile(r"^(?:video|audio)/[a-z0-9.+-]{1,40}$")
PROFILE_MAX_BYTES = 8192
PROFILE_MAX_STRINGS = 64
PROFILE_BASES = ("web", "ios-safari", "webos-5", "android-exo", "windows-webview2")


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CodecSupport(_Loose):
    mse: bool = False
    file: bool = False
    smooth: bool | None = None
    power: bool | None = None


class ProfileClient(_Loose):
    kind: Literal["web", "tv", "android", "windows", "other"] = "web"
    name: Annotated[str, Field(max_length=64)] = ""
    app: Annotated[str, Field(max_length=32)] = ""


class ProfileEngines(_Loose):
    mse: bool = False
    mms: bool = False
    native_hls: bool = False


class ProfileHdr(_Loose):
    pq: bool = False
    hlg: bool = False
    dv: bool = False
    screen: bool = False


class ProfileMax(_Loose):
    height: int = Field(default=1080, ge=144, le=4320)
    bitrate_kbps: int = Field(default=0, ge=0, le=1_000_000)


class ProfileAudio(_Loose):
    channels: int = Field(default=2, ge=1, le=8)
    track_switch: bool = False


class ClientProfile(_Loose):
    """Keuze 20: versie 1, alleen strings op een bekend patroon, hooguit 64, het geheel
    hooguit 8 KB; onbekende velden gaan weg. Het profiel is geen beveiliging."""

    v: Literal[1] = 1
    base: Literal["web", "ios-safari", "webos-5", "android-exo", "windows-webview2"] = "web"
    client: ProfileClient = Field(default_factory=ProfileClient)
    engines: ProfileEngines = Field(default_factory=ProfileEngines)
    file: list[str] = Field(default_factory=list)
    codecs: dict[str, CodecSupport] = Field(default_factory=dict)
    hdr: ProfileHdr = Field(default_factory=ProfileHdr)
    max: ProfileMax = Field(default_factory=ProfileMax)
    audio: ProfileAudio = Field(default_factory=ProfileAudio)

    @model_validator(mode="before")
    @classmethod
    def _grootte(cls, data: Any) -> Any:
        if isinstance(data, (bytes, str)) and len(data) > PROFILE_MAX_BYTES:
            raise ValueError("profiel groter dan 8 KB")
        if isinstance(data, dict):
            import json

            if len(json.dumps(data, separators=(",", ":"), default=str)) > PROFILE_MAX_BYTES:
                raise ValueError("profiel groter dan 8 KB")
        return data

    @field_validator("file", mode="before")
    @classmethod
    def _mimes(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        good = [v for v in value if isinstance(v, str) and _FILE_MIME.match(v)]
        return list(dict.fromkeys(good))[:16]

    @field_validator("codecs", mode="before")
    @classmethod
    def _strings(cls, value: Any) -> dict:
        if not isinstance(value, dict):
            return {}
        good = {k: v for k, v in value.items() if isinstance(k, str) and _CODEC.match(k)}
        return dict(list(good.items())[:PROFILE_MAX_STRINGS])


def codec_string_ok(value: str) -> bool:
    """Past deze string op een bekend patroon (Keuze 20)?"""
    return bool(_CODEC.match(value))


class Choice(BaseModel):
    """Wat de gebruiker koos (Keuze 19). subtitle: "e<index>" (ingebed) of "x<n>" (los)."""

    audio: int | None = None
    subtitle: Annotated[str, Field(pattern=r"^[ex][0-9]{1,3}$")] | None = None
    quality: Literal["auto", "source", 1080, 720, 480] = "auto"
    start_ms: int = Field(default=0, ge=0)
    hdr: Literal["tonemap", "raw"] = "tonemap"


class Policy(BaseModel):
    """Ingelogd of publieke link, en wat dan mag."""

    public: bool
    transcode: bool
    max_height: int


class Rung(BaseModel):
    """Een trede van de ladder (Keuze 24)."""

    height: int
    preset: Literal["veryfast", "superfast"]
    codec: Literal["h264", "vp9"]


class Plan(BaseModel):
    """De uitkomst van decide (Keuze 19). places: 1, of 2 voor een zwaar plan (Keuze 26)."""

    mode: Literal["direct", "remux", "transcode", "audio"]
    reasons: list[Reason] = Field(default_factory=list)
    video: Literal["copy"] | Rung | None = None
    audio: Literal["copy", "none"] | AudioEncode | None = None
    burn: int | None = None
    tonemap: Literal["pq", "hlg"] | None = None
    forced_available: str | None = None
    cost: float = 0
    places: Literal[1, 2] = 1


class BudgetView(BaseModel):
    """Wat er nu past, voor decide (admission.view, Keuze 26). rungs: de treden die nu passen,
    beste eerst; leeg is busy met reason."""

    budget: float  # B in cores (na de rem voor office)
    in_use: float  # cores van de lopende plannen
    places_free: int  # van max_transcodes
    user_free: int  # transcodes die deze gebruiker nog mag
    link_free: int | None = None  # bij een publieke link
    factor: float = 1.0  # f van de meting
    factor_dec: float = 1.0  # f_dec
    disk_ok: bool = True
    reason: Literal["cpu", "disk", "user", "link", "worker"] | None = None
    retry_after: float = 2


class Grant(BaseModel):
    """Een reservering van de toelating (Keuze 26). Houdt ook de plaats in de worker vast."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    link_id: uuid.UUID | None = None
    mode: Literal["direct", "remux", "transcode", "audio"]
    cost: float = 0
    places: int = 0


class AdmissionView(BaseModel):
    """Voor /beheer (admission.snapshot)."""

    budget: float = 0
    in_use: float = 0
    max_transcodes: int = 0
    places_used: int = 0
    grants: list[Grant] = Field(default_factory=list)


class SessionView(BaseModel):
    """Eén sessie voor /beheer (sessions.list); nooit de sid zelf (Keuze 35)."""

    id: uuid.UUID
    user_id: uuid.UUID
    node_id: uuid.UUID | None = None
    link_id: uuid.UUID | None = None
    mode: Literal["direct", "remux", "transcode", "audio"]
    rung: Rung | None = None
    created_at: datetime
    last_seen: datetime
    reasons: list[str] = Field(default_factory=list)


class SegmentGrid(BaseModel):
    """Het rooster van een stroom (spike punten 10 en 12).

    De referentie is mediatijd: segment k loopt van origin_ms + k·grid_ms tot het volgende, en
    EXTINF en map.json tellen vanaf 0 in mediatijd. Met S ≠ 0 en -copyts vallen de keyframes op
    brontijd 2k, dus op mediatijd 2k − S: origin_ms = −S (in ms). Het eerste segment is dan
    korter (of k begint bij 1 als −S < 0 het eerste leeg maakt); count komt uit de tijd van het
    laatste videobeeld, en een fragment voorbij count hoort bij het laatste segment."""

    grid_ms: int = 2000
    origin_ms: int = 0
    count: int
    last_frame_ms: int

    def start_of(self, k: int) -> int:
        return max(0, self.origin_ms + k * self.grid_ms)

    def segment_of(self, t_ms: int) -> int:
        return min(self.count - 1, max(0, (t_ms - self.origin_ms) // self.grid_ms))


class StreamInfo(BaseModel):
    """Wat streams.open teruggeeft en play doorgeeft (§3.4): de adressen onder
    /api/v1/stream/{sid}/, de mime met codecs, de offset en de segmenten."""

    mode: Literal["direct", "remux", "transcode", "audio"]
    url: str  # master.m3u8, of file bij direct, of audio.mp4
    mime: str
    duration_ms: int
    offset_ms: int = 0  # timestampOffset van de speler (spike punt 17: −1 s)
    init_gen: int = 1
    segments: int = 0
    grid: SegmentGrid | None = None
    map_url: str | None = None
    sprites_url: str | None = None
    subtitles: list[dict] = Field(default_factory=list)


class Card(BaseModel):
    """Een kaart op /media/home (Keuze 36): een item met zijn voortgang."""

    node_id: uuid.UUID
    library_id: uuid.UUID | None = None
    title: str
    kind: str
    poster: str | None = None
    position_ms: int = 0
    duration_ms: int | None = None
    watched: bool = False
    updated_at: datetime | None = None


class LoadtestFile(BaseModel):
    """Een bron voor de belastingtest (§4.6): een bestand in media/loadtest/ dat de worker met
    synth maakte. Alleen met PLATFORM_MEDIA_LOADTEST en een beheerder."""

    name: Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,40}\.mkv$")]
    path: Path
    size: int
    sha: str  # een eigen sleutel (geen blob), zodat de cache per bron werkt
