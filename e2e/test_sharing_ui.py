"""Delen in de browser (P6): een vervaldatum voor een persoon via de combobox (Alt+P, ↓,
Enter; Enter in het lege veld deelt), Gedeeld door mij, en een gedeeld album in Gedeeld met mij
zonder Kopiëren. Met de bestandsbrowser (P2) en de viewer (P4): een selectie van drie in één
keer met een groep delen, de grens van 100 items met zijn reden, en Gedeeld met mij opent een
bestand in de viewer.

Kiezen deelt nog niet: wie je kiest, komt op de lijst "Klaar om te delen", en pas Delen deelt
met de rechten die ernaast staan. Eerder deelde kiezen meteen met "Kan bekijken", en wie daarna
"Kan bewerken" koos, veranderde niets meer (de melding van Jonas): zie de tests
test_eerst_wie_dan_kan_bewerken_dan_delen*."""

from __future__ import annotations

import re
import time
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


def pending(dialog):
    """De lijst "Klaar om te delen": gekozen, nog niet gedeeld."""
    return dialog.get_by_role("list", name="Klaar om te delen", exact=True)


def people_shares(server, owner, node_id: str) -> list[tuple[str, str]]:
    """De delingen met personen en groepen: (id van de persoon of groep, rechten)."""
    shares = server.call(owner, "GET", f"/api/v1/files/{node_id}/shares", expect=200).json()
    return [
        ((s["user"] or s["group"])["id"], s["permission"]) for s in shares if s["type"] != "link"
    ]


def choose(dialog, text: str, name: str) -> None:
    """In het zoekveld typen en met de muis kiezen (zoals Jonas deed)."""
    box = dialog.get_by_role("combobox", name="Zoek een persoon of groep")
    box.click()
    box.press_sequentially(text, delay=20)
    dialog.get_by_role("option", name=re.compile(re.escape(name))).click()


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
    # Kiezen zet Bob op de lijst; gedeeld is er nog niets
    expect(pending(dialog)).to_contain_text("Bob Ontvanger")
    expect(box).to_be_focused()
    expect(box).to_have_value("")
    assert people_shares(server, owner, folder["id"]) == []
    # Enter in het lege zoekveld deelt met wie op de lijst staat
    page.keyboard.press("Enter")
    expect(toast(page, "Gedeeld met Bob Ontvanger.")).to_be_visible()
    expect(pending(dialog)).to_have_count(0)
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
    # Kopiëren naar… zit in het menu ⋯ (Acties voor …): een album heeft dat menu niet
    expect(row.get_by_role("button", name="Acties voor Vakantiealbum")).to_have_count(0)
    expect(row.get_by_role("button")).to_have_count(0)
    expect(row.get_by_role("combobox")).to_have_count(0)
    r = server.call(
        bob, "POST", "/api/v1/batch/copy", json={"ids": [album_id], "parent_id": bob.root_id}
    )
    assert [x["code"] for x in r.json()["results"]] == ["album"], r.text


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
    expect(
        dialog.get_by_text("Je deelt 3 items in één keer met wie je hieronder kiest.")
    ).to_be_visible()
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
    expect(pending(dialog)).to_contain_text(team)
    assert all(people_shares(server, owner, node_id) == [] for node_id in ids.values())
    page.keyboard.press("Enter")
    expect(toast(page, f"3 items gedeeld met {team}.")).to_be_visible()
    expect(box).to_be_focused()
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
    expect(
        dialog.get_by_text("Je deelt 100 items in één keer met wie je hieronder kiest.")
    ).to_be_visible()
    expect(dialog.get_by_role("combobox", name="Zoek een persoon of groep")).to_be_visible()
    # Delen kan pas als er iemand op de lijst staat
    expect(dialog.get_by_role("button", name="Delen", exact=True)).to_be_disabled()


def open_share_dialog(page, name: str):
    """De deeldialoog van één map of bestand, via de selectiebalk."""
    page.locator(ROWS).first.wait_for()
    pick(page, name)
    page.get_by_role("toolbar", name="Selectie").get_by_role(
        "button", name="Delen…", exact=True
    ).click()
    dialog = page.get_by_role("dialog", name=f"{name} delen", exact=True)
    expect(dialog).to_be_visible()
    return dialog


