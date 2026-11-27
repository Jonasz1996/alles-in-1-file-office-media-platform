"""De vereenvoudigde lezer voor kantoordocumenten (app/office/reader.py): de zes formaten, met
testbestanden die hier met zipfile en XML-tekst gemaakt worden, en "bommen" die binnen de
grenzen en de tijd moeten blijven."""

import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app import render
from app.office import reader as office

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ODF_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/"'
)


def png(size=(120, 90), color="purple") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def jpeg(size=(120, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "orange").save(buf, "JPEG")
    return buf.getvalue()


def ooxml(parts: dict[str, str | bytes], thumbnail: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        rels = [
            f'<Relationship Id="rId9" Type="{PKG_REL}/metadata/core-properties" '
            'Target="docProps/core.xml"/>'
        ]
        if thumbnail is not None:
            rels.append(
                f'<Relationship Id="rId8" Type="{PKG_REL}/metadata/thumbnail" '
                'Target="docProps/thumbnail.jpeg"/>'
            )
            z.writestr("docProps/thumbnail.jpeg", thumbnail)
        z.writestr(
            "_rels/.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{PKG_REL}">{"".join(rels)}'
            "</Relationships>",
        )
        z.writestr(
            "docProps/core.xml",
            '<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/'
            'package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>Het verslag</dc:title><dc:creator>Jonas</dc:creator>"
            "</cp:coreProperties>",
        )
        for name, data in parts.items():
            z.writestr(name, data)
    return buf.getvalue()


def docx_bytes(body: str | None = None, thumbnail: bytes | None = None) -> bytes:
    body = body or (
        '<w:p><w:pPr><w:pStyle w:val="Kop1"/></w:pPr><w:r><w:t>Inleiding</w:t></w:r></w:p>'
        '<w:p><w:r><w:t xml:space="preserve">Gewone tekst met </w:t></w:r>'
        '<w:r><w:rPr><w:b/></w:rPr><w:t>vet</w:t></w:r><w:r><w:t xml:space="preserve"> en '
        "</w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>schuin</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        "<w:r><w:t>Eerste punt</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        "<w:r><w:t>Dieper</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr>'
        "<w:r><w:t>Stap een</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Naam</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Aantal</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>Appels</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>3</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "<w:p><w:r><w:t>Tekstvak:</w:t></w:r><w:r><w:pict><w:txbxContent><w:p><w:r>"
        "<w:t>verborgen</w:t></w:r></w:p></w:txbxContent></w:pict></w:r></w:p>"
    )
    styles = (
        f'<?xml version="1.0"?><w:styles xmlns:w="{W_NS}">'
        '<w:style w:type="paragraph" w:styleId="Kop1"><w:name w:val="heading 1"/></w:style>'
        "</w:styles>"
    )
    numbering = (
        f'<?xml version="1.0"?><w:numbering xmlns:w="{W_NS}">'
        '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>'
        '<w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
        '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/>'
        "</w:lvl></w:abstractNum>"
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>'
    )
    document = (
        f'<?xml version="1.0"?><w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'
    )
    return ooxml(
        {
            "word/document.xml": document,
            "word/styles.xml": styles,
            "word/numbering.xml": numbering,
            "docProps/app.xml": '<?xml version="1.0"?><Properties xmlns="http://schemas.'
            'openxmlformats.org/officeDocument/2006/extended-properties"><Pages>2</Pages>'
            "</Properties>",
        },
        thumbnail,
    )


def xlsx_bytes(sheet: str | None = None, date1904: bool = False) -> bytes:
    sheet = sheet or (
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
        '<c r="C1" t="inlineStr"><is><t>Inline</t></is></c></row>'
        '<row r="2"><c r="A2" s="1"><v>45383</v></c><c r="B2"><v>3.5</v></c>'
        '<c r="C2" t="b"><v>1</v></c><c r="D2" t="e"><v>#DIV/0!</v></c></row>'
        '<row r="4"><c r="A4" s="2"><v>45383.5</v></c><c r="B4" s="3"><v>0.25</v></c>'
        '<c r="C4" s="4"><v>0.75</v></c></row>'
    )
    workbook = (
        f'<?xml version="1.0"?><workbook xmlns="{S_NS}" xmlns:r="{R_NS}">'
        f'<workbookPr date1904="{1 if date1904 else 0}"/><sheets>'
        '<sheet name="Overzicht" sheetId="1" r:id="rId1"/>'
        '<sheet name="Leeg" sheetId="2" r:id="rId2"/></sheets></workbook>'
    )
    rels = (
        f'<?xml version="1.0"?><Relationships xmlns="{PKG_REL}">'
        f'<Relationship Id="rId1" Type="{DOC_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId2" Type="{DOC_REL}/worksheet" Target="/xl/worksheets/sheet2.xml"/>'
        f'<Relationship Id="rId3" Type="{DOC_REL}/sharedStrings" Target="sharedStrings.xml"/>'
        f'<Relationship Id="rId4" Type="{DOC_REL}/styles" Target="styles.xml"/></Relationships>'
    )
    shared = (
        f'<?xml version="1.0"?><sst xmlns="{S_NS}"><si><t>Product</t></si>'
        "<si><r><t>Prijs </t></r><r><t>(EUR)</t></r><rPh><t>ふりがな</t></rPh></si></sst>"
    )
    styles = (
        f'<?xml version="1.0"?><styleSheet xmlns="{S_NS}"><numFmts>'
        '<numFmt numFmtId="164" formatCode="dd/mm/yyyy\\ hh:mm"/>'
        '<numFmt numFmtId="165" formatCode="0.0%"/></numFmts>'
        '<cellXfs><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/><xf numFmtId="165"/>'
        '<xf numFmtId="20"/></cellXfs></styleSheet>'
    )
    return ooxml(
        {
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": rels,
            "xl/sharedStrings.xml": shared,
            "xl/styles.xml": styles,
            "xl/worksheets/sheet1.xml": f'<?xml version="1.0"?><worksheet xmlns="{S_NS}">'
            f"<sheetData>{sheet}</sheetData></worksheet>",
            "xl/worksheets/sheet2.xml": f'<?xml version="1.0"?><worksheet xmlns="{S_NS}">'
            "<sheetData/></worksheet>",
        }
    )


def pptx_bytes(slides: list[str] | None = None) -> bytes:
    def shape(text_xml: str, title: bool) -> str:
        ph = '<p:nvPr><p:ph type="title"/></p:nvPr>' if title else "<p:nvPr/>"
        return f"<p:sp><p:nvSpPr>{ph}</p:nvSpPr><p:txBody>{text_xml}</p:txBody></p:sp>"

    slides = slides or [
        shape("<a:p><a:r><a:t>Tweede dia</a:t></a:r></a:p>", True)
        + shape("<a:p><a:r><a:t>Alleen tekst</a:t></a:r></a:p>", False),
        shape("<a:p><a:r><a:t>Welkom</a:t></a:r></a:p>", True)
        + shape(
            "<a:p><a:r><a:t>Punt een</a:t></a:r></a:p>"
            '<a:p><a:pPr lvl="1"/><a:r><a:t>Sub</a:t></a:r><a:br/><a:r><a:t>regel</a:t></a:r>'
            "</a:p>",
            False,
        ),
    ]
    parts = {}
    ids, rels = [], []
    for i, body in enumerate(slides):
        parts[f"ppt/slides/slide{i + 1}.xml"] = (
            f'<?xml version="1.0"?><p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}"><p:cSld><p:spTree>'
            f"{body}</p:spTree></p:cSld></p:sld>"
        )
        rels.append(
            f'<Relationship Id="rId{i + 10}" Type="{DOC_REL}/slide" '
            f'Target="slides/slide{i + 1}.xml"/>'
        )
    # De volgorde komt uit sldIdLst, niet uit de bestandsnamen: dia 2 eerst
    order = list(range(len(slides)))
    if len(order) == 2:
        order = [1, 0]
    for i in order:
        ids.append(f'<p:sldId id="{256 + i}" r:id="rId{i + 10}"/>')
    parts["ppt/presentation.xml"] = (
        f'<?xml version="1.0"?><p:presentation xmlns:p="{P_NS}" xmlns:r="{R_NS}">'
        f'<p:sldIdLst>{"".join(ids)}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/>'
        "</p:presentation>"
    )
    parts["ppt/_rels/presentation.xml.rels"] = (
        f'<?xml version="1.0"?><Relationships xmlns="{PKG_REL}">{"".join(rels)}</Relationships>'
    )
    return ooxml(parts, thumbnail=b"\x01\x00\x00\x00 EMF" + b"\x00" * 100)  # EMF: overslaan


def odf(kind: str, content: str, styles: str = "", thumbnail: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            zipfile.ZipInfo("mimetype"), f"application/vnd.oasis.opendocument.{kind}"
        )  # eerst en ongecomprimeerd, zoals de standaard wil
        z.writestr(
            "content.xml",
            f'<?xml version="1.0"?><office:document-content {ODF_NS}>{content}'
            "</office:document-content>",
        )
        z.writestr(
            "meta.xml",
            f'<?xml version="1.0"?><office:document-meta {ODF_NS}><office:meta>'
            "<dc:title>ODF-titel</dc:title><meta:initial-creator>Anna</meta:initial-creator>"
            '<meta:document-statistic meta:page-count="3"/></office:meta>'
            "</office:document-meta>",
        )
        if styles:
            z.writestr(
                "styles.xml",
                f'<?xml version="1.0"?><office:document-styles {ODF_NS}>{styles}'
                "</office:document-styles>",
            )
        if thumbnail is not None:
            z.writestr("Thumbnails/thumbnail.png", thumbnail)
    return buf.getvalue()


def odt_bytes(thumbnail: bytes | None = None) -> bytes:
    return odf(
        "text",
        '<office:automatic-styles><style:style style:name="T1" style:family="text">'
        '<style:text-properties fo:font-weight="bold"/></style:style>'
        '<text:list-style style:name="L1"><text:list-level-style-number text:level="1"/>'
        "</text:list-style></office:automatic-styles>"
        "<office:body><office:text>"
        '<text:h text:outline-level="2">Hoofdstuk</text:h>'
        '<text:p>Een <text:span text:style-name="T1">vette</text:span> zin<text:s text:c="2"/>'
        "met spaties.</text:p>"
        '<text:list text:style-name="L1"><text:list-item><text:p>Een</text:p>'
        "<text:list><text:list-item><text:p>Twee</text:p></text:list-item></text:list>"
        "</text:list-item></text:list>"
        "<table:table><table:table-row><table:table-cell><text:p>a</text:p></table:table-cell>"
        '<table:table-cell table:number-columns-repeated="2"><text:p>b</text:p>'
        "</table:table-cell></table:table-row></table:table>"
        "<text:p>Met een opmerking<office:annotation><text:p>niet tonen</text:p>"
        "</office:annotation>.</text:p>"
        "</office:text></office:body>",
        thumbnail=thumbnail,
    )


def ods_bytes(rows: str | None = None) -> bytes:
    rows = rows or (
        "<table:table-row><table:table-cell><text:p>Naam</text:p></table:table-cell>"
        "<table:table-cell><text:p>Datum</text:p></table:table-cell></table:table-row>"
        '<table:table-row><table:table-cell office:value-type="string"><text:p>Bas</text:p>'
        '</table:table-cell><table:table-cell office:value-type="date" '
        'office:date-value="2024-04-01"><text:p>01-04-24</text:p></table:table-cell>'
        "</table:table-row>"
        '<table:table-row table:number-rows-repeated="1048570"><table:table-cell '
        'table:number-columns-repeated="16384"/></table:table-row>'
    )
    return odf(
        "spreadsheet",
        '<office:body><office:spreadsheet><table:table table:name="Blad1">'
        f"{rows}</table:table>"
        '<table:table table:name="Blad2"><table:table-row><table:table-cell>'
        "<text:p>x</text:p></table:table-cell></table:table-row></table:table>"
        "</office:spreadsheet></office:body>",
    )


def odp_bytes() -> bytes:
    return odf(
        "presentation",
        '<office:body><office:presentation><draw:page draw:name="d1">'
        '<draw:frame presentation:class="title"><draw:text-box><text:p>Dia een</text:p>'
        "</draw:text-box></draw:frame>"
        '<draw:frame presentation:class="outline"><draw:text-box><text:list><text:list-item>'
        "<text:p>Punt</text:p><text:list><text:list-item><text:p>Subpunt</text:p>"
        "</text:list-item></text:list></text:list-item></text:list></draw:text-box></draw:frame>"
        "<presentation:notes><draw:frame><draw:text-box><text:p>Sprekersnotitie</text:p>"
        "</draw:text-box></draw:frame></presentation:notes>"
        '</draw:page><draw:page draw:name="d2"><draw:frame presentation:class="title">'
        "<draw:text-box><text:p>Dia twee</text:p></draw:text-box></draw:frame></draw:page>"
        "</office:presentation></office:body>",
        styles='<office:automatic-styles><style:page-layout style:name="PM1">'
        '<style:page-layout-properties fo:page-width="28cm" fo:page-height="15.75cm"/>'
        "</style:page-layout></office:automatic-styles>",
    )


def read(tmp_path: Path, data: bytes, **kw):
    path = tmp_path / "doc"
    path.write_bytes(data)
    return office.read(path, **kw)


def texts(runs) -> str:
    return "".join(r["text"] for r in runs)


def test_docx(tmp_path):
    result = read(tmp_path, docx_bytes(thumbnail=jpeg()))
    doc = result["doc"]
    assert doc["type"] == "document" and not doc["truncated"]
    blocks = doc["blocks"]
    assert blocks[0] == {"type": "h", "level": 1, "runs": [{"text": "Inleiding"}]}
    assert blocks[1]["runs"] == [
        {"text": "Gewone tekst met "},
        {"text": "vet", "b": True},
        {"text": " en "},
        {"text": "schuin", "i": True},
    ]
    assert [(b["type"], b["level"], b["ordered"], texts(b["runs"])) for b in blocks[2:5]] == [
        ("li", 0, False, "Eerste punt"),
        ("li", 1, False, "Dieper"),
        ("li", 0, True, "Stap een"),
    ]
    assert blocks[5] == {"type": "table", "rows": [["Naam", "Aantal"], ["Appels", "3"]]}
    assert texts(blocks[6]["runs"]) == "Tekstvak:"  # de tekst in het tekstvak niet
    assert result["meta"] == {
        "title": "Het verslag",
        "author": "Jonas",
        "pages": 2,
        "office": "document",
        "format": "docx",
    }
    assert result["thumbnail"].startswith(b"\xff\xd8")


def test_xlsx(tmp_path):
    doc = read(tmp_path, xlsx_bytes())["doc"]
    assert doc["type"] == "sheet"
    first, empty = doc["sheets"]
    assert first["name"] == "Overzicht"
    assert empty == {"name": "Leeg", "rows": [], "truncated": False}
    assert first["rows"] == [
        ["Product", "Prijs (EUR)", "Inline"],  # rPh (uitspraak) niet
        ["2024-04-01", "3.5", "TRUE", "#DIV/0!"],
        [],
        ["2024-04-01 12:00", "25%", "18:00:00"],
    ]
    # date1904: dezelfde getallen zijn vier jaar later
    doc = read(tmp_path, xlsx_bytes(date1904=True))["doc"]
    assert doc["sheets"][0]["rows"][1][0] == "2028-04-02"


def test_xlsx_grenzen_200_bij_50(tmp_path):
    rows = "".join(
        f'<row r="{r}">'
        + "".join(f'<c r="{office_col(c)}{r}"><v>{r * c}</v></c>' for c in range(1, 61))
        + "</row>"
        for r in range(1, 260)
    )
    doc = read(tmp_path, xlsx_bytes(rows))["doc"]
    sheet = doc["sheets"][0]
    assert len(sheet["rows"]) == 200 and len(sheet["rows"][0]) == 50
    assert sheet["truncated"] and doc["truncated"]


def office_col(n: int) -> str:
    letters = ""
    while n:
        n, rest = divmod(n - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def test_kolomletters_en_datumnotaties():
    assert office.column_index("A") == 0 and office.column_index("AA") == 26
    assert office.date_format("dd/mm/yyyy") == "date"
    assert office.date_format("[h]:mm:ss") == "time"
    assert office.date_format('0.00" dagen"') is None
    assert office.date_format("[Red]0.00") is None
    assert office.date_format("mmm") == "date"


def test_pptx(tmp_path):
    result = read(tmp_path, pptx_bytes())
    doc = result["doc"]
    assert doc["type"] == "slides" and doc["size"] == [960.0, 540.0]
    assert doc["slides"] == [
        {
            "title": "Welkom",
            "bullets": [{"text": "Punt een", "level": 0}, {"text": "Sub\nregel", "level": 1}],
        },
        {"title": "Tweede dia", "bullets": [{"text": "Alleen tekst", "level": 0}]},
    ]
    assert result["meta"]["pages"] == 2
    assert result["thumbnail"] is None  # een EMF-thumbnail slaan we over


def test_odt(tmp_path):
    result = read(tmp_path, odt_bytes(thumbnail=png()))
    blocks = result["doc"]["blocks"]
    assert blocks[0] == {"type": "h", "level": 2, "runs": [{"text": "Hoofdstuk"}]}
    assert blocks[1]["runs"] == [
        {"text": "Een "},
        {"text": "vette", "b": True},
        {"text": " zin  met spaties."},
    ]
    assert [(b["level"], b["ordered"], texts(b["runs"])) for b in blocks[2:4]] == [
        (0, True, "Een"),
        (1, False, "Twee"),
    ]
    assert blocks[4] == {"type": "table", "rows": [["a", "b", "b"]]}
    assert texts(blocks[5]["runs"]) == "Met een opmerking."
    assert result["meta"]["title"] == "ODF-titel" and result["meta"]["author"] == "Anna"
    assert result["meta"]["pages"] == 3 and result["thumbnail"].startswith(b"\x89PNG")


def test_ods_herhalingen_afgekapt(tmp_path):
    start = time.monotonic()
    doc = read(tmp_path, ods_bytes())["doc"]
    assert time.monotonic() - start < 2
    first, second = doc["sheets"]
    assert first["name"] == "Blad1"
    assert first["rows"] == [["Naam", "Datum"], ["Bas", "01-04-24"]]
    assert second["rows"] == [["x"]]


def test_ods_herhaalde_waarden_binnen_de_grens(tmp_path):
    rows = (
        '<table:table-row table:number-rows-repeated="5000"><table:table-cell '
        'table:number-columns-repeated="5000"><text:p>v</text:p></table:table-cell>'
        "</table:table-row>"
    )
    sheet = read(tmp_path, ods_bytes(rows))["doc"]["sheets"][0]
    assert len(sheet["rows"]) == 200 and all(len(r) == 50 for r in sheet["rows"])
    assert sheet["truncated"]


def test_odp(tmp_path):
    result = read(tmp_path, odp_bytes())
    doc = result["doc"]
    assert doc["size"] == [793.7, 446.5]
    assert doc["slides"] == [
        {
            "title": "Dia een",
            "bullets": [{"text": "Punt", "level": 0}, {"text": "Subpunt", "level": 1}],
        },
        {"title": "Dia twee", "bullets": []},
    ]  # sprekersnotities niet
    assert result["meta"]["pages"] == 2 and result["meta"]["office"] == "slides"


# --- bommen ---------------------------------------------------------------------------------


def test_doctype_en_entiteiten_geweigerd(tmp_path):
    laughs = (
        '<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>&lol2;</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    data = ooxml({"word/document.xml": laughs})
    with pytest.raises(office.Unreadable):
        read(tmp_path, data)
    external = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>&e;</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    with pytest.raises(office.Unreadable):
        read(tmp_path, ooxml({"word/document.xml": external}))


def test_te_groot_onderdeel_geweigerd(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "CONTENT_BYTES", 10_000)
    body = "<w:p><w:r><w:t>" + "x" * 20_000 + "</w:t></w:r></w:p>"
    with pytest.raises(office.Unreadable):
        read(tmp_path, docx_bytes(body))


def test_liegend_zip_bestand_pakt_niet_meer_uit_dan_opgegeven(tmp_path):
    """Een zip-bom: een groot onderdeel dat zegt klein te zijn. zipfile pakt nooit meer uit
    dan de opgegeven grootte (en meldt dan een fout), dus dat geeft geen geheugen of tijd."""
    buf = io.BytesIO()
    big = b"<w:p><w:r><w:t>" + b"a" * (50 * 1024 * 1024) + b"</w:t></w:r></w:p>"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", big)
    data = bytearray(buf.getvalue())
    # De opgegeven grootte in de centrale directory en de lokale kop op 100 bytes zetten
    for sig, offset in ((b"PK\x01\x02", 24), (b"PK\x03\x04", 22)):
        pos = data.find(sig)
        data[pos + offset : pos + offset + 4] = (100).to_bytes(4, "little")
    start = time.monotonic()
    with pytest.raises(office.Unreadable):
        read(tmp_path, bytes(data))
    assert time.monotonic() - start < 5


def test_veel_tekst_blijft_onder_de_grens(tmp_path):
    body = "".join(f"<w:p><w:r><w:t>{'tekst ' * 100}{i}</w:t></w:r></w:p>" for i in range(5000))
    result = read(tmp_path, docx_bytes(body))
    doc = result["doc"]
    assert doc["truncated"]
    assert sum(len(texts(b["runs"])) for b in doc["blocks"]) <= office.TEXT_CHARS
    assert len(office.fit(doc)) <= office.DOC_BYTES


def test_fit_kort_in_tot_512_kb():
    doc = {
        "type": "document",
        "blocks": [{"type": "p", "runs": [{"text": "é" * 1000}]}] * 2000,
        "truncated": False,
    }
    data = office.fit(doc)
    assert len(data) <= office.DOC_BYTES
    loaded = json.loads(data)
    assert loaded["truncated"] and 0 < len(loaded["blocks"]) < 2000


def test_trage_lezer_stopt_op_tijd(tmp_path, monkeypatch):
    real = office.sax

    def slow(stream, start, end=None, text=None):
        def slow_start(tag, attrs):
            time.sleep(0.01)
            start(tag, attrs)

        real(stream, slow_start, end, text)

    monkeypatch.setattr(office, "sax", slow)
    body = "<w:p><w:r><w:t>x</w:t></w:r></w:p>" * 2000
    start = time.monotonic()
    with pytest.raises(office.Unreadable):
        read(tmp_path, docx_bytes(body), seconds=0.3)
    assert time.monotonic() - start < 2


def test_geen_kantoordocument(tmp_path):
    for data in (b"geen zip", ooxml({"iets.xml": "<a/>"})):
        with pytest.raises(office.Unreadable):
            read(tmp_path, data)


# --- in het renderproces --------------------------------------------------------------------


def render_office(tmp_path: Path, data: bytes, name: str, mime=None):
    source = tmp_path / "bron"
    source.write_bytes(data)
    out, work = tmp_path / "out", tmp_path / "work"
    for d in (out, work):
        d.mkdir(exist_ok=True)
        for f in d.iterdir():
            f.unlink()
    result = render.render(
        {
            "source": str(source),
            "out": str(out),
            "work": str(work),
            "mime": mime,
            "name": name,
            "max_pixels": 10**8,
        }
    )
    return result, out


@pytest.mark.parametrize(
    ("maker", "name", "kind"),
    [
        (docx_bytes, "a.docx", "document"),
        (xlsx_bytes, "a.xlsx", "sheet"),
        (pptx_bytes, "a.pptx", "slides"),
        (odt_bytes, "a.odt", "document"),
        (ods_bytes, "a.ods", "sheet"),
        (odp_bytes, "a.odp", "slides"),
    ],
)
def test_render_schrijft_doc_json(tmp_path, maker, name, kind):
    result, out = render_office(tmp_path, maker(), name)
    assert result["state"] == "ready" and result["meta"]["office"] == kind
    doc = json.loads((out / "doc.json").read_bytes())
    assert doc["type"] == kind


def test_render_thumbnail_uit_het_document(tmp_path):
    result, out = render_office(tmp_path, odt_bytes(thumbnail=png((600, 400))), "a.odt")
    assert result["sizes"] == [1024, 256] or sorted(result["sizes"]) == [256, 1024]
    assert (out / "256.webp").is_file()
    result, out = render_office(tmp_path, b"PK\x03\x04kapot", "kapot.docx")
    assert result["state"] == "none" and not (out / "doc.json").exists()
