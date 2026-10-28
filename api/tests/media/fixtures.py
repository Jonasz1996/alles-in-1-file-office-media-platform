"""De mediabestanden voor de tests, gemaakt met ffmpeg uit lavfi (bouwontwerp fase 5, §3.6).

    from tests.media.fixtures import make
    path = make("h264_aac.mp4")

Niets binairs in het repo. Elk recept is een reeks stappen: ffmpeg-commando's met testsrc2 en
sine, en kleine Python-stappen (ondertitels, hoofdstukken, de .sup van sup.py, iTunSMPB). Het
resultaat staat in api/.pytest_cache/media/<hash>/<naam>, op de hash van het recept (met de
broncode van de Python-stappen en de recepten waar het van afhangt): een ander recept is een
nieuwe hash, en een oud bestand wordt nooit hergebruikt. Een hulpfunctie die een stap aanroept
(zoals _count_packets) telt niet mee: verhoog dan VERSION. prune() ruimt oude mappen op.

De fixtures worden na elkaar gemaakt, één ffmpeg tegelijk (regel 13), ook vanuit twee processen
(een slot op de map). Zware fixtures (loadtest/) komen alleen op verzoek: make_all() slaat ze
over, tenzij heavy=True.

Spike punt 14: pgs.mkv muxt de .sup met -copyts (anders 2 s te vroeg), en vobsub.mkv maakt
dvdsub uit dezelfde .sup met -fix_sub_duration (anders gaan de ondertitels nooit weg).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import inspect
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tests.media import sup

API = Path(__file__).resolve().parents[2]
CACHE = API / ".pytest_cache" / "media"
VERSION = 1  # hoger = alles opnieuw
FFMPEG = ("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y")
STEP_TIMEOUT = 600
_FX = re.compile(r"\{fx:([^}]+)\}")


@dataclass(frozen=True)
class Ff:
    """Eén ffmpeg-commando. {out}: het resultaat, {dir}: de werkmap, {fx:naam}: een andere
    fixture. stdout: schrijf stdout naar dit pad (bijvoorbeeld {out}, voor een mkv uit een pipe)."""

    args: tuple[str, ...]
    stdout: str | None = None


@dataclass(frozen=True)
class Py:
    """Een Python-stap: fn(work, out, **kwargs). De broncode telt mee in de hash."""

    fn: Callable[..., None]
    kwargs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Recipe:
    steps: tuple[Ff | Py, ...]
    kind: str = "file"  # file | dir
    heavy: bool = False


# --- bouwstenen ------------------------------------------------------------------------------


def video(size: str, rate: str | int, seconds: float) -> tuple[str, ...]:
    return ("-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={seconds}")


def tone(freq: int, seconds: float, rate: int = 48000) -> tuple[str, ...]:
    return ("-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate={rate}:duration={seconds}")


X264 = ("-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p")
AAC = ("-c:a", "aac", "-b:a", "128k")
OPUS = ("-c:a", "libopus", "-b:a", "96k")
VP9 = ("-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "600k")
PQ = (
    "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020", "-color_trc", "smpte2084",
    "-colorspace", "bt2020nc",
)  # fmt: skip
X265_HDR = (
    "-c:v", "libx265", "-preset", "ultrafast", "-x265-params",
    "log-level=error:hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:"
    "colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)"
    "WP(15635,16450)L(10000000,1):max-cll=1000,400",
)  # fmt: skip
SURROUND = ("-af", "pan=5.1|FL=c0|FR=c0|FC=c0|LFE=c0|BL=c0|BR=c0")

SRT_NL = """1
00:00:01,000 --> 00:00:02,500
Hallo, dit is de eerste regel.

2
00:00:03,000 --> 00:00:04,500
En dit is de tweede.
"""
ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, \
Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,\
2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\an8}Bovenaan in beeld
Dialogue: 0,0:00:03.00,0:00:04.50,Default,,0,0,0,,Onderaan, gewoon
"""
FORCED_SRT = """1
00:00:02,000 --> 00:00:03,000
[Een bord: UITGANG]
"""


