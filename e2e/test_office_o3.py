"""Office O3 met de nep-DS (marker office, §4.4 punt 4 en 8 van docs/fase-4/bouwontwerp.md).

Het voorbeeld met opmaak zonder de editor: een docx en een doc uploaden, de app zet ze om via
/converter van de nep-DS (die de bron bij de interne luisteraar van de app ophaalt), de
thumbnail krijgt een nieuw adres en de viewer toont echte pagina's. Geen enkele editorverbinding
(geen socket, geen download van een document voor de editor) en geen verkeer naar /office/ uit
de browser. Een odt-bom komt nooit bij de nep-DS. Achter een publieke link ook pagina's, zonder
Bewerken; een upload via een uploadlink wordt pas omgezet als een ingelogde lezer erom vraagt
(met alleen het toetsenbord).
"""

from __future__ import annotations

import base64
import hashlib
import io
import time
import zipfile
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import expect

from conftest import Account, Server, create_user
from office_harness import Office, fixture

pytestmark = pytest.mark.office

# Wat de nep-DS logt als een editor verbinding maakt: de socket, het ophalen van het document
# voor de editor en een wijziging
EDITOR_KINDS = {"socket", "download", "change", "callback"}
CONVERT_KINDS = {"convert", "convert_source", "convert_file"}
MB = 1024 * 1024


def odt_bomb(megabytes: int = 1024) -> bytes:
    """Een odt van een paar MB met een gigabyte nullen erin."""
    out = io.BytesIO()
    zeros = bytes(MB)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", "<office:document-content/>")
        with archive.open("Pictures/nul.bin", "w") as target:
            for _ in range(megabytes):
                target.write(zeros)
    return out.getvalue()


def until[T](what: str, fn: Callable[[], T | None], seconds: float = 60) -> T:
    """Wacht (begrensd) tot fn iets geeft."""
    end = time.monotonic() + seconds
    while True:
        got = fn()
        if got:
            return got
        if time.monotonic() > end:
            raise AssertionError(f"{what}: niet binnen {seconds} s")
        time.sleep(0.25)


def mkdir(server: Server, owner: Account, name: str) -> str:
    return server.call(
        owner, "POST", f"/api/v1/files/{owner.root_id}/folders", json={"name": name}, expect=201
    ).json()["id"]


def upload(server: Server, owner: Account, folder: str, name: str, data: bytes) -> str:
    return server.call(
        owner,
        "PUT",
        f"/api/v1/files/{folder}/upload",
        params={"name": name},
        content=data,
        expect=201,
    ).json()["id"]


def detail(server: Server, account: Account, node_id: str) -> dict:
    return server.call(account, "GET", f"/api/v1/files/{node_id}/preview", expect=200).json()


def thumb(server: Server, account: Account, node_id: str) -> str | None:
    got = server.call(account, "POST", "/api/v1/previews", json={"node_ids": [node_id]}, expect=200)
    return got.json()[node_id]["thumb"]


def wait_state(server: Server, account: Account, node_id: str, state: str, seconds=60) -> dict:
    """Tot de preview klaar is (of geen kan krijgen) en meta.pdf_state deze staat is."""

    def check() -> dict | None:
        d = detail(server, account, node_id)
        return d if d["state"] != "pending" and d["meta"].get("pdf_state") == state else None

    return until(f"pdf_state {state} voor {node_id}", check, seconds)


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fake_log(office: Office, since: float) -> list[dict]:
    assert office.fake is not None
    return [e for e in office.fake.log() if e["t"] >= since]


def converted(office: Office, since: float, sha: str) -> list[dict]:
    """De conversies van deze inhoud die de nep-DS zag (de key is p<v>-<sha>)."""
    return [
        e
        for e in fake_log(office, since)
        if e["kind"] in CONVERT_KINDS and sha in str(e.get("key") or "")
    ]


def watch_office(page: Any) -> list[str]:
    """Alles wat de pagina naar /office/ stuurt (verzoeken en sockets): moet leeg blijven."""
    seen: list[str] = []
    page.on("websocket", lambda ws: seen.append(f"socket {ws.url}"))
    page.on(
        "request",
        lambda r: urlsplit(r.url).path.startswith("/office/") and seen.append(f"get {r.url}"),
    )
    return seen


def wait_pages(page: Any, part: str = "/pages/") -> None:
    """Tot er een pagina als afbeelding van de server staat."""
    page.wait_for_function(
        """(part) => [...document.querySelectorAll("dialog .page img")].some(
          (i) => i.complete && i.naturalWidth > 0 && (i.currentSrc || i.src).includes(part))""",
        arg=part,
    )


