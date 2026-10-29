"""Een eigen lint voor onze HLS-playlists (§3.6 van het bouwontwerp van fase 5).

Naar RFC 8216 en de regels van Apple (HLS Authoring Specification) en LG (webOS) [doc], maar
alleen voor wat de app zelf schrijft (§2 M4, `hls.py`):

- `master.m3u8` met precies één variant: alleen de trede van de sessie, zodat een native speler
  nooit zelf een trede kiest waar geen budget voor is;
- `v.m3u8` als VOD met fMP4: één `EXT-X-MAP` met de init, dan `<n>.m4s` op volgorde.

Wat wij nooit schrijven (sleutels, byteranges, discontinuïteiten, alternatieve rendities,
I-frame-playlists, LL-HLS), is hier een fout. Zo vangt de lint ook een tag die er per ongeluk
in komt.

De regels van §3.6:

- `#EXTM3U` eerst, LF, UTF-8 zonder BOM (en geen andere stuurtekens, RFC 8216 §4.1);
- `EXT-X-VERSION` 7 (fMP4 met `EXT-X-MAP`);
- elke `EXTINF`, afgerond, ≤ `TARGETDURATION` (RFC 8216 §4.3.3.1); `EXT-X-ENDLIST` bij VOD;
- `CODECS` in elke `EXT-X-STREAM-INF`, met video én audio, of alleen video als de probe geen
  audio heeft; `BANDWIDTH` ≥ de piek (RFC 8216 §4.3.4.2);
- geen `EXT-X-INDEPENDENT-SEGMENTS` met het basisprofiel `webos-5`, en nooit bij remux;
- alleen relatieve URI's; geen query behalve `g=`.

En wat §2 M4 en Keuze 22–23 er voor onze playlists bij zeggen:

- `EXTINF` met drie decimalen; bij transcode `TARGETDURATION` 2, bij remux het langste segment
  naar boven afgerond;
- bij transcode (na de spike) mogen alleen het eerste en het laatste segment langer zijn dan 2 s.
  Het eerste loopt van 0 tot `origin + 2`, met `origin` waar de video begint. Het laatste loopt
  tot het eind van de producent, en dat kan na het laatste videobeeld nog audio zijn (review C2:
  origin 21 ms, laatste beeld 9,981 s, duur 10,6 s geeft 2,579 s). `TARGETDURATION` is
  max(2, die twee afgerond); elk segment ertussen blijft afgerond ≤ 2 (`extinf_grid`);
- bij transcode `EXT-X-INDEPENDENT-SEGMENTS` in de master (behalve voor `webos-5`): elk segment
  begint met een IDR;
- `RESOLUTION`, `FRAME-RATE` (drie decimalen) en `VIDEO-RANGE` (`SDR`, `PQ` of `HLG`) in de
  variant; `hvc1` en nooit `hev1` (Apple).

Gebruik (M4, `test_media_hls.py`):

    problems = lint(text, Context(mode="transcode", base="web", has_audio=True))
    assert_ok(text, ctx)          # AssertionError met alle problemen erin
    assert_set_ok({"master.m3u8": m, "v.m3u8": v}, ctx, sizes={"0.m4s": 812_345, ...})

Elke fout heeft een vaste code (`Problem.code`); de tests kijken naar de code, niet naar de tekst.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

VERSION = 7
WEBOS_5 = "webos-5"

# Alle tags van RFC 8216 (en 8216bis) die wij nooit schrijven
UNUSED_TAGS = frozenset(
    {
        "EXT-X-BYTERANGE",
        "EXT-X-DISCONTINUITY",
        "EXT-X-DISCONTINUITY-SEQUENCE",
        "EXT-X-KEY",
        "EXT-X-PROGRAM-DATE-TIME",
        "EXT-X-DATERANGE",
        "EXT-X-GAP",
        "EXT-X-BITRATE",
        "EXT-X-I-FRAMES-ONLY",
        "EXT-X-MEDIA",
        "EXT-X-I-FRAME-STREAM-INF",
        "EXT-X-SESSION-DATA",
        "EXT-X-SESSION-KEY",
        "EXT-X-START",
        "EXT-X-DEFINE",
        "EXT-X-CONTENT-STEERING",
        "EXT-X-PART",
        "EXT-X-PART-INF",
        "EXT-X-SERVER-CONTROL",
        "EXT-X-PRELOAD-HINT",
        "EXT-X-RENDITION-REPORT",
        "EXT-X-SKIP",
    }
)
MEDIA_TAGS = frozenset(
    {
        "EXT-X-TARGETDURATION",
        "EXT-X-MEDIA-SEQUENCE",
        "EXT-X-PLAYLIST-TYPE",
        "EXT-X-ENDLIST",
        "EXT-X-MAP",
        "EXTINF",
    }
)
MASTER_TAGS = frozenset({"EXT-X-STREAM-INF"})
BOTH_TAGS = frozenset({"EXTM3U", "EXT-X-VERSION", "EXT-X-INDEPENDENT-SEGMENTS"})

# De attributen van EXT-X-STREAM-INF: wat wij schrijven, wat mag (alleen NONE), en de rest
STREAM_INF_OURS = frozenset(
    {"BANDWIDTH", "AVERAGE-BANDWIDTH", "CODECS", "RESOLUTION", "FRAME-RATE", "VIDEO-RANGE"}
)
STREAM_INF_NONE_ONLY = frozenset({"CLOSED-CAPTIONS", "HDCP-LEVEL"})
STREAM_INF_UNUSED = frozenset(
    {
        "AUDIO",
        "VIDEO",
        "SUBTITLES",
        "SCORE",
        "SUPPLEMENTAL-CODECS",
        "ALLOWED-CPC",
        "STABLE-VARIANT-ID",
        "PATHWAY-ID",
        "REQ-VIDEO-LAYOUT",
        "PROGRAM-ID",
    }
)
VIDEO_RANGES = frozenset({"SDR", "PQ", "HLG"})

# Codec-strings (RFC 6381, Keuze 16). hev1 en dvhe: Apple wil hvc1 en dvh1 [doc]
VIDEO_CODEC = re.compile(
    r"^(avc1\.[0-9A-Fa-f]{6}"
    r"|hvc1\.[A-C]?\d{1,2}\.[0-9A-Fa-f]{1,8}\.[LH]\d{1,3}(\.[0-9A-Fa-f]{1,2}){0,6}"
    r"|vp09\.\d{2}\.\d{2}\.\d{2}(\.\d{2}){0,5}"
    r"|av01\.\d\.\d{2}[MH]\.\d{2}(\.\d(\.\d{3}){0,1}(\.\d{2}){0,3}(\.\d)?)?"
    r"|dvh1\.\d{2}\.\d{2})$"
)
AUDIO_CODEC = re.compile(r"^(mp4a\.40\.(2|5|29)|opus|flac|ac-3|ec-3)$")
APPLE_WANTS = {"hev1": "hvc1", "dvhe": "dvh1"}

ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"\r\n]*"|[^",]*)(,|$)')
INTEGER = re.compile(r"^\d{1,20}$")
DECIMAL_3 = re.compile(r"^\d+\.\d{3}$")
FLOAT = re.compile(r"^\d+(\.\d+)?$")
RESOLUTION = re.compile(r"^[1-9]\d{0,4}x[1-9]\d{0,4}$")
SEGMENT_URI = re.compile(r"^(\d{1,9})\.m4s$")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
QUERY_G = re.compile(r"^g=\d{1,9}$")


@dataclass(frozen=True)
class Context:
    """Wat de lint over de sessie moet weten.

    - `mode`: `transcode` (rooster van 2 s) of `remux` (segmenten op de keyframes);
    - `base`: het basisprofiel van de client (Keuze 20), zoals `web` of `webos-5`;
    - `has_audio`: of de probe een audiospoor heeft (Keuze 19); bepaalt wat `CODECS` moet hebben;
    - `peak_bps`: de piek die de test zelf mat (bits per seconde), of None.
    """

    mode: Literal["transcode", "remux"] = "transcode"
    base: str = "web"
    has_audio: bool = True
    peak_bps: int | None = None


@dataclass(frozen=True)
class Problem:
    code: str
    line: int  # 1 is de eerste regel; 0 gaat over de hele playlist
    message: str

    def __str__(self) -> str:
        where = f"regel {self.line}" if self.line else "playlist"
        return f"{where}: {self.message} [{self.code}]"


@dataclass
class _Line:
    number: int
    text: str
    kind: Literal["tag", "uri", "comment", "blank"]
    name: str = ""
    value: str | None = None


@dataclass
class Segment:
    """Een segment uit een mediaplaylist: duur uit `EXTINF` en de URI."""

    duration: float
    uri: str
    line: int


@dataclass
class Parsed:
    kind: Literal["master", "media", "unknown"]
    lines: list[_Line]
    target: int | None = None
    media_sequence: int = 0
    map_uri: str | None = None
    segments: list[Segment] | None = None
    variants: list[tuple[dict[str, str], str | None, int]] | None = None
    independent: bool = False


class _Collector:
    def __init__(self) -> None:
        self.problems: list[Problem] = []

    def add(self, code: str, line: int, message: str) -> None:
        self.problems.append(Problem(code, line, message))


def lint(playlist: str | bytes, ctx: Context | None = None) -> list[Problem]:
    """Alle problemen van één playlist (master of media), in de volgorde van het bestand."""
    out = _Collector()
    _lint(playlist, ctx or Context(), out)
    return out.problems


def assert_ok(playlist: str | bytes, ctx: Context | None = None) -> None:
    problems = lint(playlist, ctx)
    if problems:
        raise AssertionError(_report(problems))


def lint_set(
    playlists: Mapping[str, str | bytes],
    ctx: Context | None = None,
    *,
    sizes: Mapping[str, int] | None = None,
) -> list[Problem]:
    """Een master met zijn mediaplaylists, op hun relatieve namen (`master.m3u8`, `v.m3u8`).

    Naast de lint per playlist: de URI van elke variant moet in de set zitten, en met `sizes`
    (bytes per segment, op de URI zonder query) eist hij dat elk segment een grootte heeft en
    dat `BANDWIDTH` ten minste de piek van RFC 8216 §4.3.4.2 is."""
    ctx = ctx or Context()
    problems: list[Problem] = []
    parsed: dict[str, Parsed] = {}
    for name, text in playlists.items():
        out = _Collector()
        p = _lint(text, ctx, out)
        problems += [Problem(x.code, x.line, f"{name}: {x.message}") for x in out.problems]
        if p is not None:
            parsed[name] = p
    masters = [n for n, p in parsed.items() if p.kind == "master"]
    if len(masters) != 1:
        problems.append(Problem("set_master", 0, f"verwacht één master, gevonden {len(masters)}"))
    for name in masters:
        for attrs, uri, line in parsed[name].variants or []:
            if uri is None:
                continue
            target = uri.split("?", 1)[0]
            media = parsed.get(target)
            if media is None or media.kind != "media":
                problems.append(
                    Problem("variant_target", line, f"{name}: {uri} zit niet in de set")
                )
                continue
            if sizes is None or media.target is None or not media.segments:
                continue
            missing = [s for s in media.segments if s.uri not in sizes]
            for s in missing:
                problems.append(
                    Problem("segment_size", s.line, f"{target}: geen grootte voor {s.uri}")
                )
            if missing:
                continue
            peak = peak_bps([(s.duration, sizes[s.uri]) for s in media.segments], media.target)
            bandwidth = _int(attrs.get("BANDWIDTH"))
            if bandwidth is not None and bandwidth < peak:
                problems.append(
                    Problem(
                        "bandwidth_peak",
                        line,
                        f"{name}: BANDWIDTH {bandwidth} is kleiner dan de piek {peak} van {target}",
                    )
                )
    return problems


def assert_set_ok(
    playlists: Mapping[str, str | bytes],
    ctx: Context | None = None,
    *,
    sizes: Mapping[str, int] | None = None,
) -> None:
    problems = lint_set(playlists, ctx, sizes=sizes)
    if problems:
        raise AssertionError(_report(problems))


def peak_bps(segments: list[tuple[float, int]], target: int) -> int:
    """De piek van RFC 8216 §4.3.4.2: de hoogste bitrate van een aaneengesloten reeks segmenten
    met samen een duur tussen 0,5 en 1,5 × `TARGETDURATION`. `segments` is [(duur in s, bytes)].
    Naar boven afgerond op een hele bit per seconde."""
    best = 0.0
    low, high = 0.5 * target, 1.5 * target
    for i in range(len(segments)):
        total_s = 0.0
        total_b = 0
        for duration, size in segments[i:]:
            total_s += duration
            total_b += size
            if total_s > high + 1e-9:
                break
            if total_s >= low - 1e-9 and total_s > 0:
                best = max(best, 8 * total_b / total_s)
    return math.ceil(best)


def parse(playlist: str | bytes) -> Parsed | None:
    """De playlist als structuur (segmenten, varianten), of None als hij niet te lezen is.
    Voor tests die de inhoud willen vergelijken met `map.json` (M4)."""
    return _lint(playlist, Context(), _Collector())


# --- de lint zelf ---


def _report(problems: list[Problem]) -> str:
    return "De playlist voldoet niet:\n" + "\n".join(f"  {p}" for p in problems)


def _round(value: float) -> int:
    """Afronden naar het dichtstbijzijnde gehele getal, .5 naar boven (RFC 8216 §4.3.3.1)."""
    return math.floor(value + 0.5)


def _int(value: str | None) -> int | None:
    if value is None or not INTEGER.match(value):
        return None
    return int(value)


def _decode(playlist: str | bytes, out: _Collector) -> str | None:
    if isinstance(playlist, bytes):
        if playlist.startswith(b"\xef\xbb\xbf"):
            out.add("bom", 1, "UTF-8 met BOM; zonder BOM")
            playlist = playlist[3:]
        try:
            return playlist.decode("utf-8")
        except UnicodeDecodeError as e:
            out.add("not_utf8", 0, f"geen geldige UTF-8 (byte {e.start})")
            return None
    if playlist.startswith("\ufeff"):
        out.add("bom", 1, "UTF-8 met BOM; zonder BOM")
        playlist = playlist[1:]
    return playlist


def _split(text: str, out: _Collector) -> list[_Line]:
    if "\r" in text:
        line = text[: text.index("\r")].count("\n") + 1
        out.add("crlf", line, "regeleinde met CR; alleen LF")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        out.add("no_final_lf", text.count("\n") + 1, "de laatste regel eindigt niet op LF")
    raw = text.split("\n")
    if raw and raw[-1] == "":
        raw.pop()
    lines: list[_Line] = []
    for i, text_line in enumerate(raw, 1):
        bad = next((c for c in text_line if ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F), None)
        if bad is not None:
            out.add("control_char", i, f"stuurteken U+{ord(bad):04X}")
        if text_line == "":
            lines.append(_Line(i, text_line, "blank"))
        elif text_line.strip() == "":
            out.add("whitespace", i, "een regel met alleen witruimte")
            lines.append(_Line(i, text_line, "blank"))
        elif text_line.startswith("#EXT"):
            name, sep, value = text_line[1:].partition(":")
            lines.append(_Line(i, text_line, "tag", name, value if sep else None))
        elif text_line.startswith("#"):
            lines.append(_Line(i, text_line, "comment"))
        else:
            lines.append(_Line(i, text_line, "uri"))
    return lines


def _attributes(line: _Line, out: _Collector) -> dict[str, str] | None:
    """Een attribuutlijst (RFC 8216 §4.2): NAAM=waarde, gescheiden door komma's."""
    value = line.value or ""
    attrs: dict[str, str] = {}
    pos = 0
    if not value:
        out.add("attr_syntax", line.number, f"{line.name} zonder attributen")
        return None
    while pos < len(value):
        m = ATTRIBUTE.match(value, pos)
        if m is None or m.group(2) == "":
            out.add("attr_syntax", line.number, f"{line.name}: geen attribuutlijst bij '{value}'")
            return None
        name, val = m.group(1), m.group(2)
        if name in attrs:
            out.add("attr_duplicate", line.number, f"{line.name}: {name} staat er twee keer")
        attrs[name] = val
        pos = m.end()
        if m.group(3) == "," and pos == len(value):
            out.add("attr_syntax", line.number, f"{line.name}: een komma aan het eind")
    return attrs


