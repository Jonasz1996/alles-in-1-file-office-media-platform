"""Een preview maken van één bestand. Draait in een eigen proces (zie previews.py): een kapot
of kwaadaardig bestand kan zo hooguit dit proces laten vastlopen of crashen, niet de server.
Bovenaan alleen de standaardbibliotheek, zodat het proces snel start; Pillow en de rest
pas als ze nodig zijn.

Verzoek als JSON op stdin: {"source", "out", "work", "mime", "name", "max_pixels",
"memory_mb"}. Antwoord als JSON op stdout: {"state": "ready" | "none", "meta": {...},
"sizes": [1024, 256]}. De afbeeldingen komen in out/<grootte>.webp; work is voor
tussenbestanden. Een onverwachte fout eindigt met een exitcode en de melding op stderr.
"""

import base64
import codecs
import json
import lzma
import posixpath
import resource
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import warnings
import zipfile
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

SIZES = (1024, 256)  # groot eerst: de thumbnail wordt uit de grote gemaakt
THUMB = 256
HEAD = 8192
TEXT_BYTES = 4096
TEXT_LINES = 50
MAX_ENTRIES = 200
NAME_CHARS = 500
LIST_SECONDS = 15  # zo lang mag het uitlezen van een (ingepakt) archief duren
FFMPEG_SECONDS = 60
EMBEDDED_BYTES = 32 * 1024 * 1024  # hoes van een album of e-book
XML_BYTES = 4 * 1024 * 1024
WEBP_QUALITY = 80

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
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif"}
HEIF_BRANDS |= {b"avis"}
AUDIO_BRANDS = {b"M4A ", b"M4B ", b"M4P "}


class Unreadable(Exception):
    """Het bestand is kapot of niet te lezen: geen preview, en opnieuw proberen helpt niet."""


class TooSlow(Exception):
    pass


NONE = {"state": "none", "meta": {}, "sizes": []}


def sniff(head: bytes, name: str) -> str:
    """Soort volgens de eerste bytes: image, pdf, video, audio, text, zip, tar, epub of ""."""
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
        if lower.endswith(OFFICE_EXTENSIONS) or b"[Content_Types].xml" in head[:200]:
            return ""
        if head[30:50] == b"mimetypeapplication/":
            return ""  # OpenDocument en dergelijke
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
            return image(source, out, request["max_pixels"])
        if kind == "pdf":
            return pdf(source, out, request["max_pixels"])
        if kind == "video":
            return video(source, head, out, work, request["max_pixels"])
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


def image(source: Path, out: Path, max_pixels: int, meta: dict | None = None) -> dict:
    Image = _pillow(max_pixels)
    from PIL import ImageOps

    with Image.open(source, formats=IMAGE_FORMATS) as img:
        width, height = img.size
        if img.getexif().get(0x0112, 1) in (5, 6, 7, 8):  # gedraaid: breedte en hoogte om
            width, height = height, width
        img.draft("RGB", (SIZES[0], SIZES[0]))  # JPEG meteen kleiner decoderen
        sizes = thumbnails(ImageOps.exif_transpose(img), out)
    return {
        "state": "ready",
        "meta": {**(meta or {}), "width": width, "height": height},
        "sizes": sizes,
    }


def thumbnails(img, out: Path) -> list[int]:
    """256.webp en, als het origineel groter is, 1024.webp. Nooit vergroten; geen EXIF mee
    (zoals de plek waar een foto is gemaakt)."""
    from PIL import Image

    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    sizes = []
    for size in SIZES:
        if size != THUMB and max(img.size) <= THUMB:
            continue  # een groot formaat heeft geen zin: dat is de thumbnail al
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


def _unlimited():
    """Voor ffmpeg: de geheugengrens van dit proces niet erven (ffmpeg reserveert per thread
    veel adresruimte); ffmpeg heeft zijn eigen tijdsgrens."""
    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (hard, hard))


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=FFMPEG_SECONDS,
        preexec_fn=_unlimited,  # veilig: dit proces heeft geen threads
        check=False,
    )


def video(source: Path, head: bytes, out: Path, work: Path, max_pixels: int) -> dict:
    fmt = container(head)
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if fmt is None or ffmpeg is None or ffprobe is None:
        return dict(NONE)  # zonder ffmpeg geen preview van video's
    src = f"file:{source}"
    probe = _run(
        [ffprobe, "-v", "error", "-f", fmt, "-print_format", "json", "-show_format"]
        + ["-show_streams", src]
    )
    if probe.returncode != 0:
        return dict(NONE)
    info = json.loads(probe.stdout or b"{}")
    meta: dict = {}
    try:
        duration = float(info.get("format", {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    if duration > 0:
        meta["duration"] = round(duration, 3)
    stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if stream is None or not stream.get("width") or not stream.get("height"):
        return {"state": "ready" if meta else "none", "meta": meta, "sizes": []}
    width, height = int(stream["width"]), int(stream["height"])
    if _rotation(stream) in (90, 270):
        width, height = height, width
    meta.update(width=width, height=height)

    frame = work / "frame.png"
    box = f"scale='min({SIZES[0]},iw)':'min({SIZES[0]},ih)':force_original_aspect_ratio=decrease"
    # Een beeld op 10 % (hooguit na een halve minuut): de eerste frames zijn vaak zwart
    for at in dict.fromkeys([min(duration * 0.1, 30.0), 0.0]):
        frame.unlink(missing_ok=True)
        result = _run(
            [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}"]
            + ["-f", fmt, "-i", src, "-map", "0:v:0", "-frames:v", "1", "-an", "-sn", "-dn"]
            + ["-vf", box, "-y", str(frame)]
        )
        if result.returncode == 0 and frame.exists() and frame.stat().st_size > 0:
            return {**image(frame, out, max_pixels), "meta": meta}
    return {"state": "ready", "meta": meta, "sizes": []}


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


def text(source: Path) -> dict:
    with open(source, "rb") as f:
        data = f.read(TEXT_BYTES + 1)
    truncated = len(data) > TEXT_BYTES
    data = data[:TEXT_BYTES]
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    # Incrementeel: een teken dat op de grens van 4 KB is afgeknipt, valt weg
    content = codecs.getincrementaldecoder(encoding)(errors="replace").decode(data, final=False)
    lines = content.replace("\x00", "").splitlines(keepends=True)
    if len(lines) > TEXT_LINES:
        lines, truncated = lines[:TEXT_LINES], True
    return {"state": "ready", "meta": {"text": "".join(lines), "truncated": truncated}, "sizes": []}


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


def main() -> None:
    request = json.loads(sys.stdin.buffer.read())
    if request.get("memory_mb"):
        limit = int(request["memory_mb"]) * 1024 * 1024
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    result = render(request)
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
