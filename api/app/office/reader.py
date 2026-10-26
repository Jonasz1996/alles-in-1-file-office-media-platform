"""Een vereenvoudigde lezer voor kantoordocumenten (B21): docx, xlsx, pptx, odt, ods en odp.
Alleen de standaardbibliotheek (zipfile en expat, de parser onder ElementTree), want dit draait
in het renderproces (app/render.py), dat snel moet starten.

Wat eruit komt is tekst zonder opmaak (op vet en cursief na): koppen, alinea's, lijsten en
tabellen van een document; de eerste 200 rijen en 50 kolommen van elk werkblad; titel en
opsommingen van elke dia. Plus de metadata (titel, auteur, aantal pagina's) en de ingebedde
thumbnail. Het resultaat (OfficeDoc) gaat als doc.json naast de preview, hooguit 512 KB.

Een kwaadaardig bestand mag niets kapot maken:
- elk onderdeel van het zip-bestand heeft een grens op zijn uitgepakte grootte (zipfile pakt
  nooit meer uit dan de opgegeven grootte, dus een liegend zip-bestand helpt niet);
- de XML wordt in stukken gelezen (expat) en het lezen stopt zodra er genoeg is; een DOCTYPE
  of entiteit (de "billion laughs") wordt geweigerd;
- herhaalde rijen en cellen van ODF (table:number-rows-repeated="1048576") worden afgekapt;
- na READ_SECONDS stopt het lezen met wat er dan is.

read(path) -> {"doc": OfficeDoc, "meta": {...}, "thumbnail": bytes | None}. OfficeDoc:
  {"type": "document", "blocks": [Block], "truncated": bool}
     Block = {"type": "h", "level": 1-6, "runs": [Run]} | {"type": "p", "runs": [Run]}
           | {"type": "li", "level": 0.., "ordered": bool, "runs": [Run]}
           | {"type": "table", "rows": [[str]]}
     Run = {"text": str, "b"?: true, "i"?: true}
  {"type": "sheet", "sheets": [{"name", "rows": [[str]], "truncated": bool}], "truncated"}
  {"type": "slides", "size": [w_pt, h_pt] | None, "slides": [{"title", "bullets":
     [{"text", "level"}]}], "truncated": bool}
"""

import json
import posixpath
import re
import signal
import zipfile
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO
from xml.etree.ElementTree import Element, TreeBuilder
from xml.parsers import expat

# Grenzen
CONTENT_BYTES = 64 * 1024 * 1024  # het hoofddeel (document.xml, een werkblad, content.xml)
SMALL_BYTES = 8 * 1024 * 1024  # stijlen, relaties, workbook.xml en dergelijke
THUMB_BYTES = 8 * 1024 * 1024
DOC_BYTES = 512 * 1024  # doc.json
TEXT_CHARS = 300_000  # alle tekst samen
MAX_BLOCKS = 5000
MAX_TABLE_ROWS = 500
MAX_ROWS = 200
MAX_COLS = 50
MAX_SHEETS = 32
MAX_SLIDES = 300
MAX_BULLETS = 100
MAX_SHARED = 200_000
CELL_CHARS = 1000
META_CHARS = 500
READ_SECONDS = 20
CHUNK = 64 * 1024

# Namespaces
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
EP = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"
DC = "{http://purl.org/dc/elements/1.1/}"
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
PRES = "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}"
STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
FO = "{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}"
META = "{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}"

THUMBNAIL_REL = "/metadata/thumbnail"
ODF_KINDS = {
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.text-template": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.spreadsheet-template": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/vnd.oasis.opendocument.presentation-template": "odp",
}
DOC_TYPES = {"docx": "document", "odt": "document", "xlsx": "sheet", "ods": "sheet"}
DOC_TYPES |= {"pptx": "slides", "odp": "slides"}


class Unreadable(Exception):
    """Geen kantoordocument dat we kunnen lezen (of een poging tot misbruik)."""


class Stop(Exception):
    """Genoeg gelezen: het lezen van dit onderdeel stopt hier."""


class TooSlow(Exception):
    pass


# --- XML in stukken lezen --------------------------------------------------------------


def _refuse(*_args: Any) -> None:
    raise Unreadable("DOCTYPE of entiteit in een kantoordocument")


def sax(stream: BinaryIO, start, end=None, text=None) -> None:
    """Leest XML in stukken en roept start(tag, attrs), end(tag) en text(data) aan, met
    namen als in ElementTree ("{namespace}naam"). Stop in een callback stopt het lezen
    (geen fout). Een DOCTYPE of een entiteit is een fout."""
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse

    def name(raw: str) -> str:
        return "{" + raw if "}" in raw else raw

    parser.StartElementHandler = lambda tag, attrs: start(
        name(tag), {name(k): v for k, v in attrs.items()}
    )
    if end is not None:
        parser.EndElementHandler = lambda tag: end(name(tag))
    if text is not None:
        parser.CharacterDataHandler = text
    try:
        while chunk := stream.read(CHUNK):
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except Stop:
        return
    except expat.ExpatError as e:
        raise Unreadable(str(e)) from e


def tree(data: bytes) -> Element:
    """Een klein XML-onderdeel als ElementTree, met dezelfde bescherming als sax()."""
    import io

    builder = TreeBuilder()
    sax(io.BytesIO(data), builder.start, builder.end, builder.data)
    root = builder.close()
    if root is None:
        raise Unreadable("leeg XML-onderdeel")
    return root


