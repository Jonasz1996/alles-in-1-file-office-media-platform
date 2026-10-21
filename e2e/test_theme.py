"""Geen themaflits (P0, frozen na P0).

Het gekozen thema staat in localStorage (platform-theme). /boot.js zet data-theme synchroon
in <head>, vóór de eerste paint. Een init-script met een MutationObserver kijkt mee:

- zodra <body> zijn eerste kind krijgt (de parser is bij de body): data-theme en theme-color
  moeten al goed staan, en als de stylesheet er al is, ook --bg. Is hij er nog niet (meestal:
  de parser wacht niet op CSS), dan is er ook nog niets getekend (geen paint-entries; een
  stylesheet in <head> blokkeert het tekenen);
- in de eerste requestAnimationFrame (vlak vóór de eerste paint): --bg en de achtergrond van
  <body> horen bij het gekozen thema;
- data-theme verandert tijdens het laden nooit naar iets anders (geen flits via de app).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

LIGHT_BG, DARK_BG = "#ffffff", "#121212"
RGB = {LIGHT_BG: "rgb(255, 255, 255)", DARK_BG: "rgb(18, 18, 18)"}

PROBE_JS = r"""
(() => {
  const probe = { firstChild: null, frame: null, changes: [] };
  window.__themeProbe = probe;
  const read = () => {
    const root = document.documentElement;
    return {
      t: performance.now(),
      theme: root.getAttribute("data-theme"),
      bg: getComputedStyle(root).getPropertyValue("--bg").trim(),
      bodyBg: document.body ? getComputedStyle(document.body).backgroundColor : null,
      meta: document.querySelector('meta[name="theme-color"]')?.getAttribute("content") ?? null,
      paints: performance.getEntriesByType("paint").map((e) => e.name),
    };
  };
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === "attributes" && r.target === document.documentElement) {
        probe.changes.push(document.documentElement.getAttribute("data-theme"));
      }
    }
    if (!probe.firstChild && document.body && document.body.firstChild) probe.firstChild = read();
  }).observe(document, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  requestAnimationFrame(() => {
    probe.frame = read();
  });
})();
"""


def color(value: str | None) -> str | None:
    """#fff en #ffffff zijn dezelfde kleur (de gebouwde CSS kort ze in)."""
    if value and len(value) == 4 and value.startswith("#"):
        return "#" + "".join(c * 2 for c in value[1:]).lower()
    return value.lower() if value else value


def storage(server, theme: str | None) -> dict:
    items = [] if theme is None else [{"name": "platform-theme", "value": theme}]
    return {"cookies": [], "origins": [{"origin": server.url, "localStorage": items}]}


CASES = [
    # (opgeslagen thema, voorkeur van het systeem, verwachte data-theme, verwachte --bg)
    pytest.param("dark", "light", "dark", DARK_BG, id="donker"),
    pytest.param("light", "dark", "light", LIGHT_BG, id="licht-op-donker-systeem"),
    pytest.param(None, "dark", None, DARK_BG, id="systeem-donker"),
    pytest.param(None, "light", None, LIGHT_BG, id="systeem-licht"),
]


@pytest.mark.parametrize(("stored", "scheme", "attr", "bg"), CASES)
@pytest.mark.parametrize("path", ["/", "/map/onbekend"])
def test_geen_themaflits(new_context, server, stored, scheme, attr, bg, path):
    context = new_context(color_scheme=scheme, storage_state=storage(server, stored))
    context.add_init_script(PROBE_JS)
    page = context.new_page()
    page.goto(path)
    # Wachten tot de app er staat (het formulier); intussen kan de app nog flitsen
    expect(page.get_by_label("E-mailadres")).to_be_visible()
    probe = page.evaluate("window.__themeProbe")

    first = probe["firstChild"]
    assert first is not None, "de MutationObserver zag de body niet"
    assert first["theme"] == attr, f"data-theme bij de eerste body-node: {first}"
    assert color(first["bg"]) in (bg, ""), f"--bg bij de eerste body-node: {first}"
    if not first["bg"]:
        # De stylesheet is er nog niet: dan is er ook nog niets getekend
        assert first["paints"] == [], f"al getekend zonder stylesheet: {first}"
    assert first["meta"] == bg, f"theme-color bij de eerste body-node: {first}"

    frame = probe["frame"]
    assert frame is not None, "geen requestAnimationFrame vóór de eerste paint"
    assert frame["theme"] == attr, f"data-theme in de eerste frame: {frame}"
    assert color(frame["bg"]) == bg, f"--bg in de eerste frame: {frame}"
    assert frame["bodyBg"] == RGB[bg], f"achtergrond van body in de eerste frame: {frame}"

    # boot.js zet het thema één keer (of laat het weg bij "systeem"); daarna verandert niets
    assert all(change == attr for change in probe["changes"]), probe["changes"]
    if attr:
        expect(page.locator("html")).to_have_attribute("data-theme", attr)
    else:
        assert page.evaluate("document.documentElement.hasAttribute('data-theme')") is False
    after = page.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()"
    )
    assert color(after) == bg


def test_thema_volgt_na_wisselen_ook_bij_herladen(context_as, user_factory):
    """Wisselen in de app en dan herladen: de eerste frame heeft meteen het nieuwe thema."""
    user = user_factory(name="Thema Test")
    context = context_as(user, color_scheme="light")
    context.add_init_script(PROBE_JS)
    page = context.new_page()
    page.goto("/")
    expect(page.get_by_role("button", name=user.display_name)).to_be_visible()
    page.get_by_role("button", name=user.display_name).click()
    page.get_by_role("menuitem", name="Thema").click()
    page.get_by_role("menuitemcheckbox", name="Donker").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.reload()
    expect(page.get_by_role("button", name=user.display_name)).to_be_visible()
    probe = page.evaluate("window.__themeProbe")
    assert probe["firstChild"]["theme"] == "dark"
    assert color(probe["frame"]["bg"]) == DARK_BG
    assert all(change == "dark" for change in probe["changes"]), probe["changes"]
