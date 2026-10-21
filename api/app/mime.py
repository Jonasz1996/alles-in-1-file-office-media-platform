"""Soort bestand bepalen aan de inhoud zelf (de eerste bytes), niet alleen aan de naam.

Een duidelijke handtekening wint altijd: een .txt dat eigenlijk een PNG is, is image/png.
De naam helpt alleen waar de inhoud het niet zegt: welk soort zip (docx, epub, ...), welk
soort tekst (markdown, csv, code, ...), en als laatste redmiddel.
"""

import codecs
import mimetypes
from pathlib import PurePosixPath

# Zoveel bytes van het begin van een bestand is genoeg om het te herkennen
HEAD = 8192

# Eigen tabel van Python, niet die van het systeem (/etc/mime.types): zo is de uitkomst op
# elke machine en in elke container hetzelfde.
_names = mimetypes.MimeTypes()

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
ODT = "application/vnd.oasis.opendocument.text"
ODS = "application/vnd.oasis.opendocument.spreadsheet"
ODP = "application/vnd.oasis.opendocument.presentation"
ODG = "application/vnd.oasis.opendocument.graphics"
EPUB = "application/epub+zip"

# Formaten die eigenlijk een zip zijn; welke het is, zegt de naam of de inhoud van de zip
ZIP_BY_EXT = {
    "docx": DOCX,
    "docm": "application/vnd.ms-word.document.macroenabled.12",
    "dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    "xlsx": XLSX,
    "xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
    "pptx": PPTX,
    "pptm": "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    "odt": ODT,
    "ods": ODS,
    "odp": ODP,
    "odg": ODG,
    "epub": EPUB,
    "jar": "application/java-archive",
    "apk": "application/vnd.android.package-archive",
}
# Oude Office-bestanden zijn allemaal een OLE2-container
OLE_BY_EXT = {
    "doc": "application/msword",
    "dot": "application/msword",
    "xls": "application/vnd.ms-excel",
    "ppt": "application/vnd.ms-powerpoint",
    "msg": "application/vnd.ms-outlook",
}
# Tekst: de naam zegt welk soort. Alleen voor inhoud die tekst is (of leeg): een binaire .ts
# is een MPEG-TS-video, geen TypeScript.
TEXT_BY_EXT = {
    "txt": "text/plain",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
    "yaml": "application/yaml",
    "yml": "application/yaml",
    "toml": "application/toml",
    "xml": "application/xml",
    "html": "text/html",
    "htm": "text/html",
    "xhtml": "application/xhtml+xml",
    "svg": "image/svg+xml",
    "css": "text/css",
    "js": "text/javascript",
    "mjs": "text/javascript",
    "cjs": "text/javascript",
    "jsx": "text/javascript",
    "ts": "text/x-typescript",
    "tsx": "text/x-typescript",
    "mts": "text/x-typescript",
    "cts": "text/x-typescript",
    "py": "text/x-python",
    "rb": "text/x-ruby",
    "php": "text/x-php",
    "java": "text/x-java",
    "kt": "text/x-kotlin",
    "swift": "text/x-swift",
    "go": "text/x-go",
    "rs": "text/x-rust",
    "c": "text/x-c",
    "h": "text/x-c",
    "cpp": "text/x-c++",
    "hpp": "text/x-c++",
    "cs": "text/x-csharp",
    "sh": "text/x-shellscript",
    "bash": "text/x-shellscript",
    "ps1": "text/x-powershell",
    "sql": "text/x-sql",
    "ini": "text/plain",
    "conf": "text/plain",
    "cfg": "text/plain",
    "env": "text/plain",
    "log": "text/plain",
    "srt": "text/plain",
    "vtt": "text/vtt",
    "ics": "text/calendar",
    "vcf": "text/vcard",
    "tex": "text/x-tex",
    "rtf": "application/rtf",
    "vue": "text/plain",
}
# Wat de tabel van Python niet (goed) kent
MEDIA_BY_EXT = {
    "rar": "application/vnd.rar",
    "7z": "application/x-7z-compressed",
    "zst": "application/zstd",
    "mkv": "video/x-matroska",
    "mka": "audio/x-matroska",
    "weba": "audio/webm",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/ogg",
    "ogv": "video/ogg",
    "ts": "video/mp2t",
    "m2ts": "video/mp2t",
    "mts": "video/mp2t",
    "m4a": "audio/mp4",
    "m4v": "video/mp4",
    "wav": "audio/wav",
    "heic": "image/heic",
    "avif": "image/avif",
    "psd": "image/vnd.adobe.photoshop",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "sqlite": "application/vnd.sqlite3",
}
COMPRESSED = {"gzip": "application/gzip", "bzip2": "application/x-bzip2", "xz": "application/x-xz"}
# Wat naast de prefixen image/, video/, audio/ en text/ in een categorie valt
ARCHIVE_TYPES = frozenset(
    {
        "application/zip",
        "application/gzip",
        "application/x-tar",
        "application/x-7z-compressed",
        "application/vnd.rar",
        "application/x-bzip2",
        "application/x-xz",
        "application/zstd",
        "application/java-archive",
    }
)
DOCUMENT_TYPES = frozenset(
    {
        *(v for k, v in ZIP_BY_EXT.items() if k not in ("jar", "apk")),
        *OLE_BY_EXT.values(),
        "application/rtf",
        "application/json",
        "application/xml",
        "application/yaml",
        "application/toml",
        "application/xhtml+xml",
        "application/javascript",
        "application/x-sh",
        "application/sql",
    }
)
CATEGORIES = ("folder", "image", "video", "audio", "pdf", "document", "archive", "other")