class Package:
    """Het zip-bestand, met een grens per onderdeel."""

    def __init__(self, archive: zipfile.ZipFile):
        self.zip = archive
        self.names = {i.filename: i for i in archive.infolist()}

    def has(self, name: str) -> bool:
        return name in self.names

    def open(self, name: str, limit: int | None = None) -> BinaryIO:
        limit = CONTENT_BYTES if limit is None else limit
        info = self.names.get(name)
        if info is None:
            raise Unreadable(f"{name} ontbreekt")
        if info.file_size > limit:
            raise Unreadable(f"{name} is te groot")
        return self.zip.open(info)  # type: ignore[return-value]

    def read(self, name: str, limit: int = SMALL_BYTES) -> bytes:
        with self.open(name, limit) as f:
            return f.read(limit + 1)

    def xml(self, name: str, limit: int = SMALL_BYTES) -> Element | None:
        if name not in self.names:
            return None
        return tree(self.read(name, limit))

    def rels(self, part: str) -> dict[str, tuple[str, str]]:
        """Relaties van een onderdeel: Id -> (Type, pad in het zip-bestand)."""
        folder, base = posixpath.split(part)
        root = self.xml(posixpath.join(folder, "_rels", base + ".rels"))
        result: dict[str, tuple[str, str]] = {}
        if root is None:
            return result
        for rel in root.iter(f"{PR}Relationship"):
            target = rel.get("Target") or ""
            if rel.get("TargetMode") == "External" or not target:
                continue
            path = target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
            result[rel.get("Id") or ""] = (rel.get("Type") or "", posixpath.normpath(path))
        return result


# --- tekst bijhouden ------------------------------------------------------------------------


class Budget:
    """Hoeveel tekst er nog bij mag (alle onderdelen samen)."""

    def __init__(self, chars: int = TEXT_CHARS):
        self.left = chars
        self.truncated = False

    def take(self, text: str) -> str:
        if self.left <= 0:
            self.truncated = True
            return ""
        if len(text) > self.left:
            self.truncated = True
            text = text[: self.left]
        self.left -= len(text)
        return text

    @property
    def spent(self) -> bool:
        return self.left <= 0


def _runs(parts: list[tuple[str, bool, bool]]) -> list[dict[str, Any]]:
    """[(tekst, vet, cursief)] -> runs, met naast elkaar liggende gelijke stukken samen."""
    runs: list[dict[str, Any]] = []
    for text, bold, italic in parts:
        if not text:
            continue
        if runs and bool(runs[-1].get("b")) == bold and bool(runs[-1].get("i")) == italic:
            runs[-1]["text"] += text
            continue
        run: dict[str, Any] = {"text": text}
        if bold:
            run["b"] = True
        if italic:
            run["i"] = True
        runs.append(run)
    return runs


def _plain(parts: list[tuple[str, bool, bool]]) -> str:
    return "".join(p[0] for p in parts)


def _on(value: str | None) -> bool:
    """w:b zonder w:val is aan; w:val="0" of "false" uit."""
    return value is None or value.lower() not in ("0", "false", "off", "none")


# --- docx -----------------------------------------------------------------------------------


HEADING = re.compile(r"^(?:heading|kop)\s*([1-9])$", re.I)


def _docx_styles(pkg: Package) -> dict[str, int]:
    """styleId -> kopniveau (1-6) voor stijlen die een kop zijn."""
    root = pkg.xml("word/styles.xml")
    levels: dict[str, int] = {}
    based: dict[str, str] = {}
    if root is None:
        return levels
    for style in root.iter(f"{W}style"):
        sid = style.get(f"{W}styleId") or ""
        name_el = style.find(f"{W}name")
        name = (name_el.get(f"{W}val") if name_el is not None else "") or ""
        outline = style.find(f"{W}pPr/{W}outlineLvl")
        if (m := HEADING.match(name.strip())) is not None:
            levels[sid] = min(6, int(m.group(1)))
        elif name.strip().lower() == "title":
            levels[sid] = 1
        elif outline is not None and (outline.get(f"{W}val") or "").isdigit():
            level = int(outline.get(f"{W}val") or "9") + 1
            if level <= 6:
                levels[sid] = level
        parent = style.find(f"{W}basedOn")
        if parent is not None and parent.get(f"{W}val"):
            based[sid] = parent.get(f"{W}val") or ""
    for sid, parent in based.items():  # één stap overerving (bijv. een eigen kopstijl)
        if sid not in levels and parent in levels:
            levels[sid] = levels[parent]
    return levels


def _docx_numbering(pkg: Package) -> dict[tuple[str, int], bool]:
    """(numId, niveau) -> genummerd (True) of opsomming (False)."""
    root = pkg.xml("word/numbering.xml")
    result: dict[tuple[str, int], bool] = {}
    if root is None:
        return result
    abstract: dict[str, dict[int, bool]] = {}
    for a in root.iter(f"{W}abstractNum"):
        levels = {}
        for lvl in a.iter(f"{W}lvl"):
            fmt = lvl.find(f"{W}numFmt")
            value = (fmt.get(f"{W}val") if fmt is not None else "bullet") or "bullet"
            levels[int(lvl.get(f"{W}ilvl") or 0)] = value not in ("bullet", "none")
        abstract[a.get(f"{W}abstractNumId") or ""] = levels
    for num in root.iter(f"{W}num"):
        ref = num.find(f"{W}abstractNumId")
        levels = abstract.get((ref.get(f"{W}val") if ref is not None else "") or "", {})
        for level, ordered in levels.items():
            result[(num.get(f"{W}numId") or "", level)] = ordered
    return result


