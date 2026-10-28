"""probe: ffprobe op de invoer-fd, opgeschoond tot types.Probe (Keuze 15). Frozen.

Na elkaar, elk met lseek(0) ervoor (run.spawn), nooit twee tegelijk op de fd (spike punt 1):
1. -show_format -show_streams -show_chapters;
2. de extradata van de videosporen (-show_data), voor de codec-strings (Keuze 16);
3. bij HDR: de side data van het eerste beeld (mastering, MaxCLL; -read_intervals %+#1);
4. de tijd van het laatste videobeeld (spike punt 10): de pakketten van de laatste 10 s.

De demuxer kiest de worker zelf uit de eerste bytes (zoals render.container, plus mp3, flac,
aac en m2ts): ffprobe raadt nooit, en -format_whitelist laat alleen de witte lijst toe.

Grenzen (Keuze 12, 15): hooguit 64 streams en 64 hoofdstukken, bijlagen alleen geteld; wat
afvalt staat in truncated. Een beeld boven 8192×4320 geeft state failed met error too_large
(415 media_unsupported in de app). Het antwoord past altijd in MAX_MESSAGE (fit).

Alle tijden in mediatijd (types.py): start_ms is de start S van de bron; de streams,
hoofdstukken en last_frame_ms tellen vanaf S.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from app.media import codecs, wire
from app.media.types import Probe
from app.media.wire import Code
from app.media.worker.args import DEMUXERS, THREADS, ProbeArgs
from app.media.worker.server import Context, OpError

MAX_STREAMS = 64
MAX_CHAPTERS = 64
MAX_TAGS = 48
MAX_PIXELS = 8192 * 4320
TEXT = 200
TAG_TEXT = 500
EXTRADATA_MAX = 64 * 1024
LAST_WINDOW_S = 10.0
STEP_LIMIT_S = 20.0
WHITELIST = ",".join(DEMUXERS)
_HEX_LINE = re.compile(r"^[0-9a-f]{8}: ((?:[0-9a-f]{2,4} ?){1,8})")
_DURATION = re.compile(r"^(\d+):(\d{2}):(\d{2}(?:\.\d+)?)$")
_PIX_DEPTH = re.compile(r"p(\d{1,2})(?:le|be)?$")
_INTERLACED = {"tt", "bb", "tb", "bt"}
_TYPES = {"video", "audio", "subtitle", "data", "attachment"}


# --- de demuxer kiezen ---------------------------------------------------------------------


def _vint(data: bytes, at: int) -> tuple[int, int] | None:
    """Een EBML-getal: (waarde, lengte)."""
    if at >= len(data) or data[at] == 0:
        return None
    first = data[at]
    length = 8 - first.bit_length() + 1
    if at + length > len(data):
        return None
    value = first & ((1 << (8 - length)) - 1)
    for b in data[at + 1 : at + length]:
        value = (value << 8) | b
    return value, length


def _doctype(head: bytes) -> bytes | None:
    at = head.find(b"\x42\x82", 4, 64)
    if at < 0:
        return None
    size = _vint(head, at + 2)
    if size is None or size[0] > 16:
        return None
    start = at + 2 + size[1]
    return head[start : start + size[0]]


def _audio_frame(head: bytes) -> tuple[str, str] | None:
    if head[:4] == b"fLaC":
        return "flac", "flac"
    if len(head) >= 2 and head[0] == 0xFF:
        if head[1] & 0xF6 == 0xF0:  # ADTS: sync 0xFFF, layer 00
            return "aac", "aac"
        if head[1] & 0xE0 == 0xE0 and head[1] & 0x06:  # MPEG-audio, layer I–III
            return "mp3", "mp3"
    return None


def sniff(head: bytes, after_id3: bytes | None = None) -> tuple[str, str] | None:
    """(demuxer, container) uit de eerste bytes, of None. after_id3: de bytes na een ID3-tag
    (die de aanroeper apart leest, zie id3_size)."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return "mov", "mov"
        if brand in (b"M4V ", b"M4VP", b"M4VH"):
            return "mov", "m4v"
        if brand in (b"M4A ", b"M4B ", b"M4P "):
            return "mov", "m4a"
        if brand.startswith(b"3g"):
            return "mov", "3gp"
        return "mov", "mp4"
    if head[4:8] in (b"moov", b"mdat", b"free", b"wide", b"skip"):
        return "mov", "mov"
    if head[:4] == b"\x1aE\xdf\xa3":
        return "matroska", "webm" if _doctype(head) == b"webm" else "matroska"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi", "avi"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav", "wav"
    if head[:3] == b"FLV":
        return "flv", "flv"
    if head[:4] == b"OggS":
        return "ogg", "ogg"
    if head[:16] == bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c"):
        return "asf", "asf"
    if len(head) > 376 and head[0] == head[188] == head[376] == 0x47:
        return "mpegts", "mpegts"
    if len(head) > 388 and head[4] == head[196] == head[388] == 0x47:
        return "mpegts", "m2ts"
    if head[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return "mpeg", "mpeg"
    if head[:3] == b"ID3":
        return _audio_frame(after_id3 or b"")
    return _audio_frame(head)


def id3_size(head: bytes) -> int | None:
    """De lengte van een ID3v2-tag vooraan (met kop en eventuele voet), of None."""
    if len(head) < 10 or head[:3] != b"ID3" or any(b & 0x80 for b in head[6:10]):
        return None
    size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
    return 10 + size + (10 if head[5] & 0x10 else 0)


# --- de ruwe JSON opschonen ------------------------------------------------------------------


def _float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) < 1e12 else None  # geen NaN of oneindig


