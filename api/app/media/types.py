"""De gedeelde typen van media (bouwontwerp fase 5, §3.3 en §3.4). Frozen.

De pakketten praten alleen via deze klassen. web/src/media/types.ts heeft dezelfde vormen voor
wat over de API gaat.

Tijd: alles in mediatijd, in ms vanaf het begin van de bron na het aftrekken van de start S
(start_time van ffprobe). De uitvoer van de worker staat op mediatijd + 1 s (-output_ts_offset
1 − S, §3.3); de app trekt die 1 s er weer af. EXTINF, map.json en de voortgang gebruiken
allemaal mediatijd (spike punt 12, zie SegmentGrid: het rooster hangt aan het eerste videobeeld).
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
    # on: de zelftest van Keuze 11 slaagde (M1); off: de lokale worker; unchecked: niet lokaal,
    # maar (nog) zonder zelftest. Alleen on is "afgeschermd"
    sandbox: Literal["on", "off", "unchecked"] = "off"
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
    """Het rooster van een stroom bij transcode (Keuze 22; spike punten 10 en 12, en "Extra" in
    docs/fase-5/spike.md).

    De referentie is mediatijd. Het rooster van 2 s hangt aan het eerste videobeeld: origin_ms =
    start_time van het videospoor − S, in hele ms naar beneden, en dus altijd ≥ 0 (meestal 21,
    de voorloop van AAC). Segment 0 loopt van 0 tot origin_ms + grid_ms (iets langer dan 2 s),
    segment k ≥ 1 van origin_ms + k·grid_ms tot het volgende; een herstart op k begint met
    -ss (origin + 2k). EXTINF en map.json tellen in mediatijd met dit rooster. count komt uit de
    tijd van het laatste videobeeld: ⌊(last_frame_ms − origin_ms) / grid_ms⌋ + 1 (count_for);
    een fragment voorbij count hoort bij het laatste segment."""

    grid_ms: int = Field(default=2000, gt=0)
    origin_ms: int = Field(default=0, ge=0)
    count: int = Field(ge=1)
    last_frame_ms: int = Field(ge=0)

    @staticmethod
    def count_for(origin_ms: int, last_frame_ms: int, grid_ms: int = 2000) -> int:
        """N uit het laatste videobeeld (spike punt 10), niet uit de duur van het formaat."""
        return max(0, last_frame_ms - origin_ms) // grid_ms + 1

    def start_of(self, k: int) -> int:
        """Het begin van segment k in mediatijd: 0 voor het eerste, anders op het rooster."""
        return 0 if k <= 0 else self.origin_ms + k * self.grid_ms

    def segment_of(self, t_ms: int) -> int:
        """Het segment bij tijd t (mediatijd): nooit voor het eerste, nooit voorbij het laatste."""
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


class LoadtestFile(BaseModel):
    """Een bron voor de belastingtest (§4.6): een bestand in media/loadtest/ dat de worker met
    synth maakte. Alleen met PLATFORM_MEDIA_LOADTEST en een beheerder."""

    name: Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,40}\.mkv$")]
    path: Path
    size: int
    sha: str  # een eigen sleutel (geen blob), zodat de cache per bron werkt


# --- De vormen van de API (§2 en §3.4) -----------------------------------------------------------
#
# Wat de eindpunten van de pakketten nemen en geven. web/src/media/types.ts heeft dezelfde namen
# en velden. M0 zet de routes met deze vormen neer (503 media_off tot het pakket hem vult);
# fake_stream.py geeft precies deze vormen. Tijden in ms mediatijd, datums ISO 8601 in UTC.

LibraryKind = Literal["movies", "shows", "music", "videos"]
LIBRARY_KINDS: tuple[str, ...] = LibraryKind.__args__  # type: ignore[attr-defined]
ScanState = Literal["idle", "queued", "running", "failed"]
ItemKind = Literal["movie", "episode", "track", "video", "extra"]
CardKind = Literal["movie", "episode", "track", "video", "extra", "show"]
ExtraKind = Literal["trailer", "featurette", "deleted", "behind", "sample", "other"]
MatchState = Literal["local", "matched", "review", "manual"]
AnalysisStep = Literal["probe", "keyframes", "subs", "sprites", "audio_info"]
AnalysisState = Literal["none", "pending", "ready", "sparse", "failed", "skip"]
Mode = Literal["direct", "remux", "transcode", "audio"]
Quality = Literal["auto", "source", 1080, 720, 480]
QUALITIES: tuple = ("auto", "source", 1080, 720, 480)
SubtitleState = Literal["ready", "pending", "failed", "burn"]
ProgressKind = Literal["tick", "pause", "seek", "stop", "end"]
Provider = Literal["tmdb", "musicbrainz"]
WorkerHealthState = Literal["starting", "ok", "down", "ffmpeg_missing"]
ImportState = Literal["queued", "running", "done", "failed", "stopped"]
BusyReason = Literal["cpu", "disk", "user", "link", "worker"]

PROGRESS_BATCH_MAX = 1000
PLAYLIST_MAX = 5000
ITEMS_PAGE = 60  # standaard limit van /libraries/{id}/items
INIT_HEADER = "X-Init"


# M2: bibliotheken en items


class Library(BaseModel):
    """GET /api/v1/media/libraries (een regel)."""

    id: uuid.UUID
    name: str
    kind: LibraryKind
    root_id: uuid.UUID  # de map in de boom
    mine: bool  # van jou; anders zie je hem via een deling van de map
    item_count: int = 0
    scan_state: ScanState = "idle"
    scanned_at: datetime | None = None
    online: bool = False  # online opzoeken (Keuze 39)


class LibraryIn(BaseModel):
    """POST /api/v1/media/libraries → 201 Library."""

    root_id: uuid.UUID
    kind: LibraryKind
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    online: bool | None = None


class LibraryPatch(BaseModel):
    """PATCH /api/v1/media/libraries/{id} → 200 Library."""

    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    online: bool | None = None
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")] | None = None


class CardProgress(BaseModel):
    position_ms: int
    duration_ms: int


class Card(BaseModel):
    """Een kaart in een lijst (§2 M2): {node_id, title, year, kind, poster, progress, watched},
    plus de bibliotheek, en bij een serie of aflevering de serie. node_id: het bestand; bij een
    serie de map van de serie (None als die er niet meer is). progress None: nog niet begonnen
    of gezien. Ook de rijen van progress.home_rows (M7)."""

    node_id: uuid.UUID | None
    library_id: uuid.UUID | None = None
    title: str
    year: int | None = None
    kind: CardKind
    poster: str | None = None  # een ondertekend adres (previews.info of art/)
    progress: CardProgress | None = None
    watched: bool = False
    show_id: uuid.UUID | None = None
    season: int | None = None
    episode: int | None = None


class MediaHome(BaseModel):
    """GET /api/v1/media/home. continue is een sleutelwoord in Python: het veld heet
    continue_ en gaat als "continue" over de API."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    libraries: list[Library] = Field(default_factory=list)
    continue_: list[Card] = Field(default_factory=list, alias="continue")
    next_up: list[Card] = Field(default_factory=list)
    recent: list[Card] = Field(default_factory=list)


