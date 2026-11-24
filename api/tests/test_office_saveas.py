"""Een kopie in een ander formaat (office_saveas.py): "Kopie opslaan als" in de editor
(onRequestSaveAs, POST …/save-as) en "Omzetten" bij hernoemen (POST …/convert-to). De nep-DS
speelt de editor en de conversie (tests/office_world.py); de adressen voor "opslaan als" komen
van World.stage_saveas, met de titel en procentcodes als laatste deel, zoals DS ze geeft."""

import uuid

import pytest

from app.office import convert, sessions, urls
from app.office.oo import convert as oo_convert
from app.routers import office_saveas
from app.routers.office_new import with_extension
from tests.factories import code_of, maker
from tests.fake_docserver import docx
from tests.office_world import office_world
from tests.test_office_convert import lying_docx

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(client, settings, monkeypatch):
    monkeypatch.setattr(oo_convert, "POLL_SECONDS", 0.01)
    convert.ASKS.reset()
    async with office_world(client, settings) as w:
        yield w
    convert.ASKS.reset()


async def names_in(world, folder: uuid.UUID) -> list[str]:
    from sqlalchemy import select

    from app.models import Node

    async with maker(world.base_settings)() as session:
        rows = await session.scalars(
            select(Node.name).where(Node.parent_id == folder, Node.deleted_at.is_(None))
        )
        return sorted(rows)


async def owner_of(world, node: str) -> uuid.UUID:
    row = await world.node_row(uuid.UUID(node))
    assert row is not None
    return row.owner_id


async def save_as(world, user, node, **body):
    return await world.client.post(
        f"/api/v1/office/files/{node}/save-as", json=body, headers=user.headers
    )


async def convert_to(world, user, node, **body):
    return await world.client.post(
        f"/api/v1/office/files/{node}/convert-to", json=body, headers=user.headers
    )


# --- de adressen van "opslaan als" (urls.rewrite, soort saveas) ---


def test_saveas_adres_met_titel_en_procentcodes(tmp_path):
    from tests.test_office_core import office

    settings = office(tmp_path)
    url = (
        "https://browser.example/office/cache/files/data/k1abcDEF_12/output.txt/"
        "Rapport%20%C3%A9%C3%A9n%20(1).txt?md5=x&expires=1&filename=Rapport%20een.txt"
    )
    got = urls.rewrite(settings, url, key="k1", kind="saveas")
    assert got == (
        "http://office/cache/files/data/k1abcDEF_12/output.txt/Rapport%20%C3%A9%C3%A9n%20(1).txt"
        "?md5=x&expires=1&filename=Rapport%20een.txt"
    )
    # Zonder eigen stuk na de key mag ook
    assert urls.rewrite(
        settings, "/cache/files/data/k1/output.docx/a.docx", key="k1", kind="saveas"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/cache/files/data/k2abc/output.txt/a.txt",  # een andere key
        "/cache/files/data/k1abc/output.txt/a%2fb.txt",  # een / na decoderen
        "/cache/files/data/k1abc/output.txt/a%2Fb.txt",
        "/cache/files/data/k1abc/output.txt/%2e%2e",  # .. na decoderen
        "/cache/files/data/k1abc/output.txt/%2E",
        "/cache/files/data/k1abc/output.txt/a%5cb.txt",  # een \\
        "/cache/files/data/k1abc/output.txt/a%00.txt",  # NUL
        "/cache/files/data/k1abc/output.txt/a%zz.txt",  # geen geldige procentcode
        "/cache/files/data/k1abc/output.txt/a%2",
        "/cache/files/data/k1abc/../k2/output.txt/a.txt",
        "/cache/files/data/k1abc",  # geen bestand
        "/cache/files/data/k1a.b/output.txt/a.txt",  # een punt in het eigen stuk van DS
        "/cache/files/forgotten/k1/output.txt/a.txt",
        "/cache/files/data/k1abc/output.txt/a b.txt",  # een echte spatie
    ],
)
def test_saveas_adres_weigert(tmp_path, path):
    from tests.test_office_core import office

    with pytest.raises(ValueError):
        urls.rewrite(office(tmp_path), path, key="k1", kind="saveas")