def _quoted(value: str | None) -> str | None:
    if value is None or len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        return None
    return value[1:-1]


def _check_uri(uri: str, line: int, out: _Collector, *, what: str) -> None:
    if any(c.isspace() for c in uri):
        out.add("whitespace", line, f"{what} met witruimte: '{uri}'")
    if SCHEME.match(uri) or uri.startswith("/") or "\\" in uri:
        out.add("uri_absolute", line, f"{what} is niet relatief: '{uri}'")
        return
    path, sep, query = uri.partition("?")
    if "#" in uri or any(part in ("..", ".") for part in path.split("/")):
        out.add("uri_chars", line, f"{what} met '#', '.' of '..': '{uri}'")
    if sep and not QUERY_G.match(query):
        out.add("uri_query", line, f"{what} met een andere query dan g=<getal>: '{uri}'")


def _whitespace_in_tag(line: _Line, out: _Collector) -> None:
    if line.name == "EXTINF":
        # de titel na de komma mag witruimte hebben
        head = (line.value or "").split(",", 1)[0]
        text = f"#EXTINF:{head}"
    else:
        text = re.sub(r'"[^"]*"', '""', line.text)
    if any(c.isspace() for c in text):
        out.add("whitespace", line.number, f"witruimte in #{line.name}")


def _lint(playlist: str | bytes, ctx: Context, out: _Collector) -> Parsed | None:
    text = _decode(playlist, out)
    if text is None:
        return None
    lines = _split(text, out)
    if not lines or lines[0].text != "#EXTM3U":
        out.add("first_line", 1, "de eerste regel is niet #EXTM3U")
    tags = [x for x in lines if x.kind == "tag"]
    names = {x.name for x in tags}
    for x in tags:
        _whitespace_in_tag(x, out)
        if x.name in UNUSED_TAGS:
            out.add("tag_unused", x.number, f"#{x.name} komt in onze playlists niet voor")
        elif x.name not in MEDIA_TAGS | MASTER_TAGS | BOTH_TAGS:
            out.add("tag_unknown", x.number, f"onbekende tag #{x.name}")
    for x in lines:
        if x.kind == "tag" and x.name == "EXTM3U" and x.number != 1:
            out.add("first_line", x.number, "#EXTM3U staat niet alleen op de eerste regel")

    versions = [x for x in tags if x.name == "EXT-X-VERSION"]
    if not versions:
        out.add("version", 0, f"geen EXT-X-VERSION (verwacht {VERSION})")
    for x in versions[1:]:
        out.add("version_count", x.number, "EXT-X-VERSION staat er meer dan één keer")
    for x in versions[:1]:
        if x.value != str(VERSION):
            out.add("version", x.number, f"EXT-X-VERSION is {x.value}, verwacht {VERSION}")

    is_master = bool(names & MASTER_TAGS)
    is_media = bool(names & MEDIA_TAGS)
    if is_master and is_media:
        mixed = next(x for x in tags if x.name in (MEDIA_TAGS if is_master else MASTER_TAGS))
        out.add("mixed", mixed.number, "tags van een master en van een mediaplaylist samen")
    independent = [x for x in tags if x.name == "EXT-X-INDEPENDENT-SEGMENTS"]
    for x in independent:
        if x.value is not None:
            out.add("tag_syntax", x.number, "EXT-X-INDEPENDENT-SEGMENTS heeft geen waarde")
        if ctx.base == WEBOS_5:
            out.add(
                "independent_webos",
                x.number,
                "EXT-X-INDEPENDENT-SEGMENTS mag niet met het basisprofiel webos-5",
            )
        if ctx.mode == "remux":
            out.add(
                "independent_remux",
                x.number,
                "EXT-X-INDEPENDENT-SEGMENTS nooit bij remux (de bron kan open GOP's hebben)",
            )
    for x in independent[1:]:
        out.add("tag_count", x.number, "EXT-X-INDEPENDENT-SEGMENTS staat er meer dan één keer")

    if is_master and not is_media:
        parsed = Parsed("master", lines, independent=bool(independent))
        _master(parsed, ctx, out)
        return parsed
    if is_media:
        parsed = Parsed("media", lines, independent=bool(independent))
        _media(parsed, ctx, out)
        return parsed
    out.add("kind_unknown", 0, "geen master (EXT-X-STREAM-INF) en geen mediaplaylist (EXTINF)")
    return Parsed("unknown", lines)