class CardPage(BaseModel):
    """GET /api/v1/media/libraries/{id}/items; next: de cursor, of None aan het eind."""

    items: list[Card] = Field(default_factory=list)
    next: str | None = None


class StreamBrief(BaseModel):
    index: int
    type: Literal["video", "audio", "subtitle"]
    codec: str
    lang: str | None = None
    title: str | None = None


class SubtitleBrief(BaseModel):
    """Een ondertitel bij een item: ingebed (e<index>) of los (x<i>, Keuze 29). image: alleen
    ingebrand (PGS, VobSub, DVB)."""

    id: Annotated[str, Field(pattern=r"^[ex][0-9]{1,3}$")]
    lang: str | None = None
    label: str
    forced: bool = False
    sdh: bool = False
    kind: Literal["text", "image"]


class ItemPart(BaseModel):
    node_id: uuid.UUID
    part: int


class Item(Card):
    """GET /api/v1/media/items/{node_id}: Card plus de details (§2 M2)."""

    node_id: uuid.UUID
    library_id: uuid.UUID
    overview: str | None = None
    backdrop: str | None = None
    duration_ms: int | None = None
    streams: list[StreamBrief] = Field(default_factory=list)
    subtitles: list[SubtitleBrief] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    match_state: MatchState = "local"
    can_edit: bool = False  # mag verbeteren en koppelen (schrijfrecht, Keuze 43)
    parts: list[ItemPart] = Field(default_factory=list)
    analysis: dict[AnalysisStep, AnalysisState] = Field(default_factory=dict)
    extra: ExtraKind | None = None
    error: str | None = None  # de reden bij een mislukte probe (Keuze 46)


