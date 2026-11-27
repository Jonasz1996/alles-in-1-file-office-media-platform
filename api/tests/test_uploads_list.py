"""GET /api/v1/tus (onderbroken uploads) en lastmodified als opnamedatum (P3)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.models import Node, Upload
from tests.factories import maker
from tests.test_files import mkdir, root
from tests.test_previews import picture
from tests.test_shares import person, share_with
from tests.test_uploads import TUS, create, send

SUMMER = datetime(2024, 7, 1, 22, 30, tzinfo=UTC)  # in Amsterdam al 2 juli, 00:30


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


async def pending(client, headers):
    r = await client.get("/api/v1/tus", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def node_row(settings, node_id) -> Node:
    async with maker(settings)() as session:
        return await session.get(Node, uuid.UUID(node_id))


@pytest.mark.anyio
async def test_alleen_je_eigen_onafgemaakte_uploads(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    gedeeld = await mkdir(client, jonas, root_id, "Gedeeld")
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    await share_with(client, jonas, gedeeld, user_id=anna_id, permission="write")

    half = (await create(client, jonas, root_id, "half.bin", 10)).headers["location"]
    assert (await send(client, jonas, half, 0, b"12345")).status_code == 204
    leeg = (await create(client, jonas, root_id, "nog-niets.bin", 4)).headers["location"]
    klaar = (await create(client, jonas, root_id, "klaar.txt", 2)).headers["location"]
    assert "platform-node-id" in (await send(client, jonas, klaar, 0, b"ok")).headers
    verlopen = (await create(client, jonas, root_id, "verlopen.bin", 3)).headers["location"]
    van_anna = (await create(client, anna, gedeeld, "anna.bin", 3)).headers["location"]
    async with maker(settings)() as session:
        await session.execute(
            update(Upload)
            .where(Upload.id == uuid.UUID(verlopen.rsplit("/", 1)[1]))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()

    mine = await pending(client, jonas)
    assert [(u["name"], u["offset"], u["size"]) for u in mine] == [
        ("half.bin", 5, 10),
        ("nog-niets.bin", 0, 4),
    ]
    assert mine[0]["id"] == half.rsplit("/", 1)[1]
    assert mine[0]["parent_id"] == root_id
    assert datetime.fromisoformat(mine[0]["expires_at"]) > datetime.now(UTC)
    assert datetime.fromisoformat(mine[0]["created_at"]) <= datetime.now(UTC)

    # Anna ziet alleen de hare (in de map van Jonas); een gast heeft er geen
    theirs = await pending(client, anna)
    assert [(u["name"], u["parent_id"]) for u in theirs] == [("anna.bin", gedeeld)]
    assert van_anna.endswith(theirs[0]["id"])
    guest, _ = await person(client, jonas, "gast@example.nl", role="guest")
    assert await pending(client, guest) == []

    # Weggooien: weg uit de lijst
    assert (await client.delete(leeg, headers={**jonas, **TUS})).status_code == 204
    assert [u["name"] for u in await pending(client, jonas)] == ["half.bin"]
    assert (await client.get("/api/v1/tus")).status_code == 401


@pytest.mark.anyio
async def test_lastmodified_wordt_de_opnamedatum_zonder_exif(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    data = picture("PNG", color="purple")
    r = await create(client, headers, root_id, "p.png", len(data), lastmodified=str(ms(SUMMER)))
    assert r.status_code == 201, r.text
    r = await send(client, headers, r.headers["location"], 0, data)
    node = await node_row(settings, r.headers["platform-node-id"])
    # De kloktijd in Amsterdam (zomertijd, +2): 2 juli 00:30
    assert node.taken_at.replace(tzinfo=None) == datetime(2024, 7, 2, 0, 30)
    assert node.taken_day == 20240702

    # Zonder lastmodified: het moment van uploaden
    data = picture("PNG", color="orange")
    r = await create(client, headers, root_id, "q.png", len(data))
    r = await send(client, headers, r.headers["location"], 0, data)
    node = await node_row(settings, r.headers["platform-node-id"])
    assert node.taken_day is not None and node.taken_at.year >= 2026
