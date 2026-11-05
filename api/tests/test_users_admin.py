"""Gebruikersbeheer: PATCH /users/{id} (naam, e-mail, rol, quotum, blokkeren), DELETE
/users/{id} (alleen geblokkeerd, als taak), de regels voor de laatste beheerder (ook als twee
beheerders tegelijk iets doen) en python -m app.cli make-admin."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app import cli, events, userdelete
from app.events import Hub
from app.models import Job, User
from app.routers import events as live
from tests.factories import code_of, create_file, create_user, maker
from tests.test_2fa import login, refresh
from tests.test_events_stream import Stream
from tests.test_files import put, root
from tests.test_shares import person

PASSWORD = "lang-genoeg-123"


@pytest.fixture
def hub(monkeypatch):
    fresh = Hub()
    monkeypatch.setattr(events, "hub", fresh)
    return fresh


async def patch(client, headers, user_id, **body):
    return await client.patch(f"/api/v1/users/{user_id}", json=body, headers=headers)


def me_id(tokens) -> str:
    return tokens["user"]["id"]


@pytest.mark.anyio
async def test_naam_mail_quotum_en_rol_wijzigen(client, admin):
    jonas, _ = admin
    _, anna = await person(client, jonas, "anna@example.nl")
    r = await patch(
        client,
        jonas,
        anna,
        display_name="  Anna de Vries ",
        email="Anna.Vries@Example.nl",
        quota_bytes=1000,
        role="admin",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Anna de Vries"
    assert body["email"] == "anna.vries@example.nl"
    assert body["quota_bytes"] == 1000 and body["role"] == "admin"
    assert body["usage"] == {
        "files_bytes": 0,
        "trash_bytes": 0,
        "versions_bytes": 0,
        "file_count": 0,
    }
    # Weglaten laat het staan; 0 is onbeperkt
    r = await patch(client, jonas, anna, quota_bytes=0)
    assert r.json()["quota_bytes"] == 0 and r.json()["display_name"] == "Anna de Vries"
    # Inloggen met het nieuwe adres
    login_limit = await login(client, {"email": "anna.vries@example.nl", "password": PASSWORD})
    assert login_limit.status_code == 200


@pytest.mark.anyio
async def test_ongeldige_invoer(client, admin):
    jonas, _ = admin
    _, anna = await person(client, jonas, "anna@example.nl")
    assert (await patch(client, jonas, anna, quota_bytes=-1)).status_code == 422
    assert (await patch(client, jonas, anna, role="baas")).status_code == 422
    r = await patch(client, jonas, anna, display_name="   ")
    assert r.status_code == 422 and code_of(r) == "invalid_input"
    r = await patch(client, jonas, str(uuid.uuid4()), display_name="X")
    assert r.status_code == 404 and code_of(r) == "user_not_found"


@pytest.mark.anyio
async def test_email_al_in_gebruik(client, admin):
    jonas, _ = admin
    _, anna = await person(client, jonas, "anna@example.nl")
    await person(client, jonas, "piet@example.nl")
    r = await patch(client, jonas, anna, email="PIET@example.nl")
    assert r.status_code == 409 and code_of(r) == "email_taken"
    # Je eigen adres nog eens is geen conflict
    assert (await patch(client, jonas, anna, email="anna@example.nl")).status_code == 200


@pytest.mark.anyio
async def test_gast_kan_geen_eigen_bestanden_hebben(client, admin):
    jonas, _ = admin
    anna_h, anna = await person(client, jonas, "anna@example.nl")
    await put(client, anna_h, (await root(client, anna_h))["folder"]["id"], "a.txt", b"a")
    r = await patch(client, jonas, anna, role="guest")
    assert r.status_code == 409 and code_of(r) == "guest_has_files"
    _, piet = await person(client, jonas, "piet@example.nl")
    r = await patch(client, jonas, piet, role="guest")
    assert r.status_code == 200 and r.json()["role"] == "guest"


@pytest.mark.anyio
async def test_laatste_beheerder_blijft(client, admin):
    jonas, tokens = admin
    me = me_id(tokens)
    r = await patch(client, jonas, me, role="user")
    assert r.status_code == 409 and code_of(r) == "last_admin"
    r = await patch(client, jonas, me, disabled=True)
    assert r.status_code == 422 and code_of(r) == "own_account"
    r = await client.delete(f"/api/v1/users/{me}", headers=jonas)
    assert r.status_code == 422 and code_of(r) == "own_account"
    # Met een tweede beheerder mag je jezelf wel degraderen
    _, anna = await person(client, jonas, "anna@example.nl")
    assert (await patch(client, jonas, anna, role="admin")).status_code == 200
    assert (await patch(client, jonas, me, role="user")).status_code == 200
    users = await client.get("/api/v1/users", headers=jonas)
    assert users.status_code == 403  # geen beheerder meer


@pytest.mark.anyio
async def test_geblokkeerde_beheerder_telt_niet(client, admin):
    jonas, tokens = admin
    _, anna = await person(client, jonas, "anna@example.nl")
    assert (await patch(client, jonas, anna, role="admin")).status_code == 200
    assert (await patch(client, jonas, anna, disabled=True)).status_code == 200
    # Anna is geblokkeerd: Jonas is de enige actieve beheerder
    r = await patch(client, jonas, me_id(tokens), role="user")
    assert r.status_code == 409 and code_of(r) == "last_admin"


@pytest.mark.anyio
async def test_twee_beheerders_blokkeren_elkaar_tegelijk(client, admin):
    jonas, tokens = admin
    anna_h, anna = await person(client, jonas, "anna@example.nl")
    assert (await patch(client, jonas, anna, role="admin")).status_code == 200
    first, second = await asyncio.gather(
        patch(client, jonas, anna, disabled=True),
        patch(client, anna_h, me_id(tokens), disabled=True),
    )
    statuses = sorted([first.status_code, second.status_code])
    # Eén lukt; de ander is daarna geen actieve beheerder meer of zou de laatste blokkeren
    assert statuses[0] == 200 and statuses[1] in (401, 403, 409)
    r = await client.get("/api/v1/users", headers=jonas if first.status_code == 200 else anna_h)
    active = [u for u in r.json() if u["role"] == "admin" and not u["disabled"]]
    assert len(active) == 1


@pytest.mark.anyio
async def test_twee_beheerders_degraderen_elkaar_tegelijk(client, admin):
    jonas, tokens = admin
    anna_h, anna = await person(client, jonas, "anna@example.nl")
    assert (await patch(client, jonas, anna, role="admin")).status_code == 200
    results = await asyncio.gather(
        patch(client, jonas, anna, role="user"),
        patch(client, anna_h, me_id(tokens), role="user"),
    )
    assert sorted(r.status_code for r in results)[0] == 200
    assert any(r.status_code in (403, 409) for r in results)
    survivor = jonas if results[0].status_code == 200 else anna_h
    users = (await client.get("/api/v1/users", headers=survivor)).json()
    assert [u["role"] for u in users].count("admin") == 1


@pytest.mark.anyio
async def test_blokkeren_logt_overal_uit(client, admin, hub, monkeypatch):
    monkeypatch.setattr(live, "PING", 0.1)
    jonas, _ = admin
    anna_h, anna = await person(client, jonas, "anna@example.nl")
    tokens = (await login(client, {"email": "anna@example.nl", "password": PASSWORD})).json()
    stream = Stream(anna_h)
    await stream.open()
    await stream.until("retry: 5000\n\n")

    r = await patch(client, jonas, anna, disabled=True)
    assert r.status_code == 200 and r.json()["disabled"] is True
    await stream.end(seconds=2)
    assert hub.listeners(uuid.UUID(anna)) == 0
    r = await refresh(client, tokens)
    assert r.status_code in (401, 403)
    r = await client.get("/api/v1/auth/me", headers=anna_h)
    assert r.status_code == 401 and code_of(r) == "account_disabled"

    # Deblokkeren: inloggen kan weer (de oude logins blijven ingetrokken)
    r = await patch(client, jonas, anna, disabled=False)
    assert r.status_code == 200 and r.json()["disabled"] is False
    r = await login(client, {"email": "anna@example.nl", "password": PASSWORD})
    assert r.status_code == 200
    assert (await refresh(client, tokens)).status_code == 401


@pytest.mark.anyio
async def test_verwijderen_alleen_als_geblokkeerd(client, admin, settings):
    jonas, _ = admin
    _, anna = await person(client, jonas, "anna@example.nl")
    r = await client.delete(f"/api/v1/users/{anna}", headers=jonas)
    assert r.status_code == 409 and code_of(r) == "not_disabled"
    assert (await patch(client, jonas, anna, disabled=True)).status_code == 200
    r = await client.delete(f"/api/v1/users/{anna}", headers=jonas)
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    # Nog eens: dezelfde taak
    again = await client.delete(f"/api/v1/users/{anna}", headers=jonas)
    assert again.status_code == 202 and again.json()["job_id"] == job_id
    users = (await client.get("/api/v1/users", headers=jonas)).json()
    assert next(u for u in users if u["id"] == anna)["deleting"] is True
    # Tijdens het verwijderen kan de blokkade niet weg
    r = await patch(client, jonas, anna, disabled=False)
    assert r.status_code == 409 and code_of(r) == "user_being_deleted"
    async with maker(settings)() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        assert job.type == "user.delete" and job.payload == {"user_id": anna}
    r = await client.delete(f"/api/v1/users/{uuid.uuid4()}", headers=jonas)
    assert r.status_code == 404 and code_of(r) == "user_not_found"


@pytest.mark.anyio
async def test_alleen_voor_beheerders(client, admin):
    jonas, tokens = admin
    anna_h, anna = await person(client, jonas, "anna@example.nl")
    r = await patch(client, anna_h, me_id(tokens), display_name="X")
    assert r.status_code == 403 and code_of(r) == "admin_only"
    r = await client.delete(f"/api/v1/users/{anna}", headers=anna_h)
    assert r.status_code == 403


@pytest.mark.anyio
async def test_lijst_met_gebruik(client, admin, settings):
    jonas, _ = admin
    async with maker(settings)() as session:
        anna = await create_user(session, "anna@example.nl", quota_bytes=100)
        await create_file(session, anna, name="a.txt", data=b"12345")
        await create_file(session, anna, name="b.txt", data=b"123", deleted=True)
        await session.commit()
    users = (await client.get("/api/v1/users", headers=jonas)).json()
    found = next(u for u in users if u["email"] == "anna@example.nl")
    assert found["usage"] == {
        "files_bytes": 5,
        "trash_bytes": 3,
        "versions_bytes": 0,
        "file_count": 1,
    }
    assert found["used_bytes"] == 8 and found["quota_bytes"] == 100
    assert found["created_at"] is not None and found["deleting"] is False


@pytest.mark.anyio
async def test_cli_make_admin(settings):
    async with maker(settings)() as session:
        await create_user(session, "anna@example.nl", disabled=True)
        await session.commit()
    assert await cli.make_admin(settings, " Anna@Example.nl ") is True
    assert await cli.make_admin(settings, "niemand@example.nl") is False
    async with maker(settings)() as session:
        anna = await session.scalar(select(User).where(User.email == "anna@example.nl"))
        assert anna.role == "admin" and anna.disabled_at is None


@pytest.mark.anyio
async def test_cli_make_admin_niet_als_het_account_wordt_verwijderd(settings):
    """Zoals PATCH /users/{id} (409 user_being_deleted): de taak zou anders doorgaan met het
    verwijderen van wat dan een actieve beheerder is."""
    async with maker(settings)() as session:
        bas = await create_user(session, "bas@example.nl", disabled=True)
        await session.commit()
        await userdelete.start(session, bas.id)
        await session.commit()
    with pytest.raises(cli.BeingDeleted):
        await cli.make_admin(settings, "bas@example.nl")
    async with maker(settings)() as session:
        bas = await session.get(User, bas.id)
        assert bas.role == "user" and bas.disabled_at is not None
