"""De eigen omzetter van ondertitels naar WebVTT (bouwontwerp fase 5, Keuze 29). M5.

Pure functies op bytes van een gebruiker: de worker roept to_vtt aan (opdracht subs), voor een
los bestand (.srt, .vtt, .ass, .ssa) en voor elk ingebed spoor dat ffmpeg uitpakte. Dit bestand
importeert niets van de app, want de worker draait zonder de rest.

Waarom zelf (N18): de omzetter van ffmpeg gooit `\\an8` (boven in beeld) en kleur weg. Deze:
- ASS `{\\an1..9}` (en de oude `{\\a..}`, en de Alignment van de stijl) wordt `line:` en
  `align:` in VTT: boven `line:0`, midden `line:50%`, onder niets (de standaard); links
  `align:left`, rechts `align:right`. Dezelfde codes in een SRT (`{\\an8}`) ook;
- vet, cursief en onderstreept blijven als `<b>`, `<i>` en `<u>`;
- een eenvoudige kleur (ASS `\\c`, de PrimaryColour van de stijl, SRT `<font color>`, VTT
  `<c.klasse>`) wordt `<c.klasse>` met de dichtstbijzijnde van COLOURS (de standaardklassen van
  WebVTT); wit is de standaard en krijgt geen klasse;
- elke andere code gaat weg; een tekening (`{\\p1}` tot `{\\p0}`) wordt overgeslagen;
- tekenset: eerst een BOM (UTF-8 of UTF-16), dan geldig UTF-8, anders CP1252 (het oude
  Nederlands); stuurtekens gaan weg.

De uitvoer heeft altijd dezelfde vorm, die subtitles.check_vtt in de app streng nakijkt:

    WEBVTT
    <lege regel>
    00:00:01.000 --> 00:00:02.500 line:0
    tekst met alleen <b>, <i>, <u>, <c.klasse> en &amp; &lt; &gt;

Elk stuk tekst krijgt zijn eigen tags, dus ze zijn altijd goed genest. De cues staan op
begintijd, dubbele (ASS heeft ze vaak per laag) gaan weg, overlappende blijven allebei staan (de
browser zet ze onder elkaar). Een cue houdt hooguit MAX_CUE_CHARS tekens; meer dan MAX_CUES cues
of meer dan MAX_VTT_BYTES is SubtitleError (too_large). De tijden zijn die van de invoer: de app
trekt bij een ingebed spoor de start van de bron eraf (check_vtt).
"""

from __future__ import annotations

import functools
import html
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import NamedTuple

# De grenzen van Keuze 29 (de app controleert dezelfde in subtitles.check_vtt)
MAX_VTT_BYTES = 5 * 1024 * 1024
MAX_CUES = 20_000
MAX_CUE_CHARS = 2_000
# Een los bestand mag hooguit 5 MB zijn (Keuze 29; de worker en de app kijken dat na)
MAX_LOOSE = 5 * 1024 * 1024
# Hoeveel invoer de omzetter hooguit leest: een ASS-spoor uit een film met veel opmaak is groter
# dan zijn VTT. Gemeten: 3,2 MB ASS kost 0,5 s; de worker geeft tussendoor zijn lus de beurt
MAX_INPUT = 16 * 1024 * 1024
# Hoeveel cues de omzetter hooguit inleest (vóór het weghalen van dubbele en lege): zo blijft
# het geheugen begrensd
MAX_PARSED = 4 * MAX_CUES
# Hooguit 99:59:59.999: twee cijfers voor de uren, zoals check_vtt eist
MAX_MS = 100 * 3600 * 1000 - 1
# Hoeveel tekens van de ruwe tekst van één cue (met de codes) de omzetter hooguit leest. De
# uitvoer houdt er toch hooguit MAX_CUE_CHARS van; zo kost één cue hooguit een vaste tijd, ook
# als hij 16 MB groot is (review golf 2, S1)
MAX_RAW_CUE = 8 * MAX_CUE_CHARS
# Na zoveel tekens invoer geeft steps() een tik (None): de worker geeft dan zijn lus de beurt,
# ook in een stuk zonder cues
TICK_CHARS = 64 * 1024

KINDS = frozenset({"srt", "vtt", "webvtt", "ass", "ssa"})