def test_alleen_saveas_mag_procentcodes(tmp_path):
    from tests.test_office_core import office

    with pytest.raises(ValueError):
        urls.rewrite(
            office(tmp_path), "/cache/files/data/k1/output.docx/a%20b", key="k1", kind="data"
        )


# --- de groepen van omzetten en de namen ---


def test_welke_formaten_per_bron():
    assert office_saveas.targets("docx") == ("docx", "odt", "rtf", "txt", "pdf")
    assert office_saveas.targets("txt") == ("docx", "odt", "rtf", "txt", "pdf")
    assert office_saveas.targets("csv") == ("xlsx", "ods", "csv", "pdf")
    assert office_saveas.targets("ppt") == ("pptx", "odp", "pdf")
    assert office_saveas.targets("png") == ()


def test_de_extensie_erbij():
    assert with_extension("Rapport", "txt") == "Rapport.txt"
    assert with_extension("Rapport.TXT", "txt") == "Rapport.TXT"
    assert with_extension("Rapport.docx", "txt") == "Rapport.docx.txt"
    assert len(with_extension("x" * 255, "txt")) == 255


# --- opslaan als ---


async def test_opslaan_als_txt_naast_het_origineel(world):
    anna = await world.user("anna@example.nl")
    folder = await world.folder(anna, "Werk")
    node = await world.docx(anna, folder, "Rapport.docx", ["Hallo"])
    key = await world.begin(anna, node)
    url = world.stage_saveas(key, b"Hallo\n", "txt", "Rapport kopie één.txt")
    r = await save_as(
        world, anna, node, key=key, url=url, file_type="txt", title="Rapport kopie één.txt"
    )
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["name"] == "Rapport kopie één.txt"
    assert made["parent_id"] == str(folder)
    assert await world.content(uuid.UUID(made["id"])) == b"Hallo\n"
    # Het origineel blijft wat het was
    assert await world.paragraphs(node) == ["Hallo"]
    # Nog eens: een vrije naam
    url = world.stage_saveas(key, b"Hallo\n", "txt", "Rapport kopie één.txt")
    r = await save_as(
        world, anna, node, key=key, url=url, file_type="TXT", title="Rapport kopie één.txt"
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Rapport kopie één (1).txt"


async def test_opslaan_als_de_naam(world):
    """Zonder titel de stam van het origineel; een titel zonder extensie krijgt die erbij."""
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "Brief.docx", ["Hoi"])
    key = await world.begin(anna, node)
    url = world.stage_saveas(key, docx.make_docx(["Hoi"]), "odt", "x.odt")
    r = await save_as(world, anna, node, key=key, url=url, file_type="odt")
    assert r.status_code == 201 and r.json()["name"] == "Brief.odt", r.text
    url = world.stage_saveas(key, b"Hoi", "txt", "Notities.txt")
    r = await save_as(world, anna, node, key=key, url=url, file_type="txt", title="Notities")
    assert r.status_code == 201 and r.json()["name"] == "Notities.txt", r.text


async def test_opslaan_als_alleen_met_jouw_open_sessie(world):
    anna = await world.user("anna@example.nl")
    bob = await world.user("bob@example.nl")
    folder = await world.folder(anna, "Gedeeld")
    await world.share(anna, folder, bob, write=True)
    node = await world.docx(anna, folder, "Plan.docx", ["Plan"])
    # Nog geen sessie
    r = await save_as(world, anna, node, key="k-geen", url="/x", file_type="txt")
    assert r.status_code == 404 and code_of(r) == "not_found"
    key = await world.begin(anna, node)
    url = world.stage_saveas(key, b"Plan", "txt", "Plan.txt")
    # Een andere key
    r = await save_as(world, anna, node, key=key + "x", url=url, file_type="txt")
    assert r.status_code == 404 and code_of(r) == "not_found"
    # Bob mag schrijven, maar zit niet in de sessie
    r = await save_as(world, bob, node, key=key, url=url, file_type="txt")
    assert r.status_code == 404 and code_of(r) == "not_found"
    # Een vreemde (geen toegang): 404, net als elk bestand dat je niet ziet
    eve = await world.user("eve@example.nl")
    r = await save_as(world, eve, node, key=key, url=url, file_type="txt")
    assert r.status_code == 404
    assert await names_in(world, folder) == ["Plan.docx"]


