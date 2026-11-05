"""De rooktest met de echte DS (marker office_real, §4.5 punt 0 van docs/fase-4/bouwontwerp.md).

Draait in de job office van CI tegen DS 10.0.0.1 op digest (e2e/office_ds.sh), en lokaal zo
(docs/fase-4/e2e.md):

    bash e2e/office_ds.sh start && bash e2e/office_ds.sh wait
    cd api && E2E_REAL_DS=http://127.0.0.1:18080 \\
      E2E_REAL_DS_SECRET_FILE=/tmp/office-ds/jwt.secret \\
      uv run --with playwright pytest ../e2e -m office_real

Zonder E2E_REAL_DS wordt hij overgeslagen. Elk pakket voegt zijn eigen test_office_o<N>_real.py
toe.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from office_harness import API_JS, Office, fixture, new_key, serve_host

pytestmark = pytest.mark.office_real


def test_rooktest(real_office: Office, real_browsers) -> None:
    """api.js laadt via /office/ (ook in een browser onder de CSP van de app), /healthcheck is
    true, version zonder token geeft error 6, en een conversie van een docx lukt."""
    office = real_office

    # 1. /healthcheck van DS zelf
    with httpx.Client(trust_env=False, timeout=10) as c:
        health = c.get(f"{office.ds_url}/healthcheck")
    assert (health.status_code, health.text.strip()) == (200, "true")

    # 2. De app vindt hem gezond: de grote controle (versie, licentie, tokens geëist) slaagde
    status = office.status()
    assert (status["enabled"], status["editing"], status["state"]) == (True, True, "ok")

    # 3. version zonder token: error 6 (DS eist tokens); met token: 10.0.0.x
    async def versions(ds):
        return await ds.command("version", signed=False), await ds.command("version")

    unsigned, signed = office.ds(versions)
    assert unsigned.get("error") == 6, unsigned
    assert signed.get("error") == 0 and str(signed.get("version")).startswith("10.0.0"), signed

    # 4. api.js via de proxy: de koppen van de proxy, geen cookie of serverkop van DS
    r = office.server.request("GET", API_JS, expect=200)
    assert "javascript" in r.headers["content-type"]
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    assert "set-cookie" not in r.headers
    assert "server" not in r.headers or "nginx" not in r.headers["server"].lower()
    assert "DocsAPI" in r.text

    # 5. ... en in een browser onder precies de CSP van de app: DocsAPI.DocEditor bestaat, en
    # de bewaking (CSP-schendingen, fouten) ziet niets op de pagina zelf
    page = real_browsers.new_context(service_workers="block").new_page()
    page.goto(serve_host(page, office))
    page.wait_for_function("() => window.__api !== undefined")
    assert page.evaluate("() => window.__api") == "function"

    # 6. Een conversie van een docx naar PDF: DS haalt de bron bij de DocStub (download-JWT),
    # de app herschrijft het adres van de uitvoer naar PLATFORM_OFFICE_URL en haalt hem op
    from app.office import urls

    key = new_key("o0-rook")
    source = office.stub.source(f"{key}.docx", fixture("proef.docx"))
    params = {
        "async": False,
        "filetype": "docx",
        "key": key,
        "outputtype": "pdf",
        "title": "proef.docx",
        "url": source,
    }

    async def convert(ds):
        answer = await ds.convert(**params)
        if not answer.get("endConvert") or not answer.get("fileUrl"):
            return answer, b""
        target = urls.rewrite(office.settings(), answer["fileUrl"], key=key, kind="conv")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "uit.pdf"
            await ds.fetch(target, dest, 50 * 1024**2)
            return answer, dest.read_bytes()

    answer, pdf = office.ds(convert)
    assert answer.get("endConvert") is True, answer
    assert pdf.startswith(b"%PDF-"), pdf[:20]
    assert [d["ok"] for d in office.stub.downloads if key in d["path"]] == [True]