# De klassen van <c.…>: de standaardkleuren van WebVTT, met hun RGB. De CSS van de speler (M6)
# geeft ze met ::cue(.yellow) hun kleur
COLOURS: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "lime": (0, 255, 0),
    "cyan": (0, 255, 255),
    "red": (255, 0, 0),
    "yellow": (255, 255, 0),
    "magenta": (255, 0, 255),
    "blue": (0, 0, 255),
    "black": (0, 0, 0),
}
CLASSES = frozenset(c for c in COLOURS if c != "white")
NAMED = {
    "white": "white",
    "lime": "lime",
    "green": "lime",
    "cyan": "cyan",
    "aqua": "cyan",
    "red": "red",
    "yellow": "yellow",
    "magenta": "magenta",
    "fuchsia": "magenta",
    "blue": "blue",
    "black": "black",
}

# ASS: de plek op het numpad naar VTT
_VERTICAL = {7: "line:0", 8: "line:0", 9: "line:0", 4: "line:50%", 5: "line:50%", 6: "line:50%"}
_HORIZONTAL = {1: "align:left", 4: "align:left", 7: "align:left"}
_HORIZONTAL |= {3: "align:right", 6: "align:right", 9: "align:right"}
# SSA (v4) kent de oude nummers: 1-3 onder, +4 boven, +8 midden
_LEGACY = {1: 1, 2: 2, 3: 3, 5: 7, 6: 8, 7: 9, 9: 4, 10: 5, 11: 6}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Een entiteit met puntkomma (&amp; &#38; &#x26;); de oude vormen zonder (&copy) niet
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[a-zA-Z][a-zA-Z0-9]{1,31});")


