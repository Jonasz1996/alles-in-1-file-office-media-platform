"""Foto's en albums (P5) in de browser: de tijdlijn met toetsenbord en scrubber, selecteren met
Shift en Ctrl/Cmd, "Toevoegen aan album", de viewer met ←/→, een album delen met een tweede
gebruiker (alleen bekijken) en een publieke albumlink. Alles met het toetsenbord waar dat kan
(docs/fase-3/toetsenbord-p5.md).

De zip van een album gaat via P2's archive/link: de test downloadt hem en kijkt wat erin zit."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timedelta

import pytest
from PIL import Image
from playwright.sync_api import expect

import seed

pytestmark = pytest.mark.p5

PHOTOS = 40
GRID = "[role=grid]"
FOCUSED = '[role=gridcell][tabindex="0"]'


@pytest.fixture
def library(server, user_factory, seeded):
    """Fien met 40 foto's (elke twee dagen één, de nieuwste eerst) en Bas, die niets heeft."""
    fien = user_factory(name="Fien Foto")
    bas = user_factory(name="Bas Bekijker")

    def fill(data_dir):
        blobs = seed.make_blobs(data_dir, PHOTOS)
        seed.seed_folder(
            data_dir, fien, PHOTOS, blobs, name="Vakantie", photos_only=True,
            step=timedelta(days=2),
        )  # fmt: skip

    seeded(fill)
    months = server.call(fien, "GET", "/api/v1/photos/months", expect=200).json()["months"]
    assert sum(m["count"] for m in months) == PHOTOS
    return {"fien": fien, "bas": bas, "months": months}


def cell(page, i: int):
    return page.locator(f'[role=gridcell][data-index="{i}"]')


def toast(page, text: str):
    """Een melding (niet de kopie voor de schermlezer)."""
    return page.get_by_role("region", name="Meldingen").get_by_text(text)


def selection_bar(page):
    return page.get_by_role("toolbar", name="Selectie", exact=True)


