"""Proefbestanden voor de metingen van office deel 2 (fase 4, §4.7): groot, met echte opmaak.

Gedeeld door office_xml.py, office_convert.py en office_crowd.py. Alles wordt hier gemaakt,
met alleen de standaardbibliotheek (zipfile), en is per seed anders (een nieuwe sha, dus een
nieuwe key bij DS): DS bewaart een conversie onder zijn key, en dezelfde inhoud opnieuw
omzetten zou dan niets meten.

Wat er is (make(kind, seed, budget)):
- docx: een rapport met echte opmaak: genummerde koppen (Kop 1 en 2), alinea's met vet,
  cursief, onderstreept, kleur, markering en een andere grootte, opsommingen en genummerde
  lijsten (numbering.xml), tabellen met randen, een gekleurde kopregel en rechts uitgelijnde
  getallen, een kop- en voettekst met het paginanummer (een veld);
- docx_pages: een docx met alleen pagina-einden, één alinea per pagina (zoals pb4000k.docx van
  de spike): het zwaarste bestand per MB XML dat bekend is (±97 % van 2 GB bij 3,9 MB);
- pptx: dia's op de master, de layouts en het thema van e2e/fixtures/office/proef.pptx
  (LibreOffice), elk met een titel, een tekstvak met opsommingstekens en gemengde opmaak, een
  vorm met vulling en rand, en een tabel van 4 × 4;
- xlsx: een werkboek met drie bladen: Gegevens (een titel over samengevoegde cellen, een kopregel
  met opmaak, vastgezette rijen, een filter, kolombreedtes, voorwaardelijke opmaak, getallen als
  bedrag, datum en percentage, tekst uit sharedStrings, en per rij formules: product, BTW met
  ROUND, een lopend totaal, IF en VLOOKUP naar het blad Tarieven), Tarieven (de opzoektabel) en
  Overzicht (SUMIF en COUNTIF per categorie over de hele kolom). Met de uitkomsten als <v>,
  zoals een opgeslagen werkboek;
- odt, ods, odp: hetzelfde in ODF: koppen, opmaak in spans, lijsten, tabellen met randen en
  achtergrond; een rekenblad met getalnotaties en formules (of:=…); dia's met een titel,
  een lijst en een gekleurde vorm, op een master met een voettekst.

budget is het aantal bytes XML (zoals inspect.py telt: elk onderdeel op .xml of .rels, en elk
ander onderdeel dat met < begint); standaard net onder de grens van de invoercontrole voor die
soort (limit()). xml_size() telt na.

Verder: docx_401() (de docx van 401 pagina's uit de proef, groot.docx: veel dezelfde alinea's,
±2,6 MB XML in ±40 KB) en small(name, seed) (een fixture met een zip-commentaar als seed).
"""

from __future__ import annotations

import io
import os
import random
import re
import time
import zipfile
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "office"

XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
PKG = "http://schemas.openxmlformats.org/package/2006"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DOC = "application/vnd.openxmlformats-officedocument"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"

KINDS = ("docx", "docx_pages", "pptx", "xlsx", "odt", "ods", "odp")
EXT = {"docx_pages": "docx"}
MARGIN = 16 * 1024  # zo ver onder de grens blijft het budget standaard

WORDS = (
    "het de een van en in op met voor aan bij naar uit over door tot onder tussen zonder na "
    "rapport overzicht begroting jaarverslag vergadering besluit afdeling project planning "
    "klant leverancier factuur bestelling levering voorraad magazijn kwartaal maand week dag "
    "omzet kosten winst verlies budget investering afschrijving rente lening subsidie belasting "
    "gemeente provincie bewoners vereniging bestuur leden kascommissie notulen agenda punt "
    "voorstel advies onderzoek resultaat conclusie aanbeveling risico kans maatregel actie "
    "verantwoordelijk eigenaar datum termijn status gereed lopend vertraagd geannuleerd "
    "server netwerk opslag back-up herstel beveiliging wachtwoord sleutel certificaat toegang "
    "document bestand map versie wijziging opmerking reactie goedkeuring handtekening archief "
    "groot klein nieuw oud snel langzaam belangrijk dringend later eerst daarna tenslotte "
    "verder bovendien echter daarom omdat terwijl hoewel zodat wanneer indien mits tenzij"
).split()
COLORS = ("1f4e79", "c00000", "548235", "7030a0", "bf8f00", "2e75b6", "843c0c", "000000")
HIGHLIGHTS = ("yellow", "green", "cyan", "lightGray")
CATEGORIES = ("Kantoor", "Hardware", "Diensten", "Reizen", "Opleiding", "Software")


def limit(kind: str) -> int:
    """De grens van de invoercontrole (bytes XML) voor deze soort."""
    from app.office import inspect

    ext = EXT.get(kind, kind)
    if ext in inspect.ODF:
        return inspect.XML_BYTES_ODF
    if ext in inspect.SHEETS:
        return inspect.XML_BYTES_SHEET
    return inspect.XML_BYTES


def ext_of(kind: str) -> str:
    return EXT.get(kind, kind)


_LEADING = b" \t\r\n\x00\xef\xbb\xbf\xfe\xff"


def xml_size(data: bytes) -> int:
    """De bytes XML zoals inspect.py ze telt: .xml, .rels, en wat (na witruimte en een BOM)
    met < begint."""
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = info.filename.lower()
            if name.endswith((".xml", ".rels")):
                total += info.file_size
                continue
            with z.open(info) as f:
                head = f.read(64).lstrip(_LEADING)
            if head.startswith(b"<"):
                total += info.file_size
    return total


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _words(rnd: random.Random, n: int) -> str:
    return " ".join(rnd.choice(WORDS) for _ in range(n))


def _sentence(rnd: random.Random, lo: int = 6, hi: int = 16) -> str:
    text = _words(rnd, rnd.randint(lo, hi))
    return text[:1].upper() + text[1:] + "."


def _zip(parts: list[tuple[str, str | bytes]], *, stored: tuple[str, ...] = ()) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts:
            data = body.encode() if isinstance(body, str) else body
            kind = zipfile.ZIP_STORED if name in stored else zipfile.ZIP_DEFLATED
            z.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), data, compress_type=kind)
    return out.getvalue()


def _rels(*targets: tuple[str, str], kinds: str = REL) -> str:
    body = "".join(
        f'<Relationship Id="rId{n}" Type="{kinds}/{kind}" Target="{target}"/>'
        for n, (kind, target) in enumerate(targets, start=1)
    )
    return XML + f'<Relationships xmlns="{PKG}/relationships">{body}</Relationships>'


