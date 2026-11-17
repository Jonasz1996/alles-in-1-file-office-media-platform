"""De treden van transcode en hun kosten (Keuze 24, 26, 28). M4.

- `ORDER`: de treden in de volgorde van Keuze 26 (de eerste die past);
- `cost(rung, hdr=)`: de cores voor 1,0× uit de tabel van Keuze 24, gemeten met een bron van
  1080p24 (SDR: H.264, HDR: HEVC10). De bron zelf (d, fps, inbranden, deinterlacen) en de
  factoren f en f_dec telt decide.cost erbij (Keuze 26);
- `maxrate_k`, `level`, `size`: wat de worker per trede nodig heeft;
- `Encoder`: de naad van Keuze 28. Nu alleen software (libx264, en libvpx-vp9 voor een client
  zonder H.264). VAAPI of QSV erbij is later één klasse hier, met een eigen kostentabel.

Een trede onder 480 (een kleine bron houdt zijn eigen hoogte) kost als 480.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.media.types import Rung, Stream
from app.media.worker.args import VideoEncode

# De volgorde van Keuze 26: de eerste die past
ORDER: tuple[Rung, ...] = (
    Rung(height=1080, preset="veryfast", codec="h264"),
    Rung(height=1080, preset="superfast", codec="h264"),
    Rung(height=720, preset="veryfast", codec="h264"),
    Rung(height=720, preset="superfast", codec="h264"),
    Rung(height=480, preset="veryfast", codec="h264"),
)

# Keuze 24: cores voor 1,0× (SDR, HDR). HDR van 1080 superfast, 720 superfast en 480 is
# geschat: de SDR-prijs × 1,65 (gemeten 1,62–1,64), afgerond op twee decimalen
TABLE: dict[tuple[int, str], tuple[float, float]] = {
    (1080, "veryfast"): (1.42, 2.30),
    (1080, "superfast"): (0.95, 1.57),
    (720, "veryfast"): (0.74, 1.21),
    (720, "superfast"): (0.62, 1.02),
    (480, "veryfast"): (0.56, 0.92),
}
HDR_FACTOR = 1.65
# De dozen van de treden: een bron past erin zonder te vergroten (Keuze 24)
BOXES: dict[int, tuple[int, int]] = {1080: (1920, 1080), 720: (1280, 720), 480: (854, 480)}
# maxrate per trede in kbit/s; bufsize is het dubbele (Keuze 24)
MAXRATE_K: dict[int, int] = {1080: 6000, 720: 3500, 480: 1500}
# Het H.264-level: (tot en met de grens in fps, erboven, de grens); 480 kantelt al boven
# 25 fps (spike punt 16)
LEVELS: dict[int, tuple[str, str, float]] = {
    1080: ("4.0", "4.2", 30.0),
    720: ("3.1", "3.2", 30.0),
    480: ("3.0", "3.1", 25.0),
}
FPS_MAX = 60  # -fpsmax 60 (Keuze 22, V5)
MIN_SIDE = 64  # de kleinste maat die de worker aanneemt (VideoEncode)


def box(height: int) -> int:
    """De trede van de tabel bij een hoogte: 1080, 720, of 480 (ook voor alles eronder)."""
    if height >= 1080:
        return 1080
    if height >= 720:
        return 720
    return 480


def cost(rung: Rung, *, hdr: bool = False) -> float:
    """De cores voor 1,0× uit de tabel van Keuze 24 (een bron van 1080p24). VP9 kost hetzelfde
    als x264 (V3, S11); decide vraagt het daarom altijd met codec h264."""
    sdr, hdr_cost = TABLE[(box(rung.height), rung.preset)]
    return hdr_cost if hdr else sdr


def maxrate_k(height: int) -> int:
    return MAXRATE_K[box(height)]


def level(height: int, fps: float) -> str:
    """Het H.264-level van de trede (Keuze 24, spike punt 16)."""
    low, high, limit = LEVELS[box(height)]
    return high if min(fps, FPS_MAX) > limit else low


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def size(height: int, width_src: int | None, height_src: int | None) -> tuple[int, int]:
    """De maat van de uitvoer: de bron in de doos van de trede, met de beeldverhouding van de
    bron, nooit vergroot, en even. Een trede onder 480 is de bron zelf (even)."""
    h_src = height_src or height
    w_src = width_src or round(h_src * 16 / 9)
    if height < 480:
        scale = 1.0
    else:
        box_w, box_h = BOXES[box(height)]
        scale = min(1.0, box_w / w_src, box_h / h_src)
    return max(MIN_SIDE, _even(w_src * scale)), max(MIN_SIDE, _even(h_src * scale))


@dataclass(frozen=True)
class Encoder:
    """De naad van Keuze 28: van een trede en een bron naar wat de worker codeert. Software:
    libx264, of libvpx-vp9 voor een client zonder H.264."""

    name: str = "software"

    def video(
        self,
        rung: Rung,
        source: Stream,
        *,
        tonemap: str | None = None,
        burn_index: int | None = None,
        burn_start_s: float = 0.0,
    ) -> VideoEncode:
        fps = min(source.fps or 24.0, FPS_MAX)
        w, h = size(rung.height, source.width, source.height)
        h264 = rung.codec == "h264"
        return VideoEncode(
            codec="libx264" if h264 else "libvpx-vp9",
            width=w,
            height=h,
            preset=rung.preset,
            level=level(rung.height, fps) if h264 else None,  # type: ignore[arg-type]
            maxrate_k=maxrate_k(rung.height),
            fps_max=FPS_MAX,
            tonemap=tonemap,  # type: ignore[arg-type]
            deinterlace=source.interlaced,
            burn_index=burn_index,
            burn_start_s=round(burn_start_s, 3),
        )


SOFTWARE = Encoder()


def encoder() -> Encoder:
    """De encoder van deze installatie: software (Keuze 28: VAAPI is klaargezet, niet
    gebouwd)."""
    return SOFTWARE
