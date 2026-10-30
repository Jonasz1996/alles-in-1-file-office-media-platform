"""De playlists en map.json (M4, §2 M4 en §3.6): golden files, de lint, en map.json gelijk
aan v.m3u8."""

from __future__ import annotations

import pytest

from app.media import grid as G
from app.media import hls
from app.media.types import Probe, SegmentGrid, Stream
from tests.media import hls_lint, record


def recorded(name: str) -> Probe:
    found = record.load(name)
    assert found is not None
    return Probe.model_validate(found["data"])


def probe(*, start: int, last: int, duration: int) -> Probe:
    video = Stream(index=0, type="video", codec="h264", width=1280, height=720, fps=23.976,
                   start_ms=start, last_frame_ms=last)  # fmt: skip
    return Probe(duration_ms=duration, start_ms=-21, streams=[video])


TRANSCODE = G.transcode_layout(recorded("stream_src.mkv"))
REMUX = G.remux_layout(recorded("stream_src.mkv"), [21 + 2000 * k for k in range(10)])

GOLDEN_MASTER = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-STREAM-INF:BANDWIDTH=4026000,AVERAGE-BANDWIDTH=2610000,CODECS="avc1.64001f,mp4a.40.2",RESOLUTION=1280x720,FRAME-RATE=23.976,VIDEO-RANGE=SDR
v.m3u8
"""

GOLDEN_VARIANT = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:2
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-MAP:URI="init.mp4?g=1"
#EXTINF:2.021,
0.m4s
#EXTINF:2.000,
1.m4s
#EXTINF:2.000,
2.m4s
#EXTINF:2.000,
3.m4s
#EXTINF:2.000,
4.m4s
#EXTINF:2.000,
5.m4s
#EXTINF:2.000,
6.m4s
#EXTINF:2.000,
7.m4s
#EXTINF:2.000,
8.m4s
#EXTINF:2.000,
9.m4s
#EXT-X-ENDLIST
"""

