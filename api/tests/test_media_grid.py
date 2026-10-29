"""Het rooster van 2 s en het rooster uit keyframes (M4, Keuze 22, 23; spike punten 10 en 12)."""

from __future__ import annotations

import pytest

from app.media import grid as G
from app.media.types import Probe, SegmentGrid, Stream
from tests.media import record


def probe(*, start: int = 21, last: int | None = 19979, duration: int = 20021, fps=24.0) -> Probe:
    video = Stream(index=0, type="video", codec="h264", width=640, height=360, fps=fps,
                   start_ms=start, last_frame_ms=last, duration_ms=duration - start)  # fmt: skip
    audio = Stream(index=1, type="audio", codec="aac", start_ms=0, duration_ms=duration)
    return Probe(container="matroska", duration_ms=duration, start_ms=-21, streams=[video, audio])


def test_het_rooster_van_de_bron_van_de_nep():
    """stream_src.mkv: origin 21, laatste beeld 19,979 s: 10 segmenten, het eerste 2,021 s."""
    found = record.load("stream_src.mkv")
    assert found is not None
    lay = G.transcode_layout(Probe.model_validate(found["data"]))
    assert lay.grid == SegmentGrid(grid_ms=2000, origin_ms=21, count=10, last_frame_ms=19979)
    spans = lay.spans()
    assert spans[0] == (0, 2021) and spans[1] == (2021, 2000) and spans[-1] == (18021, 2000)
    assert lay.end == 20021
    assert [lay.start_s(k) for k in (0, 1, 5)] == [0.0, 2.021, 10.021]


@pytest.mark.parametrize(
    ("last", "count"),
    [
        (21, 1),  # één beeld
        (2020, 1),  # net vóór de grens van segment 1
        (2021, 2),  # het beeld op de grens begint segment 1
        (2022, 2),
        (19979, 10),
        (20021, 11),  # een beeld precies op origin + 20 s
    ],
)
def test_n_komt_uit_het_laatste_beeld(last, count):
    g = G.grid(probe(last=last, duration=max(20021, last + 42)))
    assert g.count == count == SegmentGrid.count_for(21, last)


def test_een_late_videostart():
    """late_video.mkv: de video begint 0,5 s na het geluid (origin 521): segment 0 is 2,521 s
    lang, en een herstart op k begint op origin + 2k (spike.md, "Extra")."""
    g = G.grid(probe(start=521, last=10479, duration=10500))
    lay = G.transcode_layout(probe(start=521, last=10479, duration=10500))
    assert g.origin_ms == 521 and g.count == 5
    assert lay.spans()[0] == (0, 2521)
    assert lay.start_s(1) == 2.521 and lay.start_s(4) == 8.521


def test_een_fragment_voorbij_n_hoort_bij_het_laatste_segment():
    lay = G.transcode_layout(probe())
    assert lay.segment_of(18021) == 9
    assert lay.segment_of(20021) == 9  # voorbij het laatste beeld (audio na de video)
    assert lay.segment_of(-5) == 0 and lay.segment_of(0) == 0 and lay.segment_of(2020) == 0
    assert lay.segment_of(2021) == 1


def test_het_eind_loopt_tot_het_laatste_beeld_of_de_duur():
    # De duur van het formaat is korter dan het laatste beeld + één beeld: het beeld telt
    assert G.end_ms(probe(last=19979, duration=19990)) == 19979 + 42
    assert G.end_ms(probe(last=19979, duration=20600)) == 20600


def test_zonder_laatste_beeld_de_duur_van_het_spoor():
    g = G.grid(probe(last=None, duration=20021))
    assert g.last_frame_ms == 21 + 20000 - 42 and g.count == 10


def test_een_probe_zonder_video_heeft_geen_rooster():
    with pytest.raises(ValueError):
        G.grid(Probe(duration_ms=1000, streams=[Stream(index=0, type="audio")]))


# --- remux (Keuze 23) ---------------------------------------------------------------------------


def test_de_regel_van_6_s():
    keys = [21 + 2000 * i for i in range(10)]
    assert G.remux_begins(keys) == [0, 6021, 12021, 18021]


def test_lange_gops_geven_langere_segmenten():
    """long_gop.mkv: een keyframe elke 25 s (600 beelden op 24 fps)."""
    keys = [0, 25000, 50000]
    assert G.remux_begins(keys) == [0, 25000, 50000]
    lay = G.remux_layout(probe(start=0, last=59958, duration=60000), keys)
    assert lay.spans() == [(0, 25000), (25000, 25000), (50000, 10000)]


def test_onregelmatige_keyframes():
    """irregular_gop.mkv: 0, 1,5, 2,2, 7,9, 8,3, 14 en 19 s."""
    keys = [0, 1500, 2200, 7900, 8300, 14000, 19000]
    assert G.remux_begins(keys) == [0, 7900, 14000, 19000]


def test_de_laatste_keyframe_en_dubbele():
    assert G.remux_begins([0, 6000, 6000, 12000]) == [0, 6000, 12000]
    assert G.remux_begins([]) == [0]
    assert G.remux_begins([5999]) == [0]  # geen keyframe ≥ 6 s
    assert G.remux_begins([-40, 0, 7000]) == [0, 7000]


def test_een_extra_keyframe_midden_in_een_segment_blijft_daarin():
    keys = [0, 2000, 3100, 4000, 6000, 8000]
    lay = G.remux_layout(probe(start=0, last=9958, duration=10000), keys)
    assert lay.begins == (0, 6000)
    assert lay.segment_of(3100) == 0 and lay.segment_of(4000) == 0
    assert lay.segment_of(6000) == 1 and lay.segment_of(5999) == 0


def test_b_frames_tellen_op_de_pts_van_het_eerste_sample():
    """Met B-frames ligt tfdt (de dts) vóór de pts van het keyframe; de app groepeert op de
    pts (tfdt + de getekende offset), dus een fragment met dts 5958 en pts 6000 hoort bij het
    segment dat op 6000 begint."""
    lay = G.remux_layout(probe(start=0, last=11958, duration=12000), [0, 6000])
    tfdt, cto = 5958, 42
    assert lay.segment_of(tfdt) == 0
    assert lay.segment_of(tfdt + cto) == 1


def test_een_herstart_bij_remux_begint_een_halve_seconde_eerder():
    lay = G.remux_layout(probe(), [21 + 2000 * i for i in range(10)])
    assert lay.start_s(0) == 0.0
    assert lay.start_s(1) == pytest.approx(6.021 - 0.5)
    assert lay.start_s(3) == pytest.approx(18.021 - 0.5)


def test_het_eind_van_remux_ligt_altijd_na_het_laatste_begin():
    lay = G.remux_layout(probe(last=6000, duration=6000), [0, 6000])
    assert lay.end > lay.begins[-1]
    assert all(d > 0 for _, d in lay.spans())