def _master(p: Parsed, ctx: Context, out: _Collector) -> None:
    variants: list[tuple[dict[str, str], str | None, int]] = []
    lines = p.lines
    for i, x in enumerate(lines):
        if x.kind == "uri":
            previous = next(
                (y for y in reversed(lines[:i]) if y.kind not in ("blank", "comment")), None
            )
            if previous is None or previous.name != "EXT-X-STREAM-INF":
                out.add("uri_orphan", x.number, f"URI zonder EXT-X-STREAM-INF ervoor: '{x.text}'")
            continue
        if x.kind != "tag" or x.name != "EXT-X-STREAM-INF":
            continue
        attrs = _attributes(x, out) or {}
        following = next((y for y in lines[i + 1 :] if y.kind not in ("blank", "comment")), None)
        uri = following.text if following is not None and following.kind == "uri" else None
        if uri is None:
            out.add("variant_uri_missing", x.number, "EXT-X-STREAM-INF zonder URI erna")
        else:
            _check_uri(uri, following.number, out, what="de URI van de variant")
        variants.append((attrs, uri, x.number))
        _stream_inf(attrs, x.number, ctx, out)
    p.variants = variants
    if len(variants) != 1:
        out.add(
            "variant_count",
            0,
            f"{len(variants)} varianten; alleen de trede van de sessie hoort erin (één)",
        )
    if ctx.mode == "transcode" and ctx.base != WEBOS_5 and not p.independent:
        out.add(
            "independent_missing",
            0,
            "bij transcode hoort EXT-X-INDEPENDENT-SEGMENTS erin (elk segment begint met een IDR)",
        )


