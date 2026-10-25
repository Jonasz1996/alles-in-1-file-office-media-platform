"""Delen in de browser (P6): een vervaldatum voor een persoon via de combobox (Alt+P, ↓,
Enter), Gedeeld door mij, en een gedeeld album in Gedeeld met mij zonder Kopiëren."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.p6


def toast(page, text: str):
    return page.get_by_role("region", name="Meldingen").get_by_text(text, exact=True)


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
