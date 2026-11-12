"""De gemaakte bestanden voor de tests van zoeken en foto's (bouwontwerp fase 6, §3.6).

Niets binairs in het repo: alles wordt bij de test gemaakt, met vaste zaden, dus elke keer
dezelfde bytes. Kleine bestanden komen als bytes terug:

    from tests.search import fixtures
    data = fixtures.pdf_tekst(paginas=3)        # PDF met een tekstlaag
    data = fixtures.foto_gps(52.0907, 5.1214)    # JPEG met EXIF en GPS

Wat groot is of uit veel bestanden bestaat (de scans voor OCR, de bijna-duplicaten van S1, de
zip-bom, de video met een plek) maakt make() één keer en bewaart het in
api/.pytest_cache/search/<hash>/<naam>, zoals de media-fixtures. De hash is die van deze
module, de versie van Pillow en het lettertype: verandert er iets, dan komt er een nieuwe map.
ZOEK_FIXTURES_CACHE zet een andere map (e2e/slim.sh doet dat: de broncode staat daar
alleen-lezen).

    pad = fixtures.make("bijna/brieven")         # een map met 150 brieven
    pad = fixtures.make("video/locatie.mp4")     # met ffmpeg: alleen onder marker media

Het lettertype van de scans is DejaVu Sans (uit Debian: fonts-dejavu-core). Ontbreekt dat,
dan tekent Pillow met zijn eigen lettertype; lettertype() zegt welk het werd, en
make("ocr/scans") schrijft het in info.json. Een OCR-test vergelijkt dan met wat erbij staat.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import math
import os
import random
import shutil
import subprocess
import tempfile
import time
import warnings
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import PIL
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, TiffImagePlugin

API = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get("ZOEK_FIXTURES_CACHE") or API / ".pytest_cache" / "search")
VERSION = 1  # hoger = alles opnieuw

# --- Woorden die de tests zoeken -----------------------------------------------------------

UTRECHT = (52.0907, 5.1214)  # de Domtoren
# In de PDF met tekst: ß en ẞ (Keuze 21), een zachte afbreking (U+00AD) en een emoji
PDF_WOORDEN = ("Straße", "GROẞE", "zoek­machine", "\N{PAGE FACING UP}")
# Een woord per plek in een kantoordocument: de hoofdtekst, voetnoot, kop- en voettekst,
# sprekersnotities, de laatste cel, en de titel en auteur (meta)
KANTOOR = {
    "tekst": "kantoorwoord",
    "voetnoot": "voetnootwoord",
    "kop": "kopwoord",
    "voet": "voettekstwoord",
    "notities": "sprekerwoord",
    "laatste_cel": "laatstecelwoord",
    "titel": "Jaarverslag kantoor",
    "auteur": "Eva Jansen",
}
OCR_WOORD = "ocrwoordx"
SCAN_HOEKEN = (0, 3, 7, -5, 90)
SCAN_TEKST = {
    "nl": (
        "Gemeente Utrecht",
        "Factuur 2024-117",
        "Datum: 3 november 2026",
        "Omschrijving: parkeervergunning",
        "Totaal inclusief btw: 123,45 euro",
        f"Kenmerk: {OCR_WOORD}",
    ),
    "en": (
        "City of Utrecht",
        "Invoice 2024-117",
        "Date: 3 November 2026",
        "Description: parking permit",
        "Total including VAT: 123.45 euro",
        f"Reference: {OCR_WOORD}",
    ),
}

# Punten voor plaatsen (Keuze 29): paren aan weerszijden van een grens, elk ±2–5 km van de
# grens (Natural Earth 10m is daar op ±1 km nauwkeurig), en punten op de Noordzee. Met de
# hand gekozen; Z3 controleert ze tegen de polygonen zodra die er zijn (zie z0-e2e.md).
GRENSPAREN = (
    (("Kerkrade", 50.8650, 6.0380, "NL"), ("Herzogenrath", 50.8710, 6.1000, "DE")),
    (("Zundert", 51.4700, 4.6600, "NL"), ("Wuustwezel", 51.3900, 4.5950, "BE")),
    (("Bergeijk", 51.3200, 5.3600, "NL"), ("Lommel", 51.2300, 5.3100, "BE")),
    (("Frisange", 49.5150, 6.1900, "LU"), ("Hettange-Grande", 49.4050, 6.1530, "FR")),
)
ZEE = (
    ("Noordzee voor Egmond", 52.6000, 4.2000),
    ("Noordzee boven Texel", 53.5000, 4.5000),
    ("Noordzee midden", 54.5000, 4.0000),
    ("Noordzee voor Walcheren", 51.7500, 3.0000),
    ("Doggersbank", 54.8000, 2.5000),
)


# --- Hulpjes --------------------------------------------------------------------------------


def _zaad(*delen: object) -> random.Random:
    return random.Random(":".join(str(d) for d in delen))


def _zip(delen: dict[str, bytes | str], *, eerst: str | None = None) -> bytes:
    """Een zip met vaste tijden (dezelfde bytes elke keer). eerst: zonder compressie vooraan
    (de mimetype van ODF)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        namen = sorted(delen, key=lambda n: (n != eerst, n))
        for naam in namen:
            info = zipfile.ZipInfo(naam, date_time=(2026, 11, 3, 12, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if naam == eerst else zipfile.ZIP_DEFLATED
            data = delen[naam]
            z.writestr(info, data.encode() if isinstance(data, str) else data)
    return buf.getvalue()


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Waar DejaVu Sans staat (Debian en Ubuntu, Fedora, met de hand)
LETTERTYPEN = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/local/share/fonts/DejaVuSans.ttf",
)


def lettertype(grootte: int = 36) -> tuple[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    """DejaVu Sans als het er is, anders het lettertype van Pillow (met een waarschuwing)."""
    for pad in LETTERTYPEN:
        if Path(pad).is_file():
            return "DejaVuSans", ImageFont.truetype(pad, grootte)
    warnings.warn(
        "DejaVu Sans ontbreekt (apt install fonts-dejavu-core): de scans gebruiken het "
        "lettertype van Pillow",
        stacklevel=2,
    )
    return "pillow-standaard", ImageFont.load_default(size=grootte)


# --- PDF ------------------------------------------------------------------------------------

# Een eenvoudig lettertype (Helvetica, WinAnsi) kent geen ẞ en geen emoji. Ze krijgen een
# vrije code, en de ToUnicode-tabel zegt wat die code is: zo staat de goede tekst in de
# tekstlaag, ook al tekent de viewer er een ander teken voor.
_EXTRA_CODES = {"\N{LATIN CAPITAL LETTER SHARP S}": 0x81, "\N{PAGE FACING UP}": 0x8D}


def _pdf_codes(text: str) -> bytes:
    uit = bytearray()
    for teken in text:
        if teken in _EXTRA_CODES:
            uit.append(_EXTRA_CODES[teken])
        else:
            uit += teken.encode("cp1252")
    return bytes(uit).replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _tounicode() -> bytes:
    paren: list[tuple[int, str]] = []
    for code in range(0x20, 0x100):
        try:
            paren.append((code, bytes([code]).decode("cp1252")))
        except UnicodeDecodeError:
            continue
    paren += [(code, teken) for teken, code in _EXTRA_CODES.items()]
    regels = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<00> <FF>",
        "endcodespacerange",
    ]
    for i in range(0, len(paren), 100):
        stuk = paren[i : i + 100]
        regels.append(f"{len(stuk)} beginbfchar")
        for code, teken in stuk:
            regels.append(f"<{code:02X}> <{teken.encode('utf-16-be').hex().upper()}>")
        regels.append("endbfchar")
    regels += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(regels).encode()


def _stream(data: bytes, extra: str = "") -> bytes:
    return f"<< /Length {len(data)}{extra} >>\nstream\n".encode() + data + b"\nendstream"


def _pdf(inhoud: list[bytes], *, titel: str = "", auteur: str = "") -> bytes:
    """Een PDF met deze content streams als pagina's (A4) en lettertype F1."""
    objecten: list[bytes] = [b"", b"", b"", b""]  # 1 catalogus, 2 pagina's, 3 font, 4 cmap
    kinderen = []
    for stroom in inhoud:
        objecten.append(_stream(stroom))
        objecten.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents "
            f"{len(objecten)} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        kinderen.append(len(objecten))
    objecten[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objecten[1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kinderen)}] "
        f"/Count {len(kinderen)} >>"
    ).encode()
    objecten[2] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding "
        b"/ToUnicode 4 0 R >>"
    )
    objecten[3] = _stream(_tounicode())
    info = ""
    if titel or auteur:
        objecten.append(
            b"<< /Title (" + _pdf_codes(titel) + b") /Author (" + _pdf_codes(auteur) + b") >>"
        )
        info = f" /Info {len(objecten)} 0 R"
    uit = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    plekken = []
    for nummer, body in enumerate(objecten, 1):
        plekken.append(len(uit))
        uit += f"{nummer} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(uit)
    uit += f"xref\n0 {len(objecten) + 1}\n0000000000 65535 f \n".encode()
    for plek in plekken:
        uit += f"{plek:010d} 00000 n \n".encode()
    uit += (
        f"trailer\n<< /Size {len(objecten) + 1} /Root 1 0 R{info} >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(uit)


def _kolom(x: int, regels: list[str]) -> bytes:
    stukken = [f"BT /F1 10 Tf 13 TL {x} 790 Td".encode()]
    for regel in regels:
        stukken.append(b"(" + _pdf_codes(regel) + b") Tj T*")
    stukken.append(b"ET")
    return b"\n".join(stukken)


def pdf_tekst(
    paginas: int = 3,
    *,
    tekst: str = "",
    titel: str = "Verslag met tekst",
    auteur: str = "Bram de Vries",
) -> bytes:
    """Een PDF met een tekstlaag: 1–20 pagina's in twee kolommen, met PDF_WOORDEN op elke
    pagina en het woord paginawoord<n> op pagina n. tekst: extra tekst op pagina 1."""
    if not 1 <= paginas <= 20:
        raise ValueError("1 tot en met 20 pagina's")
    rng = _zaad("pdf", paginas)
    vul = "de factuur voor het project staat in de bijlage van het verslag".split()
    inhoud = []
    for n in range(1, paginas + 1):
        links = [
            f"Pagina {n}: paginawoord{n} in de linkerkolom",
            f"De {PDF_WOORDEN[0]} en de {PDF_WOORDEN[1]} hal",
            f"Een {PDF_WOORDEN[2]} met zachte afbreking",
            f"Een emoji {PDF_WOORDEN[3]} midden in de zin",
        ]
        if n == 1 and tekst:
            links.insert(1, tekst)
        rechts = [f"Page {n}: the right column of the report"]
        for _ in range(40):
            links.append(" ".join(rng.choices(vul, k=6)))
            rechts.append(" ".join(rng.choices(vul, k=6)))
        inhoud.append(_kolom(50, links) + b"\n" + _kolom(310, rechts))
    return _pdf(inhoud, titel=titel, auteur=auteur)


def _ruis(breedte: int, hoogte: int, zaad: object, sigma: float) -> Image.Image:
    """Ruis rond grijs 128 met ongeveer deze sigma, vast per zaad. Niet Image.effect_noise: die
    gebruikt rand() van C, zonder zaad, en geeft elke keer andere bytes."""
    rng = _zaad("ruis", zaad)
    korrel = Image.frombytes("L", (breedte, hoogte), rng.randbytes(breedte * hoogte))
    schaal = sigma / 73.9  # de sigma van gelijkverdeeld 0..255
    return korrel.point([max(0, min(255, round(128 + (v - 127.5) * schaal))) for v in range(256)])


def _papier(breedte: int, hoogte: int, zaad: object, ruis: float = 12.0) -> Image.Image:
    """Grijs papier met ruis (vast per zaad)."""
    papier = Image.new("L", (breedte, hoogte), 232)
    return Image.blend(papier, _ruis(breedte, hoogte, ("papier", zaad), ruis), 0.12).convert("RGB")


def pdf_scan(woord: str = OCR_WOORD) -> bytes:
    """Een PDF als scan: één JPEG-pagina (A4, 100 dpi), zonder tekstlaag. woord staat alleen
    in het beeld."""
    _, font = lettertype(40)
    blad = _papier(827, 1169, ("scan", woord))
    teken = ImageDraw.Draw(blad)
    teken.text((80, 120), "Kassabon", fill=(30, 30, 30), font=font)
    teken.text((80, 220), f"Kenmerk: {woord}", fill=(30, 30, 30), font=font)
    buf = io.BytesIO()
    # Een vaste datum: Pillow zet anders nu in /CreationDate, en dan is de sha elke keer anders
    vast = time.gmtime(1_793_707_200)  # 2026-11-03 12:00 UTC
    blad.save(buf, "PDF", resolution=100.0, creationDate=vast, modDate=vast)
    return buf.getvalue()


# --- Kantoor: docx, xlsx, pptx, odt, ods, odp -----------------------------------------------

_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_PR = "http://schemas.openxmlformats.org/package/2006/relationships"
_RD = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_OFFICE = "application/vnd.openxmlformats-officedocument"


def _core(titel: str, auteur: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{_xml(titel)}</dc:title><dc:creator>{_xml(auteur)}</dc:creator>"
        "</cp:coreProperties>"
    )


def _rels(*rels: tuple[str, str, str]) -> str:
    regels = "".join(f'<Relationship Id="{i}" Type="{t}" Target="{doel}"/>' for i, t, doel in rels)
    kop = f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_PR}">'
    return f"{kop}{regels}</Relationships>"


