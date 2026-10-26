"""Het verhaal van de integratie (ontwerp §5.4, "Full e2e"): één server vanaf de eerste start,
met de productie-build op localhost, en de stappen achter elkaar zoals iemand het platform in
gebruik neemt. Zie docs/fase-3/integratie.md voor wat elke stap dekt en welke andere e2e-tests
een stap verder uitdiepen.

De stappen bouwen op elkaar voort (de mensen, hun 2FA, de bestanden); een stap die faalt,
laat de stappen die erop voortbouwen meteen falen met de naam van de stap die ontbrak. De stap
met 10.000 bestanden is `slow` (in CI's e2e-job draait hij wel mee) en niets bouwt erop voort.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect

from conftest import PASSWORD, cdp_profile, unique_email
from verhaal import (
    API,
    MOD,
    ROWS,
    Persoon,
    Verhaal,
    blur,
    enter_list,
    expect_focus,
    row,
    toast,
    toasts,
    wait_until,
    zip_names,
)

MB = 1024 * 1024


@pytest.fixture(scope="module")
def verhaal(browser: Any, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Verhaal]:
    v = Verhaal(tmp_path_factory.mktemp("integratie"), browser)
    try:
        yield v
    finally:
        problems = v.stop()
    assert not problems, problems


@pytest.fixture(autouse=True)
def bewaking(verhaal: Verhaal) -> Iterator[None]:
    """Na elke stap: wat de bewaking (CSP, i18n, fouten) in een van de contexts zag."""
    yield
    problems = verhaal.problemen()
    if problems:
        pytest.fail("Problemen in de browser:\n" + "\n".join(problems), pytrace=False)


def stap(verhaal: Verhaal, naam: str) -> None:
    verhaal.stappen.add(naam)


def stable_count(count: Any) -> int:
    """Het aantal in "… geselecteerd" zodra het niet meer verandert (na een reeks toetsen)."""
    expect(count).to_have_text(re.compile(r"^[\d.]+ geselecteerd$"))
    seen = None
    for _ in range(50):
        text = count.inner_text()
        if text == seen:
            return int(text.split()[0].replace(".", ""))
        seen = text
        time.sleep(0.3)
    raise AssertionError(f"de selectie bleef veranderen: {seen}")


# --- 1. De eerste start ---


def test_01_eerste_start_met_2fa_en_twee_accounts(verhaal: Verhaal) -> None:
    """De eerste start: het eerste account (de beheerder) in het formulier, 2FA is verplicht en
    gaat aan met een code uit de app; daarna maakt de beheerder een tweede gebruiker en een
    gast. De tweede gebruiker logt in en moet ook eerst 2FA aanzetten."""
    v = verhaal
    anna = Persoon("Anna Beheer", unique_email("anna"), PASSWORD, "admin")
    bram = Persoon("Bram Tweede", unique_email("bram"), PASSWORD, "user")
    gijs = Persoon("Gijs Gast", unique_email("gijs"), PASSWORD, "guest")
    v.mensen.update(anna=anna, bram=bram, gijs=gijs)

    page = v.page(path="/")
    expect(page.get_by_role("heading", name="Welkom", exact=True)).to_be_visible()
    page.get_by_label("E-mailadres", exact=True).fill(anna.email)
    page.get_by_label("Naam", exact=True).fill(anna.naam)
    page.get_by_label("Wachtwoord", exact=True).fill(anna.password)
    page.get_by_role("button", name="Account aanmaken", exact=True).click()
    enable_2fa_in_ui(v, page, anna)
    nav = page.get_by_role("navigation", name="Hoofdmenu")
    expect(nav.get_by_role("link", name="Beheer", exact=True)).to_be_visible()
    me = v.api(anna, "GET", f"{API}/auth/me", expect=200).json()
    assert me["role"] == "admin" and me["totp_enabled"] and not me["totp_setup_required"]
    # Een tweede /auth/setup kan niet meer
    r = v.server.request(
        "POST",
        f"{API}/auth/setup",
        json={"email": unique_email("x"), "display_name": "X", "password": PASSWORD},
    )
    assert r.status_code == 409, r.text

    # De beheerder maakt Bram (gebruiker, 1 GB) en Gijs (gast) met N in /beheer/gebruikers
    page.goto("/beheer/gebruikers")
    expect(page.get_by_role("heading", name="Gebruikers", exact=True)).to_be_visible()
    for persoon, rol, quota in ((bram, "Gebruiker", "1"), (gijs, "Gast", "")):
        blur(page)
        page.keyboard.press("n")
        dialog = page.get_by_role("dialog", name="Nieuwe gebruiker", exact=True)
        expect(dialog.get_by_label("Naam", exact=True)).to_be_focused()
        page.keyboard.type(persoon.naam)
        dialog.get_by_label("E-mailadres", exact=True).fill(persoon.email)
        dialog.get_by_label("Wachtwoord", exact=True).fill(persoon.password)
        dialog.get_by_label("Rol", exact=True).select_option(label=rol)
        dialog.get_by_label("Quotum in GB", exact=True).fill(quota)
        dialog.get_by_role("button", name="Toevoegen", exact=True).click()
        expect(dialog).to_be_hidden()
        expect(toast(page, f"{persoon.naam} is toegevoegd.")).to_be_visible()
        expect(page.get_by_role("row").filter(has_text=persoon.email)).to_contain_text(rol)
    users = {u["email"]: u for u in v.api(anna, "GET", f"{API}/users", expect=200).json()}
    assert users[bram.email]["role"] == "user" and users[bram.email]["quota_bytes"] == 1024**3
    assert users[gijs.email]["role"] == "guest"

    # Bram logt in met het formulier: eerst 2FA aanzetten, en tot dan niets anders
    other = v.page(path="/")
    expect(other.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()
    other.get_by_label("E-mailadres", exact=True).fill(bram.email)
    other.get_by_label("Wachtwoord", exact=True).fill(bram.password)
    other.get_by_role("button", name="Inloggen", exact=True).click()
    enable_2fa_in_ui(v, other, bram)
    expect(other.get_by_role("navigation", name="Hoofdmenu")).to_be_visible()
    v.stand["bram_page"] = other

    # Gijs (gast) zet 2FA via de API aan; hij heeft geen eigen bestanden
    data = v.login(gijs)
    assert data["user"]["totp_setup_required"] and data["user"]["role"] == "guest"
    r = v.api(gijs, "GET", f"{API}/shared-with-me")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "totp_setup_required"
    enable_2fa_api(v, gijs)
    assert v.api(gijs, "GET", f"{API}/shared-with-me", expect=200).json() == []
    v.stand["anna_page"] = page
    stap(v, "eerste start")


def enable_2fa_in_ui(v: Verhaal, page: Any, persoon: Persoon) -> None:
    """Na het eerste inloggen: alleen /account met "2FA verplicht"; aanzetten met de sleutel die
    de pagina toont, de herstelcodes bewaren."""
    from app import totp

    expect(page.get_by_text("Op dit platform is 2FA verplicht.", exact=True)).to_be_visible()
    expect(page.get_by_role("navigation", name="Hoofdmenu")).to_have_count(0)
    data = v.login(persoon)  # nog zonder code: 2FA staat nog uit
    assert data["user"]["totp_setup_required"]
    r = v.api(persoon, "GET", f"{API}/files/root")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "totp_setup_required"

    page.get_by_role("button", name="2FA aanzetten", exact=True).click()
    secret = page.locator(".account code").first.inner_text().replace(" ", "")
    persoon.secret = secret
    step = totp.current_step()
    persoon.last_step = step
    page.get_by_label("2. Vul de code in die de app nu toont", exact=True).fill(
        totp.code_at(secret, step)
    )
    page.get_by_label("3. Je wachtwoord, ter bevestiging", exact=True).fill(persoon.password)
    page.get_by_role("button", name="Aanzetten", exact=True).click()
    expect(page.get_by_role("heading", name="Herstelcodes", exact=True)).to_be_visible()
    codes = page.locator("ul.codes code").all_inner_texts()
    assert len(codes) == 10, codes
    persoon.recovery = list(codes)
    page.get_by_role("button", name="Ik heb ze bewaard", exact=True).click()
    # Het token van daarnet is ingetrokken (2FA aan logt de rest uit): opnieuw, nu met een code
    persoon.account.token = None  # type: ignore[union-attr]


def enable_2fa_api(v: Verhaal, persoon: Persoon) -> None:
    from conftest import totp_code

    secret = v.api(persoon, "POST", f"{API}/auth/2fa/setup", expect=200).json()["secret"]
    code, step = totp_code(secret)
    codes = v.api(
        persoon,
        "POST",
        f"{API}/auth/2fa/enable",
        json={"code": code, "password": persoon.password},
        expect=200,
    ).json()["recovery_codes"]
    persoon.secret, persoon.last_step, persoon.recovery = secret, step, list(codes)
    persoon.account.token = None  # type: ignore[union-attr]


# --- 2. Een map uploaden ---


def test_02_map_uploaden_met_botsing_en_hervatten_na_herladen(
    verhaal: Verhaal, tmp_path: Path
) -> None:
    """Een map met submappen (Shift+U), waarin een map dezelfde naam heeft als een bestand dat
    er al staat (die mislukt zichtbaar), en een groot bestand dat na herladen verder gaat."""
    v = verhaal
    v.nodig("eerste start")
    anna = v.mensen["anna"]
    reis = v.mkdir(anna, anna.root, "Reis")
    v.put(anna, reis, "Strand", b"ik ben een bestand")

    root = tmp_path / "Reis"
    (root / "Dag 1").mkdir(parents=True)
    (root / "Strand").mkdir()
    (root / "a.txt").write_text("a")
    (root / "Dag 1" / "foto.txt").write_text("foto")
    (root / "Strand" / "c.txt").write_text("ccc")
    film = root / "Dag 1" / "film.bin"
    film.write_bytes(bytes(range(256)) * (6 * MB // 256))

    page = v.stand["anna_page"]
    page.goto("/")
    expect(page.get_by_role("button", name="Uploaden", exact=True)).to_be_visible()
    page.on("dialog", lambda d: d.accept())  # "Pagina verlaten?" bij het herladen
    cdp_profile(page.context, page, latency_ms=5, down_mbit=200, up_mbit=8)
    blur(page)
    with page.expect_file_chooser() as chooser:
        page.keyboard.press("Shift+U")
    assert chooser.value.element.get_attribute("webkitdirectory") is not None
    chooser.value.set_files(root)

    lade = page.get_by_role("region", name="Uploads", exact=True)
    expect(
        lade.get_by_text("Er staat al een bestand met de naam van map Reis/Strand")
    ).to_be_visible()

    def pending() -> list[dict]:
        return v.api(anna, "GET", f"{API}/tus", expect=200).json()

    wait_until(lambda: any(p["offset"] > 0 for p in pending()), what="bytes van film.bin")
    inside = v.children(anna, reis)
    assert set(inside) == {"Strand", "a.txt", "Dag 1"}, inside  # samengevoegd in "Reis"
    assert inside["Strand"]["kind"] == "file"

    page.reload()
    onderbroken = lade.get_by_role("region", name="Onderbroken uploads")
    expect(onderbroken.get_by_text("film.bin", exact=True)).to_be_visible()
    expect(onderbroken.get_by_text(re.compile(r"van 6,0 MB"))).to_be_visible()
    cdp_profile(page.context, page, latency_ms=2, down_mbit=400, up_mbit=400)
    with page.expect_file_chooser() as chooser:
        onderbroken.get_by_role("button", name="Kies film.bin opnieuw").click()
    chooser.value.set_files(film)
    dag = inside["Dag 1"]["id"]
    wait_until(lambda: "film.bin" in v.children(anna, dag), what="film.bin", seconds=60)
    assert v.children(anna, dag)["film.bin"]["size"] == film.stat().st_size
    assert set(v.children(anna, dag)) == {"foto.txt", "film.bin"}
    assert pending() == []
    expect(lade).to_have_count(0)
    v.stand["reis"] = reis
    stap(v, "map uploaden")


# --- 3. Tienduizend in één map ---


@pytest.mark.slow
def test_03_tienduizend_scrollen_selecteren_en_bulkacties(verhaal: Verhaal) -> None:
    """10.000 in één map: scrollen in lijst en raster, selecteren, in bulk verplaatsen,
    kopiëren en naar de prullenbak met Ongedaan maken, een zip, het contextmenu, en het
    toetsenbord (ook Mod+Backspace)."""
    import seed

    v = verhaal
    v.nodig("eerste start")
    anna = v.mensen["anna"]
    n = 10_000

    def fill(data_dir: Path) -> str:
        shas = seed.make_blobs(data_dir, 200)
        return seed.seed_folder(data_dir, anna.account, n, shas, name="Tienduizend")

    folder = v.server.seeded(fill)
    page = v.stand["anna_page"]
    page.goto(f"/map/{folder}")
    grid = page.locator("#inhoud [role=grid][aria-rowcount]")
    expect(grid).to_have_attribute("aria-rowcount", str(n + 1))
    expect(page.get_by_text(re.compile(r"geladen…$"))).to_have_count(0, timeout=30_000)

    # Scrollen in de lijst: het wiel (de rij met de focus blijft), dan End (de laatste rij) en
    # Home (de eerste)
    enter_list(page)
    page.mouse.move(640, 500)
    for _ in range(10):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(30)
    assert page.locator(ROWS).count() <= 200
    page.keyboard.press("End")
    last = page.locator(f'{ROWS}[aria-rowindex="{n + 1}"]')
    expect(last).to_contain_text("foto-09999.webp")
    expect(last).to_be_focused()
    page.keyboard.press("Home")
    expect(row(page, "Map 00000")).to_be_focused()

    # Het raster (G): ook daar End en Home, en niet meer dan 200 cellen
    page.keyboard.press("g")
    raster = page.locator("#inhoud [data-view=grid]")
    expect(raster).to_be_visible()
    raster.locator('[role=gridcell][tabindex="0"]').focus()
    page.keyboard.press("End")
    expect(raster.locator("[role=gridcell]").filter(has_text="foto-09999.webp")).to_be_focused()
    expect(raster.locator("img[src*='/api/v1/previews/']").first).to_be_visible()
    assert page.locator("#inhoud [role=gridcell][data-index]").count() <= 200
    page.keyboard.press("Home")
    page.keyboard.press("g")
    expect(page.locator("#inhoud [data-view=list]")).to_be_visible()

    bar = page.get_by_role("toolbar", name="Selectie", exact=True)
    count = bar.locator(".selection-count")

    # In bulk verplaatsen: de laatste rijen met End en Shift+PageUp, dan M naar Map 00000
    enter_list(page)
    page.keyboard.press("End")
    expect(last).to_be_focused()
    for _ in range(4):
        page.keyboard.press("Shift+PageUp")
    moved = stable_count(count)
    assert moved > 40, moved
    page.keyboard.press("m")
    picker = page.get_by_role("dialog", name=f"{moved} items verplaatsen naar", exact=True)
    folders = picker.get_by_role("list", name="Mappen", exact=True)
    expect(folders.get_by_role("button", name="Map 00000", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(picker.get_by_text("Hier zijn geen submappen.", exact=True)).to_be_visible()
    picker.get_by_role("button", name="Hierheen verplaatsen", exact=True).click()
    # Gelukt staat alleen in de live region (de lijst toont het al)
    expect(page.get_by_text(f"{moved} items verplaatst", exact=True)).to_be_attached()
    expect(grid).to_have_attribute("aria-rowcount", str(n + 1 - moved))
    listing = list(v.children(anna, folder))
    assert len(listing) == n - moved
    target = v.children(anna, folder)["Map 00000"]["id"]
    assert len(v.children(anna, target)) == moved

    # Kopiëren naar… (Shift+M): de laatste drie, door "Map 00020" te typen in de kiezer; en
    # dezelfde drie als zip (D)
    enter_list(page)
    page.keyboard.press("End")
    expect(page.locator(f'{ROWS}[aria-rowindex="{n + 1 - moved}"]')).to_be_focused()
    page.keyboard.press("Shift+ArrowUp")
    page.keyboard.press("Shift+ArrowUp")
    expect(count).to_have_text("3 geselecteerd")
    page.keyboard.press("Shift+M")
    picker = page.get_by_role("dialog", name="3 items kopiëren naar", exact=True)
    folders = picker.get_by_role("list", name="Mappen", exact=True)
    expect(folders.get_by_role("button", name="Map 00000", exact=True)).to_be_focused()
    page.keyboard.type("Map 00020")
    expect(folders.get_by_role("button", name="Map 00020", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(picker.get_by_text("Hier zijn geen submappen.", exact=True)).to_be_visible()
    picker.get_by_role("button", name="Hierheen kopiëren", exact=True).click()
    expect(page.get_by_text("3 items gekopieerd", exact=True)).to_be_attached()
    copies = v.children(anna, v.children(anna, folder)["Map 00020"]["id"])
    assert sorted(copies) == sorted(listing[-3:]), (copies, listing[-3:])
    expect(count).to_have_text("3 geselecteerd")
    with page.expect_download() as download:
        page.keyboard.press("d")
    assert zip_names(download.value.path().read_bytes()) == sorted(listing[-3:])
    page.keyboard.press("Escape")
    expect(count).to_have_count(0)

    # Alles naar de prullenbak (Mod+A, Delete) en met Ongedaan maken weer terug
    left = n - moved
    page.keyboard.press(f"{MOD}+a")
    expect(count).to_have_text(f"{left:,} geselecteerd".replace(",", "."))
    page.keyboard.press("Delete")
    undo = toasts(page).locator(".toast").filter(has_text="items staan in de prullenbak")
    expect(undo).to_be_visible(timeout=60_000)
    expect(page.get_by_text("Deze map is leeg.", exact=True)).to_be_visible()
    undo.get_by_role("button", name="Ongedaan maken", exact=True).click()
    expect(grid).to_have_attribute("aria-rowcount", str(left + 1), timeout=60_000)
    assert len(v.children(anna, folder)) == left

    # Het contextmenu: rechtsklik en Shift+F10, Escape zet de focus terug op de rij
    enter_list(page)
    page.keyboard.press("Home")
    target_row = row(page, "Map 00020")
    target_row.locator(".cell-date").click(button="right")
    menu = page.get_by_role("menu", name="Acties voor Map 00020")
    expect(menu.get_by_role("menuitem", name="Downloaden als zip", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    expect(target_row).to_be_focused()
    page.keyboard.press("Shift+F10")
    expect(menu).to_be_visible()
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)

    # Alleen het toetsenbord: ↓, Shift+↓ en Mod+Backspace; Mod+Z zet terug, en nog eens
    # Mod+Backspace op de rij met de focus
    page.keyboard.press("Escape")
    expect_focus(page, "Map 00020")
    page.keyboard.press("ArrowDown")
    expect_focus(page, "Map 00040")
    page.keyboard.press("Shift+ArrowDown")
    expect(count).to_have_text("2 geselecteerd")
    page.keyboard.press(f"{MOD}+Backspace")
    expect(row(page, "Map 00040")).to_have_count(0)
    expect(row(page, "Map 00060")).to_have_count(0)
    page.keyboard.press(f"{MOD}+z")
    expect(row(page, "Map 00040")).to_be_visible()
    expect(row(page, "Map 00060")).to_be_visible()
    enter_list(page)
    page.keyboard.press("Home")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    expect_focus(page, "Map 00040")
    page.keyboard.press(f"{MOD}+Backspace")
    expect(row(page, "Map 00040")).to_have_count(0)
    assert "Map 00040" not in v.children(anna, folder)
    assert len(v.children(anna, folder)) == left - 1
    v.stand["tienduizend"] = folder
    stap(v, "tienduizend")


# --- 4. Foto's: de tijdlijn, een album, delen ---

# De opnamedatums: EXIF is de kloktijd van de camera; creation_time van een video is UTC en
# wordt de kloktijd in Europe/Amsterdam (oudejaarsavond 23:30 UTC is daar al 1 januari)
EXIF_AT = "2024-05-01T12:30:00Z"
VIDEO_UTC = "2023-12-31T23:30:00Z"
VIDEO_AT = "2024-01-01T00:30:00Z"
PNG_MTIME = "2022-03-05T09:00:00+00:00"
PNG_AT = "2022-03-05T10:00:00Z"
# Zo stonden ze er voor het lezen van de inhoud (de terugvaldatum: lastModified)
OLD_MTIME = "2025-06-01T08:00:00+00:00"


def ms(iso: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def photo_cell(page: Any, label: str) -> Any:
    return page.locator(f'[role=gridcell][aria-label^="{label}, "]')


def test_04_fotos_tijdlijn_album_delen_en_publieke_link(verhaal: Verhaal, tmp_path: Path) -> None:
    """Foto's van voor het lezen van de metadata: de backfill zet de EXIF-datum en die van de
    video (UTC omgezet) alsnog. Een album uit de tijdlijn, alleen-bekijken gedeeld met Bram
    (geen Kopiëren, niet als map te downloaden) en met Gijs (de gast ziet alleen dat), een
    publieke link zonder GPS in enig antwoord, en de zip van het album."""
    import hashlib
    import io
    from datetime import datetime

    from PIL import Image

    import seed_p4

    v = verhaal
    v.nodig("eerste start", "map uploaden")
    anna, bram, gijs = v.mensen["anna"], v.mensen["bram"], v.mensen["gijs"]

    png = io.BytesIO()
    Image.new("RGB", (64, 48), (30, 140, 90)).save(png, format="PNG")
    clip = "-f lavfi -i testsrc=duration=2:size=160x120:rate=10 -c:v libvpx-vp9 -b:v 100k"
    bodies = {
        "zomer.jpg": seed_p4.jpeg_with_gps(),
        "oudjaar.webm": seed_p4._ffmpeg(
            tmp_path, "oudjaar.webm", *clip.split(), "-metadata", f"creation_time={VIDEO_UTC}"
        ),
        "zonder-datum.png": png.getvalue(),
    }
    mtimes = {"zomer.jpg": OLD_MTIME, "oudjaar.webm": OLD_MTIME, "zonder-datum.png": PNG_MTIME}
    fotos = v.mkdir(anna, anna.root, "Foto's")
    ids = {
        name: v.put(anna, fotos, name, body, mtime=ms(mtimes[name]))
        for name, body in bodies.items()
    }
    v.wait_for_previews(anna, list(ids.values()))

    # Zoals een bibliotheek van voor de opnamedatums: alleen de terugvaldatum, geen EXIF
    def before_meta(data_dir: Path) -> None:
        from sqlalchemy import select, update

        import seed
        from app.config import Settings
        from app.models import Blob, Node
        from app.photodate import stamp_values

        tz = Settings(data_dir=data_dir).timezone
        with seed.connect(data_dir) as conn:
            for name, body in bodies.items():
                sha = hashlib.sha256(body).hexdigest()
                blob = conn.execute(select(Blob.mime, Blob.preview_meta).where(Blob.sha256 == sha))
                mime, meta = blob.one()
                meta = {k: val for k, val in (meta or {}).items() if k != "exif"}
                conn.execute(
                    update(Blob)
                    .where(Blob.sha256 == sha)
                    .values(meta_version=0, taken_at=None, preview_meta=meta)
                )
                fallback = datetime.fromisoformat(mtimes[name])
                conn.execute(
                    update(Node)
                    .where(Node.blob_id == sha)
                    .values(**stamp_values(mime, None, fallback, tz))
                )

    v.server.seeded(before_meta)

    def taken(month: str) -> dict[str, str]:
        items = v.api(anna, "GET", f"{API}/photos", params={"month": month}, expect=200).json()
        return {i["name"]: i["taken_at"] for i in items["items"] if i["name"] in bodies}

    def backfilled() -> bool:
        return taken("2024-05") == {"zomer.jpg": EXIF_AT} and taken("2024-01") == {
            "oudjaar.webm": VIDEO_AT
        }

    wait_until(backfilled, seconds=120, what="de backfill van de opnamedatums")
    assert taken("2022-03") == {"zonder-datum.png": PNG_AT}  # geen datum in de inhoud
    assert taken("2025-06") == {}

    # De tijdlijn: naar de oudste maand (de scrubber), de koppen en de cellen met hun datum
    page = v.stand["anna_page"]
    page.goto("/fotos")
    grid = page.get_by_role("grid", name="Tijdlijn", exact=True)
    expect(grid).to_be_visible()
    scrubber = page.get_by_role("slider", name="Naar maand", exact=True)
    scrubber.focus()
    page.keyboard.press("End")
    for month in ("mei 2024", "januari 2024", "maart 2022"):
        expect(page.get_by_role("columnheader", name=month, exact=True)).to_be_visible()
    jpg = photo_cell(page, "zomer.jpg")
    video = photo_cell(page, "Video oudjaar.webm")
    plain = photo_cell(page, "zonder-datum.png")
    expect(jpg).to_have_attribute("aria-label", re.compile(r"1 mei 2024.*12:30"))
    expect(video).to_have_attribute("aria-label", re.compile(r"1 jan 2024.*00:30"))
    expect(plain).to_have_attribute("aria-label", re.compile(r"5 mrt 2022.*10:00"))

    # De viewer: I toont de EXIF uit de backfill (Camera, Belichting) en geen locatie
    jpg.focus()
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name="zomer.jpg", exact=True)
    expect(viewer).to_be_visible()
    page.keyboard.press("i")

    def value(viewer: Any, label: str) -> Any:
        info = viewer.get_by_role("complementary", name="Gegevens")
        section = info.locator("section").filter(has=page.get_by_role("heading", name="Foto"))
        return section.locator("div").filter(has=page.locator("dt", has_text=label)).locator("dd")

    expect(value(viewer, "Camera")).to_have_text("Testmerk Model X")
    expect(value(viewer, "Belichting")).to_have_text("f/2,8 · 1/250 s · ISO 200 · 35 mm")
    expect(value(viewer, "Datum")).to_have_text(re.compile(r"1 mei 2024.*12:30"))
    for text in ("Locatie", "GPS", "52°", "52,375", "52.375"):
        expect(page.locator("body")).not_to_contain_text(text)
    page.keyboard.press("ArrowRight")  # de video: de datum in Amsterdam
    viewer = page.get_by_role("dialog", name="oudjaar.webm", exact=True)
    expect(value(viewer, "Datum")).to_have_text(re.compile(r"1 jan.* 2024.*00:30"))
    page.keyboard.press("i")
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()

    # Een album uit de tijdlijn: Ctrl-klik de drie, A, een nieuw album
    jpg.click(modifiers=[MOD])
    video.click(modifiers=[MOD])
    plain.click(modifiers=[MOD])
    bar = page.get_by_role("toolbar", name="Selectie", exact=True)
    expect(bar.get_by_text("3 geselecteerd", exact=True)).to_be_visible()
    page.keyboard.press("a")
    dialog = page.get_by_role("dialog", name="3 items toevoegen aan album", exact=True)
    expect(dialog.get_by_role("radio", name="Nieuw album", exact=True)).to_be_checked()
    dialog.get_by_label("Naam", exact=True).fill("Reis 2024")
    dialog.get_by_role("button", name="Toevoegen", exact=True).click()
    expect(toast(page, "3 toegevoegd aan Reis 2024.")).to_be_visible()
    page.keyboard.press("Escape")
    albums = v.api(anna, "GET", f"{API}/albums", expect=200).json()["albums"]
    [album] = [a for a in albums if a["name"] == "Reis 2024"]
    album_id = album["id"]
    assert album["count"] == 3

    # Op de albumpagina: S deelt, met Bram (alleen bekijken kan bij een album) en Gijs; Alt+L
    # maakt de publieke link
    page.goto("/albums")
    page.get_by_role("link", name=re.compile("^Reis 2024")).click()
    expect(page.get_by_role("heading", name="Reis 2024", level=1)).to_be_visible()
    page.locator('[role=gridcell][tabindex="0"]').focus()
    page.keyboard.press("s")
    share = page.get_by_role("dialog", name="Reis 2024 delen", exact=True)
    expect(share.get_by_text("Een album deel je alleen om te bekijken.")).to_be_visible()
    for persoon in (bram, gijs):
        page.keyboard.press("Alt+P")
        box = share.get_by_role("combobox", name="Zoek een persoon of groep")
        expect(box).to_be_focused()
        box.press_sequentially(persoon.naam.split()[0], delay=20)
        expect(share.get_by_role("option", name=re.compile(persoon.naam))).to_be_visible()
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        expect(toast(page, f"Gedeeld met {persoon.naam}.")).to_be_visible()
        box.fill("")
    page.keyboard.press("Alt+L")
    share.get_by_role("button", name="Link maken", exact=True).click()
    address = share.get_by_label("Adres van Link", exact=True)
    expect(address).to_have_value(re.compile(r"/s/"))
    public_url = address.input_value()
    share.get_by_role("button", name="Sluiten", exact=True).last.click()
    expect(share).to_be_hidden()
    shares = v.api(anna, "GET", f"{API}/files/{album_id}/shares", expect=200).json()
    assert sorted(s["permission"] for s in shares) == ["read", "read", "read"]

    # De zip van het album: de drie, en niets anders
    with page.expect_download() as zipped:
        page.get_by_role("button", name="Downloaden", exact=True).click()
    assert zipped.value.suggested_filename == "Reis 2024.zip"
    assert zip_names(zipped.value.path().read_bytes()) == sorted(bodies)

    # Bram: onder Gedeeld met mij, zonder Kopiëren of Downloaden (een album is geen map)
    other = v.stand["bram_page"]
    other.goto("/gedeeld")
    shared_row = other.get_by_role("row").filter(has_text="Reis 2024")
    expect(shared_row.get_by_role("link", name=re.compile("Reis 2024"))).to_have_attribute(
        "href", f"/album/{album_id}"
    )
    expect(shared_row).not_to_contain_text("Kopiëren")
    expect(shared_row.get_by_role("button", name="Reis 2024 downloaden")).to_have_count(0)
    r = v.api(bram, "POST", f"{API}/archive/link", json={"ids": [album_id], "name": "Reis 2024"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "album", r.text
    shared_row.get_by_role("link", name=re.compile("Reis 2024")).click()
    expect(other.get_by_role("heading", name="Reis 2024", level=1)).to_be_visible()
    expect(other.get_by_text("van Anna Beheer", exact=False)).to_be_visible()
    expect(other.get_by_role("gridcell")).to_have_count(3)
    for name in ("Delen…", "Hernoemen", "Album verwijderen", "Kopiëren naar…"):
        expect(other.get_by_role("button", name=name, exact=True)).to_have_count(0)

    # Gijs (gast): alleen Gedeeld met mij, met alleen het album; verder niets
    guest = v.page(gijs, "/")
    expect(guest).to_have_url(re.compile("/gedeeld$"))
    nav = guest.get_by_role("navigation", name="Hoofdmenu")
    expect(nav.get_by_role("link")).to_have_text(["Gedeeld met mij"])
    expect(guest.get_by_text("Als gast heb je geen eigen bestanden", exact=False)).to_be_visible()
    rows = guest.get_by_role("row").filter(has_not=guest.get_by_role("columnheader"))
    expect(rows).to_have_count(1)
    expect(rows).to_contain_text("Reis 2024")
    assert [s["node"]["id"] for s in v.api(gijs, "GET", f"{API}/shared-with-me").json()] == [
        album_id
    ]
    for path in (f"{API}/files/{fotos}", f"{API}/files/{v.stand['reis']}"):
        assert v.api(gijs, "GET", path).status_code == 404, path
    r = v.api(gijs, "PUT", f"{API}/files/{gijs.root}/upload", params={"name": "x"}, content=b"x")
    assert r.status_code == 403, r.text
    v.stand["gast_page"] = guest

    # De publieke link, zonder in te loggen: bekijken, en in geen enkel antwoord (ook niet in
    # die van de eigenaar) iets van GPS of een locatie
    anon = v.page()
    answers: list[Any] = []
    anon.on("response", lambda r: answers.append(r))
    anon.goto(public_url)
    expect(anon.get_by_role("heading", name="Reis 2024", level=1)).to_be_visible()
    public_grid = anon.get_by_role("grid", name="Foto's in Reis 2024", exact=True)
    expect(public_grid.get_by_role("gridcell")).to_have_count(3)
    photo_cell(anon, "zomer.jpg").click()
    viewer = anon.get_by_role("dialog", name="zomer.jpg", exact=True)
    expect(viewer.get_by_role("img", name="zomer.jpg", exact=True)).to_be_visible()
    expect(viewer).to_contain_text("1 mei 2024, 12:30 · 1 van 3")
    anon.keyboard.press("ArrowRight")
    expect(anon.get_by_role("dialog", name="oudjaar.webm", exact=True)).to_be_visible()
    anon.keyboard.press("Escape")
    own = [
        v.api(anna, "POST", f"{API}/previews", json={"node_ids": list(ids.values())}),
        v.api(anna, "GET", f"{API}/files/{ids['zomer.jpg']}/preview"),
        v.api(anna, "GET", f"{API}/photos", params={"month": "2024-05"}),
        v.api(anna, "GET", f"{API}/albums/{album_id}/items"),
    ]
    public = [r.text() for r in answers if "json" in (r.headers.get("content-type") or "")]
    assert len(public) >= 2, [r.url for r in answers]  # het album en zijn previews
    for text in [r.text for r in own] + public:
        low = text.lower()
        assert "gps" not in low and "latitude" not in low and "location" not in low, text
    v.stand.update(album=album_id, fotos=fotos, foto_ids=ids)
    stap(v, "foto's")


# --- 5. Elk soort bestand in de viewer ---

PDF_PAGES = 50


def heic() -> bytes:
    """Een HEIC zoals van een telefoon (pillow-heif kan hem hier schrijven): de browser toont hem
    niet zelf, de viewer toont de WebP van 2048 px."""
    import io

    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (200, 120, 40)).save(buf, format="HEIF", quality=60)
    return buf.getvalue()


def playing(page: Any, tag: str) -> None:
    page.wait_for_function(
        f"""() => {{ const m = document.querySelector("dialog {tag}");
          return !!m && !m.error && m.currentTime > 0.2; }}""",
        timeout=15_000,
    )


def test_05_elk_soort_in_de_viewer(verhaal: Verhaal, tmp_path: Path) -> None:
    """Van elk soort één, achter elkaar met → in de viewer: een bewegende GIF (het origineel),
    HEIC (de grotere WebP), een PDF van 50 pagina's, webm en mp3 spelen, mp4/H.264 geeft de
    eerlijke melding, cp1252-tekst, md, csv, json en de kantoorbestanden. De JPEG met EXIF stond
    al in stap 4. De PDF blijft in een eigen tab open, langer dan zijn token (stap 9)."""
    import seed_p4

    v = verhaal
    v.nodig("eerste start")
    anna = v.mensen["anna"]
    files = seed_p4.samples(tmp_path)
    del files["boek.pdf"]
    files["lang.pdf"] = seed_p4.outlined_pdf(PDF_PAGES)
    files["iphone.heic"] = heic()
    folder = v.mkdir(anna, anna.root, "Voorbeelden")
    ids = {name: v.put(anna, folder, name, body) for name, body in files.items()}
    previews = v.wait_for_previews(anna, list(ids.values()))
    assert previews[ids["iphone.heic"]]["state"] == "ready", previews[ids["iphone.heic"]]
    order = sorted(files)
    kinds = {
        "bewegend.gif": "image", "cijfers.xlsx": "office", "clip.webm": "video",
        "data.json": "text", "dias.pptx": "office", "film.mp4": "video", "foto.jpg": "image",
        "iphone.heic": "image", "lang.pdf": "pdf", "liedje.mp3": "audio",
        "notities.md": "text", "notulen.odt": "office", "oud.txt": "text",
        "tabel.csv": "text", "verslag.docx": "office",
    }  # fmt: skip
    assert sorted(kinds) == order

    page = v.stand["anna_page"]
    page.goto(f"/map/{folder}")
    page.locator("#inhoud").get_by_text(order[0], exact=True).click()
    h264 = page.evaluate(
        """() => document.createElement("video").canPlayType('video/mp4; codecs="avc1.4d401e"')"""
    )
    office = {
        "verslag.docx": ("heading", "Jaarverslag"),
        "cijfers.xlsx": ("cell", "Appels"),
        "dias.pptx": ("heading", "Welkom bij de presentatie"),
        "notulen.odt": ("heading", "Notulen"),
    }
    for i, name in enumerate(order):
        viewer = page.get_by_role("dialog", name=name, exact=True)
        expect(viewer.get_by_text(f"{i + 1} / {len(order)}")).to_be_visible()
        stage = viewer.locator(".viewer-stage")
        expect(stage).to_have_attribute("data-kind", kinds[name])
        if name == "bewegend.gif":  # bewegend: het origineel, niet de stilstaande preview
            expect(viewer.locator(".zoom-img")).to_have_attribute("src", re.compile("/download"))
        elif name == "iphone.heic":
            img = viewer.locator(".zoom-img")
            expect(img).to_have_attribute("src", re.compile("^/api/v1/previews/"))
            page.wait_for_function(
                """() => { const i = document.querySelector("dialog .zoom-img");
                  return !!i && i.complete && i.naturalWidth === 640; }"""
            )
        elif name == "lang.pdf":
            expect(viewer.get_by_role("spinbutton")).to_have_value("1")
            expect(viewer.get_by_text(f"van {PDF_PAGES}", exact=False)).to_be_visible()
            expect(viewer.get_by_role("img", name="Pagina i", exact=True)).to_be_visible()
        elif name == "clip.webm":
            playing(page, "video")
        elif name == "liedje.mp3":
            playing(page, "audio")
        elif name == "film.mp4":
            if h264:  # een Chromium met H.264 speelt hem gewoon
                playing(page, "video")
            else:
                expect(
                    viewer.get_by_text("Deze browser kan dit formaat niet afspelen", exact=True)
                ).to_be_visible(timeout=15_000)
                expect(viewer.locator(".viewer-message")).to_contain_text("h264")
        elif name == "oud.txt":
            region = viewer.get_by_role("region", name="Inhoud van het bestand")
            expect(region).to_contain_text(seed_p4.CP1252_TEXT.splitlines()[0])
            expect(viewer.get_by_label("Tekencodering")).to_have_value("windows-1252")
        elif name == "notities.md":
            expect(viewer.get_by_role("heading", name="Notities", exact=True)).to_be_visible()
            expect(viewer.locator("a[href^='javascript']")).to_have_count(0)
        elif name == "tabel.csv":
            expect(viewer.get_by_role("cell", name="Jansen, Piet", exact=True)).to_be_visible()
        elif name == "data.json":
            expect(viewer.locator(".tok-number")).to_have_text("42")
        elif name in office:
            role, text = office[name]
            expect(viewer.get_by_role(role, name=text, exact=True)).to_be_visible()
        if i < len(order) - 1:
            # Shift+→: ook als de inhoud (tekst, tabel) de pijltjes zelf gebruikt
            page.keyboard.press("Shift+ArrowRight")
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)

    # De PDF in een eigen tab, open laten staan: de tokens van /document gaan hier 1 minuut mee
    pdf = page.context.new_page()
    documents: list[str] = []
    pdf.on("request", lambda r: documents.append(r.url) if "/document" in r.url else None)
    pdf.goto(f"/map/{folder}?bestand={ids['lang.pdf']}")
    viewer = pdf.get_by_role("dialog", name="lang.pdf", exact=True)
    expect(viewer.get_by_role("img", name="Pagina i", exact=True)).to_be_visible()
    first = viewer.locator(".page img").first.get_attribute("src")
    v.stand["pdf"] = {"page": pdf, "at": time.monotonic(), "first": first, "documents": documents}
    v.stand.update(voorbeelden=folder, voorbeeld_ids=ids)
    stap(v, "viewer")


# --- 6. Een selectie delen met een groep ---


def test_06_selectie_met_een_groep_delen_en_gedeeld_door_mij(verhaal: Verhaal) -> None:
    """De beheerder maakt de groep Familie (Bram en Gijs); Anna deelt drie bestanden in één
    keer met die groep (S op de selectie), Bram en Gijs zien ze, en Gedeeld door mij toont alles
    wat Anna deelt."""
    v = verhaal
    v.nodig("eerste start", "foto's", "viewer")
    anna, bram, gijs = v.mensen["anna"], v.mensen["bram"], v.mensen["gijs"]
    page = v.stand["anna_page"]

    page.goto("/beheer/groepen")
    expect(page.get_by_role("heading", name="Groepen", exact=True)).to_be_visible()
    blur(page)
    page.keyboard.press("n")
    expect(page.get_by_label("Naam van een nieuwe groep", exact=True)).to_be_focused()
    page.keyboard.type("Familie")
    page.keyboard.press("Enter")
    group_row = page.get_by_role("row").filter(has_text="Familie")
    expect(group_row).to_have_count(1)
    group_row.get_by_role("button", name=re.compile("^Leden")).click()
    members = page.get_by_role("dialog", name="Leden van Familie", exact=True)
    for persoon in (bram, gijs):
        members.get_by_label(re.compile(persoon.naam)).check()
    members.get_by_role("button", name="Opslaan", exact=True).click()
    expect(members).to_be_hidden()
    expect(group_row).to_contain_text("2 leden")

    # De eerste drie van Voorbeelden: Home, Shift+↓ twee keer, S
    names = ["bewegend.gif", "cijfers.xlsx", "clip.webm"]
    page.goto(f"/map/{v.stand['voorbeelden']}")
    expect(row(page, names[0])).to_be_visible()
    enter_list(page)
    page.keyboard.press("Home")
    page.keyboard.press("Shift+ArrowDown")
    page.keyboard.press("Shift+ArrowDown")
    bar = page.get_by_role("toolbar", name="Selectie", exact=True)
    expect(bar.locator(".selection-count")).to_have_text("3 geselecteerd")
    page.keyboard.press("s")
    dialog = page.get_by_role("dialog", name="3 items delen", exact=True)
    expect(dialog.get_by_text("Je deelt 3 items met dezelfde persoon of groep.")).to_be_visible()
    page.keyboard.press("Alt+P")
    box = dialog.get_by_role("combobox", name="Zoek een persoon of groep")
    expect(box).to_be_focused()
    box.press_sequentially("Famil", delay=20)
    option = dialog.get_by_role("option", name=re.compile("Familie"))
    expect(option).to_be_visible()
    page.keyboard.press("ArrowDown")
    expect(option).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Enter")
    expect(toast(page, "Gedeeld: 3 items.")).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    ids = v.stand["voorbeeld_ids"]
    for name in names:
        shares = v.api(anna, "GET", f"{API}/files/{ids[name]}/shares", expect=200).json()
        assert [(s["type"], s["group"]["name"], s["permission"]) for s in shares] == [
            ("group", "Familie", "read")
        ], shares

    # Bram en Gijs zien ze, via de groep
    for persoon, other in ((bram, v.stand["bram_page"]), (gijs, v.stand["gast_page"])):
        other.goto("/gedeeld")
        for name in names:
            shared = other.get_by_role("row").filter(has_text=name)
            expect(shared).to_contain_text("Groep Familie")
        got = {s["node"]["name"] for s in v.api(persoon, "GET", f"{API}/shared-with-me").json()}
        assert got == {"Reis 2024", *names}, (persoon.naam, got)

    # Gedeeld door mij: het album (Bram, Gijs en een link) en de drie (de groep)
    page.goto("/gedeeld/door-mij")
    tabs = page.get_by_role("navigation", name="Gedeeld")
    expect(tabs.get_by_role("link", name="Gedeeld door mij", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    album_rows = page.get_by_role("row").filter(has_text="Reis 2024")
    for who in (bram.naam, gijs.naam, "Link"):
        expect(album_rows.filter(has_text=who)).to_have_count(1)
    for name in names:
        expect(page.get_by_role("row").filter(has_text=name)).to_contain_text("Groep Familie")
    stap(v, "groep")


# --- 7. De prullenbak ---


def focus_in_grid(page: Any) -> None:
    """De focus staat (zo meteen) in een lijst (role=grid), niet op <body>."""
    page.wait_for_function("() => !!document.activeElement?.closest('[role=grid]')", timeout=5000)


def test_07_prullenbak_in_bulk_terugzetten_en_legen(verhaal: Verhaal) -> None:
    """Vier bestanden met eigen inhoud naar de prullenbak (Mod+A, Delete); in de prullenbak
    twee terug met Shift+↓ en Enter, de rest weg met Shift+L: hun blobs staan dan niet meer op
    schijf, die van de teruggezette wel."""
    import os

    v = verhaal
    v.nodig("eerste start")
    anna = v.mensen["anna"]
    folder = v.mkdir(anna, anna.root, "Opruimen")
    bodies = {f"weg-{i}.bin": os.urandom(4096) for i in range(4)}
    for name, body in bodies.items():
        v.put(anna, folder, name, body)
    blobs = {name: v.blob_path(body) for name, body in bodies.items()}
    assert all(path.is_file() for path in blobs.values())

    page = v.stand["anna_page"]
    page.goto(f"/map/{folder}")
    expect(row(page, "weg-0.bin")).to_be_visible()
    enter_list(page)
    page.keyboard.press(f"{MOD}+a")
    count = page.get_by_role("toolbar", name="Selectie", exact=True).locator(".selection-count")
    expect(count).to_have_text("4 geselecteerd")
    page.keyboard.press("Delete")
    expect(toast(page, "4 items staan in de prullenbak")).to_be_visible()
    expect(page.get_by_text("Deze map is leeg.", exact=True)).to_be_visible()

    page.goto("/prullenbak")
    grid = page.get_by_role("grid", name="Inhoud van de prullenbak")
    items = grid.get_by_role("row").filter(has_not=page.get_by_role("columnheader"))
    expect(items.filter(has_text="weg-")).to_have_count(4)
    grid.locator('[role=row][tabindex="0"]').focus()
    page.keyboard.press("Home")
    page.keyboard.press("Shift+ArrowDown")
    expect(page.get_by_text("2 geselecteerd", exact=True)).to_be_visible()
    first_two = [items.nth(i).locator(".trash-label").inner_text() for i in range(2)]
    assert all(n.startswith("weg-") for n in first_two), first_two
    page.keyboard.press("Enter")
    expect(toast(page, "2 items teruggezet.")).to_be_visible()
    expect(items.filter(has_text="weg-")).to_have_count(2)
    focus_in_grid(page)  # niet naar <body>: de pijltjes werken meteen weer
    page.keyboard.press("ArrowDown")
    expect(grid.locator('[role=row][data-index="1"]')).to_be_focused()
    assert sorted(v.children(anna, folder)) == sorted(first_two)

    page.keyboard.press("Shift+L")
    ask = page.get_by_role("dialog", name="De prullenbak legen?", exact=True)
    ask.get_by_role("button", name="Prullenbak legen", exact=True).click()
    expect(page.get_by_text("De prullenbak is leeg.", exact=True)).to_be_visible()
    assert v.api(anna, "GET", f"{API}/trash", expect=200).json() == []
    gone = [blobs[n] for n in bodies if n not in first_two]
    wait_until(lambda: not any(p.exists() for p in gone), what="de blobs weg van schijf")
    assert all(blobs[n].is_file() for n in first_two)
    v.stand["opruimen"] = folder
    stap(v, "prullenbak")


# --- 8. Versies ---


def test_08_versies_uploaden_met_voortgang_bekijken_terugzetten(verhaal: Verhaal) -> None:
    """V opent Versies…; U uploadt een nieuwe versie (traag: de voortgang staat er), Bekijken
    toont de oude in de viewer, Terugzetten zet hem terug."""
    v = verhaal
    v.nodig("prullenbak")
    anna = v.mensen["anna"]
    folder = v.stand["opruimen"]
    first = b"eerste versie\n"
    node = v.put(anna, folder, "verslag.txt", first)

    page = v.stand["anna_page"]
    page.goto(f"/map/{folder}")
    expect(row(page, "verslag.txt")).to_be_visible()
    enter_list(page)
    page.keyboard.press("Home")
    expect_focus(page, "verslag.txt")
    page.keyboard.press("v")
    dialog = page.get_by_role("dialog", name="Versies van verslag.txt", exact=True)
    upload = dialog.get_by_role("button", name="Nieuwe versie uploaden", exact=True)
    expect(upload).to_be_focused()

    second = b"tweede versie, veel langer\n" * (3 * MB // 27)
    cdp_profile(page.context, page, latency_ms=5, down_mbit=200, up_mbit=4)
    with page.expect_file_chooser() as chooser:
        page.keyboard.press("u")
    chooser.value.set_files({"name": "verslag.txt", "mimeType": "text/plain", "buffer": second})
    progress = dialog.get_by_role("progressbar", name="Voortgang van de upload")
    expect(progress).to_be_visible()
    # Het gaat vooruit (niet alleen een balk): de waarde loopt op terwijl de bytes gaan
    page.wait_for_function(
        """() => { const p = document.querySelector("dialog progress");
          return !!p && p.value > 0 && p.value < 100; }"""
    )
    expect(toast(page, "Nieuwe versie opgeslagen; de vorige staat hieronder.")).to_be_visible(
        timeout=30_000
    )
    cdp_profile(page.context, page, latency_ms=2, down_mbit=400, up_mbit=400)
    expect(progress).to_have_count(0)
    assert v.api(anna, "GET", f"{API}/files/{node}", expect=200).json()["size"] == len(second)

    versions = dialog.get_by_role("list", name="Eerdere versies")
    expect(versions.get_by_role("listitem")).to_have_count(1)
    versions.get_by_role("button", name=re.compile(r"^Versie van .* bekijken$")).focus()
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name="verslag.txt", exact=True)
    expect(viewer.locator(".viewer-title")).to_contain_text(re.compile(r"· Versie van "))
    expect(viewer.locator(".viewer-stage")).to_contain_text("eerste versie")
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()
    expect(dialog).to_be_visible()

    versions.get_by_role("listitem").last.get_by_role("button", name="Terugzetten").focus()
    page.keyboard.press("Enter")
    ask = page.get_by_role("dialog", name="Deze versie terugzetten?", exact=True)
    ask.get_by_role("button", name="Terugzetten", exact=True).focus()
    page.keyboard.press("Enter")
    expect(toast(page, "Versie teruggezet.")).to_be_visible()
    assert v.api(anna, "GET", f"{API}/files/{node}", expect=200).json()["size"] == len(first)
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    stap(v, "versies")


# --- 9. De PDF van stap 5, na het verlopen van zijn token ---


def test_09_pdf_blijft_laden_na_het_token(verhaal: Verhaal) -> None:
    """De PDF uit stap 5 staat nog open, langer dan een download-token meegaat (1 minuut hier):
    de viewer heeft op tijd een nieuw /document gehaald, en pagina 50 laadt nog."""
    v = verhaal
    v.nodig("viewer")
    pdf = v.stand["pdf"]
    page = pdf["page"]
    left = 70 - (time.monotonic() - pdf["at"])
    if left > 0:
        page.wait_for_timeout(left * 1000)
    assert len(pdf["documents"]) >= 2, pdf["documents"]  # op tijd vernieuwd
    viewer = page.get_by_role("dialog", name="lang.pdf", exact=True)
    box = viewer.get_by_role("spinbutton")
    box.fill(str(PDF_PAGES))
    box.press("Enter")
    last = f"Pagina {PDF_PAGES - 2}"  # de labels: i, ii, 1, 2, …
    page.wait_for_function(
        """(alt) => [...document.querySelectorAll(".page img")].some(
          (i) => i.alt === alt && i.complete && i.naturalWidth > 0)""",
        arg=last,
        timeout=20_000,
    )
    late = viewer.get_by_role("img", name=last, exact=True).get_attribute("src") or ""
    assert "token=" in late and late.split("token=")[1] != (pdf["first"] or "").split("token=")[1]
    expect(viewer.locator(".page-failed")).to_have_count(0)
    page.close()
    stap(v, "pdf")


# --- 10. Beheer ---


def test_10_beheer_blokkeren_verwijderen_quotum_opslag_taken(verhaal: Verhaal) -> None:
    """Bram blokkeren: zijn sessie, zijn refresh en een downloadlink die hij al had, werken niet
    meer. Daarna verwijderen: de taak loopt en zijn blobs zijn weg. Onderweg de quotumbalk, de
    opslag per node en de taken."""
    import os

    v = verhaal
    v.nodig("eerste start")
    anna, bram = v.mensen["anna"], v.mensen["bram"]
    body = os.urandom(300_000)
    mine = v.put(bram, bram.root, "van-bram.bin", body)
    blob = v.blob_path(body)
    assert blob.is_file()
    link = v.api(bram, "POST", f"{API}/files/{mine}/download-link", expect=200).json()["url"]
    issued = time.monotonic()
    assert v.server.request("GET", link).status_code == 200
    bram_page = v.stand["bram_page"]
    bram_page.goto("/")
    expect(bram_page.get_by_role("button", name=bram.naam, exact=True)).to_be_visible()

    # De quotumbalk: in de lijst "… van 1 GB", in de dialoog een meter
    page = v.stand["anna_page"]
    page.goto("/beheer/gebruikers")
    expect(page.get_by_role("row").filter(has_text=bram.email)).to_be_visible()
    blur(page)
    page.keyboard.press("/")
    expect(page.get_by_label("Zoek op naam of e-mailadres", exact=True)).to_be_focused()
    page.keyboard.type("Bram")
    user_row = page.get_by_role("row").filter(has_text=bram.email)
    expect(user_row).to_contain_text(re.compile(r"van 1(,0)? GB"))
    user_row.get_by_role("button", name=f"{bram.naam} wijzigen", exact=True).click()
    dialog = page.get_by_role("dialog", name=f"{bram.naam} wijzigen", exact=True)
    expect(dialog.get_by_text(f"Opslag van {bram.naam}", exact=True)).to_be_visible()
    assert float(dialog.locator("meter").get_attribute("value") or 0) >= len(body)

    # Blokkeren (na een vraag): de sessie, de refresh en de link van daarnet stoppen
    dialog.get_by_role("button", name="Blokkeren", exact=True).click()
    ask = page.get_by_role("dialog", name=f"{bram.naam} blokkeren?", exact=True)
    ask.get_by_role("button", name="Blokkeren", exact=True).click()
    expect(dialog.get_by_role("button", name="Blokkade opheffen", exact=True)).to_be_visible()
    assert v.server.request("GET", link).status_code in (401, 403, 404), "de link werkt nog"
    assert time.monotonic() - issued < 50  # dus niet verlopen, maar tegengehouden
    r = v.server.request("GET", f"{API}/auth/me", token=bram.account.token)  # type: ignore[union-attr]
    assert r.status_code in (401, 403) and r.json()["detail"]["code"] == "account_disabled", r.text
    r = bram_page.context.request.post(f"{v.base}{API}/auth/refresh")
    assert r.status in (401, 403) and r.json()["detail"]["code"] == "account_disabled", r.text()
    bram_page.reload()
    expect(bram_page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()
    # Inloggen (met wachtwoord en code: pas daarna zegt de server dat hij geblokkeerd is)
    login = {"email": bram.email, "password": bram.password, "code": bram.code()}
    r = v.server.request("POST", f"{API}/auth/login", json=login)
    assert r.status_code in (401, 403) and r.json()["detail"]["code"] == "account_disabled", r.text

    # Verwijderen: pas als het e-mailadres er staat; de taak ruimt alles op
    remove = dialog.get_by_role("button", name="Verwijderen…", exact=True)
    expect(remove).to_be_enabled()
    remove.click()
    ask = page.get_by_role("dialog", name=f"{bram.naam} definitief verwijderen?", exact=True)
    confirm = ask.get_by_role("button", name="Definitief verwijderen", exact=True)
    expect(confirm).to_be_disabled()
    ask.get_by_role("textbox").fill(bram.email)
    confirm.click()
    expect(dialog).to_be_hidden()

    def gone() -> bool:
        users = v.api(anna, "GET", f"{API}/users", expect=200).json()
        return all(u["id"] != bram.id for u in users)

    wait_until(gone, seconds=60, what="het verwijderen van Bram")
    wait_until(lambda: not blob.exists(), seconds=30, what="de blob van Bram weg van schijf")
    jobs = v.api(anna, "GET", f"{API}/admin/jobs", expect=200).json()
    done = {(t["type"], t["status"]): t["count"] for t in jobs["by_type"]}
    assert done.get(("user.delete", "done"), 0) >= 1, jobs["by_type"]

    # Taken en opslag in de app: Alt+4 en Alt+3
    blur(page)
    page.keyboard.press("Alt+4")
    expect(page.get_by_role("heading", name="Achtergrondtaken", exact=True)).to_be_visible()
    expect(page.get_by_role("row").filter(has_text="Gebruiker verwijderen")).to_have_count(1)
    page.keyboard.press("Alt+3")
    expect(page.get_by_role("heading", name="Datadisk per node", exact=True)).to_be_visible()
    expect(page.locator(".storage-bar")).to_have_count(1)
    stap(v, "beheer")


# --- 11. In het Engels ---

# Woorden die alleen in de Nederlandse teksten van de app staan (niet in wat de mensen in het
# verhaal zelf maakten: dat gaat er eerst af)
DUTCH = re.compile(
    r"\b(Bestanden|Prullenbak|Gedeeld|Uploaden|Nieuwe|Zoeken|Sorteren|Weergave|Lijst|Raster|"
    r"Gebruikers|Opslag|Taken|Groepen|Beveiliging|Overzicht|Gewijzigd|Grootte|Naam|Terugzetten|"
    r"Downloaden|Sluiten|Volgende|Vorige|Uitloggen|geselecteerd|Wachtwoord|Taal|Tijdlijn|"
    r"Meldingen|Verwijderen|Rechten|bekijken|Deze|deze|het|een|van|met|voor|naar|niet|geen|"
    r"leeg|Inhoud|Acties|Mijn|Leden|leden|Soort|Klaar|Mislukt|Wachtend|Bezig|Pad|Hoofdmenu|"
    r"Selectie|Kopiëren|Verplaatsen|Hernoemen|Delen|Versies|Gegevens|Datum|Dagen|Apparaten|"
    r"Herstelcodes|Tweestapsverificatie|foto's|video's|Sneltoetsen|Thema|Licht|Donker|Systeem)\b"
)
USER_TEXT = [
    re.compile(r"[\w.'-]+\.(txt|bin|jpg|webm|png|pdf|gif|mp3|mp4|md|csv|json|docx|xlsx|pptx|odt|heic|webp|dat)\b"),
    re.compile(r"Map \d{5}"),
    re.compile(r"[\w.+-]+@[\w.-]+"),
]  # fmt: skip

SCREENS_JS = """() => {
  const parts = [document.title, document.body.innerText];
  for (const el of document.querySelectorAll("[aria-label], [title], [placeholder]")) {
    for (const a of ["aria-label", "title", "placeholder"]) {
      const v = el.getAttribute(a);
      if (v) parts.push(v);
    }
  }
  return parts.join("\\n");
}"""


def dutch_left(page: Any, own: list[str]) -> list[str]:
    """Wat er na het weghalen van eigen namen nog Nederlands is op de pagina (ook in
    aria-label, title en placeholder)."""
    text = page.evaluate(SCREENS_JS)
    for pattern in USER_TEXT:
        text = pattern.sub(" ", text)
    for name in sorted(own, key=len, reverse=True):
        text = text.replace(name, " ")
    found = []
    for line in text.splitlines():
        if DUTCH.search(line):
            found.append(line.strip())
    return found


def test_11_alles_in_het_engels_ook_de_fouten(verhaal: Verhaal) -> None:
    """Anna zet haar taal op Engels: elk hoofdscherm is daarna Engels (de tekst, de koppen en
    wat een schermlezer hoort), en een fout van de API ook. Daarna weer terug naar Nederlands."""
    v = verhaal
    v.nodig("eerste start", "map uploaden", "foto's", "viewer")
    page = v.stand["anna_page"]
    own = [p.naam for p in v.mensen.values()] + [
        "Foto's", "Reis 2024", "Reis", "Voorbeelden", "Opruimen", "Familie", "Tienduizend",
        "Dag 1", "Strand", "Nederlands",
    ]  # fmt: skip
    # De controle zelf: in het Nederlands vindt hij wel iets
    page.goto("/account")
    expect(page.get_by_role("heading", name="Opslag", exact=True)).to_be_visible()
    assert len(dutch_left(page, own)) > 5
    page.get_by_label("Taal", exact=True).select_option("en")
    page.get_by_role("button", name="Taal wijzigen", exact=True).click()
    expect(page.get_by_role("heading", name="Storage", exact=True)).to_be_visible()
    expect(page.locator("html")).to_have_attribute("lang", "en")
    assert v.api(v.mensen["anna"], "GET", f"{API}/auth/me").json()["locale"] == "en"

    ids = v.stand["voorbeeld_ids"]
    screens = [
        "/",
        f"/map/{v.stand['voorbeelden']}",
        f"/map/{v.stand['voorbeelden']}?bestand={ids['iphone.heic']}",
        "/fotos",
        "/albums",
        f"/album/{v.stand['album']}",
        "/gedeeld",
        "/gedeeld/door-mij",
        "/prullenbak",
        "/zoeken?q=verslag",
        "/account",
        "/beheer",
        "/beheer/gebruikers",
        "/beheer/opslag",
        "/beheer/taken",
        "/beheer/groepen",
        "/beheer/beveiliging",
    ]
    problems = {}
    for path in screens:
        page.goto(path)
        expect(page.locator("main h1, dialog h2").first).to_be_visible()
        expect(page.get_by_text(re.compile(r"^Loading"))).to_have_count(0)
        page.wait_for_timeout(300)  # de laatste lijst of het laatste antwoord
        left = dutch_left(page, own)
        if left:
            problems[path] = left
    # Ook de sneltoetsen (?) en het accountmenu
    page.goto("/")
    expect(page.get_by_role("navigation", name="Main menu")).to_be_visible()
    blur(page)
    page.keyboard.press("?")
    expect(page.get_by_role("dialog", name=re.compile("Keyboard shortcuts"))).to_be_visible()
    if left := dutch_left(page, own):
        problems["?"] = left
    page.keyboard.press("Escape")
    page.get_by_role("button", name=v.mensen["anna"].naam, exact=True).click()
    expect(page.get_by_role("menu")).to_be_visible()
    if left := dutch_left(page, own):
        problems["accountmenu"] = left
    page.keyboard.press("Escape")
    assert not problems, problems

    # Een fout van de API, in het Engels: een map met een naam die er al is
    page.get_by_role("button", name="New folder", exact=True).click()
    dialog = page.get_by_role("dialog", name="New folder", exact=True)
    dialog.get_by_label("Folder name", exact=True).fill("Reis")
    dialog.get_by_role("button", name="Create", exact=True).click()
    expect(page.get_by_text("Something with this name already exists", exact=True)).to_be_visible()
    page.keyboard.press("Escape")

    # Terug naar Nederlands (de rest van het verhaal)
    page.goto("/account")
    page.get_by_label("Language", exact=True).select_option("nl")
    page.get_by_role("button", name="Change language", exact=True).click()
    expect(page.get_by_role("heading", name="Opslag", exact=True)).to_be_visible()
    expect(page.locator("html")).to_have_attribute("lang", "nl")
    stap(v, "engels")


# --- 12. De PWA ---

CONTROLLED_JS = "() => !!navigator.serviceWorker && !!navigator.serviceWorker.controller"
UPDATE_JS = "() => navigator.serviceWorker.getRegistration().then((r) => r.update())"


def test_12_pwa_offline_en_een_nieuwe_versie(verhaal: Verhaal) -> None:
    """De service worker bedient de app op localhost; offline herladen geeft de schil met de
    balk, en weer online komt de app zelf terug; een nieuwe build geeft "Nieuwe versie
    beschikbaar" en Shift+N laadt hem."""
    from test_pwa import rebuild

    v = verhaal
    v.nodig("eerste start")
    page = v.stand["anna_page"]
    page.goto("/")
    expect(page.get_by_role("navigation", name="Hoofdmenu")).to_be_visible()
    assert page.evaluate("() => location.hostname") == "localhost"
    page.wait_for_function(CONTROLLED_JS, timeout=30_000)

    # Offline: de server even weg en de browser offline, zoals in test_pwa.py. Alleen
    # set_offline is niet genoeg: in nieuwere Chromium (CI) gaan verzoeken die de worker
    # doorlaat dan toch naar het netwerk. Daarna start de server weer op dezelfde poort en
    # met dezelfde data, voor de rest van het verhaal.
    v.server.stop()
    page.context.set_offline(True)
    try:
        page.reload()
        expect(page.get_by_role("heading", name="Geen verbinding", exact=True)).to_be_visible()
        expect(page.get_by_role("status").filter(has_text="Je bent offline")).to_be_visible()
        assert page.evaluate("getComputedStyle(document.body).margin") == "0px"  # met opmaak
    finally:
        v.server.start()
        page.context.set_offline(False)
    # Weer online: het scherm probeert het zelf opnieuw ("online"). Pas daarna herladen: wie
    # herlaadt terwijl die refresh loopt, breekt hem af na de rotatie en houdt het oude cookie
    # (dan hergebruik en uitgelogd, bewust: zie REFRESH_RACE en docs/fase-3/integratie.md)
    expect(page.get_by_role("navigation", name="Hoofdmenu")).to_be_visible()
    page.reload()
    expect(page.get_by_role("navigation", name="Hoofdmenu")).to_be_visible()
    page.wait_for_function(CONTROLLED_JS, timeout=30_000)

    # Een nieuwe build uitrollen terwijl de app open is (de eigen kopie van web/dist)
    rebuild(v.dist, "verhaal")
    page.evaluate(UPDATE_JS)
    expect(toast(page, "Nieuwe versie beschikbaar")).to_be_visible(timeout=30_000)
    blur(page)
    with page.expect_event("load"):
        page.keyboard.press("Shift+N")
    expect(page.locator('meta[name="e2e-build"]')).to_have_attribute("content", "verhaal")
    expect(page.get_by_role("navigation", name="Hoofdmenu")).to_be_visible()
    page.wait_for_function(CONTROLLED_JS, timeout=30_000)
    stap(v, "pwa")


