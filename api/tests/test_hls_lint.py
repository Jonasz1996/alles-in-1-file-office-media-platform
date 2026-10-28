"""De eigen HLS-lint (§3.6): goede playlists (de voorbeelden van §2 M4) en kapotte, per regel.

M4 gebruikt de lint in `test_media_hls.py` op de playlists van `hls.py`. Hier staat alleen de
lint zelf: elke regel heeft een playlist die hem breekt, met de verwachte code.
"""

import random
import re
from pathlib import Path

import pytest

from tests.media.hls_lint import (
    Context,
    Problem,
    assert_ok,
    assert_set_ok,
    lint,
    lint_set,
    parse,
    peak_bps,
)

# --- de voorbeelden van §2 M4, letterlijk (de "…" uitgeschreven) ---

MASTER = (
    "#EXTM3U\n"
    "#EXT-X-VERSION:7\n"
    "#EXT-X-INDEPENDENT-SEGMENTS\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=4026000,AVERAGE-BANDWIDTH=2600000,CODECS="avc1.64001f,mp4a.40.2"'
    ",RESOLUTION=1280x720,FRAME-RATE=23.976,VIDEO-RANGE=SDR\n"
    "v.m3u8\n"
)

HEAD = (
    "#EXTM3U\n"
    "#EXT-X-VERSION:7\n"
    "#EXT-X-TARGETDURATION:{target}\n"
    "#EXT-X-PLAYLIST-TYPE:VOD\n"
    "#EXT-X-MEDIA-SEQUENCE:0\n"
    '#EXT-X-MAP:URI="init.mp4?g=1"\n'
)


def media(durations: list[float], target: int = 2) -> str:
    body = "".join(f"#EXTINF:{d:.3f},\n{i}.m4s\n" for i, d in enumerate(durations))
    return HEAD.format(target=target) + body + "#EXT-X-ENDLIST\n"


# 2 uur op het rooster van 2 s: 3600 segmenten, het laatste 1,512 s (zoals in §2 M4)
TRANSCODE = media([2.0] * 3599 + [1.512])
SHORT = media([2.0, 2.0, 2.0, 1.512])
# remux: de segmenten volgen de keyframes (Keuze 23), TARGETDURATION het langste naar boven
REMUX = media([6.006, 7.341, 6.0, 12.5, 3.2], target=13)
REMUX_MASTER = MASTER.replace("#EXT-X-INDEPENDENT-SEGMENTS\n", "").replace(
    'CODECS="avc1.64001f,mp4a.40.2"', 'CODECS="hvc1.2.4.L150.90,mp4a.40.2"'
)
VP9_MASTER = MASTER.replace('"avc1.64001f,mp4a.40.2"', '"vp09.00.31.08,opus"')
NOAUDIO_MASTER = MASTER.replace('"avc1.64001f,mp4a.40.2"', '"avc1.64001f"')
WEBOS_MASTER = MASTER.replace("#EXT-X-INDEPENDENT-SEGMENTS\n", "")

TRANSCODE_CTX = Context(mode="transcode", base="web", has_audio=True)
REMUX_CTX = Context(mode="remux", base="web", has_audio=True)


def codes(problems: list[Problem]) -> set[str]:
    return {p.code for p in problems}