def _types(*overrides: tuple[str, str]) -> str:
    regels = "".join(f'<Override PartName="{p}" ContentType="{t}"/>' for p, t in overrides)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="{_CT}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        f"{regels}</Types>"
    )


def _docx_delen(document: str, titel: str, auteur: str) -> dict[str, bytes | str]:
    def deel(soort: str, wortel: str, tekst: str) -> str:
        return (
            f'<?xml version="1.0" encoding="UTF-8"?><w:{wortel} xmlns:w="{_W}">'
            f"{soort}<w:p><w:r><w:t>{_xml(tekst)}</w:t></w:r></w:p></w:{wortel}>"
        )

    voetnoten = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:footnotes xmlns:w="{_W}">'
        '<w:footnote w:id="1"><w:p><w:r><w:t>'
        f"Een voetnoot met {KANTOOR['voetnoot']}.</w:t></w:r></w:p></w:footnote></w:footnotes>"
    )
    return {
        "[Content_Types].xml": _types(
            ("/word/document.xml", f"{_OFFICE}.wordprocessingml.document.main+xml"),
            ("/word/footnotes.xml", f"{_OFFICE}.wordprocessingml.footnotes+xml"),
            ("/word/header1.xml", f"{_OFFICE}.wordprocessingml.header+xml"),
            ("/word/footer1.xml", f"{_OFFICE}.wordprocessingml.footer+xml"),
            ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
        ),
        "_rels/.rels": _rels(
            ("rId1", f"{_RD}/officeDocument", "word/document.xml"),
            (
                "rId2",
                "http://schemas.openxmlformats.org/package/2006/relationships/metadata/"
                "core-properties",
                "docProps/core.xml",
            ),
        ),
        "word/_rels/document.xml.rels": _rels(
            ("rId1", f"{_RD}/footnotes", "footnotes.xml"),
            ("rId2", f"{_RD}/header", "header1.xml"),
            ("rId3", f"{_RD}/footer", "footer1.xml"),
        ),
        "word/document.xml": document,
        "word/footnotes.xml": voetnoten,
        "word/header1.xml": deel("", "hdr", f"Koptekst met {KANTOOR['kop']}"),
        "word/footer1.xml": deel("", "ftr", f"Voettekst met {KANTOOR['voet']}"),
        "docProps/core.xml": _core(titel, auteur),
    }


