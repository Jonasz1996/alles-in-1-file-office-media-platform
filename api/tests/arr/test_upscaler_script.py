"""De pure stukjes van infra/upscaler/upscaler.py (de container zelf draait niet in CI)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "upscaler" / "upscaler.py"
spec = importlib.util.spec_from_file_location("upscaler", SCRIPT)
assert spec is not None and spec.loader is not None
up = importlib.util.module_from_spec(spec)
sys.modules["upscaler"] = up  # dataclasses zoeken hun module op
spec.loader.exec_module(up)


def test_segments_and_tiles() -> None:
    assert up.segments(0) == 1
    assert up.segments(10) == 1
    assert up.segments(10.01) == 2
    assert up.segments(7200) == 720
    assert up.tile_for(8192) == 400
    assert up.tile_for(4096) == 256
    assert up.tile_for(3000) == 128
    assert up.tile_for(1024) == 64


def test_time_and_vram() -> None:
    assert up.time_enough(120, None)  # eerste stuk: 60 s × 1,3
    assert not up.time_enough(70, None)
    assert up.time_enough(40, 30)
    assert not up.time_enough(38, 30)
    assert up.vram_free_mb("8192, 3000\n") == 5192
    assert up.vram_free_mb("rommel") is None
