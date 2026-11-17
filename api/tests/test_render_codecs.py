"""Wat het renderproces (app/render.py) in fase 3 extra vastlegt: codecs van video en audio,
of een afbeelding beweegt, 2048 px voor HEIC/HEIF/TIFF en de tekencodering van tekst."""

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app import render
from tests.test_previews import picture, run_render

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg niet geïnstalleerd"
)


def ffmpeg(path: Path, *args: str) -> Path:
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:"
         "rate=10", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", *args, "-y",
         str(path)],
        check=True,
    )  # fmt: skip
    return path


@needs_ffmpeg
def test_codecs_van_mp4_en_webm(tmp_path):
    mp4 = ffmpeg(tmp_path / "a.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac")
    result, _ = run_render(tmp_path, mp4, "a.mp4", mime="video/mp4")
    meta = result["meta"]
    assert meta["video"] == {"codec": "h264", "profile": meta["video"]["profile"],
                             "pix_fmt": "yuv420p"}  # fmt: skip
    assert meta["video"]["profile"]  # bijv. "High"
    assert meta["audio"]["codec"] == "aac" and meta["audio"]["channels"] == 1
    assert meta["audio"]["sample_rate"] > 0
    assert "mp4" in meta["container"]

    webm = ffmpeg(tmp_path / "a.webm", "-c:v", "libvpx-vp9", "-c:a", "libopus", "-b:v", "100k")
    result, _ = run_render(tmp_path, webm, "a.webm", mime="video/webm")
    meta = result["meta"]
    assert meta["video"]["codec"] == "vp9" and meta["audio"]["codec"] == "opus"
    assert "webm" in meta["container"]


def test_codecs_uit_een_kale_probe():
    probe = {
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "profile": "Main 10", "pix_fmt": None},
            {"codec_type": "audio", "codec_name": "ac3", "channels": "6", "sample_rate": "48000"},
        ],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }
    assert render.codecs_of(probe) == {
        "video": {"codec": "hevc", "profile": "Main 10"},
        "audio": {"codec": "ac3", "channels": 6, "sample_rate": 48000},
        "container": "mov,mp4,m4a,3gp,3g2,mj2",
    }
    assert render.codecs_of({}) == {}


def animated_gif() -> bytes:
    frames = [Image.new("RGB", (40, 30), c) for c in ("red", "blue", "green")]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buf.getvalue()


def test_geanimeerde_gif_en_webp(tmp_path):
    result, _ = run_render(tmp_path, animated_gif(), "a.gif")
    assert result["meta"]["animated"] is True
    result, _ = run_render(tmp_path, picture("GIF", (40, 30), "P"), "b.gif")
    assert result["meta"]["animated"] is False
    frames = [Image.new("RGB", (40, 30), c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], duration=100)
    result, _ = run_render(tmp_path, buf.getvalue(), "a.webp")
    assert result["meta"]["animated"] is True


def test_tiff_krijgt_2048(tmp_path):
    result, made = run_render(tmp_path, picture("TIFF", (3000, 2000)), "scan.tiff")
    assert made == {2048: (2048, 1365), 1024: (1024, 683), 256: (256, 171)}
    assert result["sizes"] == [2048, 1024, 256]
    # Een JPEG niet: die kan de browser zelf tonen
    _, made = run_render(tmp_path, picture("JPEG", (3000, 2000)), "foto.jpg")
    assert sorted(made) == [256, 1024]
    # Niet groter dan 1024: geen 2048 (dat zou alleen vergroten)
    _, made = run_render(tmp_path, picture("TIFF", (900, 600)), "klein.tif")
    assert sorted(made) == [256, 1024]


def test_heic_krijgt_2048(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    buf = io.BytesIO()
    try:
        pillow_heif.from_pillow(Image.new("RGB", (2400, 1800), "red")).save(buf, quality=30)
    except Exception as e:  # noqa: BLE001 - zonder encoder kan het testbestand niet gemaakt
        pytest.skip(f"Geen HEIC-encoder: {e}")
    _, made = run_render(tmp_path, buf.getvalue(), "IMG_1.HEIC")
    assert made[2048] == (2048, 1536) and made[1024] == (1024, 768)


@pytest.mark.parametrize(
    ("data", "encoding", "text"),
    [
        ("Café crème".encode("cp1252"), "windows-1252", "Café crème"),
        ("Café crème".encode(), "utf-8", "Café crème"),
        (b"\xef\xbb\xbfmet BOM", "utf-8", "met BOM"),
        ("tweeën".encode("utf-16"), "utf-16le", "tweeën"),
        (b"\xfe\xff" + "big".encode("utf-16-be"), "utf-16be", "big"),
        (b"\x80 euro in cp1252", "windows-1252", "€ euro in cp1252"),
    ],
)
def test_tekstcodering(tmp_path, data, encoding, text):
    result, _ = run_render(tmp_path, data, "a.txt", mime="text/plain")
    assert result["meta"]["encoding"] == encoding
    assert result["meta"]["text"] == text
