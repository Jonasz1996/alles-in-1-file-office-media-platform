"""De viewer (P4) in de echte app: elk soort bestand openen vanuit Bestanden, vorige en
volgende (ook ingezoomd met Shift), pannen met de pijltjes, Terug sluit, de deep link
?bestand=, PDF met PgDn en de inhoudsopgave, de tekencodering wisselen, video en audio, de
kantoordocumenten, het toetsenbord (docs/fase-3/toetsenbord-p4.md) en de viewer achter een
publieke link. En: nergens een GPS-locatie in een antwoord.

De voorbeeldbestanden komen uit seed_p4.py (Pillow, ffmpeg, zipfile)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import pytest
from playwright.sync_api import expect

from conftest import Account, Browsers, Server, create_user, make_admin
from seed_p4 import GPS_LAT, outlined_pdf, samples

pytestmark = pytest.mark.p4

# Op naam, zoals de map ze toont (mappen eerst, dan naam)
ORDER = [
    "bewegend.gif",
    "boek.pdf",
    "cijfers.xlsx",
    "clip.webm",
    "data.json",
    "dias.pptx",
    "film.mp4",
    "foto.jpg",
    "liedje.mp3",
    "notities.md",
    "notulen.odt",
    "oud.txt",
    "tabel.csv",
    "verslag.docx",
]


@dataclass
class Library:
    owner: Account
    folder: str
    ids: dict[str, str]


def wait_for_previews(server: Server, owner: Account, ids: list[str], seconds: float = 120) -> dict:
    """Tot elke preview klaar is (of mislukt): de viewer heeft dan alles wat hij wil tonen."""
    deadline = time.monotonic() + seconds
    while True:
        got = server.call(owner, "POST", "/api/v1/previews", json={"node_ids": ids}, expect=200)
        previews = got.json()
        if all(previews.get(i, {}).get("state") not in (None, "pending") for i in ids):
            return previews
        if time.monotonic() > deadline:
            raise AssertionError(f"previews niet klaar: {previews}")
        time.sleep(0.5)


@pytest.fixture(scope="module")
def library(server: Server, admin: Account, tmp_path_factory) -> Library:
    owner = create_user(server, admin, name="Kijker")
    folder = server.call(
        owner,
        "POST",
        f"/api/v1/files/{owner.root_id}/folders",
        json={"name": "Voorbeelden"},
        expect=201,
    ).json()["id"]
    ids = {}
    for name, data in samples(tmp_path_factory.mktemp("p4-samples")).items():
        r = server.call(
            owner,
            "PUT",
            f"/api/v1/files/{folder}/upload",
            params={"name": name},
            content=data,
            expect=201,
        )
        ids[name] = r.json()["id"]
    wait_for_previews(server, owner, list(ids.values()))
    return Library(owner, folder, ids)


def open_folder(context_as, library: Library):
    page = context_as(library.owner).new_page()
    page.goto(f"/map/{library.folder}")
    expect(page.locator("#inhoud").get_by_text("foto.jpg", exact=True)).to_be_visible()
    return page


def open_file(page, name: str):
    # Zoals de rooktest: de naam als tekst in #inhoud (een icoon ervoor mag)
    page.locator("#inhoud").get_by_text(name).click()
    viewer = page.get_by_role("dialog", name=name, exact=True)
    expect(viewer).to_be_visible()
    return viewer


def link_token(link: dict) -> str:
    """Het token uit het adres van een nieuwe link (…/s/<token>)."""
    return link["link"]["url"].rstrip("/").rsplit("/", 1)[-1]


def transform(page) -> str:
    return page.locator(".zoom-img").evaluate("img => img.style.transform")


def test_geen_gps_in_enig_antwoord(server: Server, library: Library):
    """De foto heeft een GPS-blok; dat komt in geen enkel antwoord terug (lijst, detail,
    publieke link)."""
    owner, photo = library.owner, library.ids["foto.jpg"]
    link = server.call(
        owner,
        "POST",
        f"/api/v1/files/{library.folder}/links",
        json={"permission": "read"},
        expect=201,
    ).json()
    key = server.request(
        "POST", "/api/v1/public/open", json={"token": link_token(link)}, expect=200
    )
    bearer = {"Authorization": f"Bearer {key.json()['key']}"}
    answers = [
        server.call(owner, "POST", "/api/v1/previews", json={"node_ids": [photo]}, expect=200),
        server.call(owner, "GET", f"/api/v1/files/{photo}/preview", expect=200),
        server.request("GET", f"/api/v1/public/preview/{photo}", headers=bearer, expect=200),
        server.request(
            "POST",
            "/api/v1/public/previews",
            headers=bearer,
            json={"node_ids": [photo]},
            expect=200,
        ),
    ]
    for r in answers:
        text = r.text.lower()
        assert "gps" not in text and "latitude" not in text and "location" not in text, text
        assert json.dumps(list(GPS_LAT)) not in text
    # De lijst blijft licht: geen tekst, inhoud van archieven of EXIF
    light = answers[0].json()[photo]["meta"]
    assert not {"text", "entries", "exif"} & set(light)


def test_elk_soort_openen_en_bladeren(context_as, library: Library):
    """Elk soort opent met zijn eigen weergave; ← en → gaan door de hele map."""
    page = open_folder(context_as, library)
    viewer = open_file(page, ORDER[0])
    expect(page).to_have_url(re.compile(rf"bestand={library.ids[ORDER[0]]}"))
    expect(viewer.get_by_text(f"1 / {len(ORDER)}")).to_be_visible()
    kinds = {}
    for i, name in enumerate(ORDER):
        viewer = page.get_by_role("dialog", name=name, exact=True)
        expect(viewer).to_be_visible()
        stage = viewer.locator(".viewer-stage")
        expect(stage).to_have_attribute("data-kind", re.compile(r".+"))
        kinds[name] = stage.get_attribute("data-kind")
        if i < len(ORDER) - 1:
            page.keyboard.press("ArrowRight")
    assert kinds == {
        "bewegend.gif": "image",
        "boek.pdf": "pdf",
        "cijfers.xlsx": "office",
        "clip.webm": "video",
        "data.json": "text",
        "dias.pptx": "office",
        "film.mp4": "video",
        "foto.jpg": "image",
        "liedje.mp3": "audio",
        "notities.md": "text",
        "notulen.odt": "office",
        "oud.txt": "text",
        "tabel.csv": "text",
        "verslag.docx": "office",
    }
    # Terug naar het begin: Home; en ← vanaf daar blijft daar
    page.keyboard.press("Home")
    expect(page.get_by_role("dialog", name=ORDER[0], exact=True)).to_be_visible()
    page.keyboard.press("End")
    expect(page.get_by_role("dialog", name=ORDER[-1], exact=True)).to_be_visible()
    page.keyboard.press("ArrowLeft")
    expect(page.get_by_role("dialog", name=ORDER[-2], exact=True)).to_be_visible()


def test_afbeelding_zoomen_pannen_en_navigeren(context_as, library: Library):
    """Ingezoomd pannen de pijltjes en navigeert Shift+←/→; passend navigeren de pijltjes."""
    page = open_folder(context_as, library)
    viewer = open_file(page, "foto.jpg")
    expect(viewer.locator(".zoom-img")).to_have_attribute("style", re.compile("scale"))
    fit = transform(page)
    fit_percent = viewer.locator(".zoom-percent").inner_text()
    page.keyboard.press("+")
    page.keyboard.press("+")
    expect(viewer.locator(".zoom-percent")).not_to_have_text(fit_percent)
    zoomed = transform(page)
    assert zoomed != fit
    # Het pijltje schuift (10 % van het venster) en blijft bij dit bestand
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("dialog", name="foto.jpg", exact=True)).to_be_visible()
    panned = transform(page)
    assert panned != zoomed
    page.keyboard.press("ArrowDown")
    assert transform(page) != panned
    # Ingezoomd is meer nodig dan 1024 px: het origineel komt (na decode) in de plaats
    expect(viewer.locator(".zoom-img")).to_have_attribute("src", re.compile("/download"))
    # Shift+→ navigeert ook ingezoomd
    page.keyboard.press("Shift+ArrowRight")
    expect(page.get_by_role("dialog", name="liedje.mp3", exact=True)).to_be_visible()
    page.keyboard.press("Shift+ArrowLeft")
    viewer = page.get_by_role("dialog", name="foto.jpg", exact=True)
    expect(viewer).to_be_visible()
    # Een nieuw bestand begint passend; R draait, 1 is 100 %, 0 weer passend
    page.keyboard.press("r")
    expect(viewer.locator(".zoom-img")).to_have_attribute("style", re.compile("rotate.90deg"))
    page.keyboard.press("1")
    expect(viewer.locator(".zoom-percent")).to_have_text("100 %")
    page.keyboard.press("0")
    expect(viewer.locator(".zoom-percent")).not_to_have_text("100 %")
    expect(viewer.locator(".zoom-img")).to_have_attribute("style", re.compile("rotate.90deg"))
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("dialog", name="liedje.mp3", exact=True)).to_be_visible()


def test_bewegende_gif_als_origineel(context_as, library: Library):
    page = open_folder(context_as, library)
    viewer = open_file(page, "bewegend.gif")
    expect(viewer.locator(".zoom-img")).to_have_attribute("src", re.compile("/download"))


def test_gegevens_zonder_locatie(context_as, library: Library):
    """I opent het paneel met gegevens; er staat geen locatie in."""
    page = open_folder(context_as, library)
    viewer = open_file(page, "foto.jpg")
    page.keyboard.press("i")
    info = viewer.get_by_role("complementary", name="Gegevens")
    expect(info).to_be_visible()
    expect(info.get_by_text(re.compile(r"^2\.?400 × 1\.?600 pixels$"))).to_be_visible()
    expect(info).not_to_contain_text("Locatie")
    expect(info).not_to_contain_text("GPS")
    page.keyboard.press("i")
    expect(info).to_be_hidden()


def test_terug_sluit_en_deep_link(context_as, library: Library):
    page = open_folder(context_as, library)
    open_file(page, "oud.txt")
    expect(page).to_have_url(re.compile("bestand="))
    page.go_back()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page).not_to_have_url(re.compile("bestand="))
    expect(page).to_have_url(re.compile(f"/map/{library.folder}"))
    # Esc sluit ook, en één keer: de map blijft open
    open_file(page, "oud.txt")
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page).to_have_url(re.compile(f"/map/{library.folder}$"))
    # Een link naar het bestand opent de viewer meteen; sluiten laat de map staan
    page.goto(f"/map/{library.folder}?bestand={library.ids['boek.pdf']}")
    viewer = page.get_by_role("dialog", name="boek.pdf", exact=True)
    expect(viewer).to_be_visible()
    viewer.get_by_role("button", name="Sluiten", exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page).to_have_url(re.compile(f"/map/{library.folder}$"))


def test_raster_opent_de_viewer_en_bladert_door_de_map(context_as, library: Library):
    """Ook vanuit het raster van Bestanden (P2): openen, ← en → door de map, Terug sluit en
    het raster blijft staan."""
    page = open_folder(context_as, library)
    page.keyboard.press("g")
    grid = page.locator("#inhoud [data-view=grid]")
    expect(grid).to_be_visible()
    grid.get_by_text("foto.jpg", exact=True).click()
    expect(page.get_by_role("dialog", name="foto.jpg", exact=True)).to_be_visible()
    expect(page).to_have_url(re.compile(rf"bestand={library.ids['foto.jpg']}"))
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("dialog", name="liedje.mp3", exact=True)).to_be_visible()
    expect(page).to_have_url(re.compile(rf"bestand={library.ids['liedje.mp3']}"))
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    expect(page.get_by_role("dialog", name="film.mp4", exact=True)).to_be_visible()
    # Bladeren vervangt de stap in de geschiedenis: één keer Terug sluit
    page.go_back()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page).to_have_url(re.compile(f"/map/{library.folder}$"))
    expect(grid).to_be_visible()


def test_zoekresultaten_openen_de_viewer(context_as, library: Library):
    """Vanuit Zoeken (P2): ← en → gaan door de resultaten, Terug sluit en de zoekopdracht
    blijft; een link met ?bestand= opent meteen."""
    page = context_as(library.owner).new_page()
    page.goto("/zoeken?q=no")
    results = page.locator("#inhoud")
    expect(results.get_by_text("notities.md", exact=True)).to_be_visible()
    expect(results.get_by_text("notulen.odt", exact=True)).to_be_visible()
    names = [
        n
        for n in results.locator("[data-open]").all_inner_texts()
        if n.strip() in ("notities.md", "notulen.odt")
    ]
    assert len(names) == 2, names
    first, second = (n.strip() for n in names)
    results.get_by_text(first, exact=True).click()
    expect(page.get_by_role("dialog", name=first, exact=True)).to_be_visible()
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("dialog", name=second, exact=True)).to_be_visible()
    expect(page).to_have_url(re.compile(rf"q=no.*bestand={library.ids[second]}"))
    page.go_back()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/zoeken\?q=no$"))
    expect(results.get_by_text(first, exact=True)).to_be_visible()
    # Deep link: de viewer meteen, sluiten laat de resultaten staan
    page.goto(f"/zoeken?q=no&bestand={library.ids['notulen.odt']}")
    viewer = page.get_by_role("dialog", name="notulen.odt", exact=True)
    expect(viewer).to_be_visible()
    viewer.get_by_role("button", name="Sluiten", exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/zoeken\?q=no$"))
    expect(results.get_by_text("notities.md", exact=True)).to_be_visible()


def test_pdf_bladeren_en_inhoudsopgave(context_as, library: Library):
    page = open_folder(context_as, library)
    viewer = open_file(page, "boek.pdf")
    box = viewer.get_by_role("spinbutton")
    expect(box).to_have_value("1")
    first = viewer.get_by_role("img", name="Pagina i", exact=True)
    expect(first).to_be_visible()
    page.wait_for_function(
        """() => [...document.querySelectorAll(".page img")].some(
          (i) => i.complete && i.naturalWidth > 0)"""
    )
    page.keyboard.press("PageDown")
    expect(box).to_have_value("2")
    page.keyboard.press("PageDown")
    expect(box).to_have_value("3")
    page.keyboard.press("PageUp")
    expect(box).to_have_value("2")
    # De inhoudsopgave (T): Hoofdstuk 1 staat op pagina 3
    page.keyboard.press("t")
    toc = viewer.get_by_role("navigation", name="Inhoudsopgave")
    expect(toc).to_be_visible()
    toc.get_by_role("button", name="Paragraaf 1.1", exact=True).click()
    expect(box).to_have_value("4")
    # Een paginanummer typen
    box.fill("8")
    box.press("Enter")
    expect(box).to_have_value("8")
    expect(viewer.get_by_role("img", name="Pagina 6", exact=True)).to_be_visible()
    # Pagina's komen via het token van /document
    srcs = viewer.locator(".page img").evaluate_all("els => els.map((e) => e.currentSrc || e.src)")
    assert srcs and all("/pages/" in s and "token=" in s for s in srcs)


def test_tekst_codering_wisselen(context_as, library: Library):
    page = open_folder(context_as, library)
    viewer = open_file(page, "oud.txt")
    region = viewer.get_by_role("region", name="Inhoud van het bestand")
    expect(region).to_contain_text("Café crème – €5 “oud”")
    select = viewer.get_by_label("Tekencodering")
    expect(select).to_have_value("windows-1252")
    select.select_option("utf-8")
    expect(region).to_contain_text("Caf�")
    select.select_option("windows-1252")
    expect(region).to_contain_text("Café crème")


def test_markdown_csv_en_code(context_as, library: Library):
    page = open_folder(context_as, library)
    viewer = open_file(page, "notities.md")
    expect(viewer.get_by_role("heading", name="Notities", exact=True)).to_be_visible()
    # Geen javascript:-link en geen script uit de tekst: alleen tekst
    expect(viewer.locator("a[href^='javascript']")).to_have_count(0)
    expect(viewer.locator(".md script")).to_have_count(0)
    expect(viewer.get_by_text("<script>alert(1)</script>")).to_be_visible()
    viewer.get_by_role("button", name="Als tekst", exact=True).click()
    expect(viewer.get_by_role("region", name="Inhoud van het bestand")).to_contain_text(
        "# Notities"
    )

    page.keyboard.press("Shift+ArrowRight")  # notulen.odt
    page.keyboard.press("Shift+ArrowRight")  # oud.txt
    page.keyboard.press("Shift+ArrowRight")  # tabel.csv
    viewer = page.get_by_role("dialog", name="tabel.csv", exact=True)
    expect(viewer.get_by_role("cell", name="Jansen, Piet", exact=True)).to_be_visible()
    expect(viewer.get_by_role("columnheader", name="stad", exact=True)).to_be_visible()

    page.goto(f"/map/{library.folder}?bestand={library.ids['data.json']}")
    viewer = page.get_by_role("dialog", name="data.json", exact=True)
    expect(viewer.locator(".tok-string").first).to_have_text('"naam"')
    expect(viewer.locator(".tok-number")).to_have_text("42")


def test_kantoordocumenten(context_as, library: Library):
    page = open_folder(context_as, library)
    cases = {
        "verslag.docx": ("heading", "Jaarverslag"),
        "cijfers.xlsx": ("cell", "Appels"),
        "dias.pptx": ("heading", "Welkom bij de presentatie"),
        "notulen.odt": ("heading", "Notulen"),
    }
    for name, (role, text) in cases.items():
        page.goto(f"/map/{library.folder}?bestand={library.ids[name]}")
        viewer = page.get_by_role("dialog", name=name, exact=True)
        expect(
            viewer.get_by_text("Vereenvoudigd voorbeeld zonder opmaak", exact=True)
        ).to_be_visible()
        expect(viewer.get_by_role(role, name=text, exact=True)).to_be_visible()


def playing(page) -> bool:
    return page.evaluate(
        """() => { const m = document.querySelector("dialog video, dialog audio");
          return !!m && !m.error && m.currentTime > 0.2; }"""
    )


def test_video_en_audio(context_as, library: Library):
    """webm (VP9/Opus) en mp3 spelen; mp4/H.264 speelt of geeft de eerlijke melding,
    afhankelijk van wat deze Chromium kan (canPlayType)."""
    page = open_folder(context_as, library)
    page.goto(f"/map/{library.folder}?bestand={library.ids['clip.webm']}")
    viewer = page.get_by_role("dialog", name="clip.webm", exact=True)
    expect(viewer.locator("video")).to_be_visible()
    page.wait_for_function(
        """() => { const m = document.querySelector("dialog video");
          return m && !m.error && m.currentTime > 0.2; }""",
        timeout=15_000,
    )
    # K/spatie pauzeert, M dempt, J/L springen
    page.keyboard.press("k")
    page.wait_for_function("() => document.querySelector('dialog video').paused")
    page.keyboard.press("m")
    assert page.evaluate("() => document.querySelector('dialog video').muted")
    page.keyboard.press("l")
    page.wait_for_function("() => document.querySelector('dialog video').currentTime >= 2.5")
    page.keyboard.press("j")
    page.wait_for_function("() => document.querySelector('dialog video').currentTime < 1")

    page.goto(f"/map/{library.folder}?bestand={library.ids['liedje.mp3']}")
    viewer = page.get_by_role("dialog", name="liedje.mp3", exact=True)
    expect(viewer.locator("audio")).to_be_attached()
    page.wait_for_function(
        """() => { const m = document.querySelector("dialog audio");
          return m && !m.error && m.currentTime > 0.2; }""",
        timeout=15_000,
    )

    page.goto(f"/map/{library.folder}?bestand={library.ids['film.mp4']}")
    viewer = page.get_by_role("dialog", name="film.mp4", exact=True)
    can = page.evaluate(
        """() => document.createElement("video").canPlayType('video/mp4; codecs="avc1.4d401e"')"""
    )
    if can:
        page.wait_for_function(
            """() => { const m = document.querySelector("dialog video");
              return m && !m.error && m.currentTime > 0.2; }""",
            timeout=15_000,
        )
    else:
        message = viewer.get_by_text("Deze browser kan dit formaat niet afspelen", exact=True)
        expect(message).to_be_visible(timeout=15_000)
        expect(viewer.get_by_text(re.compile("h264"))).to_be_visible()
        help_box = viewer.locator(".viewer-message")
        expect(help_box.get_by_role("button", name="Downloaden", exact=True)).to_be_visible()


def test_toetsenbord_downloaden_en_hulp(context_as, library: Library):
    """D downloadt, ? toont de sneltoetsen van de viewer, de knoppen werken met Tab en Enter."""
    page = open_folder(context_as, library)
    viewer = open_file(page, "oud.txt")
    with page.expect_download() as download:
        page.keyboard.press("d")
    assert download.value.suggested_filename == "oud.txt"
    page.keyboard.press("?")
    help_dialog = page.get_by_role("dialog", name=re.compile("Sneltoetsen"))
    expect(help_dialog).to_be_visible()
    expect(help_dialog.get_by_text("Vorig bestand (ook ingezoomd)")).to_be_visible()
    page.keyboard.press("Escape")
    expect(help_dialog).to_be_hidden()
    expect(viewer).to_be_visible()
    # Met Tab naar "Volgende" en Enter
    viewer.get_by_role("button", name="Volgende", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("dialog", name="tabel.csv", exact=True)).to_be_visible()


def test_publieke_link_viewer(server: Server, new_context, library: Library):
    """De viewer achter een publieke leeslink: de pagina's van een PDF komen via de sleutel
    van de link, en ← → gaan door de map."""
    link = server.call(
        library.owner,
        "POST",
        f"/api/v1/files/{library.folder}/links",
        json={"permission": "read"},
        expect=201,
    ).json()
    page = new_context().new_page()
    page.goto(f"/s/{link_token(link)}")
    expect(page.get_by_role("heading", level=1)).to_have_text("Voorbeelden")
    rows = page.get_by_role("row").filter(has_not=page.get_by_role("columnheader"))
    expect(rows).to_have_count(len(ORDER))
    page.get_by_role("button", name="boek.pdf", exact=True).click()
    viewer = page.get_by_role("dialog", name="boek.pdf", exact=True)
    expect(viewer).to_be_visible()
    expect(page).to_have_url(re.compile("bestand="))
    page.wait_for_function(
        """() => [...document.querySelectorAll(".page img")].some(
          (i) => i.complete && i.naturalWidth > 0 && i.src.includes("/api/v1/public/pages/"))"""
    )
    page.keyboard.press("ArrowRight")
    viewer = page.get_by_role("dialog", name="cijfers.xlsx", exact=True)
    expect(viewer.get_by_role("cell", name="Appels", exact=True)).to_be_visible()
    page.keyboard.press("ArrowRight")
    page.wait_for_function(
        """() => { const m = document.querySelector("dialog video");
          return m && !m.error && m.currentTime > 0.2; }""",
        timeout=15_000,
    )
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page).not_to_have_url(re.compile("bestand="))


@pytest.mark.slow
def test_pdf_pagina_s_na_het_verlopen_van_het_token(browser, tmp_path_factory):
    """Klaar als: PDF-pagina's blijven laden na de levensduur van het token. Een eigen server
    met download-tokens van 1 minuut; de viewer haalt halverwege een nieuw /document, dus een
    pagina die pas na 70 s in beeld komt, laadt nog."""
    server = Server(
        tmp_path_factory.mktemp("p4-token") / "data",
        env={"PLATFORM_DOWNLOAD_TOKEN_MINUTES": "1"},
    )
    server.start()
    browsers = Browsers(browser, server)
    try:
        admin = make_admin(server)
        owner = create_user(server, admin, name="Lezer")
        pdf = outlined_pdf()
        node = server.call(
            owner,
            "PUT",
            f"/api/v1/files/{owner.root_id}/upload",
            params={"name": "lang.pdf"},
            content=pdf,
            expect=201,
        ).json()["id"]
        wait_for_previews(server, owner, [node])
        page = browsers.context_as(owner).new_page()
        documents = []
        page.on(
            "request",
            lambda r: documents.append(r.url) if "/document" in r.url else None,
        )
        page.goto(f"/?bestand={node}")
        viewer = page.get_by_role("dialog", name="lang.pdf", exact=True)
        expect(viewer.get_by_role("img", name="Pagina i", exact=True)).to_be_visible()
        first_token = viewer.locator(".page img").first.get_attribute("src")
        page.wait_for_timeout(70_000)
        assert len(documents) >= 2, documents  # op tijd vernieuwd
        box = viewer.get_by_role("spinbutton")
        box.fill("9")
        box.press("Enter")
        page.wait_for_function(
            """() => [...document.querySelectorAll(".page img")].some(
              (i) => i.alt === "Pagina 7" && i.complete && i.naturalWidth > 0)""",
            timeout=20_000,
        )
        late = viewer.get_by_role("img", name="Pagina 7", exact=True).get_attribute("src")
        assert late and first_token and late.split("token=")[1] != first_token.split("token=")[1]
        assert not viewer.locator(".page-failed").count()
    finally:
        problems = browsers.close_all()
        server.stop()
    assert not problems, problems
