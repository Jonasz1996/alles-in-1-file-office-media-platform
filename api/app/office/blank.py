"""Lege documenten (Keuze 24): een lege docx, xlsx of pptx, alleen met zipfile.

blank(kind, locale) -> bytes. Eigen bestanden, niet de sjablonen uit het image van DS: die zijn
deel van DS (AGPL) en binden ons aan één editor. Wat erin staat, is het minimum dat de spec
vraagt, plus wat de echte DS nodig had (spike stap 8):

- docx: [Content_Types].xml, _rels/.rels, word/document.xml (één lege alinea; A4 met marges van
  1440 twips), word/styles.xml (docDefaults met w:lang van de taal, en de stijl Normal),
  word/settings.xml met compatibilityMode 15, en word/_rels/document.xml.rels. Zonder die
  compatibilityMode toont DS bij "Downloaden als docx" een melding over het nieuwe formaat
  (spike A17);
- xlsx: xl/workbook.xml met één blad ("Blad1" of "Sheet1"), xl/_rels/workbook.xml.rels,
  xl/worksheets/sheet1.xml (lege sheetData) en een minimale xl/styles.xml;
- pptx: ppt/presentation.xml in 16:9 (12192000 x 6858000 EMU), één slideMaster, één slideLayout
  (titel), ppt/slides/slide1.xml en ppt/theme/theme1.xml (12 kleuren, twee lettertypes en de
  verplichte opmaakstijlen).

Deterministisch: elk onderdeel met date_time (1980, 1, 1, 0, 0, 0), dezelfde rechten en een
vaste volgorde. Dus vaste bytes, en één blob voor alle lege documenten van een soort en taal
(ontdubbeling in de blob-opslag).

language(locale) en default_name(kind, locale) geven de taal ("nl" of "en") en de naam van een
nieuw document ("Nieuw document.docx", "New spreadsheet.xlsx", ...).
"""

import functools
import io
import zipfile
from typing import Literal

Kind = Literal["docx", "xlsx", "pptx"]
Language = Literal["nl", "en"]

KINDS: tuple[Kind, ...] = ("docx", "xlsx", "pptx")

# De naam van een nieuw document, zonder extensie, per taal (Keuze 24)
NAMES: dict[Language, dict[Kind, str]] = {
    "nl": {"docx": "Nieuw document", "xlsx": "Nieuw werkblad", "pptx": "Nieuwe presentatie"},
    "en": {"docx": "New document", "xlsx": "New spreadsheet", "pptx": "New presentation"},
}
# De taal van de tekst (w:lang, a:endParaRPr lang) en de naam van het eerste blad
TAGS: dict[Language, str] = {"nl": "nl-NL", "en": "en-GB"}
SHEETS: dict[Language, str] = {"nl": "Blad1", "en": "Sheet1"}
LAYOUTS: dict[Language, str] = {"nl": "Titeldia", "en": "Title Slide"}

DATE_TIME = (1980, 1, 1, 0, 0, 0)
# Gewone bestandsrechten (rw-r--r--) en "gemaakt op Unix", ook op een andere machine, zodat de
# bytes overal gelijk zijn
FILE_MODE = 0o100644 << 16
UNIX = 3

# Namespaces
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

# Soorten inhoud
OFFICE = "application/vnd.openxmlformats-officedocument"
WORD = f"{OFFICE}.wordprocessingml"
SHEET = f"{OFFICE}.spreadsheetml"
SLIDES = f"{OFFICE}.presentationml"
RELS_TYPE = "application/vnd.openxmlformats-package.relationships+xml"

# Een A4-pagina in twips (1/20 punt), met marges van 2,54 cm (Keuze 24)
A4 = (11906, 16838)
MARGIN = 1440
# 16:9 in EMU (Keuze 24); de notitiepagina staand
SLIDE = (12192000, 6858000)
NOTES = (6858000, 9144000)


def language(locale: str | None) -> Language:
    """De taal van een locale ("en", "en-GB", "EN"): Engels als hij met en begint, anders
    Nederlands (ook zonder locale)."""
    return "en" if (locale or "").strip().lower().startswith("en") else "nl"


def default_name(kind: Kind, locale: str | None) -> str:
    """De naam van een nieuw document: Nieuw document.docx, Nieuw werkblad.xlsx of Nieuwe
    presentatie.pptx (in het Engels New document.docx, ...)."""
    return f"{NAMES[language(locale)][kind]}.{kind}"


def blank(kind: Kind, locale: str | None) -> bytes:
    """Een leeg document van deze soort, in de taal van locale. Elke keer dezelfde bytes."""
    if kind not in KINDS:
        raise ValueError(f"onbekende soort: {kind}")
    return _build(kind, language(locale))


