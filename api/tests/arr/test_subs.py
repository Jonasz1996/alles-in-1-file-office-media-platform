"""Ondertitels van OpenSubtitles (fase 11, stap 4), tegen een nep-API."""

from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.arr import opensubs, subs
from app.arr import settings as arr_settings
from app.db import get_engine
from app.main import app
from app.media.state import MediaState
from app.models import ArrSubtitle, MediaItem, MediaLibrary, MediaProbe, Node
from tests.arr.test_wanted import make_library
from tests.test_files import mkdir, put
from tests.test_shares import person

pytestmark = pytest.mark.anyio

SRT = b"1\n00:00:01,000 --> 00:00:02,000\nHallo\n"


class FakeOS:
    def __init__(self) -> None:
        self.searches: list[dict[str, str]] = []
        self.downloads: list[int] = []
        self.results: dict[str, list[dict]] = {
            "nl": [
                {"id": 1, "lang": "nl", "dl": 10, "hash": False},
                {"id": 2, "lang": "nl", "dl": 5, "hash": True},
                {"id": 3, "lang": "nl", "dl": 999, "machine": True},
            ]
        }
        self.quota = False
        self.logins = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"].startswith("AllesInEen")
        if request.url.host == "dl.opensubtitles.com":
            assert "Api-Key" not in request.headers  # de sleutel gaat niet mee naar de link
            return httpx.Response(200, content=SRT)
        assert request.headers["Api-Key"] == "sleutel"
        path = request.url.path
        if path.endswith("/login"):
            self.logins += 1
            return httpx.Response(
                200,
                text=json.dumps(
                    {
                        "token": "tok",
                        "base_url": "vip-api.opensubtitles.com",
                        "user": {"allowed_downloads": 20},
                    }  # fmt: skip
                ),
            )
        if path.endswith("/subtitles"):
            query = request.url.query.decode()
            params = {k: v[0] for k, v in parse_qs(query).items()}
            assert list(params) == sorted(params)  # de API wil ze gesorteerd
            self.searches.append(params)
            rows = []
            for lang in params["languages"].split(","):
                for r in self.results.get(lang, []):
                    rows.append(
                        {
                            "attributes": {
                                "language": r["lang"],
                                "download_count": r["dl"],
                                "moviehash_match": r.get("hash", False),
                                "machine_translated": r.get("machine", False),
                                "release": f"release-{r['id']}",
                                "files": [{"file_id": r["id"], "file_name": "x.srt"}],
                            }
                        }
                    )
            return httpx.Response(200, text=json.dumps({"data": rows}))
        if path.endswith("/download"):
            if self.quota:
                return httpx.Response(
                    406, text=json.dumps({"reset_time_utc": "2030-01-01T00:00:00Z"})
                )
            body = json.loads(request.content)
            assert request.headers["Authorization"] == "Bearer tok"
            self.downloads.append(body["file_id"])
            return httpx.Response(
                200,
                text=json.dumps({"link": "https://dl.opensubtitles.com/x.srt", "remaining": 19}),
            )
        return httpx.Response(404)


def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


@pytest.fixture
async def fake_os(settings, monkeypatch):
    fake = FakeOS()
    monkeypatch.setattr(
        arr_settings,
        "http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake)),
    )
    monkeypatch.setattr(subs, "STATE", subs._State())
    async with maker(settings)() as session:
        await arr_settings.store_opensubs(
            session,
            settings,
            arr_settings.OpenSubsConf("sleutel", "jonas", "geheim", ("nl", "en")),
        )
    return fake


async def movie(client, headers, settings, *, ids=None, subs_=None, streams=None) -> uuid.UUID:
    lib = await make_library(client, headers, settings, "movies", "Films")
    async with maker(settings)() as session:
        lib_row = await session.get(MediaLibrary, uuid.UUID(lib))
        root_id = str(lib_row.root_id)
    folder = await mkdir(client, headers, root_id, "Inception (2010)")
    r = await put(client, headers, folder, "Inception (2010).mkv", os.urandom(200_000))
    assert r.status_code == 201, r.text
    node_id = uuid.UUID(r.json()["id"])
    async with maker(settings)() as session:
        node = await session.get(Node, node_id)
        session.add(
            MediaItem(
                node_id=node_id,
                library_id=uuid.UUID(lib),
                kind="movie",
                title="Inception",
                sort_title="inception",
                year=2010,
                ids=ids if ids is not None else {"tmdb": "27205", "imdb": "tt1375666"},
                subs=subs_ or [],
            )
        )
        if streams is not None:
            session.add(
                MediaProbe(blob_sha=node.blob_id, version=1, state="ready", streams=streams)
            )
        await session.commit()
    return node_id


async def rows(settings) -> dict[str, ArrSubtitle]:
    async with maker(settings)() as session:
        return {r.lang: r for r in await session.scalars(select(ArrSubtitle))}


