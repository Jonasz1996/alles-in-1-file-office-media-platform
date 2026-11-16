"""Zoeken op beeld (fase 6, X3b; Keuze 81 en 82) in de echte app, met alleen het toetsenbord:
Tab, de pijlen, Enter, Spatie en Esc; nergens een klik (docs/fase-6/toetsenbord-x3.md).

Een eigen app met de analyse aan (PLATFORM_ANALYSE_ENABLED) en de nep-worker van A0 als de
container: hij spreekt het echte protocol over een echte socket, in een eigen draad van deze
test, met de nep van beeld uit api/tests/beeld/nep.py (vaste uitvoer, zonder numpy). Een preview
van één kleur is zijn eigen vector; de vraag "strand" is oranje (WOORDEN hieronder). Eva zet beeld
aan en zet zes foto's neer: de echte app maakt de previews, de stap embed, en het bestand per
eigenaar. Daarna, in de browser:

- /zoeken, de stand kiezen (→ in de keuze), "strand" typen, Enter: het raster met de oranje foto's
  vooraan; een pijl, Enter opent de viewer, Esc komt terug op die foto, en Esc nog eens in het
  veld;
- "Meer zoals deze" in het infopaneel van de viewer (Tab, Enter): /zoeken?mode=image&like=…;
- de terugval: de nep antwoordt te laat, en het raster toont de treffers op naam met de melding;
- de schakelaar onder Account → Foto's staat voor een nieuwe gebruiker uit (Vraag 11), Spatie zet
  hem aan en weer uit; uit geeft op /zoeken de melding, zonder vraag aan /search, en de container
  krijgt forget (K1-08);
- het blok op /beheer: "Zet uit voor iedereen" en terug, met de melding voor Eva;
- "Meer foto's laden" bij meer dan 48 foto's: de focus naar de eerste nieuwe foto.

Elke context houdt elk verzoek bij: geen enkel verzoek gaat naar een andere host dan de app.

Tot slot de app zoals hij in 0.8.0 draait (de server van conftest, de analyse uit): /zoeken in de
stand beeld, Account → Foto's, de viewer en /beheer tonen precies wat 0.8.0 toonde, behalve de zin
op /zoeken en /beheer: die zegt sinds 0.8.1 (I2) dat zoeken op beeld uitstaat en wat de beheerder
kan doen. Verder is het enige nieuwe één GET /photos/beeld per pagina, met 501 (de stubs van Z0b;
zonder werk).
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect
from tests.analyse.fake_worker import FakeAnalyseWorker, FakeRequest
from tests.beeld.nep import Nep

from app.analyse import wire
from app.analyse.wire import Op
from conftest import (
    Account,
    Browsers,
    Server,
    _browsers_teardown,
    artifacts_dir,
    create_user,
    make_admin,
)

pytestmark = [pytest.mark.zoek, pytest.mark.analyse]

API = "/api/v1"
WACHT_S = 60.0
# De vraag "strand" is voor de nep de kleur oranje (zand); de rest kent hij uit nep.KLEUREN
WOORDEN = {"strand": "oranje"}
KLEUREN = {
    "rood": (255, 0, 0),
    "groen": (0, 255, 0),
    "blauw": (0, 0, 255),
    "oranje": (255, 128, 0),
    "paars": (128, 0, 255),
}
# Geen naam met "strand": wat het raster vindt, vindt het op beeld. Op volgorde van upload
FOTOS = (
    ("IMG_0101.jpg", "rood"),
    ("IMG_0102.jpg", "oranje"),
    ("IMG_0103.jpg", "groen"),
    ("IMG_0104.jpg", "oranje"),
    ("IMG_0105.jpg", "blauw"),
    ("zomer aan zee.jpg", "paars"),
)
# Met de analyse uit (sinds 0.8.1, I2): de zin dat zoeken op beeld uitstaat en wat de beheerder kan
# doen, in plaats van "komt later" in 0.8.0 (web/src/i18n/areas/beeld.nl.ts en search.nl.ts)
LATER = (
    "Zoeken op beeld staat uit op deze server (ANALYSE=uit in /opt/platform/.env). "
    "De beheerder kan het aanzetten met ANALYSE=aan."
)
LATER_BEHEER = (
    "Zoeken op beeld staat uit op deze server: de analyse draait niet "
    "(ANALYSE=uit in /opt/platform/.env). Aanzetten: draai het installatiescript opnieuw met "
    "ANALYSE=aan; daarna zet ieder het zelf aan bij Account → Foto's. Zie docs/runbooks/analyse.md."
)
UITLEG_080 = LATER + " Zoekt in je eigen foto's. Druk op Enter."
HINT = (
    "Je foto's worden op de achtergrond bekeken, zodat je ze kunt vinden op wat erop staat "
    "('strand', 'hond'). Dat gebeurt op deze server; er gaat niets naar buiten. Alleen jij kunt "
    "zo in je eigen foto's zoeken. Uit wist wat alleen voor jou bewaard is. Snapshots die de "
    "beheerder zelf van de server maakt, houden het tot ze verlopen. Wat je mag verwachten: één "
    "woord geeft meestal een paar goede foto's bovenaan. Sommige woorden vinden weinig; zoek dan "
    "op 'berg' in plaats van 'bergen', of op 'bootje' in plaats van 'boot'. Bij 'Meer zoals "
    "deze' gaat ongeveer de helft van de eerste foto's over hetzelfde onderwerp."
)
UIT_JIJ = "Zoeken op beeld staat uit. Je zet het aan bij Account → Foto's."
UIT_BEHEER = "Zoeken op beeld staat uit voor dit platform."
TE_TRAAG = "Zoeken op beeld duurde te lang. Dit zijn de treffers op naam en tekst."


# --- de nep-worker in een eigen draad --------------------------------------------------------


class NepWorker:
    """FakeAnalyseWorker met de nep van beeld, in een eigen draad met een eigen lus: de app (een
    ander proces) praat ermee over de socket, de test leest wat hij kreeg."""

    def __init__(self, socket: Path) -> None:
        self.fake = FakeAnalyseWorker(socket)
        self.nep = Nep(self.fake)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="nep-analyse")

    @property
    def key(self) -> str:
        return self.fake.key

    def start(self) -> None:
        self.thread.start()
        self._run(self._start())

    async def _start(self) -> None:
        self.nep.install()
        self.fake.on(Op.RANK, self._rank)
        await self.fake.start()

    async def _rank(self, req: FakeRequest) -> Any:
        """Zoals Nep._rank, met de woorden van deze test als kleur."""
        args = req.args
        if isinstance(args, wire.RankArgs) and args.text in WOORDEN:
            args = args.model_copy(update={"text": WOORDEN[args.text]})
            req = dataclasses.replace(req, args=args)
        return await self.nep._rank(req)

    def _run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=15)

    def stop(self) -> None:
        if not self.thread.is_alive():
            return
        try:
            self._run(self.fake.stop())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=15)
            self.loop.close()


# --- de app ------------------------------------------------------------------------------------


def foto(kleur: str, seed: int) -> bytes:
    """Een JPEG van één kleur (320 × 240); seed maakt de inhoud uniek."""
    from PIL import Image

    img = Image.new("RGB", (320, 240), KLEUREN[kleur])
    img.putpixel((0, 0), (seed % 256, 7, 7))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=95, comment=f"seed {seed}".encode())
    return out.getvalue()


def upload(server: Server, account: Account, naam: str, data: bytes) -> str:
    return server.call(
        account,
        "PUT",
        f"{API}/files/{account.root_id}/upload",
        params={"name": naam},
        content=data,
        expect=201,
    ).json()["id"]


def lees(server: Server, sql: str, *args: Any) -> Any:
    """Eén waarde uit de database van de draaiende server (alleen lezen)."""
    db = server.data_dir / "platform.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()


def wacht(wat: str, klaar: Any, seconden: float = WACHT_S) -> None:
    eind = time.monotonic() + seconden
    while not klaar():
        if time.monotonic() > eind:
            raise AssertionError(f"{wat}: niet binnen {seconden:.0f} s")
        time.sleep(0.2)


def geen_backfill(server: Server) -> bool:
    """Geen job beeld.on en geen backfill van de analyse meer in de rij: de stap embed wacht
    zolang er een is (K2-07), en wel een minuut."""
    (n,) = lees(
        server,
        "SELECT count(*) FROM job WHERE type IN ('beeld.on', 'analysis.backfill') "
        "AND status IN ('queued', 'running')",
    )
    return n == 0


def bestand_klaar(server: Server, account: Account, n: int) -> bool:
    """Het bestand van de eigenaar is bij en heeft alle n vectoren: elke foto is bekeken."""
    rij = lees(
        server,
        "SELECT gen, built_gen, built_rows FROM beeld_owner WHERE user_id = ?",
        uuid.UUID(account.id).hex,
    )
    return rij is not None and rij[0] == rij[1] and rij[2] == n


@dataclass
class X3App:
    server: Server
    worker: NepWorker
    admin: Account
    eva: Account
    fotos: dict[str, str]  # naam -> id


@pytest.fixture(scope="module")
def x3(tmp_path_factory: pytest.TempPathFactory) -> Iterator[X3App]:
    """De app met de analyse aan en de nep-worker; Eva met beeld aan en zes foto's, bekeken en
    in haar bestand."""
    base = tmp_path_factory.mktemp("zoek-beeld")
    worker = NepWorker(base / "run" / "worker.sock")
    worker.start()
    server = Server(
        base / "data",
        env={
            "PLATFORM_ANALYSE_ENABLED": "true",
            "PLATFORM_ANALYSE_WORKER": str(worker.fake.socket),
            "PLATFORM_ANALYSE_WORKER_KEY": worker.key,
            # Het bestand per eigenaar meteen opnieuw (standaard hooguit eens per 5 minuten)
            "PLATFORM_BEELD_REBUILD_S": "0",
        },
    )
    try:
        server.start()
        admin = make_admin(server)
        eva = create_user(server, admin, name="Eva Beeld")
        server.call(eva, "PATCH", f"{API}/me/photo-settings", json={"beeld": "aan"}, expect=200)
        wacht("de backfill na aanzetten", lambda: geen_backfill(server))
        fotos = {
            naam: upload(server, eva, naam, foto(kleur, i)) for i, (naam, kleur) in enumerate(FOTOS)
        }
        wacht("de vectoren en het bestand van Eva", lambda: bestand_klaar(server, eva, len(FOTOS)))
        yield X3App(server, worker, admin, eva, fotos)
    finally:
        server.stop()
        worker.stop()


class Bewaakt(Browsers):
    """Browsers die elk verzoek van elke context bijhouden: alles gaat naar de app zelf."""

    def __init__(self, browser: Any, server: Server) -> None:
        super().__init__(browser, server)
        self.elders: list[str] = []

    def new_context(self, **options: Any) -> Any:
        context = super().new_context(**options)
        context.on("request", self._verzoek)
        return context

    def _verzoek(self, request: Any) -> None:
        url = request.url
        if url.startswith(("data:", "blob:")) or url.startswith(f"{self.server.url}/"):
            return
        self.elders.append(f"{request.method} {url}")


def bewaakt(
    browser: Any, server: Server, request: pytest.FixtureRequest, tmp: pytest.TempPathFactory
) -> Iterator[Bewaakt]:
    b = Bewaakt(browser, server)
    yield b
    problems = _browsers_teardown(request, b, artifacts_dir(tmp))
    problems += [f"verzoek naar een andere host: {line}" for line in b.elders]
    if problems:
        pytest.fail("Problemen in de browser:\n" + "\n".join(problems), pytrace=False)


@pytest.fixture
def x3_browsers(
    browser: Any,
    x3: X3App,
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Bewaakt]:
    """Contexts op de app van x3; elke melding en elk verzoek naar een andere host faalt."""
    yield from bewaakt(browser, x3.server, request, tmp_path_factory)


@pytest.fixture
def uit_browsers(
    browser: Any,
    server: Server,
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Bewaakt]:
    """Hetzelfde op de server van conftest (de analyse uit, zoals 0.8.0)."""
    yield from bewaakt(browser, server, request, tmp_path_factory)


# --- hulp in de browser ------------------------------------------------------------------------


def tab_to(page: Page, target: Locator, limit: int = 40, back: bool = False) -> None:
    """Tab (of Shift+Tab) tot target de focus heeft."""
    target.wait_for(state="attached")
    key = "Shift+Tab" if back else "Tab"
    for _ in range(limit):
        if target.evaluate("(e) => e === document.activeElement"):
            return
        page.keyboard.press(key)
    if not target.evaluate("(e) => e === document.activeElement"):
        raise AssertionError(f"{key} kwam niet bij {target}")


def is_zoeken(r: Any) -> bool:
    return r.request.method == "GET" and "/api/v1/search?" in r.url and "mode=image" in r.url


def is_stand(r: Any) -> bool:
    return r.request.method == "GET" and r.url.split("?", 1)[0].endswith(f"{API}/photos/beeld")


def is_instellingen(r: Any) -> bool:
    return r.request.method == "PATCH" and r.url.endswith(f"{API}/me/photo-settings")


def is_beheer(method: str) -> Any:
    return lambda r: (
        r.request.method == method and r.url.split("?", 1)[0].endswith(f"{API}/admin/search")
    )


def veld(page: Page) -> Locator:
    return page.get_by_role("searchbox", name="Zoekwoorden", exact=True)


def cellen(page: Page) -> Locator:
    return page.get_by_role("grid").get_by_role("gridcell")


def toast(page: Page, tekst: str) -> Locator:
    return page.get_by_role("region", name="Meldingen").get_by_text(tekst, exact=True)


def zoekvragen(page: Page) -> list[str]:
    """Elke vraag van deze pagina aan /search (vanaf nu)."""
    gevraagd: list[str] = []
    page.on("request", lambda r: gevraagd.append(r.url) if "/api/v1/search" in r.url else None)
    return gevraagd


# --- 1. zoeken, het raster en de viewer --------------------------------------------------------


def test_zoeken_op_beeld_met_alleen_het_toetsenbord(x3: X3App, x3_browsers: Bewaakt) -> None:
    """Tab naar de keuze, → kiest "Wat er op de foto staat" (mode=image in de URL). Shift+Tab naar
    het veld, "strand" en Enter: één GET /search?mode=image, de oranje foto's vooraan, dan de rest
    op hoe dicht ze bij oranje liggen. Het raster heeft het label van de vraag en per foto de naam
    en de datum. Tab naar de eerste foto, →, Enter opent de viewer; Esc komt terug op die foto, Esc
    nog eens in het veld."""
    page = x3_browsers.context_as(x3.eva).new_page()
    page.goto("/zoeken")
    tekst = page.get_by_role("radio", name="Namen en tekst", exact=True)
    beeld = page.get_by_role("radio", name="Wat er op de foto staat", exact=True)
    expect(tekst).to_be_checked()
    tab_to(page, tekst)
    with page.expect_response(is_stand) as stand:
        page.keyboard.press("ArrowRight")
    assert stand.value.status == 200 and stand.value.json()["state"] == "ok", stand.value.text()
    expect(page).to_have_url(re.compile(r"/zoeken\?mode=image$"))
    expect(beeld).to_be_checked()
    expect(beeld).to_be_focused()
    # De server kent zoeken op beeld: het veld zegt niet meer dat het uitstaat
    expect(veld(page)).to_have_accessible_description("Zoekt in je eigen foto's. Druk op Enter.")
    expect(page.get_by_text(LATER, exact=True).filter(visible=True)).to_have_count(0)

    tab_to(page, veld(page), back=True)
    page.keyboard.type("strand")
    with page.expect_response(is_zoeken) as antwoord:
        page.keyboard.press("Enter")
    data = antwoord.value.json()
    assert data["mode"] == "image" and data["fallback"] is None, data
    ids = [h["node"]["id"] for h in data["results"]]
    f = x3.fotos
    assert set(ids[:2]) == {f["IMG_0102.jpg"], f["IMG_0104.jpg"]}, ids
    assert ids[2:] == [
        f["IMG_0101.jpg"],
        f["IMG_0103.jpg"],
        f["zomer aan zee.jpg"],
        f["IMG_0105.jpg"],
    ]
    assert all(h["match"] == "image" and h.get("snippet") is None for h in data["results"])
    expect(page).to_have_url(re.compile(r"/zoeken\?q=strand&mode=image$"))

    grid = page.get_by_role("grid", name="Foto's die passen bij 'strand'", exact=True)
    expect(grid).to_be_visible()
    namen = {v: k for k, v in f.items()}
    expect(cellen(page).locator(".beeld-name")).to_have_text([namen[i] for i in ids])
    expect(cellen(page).locator(".beeld-date")).to_have_count(len(ids))
    expect(page.locator(".search-notices")).to_have_text("")
    expect(veld(page)).to_be_focused()

    eerste = cellen(page).nth(0)
    tab_to(page, eerste)
    page.keyboard.press("ArrowRight")
    tweede = cellen(page).nth(1)
    expect(tweede).to_be_focused()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(rf"/zoeken\?q=strand&mode=image&bestand={ids[1]}$"))
    viewer = page.get_by_role("dialog", name=namen[ids[1]], exact=True)
    expect(viewer).to_be_visible()
    page.keyboard.press("Escape")
    expect(viewer).to_have_count(0)
    expect(tweede).to_be_focused()
    page.keyboard.press("Escape")
    expect(veld(page)).to_be_focused()


# Elk frame een halve seconde later, zoals op een drukke machine: wat de pagina een frame later
# doet, valt dan na de volgende toets
TRAAG_FRAME = """
(() => {
  const echt = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (cb) => echt(() => setTimeout(() => cb(performance.now()), 500));
})();
"""


def test_esc_vlak_na_de_viewer_blijft_in_het_veld(x3: X3App, x3_browsers: Bewaakt) -> None:
    """Esc direct na het sluiten van de viewer zet de focus in het veld, en daar blijft hij, ook als
    elk frame een halve seconde later komt. Zette het raster de focus een frame later op de foto,
    dan viel dat na deze Esc en sprong de focus terug (zo werd de test hierboven eens rood in
    slim.sh)."""
    page = x3_browsers.context_as(x3.eva).new_page()
    page.add_init_script(TRAAG_FRAME)
    page.goto("/zoeken?mode=image&q=strand")
    eerste = cellen(page).nth(0)
    expect(eerste).to_be_visible()
    tab_to(page, eerste)
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog")
    expect(viewer).to_be_visible()
    page.keyboard.press("Escape")
    expect(viewer).to_have_count(0)
    expect(eerste).to_be_focused()
    page.keyboard.press("Escape")
    expect(veld(page)).to_be_focused()
    page.wait_for_timeout(1500)  # langer dan één traag frame
    expect(veld(page)).to_be_focused()


# --- 2. "Meer zoals deze" ----------------------------------------------------------------------


def test_meer_zoals_deze_in_de_viewer(x3: X3App, x3_browsers: Bewaakt) -> None:
    """Eva opent een oranje foto uit het raster (Enter), het infopaneel (i), Tab naar "Meer zoals
    deze" en Enter: /zoeken?mode=image&like=<id>, één vraag met like en zonder q, de twee oranje
    foto's vooraan, het label van like; de focus op de eerste foto."""
    page = x3_browsers.context_as(x3.eva).new_page()
    with page.expect_response(is_zoeken):
        page.goto("/zoeken?mode=image&q=strand")
    eerste = cellen(page).nth(0)
    tab_to(page, eerste)
    naam = eerste.locator(".beeld-name").inner_text()
    id_ = x3.fotos[naam]
    assert naam in ("IMG_0102.jpg", "IMG_0104.jpg"), naam
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name=naam, exact=True)
    expect(viewer).to_be_visible()
    page.keyboard.press("i")
    rij = viewer.get_by_role("region", name="Zoeken op beeld", exact=True)
    link = rij.get_by_role("link", name="Meer zoals deze", exact=True)
    expect(link).to_have_attribute("href", f"/zoeken?mode=image&like={id_}")
    tab_to(page, link)
    with page.expect_response(is_zoeken) as antwoord:
        page.keyboard.press("Enter")
    assert f"like={id_}" in antwoord.value.url and "q=" not in antwoord.value.url
    data = antwoord.value.json()
    assert data["fallback"] is None, data
    ids = [h["node"]["id"] for h in data["results"]]
    assert set(ids[:2]) == {x3.fotos["IMG_0102.jpg"], x3.fotos["IMG_0104.jpg"]}, ids
    expect(page).to_have_url(re.compile(rf"/zoeken\?mode=image&like={id_}$"))
    expect(viewer).to_have_count(0)
    grid = page.get_by_role("grid", name="Foto's die lijken op de gekozen foto", exact=True)
    expect(grid).to_be_visible()
    expect(cellen(page)).to_have_count(len(FOTOS))
    expect(cellen(page).nth(0)).to_be_focused()


