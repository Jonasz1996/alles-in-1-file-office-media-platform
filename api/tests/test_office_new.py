"""POST /api/v1/office/new (O4, Keuze 24): een leeg docx, xlsx of pptx in een map. In een eigen
map, in een gedeelde map met schrijfrecht (quotum van de eigenaar), een map met alleen
leesrecht (403), naamconflicten, quotum vol, Engelse namen bij locale en, en de fouten van het
contract. Het werkt ook als DS weg is: er gaat niets naar DS."""

import secrets
import uuid

import httpx
import pytest
from sqlalchemy import select

from app import security
from app.config import Settings
from app.main import app
from app.models import Blob, Node, User
from app.office import blank, engine, runtime
from app.office.oo import client as ds_client
from app.office.state import OfficeConfig, office_policy, office_state
from app.routers.auth import used_bytes
from app.storage import BlobStore
from tests.factories import code_of, create_album, create_file, create_folder, create_user, maker

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def office_settings(settings: Settings) -> Settings:
    return Settings(
        **{
            **settings.model_dump(),
            "office_url": "http://office",
            "office_app_url": "http://platform:8081",
            "office_secret": secrets.token_urlsafe(32),
        }
    )


@pytest.fixture
def office(settings):
    """Office ingesteld (zoals de lifespan), DS gezond; daarna alles weer weg."""
    runtime.bind(app, office_settings(settings))
    engine.reset()
    office_state(app).health = "ok"
    office_policy(app).config = OfficeConfig()
    yield office_state(app)
    runtime.unbind()
    engine.reset()
    ds_client.transport = None
    for name in ("office", "office_policy"):
        if hasattr(app.state, name):
            delattr(app.state, name)


class Person:
    def __init__(self, user: User, headers: dict[str, str]) -> None:
        self.id = user.id
        self.root = user.root_id
        self.headers = headers


@pytest.fixture
def people(client, settings):
    async def make(email: str, **kw) -> Person:
        async with maker(settings)() as session:
            user = await create_user(session, email, **kw)
            await session.commit()
            token = security.access_token(settings, user, uuid.uuid4())
            return Person(user, {"Authorization": f"Bearer {token}"})

    return make


async def new(client, person: Person, parent, kind: str = "docx", *, headers=None, **extra):
    body = {"parent_id": str(parent), "kind": kind, **extra}
    return await client.post(
        "/api/v1/office/new", json=body, headers={**person.headers, **(headers or {})}
    )


async def content(settings, node_id: str) -> bytes:
    async with maker(settings)() as session:
        node = await session.get(Node, uuid.UUID(node_id))
        assert node is not None and node.blob_id
        return BlobStore(settings.blob_dir).path(node.blob_id).read_bytes()


async def names_in(settings, folder) -> list[str]:
    async with maker(settings)() as session:
        rows = await session.scalars(
            select(Node.name).where(Node.parent_id == folder, Node.deleted_at.is_(None))
        )
        return sorted(rows)


async def share(client, owner: Person, folder, other: Person, permission: str) -> None:
    r = await client.post(
        f"/api/v1/files/{folder}/shares",
        json={"user_id": str(other.id), "permission": permission},
        headers=owner.headers,
    )
    assert r.status_code == 201, r.text


async def set_quota(settings, person: Person, free: int) -> None:
    async with maker(settings)() as session:
        user = await session.get(User, person.id)
        assert user is not None
        user.quota_bytes = await used_bytes(session, person.id) + free
        await session.commit()


async def used(settings, person: Person) -> int:
    async with maker(settings)() as session:
        return await used_bytes(session, person.id)


# --- in een eigen map ---


@pytest.mark.anyio
async def test_alle_drie_in_een_eigen_map(client, settings, office, people):
    anna = await people("anna@example.nl")
    for kind, name, mime in (
        ("docx", "Nieuw document.docx", DOCX),
        ("xlsx", "Nieuw werkblad.xlsx", XLSX),
        ("pptx", "Nieuwe presentatie.pptx", PPTX),
    ):
        r = await new(client, anna, anna.root, kind)
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["name"] == name
        assert out["kind"] == "file"
        assert out["mime"] == mime
        assert out["parent_id"] == str(anna.root)
        assert out["permission"] == "owner"
        assert out["size"] == len(blank.blank(kind, "nl"))
        assert await content(settings, out["id"]) == blank.blank(kind, "nl")
    assert await names_in(settings, anna.root) == [
        "Nieuw document.docx",
        "Nieuw werkblad.xlsx",
        "Nieuwe presentatie.pptx",
    ]


@pytest.mark.anyio
async def test_een_blob_voor_alle_lege_documenten_van_een_soort_en_taal(
    client, settings, office, people
):
    anna = await people("anna@example.nl")
    first = (await new(client, anna, anna.root)).json()
    second = (await new(client, anna, anna.root)).json()
    assert first["etag"] == second["etag"]
    async with maker(settings)() as session:
        blob = await session.get(Blob, first["etag"].strip('"'))
        assert blob is not None and blob.ref_count == 2
        # Een ingelogde gebruiker maakte hem: hij mag vanzelf naar de conversie (geen "ask")
        assert blob.pdf_state != "ask"