def _root_rels(main: str) -> str:
    return (
        XML + f'<Relationships xmlns="{PKG}/relationships">'
        f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="{main}"/>'
        f'<Relationship Id="rId2" Type="{PKG}/relationships/metadata/core-properties"'
        ' Target="docProps/core.xml"/>'
        f'<Relationship Id="rId3" Type="{REL}/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def _core(title: str) -> str:
    return (
        XML + f'<cp:coreProperties xmlns:cp="{PKG}/metadata/core-properties"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{_esc(title)}</dc:title><dc:creator>Meting fase 4</dc:creator>"
        '<dcterms:created xsi:type="dcterms:W3CDTF">2026-01-01T00:00:00Z</dcterms:created>'
        "</cp:coreProperties>"
    )


APP_XML = (
    XML + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
    'extended-properties"><Application>Meting fase 4</Application></Properties>'
)


def _types(*overrides: tuple[str, str], extra: str = "") -> str:
    parts = [
        f'<Types xmlns="{PKG}/content-types">',
        f'<Default Extension="rels" ContentType="{PKG[:-5]}relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        extra,
        f'<Override PartName="/docProps/core.xml" ContentType="{PKG[:-5]}core-properties+xml"/>',
        f'<Override PartName="/docProps/app.xml" ContentType="{DOC}.extended-properties+xml"/>',
    ]
    parts += [f'<Override PartName="{name}" ContentType="{kind}"/>' for name, kind in overrides]
    return XML + "".join(parts) + "</Types>"


def _fill(budget: int, fixed: int, block, tail_block=None) -> list[str]:
    """Blokken tot het budget (bytes) op is; daarna kleinere blokken (tail_block) tot het
    zo goed als vol is. block en tail_block geven een str (UTF-8 telt)."""
    parts: list[str] = []
    size = fixed
    while True:
        b = block()
        n = len(b.encode())
        if size + n > budget:
            break
        parts.append(b)
        size += n
    if tail_block is not None:
        misses = 0
        while misses < 20:
            b = tail_block()
            n = len(b.encode())
            if size + n > budget:
                misses += 1
                continue
            parts.append(b)
            size += n
    return parts


# --- docx ---------------------------------------------------------------------------------

DOCX_STYLES = (
    XML + f'<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="nl-NL"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="259" w:lineRule="auto"/></w:pPr>'
    "</w:pPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
    "<w:qFormat/></w:style>"
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
    '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr>'
    '<w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:color w:val="1F4E79"/>'
    '<w:sz w:val="48"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
    '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/>'
    '<w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>'
    '<w:rPr><w:b/><w:color w:val="2E75B6"/><w:sz w:val="32"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
    '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/>'
    '<w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr>'
    '<w:rPr><w:b/><w:color w:val="2E75B6"/><w:sz w:val="26"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="720"/><w:contextualSpacing/></w:pPr>'
    "</w:style>"
    '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="864" w:right="864"/><w:jc w:val="center"/>'
    '</w:pPr><w:rPr><w:i/><w:color w:val="404040"/></w:rPr></w:style>'
    '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont">'
    '<w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/></w:style>'
    '<w:style w:type="character" w:styleId="Strong"><w:name w:val="Strong"/>'
    "<w:rPr><w:b/></w:rPr></w:style>"
    '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>'
    '<w:tblPr><w:tblCellMar><w:left w:w="108" w:type="dxa"/><w:right w:w="108" w:type="dxa"/>'
    "</w:tblCellMar></w:tblPr></w:style>"
    '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/>'
    '<w:basedOn w:val="TableNormal"/><w:pPr><w:spacing w:after="0" w:line="240"'
    ' w:lineRule="auto"/></w:pPr><w:tblPr><w:tblBorders>'
    '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    "</w:tblBorders></w:tblPr></w:style></w:styles>"
)


def _lvl(ilvl: int, fmt: str, text: str, left: int) -> str:
    font = '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr>' if fmt == "bullet" else ""
    return (
        f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
        f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/><w:pPr>'
        f'<w:ind w:left="{left}" w:hanging="360"/></w:pPr>{font}</w:lvl>'
    )


DOCX_NUMBERING = (
    XML + f'<w:numbering xmlns:w="{W}">'
    '<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>'
    + _lvl(0, "bullet", "", 720)
    + _lvl(1, "bullet", "o", 1440)
    + "</w:abstractNum>"
    '<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>'
    + _lvl(0, "decimal", "%1.", 720)
    + _lvl(1, "lowerLetter", "%2.", 1440)
    + "</w:abstractNum>"
    '<w:abstractNum w:abstractNumId="2"><w:multiLevelType w:val="multilevel"/>'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:pStyle'
    ' w:val="Heading1"/><w:lvlText w:val="%1"/><w:lvlJc w:val="left"/><w:pPr>'
    '<w:ind w:left="432" w:hanging="432"/></w:pPr></w:lvl>'
    '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:pStyle'
    ' w:val="Heading2"/><w:lvlText w:val="%1.%2"/><w:lvlJc w:val="left"/><w:pPr>'
    '<w:ind w:left="576" w:hanging="576"/></w:pPr></w:lvl></w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
    '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
    '<w:num w:numId="3"><w:abstractNumId w:val="2"/></w:num></w:numbering>'
)
DOCX_SETTINGS = (
    XML + f'<w:settings xmlns:w="{W}"><w:defaultTabStop w:val="708"/>'
    '<w:compat><w:compatSetting w:name="compatibilityMode"'
    ' w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>'
)
DOCX_HEADER = (
    XML + f'<w:hdr xmlns:w="{W}"><w:p><w:pPr><w:jc w:val="right"/></w:pPr><w:r><w:rPr>'
    '<w:color w:val="808080"/><w:sz w:val="18"/></w:rPr><w:t>Meting fase 4: de XML-grens</w:t>'
    "</w:r></w:p></w:hdr>"
)
DOCX_FOOTER = (
    XML + f'<w:ftr xmlns:w="{W}"><w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
    '<w:r><w:t xml:space="preserve">Pagina </w:t></w:r><w:fldSimple w:instr=" PAGE ">'
    "<w:r><w:t>1</w:t></w:r></w:fldSimple></w:p></w:ftr>"
)
DOCX_SECT = (
    '<w:sectPr><w:headerReference w:type="default" r:id="rId4"/>'
    '<w:footerReference w:type="default" r:id="rId5"/>'
    '<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440"'
    ' w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
)


def _run(text: str, props: str = "") -> str:
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{_esc(text)}</w:t></w:r>'


def _rich(rnd: random.Random, sentences: int) -> str:
    """Runs met gemengde opmaak."""
    runs = []
    for _ in range(sentences):
        pick = rnd.randrange(9)
        text = _sentence(rnd) + " "
        if pick == 0:
            runs.append(_run(text, "<w:b/>"))
        elif pick == 1:
            runs.append(_run(text, "<w:i/>"))
        elif pick == 2:
            runs.append(_run(text, '<w:u w:val="single"/>'))
        elif pick == 3:
            runs.append(_run(text, f'<w:color w:val="{rnd.choice(COLORS)}"/>'))
        elif pick == 4:
            runs.append(_run(text, f'<w:highlight w:val="{rnd.choice(HIGHLIGHTS)}"/>'))
        elif pick == 5:
            runs.append(_run(text, '<w:b/><w:i/><w:sz w:val="24"/>'))
        else:
            runs.append(_run(text))
    return "".join(runs)


def _para(body: str, style: str = "", extra: str = "") -> str:
    ppr = f'<w:pStyle w:val="{style}"/>' if style else ""
    ppr += extra
    return f"<w:p>{f'<w:pPr>{ppr}</w:pPr>' if ppr else ''}{body}</w:p>"


def _list_item(rnd: random.Random, num: int) -> str:
    return _para(
        _run(_words(rnd, 2).capitalize() + ": ", "<w:b/>") + _run(_sentence(rnd, 5, 12)),
        "ListParagraph",
        f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num}"/></w:numPr>',
    )


def _cell(text: str, width: int, *, head: bool = False, right: bool = False) -> str:
    shade = '<w:shd w:val="clear" w:color="auto" w:fill="1F4E79"/>' if head else ""
    jc = '<w:jc w:val="right"/>' if right else ""
    props = '<w:b/><w:color w:val="FFFFFF"/>' if head else ""
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}</w:tcPr>'
        f"<w:p><w:pPr>{jc}</w:pPr>{_run(text, props)}</w:p></w:tc>"
    )