@pytest.mark.parametrize("soort", ["map", "bestand"])
def test_eerst_wie_dan_kan_bewerken_dan_delen(context_as, server, user_factory, soort):
    """De melding van Jonas: eerst de persoon kiezen, dan "Kan bewerken", dan Delen. De
    ontvanger moet dan echt kunnen bewerken; eerder deelde kiezen al meteen met "Kan bekijken"
    en deed de latere keuze niets meer."""
    owner = user_factory(name="Jonas Deler")
    bob = user_factory(name=f"Bewerker {uuid.uuid4().hex[:6]}")
    work = mkdir(server, owner, owner.root_id, f"Delen {uuid.uuid4().hex[:6]}")
    if soort == "map":
        name = "Samen"
        node_id = mkdir(server, owner, work, name)
    else:
        name = "plan.txt"
        node_id = put_file(server, owner, work, name, b"eerste versie\n")

    page = context_as(owner).new_page()
    page.goto(f"/map/{work}")
    dialog = open_share_dialog(page, name)
    rights = dialog.get_by_label("Rechten voor wie je toevoegt", exact=True)
    expect(rights).to_have_value("read")  # het minste vooraf

    # 1. de persoon, 2. dan pas de rechten
    choose(dialog, bob.display_name.split()[1], bob.display_name)
    rights.select_option(label="Kan bewerken")
    expect(rights).to_have_value("write")
    # Kiezen deelde nog niets: hij staat klaar (eerder was hier al gedeeld, met Kan bekijken)
    expect(pending(dialog)).to_contain_text(bob.display_name)
    expect(dialog.get_by_text("Nog met niemand gedeeld.", exact=True)).to_be_visible()
    assert people_shares(server, owner, node_id) == []
    # 3. Delen
    share = dialog.get_by_role("button", name="Delen", exact=True)
    expect(share).to_be_enabled()
    share.click()
    expect(toast(page, f"Gedeeld met {bob.display_name}.")).to_be_visible()
    expect(pending(dialog)).to_have_count(0)
    expect(share).to_be_disabled()
    expect(dialog.get_by_role("combobox", name="Zoek een persoon of groep")).to_be_focused()
    shared = dialog.get_by_role("list", name="Gedeeld met", exact=True)
    expect(shared).to_contain_text(bob.display_name)
    expect(dialog.get_by_label(f"Rechten voor {bob.display_name}", exact=True)).to_have_value(
        "write"
    )
    assert people_shares(server, owner, node_id) == [(bob.id, "write")]

    # De ontvanger: Kan bewerken in Gedeeld met mij, en hij kan echt schrijven
    other = context_as(bob).new_page()
    other.goto("/gedeeld")
    row = other.get_by_role("row").filter(has_text=name)
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Kan bewerken")
    expect(row).not_to_contain_text("Kan bekijken")
    if soort == "map":
        server.call(
            bob, "PUT", f"/api/v1/files/{node_id}/upload",
            params={"name": "van-de-ontvanger.txt"}, content=b"hallo\n", expect=201,
        )  # fmt: skip
        listing = server.call(owner, "GET", f"/api/v1/files/{node_id}/children", expect=200)
        assert [c["name"] for c in listing.json()["children"]] == ["van-de-ontvanger.txt"]
    else:
        server.call(
            bob, "PUT", f"/api/v1/files/{node_id}/content", content=b"tweede versie\n", expect=200
        )
        current = server.call(owner, "GET", f"/api/v1/files/{node_id}", expect=200).json()
        assert current["size"] == len(b"tweede versie\n")