# --- 3. de terugval ----------------------------------------------------------------------------


def test_de_melding_bij_de_terugval(x3: X3App, x3_browsers: Bewaakt) -> None:
    """De nep antwoordt op rank pas na 3 s (de klok van de app is 800 ms): /search valt terug op
    gewoon zoeken (fallback "slow"). Het raster toont de treffer op naam ("zomer"), met de melding
    in role="status"."""
    page = x3_browsers.context_as(x3.eva).new_page()
    page.goto("/zoeken?mode=image")
    expect(veld(page)).to_be_focused()
    x3.worker.nep.rank_delay = 3.0
    try:
        page.keyboard.type("zomer")
        with page.expect_response(is_zoeken) as antwoord:
            page.keyboard.press("Enter")
        data = antwoord.value.json()
    finally:
        x3.worker.nep.rank_delay = 0.0
    assert data["fallback"] == "slow", data
    assert [h["node"]["id"] for h in data["results"]] == [x3.fotos["zomer aan zee.jpg"]]
    expect(page.get_by_role("status").filter(has_text=TE_TRAAG)).to_have_text(TE_TRAAG)
    grid = page.get_by_role("grid", name="Foto's die passen bij 'zomer'", exact=True)
    expect(grid.get_by_role("gridcell").locator(".beeld-name")).to_have_text(["zomer aan zee.jpg"])


