"""meta: wat render.py en render_meta.py van een video weten moeten (§3.3, Keuze 11). Van M1.

Eén ffprobe met -show_format -show_streams op de invoer-fd, met de witte lijsten en grenzen van
elke invoer (probe._argv). De demuxer kiest de worker zelf uit de eerste bytes, zoals de probe;
de app ontleedt de bytes van een video niet meer (regel 5).

Het antwoord heeft de vorm van de JSON van ffprobe, maar alleen met de velden die de preview
gebruikt (de duur, de codecs, de maat en de draaiing, de opnamedatum), opgeschoond en begrensd:

    {"demuxer": "mov", "container": "mp4",
     "format": {"format_name", "duration", "start_time", "bit_rate", "tags": {…}},
     "streams": [{"index", "codec_type", "codec_name", "profile", "pix_fmt", "width",
                  "height", "channels", "sample_rate", "disposition": {"attached_pic"},
                  "tags": {…}, "side_data_list": [{"rotation"}]}, …]}

Van de tags alleen de opnamedatum en de draaiing; locatie en GPS nooit (fase 6).
"""

from __future__ import annotations

import os
from typing import Any

from app.media.wire import Code
from app.media.worker.args import MetaArgs
from app.media.worker.ops import probe
from app.media.worker.server import Context, OpError

MAX_STREAMS = 64
TEXT = 200
TAG_TEXT = 64
TAGS = ("creation_time", "com.apple.quicktime.creationdate", "rotate")
FORMAT_TEXT = ("format_name",)
FORMAT_NUMBERS = ("duration", "start_time", "bit_rate")
STREAM_TEXT = ("codec_type", "codec_name", "profile", "pix_fmt")
STREAM_INTS = ("index", "width", "height", "channels", "sample_rate")
TYPES = ("video", "audio", "subtitle", "data", "attachment")
LIMIT_S = 20.0


def argv(demuxer: str) -> list[str]:
    return probe._argv(demuxer, "-show_format", "-show_streams")


def choose(fd: int, wanted: str | None) -> tuple[str, str]:
    """(demuxer, container): de gevraagde demuxer, of die uit de eerste bytes."""
    size = os.fstat(fd).st_size
    if size == 0:
        raise OpError(Code.UNREADABLE, "leeg bestand")
    head = os.pread(fd, 4096, 0)
    after = None
    skip = probe.id3_size(head)
    if skip is not None and skip < size:
        after = os.pread(fd, 16, skip)
    found = probe.sniff(head, after)
    if wanted is not None:
        return wanted, found[1] if found and found[0] == wanted else wanted
    if found is None:
        raise OpError(Code.UNKNOWN_FORMAT, "geen bekend formaat")
    return found


def _text(value: Any, limit: int = TEXT) -> str | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = str(value).replace("\x00", "").strip()
    return text[:limit] or None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if f == f and abs(f) < 1e12 else None


def _int(value: Any) -> int | None:
    f = _number(value)
    return int(f) if f is not None and 0 <= f < 1 << 31 else None


def _tags(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in list(raw.items())[:256]:
        name = str(key).strip().lower()
        if name in TAGS and name not in out:
            text = _text(value, TAG_TEXT)
            if text:
                out[name] = text
    return out


def _stream(raw: dict) -> dict[str, Any] | None:
    kind = raw.get("codec_type")
    if kind not in TYPES:
        return None
    out: dict[str, Any] = {}
    for key in STREAM_TEXT:
        if (text := _text(raw.get(key))) is not None:
            out[key] = text
    for key in STREAM_INTS:
        if (number := _int(raw.get(key))) is not None:
            out[key] = number
    if "index" not in out:
        return None
    disposition = raw.get("disposition")
    if isinstance(disposition, dict):
        out["disposition"] = {"attached_pic": 1 if disposition.get("attached_pic") else 0}
    tags = _tags(raw.get("tags"))
    if tags:
        out["tags"] = tags
    sides = raw.get("side_data_list")
    if isinstance(sides, list):
        rotations = [
            {"rotation": r}
            for side in sides[:16]
            if isinstance(side, dict) and (r := _number(side.get("rotation"))) is not None
        ]
        if rotations:
            out["side_data_list"] = rotations[:1]
    return out


def clean(raw: Any, demuxer: str, container: str) -> dict[str, Any]:
    """De JSON van ffprobe naar het antwoord van meta: alleen bekende velden, begrensd. Een
    onverwachte vorm geeft een leeg deel, nooit een fout (fuzztest, regel 10)."""
    raw = raw if isinstance(raw, dict) else {}
    fmt_raw = raw.get("format") if isinstance(raw.get("format"), dict) else {}
    fmt: dict[str, Any] = {}
    for key in FORMAT_TEXT:
        if (text := _text(fmt_raw.get(key))) is not None:
            fmt[key] = text
    for key in FORMAT_NUMBERS:
        if (number := _number(fmt_raw.get(key))) is not None:
            fmt[key] = number
    tags = _tags(fmt_raw.get("tags"))
    if tags:
        fmt["tags"] = tags
    streams_raw = raw.get("streams") if isinstance(raw.get("streams"), list) else []
    streams = []
    for item in streams_raw[: MAX_STREAMS * 2]:
        if isinstance(item, dict) and (stream := _stream(item)) is not None:
            streams.append(stream)
        if len(streams) >= MAX_STREAMS:
            break
    return {"demuxer": demuxer, "container": container, "format": fmt, "streams": streams}


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, MetaArgs)
    fd = ctx.inputs[0]
    demuxer, container = choose(fd, ctx.args.demuxer)
    raw = await probe._json(ctx, fd, argv(demuxer), required=True)
    return clean(raw, demuxer, container)