@pytest.mark.parametrize(
    ("name", "playlist", "ctx"),
    [
        ("master van §2 M4", MASTER, TRANSCODE_CTX),
        ("v.m3u8 van §2 M4 (3600 segmenten)", TRANSCODE, TRANSCODE_CTX),
        ("kort", SHORT, TRANSCODE_CTX),
        ("remux", REMUX, REMUX_CTX),
        ("remux-master met hvc1, zonder INDEPENDENT", REMUX_MASTER, REMUX_CTX),
        ("VP9 en Opus (Chromium zonder H.264)", VP9_MASTER, TRANSCODE_CTX),
        ("zonder audio (Keuze 19)", NOAUDIO_MASTER, Context(has_audio=False)),
        ("webos-5 zonder INDEPENDENT", WEBOS_MASTER, Context(base="webos-5")),
        ("webos-5 media", SHORT, Context(base="webos-5")),
        ("bytes zonder BOM", MASTER.encode(), TRANSCODE_CTX),
        (
            "HDR via remux (PQ)",
            REMUX_MASTER.replace("VIDEO-RANGE=SDR", "VIDEO-RANGE=PQ"),
            REMUX_CTX,
        ),
        (
            "AV1 met CLOSED-CAPTIONS=NONE",
            MASTER.replace('"avc1.64001f,mp4a.40.2"', '"av01.0.08M.08,opus"').replace(
                "VIDEO-RANGE=SDR", "VIDEO-RANGE=SDR,CLOSED-CAPTIONS=NONE"
            ),
            TRANSCODE_CTX,
        ),
        ("een titel bij EXTINF", SHORT.replace("#EXTINF:2.000,\n0", "#EXTINF:2.000,Deel 1\n0"),
         TRANSCODE_CTX),
        ("een commentaarregel", SHORT.replace("#EXT-X-ENDLIST", "# nep\n#EXT-X-ENDLIST"),
         TRANSCODE_CTX),
        ("eerste segment 2,021 s (S ≠ 0, spike punt 12)", media([2.021, 2.0, 1.5]),
         TRANSCODE_CTX),
    ],
)  # fmt: skip
def test_good(name, playlist, ctx):
    assert lint(playlist, ctx) == [], name
    assert_ok(playlist, ctx)


def test_good_set():
    sizes = {f"{i}.m4s": 600_000 for i in range(3)} | {"3.m4s": 400_000}
    assert_set_ok({"master.m3u8": MASTER, "v.m3u8": SHORT}, TRANSCODE_CTX, sizes=sizes)


def test_parse_gives_segments_and_variant():
    p = parse(SHORT)
    assert p is not None and p.kind == "media"
    assert p.target == 2 and p.map_uri == "init.mp4?g=1" and p.media_sequence == 0
    assert [(s.duration, s.uri) for s in p.segments] == [
        (2.0, "0.m4s"),
        (2.0, "1.m4s"),
        (2.0, "2.m4s"),
        (1.512, "3.m4s"),
    ]
    m = parse(MASTER)
    assert m is not None and m.kind == "master" and m.independent
    ((attrs, uri, _line),) = m.variants
    assert uri == "v.m3u8" and attrs["BANDWIDTH"] == "4026000"