def test_eerst_wie_dan_kan_bewerken_dan_delen_bij_een_selectie(context_as, server, user_factory):
    """Hetzelfde voor een selectie (de dialoog voor meerdere items): een map en een bestand,
    eerst de persoon, dan "Kan bewerken", dan Delen."""
    owner = user_factory(name="Jonas Selectie")
    bob = user_factory(name=f"Bewerker {uuid.uuid4().hex[:6]}")
    work = mkdir(server, owner, owner.root_id, f"Selectie {uuid.uuid4().hex[:6]}")
    folder = mkdir(server, owner, work, "Map A")
    file_id = put_file(server, owner, work, "b.txt", b"eerste versie\n")

    page = context_as(owner).new_page()
    page.goto(f"/map/{work}")
    page.locator(ROWS).first.wait_for()
    pick(page, "Map A")
    pick(page, "b.txt", ["ControlOrMeta"])
    bar = page.get_by_role("toolbar", name="Selectie")
    expect(bar.locator(".selection-count")).to_have_text("2 geselecteerd")
    bar.get_by_role("button", name="Delen…", exact=True).click()
    dialog = page.get_by_role("dialog", name="2 items delen", exact=True)
    expect(dialog).to_be_visible()

    choose(dialog, bob.display_name.split()[1], bob.display_name)
    rights = dialog.get_by_label("Rechten voor wie je toevoegt", exact=True)
    rights.select_option(label="Kan bewerken")
    # Kiezen deelde nog niets (eerder: meteen beide met Kan bekijken)
    expect(pending(dialog)).to_contain_text(bob.display_name)
    assert people_shares(server, owner, folder) == []
    assert people_shares(server, owner, file_id) == []
    dialog.get_by_role("button", name="Delen", exact=True).click()
    expect(toast(page, f"2 items gedeeld met {bob.display_name}.")).to_be_visible()
    expect(pending(dialog)).to_have_count(0)
    expect(dialog.get_by_role("alert")).to_have_count(0)
    expect(dialog.get_by_role("combobox", name="Zoek een persoon of groep")).to_be_focused()
    assert people_shares(server, owner, folder) == [(bob.id, "write")]
    assert people_shares(server, owner, file_id) == [(bob.id, "write")]

    other = context_as(bob).new_page()
    other.goto("/gedeeld")
    for name in ("Map A", "b.txt"):
        row = other.get_by_role("row").filter(has_text=name)
        expect(row).to_have_count(1)
        expect(row).to_contain_text("Kan bewerken")
    server.call(
        bob, "PUT", f"/api/v1/files/{folder}/upload",
        params={"name": "van-de-ontvanger.txt"}, content=b"hallo\n", expect=201,
    )  # fmt: skip
    server.call(bob, "PUT", f"/api/v1/files/{file_id}/content", content=b"tweede\n", expect=200)


def test_een_groep_eerst_kiezen_dan_kan_bewerken(context_as, server, admin, user_factory):
    """De stappen van Jonas met een groep: eerst de groep, dan "Kan bewerken", dan Delen. Een
    lid ziet "Kan bewerken" (via de groep) in Gedeeld met mij en kan echt uploaden."""
    owner = user_factory(name="Jonas Groep")
    lid = user_factory(name=f"Groepslid {uuid.uuid4().hex[:6]}")
    team = f"Team {uuid.uuid4().hex[:6]}"
    group = server.call(admin, "POST", "/api/v1/groups", json={"name": team}, expect=201).json()
    server.call(
        admin, "PUT", f"/api/v1/groups/{group['id']}/members",
        json={"user_ids": [lid.id]}, expect=200,
    )  # fmt: skip
    work = mkdir(server, owner, owner.root_id, f"Groep {uuid.uuid4().hex[:6]}")
    node_id = mkdir(server, owner, work, "Samen")

    page = context_as(owner).new_page()
    page.goto(f"/map/{work}")
    dialog = open_share_dialog(page, "Samen")
    choose(dialog, team, team)
    dialog.get_by_label("Rechten voor wie je toevoegt", exact=True).select_option(
        label="Kan bewerken"
    )
    expect(pending(dialog)).to_contain_text(team)
    assert people_shares(server, owner, node_id) == []
    dialog.get_by_role("button", name="Delen", exact=True).click()
    expect(toast(page, f"Gedeeld met {team}.")).to_be_visible()
    expect(dialog.get_by_label(f"Rechten voor {team}", exact=True)).to_have_value("write")
    assert people_shares(server, owner, node_id) == [(group["id"], "write")]

    other = context_as(lid).new_page()
    other.goto("/gedeeld")
    row = other.get_by_role("row").filter(has_text="Samen")
    expect(row).to_have_count(1)
    expect(row).to_contain_text(f"Groep {team}")
    expect(row).to_contain_text("Kan bewerken")
    server.call(
        lid, "PUT", f"/api/v1/files/{node_id}/upload",
        params={"name": "van-het-lid.txt"}, content=b"hallo\n", expect=201,
    )  # fmt: skip