def _stream_inf(attrs: dict[str, str], line: int, ctx: Context, out: _Collector) -> None:
    for name in attrs:
        if name in STREAM_INF_OURS:
            continue
        if name in STREAM_INF_NONE_ONLY:
            if attrs[name] != "NONE":
                out.add("attr_value", line, f"{name} mag alleen NONE zijn")
        elif name in STREAM_INF_UNUSED:
            out.add("attr_unused", line, f"{name} komt in onze playlists niet voor")
        else:
            out.add("attr_unknown", line, f"onbekend attribuut {name}")

    bandwidth = _int(attrs.get("BANDWIDTH"))
    if "BANDWIDTH" not in attrs:
        out.add("bandwidth", line, "geen BANDWIDTH")
    elif bandwidth is None or bandwidth == 0:
        out.add("bandwidth", line, f"BANDWIDTH is geen positief geheel getal: {attrs['BANDWIDTH']}")
    if "AVERAGE-BANDWIDTH" in attrs:
        average = _int(attrs["AVERAGE-BANDWIDTH"])
        if average is None or average == 0:
            out.add("average_bandwidth", line, "AVERAGE-BANDWIDTH is geen positief geheel getal")
        elif bandwidth is not None and average > bandwidth:
            out.add("average_bandwidth", line, "AVERAGE-BANDWIDTH is groter dan BANDWIDTH")
    if bandwidth is not None and ctx.peak_bps is not None and bandwidth < ctx.peak_bps:
        out.add(
            "bandwidth_peak", line, f"BANDWIDTH {bandwidth} is kleiner dan de piek {ctx.peak_bps}"
        )

    codecs = _quoted(attrs.get("CODECS"))
    if "CODECS" not in attrs:
        out.add("codecs_missing", line, "geen CODECS (Apple eist ze in elke variant)")
    elif codecs is None:
        out.add("codecs_value", line, "CODECS is geen quoted-string")
    else:
        _codecs(codecs, line, ctx, out)

    if "RESOLUTION" not in attrs:
        out.add("resolution", line, "geen RESOLUTION")
    elif not RESOLUTION.match(attrs["RESOLUTION"]):
        out.add("resolution", line, f"RESOLUTION is geen BxH: {attrs['RESOLUTION']}")
    rate = attrs.get("FRAME-RATE")
    if rate is None:
        out.add("frame_rate", line, "geen FRAME-RATE")
    elif not DECIMAL_3.match(rate) or float(rate) <= 0:
        out.add("frame_rate", line, f"FRAME-RATE niet met drie decimalen: {rate}")
    video_range = attrs.get("VIDEO-RANGE")
    if video_range is None:
        out.add("video_range", line, "geen VIDEO-RANGE")
    elif video_range not in VIDEO_RANGES:
        out.add("video_range", line, f"VIDEO-RANGE is {video_range}, niet SDR, PQ of HLG")