class SubtitleError(ValueError):
    """De ondertitel past niet binnen de grenzen; code is een wire.Code (too_large)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class Style(NamedTuple):
    """De opmaak van een stuk tekst."""

    bold: bool = False
    italic: bool = False
    underline: bool = False
    colour: str | None = None  # een klasse uit CLASSES


Run = tuple[Style, str]


@functools.lru_cache(maxsize=1024)
def _with(style: Style, field: str, value: bool | str | None) -> Style:
    """style met één veld anders. Er zijn maar een paar honderd stijlen, en _replace kost per
    keer ± 3 µs: met veel tags was dat de helft van de tijd (S1)."""
    return style._replace(**{field: value})


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    runs: list[Run]
    settings: str = ""


# --- tekenset ------------------------------------------------------------------------------------


def decode(data: bytes) -> str:
    """Bytes naar tekst: een BOM wint, dan geldig UTF-8, anders CP1252. Regeleinden worden \\n,
    stuurtekens (behalve tab) gaan weg."""
    if data.startswith(b"\xef\xbb\xbf"):
        text = data[3:].decode("utf-8", errors="replace")
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    return _CONTROL.sub("", text)


def _clean(text: str) -> str:
    """Tekst uit een entiteit: \r wordt een nieuwe regel, stuurtekens gaan weg (zoals decode)."""
    return _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def unescape(text: str) -> str:
    """De entiteiten met puntkomma als tekst (&amp; wordt &); een onbekende blijft staan. Wat
    eruit komt, maakt escape() bij het schrijven weer veilig."""
    if "&" not in text:
        return text
    return _clean(_ENTITY.sub(lambda m: html.unescape(m.group(0)), text))


def clip(text: str) -> str:
    """Hooguit MAX_RAW_CUE tekens van de ruwe tekst van een cue. Een code die daardoor niet meer
    sluit ({… of <…), gaat ook weg."""
    if len(text) <= MAX_RAW_CUE:
        return text
    text = text[:MAX_RAW_CUE]
    for opening, closing in (("{", "}"), ("<", ">")):
        at = text.rfind(opening)
        if at != -1 and text.find(closing, at) == -1:
            text = text[:at]
    return text


# --- kleur ---------------------------------------------------------------------------------------


@functools.lru_cache(maxsize=4096)
def nearest(rgb: tuple[int, int, int]) -> str | None:
    """De dichtstbijzijnde klasse; None voor wit (de standaard)."""
    best = min(
        COLOURS, key=lambda name: sum((a - b) ** 2 for a, b in zip(COLOURS[name], rgb, strict=True))
    )
    return None if best == "white" else best


@functools.lru_cache(maxsize=4096)
def _ass_colour(value: str) -> str | None:
    """&HBBGGRR& of &HAABBGGRR (hex), of een getal (SSA): de klasse, of None (wit)."""
    value = value.strip().rstrip("&").strip()
    try:
        if value[:2].upper() == "&H":
            number = int(value[2:10] or "0", 16)
        elif value.lstrip("-").isdigit():
            number = int(value[:12])
        else:
            return None
    except ValueError:
        return None
    number &= 0xFFFFFF  # alfa weg
    return nearest((number & 0xFF, (number >> 8) & 0xFF, (number >> 16) & 0xFF))


def _html_colour(value: str) -> str | None:
    """<font color="#rrggbb"> of een naam: de klasse, of None."""
    value = value.strip().strip("\"'").strip().lower()
    if value in NAMED:
        found = NAMED[value]
        return None if found == "white" else found
    match = re.fullmatch(r"#?([0-9a-f]{6}|[0-9a-f]{3})", value)
    if match is None:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return nearest((int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)))


# --- de opmaak van een tekst ---------------------------------------------------------------------


class _Runs:
    """Verzamelt (stijl, tekst); aangrenzende stukken met dezelfde stijl worden één. De stukken
    gaan in een lijst en worden één keer samengevoegd (een + per stuk is kwadratisch, S1)."""

    def __init__(self) -> None:
        self._parts: list[tuple[Style, list[str]]] = []

    def add(self, style: Style, text: str) -> None:
        if not text:
            return
        if self._parts and self._parts[-1][0] == style:
            self._parts[-1][1].append(text)
        else:
            self._parts.append((style, [text]))

    @property
    def items(self) -> list[Run]:
        return [(style, "".join(parts)) for style, parts in self._parts]


def position(align: int | None) -> str:
    """De instellingen van VTT voor een plek op het numpad van ASS (1-9)."""
    if align is None:
        return ""
    return " ".join(p for p in (_VERTICAL.get(align, ""), _HORIZONTAL.get(align, "")) if p)


_OVERRIDE = re.compile(r"\{([^{}]*+)\}")
# In een SRT is alleen {\…} een code van ASS, en {Y:i} (een stijl van MicroDVD) gaat weg, zoals
# bij ffmpeg; elk ander {…} is gewoon tekst ("{Muziek}", review golf 2, C3)
_SRT_OVERRIDE = re.compile(r"\{(\\[^{}]*+|[CcFfoPSsYy]:[^{}]*+)\}")
_TAG = re.compile(r"\\(an|a|b|i|u|1c|c|p|r)([^\\]*)")
_TRANSFORM = re.compile(r"\\t\([^)]*\)?")


def _apply(
    block: str,
    style: Style,
    base: Style,
    drawing: bool,
    align: int | None,
    styles: dict[str, tuple[Style, int | None]] | None = None,
):
    """Eén {…}-blok van ASS. Geeft (stijl, tekening, plek). De eerste \\an of \\a wint; een
    animatie (\\t(…)) telt niet. \\r zet de stijl van de regel terug, \\rNaam de stijl Naam
    uit styles (als die er is)."""
    for name, value in _TAG.findall(_TRANSFORM.sub("", block)):
        value = value.strip()
        if name == "an":
            if align is None and value[:1].isdigit() and 1 <= int(value[:1]) <= 9:
                align = int(value[:1])
        elif name == "a":
            if align is None and value.isdigit() and int(value[:3]) in _LEGACY:
                align = _LEGACY[int(value[:3])]
        elif name == "b":
            if value.isdigit():
                weight = int(value[:4])
                style = _with(style, "bold", weight == 1 or weight >= 600)
        elif name == "i":
            if value in ("0", "1"):
                style = _with(style, "italic", value == "1")
        elif name == "u":
            if value in ("0", "1"):
                style = _with(style, "underline", value == "1")
        elif name in ("c", "1c"):
            if not value:
                style = _with(style, "colour", base.colour)
            elif value[:2].upper() in ("&H", "H"):
                hexed = value if value[:1] == "&" else f"&{value}"
                style = _with(style, "colour", _ass_colour(hexed))
        elif name == "p":
            if value.isdigit():
                drawing = int(value[:4]) > 0
        elif name == "r":
            named = (styles or {}).get(value.lstrip("*").lower()) if value else None
            style = named[0] if named is not None else base
    return style, drawing, align


def _ass_plain(text: str) -> str:
    """\\N is een nieuwe regel, \\n een zachte (een spatie), \\h een harde spatie."""
    return text.replace("\\N", "\n").replace("\\n", " ").replace("\\h", "\u00a0")


def _ass_text(
    text: str, base: Style, styles: dict[str, tuple[Style, int | None]] | None = None
) -> tuple[list[Run], int | None]:
    """De tekst van een Dialogue: stukken en de plek (of None)."""
    runs = _Runs()
    style, drawing, align = base, False, None
    pos = 0
    for match in _OVERRIDE.finditer(text):
        if not drawing:
            runs.add(style, _ass_plain(text[pos : match.start()]))
        style, drawing, align = _apply(match.group(1), style, base, drawing, align, styles)
        pos = match.end()
    if not drawing:
        runs.add(style, _ass_plain(text[pos:]))
    return runs.items, align


# --- tijden --------------------------------------------------------------------------------------


def _ms(hours: str | None, minutes: str, seconds: str, fraction: str | None) -> int:
    frac = (fraction + "00")[:3] if fraction else "000"
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(frac)


def stamp(ms: int) -> str:
    """00:01:02.003; de uren in twee cijfers (hooguit 99)."""
    ms = max(0, min(ms, MAX_MS))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


# --- SRT -----------------------------------------------------------------------------------------

_SRT_TIME = r"(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2})[,.:](\d{1,3})"
_SRT_TIMING = re.compile(rf"^\s*{_SRT_TIME}\s*-->\s*{_SRT_TIME}")
# Een tag begint direct na < (of </) met een letter: "a < b > c" is gewoon tekst. De delen
# zijn bezittelijk (++, {…}+) en zonder \s* achter de attributen: zo probeert de regex per <
# hooguit één keer, en niet 200 × 200 (review golf 2, S1)
_HTML_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]{0,15}+)((?:\s[^<>]{0,200}+)?)/?>")
_FONT_COLOUR = re.compile(r"""color\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)


