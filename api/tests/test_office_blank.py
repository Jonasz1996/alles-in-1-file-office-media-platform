"""De lege documenten van O4 (Keuze 24, §2 O4): geldige zip's waarvan [Content_Types].xml elk
onderdeel noemt, leesbaar voor de lezer van B21, door de invoercontrole, en elke keer dezelfde
bytes. De docx heeft word/settings.xml met compatibilityMode 15 (spike stap 8, A17)."""

import io
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from app.config import Settings
from app.office import blank, inspect, reader

KINDS = ["docx", "xlsx", "pptx"]
LOCALES = ["nl", "en"]

CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

MAIN = {
    "docx": "/word/document.xml",
    "xlsx": "/xl/workbook.xml",
    "pptx": "/ppt/presentation.xml",
}


def unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def xml(parts: dict[str, bytes], name: str) -> ElementTree.Element:
    return ElementTree.fromstring(parts[name])


def rels_of(part: str) -> str:
    folder, name = posixpath.split(part)
    return posixpath.join(folder, "_rels", f"{name}.rels")


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("kind", KINDS)
def test_een_geldige_zip_met_alleen_goede_xml(kind, locale):
    data = blank.blank(kind, locale)
    assert data.startswith(b"PK\x03\x04")
    parts = unzip(data)
    assert list(parts)[0] == "[Content_Types].xml"
    for name in parts:
        xml(parts, name)  # elk onderdeel is goedgevormde XML
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            assert info.compress_type == zipfile.ZIP_DEFLATED
            assert info.flag_bits & 0x1 == 0  # niets versleuteld
            assert not info.is_dir()


@pytest.mark.parametrize("kind", KINDS)
def test_content_types_noemt_elk_onderdeel(kind):
    parts = unzip(blank.blank(kind, "nl"))
    types = xml(parts, "[Content_Types].xml")
    defaults = {d.get("Extension"): d.get("ContentType") for d in types.iter(f"{CT}Default")}
    overrides = {o.get("PartName"): o.get("ContentType") for o in types.iter(f"{CT}Override")}
    assert defaults["rels"] == "application/vnd.openxmlformats-package.relationships+xml"
    for name in parts:
        if name == "[Content_Types].xml":
            continue
        if name.endswith(".rels"):
            continue  # Default rels
        assert f"/{name}" in overrides, name
    # Geen Override voor een onderdeel dat er niet is
    assert {p.lstrip("/") for p in overrides} <= set(parts)
    assert overrides[MAIN[kind]].endswith(".main+xml")


@pytest.mark.parametrize("kind", KINDS)
def test_elke_relatie_wijst_naar_een_onderdeel_dat_er_is(kind):
    parts = unzip(blank.blank(kind, "nl"))
    root = xml(parts, "_rels/.rels")
    [main] = list(root.iter(f"{PKG_REL}Relationship"))
    assert main.get("Type", "").endswith("/officeDocument")
    assert f"/{main.get('Target')}" == MAIN[kind]
    for name in parts:
        if not name.endswith(".rels") or name == "_rels/.rels":
            continue
        folder = posixpath.dirname(posixpath.dirname(name))
        source = posixpath.join(folder, posixpath.basename(name)[: -len(".rels")])
        assert source in parts, name
        for rel in xml(parts, name).iter(f"{PKG_REL}Relationship"):
            target = posixpath.normpath(posixpath.join(folder, rel.get("Target", "")))
            assert target in parts, (name, rel.get("Target"))
    # Elk onderdeel met relaties heeft zijn .rels; elk ander onderdeel hangt ergens aan
    linked = {MAIN[kind].lstrip("/")}
    for name in parts:
        if name.endswith(".rels") and name != "_rels/.rels":
            folder = posixpath.dirname(posixpath.dirname(name))
            for rel in xml(parts, name).iter(f"{PKG_REL}Relationship"):
                linked.add(posixpath.normpath(posixpath.join(folder, rel.get("Target", ""))))
    content = {n for n in parts if not n.endswith(".rels") and n != "[Content_Types].xml"}
    assert content == linked


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("kind", KINDS)
def test_twee_keer_maken_geeft_dezelfde_bytes(kind, locale):
    first = blank.blank(kind, locale)
    blank._build.cache_clear()  # ook zonder het geheugen: echt opnieuw gemaakt
    second = blank.blank(kind, locale)
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 3
            assert info.external_attr == 0o100644 << 16


def test_de_taal_geeft_andere_bytes_en_volgt_de_locale():
    for kind in KINDS:
        assert blank.blank(kind, "nl") != blank.blank(kind, "en")
        assert blank.blank(kind, None) == blank.blank(kind, "nl")
        assert blank.blank(kind, "en-GB") == blank.blank(kind, "EN") == blank.blank(kind, "en")
        assert blank.blank(kind, "de") == blank.blank(kind, "nl")
    assert blank.language("en-US") == "en"
    assert blank.language(" nl-BE ") == "nl"
    assert blank.language("") == "nl"


def test_een_onbekende_soort():
    with pytest.raises(ValueError):
        blank.blank("odt", "nl")  # type: ignore[arg-type]


def test_de_namen_in_de_taal_van_de_gebruiker():
    assert [blank.default_name(k, "nl") for k in KINDS] == [
        "Nieuw document.docx",
        "Nieuw werkblad.xlsx",
        "Nieuwe presentatie.pptx",
    ]
    assert [blank.default_name(k, "en") for k in KINDS] == [
        "New document.docx",
        "New spreadsheet.xlsx",
        "New presentation.pptx",
    ]
    assert blank.default_name("docx", None) == "Nieuw document.docx"


