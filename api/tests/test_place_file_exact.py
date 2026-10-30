"""place_file met exact, squash en pdf_state (Keuze 16, 17 en 25; §3.5, §3.8): opslaan uit de
editor schrijft in het bestand zelf, ook als het intussen hernoemd, verplaatst of in de
prullenbak is; weg geeft 404. Een tussenstand van dezelfde sessie wordt geen versie, en de
oude blob is daarna vrij voor collect_garbage. Een nieuwe blob zonder actor krijgt "ask"."""

import hashlib
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.models import Blob, Node, Version
from app.routers.files import collect_garbage, place_file
from app.storage import BlobStore
from tests.factories import create_file, create_folder, create_user, maker
from tests.fake_docserver import docx

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def content(text: str) -> bytes:
    return docx.make_docx([text])


def source(settings, data: bytes) -> tuple:
    """Een bestand naast de blobs, zoals een download van DS (zelfde schijf)."""
    tmp = settings.blob_dir / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / uuid.uuid4().hex
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest(), len(data)


@pytest.fixture
async def world(settings):
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        owner = await create_user(session, "anna@example.nl")
        folder = await create_folder(session, owner, name="Werk")
        other = await create_folder(session, owner, name="Archief")
        node = await create_file(
            session, owner, folder.id, "verslag.docx", data=content("v1"), mime=DOCX, blobs=blobs
        )
        await session.commit()
        yield {
            "session": session,
            "blobs": blobs,
            "owner": owner,
            "folder": folder,
            "other": other,
            "node": node,
            "node_id": node.id,
            "folder_id": folder.id,
            "settings": settings,
        }


async def save(world, data: bytes, *, actor="owner", **kwargs):
    """Opslaan zoals O2 dat doet: exact op id, met replace."""
    path, sha, size = source(world["settings"], data)
    kwargs.setdefault("only", world["node_id"])
    kwargs.setdefault("exact", True)
    node = await place_file(
        world["session"],
        world["blobs"],
        world["owner"],
        world["folder"],
        "verslag.docx",
        "replace",
        sha,
        size,
        path,
        actor=world["owner"] if actor == "owner" else actor,
        **kwargs,
    )
    return node, sha, path


async def fresh(world, node_id) -> Node | None:
    """De node zoals hij nu in de database staat."""
    query = select(Node).where(Node.id == node_id).execution_options(populate_existing=True)
    return await world["session"].scalar(query)


async def blob(world, sha: str) -> Blob | None:
    query = select(Blob).where(Blob.sha256 == sha).execution_options(populate_existing=True)
    return await world["session"].scalar(query)


async def versions(world) -> int:
    session = world["session"]
    return await session.scalar(
        select(func.count()).select_from(Version).where(Version.node_id == world["node_id"])
    )


@pytest.mark.anyio
async def test_exact_na_hernoemen_en_verplaatsen(world):
    session, node = world["session"], world["node"]
    node.name = "ander.docx"
    node.parent_id = world["other"].id
    await session.commit()
    saved, sha, path = await save(world, content("v2"))
    assert saved.id == node.id
    got = await fresh(world, node.id)
    assert (got.name, got.parent_id, got.blob_id) == ("ander.docx", world["other"].id, sha)
    assert await versions(world) == 1  # de vorige inhoud bleef als versie
    assert not path.exists()  # verplaatst naar de blobs
    # Er is geen tweede bestand in de oude map gekomen
    count = await session.scalar(
        select(func.count()).select_from(Node).where(Node.parent_id == world["folder_id"])
    )
    assert count == 0


@pytest.mark.anyio
async def test_exact_in_de_prullenbak(world):
    session, node = world["session"], world["node"]
    from datetime import UTC, datetime

    node.deleted_at = datetime.now(UTC)
    await session.commit()
    _, sha, _ = await save(world, content("v2"))
    got = await fresh(world, node.id)
    assert got.blob_id == sha and got.deleted_at is not None  # blijft in de prullenbak


@pytest.mark.anyio
async def test_exact_als_het_weg_is(world):
    session, node = world["session"], world["node"]
    await session.execute(delete(Node).where(Node.id == node.id))
    await session.commit()
    with pytest.raises(HTTPException) as caught:
        await save(world, content("v2"))
    assert caught.value.status_code == 404
    assert caught.value.detail["code"] == "not_found"
    # Niets veranderd: de bron staat er nog, geen nieuwe blob
    leftovers = list((world["settings"].blob_dir / ".tmp").iterdir())
    assert len(leftovers) == 1
    sha = hashlib.sha256(content("v2")).hexdigest()
    assert await blob(world, sha) is None


