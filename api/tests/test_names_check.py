"""POST /files/{id}/names/check: welke namen al bezet zijn, zonder iets aan te maken (P3)."""

import uuid

import pytest
from sqlalchemy import func, select

from app.models import Upload
from tests.factories import code_of, maker
from tests.test_files import mkdir, new_user, put, root
from tests.test_shares import person, share_with


async def check(client, headers, folder, names, expect=200):
    r = await client.post(
        f"/api/v1/files/{folder}/names/check", json={"names": names}, headers=headers
    )
    assert r.status_code == expect, r.text
    return r.json()


@pytest.mark.anyio
async def test_vindt_botsingen_zonder_hoofdletters_en_negeert_de_prullenbak(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    foto = (await put(client, headers, root_id, "Foto.JPG", b"1")).json()["id"]
    map_id = await mkdir(client, headers, root_id, "Map")
    weg = (await put(client, headers, root_id, "weg.txt", b"2")).json()["id"]
    assert (await client.delete(f"/api/v1/files/{weg}", headers=headers)).status_code == 204

    asked = ["foto.jpg", "MAP", "weg.txt", "nieuw.txt", "foto.jpg"]
    out = await check(client, headers, root_id, asked)
    assert out == {
        "taken": [
            {"name": "foto.jpg", "id": foto, "kind": "file"},
            {"name": "MAP", "id": map_id, "kind": "dir"},
        ]
    }
    assert await check(client, headers, root_id, []) == {"taken": []}


@pytest.mark.anyio
async def test_maakt_niets_aan_en_reserveert_niets(client, admin, settings):
    headers, _ = admin
    small = await new_user(client, headers, "klein@example.nl", quota_bytes=10)
    small_root = (await root(client, small))["folder"]["id"]
    await check(client, small, small_root, [f"f{i}.bin" for i in range(1000)])
    async with maker(settings)() as session:
        assert await session.scalar(select(func.count()).select_from(Upload)) == 0
    assert (await root(client, small))["children"] == []

    r = await client.post(
        f"/api/v1/files/{small_root}/names/check",
        json={"names": [f"f{i}" for i in range(1001)]},
        headers=small,
    )
    assert r.status_code == 422 and code_of(r) == "too_many"


@pytest.mark.anyio
async def test_alleen_waar_je_mag_schrijven(client, admin):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    lezen = await mkdir(client, jonas, root_id, "Lezen")
    schrijven = await mkdir(client, jonas, root_id, "Schrijven")
    await put(client, jonas, schrijven, "a.txt", b"a")
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    await share_with(client, jonas, lezen, user_id=anna_id, permission="read")
    await share_with(client, jonas, schrijven, user_id=anna_id, permission="write")

    r = await client.post(f"/api/v1/files/{lezen}/names/check", json={"names": ["a"]}, headers=anna)
    assert r.status_code == 403 and code_of(r) == "read_only"
    out = await check(client, anna, schrijven, ["A.TXT"])
    assert [t["name"] for t in out["taken"]] == ["A.TXT"]
    # Iets wat je niet kunt zien: 404, zoals overal
    await check(client, anna, root_id, ["a"], expect=404)
    await check(client, anna, str(uuid.uuid4()), ["a"], expect=404)
