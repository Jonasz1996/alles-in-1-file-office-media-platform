"""Opgeslagen bestanden van de nep-DS, alleen met zipfile (§3.6).

- docx: een nieuwe docx met eerst de alinea's van het origineel en dan één alinea per gebruiker
  met zijn tekst, zodat een test de inhoud kan lezen (paragraphs);
- xlsx en pptx: het origineel plus customXml/fake.xml met de tekst.

Elk deel krijgt een vaste datum in de zip: dezelfde inhoud geeft zo altijd dezelfde bytes (en
sha). Met de klok (writestr met een naam, 2 s per stap) faalde
test_place_file_exact.py::test_squash_alleen_bij_de_juiste_sha af en toe, als twee keer dezelfde
inhoud aan weerszijden van een grens van 2 s viel (samenvoegen fase 5, golf 3).
"""

import io
import zipfile
from xml.etree import ElementTree
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FAKE_PART = "customXml/fake.xml"
DATE = (1980, 1, 1, 0, 0, 0)  # de vroegste datum van zip

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
    'relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _put(z: zipfile.ZipFile, name: str, data: str | bytes) -> None:
    """Een deel met de vaste datum (niet de klok), gecomprimeerd."""
    z.writestr(zipfile.ZipInfo(name, date_time=DATE), data, zipfile.ZIP_DEFLATED)


def make_docx(paragraphs: list[str]) -> bytes:
    """Een kleine geldige docx met deze alinea's."""
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(p)}</w:t></w:r></w:p>' for p in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        _put(z, "[Content_Types].xml", CONTENT_TYPES)
        _put(z, "_rels/.rels", RELS)
        _put(z, "word/document.xml", document)
    return out.getvalue()


def paragraphs(data: bytes) -> list[str]:
    """De tekst van elke alinea van een docx; [] als het geen leesbare docx is."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > 8 * 1024 * 1024:
                return []
            root = ElementTree.fromstring(z.read(info))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, OSError):
        return []
    out = []
    for p in root.iter(f"{{{W}}}p"):
        out.append("".join(t.text or "" for t in p.iter(f"{{{W}}}t")))
    return out


def with_text(original: bytes, text: str) -> bytes:
    """Het origineel (xlsx, pptx) plus customXml/fake.xml met de tekst."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(original)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            if info.filename != FAKE_PART:
                target.writestr(info, source.read(info))
        _put(target, FAKE_PART, f"<fake>{escape(text)}</fake>")
    return out.getvalue()


def fake_text(data: bytes) -> str | None:
    """De tekst uit customXml/fake.xml, of None."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = ElementTree.fromstring(z.read(FAKE_PART))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, OSError):
        return None
    return root.text or ""


def changes_zip(entries: list[dict]) -> bytes:
    """changes.zip: de wijzigingen als JSON (de inhoud leest niemand)."""
    import json

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        _put(z, "changes.json", json.dumps(entries))
    return out.getvalue()