def test_tijdlijn_selecteren_album_en_delen(server, library, context_as, new_context):
    fien, bas = library["fien"], library["bas"]
    page = context_as(fien).new_page()
    page.goto("/fotos")
    grid = page.get_by_role("grid", name="Tijdlijn", exact=True)
    expect(grid).to_be_visible()
    # Een kop per maand (columnheader), en de thumbnails komen van /api/v1/previews/
    expect(page.get_by_role("columnheader")).to_have_count(len(library["months"]))
    expect(cell(page, 0).locator("img")).to_have_attribute("src", re.compile("^/api/v1/previews/"))
    expect(cell(page, 0)).to_have_attribute("aria-label", re.compile(r"^foto-00000\.webp, "))

    # --- het toetsenbord: één tabstop, pijltjes, Spatie, Shift+pijltje ---
    page.locator(FOCUSED).focus()
    expect(cell(page, 0)).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(cell(page, 1)).to_be_focused()
    page.keyboard.press("Space")
    expect(cell(page, 1)).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Shift+ArrowRight")
    page.keyboard.press("Shift+ArrowRight")
    expect(selection_bar(page).get_by_text("3 geselecteerd", exact=True)).to_be_visible()
    for i in (1, 2, 3):
        expect(cell(page, i)).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Escape")
    expect(selection_bar(page)).to_be_hidden()

    # --- de muis: Ctrl/Cmd-klik erbij, Shift-klik een reeks ---
    cell(page, 5).click(modifiers=["ControlOrMeta"])
    cell(page, 8).click(modifiers=["Shift"])
    expect(selection_bar(page).get_by_text("4 geselecteerd", exact=True)).to_be_visible()
    for i in (5, 6, 7, 8):
        expect(cell(page, i)).to_have_attribute("aria-selected", "true")
    # In de selectiemodus voegt een gewone klik toe in plaats van te openen
    cell(page, 10).click()
    expect(selection_bar(page).get_by_text("5 geselecteerd", exact=True)).to_be_visible()
    cell(page, 10).click()
    expect(selection_bar(page).get_by_text("4 geselecteerd", exact=True)).to_be_visible()

    # --- Toevoegen aan album (A), een nieuw album ---
    page.keyboard.press("a")
    dialog = page.get_by_role("dialog", name="4 items toevoegen aan album", exact=True)
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("radio", name="Nieuw album", exact=True)).to_be_checked()
    dialog.get_by_label("Naam", exact=True).fill("Zomer")
    dialog.get_by_role("button", name="Toevoegen", exact=True).click()
    expect(dialog).to_be_hidden()
    expect(toast(page, "4 toegevoegd aan Zomer.")).to_be_visible()
    albums = server.call(fien, "GET", "/api/v1/albums", expect=200).json()["albums"]
    assert [(a["name"], a["count"]) for a in albums] == [("Zomer", 4)]
    album_id = albums[0]["id"]
    page.keyboard.press("Escape")
    expect(selection_bar(page)).to_be_hidden()

    # --- de viewer: Enter opent, ←/→ gaan door de tijdlijn, sluiten zet de focus terug ---
    cell(page, 2).focus()
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name="foto-00002.webp", exact=True)
    expect(viewer).to_be_visible()
    expect(page).to_have_url(re.compile(r"[?&]bestand="))
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("dialog", name="foto-00003.webp", exact=True)).to_be_visible()
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    viewer = page.get_by_role("dialog", name="foto-00001.webp", exact=True)
    expect(viewer).to_be_visible()
    viewer.get_by_role("button", name="Sluiten", exact=True).click()
    expect(page).not_to_have_url(re.compile(r"[?&]bestand="))
    expect(cell(page, 1)).to_be_focused()

    # --- de scrubber: End naar de oudste maand, Home terug ---
    scrubber = page.get_by_role("slider", name="Naar maand", exact=True)
    scrubber.focus()
    page.keyboard.press("End")
    expect(scrubber).to_have_attribute("aria-valuenow", str(len(library["months"])))
    page.wait_for_function("() => window.scrollY > 0")
    page.keyboard.press("Home")
    expect(scrubber).to_have_attribute("aria-valuenow", "1")
    page.wait_for_function("() => window.scrollY < 400")

    # --- het album: de kaart op /albums, de pagina, delen (S) opent de deeldialoog ---
    page.goto("/albums")
    card = page.get_by_role("link", name=re.compile("^Zomer"))
    expect(card).to_contain_text("4 foto's en video's")
    card.click()
    expect(page).to_have_url(re.compile(f"/album/{album_id}$"))
    expect(page.get_by_role("heading", name="Zomer", level=1)).to_be_visible()
    expect(
        page.get_by_role("grid", name="Foto's in Zomer", exact=True).get_by_role("gridcell")
    ).to_have_count(4)
    page.locator(FOCUSED).focus()
    page.keyboard.press("s")
    share = page.get_by_role("dialog", name="Zomer delen", exact=True)
    expect(share).to_be_visible()
    page.keyboard.press("Escape")
    expect(share).to_be_hidden()

    # Downloaden als zip (P2: archive/link): het hele album, de vier foto's en niets anders
    with page.expect_download() as zipped:
        page.get_by_role("button", name="Downloaden", exact=True).click()
    assert zipped.value.suggested_filename == "Zomer.zip"
    with zipfile.ZipFile(zipped.value.path()) as zf:
        assert zf.testzip() is None
        assert sorted(zf.namelist()) == [f"foto-0000{i}.webp" for i in (5, 6, 7, 8)]

    # Delen met Bas (alleen bekijken; schrijven kan een album niet)
    r = server.call(
        fien, "POST", f"/api/v1/files/{album_id}/shares",
        json={"user_id": bas.id, "permission": "write"}, expect=422,
    )  # fmt: skip
    assert r.json()["detail"]["code"] == "album_read_only"
    server.call(
        fien, "POST", f"/api/v1/files/{album_id}/shares",
        json={"user_id": bas.id, "permission": "read"}, expect=201,
    )  # fmt: skip

    # --- Bas: ziet het onder Gedeeld met mij en bekijkt een foto, zonder iets te wijzigen ---
    other = context_as(bas).new_page()
    other.goto("/gedeeld")
    # Een album opent op zijn eigen pagina (/album/:id), niet in de viewer
    other.get_by_role("link", name="Zomer", exact=True).click()
    expect(other).to_have_url(re.compile(f"/album/{album_id}$"))
    expect(other.get_by_role("dialog")).to_have_count(0)
    expect(other.get_by_role("heading", name="Zomer", level=1)).to_be_visible()
    expect(other.get_by_text("van Fien Foto", exact=False)).to_be_visible()
    for name in ("Delen…", "Hernoemen", "Album verwijderen"):
        expect(other.get_by_role("button", name=name, exact=True)).to_have_count(0)
    cells = other.get_by_role("gridcell")
    expect(cells).to_have_count(4)
    other.locator(FOCUSED).focus()
    other.keyboard.press("Enter")
    expect(other.get_by_role("dialog", name="foto-00005.webp", exact=True)).to_be_visible()
    other.keyboard.press("ArrowRight")
    expect(other.get_by_role("dialog", name="foto-00006.webp", exact=True)).to_be_visible()
    # Downloaden mag (lezen), toevoegen of weghalen niet
    item = cells.first.get_attribute("data-index")
    assert item == "0"
    ids = server.call(bas, "GET", f"/api/v1/albums/{album_id}/ids", expect=200).json()["ids"]
    server.call(bas, "POST", f"/api/v1/files/{ids[0]}/download-link", expect=200)
    # Ook de zip van het album: de foto's zijn van Fien, Bas leest ze via het gedeelde album
    url = server.call(
        bas, "POST", "/api/v1/archive/link", json={"ids": ids, "name": "Zomer"}, expect=200
    ).json()["url"]
    with zipfile.ZipFile(io.BytesIO(server.request("GET", url, expect=200).content)) as zf:
        assert sorted(zf.namelist()) == [f"foto-0000{i}.webp" for i in (5, 6, 7, 8)]
    for path in (f"/api/v1/albums/{album_id}/items", f"/api/v1/albums/{album_id}/items/remove"):
        r = server.call(bas, "POST", path, json={"node_ids": ids[:1]})
        assert r.status_code in (403, 404), (path, r.status_code)

    # --- een publieke link op het album: bekijken en downloaden zonder in te loggen ---
    link = server.call(
        fien, "POST", f"/api/v1/files/{album_id}/links", json={"permission": "read"}, expect=201
    ).json()["link"]["url"]
    anon = new_context().new_page()
    anon.goto(link)
    expect(anon.get_by_role("heading", name="Zomer", level=1)).to_be_visible()
    public_grid = anon.get_by_role("grid", name="Foto's in Zomer", exact=True)
    expect(public_grid.get_by_role("gridcell")).to_have_count(4)
    expect(public_grid.locator("img").first).to_have_attribute(
        "src", re.compile("^/api/v1/previews/")
    )
    anon.locator(FOCUSED).focus()
    anon.keyboard.press("Enter")
    photo = anon.get_by_role("dialog", name="foto-00005.webp", exact=True)
    expect(photo).to_be_visible()
    anon.keyboard.press("ArrowRight")
    photo = anon.get_by_role("dialog", name="foto-00006.webp", exact=True)
    expect(photo).to_be_visible()
    with anon.expect_download() as download:
        photo.get_by_role("button", name="Downloaden", exact=True).click()
    assert download.value.suggested_filename == "foto-00006.webp"
    # Sluiten: de focus op de laatst getoonde foto (niet op die waarmee je begon)
    anon.keyboard.press("Escape")
    expect(photo).to_be_hidden()
    expect(public_grid.locator('[role=gridcell][data-index="1"]')).to_be_focused()