def read_docx(pkg: Package, budget: Budget) -> dict[str, Any]:
    headings = _docx_styles(pkg)
    numbering = _docx_numbering(pkg)
    blocks: list[dict[str, Any]] = []
    state: dict[str, Any] = {
        "para": None,  # {"style", "num", "ilvl", "outline", "parts"}
        "run": [False, False],
        "in_rpr": False,
        "in_text": False,
        "skip": 0,  # in een tekstvak of Fallback: overslaan
        "tables": [],  # stapel tabellen; alleen de buitenste krijgt rijen
    }
    skip_tags = {f"{W}txbxContent", f"{MC}Fallback", f"{W}del", f"{W}instrText"}

    def emit(block: dict[str, Any]) -> None:
        if len(blocks) >= MAX_BLOCKS or budget.spent:
            budget.truncated = True
            raise Stop
        blocks.append(block)

    def start(tag: str, attrs: dict[str, str]) -> None:
        if state["skip"] or tag in skip_tags:
            state["skip"] += 1
            return
        para = state["para"]
        if tag == f"{W}p":
            state["para"] = {"style": "", "num": None, "ilvl": 0, "outline": None, "parts": []}
        elif tag == f"{W}tbl":
            state["tables"].append({"rows": [], "cell": None})
        elif tag == f"{W}tr" and len(state["tables"]) == 1:
            table = state["tables"][0]
            if len(table["rows"]) < MAX_TABLE_ROWS:
                table["rows"].append([])
        elif tag == f"{W}tc" and len(state["tables"]) == 1:
            state["tables"][0]["cell"] = []
        elif para is None:
            return
        elif tag == f"{W}pStyle":
            para["style"] = attrs.get(f"{W}val", "")
        elif tag == f"{W}numId":
            para["num"] = attrs.get(f"{W}val")
        elif tag == f"{W}ilvl":
            value = attrs.get(f"{W}val", "0")
            para["ilvl"] = min(8, int(value)) if value.isdigit() else 0
        elif tag == f"{W}outlineLvl" and attrs.get(f"{W}val", "").isdigit():
            level = int(attrs[f"{W}val"]) + 1
            para["outline"] = level if level <= 6 else None  # 9 = gewone tekst
        elif tag == f"{W}r":
            state["run"] = [False, False]
        elif tag == f"{W}rPr":
            state["in_rpr"] = True
        elif state["in_rpr"] and tag == f"{W}b":
            state["run"][0] = _on(attrs.get(f"{W}val"))
        elif state["in_rpr"] and tag == f"{W}i":
            state["run"][1] = _on(attrs.get(f"{W}val"))
        elif tag == f"{W}t":
            state["in_text"] = True
        elif tag == f"{W}tab":
            para["parts"].append(("\t", *state["run"]))
        elif tag in (f"{W}br", f"{W}cr"):
            para["parts"].append(("\n", *state["run"]))

    def end(tag: str) -> None:
        if state["skip"]:
            state["skip"] -= 1
            return
        if tag == f"{W}t":
            state["in_text"] = False
        elif tag == f"{W}rPr":
            state["in_rpr"] = False
        elif tag == f"{W}p" and state["para"] is not None:
            para, state["para"] = state["para"], None
            text = _plain(para["parts"])
            if state["tables"]:
                cell = state["tables"][0]["cell"]
                if cell is not None:
                    cell.append(text)
                return
            runs = _runs(para["parts"])
            level = para["outline"] or headings.get(para["style"])
            if level and text.strip():
                emit({"type": "h", "level": min(6, level), "runs": runs})
            elif para["num"] not in (None, "0"):
                ordered = numbering.get((para["num"], para["ilvl"]), False)
                emit({"type": "li", "level": para["ilvl"], "ordered": ordered, "runs": runs})
            else:
                emit({"type": "p", "runs": runs})
        elif tag == f"{W}tc" and len(state["tables"]) == 1:
            table = state["tables"][0]
            if table["cell"] is not None and table["rows"] and len(table["rows"][-1]) < MAX_COLS:
                table["rows"][-1].append("\n".join(table["cell"])[:CELL_CHARS])
            table["cell"] = None
        elif tag == f"{W}tbl" and state["tables"]:
            table = state["tables"].pop()
            if not state["tables"]:
                emit({"type": "table", "rows": table["rows"]})

    def text(data: str) -> None:
        if state["in_text"] and not state["skip"] and state["para"] is not None:
            state["para"]["parts"].append((budget.take(data), *state["run"]))

    with pkg.open("word/document.xml") as f:
        sax(f, start, end, text)
    return {"type": "document", "blocks": blocks, "truncated": budget.truncated}


# --- xlsx -----------------------------------------------------------------------------------

BUILTIN_DATES = set(range(14, 23)) | {45, 46, 47}
BUILTIN_TIMES = {18, 19, 20, 21, 45, 46, 47}
PERCENT = {9, 10}
CELL_REF = re.compile(r"^([A-Z]{1,3})(\d+)$")


def column_index(letters: str) -> int:
    """ "A" -> 0, "AA" -> 26."""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def date_format(code: str) -> str | None:
    """Wat een eigen getalnotatie toont: "date", "time", "datetime" of None (een getal)."""
    cleaned = re.sub(r'"[^"]*"|\\.|_.|\*.', "", code)
    cleaned = re.sub(r"\[(?:h+|m+|s+)\]", "h", cleaned, flags=re.I)  # verstreken tijd
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned).lower()
    cleaned = cleaned.split(";")[0]
    has_date = bool(re.search(r"[yd]", cleaned))
    has_time = bool(re.search(r"[hs]", cleaned))
    if not has_date and not has_time and "m" in cleaned and not re.search(r"[0#?]", cleaned):
        has_date = True  # alleen "mmm": een maand
    if has_date and has_time:
        return "datetime"
    return "date" if has_date else "time" if has_time else None


def format_serial(value: float, kind: str, date1904: bool) -> str:
    epoch = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    try:
        moment = epoch + timedelta(days=value)
    except OverflowError:
        return format_number(value)
    moment = moment.replace(microsecond=0) + timedelta(seconds=round(moment.microsecond / 1e6))
    if kind == "time":
        return moment.strftime("%H:%M:%S")
    if kind == "datetime" or (kind == "date" and value % 1):
        return moment.strftime("%Y-%m-%d %H:%M")
    return moment.strftime("%Y-%m-%d")


def format_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.15g}"


def _sheet_rows(cells: dict[int, dict[int, str]], last_row: int) -> list[list[str]]:
    rows = []
    for r in range(last_row + 1):
        row = cells.get(r, {})
        width = max(row) + 1 if row else 0
        rows.append([row.get(c, "") for c in range(width)])
    while rows and not rows[-1]:
        rows.pop()
    return rows