def docx(
    tekst: str = "", *, titel: str = KANTOOR["titel"], auteur: str = KANTOOR["auteur"]
) -> bytes:
    """Een docx met een kop, alinea's, een voetnoot, kop- en voettekst, en titel en auteur."""
    alineas = [f"De hoofdtekst met {KANTOOR['tekst']} en de Straße.", tekst]
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Verslag</w:t></w:r></w:p>'
        + "".join(f"<w:p><w:r><w:t>{_xml(a)}</w:t></w:r></w:p>" for a in alineas if a)
        + '<w:p><w:r><w:t xml:space="preserve">Zie de noot </w:t></w:r>'
        '<w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
        '<w:sectPr><w:headerReference w:type="default" r:id="rId2"/>'
        '<w:footerReference w:type="default" r:id="rId3"/></w:sectPr>'
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_W}" xmlns:r="{_RD}">'
        f"<w:body>{body}</w:body></w:document>"
    )
    return _zip(_docx_delen(document, titel, auteur))


def _kolomletters(n: int) -> str:
    letters = ""
    n += 1
    while n:
        n, rest = divmod(n - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _cellen(cellen: int, kolommen: int = 20) -> Iterator[tuple[int, int, str | float]]:
    """De inhoud van een werkblad: rij, kolom en waarde; de laatste cel is een woord."""
    rng = _zaad("cellen", cellen)
    for i in range(cellen):
        rij, kolom = divmod(i, kolommen)
        if i == cellen - 1:
            yield rij, kolom, KANTOOR["laatste_cel"]
        elif kolom == 0:
            yield rij, kolom, f"rij {rij + 1}"
        else:
            yield rij, kolom, round(rng.uniform(0, 10_000), 2)


def xlsx(cellen: int = 20_000) -> bytes:
    """Een xlsx met één werkblad van `cellen` cellen (20 kolommen), gedeelde tekst."""
    gedeeld: list[str] = []
    rijen: dict[int, list[str]] = {}
    for rij, kolom, waarde in _cellen(cellen):
        ref = f"{_kolomletters(kolom)}{rij + 1}"
        if isinstance(waarde, str):
            gedeeld.append(waarde)
            cel = f'<c r="{ref}" t="s"><v>{len(gedeeld) - 1}</v></c>'
        else:
            cel = f'<c r="{ref}"><v>{waarde}</v></c>'
        rijen.setdefault(rij, []).append(cel)
    s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    blad = (
        f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{s}"><sheetData>'
        + "".join(f'<row r="{r + 1}">{"".join(c)}</row>' for r, c in rijen.items())
        + "</sheetData></worksheet>"
    )
    teksten = "".join(f"<si><t>{_xml(t)}</t></si>" for t in gedeeld)
    return _zip(
        {
            "[Content_Types].xml": _types(
                ("/xl/workbook.xml", f"{_OFFICE}.spreadsheetml.sheet.main+xml"),
                ("/xl/worksheets/sheet1.xml", f"{_OFFICE}.spreadsheetml.worksheet+xml"),
                ("/xl/sharedStrings.xml", f"{_OFFICE}.spreadsheetml.sharedStrings+xml"),
                (
                    "/docProps/core.xml",
                    "application/vnd.openxmlformats-package.core-properties+xml",
                ),
            ),
            "_rels/.rels": _rels(
                ("rId1", f"{_RD}/officeDocument", "xl/workbook.xml"),
                (
                    "rId2",
                    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/"
                    "core-properties",
                    "docProps/core.xml",
                ),
            ),
            "docProps/core.xml": _core(KANTOOR["titel"], KANTOOR["auteur"]),
            "xl/workbook.xml": (
                f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{s}" '
                f'xmlns:r="{_RD}"><sheets><sheet name="Cijfers" sheetId="1" r:id="rId1"/>'
                "</sheets></workbook>"
            ),
            "xl/_rels/workbook.xml.rels": _rels(
                ("rId1", f"{_RD}/worksheet", "worksheets/sheet1.xml"),
                ("rId2", f"{_RD}/sharedStrings", "sharedStrings.xml"),
            ),
            "xl/worksheets/sheet1.xml": blad,
            "xl/sharedStrings.xml": (
                f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="{s}" count="{len(gedeeld)}" '
                f'uniqueCount="{len(gedeeld)}">{teksten}</sst>'
            ),
        }
    )


def pptx() -> bytes:
    """Een pptx met één dia (titel en opsomming) en sprekersnotities."""
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"

    def vorm(soort: str, tekst: str) -> str:
        ph = f'<p:nvPr><p:ph type="{soort}"/></p:nvPr>' if soort else "<p:nvPr/>"
        return (
            f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="v"/><p:cNvSpPr/>{ph}</p:nvSpPr>'
            f"<p:txBody><a:p><a:r><a:t>{_xml(tekst)}</a:t></a:r></a:p></p:txBody></p:sp>"
        )

    def dia(wortel: str, vormen: str) -> str:
        return (
            f'<?xml version="1.0" encoding="UTF-8"?><p:{wortel} xmlns:p="{p}" xmlns:a="{a}" '
            f'xmlns:r="{_RD}"><p:cSld><p:spTree>{vormen}</p:spTree></p:cSld></p:{wortel}>'
        )

    return _zip(
        {
            "[Content_Types].xml": _types(
                ("/ppt/presentation.xml", f"{_OFFICE}.presentationml.presentation.main+xml"),
                ("/ppt/slides/slide1.xml", f"{_OFFICE}.presentationml.slide+xml"),
                ("/ppt/notesSlides/notesSlide1.xml", f"{_OFFICE}.presentationml.notesSlide+xml"),
                (
                    "/docProps/core.xml",
                    "application/vnd.openxmlformats-package.core-properties+xml",
                ),
            ),
            "_rels/.rels": _rels(
                ("rId1", f"{_RD}/officeDocument", "ppt/presentation.xml"),
                (
                    "rId2",
                    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/"
                    "core-properties",
                    "docProps/core.xml",
                ),
            ),
            "ppt/presentation.xml": (
                f'<?xml version="1.0" encoding="UTF-8"?><p:presentation xmlns:p="{p}" '
                f'xmlns:r="{_RD}"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
                '<p:sldSz cx="12192000" cy="6858000"/></p:presentation>'
            ),
            "ppt/_rels/presentation.xml.rels": _rels(("rId1", f"{_RD}/slide", "slides/slide1.xml")),
            "ppt/slides/slide1.xml": dia(
                "sld", vorm("title", "Kwartaalcijfers") + vorm("body", KANTOOR["tekst"])
            ),
            "ppt/slides/_rels/slide1.xml.rels": _rels(
                ("rId1", f"{_RD}/notesSlide", "../notesSlides/notesSlide1.xml")
            ),
            "ppt/notesSlides/notesSlide1.xml": dia(
                "notes", vorm("body", f"Notities met {KANTOOR['notities']}")
            ),
            "docProps/core.xml": _core(KANTOOR["titel"], KANTOOR["auteur"]),
        }
    )


_ODF_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" office:version="1.3"'
)


