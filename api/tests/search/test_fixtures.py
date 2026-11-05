"""De gemaakte bestanden van tests/search/fixtures.py (bouwontwerp fase 6, §3.6): ze zijn wat
ze zeggen te zijn, elke keer dezelfde bytes, en make() bewaart ze. De lezers die ze echt
gebruiken (tekst, OCR, GPS, duplicaten) komen in Z2, X1, Z3 en Z5; hier lezen Pillow,
pypdfium2, de kantoorlezer van fase 4, ffprobe (marker media) en tesseract (marker ocr) ze."""

from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
import zipfile
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

from app.office import reader
from tests.search import fixtures as F

# --- PDF ------------------------------------------------------------------------------------


def _pdf_tekst(data: bytes) -> list[str]:
    doc = pdfium.PdfDocument(data)
    try:
        return [pagina.get_textpage().get_text_range() for pagina in doc]
    finally:
        doc.close()


def test_pdf_met_tekst():
    data = F.pdf_tekst(3, tekst="extrawoord", titel="Een titel", auteur="Iemand")
    paginas = _pdf_tekst(data)
    assert len(paginas) == 3
    for n, tekst in enumerate(paginas, start=1):
        assert f"paginawoord{n}" in tekst
        for woord in F.PDF_WOORDEN:  # ß, ẞ, de zachte afbreking en de emoji staan er zo in
            assert woord in tekst, (n, woord)
    assert "extrawoord" in paginas[0] and "extrawoord" not in paginas[1]
    # Twee kolommen: de linker eerst
    assert paginas[0].index("linkerkolom") < paginas[0].index("right column")
    meta = pdfium.PdfDocument(data).get_metadata_dict()
    assert (meta["Title"], meta["Author"]) == ("Een titel", "Iemand")
    assert len(_pdf_tekst(F.pdf_tekst(20))) == 20
    for fout in (0, 21):
        with pytest.raises(ValueError):
            F.pdf_tekst(fout)


def test_pdf_als_scan_heeft_geen_tekstlaag():
    data = F.pdf_scan()
    assert _pdf_tekst(data) == [""]
    assert b"/DCTDecode" in data  # één JPEG-pagina


# --- Kantoor --------------------------------------------------------------------------------

K = F.KANTOOR
# Waar elk woord staat: het deel van de zip (voor de lezer van Z2) en wat de lezer van fase 4
# er nu al uit haalt (de hoofdtekst en titel en auteur)
KANTOOR = {
    "docx": (lambda: F.docx("extra"), {
        "tekst": "word/document.xml", "voetnoot": "word/footnotes.xml",
        "kop": "word/header1.xml", "voet": "word/footer1.xml", "titel": "docProps/core.xml",
    }, "tekst"),
    "xlsx": (lambda: F.xlsx(2_000), {"laatste_cel": "xl/sharedStrings.xml"}, "laatste_cel"),
    "pptx": (lambda: F.pptx(), {
        "tekst": "ppt/slides/slide1.xml", "notities": "ppt/notesSlides/notesSlide1.xml",
    }, "tekst"),
    "odt": (lambda: F.odt("extra"), {
        "tekst": "content.xml", "voetnoot": "content.xml", "kop": "styles.xml",
        "voet": "styles.xml", "titel": "meta.xml",
    }, "tekst"),
    "ods": (lambda: F.ods(2_000), {"laatste_cel": "content.xml"}, "laatste_cel"),
    "odp": (lambda: F.odp(), {"tekst": "content.xml", "notities": "content.xml"}, "tekst"),
}  # fmt: skip


