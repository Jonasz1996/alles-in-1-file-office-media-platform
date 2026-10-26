"""De invoercontrole vóór elk bestand naar de document server (Keuze 23).

DS leest wat wij hem geven met zijn eigen code, op onze origin (Keuze 9). Daarom kijkt de app
eerst zelf of een bestand veilig genoeg is, met alleen zipfile en zlib, in een thread:

- zip-formaten (OOXML en ODF): elk onderdeel echt uitpakken en de bytes tellen. De opgegeven
  grootte geloven we niet; wijkt die af van wat eruit komt, dan is het bestand niet in orde.
  Stoppen zodra een grens bereikt is;
- de XML-delen (*.xml, *.rels) samen hooguit 50 MB, voor werkbladen (xlsx, ods) 150 MB;
- alles samen hooguit 512 MB en hooguit 10.000 onderdelen; boven 64 MB uitgepakt hooguit
  100 keer zo groot als het bestand;
- alleen de methoden stored en deflate, niets versleuteld (ook geen versleuteld OOXML, dat
  eigenlijk een OLE-bestand is, en geen versleuteld ODF);
- een ingebedde zip telt één niveau diep mee (met dezelfde grenzen voor het geheel);
- hooguit 20 s;
- doc, xls, ppt en rtf (geen zip): hooguit office_ole_max_bytes (20 MB).

De uitslag staat per sha (en extensie) een uur in het geheugen.

Redenen: too_big, ratio, entries, method, encrypted, time, en daarnaast corrupt (geen
geldige zip, of een onderdeel dat over zijn grootte liegt) en format (geen soort die DS van
ons krijgt).
"""

import struct
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import anyio.to_thread

from app.config import Settings

MB = 1024**2
TOTAL_BYTES = 512 * MB
MAX_ENTRIES = 10_000
RATIO = 100
RATIO_FROM = 64 * MB  # CONTENT_BYTES van B21
XML_BYTES = 50 * MB
XML_BYTES_SHEET = 150 * MB
SECONDS = 20.0
CACHE_SECONDS = 3600.0
CACHE_SIZE = 4096
CHUNK = 64 * 1024
# Een ingebedde zip blijft tot zo groot in het geheugen, daarboven op schijf
SPOOL_BYTES = 8 * MB

WORD = {"docx", "docm", "dotx", "dotm", "odt", "ott"}
SLIDES = {"pptx", "pptm", "potx", "potm", "ppsx", "ppsm", "odp", "otp"}
SHEETS = {"xlsx", "xlsm", "xltx", "xltm", "ods", "ots"}
ZIP_FORMATS = WORD | SLIDES | SHEETS
OLE_FORMATS = {"doc", "xls", "ppt", "rtf"}

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
LOCAL_HEADER = struct.Struct("<4sHHHHHIIIHH")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str | None = None  # too_big | ratio | entries | method | encrypted | time | ...


OK = Verdict(True)


class Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Budget:
    """Wat het hele bestand samen mag (ook de ingebedde zips)."""

    def __init__(self, file_size: int, xml_limit: int, deadline: float) -> None:
        self.file_size = max(file_size, 1)
        self.xml_limit = xml_limit
        self.deadline = deadline
        self.total = 0
        self.xml = 0
        self.entries = 0

    def tick(self) -> None:
        if time.monotonic() > self.deadline:
            raise Refused("time")

    def count(self, n: int, xml: bool) -> None:
        self.total += n
        if xml:
            self.xml += n
            if self.xml > self.xml_limit:
                raise Refused("too_big")
        if self.total > TOTAL_BYTES:
            raise Refused("too_big")
        if self.total > RATIO_FROM and self.total > RATIO * self.file_size:
            raise Refused("ratio")

    def add_entries(self, n: int) -> None:
        self.entries += n
        if self.entries > MAX_ENTRIES:
            raise Refused("entries")


# Zoveel bytes per regel van de centrale map, ruim (46 vast plus naam en extra velden)
DIRECTORY_BYTES_PER_ENTRY = 1024


def _declared_entries(fp: IO[bytes]) -> int:
    """Het aantal onderdelen volgens het einde van de zip, vóór zipfile alles inleest: een
    centrale map met miljoenen regels zou anders al het geheugen kosten. zipfile leest de
    hele centrale map, wat het aantal ook zegt; daarom telt ook haar grootte."""
    try:
        end = zipfile._EndRecData(fp)  # stdlib, al jaren gelijk
    except OSError:
        end = None
    if not end:
        raise Refused("corrupt")
    entries = end[zipfile._ECD_ENTRIES_TOTAL]
    if end[zipfile._ECD_SIZE] > MAX_ENTRIES * DIRECTORY_BYTES_PER_ENTRY:
        raise Refused("entries")
    return entries


def _is_xml(name: str) -> bool:
    lower = name.lower()
    return lower.endswith((".xml", ".rels"))


def _data_offset(fp: IO[bytes], info: zipfile.ZipInfo) -> int:
    fp.seek(info.header_offset)
    header = fp.read(LOCAL_HEADER.size)
    if len(header) != LOCAL_HEADER.size:
        raise Refused("corrupt")
    fields = LOCAL_HEADER.unpack(header)
    if fields[0] != ZIP_MAGIC:
        raise Refused("corrupt")
    return info.header_offset + LOCAL_HEADER.size + fields[9] + fields[10]


