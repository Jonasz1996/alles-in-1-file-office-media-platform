"""Opgeslagen bestanden van de nep-DS, alleen met zipfile (§3.6).

- docx: een nieuwe docx met eerst de alinea's van het origineel en dan één alinea per gebruiker
  met zijn tekst, zodat een test de inhoud kan lezen (paragraphs);
- xlsx en pptx: het origineel plus customXml/fake.xml met de tekst.
"""

import io
import zipfile
from xml.etree import ElementTree
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FAKE_PART = "customXml/fake.xml"

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
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", document)
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
        target.writestr(FAKE_PART, f"<fake>{escape(text)}</fake>")
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
        z.writestr("changes.json", json.dumps(entries))
    return out.getvalue()
