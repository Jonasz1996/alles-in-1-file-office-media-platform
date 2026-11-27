"""De taak user.delete: alles van de gebruiker weg, de blobs netjes vrijgegeven (ref_count en
de bestanden op schijf), albums, delingen en uploads weg, ontvangers horen share.deleted, en
pas dan de gebruiker zelf."""

import uuid

import pytest
from sqlalchemy import func, select

from app import events, jobs, userdelete
from app.events import Hub
from app.models import Album, Blob, Job, Node, Share, Upload, User, Version
from tests.factories import create_album, maker
from tests.test_files import blob_files, check_refs, mkdir, put, root
from tests.test_public import new_link
from tests.test_shares import person, share_with
from tests.test_uploads import create


@pytest.fixture
def hub(monkeypatch):
    fresh = Hub()
    monkeypatch.setattr(events, "hub", fresh)
    return fresh


async def block_and_delete(client, admin_headers, user_id) -> str:
    r = await client.patch(
        f"/api/v1/users/{user_id}", json={"disabled": True}, headers=admin_headers
    )
    assert r.status_code == 200, r.text
    r = await client.delete(f"/api/v1/users/{user_id}", headers=admin_headers)
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


async def count(settings, model, *where) -> int:
    async with maker(settings)() as session:
        return await session.scalar(select(func.count()).select_from(model).where(*where))


@pytest.mark.anyio
async def test_alles_weg_en_blobs_vrijgegeven(client, admin, settings, hub):
    jonas, _ = admin
    anna_h, anna = await person(client, jonas, "anna@example.nl")
    piet_h, piet = await person(client, jonas, "piet@example.nl")
    anna_root = (await root(client, anna_h))["folder"]["id"]
    work = await mkdir(client, anna_h, anna_root, "Werk")
    deep = await mkdir(client, anna_h, work, "Diep")
    a = (await put(client, anna_h, deep, "a.txt", b"alleen van anna")).json()["id"]
    # Een tweede versie: de eerste inhoud wordt een versie (telt als verwijzing)
    await put(client, anna_h, deep, "a.txt", b"anna versie 2", conflict="replace")
    await put(client, anna_h, work, "gedeeld.txt", b"zelfde inhoud")
    trashed = (await put(client, anna_h, anna_root, "weg.txt", b"in de prullenbak")).json()["id"]
    assert (await client.delete(f"/api/v1/files/{trashed}", headers=anna_h)).status_code == 204
    # Jonas heeft dezelfde inhoud: die blob moet blijven
    jonas_root = (await root(client, jonas))["folder"]["id"]
    await put(client, jonas, jonas_root, "kopie.txt", b"zelfde inhoud")
    share = await share_with(client, anna_h, work, user_id=piet, permission="write")
    await new_link(client, anna_h, work, permission="read")
    # Een lopende upload van Piet in Anna's map (reserveert in haar quotum)
    assert (await create(client, piet_h, work, "bezig.bin", 100)).status_code == 201
    async with maker(settings)() as session:
        owner = await session.get(User, uuid.UUID(anna))
        await create_album(
            session, owner, "Vakantie", items=[await session.get(Node, uuid.UUID(a))]
        )
        await session.commit()
    before = await check_refs(settings)
    assert len(before) == 4  # a v1, a v2, gedeeld (ref_count 2), weg

    with hub.subscribe(uuid.UUID(piet)) as sub:
        job_id = await block_and_delete(client, jonas, anna)
        await jobs.run_pending(settings)
        seen = []
        while not sub.queue.empty():
            event = sub.queue.get_nowait()
            seen.append((event["type"], event["data"]))
    assert ("share.deleted", {"share_id": share["id"], "node_id": work}) in seen

    async with maker(settings)() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        assert job.status == "done", job.error
    assert await count(settings, User, User.id == uuid.UUID(anna)) == 0
    assert await count(settings, Node, Node.owner_id == uuid.UUID(anna)) == 0
    assert await count(settings, Album) == 0
    assert await count(settings, Share) == 0
    assert await count(settings, Upload) == 0
    assert await count(settings, Version) == 0
    after = await check_refs(settings)  # ref_count klopt en de bestanden op schijf ook
    assert list(after.values()) == [1]  # alleen Jonas' kopie
    assert len(blob_files(settings)) == 1
    assert not settings.upload_dir.exists() or not any(settings.upload_dir.iterdir())
    # Jonas merkt er niets van
    r = await client.get(f"/api/v1/files/{jonas_root}/children", headers=jonas)
    assert [c["name"] for c in r.json()["children"]] == ["kopie.txt"]


@pytest.mark.anyio
async def test_opnieuw_na_een_halve_poging(client, admin, settings, monkeypatch):
    """Een poging die halverwege stopt (hier: na de eerste batch), gaat de volgende keer
    verder met wat er nog is."""
    jonas, _ = admin
    anna_h, anna = await person(client, jonas, "anna@example.nl")
    anna_root = (await root(client, anna_h))["folder"]["id"]
    for i in range(5):
        await put(client, anna_h, anna_root, f"{i}.txt", f"inhoud {i}".encode())
    monkeypatch.setattr(userdelete, "BATCH", 2)
    real = userdelete._drop_nodes
    calls = []

    async def once(session, settings_, user):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("stroom uitgevallen")
        return await real(session, settings_, user)

    monkeypatch.setattr(userdelete, "_drop_nodes", once)
    await block_and_delete(client, jonas, anna)
    await jobs.run_pending(settings)
    async with maker(settings)() as session:
        job = await session.scalar(select(Job).where(Job.type == "user.delete"))
        assert job.status == "queued" and "stroom uitgevallen" in job.error
        await session.execute(
            Job.__table__.update().where(Job.id == job.id).values(run_after=jobs.now())
        )
        await session.commit()
    await jobs.run_pending(settings)
    assert await count(settings, User, User.id == uuid.UUID(anna)) == 0
    assert await count(settings, Blob) == 0
    assert blob_files(settings) == set()


@pytest.mark.anyio
async def test_niet_als_de_blokkade_intussen_weg_is(client, admin, settings):
    jonas, _ = admin
    anna_h, anna = await person(client, jonas, "anna@example.nl")
    await put(client, anna_h, (await root(client, anna_h))["folder"]["id"], "a.txt", b"a")
    async with maker(settings)() as session:
        await userdelete.start(session, uuid.UUID(anna))  # zonder blokkade (kan via de API niet)
        await session.commit()
    await jobs.run_pending(settings)
    assert await count(settings, User, User.id == uuid.UUID(anna)) == 1
    assert await count(settings, Node, Node.owner_id == uuid.UUID(anna)) == 2


def test_key_en_user_of():
    user_id = uuid.uuid4()
    assert userdelete.user_of(userdelete.key(user_id)) == user_id
    assert userdelete.user_of("preview:abc") is None
    assert userdelete.user_of("user.delete:geen-uuid") is None
    assert userdelete.user_of(None) is None