async def test_opslaan_als_in_een_gedeelde_map_wordt_van_de_eigenaar(world):
    anna = await world.user("anna@example.nl")
    bob = await world.user("bob@example.nl")
    folder = await world.folder(anna, "Gedeeld")
    await world.share(anna, folder, bob, write=True)
    node = await world.docx(anna, folder, "Plan.docx", ["Plan"])
    key = await world.begin(bob, node)
    url = world.stage_saveas(key, b"Plan", "txt", "Plan.txt")
    r = await save_as(world, bob, node, key=key, url=url, file_type="txt", title="Plan.txt")
    assert r.status_code == 201, r.text
    assert await owner_of(world, r.json()["id"]) == anna.id


async def test_opslaan_als_weigert_een_vreemd_adres_of_formaat(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "Brief.docx", ["Hoi"])
    key = await world.begin(anna, node)
    good = world.stage_saveas(key, b"Hoi", "txt", "Brief.txt")
    for url in (
        good.replace(key, "ander", 1),  # de map van een andere key
        good.replace("Brief.txt?", "..%2fx.txt?", 1),
        "https://cloud.example/office/cache/files/data/" + key + "/output.txt/%2e%2e",
    ):
        r = await save_as(world, anna, node, key=key, url=url, file_type="txt")
        assert r.status_code == 422 and code_of(r) == "invalid_input", url
    for file_type in ("exe", "html", "doc", ""):
        r = await save_as(world, anna, node, key=key, url=good, file_type=file_type)
        assert r.status_code == 422 and code_of(r) == "office_format", file_type
    # Een adres van de goede vorm, maar de handtekening van DS klopt niet: DS geeft 403
    missing = good.replace("Brief.txt?", "Anders.txt?", 1)
    r = await save_as(world, anna, node, key=key, url=missing, file_type="txt")
    assert r.status_code == 503 and code_of(r) == "office_unavailable"
    assert await names_in(world, await world.root(anna)) == ["Brief.docx"]


async def test_opslaan_als_met_een_vol_quotum(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "Brief.docx", ["Hoi"])
    key = await world.begin(anna, node)
    await world.room(anna, 10)
    url = world.stage_saveas(key, b"x" * 5000, "txt", "Groot.txt")
    r = await save_as(world, anna, node, key=key, url=url, file_type="txt")
    assert r.status_code == 413 and code_of(r) == "no_room"


async def test_opslaan_als_zonder_schrijfrecht_op_de_map(world):
    """Bob zit in de sessie, maar zijn schrijfrecht ging intussen weg: dan geen sessie meer van
    hem (sessions.can_write), dus 404, en er komt niets bij."""
    anna = await world.user("anna@example.nl")
    bob = await world.user("bob@example.nl")
    folder = await world.folder(anna, "Gedeeld")
    share = await world.share(anna, folder, bob, write=True)
    node = await world.docx(anna, folder, "Plan.docx", ["Plan"])
    key = await world.begin(bob, node)
    await world.revoke(anna, share)
    await world.share(anna, folder, bob, write=False)
    url = world.stage_saveas(key, b"Plan", "txt", "Plan.txt")
    r = await save_as(world, bob, node, key=key, url=url, file_type="txt")
    assert r.status_code == 404
    assert await names_in(world, folder) == ["Plan.docx"]


# --- omzetten ---


async def test_docx_naar_txt_een_kopie_het_origineel_blijft(world):
    anna = await world.user("anna@example.nl")
    folder = await world.folder(anna, "Werk")
    node = await world.docx(anna, folder, "Brief.docx", ["Hallo", "Wereld"])
    r = await convert_to(world, anna, node, format="txt")
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["name"] == "Brief.txt" and made["parent_id"] == str(folder)
    assert await world.content(uuid.UUID(made["id"])) == b"Hallo\nWereld"
    assert await world.paragraphs(node) == ["Hallo", "Wereld"]
    # DS kreeg nooit de naam van het bestand, en elke keer een nieuwe key
    r = await convert_to(world, anna, node, format="txt")
    assert r.status_code == 201 and r.json()["name"] == "Brief (1).txt"
    keys = [k for k in world.fake.conversions.cache if k.startswith("cv-")]
    assert len(keys) == 2