def test_bekijken_wordt_bewerken_in_gedeeld_met(context_as, server, user_factory):
    """Een bestaande deling van "Kan bekijken" naar "Kan bewerken" in de lijst Gedeeld met
    (PATCH /shares/{id}): de ontvanger ziet het meteen in Gedeeld met mij, zonder te herladen,
    en kan dan echt opslaan. Terug naar "Kan bekijken" en opslaan mag weer niet."""
    owner = user_factory(name="Jonas Wijziger")
    bob = user_factory(name=f"Bewerker {uuid.uuid4().hex[:6]}")
    work = mkdir(server, owner, owner.root_id, f"Wijzigen {uuid.uuid4().hex[:6]}")
    file_id = put_file(server, owner, work, "plan.txt", b"eerste versie\n")
    server.call(
        owner, "POST", f"/api/v1/files/{file_id}/shares",
        json={"user_id": bob.id, "permission": "read"}, expect=201,
    )  # fmt: skip

    other = context_as(bob).new_page()
    other.goto("/gedeeld")
    row = other.get_by_role("row").filter(has_text="plan.txt")
    expect(row).to_contain_text("Kan bekijken")
    server.call(bob, "PUT", f"/api/v1/files/{file_id}/content", content=b"nee\n", expect=403)

    page = context_as(owner).new_page()
    page.goto(f"/map/{work}")
    dialog = open_share_dialog(page, "plan.txt")
    rights = dialog.get_by_label(f"Rechten voor {bob.display_name}", exact=True)
    expect(rights).to_have_value("read")

    def patch(response) -> bool:
        return response.request.method == "PATCH" and bool(
            re.search(r"/api/v1/shares/[^/]+$", response.url)
        )

    for label, value, status in (("Kan bewerken", "write", 200), ("Kan bekijken", "read", 403)):
        with page.expect_response(patch) as changed:
            rights.select_option(label=label)
        assert changed.value.status == 200
        assert changed.value.json()["permission"] == value
        expect(rights).to_have_value(value)
        expect(row).to_contain_text(label)  # live, via share.changed
        assert people_shares(server, owner, file_id) == [(bob.id, value)]
        server.call(
            bob, "PUT", f"/api/v1/files/{file_id}/content", content=b"tweede\n", expect=status
        )
    current = server.call(owner, "GET", f"/api/v1/files/{file_id}", expect=200).json()
    assert current["size"] == len(b"tweede\n")


def share_posts(page) -> list[str]:
    """De POST's naar /files/{id}/shares van deze pagina (ook wat een route opvangt)."""
    posts: list[str] = []

    def seen(request):
        if request.method == "POST" and re.search(r"/api/v1/files/[^/]+/shares$", request.url):
            posts.append(request.url)

    page.on("request", seen)
    return posts