def _int(value: Any) -> int | None:
    f = _float(value)
    return int(f) if f is not None else None


def _ms(seconds: Any) -> int | None:
    f = _float(seconds)
    return round(f * 1000) if f is not None else None


def _text(value: Any, limit: int = TEXT) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\x00", "").strip()
    return text[:limit] or None


def _tags(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip().lower()[:64]
        text = _text(value, TAG_TEXT)
        if name and text and name not in out:
            out[name] = text
        if len(out) >= MAX_TAGS:
            break
    return out


def _rate(value: Any) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    num, _, den = value.partition("/")
    n, d = _float(num), _float(den)
    if not n or not d or n <= 0 or d <= 0:
        return None
    fps = n / d
    return round(fps, 3) if 0 < fps <= 1000 else None


def _duration_tag(tags: dict[str, str]) -> int | None:
    for key in ("duration", "duration-eng"):
        m = _DURATION.match(tags.get(key, ""))
        if m:
            h, mi, s = m.groups()
            return round((int(h) * 3600 + int(mi) * 60 + float(s)) * 1000)
    return None


def _bit_depth(raw: dict, kind: str) -> int | None:
    bits = _int(raw.get("bits_per_raw_sample"))
    if bits and 0 < bits <= 32:
        return bits
    if kind != "video":
        return None
    m = _PIX_DEPTH.search(str(raw.get("pix_fmt") or ""))
    if m and 8 <= int(m.group(1)) <= 16:
        return int(m.group(1))
    return 8


def _dovi(raw: dict) -> int | None:
    for side in raw.get("side_data_list") or []:
        if isinstance(side, dict) and "DOVI" in str(side.get("side_data_type", "")):
            return _int(side.get("dv_profile"))
    return None


def _hdr(raw: dict, dv: int | None) -> str:
    if dv == 5:
        return "dv5"  # geen bruikbare basislaag
    transfer = str(raw.get("color_transfer") or "")
    if transfer == "smpte2084":
        return "hdr10"
    if transfer == "arib-std-b67":
        return "hlg"
    return "none"


def _hdr_meta(frames: Any) -> dict | None:
    """Mastering en MaxCLL uit de side data van het eerste beeld; alleen korte waarden."""
    if not isinstance(frames, list) or not frames or not isinstance(frames[0], dict):
        return None
    meta: dict[str, dict] = {}
    for side in frames[0].get("side_data_list") or []:
        if not isinstance(side, dict):
            continue
        kind = str(side.get("side_data_type", ""))
        if "Mastering" in kind:
            name = "mastering"
        elif "light level" in kind.lower():
            name = "cll"
        else:
            continue
        meta[name] = {
            str(k)[:32]: v if isinstance(v, int | float) else str(v)[:32]
            for k, v in list(side.items())[:16]
            if k != "side_data_type"
        }
    return meta or None


def stream(raw: dict, start_s: float, extradata: bytes | None = None) -> dict:
    """Eén stream van ffprobe als types.Stream."""
    kind = str(raw.get("codec_type") or "data")
    if kind not in _TYPES:
        kind = "data"
    tags = _tags(raw.get("tags"))
    disp = raw.get("disposition") if isinstance(raw.get("disposition"), dict) else {}
    out: dict[str, Any] = {
        "index": _int(raw.get("index")) or 0,
        "type": kind,
        "codec": _text(raw.get("codec_name"), 32),
        "profile": _text(raw.get("profile"), 64),
        "lang": _text(tags.get("language"), 16),
        "title": _text(tags.get("title")),
        "default": bool(disp.get("default")),
        "forced": bool(disp.get("forced")),
        "hearing_impaired": bool(disp.get("hearing_impaired")),
        "attached_pic": bool(disp.get("attached_pic")),
        "bitrate": _int(raw.get("bit_rate")) or _int(tags.get("bps")) or _int(tags.get("bps-eng")),
    }
    duration = _ms(raw.get("duration"))
    out["duration_ms"] = duration if duration is not None else _duration_tag(tags)
    start = _float(raw.get("start_time"))
    out["start_ms"] = round((start - start_s) * 1000) if start is not None else None
    if kind == "video":
        dv = _dovi(raw)
        level = _int(raw.get("level"))
        out.update(
            width=_int(raw.get("width")),
            height=_int(raw.get("height")),
            fps=_rate(raw.get("avg_frame_rate")) or _rate(raw.get("r_frame_rate")),
            pix_fmt=_text(raw.get("pix_fmt"), 32),
            bit_depth=_bit_depth(raw, kind),
            level=level if level is not None and level > 0 else None,
            color={
                "primaries": _text(raw.get("color_primaries"), 32),
                "transfer": _text(raw.get("color_transfer"), 32),
                "matrix": _text(raw.get("color_space"), 32),
            },
            hdr=_hdr(raw, dv),
            dv_profile=dv,
            interlaced=str(raw.get("field_order") or "") in _INTERLACED,
        )
    elif kind == "audio":
        out.update(
            channels=_int(raw.get("channels")),
            layout=_text(raw.get("channel_layout"), 32),
            sample_rate=_int(raw.get("sample_rate")),
            bit_depth=_bit_depth(raw, kind),
        )
    if kind in ("video", "audio"):
        try:
            out["codec_string"] = codecs.strings(raw, extradata)
        except Exception:  # een stub of lezer die struikelt, mag de probe niet breken
            out["codec_string"] = None
    return out


def clean(
    raw: dict,
    container: str | None,
    *,
    extradata: dict[int, bytes] | None = None,
    frames: dict[int, Any] | None = None,
    last_frame_s: dict[int, float] | None = None,
) -> dict:
    """De JSON van ffprobe als types.Probe (zonder blob_sha), met de grenzen van Keuze 12."""
    fmt = raw.get("format") if isinstance(raw.get("format"), dict) else {}
    start_s = _float(fmt.get("start_time")) or 0.0
    streams_raw = [s for s in raw.get("streams") or [] if isinstance(s, dict)]
    attachments = [s for s in streams_raw if s.get("codec_type") == "attachment"]
    streams_raw = [s for s in streams_raw if s.get("codec_type") != "attachment"]
    truncated: dict[str, int] = {}
    if attachments:
        truncated["attachments"] = len(attachments)
    if len(streams_raw) > MAX_STREAMS:
        truncated["streams"] = len(streams_raw) - MAX_STREAMS
        streams_raw = streams_raw[:MAX_STREAMS]
    streams = []
    for s in streams_raw:
        index = _int(s.get("index")) or 0
        item = stream(s, start_s, (extradata or {}).get(index))
        if item["type"] == "video":
            item["hdr_meta"] = _hdr_meta((frames or {}).get(index))
            last = (last_frame_s or {}).get(index)
            item["last_frame_ms"] = round((last - start_s) * 1000) if last is not None else None
        streams.append(item)
    chapters_raw = [c for c in raw.get("chapters") or [] if isinstance(c, dict)]
    if len(chapters_raw) > MAX_CHAPTERS:
        truncated["chapters"] = len(chapters_raw) - MAX_CHAPTERS
        chapters_raw = chapters_raw[:MAX_CHAPTERS]
    chapters = []
    for c in chapters_raw:
        start, end = _float(c.get("start_time")), _float(c.get("end_time"))
        if start is None or end is None:
            continue
        title = _text(_tags(c.get("tags")).get("title"))
        chapters.append(
            {
                "start_ms": round((start - start_s) * 1000),
                "end_ms": round((end - start_s) * 1000),
                "title": title,
            }
        )
    tags = _tags(fmt.get("tags"))
    first_audio = next((s for s in streams_raw if s.get("codec_type") == "audio"), None)
    # Ogg (Opus, Vorbis): de muziektags staan bij de stream. Bij andere containers zijn de tags
    # van een stream iets anders (taal, handler, duur) en horen ze niet bij het bestand
    if first_audio is not None and container == "ogg":
        for key, value in _tags(first_audio.get("tags")).items():
            if len(tags) >= MAX_TAGS:
                break
            tags.setdefault(key, value)
    data: dict[str, Any] = {
        "state": "ready",
        "container": container,
        "duration_ms": _ms(fmt.get("duration")),
        "bit_rate": _int(fmt.get("bit_rate")),
        "start_ms": round(start_s * 1000),
        "streams": streams,
        "chapters": chapters,
        "tags": tags,
        "truncated": truncated,
        "error": None,
    }
    for s in streams:
        if s["type"] == "video" and not s["attached_pic"]:
            w, h = s.get("width") or 0, s.get("height") or 0
            if w * h > MAX_PIXELS:
                data["state"] = "failed"
                data["error"] = "too_large"
    return data


def fit(data: dict, budget: int = wire.MAX_MESSAGE - 2048) -> dict:
    """Maak het antwoord kleiner tot het past: eerst de tags, dan de titels, dan de
    hoofdstukken, dan de streams (telt in truncated)."""

    def size() -> int:
        return len(json.dumps({"ev": "result", "data": data}, separators=(",", ":")))

    if size() <= budget:
        return data
    data["tags"] = {k: v[:100] for k, v in list(data["tags"].items())[:16]}
    for s in data["streams"]:
        s["title"] = (s.get("title") or "")[:40] or None
        s["hdr_meta"] = None
    for c in data["chapters"]:
        c["title"] = (c.get("title") or "")[:40] or None
    while size() > budget and data["chapters"]:
        data["chapters"].pop()
        data["truncated"]["chapters"] = data["truncated"].get("chapters", 0) + 1
    while size() > budget and len(data["streams"]) > 1:
        data["streams"].pop()
        data["truncated"]["streams"] = data["truncated"].get("streams", 0) + 1
    if size() > budget:
        data["tags"] = {}
    return data


def hexdump(text: Any) -> bytes | None:
    """De extradata uit -show_data ("00000000: 0142 c00d …  .B..")."""
    if not isinstance(text, str):
        return None
    out = bytearray()
    for line in text.splitlines():
        m = _HEX_LINE.match(line.strip())
        if not m:
            continue
        try:
            out += bytes.fromhex(m.group(1).replace(" ", ""))
        except ValueError:
            return None
        if len(out) > EXTRADATA_MAX:
            return None
    return bytes(out) or None


# --- de opdracht -----------------------------------------------------------------------------


def _argv(demuxer: str, *show: str) -> list[str]:
    return [
        "ffprobe", "-hide_banner", "-loglevel", "error", "-max_alloc", "536870912",
        "-protocol_whitelist", "fd,pipe", "-format_whitelist", WHITELIST,
        "-max_pixels", "35389440", "-threads", str(THREADS), "-f", demuxer, "-fd", "3",
        *show, "-of", "json", "-i", "fd:",
    ]  # fmt: skip


async def _json(ctx: Context, fd: int, argv: list[str], *, required: bool) -> dict | None:
    try:
        done, out, tail = await ctx.run(argv, inputs=[fd], limit_s=STEP_LIMIT_S)
    except TimeoutError:
        if required:
            raise OpError(Code.TIMEOUT, "ffprobe duurde te lang") from None
        return None
    except FileNotFoundError:
        raise OpError(Code.FFMPEG_MISSING, "ffprobe ontbreekt") from None
    except Exception as exc:  # te veel uitvoer en dergelijke
        if required:
            raise OpError(Code.UNREADABLE, str(exc)[:200]) from None
        return None
    if done.rc != 0:
        if required:
            raise OpError(Code.UNREADABLE, tail or f"ffprobe stopte met code {done.rc}")
        return None
    try:
        parsed = json.loads(out)
    except ValueError:
        if required:
            raise OpError(Code.UNREADABLE, "ffprobe gaf geen JSON") from None
        return None
    return parsed if isinstance(parsed, dict) else None


async def probe_fd(ctx: Context, fd: int, demuxer: str | None) -> dict:
    size = os.fstat(fd).st_size
    if size == 0:
        raise OpError(Code.UNREADABLE, "leeg bestand")
    head = os.pread(fd, 4096, 0)
    after = None
    skip = id3_size(head)
    if skip is not None and skip < size:
        after = os.pread(fd, 16, skip)
    found = sniff(head, after)
    if demuxer is None:
        if found is None:
            raise OpError(Code.UNKNOWN_FORMAT, "geen bekend formaat")
        demuxer, container = found
    else:
        container = found[1] if found and found[0] == demuxer else demuxer
    raw = await _json(
        ctx, fd, _argv(demuxer, "-show_format", "-show_streams", "-show_chapters"), required=True
    )
    assert raw is not None
    streams = [s for s in raw.get("streams") or [] if isinstance(s, dict)]
    videos = [
        s
        for s in streams[: MAX_STREAMS + 8]
        if s.get("codec_type") == "video"
        and not (s.get("disposition") or {}).get("attached_pic")
        and isinstance(s.get("index"), int)
    ]
    extradata: dict[int, bytes] = {}
    frames: dict[int, Any] = {}
    last: dict[int, float] = {}
    if any(s.get("codec_name") in ("h264", "hevc", "av1", "vp9") for s in videos):
        show = ("-select_streams", "v", "-show_entries", "stream=index,extradata", "-show_data")
        data = await _json(ctx, fd, _argv(demuxer, *show), required=False)
        for s in (data or {}).get("streams") or []:
            if isinstance(s, dict) and isinstance(s.get("index"), int):
                blob = hexdump(s.get("extradata"))
                if blob:
                    extradata[s["index"]] = blob
    main = videos[0] if videos else None
    if main is not None and str(main.get("color_transfer")) in ("smpte2084", "arib-std-b67"):
        show = ("-select_streams", str(main["index"]), "-read_intervals", "%+#1", "-show_frames",
                "-show_entries", "frame=side_data_list")  # fmt: skip
        data = await _json(ctx, fd, _argv(demuxer, *show), required=False)
        if data:
            frames[main["index"]] = data.get("frames")
    fmt = raw.get("format") if isinstance(raw.get("format"), dict) else {}
    duration = _float(fmt.get("duration"))
    if main is not None and duration and duration > 0:
        begin = max(0.0, (_float(fmt.get("start_time")) or 0.0) + duration - LAST_WINDOW_S)
        show = ("-select_streams", str(main["index"]), "-read_intervals", f"{begin:.3f}%",
                "-show_entries", "packet=pts_time")  # fmt: skip
        data = await _json(ctx, fd, _argv(demuxer, *show), required=False)
        times = [
            t
            for p in (data or {}).get("packets") or []
            if isinstance(p, dict) and (t := _float(p.get("pts_time"))) is not None
        ]
        if times:
            last[main["index"]] = max(times)
    cleaned = clean(raw, container, extradata=extradata, frames=frames, last_frame_s=last)
    return fit(Probe.model_validate(cleaned).model_dump(mode="json", include=_FIELDS))


_FIELDS = {
    "state",
    "container",
    "duration_ms",
    "bit_rate",
    "start_ms",
    "streams",
    "chapters",
    "tags",
    "truncated",
    "error",
}


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, ProbeArgs)
    return await probe_fd(ctx, ctx.inputs[0], ctx.args.demuxer)