# --- 4. de schakelaar onder Account ------------------------------------------------------------


def test_de_schakelaar_staat_uit_en_gaat_aan_via_account(x3: X3App, x3_browsers: Bewaakt) -> None:
    """Fien is nieuw: onder Account → Foto's staat "Zoeken op beeld" uit, met de uitleg van
    Keuze 81. Tab en Spatie: aan (PATCH 200, beeld "aan"), Spatie: weer uit; de focus blijft op
    het vinkje. Uit geeft op /zoeken de melding, zonder vraag aan /search; de container krijgt
    forget voor Fien (de job beeld.off, K1-08)."""
    fien = create_user(x3.server, x3.admin, name="Fien Schakelaar")
    hex_ = uuid.UUID(fien.id).hex
    page = x3_browsers.context_as(fien).new_page()
    page.goto("/account")
    blok = page.get_by_role("region", name="Foto's", exact=True)
    vinkje = blok.get_by_label("Zoeken op beeld", exact=True)
    expect(vinkje).not_to_be_checked()
    expect(vinkje).to_have_accessible_description(HINT)
    tab_to(page, vinkje, limit=60)
    # Na elke wijziging vraagt de pagina de stand opnieuw (forgetBeeld); die vraag is klaar
    # voordat de test verder gaat, zodat hij niet midden in de volgende navigatie landt
    with page.expect_response(is_stand) as stand_aan:
        with page.expect_response(is_instellingen) as aan:
            page.keyboard.press("Space")
    assert aan.value.status == 200 and aan.value.json()["beeld"] == "aan", aan.value.text()
    assert stand_aan.value.status == 200, stand_aan.value.text()
    expect(vinkje).to_be_checked()
    expect(toast(page, "Opgeslagen.")).to_be_visible()

    with page.expect_response(is_stand) as stand_uit:
        with page.expect_response(is_instellingen) as uit:
            page.keyboard.press("Space")
    assert uit.value.status == 200 and uit.value.json()["beeld"] == "uit", uit.value.text()
    assert stand_uit.value.json() == {"state": "uit", "done": None}, stand_uit.value.text()
    expect(vinkje).not_to_be_checked()
    expect(vinkje).to_be_focused()
    wacht("forget voor Fien", lambda: hex_ in x3.worker.nep.forgets, 30)

    gevraagd = zoekvragen(page)
    with page.expect_response(is_stand) as stand:
        page.goto("/zoeken?mode=image&q=strand")
    assert stand.value.json() == {"state": "uit", "done": None}, stand.value.text()
    expect(page.get_by_role("status").filter(has_text=UIT_JIJ)).to_have_text(UIT_JIJ)
    expect(page.get_by_role("grid")).to_have_count(0)
    assert gevraagd == []


