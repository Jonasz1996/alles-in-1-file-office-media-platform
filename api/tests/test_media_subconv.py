"""De eigen omzetter van ondertitels naar WebVTT (Keuze 29, M5): app/media/subconv.py.

- een tabel van golden files: invoer (SRT, ASS, SSA, VTT) en de VTT die eruit moet komen;
- `\\an8` wordt `line:0`, `<i>`, `<b>` en `<u>` blijven, kleur wordt een klasse, tekeningen gaan
  weg, BOM, UTF-8 en CP1252 met "é", overlappende cues blijven allebei;
- de grenzen (MAX_CUES, MAX_CUE_CHARS, MAX_INPUT);
- fuzz met een vaste seed: onzin en verminkte golden files geven nooit iets anders dan VTT die
  de strenge controle van de app (subtitles.parse_vtt) haalt, of SubtitleError.
"""

from __future__ import annotations

import asyncio
import gc
import random
import time

import pytest

from app.media import subconv, subtitles
from app.media.worker.ops import subs as subs_op
from tests.media import fixtures as F

ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, \
Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,\
2,10,10,10,1
Style: Bord,Arial,24,&H0000FFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,\
8,10,10,10,1
Style: Sign,Arial,24,&H0000FFFF,&H000000FF,&H00000000,&H00000000,0,-1,0,0,100,100,0,0,1,2,0,\
2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