def _odf(soort: str, body: str, stijlen: str = "") -> bytes:
    mime = f"application/vnd.oasis.opendocument.{soort}"
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest='
        '"urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">'
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mime}"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
        "</manifest:manifest>"
    )
    meta = (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-meta {_ODF_NS}><office:meta>'
        f"<dc:title>{_xml(KANTOOR['titel'])}</dc:title>"
        f"<meta:initial-creator>{_xml(KANTOOR['auteur'])}</meta:initial-creator>"
        "</office:meta></office:document-meta>"
    )
    return _zip(
        {
            "mimetype": mime,
            "META-INF/manifest.xml": manifest,
            "content.xml": (
                f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {_ODF_NS}>'
                f"<office:body>{body}</office:body></office:document-content>"
            ),
            "styles.xml": (
                f'<?xml version="1.0" encoding="UTF-8"?><office:document-styles {_ODF_NS}>'
                f"{stijlen}</office:document-styles>"
            ),
            "meta.xml": meta,
        },
        eerst="mimetype",
    )


def odt(tekst: str = "") -> bytes:
    """Een odt met een kop, alinea's, een voetnoot en kop- en voettekst (in styles.xml)."""
    alineas = [f"De hoofdtekst met {KANTOOR['tekst']} en de Straße.", tekst]
    body = (
        '<office:text><text:h text:outline-level="1">Verslag</text:h>'
        + "".join(f"<text:p>{_xml(a)}</text:p>" for a in alineas if a)
        + '<text:p>Zie de noot<text:note text:id="n1" text:note-class="footnote">'
        "<text:note-citation>1</text:note-citation><text:note-body><text:p>"
        f"Een voetnoot met {KANTOOR['voetnoot']}.</text:p></text:note-body></text:note></text:p>"
        "</office:text>"
    )
    stijlen = (
        '<office:master-styles><style:master-page style:name="Standard">'
        f"<style:header><text:p>Koptekst met {KANTOOR['kop']}</text:p></style:header>"
        f"<style:footer><text:p>Voettekst met {KANTOOR['voet']}</text:p></style:footer>"
        "</style:master-page></office:master-styles>"
    )
    return _odf("text", body, stijlen)