# --- 5. het blok op /beheer --------------------------------------------------------------------


def test_de_beheerder_zet_beeld_uit_voor_iedereen(x3: X3App, x3_browsers: Bewaakt) -> None:
    """/beheer → Zoeken en foto's: het blok "Zoeken op beeld" met de stand, het model, de
    container, hoeveel foto's bekeken zijn, en de vaste zin over snapshots. Tab naar "Zet uit voor
    iedereen", Enter: uit (PATCH), de focus blijft op de knop, die nu "Zet aan voor iedereen"
    heet. Eva ziet op /zoeken de melding van de beheerder; Enter zet het weer aan."""
    admin = x3_browsers.context_as(x3.admin).new_page()
    try:
        with admin.expect_response(is_beheer("GET")) as eerste:
            admin.goto("/beheer/zoeken")
        blok_api = eerste.value.json()["beeld"]
        assert blok_api["on"] is True and blok_api["model"] == "ok", blok_api
        assert (blok_api["seen"], blok_api["photos"]) == (len(FOTOS), len(FOTOS)), blok_api
        assert set(blok_api) >= {"hour", "space", "failed_by_code"}, blok_api
        blok = admin.get_by_role("region", name="Zoeken op beeld", exact=True)
        expect(blok.locator('[data-search="beeld-on"]')).to_have_text("Zoeken op beeld: aan")
        expect(blok.locator('[data-search="beeld-model"]')).to_have_text(
            "SigLIP 2 B/32-256 (Google, Apache-2.0), int8. In orde."
        )
        expect(blok.locator('[data-search="beeld-analyse"]')).to_have_text("draait")
        expect(blok.locator('[data-search="beeld-seen"]')).to_have_text(
            f"{len(FOTOS)} van {len(FOTOS)} foto's"
        )
        expect(blok.locator('[data-search="beeld-failed"]')).to_have_text("0")
        expect(
            blok.get_by_text("Reservekopieën die het platform maakt", exact=False)
        ).to_be_visible()

        knop = blok.get_by_role("button", name="Zet uit voor iedereen", exact=True)
        tab_to(admin, knop, limit=80)
        with admin.expect_response(is_beheer("PATCH")) as uit:
            admin.keyboard.press("Enter")
        assert uit.value.status == 200 and uit.value.json()["beeld"]["on"] is False
        expect(blok.locator('[data-search="beeld-on"]')).to_have_text("Zoeken op beeld: uit")
        weer = blok.get_by_role("button", name="Zet aan voor iedereen", exact=True)
        expect(weer).to_be_focused()

        eva = x3_browsers.context_as(x3.eva).new_page()
        gevraagd = zoekvragen(eva)
        with eva.expect_response(is_stand) as stand:
            eva.goto("/zoeken?mode=image&q=strand")
        assert stand.value.json() == {"state": "beheer_uit", "done": None}
        expect(eva.get_by_role("status").filter(has_text=UIT_BEHEER)).to_have_text(UIT_BEHEER)
        expect(eva.get_by_role("grid")).to_have_count(0)
        assert gevraagd == []

        with admin.expect_response(is_beheer("PATCH")) as aan:
            admin.keyboard.press("Enter")
        assert aan.value.status == 200 and aan.value.json()["beeld"]["on"] is True
        expect(blok.locator('[data-search="beeld-on"]')).to_have_text("Zoeken op beeld: aan")
        expect(blok.get_by_role("button", name="Zet uit voor iedereen", exact=True)).to_be_focused()
        with eva.expect_response(is_zoeken) as weer_aan:
            eva.reload()
        assert weer_aan.value.status == 200 and weer_aan.value.json()["fallback"] is None
        expect(cellen(eva)).to_have_count(len(FOTOS))
    finally:
        x3.server.call(x3.admin, "PATCH", f"{API}/admin/search", json={"beeld": True}, expect=200)