@functools.cache
def _build(kind: Kind, lang: Language) -> bytes:
    parts = {"docx": _docx, "xlsx": _xlsx, "pptx": _pptx}[kind](lang)
    return _pack(parts)


# --- het pakket ---


def _pack(parts: list[tuple[str, str]]) -> bytes:
    """De onderdelen als zip, in deze volgorde, met vaste datum en rechten."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = UNIX
            info.external_attr = FILE_MODE
            archive.writestr(info, text.encode("utf-8"))
    return out.getvalue()


def _types(overrides: list[tuple[str, str]]) -> str:
    """[Content_Types].xml: rels en xml als standaard, en elk onderdeel met zijn eigen soort."""
    body = (
        f'<Default Extension="rels" ContentType="{RELS_TYPE}"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
    )
    body += "".join(f'<Override PartName="{p}" ContentType="{c}"/>' for p, c in overrides)
    return f'{XML}<Types xmlns="{CT}">{body}</Types>'


def _rels(items: list[tuple[str, str, str]]) -> str:
    """Een .rels-bestand: (id, soort, doel)."""
    body = "".join(
        f'<Relationship Id="{i}" Type="{REL}/{kind}" Target="{target}"/>'
        for i, kind, target in items
    )
    return f'{XML}<Relationships xmlns="{PKG_REL}">{body}</Relationships>'


# --- docx ---


def _docx(lang: Language) -> list[tuple[str, str]]:
    tag = TAGS[lang]
    width, height = A4
    document = (
        f'{XML}<w:document xmlns:w="{W}" xmlns:r="{REL}"><w:body><w:p/>'
        f'<w:sectPr><w:pgSz w:w="{width}" w:h="{height}"/>'
        f'<w:pgMar w:top="{MARGIN}" w:right="{MARGIN}" w:bottom="{MARGIN}" w:left="{MARGIN}" '
        'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>'
    )
    styles = (
        f'{XML}<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr>'
        f'<w:lang w:val="{tag}" w:eastAsia="en-US" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>'
        "<w:pPrDefault/></w:docDefaults>"
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:qFormat/></w:style></w:styles>'
    )
    # compatibilityMode 15 (Word 2013 en later): anders opent DS hem in de compatibiliteitsstand
    # en meldt hij bij "Downloaden als docx" het nieuwe formaat (spike A17)
    settings = (
        f'{XML}<w:settings xmlns:w="{W}"><w:defaultTabStop w:val="708"/>'
        '<w:compat><w:compatSetting w:name="compatibilityMode" '
        'w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>'
        "</w:settings>"
    )
    return [
        (
            "[Content_Types].xml",
            _types(
                [
                    ("/word/document.xml", f"{WORD}.document.main+xml"),
                    ("/word/styles.xml", f"{WORD}.styles+xml"),
                    ("/word/settings.xml", f"{WORD}.settings+xml"),
                ]
            ),
        ),
        ("_rels/.rels", _rels([("rId1", "officeDocument", "word/document.xml")])),
        ("word/document.xml", document),
        (
            "word/_rels/document.xml.rels",
            _rels([("rId1", "styles", "styles.xml"), ("rId2", "settings", "settings.xml")]),
        ),
        ("word/styles.xml", styles),
        ("word/settings.xml", settings),
    ]


# --- xlsx ---


def _xlsx(lang: Language) -> list[tuple[str, str]]:
    workbook = (
        f'{XML}<workbook xmlns="{S}" xmlns:r="{REL}"><sheets>'
        f'<sheet name="{SHEETS[lang]}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    sheet = f'{XML}<worksheet xmlns="{S}"><sheetData/></worksheet>'
    styles = (
        f'{XML}<styleSheet xmlns="{S}">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        "</cellStyleXfs>"
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        "</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )
    return [
        (
            "[Content_Types].xml",
            _types(
                [
                    ("/xl/workbook.xml", f"{SHEET}.sheet.main+xml"),
                    ("/xl/worksheets/sheet1.xml", f"{SHEET}.worksheet+xml"),
                    ("/xl/styles.xml", f"{SHEET}.styles+xml"),
                ]
            ),
        ),
        ("_rels/.rels", _rels([("rId1", "officeDocument", "xl/workbook.xml")])),
        ("xl/workbook.xml", workbook),
        (
            "xl/_rels/workbook.xml.rels",
            _rels(
                [("rId1", "worksheet", "worksheets/sheet1.xml"), ("rId2", "styles", "styles.xml")]
            ),
        ),
        ("xl/worksheets/sheet1.xml", sheet),
        ("xl/styles.xml", styles),
    ]


# --- pptx ---


def _theme() -> str:
    """Het minimum dat de spec van een thema vraagt: 12 kleuren, twee lettertypes en van elke
    opmaakstijl drie."""

    def rgb(value: str) -> str:
        return f'<a:srgbClr val="{value}"/>'

    colors = (
        '<a:clrScheme name="Platform">'
        '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
        '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
        f"<a:dk2>{rgb('44546A')}</a:dk2><a:lt2>{rgb('E7E6E6')}</a:lt2>"
        f"<a:accent1>{rgb('4472C4')}</a:accent1><a:accent2>{rgb('ED7D31')}</a:accent2>"
        f"<a:accent3>{rgb('A5A5A5')}</a:accent3><a:accent4>{rgb('FFC000')}</a:accent4>"
        f"<a:accent5>{rgb('5B9BD5')}</a:accent5><a:accent6>{rgb('70AD47')}</a:accent6>"
        f"<a:hlink>{rgb('0563C1')}</a:hlink><a:folHlink>{rgb('954F72')}</a:folHlink>"
        "</a:clrScheme>"
    )
    fonts = (
        '<a:fontScheme name="Platform">'
        '<a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/>'
        "</a:majorFont>"
        '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/>'
        "</a:minorFont></a:fontScheme>"
    )
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = f'<a:ln w="6350">{fill}</a:ln>'
    effect = "<a:effectStyle><a:effectLst/></a:effectStyle>"
    formats = (
        '<a:fmtScheme name="Platform">'
        f"<a:fillStyleLst>{fill * 3}</a:fillStyleLst><a:lnStyleLst>{line * 3}</a:lnStyleLst>"
        f"<a:effectStyleLst>{effect * 3}</a:effectStyleLst>"
        f"<a:bgFillStyleLst>{fill * 3}</a:bgFillStyleLst></a:fmtScheme>"
    )
    return (
        f'{XML}<a:theme xmlns:a="{A}" name="Platform">'
        f"<a:themeElements>{colors}{fonts}{formats}</a:themeElements></a:theme>"
    )


def _pptx(lang: Language) -> list[tuple[str, str]]:
    ns = f'xmlns:a="{A}" xmlns:r="{REL}" xmlns:p="{P}"'
    group = (
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
        '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    )
    title = (
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr><a:spLocks noGrp="1"/>'
        '</p:cNvSpPr><p:nvPr><p:ph type="ctrTitle"/></p:nvPr></p:nvSpPr><p:spPr/>'
        f'<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="{TAGS[lang]}"/></a:p>'
        "</p:txBody></p:sp>"
    )
    cx, cy = SLIDE
    nx, ny = NOTES
    presentation = (
        f"{XML}<p:presentation {ns}>"
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        '<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>'
        f'<p:sldSz cx="{cx}" cy="{cy}"/><p:notesSz cx="{nx}" cy="{ny}"/></p:presentation>'
    )
    master = (
        f"{XML}<p:sldMaster {ns}><p:cSld><p:spTree>{group}</p:spTree></p:cSld>"
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
        'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" '
        'folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        "</p:sldMaster>"
    )
    layout = (
        f'{XML}<p:sldLayout {ns} type="title" preserve="1">'
        f'<p:cSld name="{LAYOUTS[lang]}"><p:spTree>{group}{title}</p:spTree></p:cSld>'
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
    )
    slide = (
        f"{XML}<p:sld {ns}><p:cSld><p:spTree>{group}{title}</p:spTree></p:cSld>"
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )
    return [
        (
            "[Content_Types].xml",
            _types(
                [
                    ("/ppt/presentation.xml", f"{SLIDES}.presentation.main+xml"),
                    ("/ppt/slideMasters/slideMaster1.xml", f"{SLIDES}.slideMaster+xml"),
                    ("/ppt/slideLayouts/slideLayout1.xml", f"{SLIDES}.slideLayout+xml"),
                    ("/ppt/slides/slide1.xml", f"{SLIDES}.slide+xml"),
                    ("/ppt/theme/theme1.xml", f"{OFFICE}.theme+xml"),
                ]
            ),
        ),
        ("_rels/.rels", _rels([("rId1", "officeDocument", "ppt/presentation.xml")])),
        ("ppt/presentation.xml", presentation),
        (
            "ppt/_rels/presentation.xml.rels",
            _rels(
                [
                    ("rId1", "slideMaster", "slideMasters/slideMaster1.xml"),
                    ("rId2", "slide", "slides/slide1.xml"),
                    ("rId3", "theme", "theme/theme1.xml"),
                ]
            ),
        ),
        ("ppt/slideMasters/slideMaster1.xml", master),
        (
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            _rels(
                [
                    ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
                    ("rId2", "theme", "../theme/theme1.xml"),
                ]
            ),
        ),
        ("ppt/slideLayouts/slideLayout1.xml", layout),
        (
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            _rels([("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")]),
        ),
        ("ppt/slides/slide1.xml", slide),
        (
            "ppt/slides/_rels/slide1.xml.rels",
            _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")]),
        ),
        ("ppt/theme/theme1.xml", _theme()),
    ]
