"""Voorbeeldbestanden voor de viewer-tests (P4): van elk soort één, hier gemaakt (Pillow,
ffmpeg, zipfile en XML-tekst), zodat de test geen bestanden in de repo nodig heeft.

samples(work_dir) -> {naam: bytes}. ffmpeg moet er zijn (in de sandbox en in de CI-job wel).
"""

from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ODF_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
)

# Wat de test op het scherm terug wil zien
CP1252_TEXT = "Café crème – €5 “oud”\nTweede regel\n"
GPS_LAT = (52, 22, 30)


def jpeg_with_gps(size: tuple[int, int] = (2400, 1600)) -> bytes:
    """Een foto met camera-EXIF én een GPS-blok: dat laatste mag nergens terugkomen."""
    img = Image.new("RGB", size, "steelblue")
    draw = ImageDraw.Draw(img)
    for x in range(0, size[0], 200):
        draw.line([(x, 0), (x, size[1])], fill="white", width=8)
    exif = Image.Exif()
    exif[0x010F] = "Testmerk"  # Make
    exif[0x0110] = "Model X"  # Model
    exif[0x0132] = "2024:05:01 12:30:00"  # DateTime
    ifd = exif.get_ifd(0x8769)  # Exif-IFD
    ifd[0x829A] = 1 / 250  # ExposureTime
    ifd[0x829D] = 2.8  # FNumber
    ifd[0x8827] = 200  # ISO
    ifd[0x920A] = 35.0  # FocalLength
    ifd[0xA434] = "Testlens 35mm"  # LensModel
    ifd[0x9003] = "2024:05:01 12:30:00"  # DateTimeOriginal
    gps = exif.get_ifd(0x8825)  # GPS-IFD
    gps[1] = "N"
    gps[2] = GPS_LAT
    gps[3] = "E"
    gps[4] = (4, 53, 0)
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif, quality=80)
    return buf.getvalue()


def animated_gif() -> bytes:
    frames = [Image.new("RGB", (120, 80), color) for color in ("red", "green", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=200, loop=0)
    return buf.getvalue()


def outlined_pdf(pages: int = 9) -> bytes:
    """Een PDF met de hand: een inhoudsopgave (Inleiding, Hoofdstuk 1 op pagina 3,
    Paragraaf 1.1 op pagina 4) en paginalabels (i, ii, 1, 2, ...)."""
    first_page = 7
    page_ids = list(range(first_page, first_page + pages))
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R /Outlines 3 0 R /PageMode /UseOutlines "
        "/PageLabels << /Nums [0 << /S /r >> 2 << /S /D >>] >> >>",
        2: f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {pages} >>",
        3: "<< /Type /Outlines /First 4 0 R /Last 5 0 R /Count 3 >>",
        4: f"<< /Title (Inleiding) /Parent 3 0 R /Next 5 0 R /Dest [{page_ids[0]} 0 R /Fit] >>",
        5: "<< /Title (Hoofdstuk 1) /Parent 3 0 R /Prev 4 0 R /First 6 0 R /Last 6 0 R "
        f"/Count 1 /Dest [{page_ids[2]} 0 R /Fit] >>",
        6: f"<< /Title (Paragraaf 1.1) /Parent 5 0 R /Dest [{page_ids[3]} 0 R /Fit] >>",
    }
    for p in page_ids:
        objects[p] = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>"
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = out.tell()
        out.write(f"{number} 0 obj\n{objects[number]}\nendobj\n".encode())
    xref = out.tell()
    size = max(objects) + 1
    out.write(f"xref\n0 {size}\n0000000000 65535 f \n".encode())
    for number in range(1, size):
        out.write(f"{offsets[number]:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def _ffmpeg(work: Path, name: str, *args: str) -> bytes:
    target = work / name
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, str(target)],
        check=True,
        timeout=120,
    )
    return target.read_bytes()


def media(work: Path) -> dict[str, bytes]:
    video = ["-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=15"]
    audio = ["-f", "lavfi", "-i", "sine=frequency=440:duration=3"]
    return {
        "clip.webm": _ffmpeg(
            work,
            "clip.webm",
            *video,
            *audio,
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            "200k",
            "-c:a",
            "libopus",
            "-shortest",
        ),
        "liedje.mp3": _ffmpeg(work, "liedje.mp3", *audio, "-c:a", "libmp3lame", "-b:a", "64k"),
        "film.mp4": _ffmpeg(
            work,
            "film.mp4",
            *video,
            *audio,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
        ),
    }