class Show(BaseModel):
    id: uuid.UUID
    library_id: uuid.UUID
    title: str
    year: int | None = None
    overview: str | None = None
    poster: str | None = None
    backdrop: str | None = None
    match_state: MatchState = "local"
    can_edit: bool = False


class ShowSeason(BaseModel):
    season: int | None  # None: absolute nummering (Keuze 8)
    episodes: list[Card] = Field(default_factory=list)


class ShowDetail(BaseModel):
    """GET /api/v1/media/shows/{show_id}."""

    show: Show
    seasons: list[ShowSeason] = Field(default_factory=list)


# M3: profiel, afspelen en sessies


class CodecsOut(BaseModel):
    """GET /api/v1/media/{node_id}/codecs: de vaste kandidaten plus de strings van dit item."""

    candidates: list[str] = Field(default_factory=list)
    item: list[str] = Field(default_factory=list)


class PlayIn(BaseModel):
    """POST /api/v1/media/{node_id}/play en /api/v1/public/media/{node_id}/play (de body).
    replace: de sid van de sessie die in dezelfde beurt dichtgaat (ander spoor of andere
    kwaliteit)."""

    profile: ClientProfile
    audio: int | None = Field(default=None, ge=0, le=999)
    subtitle: Annotated[str, Field(pattern=r"^[ex][0-9]{1,3}$")] | None = None
    quality: Quality = "auto"
    start_ms: int = Field(default=0, ge=0)
    hdr: Literal["raw"] | None = None
    replace: Annotated[str, Field(max_length=200)] | None = None

    def choice(self) -> Choice:
        return Choice(
            audio=self.audio,
            subtitle=self.subtitle,
            quality=self.quality,
            start_ms=self.start_ms,
            hdr="raw" if self.hdr == "raw" else "tonemap",
        )


class AudioTrack(BaseModel):
    index: int
    lang: str | None = None
    title: str | None = None
    codec: str
    channels: int | None = None
    default: bool = False


class SubtitleTrack(BaseModel):
    """Een ondertitel in de sessie: subs/{id}.vtt geeft 202 met Retry-After: 5 zolang hij
    pending is; burn: een beeldspoor, alleen ingebrand."""

    id: Annotated[str, Field(pattern=r"^[ex][0-9]{1,3}$")]
    lang: str | None = None
    label: str
    forced: bool = False
    sdh: bool = False
    default: bool = False
    state: SubtitleState = "ready"


class TrickInfo(BaseModel):
    """De sprites voor de zoekbalk (Keuze 30): trick.vtt met relatieve adressen."""

    url: str
    interval_s: float
    width: int
    height: int


class Gapless(BaseModel):
    """Voorloop en lengte in samples (Keuze 18, 38). MP3 als audio/mpeg: delay = enc_delay,
    zonder +529 (spike punt 13)."""

    rate: int
    delay: int
    samples: int


class PlayUrls(BaseModel):
    """Onder /api/v1/stream/{sid}/: file (direct), map en master (MSE en native HLS, remux en
    transcode), of audio (mode audio)."""

    model_config = ConfigDict(extra="forbid")

    file: str | None = None
    map: str | None = None
    master: str | None = None
    audio: str | None = None


class PlayTracks(BaseModel):
    audio: list[AudioTrack] = Field(default_factory=list)
    subtitles: list[SubtitleTrack] = Field(default_factory=list)


class QualityInfo(BaseModel):
    current: Quality
    options: list[Quality] = Field(default_factory=list)


class PlayOut(BaseModel):
    """POST …/play → 200. sid: <naam van de node>.<24 willekeurige bytes>, een sleutel in het
    pad; nooit in een log. offset: de timestampOffset van de speler (−1: tfdt = mediatijd + 1 s,
    spike punt 17). started_at gaat mee met elke PUT van de voortgang (Keuze 36)."""

    sid: str
    mode: Mode
    reasons: list[Reason] = Field(default_factory=list)
    urls: PlayUrls
    mime: str
    duration_ms: int
    start_ms: int = 0
    offset: float = 0
    tracks: PlayTracks = Field(default_factory=PlayTracks)
    chapters: list[Chapter] = Field(default_factory=list)
    credits_ms: int | None = None
    trick: TrickInfo | None = None
    forced_available: str | None = None
    quality: QualityInfo
    gapless: Gapless | None = None
    started_at: datetime
    expires_at: datetime


