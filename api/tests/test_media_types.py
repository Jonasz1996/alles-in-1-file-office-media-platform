"""Het rooster van map.json en EXTINF (app/media/types.py SegmentGrid; Keuze 22, spike punten 10
en 12, en "Extra" in docs/fase-5/spike.md). web/src/media/api.ts rekent precies zo
(segmentStart, segmentOf); de tests daar gebruiken dezelfde getallen."""

import pytest
from pydantic import ValidationError

from app.media.types import Card, MediaHome, SegmentGrid


def test_video_vanaf_het_begin():
    # Video op 0, audio op −0,021 (S = −0,021): origin 21 ms, segment 0 is 2,021 s
    grid = SegmentGrid(origin_ms=21, count=3, last_frame_ms=5980)
    assert [grid.start_of(k) for k in range(3)] == [0, 2021, 4021]
    assert grid.segment_of(0) == 0
    assert grid.segment_of(2020) == 0
    assert grid.segment_of(2021) == 1
    assert grid.segment_of(4021) == 2
    # Een fragment voorbij count hoort bij het laatste segment, een tijd ervoor bij het eerste
    assert grid.segment_of(6100) == 2
    assert grid.segment_of(-50) == 0


def test_een_late_videostart_maakt_segment_0_langer():
    # Video op 0,500 en S = −0,021: origin 521 (spike.md, "Extra")
    grid = SegmentGrid(origin_ms=521, count=5, last_frame_ms=9479)
    assert [grid.start_of(k) for k in range(5)] == [0, 2521, 4521, 6521, 8521]
    assert grid.segment_of(2520) == 0
    assert grid.segment_of(2521) == 1
    assert grid.segment_of(9479) == 4


def test_het_aantal_komt_uit_het_laatste_videobeeld():
    assert SegmentGrid.count_for(21, 5980) == 3
    assert SegmentGrid.count_for(21, 2020) == 1
    assert SegmentGrid.count_for(21, 2021) == 2
    assert SegmentGrid.count_for(521, 9479) == 5
    assert SegmentGrid.count_for(0, 0) == 1
    # Een film van 2 uur op 24 fps (§2 M4, Keuze 22)
    assert SegmentGrid.count_for(21, 7_199_979) == 3600


def test_origin_is_nooit_negatief():
    with pytest.raises(ValidationError):
        SegmentGrid(origin_ms=-400, count=3, last_frame_ms=5000)
    with pytest.raises(ValidationError):
        SegmentGrid(count=0, last_frame_ms=0)


def test_de_kaart_heeft_de_vorm_van_paragraaf_2():
    # Card = {node_id, title, year, kind, poster, progress, watched}, plus de bibliotheek
    card = Card(node_id=None, title="Serie", kind="show")
    assert card.model_dump(mode="json") == {
        "node_id": None,
        "library_id": None,
        "title": "Serie",
        "year": None,
        "kind": "show",
        "poster": None,
        "progress": None,
        "watched": False,
        "show_id": None,
        "season": None,
        "episode": None,
    }
    # continue is een sleutelwoord in Python, maar heet zo in de API
    assert set(MediaHome().model_dump(mode="json")) == {
        "libraries",
        "continue",
        "next_up",
        "recent",
    }
