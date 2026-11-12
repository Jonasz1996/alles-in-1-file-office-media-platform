"""De treden, de kostentabel en de naad Encoder (M4, Keuze 24, 26, 28)."""

from __future__ import annotations

import pytest

from app.media import decide, ladder
from app.media.types import Rung, Stream
from app.media.worker.args import VideoEncode


def rung(height: int, preset: str = "veryfast", codec: str = "h264") -> Rung:
    return Rung(height=height, preset=preset, codec=codec)  # type: ignore[arg-type]


def video(**kw) -> Stream:
    base = {"index": 0, "type": "video", "codec": "h264", "width": 1920, "height": 1080,
            "fps": 24.0}  # fmt: skip
    return Stream(**{**base, **kw})


def test_de_volgorde_van_keuze_26():
    assert [(r.height, r.preset) for r in ladder.ORDER] == [
        (1080, "veryfast"),
        (1080, "superfast"),
        (720, "veryfast"),
        (720, "superfast"),
        (480, "veryfast"),
    ]


@pytest.mark.parametrize(
    ("height", "preset", "sdr", "hdr"),
    [
        (1080, "veryfast", 1.42, 2.30),
        (1080, "superfast", 0.95, 1.57),
        (720, "veryfast", 0.74, 1.21),
        (720, "superfast", 0.62, 1.02),
        (480, "veryfast", 0.56, 0.92),
    ],
)
def test_de_tabel_van_keuze_24(height, preset, sdr, hdr):
    assert ladder.cost(rung(height, preset)) == sdr
    assert ladder.cost(rung(height, preset), hdr=True) == hdr
    # VP9 kost hetzelfde als x264 (V3, S11)
    assert ladder.cost(rung(height, preset, "vp9")) == sdr


def test_hdr_geschat_is_de_sdr_prijs_maal_1_65():
    for key in ((1080, "superfast"), (720, "superfast"), (480, "veryfast")):
        sdr, hdr = ladder.TABLE[key]
        assert hdr == round(sdr * ladder.HDR_FACTOR, 2)


def test_een_trede_onder_480_kost_als_480():
    assert ladder.cost(rung(360)) == ladder.cost(rung(480))
    assert ladder.box(270) == 480 and ladder.box(719) == 480 and ladder.box(2160) == 1080


def test_decide_rekent_met_de_tabel_van_ladder():
    """Keuze 26, het rekenvoorbeeld: 1080 veryfast van een 1080p24 H.264-bron met f = 1."""
    from app.media.types import BudgetView

    budget = BudgetView(budget=3.0, in_use=0, places_free=3, user_free=2)
    got = decide.cost(rung(1080), video(), hdr=False, burn=False, budget=budget)
    assert got == pytest.approx(1.42, abs=0.01)


@pytest.mark.parametrize(
    ("height", "fps", "level"),
    [
        (1080, 24, "4.0"),
        (1080, 30, "4.0"),
        (1080, 50, "4.2"),
        (720, 30, "3.1"),
        (720, 60, "3.2"),
        (480, 25, "3.0"),
        (480, 29.97, "3.1"),
        (360, 24, "3.0"),
        (1080, 240, "4.2"),  # -fpsmax 60
    ],
)
def test_het_level_per_trede(height, fps, level):
    assert ladder.level(height, fps) == level
    # Gelijk aan wat decide in de mime zet (de CODECS van de playlist)
    want = decide.h264_string(height if height >= 480 else 480, min(fps, 60))
    assert want == f"avc1.6400{int(level.replace('.', '')):02x}"


@pytest.mark.parametrize(
    ("height", "src", "out"),
    [
        (1080, (1920, 1080), (1920, 1080)),
        (720, (1920, 1080), (1280, 720)),
        (480, (1920, 1080), (854, 480)),
        (720, (1920, 800), (1280, 534)),  # scope: de breedte telt
        (1080, (3840, 2160), (1920, 1080)),
        (720, (1280, 720), (1280, 720)),
        (1080, (1280, 720), (1280, 720)),  # nooit vergroten
        (480, (720, 576), (600, 480)),  # PAL 4:3 (zonder SAR)
        (360, (640, 360), (640, 360)),  # onder 480: de bron zelf
        (480, (1440, 1080), (640, 480)),
    ],
)
def test_de_maat(height, src, out):
    w, h = ladder.size(height, *src)
    assert (w, h) == out
    assert w % 2 == 0 and h % 2 == 0


def test_de_encoder_maakt_een_geldige_videoencode():
    enc = ladder.encoder().video(rung(720), video(fps=50.0))
    assert isinstance(enc, VideoEncode)
    assert (enc.codec, enc.width, enc.height, enc.level, enc.maxrate_k) == (
        "libx264", 1280, 720, "3.2", 3500,
    )  # fmt: skip
    assert enc.fps_max == 60 and enc.tonemap is None and not enc.deinterlace

    vp9 = ladder.encoder().video(rung(720, codec="vp9"), video(interlaced=True), tonemap="pq")
    assert vp9.codec == "libvpx-vp9" and vp9.level is None
    assert vp9.tonemap == "pq" and vp9.deinterlace

    burn = ladder.encoder().video(rung(480), video(), burn_index=3, burn_start_s=12.34567)
    assert burn.burn_index == 3 and burn.burn_start_s == 12.346


def test_maxrate_per_trede():
    assert [ladder.maxrate_k(h) for h in (1080, 720, 480, 360)] == [6000, 3500, 1500, 1500]