def chapters_meta(titles: list[tuple[int, int, str]]) -> str:
    """Een ffmetadata-bestand met hoofdstukken (start en eind in ms)."""
    out = [";FFMETADATA1"]
    for start, end, title in titles:
        out += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}", f"title={title}"]
    return "\n".join(out) + "\n"


# --- Python-stappen --------------------------------------------------------------------------


def write_text(work: Path, out: Path, *, name: str, text: str, encoding: str = "utf-8") -> None:
    target = out if name == "{out}" else work / name
    target.write_bytes(text.encode(encoding))


def write_sup(work: Path, out: Path, *, w: int, h: int) -> None:
    sup.write_sup(work / "s.sup", w, h, [(2.0, 4.0, "HALLO"), (5.0, 7.0, "WERELD")])


def truncate(work: Path, out: Path, *, source: str, fraction: float) -> None:
    data = Path(source).read_bytes()
    out.write_bytes(data[: int(len(data) * fraction)])


def random_bytes(work: Path, out: Path, *, size: int, seed: int) -> None:
    out.write_bytes(random.Random(seed).randbytes(size))


def empty(work: Path, out: Path) -> None:
    out.write_bytes(b"")


def album_dirs(work: Path, out: Path) -> None:
    for sub in ("flac", "mp3", "opus", "aac"):
        (out / sub).mkdir(parents=True, exist_ok=True)


def itunsmpb(work: Path, out: Path, *, tracks: list[int], samples: int) -> None:
    """iTunSMPB in de eerste nummers van album/aac (Keuze 18): voorloop 1024 (de AAC-encoder
    van ffmpeg), de opvulling uit het aantal frames, en het aantal geldige samples."""
    from mutagen.mp4 import MP4, MP4FreeForm

    for n in tracks:
        path = out / "aac" / f"{n:02d}.m4a"
        frames = _count_packets(path)
        delay = 1024
        padding = frames * 1024 - samples - delay
        # " 00000000 vertraging opvulling samples(16) 00000000 × 8", zoals iTunes het schrijft
        words = ["00000000", f"{delay:08X}", f"{padding:08X}", f"{samples:016X}"]
        text = " " + " ".join(words + ["00000000"] * 8)
        tags = MP4(path)
        tags["----:com.apple.iTunes:iTunSMPB"] = [MP4FreeForm(text.encode())]
        tags.save()


def _count_packets(path: Path) -> int:
    argv = ["ffprobe", "-v", "error", "-select_streams", "a:0", "-count_packets",
            "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)]  # fmt: skip
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=True)
    return int(done.stdout.strip())


# --- de recepten -----------------------------------------------------------------------------

ALBUM_RATE = 44100
ALBUM_TRACK = 4 * ALBUM_RATE  # samples per nummer; drie nummers uit één doorlopende sinus


