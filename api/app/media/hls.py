"""De playlists (`master.m3u8`, `v.m3u8`) en `map.json` (Keuze 22, 23; §2 M4). M4.

Eén bron voor alle drie: de `grid.Layout` van de sessie. Zo beschrijven `map.json` en `v.m3u8`
altijd dezelfde segmenten, en is de som van de duren tot segment n het begin van n.

- `EXTINF` met drie decimalen, in mediatijd;
- `TARGETDURATION`: bij transcode `max(2, rond(eerste), rond(laatste))` (regel A: het eerste
  segment loopt tot `origin + 2`, het laatste tot het eind van de producent; elk segment
  ertussen is 2 s). Bij remux het langste segment naar boven afgerond;
- `EXT-X-INDEPENDENT-SEGMENTS` alleen bij transcode (elk segment begint met een IDR), en niet
  als het basisprofiel het afzet (`profile.flags`, webOS 5). Bij remux nooit: de bron kan open
  GOP's hebben;
- in de master precies één variant: alleen de trede van de sessie;
- `BANDWIDTH`: bij transcode (maxrate + audio) × 1,1; bij remux de piek uit de index (de
  grootste bitrate over aaneengesloten segmenten van samen 0,5–1,5 × `TARGETDURATION`, zoals
  RFC 8216 §4.3.4.2 hem meet) × 1,1. `AVERAGE-BANDWIDTH` is een schatting, nooit boven
  `BANDWIDTH`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from app.media.grid import Layout
from app.media.types import MediaMap

VERSION = 7
MARGIN = (11, 10)  # BANDWIDTH = de schatting × 1,1 (als breuk: geen afrondfout van floats)
AVERAGE_SHARE = 0.7  # AVERAGE-BANDWIDTH bij transcode: 70 % van maxrate, plus de audio
OFFSET = -1.0  # timestampOffset van de speler: tfdt = mediatijd + 1 s

VideoRange = Literal["SDR", "PQ", "HLG"]


def _round(value: float) -> int:
    """Afronden zoals RFC 8216 §4.3.3.1 (.5 naar boven)."""
    return math.floor(value + 0.5)


def extinf(duration_ms: int) -> str:
    return f"{duration_ms / 1000:.3f}"


def target_duration(layout: Layout) -> int:
    durations = [d for _, d in layout.spans()]
    if layout.mode == "transcode":
        return max(2, _round(durations[0] / 1000), _round(durations[-1] / 1000))
    return max(1, math.ceil(max(durations) / 1000 - 1e-9))


def peak_bps(sizes: list[int], layout: Layout) -> int:
    """De piek van RFC 8216 §4.3.4.2 over de segmenten van layout met deze groottes (bytes):
    de hoogste bitrate van aaneengesloten segmenten met samen 0,5–1,5 × TARGETDURATION."""
    target = target_duration(layout)
    low, high = 0.5 * target, 1.5 * target
    spans = layout.spans()
    best = 0.0
    for i in range(len(spans)):
        seconds = 0.0
        total = 0
        for (_b, d), size in zip(spans[i:], sizes[i:], strict=False):
            seconds += round(d / 1000, 3)
            total += size
            if seconds > high + 1e-9:
                break
            if seconds >= low - 1e-9 and seconds > 0:
                best = max(best, 8 * total / seconds)
    if best == 0 and spans:  # één kort segment: zijn eigen bitrate
        seconds = sum(d for _, d in spans) / 1000
        best = 8 * sum(sizes) / max(seconds, 0.001)
    return math.ceil(best)


@dataclass(frozen=True)
class Variant:
    """De ene variant van de master: de trede van de sessie."""

    bandwidth: int  # bit/s
    average: int  # bit/s
    codecs: str  # "avc1.64001f,mp4a.40.2", zoals in de mime
    width: int
    height: int
    fps: float
    video_range: VideoRange = "SDR"


def _margin(bps: int) -> int:
    """× 1,1, naar boven, in hele getallen."""
    up, down = MARGIN
    return -(-bps * up // down)


def transcode_bandwidth(maxrate_k: int, audio_k: int) -> tuple[int, int]:
    """(BANDWIDTH, AVERAGE-BANDWIDTH) bij transcode: (maxrate + audio) × 1,1, en een schatting
    uit de trede."""
    bandwidth = _margin((maxrate_k + audio_k) * 1000)
    average = min(bandwidth, round((maxrate_k * AVERAGE_SHARE + audio_k) * 1000))
    return bandwidth, max(1, average)


def remux_bandwidth(layout: Layout, sizes: list[int]) -> tuple[int, int]:
    """(BANDWIDTH, AVERAGE-BANDWIDTH) bij remux, uit de geschatte grootte per segment (de
    index plus het geluid)."""
    bandwidth = max(1, _margin(peak_bps(sizes, layout)))
    seconds = max(0.001, layout.end / 1000)
    average = min(bandwidth, max(1, math.ceil(8 * sum(sizes) / seconds)))
    return bandwidth, average


def master(variant: Variant, *, independent: bool) -> str:
    lines = ["#EXTM3U", f"#EXT-X-VERSION:{VERSION}"]
    if independent:
        lines.append("#EXT-X-INDEPENDENT-SEGMENTS")
    lines.append(
        f"#EXT-X-STREAM-INF:BANDWIDTH={variant.bandwidth},"
        f"AVERAGE-BANDWIDTH={min(variant.average, variant.bandwidth)},"
        f'CODECS="{variant.codecs}",RESOLUTION={variant.width}x{variant.height},'
        f"FRAME-RATE={variant.fps:.3f},VIDEO-RANGE={variant.video_range}"
    )
    lines.append("v.m3u8")
    return "\n".join(lines) + "\n"


def init_uri(gen: int) -> str:
    return f"init.mp4?g={gen}"


def variant_playlist(layout: Layout, gen: int) -> str:
    lines = [
        "#EXTM3U",
        f"#EXT-X-VERSION:{VERSION}",
        f"#EXT-X-TARGETDURATION:{target_duration(layout)}",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-MEDIA-SEQUENCE:0",
        f'#EXT-X-MAP:URI="{init_uri(gen)}"',
    ]
    for n, (_b, d) in enumerate(layout.spans()):
        lines += [f"#EXTINF:{extinf(d)},", f"{n}.m4s"]
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def media_map(layout: Layout, gen: int, mime: str) -> MediaMap:
    """map.json: het rooster bij transcode, [begin, duur] per segment bij remux."""
    segments = layout.grid if layout.grid is not None else [tuple(s) for s in layout.spans()]
    return MediaMap(gen=gen, mime=mime, offset=OFFSET, init=init_uri(gen), segments=segments)
