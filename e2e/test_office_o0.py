"""Office O0 met de nep-DS (marker office, §4.4 van docs/fase-4/bouwontwerp.md).

O0 heeft de kern: proxy en tunnel (alleen met PLATFORM_OFFICE_DEV_UNGATED), de gezondheid en
de status. Deze tests bewijzen die kern via de echte server, de echte proxy en tunnel en een
echte Chromium. De interne routes van de app zijn in O0 nog stubs; de DocStub van het harnas
speelt daarom de kant van de app voor DS (documenten en callbacks). De scenario's van O1 tot
O5 komen in hun eigen test_office_o<N>.py.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from conftest import Account, Server
from office_harness import (
    API_JS,
    Office,
    docx_text,
    editor_config,
    fake_info,
    fixture,
    frame_editor,
    new_key,
    open_editor,
    type_in_editor,
    wait_callback,
)

pytestmark = pytest.mark.office


def _state(status: dict) -> tuple[bool, bool, str]:
    return status["enabled"], status["editing"], status["state"]


def test_zonder_office_is_alles_dicht(server: Server, admin: Account) -> None:
    """De gewone e2e-server heeft geen office: /office/ en /internal/ zijn 404, ook met een
    andere methode, en de status zegt uit."""
    for method, path in (
        ("GET", API_JS),
        ("GET", "/office/healthcheck"),
        ("POST", "/office/command"),
        ("GET", "/internal/office/doc/abc"),
        ("POST", "/internal/office/callback"),
    ):
        r = server.request(method, path)
        assert r.status_code == 404, (method, path, r.status_code)
        assert r.json()["detail"]["code"] == "not_found"
    status = server.call(admin, "GET", "/api/v1/office/status", expect=200).json()
    assert _state(status) == (False, False, "off")


def test_status_en_proxy_met_de_nep(office: Office) -> None:
    """Met de nep-DS: gezond na de grote controle (versie, licentie, tokens geëist), en api.js
    komt via /office/ met de koppen van de proxy."""
    assert _state(office.status()) == (True, True, "ok")
    r = office.server.request("GET", API_JS, expect=200)
    assert r.headers["content-type"].startswith("application/javascript")
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    assert "set-cookie" not in r.headers
    # Het prefix uit X-Forwarded-Prefix staat in api.js: de proxy zette die kop
    assert '"/office"' in r.text
    # De app geeft /internal/ nooit op de publieke poort, ook niet met office aan
    assert office.server.request("GET", "/internal/office/doc/abc").status_code == 404


def test_twee_editors_via_proxy_en_tunnel(office: Office, office_browsers) -> None:
    """Anna en Bram op één key, elk in een eigen context: de tekst van Anna komt meteen bij
    Bram (via de tunnel naar dezelfde key), de nep-DS kent twee gebruikers, en na het sluiten
    komt status 2 met een docx die beide teksten heeft."""
    key = new_key("o0-samen")
    url = office.stub.put(f"{key}.docx", fixture("proef.docx"))
    anna = editor_config(office, key=key, url=url, user_id="anna-o0", user_name="Anna")
    bram = editor_config(office, key=key, url=url, user_id="bram-o0", user_name="Bram")

    page_a = office_browsers.new_context(service_workers="block").new_page()
    page_b = office_browsers.new_context(service_workers="block").new_page()
    open_editor(page_a, office, anna)
    open_editor(page_b, office, bram)
    # DS haalde het document één keer op, met het download-JWT
    assert [d["ok"] for d in office.stub.downloads if key in d["path"]] == [True]

    type_in_editor(page_a, "Anna was hier")
    expect(frame_editor(page_b).locator('#others [data-user="anna-o0"]')).to_contain_text(
        "Anna was hier"
    )
    info = fake_info(office, key)
    assert sorted(info["users"]) == ["anna-o0", "bram-o0"]
    type_in_editor(page_b, "Bram ook")
    expect(frame_editor(page_a).locator('#others [data-user="bram-o0"]')).to_contain_text(
        "Bram ook"
    )

    page_a.close()
    page_b.close()
    saved = wait_callback(office.stub, key, 2, timeout=30)
    assert saved["fetched"] == 200, saved
    text = docx_text(office.stub.last_saved(key) or b"")
    assert "Proefdocument van het platform" in text
    assert "Anna was hier" in text
    assert "Bram ook" in text
    # Elke callback had een geldig token; de stub weigerde er geen
    assert all("refused" not in c for c in office.stub.callbacks(key))
    assert not fake_info(office, key)["open"]


def test_ds_weg_en_terug(office: Office) -> None:
    """De nep-DS stoppen op PID: de proxy geeft 502 office_unavailable en de status wordt down.
    Weer starten (zelfde poort en geheimen): weer gezond zonder de app te herstarten."""
    assert office.fake is not None
    office.fake.stop()
    try:
        r = office.server.request("GET", API_JS)
        assert r.status_code == 502
        assert r.json()["detail"]["code"] == "office_unavailable"
        down = office.wait_state("down", 15)
        assert down["editing"] is False
    finally:
        office.fake.start()
    assert office.wait_state("ok", 30)["editing"] is True
    office.server.request("GET", API_JS, expect=200)
