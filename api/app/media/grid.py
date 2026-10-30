"""Het rooster van segmenten (Keuze 22, 23; spike punten 10 en 12). M4.

Eén referentie: mediatijd in ms (de tijd van de bron min de start S). De worker schrijft
mediatijd + 1 s in `tfdt`; fmp4.py trekt die seconde er al af.

**Transcode** (`grid`, types.SegmentGrid): het rooster van 2 s hangt aan het eerste videobeeld.
`origin` = de start van het videospoor in mediatijd (de probe geeft hem in hele ms, ≥ 0).
Segment 0 loopt van 0 tot `origin + 2 s`, segment k ≥ 1 van `origin + 2k`. Het aantal komt uit
het laatste videobeeld (`last_frame_ms`); een fragment voorbij N hoort bij het laatste segment.
Een herstart op k begint met `-ss (origin + 2k)`.

**Remux** (`remux_begins`): segment 0 begint op 0, segment n ≥ 1 op het eerste keyframe ≥ n·6 s
(de regel van Jellyfin). Een lange GOP geeft een langer segment. Een herstart op n begint met
`-ss (b_n − 0,5)`; ffmpeg kopieert vanaf het keyframe ervoor, en de app gooit weg wat vóór b_n
ligt.

`Layout` brengt beide samen: de begintijden, het eind, en per segment de duur (EXTINF en
map.json), het segment van een tijd, en waar een herstart begint.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Literal

from app.media.types import Probe, SegmentGrid

GRID_MS = 2000
REMUX_MS = 6000
REMUX_LEAD_S = 0.5  # -ss (b_n − 0,5) bij remux (N11)
DEFAULT_FPS = 24.0


def frame_ms(probe: Probe) -> int:
    """De duur van één videobeeld in hele ms (naar boven), voor de marges."""
    video = probe.video()
    fps = video.fps if video is not None and video.fps and video.fps > 0 else DEFAULT_FPS
    return max(1, -(-1000 // int(round(min(fps, 1000.0)))))


def grid(probe: Probe) -> SegmentGrid:
    """Het rooster van 2 s bij transcode, uit de probe (spike punten 10 en 12)."""
    video = probe.video()
    if video is None:
        raise ValueError("geen videospoor")
    origin = max(0, int(video.start_ms or 0))
    last = video.last_frame_ms
    if last is None:
        # Zonder het laatste beeld (een oude probe): de duur van het spoor min één beeld
        span = video.duration_ms if video.duration_ms is not None else probe.duration_ms
        last = origin + max(0, (span or 0) - frame_ms(probe))
    last = max(origin, int(last))
    count = SegmentGrid.count_for(origin, last, GRID_MS)
    return SegmentGrid(grid_ms=GRID_MS, origin_ms=origin, count=count, last_frame_ms=last)


def end_ms(probe: Probe, last_frame_ms: int | None = None) -> int:
    """Het eind van de stroom in mediatijd: de duur van de probe, en nooit vóór het eind van
    het laatste videobeeld (het laatste segment loopt tot het eind van de producent)."""
    video = probe.video()
    last = last_frame_ms
    if last is None and video is not None:
        last = video.last_frame_ms
    floor = (last or 0) + frame_ms(probe)
    return max(int(probe.duration_ms or 0), floor)


def remux_begins(keyframes_ms: list[int], step_ms: int = REMUX_MS) -> list[int]:
    """De begintijden bij remux (Keuze 23): 0, en dan per n het eerste keyframe ≥ n·6 s. De
    keyframes in mediatijd, oplopend (dubbele en negatieve tellen niet)."""
    keys = sorted({k for k in keyframes_ms if k >= 0})
    begins = [0]
    n = 1
    i = 0
    while True:
        target = n * step_ms
        i = bisect.bisect_left(keys, target, lo=i)
        if i >= len(keys):
            break
        k = keys[i]
        if k > begins[-1]:
            begins.append(k)
        n = max(n + 1, k // step_ms + 1)
    return begins


Mode = Literal["transcode", "remux"]


@dataclass(frozen=True)
class Layout:
    """De segmenten van één stroom: begin in mediatijd per segment en het eind."""

    mode: Mode
    begins: tuple[int, ...]
    end: int
    grid: SegmentGrid | None = None

    @property
    def count(self) -> int:
        return len(self.begins)

    def begin(self, n: int) -> int:
        return self.begins[n]

    def stop(self, n: int) -> int:
        """Het eind van segment n: het begin van het volgende, of het eind van de stroom."""
        return self.begins[n + 1] if n + 1 < len(self.begins) else self.end

    def spans(self) -> list[tuple[int, int]]:
        """(begin, duur) per segment: map.json bij remux, en EXTINF bij beide."""
        return [(b, self.stop(n) - b) for n, b in enumerate(self.begins)]

    def segment_of(self, t_ms: int) -> int:
        """Het segment van een fragment met deze pts (van het eerste sample): nooit vóór het
        eerste, nooit voorbij het laatste."""
        if self.grid is not None:
            return self.grid.segment_of(t_ms)
        return max(0, bisect.bisect_right(self.begins, t_ms) - 1)

    def start_s(self, n: int) -> float:
        """Waar de worker begint voor een run vanaf segment n (-ss vóór de invoer): bij
        transcode origin + 2n, bij remux b_n − 0,5; voor het eerste segment 0."""
        if n <= 0:
            return 0.0
        if self.mode == "transcode":
            return self.begins[n] / 1000
        return max(0.0, self.begins[n] / 1000 - REMUX_LEAD_S)


def transcode_layout(probe: Probe) -> Layout:
    g = grid(probe)
    begins = tuple(g.start_of(k) for k in range(g.count))
    return Layout("transcode", begins, end_ms(probe, g.last_frame_ms), g)


def remux_layout(probe: Probe, keyframes_ms: list[int]) -> Layout:
    begins = tuple(remux_begins(keyframes_ms))
    end = max(end_ms(probe), begins[-1] + 1)
    return Layout("remux", begins, end)
