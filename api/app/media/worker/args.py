"""De argumenten per opdracht (bouwontwerp fase 5, §3.3). Frozen.

De worker valideert elke vraag met deze klassen (pydantic, extra velden geweigerd) en bouwt
pas dan de argv. De client gebruikt dezelfde klassen. Er staat nooit een pad of vrije tekst in:
alleen getallen, vaste woorden uit een Literal en indexen van streams.

Hier staat ook wat de worker per opdracht telt (Keuze 12): de soort plaats (work_class), hoeveel
ffmpeg- of ffprobe-processen er tegelijk draaien (procs), hoeveel fd's er meekomen (fd_layout) en
de grens op het geheugen (memory_mb).

Dit bestand importeert niets van de app: de worker draait als `python -I -m app.media.worker`
in een container zonder de rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.media.wire import Op, WorkClass

# De demuxers van de witte lijst: render.container plus mp3, flac en aac (Keuze 12). lavfi staat
# er nooit in: alleen synth en bench krijgen het, zonder invoer-fd (spike, punt 7).
Demuxer = Literal[
    "mov", "matroska", "avi", "wav", "flv", "ogg", "asf", "mpegts", "mpeg", "mp3", "flac", "aac"
]
DEMUXERS: tuple[str, ...] = get_args(Demuxer)

# Van de container in media_probe naar de demuxer van ffmpeg
CONTAINER_DEMUXER: dict[str, str] = {
    "mp4": "mov",
    "mov": "mov",
    "m4v": "mov",
    "m4a": "mov",
    "3gp": "mov",
    "matroska": "matroska",
    "webm": "matroska",
    "avi": "avi",
    "wav": "wav",
    "flv": "flv",
    "ogg": "ogg",
    "asf": "asf",
    "mpegts": "mpegts",
    "m2ts": "mpegts",
    "mpeg": "mpeg",
    "mp3": "mp3",
    "flac": "flac",
    "aac": "aac",
}

# Een index van een stream; de probe houdt er hooguit 64 (Keuze 12)
Index = Annotated[int, Field(ge=0, le=63)]
Seconds = Annotated[float, Field(ge=0, le=7 * 24 * 3600)]
# De grenzen op het geheugen per proces (RLIMIT_DATA, Keuze 12 en spike punt 6)
SHORT_MEMORY_MB = 512
# De bovengrens op threads per ffmpeg- of ffprobe-proces: elke argv krijgt `-threads 4` (spike
# punt 6); de CPU-grens van de container en de plaatsen doen de rest
THREADS = 4


def demuxer_of(container: str | None) -> str | None:
    """De demuxer bij de container van een probe, of None (dan proeft de worker zelf)."""
    return CONTAINER_DEMUXER.get(container or "")


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapsArgs(_Args):
    """Geen argumenten: de worker antwoordt uit de cache van de start."""


class BenchArgs(_Args):
    """De meting van Keuze 26: zoveel seconden testsrc2 met ruis naar 720p, en zoveel
    seconden HEVC10 decoderen. M1 bouwt de opdracht."""

    encode_s: int = Field(default=10, ge=2, le=30)
    decode_s: int = Field(default=4, ge=1, le=30)


class ProbeArgs(_Args):
    """ffprobe op de invoer. demuxer None: de worker proeft de eerste bytes zelf."""

    demuxer: Demuxer | None = None


class KeyframesArgs(_Args):
    """De keyframe-index (Keuze 17). fallback: ffprobe op alle pakketten (leest het hele
    bestand, dus alleen op de achtergrond)."""

    demuxer: Demuxer
    video_index: Index
    fallback: bool = False


class AudioInfoArgs(_Args):
    """Gapless-gegevens van één audiospoor (Keuze 18)."""

    demuxer: Demuxer | None = None
    audio_index: Index = 0


class NfoArgs(_Args):
    """Een .nfo-bestand lezen (Keuze 9); geen ffmpeg. Hooguit max_bytes."""

    max_bytes: int = Field(default=1024 * 1024, ge=1, le=4 * 1024 * 1024)


class FrameArgs(_Args):
    """Eén beeld als JPEG: een preview of een ingebedde hoes."""

    demuxer: Demuxer
    video_index: Index
    at_s: Seconds = 0
    width: int = Field(default=640, ge=16, le=3840)


class MetaArgs(_Args):
    """Wat render_meta nu met ffprobe leest (opnametijd, plaats, maat)."""

    demuxer: Demuxer | None = None


class SubTrack(_Args):
    """Eén tekstspoor uit de bron; kind kiest de uitvoer van ffmpeg (§3.3)."""

    index: Index
    kind: Literal["ass", "webvtt", "srt"]


class SubsArgs(_Args):
    """Ondertitels naar WebVTT (Keuze 29). Met tracks: de sporen uit de bron, in één run. Met
    loose: een los bestand van die soort, zonder ffmpeg (hooguit 5 MB)."""

    demuxer: Demuxer | None = None
    tracks: list[SubTrack] = Field(default_factory=list, max_length=8)
    loose: Literal["srt", "vtt", "ass", "ssa"] | None = None

    @model_validator(mode="after")
    def _een_van_beide(self) -> SubsArgs:
        if bool(self.tracks) == (self.loose is not None):
            raise ValueError("precies één van tracks en loose")
        if self.tracks and self.demuxer is None:
            raise ValueError("tracks vraagt een demuxer")
        return self


class SpritesArgs(_Args):
    """Scrub-sprites (Keuze 30): elke 10 s een beeld van 160 breed, 10×10 per tegel."""

    demuxer: Demuxer
    video_index: Index


class VideoEncode(_Args):
    """Wat de transcode met het beeld doet (Keuze 24, 27). burn_index: een beeldondertitel om in
    te branden; die komt uit een tweede fd van dezelfde blob, gelezen vanaf burn_start_s
    (max(0, T − 30), spike punt 14)."""

    codec: Literal["libx264", "libvpx-vp9"]
    width: int = Field(ge=64, le=3840, multiple_of=2)
    height: int = Field(ge=64, le=2160, multiple_of=2)
    preset: Literal["veryfast", "superfast"] = "veryfast"
    # Alleen bij libx264: de levels van de tabel van Keuze 24 (480: 3.0, of 3.1 boven 25 fps,
    # spike punt 16; 720: 3.1, of 3.2 boven 30 fps; 1080: 4.0, of 4.2 boven 30 fps) en wat
    # erboven ligt
    level: Literal["3.0", "3.1", "3.2", "4.0", "4.1", "4.2", "5.0", "5.1"] | None = None
    maxrate_k: int = Field(ge=100, le=60_000)
    fps_max: int = Field(default=60, ge=1, le=60)
    tonemap: Literal["pq", "hlg"] | None = None
    deinterlace: bool = False
    burn_index: Index | None = None
    burn_start_s: Seconds = 0

    @model_validator(mode="after")
    def _level(self) -> VideoEncode:
        if self.codec == "libx264" and self.level is None:
            raise ValueError("libx264 vraagt een level")
        if self.codec == "libvpx-vp9" and self.level is not None:
            raise ValueError("libvpx-vp9 kent geen level")
        return self


class AudioEncode(_Args):
    """Wat de stroom met het geluid doet als kopiëren niet kan."""

    codec: Literal["aac", "libopus"]
    channels: int = Field(default=2, ge=1, le=2)
    bitrate_k: int = Field(default=160, ge=64, le=320)


class StreamArgs(_Args):
    """Remux of transcode naar fMP4 op pipe:1 (Keuze 22–27).

    start_s is origin + 2k (transcode; met 2k schuift het rooster, zie types.SegmentGrid) of
    b_k − 0,5 (remux), en 0 vanaf het begin. Met -copyts en -output_ts_offset 1 − S is de tijd
    in de uitvoer mediatijd + 1 s; de app rekent alles in mediatijd. TS heeft geen index: na -ss
    begint de uitvoer pas op het volgende keyframe (spike punt 11), dus de app gaat daar niet
    van exact zoeken uit."""

    mode: Literal["remux", "transcode"]
    demuxer: Demuxer
    start_s: Seconds = 0
    source_start_s: float = Field(default=0, ge=-3600, le=7 * 24 * 3600)
    video_index: Index
    audio_index: Index | None = None
    video: Literal["copy"] | VideoEncode
    audio: Literal["copy", "none"] | AudioEncode
    grid: Literal["2s"] | None = None
    hvc1: bool = False
    background: bool = False

    @model_validator(mode="after")
    def _samen(self) -> StreamArgs:
        if self.mode == "remux" and self.video != "copy":
            raise ValueError("remux kopieert het beeld")
        if self.mode == "transcode" and self.video == "copy":
            raise ValueError("transcode zet het beeld om")
        if self.mode == "transcode" and self.grid != "2s":
            raise ValueError("transcode werkt op het rooster van 2 s")
        if self.mode == "remux" and self.grid is not None:
            raise ValueError("bij remux groepeert de app (Keuze 23)")
        if (self.audio_index is None) != (self.audio == "none"):
            raise ValueError("audio none hoort bij een bron zonder audiospoor")
        return self

    @property
    def burn(self) -> bool:
        return isinstance(self.video, VideoEncode) and self.video.burn_index is not None


class RemuxAudioArgs(_Args):
    """Eén nummer naar fMP4 voor gapless (Keuze 38). Opus gaat als flac (spike punt 13)."""

    demuxer: Demuxer
    audio: int = Field(default=0, ge=0, le=63)  # het hoeveelste audiospoor (-map 0:a:N)
    codec: Literal["copy", "flac"] = "copy"
    background: bool = False


class SynthArgs(_Args):
    """Een bron voor de belastingtest (§4.6), alleen met PLATFORM_MEDIA_LOADTEST."""

    source: Literal["testsrc2", "cellauto"]
    seed: int = Field(ge=0, le=9)  # cellauto: random_seed, testsrc2: de ruis
    duration_s: int = Field(ge=30, le=600)
    height: Literal[1080, 720]


ARGS: dict[Op, type[_Args]] = {
    Op.CAPS: CapsArgs,
    Op.BENCH: BenchArgs,
    Op.PROBE: ProbeArgs,
    Op.KEYFRAMES: KeyframesArgs,
    Op.AUDIO_INFO: AudioInfoArgs,
    Op.NFO: NfoArgs,
    Op.FRAME: FrameArgs,
    Op.META: MetaArgs,
    Op.SUBS: SubsArgs,
    Op.SPRITES: SpritesArgs,
    Op.STREAM: StreamArgs,
    Op.REMUX_AUDIO: RemuxAudioArgs,
    Op.SYNTH: SynthArgs,
}


def parse(op: Op, args: object) -> _Args:
    """De argumenten van een vraag, gevalideerd. ValueError als ze niet kloppen (de worker
    antwoordt dan bad_args)."""
    if not isinstance(args, dict):
        raise ValueError("args is geen object")
    try:
        return ARGS[op].model_validate(args)
    except ValidationError as exc:
        # Alleen de plaats en de soort fout: geen waarden terug (ook niet in het log)
        where = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '-'}: {e['type']}" for e in exc.errors()
        )
        raise ValueError(where[:500]) from None


def work_class(op: Op, args: _Args) -> WorkClass:
    """De soort plaats van Keuze 12."""
    if op == Op.CAPS:
        return WorkClass.CONTROL
    if op == Op.STREAM:
        assert isinstance(args, StreamArgs)
        return WorkClass.TRANSCODE if args.mode == "transcode" else WorkClass.REMUX
    if op == Op.REMUX_AUDIO:
        return WorkClass.REMUX
    if op in (Op.SUBS, Op.SPRITES, Op.BENCH, Op.SYNTH):
        return WorkClass.BACKGROUND
    if op == Op.KEYFRAMES and isinstance(args, KeyframesArgs) and args.fallback:
        return WorkClass.BACKGROUND
    return WorkClass.SHORT


def procs(op: Op, args: _Args) -> int:
    """Hoeveel ffmpeg- en ffprobe-processen deze opdracht hooguit tegelijk draait. De worker
    reserveert ze vooraf in PLATFORM_MEDIA_MAX_PROCS. De probe draait zijn twee ffprobes na
    elkaar, nooit samen op één fd (spike punt 1); bench heeft er twee aan één pipe."""
    if op in (Op.CAPS, Op.NFO):
        return 0
    if op == Op.SUBS and isinstance(args, SubsArgs) and args.loose is not None:
        return 0
    if op == Op.BENCH:
        return 2
    return 1


@dataclass(frozen=True)
class Layout:
    """De fd's van een vraag: [invoer…, uitvoer…]. Een invoer is een eigen open() van de blob
    (nooit dezelfde fd voor twee kinderen tegelijk, spike punt 1); een uitvoer is een
    schrijfkant die de app maakte, of bij synth het bestand in media/loadtest."""

    inputs: int
    outputs: int

    @property
    def total(self) -> int:
        return self.inputs + self.outputs


def fd_layout(op: Op, args: _Args) -> Layout:
    """Hoeveel fd's de worker bij deze vraag verwacht; elk ander aantal is bad_fds.

    - stream: de bron, bij inbranden de bron nog een keer (de ondertitels), dan pipe:1;
    - subs met tracks: de bron, dan per spoor drie: de schrijfkant voor ffmpeg (pipe:4…), de
      leeskant ervan voor de worker, en de schrijfkant voor de VTT terug naar de app. De app
      geeft ze in die volgorde: [bron, (ffmpeg_w, worker_r) × N, vtt_w × N];
    - subs met loose: het bestand, dan de schrijfkant voor de VTT;
    - synth: geen invoer, één uitvoer (het bestand);
    - caps en bench: geen fd's."""
    if op in (Op.CAPS, Op.BENCH):
        return Layout(0, 0)
    if op == Op.SYNTH:
        return Layout(0, 1)
    if op == Op.STREAM:
        assert isinstance(args, StreamArgs)
        return Layout(2 if args.burn else 1, 1)
    if op == Op.SUBS:
        assert isinstance(args, SubsArgs)
        return Layout(1, 1 if args.loose else 3 * len(args.tracks))
    if op in (Op.FRAME, Op.SPRITES, Op.REMUX_AUDIO):
        return Layout(1, 1)
    return Layout(1, 0)  # probe, keyframes, audio_info, nfo, meta


def memory_mb(op: Op, job_memory_mb: int) -> int:
    """RLIMIT_DATA per kind: job_memory_mb voor live, sprites, bench en synth; 512 MB voor het
    korte werk en de ondertitels (Keuze 12)."""
    if op in (Op.STREAM, Op.REMUX_AUDIO, Op.SPRITES, Op.BENCH, Op.SYNTH):
        return job_memory_mb
    return SHORT_MEMORY_MB


def niceness(op: Op, args: _Args) -> tuple[str, ...]:
    """Het voorvoegsel van de argv (§3.3): nice 5 voor een live transcode, nice 15 en ionice -c 3
    op de achtergrond, nice 15 voor één beeld; remux en het korte werk zonder."""
    background = ("nice", "-n", "15", "ionice", "-c", "3")
    if op in (Op.STREAM, Op.REMUX_AUDIO):
        if getattr(args, "background", False):
            return background
        live = op == Op.STREAM and getattr(args, "mode", "") == "transcode"
        return ("nice", "-n", "5") if live else ()
    if work_class(op, args) == WorkClass.BACKGROUND:
        return background
    if op == Op.FRAME:
        return ("nice", "-n", "15")
    return ()
