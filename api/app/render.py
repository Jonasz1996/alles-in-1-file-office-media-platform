"""Een preview maken van één bestand. Draait in een eigen proces (zie previews.py): een kapot
of kwaadaardig bestand kan zo hooguit dit proces laten vastlopen of crashen, niet de server.
Bovenaan alleen de standaardbibliotheek, zodat het proces snel start; Pillow en de rest
pas als ze nodig zijn.

Verzoek als JSON op stdin: {"source", "out", "work", "mime", "name", "max_pixels",
"memory_mb"}, en met media aan "worker": {"socket", "key"} (fase 5, previews.py). Antwoord
als JSON op stdout: {"state": "ready" | "none", "meta": {...}, "sizes": [1024, 256]}; voor
een video ook {"state": "defer", "seconds"} als de mediaworker weg of druk is. De
afbeeldingen komen in out/<grootte>.webp (HEIC, HEIF, TIFF, PSD, JPEG 2000 en camera-RAW ook
2048.webp: die kan de browser zelf niet tonen); een kantoordocument geeft ook out/doc.json
(app/office/reader.py). work is voor tussenbestanden. Een onverwachte fout eindigt met een exitcode
en de melding op stderr.

Met een veld "pdf" (een pad) maakt hij de thumbnails uit die PDF in plaats van uit de bron:
de omgezette PDF van een kantoordocument (fase 4, app/office/convert.py).

Met "first" (de eerste ronde van previews.make) alleen 256.webp als dat snel kan: een JPEG (ook
de ingebedde van een camera-RAW) met draft, een HEIC met zijn ingebedde thumbnail. Het antwoord
heeft dan "more": de grote maten volgen in een tweede ronde. Met "nice" draait het proces zoveel
stappen lager (achtergrondwerk; de API en het afspelen gaan voor).

Andere soorten verzoeken (veld "mode"):
- "page": {"source", "out", "pages": [n, ...], "width"} -> pagina's van een PDF als
  out/<n>.webp, zo breed als width (de viewer, routers/viewer.py);
- "docinfo": {"source"} -> {"state", "pages", "page_sizes", "toc", "labels"} van een PDF;
- "meta": alleen metadata van veel bestanden (render_meta.run_meta, P5);
- "text": de tekst uit documenten (fase 6, Z2): zie run_text en text_child;
- "ocr_page": één pagina of foto door tesseract (fase 6, X1; nu een stub).

serve() is de forkserver van previews.py: één voorgeladen proces dat per verzoek een kind maakt
met fork, in plaats van elke keer een nieuwe Python met Pillow (zie daar).
"""

import asyncio
import base64
import codecs
import contextlib
import io
import json
import lzma
import mmap
import os
import posixpath
import resource
import selectors
import shutil
import signal
import struct
import sys
import tarfile
import traceback
import warnings
import zipfile
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

from app import render_meta  # ook alleen de standaardbibliotheek

SIZES = (1024, 256)  # groot eerst: de thumbnail wordt uit de grote gemaakt
DISPLAY = 2048  # extra groot voor wat de browser zelf niet kan tonen
DISPLAY_FORMATS = {"HEIF", "TIFF", "PSD", "JPEG2000"}
THUMB = 256
HEAD = 8192
TEXT_BYTES = 4096
TEXT_LINES = 50
MAX_ENTRIES = 200
NAME_CHARS = 500
LIST_SECONDS = 15  # zo lang mag het uitlezen van een (ingepakt) archief duren
FFMPEG_SECONDS = 60  # per vraag aan de mediaworker (meta, frame)
EMBEDDED_BYTES = 32 * 1024 * 1024  # hoes van een album of e-book
XML_BYTES = 4 * 1024 * 1024
WEBP_QUALITY = 80
# Zo hard comprimeert WebP (0-6): 256.webp het hardst (die laadt elke lijst), de grote maten
# sneller (1024: 68 ms met 4, 27,5 ms met 2, 5 % groter; gemeten in fase 6-voorwerk)
WEBP_METHOD_THUMB, WEBP_METHOD = 4, 2
# Een JPEG mag zoveel keer max_pixels hebben (een panorama, een telefoon van 200 MP): draft
# decodeert hem kleiner, en na draft geldt max_pixels gewoon (gemeten: 200 MP in 0,8 s, 61 MB)
JPEG_PIXELS = 4
# PDF-pagina's voor de viewer
PAGE_WIDTHS = (640, 1280, 1920, 2560)
PAGE_PIXELS = 40_000_000  # een extreem lange pagina wordt minder scherp, niet enorm
MAX_PAGES = 10_000
MAX_TOC = 1000
TOC_CHARS = 200

# Alleen deze decoders van Pillow: andere (zoals EPS, dat Ghostscript start) nooit
IMAGE_FORMATS = ["JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF", "ICO", "AVIF", "HEIF", "PSD"]
IMAGE_FORMATS += ["JPEG2000"]
IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/x-ms-bmp",
    "image/tiff",
    "image/x-icon",
    "image/vnd.microsoft.icon",
    "image/avif",
    "image/heic",
    "image/heif",
    "image/heic-sequence",
    "image/heif-sequence",
    "image/vnd.adobe.photoshop",
    "image/jp2",
}
TAR_MIMES = {
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
    "application/x-gtar",
    "application/x-compressed-tar",
    "application/x-bzip2",
    "application/x-xz",
}
TEXT_MIMES = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
    "application/x-sh",
    "application/sql",
    "application/x-ndjson",
}
# Camera-RAW (vaak TIFF van binnen): de preview komt uit de ingebedde JPEG (raw())
RAW_EXTENSIONS = (".cr2", ".cr3", ".nef", ".nrw", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef")
RAW_MIMES = {
    "image/x-canon-cr2",
    "image/x-canon-cr3",
    "image/x-nikon-nef",
    "image/x-sony-arw",
    "image/x-adobe-dng",
    "image/x-fuji-raf",
    "image/x-olympus-orf",
    "image/x-panasonic-rw2",
    "image/x-pentax-pef",
}
# TIFF met een eigen handtekening: Olympus (ORF) en Panasonic (RW2)
RAW_TIFF = (b"II*\x00", b"MM\x00*", b"IIRO", b"IIRS", b"MMOR", b"IIU\x00")
RAW_BYTES = 512 * 1024 * 1024  # zoveel van een RAW-bestand doorzoeken op een JPEG
RAW_HEAD = 4 * 1024 * 1024  # de TIFF-kop met de EXIF staat vooraan
RAW_CANDIDATES = 64
# Zoveel segmenten van JPEG-koppen mag het zoeken in één RAW samen aflopen (review F2). Een echte
# RAW heeft er hooguit een paar duizend (een DNG met tegels: een paar per tegel); een gemaakt
# bestand met een kop na elke paar bytes stopt zo na een fractie van een seconde, met wat er
# dan gevonden is
RAW_STEPS = 100_000
CR3_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
OFFICE_EXTENSIONS = (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".docm", ".xlsm", ".pptm")
# Kantoordocumenten die app/office/reader.py leest (B21); doc, xls, ppt en rtf niet
OFFICE_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-word.document.macroenabled.12",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
}
ODF_PREFIXES = (
    b"mimetypeapplication/vnd.oasis.opendocument.text",
    b"mimetypeapplication/vnd.oasis.opendocument.spreadsheet",
    b"mimetypeapplication/vnd.oasis.opendocument.presentation",
)
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif"}
HEIF_BRANDS |= {b"avis"}
AUDIO_BRANDS = {b"M4A ", b"M4B ", b"M4P "}