def ods(cellen: int = 20_000) -> bytes:
    """Een ods met één werkblad van `cellen` cellen (20 kolommen)."""
    rijen: dict[int, list[str]] = {}
    for rij, _kolom, waarde in _cellen(cellen):
        if isinstance(waarde, str):
            cel = f'<table:table-cell office:value-type="string"><text:p>{_xml(waarde)}</text:p>'
        else:
            cel = (
                f'<table:table-cell office:value-type="float" office:value="{waarde}">'
                f"<text:p>{waarde}</text:p>"
            )
        rijen.setdefault(rij, []).append(cel + "</table:table-cell>")
    body = (
        '<office:spreadsheet><table:table table:name="Cijfers">'
        + "".join(f"<table:table-row>{''.join(c)}</table:table-row>" for c in rijen.values())
        + "</table:table></office:spreadsheet>"
    )
    return _odf("spreadsheet", body)


def odp() -> bytes:
    """Een odp met één dia (titel en tekst) en sprekersnotities."""

    def kader(klasse: str, tekst: str) -> str:
        return (
            f'<draw:frame presentation:class="{klasse}" svg:width="20cm" svg:height="3cm">'
            f"<draw:text-box><text:p>{_xml(tekst)}</text:p></draw:text-box></draw:frame>"
        )

    body = (
        '<office:presentation><draw:page draw:name="dia1">'
        + kader("title", "Kwartaalcijfers")
        + kader("outline", KANTOOR["tekst"])
        + "<presentation:notes>"
        + kader("notes", f"Notities met {KANTOOR['notities']}")
        + "</presentation:notes></draw:page></office:presentation>"
    )
    return _odf("presentation", body)


