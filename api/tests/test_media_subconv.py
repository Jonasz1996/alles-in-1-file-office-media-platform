"""De eigen omzetter van ondertitels naar WebVTT (Keuze 29, M5): app/media/subconv.py.

- een tabel van golden files: invoer (SRT, ASS, SSA, VTT) en de VTT die eruit moet komen;
- `\\an8` wordt `line:0`, `<i>`, `<b>` en `<u>` blijven, kleur wordt een klasse, tekeningen gaan
  weg, BOM, UTF-8 en CP1252 met "é", overlappende cues blijven allebei;
- de grenzen (MAX_CUES, MAX_CUE_CHARS, MAX_INPUT);
- fuzz met een vaste seed: onzin en verminkte golden files geven nooit iets anders dan VTT die
  de strenge controle van de app (subtitles.parse_vtt) haalt, of SubtitleError.
"""

from __future__ import annotations

import random

import pytest

from app.media import subconv, subtitles
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


# --- fuzz (vaste seed) --------------------------------------------------------------------------

SPECIAL = [
    b"{", b"}", b"\\", b"<", b">", b"&", b"-->", b"\n", b"\n\n", b":", b",", b".", b"\\an8",
    b"{\\p1}", b"{\\c&H", b"<font color=", b"</c>", b"<c.", b"[Events]", b"Dialogue:",
    b"WEBVTT", b"\xef\xbb\xbf", b"\xff\xfe", b"\x00", b"\xe9", b"\xc3", b"99:99:99,999",
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