class Unreadable(Exception):
    """Het bestand is kapot of niet te lezen: geen preview, en opnieuw proberen helpt niet."""


class TooSlow(Exception):
    pass


NONE = {"state": "none", "meta": {}, "sizes": []}


def sniff(head: bytes, name: str) -> str:
    """Soort volgens de eerste bytes: image, pdf, video, audio, text, zip, tar, epub, office
    of ""."""
    lower = name.lower()
    if head.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")):
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    if head[:2] == b"BM" and len(head) >= 18:
        # De grootte van de DIB-header maakt het onderscheid met tekst die met BM begint
        if struct.unpack("<I", head[14:18])[0] in (12, 40, 52, 56, 64, 108, 124):
            return "image"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "raw" if lower.endswith(RAW_EXTENSIONS) else "image"
    if head[:4] in RAW_TIFF or head.startswith(b"FUJIFILMCCD-RAW"):
        return "raw"
    if head[:4] == b"\x00\x00\x01\x00" and len(head) >= 6 and head[4] > 0:
        return "image"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in HEIF_BRANDS:
            return "image"
        if brand == b"crx ":
            return "raw"  # Canon CR3
        return "audio" if brand in AUDIO_BRANDS else "video"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        if head[30:58] == b"mimetypeapplication/epub+zip" or lower.endswith(".epub"):
            return "epub"
        if head[30:].startswith(ODF_PREFIXES):
            return "office"  # OpenDocument: het eerste bestand heet mimetype
        if lower.endswith(OFFICE_EXTENSIONS) or b"[Content_Types].xml" in head[:200]:
            return "office"
        if head[30:50] == b"mimetypeapplication/":
            return ""  # andere OpenDocument-soorten (tekening, formule)
        return "zip"
    if head.startswith((b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00")) or head[257:262] == b"ustar":
        return "tar"
    if container(head):
        if head[:4] == b"OggS" or (head[:4] == b"RIFF" and head[8:12] == b"WAVE"):
            return "audio"
        return "audio" if lower.endswith((".wma", ".mka", ".weba")) else "video"
    if head.startswith((b"ID3", b"fLaC")) or (
        len(head) > 1 and head[0] == 0xFF and head[1] >= 0xE0
    ):
        return "audio"
    if lower.endswith(".svg") or b"<svg" in head[:1024].lower():
        return ""
    if b"\x00" not in head and is_utf8(head):
        return "text"
    return ""


def is_utf8(data: bytes) -> bool:
    try:
        codecs.getincrementaldecoder("utf-8")().decode(data, final=False)
    except UnicodeDecodeError:
        return False
    return True


def container(head: bytes) -> str | None:
    """De demuxer voor ffmpeg bij deze eerste bytes. Altijd zelf kiezen: anders kan een
    "video" ook een afspeellijst zijn die andere bestanden of adressen opent."""
    if head[4:8] == b"ftyp" or head[4:8] in (b"moov", b"mdat", b"free", b"wide"):
        return "mov"
    if head[:4] == b"\x1aE\xdf\xa3":
        return "matroska"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"FLV":
        return "flv"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:16] == bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c"):
        return "asf"
    if len(head) > 376 and head[0] == head[188] == head[376] == 0x47:
        return "mpegts"
    if head[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return "mpeg"
    return None


def kind_of(mime: str | None, head: bytes, name: str) -> str:
    """Wat voor preview: op de inhoudssoort (Blob.mime) als die er is, anders gesnuffeld."""
    mime = (mime or "").split(";")[0].strip().lower()
    if not mime or mime == "application/octet-stream":
        return sniff(head, name)
    lower = name.lower()
    if mime == "image/svg+xml":
        return ""
    if mime in RAW_MIMES or (mime == "image/tiff" and lower.endswith(RAW_EXTENSIONS)):
        return "raw"
    if mime in IMAGE_MIMES:
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime == "application/epub+zip":
        return "epub"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime in OFFICE_MIMES:
        return "office"
    if mime in ("application/zip", "application/x-zip-compressed"):
        return "zip"
    if mime in TAR_MIMES:
        return "tar"
    if mime.startswith("text/") or mime in TEXT_MIMES or mime.endswith(("+json", "+xml")):
        return "" if lower.endswith(".svg") else "text"
    return ""


def unreadable_errors() -> tuple[type[BaseException], ...]:
    """Fouten die betekenen: dit bestand is kapot of niet te lezen (geen preview)."""
    errors: list[type[BaseException]] = [
        Unreadable,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        tarfile.TarError,
        EOFError,
        SyntaxError,  # Pillow bij sommige kapotte bestanden; ook ElementTree.ParseError
        ValueError,
        KeyError,
        IndexError,
        struct.error,
        zlib.error,
        lzma.LZMAError,
        UnicodeError,
    ]
    for module, name in [
        ("PIL.Image", "DecompressionBombError"),
        ("PIL.Image", "DecompressionBombWarning"),
        ("pypdfium2", "PdfiumError"),
        ("mutagen", "MutagenError"),
    ]:
        mod = sys.modules.get(module)
        if mod is not None and hasattr(mod, name):
            errors.append(getattr(mod, name))
    return tuple(errors)


def is_unreadable(error: BaseException) -> bool:
    if isinstance(error, (FileNotFoundError, PermissionError, MemoryError)):
        return False  # weg, of geen geheugen: dat kan een volgende keer wel lukken
    # OSError: Pillow meldt zo een afgebroken of onbekend bestand
    return isinstance(error, (OSError, *unreadable_errors()))


def render(request: dict) -> dict:
    if request.get("pdf"):
        # De omgezette PDF van een kantoordocument (fase 4, O3): alleen de pdf-tak, met dat
        # bestand als bron in plaats van de blob zelf
        try:
            return pdf(Path(request["pdf"]), Path(request["out"]), request["max_pixels"])
        except Exception as e:
            if is_unreadable(e):
                return dict(NONE)
            raise
    source = Path(request["source"])
    out = Path(request["out"])
    work = Path(request["work"])
    name = request.get("name") or ""
    with open(source, "rb") as f:
        head = f.read(HEAD)
    kind = kind_of(request.get("mime"), head, name)
    if not kind:
        return dict(NONE)
    first = bool(request.get("first"))
    try:
        if kind == "image":
            return image(source, out, request["max_pixels"], exif=True, first=first)
        if kind == "raw":
            return raw(source, out, work, request["max_pixels"], first=first)
        if kind == "pdf":
            return pdf(source, out, request["max_pixels"])
        if kind == "video":
            return video(source, head, out, work, request["max_pixels"], request.get("worker"))
        if kind == "audio":
            return audio(source, out, work, request["max_pixels"])
        if kind == "text":
            return text(source)
        if kind == "zip":
            return zip_listing(source)
        if kind == "tar":
            return tar_listing(source)
        if kind == "epub":
            return epub(source, out, work, request["max_pixels"])
        if kind == "office":
            return office(source, out, work, request["max_pixels"])
    except Exception as e:
        if is_unreadable(e):
            return dict(NONE)
        raise
    return dict(NONE)


def _pillow(max_pixels: int):
    from PIL import Image

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:  # zonder pillow-heif geen HEIC (AVIF kan Pillow zelf)
        pass
    Image.MAX_IMAGE_PIXELS = max_pixels
    # Boven de grens geen waarschuwing maar een fout: zo'n "bom" vraagt gigabytes geheugen
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    return Image


def _is_jpeg(source: Path) -> bool:
    with open(source, "rb") as f:
        return f.read(3) == b"\xff\xd8\xff"


def image(
    source: Path,
    out: Path,
    max_pixels: int,
    meta: dict | None = None,
    *,
    exif: bool = False,
    first: bool = False,
    display: bool = False,
    orientation: int | None = None,
) -> dict:
    """exif: ook de opnamedatum en camerametadata lezen (render_meta), alleen voor een foto
    zelf, niet voor de hoes van een album of e-book of een frame van een video. first: de eerste
    ronde (alleen 256.webp als dat snel kan, zie quick). display: ook 2048.webp (een camera-RAW:
    die kan de browser niet tonen). orientation: de draaiing van een camera-RAW, als de
    ingebedde JPEG er zelf geen heeft.

    Een JPEG mag JPEG_PIXELS keer zo groot zijn als max_pixels: draft decodeert hem kleiner, en
    wat daarna overblijft valt weer onder max_pixels. Een afgekapte JPEG (een afgebroken sync)
    krijgt toch een preview: wat ontbreekt is grijs."""
    jpeg = _is_jpeg(source)
    Image = _pillow(max_pixels * JPEG_PIXELS if jpeg else max_pixels)
    from PIL import ImageFile, ImageOps

    meta = dict(meta or {})
    with Image.open(source, formats=IMAGE_FORMATS) as img:
        if exif:
            meta.update(render_meta.image_meta(img))
            # Alleen bij een foto zelf: een video-frame of hoes beweegt nooit
            meta["animated"] = bool(getattr(img, "is_animated", False))
        tags = img.getexif()
        if orientation in (2, 3, 4, 5, 6, 7, 8) and tags.get(0x0112, 1) == 1:
            tags[0x0112] = orientation  # exif_transpose leest het hier
        width, height = img.size
        if tags.get(0x0112, 1) in (5, 6, 7, 8):  # gedraaid: breedte en hoogte om
            width, height = height, width
        if img.format != "JPEG" and img.size[0] * img.size[1] > max_pixels:
            raise Unreadable("te veel pixels")  # alleen een JPEG kan kleiner gedecodeerd
        done = {**meta, "width": width, "height": height}
        truncated = ImageFile.LOAD_TRUNCATED_IMAGES
        ImageFile.LOAD_TRUNCATED_IMAGES = img.format == "JPEG"
        try:
            if first and max(img.size) > THUMB:
                sizes = quick(img, source, out, max_pixels)
                if sizes is not None:
                    return {"state": "ready", "meta": done, "sizes": sizes, "more": True}
            big = exif and (display or img.format in DISPLAY_FORMATS)
            wanted = (DISPLAY, *SIZES) if big else SIZES
            img.draft("RGB", (wanted[0], wanted[0]))  # JPEG meteen kleiner decoderen
            _decoded(img, max_pixels)
            sizes = thumbnails(ImageOps.exif_transpose(img), out, wanted)
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = truncated
    return {"state": "ready", "meta": done, "sizes": sizes}


def _decoded(img, max_pixels: int) -> None:
    """Na draft: wordt er niet meer dan max_pixels gedecodeerd?"""
    if img.size[0] * img.size[1] > max_pixels:
        raise Unreadable("te veel pixels, ook kleiner gedecodeerd")


def quick(img, source: Path, out: Path, max_pixels: int) -> list[int] | None:
    """Alleen 256.webp, als dat veel sneller is dan alles in één keer: een JPEG met draft (1/8:
    50 ms tegen 425 ms voor een foto van 12 MP), een HEIC met zijn ingebedde thumbnail. None:
    dan alles in één keer (PNG, TIFF, ... decoderen kost daar toch het meeste)."""
    from PIL import ImageOps

    if img.format == "JPEG":
        img.draft("RGB", (THUMB, THUMB))
        _decoded(img, max_pixels)
        return thumbnails(ImageOps.exif_transpose(img), out, (THUMB,))
    if img.format == "HEIF":
        small = _heif_thumbnail(source, img.size)
        if small is not None:
            return thumbnails(small, out, (THUMB,))
    return None


def _heif_thumbnail(source: Path, size: tuple[int, int]):
    """De ingebedde thumbnail van een HEIC (een iPhone zet er een van 320 px in), als die groot
    genoeg is en dezelfde verhouding heeft als de foto zelf (libheif draait hem zoals de foto;
    anders liever de foto zelf)."""
    try:
        import pillow_heif

        primary = pillow_heif.open_heif(source)[0]
        found = list(primary.info.get("thumbnails") or [])
    except Exception:  # noqa: BLE001 - dan gewoon de foto zelf
        return None
    for index, box in sorted(enumerate(found), key=lambda x: x[1]):
        if box < THUMB:
            continue
        try:
            small = primary.get_thumbnail(index).to_pillow()
        except Exception:  # noqa: BLE001 - zie hierboven
            return None
        w, h = small.size
        if w and h and abs(w / h - size[0] / size[1]) <= 0.02 * size[0] / size[1]:
            return small
        return None
    return None


def thumbnails(img, out: Path, wanted: tuple[int, ...] = SIZES) -> list[int]:
    """256.webp en, als het origineel groter is, 1024.webp (en 2048.webp als dat in wanted
    staat en het origineel groter is dan 1024). Nooit vergroten; geen EXIF mee (zoals de plek
    waar een foto is gemaakt)."""
    from PIL import Image

    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    sizes = []
    for i, size in enumerate(wanted):
        smaller = wanted[i + 1] if i + 1 < len(wanted) else 0
        if size != THUMB and max(img.size) <= smaller:
            continue  # een groter formaat heeft geen zin: dat is het volgende al
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        method = WEBP_METHOD_THUMB if size == THUMB else WEBP_METHOD
        img.save(out / f"{size}.webp", "WEBP", quality=WEBP_QUALITY, method=method)
        sizes.append(size)
    return sizes


# --- camera-RAW -----------------------------------------------------------------------------
#
# Elke camera zet een JPEG in zijn RAW (vaak op volle grootte, soms 1-2 MP), naast het eigen
# sensorbeeld dat Pillow niet kan lezen. raw() zoekt de grootste gewone JPEG (baseline of
# progressive, niet de verliesloze van de sensordata), en neemt datum, camera en draaiing uit de
# TIFF-kop van de RAW (CR2, NEF, ARW, DNG, PEF, ORF, RW2) of de CMT-boxen van een CR3. Een RAF
# heeft een JPEG met eigen EXIF. Geen libraw: alleen wat er al als JPEG in staat.


class _Scan:
    """Zoekt gewone JPEG's in een RAW met begrensd werk (review F2). Eerder zocht elke kop
    zonder einde opnieuw naar FFD9 tot het eind van het bestand, en telde een kop die niet
    deugde niet mee: een gemaakt bestand van 1 MB kostte 45 s, van 4 MB een worker tot de
    tijdgrens van de taak (en dat drie pogingen lang). Nu wordt elke byte hooguit één keer naar
    FFD9 doorzocht (none_from), en lopen alle koppen samen hooguit RAW_STEPS segmenten af."""

    def __init__(self, view) -> None:
        self.view = view
        self.steps = RAW_STEPS
        # Vanaf hier begint geen FFD9 meer (al doorzocht)
        self.none_from = len(view)

    def end_after(self, start: int) -> int:
        """Waar de eerstvolgende FFD9 vanaf start begint, of -1."""
        if start >= self.none_from:
            return -1
        end = self.view.find(b"\xff\xd9", start, self.none_from + 1)
        if end < 0:
            self.none_from = start
        return end

    def jpeg_at(self, start: int) -> tuple[int, int, int] | None:
        """(einde, breedte, hoogte) van de JPEG die op start begint, als het een gewone JPEG is
        (SOF0, 1 of 2) met een einde; anders None. Loopt de segmenten af tot de scan, en zoekt
        dan het einde (FFD9 komt in de gecomprimeerde data niet voor)."""
        view = self.view
        i, size, found = start + 2, len(view), None
        for _ in range(1000):
            if self.steps <= 0:
                return None
            self.steps -= 1
            if i + 4 > size or view[i] != 0xFF:
                return None
            marker = view[i + 1]
            if marker == 0xFF:
                i += 1  # opvulling
                continue
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = (view[i + 2] << 8) | view[i + 3]
            if length < 2:
                return None
            if marker in (0xC0, 0xC1, 0xC2) and i + 9 <= size:
                height = (view[i + 5] << 8) | view[i + 6]
                width = (view[i + 7] << 8) | view[i + 8]
                found = (width, height)
            elif 0xC3 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                # Verliesloos, hiërarchisch of rekenkundig: de sensordata, niet te tonen
                return None
            if marker == 0xDA:
                # Zonder afmetingen geen JPEG om te tonen: dan ook niet naar het einde zoeken
                if found is None or not found[0] or not found[1]:
                    return None
                end = self.end_after(i + 2 + length)
                if end < 0:
                    return None
                return end + 2, found[0], found[1]
            i += 2 + length
        return None


def embedded_jpeg(source: Path) -> bytes | None:
    """De grootste gewone JPEG in dit bestand (hooguit RAW_BYTES doorzocht, met begrensd werk:
    _Scan). Komen dezelfde afmetingen vaak voor (de tegels van een DNG), dan zijn dat stukken
    van een beeld: overslaan."""
    with open(source, "rb") as f:
        length = min(os.fstat(f.fileno()).st_size, RAW_BYTES)
        if length < 4:
            return None
        with mmap.mmap(f.fileno(), length, access=mmap.ACCESS_READ) as view:
            scan = _Scan(view)
            found: list[tuple[int, int, int, int]] = []
            pos = view.find(b"\xff\xd8\xff")
            while pos >= 0 and len(found) < RAW_CANDIDATES and scan.steps > 0:
                jpeg = scan.jpeg_at(pos)
                if jpeg is not None:
                    end, width, height = jpeg
                    found.append((width * height, pos, end, width))
                pos = view.find(b"\xff\xd8\xff", (end if jpeg else pos + 3))
            counts: dict[tuple[int, int], int] = {}
            for pixels, _pos, _end, width in found:
                counts[(pixels, width)] = counts.get((pixels, width), 0) + 1
            usable = [c for c in found if counts[(c[0], c[3])] <= 4]
            if not usable:
                return None
            _pixels, start, end, _width = max(usable)
            return bytes(view[start:end])


class _Tags:
    """Genoeg van een Pillow-Image voor render_meta.image_meta: getexif() met get en get_ifd."""

    def __init__(self, top: dict, sub: dict):
        self.top, self.sub = top, sub

    def getexif(self):
        return self

    def __len__(self) -> int:
        return len(self.top) + len(self.sub)

    def get(self, key, default=None):
        return self.top.get(key, default)

    def get_ifd(self, tag: int):
        return self.sub if tag == render_meta.EXIF_IFD else {}


def _tiff_tags(data: bytes):
    """De EXIF van een TIFF-structuur (ook ORF en RW2: een eigen handtekening, verder TIFF)."""
    from PIL import Image

    if len(data) < 8:
        return None
    data = data[:2] + (b"*\x00" if data[:2] == b"II" else b"\x00*") + data[4:]
    exif = Image.Exif()
    exif.load(b"Exif\x00\x00" + data)
    return exif


def _cr3_tags(head: bytes) -> "_Tags | None":
    """IFD0 (CMT1) en de Exif-IFD (CMT2) uit de uuid-box van Canon in moov."""

    def boxes(data: bytes, start: int, end: int):
        while start + 8 <= end:
            size, kind = struct.unpack(">I4s", data[start : start + 8])
            body = start + 8
            if size == 1 and start + 16 <= end:
                size, body = struct.unpack(">Q", data[start + 8 : start + 16])[0], start + 16
            if size < 8 or start + size > end:
                return
            yield kind, body, start + size
            start += size

    for kind, body, end in boxes(head, 0, len(head)):
        if kind != b"moov":
            continue
        for kind, inner, stop in boxes(head, body, end):
            if kind == b"uuid" and head[inner : inner + 16] == CR3_UUID:
                parts = {k: head[b:e] for k, b, e in boxes(head, inner + 16, stop)}
                top = _tiff_tags(parts.get(b"CMT1", b""))
                sub = _tiff_tags(parts.get(b"CMT2", b""))
                if top is None and sub is None:
                    return None
                return _Tags(dict(top or {}), dict(sub or {}))
    return None


def _raw_tags(source: Path):
    """Datum, camera en draaiing uit de RAW zelf (None als er niets te lezen is)."""
    with open(source, "rb") as f:
        head = f.read(RAW_HEAD)
    try:
        if head[4:12] == b"ftypcrx ":
            return _cr3_tags(head)
        if head[:4] in RAW_TIFF:
            exif = _tiff_tags(head)
            try:
                sub = dict(exif.get_ifd(render_meta.EXIF_IFD))
            except Exception:  # noqa: BLE001 - kapotte Exif-IFD: dan alleen IFD0
                sub = {}
            return _Tags(dict(exif), sub)
    except Exception:  # noqa: BLE001 - een kapotte kop: dan zonder
        return None
    return None


def raw(source: Path, out: Path, work: Path, max_pixels: int, *, first: bool = False) -> dict:
    """Een camera-RAW: de grootste ingebedde JPEG als preview (ook 2048: de browser kan een RAW
    niet tonen), met datum en camera uit de RAW zelf. Zonder bruikbare JPEG: wat Pillow van een
    TIFF-RAW kan lezen (vaak een kleine thumbnail), anders geen preview."""
    tags = _raw_tags(source)
    meta = render_meta.image_meta(tags) if tags is not None else {}
    orientation = render_meta._whole(tags.get(render_meta.ORIENTATION)) if tags else None
    data = embedded_jpeg(source)
    if data is None:
        with open(source, "rb") as f:
            if f.read(4) not in (b"II*\x00", b"MM\x00*"):
                return dict(NONE)
        return image(source, out, max_pixels, exif=True, first=first)
    path = work / "raw.jpg"
    path.write_bytes(data)
    result = image(
        path, out, max_pixels, exif=True, first=first, display=True, orientation=orientation
    )
    # Wat de RAW zelf zegt gaat voor (de ingebedde JPEG heeft vaak geen EXIF)
    return {**result, "meta": {**result["meta"], **meta}}


def pdf(source: Path, out: Path, max_pixels: int) -> dict:
    import pypdfium2 as pdfium

    _pillow(max_pixels)
    document = pdfium.PdfDocument(str(source))
    try:
        pages = len(document)
        if pages == 0:
            return dict(NONE)
        page = document[0]
        try:
            width, height = page.get_size()
            if width <= 0 or height <= 0:
                return dict(NONE)
            scale = min(SIZES[0] / max(width, height), 8.0)
            bitmap = page.render(scale=scale)
            sizes = thumbnails(bitmap.to_pil(), out)
        finally:
            page.close()
    finally:
        document.close()
    return {"state": "ready", "meta": {"pages": pages}, "sizes": sizes}


# --- video: via de mediaworker (fase 5, M1) -----------------------------------------------
#
# ffmpeg en ffprobe draaien alleen nog in de mediaworker (Keuze 11, regel 4). Dit proces vraagt
# daar `meta` (de JSON van ffprobe, opgeschoond) en `frame` (één JPEG op een pipe), en geeft de
# JPEG door de gewone renderer van afbeeldingen. Welke worker:
# - media aan: die van de app; previews.py zet socket en sleutel in het verzoek ("worker"), via
#   stdin en nooit in de omgeving of op de opdrachtregel;
# - media uit (zoals 0.6.0): een eigen worker voor dit ene proces (media/local.oneshot), in
#   dezelfde procesgroep, zodat previews.render hem bij een tijdgrens mee stopt.
# Is de worker weg of druk, dan zegt het antwoord "defer" (previews.py: jobs.Defer).

FRAME_BYTES = 16 * 1024 * 1024  # een JPEG van hooguit 1024 px is veel kleiner
DEFER_DOWN = 300  # s: de worker is weg (bouwontwerp M1)
DEFER_BUSY = 30  # s: de worker is druk (client.WorkerBusy)


class Later(Exception):
    """De worker is weg of druk: deze preview later opnieuw, zonder een poging te tellen."""

    def __init__(self, seconds: float, reason: str):
        super().__init__(reason)
        self.seconds = seconds


def installed() -> bool:
    """Staan ffmpeg en ffprobe op deze machine (voor de eigen worker bij media uit)?"""
    from app.media.worker.guard import BINARIES

    return all(shutil.which(name) for name in BINARIES)


class Media:
    """De worker voor dit renderproces, zolang het blok duurt. ask(coro) draait één vraag
    (asyncio) en maakt van WorkerDown en WorkerBusy een Later."""

    def __init__(self, worker: dict | None, work: Path):
        self.worker = worker
        self.work = work
        self._stack = contextlib.ExitStack()
        self._caps: bool | None = None

    def __enter__(self) -> "Media":
        from app.media import client, local

        if self.worker:
            path, key = Path(str(self.worker["socket"])), str(self.worker.get("key") or "")
        else:
            path, key = self._stack.enter_context(local.oneshot(self.work))
        client.bind(path, key)
        return self

    def __exit__(self, *exc) -> None:
        from app.media import client

        client.bind(None)
        self._stack.close()

    def ask(self, coro):
        from app.media import client

        try:
            return asyncio.run(coro)
        except client.WorkerDown as e:
            raise Later(DEFER_DOWN, f"mediaworker weg: {e}") from None
        except client.WorkerBusy as e:
            raise Later(DEFER_BUSY, f"mediaworker druk: {e}") from None
        except TimeoutError:
            # Geen OSError laten zijn: dat zou "onleesbaar" betekenen (is_unreadable)
            raise RuntimeError("de mediaworker antwoordde niet op tijd") from None

    def ready(self) -> bool:
        """Heeft de worker ffmpeg en ffprobe (caps, uit zijn cache)? Eén keer per proces."""
        if self._caps is None:
            self._caps = self.ask(_has_ffmpeg())
        return self._caps

    def meta(self, source: Path, demuxer: str) -> dict | None:
        """De JSON van meta, of None als de worker het bestand niet kan lezen."""
        from app.media import client
        from app.media.wire import Code

        try:
            return self.ask(_meta(source, demuxer))
        except client.WorkerFailed as e:
            if e.code in (Code.UNREADABLE, Code.UNKNOWN_FORMAT):
                return None
            raise RuntimeError(f"meta: {e}") from None

    def frame(self, source: Path, demuxer: str, index: int, at: float, width: int) -> bytes:
        """De JPEG van frame, of b"" als er geen beeld kwam (voorbij het einde, of een fout)."""
        from app.media import client

        try:
            return self.ask(_frame(source, demuxer, index, at, width))
        except client.WorkerFailed:
            return b""


async def _has_ffmpeg() -> bool:
    from app.media import client

    caps = await client.caps()
    return bool(caps.ffmpeg and caps.ffprobe)


async def _meta(source: Path, demuxer: str) -> dict:
    from app.media import client
    from app.media.wire import Op
    from app.media.worker.args import MetaArgs

    async with asyncio.timeout(FFMPEG_SECONDS):
        job, _ = await client.call(Op.META, MetaArgs(demuxer=demuxer), source=source)
        async with job:
            return await job.result()


async def _readable(loop: asyncio.AbstractEventLoop, fd: int) -> None:
    ready = loop.create_future()
    loop.add_reader(fd, lambda: ready.done() or ready.set_result(None))
    try:
        await ready
    finally:
        loop.remove_reader(fd)


async def _drain(fd: int, limit: int) -> bytes:
    """Alles van een pipe tot EOF, hooguit limit bytes (Unreadable daarboven)."""
    os.set_blocking(fd, False)
    loop = asyncio.get_running_loop()
    chunks: list[bytes] = []
    size = 0
    while True:
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            await _readable(loop, fd)
            continue
        if not data:
            return b"".join(chunks)
        size += len(data)
        if size > limit:
            raise Unreadable("het beeld van de worker is te groot")
        chunks.append(data)


async def _frame(source: Path, demuxer: str, index: int, at: float, width: int) -> bytes:
    from app.media import client
    from app.media.wire import Op
    from app.media.worker.args import FrameArgs

    args = FrameArgs(demuxer=demuxer, video_index=index, at_s=round(at, 3), width=width)
    async with asyncio.timeout(FFMPEG_SECONDS):
        job, reads = await client.call(Op.FRAME, args, source=source, outputs=1)
        async with job:
            try:
                data = await _drain(reads[0], FRAME_BYTES)
            finally:
                os.close(reads[0])
            result = await job.result()
    return data if result.get("frames") != 0 else b""


def frame_width(width: int, height: int) -> int:
    """De breedte voor frame: de lange kant hooguit SIZES[0] (zoals de box van 0.6.0), nooit
    groter dan het origineel, even, en binnen FrameArgs (16 … 3840)."""
    scale = min(1.0, SIZES[0] / max(width, height, 1))
    wanted = int(width * scale) // 2 * 2
    return max(16, min(3840, wanted))


def video(
    source: Path, head: bytes, out: Path, work: Path, max_pixels: int, worker: dict | None = None
) -> dict:
    fmt = container(head)
    if fmt is None:
        return dict(NONE)
    if not worker and not installed():
        return dict(NONE)  # media uit en geen ffmpeg: geen preview van video's (zoals 0.6.0)
    try:
        with Media(worker, work) as media:
            return _video(media, source, fmt, out, work, max_pixels)
    except Later as later:
        return {"state": "defer", "seconds": later.seconds, "reason": str(later)[:200]}


# Geen preview omdat de mediaworker (nu) geen ffmpeg heeft: zodra hij weer gezond is, zet de
# housekeeping van media hem opnieuw klaar (previews.enqueue_later); zo blijft een video niet voor
# altijd zonder thumbnail
LATER = {"state": "none", "meta": {"later": True}, "sizes": []}


def _video(media: Media, source: Path, fmt: str, out: Path, work: Path, max_pixels: int) -> dict:
    if not media.ready():
        return {**LATER, "meta": dict(LATER["meta"])}
    info = media.meta(source, fmt)
    if info is None:
        return dict(NONE)
    meta: dict = {}
    try:
        duration = float(info.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration > 0:
        meta["duration"] = round(duration, 3)
    meta.update(render_meta.video_meta(info))
    meta.update(codecs_of(info))
    stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if stream is None or not stream.get("width") or not stream.get("height"):
        return {"state": "ready" if meta else "none", "meta": meta, "sizes": []}
    width, height = int(stream["width"]), int(stream["height"])
    if _rotation(stream) in (90, 270):
        width, height = height, width  # ffmpeg draait het beeld zelf (autorotate)
    meta.update(width=width, height=height)

    frame = work / "frame.jpg"
    wide = frame_width(width, height)
    # Een beeld op 10 % (hooguit na een halve minuut): de eerste frames zijn vaak zwart
    for at in dict.fromkeys([min(duration * 0.1, 30.0), 0.0]):
        data = media.frame(source, fmt, int(stream["index"]), at, wide)
        if data:
            frame.write_bytes(data)
            return {**image(frame, out, max_pixels), "meta": meta}
    return {"state": "ready", "meta": meta, "sizes": []}


def _short(value, limit: int = 40) -> str | None:
    return str(value)[:limit] if value not in (None, "", "unknown") else None


def codecs_of(probe: dict) -> dict:
    """Wat er in de video zit, als hint voor de browser (die beslist zelf of hij het kan
    afspelen): meta.video {codec, profile, pix_fmt}, meta.audio {codec, channels,
    sample_rate} en meta.container (zoals ffprobe het noemt, bijv. "matroska,webm")."""
    meta: dict = {}
    streams = probe.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is not None:
        info = {
            "codec": _short(video.get("codec_name")),
            "profile": _short(video.get("profile")),
            "pix_fmt": _short(video.get("pix_fmt")),
        }
        meta["video"] = {k: v for k, v in info.items() if v is not None}
    if audio is not None:
        info = {"codec": _short(audio.get("codec_name"))}
        try:
            info["channels"] = int(audio.get("channels") or 0) or None
            info["sample_rate"] = int(audio.get("sample_rate") or 0) or None
        except (TypeError, ValueError):
            pass
        meta["audio"] = {k: v for k, v in info.items() if v is not None}
    container = _short((probe.get("format") or {}).get("format_name"), 80)
    if container:
        meta["container"] = container
    return meta


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(float(side["rotation"])) % 360
            except (TypeError, ValueError):
                return 0
    try:
        return int(stream.get("tags", {}).get("rotate", 0)) % 360
    except (TypeError, ValueError):
        return 0


def audio(source: Path, out: Path, work: Path, max_pixels: int) -> dict:
    import mutagen

    f = mutagen.File(source)
    if f is None:
        return dict(NONE)
    meta: dict = {}
    length = getattr(getattr(f, "info", None), "length", None)
    if length:
        meta["duration"] = round(float(length), 3)
    meta.update(_tags(f))
    cover = _cover(f)
    if cover:
        path = work / "cover"
        path.write_bytes(cover)
        try:
            return {**image(path, out, max_pixels), "meta": meta}
        except Exception as e:
            if not is_unreadable(e):
                raise
    return {"state": "ready" if meta else "none", "meta": meta, "sizes": []}


# Titel, artiest en album heten per soort tags anders: ID3, MP4, Vorbis/FLAC en ASF (wma)
TAG_NAMES = {
    "title": ("TIT2", "\xa9nam", "title", "Title"),
    "artist": ("TPE1", "\xa9ART", "artist", "Author"),
    "album": ("TALB", "\xa9alb", "album", "WM/AlbumTitle"),
}


def _tags(f) -> dict[str, str]:
    tags = getattr(f, "tags", None)
    result: dict[str, str] = {}
    if tags is None:
        return result
    for field, names in TAG_NAMES.items():
        for name in names:
            try:
                value = tags.get(name)
            except (KeyError, ValueError, TypeError):
                continue
            value = getattr(value, "text", value)  # ID3-frame: de tekst zit in .text
            if isinstance(value, list | tuple):
                value = value[0] if value else None
            if value:
                result[field] = str(value).strip()[:NAME_CHARS]
                break
    return result


def _cover(f) -> bytes | None:
    """Ingebedde hoes: ID3 (mp3, wav), MP4 (m4a), FLAC en Vorbis/Opus."""
    pictures = getattr(f, "pictures", None)
    if pictures:
        return _limit(pictures[0].data)
    tags = getattr(f, "tags", None)
    if tags is None:
        return None
    if hasattr(tags, "getall"):
        frames = tags.getall("APIC")
        return _limit(frames[0].data) if frames else None
    try:
        covers = tags.get("covr")
        if covers:
            return _limit(bytes(covers[0]))
        blocks = tags.get("metadata_block_picture")
        if blocks:
            from mutagen.flac import Picture

            return _limit(Picture(base64.b64decode(blocks[0])).data)
    except (AttributeError, TypeError):
        return None
    return None


def _limit(data: bytes) -> bytes | None:
    return data if 0 < len(data) <= EMBEDDED_BYTES else None


def encoding_of(data: bytes) -> tuple[str, str]:
    """(naam voor de web-app, codec voor Python) van het begin van een tekstbestand: een BOM
    voor UTF-8 of UTF-16, anders UTF-8 als dat klopt, en anders Windows-1252 (de tekst van
    oudere Windows-programma's)."""
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8", "utf-8-sig"
    if data.startswith(codecs.BOM_UTF16_LE):
        return "utf-16le", "utf-16"
    if data.startswith(codecs.BOM_UTF16_BE):
        return "utf-16be", "utf-16"
    return ("utf-8", "utf-8") if is_utf8(data) else ("windows-1252", "cp1252")


def text(source: Path) -> dict:
    with open(source, "rb") as f:
        data = f.read(TEXT_BYTES + 1)
    truncated = len(data) > TEXT_BYTES
    data = data[:TEXT_BYTES]
    name, codec = encoding_of(data)
    # Incrementeel: een teken dat op de grens van 4 KB is afgeknipt, valt weg
    content = codecs.getincrementaldecoder(codec)(errors="replace").decode(data, final=False)
    lines = content.replace("\x00", "").splitlines(keepends=True)
    if len(lines) > TEXT_LINES:
        lines, truncated = lines[:TEXT_LINES], True
    meta = {"text": "".join(lines), "truncated": truncated, "encoding": name}
    return {"state": "ready", "meta": meta, "sizes": []}


def _entry(name: str, size: int) -> dict:
    return {"name": name[:NAME_CHARS], "size": max(0, int(size))}


def zip_listing(source: Path) -> dict:
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
    entries = [_entry(i.filename, i.file_size) for i in infos[:MAX_ENTRIES]]
    meta = {"entries": entries, "count": len(infos), "truncated": len(infos) > MAX_ENTRIES}
    return {"state": "ready", "meta": meta, "sizes": []}


def _too_slow(_signum, _frame):
    raise TooSlow


def tar_listing(source: Path) -> dict:
    """Een ingepakte tar moet je van voren af uitpakken om de namen te zien: na
    LIST_SECONDS stoppen we met wat we hebben."""
    entries: list[dict] = []
    truncated = False
    previous = signal.signal(signal.SIGALRM, _too_slow)
    signal.setitimer(signal.ITIMER_REAL, LIST_SECONDS)
    try:
        with tarfile.open(source, "r:*") as archive:
            for member in archive:
                if len(entries) >= MAX_ENTRIES:
                    truncated = True
                    break
                entries.append(_entry(member.name, member.size))
    except TooSlow:
        truncated = True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    if not entries:
        raise Unreadable
    meta = {"entries": entries, "truncated": truncated}
    return {"state": "ready", "meta": meta, "sizes": []}


OPF = "{http://www.idpf.org/2007/opf}"
DC = "{http://purl.org/dc/elements/1.1/}"
CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"


def _read(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > limit:
        raise Unreadable
    return archive.read(info)


def epub(source: Path, out: Path, work: Path, max_pixels: int) -> dict:
    """De hoes van een e-book: in de OPF als cover-image (EPUB 3) of via <meta name="cover">."""
    with zipfile.ZipFile(source) as archive:
        root = ElementTree.fromstring(_read(archive, "META-INF/container.xml", XML_BYTES))
        rootfile = root.find(f".//{CONTAINER}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            return dict(NONE)
        opf_path = rootfile.get("full-path", "")
        opf = ElementTree.fromstring(_read(archive, opf_path, XML_BYTES))
        meta: dict = {}
        title = opf.find(f".//{DC}title")
        if title is not None and title.text:
            meta["title"] = title.text.strip()[:NAME_CHARS]
        items = {item.get("id"): item for item in opf.iter(f"{OPF}item")}
        cover = next(
            (i for i in items.values() if "cover-image" in (i.get("properties") or "").split()),
            None,
        )
        if cover is None:
            for m in opf.iter(f"{OPF}meta"):
                if m.get("name") == "cover" and m.get("content") in items:
                    cover = items[m.get("content")]
                    break
        if cover is None or not cover.get("href"):
            return {"state": "ready" if meta else "none", "meta": meta, "sizes": []}
        href = unquote(cover.get("href", ""))
        href = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))
        path = work / "cover"
        path.write_bytes(_read(archive, href, EMBEDDED_BYTES))
    return {**image(path, out, max_pixels), "meta": meta}


def office(source: Path, out: Path, work: Path, max_pixels: int) -> dict:
    """Een kantoordocument (app/office/reader.py): de inhoud als out/doc.json, de ingebedde
    thumbnail als preview."""
    from app.office import reader

    try:
        result = reader.read(source)
    except reader.Unreadable:
        return dict(NONE)
    (out / "doc.json").write_bytes(reader.fit(result["doc"]))
    meta = result["meta"]
    if result["thumbnail"]:
        path = work / "thumbnail"
        path.write_bytes(result["thumbnail"])
        try:
            return {**image(path, out, max_pixels), "meta": meta}
        except Exception as e:
            if not is_unreadable(e):
                raise
    return {"state": "ready", "meta": meta, "sizes": []}


def _open_pdf(source: Path, max_pixels: int):
    import pypdfium2 as pdfium

    _pillow(max_pixels)
    return pdfium.PdfDocument(str(source))


def docinfo(request: dict) -> dict:
    """Paginagroottes (in punten), inhoudsopgave en paginalabels van een PDF."""
    try:
        document = _open_pdf(Path(request["source"]), request.get("max_pixels", 10**8))
    except Exception as e:
        if is_unreadable(e):
            return {"state": "none"}
        raise
    try:
        count = min(len(document), MAX_PAGES)
        if count == 0:
            return {"state": "none"}
        sizes = []
        for i in range(count):
            try:
                w, h = document.get_page_size(i)
            except Exception:  # noqa: BLE001 - een kapotte pagina: A4 als plaatshouder
                w, h = 595.0, 842.0
            sizes.append([round(w, 2), round(h, 2)])
        toc = []
        for bookmark in document.get_toc(max_depth=8):
            if len(toc) >= MAX_TOC:
                break
            dest = bookmark.get_dest()
            index = dest.get_index() if dest is not None else None
            if index is None or not 0 <= index < count:
                continue
            title = bookmark.get_title().replace("\x00", "").strip()[:TOC_CHARS]
            toc.append({"title": title, "page": index + 1, "level": bookmark.level})
        labels = [document.get_page_label(i)[:40] for i in range(count)]
        if all(not label or label == str(i + 1) for i, label in enumerate(labels)):
            labels = None
        return {"state": "ready", "pages": count, "page_sizes": sizes, "toc": toc, "labels": labels}
    finally:
        document.close()


def pages(request: dict) -> dict:
    """Pagina's (vanaf 1) van een PDF als <n>.webp, width pixels breed."""
    width = int(request["width"])
    if width not in PAGE_WIDTHS:
        raise ValueError("ongeldige breedte")
    out = Path(request["out"])
    try:
        document = _open_pdf(Path(request["source"]), request.get("max_pixels", 10**8))
    except Exception as e:
        if is_unreadable(e):
            return {"state": "none", "pages": []}
        raise
    done = []
    try:
        for n in request.get("pages", []):
            if not 1 <= int(n) <= min(len(document), MAX_PAGES):
                continue
            page = document[int(n) - 1]
            try:
                w, h = page.get_size()
                if w <= 0 or h <= 0:
                    continue
                scale = width / w
                if (w * scale) * (h * scale) > PAGE_PIXELS:
                    scale = (PAGE_PIXELS / (w * h)) ** 0.5
                bitmap = page.render(scale=scale, may_draw_forms=True)
                img = bitmap.to_pil()
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(out / f"{int(n)}.webp", "WEBP", quality=WEBP_QUALITY, method=2)
                done.append(int(n))
            finally:
                page.close()
    finally:
        document.close()
    return {"state": "ready", "pages": done}


# --- tekst voor zoeken (fase 6, Z2; bouwontwerp Keuze 17) ------------------------------------
#
# Twee ingangen met dezelfde lus (_text_batch): per blob app.extract.read.extract, en de uitkomst
# als lengte-gemarkeerde stukken op de uitvoer (app/extract/frame.py). De klok per blob is 30 s
# CPU (blob_cpu_s); na batch_cpu_s CPU voor de hele batch begint het kind geen nieuwe blob meer
# (de rest komt in de volgende batch). Een fout van één blob is een uitkomst failed; alleen een
# crash van het proces neemt de batch mee.
#
# - text_child(): het kind van de worker (Op.TEXT): eerst niet-dumpbaar, dan "klaar" op de
#   socket (fd 3), dan de vraag met de fd's van de blobs en als laatste de pipe naar de app
#   (SCM_RIGHTS). Na het werk {"done": n} op de socket, of {"error": soort}; stderr is
#   /dev/null. De vraag: {"items": [{"kind"}], "max_chars", "blob_cpu_s", "batch_cpu_s"}.
# - run_text(request) (mode "text"): hetzelfde in het renderproces van de app, als media uit
#   staat (de terugval, previews.render_text): {"items": [{"path", "kind"}], "out", "cpu_s",
#   …}; met RLIMIT_CPU cpu_s (zacht: SIGXCPU, een seconde later hard), RLIMIT_AS en nice uit
#   main(). De stukken komen in het bestand out, het antwoord is {"state": "ready", "done": n}.

PR_SET_DUMPABLE = 4
TEXT_REQUEST_BYTES = 64 * 1024
TEXT_MAX_FDS = 64


def _text_batch(blobs: list, request: dict, out_fd: int) -> int:
    """blobs: open bestanden (of None: niet te openen), in de volgorde van request["items"]."""
    import time

    from app.extract import frame, read

    max_chars = int(request["max_chars"])
    blob_cpu = float(request.get("blob_cpu_s", read.BLOB_CPU_S))
    batch_end = time.process_time() + float(request.get("batch_cpu_s", 20.0))
    done = 0
    for index, (item, f) in enumerate(zip(request["items"], blobs, strict=True)):
        if index and time.process_time() > batch_end:
            break
        if f is None:
            found = read.Extracted("", "failed", 0, False, [], "", None)
            text = ""
        else:
            found, text = read.extract(
                "", f, str(item["kind"]), max_chars=max_chars, cpu_seconds=blob_cpu
            )
        frame.write(out_fd, index, read.fields(found), text)
        done += 1
    return done


def text_child() -> int:
    """Het kind van Op.TEXT; zie hierboven. Geeft de exitcode."""
    import ctypes
    import socket

    # Als eerste: niet-dumpbaar, vóór er iets van een ander in dit proces komt
    if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        return 3
    sock = socket.socket(fileno=3)
    fds: list[int] = []
    try:
        sock.sendall(b"klaar")
        data, fds, _flags, _addr = socket.recv_fds(sock, TEXT_REQUEST_BYTES, TEXT_MAX_FDS)
        request = json.loads(data)
        if len(fds) != len(request["items"]) + 1:
            raise ValueError("aantal fd's")
        out_fd = fds[-1]
        blobs = [os.fdopen(fd, "rb", closefd=False) for fd in fds[:-1]]
        done = _text_batch(blobs, request, out_fd)
        os.close(out_fd)  # de app ziet EOF zodra ook de worker zijn kant sluit
        sock.sendall(json.dumps({"done": done}).encode())
        return 0
    except Exception as e:  # noqa: BLE001 - de fout gaat over de socket, stderr is /dev/null
        with contextlib.suppress(OSError):
            sock.sendall(json.dumps({"error": type(e).__name__}).encode())
        return 1


def run_text(request: dict) -> dict:
    """mode "text": een batch via paden, in het renderproces van de app (media uit)."""
    cpu = int(request.get("cpu_s") or 0)
    if cpu > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    blobs = []
    for item in request["items"]:
        try:
            fd = os.open(item["path"], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        except OSError:
            blobs.append(None)
            continue
        blobs.append(os.fdopen(fd, "rb"))
    out = os.open(request["out"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        done = _text_batch(blobs, request, out)
    finally:
        os.close(out)
        for f in blobs:
            if f is not None:
                f.close()
    return {"state": "ready", "done": done}


def run_ocr_page(request: dict) -> dict:
    """mode "ocr_page" (fase 6, X1): één pagina of foto door tesseract. Z0 is een stub: zonder
    tesseract in het image kan dit niet, en X1 bouwt het (Vraag 1). "none" betekent: geen
    tekst gevonden; code zegt waarom (ocr_missing: tesseract staat er niet).
    """
    return {"state": "none", "chars": 0, "truncated": False, "code": "ocr_missing"}


def main() -> None:
    request = json.loads(sys.stdin.buffer.read())
    if request.get("nice"):
        os.nice(int(request["nice"]))
    if request.get("memory_mb"):
        limit = int(request["memory_mb"]) * 1024 * 1024
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    if request.get("mode") == "meta":
        result = render_meta.run_meta(request)  # alleen metadata van veel bestanden (P5)
    elif request.get("mode") == "page":
        result = pages(request)
    elif request.get("mode") == "docinfo":
        result = docinfo(request)
    elif request.get("mode") == "text":
        result = run_text(request)  # de tekst uit een document (fase 6, Z2)
    elif request.get("mode") == "ocr_page":
        result = run_ocr_page(request)  # één pagina door tesseract (fase 6, X1)
    else:
        result = render(request)
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()


# --- de forkserver (previews.Forkserver) ---------------------------------------------------
#
# Eén proces dat Pillow en de rest één keer laadt en per verzoek een kind maakt met fork: dat
# scheelt per foto het starten van Python en het laden van Pillow (gemeten: een JPEG van 12 MP
# 272 ms in een kind van de forkserver, 493 ms in een nieuw proces). Het kind is verder precies
# het oude renderproces: een eigen sessie (procesgroep, zodat previews.render het met alles wat
# het startte kan stoppen), dezelfde kale omgeving, en het draait de code van previews.CHILD met
# het verzoek op stdin; stdout en stderr gaan naar OUT en ERR in zijn werkmap. Geheugengrens en
# nice zet main() in het kind, net als in een eigen proces. Valt de forkserver weg (de app is
# weg, of hij is doodgemaakt), dan gaan zijn kinderen mee (PDEATHSIG): een kind zonder iemand
# die op de tijdgrens let, draait anders onbeperkt door.
#
# Op stdin regels JSON {"id", "code", "cwd", "request"}; op stdout per kind {"id", "pid"} zodra
# het een eigen sessie heeft (eerder zou killpg op die pid het kind nog missen) en {"id", "exit"}
# (de exitcode, of -signaal) als het klaar is, of {"id", "error"} als fork mislukte. Eén thread:
# fork is dan veilig. stdin dicht (de app is weg): klaar.

OUT, ERR = ".render.out", ".render.err"
PR_SET_PDEATHSIG = 1


def preload() -> None:
    """Wat bijna elk kind nodig heeft, één keer: Pillow met zijn decoders en pillow-heif."""
    from PIL import Image, ImageFile, ImageOps  # noqa: F401

    Image.init()
    with contextlib.suppress(ImportError):
        import pillow_heif

        pillow_heif.register_heif_opener()


def _child(message: dict, libc, parent: int, ready: int) -> None:
    """In het kind: alsof het een eigen proces was. Komt nooit terug. Eerst SIGKILL als de
    forkserver wegvalt (parent; was hij al weg, dan meteen stoppen), dan een eigen sessie, en
    pas dan ready dicht: zo meldt de forkserver de pid pas als killpg het kind raakt."""
    code = 1
    try:
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
        if os.getppid() != parent:
            os._exit(1)
        os.setsid()
        os.close(ready)
        cwd = str(message["cwd"])
        os.chdir(cwd)
        for fd, name in ((1, OUT), (2, ERR)):
            target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            os.dup2(target, fd)
            os.close(target)
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, 0)
        os.close(null)
        os.closerange(3, resource.getrlimit(resource.RLIMIT_NOFILE)[0])
        os.environ["HOME"] = cwd
        data = json.dumps(message["request"]).encode()
        sys.stdin = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8")
        sys.stdout = open(1, "w", encoding="utf-8", closefd=False)
        sys.stderr = open(2, "w", encoding="utf-8", closefd=False)
        exec(compile(str(message["code"]), "<render>", "exec"), {"__name__": "__main__"})
        code = 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:  # noqa: BLE001 - zoals een proces dat met een fout stopt
        with contextlib.suppress(Exception):
            traceback.print_exc()
    finally:
        with contextlib.suppress(Exception):
            sys.stdout.flush()
            sys.stderr.flush()
        os._exit(code)


def serve() -> None:
    import ctypes

    preload()
    libc = ctypes.CDLL(None, use_errno=True)
    parent = os.getpid()
    selector = selectors.DefaultSelector()
    selector.register(0, selectors.EVENT_READ, None)
    pending = b""

    def send(message: dict) -> None:
        data = (json.dumps(message) + "\n").encode()
        while data:
            data = data[os.write(1, data) :]

    while True:
        for key, _events in selector.select():
            if key.data is not None:
                ident, pid = key.data
                selector.unregister(key.fd)
                os.close(key.fd)
                _pid, status = os.waitpid(pid, 0)
                send({"id": ident, "exit": os.waitstatus_to_exitcode(status)})
                continue
            chunk = os.read(0, 65536)
            if not chunk:
                return  # de app is weg; lopende kinderen gaan mee (PDEATHSIG)
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                message = json.loads(line)
                waiting, ready = os.pipe()
                try:
                    pid = os.fork()
                except OSError as e:
                    os.close(waiting)
                    os.close(ready)
                    send({"id": message["id"], "error": str(e)[:200]})
                    continue
                if pid == 0:
                    os.close(waiting)
                    selector.close()
                    _child(message, libc, parent, ready)
                os.close(ready)
                os.read(waiting, 1)  # leeg: het kind heeft een eigen sessie (of is al weg)
                os.close(waiting)
                selector.register(os.pidfd_open(pid), selectors.EVENT_READ, (message["id"], pid))
                send({"id": message["id"], "pid": pid})


if __name__ == "__main__":
    main()