def _entry(
    fp: IO[bytes], info: zipfile.ZipInfo, budget: _Budget, end: int, keep: IO[bytes] | None
) -> int:
    """Pakt één onderdeel echt uit en telt de bytes; keep krijgt de inhoud (voor een
    ingebedde zip). De werkelijke grootte."""
    if info.flag_bits & 0x1:
        raise Refused("encrypted")
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise Refused("method")
    start = _data_offset(fp, info)
    if start + info.compress_size > end:
        raise Refused("corrupt")
    fp.seek(start)
    left = info.compress_size
    inflate = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
    xml = _is_xml(info.filename)
    size = 0

    def take(data: bytes) -> None:
        nonlocal size
        size += len(data)
        budget.count(len(data), xml)
        if keep is not None:
            keep.write(data)

    while left > 0:
        budget.tick()
        raw = fp.read(min(CHUNK, left))
        if not raw:
            raise Refused("corrupt")
        left -= len(raw)
        if inflate is None:
            take(raw)
            continue
        data = raw
        while data:
            try:
                out = inflate.decompress(data, CHUNK)
            except zlib.error as exc:
                raise Refused("corrupt") from exc
            take(out)
            data = inflate.unconsumed_tail
            if inflate.eof:
                break
            budget.tick()
        if inflate.eof:
            break
    if inflate is not None:
        while not inflate.eof:
            budget.tick()
            out = inflate.decompress(inflate.unconsumed_tail, CHUNK)
            if not out:
                break
            take(out)
        if not inflate.eof:
            raise Refused("corrupt")  # afgekapte stroom
    if size != info.file_size:
        raise Refused("corrupt")  # het onderdeel liegt over zijn grootte
    return size


def _zip(fp: IO[bytes], end: int, budget: _Budget, nested: bool) -> None:
    declared = _declared_entries(fp)
    budget.add_entries(declared)
    fp.seek(0)
    try:
        archive = zipfile.ZipFile(fp)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        raise Refused("corrupt") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) != declared or (not nested and not infos):
            raise Refused("corrupt")
        for info in infos:
            budget.tick()
            if info.is_dir():
                continue
            if nested:
                _entry(fp, info, budget, end, None)
                continue
            with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES) as inner:
                _entry_with_peek(fp, info, budget, end, inner)
                if inner.tell():
                    inner.seek(0)
                    if info.filename == "META-INF/manifest.xml":
                        _odf_encryption(inner)
                        continue
                    _zip(inner, inner.seek(0, 2), budget, nested=True)


def _entry_with_peek(
    fp: IO[bytes], info: zipfile.ZipInfo, budget: _Budget, end: int, inner: IO[bytes]
) -> None:
    """Als _entry, maar bewaart de inhoud als het een ingebedde zip is (of het manifest van
    ODF, om versleuteling te zien). inner blijft leeg als het niets van beide is."""
    if info.filename == "META-INF/manifest.xml":
        _entry(fp, info, budget, end, inner)
        return
    start = _data_offset(fp, info)
    if info.flag_bits & 0x1:
        raise Refused("encrypted")
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise Refused("method")
    # Eerst kijken of het een zip is: de eerste vier uitgepakte bytes
    fp.seek(start)
    head = fp.read(min(info.compress_size, CHUNK))
    if info.compress_type == zipfile.ZIP_DEFLATED:
        try:
            first = zlib.decompressobj(-15).decompress(head, 4)
        except zlib.error as exc:
            raise Refused("corrupt") from exc
    else:
        first = head[:4]
    _entry(fp, info, budget, end, inner if first == ZIP_MAGIC else None)


def _odf_encryption(manifest: IO[bytes]) -> None:
    """Een versleuteld ODF-document noemt encryption-data in zijn manifest."""
    needle = b"encryption-data"
    tail = b""
    while chunk := manifest.read(CHUNK):
        if needle in tail + chunk:
            raise Refused("encrypted")
        tail = chunk[-len(needle) :]


def _inspect(path: Path, ext: str, ole_max: int) -> Verdict:
    ext = ext.lower().lstrip(".")
    try:
        size = path.stat().st_size
        if ext in OLE_FORMATS:
            return OK if size <= ole_max else Verdict(False, "too_big")
        if ext not in ZIP_FORMATS:
            return Verdict(False, "format")
        limit = XML_BYTES_SHEET if ext in SHEETS else XML_BYTES
        budget = _Budget(size, limit, time.monotonic() + SECONDS)
        with path.open("rb") as fp:
            if fp.read(len(OLE_MAGIC)) == OLE_MAGIC:
                return Verdict(False, "encrypted")  # versleuteld OOXML is een OLE-bestand
            _zip(fp, size, budget, nested=False)
    except Refused as refused:
        return Verdict(False, refused.reason)
    except OSError:
        return Verdict(False, "corrupt")
    return OK


_cache: dict[tuple[str, str], tuple[Verdict, float]] = {}


async def check(settings: Settings, path: Path, ext: str, sha: str) -> Verdict:
    """Mag dit bestand (inhoud sha, soort ext) naar DS? In een thread; een uur bewaard."""
    key = (sha, ext.lower().lstrip("."))
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[1] > now:
        return cached[0]
    verdict = await anyio.to_thread.run_sync(_inspect, path, ext, settings.office_ole_max_bytes)
    if len(_cache) >= CACHE_SIZE:
        for old in [k for k, (_, expires) in _cache.items() if expires <= now] or [
            next(iter(_cache))
        ]:
            del _cache[old]
    _cache[key] = (verdict, time.monotonic() + CACHE_SECONDS)
    return verdict


def forget() -> None:
    """De cache leeg (tests)."""
    _cache.clear()
