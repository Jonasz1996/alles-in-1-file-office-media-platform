"""De beslissing (Keuze 19, 24, 26, 27, 29): een tabel van ruim 60 gevallen.

De probes zijn de opnames van alle fixtures (tests/media/recorded/probe, van M0), soms met een
keyframe-index of een kleine wijziging. De profielen bootsen na wat de web-app meet: de vaste
kandidaten plus de strings van het item, per string mse/file uit een model van de browser:
- chromium141: gemeten, de Chromium van Playwright hier (geen H.264, AAC, HEVC of AC-3);
- chrome_ci: Chrome for Testing 141 in CI, met H.264 en AAC (S13), zonder HEVC;
- safari17, firefox: aangenomen [doc]; webos5: alleen het basisprofiel (de TV meet niets).

De tabel van Keuze 24 komt uit ladder.cost (M4). TABLE hieronder is dezelfde tabel, met de
hand: de test kijkt na dat ladder hem zo heeft (tot M4 zette deze test hem zelf in).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import HTTPException

from app.config import MediaConfig
from app.media import decide as D
from app.media import ladder, public
from app.media import profile as P
from app.media.types import (
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
from tests.media import record

# --- de tabel van Keuze 24 (ladder.py, M4) -------------------------------------------------------

TABLE = {
    (1080, "veryfast"): (1.42, 2.30),
    (1080, "superfast"): (0.95, 1.57),
    (720, "veryfast"): (0.74, 1.21),
    (720, "superfast"): (0.62, 1.02),
    (480, "veryfast"): (0.56, 0.92),
}


def test_ladder_heeft_de_tabel_van_keuze_24():
    for (height, preset), (sdr, hdr) in TABLE.items():
        rung = Rung(height=height, preset=preset, codec="h264")  # type: ignore[arg-type]
        assert (ladder.cost(rung), ladder.cost(rung, hdr=True)) == (sdr, hdr)


# --- de browsers -------------------------------------------------------------------------------

BOTH = CodecSupport(mse=True, file=True)
FILE = CodecSupport(mse=False, file=True)
NONE = CodecSupport(mse=False, file=False)


def starts(string: str, *prefixes: str) -> bool:
    return string.lower().startswith(prefixes)


def chromium141(s: str) -> CodecSupport:
    """Gemeten in de Chromium 141 van Playwright (zie docs/fase-5/m3.md)."""
    if starts(s, "vp09", "av01", "opus", "flac"):
        return BOTH
    return FILE if s == "mp3" else NONE


def chrome_ci(s: str) -> CodecSupport:
    if starts(s, "avc1", "mp4a", "vp09", "av01", "opus", "flac"):
        return BOTH
    return FILE if s == "mp3" else NONE


def safari17(s: str) -> CodecSupport:
    if starts(s, "avc1", "hvc1", "hev1", "mp4a", "ac-3", "ec-3", "flac"):
        return BOTH  # ook Hi10P zegt ja: het basisprofiel sluit hem uit
    return FILE if s == "mp3" else NONE


def firefox(s: str) -> CodecSupport:
    if starts(s, "avc1", "mp4a", "vp09", "av01", "opus", "flac"):
        return BOTH  # Hi10P zegt ja: het basisprofiel web sluit hem uit voor Firefox
    return FILE if s == "mp3" else NONE


Browser = Callable[[str], CodecSupport]
BROWSERS: dict[str, tuple[str, str, Browser | None, dict[str, Any]]] = {
    # naam: (basisprofiel, client.name, model, de rest van het profiel)
    "chromium141": (
        "web",
        "Chromium 141",
        chromium141,
        {"file": ["video/mp4", "video/webm", "audio/mpeg", "audio/flac", "audio/ogg"]},
    ),
    "chrome_ci": (
        "web",
        "Chrome 141",
        chrome_ci,
        {"file": ["video/mp4", "video/webm", "audio/mpeg", "audio/flac", "audio/ogg", "audio/mp4"]},
    ),
    "safari17": ("ios-safari", "Safari 17", safari17, {}),
    "firefox": ("web", "Firefox 131", firefox, {}),
    "webos5": ("webos-5", "webOS 5", None, {}),
}


def profile_for(name: str, probe: Probe | None = None, **extra: Any) -> ClientProfile:
    """Het profiel zoals de client het stuurt (kandidaten plus de strings van het item, elk
    gemeten), en dan zoals play het gebruikt (profile.merge)."""
    base, client, model, rest = BROWSERS[name]
    data: dict[str, Any] = {"v": 1, "base": base, "client": {"name": client}, **rest}
    if model is not None:
        strings = list(P.candidates())
        if probe is not None:
            strings += [s.codec_string for s in probe.streams if s.codec_string]
        data["codecs"] = {s: model(s).model_dump() for s in dict.fromkeys(strings)}
    for key, value in extra.items():
        data[key] = {**data.get(key, {}), **value} if isinstance(value, dict) else value
    return P.merge(P.load(data))


# --- de probes ---------------------------------------------------------------------------------


def probe(name: str, *, keyframes: bool = False, gop: int = 2000, **changes: Any) -> Probe:
    found = record.load(name)
    assert found is not None and "data" in found, name
    p = Probe.model_validate(found["data"])
    if keyframes:
        p.keyframes_state, p.max_gop_ms = "ready", gop
    for key, value in changes.items():
        setattr(p, key, value)
    return p


def stream(p: Probe, index: int, **changes: Any) -> Probe:
    s = next(s for s in p.streams if s.index == index)
    for key, value in changes.items():
        setattr(s, key, value)
    return p


def synthetic(
    codec: str, string: str, width: int, height: int, *, hdr: str = "none", depth: int = 8,
    container: str = "matroska", fps: float = 24.0, audio: str | None = "aac",
    dv: int | None = None,
) -> Probe:  # fmt: skip
    streams = [
        Stream(index=0, type="video", codec=codec, codec_string=string, width=width,
               height=height, fps=fps, bit_depth=depth, hdr=hdr, dv_profile=dv),
    ]  # fmt: skip
    if audio:
        streams.append(
            Stream(index=1, type="audio", codec=audio, codec_string={"aac": "mp4a.40.2",
                   "ac3": "ac-3", "opus": "opus"}[audio], channels=2, default=True)
        )  # fmt: skip
    return Probe(container=container, duration_ms=60_000, bit_rate=8_000_000, streams=streams)


LOGGED = Policy(public=False, transcode=True, max_height=2160)
PUBLIC = public.policy(MediaConfig(), public=True)
PUBLIC_TC = public.policy(MediaConfig(public_transcode=True), public=True)


def budget(**changes: Any) -> BudgetView:
    values: dict[str, Any] = {"budget": 3.0, "in_use": 0.0, "places_free": 3, "user_free": 2}
    return BudgetView(**{**values, **changes})


FREE = budget()


def run(p: Probe, browser: str, *, policy: Policy = LOGGED, b: BudgetView = FREE,
        profile: dict | None = None, **choice: Any) -> Plan:  # fmt: skip
    prof = profile_for(browser, p, **(profile or {}))
    return D.decide(p, prof, Choice(**choice), public.for_source(policy, MediaConfig(), p), b)


def refused(fn: Callable[[], Any]) -> tuple[int, dict]:
    """(status, {code, ...params}) van de fout die fn geeft."""
    with pytest.raises(HTTPException) as caught:
        fn()
    detail = caught.value.detail
    return caught.value.status_code, {"code": detail["code"], **detail.get("params", {})}


# --- de tabel ----------------------------------------------------------------------------------

K = {"keyframes": True}
V720 = Rung(height=720, preset="veryfast", codec="vp9")
H720 = Rung(height=720, preset="veryfast", codec="h264")
H480_360 = Rung(height=360, preset="veryfast", codec="h264")
V480_360 = Rung(height=360, preset="veryfast", codec="vp9")
AAC, OPUS = D.AAC, D.OPUS


def pgs_forced() -> Probe:
    return stream(probe("pgs.mkv", **K), 1, forced=True)


def two_tracks() -> Probe:
    p = probe("h264_aac.mp4")
    p.streams.append(p.streams[1].model_copy(update={"index": 2, "default": False, "lang": "nld"}))
    return p


# (naam, probe, browser, keuze, verwacht: mode, redenen, en wat er verder moet kloppen)
CASES: list[tuple[str, Callable[[], Probe], str, dict, str, set[str], dict]] = [
    # chromium141: VP9, AV1, Opus en FLAC; geen H.264 of AAC
    ("c-h264-aac", lambda: probe("h264_aac.mp4"), "chromium141", {}, "transcode",
     {"video_codec", "keyframes_missing", "audio_codec"}, {"video": V720, "audio": OPUS}),
    ("c-vp9-webm", lambda: probe("vp9_opus.webm"), "chromium141", {}, "direct", set(), {}),
    ("c-vp9-mkv", lambda: probe("vp9_opus.mkv", **K), "chromium141", {}, "remux",
     {"container"}, {"audio": "copy"}),
    ("c-vp9-mkv-spoor2", lambda: probe("vp9_opus.mkv", **K), "chromium141", {"audio": 2},
     "remux", {"container", "audio_track"}, {}),
    ("c-vp9-mkv-zonder-index", lambda: probe("vp9_opus.mkv"), "chromium141", {}, "transcode",
     {"keyframes_missing"}, {"video": V480_360, "audio": "copy"}),
    ("c-av1", lambda: probe("av1_opus.mp4"), "chromium141", {}, "direct", set(), {}),
    ("c-hevc-hdr", lambda: probe("hevc10_hdr10.mkv", **K), "chromium141", {}, "transcode",
     {"video_codec", "hdr", "audio_codec"}, {"video": V720, "audio": OPUS, "tonemap": "pq"}),
    ("c-hevc-hdr-aac", lambda: probe("hevc10_hdr10.mkv", **K), "chromium141", {"audio": 2},
     "transcode", {"video_codec", "hdr", "audio_codec"}, {"audio": OPUS}),
    ("c-hevc-hdr-raw", lambda: probe("hevc10_hdr10.mkv", **K), "chromium141", {"hdr": "raw"},
     "transcode", {"video_codec", "audio_codec"}, {"tonemap": None}),
    ("c-hi10p", lambda: probe("h264_hi10p.mkv", **K), "chromium141", {}, "transcode",
     {"video_codec"}, {"video": V480_360, "audio": "none"}),
    ("c-noaudio", lambda: probe("noaudio.mp4"), "chromium141", {}, "transcode",
     {"video_codec", "keyframes_missing"}, {"audio": "none"}),
    ("c-xvid", lambda: probe("xvid_mp3.avi"), "chromium141", {}, "transcode",
     {"video_codec", "keyframes_missing", "audio_codec"}, {"audio": OPUS}),
    ("c-interlaced", lambda: probe("interlaced.ts", **K), "chromium141", {}, "transcode",
     {"video_codec", "interlaced", "audio_codec"}, {}),
    # chrome_ci: met H.264 en AAC, zonder HEVC en AC-3
    ("h-h264", lambda: probe("h264_aac.mp4"), "chrome_ci", {}, "direct", set(),
     {"audio": "copy", "video": "copy"}),
    ("h-moov-achteraan", lambda: probe("h264_aac_moov_end.mp4"), "chrome_ci", {}, "direct",
     set(), {}),
    ("h-h264-480", lambda: probe("h264_aac.mp4"), "chrome_ci", {"quality": 480}, "transcode",
     {"quality", "keyframes_missing"},
     {"video": Rung(height=480, preset="veryfast", codec="h264"), "audio": "copy"}),
    ("h-h264-480-index", lambda: probe("h264_aac.mp4", **K), "chrome_ci", {"quality": 480},
     "transcode", {"quality"}, {}),
    ("h-h264-720", lambda: probe("h264_aac.mp4"), "chrome_ci", {"quality": 720}, "direct",
     set(), {}),
    ("h-h264-1080", lambda: probe("h264_aac.mp4"), "chrome_ci", {"quality": 1080}, "direct",
     set(), {}),
    ("h-h264-source", lambda: probe("h264_aac.mp4"), "chrome_ci", {"quality": "source"},
     "direct", set(), {}),
    ("h-vp9-mkv", lambda: probe("vp9_opus.mkv", **K), "chrome_ci", {}, "remux", {"container"},
     {}),
    ("h-ac3", lambda: probe("h264_ac3_51.mkv", **K), "chrome_ci", {}, "remux",
     {"container", "audio_codec"}, {"audio": AAC, "cost": 0.07}),
    ("h-ac3-zonder-index", lambda: probe("h264_ac3_51.mkv"), "chrome_ci", {}, "transcode",
     {"keyframes_missing", "audio_codec"}, {"video": H480_360, "audio": AAC}),
    ("h-hi10p", lambda: probe("h264_hi10p.mkv", **K), "chrome_ci", {}, "remux", {"container"},
     {"audio": "none", "cost": 0.02}),
    ("h-hevc-hdr", lambda: probe("hevc10_hdr10.mkv", **K), "chrome_ci", {}, "transcode",
     {"video_codec", "hdr", "audio_codec"}, {"video": H720, "audio": AAC, "tonemap": "pq"}),
    ("h-hevc-hdr-aac", lambda: probe("hevc10_hdr10.mkv", **K), "chrome_ci", {"audio": 2},
     "transcode", {"video_codec", "hdr"}, {"audio": "copy"}),
    ("h-hevc-hdr-srt", lambda: probe("hevc10_hdr10.mkv", **K), "chrome_ci",
     {"audio": 2, "subtitle": "e3"}, "transcode", {"video_codec", "hdr"}, {"burn": None}),
    ("h-interlaced", lambda: probe("interlaced.ts", **K), "chrome_ci", {}, "transcode",
     {"interlaced"}, {"video": H720, "audio": "copy"}),
    ("h-ts", lambda: probe("a.ts", **K), "chrome_ci", {}, "remux", {"container"}, {}),
    ("h-m2ts-hevc", lambda: probe("a.m2ts", **K), "chrome_ci", {}, "transcode",
     {"video_codec"}, {"audio": "copy"}),
    ("h-noaudio", lambda: probe("noaudio.mp4"), "chrome_ci", {}, "direct", set(),
     {"audio": "none"}),
    ("h-pgs-gekozen", lambda: probe("pgs.mkv", **K), "chrome_ci", {"subtitle": "e1"},
     "transcode", {"subtitle_burn"}, {"burn": 1}),
    ("h-pgs-geforceerd", pgs_forced, "chrome_ci", {}, "remux", {"container"},
     {"forced_available": "e1", "burn": None}),
    ("h-vobsub-gekozen", lambda: probe("vobsub.mkv", **K), "chrome_ci", {"subtitle": "e1"},
     "transcode", {"subtitle_burn"}, {"burn": 1}),
    ("h-multi-audio", lambda: probe("multi_audio.mkv", **K), "chrome_ci", {}, "remux",
     {"container"}, {"forced_available": None}),
    ("h-multi-audio-spoor2", lambda: probe("multi_audio.mkv", **K), "chrome_ci", {"audio": 2},
     "remux", {"container", "audio_track"}, {}),
    ("h-ass", lambda: probe("subs_text.mkv", **K), "chrome_ci", {"subtitle": "e2"}, "remux",
     {"container"}, {"burn": None}),
    ("h-xvid", lambda: probe("xvid_mp3.avi", **K), "chrome_ci", {}, "transcode",
     {"video_codec", "audio_codec"}, {"video": H480_360, "audio": AAC}),
    ("h-fps240", lambda: probe("fps240.mp4"), "chrome_ci", {}, "direct", set(), {}),
    ("h-late-video", lambda: probe("late_video.mkv", **K), "chrome_ci", {}, "remux",
     {"container"}, {}),
    ("h-lange-gop", lambda: probe("long_gop.mkv", keyframes=True, gop=30_000), "chrome_ci", {},
     "transcode", {"gop_too_long"}, {}),
    ("h-gop-10s-80mbit", lambda: probe("stream_src.mkv", keyframes=True, gop=10_000,
                                       bit_rate=80_000_000),
     "chrome_ci", {}, "transcode", {"gop_too_long"}, {}),  # fmt: skip
    ("h-gop-10s-40mbit", lambda: probe("stream_src.mkv", keyframes=True, gop=10_000,
                                       bit_rate=40_000_000),
     "chrome_ci", {}, "remux", {"container"}, {}),  # fmt: skip
    ("h-geen-cues", lambda: probe("no_cues.mkv"), "chrome_ci", {}, "transcode",
     {"keyframes_missing"}, {}),
    ("h-index-sparse", lambda: probe("no_cues.mkv", keyframes_state="sparse"), "chrome_ci", {},
     "transcode", {"keyframes_missing"}, {}),
    ("h-ander-spoor-mp4", two_tracks, "chrome_ci", {"audio": 2}, "transcode",
     {"keyframes_missing"}, {}),
    ("h-ander-spoor-mp4-index", lambda: two_tracks().model_copy(
        update={"keyframes_state": "ready", "max_gop_ms": 2000}), "chrome_ci", {"audio": 2},
     "remux", {"audio_track"}, {}),  # fmt: skip
    ("h-niet-vloeiend", lambda: probe("h264_aac.mp4"), "chrome_ci", {}, "transcode",
     {"not_smooth", "keyframes_missing"}, {}),
    ("h-bitrate", lambda: probe("h264_aac.mp4", **K), "chrome_ci", {}, "transcode",
     {"bitrate"}, {}),
    # safari17: HEVC, AC-3 en track_switch; geen VP9, Opus of Hi10P
    ("s-h264", lambda: probe("h264_aac.mp4"), "safari17", {}, "direct", set(), {}),
    ("s-ander-spoor-mp4", two_tracks, "safari17", {"audio": 2}, "direct", set(), {}),
    ("s-hevc-hdr-geen-scherm", lambda: probe("hevc10_hdr10.mkv", **K), "safari17", {},
     "transcode", {"hdr"}, {"video": H720, "audio": "copy", "tonemap": "pq"}),
    ("s-hevc-hdr-scherm", lambda: probe("hevc10_hdr10.mkv", **K), "safari17", {}, "remux",
     {"container"}, {"audio": "copy"}),
    ("s-hevc-hdr-raw", lambda: probe("hevc10_hdr10.mkv", **K), "safari17", {"hdr": "raw"},
     "remux", {"container"}, {}),
    ("s-hevc2160-scherm", lambda: probe("hevc2160_hdr10.mkv", **K), "safari17", {}, "remux",
     {"container"}, {"audio": "none"}),
    ("s-hi10p", lambda: probe("h264_hi10p.mkv", **K), "safari17", {}, "transcode",
     {"video_codec"}, {"video": H480_360}),
    ("s-vp9-webm", lambda: probe("vp9_opus.webm"), "safari17", {}, "transcode",
     {"video_codec", "keyframes_missing", "audio_codec"}, {"audio": AAC}),
    ("s-m2ts-hevc", lambda: probe("a.m2ts", **K), "safari17", {}, "remux", {"container"}, {}),
    ("s-multi-audio-spoor2", lambda: probe("multi_audio.mkv", **K), "safari17", {"audio": 2},
     "remux", {"container"}, {}),
    # webos5: alleen het basisprofiel
    ("w-h264", lambda: probe("h264_aac.mp4"), "webos5", {}, "direct", set(), {}),
    ("w-hevc-hdr", lambda: probe("hevc10_hdr10.mkv", **K), "webos5", {}, "transcode", {"hdr"},
     {"audio": "copy"}),
    ("w-hi10p", lambda: probe("h264_hi10p.mkv", **K), "webos5", {}, "transcode",
     {"video_codec"}, {}),
    ("w-vp9-webm", lambda: probe("vp9_opus.webm", **K), "webos5", {}, "remux",
     {"audio_codec"}, {"audio": AAC}),
    ("w-ac3", lambda: probe("h264_ac3_51.mkv", **K), "webos5", {}, "remux", {"container"},
     {"audio": "copy"}),
    ("w-pgs-gekozen", lambda: probe("pgs.mkv", **K), "webos5", {"subtitle": "e1"},
     "transcode", {"subtitle_burn"}, {}),
    # firefox
    ("f-hi10p", lambda: probe("h264_hi10p.mkv", **K), "firefox", {}, "transcode",
     {"video_codec"}, {}),
    ("f-h264", lambda: probe("h264_aac.mp4"), "firefox", {}, "direct", set(), {}),
    ("f-vp9-mkv", lambda: probe("vp9_opus.mkv", **K), "firefox", {}, "remux", {"container"},
     {}),
    ("f-hevc-hdr", lambda: probe("hevc10_hdr10.mkv", **K), "firefox", {}, "transcode",
     {"video_codec", "hdr", "audio_codec"}, {"video": H720}),
]  # fmt: skip

PROFILE_EXTRA = {
    "h-niet-vloeiend": {"codecs": {"avc1.64001f": {"mse": True, "file": True, "smooth": False}}},
    "h-bitrate": {"max": {"bitrate_kbps": 1000}},
    "s-hevc-hdr-scherm": {"hdr": {"screen": True}},
    "s-hevc2160-scherm": {"hdr": {"screen": True}},
}


def test_de_tabel_heeft_genoeg_gevallen():
    assert len(CASES) >= 60
    assert len({c[0] for c in CASES}) == len(CASES)
    assert {c[2] for c in CASES} == set(BROWSERS)


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_beslissing(case):
    name, make, browser, choice, mode, reasons, more = case
    plan = run(make(), browser, profile=PROFILE_EXTRA.get(name), **choice)
    assert plan.mode == mode, (plan.mode, plan.reasons)
    assert set(plan.reasons) == reasons
    for key, value in more.items():
        assert getattr(plan, key) == value, (key, getattr(plan, key))
    if plan.mode in ("direct", "remux"):
        assert plan.video == "copy"


# --- weigeringen -------------------------------------------------------------------------------


def test_hevc_2160_hdr_auto_is_te_groot():
    status, detail = refused(lambda: run(probe("hevc2160_hdr10.mkv", **K), "chrome_ci"))
    assert status == 422 and detail["code"] == "media_hdr"
    assert detail["reason"] == "too_large" and detail["can_raw"] is False
    # met een client die HEVC kan: zonder kleurcorrectie via remux
    status, detail = refused(lambda: run(probe("hevc2160_hdr10.mkv", **K), "safari17"))
    assert status == 422 and detail["can_raw"] is True
    # wie toch raw kiest, krijgt die remux
    assert run(probe("hevc2160_hdr10.mkv", **K), "safari17", hdr="raw").mode == "remux"


def test_te_groot_voor_de_probe():
    huge = synthetic("h264", "avc1.640033", 8200, 4400)
    status, detail = refused(lambda: run(huge, "chrome_ci"))
    assert status == 415 and detail["reasons"] == ["too_large"]


def test_dolby_vision_5():
    dv5 = synthetic("hevc", "dvh1.05.06", 1920, 1080, hdr="dv5", depth=10, dv=5)
    status, detail = refused(lambda: run(dv5, "chrome_ci"))
    assert status == 422 and detail["code"] == "media_hdr"
    assert detail["reason"] == "dolby_vision" and detail["can_raw"] is False
    # ook raw helpt niet: de kleuren worden paars en groen
    assert refused(lambda: run(dv5, "chrome_ci", hdr="raw"))[1]["reason"] == "dolby_vision"
    dv_client = {"codecs": {"dvh1.05.06": {"mse": True, "file": True}}, "hdr": {"dv": True}}
    dv5.keyframes_state, dv5.max_gop_ms = "ready", 2000
    assert run(dv5, "chrome_ci", profile=dv_client).mode == "remux"


def test_dolby_vision_8_speelt_de_basislaag():
    dv8 = synthetic("hevc", "hvc1.2.4.L120.90", 1920, 1080, hdr="hdr10", depth=10, dv=8)
    dv8.keyframes_state, dv8.max_gop_ms = "ready", 2000
    assert run(dv8, "safari17", profile={"hdr": {"screen": True}}).mode == "remux"


def test_geen_codec_die_de_client_kan():
    bare = {"codecs": {"avc1.640028": {"mse": False}}}
    p = probe("h264_aac.mp4")
    status, detail = refused(
        lambda: D.decide(p, P.merge(P.load({"v": 1, **bare})), Choice(), LOGGED, FREE)
    )
    assert status == 415 and detail["reasons"] == ["video_codec", "audio_codec"]


def test_geen_aac_of_opus():
    only_video = P.merge(P.load({"v": 1, "codecs": {"avc1.640033": {"mse": True}}}))
    status, detail = refused(
        lambda: D.decide(probe("h264_ac3_51.mkv", **K), only_video, Choice(), LOGGED, FREE)
    )
    assert status == 415 and detail["reasons"] == ["audio_codec"]


# --- publieke links (Keuze 34) -------------------------------------------------------------------


def test_publiek_direct_en_remux_mogen():
    assert run(probe("h264_aac.mp4"), "chrome_ci", policy=PUBLIC).mode == "direct"
    assert run(probe("vp9_opus.mkv", **K), "chromium141", policy=PUBLIC).mode == "remux"


def test_publiek_zonder_keyframes_wacht():
    status, detail = refused(lambda: run(probe("vp9_opus.mkv"), "chromium141", policy=PUBLIC))
    assert status == 409 and detail["code"] == "media_probe_pending"
    assert detail["reason"] == "keyframes"


def test_publiek_transcode_geeft_403():
    for make in (lambda: probe("hevc10_hdr10.mkv", **K), lambda: probe("interlaced.ts", **K)):
        status, detail = refused(lambda m=make: run(m(), "chrome_ci", policy=PUBLIC))
        assert status == 403 and detail["code"] == "media_public_transcode"
    # alleen de index ontbreekt: wachten, ook als het geluid omgezet wordt (remux mag)
    status, detail = refused(lambda: run(probe("h264_ac3_51.mkv"), "chrome_ci", policy=PUBLIC))
    assert status == 409 and detail["reason"] == "keyframes"
    # mag de link omzetten, dan transcode
    plan = run(probe("h264_ac3_51.mkv"), "chrome_ci", policy=PUBLIC_TC, b=budget(link_free=1))
    assert plan.mode == "transcode"


def test_publiek_pgs_geforceerd_is_remux_geen_403():
    plan = run(pgs_forced(), "chrome_ci", policy=PUBLIC)
    assert plan.mode == "remux" and plan.forced_available == "e1"


def test_publiek_met_transcode_hooguit_720_en_nooit_de_laatste_plek():
    big = synthetic("hevc", "hvc1.1.6.L120.90", 1920, 1080)
    plan = run(big, "chrome_ci", policy=PUBLIC_TC, b=budget(link_free=1))
    assert plan.mode == "transcode" and plan.video.height == 720
    status, detail = refused(lambda: run(big, "chrome_ci", policy=PUBLIC_TC, b=budget(link_free=0)))
    assert status == 503 and detail["reason"] == "link"
    status, detail = refused(
        lambda: run(big, "chrome_ci", policy=PUBLIC_TC, b=budget(places_free=1, link_free=1))
    )
    assert status == 503 and detail["reason"] == "cpu"
    # de reserve telt de link niet mee: 2 vrij, 1 voor de link, de reserve blijft 2 × 0,56
    plan = run(big, "chrome_ci", policy=PUBLIC_TC, b=budget(places_free=2, link_free=1))
    assert plan.video == Rung(height=720, preset="veryfast", codec="h264")


# --- het budget (Keuze 26, de rekenvoorbeelden) --------------------------------------------------


def film() -> Probe:
    """Een H.264-bron van 1080p24 die omgezet moet worden (geen keyframe-index)."""
    return synthetic("h264", "avc1.640028", 1920, 1080)


def test_kijker_1_2_en_3():
    one = run(film(), "chrome_ci", b=budget())
    assert one.video == Rung(height=1080, preset="veryfast", codec="h264")
    assert one.cost == pytest.approx(1.42) and one.places == 1
    two = run(film(), "chrome_ci", b=budget(in_use=1.42, places_free=2))
    assert two.video == Rung(height=720, preset="superfast", codec="h264")
    assert "budget" in two.reasons
    three = run(film(), "chrome_ci", b=budget(in_use=1.42 + 0.62, places_free=1))
    assert three.video == Rung(height=720, preset="superfast", codec="h264")
    status, detail = refused(lambda: run(film(), "chrome_ci", b=budget(in_use=2.66, places_free=0)))
    assert status == 503 and detail["code"] == "media_busy" and detail["reason"] == "cpu"


def test_max_transcodes_2():
    one = run(film(), "chrome_ci", b=budget(places_free=2))
    assert one.video.height == 1080 and one.video.preset == "veryfast"
    two = run(film(), "chrome_ci", b=budget(in_use=1.42, places_free=1))
    assert two.video == Rung(height=1080, preset="superfast", codec="h264")
    assert "budget" not in two.reasons


def test_4k_hevc_telt_dubbel():
    uhd = synthetic("hevc", "hvc1.1.6.L153.B0", 3840, 2160)
    plan = run(uhd, "chrome_ci")
    assert plan.video == Rung(height=1080, preset="superfast", codec="h264")
    assert plan.places == 2 and plan.cost == pytest.approx(2.01)
    # daarna past nog één kijker met een 1080p-bron: 720 superfast (2,63)
    after = run(film(), "chrome_ci", b=budget(in_use=2.01, places_free=1))
    assert after.video == Rung(height=720, preset="superfast", codec="h264")
    assert refused(lambda: run(film(), "chrome_ci", b=budget(in_use=2.63, places_free=0)))


def test_hdr_naar_een_client_met_alleen_vp9():
    hdr = synthetic("hevc", "hvc1.2.4.L120.90", 1920, 1080, hdr="hdr10", depth=10)
    plan = run(hdr, "chromium141")
    assert plan.video == Rung(height=720, preset="veryfast", codec="vp9")
    assert plan.cost == pytest.approx(1.21) and plan.tonemap == "pq"
    # ook wie 1080 kiest, krijgt met VP9 hooguit 720
    config = MediaConfig(hdr_1080=True)
    prof = profile_for("chromium141", hdr)
    plan = D.decide(hdr, prof, Choice(quality=1080), public.for_source(LOGGED, config, hdr), FREE)
    assert plan.video.height == 720


def test_hdr_1080_alleen_met_de_instelling():
    hdr = synthetic("hevc", "hvc1.2.4.L120.90", 1920, 1080, hdr="hdr10", depth=10)
    prof = profile_for("chrome_ci", hdr)
    off = public.for_source(LOGGED, MediaConfig(), hdr)
    assert D.decide(hdr, prof, Choice(quality=1080), off, FREE).video.height == 720
    on = public.for_source(LOGGED, MediaConfig(hdr_1080=True), hdr)
    plan = D.decide(hdr, prof, Choice(quality=1080), on, FREE)
    # 1080 veryfast HDR (2,30) telt dubbel en past niet met de reserve; 1080 superfast wel
    assert plan.video == Rung(height=1080, preset="superfast", codec="h264")
    # auto blijft bij HDR 720, ook met de instelling
    assert D.decide(hdr, prof, Choice(), on, FREE).video.height == 720


def test_een_tragere_machine():
    plan = run(film(), "chrome_ci", b=budget(factor=1.5, factor_dec=1.0))
    assert plan.video == Rung(height=720, preset="veryfast", codec="h264")
    assert "budget" in plan.reasons


def test_de_toelating_zegt_nee():
    for changes, reason in (
        ({"user_free": 0}, "user"),
        ({"disk_ok": False}, "disk"),
        ({"reason": "worker", "retry_after": 30}, "worker"),
    ):
        status, detail = refused(lambda c=changes: run(film(), "chrome_ci", b=budget(**c)))
        assert status == 503 and detail["reason"] == reason
    # geen plek meer (M4: admission.view geeft places_free 0): wel direct en remux
    full = budget(places_free=0)
    assert refused(lambda: run(film(), "chrome_ci", b=full))[1]["reason"] == "cpu"
    assert run(probe("vp9_opus.mkv", **K), "chrome_ci", b=full).mode == "remux"


def test_kosten_tellen_de_bron_en_het_extra_werk_mee():
    v = film().video()
    assert D.decode_cost(v) == pytest.approx(0.18)
    uhd = synthetic("hevc", "x", 3840, 2160).video()
    assert D.decode_cost(uhd) == pytest.approx(1.24)
    fast = synthetic("h264", "x", 1920, 1080, fps=48).video()
    rung = Rung(height=1080, preset="veryfast", codec="h264")
    assert D.cost(rung, fast, hdr=False, burn=False, budget=FREE) == pytest.approx(
        1.42 * 2 - 0.18 + 0.36
    )
    hfr = synthetic("h264", "x", 1920, 1080, fps=240).video()
    assert D.cost(rung, hfr, hdr=False, burn=False, budget=FREE) == pytest.approx(
        1.42 * 2.5 - 0.18 + 0.18 * 2.5
    )  # -fpsmax 60
    assert D.cost(rung, v, hdr=False, burn=True, budget=FREE) == pytest.approx(1.42 + 0.15)
    ten_bit = synthetic("h264", "x", 1920, 1080, depth=10).video()
    assert D.decode_cost(ten_bit) == pytest.approx(0.31)


# --- de treden ---------------------------------------------------------------------------------


def test_auto_volgt_het_scherm():
    small = {"max": {"height": 600}}
    plan = run(film(), "chrome_ci", profile=small)
    assert plan.video.height == 720
    plan = run(film(), "chrome_ci", profile={"max": {"height": 400}})
    assert plan.video.height == 480


def test_nooit_vergroten():
    p = probe("h264_ac3_51.mkv")
    assert run(p, "chrome_ci", quality=1080).video == H480_360
    wide = synthetic("hevc", "hvc1.1.6.L120.90", 1920, 800)
    assert run(wide, "chrome_ci").video.height == 1080  # 1920 breed: de doos van 1080
    narrow = synthetic("hevc", "hvc1.1.6.L120.90", 1280, 536)
    assert run(narrow, "chrome_ci").video.height == 720


def test_screen_rung():
    assert [D.screen_rung(h) for h in (144, 480, 600, 720, 1000, 1080, 1440, 4320)] == [
        480, 480, 720, 720, 1080, 1080, 1080, 1080
    ]  # fmt: skip


def test_label():
    assert D.label(640, 360) == 480 and D.label(854, 480) == 480
    assert D.label(720, 576) == 720 and D.label(1280, 720) == 720
    assert D.label(1920, 1080) == 1080 and D.label(1920, 1088) == 1080
    assert D.label(3840, 2160) == 2160 and D.label(None, None) == 480


# --- de mime -----------------------------------------------------------------------------------


def mime_of(p: Probe, browser: str, **choice: Any) -> str:
    plan = run(p, browser, **choice)
    return D.mime(p, plan, D.pick_audio(p, choice.get("audio")))


def test_mime():
    assert mime_of(probe("h264_aac.mp4"), "chrome_ci") == (
        'video/mp4; codecs="avc1.64001f,mp4a.40.2"'
    )
    assert mime_of(probe("noaudio.mp4"), "chrome_ci") == 'video/mp4; codecs="avc1.64001e"'
    assert mime_of(probe("vp9_opus.webm"), "chromium141") == (
        'video/webm; codecs="vp09.00.21.08,opus"'
    )
    assert mime_of(probe("h264_ac3_51.mkv", **K), "chrome_ci") == (
        'video/mp4; codecs="avc1.64001e,mp4a.40.2"'
    )
    assert mime_of(probe("h264_aac.mp4"), "chromium141") == 'video/mp4; codecs="vp09.00.31.08,opus"'
    assert mime_of(probe("h264_hi10p.mkv"), "safari17") == 'video/mp4; codecs="avc1.64001e"'
    assert mime_of(film(), "chrome_ci") == 'video/mp4; codecs="avc1.640028,mp4a.40.2"'


def test_de_strings_van_de_treden():
    assert D.h264_string(1080, 24) == "avc1.640028" and D.h264_string(1080, 50) == "avc1.64002a"
    assert D.h264_string(720, 30) == "avc1.64001f" and D.h264_string(720, 60) == "avc1.640020"
    assert D.h264_string(480, 25) == "avc1.64001e" and D.h264_string(480, 30) == "avc1.64001f"
    assert D.h264_string(360, 24) == "avc1.64001e"
    assert D.vp9_string(720, 24) == "vp09.00.31.08" and D.vp9_string(1080, 60) == "vp09.00.41.08"


def test_noaudio_heeft_geen_audio_in_het_plan():
    plan = run(probe("noaudio.mp4"), "chromium141")
    assert plan.audio == "none"
    assert D.audio_string(plan, None) is None


# --- muziek (Keuze 38) -------------------------------------------------------------------------

MUSIC = [
    ("album/mp3/02.mp3", "chromium141", "direct", "audio/mpeg"),
    ("album/flac/01.flac", "chromium141", "audio", 'audio/mp4; codecs="flac"'),
    ("album/opus/03.opus", "chromium141", "audio", 'audio/mp4; codecs="flac"'),
    ("album/aac/01.m4a", "chrome_ci", "audio", 'audio/mp4; codecs="mp4a.40.2"'),
    ("album/mp3/02.mp3", "safari17", "direct", "audio/mpeg"),
    ("album/flac/01.flac", "safari17", "audio", 'audio/mp4; codecs="flac"'),
]


@pytest.mark.parametrize(("name", "browser", "mode", "mime"), MUSIC)
def test_muziek(name, browser, mode, mime):
    p = probe(name)
    plan = run(p, browser)
    assert plan.mode == mode and plan.video is None and plan.audio == "copy"
    assert D.mime(p, plan, D.default_audio(p)) == mime


def test_muziek_die_niet_kan():
    status, detail = refused(lambda: run(probe("album/aac/01.m4a"), "chromium141"))
    assert status == 415 and detail["reasons"] == ["audio_codec"]


def test_muziek_zonder_mse_is_direct():
    exo = P.merge(
        P.load({"v": 1, "base": "android-exo", "codecs": {"flac": {"mse": False, "file": True}}})
    )
    p = probe("album/flac/01.flac")
    assert D.decide(p, exo, Choice(), LOGGED, FREE).mode == "direct"


def test_geen_video_en_geen_audio():
    empty = Probe(container="matroska", streams=[])
    prof = profile_for("chrome_ci")
    status, detail = refused(lambda: D.decide(empty, prof, Choice(), LOGGED, FREE))
    assert status == 409 and detail["code"] == "media_unavailable"


# --- kiezen ------------------------------------------------------------------------------------


def test_kiezen_van_audio_en_ondertitels():
    p = probe("hevc10_hdr10.mkv")
    assert D.default_audio(p).index == 1  # het spoor met default
    assert D.pick_audio(p, 2).index == 2
    assert D.pick_audio(p, 0) is None  # dat is video
    assert D.embedded_subtitle(p, "e3").codec == "subrip"
    assert D.embedded_subtitle(p, "e1") is None and D.embedded_subtitle(p, "x0") is None
    first = probe("vp9_opus.webm")
    first.streams[1].default = False
    assert D.default_audio(first).index == 1  # geen default: het eerste


def test_geforceerd_beeld_in_de_taal_van_het_audiospoor():
    p = pgs_forced()
    p.streams.append(Stream(index=2, type="audio", codec="aac", codec_string="mp4a.40.2",
                            lang="eng", default=True))  # fmt: skip
    plan = run(p, "chrome_ci")
    assert plan.forced_available is None  # PGS is nld, het geluid eng
    p.streams[-1].lang = "nld"
    assert run(p, "chrome_ci").forced_available == "e1"
    # met een gekozen ondertitel geen aanbod
    assert run(p, "chrome_ci", subtitle="x0").forced_available is None


# --- review golf 1, K4: een geforceerd beeldspoor en direct -----------------------------------

MKV_FILE = {"file": ["video/x-matroska", "video/mp4", "video/webm"]}


@pytest.mark.parametrize("extra", [None, MKV_FILE], ids=["gewoon", "mkv-in-file"])
@pytest.mark.parametrize("browser", list(BROWSERS))
def test_pgs_geforceerd_maakt_er_nooit_een_transcode_van(browser, extra):
    """Het ontwerp (§2 M3, MED-4) vroeg "PGS geforceerd met een profiel dat direct kan: direct".
    PGS staat alleen in mkv, ts en m2ts, en mkv is nooit direct (DIRECT_KINDS), ook niet met
    video/x-matroska in file. Wat de test wel bewijst: het geforceerde beeldspoor maakt er geen
    transcode van. Remux met forced_available; chromium141 (zonder H.264) transcode om de video,
    niet om de ondertitel."""
    plan = run(pgs_forced(), browser, profile=extra)
    assert plan.forced_available == "e1" and plan.burn is None
    assert "subtitle_burn" not in plan.reasons
    want = ("transcode", ["video_codec"]) if browser == "chromium141" else ("remux", ["container"])
    assert (plan.mode, plan.reasons) == want


def mp4_met_geforceerd_beeld(lang: str | None = "eng") -> Probe:
    """h264_aac.mp4 met een geforceerd VobSub-spoor. Zo'n bestand bestaat: ffmpeg -c copy zet het
    dvd_subtitle-spoor van vobsub.mkv in een mp4, en de echte worker geeft dan container mp4,
    dvd_subtitle, forced en taal eng. Het geluid heeft taal und: zo schrijft ffmpeg een mp4."""
    p = probe("h264_aac.mp4")
    sub = Stream(index=2, type="subtitle", codec="dvd_subtitle", forced=True, lang=lang)
    p.streams.append(sub)
    return p


@pytest.mark.parametrize("browser", ["chrome_ci", "safari17", "firefox", "webos5"])
def test_geforceerd_beeld_in_een_mp4_blijft_direct(browser):
    """Het geval dat MED-4 bedoelde, in de enige vorm die bestaat: een directe container."""
    plan = run(mp4_met_geforceerd_beeld(), browser)
    assert (plan.mode, plan.reasons) == ("direct", [])
    assert plan.forced_available == "e2" and plan.burn is None
    # pas als de gebruiker het inbranden kiest: transcode
    chosen = run(mp4_met_geforceerd_beeld(), browser, subtitle="e2")
    assert chosen.mode == "transcode" and "subtitle_burn" in chosen.reasons


def test_und_telt_als_geen_taal():
    """und (ISO 639-2: onbepaald) is geen taal: anders biedt decide een geforceerd spoor in een
    mp4 van ffmpeg nooit aan."""
    p = mp4_met_geforceerd_beeld()
    assert D.default_audio(p).lang == "und"
    assert run(p, "chrome_ci").forced_available == "e2"
    assert run(mp4_met_geforceerd_beeld("und"), "chrome_ci").forced_available == "e2"
    # een echte andere taal blijft weg
    p.streams[1].lang = "nld"
    assert run(p, "chrome_ci").forced_available is None


def test_een_los_bestand_als_ondertitel_houdt_direct():
    assert run(probe("h264_aac.mp4"), "chrome_ci", subtitle="x0").mode == "direct"


def test_het_plan_is_geldig():
    plan = run(probe("h264_ac3_51.mkv", **K), "chrome_ci")
    assert isinstance(plan.audio, AudioEncode) and plan.audio.codec == "aac"
    assert plan.audio.bitrate_k == 160 and plan.audio.channels == 2
    assert Plan.model_validate(plan.model_dump()) == plan
