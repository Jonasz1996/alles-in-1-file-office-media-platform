"""De beslissing (Keuze 19): een pure functie met vaste regels.

    decide(probe, profile, choice, policy, budget) -> Plan

`profile` is het profiel zoals profile.merge het maakte (basisprofiel plus metingen). V is het
eerste videospoor (geen hoes), A het gekozen audiospoor (anders het spoor met default, anders het
eerste; geen A als de bron geen audio heeft), S de gekozen ondertitel.

De regels, in volgorde:
1. Geen video: het muziekpad (Keuze 38). MP3 gaat rauw (direct, audio/mpeg). Anders mode audio
   als MSE of MMS de codec van audio.mp4 kan (FLAC en AAC gekopieerd, de rest als FLAC), en
   anders direct als de client het bestand zelf speelt.
2. video_ok, 3. audio_ok, 4. subs_ok (een beeldondertitel kiezen is inbranden: transcode; een
   geforceerd beeldspoor gaat nooit vanzelf aan, het plan geeft dan forced_available).
5. Direct: mp4/mov/m4v of webm, de mime in `file` van het profiel, video_ok en audio_ok via
   `file`, subs_ok, en A is het eerste audiospoor of het profiel heeft track_switch.
6. Remux: video_ok via MSE of MMS (of native HLS), een codec die in fMP4 past, de keyframe-index
   er (`ready`) met max_gop_ms ≤ 20 s en bitrate × GOP ≤ 64 MB, en subs_ok. Audio kopiëren als
   dat kan en past, anders AAC 160k (of Opus 128k als AAC niet kan).
7. Transcode als de policy het toestaat en de toelating een trede geeft (Keuze 26).

Weigeringen zijn de fouten van media/errors.py: 415 media_unsupported (te groot, of geen codec
die de client kan), 422 media_hdr (too_large en dolby_vision; no_tonemap ziet de router aan de
caps), 403 media_public_transcode, 409 media_probe_pending (keyframes, bij een publieke link) en
503 media_busy. De reasons van een plan zeggen waarom het niet beter kon: bij remux waarom niet
direct, bij transcode waarom niet remux; plus audio_codec als het geluid omgezet wordt, en budget
als de toelating een lagere trede gaf dan gevraagd.

De kosten (Keuze 26): (tabel(trede, hdr) × fps/24 − d(ref) + extra) × f + d(bron) × f_dec, met
de tabel uit ladder.cost (M4). Een plan boven 1,8 × f telt dubbel (places 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.media import errors as media_errors
from app.media import ladder
from app.media import profile as profiles
from app.media.codecs import vp9_level
from app.media.types import (
    IMAGE_SUBTITLES,
    REASONS,
    BudgetView,
    Choice,
    ClientProfile,
    CodecSupport,
    Plan,
    Policy,
    Probe,
    Rung,
    Stream,
)
from app.media.worker.args import AudioEncode

MAX_PIXELS = 8192 * 4320  # daarboven weigert de probe al (Keuze 15)
HDR_MAX = (1920, 1088)  # 4K HDR live nooit (Keuze 27)
DIRECT_KINDS = {"mp4": "video/mp4", "mov": "video/mp4", "m4v": "video/mp4", "webm": "video/webm"}
FMP4_VIDEO = frozenset({"h264", "hevc", "av1", "vp9"})
FMP4_AUDIO = frozenset({"aac", "opus", "flac", "ac3", "eac3"})  # MP3 in MP4 kan MSE niet (N13)
MAX_GOP_MS = 20_000
MAX_GOP_BYTES = 64 * 1024 * 1024
# De dozen van de treden (Keuze 24): een bron past erin zonder te vergroten
BOXES: dict[int, tuple[int, int]] = {480: (854, 480), 720: (1280, 720), 1080: (1920, 1088)}
LADDER_TOP = 1080
AAC = AudioEncode(codec="aac", channels=2, bitrate_k=160)
OPUS = AudioEncode(codec="libopus", channels=2, bitrate_k=128)
# Keuze 26: decoderen van de bron, per codec voor 1080p24; 10 bit en de rest 0,31
D_CODEC = {"h264": 0.18, "hevc": 0.31, "vp9": 0.18, "av1": 0.25}
D_OTHER = 0.31
D_REF_SDR, D_REF_HDR = 0.18, 0.31
BURN_EXTRA, DEINT_EXTRA = 0.15, 0.2
HEAVY = 1.8  # boven 1,8 × f telt een plan dubbel
FILL = 0.9  # hooguit 0,9 × B
REMUX_COST, AUDIO_COST = 0.02, 0.05
TONEMAP = {"hdr10": "pq", "hlg": "hlg"}
AUDIO_MIME = {
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "mov": "audio/mp4",
    "wav": "audio/wav",
    "aac": "audio/aac",
    "webm": "audio/webm",
    "matroska": "audio/webm",
}
# H.264 per trede (Keuze 24): (level tot 30 fps, level erboven; 480: tot 25 fps)
H264_LEVEL = {1080: (0x28, 0x2A), 720: (0x1F, 0x20), 480: (0x1E, 0x1F)}


@dataclass
class Picked:
    """V, A en S zoals decide ze koos."""

    video: Stream
    audio: Stream | None
    subtitle: Stream | None  # een ingebed spoor; een los bestand (x<i>) is altijd tekst
    burn: Stream | None
    forced: str | None
    reasons: list[str] = field(default_factory=list)


def decide(
    probe: Probe, profile: ClientProfile, choice: Choice, policy: Policy, budget: BudgetView
) -> Plan:
    video = probe.video()
    if video is None:
        return _music(probe, profile, choice)
    if (video.width or 0) * (video.height or 0) > MAX_PIXELS:
        raise media_errors.unsupported(["too_large"])
    p = _pick(probe, video, choice)
    raw = choice.hdr == "raw"
    direct = _direct_reasons(probe, profile, choice, p, raw=raw)
    if not direct:
        return Plan(
            mode="direct",
            video="copy",
            audio="copy" if p.audio else "none",
            forced_available=p.forced,
        )
    remux, audio = _remux(probe, profile, choice, p, raw=raw)
    if not remux:
        extra = ["audio_codec"] if isinstance(audio, AudioEncode) else []
        cost = REMUX_COST + (AUDIO_COST if extra else 0)
        return Plan(
            mode="remux",
            reasons=_sorted(direct + extra),
            video="copy",
            audio=audio,
            forced_available=p.forced,
            cost=round(cost * budget.factor, 4),
        )
    return _transcode(probe, profile, choice, policy, budget, p, remux)


# --- kiezen ------------------------------------------------------------------------------------


def default_audio(probe: Probe) -> Stream | None:
    """A zonder keuze: het spoor met default, anders het eerste."""
    audio = probe.audio_streams()
    return next((s for s in audio if s.default), audio[0] if audio else None)


def pick_audio(probe: Probe, index: int | None) -> Stream | None:
    """A: het gekozen spoor (de index van ffprobe), anders default_audio. Een index die geen
    audiospoor is, geeft None; de router geeft daar al 404 op."""
    if index is None:
        return default_audio(probe)
    return next((s for s in probe.audio_streams() if s.index == index), None)


def embedded_subtitle(probe: Probe, sid: str | None) -> Stream | None:
    """S voor "e<index>"; None voor geen keuze, een los bestand of een onbekend spoor."""
    if not sid or not sid.startswith("e"):
        return None
    index = int(sid[1:])
    return next((s for s in probe.subtitles() if s.index == index), None)


def _forced_image(probe: Probe, audio: Stream | None) -> str | None:
    """Een geforceerd beeldspoor in de taal van A (of zonder taal): nooit vanzelf aan, wel
    aangeboden (Keuze 29)."""
    lang = audio.lang if audio else None
    for s in probe.subtitles():
        if s.forced and s.codec in IMAGE_SUBTITLES and (not lang or not s.lang or s.lang == lang):
            return f"e{s.index}"
    return None


def _pick(probe: Probe, video: Stream, choice: Choice) -> Picked:
    audio = pick_audio(probe, choice.audio)
    if audio is None and choice.audio is not None:
        audio = default_audio(probe)
    sub = embedded_subtitle(probe, choice.subtitle)
    burn = sub if sub is not None and sub.codec in IMAGE_SUBTITLES else None
    forced = None if choice.subtitle else _forced_image(probe, audio)
    return Picked(video, audio, sub, burn, forced)


# --- de toetsen --------------------------------------------------------------------------------


def _engine(profile: ClientProfile) -> bool:
    return profile.engines.mse or profile.engines.mms


def plays(profile: ClientProfile, found: CodecSupport | None, kind: str) -> bool:
    """Speelt deze string als bestand (file) of in segmenten (mse: MSE, MMS of native HLS)?"""
    if found is None:
        return False
    if kind == "file":
        return found.file
    return (found.mse and _engine(profile)) or (profile.engines.native_hls and found.file)


def label(width: int | None, height: int | None) -> int:
    """De kleinste doos van de ladder waar de bron in past (480, 720 of 1080), of 2160."""
    w, h = width or 0, height or 0
    for name, (bw, bh) in sorted(BOXES.items()):
        if w <= bw and h <= bh:
            return name
    return 2160


def _hdr_reasons(profile: ClientProfile, video: Stream, *, raw: bool) -> list[str]:
    if video.hdr == "dv5":
        return [] if profile.hdr.dv else ["dolby_vision"]  # raw kan niet: paars en groen
    if video.hdr == "none" or raw:
        return []
    can = profile.hdr.pq if video.hdr == "hdr10" else profile.hdr.hlg
    return [] if can and profile.hdr.screen else ["hdr"]


def _video_reasons(
    probe: Probe, profile: ClientProfile, choice: Choice, video: Stream, kind: str, *, raw: bool
) -> list[str]:
    reasons: list[str] = []
    found = profiles.support(profile, video.codec_string)
    if not plays(profile, found, kind):
        reasons.append("video_codec")
    elif found is not None and found.smooth is False:
        reasons.append("not_smooth")
    reasons += _hdr_reasons(profile, video, raw=raw)
    if video.interlaced:
        reasons.append("interlaced")
    if isinstance(choice.quality, int) and choice.quality < label(video.width, video.height):
        reasons.append("quality")
    limit = profile.max.bitrate_kbps
    rate = video.bitrate or probe.bit_rate
    if limit and rate and rate > limit * 1000:
        reasons.append("bitrate")
    return reasons


def _audio_plays(profile: ClientProfile, audio: Stream | None, kind: str) -> bool:
    return audio is None or plays(profile, profiles.support(profile, audio.codec_string), kind)


def _direct_reasons(
    probe: Probe, profile: ClientProfile, choice: Choice, p: Picked, *, raw: bool
) -> list[str]:
    reasons: list[str] = []
    kind = DIRECT_KINDS.get(probe.container or "")
    if kind is None or kind not in profile.file:
        reasons.append("container")
    reasons += _video_reasons(probe, profile, choice, p.video, "file", raw=raw)
    if not _audio_plays(profile, p.audio, "file"):
        reasons.append("audio_codec")
    audio = probe.audio_streams()
    if p.audio is not None and p.audio is not audio[0] and not profile.audio.track_switch:
        reasons.append("audio_track")
    if p.burn is not None:
        reasons.append("subtitle_burn")
    return reasons


def _keyframe_reasons(probe: Probe) -> list[str]:
    if probe.keyframes_state != "ready":
        return ["keyframes_missing"]
    gop = probe.max_gop_ms
    if gop is None or gop > MAX_GOP_MS:
        return ["gop_too_long"]
    rate = probe.bit_rate or 0
    if rate and rate / 8 * gop / 1000 > MAX_GOP_BYTES:
        return ["gop_too_long"]
    return []


def _audio_plan(
    profile: ClientProfile, audio: Stream | None
) -> tuple[str | AudioEncode | None, bool]:
    """Het geluid in fMP4: ("none" | "copy" | AudioEncode | None, past het zonder omzetten).
    None: de client kan AAC noch Opus, dus geen enkele vorm."""
    if audio is None:
        return "none", True
    if audio.codec in FMP4_AUDIO and _audio_plays(profile, audio, "mse"):
        return "copy", True
    if plays(profile, profiles.support(profile, "mp4a.40.2"), "mse"):
        return AAC, False
    if plays(profile, profiles.support(profile, "opus"), "mse"):
        return OPUS, False
    return None, False


def _remux(
    probe: Probe, profile: ClientProfile, choice: Choice, p: Picked, *, raw: bool
) -> tuple[list[str], str | AudioEncode | None]:
    reasons: list[str] = []
    if p.video.codec not in FMP4_VIDEO:
        reasons.append("video_codec")
    reasons += _video_reasons(probe, profile, choice, p.video, "mse", raw=raw)
    reasons += _keyframe_reasons(probe)
    if p.burn is not None:
        reasons.append("subtitle_burn")
    audio, _ = _audio_plan(profile, p.audio)
    if audio is None:
        reasons.append("audio_codec")
    return _unique(reasons), audio


def _unique(reasons: list[str]) -> list[str]:
    return list(dict.fromkeys(reasons))


def _sorted(reasons: list[str]) -> list[str]:
    order = {r: i for i, r in enumerate(REASONS)}
    return sorted(set(reasons), key=lambda r: order.get(r, len(order)))


# --- transcode ---------------------------------------------------------------------------------


def _raw_possible(probe: Probe, profile: ClientProfile, choice: Choice, p: Picked) -> bool:
    """Kan het zonder kleurcorrectie direct of via remux (can_raw van media_hdr)?"""
    if not _direct_reasons(probe, profile, choice, p, raw=True):
        return True
    reasons, _ = _remux(probe, profile, choice, p, raw=True)
    return not reasons


def screen_rung(height: int) -> int:
    """De schermhoogte afgerond op een trede: de kleinste trede die minstens zo hoog is."""
    for rung in (480, 720, 1080):
        if height <= rung:
            return rung
    return LADDER_TOP


def h264_string(height: int, fps: float) -> str:
    """De string van een H.264-trede (Keuze 24), met het hogere level boven 30 fps (480: 25)."""
    box = 480 if height < 480 else height
    low, high = H264_LEVEL[box]
    level = high if fps > (25 if box == 480 else 30) else low
    return f"avc1.6400{level:02x}"


def vp9_string(height: int, fps: float) -> str:
    width = round(height * 16 / 9)
    return f"vp09.00.{vp9_level(width, height, fps):02d}.08"


def _even(value: int) -> int:
    return max(2, value - value % 2)


def rungs_for(
    video: Stream, choice: Choice, profile: ClientProfile, policy: Policy, *, codec: str,
    hdr: bool,
) -> list[Rung]:  # fmt: skip
    """De treden die mogen, beste eerst (Keuze 24, 26, 27): nooit boven de bron, de keuze, het
    scherm (bij auto), 720 bij HDR (auto, en altijd met VP9) en het plafond van de policy."""
    source = label(video.width, video.height)
    cap = min(source, LADDER_TOP)
    if choice.quality == "auto":
        cap = min(cap, screen_rung(profile.max.height))
        if hdr:
            cap = min(cap, 720)
    elif isinstance(choice.quality, int):
        cap = min(cap, choice.quality)
    cap = min(cap, policy.max_height)
    if hdr and codec == "vp9":
        cap = min(cap, 720)  # VP9 met HDR haalt op 1080 geen realtime (S11)
    out = [
        Rung(height=r.height, preset=r.preset, codec=codec)  # type: ignore[arg-type]
        for r in ladder.ORDER
        if r.height <= cap
    ]
    if not out:
        out = [Rung(height=480, preset="veryfast", codec=codec)]  # type: ignore[arg-type]
    height = video.height or 0
    if source == 480 and 0 < height < 480:
        # een bron onder 480 houdt zijn eigen hoogte (even)
        out = [r.model_copy(update={"height": _even(height)}) for r in out[-1:]]
    return out


def _table(rung: Rung, *, hdr: bool) -> float:
    box = 480 if rung.height < 480 else rung.height
    return ladder.cost(Rung(height=box, preset=rung.preset, codec="h264"), hdr=hdr)


def _fps(video: Stream) -> float:
    fps = video.fps if video.fps and video.fps > 0 else 24.0
    return min(fps, 60.0)  # -fpsmax 60


def decode_cost(video: Stream) -> float:
    """d(bron) van Keuze 26, vóór f_dec."""
    depth = video.bit_depth or 8
    d = D_CODEC.get(video.codec or "", D_OTHER) if depth <= 8 else D_OTHER
    pixels = (video.width or 1920) * (video.height or 1080)
    return d * pixels * _fps(video) / (1920 * 1080 * 24)


def cost(rung: Rung, video: Stream, *, hdr: bool, burn: bool, budget: BudgetView) -> float:
    """De kosten van een plan in cores (Keuze 26)."""
    extra = (BURN_EXTRA if burn else 0) + (DEINT_EXTRA if video.interlaced else 0)
    d_ref = D_REF_HDR if hdr else D_REF_SDR
    table = _table(rung, hdr=hdr) * _fps(video) / 24
    return (table - d_ref + extra) * budget.factor + decode_cost(video) * budget.factor_dec


def _reserve_unit(budget: BudgetView) -> float:
    return _table(Rung(height=480, preset="veryfast", codec="h264"), hdr=False) * budget.factor


def admit(
    rungs: list[Rung], video: Stream, *, hdr: bool, burn: bool, budget: BudgetView,
    public: bool,
) -> tuple[Rung, float, int]:  # fmt: skip
    """De eerste trede die past (Keuze 26): in gebruik + kosten + reserve ≤ 0,9 × B, met
    reserve = (vrije plaatsen − plaatsen van dit plan) × kosten(480 veryfast). Een publieke link
    telt niet mee in de reserve en neemt nooit de laatste plek. Anders 503 media_busy."""
    if budget.reason is not None:
        raise media_errors.busy(budget.reason, budget.retry_after)
    if not budget.disk_ok:
        raise media_errors.busy("disk", budget.retry_after)
    if budget.user_free <= 0:
        raise media_errors.busy("user", budget.retry_after)
    if public and budget.link_free is not None and budget.link_free <= 0:
        raise media_errors.busy("link", budget.retry_after)
    unit = _reserve_unit(budget)
    for rung in rungs:
        c = cost(rung, video, hdr=hdr, burn=burn, budget=budget)
        places = 2 if c > HEAVY * budget.factor else 1
        free = budget.places_free - places
        if free < (1 if public else 0):
            continue
        reserve = (budget.places_free if public else free) * unit
        if budget.in_use + c + reserve <= FILL * budget.budget + 1e-9:
            return rung, c, places
    raise media_errors.busy("cpu", budget.retry_after)


def _transcode(
    probe: Probe,
    profile: ClientProfile,
    choice: Choice,
    policy: Policy,
    budget: BudgetView,
    p: Picked,
    remux: list[str],
) -> Plan:
    video = p.video
    raw = choice.hdr == "raw"
    if video.hdr == "dv5":
        raise media_errors.hdr("dolby_vision", False)
    hdr = video.hdr != "none" and not raw
    big = (video.width or 0) > HDR_MAX[0] or (video.height or 0) > HDR_MAX[1]
    if video.hdr != "none" and big:
        raise media_errors.hdr("too_large", not raw and _raw_possible(probe, profile, choice, p))
    if not policy.transcode:
        if policy.public and remux == ["keyframes_missing"]:
            raise media_errors.probe_pending("keyframes", 30)
        if policy.public:
            raise media_errors.public_transcode()
        raise media_errors.unsupported(remux)
    audio, _ = _audio_plan(profile, p.audio)
    fps = _fps(video)
    rungs: list[Rung] = []
    for codec, string in (("h264", h264_string), ("vp9", vp9_string)):
        rungs = [
            r
            for r in rungs_for(video, choice, profile, policy, codec=codec, hdr=hdr)
            if plays(profile, profiles.support(profile, string(r.height, fps)), "mse")
        ]
        if rungs:
            break
    missing = ([] if rungs else ["video_codec"]) + (["audio_codec"] if audio is None else [])
    if missing:
        raise media_errors.unsupported(missing)
    rung, c, places = admit(
        rungs, video, hdr=hdr, burn=p.burn is not None, budget=budget, public=policy.public
    )
    reasons = [r for r in remux if r != "audio_codec"]
    if isinstance(audio, AudioEncode):
        reasons.append("audio_codec")
    if rung.height < rungs[0].height:
        reasons.append("budget")
    return Plan(
        mode="transcode",
        reasons=_sorted(reasons),
        video=rung,
        audio=audio,
        burn=p.burn.index if p.burn is not None else None,
        tonemap=TONEMAP.get(video.hdr) if hdr else None,  # type: ignore[arg-type]
        forced_available=p.forced,
        cost=round(c, 4),
        places=places,  # type: ignore[arg-type]
    )


# --- muziek ------------------------------------------------------------------------------------


def _music(probe: Probe, profile: ClientProfile, choice: Choice) -> Plan:
    audio = pick_audio(probe, choice.audio) or default_audio(probe)
    if audio is None:
        raise media_errors.unavailable("probe")
    found = profiles.support(profile, audio.codec_string)
    kind = AUDIO_MIME.get(probe.container or "")
    as_file = kind is not None and kind in profile.file and plays(profile, found, "file")
    if audio.codec == "mp3":
        if as_file or plays(profile, found, "file"):
            return Plan(mode="direct", audio="copy")
        raise media_errors.unsupported(["audio_codec"])
    target = audio.codec_string if audio.codec in ("flac", "aac") else "flac"
    if _engine(profile) and plays(profile, profiles.support(profile, target), "mse"):
        return Plan(mode="audio", audio="copy")
    if as_file:
        return Plan(mode="direct", audio="copy")
    raise media_errors.unsupported(["audio_codec"])


# --- de mime -----------------------------------------------------------------------------------


def _codecs(*strings: str | None) -> str:
    return ",".join(s for s in strings if s)


def audio_string(plan: Plan, audio: Stream | None) -> str | None:
    if plan.audio == "none" or audio is None:
        return None
    if isinstance(plan.audio, AudioEncode):
        return "mp4a.40.2" if plan.audio.codec == "aac" else "opus"
    return audio.codec_string


def mime(probe: Probe, plan: Plan, audio: Stream | None) -> str:
    """De mime met codecs voor de speler: van het bestand (direct), van fMP4 (remux en
    transcode), of van audio.mp4 (mode audio). Zonder audio alleen de videocodec."""
    video = probe.video()
    if video is None:
        if plan.mode == "audio":
            codec = audio.codec_string if audio and audio.codec in ("flac", "aac") else "flac"
            return f'audio/mp4; codecs="{codec}"'
        kind = AUDIO_MIME.get(probe.container or "", "audio/mpeg")
        return kind
    a = audio_string(plan, audio)
    if plan.mode == "direct":
        kind = DIRECT_KINDS.get(probe.container or "", "video/mp4")
        return f'{kind}; codecs="{_codecs(video.codec_string, a)}"'
    if plan.mode == "remux" or not isinstance(plan.video, Rung):
        return f'video/mp4; codecs="{_codecs(video.codec_string, a)}"'
    fps = _fps(video)
    v = plan.video
    string = h264_string(v.height, fps) if v.codec == "h264" else vp9_string(v.height, fps)
    return f'video/mp4; codecs="{_codecs(string, a)}"'
