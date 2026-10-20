"""Delen en gelijktijdigheid: uploads van anderen in een gedeelde map tegen definitief
verwijderen, intrekken en het quotum van de eigenaar."""

import asyncio

import anyio
import pytest

from app.routers import files, uploads
from tests.test_copy import meet_up
from tests.test_files import blob_files, check_refs, mkdir, put, root
from tests.test_shares import person, share_with
from tests.test_uploads import TUS, create, hold, send, upload_file


async def used(client, headers) -> int:
    return (await client.get("/api/v1/auth/me", headers=headers)).json()["used_bytes"]


async def names(client, headers, folder) -> list[str]:
    r = await client.get(f"/api/v1/files/{folder}/children", headers=headers)
    return [c["name"] for c in r.json()["children"]]


@pytest.fixture
async def shared(client, admin):
    """Jonas deelt /Gedeeld (met Sub erin) met Anna om te bewerken."""
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    gedeeld = await mkdir(client, jonas, root_id, "Gedeeld")
    sub = await mkdir(client, jonas, gedeeld, "Sub")
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    share = await share_with(client, jonas, gedeeld, user_id=anna_id, permission="write")
    return {"jonas": jonas, "anna": anna, "root": root_id, "gedeeld": gedeeld, "sub": sub,
            "share": share["id"]}  # fmt: skip


def slow_body(halfway: asyncio.Event, go: asyncio.Event, data=(b"nieuwe ", b"inhoud")):
    async def body():
        yield data[0]
        halfway.set()
        await go.wait()
        yield data[1]

    return body()


@pytest.mark.anyio
async def test_upload_van_een_ander_terwijl_de_map_definitief_weggaat(client, shared, settings):
    s = shared
    jonas, anna = s["jonas"], s["anna"]
    halfway, go = asyncio.Event(), asyncio.Event()
    with anyio.fail_after(5):
        uploading = asyncio.create_task(
            put(client, anna, s["sub"], "x.txt", slow_body(halfway, go))
        )
        await halfway.wait()
        await client.delete(f"/api/v1/files/{s['gedeeld']}", headers=jonas)
        assert (
            await client.delete(f"/api/v1/trash/{s['gedeeld']}", headers=jonas)
        ).status_code == 204
        go.set()
        r = await uploading
    assert r.status_code == 404
    assert await names(client, jonas, s["root"]) == []
    assert await used(client, jonas) == 0
    await check_refs(settings)


@pytest.mark.anyio
async def test_definitief_verwijderen_wacht_op_upload_van_een_ander(
    client, shared, settings, monkeypatch
):
    """Het neerzetten is al begonnen (onder het slot): dan gaat het bestand mee weg."""
    s = shared
    jonas, anna = s["jonas"], s["anna"]
    await put(client, jonas, s["sub"], "oud.txt", b"OUD")
    entered, release = hold(monkeypatch, files, "sibling")  # onder het slot, voor de commit
    with anyio.fail_after(5):
        uploading = asyncio.create_task(put(client, anna, s["sub"], "x.txt", b"OUD"))
        await entered.wait()
        await client.delete(f"/api/v1/files/{s['gedeeld']}", headers=jonas)
        purging = asyncio.create_task(client.delete(f"/api/v1/trash/{s['gedeeld']}", headers=jonas))
        await asyncio.sleep(0.05)
        assert not purging.done()  # wacht op het blob-slot
        release.set()
        r, p = await uploading, await purging
    assert (r.status_code, p.status_code) in [(201, 204), (404, 204)]
    assert await names(client, jonas, s["root"]) == []
    assert await used(client, jonas) == 0
    await check_refs(settings)
    assert blob_files(settings) == set()


