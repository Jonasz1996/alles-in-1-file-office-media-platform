"""Sessies krijgen browser en os los (de web-app maakt er zelf "Firefox op Windows" van, in
zijn eigen taal); useragent.device_parts ontleedt wat er bewaard is."""

import pytest

from app.security import login_limiter
from app.useragent import UNKNOWN, device_parts
from tests.conftest import ADMIN
from tests.test_account import FIREFOX_WINDOWS, SAFARI_IPHONE

LOGIN = {"email": ADMIN["email"], "password": ADMIN["password"]}


def test_onderdelen_van_een_bewaard_apparaat():
    assert device_parts("Firefox op Windows") == ("Firefox", "Windows")
    assert device_parts("Samsung Internet op Android") == ("Samsung Internet", "Android")
    assert device_parts("Safari") == ("Safari", None)
    assert device_parts("iPad") == (None, "iPad")
    assert device_parts(FIREFOX_WINDOWS) == ("Firefox", "Windows")  # oude, hele User-Agent
    assert device_parts("curl/8.0") == (None, None)
    # Een naam die een app zelf meegaf, of niets bekends
    assert device_parts("Werklaptop") == (None, None)
    assert device_parts("Firefox op Werklaptop") == (None, None)
    assert device_parts(UNKNOWN) == (None, None)
    assert device_parts("setup") == (None, None)
    assert device_parts("  ") == (None, None)


@pytest.mark.anyio
async def test_sessies_met_browser_en_os(client, admin):
    headers, _ = admin
    for agent, device in ((FIREFOX_WINDOWS, ""), (SAFARI_IPHONE, ""), (FIREFOX_WINDOWS, "Werk")):
        login_limiter.reset()
        r = await client.post(
            "/api/v1/auth/login", json={**LOGIN, "device": device}, headers={"User-Agent": agent}
        )
        assert r.status_code == 200, r.text
    rows = (await client.get("/api/v1/auth/sessions", headers=headers)).json()
    parts = {(s["device"], s["browser"], s["os"]) for s in rows}
    assert parts == {
        ("Firefox op Windows", "Firefox", "Windows"),
        ("Safari op iPhone", "Safari", "iPhone"),
        ("Werk", None, None),
        (UNKNOWN, None, None),
    }
