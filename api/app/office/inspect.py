"""De invoercontrole vóór elk bestand naar de document server (Keuze 23).

DS leest wat wij hem geven met zijn eigen code, op onze origin (Keuze 9). Daarom kijkt de app
eerst zelf of een bestand veilig genoeg is, met alleen zipfile en zlib, in een thread:

- de inhoud beslist, niet de naam: DS kijkt zelf naar de bytes en negeert de opgegeven soort
  [gemeten]. Een zip-formaat moet met PK\x03\x04 beginnen, doc, xls en ppt met de kop van
  OLE, rtf met {\rtf; anders "format" (een zip met de naam .doc kreeg vroeger alleen de
  controle op grootte);
- zip-formaten (OOXML en ODF): elk onderdeel echt uitpakken en de bytes tellen. De opgegeven
  grootte geloven we niet; wijkt die af van wat eruit komt, dan is het bestand niet in orde.
  Stoppen zodra een grens bereikt is;
- de XML samen hooguit XML_BYTES (OOXML-tekst en -presentaties), XML_BYTES_ODF (elk
  ODF-document) of XML_BYTES_SHEET (een OOXML-werkblad). Ook hier de inhoud: XML is elk
  onderdeel dat op .xml of .rels eindigt, en elk ander onderdeel dat (na een BOM en witruimte)
  met < begint, want in OOXML mag het hoofddeel elke naam hebben. De grens is de kleinste van
  wat de naam en wat de inhoud zegt: alleen een bestand dat echt een werkblad is (het
  hoofddeel in [Content_Types].xml, en geen tekst of presentatie) krijgt de ruimere grens;
- alles samen hooguit 512 MB en hooguit 10.000 onderdelen; boven 64 MB uitgepakt hooguit
  100 keer zo groot als het bestand;
- alleen de methoden stored en deflate, niets versleuteld (ook geen versleuteld OOXML, dat
  eigenlijk een OLE-bestand is, en geen versleuteld ODF);
- een ingebedde zip telt één niveau diep mee (met dezelfde grenzen voor het geheel);
- hooguit 20 s;
- doc, xls, ppt en rtf (geen zip): hooguit office_ole_max_bytes (20 MB).

De XML-grenzen komen uit een meting bij 2 GB geheugen (DS 10.0.0.1, omzetten naar PDF,
docs/fase-4/spike.md A11): x2t liep vast (OOM, error -3) op een docx met 10 MB XML, een odt
met 7 MB en een xlsx met 30 MB; een docx met 7 MB, een odt met 5 MB en een xlsx met 20 MB
gingen nog, maar op 85 tot 100 % van het geheugen. De grenzen liggen daar ruim onder (4, 2 en
10 MB: hooguit ±1,3 GB). De integratie meet ze opnieuw (§4.7). Let op: de grootte van de XML
is maar een maat. Een docx met 3,9 MB aan alleen pagina-einden (±95.000 pagina's) ging nog,
maar op ±97 %; daarvoor zijn de geheugengrens van de container (x2t gaat dood, docservice
blijft) en de twee pogingen van een conversie (Keuze 25).

De uitslag staat per sha (en extensie) een uur in het geheugen.

Redenen: too_big, ratio, entries, method, encrypted, time, en daarnaast corrupt (geen
geldige zip, of een onderdeel dat over zijn grootte liegt) en format (geen soort die DS van
ons krijgt).
"""

import re
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
# Gemeten bij 2 GB (zie boven); de integratie meet opnieuw (§4.7)
XML_BYTES = 4 * MB  # OOXML-tekst en -presentaties (vast bij 10 MB, docx)
XML_BYTES_ODF = 2 * MB  # elk ODF-document (vast bij 7 MB, odt)
XML_BYTES_SHEET = 10 * MB  # een echt OOXML-werkblad (vast bij 30 MB, xlsx)
SECONDS = 20.0
CACHE_SECONDS = 3600.0
CACHE_SIZE = 4096
CHUNK = 64 * 1024
# Een ingebedde zip blijft tot zo groot in het geheugen, daarboven op schijf
SPOOL_BYTES = 8 * MB

WORD = {"docx", "docm", "dotx", "dotm", "odt", "ott"}
SLIDES = {"pptx", "pptm", "potx", "potm", "ppsx", "ppsm", "odp", "otp"}
SHEETS = {"xlsx", "xlsm", "xltx", "xltm", "ods", "ots"}
ODF = {"odt", "ott", "odp", "otp", "ods", "ots"}
ZIP_FORMATS = WORD | SLIDES | SHEETS
OLE_FORMATS = {"doc", "xls", "ppt", "rtf"}

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
RTF_MAGIC = b"{\\rtf"
LOCAL_HEADER = struct.Struct("<4sHHHHHIIIHH")