def _table(rnd: random.Random, rows: int = 6) -> str:
    widths = (3000, 2000, 2000, 2026)
    head = ("Omschrijving", "Aantal", "Prijs", "Totaal")
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    out = [
        '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/>'
        f'<w:tblLook w:val="04A0"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>',
        "<w:tr><w:trPr><w:tblHeader/></w:trPr>"
        + "".join(_cell(h, w, head=True) for h, w in zip(head, widths, strict=True))
        + "</w:tr>",
    ]
    for _ in range(rows):
        n, price = rnd.randint(1, 250), rnd.uniform(1, 900)
        cells = (
            (_words(rnd, 3).capitalize(), False),
            (str(n), True),
            (f"€ {price:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), True),
            (
                f"€ {n * price:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                True,
            ),
        )
        out.append(
            "<w:tr>"
            + "".join(_cell(t, w, right=r) for (t, r), w in zip(cells, widths, strict=True))
            + "</w:tr>"
        )
    out.append("</w:tbl>")
    return "".join(out)


def _docx(body: str, title: str, comment: str = "") -> bytes:
    document = (
        XML + f'<w:document xmlns:w="{W}" xmlns:r="{REL}"><w:body>{body}{DOCX_SECT}'
        "</w:body></w:document>"
    )
    parts = [
        (
            "[Content_Types].xml",
            _types(
                ("/word/document.xml", f"{DOC}.wordprocessingml.document.main+xml"),
                ("/word/styles.xml", f"{DOC}.wordprocessingml.styles+xml"),
                ("/word/numbering.xml", f"{DOC}.wordprocessingml.numbering+xml"),
                ("/word/settings.xml", f"{DOC}.wordprocessingml.settings+xml"),
                ("/word/header1.xml", f"{DOC}.wordprocessingml.header+xml"),
                ("/word/footer1.xml", f"{DOC}.wordprocessingml.footer+xml"),
            ),
        ),
        ("_rels/.rels", _root_rels("word/document.xml")),
        ("docProps/core.xml", _core(title)),
        ("docProps/app.xml", APP_XML),
        ("word/document.xml", document),
        (
            "word/_rels/document.xml.rels",
            _rels(
                ("styles", "styles.xml"),
                ("numbering", "numbering.xml"),
                ("settings", "settings.xml"),
                ("header", "header1.xml"),
                ("footer", "footer1.xml"),
            ),
        ),
        ("word/styles.xml", DOCX_STYLES),
        ("word/numbering.xml", DOCX_NUMBERING),
        ("word/settings.xml", DOCX_SETTINGS),
        ("word/header1.xml", DOCX_HEADER),
        ("word/footer1.xml", DOCX_FOOTER),
    ]
    data = _zip(parts)
    return _with_comment(data, comment) if comment else data


def _docx_fixed(title: str) -> int:
    """De bytes XML van een docx zonder inhoud (alles behalve de blokken)."""
    return xml_size(_docx("", title))


def make_docx(seed: int, budget: int) -> tuple[bytes, dict[str, Any]]:
    rnd = random.Random(seed)
    title = f"Jaarverslag {seed}"
    counter = {"h": 0, "tables": 0}

    def block() -> str:
        counter["h"] += 1
        counter["tables"] += 1
        heading = _para(
            _run(f"Hoofdstuk {counter['h']}: {_words(rnd, 3)}"),
            "Heading1",
            '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr>',
        )
        paras = "".join(_para(_rich(rnd, rnd.randint(4, 7))) for _ in range(3))
        bullets = "".join(_list_item(rnd, 1) for _ in range(4))
        sub = _para(
            _run(_words(rnd, 4).capitalize()),
            "Heading2",
            '<w:numPr><w:ilvl w:val="1"/><w:numId w:val="3"/></w:numPr>',
        )
        more = "".join(_para(_rich(rnd, rnd.randint(3, 6))) for _ in range(2))
        numbered = "".join(_list_item(rnd, 2) for _ in range(3))
        quote = _para(_run(_sentence(rnd, 10, 18)), "Quote")
        return heading + paras + bullets + _table(rnd) + sub + more + numbered + quote

    def tail() -> str:
        return _para(_rich(rnd, 1))

    head = _para(_run(f"{title}: meting {seed}"), "Title")
    fixed = _docx_fixed(title) + len(head.encode())
    blocks = _fill(budget, fixed, block, tail)
    data = _docx(head + "".join(blocks), title)
    return data, {"chapters": counter["h"], "tables": counter["tables"]}


def make_docx_pages(
    seed: int, budget: int, pages: int | None = None
) -> tuple[bytes, dict[str, Any]]:
    """Alleen pagina-einden: één alinea per pagina, zoals pb4000k.docx van de spike. Met pages
    precies zoveel pagina's (dan telt budget niet): voor het zoeken van de grens van x2t."""
    title = f"Pagina's {seed}"
    head = _para(_run(f"Heel veel pagina's, meting {seed}"), "Title")
    brk = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
    fixed = _docx_fixed(title) + len(head.encode())
    n = max(0, (budget - fixed) // len(brk)) if pages is None else max(0, pages - 1)
    data = _docx(head + brk * n, title)
    return data, {"pages": n + 1}


def docx_401(seed: int, paragraphs: int = 19_240) -> tuple[bytes, dict[str, Any]]:
    """Zoals groot.docx van de proef (stap 16 en 18 van de spike): heel veel korte alinea's
    van één regel, ±2,6 MB XML, 401 pagina's A4 (paragraphs is daarop afgesteld met DS
    10.0.0.1)."""
    title = f"Groot {seed}"
    lines = "".join(
        '<w:p><w:pPr><w:spacing w:after="0"/></w:pPr><w:r><w:t>'
        f"Regel {n}: een korte regel tekst van het grote proefdocument.</w:t></w:r></w:p>"
        for n in range(1, paragraphs + 1)
    )
    head = _para(_run(f"Groot document, meting {seed}"), "Title")
    data = _docx(head + lines, title)
    return data, {"paragraphs": paragraphs, "xml_bytes": xml_size(data), "file_bytes": len(data)}


# --- pptx ---------------------------------------------------------------------------------


def _ppt_run(text: str, rnd: random.Random | None = None, *, size: int = 2000) -> str:
    attrs, fill = f'lang="nl-NL" sz="{size}"', "000000"
    if rnd is not None:
        pick = rnd.randrange(7)
        if pick == 0:
            attrs += ' b="1"'
        elif pick == 1:
            attrs += ' i="1"'
        elif pick == 2:
            attrs += ' u="sng"'
        elif pick == 3:
            fill = rnd.choice(COLORS)
        elif pick == 4:
            attrs += ' b="1" i="1"'
    return (
        f'<a:r><a:rPr {attrs} dirty="0"><a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>'
        f'<a:latin typeface="Arial"/></a:rPr><a:t>{_esc(text)}</a:t></a:r>'
    )


def _ppt_box(
    sid: int, name: str, x: int, y: int, cx: int, cy: int, paras: str, fill: str = ""
) -> str:
    sp_fill = (
        f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill><a:ln w="12700"><a:solidFill>'
        '<a:srgbClr val="1F4E79"/></a:solidFill></a:ln>'
        if fill
        else "<a:noFill/>"
    )
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/><p:cNvSpPr txBox="1"/><p:nvPr/>'
        f'</p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/>'
        f'</a:xfrm><a:prstGeom prst="{"roundRect" if fill else "rect"}"><a:avLst/></a:prstGeom>'
        f'{sp_fill}</p:spPr><p:txBody><a:bodyPr wrap="square" lIns="91440" rIns="91440"'
        ' tIns="45720" bIns="45720"><a:normAutofit/></a:bodyPr><a:lstStyle/>'
        f"{paras}</p:txBody></p:sp>"
    )


def _ppt_table(rnd: random.Random, sid: int) -> str:
    cols = (2000000, 1400000, 1400000, 1400000)
    grid = "".join(f'<a:gridCol w="{w}"/>' for w in cols)
    rows = []
    for r in range(4):
        cells = []
        for c in range(4):
            text = (
                ("Onderdeel", "Q1", "Q2", "Q3")[c]
                if r == 0
                else (_words(rnd, 1).capitalize() if c == 0 else str(rnd.randint(10, 9999)))
            )
            fill = "1F4E79" if r == 0 else ("DDEBF7" if r % 2 else "FFFFFF")
            color = "FFFFFF" if r == 0 else "000000"
            cells.append(
                f'<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="nl-NL"'
                f' sz="1400"{' b="1"' if r == 0 else ""} dirty="0"><a:solidFill><a:srgbClr'
                f' val="{color}"/></a:solidFill></a:rPr><a:t>{_esc(text)}</a:t></a:r></a:p>'
                f'</a:txBody><a:tcPr><a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>'
                "</a:tcPr></a:tc>"
            )
        rows.append(f'<a:tr h="370840">{"".join(cells)}</a:tr>')
    return (
        f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{sid}" name="Tabel {sid}"/>'
        '<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/>'
        '</p:nvGraphicFramePr><p:xfrm><a:off x="504000" y="3500000"/><a:ext cx="6200000"'
        f' cy="1483360"/></p:xfrm><a:graphic><a:graphicData uri="{A}/table"><a:tbl>'
        f'<a:tblPr firstRow="1" bandRow="1"/><a:tblGrid>{grid}</a:tblGrid>{"".join(rows)}'
        "</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )


def _slide(rnd: random.Random, n: int) -> str:
    title = f'<a:p><a:pPr algn="l"/>{_ppt_run(f"Dia {n}: {_words(rnd, 3)}", size=3200)}</a:p>'
    bullets = "".join(
        '<a:p><a:pPr marL="342900" indent="-342900"><a:buFont typeface="Arial"/>'
        '<a:buChar char="&#8226;"/></a:pPr>'
        + "".join(_ppt_run(_sentence(rnd, 3, 8) + " ", rnd, size=1800) for _ in range(2))
        + "</a:p>"
        for _ in range(5)
    )
    note = f'<a:p><a:pPr algn="ctr"/>{_ppt_run(_sentence(rnd, 4, 8), size=1600)}</a:p>'
    tree = (
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
        '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + _ppt_box(2, "Titel", 504000, 226080, 9071640, 900000, title)
        + _ppt_box(3, "Tekst", 504000, 1200000, 6200000, 2200000, bullets)
        + _ppt_box(
            4,
            "Vorm",
            6900000,
            1300000,
            2600000,
            1600000,
            note,
            fill=rnd.choice(("FFF2CC", "E2F0D9", "DDEBF7", "FCE4D6")),
        )
        + _ppt_table(rnd, 5)
    )
    return (
        XML + f'<p:sld xmlns:a="{A}" xmlns:r="{REL}" xmlns:p="{P}"><p:cSld><p:spTree>{tree}'
        "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


SLIDE_RELS = _rels(("slideLayout", "../slideLayouts/slideLayout2.xml"))


def _pptx(slides: list[str], title: str) -> bytes:
    with zipfile.ZipFile(FIXTURES / "proef.pptx") as z:
        keep = {
            name: z.read(name)
            for name in z.namelist()
            if name.startswith(("ppt/slideMasters/", "ppt/slideLayouts/", "ppt/theme/"))
            or name == "ppt/presProps.xml"
        }
    count = len(slides)
    rels = [
        ("theme", "theme/theme1.xml"),
        ("slideMaster", "slideMasters/slideMaster1.xml"),
        ("presProps", "presProps.xml"),
    ]
    rels += [("slide", f"slides/slide{n}.xml") for n in range(1, count + 1)]
    ids = "".join(f'<p:sldId id="{255 + n}" r:id="rId{n + 3}"/>' for n in range(1, count + 1))
    presentation = (
        XML + f'<p:presentation xmlns:a="{A}" xmlns:r="{REL}" xmlns:p="{P}">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId2"/></p:sldMasterIdLst>'
        f'<p:sldIdLst>{ids}</p:sldIdLst><p:sldSz cx="10080625" cy="5670550"/>'
        '<p:notesSz cx="7559675" cy="10691813"/></p:presentation>'
    )
    overrides = [
        ("/ppt/presentation.xml", f"{DOC}.presentationml.presentation.main+xml"),
        ("/ppt/slideMasters/slideMaster1.xml", f"{DOC}.presentationml.slideMaster+xml"),
        ("/ppt/slideLayouts/slideLayout1.xml", f"{DOC}.presentationml.slideLayout+xml"),
        ("/ppt/slideLayouts/slideLayout2.xml", f"{DOC}.presentationml.slideLayout+xml"),
        ("/ppt/theme/theme1.xml", f"{DOC}.theme+xml"),
        ("/ppt/presProps.xml", f"{DOC}.presentationml.presProps+xml"),
    ] + [
        (f"/ppt/slides/slide{n}.xml", f"{DOC}.presentationml.slide+xml")
        for n in range(1, count + 1)
    ]
    parts: list[tuple[str, str | bytes]] = [
        ("[Content_Types].xml", _types(*overrides)),
        ("_rels/.rels", _root_rels("ppt/presentation.xml")),
        ("docProps/core.xml", _core(title)),
        ("docProps/app.xml", APP_XML),
        ("ppt/presentation.xml", presentation),
        ("ppt/_rels/presentation.xml.rels", _rels(*rels)),
        *keep.items(),
    ]
    for n, slide in enumerate(slides, start=1):
        parts.append((f"ppt/slides/slide{n}.xml", slide))
        parts.append((f"ppt/slides/_rels/slide{n}.xml.rels", SLIDE_RELS))
    return _zip(parts)


def make_pptx(seed: int, budget: int) -> tuple[bytes, dict[str, Any]]:
    rnd = random.Random(seed)
    title = f"Presentatie {seed}"
    base = xml_size(_pptx([], title))
    # Elke dia kost zijn XML, zijn rels, een Override en een sldId en een Relationship
    per = len(SLIDE_RELS.encode()) + 260
    slides: list[str] = []
    size = base
    while True:
        s = _slide(rnd, len(slides) + 1)
        n = len(s.encode()) + per
        if size + n > budget:
            break
        slides.append(s)
        size += n
    data = _pptx(slides, title)
    while xml_size(data) > budget and slides:  # de schatting per dia was te krap
        slides.pop()
        data = _pptx(slides, title)
    return data, {"slides": len(slides)}


# --- xlsx ---------------------------------------------------------------------------------

XLSX_STYLES = (
    XML + f'<styleSheet xmlns="{S}"><numFmts count="3">'
    '<numFmt numFmtId="164" formatCode="&quot;€&quot;\\ #,##0.00"/>'
    '<numFmt numFmtId="165" formatCode="dd/mm/yyyy"/>'
    '<numFmt numFmtId="166" formatCode="0.0%"/></numFmts>'
    '<fonts count="4"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>'
    '<font><b/><sz val="16"/><color rgb="FF1F4E79"/><name val="Calibri"/><family val="2"/></font>'
    '<font><i/><sz val="11"/><color rgb="FF7F7F7F"/><name val="Calibri"/><family val="2"/></font>'
    "</fonts>"
    '<fills count="4"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E79"/><bgColor indexed="64"/>'
    "</patternFill></fill>"
    '<fill><patternFill patternType="solid"><fgColor rgb="FFDDEBF7"/><bgColor indexed="64"/>'
    "</patternFill></fill></fills>"
    '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left style="thin"><color rgb="FFBFBFBF"/></left><right style="thin"><color'
    ' rgb="FFBFBFBF"/></right><top style="thin"><color rgb="FFBFBFBF"/></top><bottom'
    ' style="thin"><color rgb="FFBFBFBF"/></bottom><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="10">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'  # 0 gewoon
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'  # 1 titel
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1"'
    ' applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>'  # 2 kop
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>'  # 3 tekst
    '<xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"'
    ' applyBorder="1"/>'  # 4 datum
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"'
    ' applyBorder="1"/>'  # 5 bedrag
    '<xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"'
    ' applyBorder="1"/>'  # 6 aantal
    '<xf numFmtId="166" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"'
    ' applyBorder="1"/>'  # 7 procent
    '<xf numFmtId="164" fontId="0" fillId="3" borderId="1" xfId="0" applyNumberFormat="1"'
    ' applyFill="1" applyBorder="1"/>'  # 8 bedrag, gekleurd
    '<xf numFmtId="0" fontId="3" fillId="0" borderId="1" xfId="0" applyFont="1"'
    ' applyBorder="1"/>'  # 9 grijze tekst
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '<dxfs count="1"><dxf><font><b/><color rgb="FF9C0006"/></font><fill><patternFill>'
    '<bgColor rgb="FFFFC7CE"/></patternFill></fill></dxf></dxfs></styleSheet>'
)
TARIFFS = {c: round(0.05 + 0.03 * n, 2) for n, c in enumerate(CATEGORIES)}


def _shared(strings: list[str]) -> str:
    items = "".join(f"<si><t>{_esc(s)}</t></si>" for s in strings)
    return (
        XML + f'<sst xmlns="{S}" count="{len(strings)}" uniqueCount="{len(strings)}">{items}</sst>'
    )


def _xlsx(sheet1_rows: list[str], strings: list[str], rows: int, title: str) -> bytes:
    last = rows + 2  # rij 1 titel, rij 2 kop, daarna de gegevens
    sheet1 = (
        XML + f'<worksheet xmlns="{S}" xmlns:r="{REL}"><dimension ref="A1:L{last}"/>'
        '<sheetViews><sheetView tabSelected="1" workbookViewId="0"><pane ySplit="2"'
        ' topLeftCell="A3" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/><cols>'
        '<col min="1" max="1" width="8" customWidth="1"/>'
        '<col min="2" max="2" width="12" customWidth="1"/>'
        '<col min="3" max="3" width="28" customWidth="1"/>'
        '<col min="4" max="4" width="14" customWidth="1"/>'
        '<col min="5" max="12" width="13" customWidth="1"/></cols>'
        f"<sheetData>{''.join(sheet1_rows)}</sheetData>"
        f'<autoFilter ref="A2:L{last}"/><mergeCells count="1"><mergeCell ref="A1:L1"/>'
        f'</mergeCells><conditionalFormatting sqref="G3:G{last}"><cfRule type="cellIs"'
        ' dxfId="0" priority="1" operator="greaterThan"><formula>50000</formula></cfRule>'
        '</conditionalFormatting><pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75"'
        ' header="0.3" footer="0.3"/><pageSetup paperSize="9" orientation="landscape"/>'
        "</worksheet>"
    )
    tariff_rows = [
        '<row r="1"><c r="A1" s="2" t="inlineStr"><is><t>Categorie</t></is></c>'
        '<c r="B1" s="2" t="inlineStr"><is><t>Korting</t></is></c></row>'
    ]
    for n, (cat, pct) in enumerate(TARIFFS.items(), start=2):
        tariff_rows.append(
            f'<row r="{n}"><c r="A{n}" s="3" t="inlineStr"><is><t>{cat}</t></is></c>'
            f'<c r="B{n}" s="7"><v>{pct}</v></c></row>'
        )
    sheet2 = (
        XML + f'<worksheet xmlns="{S}"><sheetData>{"".join(tariff_rows)}</sheetData></worksheet>'
    )
    summary = [
        '<row r="1"><c r="A1" s="2" t="inlineStr"><is><t>Categorie</t></is></c>'
        '<c r="B1" s="2" t="inlineStr"><is><t>Totaal</t></is></c>'
        '<c r="C1" s="2" t="inlineStr"><is><t>Regels</t></is></c></row>'
    ]
    for n, cat in enumerate(CATEGORIES, start=2):
        summary.append(
            f'<row r="{n}"><c r="A{n}" s="3" t="inlineStr"><is><t>{cat}</t></is></c>'
            f'<c r="B{n}" s="5"><f>SUMIF(Gegevens!D:D,A{n},Gegevens!G:G)</f><v>0</v></c>'
            f'<c r="C{n}" s="6"><f>COUNTIF(Gegevens!D:D,A{n})</f><v>0</v></c></row>'
        )
    n = len(CATEGORIES) + 2
    summary.append(
        f'<row r="{n}"><c r="A{n}" s="2" t="inlineStr"><is><t>Alles</t></is></c>'
        f'<c r="B{n}" s="8"><f>SUM(B2:B{n - 1})</f><v>0</v></c>'
        f'<c r="C{n}" s="6"><f>SUM(C2:C{n - 1})</f><v>0</v></c></row>'
    )
    sheet3 = XML + f'<worksheet xmlns="{S}"><sheetData>{"".join(summary)}</sheetData></worksheet>'
    workbook = (
        XML + f'<workbook xmlns="{S}" xmlns:r="{REL}"><bookViews><workbookView/></bookViews>'
        '<sheets><sheet name="Gegevens" sheetId="1" r:id="rId1"/>'
        '<sheet name="Tarieven" sheetId="2" r:id="rId2"/>'
        '<sheet name="Overzicht" sheetId="3" r:id="rId3"/></sheets>'
        '<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
        f"Gegevens!$A$2:$L${last}</definedName></definedNames>"
        '<calcPr calcId="191029"/></workbook>'
    )
    overrides = [
        ("/xl/workbook.xml", f"{DOC}.spreadsheetml.sheet.main+xml"),
        ("/xl/styles.xml", f"{DOC}.spreadsheetml.styles+xml"),
        ("/xl/sharedStrings.xml", f"{DOC}.spreadsheetml.sharedStrings+xml"),
        *[
            (f"/xl/worksheets/sheet{n}.xml", f"{DOC}.spreadsheetml.worksheet+xml")
            for n in (1, 2, 3)
        ],
    ]
    parts = [
        ("[Content_Types].xml", _types(*overrides)),
        ("_rels/.rels", _root_rels("xl/workbook.xml")),
        ("docProps/core.xml", _core(title)),
        ("docProps/app.xml", APP_XML),
        ("xl/workbook.xml", workbook),
        (
            "xl/_rels/workbook.xml.rels",
            _rels(
                ("worksheet", "worksheets/sheet1.xml"),
                ("worksheet", "worksheets/sheet2.xml"),
                ("worksheet", "worksheets/sheet3.xml"),
                ("styles", "styles.xml"),
                ("sharedStrings", "sharedStrings.xml"),
            ),
        ),
        ("xl/styles.xml", XLSX_STYLES),
        ("xl/sharedStrings.xml", _shared(strings)),
        ("xl/worksheets/sheet1.xml", sheet1),
        ("xl/worksheets/sheet2.xml", sheet2),
        ("xl/worksheets/sheet3.xml", sheet3),
    ]
    return _zip(parts)


HEADERS = (
    "Nr",
    "Datum",
    "Klant",
    "Categorie",
    "Prijs",
    "Aantal",
    "Bedrag",
    "BTW",
    "Totaal",
    "Lopend",
    "Grootte",
    "Korting",
)


def make_xlsx(seed: int, budget: int) -> tuple[bytes, dict[str, Any]]:
    rnd = random.Random(seed)
    title = f"Werkboek {seed}"
    strings = [f"Overzicht {seed}", *HEADERS, *CATEGORIES]
    index = {s: i for i, s in enumerate(strings)}
    head = (
        '<row r="1" ht="24" customHeight="1"><c r="A1" s="1" t="s"><v>0</v></c></row>'
        '<row r="2">'
        + "".join(
            f'<c r="{chr(65 + i)}2" s="2" t="s"><v>{index[h]}</v></c>'
            for i, h in enumerate(HEADERS)
        )
        + "</row>"
    )
    rows = [head]
    running = 0.0
    customers = 0
    # Een vast deel van de XML: alles zonder de regels (en de klantnamen in sharedStrings)
    fixed = xml_size(_xlsx(rows, strings, 0, title))
    size = fixed
    r = 2
    while True:
        r += 1
        name = f"{_words(rnd, 2).title()} {rnd.randrange(100_000):05d}"
        new_string = name not in index
        cat = rnd.choice(CATEGORIES)
        price, qty = round(rnd.uniform(1, 2500), 2), rnd.randint(1, 60)
        amount = round(price * qty, 2)
        vat = round(amount * 0.21, 2)
        total = round(amount + vat, 2)
        running = round(running + total, 2)
        day = 46023 + rnd.randrange(365)  # 2026
        si = index.get(name, len(strings))
        row = (
            f'<row r="{r}">'
            f'<c r="A{r}" s="6"><v>{r - 2}</v></c>'
            f'<c r="B{r}" s="4"><v>{day}</v></c>'
            f'<c r="C{r}" s="3" t="s"><v>{si}</v></c>'
            f'<c r="D{r}" s="3" t="s"><v>{index[cat]}</v></c>'
            f'<c r="E{r}" s="5"><v>{price}</v></c>'
            f'<c r="F{r}" s="6"><v>{qty}</v></c>'
            f'<c r="G{r}" s="5"><f>E{r}*F{r}</f><v>{amount}</v></c>'
            f'<c r="H{r}" s="5"><f>ROUND(G{r}*0.21,2)</f><v>{vat}</v></c>'
            f'<c r="I{r}" s="8"><f>G{r}+H{r}</f><v>{total}</v></c>'
            f'<c r="J{r}" s="5"><f>{"I3" if r == 3 else f"J{r - 1}+I{r}"}</f><v>{running}</v></c>'
            f'<c r="K{r}" s="9" t="str"><f>IF(G{r}&gt;50000,"groot","gewoon")</f>'
            f"<v>{'groot' if amount > 50000 else 'gewoon'}</v></c>"
            f'<c r="L{r}" s="7"><f>VLOOKUP(D{r},Tarieven!$A$2:$B$7,2,FALSE)</f>'
            f"<v>{TARIFFS[cat]}</v></c></row>"
        )
        cost = len(row.encode()) + (
            len(f"<si><t>{_esc(name)}</t></si>".encode()) if new_string else 0
        )
        if size + cost > budget - 64:  # 64: de tellers in de koppen worden langer
            r -= 1
            break
        if new_string:
            index[name] = len(strings)
            strings.append(name)
            customers += 1
        rows.append(row)
        size += cost
    data = _xlsx(rows, strings, r - 2, title)
    while xml_size(data) > budget:
        rows.pop()
        r -= 1
        data = _xlsx(rows, strings, r - 2, title)
    return data, {"rows": r - 2, "formulas_per_row": 6, "customers": customers}


# --- ODF ----------------------------------------------------------------------------------

NS_ODF = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    ' xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
    ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
    ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
    ' xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
    ' xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"'
    ' xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"'
    ' xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0"'
    ' xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"'
    ' xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2"'
    ' xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.3"'
)
ODF_MIME = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
}
TEXT_STYLES = (
    '<style:style style:name="T1" style:family="text"><style:text-properties'
    ' fo:font-weight="bold"/></style:style>'
    '<style:style style:name="T2" style:family="text"><style:text-properties'
    ' fo:font-style="italic"/></style:style>'
    '<style:style style:name="T3" style:family="text"><style:text-properties'
    ' fo:color="#c00000"/></style:style>'
    '<style:style style:name="T4" style:family="text"><style:text-properties'
    ' style:text-underline-style="solid" style:text-underline-width="auto"'
    ' style:text-underline-color="font-color"/></style:style>'
    '<style:style style:name="T5" style:family="text"><style:text-properties'
    ' fo:background-color="#ffff00"/></style:style>'
    '<style:style style:name="T6" style:family="text"><style:text-properties'
    ' fo:font-weight="bold" fo:font-style="italic" fo:font-size="12pt"/></style:style>'
)


def _meta(title: str) -> str:
    return (
        XML + f"<office:document-meta {NS_ODF}><office:meta><dc:title>{_esc(title)}</dc:title>"
        "<meta:generator>Meting fase 4</meta:generator></office:meta></office:document-meta>"
    )


def _manifest(kind: str) -> str:
    return (
        XML
        + '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"'
        ' manifest:version="1.3">'
        f'<manifest:file-entry manifest:full-path="/" manifest:version="1.3"'
        f' manifest:media-type="{ODF_MIME[kind]}"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
        "</manifest:manifest>"
    )


def _odf(kind: str, content: str, styles: str, title: str) -> bytes:
    parts: list[tuple[str, str | bytes]] = [
        ("mimetype", ODF_MIME[kind]),
        ("META-INF/manifest.xml", _manifest(kind)),
        ("content.xml", content),
        ("styles.xml", styles),
        ("meta.xml", _meta(title)),
    ]
    return _zip(parts, stored=("mimetype",))


def _span(rnd: random.Random, text: str) -> str:
    pick = rnd.randrange(10)
    if pick < 6:
        return _esc(text)
    return f'<text:span text:style-name="T{pick - 5}">{_esc(text)}</text:span>'


def _spans(rnd: random.Random, sentences: int) -> str:
    return "".join(_span(rnd, _sentence(rnd) + " ") for _ in range(sentences))


ODT_STYLES = (
    XML + f"<office:document-styles {NS_ODF}><office:font-face-decls>"
    '<style:font-face style:name="Liberation Sans" svg:font-family="&apos;Liberation Sans&apos;"'
    ' style:font-family-generic="swiss"/></office:font-face-decls><office:styles>'
    '<style:default-style style:family="paragraph"><style:paragraph-properties'
    ' fo:margin-bottom="0.25cm" fo:line-height="115%"/><style:text-properties'
    ' style:font-name="Liberation Sans" fo:font-size="11pt" fo:language="nl" fo:country="NL"/>'
    "</style:default-style>"
    '<style:style style:name="Standard" style:family="paragraph" style:class="text"/>'
    '<style:style style:name="Text_20_body" style:display-name="Text body"'
    ' style:family="paragraph" style:parent-style-name="Standard" style:class="text"/>'
    '<style:style style:name="Title" style:family="paragraph" style:parent-style-name="Standard"'
    ' style:class="chapter"><style:text-properties fo:font-size="24pt" fo:font-weight="bold"'
    ' fo:color="#1f4e79"/></style:style>'
    '<style:style style:name="Heading_20_1" style:display-name="Heading 1"'
    ' style:family="paragraph" style:parent-style-name="Standard"'
    ' style:next-style-name="Text_20_body" style:default-outline-level="1"'
    ' style:class="text"><style:paragraph-properties fo:margin-top="0.6cm"'
    ' fo:keep-with-next="always"/><style:text-properties fo:font-size="16pt"'
    ' fo:font-weight="bold" fo:color="#2e75b6"/></style:style>'
    '<style:style style:name="Heading_20_2" style:display-name="Heading 2"'
    ' style:family="paragraph" style:parent-style-name="Standard"'
    ' style:next-style-name="Text_20_body" style:default-outline-level="2"'
    ' style:class="text"><style:text-properties fo:font-size="13pt" fo:font-weight="bold"'
    ' fo:color="#2e75b6"/></style:style>'
    '<style:style style:name="Footer" style:family="paragraph" style:parent-style-name="Standard"'
    ' style:class="extra"><style:paragraph-properties fo:text-align="center"/></style:style>'
    '</office:styles><office:automatic-styles><style:page-layout style:name="pm1">'
    '<style:page-layout-properties fo:page-width="21cm" fo:page-height="29.7cm"'
    ' fo:margin-top="2cm" fo:margin-bottom="2cm" fo:margin-left="2.5cm"'
    ' fo:margin-right="2.5cm"/></style:page-layout></office:automatic-styles>'
    '<office:master-styles><style:master-page style:name="Standard"'
    ' style:page-layout-name="pm1"><style:footer><text:p text:style-name="Footer">Pagina'
    ' <text:page-number text:select-page="current">1</text:page-number></text:p>'
    "</style:footer></style:master-page></office:master-styles></office:document-styles>"
)
ODT_AUTO = (
    TEXT_STYLES + '<style:style style:name="Tabel1" style:family="table"><style:table-properties'
    ' style:width="16cm" table:align="margins"/></style:style>'
    '<style:style style:name="Tabel1.A" style:family="table-column"><style:table-column-properties'
    ' style:column-width="4cm"/></style:style>'
    '<style:style style:name="Tabel1.K" style:family="table-cell"><style:table-cell-properties'
    ' fo:background-color="#1f4e79" fo:padding="0.1cm" fo:border="0.5pt solid #000000"/>'
    "</style:style>"
    '<style:style style:name="Tabel1.C" style:family="table-cell"><style:table-cell-properties'
    ' fo:padding="0.1cm" fo:border="0.5pt solid #000000"/></style:style>'
    '<style:style style:name="P1" style:family="paragraph" style:parent-style-name="Standard">'
    '<style:text-properties fo:color="#ffffff" fo:font-weight="bold"/></style:style>'
    '<style:style style:name="P2" style:family="paragraph" style:parent-style-name="Standard">'
    '<style:paragraph-properties fo:text-align="end"/></style:style>'
    '<text:list-style style:name="L1"><text:list-level-style-bullet text:level="1"'
    ' text:bullet-char="•"><style:list-level-properties'
    ' text:list-level-position-and-space-mode="label-alignment"><style:list-level-label-alignment'
    ' text:label-followed-by="listtab" text:list-tab-stop-position="1.27cm"'
    ' fo:text-indent="-0.635cm" fo:margin-left="1.27cm"/></style:list-level-properties>'
    "</text:list-level-style-bullet></text:list-style>"
    '<text:list-style style:name="L2"><text:list-level-style-number text:level="1"'
    ' style:num-suffix="." style:num-format="1"><style:list-level-properties'
    ' text:list-level-position-and-space-mode="label-alignment"><style:list-level-label-alignment'
    ' text:label-followed-by="listtab" text:list-tab-stop-position="1.27cm"'
    ' fo:text-indent="-0.635cm" fo:margin-left="1.27cm"/></style:list-level-properties>'
    "</text:list-level-style-number></text:list-style>"
)


def _odt_table(rnd: random.Random, n: int) -> str:
    head = "".join(
        f'<table:table-cell table:style-name="Tabel1.K" office:value-type="string">'
        f'<text:p text:style-name="P1">{h}</text:p></table:table-cell>'
        for h in ("Omschrijving", "Aantal", "Prijs", "Totaal")
    )
    rows = [
        f"<table:table-header-rows><table:table-row>{head}</table:table-row>"
        "</table:table-header-rows>"
    ]
    for _ in range(6):
        qty, price = rnd.randint(1, 250), rnd.uniform(1, 900)
        cells = [
            f'<table:table-cell table:style-name="Tabel1.C" office:value-type="string"><text:p>'
            f"{_esc(_words(rnd, 3).capitalize())}</text:p></table:table-cell>"
        ] + [
            f'<table:table-cell table:style-name="Tabel1.C" office:value-type="string">'
            f'<text:p text:style-name="P2">{v}</text:p></table:table-cell>'
            for v in (
                str(qty),
                f"{price:.2f}".replace(".", ","),
                f"{qty * price:.2f}".replace(".", ","),
            )
        ]
        rows.append(f"<table:table-row>{''.join(cells)}</table:table-row>")
    return (
        f'<table:table table:name="Tabel{n}" table:style-name="Tabel1">'
        '<table:table-column table:style-name="Tabel1.A" table:number-columns-repeated="4"/>'
        f"{''.join(rows)}</table:table>"
    )


def _odt_list(rnd: random.Random, style: str, items: int) -> str:
    body = "".join(
        f'<text:list-item><text:p text:style-name="Standard"><text:span text:style-name="T1">'
        f"{_esc(_words(rnd, 2).capitalize())}: </text:span>{_esc(_sentence(rnd, 5, 12))}</text:p>"
        "</text:list-item>"
        for _ in range(items)
    )
    return f'<text:list text:style-name="{style}">{body}</text:list>'


def _odt_content(body: str) -> str:
    return (
        XML + f"<office:document-content {NS_ODF}><office:automatic-styles>{ODT_AUTO}"
        f"</office:automatic-styles><office:body><office:text>{body}</office:text></office:body>"
        "</office:document-content>"
    )


def make_odt(seed: int, budget: int) -> tuple[bytes, dict[str, Any]]:
    rnd = random.Random(seed)
    title = f"Verslag {seed}"
    counter = {"h": 0}

    def block() -> str:
        counter["h"] += 1
        h = counter["h"]
        return (
            f'<text:h text:style-name="Heading_20_1" text:outline-level="1">Hoofdstuk {h}: '
            f"{_esc(_words(rnd, 3))}</text:h>"
            + "".join(
                f'<text:p text:style-name="Text_20_body">{_spans(rnd, rnd.randint(4, 7))}</text:p>'
                for _ in range(3)
            )
            + _odt_list(rnd, "L1", 4)
            + _odt_table(rnd, h)
            + f'<text:h text:style-name="Heading_20_2" text:outline-level="2">'
            f"{_esc(_words(rnd, 4).capitalize())}</text:h>"
            + "".join(
                f'<text:p text:style-name="Text_20_body">{_spans(rnd, rnd.randint(3, 6))}</text:p>'
                for _ in range(2)
            )
            + _odt_list(rnd, "L2", 3)
        )

    def tail() -> str:
        return f'<text:p text:style-name="Text_20_body">{_spans(rnd, 1)}</text:p>'

    head = f'<text:p text:style-name="Title">{_esc(title)}: meting {seed}</text:p>'
    fixed = xml_size(_odf("odt", _odt_content(head), ODT_STYLES, title))
    blocks = _fill(budget, fixed, block, tail)
    data = _odf("odt", _odt_content(head + "".join(blocks)), ODT_STYLES, title)
    return data, {"chapters": counter["h"]}


ODS_STYLES = (
    XML + f"<office:document-styles {NS_ODF}><office:styles>"
    '<style:default-style style:family="table-cell"><style:text-properties'
    ' fo:font-size="10pt" fo:language="nl" fo:country="NL"/></style:default-style>'
    '<style:style style:name="Default" style:family="table-cell"/></office:styles>'
    '<office:automatic-styles><style:page-layout style:name="pm1"><style:page-layout-properties'
    ' style:print-orientation="landscape" fo:page-width="29.7cm" fo:page-height="21cm"/>'
    "</style:page-layout></office:automatic-styles><office:master-styles>"
    '<style:master-page style:name="Default" style:page-layout-name="pm1"/>'
    "</office:master-styles></office:document-styles>"
)
ODS_AUTO = (
    '<number:number-style style:name="N1"><number:number number:decimal-places="2"'
    ' number:min-decimal-places="2" number:min-integer-digits="1" number:grouping="true"/>'
    "</number:number-style>"
    '<number:date-style style:name="N2"><number:day number:style="long"/><number:text>-'
    '</number:text><number:month number:style="long"/><number:text>-</number:text>'
    '<number:year number:style="long"/></number:date-style>'
    '<number:percentage-style style:name="N3"><number:number number:decimal-places="1"'
    ' number:min-decimal-places="1" number:min-integer-digits="1"/><number:text>%</number:text>'
    "</number:percentage-style>"
    '<style:style style:name="co1" style:family="table-column"><style:table-column-properties'
    ' style:column-width="2.5cm"/></style:style>'
    '<style:style style:name="co2" style:family="table-column"><style:table-column-properties'
    ' style:column-width="5cm"/></style:style>'
    '<style:style style:name="ce1" style:family="table-cell" style:parent-style-name="Default">'
    '<style:table-cell-properties fo:background-color="#1f4e79" fo:border="0.5pt solid #bfbfbf"/>'
    '<style:text-properties fo:color="#ffffff" fo:font-weight="bold"/></style:style>'
    '<style:style style:name="ce2" style:family="table-cell" style:parent-style-name="Default"'
    ' style:data-style-name="N1"><style:table-cell-properties fo:border="0.5pt solid #bfbfbf"/>'
    "</style:style>"
    '<style:style style:name="ce3" style:family="table-cell" style:parent-style-name="Default"'
    ' style:data-style-name="N2"><style:table-cell-properties fo:border="0.5pt solid #bfbfbf"/>'
    "</style:style>"
    '<style:style style:name="ce4" style:family="table-cell" style:parent-style-name="Default"'
    ' style:data-style-name="N3"><style:table-cell-properties fo:border="0.5pt solid #bfbfbf"/>'
    "</style:style>"
    '<style:style style:name="ce5" style:family="table-cell" style:parent-style-name="Default">'
    '<style:table-cell-properties fo:border="0.5pt solid #bfbfbf"/></style:style>'
    '<style:style style:name="ce6" style:family="table-cell" style:parent-style-name="Default"'
    ' style:data-style-name="N1"><style:table-cell-properties fo:background-color="#ddebf7"'
    ' fo:border="0.5pt solid #bfbfbf"/><style:text-properties fo:font-weight="bold"/>'
    "</style:style>"
)


def _ods_cell(value: Any, style: str, *, formula: str = "", kind: str = "float") -> str:
    f = f' table:formula="of:={_esc(formula)}"' if formula else ""
    if kind == "string":
        return (
            f'<table:table-cell table:style-name="{style}"{f} office:value-type="string">'
            f"<text:p>{_esc(str(value))}</text:p></table:table-cell>"
        )
    if kind == "date":
        return (
            f'<table:table-cell table:style-name="{style}" office:value-type="date"'
            f' office:date-value="{value}"><text:p>{value}</text:p></table:table-cell>'
        )
    shown = f"{value:.2f}" if isinstance(value, float) else str(value)
    return (
        f'<table:table-cell table:style-name="{style}"{f} office:value-type="{kind}"'
        f' office:value="{value}"><text:p>{shown}</text:p></table:table-cell>'
    )


def _ods_content(rows: list[str]) -> str:
    return (
        XML + f"<office:document-content {NS_ODF}><office:automatic-styles>{ODS_AUTO}"
        "</office:automatic-styles><office:body><office:spreadsheet><table:table"
        ' table:name="Gegevens"><table:table-column table:style-name="co1"'
        ' table:number-columns-repeated="2"/><table:table-column table:style-name="co2"/>'
        '<table:table-column table:style-name="co1" table:number-columns-repeated="7"/>'
        f"{''.join(rows)}</table:table></office:spreadsheet></office:body>"
        "</office:document-content>"
    )


def make_ods(seed: int, budget: int) -> tuple[bytes, dict[str, Any]]:
    rnd = random.Random(seed)
    title = f"Rekenblad {seed}"
    head = (
        "<table:table-row>"
        + "".join(_ods_cell(h, "ce1", kind="string") for h in HEADERS[:10])
        + "</table:table-row>"
    )
    rows = [head]
    fixed = xml_size(_odf("ods", _ods_content(rows), ODS_STYLES, title))
    size, r, running = fixed, 1, 0.0
    while True:
        r += 1
        price, qty = round(rnd.uniform(1, 2500), 2), rnd.randint(1, 60)
        amount = round(price * qty, 2)
        vat = round(amount * 0.21, 2)
        total = round(amount + vat, 2)
        running = round(running + total, 2)
        day = f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
        row = (
            "<table:table-row>"
            + _ods_cell(r - 1, "ce5")
            + _ods_cell(day, "ce3", kind="date")
            + _ods_cell(
                f"{_words(rnd, 2).title()} {rnd.randrange(100_000):05d}", "ce5", kind="string"
            )
            + _ods_cell(rnd.choice(CATEGORIES), "ce5", kind="string")
            + _ods_cell(price, "ce2")
            + _ods_cell(qty, "ce5")
            + _ods_cell(amount, "ce2", formula=f"[.E{r}]*[.F{r}]")
            + _ods_cell(vat, "ce2", formula=f"ROUND([.G{r}]*0.21;2)")
            + _ods_cell(total, "ce6", formula=f"[.G{r}]+[.H{r}]")
            + _ods_cell(running, "ce2", formula="[.I2]" if r == 2 else f"[.J{r - 1}]+[.I{r}]")
            + "</table:table-row>"
        )
        n = len(row.encode())
        if size + n > budget:
            r -= 1
            break
        rows.append(row)
        size += n
    data = _odf("ods", _ods_content(rows), ODS_STYLES, title)
    return data, {"rows": r - 1, "formulas_per_row": 4}


ODP_STYLES = (
    XML + f"<office:document-styles {NS_ODF}><office:styles>"
    '<style:default-style style:family="graphic"><style:text-properties fo:font-size="18pt"'
    ' fo:language="nl" fo:country="NL"/></style:default-style></office:styles>'
    '<office:automatic-styles><style:page-layout style:name="PM1"><style:page-layout-properties'
    ' fo:page-width="28cm" fo:page-height="15.75cm" style:print-orientation="landscape"/>'
    '</style:page-layout><style:style style:name="Mpr1" style:family="presentation">'
    '<style:graphic-properties draw:stroke="none" draw:fill="none"/></style:style>'
    '</office:automatic-styles><office:master-styles><style:master-page style:name="Standaard"'
    ' style:page-layout-name="PM1"><draw:frame presentation:style-name="Mpr1"'
    ' draw:layer="backgroundobjects" svg:width="25cm" svg:height="1cm" svg:x="1.5cm"'
    ' svg:y="14.5cm" presentation:class="footer"><draw:text-box><text:p>Meting fase 4</text:p>'
    "</draw:text-box></draw:frame></style:master-page></office:master-styles>"
    "</office:document-styles>"
)
ODP_AUTO = (
    TEXT_STYLES + '<style:style style:name="gr1" style:family="graphic"><style:graphic-properties'
    ' draw:stroke="none" draw:fill="none" draw:textarea-vertical-align="top"/></style:style>'
    '<style:style style:name="gr2" style:family="graphic"><style:graphic-properties'
    ' draw:stroke="solid" svg:stroke-color="#1f4e79" draw:fill="solid"'
    ' draw:fill-color="#ddebf7" draw:textarea-vertical-align="middle"/></style:style>'
    '<style:style style:name="P1" style:family="paragraph"><style:text-properties'
    ' fo:font-size="30pt" fo:font-weight="bold" fo:color="#1f4e79"/></style:style>'
    '<style:style style:name="P2" style:family="paragraph"><style:paragraph-properties'
    ' fo:text-align="center"/><style:text-properties fo:font-size="16pt"/></style:style>'
    '<text:list-style style:name="L1"><text:list-level-style-bullet text:level="1"'
    ' text:bullet-char="•"><style:list-level-properties text:space-before="0.3cm"'
    ' text:min-label-width="0.6cm"/></text:list-level-style-bullet></text:list-style>'
)


def _odp_slide(rnd: random.Random, n: int) -> str:
    items = "".join(
        f"<text:list-item><text:p>{_spans(rnd, 2)}</text:p></text:list-item>" for _ in range(5)
    )
    return (
        f'<draw:page draw:name="dia{n}" draw:master-page-name="Standaard">'
        '<draw:frame draw:style-name="gr1" svg:width="25cm" svg:height="2.5cm" svg:x="1.5cm"'
        f' svg:y="0.8cm"><draw:text-box><text:p text:style-name="P1">Dia {n}: '
        f"{_esc(_words(rnd, 3))}</text:p></draw:text-box></draw:frame>"
        '<draw:frame draw:style-name="gr1" svg:width="16cm" svg:height="10cm" svg:x="1.5cm"'
        f' svg:y="3.5cm"><draw:text-box><text:list text:style-name="L1">{items}</text:list>'
        "</draw:text-box></draw:frame>"
        '<draw:custom-shape draw:style-name="gr2" svg:width="8cm" svg:height="5cm"'
        f' svg:x="18.5cm" svg:y="4cm"><text:p text:style-name="P2">{_spans(rnd, 1)}</text:p>'
        '<draw:enhanced-geometry svg:viewBox="0 0 21600 21600" draw:type="round-rectangle"'
        ' draw:enhanced-path="M 0 0 L 21600 0 21600 21600 0 21600 Z N"/></draw:custom-shape>'
        "</draw:page>"
    )


def _odp_content(slides: list[str]) -> str:
    return (
        XML + f"<office:document-content {NS_ODF}><office:automatic-styles>{ODP_AUTO}"
        f"</office:automatic-styles><office:body><office:presentation>{''.join(slides)}"
        "</office:presentation></office:body></office:document-content>"
    )


def make_odp(seed: int, budget: int) -> tuple[bytes, dict[str, Any]]:
    rnd = random.Random(seed)
    title = f"Presentatie {seed}"
    fixed = xml_size(_odf("odp", _odp_content([]), ODP_STYLES, title))
    slides: list[str] = []
    size = fixed
    while True:
        s = _odp_slide(rnd, len(slides) + 1)
        n = len(s.encode())
        if size + n > budget:
            break
        slides.append(s)
        size += n
    data = _odf("odp", _odp_content(slides), ODP_STYLES, title)
    return data, {"slides": len(slides)}


# --- samen --------------------------------------------------------------------------------

MAKERS = {
    "docx": make_docx,
    "docx_pages": make_docx_pages,
    "pptx": make_pptx,
    "xlsx": make_xlsx,
    "odt": make_odt,
    "ods": make_ods,
    "odp": make_odp,
}


def make(kind: str, seed: int, budget: int | None = None) -> tuple[bytes, dict[str, Any]]:
    """(bytes, wat erin zit) voor deze soort; budget standaard limit(kind) - MARGIN."""
    budget = limit(kind) - MARGIN if budget is None else budget
    data, info = MAKERS[kind](seed, budget)
    return data, {
        "kind": kind,
        "ext": ext_of(kind),
        "seed": seed,
        "budget": budget,
        "limit": limit(kind),
        "xml_bytes": xml_size(data),
        "file_bytes": len(data),
        **info,
    }


def _with_comment(data: bytes, comment: str) -> bytes:
    """Dezelfde zip met een zip-commentaar: een andere sha, verder niets anders."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename), compress_type=info.compress_type)
        dst.comment = comment.encode()
    return out.getvalue()


_RUN: list[int] = []


def run_seed() -> int:
    """Eén getal per run (E2E_PERF_SEED, anders de klok), voor in de seeds: elke run maakt zo
    nieuwe bestanden. DS bewaart conversies onder de key, en die volgt de sha (keys.convert_key):
    hetzelfde bestand nog eens komt meteen uit zijn cache, zonder x2t."""
    if not _RUN:
        _RUN.append(int(os.environ.get("E2E_PERF_SEED") or time.time()) % 1_000_000)
    return _RUN[0]


def small(name: str, seed: int | str) -> bytes:
    """Een fixture (bijv. proef.docx) met een zip-commentaar per seed: een nieuwe sha."""
    return _with_comment((FIXTURES / name).read_bytes(), f"meting {seed}")


def pdf_pages(data: bytes) -> int:
    """Het aantal pagina's van een PDF (de objecten /Type /Page, ook in objectstromen niet:
    DS schrijft ze plat)."""
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data))