def sub(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


# (naam, playlist, context, verwachte code)
BROKEN = [
    # de vorm van het bestand
    ("BOM als bytes", b"\xef\xbb\xbf" + MASTER.encode(), TRANSCODE_CTX, "bom"),
    ("BOM als tekst", "\ufeff" + MASTER, TRANSCODE_CTX, "bom"),
    ("CRLF", MASTER.replace("\n", "\r\n"), TRANSCODE_CTX, "crlf"),
    ("geen UTF-8", MASTER.encode().replace(b"v.m3u8", b"v\xff.m3u8"), TRANSCODE_CTX, "not_utf8"),
    ("tab", sub(MASTER, "v.m3u8", "v.m3u8\t"), TRANSCODE_CTX, "control_char"),
    ("geen LF aan het eind", MASTER.rstrip("\n"), TRANSCODE_CTX, "no_final_lf"),
    ("geen #EXTM3U", MASTER.replace("#EXTM3U\n", ""), TRANSCODE_CTX, "first_line"),
    ("#EXTM3U niet eerst", sub(MASTER, "#EXTM3U\n#EXT-X-VERSION:7\n",
                                "#EXT-X-VERSION:7\n#EXTM3U\n"), TRANSCODE_CTX, "first_line"),
    ("witruimte in een tag", sub(SHORT, "#EXT-X-TARGETDURATION:2", "#EXT-X-TARGETDURATION: 2"),
     TRANSCODE_CTX, "whitespace"),
    ("regel met spaties", sub(SHORT, "#EXT-X-ENDLIST", "   \n#EXT-X-ENDLIST"), TRANSCODE_CTX,
     "whitespace"),
    ("leeg", "", TRANSCODE_CTX, "kind_unknown"),
    # versie en tags
    ("versie 6", SHORT.replace("VERSION:7", "VERSION:6"), TRANSCODE_CTX, "version"),
    ("geen versie", SHORT.replace("#EXT-X-VERSION:7\n", ""), TRANSCODE_CTX, "version"),
    ("versie twee keer", sub(SHORT, "#EXT-X-VERSION:7\n", "#EXT-X-VERSION:7\n#EXT-X-VERSION:7\n"),
     TRANSCODE_CTX, "version_count"),
    ("een sleutel", sub(SHORT, "#EXT-X-MAP", '#EXT-X-KEY:METHOD=NONE\n#EXT-X-MAP'), TRANSCODE_CTX,
     "tag_unused"),
    ("discontinuïteit", sub(SHORT, "#EXTINF:2.000,\n1.m4s", "#EXT-X-DISCONTINUITY\n#EXTINF:2.000,"
                            "\n1.m4s"), TRANSCODE_CTX, "tag_unused"),
    ("alternatieve audio", sub(MASTER, "#EXT-X-STREAM-INF",
                               '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="nl",URI="a.m3u8"\n'
                               "#EXT-X-STREAM-INF"), TRANSCODE_CTX, "tag_unused"),
    ("onbekende tag", sub(SHORT, "#EXT-X-ENDLIST", "#EXT-X-NEP:1\n#EXT-X-ENDLIST"), TRANSCODE_CTX,
     "tag_unknown"),
    ("master en media samen", sub(MASTER, "v.m3u8\n", "v.m3u8\n#EXTINF:2.000,\n0.m4s\n"),
     TRANSCODE_CTX, "mixed"),
    # EXTINF en TARGETDURATION
    ("EXTINF afgerond > target", media([2.0, 2.6, 1.0]), TRANSCODE_CTX, "extinf_too_long"),
    ("EXTINF 2,5 rondt naar 3", media([2.5, 1.0]), TRANSCODE_CTX, "extinf_too_long"),
    ("EXTINF zonder drie decimalen", sub(SHORT, "#EXTINF:2.000,", "#EXTINF:2,"), TRANSCODE_CTX,
     "extinf_decimals"),
    ("EXTINF zonder komma", sub(SHORT, "#EXTINF:2.000,", "#EXTINF:2.000"), TRANSCODE_CTX,
     "extinf_syntax"),
    ("EXTINF geen getal", sub(SHORT, "#EXTINF:2.000,", "#EXTINF:twee,"), TRANSCODE_CTX,
     "extinf_syntax"),
    ("EXTINF 0", sub(SHORT, "#EXTINF:2.000,", "#EXTINF:0.000,"), TRANSCODE_CTX, "extinf_value"),
    ("EXTINF zonder URI", sub(SHORT, "#EXT-X-ENDLIST", "#EXTINF:1.000,\n#EXT-X-ENDLIST"),
     TRANSCODE_CTX, "extinf_orphan"),
    ("URI zonder EXTINF", sub(SHORT, "#EXT-X-ENDLIST", "4.m4s\n#EXT-X-ENDLIST"), TRANSCODE_CTX,
     "uri_without_extinf"),
    ("geen target", SHORT.replace("#EXT-X-TARGETDURATION:2\n", ""), TRANSCODE_CTX,
     "target_missing"),
    ("target 0", SHORT.replace("TARGETDURATION:2", "TARGETDURATION:0"), TRANSCODE_CTX,
     "target_value"),
    ("target twee keer", sub(SHORT, "#EXT-X-PLAYLIST-TYPE", "#EXT-X-TARGETDURATION:2\n"
                             "#EXT-X-PLAYLIST-TYPE"), TRANSCODE_CTX, "tag_count"),
    ("transcode met target 6", media([2.0, 2.0], target=6), TRANSCODE_CTX, "target_transcode"),
    ("remux met target te groot", media([6.0, 5.2], target=7), REMUX_CTX, "target_remux"),
    ("remux met target afgerond i.p.v. naar boven", media([6.4, 5.0], target=6), REMUX_CTX,
     "target_remux"),
    # VOD
    ("geen ENDLIST", SHORT.replace("#EXT-X-ENDLIST\n", ""), TRANSCODE_CTX, "endlist_missing"),
    ("segment na ENDLIST", SHORT + "#EXTINF:1.000,\n4.m4s\n", TRANSCODE_CTX, "segment_after_end"),
    ("geen PLAYLIST-TYPE", SHORT.replace("#EXT-X-PLAYLIST-TYPE:VOD\n", ""), TRANSCODE_CTX,
     "playlist_type"),
    ("EVENT", SHORT.replace("PLAYLIST-TYPE:VOD", "PLAYLIST-TYPE:EVENT"), TRANSCODE_CTX,
     "playlist_type"),
    ("MEDIA-SEQUENCE geen getal", SHORT.replace("SEQUENCE:0", "SEQUENCE:x"), TRANSCODE_CTX,
     "media_sequence"),
    ("MEDIA-SEQUENCE na het eerste segment", sub(SHORT, "#EXT-X-MEDIA-SEQUENCE:0\n", "").replace(
        "#EXT-X-ENDLIST", "#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-ENDLIST"), TRANSCODE_CTX, "tag_order"),
    # de init
    ("geen MAP", SHORT.replace('#EXT-X-MAP:URI="init.mp4?g=1"\n', ""), TRANSCODE_CTX,
     "map_missing"),
    ("MAP twee keer", sub(SHORT, "#EXTINF:2.000,\n2.m4s", '#EXT-X-MAP:URI="init.mp4?g=2"\n'
                          "#EXTINF:2.000,\n2.m4s"), TRANSCODE_CTX, "map_count"),
    ("MAP zonder quotes", SHORT.replace('URI="init.mp4?g=1"', "URI=init.mp4"), TRANSCODE_CTX,
     "map_uri"),
    ("MAP met BYTERANGE", SHORT.replace('URI="init.mp4?g=1"', 'URI="init.mp4?g=1",BYTERANGE="9@0"'),
     TRANSCODE_CTX, "attr_unused"),
    ("MAP-attribuut twee keer", SHORT.replace('URI="init.mp4?g=1"', 'URI="a.mp4",URI="b.mp4"'),
     TRANSCODE_CTX, "attr_duplicate"),
    ("MAP met een komma aan het eind", SHORT.replace('URI="init.mp4?g=1"', 'URI="init.mp4?g=1",'),
     TRANSCODE_CTX, "attr_syntax"),
    # URI's
    ("absolute URI", sub(SHORT, "\n0.m4s", "\nhttps://evil.example/0.m4s"), TRANSCODE_CTX,
     "uri_absolute"),
    ("URI vanaf de wortel", sub(SHORT, "\n0.m4s", "\n/api/v1/stream/x/0.m4s"), TRANSCODE_CTX,
     "uri_absolute"),
    ("URI met ..", sub(SHORT, "\n0.m4s", "\n../0.m4s"), TRANSCODE_CTX, "uri_chars"),
    ("query met een sleutel", sub(SHORT, "\n0.m4s", "\n0.m4s?token=geheim"), TRANSCODE_CTX,
     "uri_query"),
    ("init met een andere query", SHORT.replace("init.mp4?g=1", "init.mp4?sid=1"), TRANSCODE_CTX,
     "uri_query"),
    ("master-URI met een query", sub(MASTER, "\nv.m3u8", "\nv.m3u8?sid=x"), TRANSCODE_CTX,
     "uri_query"),
    ("segmentnaam", sub(SHORT, "\n1.m4s", "\nseg1.m4s"), TRANSCODE_CTX, "segment_name"),
    ("segment overgeslagen", sub(SHORT, "\n2.m4s", "\n7.m4s"), TRANSCODE_CTX, "segment_name"),
    ("segment twee keer", sub(SHORT, "\n2.m4s", "\n1.m4s"), TRANSCODE_CTX, "segment_duplicate"),
    ("geen segmenten", HEAD.format(target=2) + "#EXT-X-ENDLIST\n", TRANSCODE_CTX, "no_segments"),
    # de variant
    ("twee varianten", MASTER + MASTER.split("\n", 3)[3], TRANSCODE_CTX, "variant_count"),
    ("variant zonder URI", MASTER.replace("v.m3u8\n", ""), TRANSCODE_CTX, "variant_uri_missing"),
    ("URI zonder variant", sub(MASTER, "#EXT-X-INDEPENDENT-SEGMENTS\n",
                               "#EXT-X-INDEPENDENT-SEGMENTS\nlos.m3u8\n"), TRANSCODE_CTX,
     "uri_orphan"),
    ("geen CODECS", MASTER.replace(',CODECS="avc1.64001f,mp4a.40.2"', ""), TRANSCODE_CTX,
     "codecs_missing"),
    ("CODECS zonder quotes", MASTER.replace('CODECS="avc1.64001f,mp4a.40.2"', "CODECS=avc1.64001f"),
     TRANSCODE_CTX, "codecs_value"),
    ("alleen video terwijl de bron audio heeft", NOAUDIO_MASTER, TRANSCODE_CTX, "codecs_audio"),
    ("audio terwijl de bron geen audio heeft", MASTER, Context(has_audio=False),
     "codecs_no_audio"),
    ("geen video", MASTER.replace('"avc1.64001f,mp4a.40.2"', '"mp4a.40.2"'), TRANSCODE_CTX,
     "codecs_video"),
    ("hev1 in plaats van hvc1", REMUX_MASTER.replace("hvc1.", "hev1."), REMUX_CTX, "codecs_value"),
    ("onbekende codec", MASTER.replace("mp4a.40.2", "mp3"), TRANSCODE_CTX, "codecs_value"),
    ("geen BANDWIDTH", MASTER.replace("STREAM-INF:BANDWIDTH=4026000,", "STREAM-INF:"),
     TRANSCODE_CTX, "bandwidth"),
    ("BANDWIDTH 0", MASTER.replace("BANDWIDTH=4026000", "BANDWIDTH=0"), TRANSCODE_CTX,
     "bandwidth"),
    ("AVERAGE groter dan BANDWIDTH", MASTER.replace("2600000", "5000000"), TRANSCODE_CTX,
     "average_bandwidth"),
    ("BANDWIDTH onder de piek", MASTER, Context(peak_bps=5_000_000), "bandwidth_peak"),
    ("geen RESOLUTION", MASTER.replace(",RESOLUTION=1280x720", ""), TRANSCODE_CTX, "resolution"),
    ("RESOLUTION fout", MASTER.replace("1280x720", "1280*720"), TRANSCODE_CTX, "resolution"),
    ("FRAME-RATE met twee decimalen", MASTER.replace("23.976", "23.98"), TRANSCODE_CTX,
     "frame_rate"),
    ("geen FRAME-RATE", MASTER.replace(",FRAME-RATE=23.976", ""), TRANSCODE_CTX, "frame_rate"),
    ("VIDEO-RANGE fout", MASTER.replace("VIDEO-RANGE=SDR", "VIDEO-RANGE=HDR10"), TRANSCODE_CTX,
     "video_range"),
    ("geen VIDEO-RANGE", MASTER.replace(",VIDEO-RANGE=SDR", ""), TRANSCODE_CTX, "video_range"),
    ("AUDIO-groep", MASTER.replace("VIDEO-RANGE=SDR", 'VIDEO-RANGE=SDR,AUDIO="a"'), TRANSCODE_CTX,
     "attr_unused"),
    ("onbekend attribuut", MASTER.replace("VIDEO-RANGE=SDR", "VIDEO-RANGE=SDR,NEP=1"),
     TRANSCODE_CTX, "attr_unknown"),
    ("CLOSED-CAPTIONS met een groep", MASTER.replace("VIDEO-RANGE=SDR",
                                                     'VIDEO-RANGE=SDR,CLOSED-CAPTIONS="cc"'),
     TRANSCODE_CTX, "attr_value"),
    ("attribuutlijst kapot", MASTER.replace("BANDWIDTH=4026000,", "BANDWIDTH=4026000,,"),
     TRANSCODE_CTX, "attr_syntax"),
    # EXT-X-INDEPENDENT-SEGMENTS
    ("INDEPENDENT met webos-5", MASTER, Context(base="webos-5"), "independent_webos"),
    ("INDEPENDENT in media met webos-5", sub(SHORT, "#EXT-X-TARGETDURATION",
                                             "#EXT-X-INDEPENDENT-SEGMENTS\n#EXT-X-TARGETDURATION"),
     Context(base="webos-5"), "independent_webos"),
    ("INDEPENDENT bij remux", MASTER, REMUX_CTX, "independent_remux"),
    ("transcode zonder INDEPENDENT", WEBOS_MASTER, TRANSCODE_CTX, "independent_missing"),
    ("INDEPENDENT met een waarde", MASTER.replace("INDEPENDENT-SEGMENTS", "INDEPENDENT-SEGMENTS:1"),
     TRANSCODE_CTX, "tag_syntax"),
]  # fmt: skip


@pytest.mark.parametrize(("name", "playlist", "ctx", "code"), BROKEN, ids=[b[0] for b in BROKEN])
def test_broken(name, playlist, ctx, code):
    problems = lint(playlist, ctx)
    assert code in codes(problems), f"{name}: verwacht {code}, kreeg {problems}"
    with pytest.raises(AssertionError, match=code):
        assert_ok(playlist, ctx)


def test_each_rule_has_a_broken_case():
    """Elke code die de lint kan geven, heeft hierboven een kapotte playlist (behalve die van
    lint_set, met een eigen test)."""
    import tests.media.hls_lint as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    emitted = set(re.findall(r'(?:out\.add|Problem)\(\s*"([a-z_0-9]+)"', source))
    covered = {b[3] for b in BROKEN} | {"set_master", "variant_target", "segment_size"}
    assert emitted - covered == set()


def test_problem_has_line_and_text():
    (p,) = [x for x in lint(media([2.0, 2.6, 1.0]), TRANSCODE_CTX) if x.code == "extinf_too_long"]
    assert p.line == 9  # #EXTINF:2.600, is regel 9
    assert "regel 9" in str(p) and "TARGETDURATION 2" in str(p)


# --- de set: master plus media, met de piek uit de echte segmentgroottes ---


def test_set_peak_from_sizes():
    # 2 s per segment, 1,2 MB → 4,8 Mbit/s: meer dan de BANDWIDTH van 4.026.000
    sizes = {"0.m4s": 1_200_000, "1.m4s": 500_000, "2.m4s": 500_000, "3.m4s": 300_000}
    problems = lint_set({"master.m3u8": MASTER, "v.m3u8": SHORT}, TRANSCODE_CTX, sizes=sizes)
    assert codes(problems) == {"bandwidth_peak"}
    assert "4800000" in problems[0].message


def test_set_missing_size_and_target():
    problems = lint_set({"master.m3u8": MASTER, "v.m3u8": SHORT}, TRANSCODE_CTX, sizes={"0.m4s": 1})
    assert codes(problems) == {"segment_size"}
    assert len(problems) == 3
    problems = lint_set({"master.m3u8": MASTER, "w.m3u8": SHORT}, TRANSCODE_CTX)
    assert codes(problems) == {"variant_target"}
    problems = lint_set({"v.m3u8": SHORT}, TRANSCODE_CTX)
    assert codes(problems) == {"set_master"}


def test_set_prefixes_the_name():
    problems = lint_set({"master.m3u8": MASTER, "v.m3u8": media([2.0, 3.0])}, TRANSCODE_CTX)
    assert [p.code for p in problems] == ["extinf_too_long"]
    assert problems[0].message.startswith("v.m3u8: ")


def test_peak_window_of_rfc():
    # target 2: reeksen tussen 1 en 3 s. Eén segment van 2 s met 1 MB = 4 Mbit/s
    assert peak_bps([(2.0, 1_000_000)] * 3, 2) == 4_000_000
    # een kort laatste segment telt alleen samen met het vorige (0,4 s < 1 s)
    assert peak_bps([(2.0, 500_000), (0.4, 400_000)], 2) == 3_000_000
    # remux: target 7, één lang segment van 6,5 s
    assert peak_bps([(6.5, 6_500_000)], 7) == 8_000_000
    assert peak_bps([], 2) == 0


def test_fuzz_never_raises():
    """De lint leest onze eigen uitvoer, maar mag op niets vastlopen of crashen (vaste seed)."""
    rng = random.Random(5)
    seeds = [MASTER, SHORT, REMUX, NOAUDIO_MASTER]
    alphabet = '#EXT-:,="\n\r\t .0123456789xABCDEFabcdef?g/\ufeff\x00'
    for _ in range(600):
        text = list(rng.choice(seeds))
        for _ in range(rng.randint(1, 8)):
            i = rng.randrange(len(text) + 1)
            action = rng.random()
            if action < 0.4 and text:
                del text[min(i, len(text) - 1)]
            elif action < 0.8:
                text.insert(i, rng.choice(alphabet))
            else:
                j = rng.randrange(len(text) + 1)
                text[i:j] = []
        playlist = "".join(text)
        for ctx in (TRANSCODE_CTX, REMUX_CTX, Context(base="webos-5", has_audio=False)):
            result = lint(playlist, ctx)
            assert isinstance(result, list)
            lint(playlist.encode("utf-8", "surrogatepass"), ctx)
