"""O6 met de echte DS: de instellingen van infra/single/office/local-production-linux.json.

De grenzen van DS (`inputLimits`) mogen niet hoger zijn dan die van de invoercontrole van de app
(Keuze 23, spike A11): 4 MB XML voor OOXML-tekst en -presentaties, 10 MB voor een werkblad en
2 MB voor ODF (`app/office/inspect.py`). DS is zo de tweede verdediging: een bestand dat net
boven de grens van de app zit, weigert DS ook (-10), en een bestand net eronder zet hij gewoon
om. Daarnaast: `aiSettings.enable: false` werkt (`/ai-proxy` weigert).

Deze test stuurt de bestanden rechtstreeks naar DS, langs de app heen: dat mag alleen hier, om
de grens van DS zelf te meten. De app stuurt nooit een bestand naar DS zonder `inspect`; de
test controleert daarom ook dat `inspect` over elk bestand hetzelfde zegt.

DS moet met dat bestand draaien (`OFFICE_DS_CONFIG`, zoals in de job office):

    OFFICE_DS_CONFIG=$PWD/infra/single/office/local-production-linux.json \\
      bash e2e/office_ds.sh start && bash e2e/office_ds.sh wait
    cd api && E2E_REAL_DS=http://127.0.0.1:18080 \\
      E2E_REAL_DS_SECRET_FILE=/tmp/office-ds/jwt.secret \\
      uv run --with playwright pytest ../e2e/test_office_o6_real.py -m office_real
"""

from __future__ import annotations

import hashlib
import io
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import httpx
import pytest

from office_harness import Office, fixture, new_key, run_async

pytestmark = pytest.mark.office_real

MIB = 1024**2
# Zo ver onder of boven de grens; ruim kleiner dan elk verschil dat ertoe doet
MARGIN = 16 * 1024
ZONDER_CONFIG = (
    "draait DS met infra/single/office/local-production-linux.json? Start hem met "
    "OFFICE_DS_CONFIG=$PWD/infra/single/office/local-production-linux.json (docs/fase-4/e2e.md)"
)

OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
ODF_MIME = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
}
FLAT = {"odt": "bron/proef.fodt", "ods": "bron/proef.fods", "odp": "bron/proef.fodp"}
OOXML = {"docx": "proef.docx", "xlsx": "proef.xlsx", "pptx": "proef.pptx"}
PARTS = ["content.xml", "styles.xml", "meta.xml"]

# De grens van de app per soort (app/office/inspect.py), en dus ook die van DS
LIMITS = {
    "docx": 4 * MIB,
    "pptx": 4 * MIB,
    "xlsx": 10 * MIB,
    "odt": 2 * MIB,
    "ods": 2 * MIB,
    "odp": 2 * MIB,
}


def _odf(ext: str) -> bytes:
    """Een gewone (ingepakte) ODF van de platte bron in fixtures/office/bron: content.xml,
    styles.xml en meta.xml, met dezelfde voorvoegsels als de bron."""
    flat = fixture(FLAT[ext])
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(flat), events=["start-ns"]):
        ET.register_namespace(prefix, uri)
    root = ET.fromstring(flat)

    def part(tag: str, names: list[str]) -> bytes:
        doc = ET.Element(f"{{{OFFICE_NS}}}{tag}", {f"{{{OFFICE_NS}}}version": "1.3"})
        for name in names:
            doc.extend(root.findall(f"{{{OFFICE_NS}}}{name}"))
        return ET.tostring(doc, encoding="UTF-8", xml_declaration=True)

    content = part("document-content", ["font-face-decls", "automatic-styles", "body"])
    styles = part(
        "document-styles", ["font-face-decls", "styles", "automatic-styles", "master-styles"]
    )
    meta = part("document-meta", ["meta"])
    ET.register_namespace("manifest", MANIFEST_NS)
    manifest = ET.Element(f"{{{MANIFEST_NS}}}manifest", {f"{{{MANIFEST_NS}}}version": "1.3"})
    for path, kind in [("/", ODF_MIME[ext]), *[(n, "text/xml") for n in PARTS]]:
        ET.SubElement(
            manifest,
            f"{{{MANIFEST_NS}}}file-entry",
            {f"{{{MANIFEST_NS}}}full-path": path, f"{{{MANIFEST_NS}}}media-type": kind},
        )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), ODF_MIME[ext], compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/manifest.xml", ET.tostring(manifest, "UTF-8", xml_declaration=True))
        z.writestr("content.xml", content)
        z.writestr("styles.xml", styles)
        z.writestr("meta.xml", meta)
    return out.getvalue()


def base(ext: str) -> bytes:
    return fixture(OOXML[ext]) if ext in OOXML else _odf(ext)


