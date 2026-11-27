"""Collectie en kijkwijzer uit TMDB (fase 11, stap 7)."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select

from app.arr import facts
from app.main import app
from app.media.providers import http, tmdb
from app.media.state import MediaState
from app.models import MediaFacts, MediaItem, MediaLibrary
from tests.arr.test_subs import maker
from tests.arr.test_wanted import make_library
from tests.test_files import mkdir, put
from tests.test_shares import person

pytestmark = pytest.mark.anyio


class FakeTmdb:
    def __init__(self) -> None:
        self.asked: list[tuple[str, int]] = []
        self.data = {
            ("movie", 120): tmdb.Facts(119, "The Lord of the Rings Collection", 12),
            ("movie", 121): tmdb.Facts(119, "The Lord of the Rings Collection", 12),
            ("movie", 122): tmdb.Facts(119, "The Lord of the Rings Collection", 16),
            ("movie", 603): tmdb.Facts(None, None, 16),
        }

    async def facts(self, kind: str, number: int) -> tmdb.Facts:
        self.asked.append((kind, number))
        if (kind, number) not in self.data:
            raise http.NotFound("weg")
        return self.data[(kind, number)]


@pytest.fixture
def fake_tmdb(monkeypatch):
    fake = FakeTmdb()

    async def provider(session, settings):
        return fake

    monkeypatch.setattr(facts, "_provider", provider)
    monkeypatch.setattr(facts, "STATE", facts._State())
    monkeypatch.setattr(app.state, "media", MediaState(enabled=True), raising=False)
    return fake


async def films(client, headers, settings, titles: dict[str, str]) -> dict[str, uuid.UUID]:
    """Films met een TMDB-id in één nieuwe bibliotheek: {titel: node_id}."""
    lib = await make_library(client, headers, settings, "movies", "Films")
    async with maker(settings)() as session:
        root_id = str((await session.get(MediaLibrary, uuid.UUID(lib))).root_id)
    out = {}
    for i, (title, number) in enumerate(titles.items()):
        folder = await mkdir(client, headers, root_id, title)
        r = await put(client, headers, folder, f"{title}.mkv", os.urandom(1000))
        node_id = uuid.UUID(r.json()["id"])
        async with maker(settings)() as session:
            session.add(
                MediaItem(
                    node_id=node_id,
                    library_id=uuid.UUID(lib),
                    kind="movie",
                    title=title,
                    sort_title=title.lower(),
                    year=2001 + i,
                    ids={"tmdb": number} if number else {},
                )
            )
            await session.commit()
        out[title] = node_id
    return out


def test_kijkwijzer() -> None:
    nl = {"results": [{"iso_3166_1": "NL", "release_dates": [{"certification": "12"},
                                                             {"certification": "16"}]},
                      {"iso_3166_1": "US", "release_dates": [{"certification": "R"}]}]}  # fmt: skip
    assert tmdb.age_of({"release_dates": nl}, "movie") == 16  # de strengste
    us = {"results": [{"iso_3166_1": "US", "release_dates": [{"certification": "PG-13"}]}]}
    assert tmdb.age_of({"release_dates": us}, "movie") == 12
    tv = {"results": [{"iso_3166_1": "NL", "rating": "AL"}]}
    assert tmdb.age_of({"content_ratings": tv}, "tv") == 0
    assert tmdb.age_of({}, "movie") is None
    data = {"belongs_to_collection": {"id": 10, "name": "Star Wars"}}
    assert tmdb.facts_of(data, "movie") == tmdb.Facts(10, "Star Wars", None)
    assert facts.tmdb_number({"tmdb": "movie:0603"}) == "603"
    assert facts.tmdb_number({"tmdb": "abc"}) is None


async def test_tick_and_collection(client, admin, settings, fake_tmdb):
    headers, _ = admin
    nodes = await films(
        client,
        headers,
        settings,
        {"Fellowship": "120", "Towers": "121", "King": "122", "Matrix": "603", "Weg": "999",
         "Zonder": ""},
    )  # fmt: skip
    assert await facts.tick(settings, force=True) == 5
    assert sorted(fake_tmdb.asked) == [("movie", 120), ("movie", 121), ("movie", 122),
                                       ("movie", 603), ("movie", 999)]  # fmt: skip
    async with maker(settings)() as session:
        rows = {f.tmdb_id: f for f in await session.scalars(select(MediaFacts))}
    assert rows["603"].age == 16 and rows["603"].collection_id is None
    assert rows["999"].age is None  # niet bij TMDB: bewaard, niet elke ronde opnieuw
    # Meteen nog een ronde: niets
    fake_tmdb.asked.clear()
    assert await facts.tick(settings, force=True) == 0 and fake_tmdb.asked == []

    url = f"/api/v1/media/items/{nodes['Towers']}/collection"
    r = await client.get(url, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "The Lord of the Rings Collection"
    assert [c["title"] for c in body["cards"]] == ["Fellowship", "King"]
    r = await client.get(f"/api/v1/media/items/{nodes['Matrix']}/collection", headers=headers)
    assert r.status_code == 200 and r.json() is None
    # Een ander ziet de film niet
    other, _ = await person(client, headers, "gezin@example.nl")
    assert (await client.get(url, headers=other)).status_code == 404
