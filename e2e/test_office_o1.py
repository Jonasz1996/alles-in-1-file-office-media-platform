"""O1 (toegang) met de nep-DS en de editor van O2 (marker office; §4.4 punten 3 en 10 van
docs/fase-4/bouwontwerp.md, en de editor over http en over https).

Alles gaat door de echte server en de echte poort: het poort-cookie office uit POST …/session,
de proxy (elk pad een klasse, met de controle van Keuze 7) en de tunnel (Origin, cookie en recht
vóór de upgrade, daarna de StreamGuard en de controle elke 15 s). Het harnas zet
PLATFORM_OFFICE_DEV_UNGATED niet meer, en de vlag doet sinds O1 niets meer (test_office_gate.py
bewijst het): hier geldt de echte poort.

De hulpjes (open_editor_page, probe, upgrade_status, session_token) gebruikt ook
test_office_o1_real.py, tegen de echte DS.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import ssl
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import BrowserContext, Page, expect

from conftest import Account, Browsers, create_user
from office_harness import Office, fake_info, fixture, frame_editor, type_in_editor
from test_office_o2 import duo, is_session, ready, uid, wait_until

pytestmark = pytest.mark.office

DROPPED = "Je hebt geen toegang meer; de editor is gesloten."
# Het domein van het zelfgetekende certificaat (de browser negeert de fout, de test ook)
TLS_DOMAIN = "platform.test"

# Een socket naar /office/ vanuit de pagina zelf (zoals een script in de pagina): dezelfde
# origin, dus met het poort-cookie. first gaat weg na het "0"-pakket van engine.io; until sluit
# de socket zelf bij een frame dat zo begint. Geeft {opened, frames, code}.
PROBE_JS = r"""
([path, first, until, wait]) => new Promise((resolve) => {
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  const out = { opened: false, frames: [], code: null };
  let sock;
  try {
    sock = new WebSocket(scheme + location.host + path);
  } catch (e) {
    out.code = String(e);
    resolve(out);
    return;
  }
  const timer = setTimeout(() => {
    out.code = "wacht";
    sock.close();
    resolve(out);
  }, wait);
  sock.onopen = () => { out.opened = true; };
  sock.onmessage = (ev) => {
    const text = String(ev.data);
    out.frames.push(text);
    if (first && out.frames.length === 1 && text.startsWith("0")) sock.send(first);
    if (until && text.startsWith(until)) sock.close(1000);
  };
  sock.onclose = (ev) => {
    clearTimeout(timer);
    out.code = ev.code;
    resolve(out);
  };
})
"""


@dataclass
class Opened:
    """Een editor die via /bewerken/<id> open is, met wat de context zag."""

    context: BrowserContext
    page: Page
    key: str
    config: dict
    requests: list[str] = field(default_factory=list)
    responses: dict[str, int] = field(default_factory=dict)
    sockets: list[str] = field(default_factory=list)
    frames: list[str] = field(default_factory=list)  # van DS naar de browser
    sent: list[str] = field(default_factory=list)  # van de browser naar DS

    def data_urls(self) -> list[str]:
        """De DATA-paden (cache/files/data) die de editor voor zijn key ophaalde."""
        return [u for u in self.requests if f"/office/cache/files/data/{self.key}" in u]

    def editor_url(self) -> str:
        """Het adres van de editorpagina in de iframe (/office/<V>/web-apps/…/index.html?…)."""
        return frame_editor(self.page).url

    def version(self) -> str:
        """Het versiesegment <V> uit het adres van de editorpagina."""
        return urlsplit(self.editor_url()).path.split("/")[2]


def watch(context: BrowserContext, page: Page, opened: Opened) -> None:
    """Verzoeken en antwoorden van de context, en de sockets van de pagina met hun frames
    (ook die van de iframe van de editor) bijhouden."""
    context.on("request", lambda r: opened.requests.append(r.url))
    context.on("response", lambda r: opened.responses.__setitem__(r.url, r.status))

    def on_socket(ws: Any) -> None:
        opened.sockets.append(ws.url)

        def into(frames: list[str]) -> Any:
            return lambda payload: frames.append(payload) if isinstance(payload, str) else None

        ws.on("framereceived", into(opened.frames))
        ws.on("framesent", into(opened.sent))

    page.on("websocket", on_socket)


def open_editor_page(context: BrowserContext, node: str, wait: Any = ready) -> Opened:
    """In deze context /bewerken/<id> openen en wachten tot de editor klaar is."""
    page = context.new_page()
    opened = Opened(context=context, page=page, key="", config={})
    watch(context, page, opened)
    with page.expect_response(is_session(node)) as answer:
        page.goto(f"/bewerken/{node}")
    assert answer.value.status == 200, answer.value.text()
    body = answer.value.json()
    opened.key, opened.config = body["key"], body["config"]
    wait(page)
    return opened


def login(browsers: Browsers, account: Account) -> BrowserContext:
    return browsers.context_as(account, service_workers="block")


def office_cookie(context: BrowserContext) -> dict | None:
    found = [c for c in context.cookies() if c["name"] == "office"]
    assert len(found) <= 1, found
    return found[0] if found else None


def socket_path(version: str, key: str) -> str:
    return f"/office/{version}/doc/{key}/c/?shardkey={key}&EIO=4&transport=websocket"


def probe(
    page: Page, path: str, first: str | None = None, until: str | None = None, wait: int = 10_000
) -> dict:
    return page.evaluate(PROBE_JS, [path, first, until, wait])


def connect_packet(key: str, token: str, *, session: bool = False) -> str:
    """Het CONNECT-pakket van socket.io zoals de editor het stuurt (§3.10, spike stap 12): de
    data met type auth en docid, het token als token (eerste keer) of session (herverbinden)."""
    data = {"type": "auth", "docid": key, "jwtSession" if session else "jwtOpen": token}
    return "40" + json.dumps({"data": data, "session" if session else "token": token})


def upgrade_status(base: str, path: str, cookie: str | None) -> int:
    """De status van een upgrade zoals de browser hem stuurt (dezelfde Origin, het cookie als
    dat er is); daarna meteen dicht. Over https zonder controle van het certificaat (het is
    zelfgetekend)."""
    parts = urlsplit(base)
    assert parts.hostname is not None and parts.port is not None
    raw = socket.create_connection((parts.hostname, parts.port), timeout=10)
    if parts.scheme == "https":
        tls = ssl.create_default_context()
        tls.check_hostname = False
        tls.verify_mode = ssl.CERT_NONE
        raw = tls.wrap_socket(raw, server_hostname=TLS_DOMAIN)
    try:
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {parts.netloc}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}",
            "Sec-WebSocket-Version: 13",
            f"Origin: {parts.scheme}://{parts.netloc}",
        ]
        if cookie:
            lines.append(f"Cookie: office={cookie}")
        raw.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = raw.recv(4096)
            if not chunk:
                break
            head += chunk
        return int(head.split(b" ", 2)[1])
    finally:
        raw.close()


def session_token(office: Office, opened: Opened) -> str:
    """Het sessietoken dat DS de editor gaf (het antwoord op auth, veld jwt), uit de frames die
    de browser ontving. Alleen een token dat met de afgeleide sleutel van DS klopt en de key van
    deze editor draagt."""
    from app.office.oo import stream

    for raw in opened.frames:
        if not raw.startswith("42"):
            continue
        try:
            data = json.loads(raw[2:])
        except ValueError:
            continue
        if not (isinstance(data, list) and len(data) > 1 and isinstance(data[1], dict)):
            continue
        token = data[1].get("jwt") if data[1].get("type") == "auth" else None
        if isinstance(token, str):
            if stream.token_key(office.settings(), token, session_only=True) == opened.key:
                return token
    raise AssertionError(f"geen sessietoken van DS in {len(opened.frames)} frames")


def gate_code(response: Any) -> str | None:
    try:
        return response.json()["detail"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def fake_sockets(office: Office, key: str) -> int:
    """Hoe vaak een socket van de browser bij de nep-DS tot CONNECT kwam op deze key."""
    assert office.fake is not None
    return sum(1 for e in office.fake.log() if e.get("kind") == "socket" and e.get("key") == key)


def fake_data(office: Office, key: str) -> int:
    """Hoe vaak de nep-DS een DATA-pad van deze key kreeg."""
    assert office.fake is not None
    return sum(
        1
        for e in office.fake.log()
        if e.get("kind") == "http" and f"/cache/files/data/{key}" in str(e.get("path"))
    )


def session_closed(office: Office, account: Account, node: str) -> bool:
    """Geen sessie meer die niet dicht is op dit bestand."""
    r = office.server.call(account, "GET", f"/api/v1/office/files/{node}", expect=200)
    return r.json()["open"] is None


def open_account_menu(page: Page, name: str) -> Any:
    page.get_by_role("button", name=name, exact=True).click()
    menu = page.get_by_role("menu", name=f"Accountmenu van {name}")
    expect(menu).to_be_visible()
    return menu


# --- twee contexts, via de tunnel ---


def test_twee_contexts_bewerken_samen_via_de_tunnel(office: Office, office_browsers) -> None:
    """Anna en Bram openen dezelfde docx in een eigen context. Elk krijgt het poort-cookie
    (HttpOnly, SameSite=Strict, Path=/office/), de socket gaat via /office/ (ws:) en de tekst
    van de één verschijnt bij de ander. Elke socket kwam bij de nep-DS aan met een geldig
    eerste pakket (de StreamGuard liet hem door)."""
    d = duo(office)
    a = open_editor_page(login(office_browsers, d.anna), d.node)
    b = open_editor_page(login(office_browsers, d.bram), d.node)
    assert a.key == b.key
    for opened in (a, b):
        cookie = office_cookie(opened.context)
        assert cookie is not None
        assert cookie["httpOnly"] is True and cookie["sameSite"] == "Strict"
        assert cookie["path"] == "/office/" and cookie["secure"] is False
        assert any(
            u.startswith("ws://") and f"/office/{opened.version()}/doc/{a.key}/c/" in u
            for u in opened.sockets
        ), opened.sockets
        assert opened.data_urls(), "de editor haalde zijn document via /office/cache/files"

    type_in_editor(a.page, "Anna via de tunnel")
    expect(frame_editor(b.page).locator("#others")).to_contain_text("Anna via de tunnel")
    type_in_editor(b.page, "Bram via de tunnel")
    expect(frame_editor(a.page).locator("#others")).to_contain_text("Bram via de tunnel")
    info = fake_info(office, a.key)
    assert sorted(info["users"]) == sorted([uid(office, d.anna), uid(office, d.bram)])
    assert office.fake is not None
    entries = [e for e in office.fake.log() if e.get("kind") == "socket" and e.get("key") == a.key]
    assert len(entries) >= 2 and all(e.get("error") is None for e in entries), entries
    for opened in (a, b):
        opened.page.get_by_role("button", name="Sluiten", exact=True).click()
        expect(opened.page).to_have_url(re.compile(rf"/map/{d.folder}$"))


# --- §4.4 punt 3: rechten ---


def test_alleen_lezen_ziet_geen_bewerken_en_komt_niet_bij_ds(
    office: Office, office_browsers
) -> None:
    """Cees heeft leesrecht: in de viewer geen Bewerken, /bewerken geeft 403 read_only (en geen
    cookie). Ook met een geldig poort-cookie uit een eigen document komt hij niet bij de key
    van Anna: het DATA-pad 401 office_gate (DS ziet het niet) en de upgrade 401."""
    d = duo(office)
    server = office.server
    cees = create_user(server, office.admin, name=f"Cees {uuid.uuid4().hex[:6]}")
    server.call(
        d.anna,
        "POST",
        f"/api/v1/files/{d.folder}/shares",
        json={"user_id": cees.id, "permission": "read"},
        expect=201,
    )
    anna = open_editor_page(login(office_browsers, d.anna), d.node)
    data_url = anna.data_urls()[0]
    path = socket_path(anna.version(), anna.key)

    context = login(office_browsers, cees)
    page = context.new_page()
    page.goto(f"/map/{d.folder}?bestand={d.node}")
    viewer = page.get_by_role("dialog", name="verslag.docx", exact=True)
    # Downloaden als PDF hangt ook aan de status van office: is die er, dan was Bewerken er ook
    expect(viewer.get_by_role("button", name="Downloaden als PDF", exact=True)).to_be_visible()
    expect(viewer.get_by_role("button", name="Bewerken")).to_have_count(0)

    with page.expect_response(is_session(d.node)) as answer:
        page.goto(f"/bewerken/{d.node}")
    assert answer.value.status == 403 and gate_code(answer.value) == "read_only"
    expect(page.get_by_role("alert")).to_contain_text("alleen bekijken")
    expect(page.locator("iframe[name=frameEditor]")).to_have_count(0)
    assert office_cookie(context) is None
    r = server.call(cees, "POST", f"/api/v1/office/files/{d.node}/session", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "read_only"

    # Een eigen document geeft Cees wel een cookie
    own = server.call(
        cees,
        "PUT",
        f"/api/v1/files/{cees.root_id}/upload",
        params={"name": "eigen.docx"},
        content=fixture("proef.docx"),
        expect=201,
    ).json()["id"]
    mine = open_editor_page(context, own)
    cookie = office_cookie(context)
    assert cookie is not None
    seen = fake_data(office, anna.key)
    r = context.request.get(data_url)
    assert r.status == 401 and gate_code(r) == "office_gate", r.text()
    assert fake_data(office, anna.key) == seen
    assert upgrade_status(server.url, path, cookie["value"]) == 401
    out = probe(mine.page, path)
    assert out["opened"] is False and out["frames"] == [], out
    # Anna zelf: wel
    assert anna.context.request.get(data_url).status == 200


def test_uitloggen_in_een_andere_context_sluit_de_editor(office: Office, office_browsers) -> None:
    """Bram bewerkt in context A. Dezelfde login (de cookies van A) logt in context B uit, via
    het accountmenu. Binnen 10 s is Bram uit de editor (uitloggen sluit de tunnels van die
    login meteen; eerst was het de controle van de tunnel elke 15 s, gemeten 14,5 s; de pagina
    blijft staan en zegt "Verbinding weg"), en daarna geeft het DATA-pad 401 office_gate en de
    upgrade 401, ook vanuit de pagina zelf."""
    d = duo(office)
    context = login(office_browsers, d.bram)
    bram = open_editor_page(context, d.node)
    data_url = bram.data_urls()[0]
    path = socket_path(bram.version(), bram.key)
    cookie = office_cookie(context)
    assert cookie is not None
    assert context.request.get(data_url).status == 200
    assert upgrade_status(office.server.url, path, cookie["value"]) == 101

    other = office_browsers.new_context(
        storage_state=context.storage_state(), service_workers="block"
    )
    page_b = other.new_page()
    page_b.goto("/")
    open_account_menu(page_b, d.bram.display_name).get_by_role(
        "menuitem", name="Uitloggen", exact=True
    ).click()
    expect(page_b.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()
    t0 = time.monotonic()

    me = uid(office, d.bram)
    wait_until(lambda: me not in fake_info(office, bram.key)["users"], 10, "Bram uit de editor")
    took = time.monotonic() - t0
    assert took <= 10, took
    # Dicht door de tunnel: de editorpagina staat er nog, de nep-editor kan niet terug
    expect(bram.page.locator(".editor-status")).to_have_text("Verbinding weg", timeout=10_000)
    expect(bram.page).to_have_url(re.compile(rf"/bewerken/{d.node}$"))
    assert me not in fake_info(office, bram.key)["users"]

    seen = fake_data(office, bram.key)
    r = context.request.get(data_url)
    assert r.status == 401 and gate_code(r) == "office_gate", r.text()
    assert fake_data(office, bram.key) == seen
    assert upgrade_status(office.server.url, path, cookie["value"]) == 401
    out = probe(bram.page, path)
    assert out["opened"] is False and out["frames"] == [], out
    print(f"uitgelogd: Bram {took:.1f} s later uit de editor", flush=True)


# --- §4.4 punt 10: afscherming ---


def test_de_editorpagina_als_pagina_geeft_404(office: Office, office_browsers) -> None:
    """De editorpagina (…/documenteditor/main/index.html) laadt in de iframe (200), maar als
    pagina geopend is het 404: in een context zonder cookie, en in de context van Bram mét het
    cookie (Sec-Fetch-Dest document). Zonder cookie ook geen DATA-pad en geen upgrade."""
    d = duo(office)
    bram = open_editor_page(login(office_browsers, d.bram), d.node)
    src = bram.editor_url()
    assert urlsplit(src).path.endswith("/web-apps/apps/documenteditor/main/index.html"), src
    assert bram.responses.get(src) == 200, bram.responses

    fresh = office_browsers.new_context(service_workers="block")
    page = fresh.new_page()
    for url in (src, src.split("?", 1)[0]):
        r = page.goto(url)
        assert r is not None and r.status == 404, (url, r and r.status)

    page2 = bram.context.new_page()
    r = page2.goto(src)
    assert r is not None and r.status == 404
    headers = r.request.all_headers()
    assert headers.get("sec-fetch-dest") == "document", headers
    assert "office=" in headers.get("cookie", ""), "het cookie ging mee"
    page2.close()

    # Zonder cookie: geen DATA-pad en geen upgrade (ook niet vanuit een pagina van de app)
    path = socket_path(bram.version(), bram.key)
    seen = fake_data(office, bram.key)
    r = fresh.request.get(bram.data_urls()[0])
    assert r.status == 401 and gate_code(r) == "office_gate"
    assert fake_data(office, bram.key) == seen
    assert upgrade_status(office.server.url, path, None) == 401
    page.goto("/")
    out = probe(page, path)
    assert out["opened"] is False and out["frames"] == [], out


def test_na_intrekken_geen_nieuwe_socket_met_het_oude_sessietoken(
    office: Office, office_browsers
) -> None:
    """Bram bewerkt; Anna trekt de deling in. Na de melding opent een script in de pagina van
    Bram een nieuwe socket met zijn oude sessietoken (het jwt dat DS hem gaf): geweigerd bij de
    upgrade (401), vóór er iets bij DS komt. Ook de editorpagina zelf krijgt hij niet meer."""
    d = duo(office)
    bram = open_editor_page(login(office_browsers, d.bram), d.node)
    token = session_token(office, bram)
    cookie = office_cookie(bram.context)
    assert cookie is not None
    path = socket_path(bram.version(), bram.key)
    src = bram.editor_url()
    before = fake_sockets(office, bram.key)

    office.server.call(d.anna, "DELETE", f"/api/v1/shares/{d.share}", expect=204)
    expect(bram.page.get_by_role("alert")).to_have_text(DROPPED, timeout=10_000)
    first = connect_packet(bram.key, token, session=True)
    out = probe(bram.page, path, first=first)
    assert out["opened"] is False and out["frames"] == [], out
    assert upgrade_status(office.server.url, path, cookie["value"]) == 401
    assert fake_sockets(office, bram.key) == before
    r = bram.context.request.get(src, headers={"Sec-Fetch-Dest": "iframe"})
    assert r.status == 404


def test_socket_met_het_token_van_een_andere_key_gaat_dicht(
    office: Office, office_browsers
) -> None:
    """Anna heeft twee documenten open, elk in een eigen pagina (key A en key B). Een socket op
    het pad van A met het config-token van A komt verbonden ("40"); met het token van B krijgt de
    browser alleen het "0"-pakket en gaat de socket dicht (de StreamGuard), zonder dat DS een
    CONNECT op A ziet. Daarna gaan beide editors dicht: een sessie die pending blijft, houdt de
    conversies van O3 tegen (sessions.busy)."""
    d = duo(office)
    server = office.server
    other = server.call(
        d.anna,
        "PUT",
        f"/api/v1/files/{d.folder}/upload",
        params={"name": "ander.docx"},
        content=fixture("proef.docx"),
        expect=201,
    ).json()["id"]
    anna = open_editor_page(login(office_browsers, d.anna), d.node)
    second = open_editor_page(anna.context, other)
    assert second.key != anna.key
    token_b = second.config["token"]
    path = socket_path(anna.version(), anna.key)

    good = probe(anna.page, path, first=connect_packet(anna.key, anna.config["token"]), until="40")
    assert good["opened"] is True and any(f.startswith("40{") for f in good["frames"]), good
    before = fake_sockets(office, anna.key)
    bad = probe(anna.page, path, first=connect_packet(anna.key, token_b))
    assert bad["opened"] is True, bad
    assert len(bad["frames"]) == 1 and bad["frames"][0].startswith("0{"), bad
    assert bad["code"] == 1006, bad  # de tunnel sloot de verbinding, zonder sluitframe
    assert fake_sockets(office, anna.key) == before
    # De editor van Anna merkte niets
    expect(frame_editor(anna.page).locator('textarea[aria-label="Nep-editor"]')).to_be_enabled()
    for opened, node in ((anna, d.node), (second, other)):
        opened.page.get_by_role("button", name="Sluiten", exact=True).click()
        expect(opened.page).to_have_url(re.compile(rf"/map/{d.folder}$"))
        wait_until(lambda n=node: session_closed(office, d.anna, n), 20, "de sessie dicht")


# --- http en https ---


def test_de_editor_over_http_en_https(browser: Any, tmp_path_factory) -> None:
    """Een eigen server met de nep-DS en een zelfgetekend certificaat: Anna bewerkt over http,
    Bram over https (wss: via /office/, het cookie met Secure), en ze zien elkaars tekst."""
    from tests.test_certs import write_cert

    from app.certs import CertManager
    from office_harness import start_fake_office

    office = start_fake_office(tmp_path_factory.mktemp("office-https"))
    browsers: Browsers | None = None
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
        browsers = Browsers(browser, office.server)
        d = duo(office)
        a = open_editor_page(login(browsers, d.anna), d.node)
        context = browsers.new_context(
            base_url=secure, ignore_https_errors=True, service_workers="block"
        )
        r = context.request.post(
            f"{secure}/api/v1/auth/login",
            data={"email": d.bram.email, "password": d.bram.password},
        )
        assert r.ok, r.text()
        b = open_editor_page(context, d.node)
        assert b.key == a.key
        assert b.page.url.startswith(f"{secure}/bewerken/")
        assert b.editor_url().startswith(f"{secure}/office/")
        assert any(
            u.startswith(f"wss://127.0.0.1:{office.server.https_port}/office/") for u in b.sockets
        ), b.sockets
        cookie_a, cookie_b = office_cookie(a.context), office_cookie(b.context)
        assert cookie_a is not None and cookie_a["secure"] is False
        assert cookie_b is not None and cookie_b["secure"] is True

        type_in_editor(a.page, "Anna over http")
        expect(frame_editor(b.page).locator("#others")).to_contain_text("Anna over http")
        type_in_editor(b.page, "Bram over https")
        expect(frame_editor(a.page).locator("#others")).to_contain_text("Bram over https")
        path = socket_path(b.version(), b.key)
        assert upgrade_status(secure, path, cookie_b["value"]) == 101
        assert upgrade_status(secure, path, None) == 401
        for opened in (a, b):
            opened.page.get_by_role("button", name="Sluiten", exact=True).click()
            expect(opened.page).to_have_url(re.compile(rf"/map/{d.folder}$"))
    finally:
        problems = browsers.close_all() if browsers is not None else []
        office.stop()
    if problems:
        pytest.fail("Problemen in de browser:\n" + "\n".join(problems), pytrace=False)
