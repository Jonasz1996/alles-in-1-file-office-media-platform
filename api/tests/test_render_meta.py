"""EXIF en opnamedatum lezen (app/render_meta.py): camera, objectief, belichting en datum. De
plek alleen in mode "meta" (VERSION 2, Keuze 25), nooit voor de preview; de plek zelf staat in
tests/geo/test_geo_read.py."""

import io
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image, TiffImagePlugin

from app import photodate, render, render_meta
from tests.test_previews import needs_ffmpeg, run_render

TZ = "Europe/Amsterdam"
GPS_IFD = 0x8825


def camera_jpeg(
    taken: str | None = "2024:07:01 12:30:05",
    *,
    offset: str | None = None,
    gps: bool = True,
    size=(64, 48),
    color="red",
) -> bytes:
    """Een JPEG zoals uit een telefoon: merk, model, objectief, belichting, datum en GPS."""
    exif = Image.Exif()
    exif[0x010F] = "Fabrikant\x00"
    exif[0x0110] = "  Camera X1 "
    exif[0x0131] = "Firmware 1.0"
    exif[0x0112] = 1
    sub = exif.get_ifd(0x8769)
    if taken is not None:
        sub[0x9003] = taken
    if offset is not None:
        sub[0x9011] = offset
    sub[0x829A] = TiffImagePlugin.IFDRational(1, 250)
    sub[0x829D] = TiffImagePlugin.IFDRational(28, 10)
    sub[0x8827] = 200
    sub[0x920A] = TiffImagePlugin.IFDRational(42, 10)
    sub[0xA405] = 26
    sub[0x9209] = 16
    sub[0x9204] = TiffImagePlugin.IFDRational(-1, 3)
    sub[0xA434] = "Lens 4.2mm f/2.8"
    if gps:
        info = exif.get_ifd(GPS_IFD)
        info[1] = "N"
        info[2] = (52.0, 22.0, 1.0)
        info[3] = "E"
        info[4] = (4.0, 53.0, 2.0)
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def meta_of(data: bytes) -> dict:
    with Image.open(io.BytesIO(data)) as img:
        return render_meta.image_meta(img)


# De coördinaten van camera_jpeg() (52.0 en 22.0) als losse getallen; niet de "22.0" in een
# tijdstip als "09:33:22.006450Z" of in 1022.0
COORDINATE = re.compile(r"(?<![\d:.])(52|22)\.0(?!\d)")


def no_gps(value) -> None:
    """Nergens een GPS-waarde: geen sleutel gps/lat/lon/location, geen coördinaten."""
    text = json.dumps(value).lower()
    for word in ("gps", "latitude", "longitude", '"lat"', '"lon"', "location"):
        assert word not in text, (word, value)
    found = COORDINATE.search(text)
    assert found is None, (found and found.group(0), value)


def test_exif_uit_een_jpeg():
    meta = meta_of(camera_jpeg())
    assert meta["taken_at"] == "2024-07-01T12:30:05" and meta["taken_kind"] == "local"
    exif = meta["exif"]
    assert exif == {
        "make": "Fabrikant",
        "model": "Camera X1",
        "lens": "Lens 4.2mm f/2.8",
        "software": "Firmware 1.0",
        "fnumber": 2.8,
        "exposure": 0.004,
        "iso": 200,
        "focal": 4.2,
        "focal35": 26,
        "flash": 16,
        "bias": pytest.approx(-1 / 3),
        "orientation": 1,
    }
    no_gps(meta)
    json.dumps(meta)  # alles is gewone JSON (geen IFDRational)


def test_offset_houdt_de_kloktijd():
    meta = meta_of(camera_jpeg(offset="+09:00"))
    assert meta["taken_at"] == "2024-07-01T12:30:05" and meta["taken_kind"] == "local"
    taken = photodate.captured_at(meta, TZ)
    assert taken == datetime(2024, 7, 1, 12, 30, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    "bad", ["0000:00:00 00:00:00", "1850:01:01 10:00:00", "2024:13:40 10:00:00", "onzin", ""]
)
def test_kapotte_datum_valt_weg(bad):
    meta = meta_of(camera_jpeg(bad))
    assert "taken_at" not in meta and "taken_kind" not in meta
    assert meta["exif"]["model"] == "Camera X1"  # de rest blijft


def test_zonder_exif_niets():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    assert meta_of(buf.getvalue()) == {}
    # Alleen een draaiing is geen camerametadata
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "JPEG", exif=exif)
    assert meta_of(buf.getvalue()) == {}


def test_rationaal_met_noemer_nul_en_lange_tekst():
    exif = Image.Exif()
    exif[0x0110] = "x" * 1000
    sub = exif.get_ifd(0x8769)
    sub[0x829A] = TiffImagePlugin.IFDRational(1, 0)
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "JPEG", exif=exif)
    meta = meta_of(buf.getvalue())
    assert meta["exif"]["model"] == "x" * render_meta.TEXT_CHARS
    assert "exposure" not in meta["exif"]
    json.dumps(meta, allow_nan=False)


