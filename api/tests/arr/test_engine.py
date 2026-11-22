"""De keten van mediabeheer (fase 11, stap 3): zoeken, kiezen, downloaden, vastgelopen
vervangen en importeren, tegen een nep-Prowlarr, nep-Torznab en nep-qBittorrent."""

from __future__ import annotations

import json
import time
import uuid
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.arr import engine
from app.arr import settings as arr_settings
from app.db import get_engine
from app.models import ArrGrab, ArrWanted
from tests.arr.test_qbit import FakeQBit
from tests.arr.test_wanted import make_library

pytestmark = pytest.mark.anyio

KEY = "0123456789abcdef0123456789abcdef"
CAPS = b"""<caps><searching>
<search available="yes" supportedParams="q"/>
<tv-search available="yes" supportedParams="q,season,ep"/>
<movie-search available="yes" supportedParams="q,imdbid"/>
</searching></caps>"""


def rss(items: list[tuple[str, int, int]]) -> bytes:
    """items: (titel, grootte in GB*10, seeders)."""
    body = "".join(
        f"""<item><title>{t}</title><guid>{t}</guid>
<link>magnet:?xt=urn:btih:{uuid.uuid5(uuid.NAMESPACE_URL, t).hex}{"0" * 8}&amp;dn=x</link>
<torznab:attr name="size" value="{size * 100_000_000}"/>
<torznab:attr name="seeders" value="{seeders}"/></item>"""
        for t, size, seeders in items
    )
    return (
        '<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>'
        + body
        + "</channel></rss>"
    ).encode()


class World:
    def __init__(self) -> None:
        self.qbit = FakeQBit(password="pw")
        self.movie_results = rss(
            [
                ("Inception.2010.1080p.WEB-DL.x264-GOED", 40, 50),
                ("Inception.2010.720p.HDTV.x264-MINDER", 20, 80),
                ("Inception.2010.HDCAM.x264-SLECHT", 10, 500),
                ("Andere.Film.2010.1080p.WEB-DL.x264-X", 40, 900),
            ]
        )
        self.show_results = rss(
            [
                ("Breaking.Bad.S01.1080p.WEB-DL.x264-PAK", 200, 30),
                ("Breaking.Bad.S01E01.1080p.WEB-DL.x264-EEN", 20, 30),
            ]
        )
        self.searches: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "qbittorrent":
            return self.qbit(request)
        if request.url.path == "/api/v1/indexer":
            site = {"id": 1, "name": "Site", "enable": True, "protocol": "torrent"}
            return httpx.Response(200, text=json.dumps([site]))
        if request.url.path == "/1/api":
            params = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
            if params["t"] == "caps":
                return httpx.Response(200, content=CAPS)
            self.searches.append(params)
            if params["t"] == "tvsearch":
                return httpx.Response(200, content=self.show_results)
            return httpx.Response(200, content=self.movie_results)
        return httpx.Response(404)


@pytest.fixture
async def world(client, admin, settings, monkeypatch):
    w = World()
    monkeypatch.setattr(
        arr_settings, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(w))
    )
    imports: list[tuple[str, str]] = []

    async def start_import(source, library):
        imports.append((source, str(library.id)))

    monkeypatch.setattr(engine, "start_import", start_import)

    async def tmdb_episodes(session, settings, wanted):
        return {1: [1, 2]}

    monkeypatch.setattr(engine, "tmdb_episodes", tmdb_episodes)
    w.imports = imports
    async with maker(settings)() as session:
        await arr_settings.store_prowlarr(
            session, settings, arr_settings.ProwlarrConf("http://prowlarr:9696", KEY)
        )
        await arr_settings.store_qbit(
            session,
            settings,
            arr_settings.QBitConf("http://qbittorrent:8080", "admin", "pw"),
        )
    return w


def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def follow(settings, library_id: str, **fields) -> uuid.UUID:
    async with maker(settings)() as session:
        row = ArrWanted(
            library_id=uuid.UUID(library_id), profile="hd-1080p", monitored=True, **fields
        )
        session.add(row)
        await session.commit()
        return row.id


async def grabs(settings) -> list[ArrGrab]:
    async with maker(settings)() as session:
        return list(await session.scalars(select(ArrGrab).order_by(ArrGrab.created_at)))


def finish(world: World, grab: ArrGrab, settings, name: str, files: dict[str, bytes]) -> None:
    """qBittorrent is klaar: de bestanden staan in de downloadmap."""
    folder = settings.data_dir / "import" / "torrents" / name
    for rel, data in files.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(data)
    world.qbit.torrents.append(
        {
            "hash": uuid.uuid4().hex + "0" * 8,
            "name": name,
            "state": "stalledUP",
            "progress": 1,
            "tags": engine.tag_of(grab.id),
            "category": "platform",
            "content_path": f"/downloads/{name}",
        }
    )