def _codecs(codecs: str, line: int, ctx: Context, out: _Collector) -> None:
    video = audio = 0
    for codec in codecs.split(","):
        if codec != codec.strip() or not codec:
            out.add("codecs_value", line, f"lege codec of witruimte in CODECS: '{codecs}'")
            continue
        prefix = codec.split(".", 1)[0]
        if prefix in APPLE_WANTS:
            out.add(
                "codecs_value", line, f"{codec}: Apple wil {APPLE_WANTS[prefix]}, nooit {prefix}"
            )
            video += 1
        elif VIDEO_CODEC.match(codec):
            video += 1
        elif AUDIO_CODEC.match(codec):
            audio += 1
        else:
            out.add("codecs_value", line, f"onbekende codec-string: {codec}")
    if video != 1:
        out.add("codecs_video", line, f"CODECS heeft {video} videocodecs, verwacht één")
    if ctx.has_audio and audio != 1:
        out.add("codecs_audio", line, f"CODECS heeft {audio} audiocodecs, verwacht één")
    if not ctx.has_audio and audio:
        out.add("codecs_no_audio", line, "CODECS noemt audio, maar de bron heeft geen audio")


def _media(p: Parsed, ctx: Context, out: _Collector) -> None:
    lines = p.lines
    tags = [x for x in lines if x.kind == "tag"]

    def once(name: str) -> _Line | None:
        found = [x for x in tags if x.name == name]
        for x in found[1:]:
            out.add("tag_count", x.number, f"#{name} staat er meer dan één keer")
        return found[0] if found else None

    target_line = once("EXT-X-TARGETDURATION")
    if target_line is None:
        out.add("target_missing", 0, "geen EXT-X-TARGETDURATION")
    else:
        p.target = _int(target_line.value)
        if p.target is None or p.target == 0:
            out.add("target_value", target_line.number, "TARGETDURATION is geen positief getal")
            p.target = None
    type_line = once("EXT-X-PLAYLIST-TYPE")
    if type_line is None:
        out.add("playlist_type", 0, "geen EXT-X-PLAYLIST-TYPE (wij schrijven VOD)")
    elif type_line.value != "VOD":
        out.add("playlist_type", type_line.number, f"PLAYLIST-TYPE is {type_line.value}, niet VOD")
    sequence_line = once("EXT-X-MEDIA-SEQUENCE")
    if sequence_line is not None:
        sequence = _int(sequence_line.value)
        if sequence is None:
            out.add("media_sequence", sequence_line.number, "MEDIA-SEQUENCE is geen getal")
        else:
            p.media_sequence = sequence
    endlist = [x for x in tags if x.name == "EXT-X-ENDLIST"]
    if not endlist:
        out.add("endlist_missing", 0, "geen EXT-X-ENDLIST (VOD)")
    for x in endlist:
        if x.value is not None:
            out.add("tag_syntax", x.number, "EXT-X-ENDLIST heeft geen waarde")
    for x in endlist[1:]:
        out.add("tag_count", x.number, "EXT-X-ENDLIST staat er meer dan één keer")

    maps = [x for x in tags if x.name == "EXT-X-MAP"]
    if not maps:
        out.add("map_missing", 0, "geen EXT-X-MAP (fMP4 heeft een init)")
    for x in maps[1:]:
        out.add("map_count", x.number, "meer dan één EXT-X-MAP (een nieuwe init is een 410)")
    for x in maps[:1]:
        attrs = _attributes(x, out) or {}
        for name in attrs:
            if name == "BYTERANGE":
                out.add("attr_unused", x.number, "BYTERANGE komt in onze playlists niet voor")
            elif name != "URI":
                out.add("attr_unknown", x.number, f"onbekend attribuut {name} in EXT-X-MAP")
        uri = _quoted(attrs.get("URI"))
        if uri is None:
            out.add("map_uri", x.number, "EXT-X-MAP zonder URI als quoted-string")
        else:
            p.map_uri = uri
            _check_uri(uri, x.number, out, what="de URI van de init")

    segments: list[Segment] = []
    pending: _Line | None = None
    for x in lines:
        if x.kind == "tag" and x.name in ("EXTINF", "EXT-X-ENDLIST"):
            if pending is not None:
                out.add("extinf_orphan", pending.number, "EXTINF zonder URI erna")
            pending = x if x.name == "EXTINF" else None
        elif x.kind == "uri":
            if pending is None:
                out.add("uri_without_extinf", x.number, f"URI zonder EXTINF: '{x.text}'")
                continue
            duration = _extinf(pending, p.target, out)
            _check_uri(x.text, x.number, out, what="de URI van het segment")
            segments.append(Segment(duration if duration is not None else 0.0, x.text, x.number))
            pending = None
    if pending is not None:
        out.add("extinf_orphan", pending.number, "EXTINF zonder URI erna")
    p.segments = segments

    first = next((x.number for x in tags if x.name == "EXTINF"), None)
    if first is not None:
        # RFC 8216 §4.3.3.2 en §4.3.2.5: de sequence en de init gelden pas vanaf waar ze staan
        for x in tags:
            if x.number > first and x.name in (
                "EXT-X-MAP",
                "EXT-X-TARGETDURATION",
                "EXT-X-MEDIA-SEQUENCE",
            ):
                out.add("tag_order", x.number, f"#{x.name} na het eerste segment")
    if endlist:
        end = endlist[0].number
        for x in lines:
            if x.number > end and x.kind in ("tag", "uri"):
                what = f"#{x.name}" if x.kind == "tag" else f"de URI '{x.text}'"
                out.add("segment_after_end", x.number, f"{what} na EXT-X-ENDLIST")
    if not segments:
        out.add("no_segments", 0, "geen enkel segment")
        return

    seen: set[str] = set()
    for index, s in enumerate(segments):
        if s.uri in seen:
            out.add("segment_duplicate", s.line, f"{s.uri} staat er twee keer")
        seen.add(s.uri)
        m = SEGMENT_URI.match(s.uri)
        expected = p.media_sequence + index
        if m is None or int(m.group(1)) != expected:
            out.add("segment_name", s.line, f"verwacht {expected}.m4s, gevonden '{s.uri}'")

    if p.target is not None:
        longest = max(s.duration for s in segments)
        # Het eerste segment loopt van 0 tot origin + 2 s (origin: waar de video begint, in
        # mediatijd; Keuze 22 na de spike), het laatste tot het eind van de producent (audio kan
        # langer zijn dan de video; review C2). Alleen die twee mogen langer zijn dan 2 s.
        wanted = max(2, _round(segments[0].duration), _round(segments[-1].duration))
        if ctx.mode == "transcode" and p.target != wanted:
            out.add(
                "target_transcode",
                target_line.number if target_line else 0,
                f"TARGETDURATION is {p.target}; bij transcode {wanted} (2, of het eerste of "
                "laatste segment afgerond als dat langer is; Keuze 22)",
            )
        if ctx.mode == "transcode":
            for s in segments[1:-1]:
                if _round(s.duration) > 2:
                    out.add(
                        "extinf_grid",
                        s.line,
                        f"EXTINF {s.duration:.3f} tussen het eerste en het laatste segment; bij "
                        "transcode hooguit 2 s afgerond (het rooster, Keuze 22)",
                    )
        if ctx.mode == "remux" and p.target != math.ceil(longest - 1e-9):
            out.add(
                "target_remux",
                target_line.number if target_line else 0,
                f"TARGETDURATION is {p.target}; bij remux het langste segment naar boven "
                f"afgerond ({math.ceil(longest - 1e-9)}, Keuze 23)",
            )


def _extinf(x: _Line, target: int | None, out: _Collector) -> float | None:
    value = x.value
    if value is None or "," not in value:
        out.add("extinf_syntax", x.number, "EXTINF zonder komma: #EXTINF:<duur>,[titel]")
        return None
    duration_text = value.split(",", 1)[0]
    if not FLOAT.match(duration_text):
        out.add("extinf_syntax", x.number, f"EXTINF heeft geen duur: '{duration_text}'")
        return None
    duration = float(duration_text)
    if not DECIMAL_3.match(duration_text):
        out.add("extinf_decimals", x.number, f"EXTINF {duration_text} niet met drie decimalen")
    if duration <= 0:
        out.add("extinf_value", x.number, "EXTINF is 0")
    if target is not None and _round(duration) > target:
        out.add(
            "extinf_too_long",
            x.number,
            f"EXTINF {duration_text} is afgerond {_round(duration)}, meer dan TARGETDURATION "
            f"{target}",
        )
    return duration