def test_de_lijst_om_te_delen_weghalen_en_dubbelklik(context_as, server, user_factory):
    """Twee op de lijst, één eraf met ×: Delen deelt alleen met wie er nog staat, één keer, ook
    bij twee klikken vóór React opnieuw rendert; de vervaldatum en de rechten gelden voor
    iedereen op de lijst."""
    owner = user_factory(name="Lijst Deler")
    tag = uuid.uuid4().hex[:6]
    ans = user_factory(name=f"Ans {tag}")
    kees = user_factory(name=f"Kees {tag}")
    piet = user_factory(name=f"Piet {tag}")
    work = mkdir(server, owner, owner.root_id, f"Lijst {tag}")
    node_id = mkdir(server, owner, work, "Gezamenlijk")

    page = context_as(owner).new_page()
    posts = share_posts(page)
    page.goto(f"/map/{work}")
    dialog = open_share_dialog(page, "Gezamenlijk")
    share = dialog.get_by_role("button", name="Delen", exact=True)
    expect(share).to_be_disabled()  # nog niemand op de lijst
    for person in (ans, kees, piet):
        choose(dialog, person.display_name, person.display_name)
    items = pending(dialog).get_by_role("listitem")
    expect(items).to_have_count(3)
    expect(share).to_be_enabled()
    # Wie al op de lijst staat, wordt niet nog eens aangeboden
    box = dialog.get_by_role("combobox", name="Zoek een persoon of groep")
    box.press_sequentially(tag, delay=20)
    expect(dialog.get_by_text("Niemand gevonden.", exact=True)).to_be_visible()
    box.fill("")

    # Kees eraf: de focus gaat naar de × van de volgende (Piet)
    dialog.get_by_role("button", name=f"{kees.display_name} van de lijst halen").click()
    expect(items).to_have_count(2)
    expect(pending(dialog)).not_to_contain_text(kees.display_name)
    remove_piet = dialog.get_by_role("button", name=f"{piet.display_name} van de lijst halen")
    expect(remove_piet).to_be_focused()
    # Piet (de laatste) eraf: dan naar de × van de vorige (Ans); en Piet er weer op
    page.keyboard.press("Enter")
    expect(items).to_have_count(1)
    expect(
        dialog.get_by_role("button", name=f"{ans.display_name} van de lijst halen")
    ).to_be_focused()
    choose(dialog, piet.display_name, piet.display_name)
    expect(items).to_have_count(2)

    until = date.today() + timedelta(days=3)
    dialog.get_by_label("Geldig tot en met (optioneel)", exact=True).fill(until.isoformat())
    dialog.get_by_label("Rechten voor wie je toevoegt", exact=True).select_option("write")
    # Twee klikken in dezelfde taak, vóór React opnieuw rendert (een dubbelklik op een trage
    # telefoon): de knop is dan nog niet bezig; alleen de vlag in AddPeople houdt de tweede tegen
    share.evaluate("(b) => { b.click(); b.click(); }")
    expect(toast(page, f"Gedeeld met {ans.display_name} en {piet.display_name}.")).to_be_visible()
    expect(dialog.get_by_role("alert")).to_have_count(0)
    shares = server.call(owner, "GET", f"/api/v1/files/{node_id}/shares", expect=200).json()
    assert sorted((s["user"]["id"], s["permission"]) for s in shares) == sorted(
        [(ans.id, "write"), (piet.id, "write")]
    )
    assert all(s["expires_at"] is not None for s in shares)
    assert len(posts) == 2, posts  # één POST per persoon, niet twee
    # Na het delen is de datum weer leeg; de rechten blijven staan (je ziet ze)
    expect(dialog.get_by_label("Geldig tot en met (optioneel)", exact=True)).to_have_value("")


def test_sluiten_met_iemand_op_de_lijst_vraagt_eerst(context_as, server, user_factory):
    """Kiezen deelt niet meer meteen. Wie dan sluit (Escape, × of Sluiten) met iemand op "Klaar
    om te delen", krijgt eerst een vraag; eerder viel de lijst stil weg en kreeg de ander niets.
    Ook de derde Escape op rij (zonder klik ertussen) houdt de dialoog open: Chromium sloot hem
    dan zelf, buiten de vraag om."""
    owner = user_factory(name="Sluit Deler")
    bob = user_factory(name=f"Bewerker {uuid.uuid4().hex[:6]}")
    work = mkdir(server, owner, owner.root_id, f"Sluiten {uuid.uuid4().hex[:6]}")
    node_id = mkdir(server, owner, work, "Samen")

    page = context_as(owner).new_page()
    page.goto(f"/map/{work}")
    dialog = open_share_dialog(page, "Samen")
    choose(dialog, bob.display_name.split()[1], bob.display_name)
    rights = dialog.get_by_label("Rechten voor wie je toevoegt", exact=True)
    rights.select_option(label="Kan bewerken")
    # Zichtbaar, niet alleen voor schermlezers: hij staat klaar, nog niet gedeeld
    expect(pending(dialog)).to_contain_text(bob.display_name)
    expect(dialog.get_by_text("Klaar om te delen", exact=True)).to_be_visible()
    hint = dialog.get_by_text("Nog niet gedeeld: kies de rechten en druk op Delen.", exact=True)
    expect(hint).to_be_visible()

    ask = page.get_by_role("dialog", name="Sluiten zonder te delen?", exact=True)
    back = ask.get_by_role("button", name="Terug naar delen", exact=True)
    box = dialog.get_by_role("combobox", name="Zoek een persoon of groep")
    box.focus()
    for _ in range(3):
        # Escape in het lege zoekveld: eerst de vraag (de focus op Terug), de dialoog blijft
        page.keyboard.press("Escape")
        expect(ask).to_be_visible()
        expect(ask).to_contain_text(f"Nog niet gedeeld met {bob.display_name}.")
        expect(back).to_be_focused()
        expect(dialog).to_be_visible()
        # Escape op de vraag: terug, met de lijst, de rechten en de focus in het zoekveld
        page.keyboard.press("Escape")
        expect(ask).to_be_hidden()
        expect(dialog).to_be_visible()
        assert dialog.evaluate("(d) => d.open")
        expect(pending(dialog)).to_contain_text(bob.display_name)
        expect(rights).to_have_value("write")
        expect(box).to_be_focused()
    assert people_shares(server, owner, node_id) == []

    # Sluiten onderaan: de vraag; Terug naar delen houdt alles zoals het was
    dialog.locator(".dialog-actions").get_by_role("button", name="Sluiten", exact=True).click()
    expect(ask).to_be_visible()
    back.click()
    expect(ask).to_be_hidden()
    expect(dialog).to_be_visible()
    expect(pending(dialog)).to_contain_text(bob.display_name)
    # De × bovenaan, en dan echt sluiten zonder delen
    dialog.locator("header").get_by_role("button", name="Sluiten", exact=True).click()
    expect(ask).to_be_visible()
    ask.get_by_role("button", name="Sluiten zonder delen", exact=True).click()
    expect(ask).to_be_hidden()
    expect(dialog).to_be_hidden()
    assert people_shares(server, owner, node_id) == []

    # Na Delen is de lijst leeg: dan sluit Escape meteen, zonder vraag
    dialog = open_share_dialog(page, "Samen")
    choose(dialog, bob.display_name.split()[1], bob.display_name)
    dialog.get_by_role("button", name="Delen", exact=True).click()
    expect(toast(page, f"Gedeeld met {bob.display_name}.")).to_be_visible()
    expect(pending(dialog)).to_have_count(0)
    expect(dialog.get_by_role("combobox", name="Zoek een persoon of groep")).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(ask).to_have_count(0)
    assert people_shares(server, owner, node_id) == [(bob.id, "read")]