def _srt_text(text: str) -> tuple[list[Run], int | None]:
    """SRT-tekst met <b>, <i>, <u>, <font color> en eventueel {\\an8}; andere tags gaan weg.
    Entiteiten (&amp;) worden tekst, en bij het schrijven weer veilig (C4)."""
    runs = _Runs()
    base = Style()
    style = base
    colours: list[str | None] = []
    align: int | None = None
    pos = 0
    tokens = sorted(
        [(m.start(), m.end(), True, m) for m in _HTML_TAG.finditer(text)]
        + [(m.start(), m.end(), False, m) for m in _SRT_OVERRIDE.finditer(text)],
        key=lambda t: t[0],
    )
    for start, end, is_html, match in tokens:
        if start < pos:
            continue  # binnen een vorige code
        runs.add(style, unescape(text[pos:start]))
        pos = end
        if not is_html:
            style, _, align = _apply(match.group(1), style, base, False, align)
            continue
        closing, name = match.group(1) == "/", match.group(2).lower()
        if name == "b":
            style = _with(style, "bold", not closing)
        elif name == "i":
            style = _with(style, "italic", not closing)
        elif name == "u":
            style = _with(style, "underline", not closing)
        elif name == "font":
            if closing:
                style = _with(style, "colour", colours.pop() if colours else base.colour)
            else:
                found = _FONT_COLOUR.search(match.group(3))
                colours.append(style.colour)
                if found is not None:
                    style = _with(style, "colour", _html_colour(found.group(1)))
    runs.add(style, unescape(text[pos:]))
    return runs.items, align


def parse_srt(text: str) -> Iterator[Cue | None]:
    """De cues van een SRT, met na elke TICK_CHARS tekens een tik (None)."""
    lines = text.split("\n")
    i = 0
    scanned = 0
    while i < len(lines):
        if scanned >= TICK_CHARS:
            scanned = 0
            yield None
        scanned += len(lines[i]) + 1
        match = _SRT_TIMING.match(lines[i])
        if match is None:
            i += 1
            continue
        g = match.groups()
        start, end = _ms(*g[0:4]), _ms(*g[4:8])
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].strip():
            if _SRT_TIMING.match(lines[i]):
                break
            # zonder lege regel ervoor: een nummer met een tijd eronder is de volgende cue
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if lines[i].strip().isdigit() and _SRT_TIMING.match(nxt):
                break
            body.append(lines[i])
            scanned += len(lines[i]) + 1
            i += 1
        runs, align = _srt_text(clip("\n".join(body)))
        yield Cue(start, end, runs, position(align))


