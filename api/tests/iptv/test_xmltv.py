import datetime as dt
import gzip

import pytest

from app.iptv import xmltv

NOW = dt.datetime(2026, 11, 26, 18, 0, tzinfo=dt.UTC)
GUIDE = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="een.be"><display-name>Eén</display-name></channel>
  <programme start="20261126190000 +0100" stop="20261126194000 +0100" channel="een.be">
    <title lang="en">News</title><title lang="nl">Het Journaal</title>
    <desc lang="nl">Het nieuws.</desc><category>Nieuws</category>
    <episode-num system="xmltv_ns">1.4.</episode-num>
  </programme>
  <programme start="20261126194000 +0100" stop="20261126200000 +0100" channel="andere.be">
    <title>Niet in de lijst</title>
  </programme>
  <programme start="20261101190000 +0100" stop="20261101200000 +0100" channel="een.be">
    <title>Te oud</title>
  </programme>
</tv>
"""


def test_tijd():
    assert xmltv.parse_time("20261126190000 +0100") == dt.datetime(2026, 11, 26, 18, tzinfo=dt.UTC)
    assert xmltv.parse_time("20261126190000") == dt.datetime(2026, 11, 26, 19, tzinfo=dt.UTC)
    assert xmltv.parse_time("gisteren") is None


@pytest.mark.parametrize("packed", [False, True])
def test_leest_gids(tmp_path, packed):
    path = tmp_path / "guide.xml"
    data = GUIDE.encode()
    path.write_bytes(gzip.compress(data) if packed else data)
    progs = list(xmltv.read(path, {"een.be"}, now=NOW))
    assert len(progs) == 1
    p = progs[0]
    assert p.title == "Het Journaal"
    assert p.description == "Het nieuws."
    assert p.category == "Nieuws"
    assert p.episode == "S2 E5"
    assert p.start == dt.datetime(2026, 11, 26, 18, tzinfo=dt.UTC)


def test_geen_xmltv(tmp_path):
    path = tmp_path / "x.xml"
    path.write_text("<html></html>")
    with pytest.raises(xmltv.GuideError):
        list(xmltv.read(path, set(), now=NOW))
