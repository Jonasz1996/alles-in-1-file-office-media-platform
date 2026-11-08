"""Zoeken in de browser (fase 6, Z4; bouwontwerp Z4, Tests): een rapport, geen poort.

Gemeten, in Chromium met het profiel van first_view.py (latency 2 ms, 100 Mbit/s, CPU 1×):

- **Eerste suggestie na een toets**: van de laatste toets in het veld bovenaan (keydown) tot de
  lijst met de suggesties getekend is (de eerste requestAnimationFrame nadat hij in de DOM
  kwam). De lijst gaat pas open met het antwoord op de vraag van die laatste toets, dus dit is de
  wachttijd van 120 ms na de laatste toets, het verzoek GET /search/suggest en het tekenen.
  Koud: elke keer een nieuwe context (de chunk van de suggesties laadt pas na de eerste toets);
  warm: in dezelfde pagina de volgende vraag (het veld eerst leeg).
- **Tekenen van /zoeken**: koud, een nieuwe context met het adres /zoeken?q=…: van het begin van
  de navigatie (performance.timeOrigin) tot de eerste treffer getekend is (of "Niets gevonden");
  warm, op /zoeken: van Enter in het veld Zoekwoorden tot de eerste treffer van de nieuwe vraag
  getekend is.
- Daarbij de duur van de verzoeken zelf (Resource Timing), om het tekenen ervan te scheiden.

Data: de boom van bench.py (search_harness.start_boom, standaard 10.000 bestanden) met de index
aan, ingelogd als de eigenaar (rol "eigenaar" van §4.4), met de vragen van §4.3
(api/tests/search/vragen.tsv), elke meting met een andere vraag, in een vaste willekeurige
volgorde. De suggesties krijgen de vragen die het begin van een woord of een stuk van een naam
zijn (de soorten "voorvoegsel" en "naamstuk": wat je typt voordat je een suggestie kiest),
/zoeken alle soorten (ook "leeg": dan tot "Niets gevonden"). Een eigen server; alles stopt op
pid.

Uitvoer: per meting mediaan, p95 en max in ms, als blok "=== kort ===", en met --json (of
$E2E_PERF_OUT) alles als JSON. Geen vraag in de uitvoer: alleen soorten en getallen.

    cd api && uv run --with playwright python ../e2e/perf/zoek_ui.py
    cd api && uv run --with playwright python ../e2e/perf/zoek_ui.py --n 1000 --koud 3 \\
        --warm 10 --json /tmp/zoek_ui.json
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # e2e/

from conftest import (  # noqa: E402 - pas na het pad naar e2e/
    Browsers,
    cdp_profile,
    launch_browser,
    median,
    percentile,
    write_result,
)

VRAGEN = HERE.parents[1] / "api" / "tests" / "search" / "vragen.tsv"
PROFIEL = {"latency_ms": 2, "down_mbit": 100, "up_mbit": 100, "cpu": 1}
TIMEOUT_MS = 30_000
SEED = 4
# De soorten van vragen.tsv die je typt voordat je een suggestie kiest
TYPEN = ("voorvoegsel", "naamstuk")

# In elke pagina, vóór de app: de toetsen in een invoerveld en, na elke wijziging van de DOM, bij
# het volgende frame wat er staat (de lijst met suggesties, de eerste treffer, "Niets gevonden").
# Alleen goedkope reads: geen layout.
METER_JS = r"""
(() => {
  const zk = (window.__zk = { keys: [], snaps: [] });
  document.addEventListener("keydown", (e) => {
    if (e.target instanceof HTMLInputElement) zk.keys.push(performance.now());
  }, true);
  let pending = false;
  const snap = () => {
    pending = false;
    const list = document.getElementById("zoek-suggesties");
    const row = document.querySelector('#inhoud [role=grid] [role=row][data-index="0"] .name');
    const status = document.querySelectorAll("#inhoud [role=status]");
    zk.snaps.push({
      t: performance.now(),
      opts: list ? list.querySelectorAll("[role=option]").length : 0,
      first: row ? row.textContent : null,
      none: [...status].some((s) => s.textContent.includes("Niets gevonden")),
    });
  };
  new MutationObserver(() => {
    if (!pending) {
      pending = true;
      requestAnimationFrame(snap);
    }
  }).observe(document, { subtree: true, childList: true, characterData: true });
})();
"""

# De duur van het laatste verzoek naar een pad (Resource Timing), en wanneer het antwoord er was
LAATSTE_JS = """(pad) => {
  const e = performance.getEntriesByType("resource").filter((r) => r.name.includes(pad)).pop();
  return e ? { duur: e.duration, eind: e.responseEnd } : null;
}"""


def lees_vragen(pad: Path) -> tuple[list[str], list[str]]:
    """(om te typen voor de suggesties, voor /zoeken), elk in een vaste willekeurige volgorde."""
    regels = pad.read_text(encoding="utf-8").splitlines()
    paren = [r.split("\t", 1) for r in regels if r and not r.startswith("#") and "\t" in r]
    typen = [v for soort, v in paren if soort in TYPEN]
    zoeken = [v for _, v in paren]
    rng = random.Random(SEED)
    rng.shuffle(typen)
    rng.shuffle(zoeken)
    return typen, zoeken


def samen(waarden: list[float]) -> dict[str, Any]:
    return {
        "n": len(waarden),
        "mediaan": round(median(waarden), 1) if waarden else None,
        "p95": round(percentile(waarden, 95), 1) if waarden else None,
        "max": round(max(waarden), 1) if waarden else None,
    }


class Meter:
    """De browser met contexts als de eigenaar, en de vragen om de beurt."""

    def __init__(self, browsers: Browsers, account: Any, typen: list[str], zoeken: list[str]):
        self.browsers = browsers
        self.account = account
        self.vragen = {"typen": typen, "zoeken": zoeken}
        self.volgende = {"typen": 0, "zoeken": 0}

    def vraag(self, soort: str = "zoeken") -> str:
        lijst = self.vragen[soort]
        q = lijst[self.volgende[soort] % len(lijst)]
        self.volgende[soort] += 1
        return q

    def pagina(self) -> tuple[Any, Any]:
        context = self.browsers.context_as(self.account)
        context.add_init_script(METER_JS)
        page = context.new_page()
        cdp_profile(context, page, **PROFIEL)
        return context, page

    def sluit(self, context: Any) -> None:
        problemen = self.browsers.close(context)
        if problemen:
            raise AssertionError("Problemen in de browser:\n" + "\n".join(problemen))

    @staticmethod
    def wis(page: Any) -> None:
        page.evaluate("() => { window.__zk.keys = []; window.__zk.snaps = []; }")

    def suggestie(self, page: Any) -> dict[str, Any]:
        """Typ een vraag in het lege veld bovenaan (de focus staat erin); tot de lijst er is."""
        q = self.vraag("typen")
        self.wis(page)
        with page.expect_response(lambda r: "/api/v1/search/suggest?" in r.url) as antwoord:
            page.keyboard.type(q)
        s = antwoord.value.json()
        namen = sum(len(s[k]) for k in ("names", "people", "places", "tags", "albums"))
        page.wait_for_function(
            "() => { const z = window.__zk; const t = z.keys[z.keys.length - 1];"
            " return z.snaps.some((s) => s.t > t && s.opts > 0); }",
            timeout=TIMEOUT_MS,
        )
        zk = page.evaluate("() => window.__zk")
        t0 = zk["keys"][-1]
        t1 = next(s["t"] for s in zk["snaps"] if s["t"] > t0 and s["opts"] > 0)
        verzoek = page.evaluate(LAATSTE_JS, "/api/v1/search/suggest?")
        return {"ms": t1 - t0, "verzoek_ms": verzoek and verzoek["duur"], "met_namen": namen > 0}

    def getekend(self, page: Any, data: dict[str, Any], na: float) -> float:
        """Wanneer de treffers van dit antwoord er stonden (performance.now van de pagina)."""
        results = data["results"]
        eerste = results[0]["node"]["name"] if results else None
        verzoek = page.evaluate(LAATSTE_JS, "/api/v1/search?") or {"eind": na}
        vanaf = max(na, verzoek["eind"])
        page.wait_for_function(
            "([vanaf, eerste]) => window.__zk.snaps.some((s) => s.t >= vanaf &&"
            " (eerste === null ? s.none : s.first === eerste))",
            arg=[vanaf, eerste],
            timeout=TIMEOUT_MS,
        )
        snaps = page.evaluate("() => window.__zk.snaps")
        return next(
            s["t"]
            for s in snaps
            if s["t"] >= vanaf and (s["none"] if eerste is None else s["first"] == eerste)
        )

    def zoeken_koud(self) -> dict[str, Any]:
        context, page = self.pagina()
        try:
            with page.expect_response(lambda r: "/api/v1/search?" in r.url) as antwoord:
                page.goto(f"/zoeken?{urlencode({'q': self.vraag()})}")
            data = antwoord.value.json()
            t1 = self.getekend(page, data, 0.0)
            verzoek = page.evaluate(LAATSTE_JS, "/api/v1/search?")
            return {
                "ms": t1,
                "verzoek_ms": verzoek and verzoek["duur"],
                "treffers": len(data["results"]),
            }
        finally:
            self.sluit(context)

    def zoeken_warm(self, page: Any) -> dict[str, Any]:
        veld = page.get_by_label("Zoekwoorden", exact=True)
        veld.fill(self.vraag())
        self.wis(page)
        with page.expect_response(lambda r: "/api/v1/search?" in r.url) as antwoord:
            veld.press("Enter")
        data = antwoord.value.json()
        t0 = page.evaluate("() => window.__zk.keys[window.__zk.keys.length - 1]")
        t1 = self.getekend(page, data, t0)
        verzoek = page.evaluate(LAATSTE_JS, "/api/v1/search?")
        return {
            "ms": t1 - t0,
            "verzoek_ms": verzoek and verzoek["duur"],
            "treffers": len(data["results"]),
        }


def bestanden_open(page: Any) -> None:
    page.goto("/")
    page.wait_for_function(
        "() => performance.getEntriesByName('app:ready:folder').length > 0", timeout=TIMEOUT_MS
    )
    page.keyboard.press("Control+k")
    page.locator("#zoekveld:focus").wait_for(timeout=TIMEOUT_MS)


def meet(m: Meter, koud: int, warm: int) -> dict[str, Any]:
    uit: dict[str, list[dict[str, Any]]] = {
        "suggestie_koud": [],
        "suggestie_warm": [],
        "zoeken_koud": [],
        "zoeken_warm": [],
    }
    for k in range(koud):
        context, page = m.pagina()
        try:
            bestanden_open(page)
            uit["suggestie_koud"].append(m.suggestie(page))
        finally:
            m.sluit(context)
        uit["zoeken_koud"].append(m.zoeken_koud())
        print(f"koud {k + 1}/{koud}", flush=True)

    context, page = m.pagina()
    try:
        bestanden_open(page)
        m.suggestie(page)  # de chunk laden; telt niet
        for _ in range(warm):
            page.locator("#zoekveld").fill("")
            page.locator("#zoek-suggesties").wait_for(state="detached", timeout=TIMEOUT_MS)
            uit["suggestie_warm"].append(m.suggestie(page))
        with page.expect_response(lambda r: "/api/v1/search?" in r.url):
            page.goto(f"/zoeken?{urlencode({'q': m.vraag()})}")
        page.get_by_label("Zoekwoorden", exact=True).wait_for(timeout=TIMEOUT_MS)
        for _ in range(warm):
            uit["zoeken_warm"].append(m.zoeken_warm(page))
    finally:
        m.sluit(context)
    print(f"warm {warm}", flush=True)

    res: dict[str, Any] = {}
    for naam, rijen in uit.items():
        res[naam] = {
            **samen([r["ms"] for r in rijen]),
            "verzoek": samen([r["verzoek_ms"] for r in rijen if r["verzoek_ms"] is not None]),
        }
        if naam.startswith("suggestie"):
            res[naam]["met_namen"] = sum(1 for r in rijen if r["met_namen"])
        else:
            res[naam]["zonder_treffers"] = sum(1 for r in rijen if r["treffers"] == 0)
    return res


def kort(res: dict[str, Any]) -> str:
    uitleg = {
        "suggestie_koud": "eerste suggestie na een toets, koud (met de chunk)",
        "suggestie_warm": "eerste suggestie na een toets, warm",
        "zoeken_koud": "/zoeken?q= laden tot de eerste treffer",
        "zoeken_warm": "Enter in Zoekwoorden tot de eerste treffer",
    }
    regels = [
        "=== kort ===",
        "zoek_ui: ms, mediaan / p95 / max (n); verzoek: de duur van het verzoek",
    ]
    for naam, r in res["metingen"].items():
        v = r["verzoek"]
        regels.append(
            f"{uitleg[naam]}: {r['mediaan']} / {r['p95']} / {r['max']} ({r['n']}); "
            f"verzoek {v['mediaan']} / {v['p95']}"
        )
    regels.append(f"data: {res['n']} bestanden, profiel {PROFIEL}")
    return "\n".join(regels)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, default=10_000, help="bestanden in de boom")
    parser.add_argument("--koud", type=int, default=5, help="metingen met een nieuwe context")
    parser.add_argument("--warm", type=int, default=20, help="metingen in dezelfde pagina")
    parser.add_argument("--json", type=Path, help="schrijf alles als JSON hierheen")
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    from search_harness import start_boom

    typen, zoeken = lees_vragen(VRAGEN)
    base = Path(tempfile.mkdtemp(prefix="zoek-ui-"))
    app = start_boom(base, args.n, zoekindex="aan")
    try:
        with sync_playwright() as pw:
            browser = launch_browser(pw)
            try:
                browsers = Browsers(browser, app.server)
                m = Meter(browsers, app.accounts["eigenaar"], typen, zoeken)
                metingen = meet(m, args.koud, args.warm)
            finally:
                browser.close()
    finally:
        app.stop()
        shutil.rmtree(base, ignore_errors=True)

    res = {"n": args.n, "profiel": PROFIEL, "seconden": app.seconden, "metingen": metingen}
    print(kort(res))
    if args.json:
        args.json.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    write_result("zoek_ui", res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