@pytest.mark.anyio
async def test_naamconflict_geeft_een_nummer(client, settings, office, people):
    anna = await people("anna@example.nl")
    async with maker(settings)() as session:
        user = await session.get(User, anna.id)
        assert user is not None
        # Een bestaand bestand met dezelfde naam in andere hoofdletters telt ook
        await create_file(session, user, name="nieuw document.docx")
        await session.commit()
    names = [(await new(client, anna, anna.root)).json()["name"] for _ in range(2)]
    assert names == ["Nieuw document (1).docx", "Nieuw document (2).docx"]


@pytest.mark.anyio
async def test_een_eigen_naam_krijgt_de_extensie_erbij(client, settings, office, people):
    anna = await people("anna@example.nl")
    cases = [
        ("docx", "Rapport", "Rapport.docx"),
        ("docx", "  Notulen.DOCX ", "Notulen.DOCX"),
        ("xlsx", "Begroting.xlsx", "Begroting.xlsx"),
        ("pptx", "plan.docx", "plan.docx.pptx"),
        ("docx", ".docx", ".docx.docx"),
        ("xlsx", "   ", "Nieuw werkblad.xlsx"),
    ]
    for kind, given, expected in cases:
        r = await new(client, anna, anna.root, kind, name=given)
        assert r.status_code == 201, (given, r.text)
        assert r.json()["name"] == expected, given
    # Te lang met de extensie erbij: de stam wordt korter, de naam past precies
    r = await new(client, anna, anna.root, "docx", name="x" * 255)
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "x" * 250 + ".docx"
    for bad in ("a/b", "..", "tab\there"):
        r = await new(client, anna, anna.root, "docx", name=bad)
        assert r.status_code == 422 and code_of(r) == "invalid_name", bad
    r = await new(client, anna, anna.root, "docx", name="x" * 256)
    assert r.status_code == 422 and code_of(r) == "invalid_input"


@pytest.mark.anyio
async def test_engelse_namen_bij_locale_en(client, settings, office, people):
    bob = await people("bob@example.nl")
    async with maker(settings)() as session:
        user = await session.get(User, bob.id)
        assert user is not None
        user.locale = "en"
        await session.commit()
    for kind, name in (
        ("docx", "New document.docx"),
        ("xlsx", "New spreadsheet.xlsx"),
        ("pptx", "New presentation.pptx"),
    ):
        # De keuze in het account gaat voor de taal van de browser
        r = await new(client, bob, bob.root, kind, headers={"Accept-Language": "nl-NL,nl"})
        assert r.status_code == 201, r.text
        assert r.json()["name"] == name
        assert await content(settings, r.json()["id"]) == blank.blank(kind, "en")


@pytest.mark.anyio
async def test_zonder_keuze_de_eerste_taal_van_de_browser(client, settings, office, people):
    cara = await people("cara@example.nl")
    for header, name in (
        ({"Accept-Language": "en-GB,en;q=0.9,nl;q=0.8"}, "New document.docx"),
        ({"Accept-Language": "de-DE,de;q=0.9"}, "New document (1).docx"),  # zoals de web-app: en
        ({"Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8"}, "Nieuw document.docx"),
        ({}, "Nieuw document (1).docx"),
        ({"Accept-Language": "*"}, "Nieuw document (2).docx"),
    ):
        r = await new(client, cara, cara.root, headers=header)
        assert r.status_code == 201, r.text
        assert r.json()["name"] == name, header


# --- gedeeld ---


@pytest.mark.anyio
async def test_in_een_gedeelde_map_met_schrijfrecht_van_de_eigenaar(
    client, settings, office, people
):
    anna = await people("anna@example.nl")
    bram = await people("bram@example.nl")
    async with maker(settings)() as session:
        user = await session.get(User, anna.id)
        assert user is not None
        folder = (await create_folder(session, user, name="Samen")).id
        await session.commit()
    await share(client, anna, folder, bram, "write")
    before_a, before_b = await used(settings, anna), await used(settings, bram)

    r = await new(client, bram, folder, "xlsx")
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["permission"] == "write"
    assert out["owner"]["id"] == str(anna.id)
    size = len(blank.blank("xlsx", "nl"))
    # Het quotum van de eigenaar, niet dat van wie het maakt (B18)
    assert await used(settings, anna) == before_a + size
    assert await used(settings, bram) == before_b
    async with maker(settings)() as session:
        node = await session.get(Node, uuid.UUID(out["id"]))
        assert node is not None and node.owner_id == anna.id and node.parent_id == folder

    # Het quotum van Anna is op: Bram krijgt no_room_owner met haar naam
    await set_quota(settings, anna, 10)
    r = await new(client, bram, folder, "docx")
    assert r.status_code == 413 and code_of(r) == "no_room_owner", r.text
    assert await names_in(settings, folder) == ["Nieuw werkblad.xlsx"]