GOLDEN_REMUX = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:7
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-MAP:URI="init.mp4?g=2"
#EXTINF:6.021,
0.m4s
#EXTINF:6.000,
1.m4s
#EXTINF:6.000,
2.m4s
#EXTINF:2.000,
3.m4s
#EXT-X-ENDLIST
"""


def variant(**kw) -> hls.Variant:
    bandwidth, average = hls.transcode_bandwidth(3500, 160)
    base = {"bandwidth": bandwidth, "average": average, "codecs": "avc1.64001f,mp4a.40.2",
            "width": 1280, "height": 720, "fps": 23.976}  # fmt: skip
    return hls.Variant(**{**base, **kw})


def test_de_master_zoals_in_het_ontwerp():
    """§2 M4: BANDWIDTH = (3500 + 160) × 1,1 = 4.026.000."""
    assert hls.master(variant(), independent=True) == GOLDEN_MASTER


def test_de_variant_van_transcode():
    assert hls.variant_playlist(TRANSCODE, 1) == GOLDEN_VARIANT


def test_de_variant_van_remux():
    assert hls.variant_playlist(REMUX, 2) == GOLDEN_REMUX


def test_webos_5_en_remux_zonder_independent_segments():
    text = hls.master(variant(), independent=False)
    assert "INDEPENDENT" not in text
    hls_lint.assert_ok(text, hls_lint.Context(mode="transcode", base="webos-5"))
    hls_lint.assert_ok(text, hls_lint.Context(mode="remux"))
    problems = hls_lint.lint(text, hls_lint.Context(mode="transcode", base="web"))
    assert [p.code for p in problems] == ["independent_missing"]


@pytest.mark.parametrize("layout", [TRANSCODE, REMUX], ids=["transcode", "remux"])
def test_de_lint_van_36(layout):
    ctx = hls_lint.Context(mode=layout.mode, base="web", has_audio=True)
    master = hls.master(variant(), independent=layout.mode == "transcode")
    playlist = hls.variant_playlist(layout, 1)
    hls_lint.assert_ok(playlist, ctx)
    sizes = {f"{n}.m4s": 400_000 for n in range(layout.count)}
    hls_lint.assert_set_ok({"master.m3u8": master, "v.m3u8": playlist}, ctx, sizes=sizes)


@pytest.mark.parametrize("layout", [TRANSCODE, REMUX], ids=["transcode", "remux"])
def test_map_json_is_v_m3u8(layout):
    """Dezelfde segmenten: de duren uit de playlist zijn de duren uit map.json, en de som tot
    n is het begin van n (mediatijd)."""
    m = hls.media_map(layout, 3, 'video/mp4; codecs="avc1.64001e"')
    parsed = hls_lint.parse(hls.variant_playlist(layout, 3))
    assert parsed is not None and parsed.segments is not None
    assert m.gen == 3 and m.init == parsed.map_uri == "init.mp4?g=3" and m.offset == -1.0
    if isinstance(m.segments, SegmentGrid):
        g = m.segments
        begins = [g.start_of(k) for k in range(g.count)]
        spans = list(zip(begins, [*begins[1:], layout.end], strict=True))
        spans = [(b, e - b) for b, e in spans]
    else:
        spans = [tuple(s) for s in m.segments]
    assert [s.duration for s in parsed.segments] == [round(d / 1000, 3) for _, d in spans]
    total = 0
    for n, (b, d) in enumerate(spans):
        assert b == total, n
        total += d
    assert total == layout.end


def test_regel_a_een_late_videostart():
    """Begint de video laat, dan is segment 0 langer: TARGETDURATION is het eerste afgerond."""
    lay = G.transcode_layout(probe(start=521, last=10479, duration=10500))
    assert lay.spans()[0] == (0, 2521)
    assert hls.target_duration(lay) == 3
    hls_lint.assert_ok(hls.variant_playlist(lay, 1), hls_lint.Context(mode="transcode"))


def test_regel_a_audio_na_het_laatste_beeld():
    """Review M0, C2: origin 21, laatste beeld 9,981 s, duur 10,6 s: het laatste segment is
    2,579 s, en TARGETDURATION 3."""
    lay = G.transcode_layout(probe(start=21, last=9981, duration=10600))
    assert lay.spans()[-1] == (8021, 2579)
    assert hls.target_duration(lay) == 3
    hls_lint.assert_ok(hls.variant_playlist(lay, 1), hls_lint.Context(mode="transcode"))


def test_regel_a_niet_met_alleen_segment_0():
    """Een laatste segment van 2,4 s rondt af op 2: TARGETDURATION blijft 2."""
    lay = G.transcode_layout(probe(start=21, last=9981, duration=10421))
    assert lay.spans()[-1][1] == 2400 and hls.target_duration(lay) == 2


def test_een_lange_gop_bij_remux():
    lay = G.remux_layout(probe(start=0, last=59958, duration=60000), [0, 25000, 50000])
    assert hls.target_duration(lay) == 25
    hls_lint.assert_ok(hls.variant_playlist(lay, 1), hls_lint.Context(mode="remux"))


def test_bandwidth_bij_remux_is_de_piek_maal_1_1():
    """De piek van RFC 8216 met de geschatte groottes: dezelfde rekensom als de lint."""
    sizes = [500_000, 1_500_000, 600_000, 100_000]
    bandwidth, average = hls.remux_bandwidth(REMUX, sizes)
    peak = hls_lint.peak_bps(
        [(round(d / 1000, 3), s) for (_, d), s in zip(REMUX.spans(), sizes, strict=True)],
        hls.target_duration(REMUX),
    )
    assert hls.peak_bps(sizes, REMUX) == peak
    assert bandwidth == -(-peak * 11 // 10)
    assert bandwidth >= peak and average <= bandwidth
    master = hls.master(variant(bandwidth=bandwidth, average=average), independent=False)
    playlist = hls.variant_playlist(REMUX, 1)
    hls_lint.assert_set_ok(
        {"master.m3u8": master, "v.m3u8": playlist},
        hls_lint.Context(mode="remux"),
        sizes={f"{n}.m4s": s for n, s in enumerate(sizes)},
    )


def test_zonder_audio_alleen_de_videocodec():
    master = hls.master(variant(codecs="avc1.64001f"), independent=True)
    hls_lint.assert_ok(master, hls_lint.Context(mode="transcode", has_audio=False))


def test_video_range_van_een_hdr_remux():
    master = hls.master(variant(codecs="hvc1.2.4.L153.B0", video_range="PQ"), independent=False)
    assert "VIDEO-RANGE=PQ" in master
    hls_lint.assert_ok(master, hls_lint.Context(mode="remux", has_audio=False))


def test_average_nooit_boven_bandwidth():
    text = hls.master(variant(bandwidth=1000, average=5000), independent=True)
    hls_lint.assert_ok(text, hls_lint.Context(mode="transcode"))