def xml_entiteit() -> bytes:
    """Een docx met een "billion laughs" in document.xml: een DOCTYPE met entiteiten."""
    lagen = ['<!ENTITY lol0 "lol">'] + [
        f'<!ENTITY lol{i} "{f"&lol{i - 1};" * 10}">' for i in range(1, 10)
    ]
    document = (
        '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE w:document ['
        + "".join(lagen)
        + f']><w:document xmlns:w="{_W}"><w:body><w:p><w:r><w:t>&lol9;</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    return _zip(_docx_delen(document, KANTOOR["titel"], KANTOOR["auteur"]))


def _zip_bom(uit: Path, megabytes: int = 512) -> None:
    """Een docx waarvan document.xml uitgepakt `megabytes` MB is (gepakt ±0,5 MB)."""
    delen = _docx_delen("", KANTOOR["titel"], KANTOOR["auteur"])
    del delen["word/document.xml"]
    with zipfile.ZipFile(uit, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for naam, data in sorted(delen.items()):
            z.writestr(zipfile.ZipInfo(naam, date_time=(2026, 11, 3, 12, 0, 0)), data)
        info = zipfile.ZipInfo("word/document.xml", date_time=(2026, 11, 3, 12, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        with z.open(info, "w", force_zip64=True) as f:
            f.write(f'<w:document xmlns:w="{_W}"><w:body><w:p><w:r><w:t>'.encode())
            blok = b"a" * (1 << 20)
            for _ in range(megabytes):
                f.write(blok)
            f.write(b"</w:t></w:r></w:p></w:body></w:document>")


# --- Scans voor OCR -------------------------------------------------------------------------


def scan(taal: str = "nl", hoek: float = 0, formaat: str = "png") -> bytes:
    """Een gescande brief (150 dpi) met SCAN_TEKST[taal] op grijs papier met ruis, gedraaid
    over `hoek` graden (tegen de klok in), als PNG of JPEG (q80)."""
    _, font = lettertype(36)
    # Grijswaarden, zoals een scanner die meestal geeft (en drie keer minder om te schrijven)
    blad = _papier(1240, 1754, ("ocr", taal)).convert("L")
    teken = ImageDraw.Draw(blad)
    for i, regel in enumerate(SCAN_TEKST[taal]):
        teken.text((140, 200 + i * 90), regel, fill=25, font=font)
    if hoek:
        blad = blad.rotate(hoek, expand=True, fillcolor=232, resample=Image.BICUBIC)
    buf = io.BytesIO()
    if formaat == "png":
        blad.save(buf, "PNG", compress_level=1, dpi=(150, 150))
    elif formaat == "jpg":
        blad.save(buf, "JPEG", quality=80, dpi=(150, 150))
    else:
        raise ValueError(formaat)
    return buf.getvalue()


def _scans(uit: Path) -> None:
    uit.mkdir()
    naam, _ = lettertype()
    for taal in SCAN_TEKST:
        for hoek in SCAN_HOEKEN:
            for formaat in ("png", "jpg"):
                (uit / f"{taal}_{hoek}.{formaat}").write_bytes(scan(taal, hoek, formaat))
    info = {"lettertype": naam, "hoeken": SCAN_HOEKEN, "tekst": SCAN_TEKST}
    (uit / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1))


# --- Foto's met EXIF en GPS -----------------------------------------------------------------


def beeld(zaad: object, breedte: int = 640, hoogte: int = 480) -> Image.Image:
    """Een "foto": een verloop met wat vormen, vast per zaad en elk zaad anders."""
    rng = _zaad("beeld", zaad)
    a = [rng.randint(0, 255) for _ in range(3)]
    b = [rng.randint(0, 255) for _ in range(3)]
    verloop = Image.linear_gradient("L").resize((breedte, hoogte))
    img = Image.composite(Image.new("RGB", (breedte, hoogte), tuple(a)),
                          Image.new("RGB", (breedte, hoogte), tuple(b)), verloop)  # fmt: skip
    teken = ImageDraw.Draw(img)
    for _ in range(rng.randint(4, 9)):
        x, y = rng.randint(0, breedte), rng.randint(0, hoogte)
        r = rng.randint(hoogte // 20, hoogte // 4)
        kleur = tuple(rng.randint(0, 255) for _ in range(3))
        if rng.random() < 0.5:
            teken.ellipse((x - r, y - r, x + r, y + r), fill=kleur)
        else:
            teken.rectangle((x - r, y - r // 2, x + r, y + r // 2), fill=kleur)
    return img


def _dms(waarde: float) -> tuple[float, float, float]:
    """Graden, minuten en seconden (op 4 decimalen), minuten en seconden altijd onder 60.
    Eerst de seconden in totaal afronden en dan delen: los gerekend gaf 54,8 (54, 47, 60.0) en
    6,1 (6, 5, 60.0), en die plek leest render_meta niet (seconden onder 60), dus zonder GPS."""
    totaal = round(abs(waarde) * 3600, 4)
    graden, rest = divmod(totaal, 3600)
    minuten, seconden = divmod(rest, 60)
    return (float(graden), float(minuten), round(seconden, 4))


def exif(
    lat: float | None = None,
    lon: float | None = None,
    *,
    alt: float | None = None,
    genomen: str = "2024:06:01 14:30:00",
    camera: tuple[str, str] = ("FUJIFILM", "X-T30"),
    draaiing: int | None = None,
) -> Image.Exif:
    """EXIF met merk, model, datum, en (als lat en lon er zijn) de GPS-IFD."""
    ex = Image.Exif()
    ex[0x010F], ex[0x0110] = camera
    if draaiing is not None:
        ex[0x0112] = draaiing
    ex.get_ifd(0x8769)[0x9003] = genomen
    if lat is not None and lon is not None:
        gps = {1: "N" if lat >= 0 else "S", 2: _dms(lat), 3: "E" if lon >= 0 else "W", 4: _dms(lon)}
        if alt is not None:
            gps[5] = 0 if alt >= 0 else 1
            gps[6] = abs(alt)
        ex[0x8825] = gps
    return ex


def _jpeg(img: Image.Image, ex: Image.Exif | None = None, **opties) -> bytes:
    buf = io.BytesIO()
    if ex is not None:
        opties["exif"] = ex.tobytes()
    img.save(buf, "JPEG", quality=opties.pop("quality", 88), **opties)
    return buf.getvalue()


def foto_gps(lat: float, lon: float, *, alt: float | None = None, zaad: object = "gps") -> bytes:
    """Een JPEG met EXIF (FUJIFILM X-T30, 1 juni 2024) en GPS."""
    return _jpeg(beeld(zaad), exif(lat, lon, alt=alt))


def foto_zonder_gps(zaad: object = "zonder") -> bytes:
    return _jpeg(beeld(zaad), exif())


def foto_klein(data: bytes, factor: float = 0.5) -> bytes:
    """Een verkleinde kopie van een JPEG, met dezelfde EXIF (een eigen sha, bijna gelijk)."""
    img = Image.open(io.BytesIO(data))
    ex = img.getexif()
    klein = img.resize((int(img.width * factor), int(img.height * factor)), Image.LANCZOS)
    return _jpeg(klein, ex)


def foto_draaiing6() -> bytes:
    """Een telefoonfoto (staand opgeslagen als liggend) met EXIF-draaiing 6 en GPS."""
    return _jpeg(
        beeld("telefoon", 800, 600),
        exif(*UTRECHT, camera=("Apple", "iPhone 13"), draaiing=6),
    )


def heic_gps(lat: float = UTRECHT[0], lon: float = UTRECHT[1]) -> bytes:
    """Een HEIC (pillow-heif) met EXIF en GPS."""
    import pillow_heif

    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    ex = exif(lat, lon, camera=("Apple", "iPhone 13"))
    beeld("heic").save(buf, "HEIF", quality=50, exif=ex.tobytes())
    return buf.getvalue()


def _xmp_graad(waarde: float, plus: str, min_: str) -> str:
    graden = int(abs(waarde))
    minuten = (abs(waarde) - graden) * 60
    return f"{graden},{minuten:.4f}{plus if waarde >= 0 else min_}"


def xmp_gps(lat: float = UTRECHT[0], lon: float = UTRECHT[1], alt: float = 5.0) -> bytes:
    """Een JPEG zonder GPS in EXIF, met GPS in XMP (exif:GPSLatitude, -Longitude, -Altitude)."""
    xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf='
        '"http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" '
        'xmlns:exif="http://ns.adobe.com/exif/1.0/" '
        f'exif:GPSLatitude="{_xmp_graad(lat, "N", "S")}" '
        f'exif:GPSLongitude="{_xmp_graad(lon, "E", "W")}" '
        f'exif:GPSAltitude="{int(alt * 10)}/10"/></rdf:RDF></x:xmpmeta>'
    )
    return _jpeg(beeld("xmp"), exif(), xmp=xmp.encode())


def xmp_laughs() -> bytes:
    """Een JPEG met een "billion laughs" in XMP (een DOCTYPE met entiteiten)."""
    lagen = ['<!ENTITY lol0 "lol">'] + [
        f'<!ENTITY lol{i} "{f"&lol{i - 1};" * 10}">' for i in range(1, 10)
    ]
    xmp = (
        '<?xml version="1.0"?><!DOCTYPE x:xmpmeta [' + "".join(lagen) + "]>"
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf='
        '"http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" '
        'xmlns:exif="http://ns.adobe.com/exif/1.0/" exif:GPSLatitude="&lol9;"/>'
        "</rdf:RDF></x:xmpmeta>"
    )
    return _jpeg(beeld("laughs"), exif(), xmp=xmp.encode())


def gps_nan() -> bytes:
    """GPS met IFDRational(0, 0) als breedte: dat is NaN, dus geen plek."""
    ex = exif()
    nul = TiffImagePlugin.IFDRational(0, 0)
    ex[0x8825] = {1: "N", 2: (nul, nul, nul), 3: "E", 4: _dms(UTRECHT[1])}
    return _jpeg(beeld("nan"), ex)


def gps_zonder_ref() -> bytes:
    """GPS zonder GPSLatitudeRef en GPSLongitudeRef: ook geen plek."""
    ex = exif()
    ex[0x8825] = {2: _dms(UTRECHT[0]), 4: _dms(UTRECHT[1])}
    return _jpeg(beeld("zonder-ref"), ex)


def gps_nul() -> bytes:
    """GPS op (0, 0): "geen plek"."""
    return _jpeg(beeld("nul"), exif(0.0, 0.0))


def _video(uit: Path, *, iso6709: bool) -> None:
    """Een seconde video met een plek: ©xyz (location) of de Apple-sleutel ISO 6709."""
    lat, lon = UTRECHT
    plek = f"{lat:+08.4f}{lon:+09.4f}/"
    opties = ["-metadata", f"location={plek}"]
    if iso6709:
        opties = [
            "-movflags", "use_metadata_tags",
            "-metadata", f"com.apple.quicktime.location.ISO6709={plek}",
        ]  # fmt: skip
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
         *opties, str(uit)],
        check=True, timeout=120,
    )  # fmt: skip


# --- Bijna-duplicaten (S1) ------------------------------------------------------------------


def _varianten(uit: Path) -> None:
    """Tien originelen, elk met vier varianten: kleiner, harder gecomprimeerd, bijgesneden
    en lichter. Een variant hoort bij zijn origineel (zeker), niet bij een ander."""
    uit.mkdir()
    for i in range(10):
        img = beeld(("origineel", i), 480, 360)
        (uit / f"orig-{i}.jpg").write_bytes(_jpeg(img))
        (uit / f"orig-{i}-klein.jpg").write_bytes(_jpeg(img.resize((240, 180), Image.LANCZOS)))
        (uit / f"orig-{i}-q40.jpg").write_bytes(_jpeg(img, quality=40))
        rand = img.crop((14, 10, 466, 350)).resize((480, 360), Image.LANCZOS)
        (uit / f"orig-{i}-bijgesneden.jpg").write_bytes(_jpeg(rand))
        (uit / f"orig-{i}-lichter.jpg").write_bytes(
            _jpeg(ImageEnhance.Brightness(img).enhance(1.12))
        )


_NAMEN = "Jansen de Vries Bakker Visser Smit Meijer de Boer Mulder de Groot Bos Vos Peters".split()


def _brieven(uit: Path) -> None:
    """150 brieven op één sjabloon (briefhoofd, adres, datum), elk met andere tekst."""
    uit.mkdir()
    _, font = lettertype(16)
    rng = _zaad("brieven")
    for i in range(150):
        blad = Image.new("RGB", (420, 594), "white")
        teken = ImageDraw.Draw(blad)
        teken.rectangle((30, 24, 120, 70), fill=(20, 60, 140))
        teken.text((140, 34), "Gemeente Voorbeeld", fill="black", font=font)
        teken.text(
            (30, 110), f"Aan: {rng.choice(_NAMEN)} {rng.choice(_NAMEN)}", fill="black", font=font
        )
        teken.text((30, 134), f"Kenmerk {rng.randint(1000, 9999)}-{i}", fill="black", font=font)
        for r in range(rng.randint(6, 14)):
            woorden = " ".join(rng.choices("de het een factuur betaling termijn".split(), k=5))
            teken.text((30, 190 + r * 22), woorden, fill=(40, 40, 40), font=font)
        (uit / f"brief-{i:03d}.png").write_bytes(_png(blad))


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _formulieren(uit: Path) -> None:
    """30 gescande formulieren: dezelfde vakken, andere invulling, scheef en met ruis."""
    uit.mkdir()
    _, font = lettertype(18)
    rng = _zaad("formulieren")
    for i in range(30):
        blad = _papier(420, 594, ("formulier", i))
        teken = ImageDraw.Draw(blad)
        for r, label in enumerate(("Naam", "Adres", "Datum", "Bedrag")):
            y = 60 + r * 70
            teken.text((30, y), label, fill=(60, 60, 60), font=font)
            teken.rectangle((140, y - 6, 390, y + 30), outline=(60, 60, 60), width=2)
            invul = f"{rng.choice(_NAMEN)} {rng.randint(1, 999)}"
            teken.text((150, y), invul, fill=(10, 10, 120), font=font)
        blad = blad.rotate(rng.uniform(-1.5, 1.5), fillcolor=(232, 232, 232))
        (uit / f"formulier-{i:02d}.jpg").write_bytes(_jpeg(blad, quality=75))


def _schermen(uit: Path) -> None:
    """30 schermafdrukken van één app: dezelfde balken en knoppen, andere tekst."""
    uit.mkdir()
    _, font = lettertype(15)
    rng = _zaad("schermen")
    for i in range(30):
        scherm = Image.new("RGB", (640, 400), (246, 246, 248))
        teken = ImageDraw.Draw(scherm)
        teken.rectangle((0, 0, 640, 40), fill=(32, 92, 168))
        teken.rectangle((0, 40, 150, 400), fill=(228, 230, 235))
        for k in range(5):
            teken.rectangle((16, 60 + k * 36, 134, 84 + k * 36), fill=(210, 214, 222))
        for r in range(8):
            regel = " ".join(
                rng.choices("bericht van vandaag over het project en de planning".split(), k=6)
            )
            teken.text((170, 60 + r * 30), regel, fill=(30, 30, 30), font=font)
        (uit / f"scherm-{i:02d}.png").write_bytes(_png(scherm))


def _egaal(uit: Path) -> None:
    """150 bijna egale foto's: 50 zwart, 50 lucht en 50 muur, met wat ruis."""
    uit.mkdir()
    for soort, kleur in (
        ("zwart", (6, 6, 8)),
        ("lucht", (120, 170, 225)),
        ("muur", (214, 200, 176)),
    ):
        for i in range(50):
            img = Image.new("RGB", (320, 240), kleur)
            if soort == "lucht":
                verloop = Image.linear_gradient("L").resize((320, 240))
                img = Image.composite(img, Image.new("RGB", (320, 240), (180, 210, 240)), verloop)
            ruis = _ruis(320, 240, (soort, i), 6 + i % 7).convert("RGB")
            img = Image.blend(img, ruis, 0.04 + (i % 5) / 100)
            (uit / f"{soort}-{i:02d}.jpg").write_bytes(_jpeg(img, quality=85))


def _anders(uit: Path) -> None:
    """50 foto's die niet op elkaar lijken."""
    uit.mkdir()
    for i in range(50):
        (uit / f"anders-{i:02d}.jpg").write_bytes(_jpeg(beeld(("anders", i), 480, 360)))


# --- make(): gemaakt en bewaard -------------------------------------------------------------

RECEPTEN: dict[str, Callable[[Path], None]] = {
    "ocr/scans": _scans,
    "kantoor/zip-bom.docx": _zip_bom,
    "bijna/varianten": _varianten,
    "bijna/brieven": _brieven,
    "bijna/formulieren": _formulieren,
    "bijna/schermen": _schermen,
    "bijna/egaal": _egaal,
    "bijna/anders": _anders,
    # Alleen met ffmpeg (marker media)
    "video/locatie.mp4": lambda uit: _video(uit, iso6709=False),
    "video/iso6709.mov": lambda uit: _video(uit, iso6709=True),
}


def sleutel() -> str:
    """De hash van deze module, Pillow en het lettertype: een andere waarde, een nieuwe map."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        naam, _ = lettertype()
    bron = Path(__file__).read_bytes()
    tekst = f"{VERSION}|{PIL.__version__}|{naam}|".encode() + bron
    return hashlib.sha256(tekst).hexdigest()[:16]


@contextlib.contextmanager
def _slot() -> Iterator[None]:
    CACHE.mkdir(parents=True, exist_ok=True)
    with open(CACHE / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def make(naam: str) -> Path:
    """Het pad naar fixture `naam` (een bestand of een map), zo nodig eerst gemaakt."""
    recept = RECEPTEN[naam]
    doel = CACHE / sleutel() / naam
    if doel.exists():
        return doel
    with _slot():
        if doel.exists():
            return doel
        doel.parent.mkdir(parents=True, exist_ok=True)
        werk = Path(tempfile.mkdtemp(dir=CACHE, prefix=".maak-"))
        try:
            uit = werk / Path(naam).name
            recept(uit)
            os.replace(uit, doel)
        finally:
            shutil.rmtree(werk, ignore_errors=True)
    return doel


def prune() -> None:
    """Gooit de mappen van andere versies weg."""
    if not CACHE.is_dir():
        return
    houden = sleutel()
    for pad in CACHE.iterdir():
        if pad.is_dir() and pad.name != houden:
            shutil.rmtree(pad, ignore_errors=True)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """De afstand tussen twee punten (lat, lon) in km."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))