async def test_movie_chain(client, admin, settings, world):
    headers, _ = admin
    lib = await make_library(client, headers, settings, "movies", "Films")
    await follow(settings, lib, kind="movie", tmdb_id=27205, imdb_id="tt1375666",
                 title="Inception", year=2010, runtime_min=148)  # fmt: skip

    await engine.tick(settings)
    [grab] = await grabs(settings)
    assert grab.release == "Inception.2010.1080p.WEB-DL.x264-GOED" and grab.state == "downloading"
    assert world.qbit.added[0]["tags"] == engine.tag_of(grab.id)
    assert world.searches[0]["imdbid"] == "1375666"

    # Nog een ronde: niet opnieuw zoeken, er loopt een download
    await engine.tick(settings)
    assert len(world.searches) == 1

    name = "Inception.2010.1080p.WEB-DL.x264-GOED"
    finish(world, grab, settings, name, {
        "Inception.2010.1080p.mkv": b"film" * 1000,
        "Sample/inception-sample.mkv": b"s",
        "Subs/Inception.nl.srt": b"1\n",
        "info.nfo": b"x",
    })  # fmt: skip
    await engine.tick(settings)
    [grab] = await grabs(settings)
    assert grab.state == "imported", grab.reason
    assert world.imports == [(f"arr/{grab.id}", lib)]
    staged = settings.data_dir / "import" / "arr" / str(grab.id) / "Inception (2010)"
    assert sorted(p.name for p in staged.iterdir()) == [
        "Inception (2010).mkv",
        "Inception (2010).nl.srt",
    ]
    assert (staged / "Inception (2010).mkv").stat().st_nlink == 1  # de torrent is weg
    assert world.qbit.torrents == []


async def test_stalled_is_replaced(client, admin, settings, world):
    headers, _ = admin
    lib = await make_library(client, headers, settings, "movies", "Films")
    await follow(settings, lib, kind="movie", tmdb_id=27205, imdb_id="tt1375666",
                 title="Inception", year=2010, runtime_min=148)  # fmt: skip
    await engine.tick(settings)
    [first] = await grabs(settings)
    world.qbit.torrents.append(
        {
            "hash": "c" * 40,
            "name": "x",
            "state": "stalledDL",
            "progress": 0.1,
            "tags": engine.tag_of(first.id),
            "category": "platform",
            "added_on": int(time.time()) - 7 * 3600,
            "last_activity": int(time.time()) - 7 * 3600,
        }
    )
    await engine.tick(settings)
    first_after, second = await grabs(settings)
    assert first_after.state == "failed" and "vastgelopen" in first_after.reason
    assert second.release == "Inception.2010.720p.HDTV.x264-MINDER"  # de volgende goede
    assert world.qbit.torrents == []


async def test_no_video_fails(client, admin, settings, world):
    headers, _ = admin
    lib = await make_library(client, headers, settings, "movies", "Films")
    await follow(settings, lib, kind="movie", tmdb_id=27205, imdb_id="tt1375666",
                 title="Inception", year=2010, runtime_min=148)  # fmt: skip
    await engine.tick(settings)
    [grab] = await grabs(settings)
    finish(world, grab, settings, "Inception.rar", {"inception.rar": b"rar"})
    await engine.tick(settings)
    failed, retry = await grabs(settings)
    assert failed.state == "failed" and "geen video" in failed.reason
    assert retry.state == "downloading" and retry.guid != failed.guid


async def test_show_season_pack(client, admin, settings, world):
    headers, _ = admin
    lib = await make_library(client, headers, settings, "shows", "Series")
    await follow(settings, lib, kind="show", tmdb_id=1396, title="Breaking Bad", year=2008)
    await engine.tick(settings)
    [grab] = await grabs(settings)
    assert grab.release.startswith("Breaking.Bad.S01.") and grab.season == 1
    assert world.searches[0]["season"] == "1"

    name = "Breaking.Bad.S01.1080p.WEB-DL.x264-PAK"
    finish(world, grab, settings, name, {
        "Breaking.Bad.S01E01.1080p.mkv": b"1" * 100,
        "Breaking.Bad.S01E02.1080p.mkv": b"2" * 100,
    })  # fmt: skip
    await engine.tick(settings)
    [grab] = await grabs(settings)
    assert grab.state == "imported"
    season = settings.data_dir / "import" / "arr" / str(grab.id) / "Breaking Bad" / "Season 01"
    assert sorted(p.name for p in season.iterdir()) == [
        "Breaking.Bad.S01E01.1080p.mkv",
        "Breaking.Bad.S01E02.1080p.mkv",
    ]


async def test_fits_and_paths(settings) -> None:
    wanted = ArrWanted(kind="movie", title="De Matrix", original_title="The Matrix", year=1999)
    assert engine.fits(wanted, "The.Matrix.1999.1080p.BluRay.x264")
    assert engine.fits(wanted, "The Matrix 2000 720p WEB-DL")  # een jaar verschil mag
    assert not engine.fits(wanted, "The.Matrix.Reloaded.2003.1080p")
    assert not engine.fits(wanted, "The.Matrix.1990.1080p")
    conf = arr_settings.QBitConf("http://q", "a", "b", save_path="/downloads")
    assert engine.local_path(settings, conf, "/downloads/a/b.mkv") == (
        settings.data_dir / "import" / "torrents" / "a" / "b.mkv"
    )
    assert engine.local_path(settings, conf, "/etc/passwd") is None
    assert engine.local_path(settings, conf, "/downloads/../x") is None


async def test_grabs_and_search_now(client, admin, settings, world):
    headers, _ = admin
    lib = await make_library(client, headers, settings, "movies", "Films")
    wanted = await follow(settings, lib, kind="movie", tmdb_id=27205, imdb_id="tt1375666",
                          title="Inception", year=2010, runtime_min=148)  # fmt: skip
    await engine.tick(settings)
    r = await client.get("/api/v1/arr/grabs", headers=headers)
    assert r.status_code == 200
    [g] = r.json()
    assert (
        g["title"] == "Inception" and g["state"] == "downloading" and g["quality"] == "webdl-1080p"
    )
    r = await client.post(f"/api/v1/arr/wanted/{wanted}/search", headers=headers)
    assert r.status_code == 202
    async with maker(settings)() as session:
        assert (await session.get(ArrWanted, wanted)).searched_at is None