def test_heic_als_pillow_heif_kan_schrijven():
    pillow_heif = pytest.importorskip("pillow_heif")
    jpeg = Image.open(io.BytesIO(camera_jpeg()))
    buf = io.BytesIO()
    try:
        heif = pillow_heif.from_pillow(jpeg)
        heif.info["exif"] = jpeg.info["exif"]
        heif.save(buf, quality=50)
    except Exception as e:  # noqa: BLE001 - zonder encoder kan het testbestand niet gemaakt
        pytest.skip(f"Geen HEIC-encoder: {e}")
    pillow_heif.register_heif_opener()
    meta = meta_of(buf.getvalue())
    assert meta["taken_at"] == "2024-07-01T12:30:05"
    assert meta["exif"]["model"] == "Camera X1"
    no_gps(meta)


def test_preview_proces_geeft_exif_zonder_gps(tmp_path):
    result, _ = run_render(tmp_path, camera_jpeg(), "foto.jpg")
    meta = result["meta"]
    assert meta["exif"]["make"] == "Fabrikant" and meta["width"] == 64
    assert meta["taken_at"] == "2024-07-01T12:30:05"
    no_gps(meta)


def test_video_meta_utc_en_quicktime():
    utc = {"format": {"tags": {"creation_time": "2024-06-30T22:30:00.000000Z"}}}
    meta = render_meta.video_meta(utc)
    assert meta == {"taken_at": "2024-06-30T22:30:00", "taken_kind": "utc"}
    taken = photodate.captured_at(meta, TZ)
    assert taken == datetime(2024, 7, 1, 0, 30, tzinfo=UTC)
    assert photodate.day_of(taken) == 20240701

    apple = {
        "format": {
            "tags": {
                "creation_time": "2024-06-30T22:30:00.000000Z",
                "com.apple.quicktime.creationdate": "2024-07-01T00:30:00+0200",
                "com.apple.quicktime.location.ISO6709": "+52.3700+004.8900/",
                "location": "+52.3700+004.8900/",
            }
        }
    }
    meta = render_meta.video_meta(apple)
    assert meta == {"taken_at": "2024-07-01T00:30:00", "taken_kind": "local"}
    assert photodate.captured_at(meta, TZ) == datetime(2024, 7, 1, 0, 30, tzinfo=UTC)
    no_gps(meta)

    # In een stream, en een klok die nooit gezet is
    stream = {"streams": [{"tags": {"creation_time": "2023-01-02T03:04:05Z"}}]}
    assert render_meta.video_meta(stream)["taken_at"] == "2023-01-02T03:04:05"
    for never in ("1970-01-01T00:00:00Z", "1904-01-01T00:00:00Z", "onzin"):
        assert render_meta.video_meta({"format": {"tags": {"creation_time": never}}}) == {}
    assert render_meta.video_meta({}) == {}
    assert render_meta.video_meta({"format": None, "streams": None}) == {}


def make_video(path: Path, *metadata: str) -> Path:
    args = []
    for item in metadata:
        args += ["-metadata", item]
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x48:rate=5",
        ]
        + ["-pix_fmt", "yuv420p", *args, "-y", str(path)],
        check=True,
    )
    return path


@needs_ffmpeg
def test_video_uit_ffprobe(tmp_path):
    film = make_video(tmp_path / "film.mp4", "creation_time=2024-06-30T22:30:00Z")
    result, _ = run_render(tmp_path, film, "film.mp4")
    assert result["meta"]["taken_at"] == "2024-06-30T22:30:00"
    assert result["meta"]["taken_kind"] == "utc"


@needs_ffmpeg
def test_run_meta_veel_bestanden_in_een_proces(tmp_path):
    foto = tmp_path / "a.jpg"
    foto.write_bytes(camera_jpeg())
    kapot = tmp_path / "b.jpg"
    kapot.write_bytes(b"geen jpeg")
    film = make_video(tmp_path / "c.mp4", "creation_time=2024-06-30T22:30:00Z")
    request = {
        "mode": "meta",
        "items": [
            {"sha": "a", "source": str(foto), "mime": "image/jpeg"},
            {"sha": "b", "source": str(kapot), "mime": "image/jpeg"},
            {"sha": "c", "source": str(film), "mime": "video/mp4"},
            {"sha": "d", "source": str(tmp_path / "weg.jpg"), "mime": "image/jpeg"},
            {"sha": "e", "source": str(foto), "mime": "text/plain"},
        ],
    }
    # Zoals de server het doet: het eigen proces van render.py
    proc = subprocess.run(
        [sys.executable, "-I", "-c", render_meta_child()],
        input=json.dumps(request).encode(),
        capture_output=True,
        check=True,
    )
    out = json.loads(proc.stdout)
    assert out["state"] == "ready"
    results = out["results"]
    assert results["a"]["exif"]["model"] == "Camera X1"
    assert results["a"]["taken_at"] == "2024-07-01T12:30:05"
    assert "error" in results["b"] and "error" in results["d"]
    # VERSION 2: mode meta geeft ook de plek (Keuze 25), None als er gelezen is en er geen is
    assert results["a"]["location"] == {
        "lat": 52.3669444,
        "lon": 4.8838889,
        "alt": None,
        "source": "exif",
    }
    assert results["c"] == {
        "taken_at": "2024-06-30T22:30:00",
        "taken_kind": "utc",
        "location": None,
    }
    assert results["e"] == {}
    # Verder nergens een plek: niet in de fouten, niet in de rest van de metadata
    rest = {sha: {k: v for k, v in r.items() if k != "location"} for sha, r in results.items()}
    no_gps(rest)