# --- 13. Alleen het toetsenbord ---


def shown_names(page: Any) -> list[str]:
    """De namen in de lijst, in de volgorde op het scherm (de sortering van de gebruiker)."""
    return page.evaluate(
        """(sel) => [...document.querySelectorAll(sel)]
          .sort((a, b) => a.dataset.index - b.dataset.index)
          .map((r) => r.querySelector(".name").textContent)""",
        ROWS,
    )


def arrow_to(page: Any, name: str) -> None:
    """Vanaf de eerste rij met ↓ naar de rij met deze naam."""
    for _ in range(shown_names(page).index(name)):
        page.keyboard.press("ArrowDown")
    expect_focus(page, name)


def test_13_alleen_het_toetsenbord(verhaal: Verhaal, tmp_path: Path) -> None:
    """De checklists van toetsenbord-p1 … p6, voor zover het verhaal ze nog niet met het
    toetsenbord deed: inloggen met 2FA (P1), een nieuwe map, F2, knippen en plakken en de hulp
    (P2), U en de vraag over een dubbel bestand (P3), de viewer openen, downloaden en sluiten
    (P4), selecteren in de tijdlijn (P5). De rest staat in de stappen ervoor (zie
    docs/fase-3/integratie.md)."""
    v = verhaal
    v.nodig("eerste start", "foto's", "prullenbak", "versies")
    gijs = v.mensen["gijs"]

    # P1: Tab naar het e-mailadres, Alt+Shift+P, Enter naar de code, Escape terug, Enter, code
    login = v.page(path="/")
    expect(login.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()
    email = login.get_by_label("E-mailadres", exact=True)
    password = login.get_by_label("Wachtwoord", exact=True)
    for _ in range(5):
        login.keyboard.press("Tab")
        if login.evaluate("() => document.activeElement?.getAttribute('type')") == "email":
            break
    expect(email).to_be_focused()
    login.keyboard.type(gijs.email)
    login.keyboard.press("Tab")
    expect(password).to_be_focused()
    login.keyboard.type(gijs.password)
    login.keyboard.press("Alt+Shift+P")
    expect(password).to_have_attribute("type", "text")
    login.keyboard.press("Alt+Shift+P")
    expect(password).to_have_attribute("type", "password")
    login.keyboard.press("Enter")
    code = login.get_by_label("Code uit je authenticator-app", exact=True)
    expect(code).to_be_focused()
    login.keyboard.press("Escape")
    expect(password).to_be_focused()
    login.keyboard.press("Enter")
    expect(code).to_be_focused()
    login.keyboard.type(gijs.code() or "")
    login.keyboard.press("Enter")
    expect(login.get_by_role("heading", name="Gedeeld met mij", exact=True)).to_be_visible()
    login.context.close()

    # P2: N (nieuwe map), F2 (alleen de naam is geselecteerd), Mod+X, Enter (de map in), Mod+V
    anna = v.mensen["anna"]
    folder = v.stand["opruimen"]
    page = v.stand["anna_page"]
    page.goto(f"/map/{folder}")
    expect(row(page, "verslag.txt")).to_be_visible()
    blur(page)
    page.keyboard.press("n")
    dialog = page.get_by_role("dialog", name="Nieuwe map", exact=True)
    expect(dialog.get_by_label("Naam van de map", exact=True)).to_be_focused()
    page.keyboard.type("Archief")
    page.keyboard.press("Enter")
    expect(dialog).to_be_hidden()
    expect_focus(page, "Archief")  # de nieuwe map in beeld en met de focus
    names = shown_names(page)
    assert names[0] == "Archief" and "verslag.txt" in names, names  # mappen eerst
    page.keyboard.press("End")
    expect_focus(page, names[-1])
    page.keyboard.press("Home")
    expect_focus(page, "Archief")
    arrow_to(page, "verslag.txt")
    page.keyboard.press("F2")
    rename = page.get_by_role("dialog", name="Naam wijzigen", exact=True)
    field = rename.get_by_label("Nieuwe naam", exact=True)
    expect(field).to_be_focused()
    assert field.evaluate("(el) => el.value.slice(el.selectionStart, el.selectionEnd)") == "verslag"
    page.keyboard.type("notitie")
    page.keyboard.press("Enter")
    expect(row(page, "notitie.txt")).to_be_visible()
    expect_focus(page, "notitie.txt")
    page.keyboard.press(f"{MOD}+x")
    cut = page.get_by_text("1 item geknipt; plak het in een andere map", exact=True)
    expect(cut).to_be_attached()
    page.keyboard.press("Home")
    expect_focus(page, "Archief")
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Archief", level=1)).to_be_visible()
    expect(page.get_by_text("Deze map is leeg.", exact=True)).to_be_visible()
    blur(page)
    page.keyboard.press(f"{MOD}+v")
    expect(row(page, "notitie.txt")).to_be_visible()
    archief = v.children(anna, folder)["Archief"]["id"]
    assert list(v.children(anna, archief)) == ["notitie.txt"]
    page.keyboard.press("?")
    help_dialog = page.get_by_role("dialog", name=re.compile("Sneltoetsen"))
    expect(help_dialog.get_by_role("definition").filter(has_text="Nieuwe map")).to_be_visible()
    page.keyboard.press("Escape")
    expect(help_dialog).to_be_hidden()

    # P3: U, en nog een notitie.txt: de vraag (focus op Beide houden), B houdt beide
    blur(page)
    with page.expect_file_chooser() as chooser:
        page.keyboard.press("u")
    assert chooser.value.element.get_attribute("webkitdirectory") is None
    chooser.value.set_files({"name": "notitie.txt", "mimeType": "text/plain", "buffer": b"nog een"})
    ask = page.get_by_role("dialog", name="1 bestand bestaat al", exact=True)
    expect(ask.get_by_role("button", name=re.compile("^Beide houden"))).to_be_focused()
    page.keyboard.press("b")
    expect(row(page, "notitie (1).txt")).to_be_visible()
    assert sorted(v.children(anna, archief)) == ["notitie (1).txt", "notitie.txt"]

    # P4: Enter opent de viewer, D downloadt, Escape sluit en de focus staat weer op de rij
    enter_list(page)
    page.keyboard.press("Home")
    arrow_to(page, "notitie.txt")
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name="notitie.txt", exact=True)
    expect(viewer.locator(".viewer-stage")).to_contain_text("eerste versie")
    with page.expect_download() as download:
        page.keyboard.press("d")
    assert download.value.suggested_filename == "notitie.txt"
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()
    expect_focus(page, "notitie.txt")

    # P5: de tijdlijn, Spatie en Shift+→, Escape heft de selectie op
    page.goto("/fotos")
    cell = page.locator('[role=gridcell][tabindex="0"]')
    expect(cell).to_be_visible()
    cell.focus()
    page.keyboard.press("Space")
    page.keyboard.press("Shift+ArrowRight")
    bar = page.get_by_role("toolbar", name="Selectie", exact=True)
    expect(bar.get_by_text("2 geselecteerd", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(bar).to_be_hidden()
    stap(v, "toetsenbord")