class MediaMap(BaseModel):
    """GET /api/v1/stream/{sid}/map.json (no-store). segments: het rooster bij transcode, of
    [begin_ms, duur_ms] per segment uit de keyframe-index bij remux (Keuze 22, 23)."""

    gen: int
    mime: str
    offset: float
    init: str  # "init.mp4?g=1", relatief aan /api/v1/stream/{sid}/
    segments: SegmentGrid | list[tuple[int, int]]


# M7: voortgang


class ProgressIn(BaseModel):
    """PUT /api/v1/media/progress/{node_id} → 204 | 429 rate_limited | 404 not_found."""

    position_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    event: ProgressKind
    origin: Annotated[str, Field(min_length=1, max_length=64)]
    seq: int = Field(ge=0)
    started_at: datetime
    audio: int | None = None
    subtitle: Annotated[str, Field(pattern=r"^[ex][0-9]{1,3}$")] | None = None


class ProgressBatchIn(BaseModel):
    """POST /api/v1/media/progress/batch: node_ids (≤ 1.000), of een serie met een seizoen."""

    node_ids: Annotated[list[uuid.UUID], Field(max_length=PROGRESS_BATCH_MAX)] | None = None
    show_id: uuid.UUID | None = None
    season: int | None = None
    watched: bool

    @model_validator(mode="after")
    def _een_van_beide(self) -> ProgressBatchIn:
        if (self.node_ids is None) == (self.show_id is None):
            raise ValueError("node_ids of show_id, niet allebei")
        if self.season is not None and self.show_id is None:
            raise ValueError("season hoort bij show_id")
        return self


class ProgressBatchOut(BaseModel):
    updated: int


class PlaybackProgressEvent(BaseModel):
    """playback.progress, alleen naar de gebruiker zelf (Keuze 36)."""

    node_id: uuid.UUID
    position_ms: int
    duration_ms: int
    watched: bool
    origin: str
    updated_at: datetime


# M8: metadata online


class MatchCandidate(BaseModel):
    """GET /api/v1/media/items/{node_id}/candidates?q=&year= (een regel)."""

    provider: Provider
    id: str
    title: str
    year: int | None = None
    original_title: str | None = None
    overview: str | None = None
    poster: str | None = None


class MatchIn(BaseModel):
    """POST …/items/{node_id}/match en …/shows/{show_id}/match. id bijvoorbeeld "movie:603"."""

    provider: Provider
    id: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9:_-]+$")]


class ItemPatch(BaseModel):
    """PATCH /api/v1/media/items/{node_id} → Item. unlock: velden die weer vrij mogen."""

    title: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    sort_title: Annotated[str, Field(max_length=500)] | None = None
    year: int | None = Field(default=None, ge=1800, le=2200)
    overview: Annotated[str, Field(max_length=10_000)] | None = None
    season: int | None = Field(default=None, ge=0, le=9999)
    episode: int | None = Field(default=None, ge=0, le=99_999)
    unlock: list[str] | None = None


# M9: muziek


class AlbumCard(BaseModel):
    id: uuid.UUID
    library_id: uuid.UUID
    title: str
    album_artist: str
    year: int | None = None
    compilation: bool = False
    cover: str | None = None
    track_count: int = 0
    duration_ms: int = 0


class AlbumPage(BaseModel):
    """GET /api/v1/media/libraries/{id}/albums?sort=&cursor=."""

    items: list[AlbumCard] = Field(default_factory=list)
    next: str | None = None


class Track(BaseModel):
    node_id: uuid.UUID
    title: str
    artist: str | None = None
    album_id: uuid.UUID | None = None
    disc: int | None = None
    track: int | None = None
    duration_ms: int | None = None


class AlbumInfo(AlbumCard):
    mbid: str | None = None
    match_state: MatchState = "local"


class AlbumDetail(BaseModel):
    """GET /api/v1/media/albums/{id}."""

    album: AlbumInfo
    tracks: list[Track] = Field(default_factory=list)


class Artist(BaseModel):
    """GET /api/v1/media/libraries/{id}/artists (een regel); key genormaliseerd (Keuze 37)."""

    key: str
    name: str
    album_count: int = 0


class ArtistDetail(BaseModel):
    """GET /api/v1/media/artists/{key}?library=."""

    name: str
    albums: list[AlbumCard] = Field(default_factory=list)


