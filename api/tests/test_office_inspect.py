"""De invoercontrole vóór DS (Keuze 23, §3.8): een gewone docx, xlsx, pptx en odt en de lege
bestanden gaan door; een bom, een zip die over zijn grootte liegt, te veel onderdelen, een
vreemde methode, iets versleuteld en een bom in een ingebedde zip niet; een pptx met 80 MB
foto's wel; doc boven 20 MB niet; de tijdgrens; de cache per sha. En de inhoud beslist, niet
de naam: een zip onder de naam .doc, XML in een onderdeel dat niet op .xml eindigt, en een
docx die zich werkblad noemt."""

import io
import os
import secrets
import struct
import zipfile
from pathlib import Path

import pytest

from app.config import Settings
from app.office import inspect
from tests.fake_docserver import docx
from tests.test_office import docx_bytes, odf, odt_bytes, ooxml, pptx_bytes, xlsx_bytes

MB = 1024**2
ZEROS = bytes(MB)
PARAGRAPH = b"<w:p><w:r><w:t>tekst</w:t></w:r></w:p>" * 4096
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
OOXML_MAIN = "application/vnd.openxmlformats-officedocument"
SHEET_TYPES = (
    f'<?xml version="1.0"?><Types xmlns="{CT_NS}">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" '
    f'ContentType="{OOXML_MAIN}.spreadsheetml.sheet.main+xml"/>'
    # een ingebedde docx noemt het type van tekst, maar niet als hoofddeel
    f'<Default Extension="docx" ContentType="{OOXML_MAIN}.wordprocessingml.document"/>'
    "</Types>"
)
WORD_TYPES = (
    f'<?xml version="1.0"?><Types xmlns="{CT_NS}">'
    '<Override PartName="/word/main.dat" '
    f'ContentType="{OOXML_MAIN}.wordprocessingml.document.main+xml"/>'
    "</Types>"
)


@pytest.fixture(autouse=True)
def empty_cache():
    inspect.forget()
    yield
    inspect.forget()


@pytest.fixture
def office(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, jwt_secret="test" * 8)


def put(tmp_path: Path, data: bytes, name: str = "bestand") -> Path:
    path = tmp_path / f"{name}-{secrets.token_hex(4)}"
    path.write_bytes(data)
    return path


def verdict(tmp_path: Path, data: bytes, ext: str) -> inspect.Verdict:
    return inspect._inspect(put(tmp_path, data), ext, 20 * MB)


