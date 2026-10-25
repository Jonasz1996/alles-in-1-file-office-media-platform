"""Uploaden (P3): mapupload met de knop en met slepen, pauzeren en hervatten, onderbroken
uploads na herladen, één vraag over dubbele bestanden (zonder tus-verzoek vóór de keuze), en
de uploadlade op een andere pagina. Alles met het toetsenbord waar dat kan (U, Shift+U,
Shift+P, en V/B/O in de vraag); zie docs/fase-3/toetsenbord-p3.md."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect

from conftest import cdp_profile

pytestmark = pytest.mark.p3

MB = 1024 * 1024


def wait_until(check: Callable[[], Any], timeout: float = 20, what: str = "voorwaarde") -> Any:
    """Wacht (synchroon) tot check() iets waars geeft."""
    end = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > end:
            raise AssertionError(f"wachten op {what} duurde te lang")
        time.sleep(0.1)


def children(server, user, folder_id: str | None = None) -> dict[str, dict]:
    path = f"/api/v1/files/{folder_id}/children" if folder_id else "/api/v1/files/root"
    listing = server.call(user, "GET", path, expect=200).json()
    return {c["name"]: c for c in listing["children"]}


def put(server, user, parent: str, name: str, data: bytes) -> dict:
    return server.call(
        user,
        "PUT",
        f"/api/v1/files/{parent}/upload",
        params={"name": name},
        content=data,
        expect=201,
    ).json()


def open_files(context_as, user):
    page = context_as(user).new_page()
    page.goto("/")
    expect(page.get_by_role("button", name="Uploaden", exact=True)).to_be_visible()
    return page


def blur(page) -> None:
    """De focus uit een knop of veld, zodat een sneltoets van één letter werkt."""
    page.evaluate("() => document.activeElement && document.activeElement.blur()")


def tray(page):
    return page.get_by_role("region", name="Uploads", exact=True)


def throttle(page, mbit: float) -> dict:
    """Uploaden langzaam (CDP), zodat er tijd is om te pauzeren of te herladen."""
    return cdp_profile(page.context, page, latency_ms=5, down_mbit=200, up_mbit=mbit)


def pending(server, user) -> list[dict]:
    return server.call(user, "GET", "/api/v1/tus", expect=200).json()


def test_mapupload_met_shift_u_voegt_samen_en_toont_een_botsing(
    context_as, user_factory, server, tmp_path: Path
):
    user = user_factory(name="Map Uploader")
    # Er staat al een map "vakantie" (wordt hergebruikt) en daarin een *bestand* "Strand"
    vakantie = server.call(
        user, "POST", f"/api/v1/files/{user.root_id}/folders", json={"name": "vakantie"}, expect=201
    ).json()["id"]
    put(server, user, vakantie, "Strand", b"ik ben een bestand")

    root = tmp_path / "Vakantie"
    (root / "2024" / "Leeg").mkdir(parents=True)
    (root / "Strand").mkdir()
    (root / "a.txt").write_text("a")
    (root / "2024" / "b.txt").write_text("bb")
    (root / "Strand" / "c.txt").write_text("ccc")

    page = open_files(context_as, user)
    blur(page)
    with page.expect_file_chooser() as chooser:
        page.keyboard.press("Shift+U")
    assert chooser.value.element.get_attribute("webkitdirectory") is not None
    chooser.value.set_files(root)

    lade = tray(page)
    # De map onder het bestand "Strand" lukt niet: zichtbaar mislukt, met de reden en zonder
    # Opnieuw
    expect(
        lade.get_by_text("Er staat al een bestand met de naam van map Vakantie/Strand")
    ).to_be_visible()
    expect(lade.get_by_role("button", name="Opnieuw: Vakantie/Strand/c.txt")).to_have_count(0)

    def arrived() -> bool:
        sub = children(server, user, vakantie).get("2024")
        return bool(sub) and "b.txt" in children(server, user, sub["id"])

    wait_until(arrived, what="b.txt")
    top = children(server, user)
    assert list(top) == ["vakantie"]  # samengevoegd, geen tweede map
    inside = children(server, user, vakantie)
    assert set(inside) == {"2024", "Strand", "a.txt"}
    assert inside["Strand"]["kind"] == "file"
    # Een lege map komt niet mee: een bestandskiezer met webkitdirectory geeft alleen bestanden
    # (slepen neemt lege mappen wel mee, zie test_een_map_slepen)
    assert set(children(server, user, inside["2024"]["id"])) == {"b.txt"}
    # Weggooien haalt de mislukte uit de lade; dan is de lade leeg en weg
    lade.get_by_role("button", name="Weggooien: Vakantie/Strand/c.txt").click()
    expect(lade).to_have_count(0)


def test_uploaden_met_u(context_as, user_factory, server):
    user = user_factory(name="Toets U")
    page = open_files(context_as, user)
    blur(page)
    with page.expect_file_chooser() as chooser:
        page.keyboard.press("u")
    assert chooser.value.element.get_attribute("webkitdirectory") is None
    chooser.value.set_files([{"name": "toets.txt", "mimeType": "text/plain", "buffer": b"u"}])
    wait_until(lambda: "toets.txt" in children(server, user), what="toets.txt")


def test_pauzeren_en_hervatten(context_as, user_factory, server, tmp_path: Path):
    user = user_factory(name="Pauze")
    big = tmp_path / "groot.bin"
    big.write_bytes(bytes(range(256)) * (6 * MB // 256))
    page = open_files(context_as, user)
    throttle(page, mbit=8)  # 6 MB duurt zo ~6 s
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Uploaden", exact=True).click()
    chooser.value.set_files(big)

    lade = tray(page)
    pause = lade.get_by_role("button", name="Pauzeren: groot.bin")
    expect(pause).to_be_visible()
    wait_until(
        lambda: pending(server, user) and pending(server, user)[0]["offset"] > 0, what="bytes"
    )
    pause.click()
    expect(lade.get_by_text("Gepauzeerd", exact=True).first).to_be_visible()
    # Staat stil op de server, en blijft daar staan
    first = pending(server, user)[0]["offset"]
    page.wait_for_timeout(1000)
    still = pending(server, user)
    assert len(still) == 1 and still[0]["offset"] == first < big.stat().st_size

    # Hervatten met het toetsenbord: Shift+P (alles pauzeren of hervatten)
    blur(page)
    page.keyboard.press("Shift+P")
    expect(lade.get_by_role("button", name="Pauzeren: groot.bin")).to_be_visible()
    page.keyboard.press("Shift+P")  # en weer pauzeren
    expect(lade.get_by_role("button", name="Hervatten: groot.bin")).to_be_visible()
    # Alles hervatten en Alles pauzeren met Enter: de focus gaat mee naar de andere knop (niet
    # naar <body>), zodat nog eens Enter het weer omdraait
    resume_all = lade.get_by_role("button", name="Alles hervatten", exact=True)
    resume_all.focus()
    page.keyboard.press("Enter")
    expect(lade.get_by_role("button", name="Alles pauzeren", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(resume_all).to_be_focused()
    expect(lade.get_by_role("button", name="Hervatten: groot.bin")).to_be_visible()
    throttle(page, mbit=400)
    lade.get_by_role("button", name="Hervatten: groot.bin").click()
    wait_until(lambda: "groot.bin" in children(server, user), what="groot.bin")
    node = children(server, user)["groot.bin"]
    assert node["size"] == big.stat().st_size
    assert pending(server, user) == []  # dezelfde upload, geen tweede
    expect(lade).to_have_count(0)


def test_herladen_dan_onderbroken_uploads_en_opnieuw_kiezen(
    context_as, user_factory, server, tmp_path: Path
):
    user = user_factory(name="Herlader")
    big = tmp_path / "film.bin"
    big.write_bytes(bytes(range(256)) * (6 * MB // 256))
    page = open_files(context_as, user)
    page.on("dialog", lambda d: d.accept())  # "Pagina verlaten?" (beforeunload)
    throttle(page, mbit=8)
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Uploaden", exact=True).click()
    chooser.value.set_files(big)
    wait_until(
        lambda: pending(server, user) and pending(server, user)[0]["offset"] > 0, what="bytes"
    )

    page.reload()
    lade = tray(page)
    onderbroken = lade.get_by_role("region", name="Onderbroken uploads")
    expect(onderbroken.get_by_text("film.bin", exact=True)).to_be_visible()
    expect(onderbroken.get_by_text(re.compile(r"van 6,0 MB"))).to_be_visible()

    # Een ander bestand kiezen: dat zegt hij
    with page.expect_file_chooser() as chooser:
        onderbroken.get_by_role("button", name="Kies film.bin opnieuw").click()
    chooser.value.set_files([{"name": "film.bin", "mimeType": "x/y", "buffer": b"te kort"}])
    expect(onderbroken.get_by_role("alert")).to_contain_text("Dat is een ander bestand")

    throttle(page, mbit=400)
    with page.expect_file_chooser() as chooser:
        onderbroken.get_by_role("button", name="Kies film.bin opnieuw").click()
    chooser.value.set_files(big)
    wait_until(lambda: "film.bin" in children(server, user), what="film.bin")
    assert children(server, user)["film.bin"]["size"] == big.stat().st_size
    assert pending(server, user) == []
    expect(lade).to_have_count(0)


def test_dubbele_bestanden_een_vraag_en_geen_tus_ervoor(context_as, user_factory, server):
    user = user_factory(name="Dubbel")
    put(server, user, user.root_id, "a.txt", b"oud a")
    put(server, user, user.root_id, "B.txt", b"oud b")
    page = open_files(context_as, user)
    posts: list[str] = []
    page.on(
        "request",
        lambda r: posts.append(r.url) if r.method == "POST" and "/api/v1/tus" in r.url else None,
    )
    files = [
        {"name": n, "mimeType": "text/plain", "buffer": f"nieuw {n}".encode()}
        for n in ("a.txt", "b.txt", "c.txt")
    ]
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Uploaden", exact=True).click()
    chooser.value.set_files(files)

    dialog = page.get_by_role("dialog", name="2 bestanden bestaan al")
    expect(dialog).to_be_visible()
    page.wait_for_timeout(300)
    assert posts == [], "tus-verzoeken vóór de keuze"
    dialog.get_by_label("Voor allemaal (2)").check()
    dialog.get_by_role("button", name="Vervangen", exact=True).click()
    expect(dialog).to_be_hidden()
    wait_until(lambda: "c.txt" in children(server, user), what="c.txt")
    wait_until(lambda: len(posts) == 3, what="drie uploads")
    # Drie tegelijk: c.txt kan eerder klaar zijn dan het vervangen van a.txt en B.txt
    expect(tray(page)).to_have_count(0)
    names = children(server, user)
    assert set(names) == {"a.txt", "B.txt", "c.txt"}  # vervangen, niets dubbel
    for name in ("a.txt", "B.txt"):
        versions = server.call(
            user, "GET", f"/api/v1/files/{names[name]['id']}/versions", expect=200
        )
        assert len(versions.json()) >= 1, name  # de vorige is een versie

    # Nog eens, nu één voor één met het toetsenbord: B (beide houden), dan O (overslaan)
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Uploaden", exact=True).click()
    chooser.value.set_files(files[:2])
    dialog = page.get_by_role("dialog", name="2 bestanden bestaan al")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text("a.txt", exact=True)).to_be_visible()
    page.keyboard.press("b")
    expect(dialog.get_by_text("b.txt", exact=True)).to_be_visible()
    page.keyboard.press("o")
    expect(dialog).to_be_hidden()
    wait_until(lambda: "a (1).txt" in children(server, user), what="a (1).txt")
    assert set(children(server, user)) == {"a.txt", "a (1).txt", "B.txt", "c.txt"}

    # Esc: niets van de batch
    before = len(posts)
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Uploaden", exact=True).click()
    chooser.value.set_files(
        files[2:] + [{"name": "d.txt", "mimeType": "text/plain", "buffer": b"d"}]
    )
    dialog = page.get_by_role("dialog", name="1 bestand bestaat al")
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(page.get_by_text("Upload geannuleerd")).to_be_visible()
    page.wait_for_timeout(300)
    assert len(posts) == before
    assert "d.txt" not in children(server, user)


def test_de_lade_blijft_op_gedeeld(context_as, user_factory, server, tmp_path: Path):
    user = user_factory(name="Onderweg")
    big = tmp_path / "onderweg.bin"
    big.write_bytes(b"x" * (3 * MB))
    page = open_files(context_as, user)
    throttle(page, mbit=4)
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Uploaden", exact=True).click()
    chooser.value.set_files(big)
    lade = tray(page)
    expect(lade.get_by_role("button", name="Pauzeren: onderweg.bin")).to_be_visible()
    page.get_by_role("navigation", name="Hoofdmenu").get_by_role(
        "link", name="Gedeeld met mij"
    ).click()
    expect(page).to_have_url(re.compile(r"/gedeeld$"))
    expect(lade.get_by_role("button", name="Pauzeren: onderweg.bin")).to_be_visible()
    expect(lade.locator("[aria-live=polite]")).to_contain_text(
        re.compile(r"0 van 1 · .* van 3,0 MB")
    )
    throttle(page, mbit=400)
    wait_until(lambda: "onderweg.bin" in children(server, user), what="onderweg.bin")
    expect(lade).to_have_count(0)


DROP_JS = r"""
async (folderName) => {
  // Een synthetische drop van een map: DataTransfer met echte File-objecten, en
  // webkitGetAsEntry (dat een synthetische DataTransfer niet voor mappen geeft) als nep-entry
  // met readEntries in stukken, zoals Chromium
  const make = (name, text) => new File([text], name, { type: "text/plain" });
  const files = {
    "a.txt": make("a.txt", "a"),
    "b.txt": make("b.txt", "bb"),
    "los.txt": make("los.txt", "los"),
  };
  const fileEntry = (path, file) => ({
    isFile: true, isDirectory: false, name: file.name, fullPath: path,
    file: (ok) => setTimeout(() => ok(file), 0),
  });
  const dirEntry = (path, kids) => ({
    isFile: false, isDirectory: true, name: path.split("/").pop(), fullPath: path,
    createReader() {
      let at = 0;
      return {
        readEntries(ok) {
          const part = kids.slice(at, at + 1);
          at += part.length;
          setTimeout(() => ok(part), 0);
        },
      };
    },
  });
  const tree = dirEntry("/" + folderName, [
    fileEntry(`/${folderName}/a.txt`, files["a.txt"]),
    dirEntry(`/${folderName}/Sub`, [fileEntry(`/${folderName}/Sub/b.txt`, files["b.txt"])]),
    dirEntry(`/${folderName}/Leeg`, []),
  ]);
  const dt = new DataTransfer();
  dt.items.add(new File([], folderName));  // zoals een browser een map in files zet
  dt.items.add(files["los.txt"]);
  const entries = { [folderName]: tree, "los.txt": fileEntry("/los.txt", files["los.txt"]) };
  const original = DataTransferItem.prototype.webkitGetAsEntry;
  DataTransferItem.prototype.webkitGetAsEntry = function () {
    const name = this.getAsFile()?.name;
    return (name && entries[name]) || original.call(this);
  };
  const target = document.querySelector("main");
  for (const type of ["dragenter", "dragover", "drop"]) {
    const init = { bubbles: true, cancelable: true, dataTransfer: dt };
    target.dispatchEvent(new DragEvent(type, init));
  }
  DataTransferItem.prototype.webkitGetAsEntry = original;
  return dt.types.includes("Files");
}
"""


def test_een_map_slepen(context_as, user_factory, server):
    user = user_factory(name="Sleper")
    page = open_files(context_as, user)
    assert page.evaluate(DROP_JS, "Gesleept") is True
    wait_until(
        lambda: "los.txt" in children(server, user) and "Gesleept" in children(server, user),
        what="drop",
    )
    top = children(server, user)
    assert set(top) == {"Gesleept", "los.txt"}
    inside = children(server, user, top["Gesleept"]["id"])
    assert set(inside) == {"a.txt", "Sub", "Leeg"}
    sub = children(server, user, inside["Sub"]["id"])
    assert set(sub) == {"b.txt"}
    assert sub["b.txt"]["size"] == 2