async def test_movie_gets_dutch(client, admin, settings, fake_os):
    headers, _ = admin
    node_id = await movie(client, headers, settings)
    await subs.tick(settings, force=True)

    [search] = fake_os.searches
    assert search["imdb_id"] == "1375666" and search["languages"] == "en,nl"
    assert len(search["moviehash"]) == 16
    assert fake_os.downloads == [2]  # de hash-treffer, niet de machinevertaling
    found = await rows(settings)
    assert found["nl"].state == "found" and found["en"].state == "none"
    async with maker(settings)() as session:
        video = await session.get(Node, node_id)
        sub = await session.get(Node, found["nl"].sub_node_id)
        assert sub.parent_id == video.parent_id and sub.name == "Inception (2010).nl.srt"
        assert sub.owner_id == video.owner_id and sub.size == len(SRT)

    # Meteen nog een ronde: niets aan de beurt
    await subs.tick(settings, force=True)
    assert len(fake_os.searches) == 1

    # Na 6 uur: alleen Engels opnieuw
    async with maker(settings)() as session:
        row = await session.get(ArrSubtitle, (node_id, "en"))
        row.tried_at = row.tried_at - timedelta(hours=7)
        await session.commit()
    await subs.tick(settings, force=True)
    assert fake_os.searches[-1]["languages"] == "en"
    assert (await rows(settings))["en"].tries == 2
    assert subs.retry_after(2) == timedelta(days=1)


async def test_existing_and_no_ids(client, admin, settings, fake_os):
    headers, _ = admin
    await movie(
        client,
        headers,
        settings,
        subs_=[{"node": "x", "lang": "nld", "forced": False}],
        streams=[
            {"index": 2, "type": "subtitle", "lang": "eng", "forced": False},
        ],
    )
    await subs.tick(settings, force=True)
    assert fake_os.searches == []


async def test_without_ids_no_search(client, admin, settings, fake_os):
    headers, _ = admin
    await movie(client, headers, settings, ids={})
    await subs.tick(settings, force=True)
    assert fake_os.searches == []
    assert {r.state for r in (await rows(settings)).values()} == {"none"}


async def test_quota_pauses(client, admin, settings, fake_os):
    headers, _ = admin
    await movie(client, headers, settings)
    fake_os.quota = True
    await subs.tick(settings, force=True)
    assert subs.STATE.paused_until is not None and subs.STATE.paused_until.year == 2030
    assert await rows(settings) == {}  # niets onthouden: later opnieuw
    await subs.tick(settings)
    assert len(fake_os.searches) == 1


async def test_admin_routes(client, admin, settings, monkeypatch):
    headers, _ = admin
    fake = FakeOS()
    monkeypatch.setattr(
        arr_settings,
        "http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake)),
    )
    body = {"key": "sleutel", "username": "jonas", "password": "geheim", "languages": ["nl"]}
    r = await client.put("/api/v1/admin/arr/opensubtitles", json=body, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"remaining": 20}
    view = (await client.get("/api/v1/admin/arr", headers=headers)).json()["opensubtitles"]
    assert view == {"key_set": True, "username": "jonas", "password_set": True,
                    "languages": ["nl"]}  # fmt: skip
    assert "geheim" not in json.dumps(view) and "sleutel" not in json.dumps(view)
    # Zonder nieuwe sleutel en wachtwoord blijven de oude
    r = await client.put(
        "/api/v1/admin/arr/opensubtitles",
        json={"username": "jonas", "languages": ["nl", "en"]},
        headers=headers,
    )
    assert r.status_code == 200
    bad = {"key": "x", "languages": ["Nederlands"]}
    r = await client.put("/api/v1/admin/arr/opensubtitles", json=bad, headers=headers)
    assert r.status_code == 422
    r = await client.post("/api/v1/admin/arr/opensubtitles/test", headers=headers)
    assert r.status_code == 200
    r = await client.delete("/api/v1/admin/arr/opensubtitles", headers=headers)
    assert r.status_code == 204
    r = await client.post("/api/v1/admin/arr/opensubtitles/test", headers=headers)
    assert r.status_code == 404


def test_parsing_and_hash(tmp_path) -> None:
    q = opensubs.search_params(["NL", "en"], show_imdb="tt0903747", season=1, episode=2)
    assert q == "episode_number=2&languages=en%2Cnl&parent_imdb_id=903747&season_number=1"
    assert opensubs.search_params(["nl"], tmdb=603) == "languages=nl&tmdb_id=603"
    small = tmp_path / "klein.mkv"
    small.write_bytes(b"x" * 1000)
    assert opensubs.moviehash(small) is None
    big = tmp_path / "groot.mkv"
    big.write_bytes(b"\x00" * 200_000)
    assert opensubs.moviehash(big) == f"{200_000:016x}"  # alleen de grootte telt bij nullen
    assert subs.sub_name("Film (2010).mkv", "pt-br") == "Film (2010).pt.srt"
    assert not opensubs._plain_host("evil.example.com")
    assert opensubs._plain_host("vip-api.opensubtitles.com")


async def test_sub_offset(client, admin, settings, fake_os, monkeypatch):
    headers, _ = admin
    monkeypatch.setattr(app.state, "media", MediaState(enabled=True), raising=False)
    node_id = await movie(client, headers, settings)
    url = f"/api/v1/media/sub-offset/{node_id}"
    assert (await client.get(url, headers=headers)).json() == []
    body = {"sub_id": "x0", "label": "Nederlands", "offset_ms": -1500}
    assert (await client.put(url, json=body, headers=headers)).status_code == 204
    body["offset_ms"] = 2500
    assert (await client.put(url, json=body, headers=headers)).status_code == 204
    assert (await client.get(url, headers=headers)).json() == [body]
    too_far = {**body, "offset_ms": 11 * 60 * 1000}
    assert (await client.put(url, json=too_far, headers=headers)).status_code == 422
    assert (
        await client.put(url, json={**body, "offset_ms": 0}, headers=headers)
    ).status_code == 204
    assert (await client.get(url, headers=headers)).json() == []
    # Een ander ziet de video niet: 404
    other, _ = await person(client, headers, "gezin@example.nl")
    assert (await client.get(url, headers=other)).status_code == 404