def build(entries: dict[str, bytes | str], method=zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", method, compresslevel=1) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def write_big(archive: zipfile.ZipFile, name: str, chunk: bytes, count: int) -> None:
    """Een groot onderdeel in stukken, met de methode en het niveau van het archief."""
    with archive.open(name, "w") as target:
        for _ in range(count):
            target.write(chunk)


def big_xml(part: str, megabytes: float, types: str = "<Types/>", extra: int = 0) -> bytes:
    """Een zip met zoveel MB echte XML in part (en extra bytes erbij)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("[Content_Types].xml", types)
        with archive.open(part, "w") as target:
            left = int(megabytes * MB) + extra
            while left > 0:
                target.write(PARAGRAPH[:left])
                left -= len(PARAGRAPH)
    return out.getvalue()


def bomb(megabytes: int = 128) -> bytes:
    """Klein, maar megabytes nullen uitgepakt, in een onderdeel dat geen XML is."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        write_big(archive, "word/media/nul.bin", ZEROS, megabytes)
    return out.getvalue()


def central_offsets(data: bytes) -> list[int]:
    """Waar elke regel van de centrale map begint."""
    end = data.rindex(b"PK\x05\x06")
    size, start = struct.unpack_from("<II", data, end + 12)
    found, at = [], start
    while at < start + size:
        assert data[at : at + 4] == b"PK\x01\x02"
        found.append(at)
        name, extra, comment = struct.unpack_from("<HHH", data, at + 28)
        at += 46 + name + extra + comment
    return found


def local_offset(data: bytes, central: int) -> int:
    return struct.unpack_from("<I", data, central + 42)[0]


# Gewone bestanden


@pytest.mark.parametrize(
    ("make", "ext"),
    [
        (docx_bytes, "docx"),
        (xlsx_bytes, "xlsx"),
        (pptx_bytes, "pptx"),
        (odt_bytes, "odt"),
        (lambda: docx.make_docx(["Hallo", "wereld"]), "docx"),
        (lambda: docx.make_docx([]), "docx"),  # een leeg document
        (lambda: odf("spreadsheet", "<office:spreadsheet/>"), "ods"),
        (lambda: odf("presentation", "<office:presentation/>"), "odp"),
    ],
)
def test_gewone_bestanden_gaan_door(tmp_path, make, ext):
    assert verdict(tmp_path, make(), ext) == inspect.OK


def test_hoofdletters_en_punt_in_de_extensie(tmp_path):
    assert verdict(tmp_path, docx_bytes(), ".DOCX") == inspect.OK


def test_pptx_met_80_mb_fotos(tmp_path):
    """Opgeslagen foto's pakken niet groter uit dan het bestand: geen bom."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
        for n in range(8):
            info = zipfile.ZipInfo(f"ppt/media/image{n}.jpeg")
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, JPEG + os.urandom(10 * MB))
    data = out.getvalue()
    assert len(data) > 80 * MB
    assert verdict(tmp_path, data, "pptx") == inspect.OK


def test_veel_kleine_fotos_tellen_niet_als_xml(tmp_path):
    """Een onderdeel dat geen XML blijkt, telt vanaf het eerste stuk niet mee: 400 foto's van
    20 KB (samen ruim boven XML_BYTES) zijn geen XML."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
        for n in range(400):
            archive.writestr(f"ppt/media/image{n}.jpeg", JPEG + os.urandom(20 * 1024))
    assert 400 * 20 * 1024 > inspect.XML_BYTES
    assert verdict(tmp_path, out.getvalue(), "pptx") == inspect.OK


def test_een_echt_werkblad_krijgt_de_ruimere_grens(tmp_path):
    """Tot XML_BYTES_SHEET, maar alleen als de inhoud een werkblad is (het hoofddeel in
    [Content_Types].xml), niet omdat de naam op .xlsx eindigt."""
    row = b"<row><c><v>1</v></c></row>" * 4096
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("[Content_Types].xml", SHEET_TYPES)
        write_big(archive, "xl/worksheets/sheet1.xml", row, inspect.XML_BYTES_SHEET // len(row))
    sheet = out.getvalue()
    assert inspect.XML_BYTES < inspect.XML_BYTES_SHEET
    assert verdict(tmp_path, sheet, "xlsx") == inspect.OK
    # Dezelfde inhoud onder de naam van een docx: de naam geeft de strengere grens
    assert verdict(tmp_path, sheet, "docx") == inspect.Verdict(False, "too_big")
    # Boven XML_BYTES_SHEET ook als werkblad niet (x2t liep bij 2 GB vast op 30 MB)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("[Content_Types].xml", SHEET_TYPES)
        write_big(archive, "xl/worksheets/sheet1.xml", row, inspect.XML_BYTES_SHEET // len(row) + 1)
    assert verdict(tmp_path, out.getvalue(), "xlsx") == inspect.Verdict(False, "too_big")


# Geweigerd


def test_bom(tmp_path):
    data = bomb()
    assert len(data) < 2 * MB
    assert verdict(tmp_path, data, "docx") == inspect.Verdict(False, "ratio")


def test_te_veel_xml_in_een_docx(tmp_path):
    """De spike (A11) zag bij 2 GB een docx met 42 MB XML docservice laten vastlopen, en de
    meting van de fixer van O0 x2t al bij 10 MB: zo ver komt het niet meer."""
    limit = inspect.XML_BYTES - len("<Types/>")
    assert verdict(tmp_path, big_xml("word/document.xml", 0, extra=limit), "docx") == inspect.OK
    over = big_xml("word/document.xml", 0, extra=limit + 1)
    assert verdict(tmp_path, over, "docx") == inspect.Verdict(False, "too_big")
    for megabytes in (11, 42):
        data = big_xml("word/document.xml", megabytes)
        assert len(data) < MB  # klein bestand, veel XML
        assert verdict(tmp_path, data, "docx") == inspect.Verdict(False, "too_big")
    # Ook de naam van een werkblad helpt niet: de inhoud is geen werkblad
    assert verdict(tmp_path, over, "xlsx") == inspect.Verdict(False, "too_big")
    assert verdict(tmp_path, over, "pptx") == inspect.Verdict(False, "too_big")


def test_xml_onder_een_andere_naam_telt_mee(tmp_path):
    """In OOXML mag het hoofddeel elke naam hebben, zolang [Content_Types].xml en de relaties
    ernaar wijzen; DS leest het dan gewoon (review S2: word/main.dat met 60 MB XML)."""
    small = big_xml("word/main.dat", 0, WORD_TYPES, extra=1000)
    assert verdict(tmp_path, small, "docx") == inspect.OK
    over = inspect.XML_BYTES + 1
    data = big_xml("word/main.dat", 0, WORD_TYPES, extra=over)
    assert verdict(tmp_path, data, "docx") == inspect.Verdict(False, "too_big")
    assert verdict(tmp_path, big_xml("word/main.dat", 60, WORD_TYPES), "docx") == inspect.Verdict(
        False, "too_big"
    )
    # Een BOM, witruimte (ook veel) en UTF-16 houden het niet tegen
    utf16 = PARAGRAPH.decode().encode("utf-16-le")
    for body in (
        b"\xef\xbb\xbf" + PARAGRAPH,
        b" \r\n" * 50_000 + PARAGRAPH,
        b"\xff\xfe" + utf16,
        utf16,  # zonder BOM: < en dan een NUL
    ):
        data = build(
            {"[Content_Types].xml": WORD_TYPES, "word/main.dat": body * (over // len(body) + 1)}
        )
        assert verdict(tmp_path, data, "docx") == inspect.Verdict(False, "too_big"), body[:4]
    # Wat geen XML is (een foto, nullen), telt niet als XML
    photo = build({"[Content_Types].xml": WORD_TYPES, "word/media/a.jpg": JPEG + b"<" * over})
    assert verdict(tmp_path, photo, "docx") == inspect.OK
    nul = build({"[Content_Types].xml": WORD_TYPES, "word/media/a.bin": bytes(over)})
    assert verdict(tmp_path, nul, "docx") == inspect.OK


def test_odf_heeft_een_eigen_strengere_grens(tmp_path):
    """odt met 21 MB XML liet x2t vastlopen (spike stap 18); ook ods krijgt geen ruimere
    grens (niet gemeten)."""
    assert inspect.XML_BYTES_ODF <= inspect.XML_BYTES
    body = "<text:p>tekst</text:p>" * (inspect.XML_BYTES_ODF // 22 + 1)
    for kind, ext in (("text", "odt"), ("spreadsheet", "ods"), ("presentation", "odp")):
        data = odf(kind, f"<office:body>{body}</office:body>")
        assert verdict(tmp_path, data, ext) == inspect.Verdict(False, "too_big"), ext
    # Een docx-naam maakt van een ODF-inhoud geen OOXML: ook dan de grens van ODF
    assert verdict(tmp_path, data, "docx") == inspect.Verdict(False, "too_big")
    assert verdict(tmp_path, data, "xlsx") == inspect.Verdict(False, "too_big")
    # Net onder de grens mag
    small = odf("text", "<office:body>" + "<text:p>x</text:p>" * 1000 + "</office:body>")
    assert verdict(tmp_path, small, "odt") == inspect.OK


@pytest.mark.parametrize(
    "types",
    [
        b"<Types/>",  # zegt niets
        WORD_TYPES.encode(),  # een docx die zich werkblad noemt
        SHEET_TYPES.replace("</Types>", WORD_TYPES[WORD_TYPES.index("<Override") :]).encode(),
        SHEET_TYPES.replace("ms-excel", "x").replace("sheet.main", "sheet.m&#97;in").encode(),
        SHEET_TYPES.encode("utf-16"),  # een codering die we niet lezen
    ],
    ids=["leeg", "tekst", "allebei", "entiteit", "utf-16"],
)
def test_alleen_een_echt_werkblad_krijgt_de_ruimere_grens(tmp_path, monkeypatch, types):
    monkeypatch.setattr(inspect, "XML_BYTES", MB)
    monkeypatch.setattr(inspect, "XML_BYTES_SHEET", 4 * MB)
    sheet_xml = b"<row><c><v>1</v></c></row>" * (2 * MB // 26)
    data = build({"[Content_Types].xml": types, "xl/worksheets/sheet1.xml": sheet_xml})
    assert verdict(tmp_path, data, "xlsx") == inspect.Verdict(False, "too_big")
    # Het echte werkblad wel, ook met [content_types].xml in kleine letters
    for name in ("[Content_Types].xml", "[content_types].xml"):
        data = build({name: SHEET_TYPES, "xl/worksheets/sheet1.xml": sheet_xml})
        assert verdict(tmp_path, data, "xlsx") == inspect.OK, name
    # Twee keer [Content_Types].xml: allebei moeten een werkblad zeggen
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", SHEET_TYPES)
        with pytest.warns(UserWarning):  # zipfile waarschuwt voor een dubbele naam
            archive.writestr("[Content_Types].xml", WORD_TYPES)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    assert verdict(tmp_path, out.getvalue(), "xlsx") == inspect.Verdict(False, "too_big")


def test_alles_samen_te_groot(tmp_path, monkeypatch):
    monkeypatch.setattr(inspect, "TOTAL_BYTES", 1000)
    data = build({"a.bin": os.urandom(800), "b.bin": os.urandom(800)}, zipfile.ZIP_STORED)
    assert verdict(tmp_path, data, "docx") == inspect.Verdict(False, "too_big")


def test_een_zip_die_over_zijn_grootte_liegt(tmp_path):
    data = bytearray(build({"[Content_Types].xml": "<Types/>", "word/document.xml": "x" * 5000}))
    central = central_offsets(bytes(data))[1]
    local = local_offset(bytes(data), central)
    # Opgegeven: 10 bytes, in de centrale map (offset 24) en de lokale kop (offset 22)
    struct.pack_into("<I", data, central + 24, 10)
    struct.pack_into("<I", data, local + 22, 10)
    assert verdict(tmp_path, bytes(data), "docx") == inspect.Verdict(False, "corrupt")
    # En groter dan wat eruit komt ook
    struct.pack_into("<I", data, central + 24, 50_000)
    struct.pack_into("<I", data, local + 22, 50_000)
    assert verdict(tmp_path, bytes(data), "docx") == inspect.Verdict(False, "corrupt")


def test_te_veel_onderdelen(tmp_path):
    entries = {f"d/{n}": b"" for n in range(inspect.MAX_ENTRIES + 1)}
    data = build(entries, zipfile.ZIP_STORED)
    assert verdict(tmp_path, data, "docx") == inspect.Verdict(False, "entries")
    # Precies de grens mag
    entries.pop("d/0")
    assert verdict(tmp_path, build(entries, zipfile.ZIP_STORED), "docx") == inspect.OK


def test_een_centrale_map_die_te_groot_zegt_te_zijn(tmp_path):
    data = bytearray(docx_bytes())
    end = bytes(data).rindex(b"PK\x05\x06")
    struct.pack_into("<I", data, end + 12, 0xFFFFFF00)
    assert verdict(tmp_path, bytes(data), "docx") == inspect.Verdict(False, "entries")


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_vreemde_methode(tmp_path, method):
    data = build({"[Content_Types].xml": "<Types/>", "word/document.xml": "<w:document/>"}, method)
    assert verdict(tmp_path, data, "docx") == inspect.Verdict(False, "method")


def test_versleuteld_onderdeel(tmp_path):
    data = bytearray(docx_bytes())
    central = central_offsets(bytes(data))[-1]
    flags = struct.unpack_from("<H", data, central + 8)[0]
    struct.pack_into("<H", data, central + 8, flags | 0x1)
    assert verdict(tmp_path, bytes(data), "docx") == inspect.Verdict(False, "encrypted")


def test_versleuteld_ooxml_is_een_ole_bestand(tmp_path):
    data = inspect.OLE_MAGIC + bytes(4096)
    for ext in ("docx", "xlsx", "pptx"):
        assert verdict(tmp_path, data, ext) == inspect.Verdict(False, "encrypted")


def test_versleuteld_odf(tmp_path):
    manifest = (
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml">'
        '<manifest:encryption-data manifest:checksum-type="SHA1/1K"/>'
        "</manifest:file-entry></manifest:manifest>"
    )
    data = build(
        {
            "mimetype": "application/vnd.oasis.opendocument.text",
            "META-INF/manifest.xml": manifest,
            "content.xml": os.urandom(256),
        }
    )
    assert verdict(tmp_path, data, "odt") == inspect.Verdict(False, "encrypted")


def test_bom_in_een_ingebedde_zip(tmp_path):
    inner = bomb()
    data = ooxml({"word/document.xml": "<w:document/>", "word/embeddings/pakket.bin": inner})
    assert len(data) < 2 * MB
    assert verdict(tmp_path, data, "docx") == inspect.Verdict(False, "ratio")
    # Ook als de ingebedde zip zelf opgeslagen (niet ingepakt) is
    stored = build({"[Content_Types].xml": "<Types/>", "x.zip": inner}, zipfile.ZIP_STORED)
    assert verdict(tmp_path, stored, "docx") == inspect.Verdict(False, "ratio")


def test_een_gewone_ingebedde_zip_mag(tmp_path):
    inner = build({"a.txt": "hallo", "b.xml": "<b/>"})
    data = ooxml({"word/document.xml": "<w:document/>", "word/embeddings/pakket.bin": inner})
    assert verdict(tmp_path, data, "docx") == inspect.OK


def test_ingebedde_zip_telt_mee_voor_onderdelen(tmp_path, monkeypatch):
    monkeypatch.setattr(inspect, "MAX_ENTRIES", 10)
    inner = build({f"{n}.txt": "x" for n in range(8)})
    data = build({"[Content_Types].xml": "<Types/>", "x.zip": inner})
    assert verdict(tmp_path, data, "docx") == inspect.OK  # 2 + 8: precies de grens
    inner = build({f"{n}.txt": "x" for n in range(9)})
    data = build({"[Content_Types].xml": "<Types/>", "x.zip": inner})
    assert verdict(tmp_path, data, "docx") == inspect.Verdict(False, "entries")


OLE = inspect.OLE_MAGIC + bytes(504)
RTF = b"{\\rtf1\\ansi hallo}"


def test_ole_formaten_op_grootte(tmp_path):
    for ext in ("doc", "xls", "ppt"):
        assert verdict(tmp_path, OLE, ext) == inspect.OK
    assert verdict(tmp_path, RTF, "rtf") == inspect.OK
    big = tmp_path / "groot.doc"
    with big.open("wb") as f:
        f.write(inspect.OLE_MAGIC)
        f.truncate(20 * MB + 1)  # spaarzaam bestand: snel
    assert inspect._inspect(big, "doc", 20 * MB) == inspect.Verdict(False, "too_big")
    exact = tmp_path / "precies.doc"
    with exact.open("wb") as f:
        f.write(inspect.OLE_MAGIC)
        f.truncate(20 * MB)
    assert inspect._inspect(exact, "doc", 20 * MB) == inspect.OK


@pytest.mark.parametrize("ext", ["doc", "xls", "ppt", "rtf"])
def test_ole_formaten_volgens_de_inhoud(tmp_path, ext):
    """DS kijkt naar de inhoud: een zip onder de naam .doc zette hij gewoon om als docx (review
    K1 en S4), en kreeg hier vroeger alleen de controle op grootte."""
    for data in (bomb(), docx_bytes(), b"", b"<w:document/>", b"\x00" * 512):
        assert verdict(tmp_path, data, ext) == inspect.Verdict(False, "format")
    other = OLE if ext == "rtf" else RTF
    assert verdict(tmp_path, other, ext) == inspect.Verdict(False, "format")


def test_de_tijdgrens(tmp_path, monkeypatch):
    monkeypatch.setattr(inspect, "SECONDS", -1)
    assert verdict(tmp_path, docx_bytes(), "docx") == inspect.Verdict(False, "time")


@pytest.mark.parametrize(
    "data",
    [b"", b"geen zip", b"PK\x03\x04" + bytes(100), docx_bytes()[:-30]],
)
def test_geen_geldige_zip(tmp_path, data):
    assert verdict(tmp_path, data, "docx") == inspect.Verdict(False, "corrupt")


def test_een_lege_zip_is_geen_document(tmp_path):
    assert verdict(tmp_path, build({}), "docx") == inspect.Verdict(False, "corrupt")


def test_afgekapte_inhoud(tmp_path):
    data = bytearray(build({"word/document.xml": os.urandom(4000)}, zipfile.ZIP_STORED))
    central = central_offsets(bytes(data))[0]
    # De ingepakte grootte groter dan het bestand
    struct.pack_into("<I", data, central + 20, 10 * MB)
    assert verdict(tmp_path, bytes(data), "docx") == inspect.Verdict(False, "corrupt")


@pytest.mark.parametrize("ext", ["exe", "pdf", "txt", "", "zip"])
def test_onbekende_soort(tmp_path, ext):
    assert verdict(tmp_path, docx_bytes(), ext) == inspect.Verdict(False, "format")


def test_bestand_weg(tmp_path):
    assert inspect._inspect(tmp_path / "bestaat-niet", "docx", MB) == inspect.Verdict(
        False, "corrupt"
    )


# De cache


@pytest.mark.anyio
async def test_cache_per_sha_en_extensie(tmp_path, office):
    path = put(tmp_path, docx_bytes())
    sha = "a" * 64
    assert await inspect.check(office, path, "docx", sha) == inspect.OK
    # Andere inhoud op dezelfde plek: zelfde sha, dus de bewaarde uitslag
    path.write_bytes(b"kapot")
    assert await inspect.check(office, path, "docx", sha) == inspect.OK
    assert await inspect.check(office, path, ".DOCX", sha) == inspect.OK  # zelfde sleutel
    # Een andere sha of soort kijkt opnieuw
    assert await inspect.check(office, path, "docx", "b" * 64) == inspect.Verdict(False, "corrupt")
    assert await inspect.check(office, path, "xlsx", sha) == inspect.Verdict(False, "corrupt")
    inspect.forget()
    assert await inspect.check(office, path, "docx", sha) == inspect.Verdict(False, "corrupt")


@pytest.mark.anyio
async def test_cache_verloopt_en_blijft_begrensd(tmp_path, office, monkeypatch):
    path = put(tmp_path, docx_bytes())
    monkeypatch.setattr(inspect, "CACHE_SECONDS", -1)  # meteen verlopen
    assert await inspect.check(office, path, "docx", "a" * 64) == inspect.OK
    path.write_bytes(b"kapot")
    assert await inspect.check(office, path, "docx", "a" * 64) == inspect.Verdict(False, "corrupt")
    monkeypatch.setattr(inspect, "CACHE_SECONDS", 3600.0)
    monkeypatch.setattr(inspect, "CACHE_SIZE", 3)
    for n in range(10):
        await inspect.check(office, path, "docx", f"{n:064d}")
    assert len(inspect._cache) <= 3


@pytest.mark.anyio
async def test_ole_grens_uit_de_instellingen(tmp_path):
    settings = Settings(data_dir=tmp_path, jwt_secret="test" * 8, office_ole_max_bytes=100)
    path = put(tmp_path, inspect.OLE_MAGIC + bytes(93))
    assert await inspect.check(settings, path, "doc", "c" * 64) == inspect.Verdict(False, "too_big")
