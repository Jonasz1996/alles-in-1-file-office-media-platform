"""Delen in de browser (P6): een vervaldatum voor een persoon via de combobox (Alt+P, ↓,
Enter), Gedeeld door mij, en een gedeeld album in Gedeeld met mij zonder Kopiëren. Met de
bestandsbrowser (P2) en de viewer (P4): een selectie van drie in één keer met een groep delen,
de grens van 100 items met zijn reden, en Gedeeld met mij opent een bestand in de viewer."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.p6

# De rijen van de bestandsbrowser (P2), zonder de kopregel
ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"


def toast(page, text: str):
    return page.get_by_role("region", name="Meldingen").get_by_text(text, exact=True)


def browser_row(page, name: str):
    return page.locator(ROWS).filter(has=page.get_by_text(name, exact=True))


def pick(page, name: str, modifiers: list[str] | None = None) -> None:
    """Een rij selecteren met een klik naast de naam (de naam zelf opent)."""
    browser_row(page, name).locator(".cell-date").click(modifiers=modifiers or [])


def mkdir(server, account, parent: str, name: str) -> str:
    return server.call(
        account, "POST", f"/api/v1/files/{parent}/folders", json={"name": name}, expect=201
    ).json()["id"]


def put_file(server, account, parent: str, name: str, body: bytes) -> str:
    return server.call(
        account,
        "PUT",
        f"/api/v1/files/{parent}/upload",
        params={"name": name},
        content=body,
        expect=201,
    ).json()["id"]


def test_vervaldatum_voor_een_persoon_en_gedeeld_door_mij(context_as, server, user_factory):
    owner = user_factory(name="Deler Een")
    bob = user_factory(name="Bob Ontvanger")
    folder = server.call(
        owner,
        "POST",
        f"/api/v1/files/{owner.root_id}/folders",
        json={"name": "Project"},
        expect=201,
    ).json()
    server.call(
        owner,
        "POST",
        f"/api/v1/files/{folder['id']}/links",
        json={"permission": "read"},
        expect=201,
    )

    page = context_as(owner).new_page()
    page.goto("/gedeeld/door-mij")
    tabs = page.get_by_role("navigation", name="Gedeeld")
    expect(tabs.get_by_role("link", name="Gedeeld door mij", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    row = page.get_by_role("row").filter(has_text="Project")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Link")
    row.get_by_role("button", name="Delen van Project beheren", exact=True).click()
    dialog = page.get_by_role("dialog", name="Project delen", exact=True)
    expect(dialog).to_be_visible()

    # Eerst de vervaldatum, dan met Alt+P naar de combobox: ↓ en Enter kiezen Bob
    until = date.today() + timedelta(days=7)
    dialog.get_by_label("Geldig tot en met (optioneel)", exact=True).fill(until.isoformat())
    page.keyboard.press("Alt+P")
    box = dialog.get_by_role("combobox", name="Zoek een persoon of groep")
    expect(box).to_be_focused()
    box.press_sequentially("Ontvanger", delay=20)
    option = dialog.get_by_role("option", name=re.compile("Bob Ontvanger"))
    expect(option).to_be_visible()
    expect(box).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("ArrowDown")
    expect(box).to_have_attribute("aria-activedescendant", re.compile("."))
    expect(option).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Enter")
    expect(toast(page, "Gedeeld met Bob Ontvanger.")).to_be_visible()
    # De focus blijft in het zoekveld (niet naar <body>): meteen de volgende typen
    expect(box).to_be_focused()
    shared = dialog.get_by_role("list", name="Gedeeld met")
    expect(shared).to_contain_text("Bob Ontvanger")
    expect(shared).to_contain_text("verloopt op")
    shares = server.call(owner, "GET", f"/api/v1/files/{folder['id']}/shares", expect=200).json()
    person = next(s for s in shares if s["type"] == "user")
    assert person["user"]["id"] == bob.id and person["expires_at"] is not None
    assert datetime.fromisoformat(person["expires_at"]).astimezone(UTC).date() >= until - timedelta(
        days=1
    )

    # Sluiten: Gedeeld door mij toont nu ook Bob
    dialog.get_by_role("button", name="Sluiten", exact=True).last.click()
    expect(dialog).to_be_hidden()
    expect(page.get_by_role("row").filter(has_text="Bob Ontvanger")).to_have_count(1)

    # Bob ziet de map, om te bekijken
    other = context_as(bob).new_page()
    other.goto("/gedeeld")
    row = other.get_by_role("row").filter(has_text="Project")
    expect(row).to_contain_text("Kan bekijken")
    expect(row.get_by_role("link", name=re.compile("Project"))).to_have_attribute(
        "href", f"/map/{folder['id']}"
    )


def test_gedeeld_album_zonder_kopieren(context_as, server, user_factory, seeded):
    owner = user_factory(name="Album Eigenaar")
    bob = user_factory(name="Album Kijker")

    def seed_album(data_dir):
        import seed
        from app.models import Node, Share

        album_id = uuid.uuid4()
        now = datetime.now(UTC)
        with seed.connect(data_dir) as conn:
            conn.execute(
                Node.__table__.insert(),
                {
                    "id": album_id,
                    "parent_id": None,
                    "owner_id": uuid.UUID(owner.id),
                    "name": "Vakantiealbum",
                    "kind": "album",
                    "mime": None,
                    "size": 0,
                    "blob_id": None,
                    "created_at": now,
                    "modified_at": now,
                    "deleted_at": None,
                    "search_name": "vakantiealbum",
                    "taken_at": None,
                    "taken_day": None,
                },
            )
            conn.execute(
                Share.__table__.insert(),
                {
                    "id": uuid.uuid4(),
                    "node_id": album_id,
                    "owner_id": uuid.UUID(owner.id),
                    "grantee_id": uuid.UUID(bob.id),
                    "group_id": None,
                    "token": None,
                    "permission": "read",
                    "password_hash": None,
                    "expires_at": None,
                    "created_at": now,
                    "label": None,
                },
            )
        return str(album_id)

    album_id = seeded(seed_album)
    page = context_as(bob).new_page()
    page.goto("/gedeeld")
    row = page.get_by_role("row").filter(has_text="Vakantiealbum")
    expect(row).to_have_count(1)
    expect(row.get_by_role("link", name=re.compile("Vakantiealbum"))).to_have_attribute(
        "href", f"/album/{album_id}"
    )
    expect(row).not_to_contain_text("Kopiëren")
    expect(row.get_by_role("combobox")).to_have_count(0)


def test_selectie_van_drie_met_een_groep_delen_en_bekijken(context_as, server, admin, user_factory):
    owner = user_factory(name="Selectie Deler")
    bob = user_factory(name="Groep Lid")
    team = f"Team {uuid.uuid4().hex[:6]}"
    group = server.call(admin, "POST", "/api/v1/groups", json={"name": team}, expect=201).json()
    server.call(
        admin,
        "PUT",
        f"/api/v1/groups/{group['id']}/members",
        json={"user_ids": [bob.id]},
        expect=200,
    )
    work = mkdir(server, owner, owner.root_id, f"Werk {uuid.uuid4().hex[:6]}")
    ids = {
        n: put_file(server, owner, work, n, f"inhoud van {n}\n".encode())
        for n in ("a.txt", "b.txt", "c.txt")
    }

    # De selectiebalk van P2 opent de deeldialoog van P6 met alle drie (nodes=[...])
    page = context_as(owner).new_page()
    page.goto(f"/map/{work}")
    page.locator(ROWS).first.wait_for()
    pick(page, "a.txt")
    pick(page, "c.txt", ["Shift"])
    bar = page.get_by_role("toolbar", name="Selectie")
    expect(bar.locator(".selection-count")).to_have_text("3 geselecteerd")
    bar.get_by_role("button", name="Delen…", exact=True).click()
    dialog = page.get_by_role("dialog", name="3 items delen", exact=True)
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text("Je deelt 3 items met dezelfde persoon of groep.")).to_be_visible()
    items = dialog.get_by_role("list").filter(has_text="a.txt")
    expect(items.get_by_role("listitem")).to_have_count(3)

    page.keyboard.press("Alt+P")
    box = dialog.get_by_role("combobox", name="Zoek een persoon of groep")
    expect(box).to_be_focused()
    box.press_sequentially(team, delay=20)
    option = dialog.get_by_role("option", name=re.compile(re.escape(team)))
    expect(option).to_be_visible()
    page.keyboard.press("ArrowDown")
    expect(option).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Enter")
    expect(toast(page, "Gedeeld: 3 items.")).to_be_visible()
    for node_id in ids.values():
        shares = server.call(owner, "GET", f"/api/v1/files/{node_id}/shares", expect=200).json()
        groups = [s for s in shares if s["type"] == "group"]
        assert [g["group"]["id"] for g in groups] == [group["id"]]
        assert groups[0]["permission"] == "read"
    dialog.get_by_role("button", name="Sluiten", exact=True).last.click()
    expect(dialog).to_be_hidden()

    # Bob (lid van de groep) ziet ze in Gedeeld met mij; een bestand opent in de viewer (P4)
    other = context_as(bob).new_page()
    other.goto("/gedeeld")
    for name in ids:
        row = other.get_by_role("row").filter(has_text=name)
        expect(row).to_have_count(1)
        expect(row).to_contain_text(f"Groep {team}")
    other.get_by_role("row").filter(has_text="b.txt").get_by_role(
        "button", name="b.txt", exact=True
    ).click()
    viewer = other.get_by_role("dialog", name="b.txt", exact=True)
    expect(viewer).to_be_visible()
    expect(other).to_have_url(re.compile(rf"bestand={ids['b.txt']}"))
    stage = viewer.locator(".viewer-stage")
    expect(stage).to_have_attribute("data-kind", "text")
    expect(stage).to_contain_text("inhoud van b.txt")
    expect(viewer.get_by_text("2 / 3")).to_be_visible()
    other.keyboard.press("ArrowRight")
    viewer = other.get_by_role("dialog", name="c.txt", exact=True)
    expect(viewer.locator(".viewer-stage")).to_contain_text("inhoud van c.txt")
    other.keyboard.press("Escape")
    expect(other.get_by_role("dialog")).to_have_count(0)


def test_hooguit_honderd_items_tegelijk_delen(context_as, server, user_factory):
    owner = user_factory(name="Veel Deler")
    work = mkdir(server, owner, owner.root_id, f"Veel {uuid.uuid4().hex[:6]}")
    for i in range(101):
        put_file(server, owner, work, f"bestand-{i:03d}.txt", b"x")

    page = context_as(owner).new_page()
    page.goto(f"/map/{work}")
    page.locator(ROWS).first.wait_for()
    pick(page, "bestand-000.txt")
    page.keyboard.press("ControlOrMeta+a")
    bar = page.get_by_role("toolbar", name="Selectie")
    expect(bar.locator(".selection-count")).to_have_text("101 geselecteerd")

    # 101: Delen kan niet, en zegt waarom (de grens van de deeldialoog)
    reason = "Hooguit 100 items tegelijk; deel liever de map"
    share = bar.get_by_role("button", name="Delen…", exact=True)
    expect(share).to_have_attribute("aria-disabled", "true")
    expect(share).to_have_attribute("title", reason)
    # aria-disabled (niet disabled): de knop blijft focusbaar en een klik geeft de reden
    share.click(force=True)
    expect(toast(page, reason)).to_be_visible()
    expect(page.get_by_role("dialog")).to_have_count(0)

    # Eén eraf: 100 mag wel
    pick(page, "bestand-000.txt", ["ControlOrMeta"])
    expect(bar.locator(".selection-count")).to_have_text("100 geselecteerd")
    expect(share).not_to_have_attribute("aria-disabled", "true")
    share.click()
    dialog = page.get_by_role("dialog", name="100 items delen", exact=True)
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text("Je deelt 100 items met dezelfde persoon of groep.")).to_be_visible()
    expect(dialog.get_by_role("combobox", name="Zoek een persoon of groep")).to_be_visible()