def test_voorbeeld_zonder_editor(office: Office, office_browsers) -> None:
    """§4.4 punt 4: een docx en een doc → nieuwe thumbnail en pagina's in de viewer, zonder
    editorverbinding; de odt-bom komt nooit bij de nep-DS."""
    assert office.fake is not None
    server = office.server
    t0 = time.time()
    owner = create_user(server, office.admin, name="Voorbeeld O3")
    folder = mkdir(server, owner, "Kantoor")
    docx, doc, bomb = fixture("proef.docx"), fixture("proef.doc"), odt_bomb()

    # De nep-DS houdt de conversies even vast: dan is de thumbnail van B21 er nog
    office.fake.control("convert", {"hold": True})
    try:
        ids = {
            "proef.docx": upload(server, owner, folder, "proef.docx", docx),
            "proef.doc": upload(server, owner, folder, "proef.doc", doc),
            "bom.odt": upload(server, owner, folder, "bom.odt", bomb),
        }
        wait_state(server, owner, ids["proef.docx"], "pending")
        before = {name: thumb(server, owner, ids[name]) for name in ("proef.docx", "proef.doc")}
        # B21 maakt voor een kantoordocument geen thumbnail (alleen doc.json); een eerdere zou
        # geen v hebben
        assert all(t is None or "&v=" not in t for t in before.values()), before
        until("de nep-DS haalt de bron op", lambda: converted(office, t0, sha_of(docx)), 30)
    finally:
        office.fake.control("convert", {"hold": False})

    for name in ("proef.docx", "proef.doc"):
        wait_state(server, owner, ids[name], "ready")
        # De thumbnails maakt de taak net na de PDF (pagina 1, in het renderproces)
        after = until(f"thumbnail van {name}", lambda n=name: thumb(server, owner, ids[n]), 30)
        assert "&v=1&" in after and after != before[name], (name, before[name], after)
    # De bom: nooit bij de nep-DS, en zo gemarkeerd
    assert wait_state(server, owner, ids["bom.odt"], "unsafe")
    assert converted(office, t0, sha_of(bomb)) == []
    # De bron haalde de nep-DS bij de interne luisteraar, met het token
    sources = [e for e in fake_log(office, t0) if e["kind"] == "convert_source"]
    assert sources and all(e.get("http") == 200 for e in sources), sources

    context = office_browsers.context_as(owner, service_workers="block")
    page = context.new_page()
    traffic = watch_office(page)
    for name, ext in (("proef.docx", "docx"), ("proef.doc", "doc")):
        page.goto(f"/map/{folder}?bestand={ids[name]}")
        viewer = page.get_by_role("dialog", name=name, exact=True)
        expect(viewer).to_be_visible()
        expect(viewer.locator(".pages-note")).to_contain_text("Voorbeeld")
        wait_pages(page)
        expect(viewer.get_by_role("spinbutton")).to_have_value("1")
        expect(viewer.get_by_text("Vereenvoudigd voorbeeld zonder opmaak")).to_have_count(0)
        with page.expect_download() as download:
            viewer.get_by_role("button", name="Downloaden als PDF", exact=True).click()
        assert download.value.suggested_filename == "proef.pdf", ext
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog")).to_have_count(0)
    assert traffic == []

    # Geen enkele editorverbinding in de log van de nep-DS
    assert [e for e in fake_log(office, t0) if e["kind"] in EDITOR_KINDS] == []


def _upload_link(server: Server, owner: Account, folder: str, name: str, data: bytes) -> None:
    """Een upload via een uploadlink (tus, zonder login)."""
    link = server.call(
        owner, "POST", f"/api/v1/files/{folder}/links", json={"permission": "upload"}, expect=201
    ).json()
    token = link["link"]["url"].rstrip("/").rsplit("/", 1)[-1]
    key = server.request("POST", "/api/v1/public/open", json={"token": token}, expect=200).json()[
        "key"
    ]
    auth = {"Authorization": f"Bearer {key}", "Tus-Resumable": "1.0.0"}
    meta = f"filename {base64.b64encode(name.encode()).decode()}"
    r = server.request(
        "POST",
        "/api/v1/public/tus",
        headers={**auth, "Upload-Length": str(len(data)), "Upload-Metadata": meta},
        expect=201,
    )
    location = r.headers["location"]
    server.request(
        "PATCH",
        location,
        content=data,
        headers={
            **auth,
            "Content-Type": "application/offset+octet-stream",
            "Upload-Offset": "0",
        },
        expect=204,
    )


def child_id(server: Server, owner: Account, folder: str, name: str) -> str | None:
    children = server.call(owner, "GET", f"/api/v1/files/{folder}/children", expect=200).json()
    return next((c["id"] for c in children["children"] if c["name"] == name), None)