@pytest.mark.anyio
async def test_hervatbare_upload_van_een_ander_terwijl_de_map_weggaat(
    client, shared, settings, monkeypatch
):
    s = shared
    jonas, anna = s["jonas"], s["anna"]
    location = (await create(client, anna, s["sub"], "x.bin", 4)).headers["location"]
    entered, release = hold(monkeypatch, uploads, "room_left")  # midden in finish()
    with anyio.fail_after(5):
        finishing = asyncio.create_task(send(client, anna, location, 0, b"abcd"))
        await entered.wait()
        await client.delete(f"/api/v1/files/{s['gedeeld']}", headers=jonas)
        assert (
            await client.delete(f"/api/v1/trash/{s['gedeeld']}", headers=jonas)
        ).status_code == 204
        release.set()
        r = await finishing
    assert r.status_code == 404
    assert not upload_file(settings, location).exists()
    # Niet uitgeweken naar de hoofdmap van Jonas
    assert await names(client, jonas, s["root"]) == []
    assert blob_files(settings) == set()
    await check_refs(settings)


@pytest.mark.anyio
async def test_map_in_de_prullenbak_dan_geen_uitwijken_naar_de_hoofdmap(client, shared, settings):
    s = shared
    jonas, anna = s["jonas"], s["anna"]
    location = (await create(client, anna, s["sub"], "x.txt", 2)).headers["location"]
    # Jonas gooit de map weg terwijl de upload loopt (met een PUT is het hetzelfde)
    await client.delete(f"/api/v1/files/{s['sub']}", headers=jonas)
    r = await send(client, anna, location, 0, b"ok")
    assert r.status_code == 404
    assert await names(client, jonas, s["root"]) == ["Gedeeld"]
    assert await names(client, jonas, s["gedeeld"]) == []
    assert await used(client, jonas) == 0
    # Weg tijdens een PUT
    halfway, go = asyncio.Event(), asyncio.Event()
    with anyio.fail_after(5):
        uploading = asyncio.create_task(
            put(client, anna, s["gedeeld"], "z.txt", slow_body(halfway, go))
        )
        await halfway.wait()
        await client.delete(f"/api/v1/files/{s['gedeeld']}", headers=jonas)
        go.set()
        assert (await uploading).status_code == 404
    assert await names(client, jonas, s["root"]) == []
    assert await used(client, jonas) == 0
    await check_refs(settings)


@pytest.mark.parametrize("how", ["prullenbak", "ingetrokken"])
@pytest.mark.anyio
async def test_weg_tijdens_het_afronden_dan_upload_weg(client, shared, settings, monkeypatch, how):
    """Alle bytes zijn binnen en het afronden loopt al, en dan gaat de map in de prullenbak of
    wordt de deling ingetrokken: de upload gaat weg, er komt niets bij."""
    s = shared
    jonas, anna = s["jonas"], s["anna"]
    location = (await create(client, anna, s["sub"], "x.bin", 4)).headers["location"]
    entered, release = hold(monkeypatch, uploads, "room_left")  # midden in finish()
    with anyio.fail_after(5):
        finishing = asyncio.create_task(send(client, anna, location, 0, b"abcd"))
        await entered.wait()
        if how == "prullenbak":
            await client.delete(f"/api/v1/files/{s['sub']}", headers=jonas)
        else:
            await client.delete(f"/api/v1/shares/{s['share']}", headers=jonas)
        release.set()
        r = await finishing
    assert r.status_code == 404
    assert not upload_file(settings, location).exists()
    if how == "prullenbak":
        await client.post(f"/api/v1/files/{s['sub']}/restore", headers=jonas)
    assert await names(client, jonas, s["sub"]) == []
    assert await names(client, jonas, s["root"]) == ["Gedeeld"]
    assert await used(client, jonas) == 0
    assert blob_files(settings) == set()
    await check_refs(settings)