def read_xlsx(pkg: Package, budget: Budget) -> dict[str, Any]:
    book = pkg.xml("xl/workbook.xml")
    if book is None:
        raise Unreadable("xl/workbook.xml ontbreekt")
    rels = pkg.rels("xl/workbook.xml")
    pr = book.find(f"{S}workbookPr")
    date1904 = pr is not None and _on(pr.get("date1904")) and pr.get("date1904") is not None
    shared = _shared_strings(pkg, rels, budget)
    formats = _xlsx_formats(pkg, rels)
    sheets: list[dict[str, Any]] = []
    truncated = False
    for sheet in book.iter(f"{S}sheet"):
        if len(sheets) >= MAX_SHEETS:
            truncated = True
            break
        rid = sheet.get(f"{R}id") or ""
        name = (sheet.get("name") or "")[:META_CHARS]
        path = rels.get(rid, ("", ""))[1]
        if not path or not pkg.has(path):
            continue
        sheets.append(_xlsx_sheet(pkg, path, name, shared, formats, date1904, budget))
    return {
        "type": "sheet",
        "sheets": sheets,
        "truncated": truncated or budget.truncated or any(s["truncated"] for s in sheets),
    }


def _shared_strings(pkg: Package, rels: dict[str, tuple[str, str]], budget: Budget) -> list[str]:
    path = next((p for t, p in rels.values() if t.endswith("/sharedStrings")), "")
    path = path or "xl/sharedStrings.xml"
    if not pkg.has(path):
        return []
    strings: list[str] = []
    state = {"si": None, "t": False, "skip": 0}

    def start(tag: str, _attrs: dict[str, str]) -> None:
        if tag == f"{S}rPh":
            state["skip"] += 1
        elif tag == f"{S}si":
            state["si"] = []
        elif tag == f"{S}t":
            state["t"] = True

    def end(tag: str) -> None:
        if tag == f"{S}rPh":
            state["skip"] -= 1
        elif tag == f"{S}t":
            state["t"] = False
        elif tag == f"{S}si":
            strings.append("".join(state["si"] or [])[:CELL_CHARS])
            state["si"] = None
            if len(strings) >= MAX_SHARED:
                raise Stop

    def text(data: str) -> None:
        if state["t"] and not state["skip"] and state["si"] is not None:
            state["si"].append(data)

    with pkg.open(path) as f:
        sax(f, start, end, text)
    return strings


def _xlsx_formats(pkg: Package, rels: dict[str, tuple[str, str]]) -> list[str | None]:
    """Per stijlindex (cellXfs) wat de getalnotatie toont: "date", "time", "datetime",
    "percent" of None."""
    path = next((p for t, p in rels.values() if t.endswith("/styles")), "") or "xl/styles.xml"
    root = pkg.xml(path) if pkg.has(path) else None
    if root is None:
        return []
    custom = {
        int(f.get("numFmtId") or -1): f.get("formatCode") or "" for f in root.iter(f"{S}numFmt")
    }
    result: list[str | None] = []
    xfs = root.find(f"{S}cellXfs")
    for xf in xfs.iter(f"{S}xf") if xfs is not None else []:
        try:
            fid = int(xf.get("numFmtId") or 0)
        except ValueError:
            fid = 0
        if fid in custom:
            code = custom[fid]
            kind = date_format(code) or ("percent" if "%" in code else None)
        elif fid in BUILTIN_TIMES:
            kind = "time" if fid != 22 else "datetime"
        elif fid == 22:
            kind = "datetime"
        elif fid in BUILTIN_DATES:
            kind = "date"
        elif fid in PERCENT:
            kind = "percent"
        else:
            kind = None
        result.append(kind)
    return result