def test_publieke_link_en_uploadlink(office: Office, office_browsers) -> None:
    """§4.4 punt 8: achter een publieke link pagina's, geen Bewerken en geen /office/. Een
    upload via een uploadlink wacht tot een ingelogde lezer erom vraagt."""
    assert office.fake is not None
    server = office.server
    t0 = time.time()
    owner = create_user(server, office.admin, name="Eigenaar O3")
    reader = create_user(server, office.admin, name="Lezer O3")
    folder = mkdir(server, owner, "Gedeeld O3")
    docx = fixture("proef.docx")
    node = upload(server, owner, folder, "proef.docx", docx)
    wait_state(server, owner, node, "ready")

    # De publieke leeslink
    link = server.call(
        owner, "POST", f"/api/v1/files/{folder}/links", json={"permission": "read"}, expect=201
    ).json()
    token = link["link"]["url"].rstrip("/").rsplit("/", 1)[-1]
    page = office_browsers.new_context(service_workers="block").new_page()
    traffic = watch_office(page)
    page.goto(f"/s/{token}")
    expect(page.get_by_role("heading", level=1)).to_have_text("Gedeeld O3")
    page.get_by_role("button", name="proef.docx", exact=True).click()
    viewer = page.get_by_role("dialog", name="proef.docx", exact=True)
    expect(viewer).to_be_visible()
    wait_pages(page, "/api/v1/public/pages/")
    expect(viewer.locator(".pages-note")).to_contain_text("Voorbeeld")
    expect(viewer.get_by_role("button", name="Bewerken")).to_have_count(0)
    expect(viewer.get_by_role("button", name="Downloaden als PDF")).to_have_count(0)
    # Met de sleutel van de link start niemand een conversie
    key = server.request("POST", "/api/v1/public/open", json={"token": token}, expect=200).json()[
        "key"
    ]
    server.request("POST", f"/api/v1/office/files/{node}/convert", token=key, expect=401)
    assert traffic == []

    # Een upload via een uploadlink in een map die de lezer mag lezen
    inbox = mkdir(server, owner, "Inbox O3")
    server.call(
        owner,
        "POST",
        f"/api/v1/files/{inbox}/shares",
        json={"user_id": reader.id, "permission": "read"},
        expect=201,
    )
    data = _with_extra(docx)  # een andere inhoud: geen PDF die er al is
    _upload_link(server, owner, inbox, "van-buiten.docx", data)
    uploaded = until(
        "de upload staat in de map", lambda: child_id(server, owner, inbox, "van-buiten.docx")
    )
    wait_state(server, owner, uploaded, "ask")
    time.sleep(3)  # de taken lopen elke seconde: er komt geen conversie vanzelf
    assert detail(server, owner, uploaded)["meta"]["pdf_state"] == "ask"
    assert converted(office, t0, sha_of(data)) == []

    # De lezer vraagt het, met alleen het toetsenbord
    context = office_browsers.context_as(reader, service_workers="block")
    page = context.new_page()
    traffic = watch_office(page)
    page.goto(f"/map/{inbox}?bestand={uploaded}")
    viewer = page.get_by_role("dialog", name="van-buiten.docx", exact=True)
    expect(viewer).to_be_visible()
    ask = viewer.get_by_role("button", name="Voorbeeld met opmaak maken", exact=True)
    expect(ask).to_be_visible()
    expect(viewer.get_by_text("Vereenvoudigd voorbeeld zonder opmaak")).to_be_visible()
    for _ in range(40):
        if ask.evaluate("b => b === document.activeElement"):
            break
        page.keyboard.press("Tab")
    else:
        raise AssertionError("de knop is niet met Tab te bereiken")
    page.keyboard.press("Enter")
    expect(viewer.locator(".pages-note")).to_contain_text("Voorbeeld", timeout=30_000)
    wait_pages(page)
    assert converted(office, t0, sha_of(data)), "de nep-DS zag geen conversie"
    assert detail(server, owner, uploaded)["meta"]["pdf_state"] == "ready"
    assert traffic == []
    assert [e for e in fake_log(office, t0) if e["kind"] in EDITOR_KINDS] == []


def _with_extra(data: bytes) -> bytes:
    """Dezelfde docx met een extra onderdeel: andere bytes, dezelfde tekst."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr("docProps/extra.xml", "<extra/>")
    return out.getvalue()


def test_zonder_office_geen_omzetting(server: Server, admin: Account) -> None:
    """De gewone e2e-server (zonder office): geen pdf_state, convert en pdf geven office_off,
    en de bron-route bestaat niet op de publieke poort."""
    owner = create_user(server, admin, name="Zonder office O3")
    node = upload(server, owner, owner.root_id, "los.docx", fixture("proef.docx"))
    until("preview", lambda: detail(server, owner, node)["state"] != "pending")
    assert "pdf_state" not in detail(server, owner, node)["meta"]
    for path in (f"/api/v1/office/files/{node}/convert", f"/api/v1/office/files/{node}/pdf"):
        r = server.call(owner, "POST", path, expect=503)
        assert r.json()["detail"]["code"] == "office_off"
    with httpx.Client(base_url=server.url, trust_env=False) as client:
        assert client.get("/internal/office/src/abc").status_code == 404
