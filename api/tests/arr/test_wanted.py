"""Films en series volgen, en de kalender (fase 11, stap 2d)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_engine
from app.media.providers.base import Candidate, Details, EpisodeDetails, ProviderUnavailable
from app.models import MediaLibrary
from app.routers import arr_wanted
from tests.test_files import mkdir, root
from tests.test_shares import person

pytestmark = pytest.mark.anyio

BASE = "/api/v1/arr"
TODAY = dt.date.today()


class FakeTmdb:
    name = "tmdb"

    async def search(self, kind, title, year, lang):
        prefix = "movie" if kind == "movie" else "tv"
        return [
            Candidate(provider="tmdb", id=f"{prefix}:603", kind=kind, title="The Matrix", year=1999)
        ]

    async def details(self, kind, id, lang):
        if id == "tv:1396":
            return Details(
                provider="tmdb",
                id=id,
                kind="show",
                title="Breaking Bad",
                year=2008,
                seasons=2,
                ids={"tmdb": "1396"},
            )
        return Details(
            provider="tmdb",
            id=id,
            kind="movie",
            title="The Matrix",
            year=1999,
            runtime_min=136,
            ids={"tmdb": "603", "imdb": "tt0133093"},
        )

    async def season(self, show_id, n, lang):
        if n == 3:
            raise ProviderUnavailable("tmdb: 404")
        soon = (TODAY + dt.timedelta(days=3)).isoformat()
        late = (TODAY + dt.timedelta(days=60)).isoformat()
        old = (TODAY - dt.timedelta(days=3)).isoformat()
        return [
            EpisodeDetails(provider="tmdb", show_id=show_id, season=n, episode=1, aired=old),
            EpisodeDetails(
                provider="tmdb", show_id=show_id, season=n, episode=2, aired=soon, title="Nieuw"
            ),
            EpisodeDetails(provider="tmdb", show_id=show_id, season=n, episode=3, aired=late),
        ]


@pytest.fixture
def fake_tmdb(monkeypatch):
    fake = FakeTmdb()

    async def provider(session, settings):
        return fake

    monkeypatch.setattr(arr_wanted, "provider", provider)
    return fake


async def make_library(client, headers, settings, kind: str, name: str) -> str:
    folder = await mkdir(client, headers, (await root(client, headers))["folder"]["id"], name)
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    async with async_sessionmaker(get_engine(settings), expire_on_commit=False)() as session:
        lib = MediaLibrary(
            owner_id=uuid.UUID(me["id"]), root_id=uuid.UUID(folder), kind=kind, name=name
        )
        session.add(lib)
        await session.commit()
        return str(lib.id)


async def test_only_admins(client, admin, fake_tmdb):
    headers, _ = admin
    user, _ = await person(client, headers, "gezin@example.nl")
    for method, path in [
        ("GET", f"{BASE}/wanted"),
        ("GET", f"{BASE}/calendar"),
        ("GET", f"{BASE}/profiles"),
    ]:
        r = await client.request(method, path, headers=user)
        assert r.status_code == 403, path


async def test_lookup_add_change_remove(client, admin, settings, fake_tmdb):
    headers, _ = admin
    films = await make_library(client, headers, settings, "movies", "Films")
    series = await make_library(client, headers, settings, "shows", "Series")

    r = await client.get(f"{BASE}/lookup", params={"kind": "movie", "q": "matrix"}, headers=headers)
    assert r.status_code == 200 and r.json()[0]["tmdb_id"] == 603

    body = {"kind": "movie", "tmdb_id": 603, "library_id": series}
    r = await client.post(f"{BASE}/wanted", json=body, headers=headers)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "arr_library_kind"

    body["library_id"] = films
    r = await client.post(
        f"{BASE}/wanted", json={**body, "profile": "bestaat-niet"}, headers=headers
    )
    assert r.status_code == 422
    r = await client.post(f"{BASE}/wanted", json=body, headers=headers)
    assert r.status_code == 201, r.text
    film = r.json()
    assert film["title"] == "The Matrix" and film["imdb_id"] == "tt0133093"
    assert film["profile"] == "hd-1080p" and film["seasons"] is None
    r = await client.post(f"{BASE}/wanted", json=body, headers=headers)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "arr_wanted_exists"

    show = {"kind": "show", "tmdb_id": 1396, "library_id": series, "seasons": [2, 1, 2]}
    r = await client.post(f"{BASE}/wanted", json=show, headers=headers)
    assert r.status_code == 201 and r.json()["seasons"] == [1, 2]
    show_id = r.json()["id"]

    r = await client.patch(
        f"{BASE}/wanted/{show_id}",
        json={"all_seasons": True, "profile": "uhd-2160p"},
        headers=headers,
    )
    assert r.json()["seasons"] is None and r.json()["profile"] == "uhd-2160p"
    r = await client.get(f"{BASE}/wanted", headers=headers)
    assert [w["title"] for w in r.json()] == ["Breaking Bad", "The Matrix"]

    r = await client.delete(f"{BASE}/wanted/{film['id']}", headers=headers)
    assert r.status_code == 204
    r = await client.delete(f"{BASE}/wanted/{film['id']}", headers=headers)
    assert r.status_code == 404


async def test_calendar(client, admin, settings, fake_tmdb):
    headers, _ = admin
    r = await client.get(f"{BASE}/calendar", headers=headers)
    assert r.status_code == 200 and r.json() == []

    series = await make_library(client, headers, settings, "shows", "Series")
    show = {"kind": "show", "tmdb_id": 1396, "library_id": series}
    assert (await client.post(f"{BASE}/wanted", json=show, headers=headers)).status_code == 201
    r = await client.get(f"{BASE}/calendar", params={"days": 30}, headers=headers)
    assert r.status_code == 200
    entries = r.json()
    # seizoen 2 (het laatste) heeft één aflevering binnen 30 dagen; seizoen 3 bestaat niet
    assert [(e["season"], e["episode"], e["episode_title"]) for e in entries] == [(2, 2, "Nieuw")]


async def test_without_tmdb_key(client, admin, settings):
    headers, _ = admin
    r = await client.get(f"{BASE}/lookup", params={"kind": "show", "q": "x"}, headers=headers)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "arr_tmdb_needed"
    r = await client.get(f"{BASE}/profiles", headers=headers)
    assert {p["key"] for p in r.json()} >= {"hd-1080p", "uhd-2160p"}