# --- ASS en SSA ----------------------------------------------------------------------------------

_ASS_TIME = re.compile(r"^\s*(\d{1,3}):(\d{1,2}):(\d{1,2})[.:](\d{1,3})\s*$")
_DEFAULT_EVENTS = [
    "layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"
]  # fmt: skip


def _ass_ms(value: str) -> int | None:
    match = _ASS_TIME.match(value)
    if match is None:
        return None
    h, m, s, frac = match.groups()
    # ASS geeft honderdsten (0:00:01.50): twee cijfers zijn dus ×10
    millis = int(frac) * (10 ** (3 - len(frac)))
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + millis


def _flag(value: str | None) -> bool:
    """-1 (of 1) is aan in een stijl van ASS."""
    try:
        return int((value or "0").strip()[:4]) != 0
    except ValueError:
        return False


def _style(fields: dict[str, str], legacy: bool) -> tuple[Style, int | None]:
    align = None
    raw = (fields.get("alignment") or "").strip()
    if raw.isdigit():
        number = int(raw[:3])
        align = _LEGACY.get(number) if legacy else (number if 1 <= number <= 9 else None)
    style = Style(
        bold=_flag(fields.get("bold")),
        italic=_flag(fields.get("italic")),
        underline=_flag(fields.get("underline")),
        colour=_ass_colour(fields.get("primarycolour") or "&HFFFFFF"),
    )
    return style, align


def parse_ass(text: str) -> Iterator[Cue | None]:
    """De cues van een ASS of SSA, met na elke TICK_CHARS tekens een tik (None)."""
    section = ""
    legacy = False  # [V4 Styles]: de oude nummers van Alignment
    style_format: list[str] = []
    event_format = list(_DEFAULT_EVENTS)
    styles: dict[str, tuple[Style, int | None]] = {}
    scanned = 0
    for raw in text.split("\n"):
        if scanned >= TICK_CHARS:
            scanned = 0
            yield None
        scanned += len(raw) + 1
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            legacy = section == "v4 styles"
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        if section.endswith("styles"):
            if key == "format":
                style_format = [f.strip().lower() for f in value.split(",")]
            elif key == "style" and style_format:
                parts = value.split(",", len(style_format) - 1)
                fields = dict(zip(style_format, parts, strict=False))
                name = fields.get("name", "").strip().lstrip("*").lower()
                styles[name] = _style(fields, legacy)
        elif section == "events":
            if key == "format":
                event_format = [f.strip().lower() for f in value.split(",")]
            elif key == "dialogue" and "text" in event_format:
                parts = value.split(",", len(event_format) - 1)
                if len(parts) < len(event_format):
                    continue
                fields = dict(zip(event_format, parts, strict=True))
                start, end = _ass_ms(fields.get("start", "")), _ass_ms(fields.get("end", ""))
                if start is None or end is None:
                    continue
                name = (fields.get("style") or "").strip().lstrip("*").lower()
                base, style_align = styles.get(name) or styles.get("default") or (Style(), None)
                runs, align = _ass_text(clip(fields["text"]), base, styles)
                yield Cue(start, end, runs, position(align or style_align))


# --- WebVTT --------------------------------------------------------------------------------------

_VTT_TIME = r"(?:(\d{1,3}):)?(\d{2}):(\d{2})\.(\d{3})"
_VTT_TIMING = re.compile(rf"^\s*{_VTT_TIME}[ \t]+-->[ \t]+{_VTT_TIME}(.*)$")
_VTT_TAG = re.compile(r"<([^<>]{0,200}+)>")
_PERCENT = r"(?:100(?:\.0{1,3})?|\d{1,2}(?:\.\d{1,3})?)%"
_SETTING = {
    "line": re.compile(rf"^(-?\d{{1,3}}|{_PERCENT})(?:,(?:start|center|end))?$"),
    "position": re.compile(rf"^({_PERCENT})(?:,(?:line-left|center|line-right|auto))?$"),
    "size": re.compile(rf"^({_PERCENT})$"),
    "align": re.compile(r"^(start|center|end|left|right)$"),
}


