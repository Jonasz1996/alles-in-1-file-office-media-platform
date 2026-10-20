import gzip
import io
import tarfile
import zipfile

import pytest

from app.mime import CATEGORIES, HEAD, category, read_head, sniff


def zip_bytes(entries: list[tuple[str, bytes]], first_stored: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (name, data) in enumerate(entries):
            method = zipfile.ZIP_STORED if first_stored and i == 0 else zipfile.ZIP_DEFLATED
            z.writestr(name, data, compress_type=method)
    return buffer.getvalue()


def ftyp(major: bytes, *compatible: bytes) -> bytes:
    body = major + b"\0\0\0\0" + b"".join(compatible)
    return (8 + len(body)).to_bytes(4, "big") + b"ftyp" + body + b"\0\0\0\x08free"


def riff(form: bytes) -> bytes:
    return b"RIFF" + (100).to_bytes(4, "little") + form + b"\0" * 32


def tar_bytes() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as t:
        info = tarfile.TarInfo("a.txt")
        info.size = 2
        t.addfile(info, io.BytesIO(b"hi"))
    return buffer.getvalue()


BMP = b"BM" + (70).to_bytes(4, "little") + b"\0\0\0\0" + (54).to_bytes(4, "little")
BMP += (40).to_bytes(4, "little") + b"\0" * 40
ICO = b"\0\0\1\0\1\0" + b"\x10\x10\0\0\1\0\x20\0" + b"\0" * 16
OOXML = [("[Content_Types].xml", b"<Types/>"), ("_rels/.rels", b"<x/>")]
EBML = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84"

SAMPLES = [
    # (naam, inhoud, verwacht)
    ("foto.jpg", b"\xff\xd8\xff\xe0\0\x10JFIF\0", "image/jpeg"),
    ("a.png", b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR", "image/png"),
    ("a.gif", b"GIF89a\x01\0\x01\0", "image/gif"),
    ("a.webp", riff(b"WEBP"), "image/webp"),
    ("a.avif", ftyp(b"avif", b"mif1", b"miaf"), "image/avif"),
    ("a.heic", ftyp(b"heic", b"mif1", b"heic"), "image/heic"),
    ("a.heic", ftyp(b"mif1", b"heic"), "image/heic"),
    ("a.bmp", BMP, "image/bmp"),
    ("a.tif", b"II*\0\x08\0\0\0", "image/tiff"),
    ("a.tif", b"MM\0*\0\0\0\x08", "image/tiff"),
    ("favicon.ico", ICO, "image/vnd.microsoft.icon"),
    ("a.pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", "application/pdf"),
    ("rare.pdf", b"\xef\xbb\xbfjunk\x80\x81 %PDF-1.4\n", "application/pdf"),
    ("brief.docx", zip_bytes(OOXML + [("word/document.xml", b"<w/>")]), None),
    ("zonder-naam", zip_bytes(OOXML + [("word/document.xml", b"<w/>")]), None),
    ("cijfers", zip_bytes(OOXML + [("xl/workbook.xml", b"<w/>")]), None),
    ("dia", zip_bytes(OOXML + [("ppt/presentation.xml", b"<w/>")]), None),
    (
        "brief",
        zip_bytes([("mimetype", b"application/vnd.oasis.opendocument.text")], True),
        "application/vnd.oasis.opendocument.text",
    ),
    (
        "a.ods",
        zip_bytes([("content.xml", b"<x/>")]),
        "application/vnd.oasis.opendocument.spreadsheet",
    ),
    ("a.odp", zip_bytes([("x", b"x")]), "application/vnd.oasis.opendocument.presentation"),
    ("boek", zip_bytes([("mimetype", b"application/epub+zip")], True), "application/epub+zip"),
    ("a.zip", zip_bytes([("a.txt", b"hallo")]), "application/zip"),
    ("leeg.zip", b"PK\x05\x06" + b"\0" * 18, "application/zip"),
    ("a.gz", gzip.compress(b"hallo"), "application/gzip"),
    ("a.tar.gz", gzip.compress(b"hallo"), "application/gzip"),
    ("a.7z", b"7z\xbc\xaf\x27\x1c\0\x04", "application/x-7z-compressed"),
    ("a.rar", b"Rar!\x1a\x07\x01\0", "application/vnd.rar"),
    ("a.tar", tar_bytes(), "application/x-tar"),
    ("a.bz2", b"BZh91AY&SY" + b"\0" * 8, "application/x-bzip2"),
    ("a.xz", b"\xfd7zXZ\0\0\x04", "application/x-xz"),
    ("film.mp4", ftyp(b"isom", b"iso2", b"avc1", b"mp41"), "video/mp4"),
    ("liedje.m4a", ftyp(b"M4A ", b"isom"), "audio/mp4"),
    ("liedje.m4a", ftyp(b"isom", b"iso2"), "audio/mp4"),
    ("film.mov", ftyp(b"qt  ", b"qt  "), "video/quicktime"),
    ("film.mkv", EBML + b"matroska", "video/x-matroska"),
    ("film.webm", EBML + b"webm", "video/webm"),
    ("geluid.weba", EBML + b"webm", "audio/webm"),
    ("film.avi", riff(b"AVI "), "video/x-msvideo"),
    ("geluid.wav", riff(b"WAVE"), "audio/wav"),
    ("a.mp3", b"ID3\x04\0\0\0\0\0\x10" + b"\0" * 16, "audio/mpeg"),
    ("a.mp3", b"\xff\xfb\x90\x64" + b"\0" * 16, "audio/mpeg"),
    ("a.flac", b"fLaC\0\0\0\x22", "audio/flac"),
    ("a.flac", b"ID3\x04\0\0\0\0\0\x04\0\0\0\0fLaC", "audio/flac"),
    ("a.opus", b"OggS\0\x02" + b"\0" * 22 + b"OpusHead", "audio/ogg"),
    ("a.ogv", b"OggS\0\x02" + b"\0" * 22 + b"\x80theora", "video/ogg"),
    ("a.aac", b"\xff\xf1\x50\x80", "audio/aac"),
    ("data.db", b"SQLite format 3\0", "application/vnd.sqlite3"),
    # Tekst: de naam zegt welk soort tekst
    ("notitie.txt", b"Gewoon tekst\n", "text/plain"),
    ("LEESMIJ.md", "# Kopje met één accent".encode(), "text/markdown"),
    ("lijst.csv", b"a;b\n1;2\n", "text/csv"),
    ("data.json", b'{"a": 1}', "application/json"),
    ("conf.yaml", b"a: 1\n", "application/yaml"),
    ("conf.yml", b"a: 1\n", "application/yaml"),
    ("a.xml", b"<?xml version='1.0'?><a/>", "application/xml"),
    ("pagina.html", b"<!doctype html><p>hoi</p>", "text/html"),
    ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
    ("app.py", b"print('hoi')\n", "text/x-python"),
    ("app.ts", b"const a: number = 1;\n", "text/x-typescript"),
    ("main.go", b"package main\n", "text/x-go"),
    ("Makefile", b"all:\n\techo hoi\n", "text/plain"),
    ("tekst-zonder-extensie", b"hallo", "text/plain"),
    # Een tekst die "%PDF-" noemt, blijft tekst
    ("over-pdf.md", b"Een PDF begint met %PDF-1.7\n", "text/markdown"),
    # Leeg: alleen de naam zegt iets
    ("leeg.docx", b"", None),
    ("leeg.txt", b"", "text/plain"),
    ("leeg", b"", "application/octet-stream"),
    # Onbekende binaire inhoud: de naam, en anders octet-stream
    ("onbekend.bin", b"\x00\x01\x02\x03\xfe", "application/octet-stream"),
    ("raar.xyz", b"\x00\x9f\x92\x96", "application/octet-stream"),
]
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EXPECTED_OFFICE = {
    "brief.docx": DOCX,
    "zonder-naam": DOCX,
    "cijfers": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "dia": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "leeg.docx": DOCX,
}


@pytest.mark.parametrize(("name", "data", "expected"), SAMPLES)
def test_sniff(name, data, expected):
    assert sniff(data[:HEAD], name) == (expected or EXPECTED_OFFICE[name])


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        # De naam wint nooit van een duidelijke handtekening
        ("eigenlijk-een-foto.txt", b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR", "image/png"),
        ("pagina.html", b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR", "image/png"),
        ("script.js", b"\xff\xd8\xff\xe0\0\x10JFIF\0", "image/jpeg"),
        ("rapport.docx", b"%PDF-1.4\n%\xe2\xe3", "application/pdf"),
        ("film.mp4", b"ID3\x03\0\0\0\0\0\0", "audio/mpeg"),
        # En tekst met de naam van een binair formaat is gewoon tekst
        ("zee.jpg", b"jpegdata", "text/plain"),
        ("foto.png", b"<script>alert(1)</script>", "text/plain"),
    ],
)
def test_naam_wint_niet_van_de_inhoud(name, data, expected):
    assert sniff(data, name) == expected


def test_afgekapt_utf8_teken_aan_het_eind_is_nog_tekst():
    data = ("é" * HEAD).encode()[: HEAD - 1]  # het laatste teken is half
    assert sniff(data, "a.txt") == "text/plain"
    assert sniff(b"abc\xc3", "a.txt") == "text/plain"
    assert sniff(b"abc\xc3(", "a") == "application/octet-stream"  # ongeldig in het midden
    assert sniff(b"abc\0def", "a") == "application/octet-stream"  # NUL: geen tekst


def test_bmp_en_mp3_niet_bij_toeval():
    assert sniff(b"BMW is een merk\n", "auto.txt") == "text/plain"
    assert sniff(b"\xff\xfb\xf0\x64", "x") == "application/octet-stream"  # bitrate ongeldig


@pytest.mark.parametrize(
    ("mime", "kind", "expected"),
    [
        (None, "dir", "folder"),
        ("image/png", "file", "image"),
        ("image/svg+xml", "file", "image"),
        ("video/mp4", "file", "video"),
        ("audio/ogg", "file", "audio"),
        ("application/pdf", "file", "pdf"),
        ("text/plain", "file", "document"),
        ("text/markdown; charset=utf-8", "file", "document"),
        (DOCX, "file", "document"),
        ("application/vnd.oasis.opendocument.spreadsheet", "file", "document"),
        ("application/msword", "file", "document"),
        ("application/json", "file", "document"),
        ("application/epub+zip", "file", "document"),
        ("application/zip", "file", "archive"),
        ("application/gzip", "file", "archive"),
        ("application/vnd.rar", "file", "archive"),
        ("application/x-7z-compressed", "file", "archive"),
        ("application/x-tar", "file", "archive"),
        ("application/octet-stream", "file", "other"),
        ("application/vnd.sqlite3", "file", "other"),
        (None, "file", "other"),
    ],
)
def test_category(mime, kind, expected):
    assert category(mime, kind) == expected
    assert expected in CATEGORIES


def test_read_head(tmp_path):
    path = tmp_path / "groot.bin"
    path.write_bytes(b"x" * (HEAD * 3))
    assert read_head(path) == b"x" * HEAD
    assert read_head(tmp_path / "weg") == b""