@needs_ffmpeg
@pytest.mark.parametrize("werkmap", ["alleen_lezen", "weg"])
def test_run_meta_met_een_werkmap_waar_niets_in_mag(tmp_path, werkmap):
    """Media uit: de eigen worker maakt zijn socket in de werkmap van het renderproces. Mag
    daar niets in (als nobody vanuit api/, dat van root is) of is die weg, dan in de tijdelijke
    map van het systeem; de video krijgt gewoon zijn datum (I1, S4)."""
    film = make_video(tmp_path / "c.mp4", "creation_time=2024-06-30T22:30:00Z")
    request = {"mode": "meta", "items": [{"sha": "c", "source": str(film), "mime": "video/mp4"}]}
    child = render_meta_child()
    if werkmap == "alleen_lezen":
        # Daar mag niemand iets maken, ook root niet (chmod 555 houdt root niet tegen)
        cwd = Path("/sys")
    else:
        cwd = tmp_path / "werk"
        cwd.mkdir()
        child = f"import os; os.rmdir(os.getcwd()); {child}"
    proc = subprocess.run(
        [sys.executable, "-I", "-c", child],
        input=json.dumps(request).encode(),
        capture_output=True,
        cwd=cwd,
        check=True,
    )
    out = json.loads(proc.stdout)
    assert out["results"]["c"] == {
        "taken_at": "2024-06-30T22:30:00",
        "taken_kind": "utc",
        "location": None,
    }


def render_meta_child() -> str:
    root = str(Path(render.__file__).resolve().parent.parent)
    return f"import sys; sys.path.insert(0, {root!r}); from app.render import main; main()"


def gps_jpeg(lat: tuple, lon: tuple = (4.0, 53.0, 2.0)) -> bytes:
    """Een JPEG met alleen een GPS-IFD (noord en oost)."""
    exif = Image.Exif()
    info = exif.get_ifd(GPS_IFD)
    info.update({1: "N", 2: lat, 3: "E", 4: lon})
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def gps_plek(data: bytes):
    with Image.open(io.BytesIO(data)) as img:
        return render_meta.image_location(img)


R = TiffImagePlugin.IFDRational


@pytest.mark.parametrize(
    ("seconden", "breedte"),
    [
        # Precies 60,0 seconden is de volgende minuut: 54° 47' 60" = 54° 48' (M9, metingen.md
        # Bevinding 5); ook als rational zoals een camera hem schrijft
        (60.0, 54 + 48 / 60),
        (R(60, 1), 54 + 48 / 60),
        (R(600, 10), 54 + 48 / 60),
        # Net onder 60 blijft gewoon in de minuut
        (59.999, 54 + 47 / 60 + 59.999 / 3600),
    ],
)
def test_gps_seconden_tot_en_met_60(seconden, breedte):
    plek = gps_plek(gps_jpeg((54.0, 47.0, seconden)))
    assert plek is not None, seconden
    assert plek["lat"] == round(breedte, 7)
    assert plek["lon"] == round(4 + 53 / 60 + 2 / 3600, 7)
    assert render_meta._degrees((54.0, 47.0, seconden)) == pytest.approx(breedte, abs=1e-12)


def test_gps_60_seconden_aan_het_eind_van_een_graad():
    """59' 60" schuift door tot de volgende graad: 54° 59' 60" = 55°."""
    assert gps_plek(gps_jpeg((54.0, 59.0, 60.0)))["lat"] == 55.0


@pytest.mark.parametrize("seconden", [60.5, R(601, 10), 61.0, 3600.0])
def test_gps_seconden_boven_60_geven_geen_plek(seconden):
    assert gps_plek(gps_jpeg((54.0, 47.0, seconden))) is None
    assert render_meta._degrees((54.0, 47.0, seconden)) is None


def test_gps_minuten_60_blijven_fout():
    """Alleen de seconden schuiven door; 60 minuten is geen afronding van een camera."""
    assert gps_plek(gps_jpeg((54.0, 60.0, 0.0))) is None