FTYP_IMAGE = {
    b"avif": "image/avif",
    b"avis": "image/avif",
    b"heic": "image/heic",
    b"heix": "image/heic",
    b"heim": "image/heic",
    b"heis": "image/heic",
    b"hevc": "image/heic",
    b"hevx": "image/heic",
    b"mif1": "image/heif",
    b"msf1": "image/heif",
    b"jp2 ": "image/jp2",
}
FTYP_OTHER = {
    b"qt  ": "video/quicktime",
    b"M4A ": "audio/mp4",
    b"M4B ": "audio/mp4",
    b"M4P ": "audio/mp4",
    b"F4A ": "audio/mp4",
    b"M4V ": "video/mp4",
    b"M4VH": "video/mp4",
    b"M4VP": "video/mp4",
    b"crx ": "image/x-canon-cr3",
}


def _ext(name: str) -> str:
    suffix = PurePosixPath(name.lower()).suffix
    return suffix[1:] if suffix else ""


def by_name(name: str, text: bool = True) -> str | None:
    """Alleen op de naam, voor als de inhoud niets zegt. text=False: de inhoud is binair, dus
    geen soort tekst op grond van de extensie (een binaire .ts is een video)."""
    ext = _ext(name)
    if text and ext in TEXT_BY_EXT:
        return TEXT_BY_EXT[ext]
    if ext in ZIP_BY_EXT:
        return ZIP_BY_EXT[ext]
    if ext in OLE_BY_EXT:
        return OLE_BY_EXT[ext]
    if ext in MEDIA_BY_EXT:
        return MEDIA_BY_EXT[ext]
    mime, encoding = _names.guess_type(name, strict=False)
    if encoding:  # .gz, .tar.gz, .bz2, .xz: wat je hebt is het gecomprimeerde bestand
        return COMPRESSED.get(encoding, "application/octet-stream")
    return mime


def _ftyp(head: bytes, name: str) -> str | None:
    """ISO-mediabestanden (mp4, mov, m4a, heic, avif): het merk staat in het ftyp-blok. Met
    een redelijke grootte en een leesbaar merk, anders is een tekst met "ftyp" op plek 4 al
    een video."""
    if head[4:8] != b"ftyp" or len(head) < 12:
        return None
    major = head[8:12]
    size = int.from_bytes(head[0:4], "big")
    if not 8 <= size <= 4096 or not all(0x20 <= b < 0x7F for b in major):
        return None
    brands = [major] + [head[i : i + 4] for i in range(16, min(size, len(head), 256) - 3, 4)]
    for brand in brands:  # een HEIF-bestand kan avif of heic als compatibel merk hebben
        if brand in (b"avif", b"avis"):
            return "image/avif"
    if major in FTYP_IMAGE:
        for brand in brands:
            if brand in FTYP_IMAGE and FTYP_IMAGE[brand] != "image/heif":
                return FTYP_IMAGE[brand]
        return FTYP_IMAGE[major]
    if major in FTYP_OTHER:
        return FTYP_OTHER[major]
    if major.startswith(b"3g2"):
        return "video/3gpp2"
    if major.startswith(b"3gp"):
        return "video/3gpp"
    # isom, mp41, mp42, avc1, dash, ...: een mp4; met alleen geluid heet het vaak .m4a
    return "audio/mp4" if _ext(name) in ("m4a", "m4b") else "video/mp4"


