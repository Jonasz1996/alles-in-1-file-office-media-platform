"""De haken van media in de rest van de app (fase 5, §3.5): één regel elk, en zonder media doen ze
niets wat fase 3 en 4 anders maakt.

- files.notify en notify_many → media.hooks.touched(ids, type), na de commit; een nieuwe versie
  heet daar node.version (het event blijft node.changed);
- touched bij node.deleted en node.version → sessions.close_node per node;
- auth: revoke_other_logins → sessions.close_user(user, keep), revoke_family →
  sessions.close_login(family), na dezelfde commit (niet bij een rollback);
- previews.discard → hooks.on_discard(sha) na convert.on_discard; on_discard sluit eerst de
  sessies op die sha (close_sha);
- usage.media_bytes: de lege haak voor het deel media (0, en StorageParts blijft gelijk);
- routers/admin.py: de meldingen van media; een fout daarin breekt /beheer niet.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import previews, usage
from app.db import get_engine
from app.main import app
from app.media import hooks as media_hooks
from app.media import monitor as media_monitor
from app.media import sessions as media_sessions
from app.media.state import MediaState
from app.office import convert as office_convert
from app.routers import auth
from app.storage import BlobStore
from tests.test_account import family
from tests.test_files import put, root


@pytest.fixture
def touched(monkeypatch) -> list[tuple[list[uuid.UUID], str]]:
    """Wat touched hoorde, via een aangemelde luisteraar (zoals de scanner van M2)."""
    seen: list[tuple[list[uuid.UUID], str]] = []
    monkeypatch.setattr(media_hooks, "_touched", [])
    media_hooks.register_touched(lambda ids, type: seen.append((list(ids), type)))
    return seen


@pytest.fixture
def closed(monkeypatch) -> dict[str, list]:
    """Wat de haken in sessions aanroepen."""
    calls: dict[str, list] = {"node": [], "sha": [], "user": [], "login": []}
    monkeypatch.setattr(media_sessions, "close_node", lambda n: calls["node"].append(n) or 0)
    monkeypatch.setattr(media_sessions, "close_sha", lambda s: calls["sha"].append(s) or 0)
    monkeypatch.setattr(
        media_sessions, "close_user", lambda u, keep=None: calls["user"].append((u, keep)) or 0
    )
    monkeypatch.setattr(media_sessions, "close_login", lambda f: calls["login"].append(f) or 0)
    return calls


async def folder_id(client, headers) -> str:
    return (await root(client, headers))["folder"]["id"]


@pytest.mark.anyio
async def test_notify_meldt_elke_verandering_aan_media(client, admin, touched, closed):
    headers, _ = admin
    parent = await folder_id(client, headers)
    node = (await put(client, headers, parent, "film.mkv", b"een")).json()
    node_id = uuid.UUID(node["id"])
    assert touched[-1] == ([node_id], "node.created")
    assert closed["node"] == []

    # Nieuwe inhoud: een nieuwe versie; media sluit de sessies op die node
    r = await put(client, headers, parent, "film.mkv", b"twee", conflict="replace")
    assert r.status_code == 201, r.text
    assert touched[-1] == ([node_id], "node.version")
    assert closed["node"] == [node_id]

    # Hernoemen sluit niets (de guard kijkt per verzoek naar de gepinde sha)
    r = await client.patch(f"/api/v1/files/{node['id']}", json={"name": "f.mkv"}, headers=headers)
    assert r.status_code == 200, r.text
    assert touched[-1] == ([node_id], "node.changed")
    assert closed["node"] == [node_id]

    # Een oude versie terugzetten is ook nieuwe inhoud
    found = await client.get(f"/api/v1/files/{node['id']}/versions", headers=headers)
    version_id = found.json()[0]["id"]
    r = await client.post(
        f"/api/v1/files/{node['id']}/versions/{version_id}/restore", headers=headers
    )
    assert r.status_code == 200, r.text
    assert touched[-1] == ([node_id], "node.version")
    assert closed["node"] == [node_id, node_id]

    # De prullenbak
    r = await client.delete(f"/api/v1/files/{node['id']}", headers=headers)
    assert r.status_code == 204, r.text
    assert touched[-1] == ([node_id], "node.deleted")
    assert closed["node"] == [node_id, node_id, node_id]


@pytest.mark.anyio
async def test_notify_many_meldt_de_hele_groep(client, admin, touched, closed):
    headers, _ = admin
    parent = await folder_id(client, headers)
    ids = [(await put(client, headers, parent, f"{i}.mp3", b"x")).json()["id"] for i in range(3)]
    touched.clear()
    r = await client.post("/api/v1/batch/trash", json={"ids": ids}, headers=headers)
    assert r.status_code == 200, r.text
    assert [(sorted(map(str, got)), type) for got, type in touched] == [
        (sorted(ids), "node.deleted")
    ]
    assert sorted(map(str, closed["node"])) == sorted(ids)


def test_touched_faalt_nooit(monkeypatch, caplog):
    monkeypatch.setattr(media_hooks, "_touched", [])

    def broken(ids, type):
        raise RuntimeError("stuk")

    def close(node_id):
        raise RuntimeError("ook stuk")

    media_hooks.register_touched(broken)
    monkeypatch.setattr(media_sessions, "close_node", close)
    media_hooks.touched([uuid.uuid4()], "node.deleted")  # geen fout naar de aanroeper
    media_hooks.touched([], "node.deleted")  # niets te doen


@pytest.mark.anyio
async def test_uitloggen_sluit_de_login_na_de_commit(client, admin, closed):
    _, tokens = admin
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 204
    assert closed["login"] == [uuid.UUID(family(tokens))]


@pytest.mark.anyio
async def test_nieuw_wachtwoord_sluit_de_andere_logins(client, admin, closed):
    headers, tokens = admin
    body = {"current_password": "lang-genoeg-123", "new_password": "nieuw-wachtwoord-789"}
    r = await client.post("/api/v1/auth/password", json=body, headers=headers)
    assert r.status_code == 204, r.text
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert closed["user"] == [(uuid.UUID(me["id"]), uuid.UUID(family(tokens)))]


@pytest.mark.anyio
async def test_geen_sluiten_bij_een_rollback(settings, closed):
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    family_id = uuid.uuid4()
    async with maker() as session:
        await auth.revoke_family(session, family_id, datetime.now(UTC))
        await session.rollback()
    assert closed["login"] == []
    async with maker() as session:
        await auth.revoke_family(session, family_id, datetime.now(UTC))
        assert closed["login"] == []  # nog niet: pas na de commit
        await session.commit()
    assert closed["login"] == [family_id]


@pytest.mark.anyio
async def test_discard_roept_media_na_office(settings, closed, monkeypatch):
    order: list[str] = []

    async def office(blobs, sha):
        order.append("office")

    async def cache(sha):
        order.append(f"cache {sha}")

    monkeypatch.setattr(office_convert, "on_discard", office)
    monkeypatch.setattr(media_hooks, "_discard", [])
    media_hooks.register_discard(cache)
    blobs = BlobStore(settings.data_dir / "blobs")
    sha = "b" * 64
    await previews.discard(blobs, sha)
    assert order == ["office", f"cache {sha}"]
    assert closed["sha"] == [sha]


@pytest.mark.anyio
async def test_usage_heeft_een_lege_haak_voor_media(settings, monkeypatch):
    """De haak geeft 0 en StorageParts blijft die van 0.6.0; vult M10 hem, dan telt het deel
    media als bekend en wordt "overig" net zoveel kleiner."""
    assert usage.media_bytes(settings) == 0
    assert set(usage.StorageParts.model_fields) == {
        "blobs",
        "previews",
        "uploads",
        "database",
        "other",
    }
    disk = SimpleNamespace(total=10**9, used=6 * 10**8, free=4 * 10**8)
    monkeypatch.setattr(usage.shutil, "disk_usage", lambda path: disk)
    before = await usage.measure(settings)
    monkeypatch.setattr(usage, "media_bytes", lambda s: 1000)
    after = await usage.measure(settings)
    assert before.parts.other - after.parts.other == 1000
    assert after.parts.model_dump().keys() == before.parts.model_dump().keys()


@pytest.mark.anyio
async def test_beheer_toont_de_meldingen_van_media(client, admin, monkeypatch):
    headers, _ = admin

    async def codes() -> set[str]:
        r = await client.get("/api/v1/admin/status", headers=headers)
        assert r.status_code == 200, r.text
        return {n["code"] for n in r.json()["notices"]}

    # Zonder media: geen enkele melding van media
    assert not {c for c in await codes() if c.startswith("media_")}

    async def some(app_, session):
        return [media_monitor.media_notice("media_worker_down")]

    monkeypatch.setattr(media_monitor, "notices", some)
    assert "media_worker_down" in await codes()

    async def broken(app_, session):
        raise RuntimeError("stuk")

    monkeypatch.setattr(media_monitor, "notices", broken)
    assert "media_worker_down" not in await codes()  # en /beheer werkt gewoon


@pytest.mark.anyio
async def test_de_stub_van_monitor(client, monkeypatch):
    assert set(media_monitor.NOTICES) == {
        "media_worker_down",
        "media_ffmpeg_missing",
        "media_cache_full",
        "media_sandbox_off",
        "media_provider_auth",
        "media_provider_failing",
    }
    notice = media_monitor.media_notice("media_cache_full")
    assert notice.code == "media_cache_full" and notice.text == media_monitor.NOTICES[notice.code]
    monkeypatch.setattr(app.state, "media", MediaState(enabled=True), raising=False)
    assert await media_monitor.notices(app, None) == []