async def test_txt_naar_docx_met_een_naam(world):
    anna = await world.user("anna@example.nl")
    node = await world.put_file(anna, None, "notities.txt", b"een\ntwee\n")
    r = await convert_to(world, anna, node, format="docx", name="Notities netjes")
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["name"] == "Notities netjes.docx"
    assert await world.paragraphs(uuid.UUID(made["id"])) == ["een", "twee"]
    assert await world.content(node) == b"een\ntwee\n"


async def test_csv_naar_xlsx(world):
    anna = await world.user("anna@example.nl")
    node = await world.put_file(anna, None, "cijfers.csv", b"a;b\n1;2\n")
    r = await convert_to(world, anna, node, format="xlsx")
    assert r.status_code == 201 and r.json()["name"] == "cijfers.xlsx", r.text


async def test_omzetten_buiten_de_groep_kan_niet(world):
    anna = await world.user("anna@example.nl")
    doc = await world.docx(anna, None, "Brief.docx", ["Hoi"])
    txt = await world.put_file(anna, None, "a.txt", b"tekst")
    png = await world.put_file(anna, None, "a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    for node, fmt in (
        (doc, "xlsx"),
        (doc, "docx"),  # hetzelfde formaat
        (doc, "doc"),  # DS maakt geen doc
        (txt, "pptx"),
        (txt, "txt"),
        (png, "txt"),
    ):
        r = await convert_to(world, anna, node, format=fmt)
        assert r.status_code == 422 and code_of(r) == "office_format", (fmt, r.text)
    assert world.fake.conversions.started == 0


async def test_omzetten_een_fout_van_ds(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "Brief.docx", ["Hoi"])
    world.fake.conversions.fail = [-3]
    r = await convert_to(world, anna, node, format="odt")
    assert r.status_code == 502 and code_of(r) == "office_convert_to_failed", r.text
    assert r.json()["detail"]["params"] == {"code": -3}
    assert await names_in(world, await world.root(anna)) == ["Brief.docx"]


async def test_omzetten_alleen_als_office_aan_en_gezond_is(world):
    from app.main import app
    from app.office.state import OfficeConfig, office_policy

    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "Brief.docx", ["Hoi"])
    world.state.health = "down"
    r = await convert_to(world, anna, node, format="txt")
    assert r.status_code == 503 and code_of(r) == "office_unavailable"
    world.state.health = "ok"
    office_policy(app).config = OfficeConfig(enabled=False)
    r = await convert_to(world, anna, node, format="txt")
    assert r.status_code == 503 and code_of(r) == "office_off"


async def test_omzetten_met_alleen_leesrecht_op_de_map(world):
    anna = await world.user("anna@example.nl")
    bob = await world.user("bob@example.nl")
    folder = await world.folder(anna, "Gedeeld")
    await world.share(anna, folder, bob, write=False)
    node = await world.docx(anna, folder, "Plan.docx", ["Plan"])
    r = await convert_to(world, bob, node, format="txt")
    assert r.status_code == 403 and code_of(r) == "read_only"
    assert world.fake.conversions.started == 0


async def test_omzetten_na_de_invoercontrole(world):
    anna = await world.user("anna@example.nl")
    node = await world.put_file(anna, None, "Vreemd.docx", lying_docx())
    r = await convert_to(world, anna, node, format="txt")
    assert r.status_code == 422 and code_of(r) == "office_unsafe", r.text
    assert world.fake.conversions.started == 0


async def test_omzetten_begrensd_per_gebruiker(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "Brief.docx", ["Hoi"])
    for _ in range(10):
        assert (await convert_to(world, anna, node, format="txt")).status_code == 201
    r = await convert_to(world, anna, node, format="txt")
    assert r.status_code == 429 and code_of(r) == "rate_limited"


async def test_een_sessie_telt_niet_als_omzetten(world):
    """Omzetten raakt de sessie van de editor niet: de kopie is een nieuw bestand."""
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "Brief.docx", ["Hoi"])
    key = await world.begin(anna, node)
    r = await convert_to(world, anna, node, format="pdf")
    assert r.status_code == 201 and r.json()["name"] == "Brief.pdf", r.text
    async with maker(world.base_settings)() as session:
        row = await sessions.find_by_key(session, key)
        assert row is not None and row.state == "open"