def _zip(head: bytes, name: str) -> str:
    ext = _ext(name)
    if ext in ZIP_BY_EXT:
        return ZIP_BY_EXT[ext]
    # Eerste item in de zip: naam op offset 30, lengte op 26
    length = int.from_bytes(head[26:28], "little")
    first = head[30 : 30 + length]
    if first == b"mimetype":
        # ODF en epub zetten hun soort ongecomprimeerd als eerste item
        extra = int.from_bytes(head[28:30], "little")
        start = 30 + length + extra
        stored_size = int.from_bytes(head[18:22], "little")
        if 0 < stored_size <= 100:
            stored = head[start : start + stored_size]
        else:  # grootte staat pas achter de inhoud: tot de volgende header
            stored = head[start : start + 100].split(b"PK\x03\x04")[0]
        stored = stored.strip()
        try:
            value = stored.decode("ascii")
        except UnicodeDecodeError:
            value = ""
        if value in DOCUMENT_TYPES:
            return value
    if first == b"[Content_Types].xml" or b"[Content_Types].xml" in head:
        # Office Open XML: welke map erin zit zegt of het tekst, rekenblad of presentatie is
        for marker, mime in ((b"word/", DOCX), (b"xl/", XLSX), (b"ppt/", PPTX)):
            if marker in head:
                return mime
    return "application/zip"


def _riff(head: bytes) -> str | None:
    form = head[8:12]
    return {b"WEBP": "image/webp", b"WAVE": "audio/wav", b"AVI ": "video/x-msvideo"}.get(form)


def _ebml(head: bytes, name: str) -> str:
    audio = _ext(name) in ("weba", "mka")
    if b"webm" in head[:4096]:
        return "audio/webm" if audio else "video/webm"
    return "audio/x-matroska" if audio else "video/x-matroska"


def _id3(head: bytes) -> bool:
    """ID3v2: versie 2 tot 4, een revisie, en de grootte in 4 x 7 bits."""
    return (
        len(head) >= 10
        and head.startswith(b"ID3")
        and head[3] in (2, 3, 4)
        and head[4] != 0xFF
        and all(b < 0x80 for b in head[6:10])
    )


def _tar(head: bytes) -> bool:
    """ustar op plek 257 en een kloppende controlesom van de header."""
    if len(head) < 512 or head[257:263] not in (b"ustar\x00", b"ustar "):
        return False
    try:
        stored = int(head[148:156].replace(b"\0", b" ").strip() or b"x", 8)
    except ValueError:
        return False
    # Het veld van de controlesom telt mee als spaties; oude tars telden met tekens
    rest = head[:148] + head[156:512]
    unsigned = sum(rest) + 8 * 0x20
    return stored in (unsigned, unsigned - 256 * sum(1 for b in rest if b >= 0x80))


def _mpeg_ts(head: bytes) -> bool:
    """MPEG-TS: pakketten van 188 bytes (192 bij m2ts/mts: 4 bytes tijd ervoor) die elk met
    0x47 beginnen; minstens drie, en elk pakket in head. Tekst is het nooit (een tekst met
    toevallig een G op die plekken)."""
    for size, start in ((188, 0), (192, 4)):
        offsets = range(start, len(head), size)
        if len(offsets) >= 3 and all(head[i] == 0x47 for i in offsets):
            return not _is_text(head)
    return False


def _ogg(head: bytes) -> str:
    if b"\x80theora" in head:
        return "video/ogg"
    return "audio/ogg"  # vorbis, opus, flac in ogg


def _mp3_frame(head: bytes) -> bool:
    """Een MPEG-audioframe (Layer III) zonder ID3-tag: alleen met geldige velden, want
    twee bytes alleen zijn te vaak toeval."""
    if len(head) < 4 or head[0] != 0xFF or head[1] & 0xE0 != 0xE0:
        return False
    version = (head[1] >> 3) & 0x03
    layer = (head[1] >> 1) & 0x03
    bitrate = head[2] >> 4
    rate = (head[2] >> 2) & 0x03
    return version != 0x01 and layer == 0x01 and bitrate not in (0, 0x0F) and rate != 0x03


def _bmp(head: bytes) -> bool:
    if len(head) < 18 or head[:2] != b"BM":
        return False
    reserved = head[6:10]
    header = int.from_bytes(head[14:18], "little")
    return reserved == b"\0\0\0\0" and header in (12, 40, 52, 56, 64, 108, 124)


def _ico(head: bytes) -> bool:
    # Gereserveerd 0, soort 1 (ico), 1 tot 255 afbeeldingen, eerste afbeelding met reserved 0
    count = int.from_bytes(head[4:6], "little")
    return head[:4] == b"\0\0\1\0" and 0 < count < 256 and len(head) >= 22 and head[9] == 0