# fmt: off
def _album() -> Recipe:
    steps: list[Ff | Py] = [
        Py(album_dirs),
        Ff((*tone(440, 12, ALBUM_RATE), "-ac", "2", "-c:a", "pcm_s16le", "{dir}/full.wav")),
        Ff(("-f", "lavfi", "-i", "testsrc2=size=300x300:rate=1", "-frames:v", "1",
            "{dir}/cover.png")),
    ]
    codecs = {
        "flac": ("flac", ("-c:a", "flac"), True),
        "mp3": ("mp3", ("-c:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "3"), True),
        "opus": ("opus", ("-c:a", "libopus", "-b:a", "128k"), False),
        "aac": ("m4a", ("-c:a", "aac", "-b:a", "192k"), False),
    }
    for sub, (ext, enc, cover) in codecs.items():
        for n in (1, 2, 3):
            start, end = (n - 1) * ALBUM_TRACK, n * ALBUM_TRACK
            meta = (
                "-metadata", f"title=Nummer {n}", "-metadata", "artist=Fase Vijf",
                "-metadata", f"album=Sinus ({sub})", "-metadata", f"track={n}/3",
                "-metadata", "date=2025",
            )
            inputs: tuple[str, ...] = ("-i", "{dir}/full.wav")
            maps: tuple[str, ...] = ("-map", "0:a")
            if cover:
                inputs += ("-i", "{dir}/cover.png")
                maps += ("-map", "1:v", "-c:v", "png" if sub == "flac" else "mjpeg",
                         "-disposition:v", "attached_pic")
            # elk nummer begint op tijd 0 (asetpts), anders telt de tijd van de sinus door
            trim = f"atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS"
            steps.append(Ff((*inputs, *maps, "-af", trim, *enc, *meta,
                             f"{{out}}/{sub}/{n:02d}.{ext}")))
    steps.append(Py(itunsmpb, {"tracks": [1, 2], "samples": ALBUM_TRACK}))
    return Recipe(tuple(steps), kind="dir")


def _loadtest() -> Recipe:
    sources = {
        "testsrc2": "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t",
        "cellauto1": "cellauto=s=1920x1080:rate=24:rule=110:random_seed=1,noise=alls=20:allf=t",
        "cellauto2": "cellauto=s=1920x1080:rate=24:rule=110:random_seed=2,noise=alls=20:allf=t",
    }
    steps = tuple(
        Ff(("-f", "lavfi", "-i", src, *tone(440, 300), "-t", "300", "-c:v", "libx264",
            "-preset", "ultrafast", "-b:v", "6M", "-maxrate", "6M", "-bufsize", "12M",
            "-threads", "3", *AAC, f"{{out}}/{name}.mkv"))
        for name, src in sources.items()
    )
    return Recipe((Py(album_dirs_flat), *steps), kind="dir", heavy=True)
# fmt: on


def album_dirs_flat(work: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)


RECIPES: dict[str, Recipe] = {
    "h264_aac.mp4": Recipe((
        Ff((*video("1280x720", 24, 10), *tone(440, 10), *X264, "-g", "48", *AAC,
            "-movflags", "+faststart", "{out}")),
    )),
    "h264_aac_moov_end.mp4": Recipe((
        Ff(("-i", "{fx:h264_aac.mp4}", "-map", "0", "-c", "copy", "-f", "mp4", "{out}")),
    )),
    "vp9_opus.webm": Recipe((
        Ff((*video("640x360", 24, 6), *tone(440, 6), *VP9, "-g", "48", *OPUS, "{out}")),
    )),
    "vp9_opus.mkv": Recipe((
        Ff((*video("640x360", 24, 6), *tone(440, 6), *tone(660, 6), "-map", "0:v", "-map", "1:a",
            "-map", "2:a", *VP9, "-g", "48", *OPUS, "-metadata:s:a:0", "language=nld",
            "-metadata:s:a:1", "language=eng", "{out}")),
    )),
    "av1_opus.mp4": Recipe((
        Ff((*video("640x360", 24, 4), *tone(440, 4), "-c:v", "libsvtav1", "-preset", "12",
            "-g", "48", *OPUS, "{out}")),
    )),
    "hevc10_hdr10.mkv": Recipe((
        Py(write_text, {"name": "nl.srt", "text": SRT_NL}),
        Py(write_text, {"name": "top.ass", "text": ASS}),
        Py(write_text, {"name": "ch.ffmeta", "text": chapters_meta(
            [(0, 2000, "Begin"), (2000, 4000, "Midden"), (4000, 6000, "Einde")])}),
        Ff((*video("1280x720", 24, 6), *tone(440, 6), *tone(660, 6),
            "-i", "{dir}/nl.srt", "-i", "{dir}/top.ass", "-i", "{dir}/ch.ffmeta",
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s", "-map", "4:s",
            "-map_chapters", "5", *X265_HDR, *PQ,
            "-filter:a:0", "pan=5.1|FL=c0|FR=c0|FC=c0|LFE=c0|BL=c0|BR=c0",
            "-c:a:0", "ac3", "-b:a:0", "384k", "-c:a:1", "aac", "-b:a:1", "128k",
            "-c:s:0", "srt", "-c:s:1", "ass",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=nld",
            "-metadata:s:s:0", "language=nld", "-metadata:s:s:1", "language=nld",
            "{out}")),
    )),
    "h264_ac3_51.mkv": Recipe((
        Ff((*video("640x360", 24, 6), *tone(440, 6), *X264, *SURROUND, "-c:a", "ac3",
            "-b:a", "384k", "{out}")),
    )),
    "xvid_mp3.avi": Recipe((
        Ff((*video("640x360", 25, 6), *tone(440, 6), "-c:v", "mpeg4", "-vtag", "XVID",
            "-q:v", "5", "-c:a", "libmp3lame", "-b:a", "128k", "{out}")),
    )),
    "interlaced.ts": Recipe((
        Ff((*video("720x576", 25, 6), *tone(440, 6), "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-flags", "+ilme+ildct", "-top", "1",
            "-x264-params", "interlaced=1:tff=1", "-g", "35", *AAC, "-f", "mpegts", "{out}")),
    )),
    "fps23976.mkv": Recipe((
        Ff((*video("640x360", "24000/1001", 8), *tone(440, 8), *X264, *AAC, "{out}")),
    )),
    # Het rooster hangt aan het eerste videobeeld (Keuze 22, spike.md "Extra"): video vanaf
    # 0,5 s, audio vanaf 0 (S = −0,021), dus origin 521 ms en een lang eerste segment
    "late_video.mkv": Recipe((
        Ff(("-itsoffset", "0.5", *video("320x240", 24, 10), *tone(440, 10), "-map", "0:v",
            "-map", "1:a", *X264, "-g", "12", *AAC, "{out}")),
    )),
    "multi_audio.mkv": Recipe((
        Py(write_text, {"name": "forced.srt", "text": FORCED_SRT}),
        Ff((*video("640x360", 24, 6), *tone(440, 6), *tone(660, 6), "-i", "{dir}/forced.srt",
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s", *X264, *AAC,
            "-c:s", "srt", "-metadata:s:a:0", "language=nld", "-metadata:s:a:1", "language=eng",
            "-metadata:s:s:0", "language=nld", "-disposition:a:0", "default",
            "-disposition:a:1", "0", "-disposition:s:0", "forced", "{out}")),
    )),
    "subs_text.mkv": Recipe((
        Py(write_text, {"name": "nl.srt", "text": SRT_NL}),
        Py(write_text, {"name": "top.ass", "text": ASS}),
        Ff((*video("320x180", 24, 6), "-i", "{dir}/nl.srt", "-i", "{dir}/top.ass",
            "-map", "0:v", "-map", "1:s", "-map", "2:s", *X264, "-c:s:0", "srt",
            "-c:s:1", "ass", "-metadata:s:s:0", "language=nld", "-metadata:s:s:1",
            "language=eng", "{out}")),
    )),
    "nl.srt": Recipe((
        Py(write_text, {"name": "{out}", "encoding": "cp1252",
                        "text": "1\r\n00:00:01,000 --> 00:00:02,000\r\nCafé, één, ë en ü\r\n"}),
    )),
    "en.ass": Recipe((Py(write_text, {"name": "{out}", "text": ASS}),)),
    "film.nl.forced.srt": Recipe((Py(write_text, {"name": "{out}", "text": FORCED_SRT}),)),
    "bom.srt": Recipe((Py(write_text, {"name": "{out}", "text": "\ufeff" + SRT_NL}),)),
    "pgs.mkv": Recipe((
        Py(write_sup, {"w": 1280, "h": 720}),
        Ff((*video("1280x720", 24, 8), *X264, "-g", "48", "{dir}/v.mkv")),
        Ff(("-copyts", "-i", "{dir}/v.mkv", "-i", "{dir}/s.sup", "-map", "0:v", "-map", "1:0",
            "-c", "copy", "-metadata:s:s:0", "language=nld", "{out}")),
    )),
    "vobsub.mkv": Recipe((
        Py(write_sup, {"w": 1280, "h": 720}),
        Ff((*video("1280x720", 24, 8), *X264, "-g", "48", "{dir}/v.mkv")),
        Ff(("-copyts", "-i", "{dir}/v.mkv", "-fix_sub_duration", "-i", "{dir}/s.sup",
            "-map", "0:v", "-map", "1:0", "-c:v", "copy", "-c:s", "dvdsub",
            "-metadata:s:s:0", "language=nld", "{out}")),
    )),
    "fps240.mp4": Recipe((
        Ff((*video("640x360", 240, 6), "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "{out}")),
    )),
    "noaudio.mp4": Recipe((
        Ff((*video("640x360", 24, 4), *X264, "-movflags", "+faststart", "{out}")),
    )),
    "h264_hi10p.mkv": Recipe((
        Ff((*video("640x360", 24, 4), "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p10le", "-profile:v", "high10", "{out}")),
    )),
    "a.ts": Recipe((
        Ff((*video("640x360", 24, 4), *tone(440, 4), *X264, *AAC, "-f", "mpegts", "{out}")),
    )),
    "a.m2ts": Recipe((
        Ff((*video("640x360", 24, 4), *tone(440, 4), "-c:v", "libx265", "-preset", "ultrafast",
            "-x265-params", "log-level=error", "-pix_fmt", "yuv420p", *AAC,
            "-f", "mpegts", "-mpegts_m2ts_mode", "1", "{out}")),
    )),
    "hevc2160_hdr10.mkv": Recipe((
        # 960×540 opgeschaald (naaste buur): hetzelfde formaat, de helft van de tijd
        Ff((*video("960x540", 24, 2), "-vf", "scale=3840:2160:flags=neighbor", *X265_HDR, *PQ,
            "{out}")),
    )),
    "long_gop.mkv": Recipe((
        Ff((*video("320x180", 24, 60), *X264, "-g", "600", "-keyint_min", "600",
            "-sc_threshold", "0", "{out}")),
    )),
    "irregular_gop.mkv": Recipe((
        Ff((*video("320x180", 24, 20), *X264, "-g", "1000", "-sc_threshold", "0",
            "-force_key_frames", "0,1.5,2.2,7.9,8.3,14,19", "{out}")),
    )),
    "no_cues.mkv": Recipe((
        Ff((*video("320x180", 24, 6), *tone(440, 6), *X264, *AAC, "-f", "matroska", "pipe:1"),
           stdout="{out}"),
    )),
    "chapters.mkv": Recipe((
        Py(write_text, {"name": "ch.ffmeta", "text": chapters_meta(
            [(0, 5000, "Intro"), (5000, 24000, "Deel 1"), (24000, 30000, "End Credits")])}),
        Ff((*video("320x180", 24, 30), *tone(440, 30), "-i", "{dir}/ch.ffmeta",
            "-map", "0:v", "-map", "1:a", "-map_chapters", "2", *X264, *AAC, "{out}")),
    )),
    "album": _album(),
    "broken.mp4": Recipe((
        Py(truncate, {"source": "{fx:h264_aac_moov_end.mp4}", "fraction": 0.4}),
    )),
    "random.mkv": Recipe((Py(random_bytes, {"size": 256 * 1024, "seed": 5}),)),
    "empty.mkv": Recipe((Py(empty),)),
    "loadtest": _loadtest(),
}  # fmt: skip

NAMES = tuple(RECIPES)
HEAVY = frozenset(name for name, r in RECIPES.items() if r.heavy)
# Wat de probe niet kan lezen: een nette fout, geen probe
UNREADABLE = frozenset({"broken.mp4", "random.mkv", "empty.mkv"})
LOOSE_SUBTITLES = frozenset({"nl.srt", "en.ass", "film.nl.forced.srt", "bom.srt"})


def _deps(recipe: Recipe) -> list[str]:
    found: list[str] = []
    for step in recipe.steps:
        values = step.args if isinstance(step, Ff) else [str(v) for v in step.kwargs.values()]
        for value in values:
            found += _FX.findall(value)
    return found


def recipe_hash(name: str) -> str:
    recipe = RECIPES[name]
    parts: list = [VERSION, name, recipe.kind]
    for step in recipe.steps:
        if isinstance(step, Ff):
            parts.append(["ff", list(step.args), step.stdout])
        else:
            parts.append(
                ["py", inspect.getsource(step.fn), json.dumps(step.kwargs, sort_keys=True)]
            )
    parts.append({dep: recipe_hash(dep) for dep in _deps(recipe)})
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


def path_of(name: str) -> Path:
    return CACHE / recipe_hash(name) / name


@contextlib.contextmanager
def _lock() -> Iterator[None]:
    CACHE.mkdir(parents=True, exist_ok=True)
    with open(CACHE / ".lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _fill(value: str, out: Path, work: Path) -> str:
    value = value.replace("{out}", str(out)).replace("{dir}", str(work))
    return _FX.sub(lambda m: str(_make(m.group(1))), value)


def _env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C", "HOME": "/tmp"}
    env["SVT_LOG"] = "1"  # libsvtav1 alleen fouten
    return env


def _build(name: str, recipe: Recipe, out: Path, work: Path) -> None:
    if recipe.kind == "dir":
        out.mkdir(parents=True)
    for step in recipe.steps:
        if isinstance(step, Py):
            kwargs = {
                k: _fill(v, out, work) if isinstance(v, str) else v for k, v in step.kwargs.items()
            }
            step.fn(work, out, **kwargs)
            continue
        argv = [*FFMPEG, *(_fill(a, out, work) for a in step.args)]
        stdout = None
        try:
            if step.stdout is not None:
                stdout = open(_fill(step.stdout, out, work), "wb")  # noqa: SIM115
            done = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout or subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=STEP_TIMEOUT,
                check=False,
                env=_env(),
            )
        finally:
            if stdout is not None:
                stdout.close()
        if done.returncode != 0:
            tail = done.stderr.decode("utf-8", "replace")[-1500:]
            raise RuntimeError(f"fixture {name}: ffmpeg stopte met {done.returncode}: {tail}")


def _make(name: str) -> Path:
    if name not in RECIPES:
        raise KeyError(f"onbekende fixture {name}")
    target = path_of(name)
    if target.exists():
        return target
    recipe = RECIPES[name]
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE, prefix=".maak-") as tmp:
        work = Path(tmp)
        out = work / "uit" / name
        out.parent.mkdir()
        _build(name, recipe, out, work)
        os.replace(out, target)
    return target


def make(name: str) -> Path:
    """Het pad van deze fixture; maakt hem als hij er (met dit recept) nog niet is."""
    target = path_of(name)
    if target.exists():
        return target
    with _lock():
        return _make(name)


def make_all(*, heavy: bool = False) -> dict[str, Path]:
    """Alle fixtures (zonder loadtest/, tenzij heavy), na elkaar."""
    return {name: make(name) for name in NAMES if heavy or name not in HEAVY}


def available() -> bool:
    """Zijn ffmpeg en ffprobe er (anders slaan de tests met marker media over)?"""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def prune() -> int:
    """Weg met elke map in de cache die niet bij een huidig recept hoort; geeft het aantal."""
    keep = {recipe_hash(name) for name in NAMES}
    removed = 0
    if not CACHE.exists():
        return 0
    with _lock():
        for entry in CACHE.iterdir():
            if entry.is_dir() and not entry.name.startswith(".") and entry.name not in keep:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
    return removed
