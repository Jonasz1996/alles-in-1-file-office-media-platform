"""O1 (toegang) met de echte DS (marker office_real; §4.5 punt 5 van docs/fase-4/bouwontwerp.md,
en de echte editor via /office/ over http en over https).

Punt 10 van §4.4 tegen de echte DS: de editorpagina als pagina is 404 (zonder en met cookie);
zonder cookie geen DATA-pad en geen upgrade; een socket op het pad van key A met het token van
key B gaat dicht na het eerste pakket; en na het intrekken van de deling lukt een nieuwe socket
met het oude sessietoken niet (geweigerd bij de upgrade).

Draait in de job office van CI, en lokaal zo (docs/fase-4/e2e.md):

    bash e2e/office_ds.sh start && bash e2e/office_ds.sh wait
    cd web && npm run build
    cd api && E2E_REAL_DS=http://127.0.0.1:18080 \\
      E2E_REAL_DS_SECRET_FILE=/tmp/office-ds/jwt.secret \\
      uv run --with playwright pytest ../e2e -m office_real

Zonder E2E_REAL_DS wordt hij overgeslagen.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import expect

from conftest import Browsers
from office_harness import Office, fixture, type_in_editor
from test_office_o1 import (
    DROPPED,
    TLS_DOMAIN,
    Opened,
    gate_code,
    login,
    office_cookie,
    open_editor_page,
    probe,
    session_token,
    socket_path,
    upgrade_status,
)
from test_office_o2 import duo
from test_office_o2_real import info_users, ready, text, uid, wait_until

pytestmark = pytest.mark.office_real


def editor_connect(opened: Opened) -> dict:
    """Het eerste CONNECT-pakket dat de echte editor zelf stuurde (met het config-token)."""
    for raw in opened.sent:
        if not raw.startswith("40{"):
            continue
        packet = json.loads(raw[2:])
        if isinstance(packet, dict) and isinstance(packet.get("token"), str):
            return packet
    raise AssertionError(f"geen CONNECT van de editor in {len(opened.sent)} frames")


def with_token(packet: dict, token: str) -> str:
    """Hetzelfde CONNECT-pakket, met een ander token op elke plek waar de editor er een zet."""
    out = json.loads(json.dumps(packet))
    out["token"] = token
    out.pop("session", None)
    data = out.get("data")
    if isinstance(data, dict):
        data["jwtOpen"] = token
        data.pop("jwtSession", None)
    return "40" + json.dumps(out, separators=(",", ":"))


def test_afscherming_met_de_echte_ds(real_office: Office, real_browsers) -> None:
    """§4.5 punt 5: punt 10 van §4.4 tegen de echte DS, met de echte editor van Bram open."""
    office = real_office
    server = office.server
    d = duo(office)
    other = server.call(
        d.anna,
        "PUT",
        f"/api/v1/files/{d.folder}/upload",
        params={"name": "ander.docx"},
        content=fixture("proef.docx"),
        expect=201,
    ).json()["id"]
    bram = open_editor_page(login(real_browsers, d.bram), d.node, wait=ready)
    src = bram.editor_url()
    assert urlsplit(src).path.endswith("/web-apps/apps/documenteditor/main/index.html"), src
    assert bram.responses.get(src) == 200
    path = socket_path(bram.version(), bram.key)
    data_url = bram.data_urls()[0]
    cookie = office_cookie(bram.context)
    assert cookie is not None

    # De editorpagina als pagina: 404 zonder cookie, en ook met het cookie van Bram
    fresh = real_browsers.new_context(service_workers="block")
    page = fresh.new_page()
    r = page.goto(src)
    assert r is not None and r.status == 404
    own = bram.context.new_page()
    r = own.goto(src)
    assert r is not None and r.status == 404
    headers = r.request.all_headers()
    assert headers.get("sec-fetch-dest") == "document" and "office=" in headers.get("cookie", "")
    own.close()

    # Zonder cookie geen dynamisch pad, ook niet vanuit een pagina van de app
    r = fresh.request.get(data_url)
    assert r.status == 401 and gate_code(r) == "office_gate"
    assert upgrade_status(server.url, path, None) == 401
    page.goto("/")
    out = probe(page, path)
    assert out["opened"] is False and out["frames"] == [], out
    assert bram.context.request.get(data_url).status == 200

    # Een socket op het pad van A: het eigen CONNECT-pakket van de editor komt verbonden; met
    # het config-token van B (een tweede sessie van Bram) alleen het "0"-pakket, dan dicht
    token_b = server.call(
        d.bram, "POST", f"/api/v1/office/files/{other}/session", json={}, expect=200
    ).json()["config"]["token"]
    connect = editor_connect(bram)
    good = probe(bram.page, path, first="40" + json.dumps(connect), until="40")
    assert good["opened"] is True and any(f.startswith("40{") for f in good["frames"]), good
    bad = probe(bram.page, path, first=with_token(connect, token_b))
    assert bad["opened"] is True, bad
    assert len(bad["frames"]) == 1 and bad["frames"][0].startswith("0{"), bad
    assert bad["code"] == 1006, bad

    # De deling ingetrokken: Bram is eruit, en een nieuwe socket met zijn oude sessietoken komt
    # niet door de upgrade
    token = session_token(office, bram)
    server.call(d.anna, "DELETE", f"/api/v1/shares/{d.share}", expect=204)
    expect(bram.page.get_by_role("alert")).to_have_text(DROPPED, timeout=20_000)
    session = {"data": {**connect["data"], "jwtSession": token}, "session": token}
    session["data"].pop("jwtOpen", None)
    out = probe(bram.page, path, first="40" + json.dumps(session))
    assert out["opened"] is False and out["frames"] == [], out
    assert upgrade_status(server.url, path, cookie["value"]) == 401
    r = bram.context.request.get(data_url)
    assert r.status == 401 and gate_code(r) == "office_gate"
    me = uid(office, d.bram)
    wait_until(lambda: me not in info_users(office, bram.key), 30, "Bram niet meer bij DS")


def test_de_echte_editor_over_http_en_https(browser: Any, tmp_path_factory) -> None:
    """Een eigen server tegen de echte DS met een zelfgetekend certificaat: Anna bewerkt over
    http, Bram over https (de editor, de bestanden en de socket via https://…/office/ en
    wss:). Ze zitten op dezelfde key, en wat Bram typt staat na Nu opslaan in de docx."""
    url = os.environ.get("E2E_REAL_DS")
    if not url:
        pytest.skip("geen echte DS (E2E_REAL_DS staat niet; zie docs/fase-4/e2e.md)")
    secret_file = os.environ.get("E2E_REAL_DS_SECRET_FILE")
    if not secret_file or not Path(secret_file).is_file():
        pytest.fail("E2E_REAL_DS staat, maar E2E_REAL_DS_SECRET_FILE wijst niet naar een bestand")
    from tests.test_certs import write_cert

    from app.certs import CertManager
    from office_harness import start_real_office

    secret = Path(secret_file).read_text().strip()
    office = start_real_office(tmp_path_factory.mktemp("office-echt-https"), url, secret)
    browsers = Browsers(browser, office.server, office_frames="record")
    try:
        write_cert(CertManager(office.settings()), TLS_DOMAIN)
        office.server.restart()
        office.wait_state("ok", 60)
        secure = f"https://127.0.0.1:{office.server.https_port}"

        def https_up() -> bool:
            try:
                with httpx.Client(verify=False, trust_env=False, timeout=2) as c:
                    return c.get(f"{secure}/health").status_code == 200
            except httpx.HTTPError:
                return False

        wait_until(https_up, 30, "https op de server")
        d = duo(office)
        a = open_editor_page(login(browsers, d.anna), d.node, wait=ready)
        context = browsers.new_context(
            base_url=secure, ignore_https_errors=True, service_workers="block"
        )
        r = context.request.post(
            f"{secure}/api/v1/auth/login",
            data={"email": d.bram.email, "password": d.bram.password},
        )
        assert r.ok, r.text()
        b = open_editor_page(context, d.node, wait=ready)
        assert b.key == a.key
        assert b.editor_url().startswith(f"{secure}/office/")
        assert any(
            u.startswith(f"wss://127.0.0.1:{office.server.https_port}/office/") for u in b.sockets
        ), b.sockets
        assert any(u.startswith(f"{secure}/office/cache/files/data/") for u in b.data_urls())
        cookie = office_cookie(b.context)
        assert cookie is not None and cookie["secure"] is True
        for page in (a.page, b.page):
            expect(page.locator(".editor-status")).to_have_text(
                "Bewerken met 1 ander", timeout=30_000
            )
        users = wait_until(lambda: info_users(office, a.key), 30, "info met gebruikers")
        assert sorted(users) == sorted([uid(office, d.anna), uid(office, d.bram)]), users

        word = f"https{secrets.token_hex(4)}"
        type_in_editor(b.page, f"Bram {word}")
        b.page.wait_for_timeout(2000)  # de wijziging bij DS
        b.page.get_by_role("button", name="Nu opslaan", exact=True).click()
        wait_until(lambda: word in text(office, d.anna, d.node), 60, "het woord van Bram")
        for opened in (a, b):
            opened.page.get_by_role("button", name="Sluiten", exact=True).click()
            expect(opened.page).to_have_url(re.compile(rf"/map/{d.folder}$"))
    finally:
        problems = browsers.close_all()
        if browsers.office_records:
            out = tmp_path_factory.getbasetemp() / "o1-echt-https-office-frames.txt"
            out.write_text("\n".join(browsers.office_records) + "\n")
        office.stop()
    if problems:
        pytest.fail("Problemen in de browser:\n" + "\n".join(problems), pytrace=False)
