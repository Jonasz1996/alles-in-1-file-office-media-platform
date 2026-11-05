"""De terugval (bouwontwerp fase 6, Keuze 10 en 12; engine.engine_for): LikeFallback, het zoeken
op naam van 0.7.0, als

- de database geen FTS5 heeft (de migratie maakte dan geen fts_node);
- de index uit staat (PLATFORM_SEARCH_INDEX=uit);
- er een herbouw in stukken loopt (search_state.cursor gezet): per verzoek, en meteen weer
  FtsEngine als de herbouw klaar is.

In alle drie: hetzelfde antwoord als in 0.7.0 (op naam, engine "like", names_only), de
suggesties leeg en geen fout. En LikeFallback bladert met offset.
"""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.certs import CertManager
from app.config import get_settings
from app.db import get_engine
from app.main import app
from app.models import Node
from app.search import engine as engines
from app.security import SecurityConfig, SecurityPolicy, login_limiter
from tests.conftest import ADMIN
from tests.search.migratie import migrate, settings_at
from tests.test_files import put, root

pytestmark = pytest.mark.anyio


async def _vul(client, headers, settings) -> None:
    """Een bestand met het woord in de naam, en een met het woord alleen in de tekst."""
    root_id = (await root(client, headers))["folder"]["id"]
    await put(client, headers, root_id, "Kwartaalcijfers.txt", b"a")
    tekst = (await put(client, headers, root_id, "notities.txt", b"b")).json()["id"]
    async with get_engine(settings).begin() as conn:
        sha = (
            await conn.execute(select(Node.blob_id).where(Node.id == uuid.UUID(tekst)))
        ).scalar_one()
        if (
            await conn.execute(text("SELECT 1 FROM sqlite_master WHERE name = 'blob_text'"))
        ).scalar() is not None:
            await conn.execute(
                text(
                    "INSERT INTO blob_text (blob_sha, body, ocr, meta, truncated, updated_at) "
                    "VALUES (:sha, 'de kwartaalcijfers van maart', '', '', 0, '2026-11-03')"
                ),
                {"sha": sha},
            )


async def _zoek(client, headers, q: str, **params) -> dict:
    r = await client.get("/api/v1/search", params={"q": q, **params}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _als_070(client, headers) -> None:
    """Wat de terugval geeft: alleen op naam, zoals 0.7.0, met de velden van fase 6."""
    page = await _zoek(client, headers, "kwartaalcijfers")
    assert [h["node"]["name"] for h in page["results"]] == ["Kwartaalcijfers.txt"]
    assert page["engine"] == "like" and page["names_only"] is True
    assert page["partial"] is False and page["more"] is False and page["offset"] == 0
    # Stukken van namen ook
    page = await _zoek(client, headers, "aalcij")
    assert [h["node"]["name"] for h in page["results"]] == ["Kwartaalcijfers.txt"]
    r = await client.get("/api/v1/search/suggest", params={"q": "kwar"}, headers=headers)
    assert r.status_code == 200
    assert r.json() == {"names": [], "people": [], "places": [], "tags": [], "albums": []}


async def test_met_de_index_uit(client, admin, settings, monkeypatch):
    headers, _ = admin
    await _vul(client, headers, settings)
    page = await _zoek(client, headers, "kwartaalcijfers")
    assert page["engine"] == "fts"
    assert {h["node"]["name"] for h in page["results"]} == {"Kwartaalcijfers.txt", "notities.txt"}
    monkeypatch.setattr(settings, "search_index", "uit")
    await _als_070(client, headers)


async def test_tijdens_een_herbouw(client, admin, settings):
    headers, _ = admin
    await _vul(client, headers, settings)
    async with get_engine(settings).begin() as conn:
        await conn.execute(text("UPDATE search_state SET cursor = 1 WHERE id = 1"))
    await _als_070(client, headers)
    # De herbouw is klaar: meteen weer de index, met de tekst erbij
    async with get_engine(settings).begin() as conn:
        await conn.execute(text("UPDATE search_state SET cursor = NULL WHERE id = 1"))
    page = await _zoek(client, headers, "kwartaalcijfers")
    assert page["engine"] == "fts" and page["names_only"] is False
    assert {h["node"]["name"] for h in page["results"]} == {"Kwartaalcijfers.txt", "notities.txt"}


@pytest.fixture
async def zonder_fts5(tmp_path):
    """De app op een database die zonder FTS5 gemigreerd is (geen fts_node, geen fts_name)."""
    settings = settings_at(tmp_path, "zonder_fts5")
    await migrate(settings, command.upgrade, "head", fts5=False)
    engines.forget(settings.db_url)
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.certs = CertManager(settings)
    app.state.security = SecurityPolicy(SecurityConfig(require_2fa=False))
    login_limiter.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, settings
    app.dependency_overrides.clear()
    del app.state.certs
    del app.state.security
    engines.forget(settings.db_url)


async def test_zonder_fts5(zonder_fts5):
    client, settings = zonder_fts5
    async with get_engine(settings).connect() as conn:
        info = await engines.read_conn_info(conn)
        assert info.fts is False
        assert (
            await conn.execute(text("SELECT 1 FROM sqlite_master WHERE name = 'fts_node'"))
        ).scalar() is None
    r = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await _vul(client, headers, settings)
    await _als_070(client, headers)
    # Ook met de index aan (de standaard): er is geen index
    assert settings.search_index == "aan"


async def test_likefallback_bladert_met_offset(client, admin, settings, monkeypatch):
    headers, _ = admin
    monkeypatch.setattr(settings, "search_index", "uit")
    root_id = (await root(client, headers))["folder"]["id"]
    for i in range(5):
        await put(client, headers, root_id, f"notitie {i}.txt", b"x%d" % i)
    gezien = []
    for offset in (0, 2, 4):
        page = await _zoek(client, headers, "notitie", limit=2, offset=offset)
        assert page["engine"] == "like" and page["offset"] == offset
        assert page["more"] is (offset < 4)
        gezien += [h["node"]["name"] for h in page["results"]]
    assert gezien == [f"notitie {i}.txt" for i in range(5)]