# Wat vóór het eerste teken van XML mag staan: witruimte, NUL (UTF-16 en UTF-32) en de bytes
# van een BOM. Vier NUL's achter elkaar komen in geen enkele codering van XML voor: dan is het
# geen XML (zo telt een onderdeel vol nullen niet als XML)
LEADING = b" \t\r\n\x00\xef\xbb\xbf\xfe\xff"
NULS = b"\x00" * 4
# Onderdelen die zeggen wat voor document het is; ze blijven zo groot in het geheugen
CONTENT_TYPES = "[content_types].xml"
ODF_MARKERS = {"mimetype", "content.xml", "meta-inf/manifest.xml"}
CAPTURE_BYTES = 1 * MB
# Het hoofddeel in [Content_Types].xml (kleine letters, zoals de inhoud na lower())
SHEET_MAIN = re.compile(rb"(?:spreadsheetml|ms-excel)\.[a-z.]*main")
OTHER_MAIN = re.compile(rb"(?:wordprocessingml|presentationml|ms-word|ms-powerpoint)\.[a-z.]*main")


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
    """Wat het hele bestand samen mag (ook de ingebedde zips). Onderweg geldt de XML-grens van
    de naam; na het lezen de kleinste van die en wat de inhoud zegt (_content_limit)."""

    def __init__(self, file_size: int, xml_limit: int, deadline: float) -> None:
        self.file_size = max(file_size, 1)
        self.xml_limit = xml_limit
        self.deadline = deadline
        self.total = 0
        self.xml = 0
        self.entries = 0
        # Wat het document over zichzelf zegt (alleen het buitenste niveau)
        self.content_types: list[bytes | None] = []  # None: groter dan CAPTURE_BYTES
        self.odf = False

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


class _Sniff:
    """Is een onderdeel XML? Beslist op het eerste teken na witruimte, NUL's en een BOM: < is
    XML. Het stuk waarin dat teken staat, telt al naar die uitslag; stukken met alleen
    witruimte daarvóór tellen als XML (voorzichtig)."""

    def __init__(self) -> None:
        self.xml: bool | None = None
        self.tail = b""

    def feed(self, data: bytes) -> bool:
        """Telt dit stuk als XML?"""
        if self.xml is None:
            rest = data.lstrip(LEADING)
            lead = self.tail + data[: len(data) - len(rest)]
            if NULS in lead:
                self.xml = False
            elif rest:
                self.xml = rest[:1] == b"<"
            else:
                self.tail = lead[-3:]
                return True
        return self.xml


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
    sniff = None if _is_xml(info.filename) else _Sniff()
    size = 0

    def take(data: bytes) -> None:
        nonlocal size
        size += len(data)
        budget.count(len(data), sniff is None or sniff.feed(data))
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
            name = info.filename.lower()
            if name in ODF_MARKERS:
                budget.odf = True
            with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES) as inner:
                _entry_with_peek(fp, info, budget, end, inner)
                if name == CONTENT_TYPES:
                    size = inner.tell()
                    inner.seek(0)
                    budget.content_types.append(
                        inner.read(CAPTURE_BYTES) if size <= CAPTURE_BYTES else None
                    )
                    continue
                if inner.tell():
                    inner.seek(0)
                    if name == "meta-inf/manifest.xml":
                        _odf_encryption(inner)
                        continue
                    _zip(inner, inner.seek(0, 2), budget, nested=True)


def _entry_with_peek(
    fp: IO[bytes], info: zipfile.ZipInfo, budget: _Budget, end: int, inner: IO[bytes]
) -> None:
    """Als _entry, maar bewaart de inhoud als het een ingebedde zip is (of het manifest van
    ODF, om versleuteling te zien, of [Content_Types].xml, om de soort te zien). inner blijft
    leeg als het niets daarvan is."""
    if info.filename.lower() in ("meta-inf/manifest.xml", CONTENT_TYPES):
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


def _name_limit(ext: str) -> int:
    if ext in ODF:
        return XML_BYTES_ODF
    return XML_BYTES_SHEET if ext in SHEETS else XML_BYTES


def _is_sheet(content_types: bytes | None) -> bool:
    """Een OOXML-werkblad volgens [Content_Types].xml: een hoofddeel van een werkblad, en geen
    van tekst of presentatie. Alleen platte tekst zonder entiteiten; wat we niet zeker kunnen
    lezen, is geen werkblad."""
    if content_types is None or b"&" in content_types or b"\x00" in content_types:
        return False
    lower = content_types.lower()
    return SHEET_MAIN.search(lower) is not None and OTHER_MAIN.search(lower) is None


def _content_limit(budget: _Budget) -> int:
    """De XML-grens volgens de inhoud. Twijfel geeft de strengste."""
    if budget.odf:
        return XML_BYTES_ODF
    if budget.content_types and all(_is_sheet(c) for c in budget.content_types):
        return XML_BYTES_SHEET
    return XML_BYTES


def _inspect(path: Path, ext: str, ole_max: int) -> Verdict:
    ext = ext.lower().lstrip(".")
    try:
        if ext not in ZIP_FORMATS and ext not in OLE_FORMATS:
            return Verdict(False, "format")
        size = path.stat().st_size
        with path.open("rb") as fp:
            head = fp.read(len(OLE_MAGIC))
            if ext in OLE_FORMATS:
                # Geen zip: DS kijkt naar de inhoud, dus een zip met de naam .doc zou anders
                # zonder de controles hieronder bij DS komen
                magic = RTF_MAGIC if ext == "rtf" else OLE_MAGIC
                if not head.startswith(magic):
                    return Verdict(False, "format")
                return OK if size <= ole_max else Verdict(False, "too_big")
            if head.startswith(OLE_MAGIC):
                return Verdict(False, "encrypted")  # versleuteld OOXML is een OLE-bestand
            if not head.startswith(ZIP_MAGIC):
                return Verdict(False, "corrupt")
            budget = _Budget(size, _name_limit(ext), time.monotonic() + SECONDS)
            _zip(fp, size, budget, nested=False)
            if budget.xml > min(_name_limit(ext), _content_limit(budget)):
                return Verdict(False, "too_big")
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