# --- 6. meer foto's laden ----------------------------------------------------------------------

MEER = 50  # meer dan één pagina van 48


def test_meer_fotos_laden_zet_de_focus_op_de_eerste_nieuwe(x3: X3App, x3_browsers: Bewaakt) -> None:
    """Gijs heeft 50 foto's: het raster toont er 48, met "Meer foto's laden". Tab naar de knop,
    Enter: één vraag met offset=48, de twee laatste foto's erbij, de knop weg en de focus op de
    eerste nieuwe foto (zoals "Meer laden" van Z4), niet ergens op de pagina."""
    gijs = create_user(x3.server, x3.admin, name="Gijs Meer")
    x3.server.call(gijs, "PATCH", f"{API}/me/photo-settings", json={"beeld": "aan"}, expect=200)
    wacht("de backfill na aanzetten", lambda: geen_backfill(x3.server))
    kleuren = list(KLEUREN)
    for i in range(MEER):
        upload(x3.server, gijs, f"IMG_{1000 + i}.jpg", foto(kleuren[i % len(kleuren)], 1000 + i))
    wacht("de vectoren en het bestand van Gijs", lambda: bestand_klaar(x3.server, gijs, MEER), 120)

    page = x3_browsers.context_as(gijs).new_page()
    with page.expect_response(is_zoeken) as eerste:
        page.goto("/zoeken?mode=image&q=strand")
    data = eerste.value.json()
    assert len(data["results"]) == 48 and data["more"] is True, data
    expect(cellen(page)).to_have_count(48)
    knop = page.get_by_role("button", name="Meer foto's laden", exact=True)
    tab_to(page, knop, limit=80)
    with page.expect_response(is_zoeken) as tweede:
        page.keyboard.press("Enter")
    assert "offset=48" in tweede.value.url, tweede.value.url
    data = tweede.value.json()
    assert len(data["results"]) == MEER - 48 and data["more"] is False, data
    expect(cellen(page)).to_have_count(MEER)
    expect(cellen(page).nth(48)).to_be_focused()
    expect(page.get_by_role("button", name=re.compile("^Meer foto's laden"))).to_have_count(0)


