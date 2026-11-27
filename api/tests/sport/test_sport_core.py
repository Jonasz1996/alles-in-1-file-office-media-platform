"""Sport: GPX/TCX lezen, het hoogtefilter en de cijfers (zonder database)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from app.sport import elevation, pack, track
from app.sport.parse import ParseError, parse

START = datetime(2026, 5, 3, 7, 30, tzinfo=UTC)


def gpx(points, *, name="Test", kind="running", ns=True) -> bytes:
    """Een GPX met (lat, lon, ele of None, seconden, hr of None)."""
    rows = []
    for lat, lon, ele, sec, hr in points:
        when = (START + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = f"<ele>{ele}</ele>" if ele is not None else ""
        ext = (
            f"<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{hr}</gpxtpx:hr>"
            "</gpxtpx:TrackPointExtension></extensions>"
            if hr
            else ""
        )
        rows.append(f'<trkpt lat="{lat}" lon="{lon}">{e}<time>{when}</time>{ext}</trkpt>')
    xmlns = (
        ' xmlns="http://www.topografix.com/GPX/1/1"'
        ' xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"'
        if ns
        else ' xmlns:gpxtpx="x"'
    )
    return (
        f'<?xml version="1.0"?><gpx version="1.1"{xmlns}><trk><name>{name}</name>'
        f"<type>{kind}</type><trkseg>{''.join(rows)}</trkseg></trk></gpx>"
    ).encode()


def line(n, *, speed=3.0, ele=lambda i: 100.0, hr=150):
    """n punten om de seconde naar het noorden, met speed m/s."""
    dlat = speed / 111_195.0
    return [(51.0 + i * dlat, 4.0, ele(i), i, hr) for i in range(n)]


def test_gpx_met_hartslag_en_sport():
    p = parse(gpx(line(10)))
    assert p.fmt == "gpx" and p.name == "Test" and p.sport_hint == "running"
    assert len(p.points) == 10
    assert p.points[0].hr == 150 and p.points[0].ele == 100.0
    assert p.points[1].time == START + timedelta(seconds=1)


def test_geen_gpx():
    for data in (b"<html/>", b"geen xml", gpx([])):
        try:
            parse(data)
        except ParseError:
            continue
        raise AssertionError(data)


def test_dtd_geweigerd():
    data = b'<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "b">]><gpx></gpx>'
    try:
        parse(data)
    except ParseError:
        return
    raise AssertionError


def test_tcx():
    data = (
        b'<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/'
        b'TrainingCenterDatabase/v2"><Activities><Activity Sport="Biking"><Lap><Track>'
        b"<Trackpoint><Time>2026-05-03T07:30:00Z</Time><Position><LatitudeDegrees>51"
        b"</LatitudeDegrees><LongitudeDegrees>4</LongitudeDegrees></Position><AltitudeMeters>"
        b"12</AltitudeMeters><HeartRateBpm><Value>120</Value></HeartRateBpm></Trackpoint>"
        b"<Trackpoint><Time>2026-05-03T07:30:10Z</Time><Position><LatitudeDegrees>51.001"
        b"</LatitudeDegrees><LongitudeDegrees>4</LongitudeDegrees></Position></Trackpoint>"
        b"</Track></Lap></Activity></Activities></TrainingCenterDatabase>"
    )
    p = parse(data)
    assert p.fmt == "tcx" and p.sport_hint == "Biking" and len(p.points) == 2
    assert p.points[0].hr == 120 and p.points[0].ele == 12
    assert track.guess_sport(p.sport_hint) == "ride"


def _analyse(points, sport="run"):
    raw, start = track.from_parsed(parse(gpx(points)))
    return raw, track.analyse(raw, sport)


def test_uitval_naar_nul_telt_niet_als_klimmen():
    """Het probleem van Jonas: de GPS valt weg, de hoogte zakt naar 0 en springt terug."""

    def ele(i):
        if 300 <= i < 420:  # twee minuten uitval
            return 0.0
        return 250.0 + 0.02 * i  # een lichte klim: 12 m over 600 s

    raw, a = _analyse(line(600, ele=ele))
    assert a.fixed == 120
    assert a.gain < 20, a.gain
    assert a.loss < 5, a.loss
    assert min(a.ele) > 240


def test_uitval_aan_het_begin():
    def ele(i):
        return 0.0 if i < 30 else 480.0

    raw, a = _analyse(line(300, ele=ele))
    assert a.fixed == 30
    assert a.gain < 3 and a.loss < 3
    assert abs(a.ele[0] - 480.0) < 1


def test_losse_piek_weg():
    def ele(i):
        return 900.0 if i == 100 else 50.0

    raw, a = _analyse(line(200, ele=ele))
    assert a.fixed == 1
    assert a.gain < 3


def test_echte_klim_blijft():
    """Een helling van 8 %: bij 3 m/s 0,24 m per seconde, 600 s lang (±144 m)."""
    raw, a = _analyse(line(600, ele=lambda i: 10 + 0.24 * i))
    assert a.fixed == 0
    assert 130 < a.gain < 150, a.gain


def test_klim_tijdens_uitval_blijft():
    """Vijf minuten uitval op een klim van 0,1 m/s: daarna 30 m hoger, en dat klopt."""

    def ele(i):
        if 200 <= i < 500:
            return 0.0
        return 100 + 0.1 * i

    raw, a = _analyse(line(800, ele=ele))
    assert a.fixed == 300
    assert 70 < a.gain < 85, a.gain


def test_ruis_telt_niet():
    raw, a = _analyse(line(1200, ele=lambda i: 40 + 2.0 * math.sin(i * 1.7)))
    assert a.gain < 5, a.gain


def test_filter_uit():
    def ele(i):
        return 0.0 if 50 <= i < 60 else 300.0

    raw, _ = track.from_parsed(parse(gpx(line(100, ele=ele))))
    a = track.analyse(raw, "run", ele_filter=False)
    assert a.gain > 250


def test_afstand_tijd_splits_en_beste_tijden():
    raw, a = _analyse(line(1200, speed=3.0))  # 3,6 km in 20 minuten
    assert abs(a.distance - 3597) < 10
    assert a.elapsed == 1199
    assert abs(a.moving - 1199) < 2
    assert [s.km for s in a.splits] == [1, 2, 3, 4]
    assert abs(a.splits[0].seconds - 333.3) < 2
    assert abs(a.efforts["1000"] - 333.3) < 2
    assert "5000" not in a.efforts
    assert a.avg_hr == 150


def test_stilstaan_is_geen_bewegingstijd():
    pts = line(300)
    last = pts[-1]
    pts += [(last[0], last[1], 100.0, 300 + i, 150) for i in range(1, 121)]
    raw, a = _analyse(pts)
    assert a.elapsed == 420
    assert a.moving < 310


def test_gps_sprong_weg():
    pts = line(100)
    lat, lon, e, s, hr = pts[50]
    pts[50] = (lat + 0.05, lon, e, s, hr)  # 5,5 km weg en terug in 2 seconden
    raw, a = _analyse(pts)
    assert len(raw) == 99
    assert abs(a.distance - 297) < 5


def test_sport_raden():
    assert track.guess_sport("9") == "run"
    assert track.guess_sport("cycling") == "ride"
    assert track.guess_sport("Wandelen") == "walk"
    a = track.Analysis(distance=20000, moving=2400)
    assert track.guess_sport(None, None, a) == "ride"


def test_pack_heen_en_terug():
    raw, _ = track.from_parsed(parse(gpx(line(50, ele=lambda i: None if i == 3 else 5.5))))
    back = pack.unpack(pack.pack(raw))
    assert back.lat == raw.lat and back.t == raw.t
    assert back.ele[3] is None and back.ele[4] == 5.5
    assert back.hr[0] == 150 and back.cad[0] is None


def test_gain_loss_hysterese():
    assert elevation.gain_loss([0, 1, 2, 1, 2, 1, 2]) == (0.0, 0.0)
    assert elevation.gain_loss([0, 5, 0]) == (5.0, 5.0)


def test_naam():
    assert track.default_name("run", START.replace(hour=7)) == "Ochtendloop"
    assert track.default_name("ride", START.replace(hour=19)) == "Avondrit"