def _vtt_settings(raw: str) -> str:
    """De instellingen die we kennen (line, position, size, align), zonder hun uitlijning."""
    out: list[str] = []
    seen: set[str] = set()
    for token in raw.split():
        name, sep, value = token.partition(":")
        pattern = _SETTING.get(name)
        if not sep or pattern is None or name in seen:
            continue
        match = pattern.match(value)
        if match is not None:
            seen.add(name)
            out.append(f"{name}:{match.group(1)}")
    return " ".join(out)


def _vtt_text(text: str) -> list[Run]:
    """<b>, <i>, <u> en <c.klasse> blijven (alleen klassen uit CLASSES); <v>, <lang>, <ruby>,
    tijden en de rest gaan weg. Entiteiten worden tekst (en bij het schrijven weer veilig)."""
    runs = _Runs()
    base = Style()
    style = base
    classes: list[str | None] = []
    pos = 0
    for match in _VTT_TAG.finditer(text):
        runs.add(style, _clean(html.unescape(text[pos : match.start()])))
        pos = match.end()
        tag = match.group(1).strip()
        closing = tag.startswith("/")
        words = tag.lstrip("/").split(None, 1)
        name = words[0] if words else ""
        head = name.split(".")[0].lower()
        if head == "b":
            style = _with(style, "bold", not closing)
        elif head == "i":
            style = _with(style, "italic", not closing)
        elif head == "u":
            style = _with(style, "underline", not closing)
        elif head == "c":
            if closing:
                style = _with(style, "colour", classes.pop() if classes else base.colour)
            else:
                classes.append(style.colour)
                wanted = [c for c in name.split(".")[1:] if c in CLASSES]
                if wanted:
                    style = _with(style, "colour", wanted[-1])
    runs.add(style, _clean(html.unescape(text[pos:])))
    return runs.items


def parse_vtt(text: str) -> Iterator[Cue | None]:
    """De cues van een WebVTT, met na elke TICK_CHARS tekens een tik (None)."""
    scanned = 0
    for block in re.split(r"\n[ \t]*\n", text):
        if scanned >= TICK_CHARS:
            scanned = 0
            yield None
        scanned += len(block) + 2
        lines = block.split("\n")
        at = next((n for n, line in enumerate(lines) if "-->" in line), None)
        if at is None:
            continue  # de kop, NOTE, STYLE, REGION
        first = lines[0].split()
        if at > 0 and first and first[0] in ("NOTE", "STYLE", "REGION"):
            continue
        match = _VTT_TIMING.match(lines[at])
        if match is None:
            continue
        g = match.groups()
        start, end = _ms(*g[0:4]), _ms(*g[4:8])
        runs = _vtt_text(clip("\n".join(lines[at + 1 :])))
        yield Cue(start, end, runs, _vtt_settings(g[8]))


# --- schrijven -----------------------------------------------------------------------------------


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@functools.lru_cache(maxsize=256)
def _tags(style: Style) -> tuple[str, str]:
    opening, closing = [], []
    if style.colour:
        opening.append(f"<c.{style.colour}>")
        closing.append("</c>")
    for flag, tag in ((style.bold, "b"), (style.italic, "i"), (style.underline, "u")):
        if flag:
            opening.append(f"<{tag}>")
            closing.append(f"</{tag}>")
    return "".join(opening), "".join(reversed(closing))


def _lines(runs: list[Run]) -> list[list[Run]]:
    """De stukken per regel; lege regels weg (een lege regel eindigt een cue) en geen spaties
    aan het begin of eind van een regel."""
    lines: list[list[Run]] = [[]]
    for style, text in runs:
        for n, part in enumerate(text.split("\n")):
            if n:
                lines.append([])
            if part:
                lines[-1].append((style, part))
    out = []
    for line in lines:
        # met indexen, niet met pop(0): dat is kwadratisch bij veel lege stukken (S1)
        first, last = 0, len(line)
        while first < last and not line[first][1].strip():
            first += 1
        while last > first and not line[last - 1][1].strip():
            last -= 1
        if first < last:
            kept = line[first:last]
            kept[0] = (kept[0][0], kept[0][1].lstrip())
            kept[-1] = (kept[-1][0], kept[-1][1].rstrip())
            out.append(kept)
    return out