def test_sluiten_bij_een_selectie_vraagt_ook_eerst(context_as, server, user_factory):
    """Ook de dialoog voor meerdere items vraagt eerst, zolang er iemand op "Klaar om te delen"
    staat. Escape in het zoekveld sluit eerst de lijst met namen, maakt dan het veld leeg, en pas
    daarna komt de vraag: ui/Dialog handelt Escape zelf af, maar na de combobox. De browser
    stuurt daarbij geen cancel, dus de dialoog krijgt elke Escape één keer."""
    owner = user_factory(name="Selectie Sluiter")
    tag = uuid.uuid4().hex[:6]
    bob = user_factory(name=f"Bewerker {tag}")
    zoe = user_factory(name=f"Zoe {tag}")
    work = mkdir(server, owner, owner.root_id, f"Sluiten {tag}")
    ids = [put_file(server, owner, work, name, b"x\n") for name in ("a.txt", "b.txt")]

    page = context_as(owner).new_page()
    page.goto(f"/map/{work}")
    page.locator(ROWS).first.wait_for()
    pick(page, "a.txt")
    pick(page, "b.txt", ["ControlOrMeta"])
    bar = page.get_by_role("toolbar", name="Selectie")
    expect(bar.locator(".selection-count")).to_have_text("2 geselecteerd")
    bar.get_by_role("button", name="Delen…", exact=True).click()
    dialog = page.get_by_role("dialog", name="2 items delen", exact=True)
    expect(dialog).to_be_visible()
    page.evaluate(
        """() => {
          window.__cancels = 0;
          document.addEventListener('cancel', () => { window.__cancels++; }, true);
        }"""
    )
    choose(dialog, bob.display_name.split()[1], bob.display_name)
    expect(pending(dialog)).to_contain_text(bob.display_name)

    ask = page.get_by_role("dialog", name="Sluiten zonder te delen?", exact=True)
    box = dialog.get_by_role("combobox", name="Zoek een persoon of groep")
    box.press_sequentially(zoe.display_name, delay=20)
    option = dialog.get_by_role("option", name=re.compile(re.escape(zoe.display_name)))
    expect(option).to_be_visible()
    expect(box).to_have_attribute("aria-expanded", "true")
    # 1. de lijst met namen dicht; het veld houdt de tekst en de focus
    page.keyboard.press("Escape")
    expect(box).to_have_attribute("aria-expanded", "false")
    expect(box).to_have_value(zoe.display_name)
    expect(box).to_be_focused()
    # 2. het veld leeg
    page.keyboard.press("Escape")
    expect(box).to_have_value("")
    expect(box).to_be_focused()
    expect(ask).to_have_count(0)
    # 3. de vraag; Escape daarop gaat terug, met de lijst
    page.keyboard.press("Escape")
    expect(ask).to_be_visible()
    expect(ask).to_contain_text(f"Nog niet gedeeld met {bob.display_name}.")
    page.keyboard.press("Escape")
    expect(ask).to_be_hidden()
    expect(box).to_be_focused()
    expect(pending(dialog)).to_contain_text(bob.display_name)
    # Sluiten onderaan: de vraag, en dan echt sluiten zonder delen
    dialog.locator(".dialog-actions").get_by_role("button", name="Sluiten", exact=True).click()
    expect(ask).to_be_visible()
    ask.get_by_role("button", name="Sluiten zonder delen", exact=True).click()
    expect(dialog).to_be_hidden()
    assert all(people_shares(server, owner, node_id) == [] for node_id in ids)
    assert page.evaluate("() => window.__cancels") == 0


