"""Een preview maken van één bestand. Draait in een eigen proces (zie previews.py): een kapot
of kwaadaardig bestand kan zo hooguit dit proces laten vastlopen of crashen, niet de server.
Bovenaan alleen de standaardbibliotheek, zodat het proces snel start; Pillow en de rest
pas als ze nodig zijn.

Verzoek als JSON op stdin: {"source", "out", "work", "mime", "name", "max_pixels",
"memory_mb"}, en met media aan "worker": {"socket", "key"} (fase 5, previews.py). Antwoord
als JSON op stdout: {"state": "ready" | "none", "meta": {...}, "sizes": [1024, 256]}; voor
een video ook {"state": "defer", "seconds"} als de mediaworker weg of druk is. De
afbeeldingen komen in out/<grootte>.webp (HEIC, HEIF en TIFF ook 2048.webp: die kan de browser
zelf niet tonen); een kantoordocument geeft ook out/doc.json
(app/office/reader.py). work is voor tussenbestanden. Een onverwachte fout eindigt met een exitcode
en de melding op stderr.

Met een veld "pdf" (een pad) maakt hij de thumbnails uit die PDF in plaats van uit de bron:
de omgezette PDF van een kantoordocument (fase 4, app/office/convert.py).

Andere soorten verzoeken (veld "mode"):
- "page": {"source", "out", "pages": [n, ...], "width"} -> pagina's van een PDF als
  out/<n>.webp, zo breed als width (de viewer, routers/viewer.py);
- "docinfo": {"source"} -> {"state", "pages", "page_sizes", "toc", "labels"} van een PDF;
- "meta": alleen metadata van veel bestanden (render_meta.run_meta, P5).
"""

import asyncio
import base64
import codecs
import contextlib
import json
import lzma
import os
import posixpath
import resource
import shutil
import signal
import struct
import sys
import tarfile
import warnings
import zipfile
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

from app import render_meta  # ook alleen de standaardbibliotheek

SIZES = (1024, 256)  # groot eerst: de thumbnail wordt uit de grote gemaakt
DISPLAY = 2048  # extra groot voor wat de browser zelf niet kan tonen
DISPLAY_FORMATS = {"HEIF", "TIFF"}
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
# PDF-pagina's voor de viewer
PAGE_WIDTHS = (640, 1280, 1920, 2560)
PAGE_PIXELS = 40_000_000  # een extreem lange pagina wordt minder scherp, niet enorm
MAX_PAGES = 10_000
MAX_TOC = 1000
TOC_CHARS = 200

# Alleen deze decoders van Pillow: andere (zoals EPS, dat Ghostscript start) nooit
IMAGE_FORMATS = ["JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF", "ICO", "AVIF", "HEIF"]
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
# Camera-RAW is vaak TIFF van binnen; daar maken we (nog) geen preview van
RAW_EXTENSIONS = (".cr2", ".cr3", ".nef", ".nrw", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef")
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
        return "" if lower.endswith(RAW_EXTENSIONS) else "image"
    if head[:4] == b"\x00\x00\x01\x00" and len(head) >= 6 and head[4] > 0:
        return "image"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in HEIF_BRANDS:
            return "image"
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
    try:
        if kind == "image":
            return image(source, out, request["max_pixels"], exif=True)
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


def image(
    source: Path, out: Path, max_pixels: int, meta: dict | None = None, *, exif: bool = False
) -> dict:
    """exif: ook de opnamedatum en camerametadata lezen (render_meta), alleen voor een foto
    zelf, niet voor de hoes van een album of e-book of een frame van een video."""
    Image = _pillow(max_pixels)
    from PIL import ImageOps

    meta = dict(meta or {})
    with Image.open(source, formats=IMAGE_FORMATS) as img:
        if exif:
            meta.update(render_meta.image_meta(img))
            # Alleen bij een foto zelf: een video-frame of hoes beweegt nooit
            meta["animated"] = bool(getattr(img, "is_animated", False))
        width, height = img.size
        if img.getexif().get(0x0112, 1) in (5, 6, 7, 8):  # gedraaid: breedte en hoogte om
            width, height = height, width
        wanted = (DISPLAY, *SIZES) if exif and img.format in DISPLAY_FORMATS else SIZES
        img.draft("RGB", (wanted[0], wanted[0]))  # JPEG meteen kleiner decoderen
        sizes = thumbnails(ImageOps.exif_transpose(img), out, wanted)
    return {
        "state": "ready",
        "meta": {**meta, "width": width, "height": height},
        "sizes": sizes,
    }


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
        img.save(out / f"{size}.webp", "WEBP", quality=WEBP_QUALITY, method=4)
        sizes.append(size)
    return sizes


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


def _video(media: Media, source: Path, fmt: str, out: Path, work: Path, max_pixels: int) -> dict:
    if not media.ready():
        return dict(NONE)  # de worker heeft geen ffmpeg: zoals 0.6.0 zonder ffmpeg
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


def main() -> None:
    request = json.loads(sys.stdin.buffer.read())
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
    else:
        result = render(request)
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