def _cut(body: str, room: int) -> str:
    """Hooguit room tekens, zonder een entiteit (&amp;) door te knippen."""
    cut = body[:room]
    amp = cut.rfind("&")
    if amp != -1 and ";" not in cut[amp:]:
        cut = cut[:amp]
    return cut.rstrip()


def payload(runs: list[Run], limit: int = MAX_CUE_CHARS) -> str:
    """De tekst van een cue: elk stuk met zijn eigen tags, hooguit limit tekens."""
    out: list[str] = []
    size = 0
    for line in _lines(runs):
        parts: list[str] = []
        size += 1 if out else 0  # de \n
        full = False
        for style, text in line:
            opening, closing = _tags(style)
            body = escape(text)
            room = limit - size - len(opening) - len(closing)
            if len(body) > room:
                body = _cut(body, room) if room > 0 else ""
                full = True
            if body:
                parts.append(f"{opening}{body}{closing}")
                size += len(opening) + len(body) + len(closing)
            if full:
                break
        if parts:
            out.append("".join(parts))
        if full:
            break
    return "\n".join(out)


HEADER = "WEBVTT\n"


def blocks(cues: Iterable[Cue]) -> Iterator[str]:
    """De cues als blokken van WebVTT (na HEADER), in de vorm die check_vtt verwacht: op
    begintijd, zonder dubbele en lege. SubtitleError bij meer dan MAX_CUES of MAX_VTT_BYTES."""
    size = len(HEADER)
    seen: set[tuple[int, int, str, str]] = set()
    for cue in sorted(cues, key=lambda c: (c.start_ms, c.end_ms)):
        start, end = max(0, cue.start_ms), min(cue.end_ms, MAX_MS)
        if end <= start:
            continue
        text = payload(cue.runs)
        if not text:
            continue
        key = (start, end, cue.settings, text)
        if key in seen:
            continue
        seen.add(key)
        timing = f"{stamp(start)} --> {stamp(end)}" + (f" {cue.settings}" if cue.settings else "")
        block = f"\n{timing}\n{text}\n"
        size += len(block.encode())
        if len(seen) > MAX_CUES:
            raise SubtitleError("too_large", f"meer dan {MAX_CUES} cues")
        if size > MAX_VTT_BYTES:
            raise SubtitleError("too_large", f"meer dan {MAX_VTT_BYTES} bytes WebVTT")
        yield block


def render(cues: Iterable[Cue]) -> bytes:
    """De cues als WebVTT. SubtitleError bij te veel."""
    return (HEADER + "".join(blocks(cues))).encode()


def steps(data: bytes, kind: str) -> Iterator[Cue | None]:
    """De cues van een ondertitel van deze soort (srt, vtt of webvtt, ass of ssa), een voor een,
    met na elke TICK_CHARS tekens invoer een tik (None): de worker geeft dan zijn lus de beurt
    (ops/subs.py), ook in een lang stuk zonder cues. Elke stap kost hooguit een vaste tijd: één
    cue leest hooguit MAX_RAW_CUE tekens, en alles is lineair (review golf 2, S1)."""
    if kind not in KINDS:
        raise ValueError(f"onbekende soort {kind!r}")
    if len(data) > MAX_INPUT:
        raise SubtitleError("too_large", f"meer dan {MAX_INPUT} bytes")
    text = decode(data)
    if kind in ("ass", "ssa"):
        return parse_ass(text)
    if kind in ("vtt", "webvtt"):
        return parse_vtt(text)
    return parse_srt(text)


def parse(data: bytes, kind: str) -> Iterator[Cue]:
    """De cues van een ondertitel van deze soort, een voor een (steps zonder de tikken)."""
    return (cue for cue in steps(data, kind) if cue is not None)


def take(cue: Cue, cues: list[Cue]) -> None:
    """Een cue erbij; SubtitleError boven MAX_PARSED."""
    if len(cues) >= MAX_PARSED:
        raise SubtitleError("too_large", f"meer dan {MAX_PARSED} cues ingelezen")
    cues.append(cue)


def to_vtt(data: bytes, kind: str) -> bytes:
    """Een ondertitel (bytes van een gebruiker) als WebVTT. Faalt alleen op de grenzen
    (SubtitleError too_large) of een onbekende soort (ValueError); onzin geeft 0 cues."""
    cues: list[Cue] = []
    for cue in parse(data, kind):
        take(cue, cues)
    return render(cues)