def test_selectie_met_een_item_dat_al_gedeeld_is(context_as, server, user_factory):
    """Een selectie van drie: Map A had Bob al (Kan bekijken), en c.txt mislukt de eerste keer
    (503). Map A telt als klaar en houdt zijn rechten, en de melding noemt hem; nog een keer
    Delen stuurt alleen c.txt, en dan gaat Bob van de lijst af. Eerder bleef hij erop staan en
    deelde Delen alles opnieuw ("lukte bij 2 items niet: Al gedeeld")."""
    owner = user_factory(name="Jonas Opnieuw")
    bob = user_factory(name=f"Bewerker {uuid.uuid4().hex[:6]}")
    work = mkdir(server, owner, owner.root_id, f"Opnieuw {uuid.uuid4().hex[:6]}")
    folder = mkdir(server, owner, work, "Map A")
    b_id = put_file(server, owner, work, "b.txt", b"b\n")
    c_id = put_file(server, owner, work, "c.txt", b"c\n")
    server.call(
        owner, "POST", f"/api/v1/files/{folder}/shares",
        json={"user_id": bob.id, "permission": "read"}, expect=201,
    )  # fmt: skip

    page = context_as(owner).new_page()
    posts = share_posts(page)
    failed: list[str] = []

    def once_unavailable(route):
        if route.request.method == "POST" and not failed:
            failed.append(route.request.url)
            body = {"detail": {"message": "Even niet beschikbaar", "code": "unavailable"}}
            route.fulfill(status=503, json=body)
        else:
            route.fallback()

    page.route(f"**/api/v1/files/{c_id}/shares", once_unavailable)
    page.goto(f"/map/{work}")
    page.locator(ROWS).first.wait_for()
    pick(page, "Map A")
    pick(page, "b.txt", ["ControlOrMeta"])
    pick(page, "c.txt", ["ControlOrMeta"])
    bar = page.get_by_role("toolbar", name="Selectie")
    expect(bar.locator(".selection-count")).to_have_text("3 geselecteerd")
    bar.get_by_role("button", name="Delen…", exact=True).click()
    dialog = page.get_by_role("dialog", name="3 items delen", exact=True)
    expect(dialog).to_be_visible()

    choose(dialog, bob.display_name.split()[1], bob.display_name)
    rights = dialog.get_by_label("Rechten voor wie je toevoegt", exact=True)
    rights.select_option(label="Kan bewerken")
    share = dialog.get_by_role("button", name="Delen", exact=True)
    share.click()
    expect(toast(page, f"1 item gedeeld met {bob.display_name}.")).to_be_visible()
    alert = dialog.get_by_role("alert")
    expect(alert).to_contain_text(
        f"{bob.display_name} had Map A al: die deling blijft zoals hij was, "
        "met de rechten van toen."
    )
    expect(alert).to_contain_text(
        f"Delen met {bob.display_name} lukte bij 1 item niet: Even niet beschikbaar"
    )
    # c.txt mist nog: Bob blijft staan, Delen kan nog een keer
    expect(pending(dialog)).to_contain_text(bob.display_name)
    expect(share).to_be_enabled()
    assert len(posts) == 3, posts
    assert people_shares(server, owner, folder) == [(bob.id, "read")]
    assert people_shares(server, owner, b_id) == [(bob.id, "write")]
    assert people_shares(server, owner, c_id) == []

    # Nog een keer: alleen c.txt (niet Map A en b.txt opnieuw), en dan is Bob klaar
    share.click()
    expect(pending(dialog)).to_have_count(0)
    expect(alert).to_have_count(0)
    expect(share).to_be_disabled()
    assert posts[3:] == [posts[2]], posts
    assert posts[2].endswith(f"/files/{c_id}/shares")
    assert people_shares(server, owner, c_id) == [(bob.id, "write")]
    assert people_shares(server, owner, folder) == [(bob.id, "read")]  # de oude rechten
    assert people_shares(server, owner, b_id) == [(bob.id, "write")]
    # Wie alles al heeft, wordt niet meer aangeboden
    box = dialog.get_by_role("combobox", name="Zoek een persoon of groep")
    box.press_sequentially(bob.display_name.split()[1], delay=20)
    expect(dialog.get_by_text("Niemand gevonden.", exact=True)).to_be_visible()