def _ooxml(parts: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr(
            "_rels/.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{PKG_REL}"></Relationships>',
        )
        for name, data in parts.items():
            z.writestr(name, data)
    return buf.getvalue()


def docx() -> bytes:
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Kop1"/></w:pPr><w:r><w:t>Jaarverslag</w:t></w:r></w:p>'
        '<w:p><w:r><w:t xml:space="preserve">Een gewone alinea met </w:t></w:r>'
        "<w:r><w:rPr><w:b/></w:rPr><w:t>vet</w:t></w:r></w:p>"
    )
    return _ooxml(
        {
            "word/document.xml": f'<?xml version="1.0"?><w:document xmlns:w="{W_NS}">'
            f"<w:body>{body}</w:body></w:document>",
            "word/styles.xml": f'<?xml version="1.0"?><w:styles xmlns:w="{W_NS}">'
            '<w:style w:type="paragraph" w:styleId="Kop1"><w:name w:val="heading 1"/>'
            "</w:style></w:styles>",
        }
    )


def xlsx() -> bytes:
    sheet = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Product</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Prijs</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>Appels</t></is></c><c r="B2"><v>3.5</v></c>'
        "</row>"
    )
    return _ooxml(
        {
            "xl/workbook.xml": f'<?xml version="1.0"?><workbook xmlns="{S_NS}" '
            f'xmlns:r="{R_NS}"><sheets><sheet name="Fruit" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>",
            "xl/_rels/workbook.xml.rels": f'<?xml version="1.0"?><Relationships '
            f'xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{DOC_REL}/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
            "xl/worksheets/sheet1.xml": f'<?xml version="1.0"?><worksheet xmlns="{S_NS}">'
            f"<sheetData>{sheet}</sheetData></worksheet>",
        }
    )


def pptx() -> bytes:
    def shape(text: str, title: bool) -> str:
        ph = '<p:nvPr><p:ph type="title"/></p:nvPr>' if title else "<p:nvPr/>"
        return (
            f"<p:sp><p:nvSpPr>{ph}</p:nvSpPr><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r>"
            "</a:p></p:txBody></p:sp>"
        )

    slide = (
        f'<?xml version="1.0"?><p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}"><p:cSld><p:spTree>'
        f"{shape('Welkom bij de presentatie', True)}{shape('Eerste punt', False)}"
        "</p:spTree></p:cSld></p:sld>"
    )
    return _ooxml(
        {
            "ppt/slides/slide1.xml": slide,
            "ppt/presentation.xml": f'<?xml version="1.0"?><p:presentation xmlns:p="{P_NS}" '
            f'xmlns:r="{R_NS}"><p:sldIdLst><p:sldId id="256" r:id="rId10"/></p:sldIdLst>'
            '<p:sldSz cx="12192000" cy="6858000"/></p:presentation>',
            "ppt/_rels/presentation.xml.rels": f'<?xml version="1.0"?><Relationships '
            f'xmlns="{PKG_REL}"><Relationship Id="rId10" Type="{DOC_REL}/slide" '
            'Target="slides/slide1.xml"/></Relationships>',
        }
    )


def odt() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        z.writestr(
            "content.xml",
            f'<?xml version="1.0"?><office:document-content {ODF_NS}><office:body>'
            '<office:text><text:h text:outline-level="1">Notulen</text:h>'
            "<text:p>Afgesproken: alles gaat door.</text:p></office:text></office:body>"
            "</office:document-content>",
        )
    return buf.getvalue()


def samples(work: Path) -> dict[str, bytes]:
    """Alle voorbeeldbestanden, op naam (de volgorde in de map is op naam)."""
    work.mkdir(parents=True, exist_ok=True)
    files = {
        "foto.jpg": jpeg_with_gps(),
        "bewegend.gif": animated_gif(),
        "boek.pdf": outlined_pdf(),
        "oud.txt": CP1252_TEXT.encode("cp1252"),
        "notities.md": b"# Notities\n\nEen **vette** zin en een [link](javascript:alert(1)).\n\n"
        b"- een\n- twee\n\n<script>alert(1)</script>\n",
        "tabel.csv": b'naam,stad\n"Jansen, Piet",Utrecht\nAnna,"Den\nHaag"\n',
        "data.json": b'{"naam": "waarde", "getal": 42, "lijst": [true, null]}\n',
        "verslag.docx": docx(),
        "cijfers.xlsx": xlsx(),
        "dias.pptx": pptx(),
        "notulen.odt": odt(),
    }
    files.update(media(work))
    return files