def test_naar_prullenbak_en_terug(server, library, context_as):
    """Delete (of Mod+Backspace) gooit de selectie weg; Ongedaan maken zet hem terug."""
    fien = library["fien"]
    page = context_as(fien).new_page()
    page.goto("/fotos")
    expect(cell(page, 0)).to_be_visible()
    page.locator(FOCUSED).focus()
    # Selectie wissen met de knop in de balk: de balk gaat weg, de focus terug in het raster
    page.keyboard.press("Space")
    selection_bar(page).get_by_role("button", name="Selectie wissen", exact=True).focus()
    page.keyboard.press("Enter")
    expect(selection_bar(page)).to_be_hidden()
    expect(cell(page, 0)).to_be_focused()
    page.keyboard.press("Space")
    page.keyboard.press("Shift+ArrowRight")
    expect(selection_bar(page).get_by_text("2 geselecteerd", exact=True)).to_be_visible()
    page.keyboard.press("Delete")
    expect(toast(page, "2 items naar de prullenbak")).to_be_visible()
    expect(cell(page, 0)).to_have_attribute("aria-label", re.compile(r"^foto-00002\.webp"))
    total = sum(
        m["count"] for m in server.call(fien, "GET", "/api/v1/photos/months").json()["months"]
    )
    assert total == PHOTOS - 2
    page.get_by_role("button", name="Ongedaan maken", exact=True).click()
    expect(cell(page, 0)).to_have_attribute("aria-label", re.compile(r"^foto-00000\.webp"))


def test_viewer_blijft_open_als_zijn_maand_verandert(server, library, context_as):
    """Een nieuwe foto in de maand die je bekijkt (een upload elders): de viewer blijft dezelfde
    (geen nieuwe start van een video, de zoom blijft) terwijl de maand opnieuw laadt."""
    fien = library["fien"]
    first = server.call(fien, "GET", "/api/v1/photos", expect=200).json()["items"][0]
    page = context_as(fien).new_page()
    page.goto("/fotos")
    expect(cell(page, 0)).to_be_visible()
    cell(page, 0).focus()
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name=first["name"], exact=True)
    expect(viewer).to_be_visible()
    viewer.evaluate("(el) => { el.dataset.e2eMark = 'zelfde'; }")

    # Een foto in dezelfde maand, een minuut ouder dan die in de viewer
    taken = datetime.fromisoformat(first["modified_at"].replace("Z", "+00:00"))
    image = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 40, 40)).save(image, format="PNG")
    mtime = int((taken - timedelta(minutes=1)).timestamp() * 1000)
    server.call(
        fien, "PUT", f"/api/v1/files/{fien.root_id}/upload",
        params={"name": "erbij.png", "mtime": mtime}, content=image.getvalue(), expect=201,
    )  # fmt: skip
    # De tijdlijn achter de viewer laadt de maand opnieuw (met de nieuwe foto erin)
    expect(page.locator('[role=gridcell][aria-label^="erbij.png"]')).to_have_count(1)
    expect(viewer).to_be_visible()
    assert viewer.evaluate("(el) => el.dataset.e2eMark") == "zelfde"
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("dialog", name="erbij.png", exact=True)).to_be_visible()