def test_focus_blijft_op_delen_tijdens_het_delen(context_as, server, user_factory):
    """Met het toetsenbord: Enter op Delen bij een selectie. Zolang het delen loopt, blijft de
    focus op Delen (bezig: aria-busy, niet uitgeschakeld) en nooit op <body>; daarna in het
    zoekveld. Eerder werd Delen uitgeschakeld en stond de focus de hele lus op <body>."""
    owner = user_factory(name="Focus Deler")
    tag = uuid.uuid4().hex[:6]
    ans = user_factory(name=f"Ans {tag}")
    kees = user_factory(name=f"Kees {tag}")
    work = mkdir(server, owner, owner.root_id, f"Focus {tag}")
    for name in ("a.txt", "b.txt", "c.txt"):
        put_file(server, owner, work, name, b"x")

    page = context_as(owner).new_page()

    def slow(route):
        # Elke deling even trager, zodat er tijdens het delen iets te meten valt
        if route.request.method == "POST":
            time.sleep(0.15)
        route.fallback()

    page.route("**/api/v1/files/*/shares", slow)
    page.goto(f"/map/{work}")
    page.locator(ROWS).first.wait_for()
    pick(page, "a.txt")
    page.keyboard.press("ControlOrMeta+a")
    bar = page.get_by_role("toolbar", name="Selectie")
    expect(bar.locator(".selection-count")).to_have_text("3 geselecteerd")
    bar.get_by_role("button", name="Delen…", exact=True).click()
    dialog = page.get_by_role("dialog", name="3 items delen", exact=True)
    expect(dialog).to_be_visible()
    for person in (ans, kees):
        choose(dialog, person.display_name, person.display_name)
    expect(pending(dialog).get_by_role("listitem")).to_have_count(2)

    share = dialog.get_by_role("button", name="Delen", exact=True)
    share.focus()
    # Waar de focus staat zolang de voortgang er is, elke 10 ms
    page.evaluate(
        """() => {
          window.__focus = [];
          window.__timer = setInterval(() => {
            if (!document.querySelector('.share-progress')) return;
            const el = document.activeElement;
            window.__focus.push(!el || el === document.body ? 'BODY' : el.textContent.trim());
          }, 10);
        }"""
    )
    page.keyboard.press("Enter")
    progress = dialog.locator(".share-progress")
    expect(progress).to_be_visible()
    expect(share).to_have_attribute("aria-busy", "true")
    expect(share).to_be_enabled()
    expect(progress).to_be_hidden(timeout=30_000)
    done = f"3 items gedeeld met {ans.display_name} en {kees.display_name}."
    expect(toast(page, done)).to_be_visible()
    seen = page.evaluate("() => { clearInterval(window.__timer); return window.__focus; }")
    assert len(seen) >= 5, seen
    assert set(seen) == {"Delen"}, seen  # nooit <body>, nooit ergens anders
    expect(dialog.get_by_role("combobox", name="Zoek een persoon of groep")).to_be_focused()
    expect(share).to_be_disabled()
