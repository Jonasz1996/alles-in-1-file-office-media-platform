import pytest

from app.iptv import upstream

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000
laag/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000
hoog/index.m3u8
"""
MEDIA = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:100
#EXTINF:6.0,
a.ts
#EXTINF:6.0,
b.ts
"""


def test_variant_en_stukken():
    base = "http://aanbieder/kanaal/master.m3u8"
    assert upstream.pick_variant(MASTER, base) == "http://aanbieder/kanaal/hoog/index.m3u8"
    media = upstream.parse_media(MEDIA, "http://aanbieder/kanaal/hoog/index.m3u8")
    assert media.target == 6.0
    assert media.segments == [
        (100, "http://aanbieder/kanaal/hoog/a.ts"),
        (101, "http://aanbieder/kanaal/hoog/b.ts"),
    ]
    assert not media.ended


def test_versleuteld_en_fmp4_weigeren():
    with pytest.raises(upstream.UpstreamError):
        upstream.parse_media('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n', "http://x/")
    with pytest.raises(upstream.UpstreamError):
        upstream.parse_media('#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n', "http://x/")
