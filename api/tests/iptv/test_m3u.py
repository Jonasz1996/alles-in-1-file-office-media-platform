import pytest

from app.iptv import m3u

EEN = (
    '#EXTINF:-1 tvg-id="een.be" tvg-name="Een" tvg-logo="https://logo.example/een.png" '
    'tvg-chno="1" group-title="BE, Vlaams",Eén HD'
)
LIST = f"""#EXTM3U url-tvg="https://gids.example/guide.xml"
{EEN}
http://aanbieder:8080/live/gebruiker/geheim/1.ts
#EXTINF:-1 tvg-id="canvas.be" group-title="BE",Canvas
#EXTVLCOPT:http-user-agent=Kodi/20
http://aanbieder:8080/live/gebruiker/geheim/2.ts
#EXTINF:-1,Radio via udp
udp://239.0.0.1:1234
#EXTINF:-1 tvg-id="canvas.be" group-title="BE",Canvas
https://aanbieder/hls/canvas.m3u8
"""


def test_leest_kanalen():
    pl = m3u.parse(LIST)
    assert pl.guide_url == "https://gids.example/guide.xml"
    assert pl.skipped == 1
    assert [e.name for e in pl.entries] == ["Eén HD", "Canvas", "Canvas"]
    een = pl.entries[0]
    assert een.tvg_id == "een.be"
    assert een.group == "BE, Vlaams"
    assert een.number == 1
    assert een.logo == "https://logo.example/een.png"
    assert pl.entries[1].user_agent == "Kodi/20"
    # dezelfde naam en tvg-id twee keer: een andere, vaste sleutel
    assert pl.entries[1].key != pl.entries[2].key
    assert m3u.parse(LIST).entries[2].key == pl.entries[2].key


def test_geen_m3u_zegt_niets_van_de_inhoud():
    with pytest.raises(m3u.M3UError) as exc:
        m3u.parse("http://aanbieder/live/gebruiker/geheim/1.ts")
    assert "geheim" not in str(exc.value)
    with pytest.raises(m3u.M3UError):
        m3u.parse("#EXTM3U\n#EXTINF:-1,Alleen udp\nudp://x\n")