def _signature(head: bytes, name: str) -> str | None:
    """Een duidelijke binaire handtekening, of None."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head.startswith(b"RIFF") and (riff := _riff(head)):
        return riff
    if mime := _ftyp(head, name):
        return mime
    if head[:4] in (b"II*\0", b"MM\0*"):
        return "image/tiff"
    if _bmp(head):
        return "image/bmp"
    if _ico(head):
        return "image/vnd.microsoft.icon"
    if head.startswith(b"8BPS") and head[4:6] in (b"\0\x01", b"\0\x02"):  # psd, psb
        return "image/vnd.adobe.photoshop"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return _zip(head, name)
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return OLE_BY_EXT.get(_ext(name), "application/x-ole-storage")
    if head.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "application/x-7z-compressed"
    if head.startswith(b"Rar!\x1a\x07"):
        return "application/vnd.rar"
    if head[:3] == b"BZh" and head[3:4].isdigit() and head[4:10] == b"\x31\x41\x59\x26\x53\x59":
        return "application/x-bzip2"
    if head.startswith(b"\xfd7zXZ\x00"):
        return "application/x-xz"
    if head.startswith(b"\x28\xb5\x2f\xfd"):
        return "application/zstd"
    if _tar(head):
        return "application/x-tar"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return _ebml(head, name)
    if _id3(head):
        # Na de ID3-tag kan ook FLAC komen; de grootte staat in 4 x 7 bits
        size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9] if len(head) > 9 else 0
        return "audio/flac" if head[10 + size : 14 + size] == b"fLaC" else "audio/mpeg"
    if head.startswith(b"fLaC") and head[4:5] in (b"\x00", b"\x80"):  # eerst STREAMINFO
        return "audio/flac"
    if head.startswith(b"OggS\0"):  # versie 0
        return _ogg(head)
    if head.startswith(b"MThd\0\0\0\x06"):
        return "audio/midi"
    if _mp3_frame(head):
        return "audio/mpeg"
    if len(head) > 2 and head[0] == 0xFF and head[1] & 0xF6 == 0xF0:
        return "audio/aac"  # ADTS
    if _mpeg_ts(head):
        return "video/mp2t"
    if head.startswith(b"SQLite format 3\0"):
        return "application/vnd.sqlite3"
    if head.startswith(b"\x7fELF"):
        return "application/x-executable"
    if head[:4] in (b"wOFF", b"wOF2"):
        return "font/woff" if head[3:4] == b"F" else "font/woff2"
    return None


def _is_text(head: bytes) -> bool:
    """Geldige UTF-8 zonder NUL-bytes. Het laatste teken mag halverwege zijn afgekapt."""
    if b"\0" in head:
        return False
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except UnicodeDecodeError:
        return False
    return True


def _text(name: str) -> str:
    ext = _ext(name)
    if ext in TEXT_BY_EXT:
        return TEXT_BY_EXT[ext]
    guess = _names.guess_type(name, strict=False)[0]
    if guess and (
        guess.startswith("text/") or guess in DOCUMENT_TYPES or guess.endswith(("+xml", "+json"))
    ):
        return guess
    return "text/plain"


def sniff(head: bytes, name: str) -> str:
    """Het soort (MIME-type) van een bestand met deze eerste bytes (HEAD is genoeg) en naam."""
    if mime := _signature(head, name):
        return mime
    if head and _is_text(head):
        return _text(name)
    if b"%PDF-" in head[:1024]:  # PDF met iets ervoor; een tekst die "%PDF-" noemt niet
        return "application/pdf"
    # Binair (of leeg): alleen nog de naam
    return by_name(name, text=not head) or "application/octet-stream"


def category(mime: str | None, kind: str = "file") -> str:
    """Grove soort voor filters en pictogrammen; zie CATEGORIES."""
    if kind == "dir":
        return "folder"
    mime = (mime or "").split(";")[0].strip().lower()
    if mime in ARCHIVE_TYPES:
        return "archive"
    if mime == "application/pdf":
        return "pdf"
    for prefix in ("image", "video", "audio"):
        if mime.startswith(prefix + "/"):
            return prefix
    if mime.startswith("text/") or mime in DOCUMENT_TYPES:
        return "document"
    return "other"


def read_head(path) -> bytes:
    """Eerste HEAD bytes van een bestand; leeg als het er niet is. Draai dit in een thread."""
    try:
        with open(path, "rb") as f:
            return f.read(HEAD)
    except FileNotFoundError:
        return b""