@pytest.mark.anyio
async def test_twee_anderen_tegelijk_niet_over_het_quotum_van_de_eigenaar(
    client, admin, settings, monkeypatch
):
    jonas, _ = admin
    klein, _ = await person(client, jonas, "klein@example.nl", quota_bytes=20)
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    bert, bert_id = await person(client, jonas, "bert@example.nl")
    kroot = (await root(client, klein))["folder"]["id"]
    folder = await mkdir(client, klein, kroot, "Samen")
    await put(client, klein, kroot, "acht", b"12345678")
    await share_with(client, klein, folder, user_id=anna_id, permission="write")
    await share_with(client, klein, folder, user_id=bert_id, permission="write")

    meet_up(monkeypatch, files)
    # Vooraf past elk (8 + 8); bij het neerzetten wordt het onder het slot van Klein opnieuw
    # gecontroleerd
    r1, r2 = await asyncio.gather(
        put(client, anna, folder, "van anna", b"abcdefgh"),
        put(client, bert, folder, "van bert", b"ABCDEFGH"),
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 413]
    assert await used(client, klein) == 16
    monkeypatch.undo()

    # Reserveren voor hervatbare uploads: ook één tegelijk
    real = uploads.room_left

    async def slow(*args, **kwargs):
        await asyncio.sleep(0.01)  # zodat beide verzoeken tegelijk controleren
        return await real(*args, **kwargs)

    monkeypatch.setattr(uploads, "room_left", slow)
    r1, r2 = await asyncio.gather(
        create(client, anna, folder, "a", 3), create(client, bert, folder, "b", 3)
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 413]
    await check_refs(settings)


@pytest.mark.anyio
async def test_deling_ingetrokken_tijdens_upload(client, shared, settings):
    s = shared
    jonas, anna = s["jonas"], s["anna"]
    halfway, go = asyncio.Event(), asyncio.Event()
    with anyio.fail_after(5):
        uploading = asyncio.create_task(
            put(client, anna, s["gedeeld"], "x.txt", slow_body(halfway, go))
        )
        await halfway.wait()
        assert (
            await client.delete(f"/api/v1/shares/{s['share']}", headers=jonas)
        ).status_code == 204
        go.set()
        r = await uploading
    # Ingetrokken terwijl de bytes binnenkwamen: er komt niets bij Jonas
    assert r.status_code == 404
    assert await names(client, jonas, s["gedeeld"]) == ["Sub"]
    assert await used(client, jonas) == 0
    await check_refs(settings)


@pytest.mark.anyio
async def test_intrekken_geeft_gereserveerde_ruimte_vrij(client, admin, settings):
    jonas, _ = admin
    klein, _ = await person(client, jonas, "klein@example.nl", quota_bytes=10)
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    kroot = (await root(client, klein))["folder"]["id"]
    folder = await mkdir(client, klein, kroot, "Samen")
    share = await share_with(client, klein, folder, user_id=anna_id, permission="write")
    location = (await create(client, anna, folder, "groot.bin", 8)).headers["location"]
    assert (await send(client, anna, location, 0, b"1234")).status_code == 204
    # Gereserveerd voor Anna: Klein heeft nog 2 over
    assert (await create(client, klein, kroot, "eigen", 5)).status_code == 413
    # Alleen nog kijken: de upload van Anna gaat weg, de ruimte komt vrij
    r = await client.patch(
        f"/api/v1/shares/{share['id']}", json={"permission": "read"}, headers=klein
    )
    assert r.status_code == 200
    assert (await create(client, klein, kroot, "eigen", 5)).status_code == 201
    assert (await send(client, anna, location, 4, b"5678")).status_code == 404
    assert (await client.head(location, headers={**anna, **TUS})).status_code == 404
    # Weer schrijven, en dan uit een groep: ook dan
    group = (await client.post("/api/v1/groups", json={"name": "G"}, headers=jonas)).json()["id"]
    await client.put(f"/api/v1/groups/{group}/members", json={"user_ids": [anna_id]}, headers=jonas)
    await client.delete(f"/api/v1/shares/{share['id']}", headers=klein)
    await share_with(client, klein, folder, group_id=group, permission="write")
    location = (await create(client, anna, folder, "twee.bin", 4)).headers["location"]
    assert (await create(client, klein, kroot, "nog", 2)).status_code == 413
    await client.put(f"/api/v1/groups/{group}/members", json={"user_ids": []}, headers=jonas)
    assert (await create(client, klein, kroot, "nog", 2)).status_code == 201
    assert (await client.head(location, headers={**anna, **TUS})).status_code == 404
    await check_refs(settings)