@pytest.mark.parametrize("soort", sorted(KANTOOR))
def test_kantoordocumenten(soort, tmp_path):
    maak, plekken, hoofd = KANTOOR[soort]
    data = maak()
    assert data == maak()
    z = zipfile.ZipFile(io.BytesIO(data))
    for sleutel, deel in plekken.items():
        assert K[sleutel].encode() in z.read(deel), (sleutel, deel)
    pad = tmp_path / f"doc.{soort}"
    pad.write_bytes(data)
    gelezen = reader.read(pad)
    alles = json.dumps(gelezen["doc"], ensure_ascii=False)
    assert K[hoofd] in alles
    assert gelezen["meta"]["format"] == soort
    assert K["titel"] in json.dumps(gelezen["meta"], ensure_ascii=False)


def test_xlsx_heeft_20000_cellen():
    z = zipfile.ZipFile(io.BytesIO(F.xlsx()))
    blad = z.read("xl/worksheets/sheet1.xml")
    assert blad.count(b"<c ") == 20_000
    assert b'r="T1000"' in blad  # 20 kolommen, 1.000 rijen


def test_een_entiteit_en_een_zip_bom_worden_geweigerd(tmp_path):
    lol = tmp_path / "lol.docx"
    lol.write_bytes(F.xml_entiteit())
    assert b"<!ENTITY lol9" in zipfile.ZipFile(lol).read("word/document.xml")
    with pytest.raises(reader.Unreadable):
        reader.read(lol)
    bom = F.make("kantoor/zip-bom.docx")
    assert bom.stat().st_size < 2_000_000
    info = zipfile.ZipFile(bom).getinfo("word/document.xml")
    assert info.file_size > 512 * 1024 * 1024
    with pytest.raises(reader.Unreadable):
        reader.read(bom)


# --- Scans ----------------------------------------------------------------------------------


def test_de_scans_voor_ocr():
    map_ = F.make("ocr/scans")
    info = json.loads((map_ / "info.json").read_text())
    assert info["hoeken"] == list(F.SCAN_HOEKEN)
    bestanden = sorted(p.name for p in map_.iterdir() if p.suffix in (".png", ".jpg"))
    assert len(bestanden) == 2 * len(F.SCAN_HOEKEN) * 2
    for taal in F.SCAN_TEKST:
        assert F.OCR_WOORD in info["tekst"][taal][-1]
        rechtop = Image.open(map_ / f"{taal}_0.png")
        assert rechtop.mode == "L" and rechtop.size == (1240, 1754)
        assert round(rechtop.info["dpi"][0]) == 150
        assert Image.open(map_ / f"{taal}_90.jpg").size == (1754, 1240)
        assert Image.open(map_ / f"{taal}_7.jpg").format == "JPEG"


def test_zonder_dejavu_een_duidelijke_terugval(monkeypatch):
    monkeypatch.setattr(F, "LETTERTYPEN", ())
    with pytest.warns(UserWarning, match="DejaVu Sans ontbreekt"):
        naam, font = F.lettertype(20)
    assert naam == "pillow-standaard" and font is not None


# --- Foto's ---------------------------------------------------------------------------------


def _graden(dms, ref: str) -> float:
    d, m, s = (float(x) for x in dms)
    return (d + m / 60 + s / 3600) * (-1 if ref in ("S", "W") else 1)


def _gps(data: bytes) -> dict:
    return dict(Image.open(io.BytesIO(data)).getexif().get_ifd(0x8825))


def test_foto_met_gps():
    gps = _gps(F.foto_gps(*F.UTRECHT, alt=12.5))
    lat, lon = _graden(gps[2], gps[1]), _graden(gps[4], gps[3])
    assert F.haversine_km((lat, lon), F.UTRECHT) < 0.01
    assert float(gps[6]) == 12.5 and gps[5] in (0, b"\x00")  # boven zeeniveau
    ex = Image.open(io.BytesIO(F.foto_gps(*F.UTRECHT))).getexif()
    assert (ex[0x010F], ex[0x0110]) == ("FUJIFILM", "X-T30")
    assert ex.get_ifd(0x8769)[0x9003] == "2024:06:01 14:30:00"
    # Ten zuiden en ten westen: de ref draait het teken om
    zuid = _gps(F.foto_gps(-33.86, -70.65))
    assert (zuid[1], zuid[3]) == ("S", "W")
    assert _graden(zuid[2], zuid[1]) == pytest.approx(-33.86, abs=1e-4)