SSA_HEAD = """[Script Info]
ScriptType: v4.00

[V4 Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, TertiaryColour, BackColour, \
Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, AlphaLevel, \
Encoding
Style: Default,Arial,20,16777215,65535,65535,0,0,0,1,2,0,2,10,10,10,0,0
Style: Boven,Arial,20,16777215,65535,65535,0,0,0,1,2,0,6,10,10,10,0,0

[Events]
Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def srt(*cues: tuple[str, str, str]) -> bytes:
    blocks = [f"{n}\n{a} --> {b}\n{text}\n" for n, (a, b, text) in enumerate(cues, 1)]
    return "\n".join(blocks).encode()


def vtt(*cues: tuple[str, str]) -> bytes:
    return ("WEBVTT\n" + "".join(f"\n{timing}\n{text}\n" for timing, text in cues)).encode()


# (naam, soort, invoer, verwachte VTT)
GOLDEN: list[tuple[str, str, bytes, bytes]] = [
    (
        "srt_twee_cues",
        "srt",
        F.SRT_NL.encode(),
        vtt(
            ("00:00:01.000 --> 00:00:02.500", "Hallo, dit is de eerste regel."),
            ("00:00:03.000 --> 00:00:04.500", "En dit is de tweede."),
        ),
    ),
    (
        "srt_cp1252_crlf",
        "srt",
        "1\r\n00:00:01,000 --> 00:00:02,000\r\nCafé, één, ë en ü\r\n".encode("cp1252"),
        vtt(("00:00:01.000 --> 00:00:02.000", "Café, één, ë en ü")),
    ),
    (
        "srt_bom_utf8",
        "srt",
        ("﻿" + "1\n00:00:01,000 --> 00:00:02,000\nCafé\n").encode(),
        vtt(("00:00:01.000 --> 00:00:02.000", "Café")),
    ),
    (
        "srt_utf16_bom",
        "srt",
        "1\n00:00:01,000 --> 00:00:02,000\nÉén\n".encode("utf-16"),
        vtt(("00:00:01.000 --> 00:00:02.000", "Één")),
    ),
    (
        "srt_tags",
        "srt",
        srt(
            (
                "00:00:01,000",
                "00:00:02,000",
                '<i>schuin</i> <b>vet</b> <u>onder</u> <font color="#ffff00">geel</font> '
                "<span>weg</span>",
            )
        ),
        vtt(
            (
                "00:00:01.000 --> 00:00:02.000",
                "<i>schuin</i> <b>vet</b> <u>onder</u> <c.yellow>geel</c> weg",
            )
        ),
    ),
    (
        "srt_an8_en_kleurnaam",
        "srt",
        srt(("00:00:01,000", "00:00:02,000", '{\\an8}<font color="red">Boven</font>')),
        vtt(("00:00:01.000 --> 00:00:02.000 line:0", "<c.red>Boven</c>")),
    ),
    (
        "srt_escape",
        "srt",
        srt(("00:00:01,000", "00:00:02,000", "a < b & c > d --> e")),
        vtt(("00:00:01.000 --> 00:00:02.000", "a &lt; b &amp; c &gt; d --&gt; e")),
    ),
    (
        "srt_overlappend_en_ongesorteerd",
        "srt",
        srt(
            ("00:00:05,000", "00:00:06,000", "Drie"),
            ("00:00:01,000", "00:00:04,000", "Een"),
            ("00:00:02,000", "00:00:03,000", "Twee"),
        ),
        vtt(
            ("00:00:01.000 --> 00:00:04.000", "Een"),
            ("00:00:02.000 --> 00:00:03.000", "Twee"),
            ("00:00:05.000 --> 00:00:06.000", "Drie"),
        ),
    ),
    (
        "srt_dubbel_leeg_en_nul",
        "srt",
        srt(
            ("00:00:01,000", "00:00:02,000", "Een"),
            ("00:00:01,000", "00:00:02,000", "Een"),
            ("00:00:03,000", "00:00:03,000", "Nul lang"),
            ("00:00:04,000", "00:00:05,000", "<i> </i>"),
        ),
        vtt(("00:00:01.000 --> 00:00:02.000", "Een")),
    ),
    (
        "srt_zonder_lege_regels",
        "srt",
        b"1\n00:00:01,000 --> 00:00:02,000\nEen\n2\n00:00:03,000 --> 00:00:04,000\nTwee\n",
        vtt(
            ("00:00:01.000 --> 00:00:02.000", "Een"),
            ("00:00:03.000 --> 00:00:04.000", "Twee"),
        ),
    ),
    (
        "srt_twee_regels_en_lege_regel_weg",
        "srt",
        srt(("00:00:01,000", "00:00:02,000", "  Regel een  \n<i>Regel</i> twee")),
        vtt(("00:00:01.000 --> 00:00:02.000", "Regel een\n<i>Regel</i> twee")),
    ),
    (
        "ass_fixture",
        "ass",
        F.ASS.encode(),
        vtt(
            ("00:00:01.000 --> 00:00:02.500 line:0", "Bovenaan in beeld"),
            ("00:00:03.000 --> 00:00:04.500", "Onderaan, gewoon"),
        ),
    ),
    (
        "ass_stijl_boven_vet_geel",
        "ass",
        (ASS_HEAD + "Dialogue: 0,0:00:01.00,0:00:02.00,Bord,,0,0,0,,UITGANG\n").encode(),
        vtt(("00:00:01.000 --> 00:00:02.000 line:0", "<c.yellow><b>UITGANG</b></c>")),
    ),
    (
        "ass_codes",
        "ass",
        (
            ASS_HEAD + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,"
            "{\\i1}schuin{\\i0} {\\c&H0000FF&}rood{\\c} {\\b1\\fs40\\bord3}vet{\\b0}"
            "{\\pos(10,10)\\blur2\\clip(0,0,5,5)} klaar\n"
        ).encode(),
        vtt(
            (
                "00:00:01.000 --> 00:00:02.000",
                "<i>schuin</i> <c.red>rood</c> <b>vet</b> klaar",
            )
        ),
    ),
    (
        "ass_tekening_weg",
        "ass",
        (
            ASS_HEAD
            + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\p1}m 0 0 l 100 0 100 100"
            "{\\p0}Tekst\n"
            "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,{\\an7\\p2}m 0 0 l 1 1\n"
        ).encode(),
        vtt(("00:00:01.000 --> 00:00:02.000", "Tekst")),
    ),
    (
        "ass_regels_en_plekken",
        "ass",
        (
            ASS_HEAD + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\an7}Links\\Nboven\n"
            "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,{\\an3}Rechts\\honder\n"
            "Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,{\\an5}Midden\\nzacht\n"
            "Dialogue: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,{\\an9\\an1}Eerste wint\n"
        ).encode(),
        vtt(
            ("00:00:01.000 --> 00:00:02.000 line:0 align:left", "Links\nboven"),
            ("00:00:03.000 --> 00:00:04.000 align:right", "Rechts onder"),
            ("00:00:05.000 --> 00:00:06.000 line:50%", "Midden zacht"),
            ("00:00:07.000 --> 00:00:08.000 line:0 align:right", "Eerste wint"),
        ),
    ),
    (
        "ass_lagen_en_commentaar",
        "ass",
        (
            ASS_HEAD + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Twee keer\n"
            "Dialogue: 1,0:00:01.00,0:00:02.00,Default,,0,0,0,,Twee keer\n"
            "Comment: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,Niet tonen\n"
            "Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,Met, komma's, erin\n"
        ).encode(),
        vtt(
            ("00:00:01.000 --> 00:00:02.000", "Twee keer"),
            ("00:00:05.000 --> 00:00:06.000", "Met, komma's, erin"),
        ),
    ),
    (
        "ssa_v4_oude_nummers",
        "ssa",
        (
            SSA_HEAD + "Dialogue: Marked=0,0:00:01.00,0:00:02.00,Boven,,0,0,0,,Stijl boven\n"
            "Dialogue: Marked=0,0:00:03.00,0:00:04.00,Default,,0,0,0,,{\\a6}Code boven\n"
            "Dialogue: Marked=0,0:00:05.00,0:00:06.00,Default,,0,0,0,,{\\a9}Links midden\n"
        ).encode(),
        vtt(
            ("00:00:01.000 --> 00:00:02.000 line:0", "Stijl boven"),
            ("00:00:03.000 --> 00:00:04.000 line:0", "Code boven"),
            ("00:00:05.000 --> 00:00:06.000 line:50% align:left", "Links midden"),
        ),
    ),
    (
        "vtt_opschonen",
        "vtt",
        (
            b"WEBVTT - een titel\n\nSTYLE\n::cue { color: red }\n\nNOTE een notitie\n\n"
            b"id-1\n00:01.000 --> 00:02.000 line:0 align:start position:10%,line-left foo:bar\n"
            b"<v Jan>Hallo</v> <c.yellow.bg_black>geel</c> <c.onbekend>gewoon</c> "
            b"&amp; &lt;tag&gt; <00:01.500><ruby>a<rt>b</rt></ruby>\n\n"
            b"01:00:00.000 --> 01:00:01.000 size:50% line:-2\nlaat\n"
        ),
        vtt(
            (
                "00:00:01.000 --> 00:00:02.000 line:0 align:start position:10%",
                "Hallo <c.yellow>geel</c> gewoon &amp; &lt;tag&gt; ab",
            ),
            ("01:00:00.000 --> 01:00:01.000 size:50% line:-2", "laat"),
        ),
    ),
    (
        "webvtt_van_ffmpeg",
        "webvtt",
        b"WEBVTT\n\n00:01.000 --> 00:02.500\n<b>Boven</b> & geel\n\n00:03.000 --> 00:04.000\n"
        b"Tweede\n",
        vtt(
            ("00:00:01.000 --> 00:00:02.500", "<b>Boven</b> &amp; geel"),
            ("00:00:03.000 --> 00:00:04.000", "Tweede"),
        ),
    ),
    # review golf 2, C3: in een SRT is alleen {\…} een code; {Muziek} is tekst. {Y:i} (een stijl
    # van MicroDVD) gaat weg, zoals bij ffmpeg
    (
        "srt_accolades_zijn_tekst",
        "srt",
        srt(("00:00:01,000", "00:00:02,000", "{Muziek} hallo {lacht} {Y:i}{\\an8}boven")),
        vtt(("00:00:01.000 --> 00:00:02.000 line:0", "{Muziek} hallo {lacht} boven")),
    ),
    # review golf 2, C4: entiteiten in een SRT zijn tekst (niet &amp;amp;); zonder puntkomma
    # (&copy) of onbekend blijft het staan
    (
        "srt_entiteiten",
        "srt",
        srt(
            ("00:00:01,000", "00:00:02,000", "Tom &amp; Jerry &lt;3 &#233;&#xE9; AT&T &copy &foo;")
        ),
        vtt(
            (
                "00:00:01.000 --> 00:00:02.000",
                "Tom &amp; Jerry &lt;3 éé AT&amp;T &amp;copy &amp;foo;",
            )
        ),
    ),
    # review golf 2, C4: \rNaam zet de stijl Naam (cursief geel), \r de stijl van de regel
    (
        "ass_r_met_een_stijl",
        "ass",
        (
            ASS_HEAD + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,"
            "gewoon {\\rSign}bord{\\r} terug {\\rOnbekend}ook terug\n"
        ).encode(),
        vtt(
            (
                "00:00:01.000 --> 00:00:02.000",
                "gewoon <c.yellow><i>bord</i></c> terug ook terug",
            )
        ),
    ),
    # Een \r of een stuurteken uit een entiteit (&#13;) breekt de VTT niet (gevonden bij C4)
    (
        "vtt_entiteit_cr",
        "vtt",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\na&#13;b&#12;c\n",
        vtt(("00:00:01.000 --> 00:00:02.000", "a\nbc")),
    ),
    ("leeg", "srt", b"", vtt()),
]


@pytest.mark.parametrize(("name", "kind", "data", "want"), GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden(name: str, kind: str, data: bytes, want: bytes) -> None:
    got = subconv.to_vtt(data, kind)
    assert got.decode() == want.decode()
    subtitles.parse_vtt(got)  # de controle van de app keurt hem goed


def test_an8_wordt_line_0() -> None:
    out = subconv.to_vtt(F.ASS.encode(), "ass").decode()
    assert "00:00:01.000 --> 00:00:02.500 line:0\nBovenaan in beeld" in out


def test_tekenset_bom_utf8_cp1252() -> None:
    text = "1\n00:00:01,000 --> 00:00:02,000\nCafé\n"
    for data in (text.encode(), b"\xef\xbb\xbf" + text.encode(), text.encode("cp1252")):
        assert "Café" in subconv.to_vtt(data, "srt").decode()
    # CP1252 met een teken dat CP1252 niet kent (0x81): dat wordt het vervangteken
    assert "�" in subconv.to_vtt(b"1\n00:00:01,000 --> 00:00:02,000\n\x81\xe9\n", "srt").decode()


def test_stuurtekens_gaan_weg() -> None:
    out = subconv.to_vtt(b"1\n00:00:01,000 --> 00:00:02,000\na\x00b\x1bc\x7fd\te\n", "srt")
    assert out.decode().endswith("abcd\te\n")


def test_kleur_naar_de_dichtstbijzijnde_klasse() -> None:
    assert subconv.nearest((250, 250, 10)) == "yellow"
    assert subconv.nearest((240, 240, 240)) is None  # wit is de standaard
    assert subconv.nearest((10, 10, 200)) == "blue"
    assert subconv._html_colour("#0f0") == "lime"
    assert subconv._html_colour("onzin") is None
    assert subconv._ass_colour("&H0000FF00") == "lime"  # BBGGRR: groen
    assert subconv._ass_colour("65535") == "yellow"  # SSA: een getal (0x00FFFF = geel)


def test_een_lange_cue_wordt_afgekapt() -> None:
    long = "x" * 5000 + " & " + "y" * 10
    out = subconv.to_vtt(srt(("00:00:01,000", "00:00:02,000", f"<i>{long}</i>")), "srt")
    payload = out.decode().split("\n")[3]
    assert len(payload) <= subconv.MAX_CUE_CHARS
    assert payload.startswith("<i>xxx") and payload.endswith("</i>")
    subtitles.parse_vtt(out)


def test_een_entiteit_wordt_niet_doorgeknipt() -> None:
    runs = [(subconv.Style(), "a" * (subconv.MAX_CUE_CHARS - 3) + "&b")]
    text = subconv.payload(runs)
    assert text.endswith("a") and "&" not in text


def test_te_veel_cues() -> None:
    cues = [
        (f"00:00:{i % 60:02d},{i // 60:03d}", f"00:01:00,{i // 60:03d}", f"c{i}") for i in range(10)
    ]
    assert subconv.to_vtt(srt(*cues), "srt").count(b"-->") == 10
    many = "\n".join(
        f"{i}\n{subconv.stamp(i * 10).replace('.', ',')} --> "
        f"{subconv.stamp(i * 10 + 5).replace('.', ',')}\nc{i}\n"
        for i in range(subconv.MAX_CUES + 1)
    ).encode()
    with pytest.raises(subconv.SubtitleError) as caught:
        subconv.to_vtt(many, "srt")
    assert caught.value.code == "too_large"


def test_te_veel_ingelezen_cues() -> None:
    # Allemaal dezelfde (die vallen weg), maar inlezen stopt toch bij MAX_PARSED
    one = "1\n00:00:01,000 --> 00:00:02,000\nx\n\n"
    with pytest.raises(subconv.SubtitleError):
        subconv.to_vtt((one * (subconv.MAX_PARSED + 1)).encode(), "srt")


def test_te_grote_invoer() -> None:
    with pytest.raises(subconv.SubtitleError):
        subconv.to_vtt(b" " * (subconv.MAX_INPUT + 1), "srt")


def test_onbekende_soort() -> None:
    with pytest.raises(ValueError, match="soort"):
        subconv.to_vtt(b"", "sup")


def test_tijden_tot_99_uur() -> None:
    out = subconv.to_vtt(srt(("99:59:59,000", "120:00:00,000", "laat")), "srt").decode()
    assert "99:59:59.000 --> 99:59:59.999" in out
    assert subconv.stamp(-5) == "00:00:00.000"


# --- tijd (review golf 2, S1) -------------------------------------------------------------------
#
# Eerst waren drie dingen kwadratisch in de lengte van één cue (pop(0) in _lines, + in _Runs, en
# de regex van de HTML-tags die per < 200 × 200 keer terugzocht): een SRT van 1,6 MB met
# "<b> </b> " kostte 15 s, en de worker (één lus) gaf intussen niets anders de beurt.

EVIL = {
    "srt_tags": ("srt", "<b> </b> "),
    "srt_open_tag": ("srt", "<a" + " " * 199),
    "ass_codes": ("ass", "{\\b1} {\\b0} "),
    "vtt_tags": ("vtt", "<b> </b> "),
}
ASS_EVENT = "Dialogue: 0,0:00:{s:02d}.00,0:00:{s:02d}.50,Default,,0,0,0,,{text}\n"


def _one_cue(kind: str, text: str, s: int = 1) -> str:
    if kind == "srt":
        return f"{s}\n00:00:{s:02d},000 --> 00:00:{s:02d},500\n{text}x\n\n"
    if kind == "vtt":
        return f"00:00:{s:02d}.000 --> 00:00:{s:02d}.500\n{text}x\n\n"
    return ASS_EVENT.format(s=s, text=text + "x")


def _file(kind: str, cues: list[str]) -> bytes:
    head = {"srt": "", "vtt": "WEBVTT\n\n", "ass": ASS_HEAD}[kind]
    return (head + "".join(cues)).encode()


def _timed(fn, *args):
    """(uitvoer, de tijd van fn), met de heap van de testset in gc.freeze (zie _longest_gap)."""
    gc.collect()
    gc.freeze()
    try:
        start = time.perf_counter()
        result = fn(*args)
        return result, time.perf_counter() - start
    finally:
        gc.unfreeze()


@pytest.mark.parametrize("name", sorted(EVIL))
def test_een_cue_van_5_mb_kost_geen_minuten(name: str) -> None:
    kind, unit = EVIL[name]
    data = _file(kind, [_one_cue(kind, unit * (5_000_000 // len(unit)))])
    assert len(data) > 4_900_000
    out, took = _timed(subconv.to_vtt, data, kind)
    subtitles.parse_vtt(out)
    assert took < 1.0, f"{name}: {took:.2f} s"


@pytest.mark.parametrize("name", sorted(EVIL))
def test_veel_cues_tot_de_grens_lineair(name: str) -> None:
    # 1 MB aan cues die elk MAX_RAW_CUE groot zijn: elke cue kost hooguit een vaste tijd
    kind, unit = EVIL[name]
    text = unit * (subconv.MAX_RAW_CUE // len(unit))
    data = _file(kind, [_one_cue(kind, text, s % 60) for s in range(1_000_000 // len(text))])
    out, took = _timed(subconv.to_vtt, data, kind)
    subtitles.parse_vtt(out)
    assert took < 3.0, f"{name}: {took:.2f} s"


def test_lege_stukken_weghalen_is_lineair() -> None:
    # 2 x 100.000 lege stukken: kwadratisch is dat seconden, lineair een tiende daarvan
    runs = [(subconv.Style(bold=n % 2 == 0), " ") for n in range(100_000)]
    lines, took = _timed(subconv._lines, [*runs, (subconv.Style(), "x"), *runs])
    assert lines == [[(subconv.Style(), "x")]]
    assert took < 0.5, f"{took:.2f} s"


def test_stukken_samenvoegen_is_lineair() -> None:
    runs = subconv._Runs()
    _, took = _timed(lambda: [runs.add(subconv.Style(), "x" * 20) for _ in range(50_000)])
    assert runs.items == [(subconv.Style(), "x" * 1_000_000)]
    assert took < 0.5, f"{took:.2f} s"


def test_de_regex_van_de_tags_zoekt_niet_terug() -> None:
    text = ("<a" + " " * 199) * 5_000
    found, took = _timed(subconv._HTML_TAG.findall, text)
    assert found == []
    assert took < 0.3, f"{took:.2f} s"
    assert subconv._HTML_TAG.fullmatch('<font color="red" />') is not None
    assert subconv._HTML_TAG.fullmatch("<br/>") is not None


def test_een_te_lange_ruwe_cue_wordt_afgekapt_zonder_halve_code() -> None:
    cut = subconv.clip("a" * (subconv.MAX_RAW_CUE - 3) + "{\\b1}b")
    assert cut == "a" * (subconv.MAX_RAW_CUE - 3)
    cut = subconv.clip("a" * (subconv.MAX_RAW_CUE - 3) + "<font color=red>b")
    assert cut == "a" * (subconv.MAX_RAW_CUE - 3)
    assert subconv.clip("kort {") == "kort {"


async def _longest_gap(work) -> tuple[bytes, float]:
    """Het werk naast een taak die elke ms wakker wil worden: (uitvoer, het langste gat).

    De heap van dit testproces (na duizenden tests) gaat eerst in gc.freeze: anders meet een
    volle ronde van de garbage collector over al die objecten mee (in de hele set tot 0,5 s),
    en dat is niet wat de omzetter doet. De worker is een eigen proces met een kleine heap; wat
    de omzetter zelf aan afval maakt, ruimt de collector hier nog steeds op, en dat telt mee."""
    gaps: list[float] = []
    done = asyncio.Event()
    gc.collect()
    gc.freeze()

    async def tick() -> None:
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.001)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0)
    try:
        out, _ = await work
    finally:
        done.set()
        await ticker
        gc.unfreeze()
    return out, max(gaps)


@pytest.mark.anyio
@pytest.mark.parametrize("name", sorted(EVIL))
async def test_de_worker_geeft_zijn_lus_de_beurt(name: str) -> None:
    """ops/subs.convert (in de lus van de worker): geen gat van meer dan 0,25 s, ook niet met
    één grote cue of 1 MB zonder een enkele cue."""
    kind, unit = EVIL[name]
    text = unit * (subconv.MAX_RAW_CUE // len(unit))
    inputs = [
        _file(kind, [_one_cue(kind, text, s % 60) for s in range(1_000_000 // len(text))]),
        _file(kind, [_one_cue(kind, unit * (1_000_000 // len(unit)))]),
        _file(kind, [unit * (1_000_000 // len(unit))]),
    ]
    for data in inputs:
        vtt_out, gap = await _longest_gap(subs_op.convert(data, kind))
        subtitles.parse_vtt(vtt_out)
        assert gap < 0.25, f"{name}: de lus stond {gap:.2f} s stil"


# --- fuzz (vaste seed) --------------------------------------------------------------------------

SPECIAL = [
    b"{", b"}", b"\\", b"<", b">", b"&", b"-->", b"\n", b"\n\n", b":", b",", b".", b"\\an8",
    b"{\\p1}", b"{\\c&H", b"<font color=", b"</c>", b"<c.", b"[Events]", b"Dialogue:",
    b"WEBVTT", b"\xef\xbb\xbf", b"\xff\xfe", b"\x00", b"\xe9", b"\xc3", b"99:99:99,999",
    b"&amp;", b"&#13;", b"&#12;", b"&#0;", b"&#x110000;", b"{\\rSign}", b"{\\r}", b"{Y:i}",
    b"<b> </b> ", b"<a" + b" " * 199, b"{\\b1} {\\b0} ",
]  # fmt: skip


def _mutate(rng: random.Random, data: bytes) -> bytes:
    out = bytearray(data)
    for _ in range(rng.randint(1, 8)):
        choice = rng.random()
        pos = rng.randint(0, len(out)) if out else 0
        if choice < 0.3 and out:
            out[min(pos, len(out) - 1)] = rng.randrange(256)
        elif choice < 0.6:
            out[pos:pos] = rng.choice(SPECIAL)
        elif choice < 0.8 and out:
            del out[pos : pos + rng.randint(1, 20)]
        else:
            out[pos:pos] = out[rng.randint(0, len(out)) :][: rng.randint(0, 200)]
    return bytes(out)


def _ok(data: bytes, kind: str) -> None:
    try:
        out = subconv.to_vtt(data, kind)
    except subconv.SubtitleError:
        return
    subtitles.parse_vtt(out)
    assert subconv.to_vtt(data, kind) == out  # altijd hetzelfde


def test_fuzz_willekeur() -> None:
    rng = random.Random(20261029)
    for _ in range(400):
        data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 2000)))
        for kind in ("srt", "vtt", "ass"):
            _ok(data, kind)


def test_fuzz_verminkte_golden_files() -> None:
    rng = random.Random(5)
    seeds = [(kind, data) for _, kind, data, _ in GOLDEN if data]
    for _ in range(1500):
        kind, data = rng.choice(seeds)
        _ok(_mutate(rng, data), kind)
        _ok(_mutate(rng, data), rng.choice(["srt", "vtt", "ass", "ssa", "webvtt"]))


def test_fuzz_groot_en_op_tijd() -> None:
    """Grote invoer (20 KB tot 400 KB) uit golden files en de lastige stukken door elkaar:
    altijd geldige VTT (of SubtitleError), en elk binnen 1 s (review golf 2, S1; regel 10)."""
    rng = random.Random(29)
    seeds = [(kind, data) for _, kind, data, _ in GOLDEN if data]
    for _ in range(40):
        kind, data = rng.choice(seeds)
        parts = [data]
        while sum(map(len, parts)) < rng.randint(20_000, 400_000):
            if rng.random() < 0.5:
                parts.append(rng.choice(SPECIAL) * rng.randint(1, 2000))
            else:
                parts.append(_mutate(rng, rng.choice(seeds)[1]))
        big = b"".join(parts)
        for target in (kind, rng.choice(["srt", "vtt", "ass"])):
            _, took = _timed(_ok, big, target)
            assert took < 1.0, f"{len(big)} bytes als {target}: {took:.2f} s"