def padded(data: bytes, pad: int) -> bytes:
    """Het document met een los onderdeel opvulling.xml van precies pad bytes erbij. Geen ander
    onderdeel verwijst ernaar: de editor en de converter lezen het niet, maar de grens van DS
    (`*.xml`, uitgepakt) en die van de app tellen het wel mee. De inhoud is elke keer anders."""
    head = b'<?xml version="1.0" encoding="UTF-8"?>\n<opvulling xmlns="urn:platform:o6">\n'
    tail = b"</opvulling>\n"
    line = f"<r>{uuid.uuid4().hex}</r>\n".encode()
    body = (line * (pad // len(line) + 1))[: pad - len(head) - len(tail)]
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as z:
        for info in src.infolist():
            z.writestr(info, src.read(info))
        z.writestr("opvulling.xml", head + body + tail, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


def unpacked(data: bytes) -> int:
    """Alles uitgepakt samen: nooit minder dan wat de app of DS als XML telt."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return sum(i.file_size for i in z.infolist())


def under(ext: str) -> bytes:
    """Net onder de grens: alles samen uitgepakt MARGIN kleiner dan de grens."""
    data = base(ext)
    return padded(data, LIMITS[ext] - MARGIN - unpacked(data) - 200)


def over(ext: str) -> bytes:
    """Net boven de grens: de opvulling alleen is al MARGIN groter dan de grens."""
    return padded(base(ext), LIMITS[ext] + MARGIN)


def _verdict(office: Office, data: bytes, ext: str) -> tuple[bool, str | None]:
    """Wat de invoercontrole van de app over dit bestand zegt."""
    from app.office import inspect

    async def go() -> tuple[bool, str | None]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"bestand.{ext}"
            path.write_bytes(data)
            sha = hashlib.sha256(data).hexdigest()
            v = await inspect.check(office.settings(), path, ext, sha)
            return v.ok, v.reason

    return run_async(go)


def _convert(office: Office, data: bytes, ext: str, tag: str) -> tuple[dict, bytes]:
    """Rechtstreeks naar DS (/converter, naar PDF); het antwoord en de PDF (leeg bij een fout)."""
    from app.office import urls

    key = new_key(tag)
    source = office.stub.source(f"{key}.{ext}", data)
    params = {
        "async": False,
        "filetype": ext,
        "key": key,
        "outputtype": "pdf",
        "title": f"grens.{ext}",
        "url": source,
    }

    async def go(ds):
        answer = await ds.convert(**params)
        if not answer.get("endConvert") or not answer.get("fileUrl"):
            return answer, b""
        target = urls.rewrite(office.settings(), answer["fileUrl"], key=key, kind="conv")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "uit.pdf"
            await ds.fetch(target, dest, 50 * MIB)
            return answer, dest.read_bytes()

    return office.ds(go)


@pytest.mark.parametrize("ext", ["docx", "pptx", "xlsx", "odt", "ods", "odp"])
def test_grens_van_ds_gelijk_aan_die_van_de_app(real_office: Office, ext: str) -> None:
    """Net onder de grens: de app laat hem door en DS maakt er een PDF van. Net erboven: de app
    weigert (too_big) en DS ook (-10). DS gaat dus niet hoger dan de app (Keuze 23)."""
    office = real_office

    small = under(ext)
    assert unpacked(small) <= LIMITS[ext] - MARGIN
    assert _verdict(office, small, ext) == (True, None)
    answer, pdf = _convert(office, small, ext, f"o6-onder-{ext}")
    assert answer.get("endConvert") is True, (answer, ZONDER_CONFIG)
    assert pdf.startswith(b"%PDF-"), pdf[:20]

    big = over(ext)
    assert _verdict(office, big, ext) == (False, "too_big")
    answer, _ = _convert(office, big, ext, f"o6-boven-{ext}")
    assert answer.get("error") == -10, (answer, ZONDER_CONFIG)


def test_gewoon_document_en_ai_uit(real_office: Office) -> None:
    """Een gewoon document zet DS met deze instellingen om, en de AI-proxy van DS weigert
    (`aiSettings.enable: false`)."""
    office = real_office
    answer, pdf = _convert(office, fixture("proef.docx"), "docx", "o6-gewoon")
    assert answer.get("endConvert") is True, answer
    assert pdf.startswith(b"%PDF-"), pdf[:20]

    with httpx.Client(trust_env=False, timeout=10) as c:
        r = c.post(f"{office.ds_url}/ai-proxy", json={})
    assert r.status_code == 403, r.text
    assert "AI is disabled" in r.text, (r.text, ZONDER_CONFIG)