def test_foto_zonder_gps_en_de_verkleinde_kopie():
    assert _gps(F.foto_zonder_gps()) == {}
    groot = F.foto_gps(*F.UTRECHT, zaad="kopie")
    klein = F.foto_klein(groot)
    assert klein != groot
    assert Image.open(io.BytesIO(klein)).size == (320, 240)
    assert _gps(klein) == _gps(groot)


def test_telefoonfoto_met_draaiing_6():
    ex = Image.open(io.BytesIO(F.foto_draaiing6())).getexif()
    assert ex[0x0112] == 6 and ex[0x010F] == "Apple"


def test_heic_met_gps():
    import pillow_heif

    pillow_heif.register_heif_opener()
    data = F.heic_gps()
    img = Image.open(io.BytesIO(data))
    assert img.format == "HEIF"
    gps = dict(img.getexif().get_ifd(0x8825))
    assert _graden(gps[2], gps[1]) == pytest.approx(F.UTRECHT[0], abs=1e-4)


def test_xmp_met_gps_en_met_entiteiten():
    xmp = Image.open(io.BytesIO(F.xmp_gps())).info["xmp"]
    assert b'exif:GPSLatitude="52,5.4420N"' in xmp
    assert b'exif:GPSLongitude="5,7.2840E"' in xmp
    assert _gps(F.xmp_gps()) == {}  # alleen in XMP
    lach = Image.open(io.BytesIO(F.xmp_laughs())).info["xmp"]
    assert b"<!ENTITY lol9" in lach and b"&lol9;" in lach


def test_gps_die_geen_plek_is():
    nan = _gps(F.gps_nan())
    assert math.isnan(float(nan[2][0]))
    zonder = _gps(F.gps_zonder_ref())
    assert 2 in zonder and 1 not in zonder and 3 not in zonder
    nul = _gps(F.gps_nul())
    assert _graden(nul[2], nul[1]) == 0.0 and _graden(nul[4], nul[3]) == 0.0


def test_elke_keer_dezelfde_bytes():
    for maak in (
        lambda: F.pdf_tekst(2),
        F.pdf_scan,
        lambda: F.scan("en", 3, "jpg"),
        lambda: F.foto_gps(*F.UTRECHT),
        F.foto_draaiing6,
        F.xmp_gps,
        F.gps_nan,
        F.heic_gps,
    ):
        assert maak() == maak(), maak


# --- Bijna-duplicaten en punten -------------------------------------------------------------

BIJNA = {"varianten": 50, "brieven": 150, "formulieren": 30, "schermen": 30, "egaal": 150,
         "anders": 50}  # fmt: skip


@pytest.mark.parametrize("naam", sorted(BIJNA))
def test_bijna_duplicaten(naam):
    map_ = F.make(f"bijna/{naam}")
    beelden = sorted(map_.iterdir())
    assert len(beelden) == BIJNA[naam]
    for pad in beelden[:3]:
        Image.open(pad).verify()


def test_varianten_horen_bij_hun_origineel():
    namen = {p.name for p in F.make("bijna/varianten").iterdir()}
    for i in range(10):
        for soort in ("", "-klein", "-q40", "-bijgesneden", "-lichter"):
            assert f"orig-{i}{soort}.jpg" in namen


def test_punten_bij_een_grens_en_op_zee():
    for a, b in F.GRENSPAREN:
        assert a[3] != b[3], (a, b)  # aan weerszijden van een grens
        assert 3 < F.haversine_km(a[1:3], b[1:3]) < 15, (a, b)
    landen = {p[3] for paar in F.GRENSPAREN for p in paar}
    assert {"NL", "BE", "DE", "LU", "FR"} <= landen
    for naam, lat, lon in F.ZEE:
        assert 51.5 < lat < 55.5 and 2 < lon < 4.6, naam
    # Utrecht–Amsterdam is ±35 km
    assert 33 < F.haversine_km(F.UTRECHT, (52.3730, 4.8924)) < 37