class Playlist(BaseModel):
    id: uuid.UUID
    name: str
    item_count: int = 0
    duration_ms: int = 0


class PlaylistIn(BaseModel):
    """POST /api/v1/media/playlists → 201 Playlist, en PATCH …/{id}."""

    name: Annotated[str, Field(min_length=1, max_length=200)]


class PlaylistItem(BaseModel):
    id: uuid.UUID
    node_id: uuid.UUID
    track: Track


class PlaylistDetail(BaseModel):
    """GET /api/v1/media/playlists/{id}; een nummer dat je niet meer mag lezen staat er niet in."""

    playlist: Playlist
    items: list[PlaylistItem] = Field(default_factory=list)


class PlaylistItemsIn(BaseModel):
    """POST /api/v1/media/playlists/{id}/items → {items} | 409 media_playlist_full {max}."""

    node_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=PLAYLIST_MAX)]
    position: int | None = Field(default=None, ge=0)


class PlaylistItemsOut(BaseModel):
    items: list[PlaylistItem] = Field(default_factory=list)


class PlaylistOrderIn(BaseModel):
    """PUT /api/v1/media/playlists/{id}/order → 204."""

    item_ids: Annotated[list[uuid.UUID], Field(max_length=PLAYLIST_MAX)]


# M10: beheer