# --- 7. met de analyse uit: zoals 0.8.0 --------------------------------------------------------


def test_met_de_analyse_uit_blijft_de_web_zoals_in_0_8_0(
    server: Server, admin: Account, user_factory: Any, uit_browsers: Bewaakt
) -> None:
    """De server van conftest (geen PLATFORM_ANALYSE_ENABLED): GET /photos/beeld geeft 501, en
    elke plek toont wat 0.8.0 toonde, met de zinnen van 0.8.1 (I2). /zoeken in de stand beeld: de
    ene zin dat zoeken op beeld uitstaat, dezelfde zin als uitleg van het veld, geen raster en geen
    vraag aan /search. Account → Foto's: het vinkje van Z6 (zonder de uitleg van X3). De viewer:
    geen "Meer zoals deze". /beheer: dat het uitstaat en wat de beheerder kan doen, zonder
    knoppen."""
    lisa = user_factory(name="Lisa Zonder Analyse")
    id_ = upload(server, lisa, "IMG_0201.jpg", foto("oranje", 201))
    page = uit_browsers.context_as(lisa).new_page()
    gevraagd = zoekvragen(page)

    with page.expect_response(is_stand) as stand:
        page.goto("/zoeken?mode=image&q=strand")
    assert stand.value.status == 501, stand.value.text()
    later = page.get_by_text(LATER, exact=True)
    expect(later.filter(visible=True)).to_have_count(1)
    expect(veld(page)).to_have_accessible_description(UITLEG_080)
    expect(page.get_by_role("grid")).to_have_count(0)
    expect(page.locator(".search-notices")).to_have_count(0)
    assert gevraagd == []

    with page.expect_response(is_stand) as stand:
        page.goto("/account")
    assert stand.value.status == 501
    blok = page.get_by_role("region", name="Foto's", exact=True)
    expect(blok.get_by_label("Zoeken op beeld", exact=True)).to_have_count(1)
    expect(blok.get_by_text(HINT, exact=True)).to_have_count(0)

    page.goto(f"/?bestand={id_}")
    viewer = page.get_by_role("dialog", name="IMG_0201.jpg", exact=True)
    expect(viewer).to_be_visible()
    with page.expect_response(is_stand) as stand:
        page.keyboard.press("i")
    assert stand.value.status == 501
    expect(viewer.get_by_role("link", name="Meer zoals deze", exact=True)).to_have_count(0)
    expect(viewer.get_by_role("region", name="Zoeken op beeld", exact=True)).to_have_count(0)

    beheer = uit_browsers.context_as(admin).new_page()
    with beheer.expect_response(is_beheer("GET")) as blok_api:
        beheer.goto("/beheer/zoeken")
    assert blok_api.value.json()["beeld"] is None
    tab = beheer.get_by_role("region", name="Zoeken op beeld", exact=True)
    expect(tab.get_by_text(LATER_BEHEER, exact=True)).to_be_visible()
    expect(tab.get_by_role("button")).to_have_count(0)