# --- make() ---------------------------------------------------------------------------------


def test_make_maakt_een_keer_en_ruimt_op(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "CACHE", tmp_path)
    keer = []

    def recept(uit: Path) -> None:
        keer.append(uit)
        uit.write_text("klaar")

    monkeypatch.setitem(F.RECEPTEN, "proef/een.txt", recept)
    pad = F.make("proef/een.txt")
    assert pad == tmp_path / F.sleutel() / "proef" / "een.txt"
    assert pad.read_text() == "klaar"
    assert F.make("proef/een.txt") == pad and len(keer) == 1
    assert not list(tmp_path.glob(".maak-*"))  # de werkmap is weg
    oud = tmp_path / "0123456789abcdef"
    oud.mkdir()
    F.prune()
    assert not oud.exists() and pad.exists()


def test_make_laat_niets_half_achter(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "CACHE", tmp_path)

    def stuk(uit: Path) -> None:
        uit.write_text("half")
        raise RuntimeError("stuk")

    monkeypatch.setitem(F.RECEPTEN, "proef/stuk.txt", stuk)
    with pytest.raises(RuntimeError):
        F.make("proef/stuk.txt")
    assert not (tmp_path / F.sleutel() / "proef" / "stuk.txt").exists()
    assert not list(tmp_path.glob(".maak-*"))


def test_de_sleutel_volgt_het_lettertype(monkeypatch):
    """Met en zonder DejaVu een andere map (de scans zien er anders uit). Beide gevallen met
    de hand, zodat de test ook klopt waar DejaVu ontbreekt (python:3.12-slim in slim.sh)."""
    monkeypatch.setattr(F, "lettertype", lambda grootte=36: ("DejaVuSans", None))
    met = F.sleutel()
    monkeypatch.setattr(F, "lettertype", lambda grootte=36: ("pillow-standaard", None))
    assert F.sleutel() != met


# --- Met ffmpeg en met tesseract ------------------------------------------------------------


@pytest.mark.media
@pytest.mark.parametrize("naam", ["video/locatie.mp4", "video/iso6709.mov"])
def test_video_met_een_plek(naam):
    pad = F.make(naam)
    uit = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(pad)],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    tags = json.loads(uit.stdout)["format"]["tags"]
    plek = tags.get("location") or tags.get("com.apple.quicktime.location.ISO6709")
    assert plek == "+52.0907+005.1214/", tags


def _tesseract(pad: Path, *opties: str) -> str:
    uit = subprocess.run(["tesseract", str(pad), "-", *opties], capture_output=True, text=True,
                         timeout=120)  # fmt: skip
    return uit.stdout + uit.stderr


@pytest.mark.ocr
def test_tesseract_leest_de_scans():
    """Wat tesseract zonder voorbewerking haalt: het woord bij 0°, 3° en −5°, en bij 90° ziet
    OSD de draaiing. 7° leest hij niet altijd: daar moet X1 eerst rechtzetten (zie
    docs/fase-6/z0-e2e.md)."""
    assert shutil.which("tesseract"), "tesseract ontbreekt (marker ocr: de job ocr heeft hem)"
    map_ = F.make("ocr/scans")
    for taal in F.SCAN_TEKST:
        for hoek in (0, 3, -5):
            for formaat in ("png", "jpg"):
                pad = map_ / f"{taal}_{hoek}.{formaat}"
                tekst = _tesseract(pad, "-l", "nld+eng")
                assert F.OCR_WOORD in tekst.lower(), (pad.name, tekst)
        osd = _tesseract(map_ / f"{taal}_90.png", "--psm", "0")
        assert "Rotate: 90" in osd, osd