class MediaConfigIn(BaseModel):
    """PUT /api/v1/admin/media/config: MediaConfig zonder bench (alleen lezen), elk veld los."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    cpu_budget: float | None = Field(default=None, ge=0.5, le=16)
    max_transcodes: int | None = Field(default=None, ge=0, le=16)
    max_user_transcodes: int | None = Field(default=None, ge=1, le=16)
    public_transcode: bool | None = None
    hdr_1080: bool | None = None
    cache_gb: int | None = Field(default=None, ge=1, le=10_000)
    musicbrainz: bool | None = None


class UserRef(BaseModel):
    id: uuid.UUID
    display_name: str


class AdminSessionView(SessionView):
    """Een sessie op /beheer: SessionView plus wat M10 erbij zet (Keuze 45)."""

    user: UserRef | None = None
    title: str | None = None
    speed: float | None = None  # × realtime
    ahead_s: float | None = None  # voorsprong in mediatijd


class Selftest(BaseModel):
    ok: bool
    sandbox: Literal["on", "off"]
    problems: list[str] = Field(default_factory=list)


class PlaceUse(BaseModel):
    used: int = 0
    max: int = 0


class WorkerView(BaseModel):
    mode: Literal["container", "local"]
    state: WorkerHealthState
    since: datetime
    selftest: Selftest | None = None
    places: dict[str, PlaceUse] = Field(default_factory=dict)  # per WorkClass


class FfmpegView(BaseModel):
    version: str | None = None
    encoders: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    hwaccel: list[str] = Field(default_factory=list)
    gpu: str | None = None


class BudgetUse(BaseModel):
    used: float = 0
    total: float = 0
    ds_starting: bool = False  # B − 1,0 zolang de document server start (Keuze 26)


class ReasonDay(BaseModel):
    day: str  # YYYY-MM-DD
    counts: dict[Reason, int] = Field(default_factory=dict)


class JobCounts(BaseModel):
    queued: int = 0
    running: int = 0
    failed: int = 0


class AdminLibrary(Library):
    owner: UserRef
    errors: int = 0
    low_confidence: int = 0


class CacheParts(BaseModel):
    sessions: int = 0
    subs: int = 0
    trick: int = 0
    art: int = 0
    audio: int = 0


class CacheView(BaseModel):
    parts: CacheParts = Field(default_factory=CacheParts)
    free_bytes: int = 0


class ProviderView(BaseModel):
    name: Literal["tmdb", "musicbrainz", "caa"]
    key_set: bool = False
    last_ok: datetime | None = None
    last_error: str | None = None
    breaker: Literal["closed", "open", "half_open"] = "closed"


class SkippedItem(BaseModel):
    path: str
    reason: str


class ImportView(BaseModel):
    """De import van Keuze 2; ook de data van het event media.import (zonder de lijsten)."""

    job: uuid.UUID
    state: ImportState
    source: str
    target_id: uuid.UUID
    owner_id: uuid.UUID
    move: bool = False
    done: int = 0
    total: int = 0
    skipped: int = 0
    bytes: int = 0
    skipped_items: list[SkippedItem] = Field(default_factory=list)


class NoticeView(BaseModel):
    code: str
    params: dict[str, Any] = Field(default_factory=dict)
    text: str


class MediaAdmin(BaseModel):
    """GET /api/v1/admin/media (Keuze 45)."""

    worker: WorkerView
    ffmpeg: FfmpegView | None = None
    bench: dict[str, Any] | None = None  # MediaConfig.bench: {f, f_dec, at}
    budget: BudgetUse = Field(default_factory=BudgetUse)
    sessions: list[AdminSessionView] = Field(default_factory=list)
    reasons: list[ReasonDay] = Field(default_factory=list)
    jobs: JobCounts = Field(default_factory=JobCounts)
    libraries: list[AdminLibrary] = Field(default_factory=list)
    cache: CacheView = Field(default_factory=CacheView)
    providers: list[ProviderView] = Field(default_factory=list)
    import_: ImportView | None = Field(default=None, alias="import")
    config: dict[str, Any] = Field(default_factory=dict)  # MediaConfig
    notices: list[NoticeView] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class CacheClearIn(BaseModel):
    """POST /api/v1/admin/media/cache/clear."""

    what: Literal["sessions", "audio", "trick"]


class TmdbKeyIn(BaseModel):
    """PUT /api/v1/admin/media/tmdb: de sleutel komt nooit terug (Keuze 40)."""

    key: Annotated[str, Field(min_length=1, max_length=1000)]


class ImportSource(BaseModel):
    """GET /api/v1/admin/media/import/sources (een regel); path ten opzichte van /data/import."""

    path: str
    files: int = 0
    bytes: int = 0
    unreadable: int = 0
    unmovable: int = 0


class ImportIn(BaseModel):
    """POST /api/v1/admin/media/import → 202 {job} | 409 media_import_busy | 422
    media_import_path."""

    source: Annotated[str, Field(min_length=1, max_length=4096)]
    target_id: uuid.UUID
    owner_id: uuid.UUID
    move: bool = False


class LoadtestIn(BaseModel):
    """POST /api/v1/admin/media/loadtest (§4.6) → 202 | 409 media_bench_busy."""

    streams: int = Field(default=3, ge=1, le=3)
    seconds: int = Field(default=300, ge=30, le=600)
    codec: Literal["h264", "vp9"] = "h264"
    sources: list[uuid.UUID] | None = None


class LoadtestView(BaseModel):
    """GET /api/v1/admin/media/loadtest: de laatste of lopende belastingtest."""

    state: Literal["idle", "making_sources", "running", "done", "failed", "stopped"] = "idle"
    phase: Literal["a", "b", "c", "d"] | None = None
    started_at: datetime | None = None
    report: dict[str, Any] | None = None


class Accepted(BaseModel):
    """Een antwoord met een taak (202)."""

    job: uuid.UUID


# Gebeurtenissen (SSE, via events.publish, na de commit; §3.4). affectsFolder in de web-app
# negeert ze: node.changed komt al van files.notify.


class LibraryEvent(BaseModel):
    """media.library, naar wie de bibliotheek ziet."""

    library_id: uuid.UUID
    scan_state: ScanState
    item_count: int


class ItemEvent(BaseModel):
    """media.item, naar wie de bibliotheek ziet."""

    node_id: uuid.UUID
    library_id: uuid.UUID


class ImportEvent(BaseModel):
    """media.import, naar beheerders."""

    job: uuid.UUID
    state: ImportState
    done: int
    total: int
    skipped: int
    bytes: int


EVENT_PROGRESS = "playback.progress"
EVENT_LIBRARY = "media.library"
EVENT_ITEM = "media.item"
EVENT_IMPORT = "media.import"
EVENTS: dict[str, type[BaseModel]] = {
    EVENT_PROGRESS: PlaybackProgressEvent,
    EVENT_LIBRARY: LibraryEvent,
    EVENT_ITEM: ItemEvent,
    EVENT_IMPORT: ImportEvent,
}


def event_data(type: str, data: BaseModel) -> dict[str, Any]:
    """De data van een gebeurtenis als JSON, gecontroleerd tegen de vorm van dat type."""
    model = EVENTS[type]
    if not isinstance(data, model):
        raise TypeError(f"{type} verwacht {model.__name__}")
    return data.model_dump(mode="json")