def _xlsx_sheet(
    pkg: Package,
    path: str,
    name: str,
    shared: list[str],
    formats: list[str | None],
    date1904: bool,
    budget: Budget,
) -> dict[str, Any]:
    cells: dict[int, dict[int, str]] = {}
    state: dict[str, Any] = {"row": -1, "col": -1, "cell": None, "value": None, "in": None}
    result = {"truncated": False}

    def start(tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{S}row":
            r = attrs.get("r", "")
            state["row"] = int(r) - 1 if r.isdigit() else state["row"] + 1
            state["col"] = -1
            if state["row"] >= MAX_ROWS:
                result["truncated"] = True
                raise Stop
        elif tag == f"{S}c":
            m = CELL_REF.match(attrs.get("r", ""))
            state["col"] = column_index(m.group(1)) if m else state["col"] + 1
            state["cell"] = attrs
            state["value"] = []
        elif tag in (f"{S}v", f"{S}t") and state["cell"] is not None:
            state["in"] = tag
        elif tag == f"{S}rPh":
            state["in"] = None

    def end(tag: str) -> None:
        if tag in (f"{S}v", f"{S}t"):
            state["in"] = None
        elif tag == f"{S}c" and state["cell"] is not None:
            attrs, raw = state["cell"], "".join(state["value"])
            state["cell"] = None
            if state["col"] >= MAX_COLS:
                result["truncated"] = True
                return
            value = _xlsx_value(attrs, raw, shared, formats, date1904)
            if value:
                cells.setdefault(state["row"], {})[state["col"]] = budget.take(value[:CELL_CHARS])

    def text(data: str) -> None:
        if state["in"] is not None:
            state["value"].append(data)

    with pkg.open(path) as f:
        sax(f, start, end, text)
    last = max(cells) if cells else -1
    return {"name": name, "rows": _sheet_rows(cells, last), "truncated": result["truncated"]}


def _xlsx_value(
    attrs: dict[str, str], raw: str, shared: list[str], formats: list[str | None], date1904: bool
) -> str:
    kind = attrs.get("t", "n")
    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if kind in ("inlineStr", "str", "e", "d"):
        return raw
    if kind == "b":
        return "TRUE" if raw.strip() == "1" else "FALSE"
    if not raw:
        return ""
    try:
        number = float(raw)
    except ValueError:
        return raw
    try:
        style = formats[int(attrs.get("s", "0") or 0)]
    except (ValueError, IndexError):
        style = None
    if style in ("date", "time", "datetime"):
        return format_serial(number, style, date1904)
    if style == "percent":
        return format_number(round(number * 100, 10)) + "%"
    return format_number(number)


# --- pptx -----------------------------------------------------------------------------------


def read_pptx(pkg: Package, budget: Budget) -> dict[str, Any]:
    pres = pkg.xml("ppt/presentation.xml")
    if pres is None:
        raise Unreadable("ppt/presentation.xml ontbreekt")
    rels = pkg.rels("ppt/presentation.xml")
    size = None
    sz = pres.find(f"{P}sldSz")
    if sz is not None and (sz.get("cx") or "").isdigit() and (sz.get("cy") or "").isdigit():
        size = [round(int(sz.get("cx") or 0) / 12700, 1), round(int(sz.get("cy") or 0) / 12700, 1)]
    slides = []
    truncated = False
    for sld in pres.iter(f"{P}sldId"):
        if len(slides) >= MAX_SLIDES or budget.spent:
            truncated = True
            break
        path = rels.get(sld.get(f"{R}id") or "", ("", ""))[1]
        if path and pkg.has(path):
            slides.append(_pptx_slide(pkg, path, budget))
    return {
        "type": "slides",
        "size": size,
        "slides": slides,
        "truncated": truncated or budget.truncated,
    }


def _pptx_slide(pkg: Package, path: str, budget: Budget) -> dict[str, Any]:
    slide: dict[str, Any] = {"title": "", "bullets": []}
    state: dict[str, Any] = {"shape": None, "para": None, "level": 0, "t": False, "row": None}

    def start(tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{P}sp":
            state["shape"] = {"title": False}
        elif tag == f"{P}ph" and state["shape"] is not None:
            state["shape"]["title"] = attrs.get("type") in ("title", "ctrTitle")
        elif tag == f"{A}tr":
            state["row"] = []
        elif tag == f"{A}p":
            state["para"] = []
            state["level"] = 0
        elif tag == f"{A}pPr" and state["para"] is not None:
            lvl = attrs.get("lvl", "0")
            state["level"] = min(8, int(lvl)) if lvl.isdigit() else 0
        elif tag == f"{A}t":
            state["t"] = True
        elif tag == f"{A}br" and state["para"] is not None:
            state["para"].append("\n")

    def end(tag: str) -> None:
        if tag == f"{A}t":
            state["t"] = False
        elif tag == f"{A}p" and state["para"] is not None:
            text = budget.take("".join(state["para"]).strip())
            state["para"] = None
            if not text:
                return
            if state["row"] is not None:
                state["row"].append(text)
            elif state["shape"] is not None and state["shape"]["title"]:
                slide["title"] = (slide["title"] + " " + text).strip()[:META_CHARS]
            elif len(slide["bullets"]) < MAX_BULLETS:
                slide["bullets"].append({"text": text, "level": state["level"]})
        elif tag == f"{A}tr" and state["row"] is not None:
            row, state["row"] = state["row"], None
            if row and len(slide["bullets"]) < MAX_BULLETS:
                slide["bullets"].append({"text": " | ".join(row), "level": 0})
        elif tag == f"{P}sp":
            state["shape"] = None

    def text(data: str) -> None:
        if state["t"] and state["para"] is not None:
            state["para"].append(data)

    with pkg.open(path) as f:
        sax(f, start, end, text)
    return slide


# --- OpenDocument ---------------------------------------------------------------------------


def _repeat(attrs: dict[str, str], name: str, cap: int) -> int:
    raw = attrs.get(name, "1")
    return max(1, min(cap, int(raw))) if raw.isdigit() else 1


def _odf_styles(pkg: Package) -> tuple[dict[str, tuple[bool, bool]], dict[str, dict[int, bool]]]:
    """Automatische stijlen uit content.xml: tekststijl -> (vet, cursief), en lijststijl ->
    {niveau: genummerd}. Ze staan vóór de inhoud, dus het lezen stopt bij office:body."""
    text_styles: dict[str, tuple[bool, bool]] = {}
    lists: dict[str, dict[int, bool]] = {}
    state: dict[str, Any] = {"style": None, "list": None}

    def start(tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{OFFICE}body":
            raise Stop
        if tag == f"{STYLE}style":
            state["style"] = attrs.get(f"{STYLE}name")
        elif tag == f"{STYLE}text-properties" and state["style"]:
            bold = attrs.get(f"{FO}font-weight") in ("bold", "600", "700", "800", "900")
            italic = attrs.get(f"{FO}font-style") in ("italic", "oblique")
            text_styles[state["style"]] = (bold, italic)
        elif tag == f"{TEXT}list-style":
            state["list"] = attrs.get(f"{STYLE}name")
            lists[state["list"] or ""] = {}
        elif tag in (f"{TEXT}list-level-style-number", f"{TEXT}list-level-style-bullet"):
            level = attrs.get(f"{TEXT}level", "1")
            if state["list"] is not None and level.isdigit():
                lists[state["list"]][int(level) - 1] = tag.endswith("number")

    def end(tag: str) -> None:
        if tag == f"{STYLE}style":
            state["style"] = None
        elif tag == f"{TEXT}list-style":
            state["list"] = None

    with pkg.open("content.xml") as f:
        sax(f, start, end)
    return text_styles, lists


# Opmerkingen, voetnoten en bijgehouden wijzigingen horen niet bij de lopende tekst
SKIPPED = {f"{OFFICE}annotation", f"{TEXT}note", f"{TEXT}tracked-changes"}


class OdfText:
    """Tekst van text:p en text:h, met text:span (vet/cursief), text:s, text:tab en
    text:line-break. Gedeeld door odt, ods en odp."""

    def __init__(self, styles: dict[str, tuple[bool, bool]], budget: Budget):
        self.styles = styles
        self.budget = budget
        self.parts: list[tuple[str, bool, bool]] | None = None
        self.spans: list[tuple[bool, bool]] = []
        self.skip = 0

    def start(self, tag: str, attrs: dict[str, str]) -> bool:
        """True als de tag hier is afgehandeld."""
        if tag in SKIPPED:
            self.skip += 1
            return True
        if self.skip:
            return True
        if tag in (f"{TEXT}p", f"{TEXT}h"):
            self.parts = []
            self.spans = [self.styles.get(attrs.get(f"{TEXT}style-name", ""), (False, False))]
            return False
        if self.parts is None:
            return False
        props = self.spans[-1] if self.spans else (False, False)
        if tag == f"{TEXT}span":
            style = self.styles.get(attrs.get(f"{TEXT}style-name", ""))
            self.spans.append((props[0] or style[0], props[1] or style[1]) if style else props)
        elif tag == f"{TEXT}s":
            self.parts.append((" " * _repeat(attrs, f"{TEXT}c", 100), *props))
        elif tag == f"{TEXT}tab":
            self.parts.append(("\t", *props))
        elif tag == f"{TEXT}line-break":
            self.parts.append(("\n", *props))
        return True

    def end(self, tag: str) -> list[tuple[str, bool, bool]] | None:
        """Bij het eind van text:p of text:h: de stukken tekst."""
        if self.skip:
            if tag in SKIPPED:
                self.skip -= 1
            return None
        if tag == f"{TEXT}span" and len(self.spans) > 1:
            self.spans.pop()
        if tag in (f"{TEXT}p", f"{TEXT}h") and self.parts is not None:
            parts, self.parts = self.parts, None
            return parts
        return None

    def text(self, data: str) -> None:
        if self.parts is not None and not self.skip:
            props = self.spans[-1] if self.spans else (False, False)
            self.parts.append((self.budget.take(data), *props))


def read_odt(pkg: Package, budget: Budget) -> dict[str, Any]:
    styles, lists = _odf_styles(pkg)
    reader = OdfText(styles, budget)
    blocks: list[dict[str, Any]] = []
    state: dict[str, Any] = {"body": False, "lists": [], "heading": 0, "tables": [], "frame": 0}

    def emit(block: dict[str, Any]) -> None:
        if len(blocks) >= MAX_BLOCKS or budget.spent:
            budget.truncated = True
            raise Stop
        blocks.append(block)

    def start(tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{OFFICE}text":
            state["body"] = True
        if not state["body"]:
            return
        if tag == f"{DRAW}frame":
            state["frame"] += 1
        if state["frame"]:
            return
        if reader.start(tag, attrs):
            return
        if tag == f"{TEXT}h":
            level = attrs.get(f"{TEXT}outline-level", "1")
            state["heading"] = min(6, int(level)) if level.isdigit() and level != "0" else 1
        elif tag == f"{TEXT}list":
            name = attrs.get(f"{TEXT}style-name")
            outer = state["lists"][-1] if state["lists"] else None
            state["lists"].append(name or outer)
        elif tag == f"{TABLE}table":
            state["tables"].append({"rows": [], "cell": None})
        elif tag == f"{TABLE}table-row" and len(state["tables"]) == 1:
            table = state["tables"][0]
            for _ in range(_repeat(attrs, f"{TABLE}number-rows-repeated", 10)):
                if len(table["rows"]) < MAX_TABLE_ROWS:
                    table["rows"].append([])
            table["repeat_cols"] = 1
        elif (
            tag in (f"{TABLE}table-cell", f"{TABLE}covered-table-cell")
            and len(state["tables"]) == 1
        ):
            table = state["tables"][0]
            table["cell"] = []
            table["repeat_cols"] = _repeat(attrs, f"{TABLE}number-columns-repeated", MAX_COLS)

    def end(tag: str) -> None:
        if not state["body"]:
            return
        if tag == f"{DRAW}frame":
            state["frame"] -= 1
            return
        if state["frame"]:
            return
        parts = reader.end(tag)
        if parts is not None:
            text = _plain(parts)
            if state["tables"]:
                cell = state["tables"][0]["cell"]
                if cell is not None:
                    cell.append(text)
            elif tag == f"{TEXT}h":
                if text.strip():
                    emit({"type": "h", "level": state["heading"] or 1, "runs": _runs(parts)})
            elif state["lists"]:
                depth = len(state["lists"]) - 1
                ordered = lists.get(state["lists"][-1] or "", {}).get(depth, False)
                emit({"type": "li", "level": depth, "ordered": ordered, "runs": _runs(parts)})
            else:
                emit({"type": "p", "runs": _runs(parts)})
            return
        if tag == f"{TEXT}list" and state["lists"]:
            state["lists"].pop()
        elif (
            tag in (f"{TABLE}table-cell", f"{TABLE}covered-table-cell")
            and len(state["tables"]) == 1
        ):
            table = state["tables"][0]
            value = "\n".join(table["cell"] or [])[:CELL_CHARS]
            table["cell"] = None
            if table["rows"]:
                # Een herhaalde rij: alle kopieën krijgen de cel; lege cellen achteraan niet
                for row in table["rows"][-1:]:
                    for _ in range(table.get("repeat_cols", 1)):
                        if len(row) < MAX_COLS:
                            row.append(value)
        elif tag == f"{TABLE}table-row" and len(state["tables"]) == 1:
            rows = state["tables"][0]["rows"]
            if rows:
                while rows[-1] and not rows[-1][-1]:
                    rows[-1].pop()
        elif tag == f"{TABLE}table" and state["tables"]:
            table = state["tables"].pop()
            if not state["tables"]:
                rows = table["rows"]
                while rows and not rows[-1]:
                    rows.pop()
                emit({"type": "table", "rows": rows})
        elif tag == f"{OFFICE}text":
            raise Stop

    with pkg.open("content.xml") as f:
        sax(f, start, end, reader.text)
    return {"type": "document", "blocks": blocks, "truncated": budget.truncated}


def read_ods(pkg: Package, budget: Budget) -> dict[str, Any]:
    styles, _lists = _odf_styles(pkg)
    reader = OdfText(styles, budget)
    sheets: list[dict[str, Any]] = []
    state: dict[str, Any] = {"sheet": None, "row": -1, "rows_rep": 1, "col": 0, "cell": None}
    state["cols_rep"] = 1
    result = {"truncated": False}

    def start(tag: str, attrs: dict[str, str]) -> None:
        if reader.start(tag, attrs):
            return
        if tag == f"{TABLE}table":
            if len(sheets) >= MAX_SHEETS:
                result["truncated"] = True
                raise Stop
            state["sheet"] = {"name": attrs.get(f"{TABLE}name", "")[:META_CHARS], "cells": {}}
            state["sheet"]["truncated"] = False
            state["row"] = -1
        elif state["sheet"] is None:
            return
        elif tag == f"{TABLE}table-row":
            state["row"] += state["rows_rep"] if state["row"] >= 0 else 1
            if state["row"] < 0:
                state["row"] = 0
            state["rows_rep"] = _repeat(attrs, f"{TABLE}number-rows-repeated", MAX_ROWS + 1)
            state["col"] = 0
        elif tag in (f"{TABLE}table-cell", f"{TABLE}covered-table-cell"):
            state["cell"] = []
            state["cols_rep"] = _repeat(attrs, f"{TABLE}number-columns-repeated", MAX_COLS + 1)

    def end(tag: str) -> None:
        parts = reader.end(tag)
        sheet = state["sheet"]
        if parts is not None:
            if state["cell"] is not None:
                state["cell"].append(_plain(parts))
            return
        if sheet is None:
            return
        if tag in (f"{TABLE}table-cell", f"{TABLE}covered-table-cell"):
            value = "\n".join(state["cell"] or [])[:CELL_CHARS]
            state["cell"] = None
            if value:
                for dr in range(state["rows_rep"]):
                    r = state["row"] + dr
                    if r >= MAX_ROWS:
                        sheet["truncated"] = True
                        break
                    for dc in range(state["cols_rep"]):
                        c = state["col"] + dc
                        if c >= MAX_COLS:
                            sheet["truncated"] = True
                            break
                        sheet["cells"].setdefault(r, {})[c] = value
            state["col"] += state["cols_rep"]
        elif tag == f"{TABLE}table":
            cells = sheet["cells"]
            last = max(cells) if cells else -1
            sheets.append(
                {
                    "name": sheet["name"],
                    "rows": _sheet_rows(cells, last),
                    "truncated": sheet["truncated"],
                }
            )
            state["sheet"] = None
            state["rows_rep"] = 1

    with pkg.open("content.xml") as f:
        sax(f, start, end, reader.text)
    return {
        "type": "sheet",
        "sheets": sheets,
        "truncated": result["truncated"] or budget.truncated or any(s["truncated"] for s in sheets),
    }


ODF_LENGTH = re.compile(r"^([\d.]+)(cm|mm|in|pt|pc|px)$")
TO_PT = {"cm": 72 / 2.54, "mm": 72 / 25.4, "in": 72.0, "pt": 1.0, "pc": 12.0, "px": 0.75}


def _odf_length(value: str | None) -> float | None:
    m = ODF_LENGTH.match((value or "").strip())
    if not m:
        return None
    try:
        return round(float(m.group(1)) * TO_PT[m.group(2)], 1)
    except ValueError:
        return None


def _odp_size(pkg: Package) -> list[float] | None:
    if not pkg.has("styles.xml"):
        return None
    found: list[float] = []

    def start(tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{STYLE}page-layout-properties":
            w = _odf_length(attrs.get(f"{FO}page-width"))
            h = _odf_length(attrs.get(f"{FO}page-height"))
            if w and h:
                found.extend([w, h])
                raise Stop

    with pkg.open("styles.xml", SMALL_BYTES) as f:
        sax(f, start)
    return found or None


def read_odp(pkg: Package, budget: Budget) -> dict[str, Any]:
    styles, _lists = _odf_styles(pkg)
    reader = OdfText(styles, budget)
    slides: list[dict[str, Any]] = []
    state: dict[str, Any] = {"slide": None, "frame": None, "lists": 0, "notes": 0}
    result = {"truncated": False}

    def start(tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{PRES}notes":
            state["notes"] += 1
        if state["notes"] or reader.start(tag, attrs):
            return
        if tag == f"{DRAW}page":
            if len(slides) >= MAX_SLIDES or budget.spent:
                result["truncated"] = True
                raise Stop
            state["slide"] = {"title": "", "bullets": []}
        elif tag == f"{DRAW}frame" and state["slide"] is not None:
            state["frame"] = attrs.get(f"{PRES}class", "")
        elif tag == f"{TEXT}list":
            state["lists"] += 1

    def end(tag: str) -> None:
        if tag == f"{PRES}notes":
            state["notes"] -= 1
            return
        if state["notes"]:
            return
        parts = reader.end(tag)
        slide = state["slide"]
        if parts is not None:
            text = _plain(parts).strip()
            if not text or slide is None:
                return
            if state["frame"] == "title":
                slide["title"] = (slide["title"] + " " + text).strip()[:META_CHARS]
            elif len(slide["bullets"]) < MAX_BULLETS:
                slide["bullets"].append({"text": text, "level": max(0, state["lists"] - 1)})
            return
        if tag == f"{TEXT}list":
            state["lists"] -= 1
        elif tag == f"{DRAW}frame":
            state["frame"] = None
        elif tag == f"{DRAW}page" and slide is not None:
            slides.append(slide)
            state["slide"] = None

    with pkg.open("content.xml") as f:
        sax(f, start, end, reader.text)
    return {
        "type": "slides",
        "size": _odp_size(pkg),
        "slides": slides,
        "truncated": result["truncated"] or budget.truncated,
    }


# --- metadata en thumbnail ------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    value = (value or "").replace("\x00", "").strip()
    return value[:META_CHARS] or None


def ooxml_meta(pkg: Package) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    core = pkg.xml("docProps/core.xml")
    if core is not None:
        title = core.find(f"{DC}title")
        creator = core.find(f"{DC}creator")
        if title is not None and _clean(title.text):
            meta["title"] = _clean(title.text)
        if creator is not None and _clean(creator.text):
            meta["author"] = _clean(creator.text)
    app = pkg.xml("docProps/app.xml")
    if app is not None:
        for tag in ("Pages", "Slides"):
            el = app.find(f"{EP}{tag}")
            if el is not None and (el.text or "").strip().isdigit():
                meta["pages"] = int((el.text or "0").strip())
                break
    return meta


def odf_meta(pkg: Package) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    root = pkg.xml("meta.xml")
    if root is None:
        return meta
    title = root.find(f".//{DC}title")
    creator = root.find(f".//{META}initial-creator")
    if creator is None:
        creator = root.find(f".//{DC}creator")
    if title is not None and _clean(title.text):
        meta["title"] = _clean(title.text)
    if creator is not None and _clean(creator.text):
        meta["author"] = _clean(creator.text)
    stats = root.find(f".//{META}document-statistic")
    if stats is not None and (stats.get(f"{META}page-count") or "").isdigit():
        meta["pages"] = int(stats.get(f"{META}page-count") or 0)
    return meta


def is_picture(head: bytes) -> bool:
    """Alleen gewone afbeeldingen als thumbnail; WMF en EMF (vaak in Office) niet."""
    return head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")) or (
        head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    )


def thumbnail(pkg: Package, kind: str) -> bytes | None:
    candidates = []
    if kind in ("odt", "ods", "odp"):
        candidates.append("Thumbnails/thumbnail.png")
    else:
        for rel_type, path in pkg.rels("").values():
            if rel_type.endswith(THUMBNAIL_REL):
                candidates.append(path)
        candidates.append("docProps/thumbnail.jpeg")
    for name in candidates:
        if not pkg.has(name):
            continue
        try:
            data = pkg.read(name, THUMB_BYTES)
        except Unreadable:
            continue
        if is_picture(data[:16]):
            return data
    return None


# --- alles samen ----------------------------------------------------------------------------


def detect(pkg: Package) -> str:
    """docx, xlsx, pptx, odt, ods of odp."""
    if pkg.has("mimetype"):
        mimetype = pkg.read("mimetype", 200).decode("ascii", "replace").strip()
        if mimetype in ODF_KINDS:
            return ODF_KINDS[mimetype]
    if pkg.has("word/document.xml"):
        return "docx"
    if pkg.has("xl/workbook.xml"):
        return "xlsx"
    if pkg.has("ppt/presentation.xml"):
        return "pptx"
    raise Unreadable("geen bekend kantoordocument")


READERS = {
    "docx": read_docx,
    "xlsx": read_xlsx,
    "pptx": read_pptx,
    "odt": read_odt,
    "ods": read_ods,
    "odp": read_odp,
}


def _too_slow(_signum, _frame):
    raise TooSlow


def fit(doc: dict[str, Any], limit: int = DOC_BYTES) -> bytes:
    """doc als JSON van hooguit limit bytes: anders van achteren af inkorten."""
    data = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
    if len(data) <= limit:
        return data
    key = {"document": "blocks", "sheet": "sheets", "slides": "slides"}[doc["type"]]
    items = list(doc[key])
    while items:
        # Grof halveren tot het past, dan per stuk erbij
        cut = max(1, len(items) // 2)
        trial = {**doc, key: items[:-cut], "truncated": True}
        data = json.dumps(trial, ensure_ascii=False, separators=(",", ":")).encode()
        items = items[:-cut]
        if len(data) <= limit:
            return data
    if doc["type"] == "sheet" and doc["sheets"]:
        # Eén enorm werkblad: rijen inkorten
        sheet = doc["sheets"][0]
        rows = list(sheet["rows"])
        while rows:
            rows = rows[: len(rows) // 2]
            trial = {**doc, "sheets": [{**sheet, "rows": rows, "truncated": True}]}
            trial["truncated"] = True
            data = json.dumps(trial, ensure_ascii=False, separators=(",", ":")).encode()
            if len(data) <= limit:
                return data
    empty = {**doc, key: [], "truncated": True}
    return json.dumps(empty, ensure_ascii=False, separators=(",", ":")).encode()


def read(source: Path, seconds: float = READ_SECONDS) -> dict[str, Any]:
    """Leest een kantoordocument; zie de uitleg bovenaan. Unreadable als het geen (leesbaar)
    kantoordocument is."""
    previous = signal.signal(signal.SIGALRM, _too_slow)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        with zipfile.ZipFile(source) as archive:
            pkg = Package(archive)
            kind = detect(pkg)
            budget = Budget()
            try:
                doc = READERS[kind](pkg, budget)
            except TooSlow:
                raise Unreadable("het lezen duurde te lang") from None
            signal.setitimer(signal.ITIMER_REAL, 0)
            meta = odf_meta(pkg) if kind in ("odt", "ods", "odp") else ooxml_meta(pkg)
            if doc["type"] == "slides":
                meta["pages"] = len(doc["slides"]) if not doc["truncated"] else meta.get("pages")
                if meta["pages"] is None:
                    del meta["pages"]
            meta["office"] = doc["type"]
            meta["format"] = kind
            return {"doc": doc, "meta": meta, "thumbnail": thumbnail(pkg, kind)}
    except (FileNotFoundError, PermissionError, MemoryError):
        raise  # weg of geen geheugen: dat kan een volgende keer wel lukken
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        KeyError,
        EOFError,
        ValueError,
        NotImplementedError,  # een onbekende compressie
        zlib.error,
        OSError,
    ) as e:
        raise Unreadable(str(e)) from e
    except TooSlow:
        raise Unreadable("het lezen duurde te lang") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def doc_type(kind: str) -> str:
    return DOC_TYPES[kind]