@pytest.mark.anyio
async def test_een_map_met_alleen_leesrecht_geeft_403(client, settings, office, people):
    anna = await people("anna@example.nl")
    bram = await people("bram@example.nl")
    async with maker(settings)() as session:
        user = await session.get(User, anna.id)
        assert user is not None
        folder = (await create_folder(session, user, name="Kijken")).id
        await session.commit()
    await share(client, anna, folder, bram, "read")
    r = await new(client, bram, folder)
    assert r.status_code == 403 and code_of(r) == "read_only", r.text
    assert await names_in(settings, folder) == []


@pytest.mark.anyio
async def test_een_gast_in_een_gedeelde_map(client, settings, office, people):
    anna = await people("anna@example.nl")
    gast = await people("gast@example.nl", role="guest")
    async with maker(settings)() as session:
        user = await session.get(User, anna.id)
        assert user is not None
        folder = (await create_folder(session, user, name="Voor gasten")).id
        await session.commit()
    await share(client, anna, folder, gast, "write")
    r = await new(client, gast, folder, "pptx")
    assert r.status_code == 201, r.text
    assert r.json()["owner"]["id"] == str(anna.id)


# --- quotum en de map ---


@pytest.mark.anyio
async def test_quotum_vol(client, settings, office, people):
    anna = await people("anna@example.nl")
    await set_quota(settings, anna, 100)  # kleiner dan een leeg document
    r = await new(client, anna, anna.root)
    assert r.status_code == 413 and code_of(r) == "no_room", r.text
    assert await names_in(settings, anna.root) == []
    # Precies genoeg ruimte: dan wel
    await set_quota(settings, anna, len(blank.blank("docx", "nl")))
    assert (await new(client, anna, anna.root)).status_code == 201


@pytest.mark.anyio
async def test_geen_map_geeft_folder_not_found(client, settings, office, people):
    anna = await people("anna@example.nl")
    other = await people("ander@example.nl")
    async with maker(settings)() as session:
        user = await session.get(User, anna.id)
        assert user is not None
        file_id = (await create_file(session, user, name="los.txt")).id
        trashed = (await create_folder(session, user, name="Weg", deleted=True)).id
        below = (await create_folder(session, user, trashed, name="Eronder")).id
        await session.commit()
    for parent in (uuid.uuid4(), file_id, trashed, below, other.root):
        r = await new(client, anna, parent)
        assert r.status_code == 404 and code_of(r) == "folder_not_found", (parent, r.text)


@pytest.mark.anyio
async def test_een_album_geeft_422(client, settings, office, people):
    anna = await people("anna@example.nl")
    async with maker(settings)() as session:
        user = await session.get(User, anna.id)
        assert user is not None
        album = (await create_album(session, user)).id
        await session.commit()
    r = await new(client, anna, album)
    assert r.status_code == 422 and code_of(r) == "album", r.text


@pytest.mark.anyio
async def test_een_andere_soort_of_geen_login(client, settings, office, people):
    anna = await people("anna@example.nl")
    for kind in ("odt", "doc", "txt", ""):
        r = await new(client, anna, anna.root, kind)
        assert r.status_code == 422 and code_of(r) == "invalid_input", kind
    r = await client.post("/api/v1/office/new", json={"parent_id": str(anna.root), "kind": "docx"})
    assert r.status_code == 401


# --- office uit, of DS weg ---


@pytest.mark.anyio
@pytest.mark.parametrize("health", ["down", "starting", "misconfigured"])
async def test_werkt_ook_als_ds_weg_is(client, settings, office, people, health):
    """Nieuw document is gewoon een bestand (Keuze 28): er gaat niets naar DS."""
    office.health = health

    def nowhere(request):  # elk verzoek naar DS faalt de test
        raise AssertionError(f"verzoek naar DS: {request.url}")

    ds_client.transport = httpx.MockTransport(nowhere)
    anna = await people("anna@example.nl")
    r = await new(client, anna, anna.root)
    assert r.status_code == 201, r.text


@pytest.mark.anyio
async def test_zonder_office_of_uitgezet_office_off(client, settings, people):
    anna = await people("anna@example.nl")
    # Niet ingesteld: precies fase 3
    r = await new(client, anna, anna.root)
    assert r.status_code == 503 and code_of(r) == "office_off"
    # Ingesteld, maar de beheerder zette office uit
    runtime.bind(app, office_settings(settings))
    try:
        office_state(app).health = "ok"
        office_policy(app).config = OfficeConfig(enabled=False)
        r = await new(client, anna, anna.root)
        assert r.status_code == 503 and code_of(r) == "office_off"
        office_policy(app).config = OfficeConfig(enabled=True)
        assert (await new(client, anna, anna.root)).status_code == 201
    finally:
        runtime.unbind()
        engine.reset()
        for name in ("office", "office_policy"):
            if hasattr(app.state, name):
                delattr(app.state, name)
    assert await names_in(settings, anna.root) == ["Nieuw document.docx"]