@pytest.mark.anyio
async def test_zonder_exact_geeft_verplaatst_409(world):
    session, node = world["session"], world["node"]
    node.name = "ander.docx"
    await session.commit()
    with pytest.raises(HTTPException) as caught:
        await save(world, content("v2"), exact=False)
    assert caught.value.status_code == 409 and caught.value.detail["code"] == "moved"


@pytest.mark.anyio
async def test_exact_alleen_met_only_en_replace(world):
    path, sha, size = source(world["settings"], content("v2"))
    for only, conflict in ((None, "replace"), (world["node"].id, "rename")):
        with pytest.raises(ValueError):
            await place_file(
                world["session"],
                world["blobs"],
                world["owner"],
                world["folder"],
                "verslag.docx",
                conflict,
                sha,
                size,
                path,
                only=only,
                exact=True,
            )


@pytest.mark.anyio
async def test_exact_op_een_map_is_404(world):
    with pytest.raises(HTTPException) as caught:
        await save(world, content("v2"), only=world["folder"].id)
    assert caught.value.status_code == 404


@pytest.mark.anyio
async def test_squash_alleen_bij_de_juiste_sha(world):
    session, blobs = world["session"], world["blobs"]
    first, first_sha, _ = await save(world, content("tussenstand 1"))
    assert await versions(world) == 1  # v1 werd een versie (squash niet gegeven)
    # De huidige inhoud is de tussenstand van deze sessie: geen versie, en de verwijzing
    # vervalt; collect_garbage (aan het eind van place_file) ruimt hem meteen op
    path = blobs.path(first_sha)
    assert path.exists()
    _, second_sha, _ = await save(world, content("tussenstand 2"), squash=first_sha)
    assert await versions(world) == 1
    assert await blob(world, first_sha) is None
    assert not path.exists()
    # Een andere sha dan de huidige: gewoon een versie
    _, third_sha, _ = await save(world, content("tussenstand 3"), squash=first_sha)
    assert await versions(world) == 2
    # Dezelfde inhoud als nu: niets
    await save(world, content("tussenstand 3"), squash=third_sha)
    assert await versions(world) == 2
    got = await fresh(world, world["node_id"])
    assert got.blob_id == third_sha
    # De rest blijft: tussenstand 2 is nu een versie, 3 is de inhoud
    await collect_garbage(session, blobs)
    for sha in (second_sha, third_sha):
        assert await blob(world, sha) is not None
        assert blobs.path(sha).exists()


@pytest.mark.anyio
async def test_squash_telt_alleen_het_verschil_voor_het_quotum(world):
    session, owner = world["session"], world["owner"]
    first, first_sha, _ = await save(world, content("a" * 10))
    used = await session.scalar(select(func.sum(Blob.size)))
    # Net genoeg ruimte voor het verschil, niet voor een hele nieuwe inhoud
    bigger = content("a" * 10 + "b" * 400)
    owner.quota_bytes = used + (len(bigger) - first.size) + 5
    await session.commit()
    with pytest.raises(HTTPException) as caught:
        await save(world, bigger)
    assert caught.value.status_code == 413
    # Na de rollback opnieuw laden, zoals de aanroeper dat doet
    for obj in (world["owner"], world["folder"]):
        await session.refresh(obj)
    _, sha, _ = await save(world, bigger, squash=first_sha)
    assert (await fresh(world, world["node_id"])).blob_id == sha


@pytest.mark.anyio
async def test_pdf_state_ask_zonder_actor(world):
    session = world["session"]
    _, by_link, _ = await save(world, content("via een uploadlink"), actor=None)
    assert (await blob(world, by_link)).pdf_state == "ask"
    _, by_user, _ = await save(world, content("van de eigenaar"))
    assert (await blob(world, by_user)).pdf_state is None
    # Een bestaande blob verandert niet
    await save(world, content("van de eigenaar"), actor=None)
    found = await blob(world, by_user)
    assert found.pdf_state is None and found.pdf_version == 0
    # En een nieuw bestand (zonder only) via een link ook
    path, sha, size = source(world["settings"], content("nieuw via link"))
    await place_file(
        session,
        world["blobs"],
        world["owner"],
        world["folder"],
        "nieuw.docx",
        "rename",
        sha,
        size,
        path,
    )
    assert (await blob(world, sha)).pdf_state == "ask"