@pytest.mark.parametrize("locale,tag", [("nl", "nl-NL"), ("en", "en-GB")])
def test_docx_a4_een_lege_alinea_de_taal_en_compatibilitymode_15(locale, tag):
    parts = unzip(blank.blank("docx", locale))
    document = xml(parts, "word/document.xml")
    body = document.find(f"{W}body")
    assert body is not None
    paragraphs = body.findall(f"{W}p")
    assert len(paragraphs) == 1 and len(paragraphs[0]) == 0  # één lege alinea
    size = body.find(f"{W}sectPr/{W}pgSz")
    assert size is not None
    assert (size.get(f"{W}w"), size.get(f"{W}h")) == ("11906", "16838")
    margin = body.find(f"{W}sectPr/{W}pgMar")
    assert margin is not None
    assert {margin.get(f"{W}{side}") for side in ("top", "right", "bottom", "left")} == {"1440"}
    lang = xml(parts, "word/styles.xml").find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}lang")
    assert lang is not None and lang.get(f"{W}val") == tag

    # A17: zonder compatibilityMode toont DS bij "Downloaden als docx" de melding over het
    # nieuwe formaat. settings.xml staat in de relaties van document.xml en in de content types.
    settings = xml(parts, "word/settings.xml")
    [mode] = [
        s for s in settings.iter(f"{W}compatSetting") if s.get(f"{W}name") == "compatibilityMode"
    ]
    assert mode.get(f"{W}val") == "15"
    assert mode.get(f"{W}uri") == "http://schemas.microsoft.com/office/word"
    targets = {
        (r.get("Type", "").rsplit("/", 1)[-1], r.get("Target"))
        for r in xml(parts, "word/_rels/document.xml.rels").iter(f"{PKG_REL}Relationship")
    }
    assert ("settings", "settings.xml") in targets
    assert ("styles", "styles.xml") in targets


@pytest.mark.parametrize("locale,sheet", [("nl", "Blad1"), ("en", "Sheet1")])
def test_xlsx_een_blad_met_lege_sheetdata(locale, sheet):
    parts = unzip(blank.blank("xlsx", locale))
    sheets = xml(parts, "xl/workbook.xml").findall(f"{S}sheets/{S}sheet")
    assert [s.get("name") for s in sheets] == [sheet]
    data = xml(parts, "xl/worksheets/sheet1.xml").find(f"{S}sheetData")
    assert data is not None and len(data) == 0
    assert "xl/styles.xml" in parts


def test_pptx_16_9_met_master_layout_dia_en_thema():
    parts = unzip(blank.blank("pptx", "nl"))
    presentation = xml(parts, "ppt/presentation.xml")
    size = presentation.find(f"{P}sldSz")
    assert size is not None and (size.get("cx"), size.get("cy")) == ("12192000", "6858000")
    assert len(presentation.findall(f"{P}sldMasterIdLst/{P}sldMasterId")) == 1
    assert len(presentation.findall(f"{P}sldIdLst/{P}sldId")) == 1
    layout = xml(parts, "ppt/slideLayouts/slideLayout1.xml")
    assert layout.get("type") == "title"
    theme = xml(parts, "ppt/theme/theme1.xml")
    scheme = theme.find(f"{A}themeElements/{A}clrScheme")
    assert scheme is not None and len(scheme) == 12
    fonts = theme.find(f"{A}themeElements/{A}fontScheme")
    assert fonts is not None
    assert {f.tag for f in fonts} == {f"{A}majorFont", f"{A}minorFont"}
    formats = theme.find(f"{A}themeElements/{A}fmtScheme")
    assert formats is not None and all(len(lst) == 3 for lst in formats)
    lang = {e.get("lang") for e in xml(parts, "ppt/slides/slide1.xml").iter(f"{A}endParaRPr")}
    assert lang == {"nl-NL"}


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("kind", KINDS)
def test_de_lezer_van_b21_leest_alle_drie(tmp_path, kind, locale):
    path = tmp_path / f"leeg.{kind}"
    path.write_bytes(blank.blank(kind, locale))
    doc = reader.read(path)["doc"]
    if kind == "docx":
        assert doc == {
            "type": "document",
            "blocks": [{"type": "p", "runs": []}],
            "truncated": False,
        }
    elif kind == "xlsx":
        name = "Blad1" if locale == "nl" else "Sheet1"
        assert doc["type"] == "sheet"
        assert doc["sheets"] == [{"name": name, "rows": [], "truncated": False}]
    else:
        assert doc["type"] == "slides"
        assert doc["size"] == [960.0, 540.0]  # 16:9 in punten
        assert doc["slides"] == [{"title": "", "bullets": []}]


@pytest.mark.anyio
@pytest.mark.parametrize("kind", KINDS)
async def test_de_invoercontrole_laat_alle_drie_door(tmp_path, kind):
    inspect.forget()
    settings = Settings(data_dir=tmp_path, jwt_secret="test" * 8)
    for locale in LOCALES:
        path = Path(tmp_path) / f"leeg-{locale}.{kind}"
        path.write_bytes(blank.blank(kind, locale))
        verdict = await inspect.check(settings, path, kind, f"leeg-{kind}-{locale}")
        assert verdict.ok, verdict.reason
    inspect.forget()
