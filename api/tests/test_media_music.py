"""Albums, artiesten en de stap audio_info (Keuze 37, 18; M9): app/media/music.py en de routes
van routers/media_music.py voor albums en artiesten.

- group() puur: compilaties (tag, "Various Artists", veel artiesten), geen albumartiest,
  schijven, hetzelfde album bij twee artiesten, de terugval op de mappen;
- sleutels en sorteren (The/De/Het, sorteertags, AC/DC, "!!!");
- gapless_of (alles nagekeken; delay 0 voor wat naar FLAC gaat);
- regroup via de echte scanner en de routes: sorteren, pagina's, de hoes, toegang (404 voor wie
  de bibliotheek niet ziet, wel na een deling), de prullenbak, refresh met één job;
- de stap audio_info via analysis.run met de nep-worker: ready, skip, failed, busy (Defer).
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app import jobs
from app.media import analysis, music
from app.media.types import Probe, Stream
from app.media.wire import Code, Op
from app.media.worker.args import AudioInfoArgs
from app.models import Blob, Job, MediaAlbum, MediaItem, MediaProbe
from tests.media.fake_worker import OpError
from tests.test_files import mkdir, root
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_scanner import library, scan, upload, world  # noqa: F401
from tests.test_media_sessions import blob_of, content, maker
from tests.test_shares import person, share_with

SEED = 20261030


@pytest.fixture(autouse=True)
def clean():
    music.reset()
    yield
    music.reset()


# --- puur: groeperen ------------------------------------------------------------------------------


def path(*names: str) -> list[tuple[uuid.UUID, str]]:
    """Mappen met een vaste id per pad (dezelfde map in twee nummers is dezelfde id)."""
    out = []
    for i, name in enumerate(names):
        out.append((uuid.uuid5(uuid.NAMESPACE_URL, "/".join(names[: i + 1])), name))
    return out


def track(name: str, *dirs: str, duration_ms: int = 1000, **tags: str) -> music.TrackIn:
    return music.TrackIn(
        node_id=uuid.uuid4(), name=name, dirs=path(*dirs), tags=tags, duration_ms=duration_ms
    )


def by_title(albums: list[music.AlbumOut]) -> dict[str, music.AlbumOut]:
    out = {a.title: a for a in albums}
    assert len(out) == len(albums)
    return out


def test_compilatie_door_de_tag():
    tracks = [
        track(f"{i}.flac", "Hits", album="Hits 2020", artist=a, compilation="1", track=str(i))
        for i, a in enumerate(["Anna", "Bas", "Anna"], 1)
    ]
    [album] = music.group(tracks)
    assert album.compilation is True and album.album_artist == music.VARIOUS
    assert album.key.startswith("v|") and album.tracks == [t.node_id for t in tracks]
    assert album.artist_sort == music.music_sort(music.VARIOUS)


def test_various_artists_over_twee_mappen_is_een_album():
    one = track("1.flac", "Mix 1", album="Zomer", album_artist="Various Artists", artist="A")
    two = track("2.flac", "Mix 2", album="Zomer", album_artist="VA", artist="B", track="2")
    [album] = music.group([one, two])
    assert album.compilation is True and album.album_artist == music.VARIOUS
    assert set(album.folders) == {one.dirs[-1][0], two.dirs[-1][0]}
    assert album.tracks == [two.node_id, one.node_id]  # met nummer eerst


def test_compilatie_door_veel_artiesten_maar_niet_bij_een_hoofdartiest():
    many = [track(f"{a}.flac", "Mix", album="Mix", artist=a) for a in "ABCD"]
    [album] = music.group(many)
    assert album.compilation is True and album.album_artist == music.VARIOUS
    assert album.key.startswith("d|")
    # 3× A en 1× B: A is de artiest (twee artiesten, minder dan 3)
    mostly = [track(f"{i}.flac", "Live", album="Live", artist=a) for i, a in enumerate("AAAB")]
    [album] = music.group(mostly)
    assert album.compilation is False and album.album_artist == "A"
    # A, A, B, C: drie artiesten, maar A heeft de helft
    half = [track(f"{i}.flac", "Duo", album="Duo", artist=a) for i, a in enumerate("AABC")]
    [album] = music.group(half)
    assert album.compilation is False and album.album_artist == "A"


def test_zonder_albumartiest_de_map_en_de_tag():
    tracks = [
        track("1.flac", "Kim", "Eerste", album="Eerste", artist="Kim", track="1", date="2001"),
        track("2.flac", "Kim", "Eerste", album="Eerste", artist="Kim", track="2", date="2001"),
        # dezelfde titel in een andere map: een ander album
        track("1.flac", "Kim", "Kopie", album="Eerste", artist="Kim"),
    ]
    albums = music.group(tracks)
    assert len(albums) == 2
    first = next(a for a in albums if len(a.tracks) == 2)
    assert first.album_artist == "Kim" and first.year == 2001 and first.compilation is False
    assert first.tracks == [tracks[0].node_id, tracks[1].node_id]


def test_schijven_zijn_een_album_op_volgorde():
    tags = {"album": "Dubbel", "album_artist": "Lot"}
    tracks = [
        track("02.flac", "Lot", "Dubbel", "CD2", disc="2/2", track="1", **tags),
        track("01.flac", "Lot", "Dubbel", "CD1", disc="1/2", track="2", **tags),
        track("00.flac", "Lot", "Dubbel", "CD1", disc="1/2", track="1", **tags),
    ]
    [album] = music.group(tracks)
    assert album.tracks == [tracks[2].node_id, tracks[1].node_id, tracks[0].node_id]
    folder = path("Lot", "Dubbel")[-1][0]
    assert album.folders[0] == folder  # de map van het album eerst, dan die van de schijven
    assert set(album.folders[1:]) == {tracks[0].dirs[-1][0], tracks[1].dirs[-1][0]}


def test_schijven_zonder_tags_uit_de_mappen():
    tracks = [
        track("01 - Twee.flac", "Joop", "Plaat (1999)", "Disc 2"),
        track("01 - Een.flac", "Joop", "Plaat (1999)", "Disc 1"),
        track("02 - Ook.flac", "Joop", "Plaat (1999)", "Disc 1"),
    ]
    [album] = music.group(tracks)
    assert (album.title, album.album_artist, album.year) == ("Plaat", "Joop", 1999)
    assert album.tracks == [tracks[1].node_id, tracks[2].node_id, tracks[0].node_id]


def test_een_schijf_achter_de_titel_splitst_het_album_niet():
    """Review golf 4, C3: Artiest/Album/CD1 en CD2 met album="Album (Disc 1)" en "Album
    (Disc 2)" werd twee albums met elk één schijf. Nu valt het schijfnummer achter de titel weg
    als het nummer een schijf heeft (tag of map), en telt het als schijf als die er niet is."""
    tags = {"album_artist": "Lot", "artist": "Lot"}
    tracks = [
        track("01.flac", "Lot", "Album", "CD2", album="Album (Disc 2)", track="1", **tags),
        track("01.flac", "Lot", "Album", "CD1", album="Album (Disc 1)", track="1", **tags),
        track("02.flac", "Lot", "Album", "CD1", album="Album (Disc 1)", track="2", **tags),
    ]
    [album] = music.group(tracks)
    assert album.title == "Album" and album.album_artist == "Lot"
    assert album.tracks == [tracks[1].node_id, tracks[2].node_id, tracks[0].node_id]
    # zonder map per schijf, met een schijftag: ook één album
    flat = [
        track("1.flac", "Kim", "Plaat", album="Plaat [CD 1]", artist="Kim", disc="1"),
        track("2.flac", "Kim", "Plaat", album="Plaat - cd2", artist="Kim", disc="2"),
        track("3.flac", "Kim", "Plaat", album="Plaat (Disc 2 of 2)", artist="Kim", disc="2"),
    ]
    [album] = music.group(flat)
    assert album.title == "Plaat" and len(album.tracks) == 3
    # zonder schijf in tag of map blijft de titel zoals hij is (geen bewijs van een schijf)
    alone = [track("1.flac", "Ed", "Losse", album="Losse CD 2", artist="Ed")]
    [album] = music.group(alone)
    assert album.title == "Losse CD 2"
    # alleen een schijfnummer als titel: dat blijft de titel
    only = [track("1.flac", "Ed", "CD1", album="CD 1", artist="Ed", disc="1")]
    [album] = music.group(only)
    assert album.title == "CD 1"


def test_gastartiesten_maken_geen_compilatie():
    """Review golf 4, C4: een album zonder albumartiest met "A", "A feat. B", "A feat. C",
    "A feat. D" werd "Diverse artiesten" (vier verschillende artiesten). Nu telt de
    hoofdartiest: het album hoort bij A."""
    who = ["A", "A feat. B", "A ft. C", "A featuring D", "A (feat. E)", "A & F", "A, G"]
    tracks = [
        track(f"{i}.flac", "A", "Plaat", album="Plaat", artist=a, track=str(i))
        for i, a in enumerate(who, 1)
    ]
    [album] = music.group(tracks)
    assert (album.title, album.album_artist, album.compilation) == ("Plaat", "A", False)
    # alleen nummers met een gast: de naam zonder gast
    guests = [track(f"{i}.flac", "B", "Duet", album="Duet", artist=f"B feat. {g}")
              for i, g in enumerate("XYZ")]  # fmt: skip
    [album] = music.group(guests)
    assert (album.album_artist, album.compilation) == ("B", False)
    # een duo blijft een duo, en vier echte artiesten blijven een compilatie
    duo = [track(f"{i}.flac", "SG", "Live", album="Live", artist="Simon & Garfunkel")
           for i in range(3)]  # fmt: skip
    [album] = music.group(duo)
    assert (album.album_artist, album.compilation) == ("Simon & Garfunkel", False)
    many = [track(f"{a}.flac", "Mix", album="Mix", artist=f"{a} feat. Q") for a in "KLMN"]
    [album] = music.group(many)
    assert album.compilation is True and album.album_artist == music.VARIOUS


def test_hetzelfde_album_bij_twee_artiesten():
    a = track("1.flac", "X", "Greatest Hits", album="Greatest Hits", album_artist="Queen")
    b = track("1.flac", "Y", "Greatest Hits", album="Greatest Hits", album_artist="ABBA")
    # ook zonder albumartiest, in twee mappen
    c = track("1.flac", "Z", "Best Of", album="Best Of", artist="Kim")
    d = track("1.flac", "W", "Best Of", album="Best Of", artist="Lot")
    albums = music.group([a, b, c, d])
    assert len(albums) == 4
    assert sorted(x.album_artist for x in albums) == ["ABBA", "Kim", "Lot", "Queen"]


def test_een_nummer_zonder_tags_hoort_bij_het_album_in_zijn_map():
    tagged = [
        track(f"0{i} - x.flac", "Ana", "Licht", album="Licht", album_artist="Ana", track=str(i))
        for i in (1, 2)
    ]
    loose = track("03 - Derde.flac", "Ana", "Licht")
    [album] = music.group([*tagged, loose])
    assert album.key.startswith("a|") and album.tracks[-1] == loose.node_id


def test_de_terugval_van_de_mappen_en_los_in_de_bibliotheek():
    t = track("01 - Lied.flac", "Artiest", "Album (1999)")
    [album] = music.group([t])
    assert (album.title, album.album_artist, album.year) == ("Album", "Artiest", 1999)
    # los in de map van de bibliotheek, zonder tags: geen album; met een albumtag wel
    assert music.group([track("los.flac")]) == []
    [tagged] = music.group([track("los.flac", album="Single", artist="Kim")])
    assert tagged.title == "Single" and tagged.album_artist == "Kim"


def test_mbid_jaar_en_duur():
    mbid = "0B6E3B1C-1111-4222-8333-944455556666"
    tracks = [
        track("1.flac", "A", "B", album="B", album_artist="A", date="2001-05-01",
              musicbrainz_albumid=mbid, duration_ms=1500),
        track("2.flac", "A", "B", album="B", album_artist="A", date="2001", duration_ms=-5),
        track("3.flac", "A", "B", album="B", album_artist="A", date="1999", duration_ms=500),
        track("4.flac", "A", "B", album="B", album_artist="A", musicbrainz_albumid="nee"),
    ]  # fmt: skip
    [album] = music.group(tracks)
    assert album.mbid == mbid.lower() and album.year == 2001 and album.duration_ms == 3000


def test_group_faalt_nooit_op_rommel():
    rng = random.Random(SEED)
    alphabet = "aAbB /-_.()[]0123456789!éß\x00​"
    keys = ["album", "artist", "album_artist", "track", "disc", "date", "compilation", "title"]
    for _ in range(300):
        tracks = []
        for _ in range(rng.randint(0, 6)):
            tags = {
                rng.choice(keys): "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
                for _ in range(rng.randint(0, 5))
            }
            dirs = ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))
                    for _ in range(rng.randint(0, 3))]  # fmt: skip
            tracks.append(track(f"{rng.random()}.flac", *dirs, **tags))
        for album in music.group(tracks):
            assert len(album.key) <= music.KEY_MAX and len(album.album_artist) <= 255
            assert album.tracks


# --- puur: sleutels en sorteren -------------------------------------------------------------------


def test_sorteren_en_sleutels():
    assert music.music_sort("The Beatles") == "beatles"
    assert (
        music.music_sort("De Dijk") == "dijk" and music.music_sort("Het Goede Doel") == "goede doel"
    )
    assert music.music_sort("Theo") == "theo" and music.music_sort("The") == "the"
    assert music.sort_of("The Beatles", "Beatles, The") == "beatles the"
    assert music.sort_of("Abba", "") == "abba"
    assert music.artist_key("AC/DC") == "ac dc" and "/" not in music.artist_key("AC/DC")
    weird = music.artist_key("!!!")
    assert weird.startswith("~") and weird == music.artist_key("!!!") != music.artist_key("???")
    long = music.album_key("a", "x" * 300, "y" * 300)
    assert len(long) == music.KEY_MAX and long != music.album_key("a", "x" * 300, "z" * 300)
    assert music.album_key("v", "hits") == "v|hits"


def test_tags_nummers_en_jaren():
    assert music.number("3/12") == 3 and music.number("03") == 3 and music.number("0") is None
    assert music.number("x") is None and music.number("120", 99) is None
    assert music.year_of("2001-05-01") == 2001 and music.year_of("12345") is None
    assert music.year_of("0001") is None
    assert music.truthy("1") and music.truthy(" Yes ") and not music.truthy("0")
    assert music.tag({"TALB": "x", "talb": "  a\x00 b  "}, music.ALBUM) == "a b"
    assert music.tag({"album": 5}, music.ALBUM) is None and music.tag(None, music.ALBUM) is None


# --- puur: gapless_of -----------------------------------------------------------------------------


def test_gapless_of():
    good = {"rate": 44100, "samples": 176400, "delay": 0, "padding": 0, "source": "flac"}
    assert music.gapless_of(good, "flac") == ("ready", good)
    mp3 = {"rate": 44100, "samples": 1000, "delay": 1105, "padding": 300, "source": "lame"}
    assert music.gapless_of(mp3, "mp3")[1]["delay"] == 1105
    # naar FLAC (Opus, Vorbis, ALAC): de decoder knipt de voorloop zelf
    opus = {"rate": 48000, "samples": 1000, "delay": 312, "padding": 0, "source": "opus"}
    assert music.gapless_of(opus, "opus") == ("ready", {**opus, "delay": 0})
    assert music.gapless_of({"skip": "geen"}, "flac") == ("skip", None)
    assert music.gapless_of({"rate": 44100, "samples": 9, "delay": 0}, "flac")[0] == "ready"
    bad = [
        None, [], "x", {"rate": True, "samples": 1, "delay": 0},
        {"rate": 44100, "samples": 0, "delay": 0}, {"rate": 0, "samples": 1, "delay": 0},
        {"rate": 44100, "samples": 1, "delay": -1}, {"rate": 44100, "samples": 1},
        {"rate": 44100, "samples": 1, "delay": 44100 * 11},
        {"rate": 10**7, "samples": 1, "delay": 0}, {"rate": 44100, "samples": 2**41, "delay": 0},
        {"rate": 44100, "samples": 1, "delay": 0, "padding": "1"},
        {"rate": 44100.0, "samples": 1, "delay": 0},
    ]  # fmt: skip
    for found in bad:
        assert music.gapless_of(found, "flac") == ("failed", None), found
    long_source = {**good, "source": "x" * 100}
    assert len(music.gapless_of(long_source, "flac")[1]["source"]) == 16


def test_audio_position_volgt_play():
    probe = Probe(
        streams=[
            Stream(index=0, type="video", codec="mjpeg"),
            Stream(index=1, type="audio", codec="aac", lang="eng"),
            Stream(index=2, type="audio", codec="flac", default=True),
        ]
    )
    assert music.audio_position(probe) == 1
    assert music.audio_position(Probe(streams=[])) == 0


# --- de database en de routes ---------------------------------------------------------------------


async def song(
    client, headers, settings, parent: str, name: str, *, codec: str = "flac",
    duration_ms: int = 200_000, **tags: str,
) -> tuple[str, str]:  # fmt: skip
    """Een nummer met een probe met deze tags: (node, sha)."""
    node = await upload(client, headers, parent, name, content(f"{parent}/{name}", 3000))
    sha = await blob_of(settings, node)
    container = {"flac": "flac", "mp3": "mp3", "opus": "ogg", "aac": "mp4"}[codec]
    probe = Probe(
        container=container,
        duration_ms=duration_ms,
        streams=[Stream(index=0, type="audio", codec=codec, sample_rate=44100, channels=2)],
        tags=tags,
    )
    await analysis.save_probe(maker(settings), sha, probe)
    return node, sha


@pytest.fixture
async def shelf(world, client):  # noqa: F811
    """Een muziekbibliotheek met drie albums en een hoes: alles klaar en gegroepeerd."""
    settings, fake, headers = world
    library_id, folder = await library(client, headers, name="Muziek", kind="music")
    abba = await mkdir(client, headers, folder, "ABBA")
    gold = await mkdir(client, headers, abba, "Gold (1992)")
    beatles = await mkdir(client, headers, folder, "The Beatles")
    road = await mkdir(client, headers, beatles, "Abbey Road")
    mix = await mkdir(client, headers, folder, "Zomer")
    nodes = {}
    tags = {"album": "Gold", "album_artist": "ABBA", "artist": "ABBA", "date": "1992"}
    nodes["queen"], _ = await song(
        client, headers, settings, gold, "01.flac", title="Dancing Queen", track="1/2", **tags
    )
    nodes["waterloo"], _ = await song(
        client, headers, settings, gold, "02.flac", title="Waterloo", track="2/2", **tags
    )
    nodes["cover"] = await upload(client, headers, gold, "Cover.JPG", content("hoes", 500))
    nodes["together"], _ = await song(
        client, headers, settings, road, "01 - Come Together.flac", album="Abbey Road",
        album_artist="The Beatles", artist="The Beatles", date="1969-09-26",
    )  # fmt: skip
    for i, who in enumerate(("Anna", "Bas", "Cor"), 1):
        nodes[who], _ = await song(
            client, headers, settings, mix, f"0{i}.mp3", codec="mp3", album="Zomer", artist=who
        )
    await scan(settings)
    assert await music.regroup(settings, uuid.UUID(library_id)) is True
    return {"settings": settings, "fake": fake, "headers": headers, "library": library_id,
            "folder": folder, "nodes": nodes, "gold": gold}  # fmt: skip


async def albums(client, headers, library_id: str, **params) -> dict:
    r = await client.get(
        f"/api/v1/media/libraries/{library_id}/albums", params=params, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()


async def all_pages(client, headers, library_id: str, sort: str) -> list[str]:
    titles: list[str] = []
    cursor = None
    for _ in range(20):
        params = {"sort": sort, **({"cursor": cursor} if cursor else {})}
        page = await albums(client, headers, library_id, **params)
        titles += [a["title"] for a in page["items"]]
        cursor = page["next"]
        if cursor is None:
            return titles
    raise AssertionError("de pagina's houden niet op")


@pytest.mark.anyio
async def test_albums_sorteren_en_kaarten(shelf, client):
    headers, library_id = shelf["headers"], shelf["library"]
    page = await albums(client, headers, library_id)
    assert [a["title"] for a in page["items"]] == ["Abbey Road", "Gold", "Zomer"]
    assert page["next"] is None
    cards = {a["title"]: a for a in page["items"]}
    assert cards["Gold"]["album_artist"] == "ABBA" and cards["Gold"]["year"] == 1992
    assert cards["Gold"]["track_count"] == 2 and cards["Gold"]["duration_ms"] == 400_000
    assert cards["Zomer"]["compilation"] is True
    assert cards["Zomer"]["album_artist"] == music.VARIOUS
    assert cards["Abbey Road"]["year"] == 1969
    year = await albums(client, headers, library_id, sort="year")
    assert [a["title"] for a in year["items"]] == ["Gold", "Abbey Road", "Zomer"]
    # toegevoegd: het nieuwste nummer van het album eerst
    settings = shelf["settings"]
    base = datetime(2026, 1, 1, tzinfo=UTC)
    async with maker(settings)() as session:
        for key, days in (("together", 3), ("queen", 1), ("Anna", 2)):
            await session.execute(
                update(MediaItem)
                .where(MediaItem.node_id == uuid.UUID(shelf["nodes"][key]))
                .values(added_at=base + timedelta(days=days))
            )
        await session.execute(
            update(MediaItem)
            .where(MediaItem.node_id.in_([uuid.UUID(shelf["nodes"][k])
                                          for k in ("waterloo", "Bas", "Cor")]))
            .values(added_at=base)
        )  # fmt: skip
        await session.commit()
    added = await albums(client, headers, library_id, sort="added")
    assert [a["title"] for a in added["items"]] == ["Abbey Road", "Zomer", "Gold"]


@pytest.mark.anyio
async def test_albums_in_paginas(shelf, client, monkeypatch):
    headers, library_id = shelf["headers"], shelf["library"]
    monkeypatch.setattr(music, "ALBUMS_PAGE", 1)
    assert await all_pages(client, headers, library_id, "title") == ["Abbey Road", "Gold", "Zomer"]
    assert await all_pages(client, headers, library_id, "year") == ["Gold", "Abbey Road", "Zomer"]
    assert sorted(await all_pages(client, headers, library_id, "added")) == [
        "Abbey Road", "Gold", "Zomer"
    ]  # fmt: skip
    r = await client.get(
        f"/api/v1/media/libraries/{library_id}/albums",
        params={"cursor": "kapot!"},
        headers=headers,
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_cursor"
    r = await client.get(
        f"/api/v1/media/libraries/{library_id}/albums", params={"sort": "x"}, headers=headers
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_een_album_met_nummers_en_hoes(shelf, client):
    headers, settings = shelf["headers"], shelf["settings"]
    page = await albums(client, headers, shelf["library"])
    gold = next(a for a in page["items"] if a["title"] == "Gold")
    assert gold["cover"] is None  # de preview van de hoes is er nog niet
    async with maker(settings)() as session:
        album = await session.get(MediaAlbum, uuid.UUID(gold["id"]))
        assert album.art == {
            "cover": {"node": shelf["nodes"]["cover"]},
            "thumb": {"node": shelf["nodes"]["queen"]},
        }
        cover_sha = await blob_of(settings, shelf["nodes"]["cover"])
        await session.execute(
            update(Blob)
            .where(Blob.sha256 == cover_sha)
            .values(preview="ready", preview_meta={"sizes": [256]})
        )
        await session.commit()
    r = await client.get(f"/api/v1/media/albums/{gold['id']}", headers=headers)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert cover_sha in detail["album"]["cover"]
    assert detail["album"]["mbid"] is None and detail["album"]["match_state"] == "local"
    assert [(t["title"], t["track"]) for t in detail["tracks"]] == [
        ("Dancing Queen", 1), ("Waterloo", 2)
    ]  # fmt: skip
    assert all(t["album_id"] == gold["id"] and t["artist"] == "ABBA" for t in detail["tracks"])
    assert detail["album"]["duration_ms"] == 400_000
    r = await client.get(f"/api/v1/media/albums/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"


@pytest.mark.anyio
async def test_een_provider_houdt_zijn_plaatje(shelf, client):
    """Een adres van een provider (M8) blijft bij een nieuwe regroup; de lokale hoes gaat
    voor."""
    settings, library_id = shelf["settings"], uuid.UUID(shelf["library"])
    async with maker(settings)() as session:
        album = await session.scalar(select(MediaAlbum).where(MediaAlbum.title == "Zomer"))
        album.art = {"cover": {"url": "/api/v1/media/art/x"}, "poster": {"url": "/api/x"}}
        await session.commit()
        zomer = album.id
    await music.regroup(settings, library_id)
    async with maker(settings)() as session:
        album = await session.get(MediaAlbum, zomer)
        assert album.art["poster"] == {"url": "/api/x"} and "node" in album.art["thumb"]
    r = await client.get(f"/api/v1/media/albums/{zomer}", headers=shelf["headers"])
    assert r.json()["album"]["cover"] == "/api/v1/media/art/x"


@pytest.mark.anyio
async def test_artiesten(shelf, client):
    headers, library_id = shelf["headers"], shelf["library"]
    r = await client.get(f"/api/v1/media/libraries/{library_id}/artists", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"key": "abba", "name": "ABBA", "album_count": 1},
        {"key": "the beatles", "name": "The Beatles", "album_count": 1},
        {"key": "diverse artiesten", "name": music.VARIOUS, "album_count": 1},
    ]
    r = await client.get("/api/v1/media/artists/the beatles", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "The Beatles"
    assert [a["title"] for a in r.json()["albums"]] == ["Abbey Road"]
    r = await client.get(
        "/api/v1/media/artists/abba", params={"library": library_id}, headers=headers
    )
    assert [a["title"] for a in r.json()["albums"]] == ["Gold"]
    r = await client.get("/api/v1/media/artists/niemand", headers=headers)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"


@pytest.mark.anyio
async def test_de_sorteertag_van_de_artiest(world, client):  # noqa: F811
    settings, _, headers = world
    library_id, folder = await library(client, headers, name="Muziek", kind="music")
    for artist, sort in (("Zeppelin", None), ("Prince", "Aaa Prince"), ("The Who", None)):
        sub = await mkdir(client, headers, folder, artist)
        extra = {"album_artist_sort": sort} if sort else {}
        await song(client, headers, settings, sub, "1.flac", album="X", album_artist=artist,
                   **extra)  # fmt: skip
    await scan(settings)
    await music.regroup(settings, uuid.UUID(library_id))
    r = await client.get(f"/api/v1/media/libraries/{library_id}/artists", headers=headers)
    assert [a["name"] for a in r.json()] == ["Prince", "The Who", "Zeppelin"]


@pytest.mark.anyio
async def test_wie_de_bibliotheek_niet_ziet_krijgt_404(shelf, client):
    headers, library_id = shelf["headers"], shelf["library"]
    other, other_id = await person(client, headers, "bas@example.com")
    page = await albums(client, headers, library_id)
    album_id = page["items"][0]["id"]
    for url in (
        f"/api/v1/media/libraries/{library_id}/albums",
        f"/api/v1/media/libraries/{library_id}/artists",
        f"/api/v1/media/albums/{album_id}",
        "/api/v1/media/artists/abba",
        f"/api/v1/media/artists/abba?library={library_id}",
    ):
        r = await client.get(url, headers=other)
        assert r.status_code == 404, (url, r.text)
        assert r.json()["detail"]["code"] == "not_found"
    await share_with(client, headers, shelf["folder"], user_id=other_id, permission="read")
    assert len((await albums(client, other, library_id))["items"]) == 3
    r = await client.get(f"/api/v1/media/albums/{album_id}", headers=other)
    assert r.status_code == 200, r.text
    r = await client.get("/api/v1/media/artists/abba", headers=other)
    assert r.status_code == 200, r.text
    # zonder login 401
    r = await client.get(f"/api/v1/media/libraries/{library_id}/albums")
    assert r.status_code == 401


@pytest.mark.anyio
async def test_de_prullenbak(shelf, client):
    headers, settings = shelf["headers"], shelf["settings"]
    library_id, nodes = shelf["library"], shelf["nodes"]
    gold = next(a for a in (await albums(client, headers, library_id))["items"]
                if a["title"] == "Gold")  # fmt: skip
    r = await client.delete(f"/api/v1/files/{nodes['waterloo']}", headers=headers)
    assert r.status_code == 204
    # meteen: het album toont alleen wat je mag lezen
    detail = (await client.get(f"/api/v1/media/albums/{gold['id']}", headers=headers)).json()
    assert [t["title"] for t in detail["tracks"]] == ["Dancing Queen"]
    # na de scan telt de kaart het ook niet meer
    await scan(settings)
    gold = next(a for a in (await albums(client, headers, library_id))["items"]
                if a["title"] == "Gold")  # fmt: skip
    assert gold["track_count"] == 1 and gold["duration_ms"] == 200_000
    # het hele album weg: na de scan geen kaart meer, en na regroup ook geen rij
    r = await client.delete(f"/api/v1/files/{shelf['gold']}", headers=headers)
    assert r.status_code == 204
    await scan(settings)
    titles = [a["title"] for a in (await albums(client, headers, library_id))["items"]]
    assert titles == ["Abbey Road", "Zomer"]
    r = await client.get(f"/api/v1/media/albums/{gold['id']}", headers=headers)
    assert r.status_code == 404
    await music.regroup(settings, uuid.UUID(library_id))
    async with maker(settings)() as session:
        assert await session.get(MediaAlbum, uuid.UUID(gold["id"])) is None
        item = await session.scalar(
            select(MediaItem).where(MediaItem.node_id == uuid.UUID(nodes["queen"]))
        )
        assert item.album_id is None
    # terug uit de prullenbak: hetzelfde album komt terug (een nieuwe id, dezelfde sleutel)
    r = await client.post(f"/api/v1/files/{shelf['gold']}/restore", headers=headers)
    assert r.status_code == 200
    await scan(settings)
    await music.regroup(settings, uuid.UUID(library_id))
    titles = [a["title"] for a in (await albums(client, headers, library_id))["items"]]
    assert titles == ["Abbey Road", "Gold", "Zomer"]


@pytest.mark.anyio
async def test_weg_uit_de_bibliotheek_telt_meteen_ook_voor_wie_via_een_deling_kijkt(shelf, client):
    """Review golf 4, S3: Alice verhuist het album Gold naar een eigen map buiten de
    bibliotheek. Vóór de volgende scan zag Bob (lezen via de deling) Gold nog in de lijst van
    albums en ABBA bij de artiesten, terwijl /files hem al 404 gaf. Nu telt een lijst alleen de
    nummers die nu onder de map van de bibliotheek liggen (de regel van golf 3, S4)."""
    headers, library_id, nodes = shelf["headers"], shelf["library"], shelf["nodes"]
    bob, bob_id = await person(client, headers, "bob@example.com")
    await share_with(client, headers, shelf["folder"], user_id=bob_id, permission="read")
    titles = [a["title"] for a in (await albums(client, bob, library_id))["items"]]
    assert titles == ["Abbey Road", "Gold", "Zomer"]
    private = await mkdir(client, headers, (await root(client, headers))["folder"]["id"], "Privé")
    r = await client.patch(
        f"/api/v1/files/{shelf['gold']}", json={"parent_id": private}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/v1/files/{shelf['gold']}", headers=bob)).status_code == 404
    # één nummer van Abbey Road in de prullenbak: de kaart telt het meteen niet meer
    r = await client.delete(f"/api/v1/files/{nodes['together']}", headers=headers)
    assert r.status_code == 204
    # een nummer van Zomer in de prullenbak: de kaart telt twee nummers
    r = await client.delete(f"/api/v1/files/{nodes['Anna']}", headers=headers)
    assert r.status_code == 204
    for who in (bob, headers):
        for sort in ("title", "year", "added"):
            page = await albums(client, who, library_id, sort=sort)
            cards = {a["title"]: a for a in page["items"]}
            assert set(cards) == {"Zomer"}, (sort, cards)
            assert cards["Zomer"]["track_count"] == 2
            assert cards["Zomer"]["duration_ms"] == 400_000
        r = await client.get(f"/api/v1/media/libraries/{library_id}/artists", headers=who)
        assert r.json() == [{"key": "diverse artiesten", "name": music.VARIOUS, "album_count": 1}]
        for params in ({}, {"library": library_id}):
            r = await client.get("/api/v1/media/artists/abba", params=params, headers=who)
            assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found", params
        r = await client.get("/api/v1/media/artists/diverse artiesten", headers=who)
        assert [(a["title"], a["track_count"]) for a in r.json()["albums"]] == [("Zomer", 2)]


@pytest.mark.anyio
async def test_een_pagina_vol_onzichtbare_albums_houdt_de_volgende(shelf, client, monkeypatch):
    """S3: een pagina waarvan alle albums weg zijn, geeft toch een cursor naar de rest (zoals de
    pagina's van M2: de pagina telt de rijen, niet wat zichtbaar blijft)."""
    headers, library_id = shelf["headers"], shelf["library"]
    monkeypatch.setattr(music, "ALBUMS_PAGE", 1)
    r = await client.delete(f"/api/v1/files/{shelf['nodes']['together']}", headers=headers)
    assert r.status_code == 204
    assert await all_pages(client, headers, library_id, "title") == ["Gold", "Zomer"]


@pytest.mark.anyio
async def test_regroup_houdt_de_id_en_doet_niets_zonder_verandering(shelf, client):
    settings, library_id = shelf["settings"], uuid.UUID(shelf["library"])
    async with maker(settings)() as session:
        before = {a.key: a.id for a in (await session.scalars(select(MediaAlbum))).all()}
    assert await music.regroup(settings, library_id) is False
    async with maker(settings)() as session:
        after = {a.key: a.id for a in (await session.scalars(select(MediaAlbum))).all()}
    assert before == after and len(after) == 3
    # een andere bibliotheek of een die er niet is: niets
    assert await music.regroup(settings, uuid.uuid4()) is False


@pytest.mark.anyio
async def test_refresh_zet_een_regroup_klaar(world, client):  # noqa: F811
    """De regroup komt bij het lezen: na een touched, of als de scanner sinds de vorige keer
    klaar was. Een regroup die liep voor de scan van een nieuw nummer klaar was, mist het
    nummer; de volgende vraag zet er dan nog een klaar (scanned_at)."""
    settings, _, headers = world
    library_id, folder = await library(client, headers, name="Muziek", kind="music")
    await song(client, headers, settings, folder, "1.flac", album="Een", artist="Kim")
    await scan(settings)

    async def queued() -> list[Job]:
        async with maker(settings)() as session:
            rows = await session.scalars(
                select(Job).where(Job.type == music.JOB, Job.status.in_(jobs.ACTIVE))
            )
            return list(rows.all())

    async def due() -> None:
        """De regroup die klaarstaat nu laten draaien (anders na DELAY_S)."""
        async with maker(settings)() as session:
            await session.execute(
                update(Job)
                .where(Job.type == music.JOB, Job.status == "queued")
                .values(run_after=jobs.now() - timedelta(seconds=1))
            )
            await session.commit()
        await jobs.run_pending(settings, lane=music.LANE)

    url = f"/api/v1/media/libraries/{library_id}/albums"
    assert (await client.get(url, headers=headers)).json()["items"] == []
    assert len(await queued()) == 1
    # nog een keer, en de artiesten: niets veranderd, geen nieuwe job
    await client.get(url, headers=headers)
    await client.get(f"/api/v1/media/libraries/{library_id}/artists", headers=headers)
    [job] = await queued()
    assert job.payload == {"library_id": library_id} and job.key == f"{music.JOB}:{library_id}"
    # de job is nog niet aan de beurt (DELAY_S)
    await jobs.run_pending(settings, lane=music.LANE)
    assert len(await queued()) == 1
    await due()
    assert await queued() == []
    [card] = (await client.get(url, headers=headers)).json()["items"]
    assert card["title"] == "Een" and card["track_count"] == 1
    assert await queued() == []  # niets veranderd sinds de regroup
    # een nieuw nummer: de vraag na de upload zet een regroup klaar die loopt voor de scan
    await song(client, headers, settings, folder, "2.flac", album="Een", artist="Kim")
    await client.get(url, headers=headers)
    await due()
    await scan(settings)
    [card] = (await client.get(url, headers=headers)).json()["items"]
    assert card["track_count"] == 1  # het item was er nog niet toen de regroup liep
    # ... maar de scan is klaar sinds die vraag: er staat er weer een klaar
    assert len(await queued()) == 1
    await due()
    [card] = (await client.get(url, headers=headers)).json()["items"]
    assert card["track_count"] == 2


@pytest.mark.anyio
async def test_na_een_scan_staat_er_een_regroup_klaar(world, client):  # noqa: F811
    """De haak na een scan (integratie; m9.md, afwijking 8): na de scan van een
    muziekbibliotheek staat er een regroup klaar zonder dat iemand iets vroeg, zodat de eerste
    vraag na de scan de nieuwe indeling al ziet. Een bibliotheek met films krijgt er geen."""
    settings, _, headers = world
    library_id, folder = await library(client, headers, name="Muziek", kind="music")
    films_id, _ = await library(client, headers, name="Films", kind="movies")
    await song(client, headers, settings, folder, "1.flac", album="Een", artist="Kim")
    await scan(settings)
    async with maker(settings)() as session:
        rows = (
            await session.scalars(
                select(Job).where(Job.type == music.JOB, Job.status.in_(jobs.ACTIVE))
            )
        ).all()
        assert [r.payload for r in rows] == [{"library_id": library_id}], rows
        await session.execute(
            update(Job)
            .where(Job.type == music.JOB, Job.status == "queued")
            .values(run_after=jobs.now() - timedelta(seconds=1))
        )
        await session.commit()
    await jobs.run_pending(settings, lane=music.LANE)
    url = f"/api/v1/media/libraries/{library_id}/albums"
    [card] = (await client.get(url, headers=headers)).json()["items"]
    assert card["title"] == "Een" and card["track_count"] == 1
    assert films_id != library_id


@pytest.mark.anyio
async def test_een_bibliotheek_met_films_heeft_geen_albums(world, client):  # noqa: F811
    settings, _, headers = world
    library_id, _ = await library(client, headers)
    r = await client.get(f"/api/v1/media/libraries/{library_id}/albums", headers=headers)
    assert r.status_code == 200 and r.json() == {"items": [], "next": None}
    assert await music.regroup(settings, uuid.UUID(library_id)) is False


# --- de stap audio_info ---------------------------------------------------------------------------


class Info:
    """Een antwoord van de nep-worker op audio_info."""

    def __init__(self, reply=None, error: Code | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[AudioInfoArgs] = []

    async def __call__(self, job) -> dict:
        assert isinstance(job.args, AudioInfoArgs) and len(job.inputs) == 1
        self.calls.append(job.args)
        if self.error is not None:
            raise OpError(self.error, "nee")
        return self.reply


async def state_of(settings, sha: str) -> tuple[str, dict | None]:
    async with maker(settings)() as session:
        row = await session.get(MediaProbe, sha)
        return row.audio_state, row.audio


@pytest.mark.anyio
async def test_audio_info_via_de_analyse(world, client):  # noqa: F811
    settings, fake, headers = world
    library_id, folder = await library(client, headers, name="Muziek", kind="music")
    node, sha = await song(client, headers, settings, folder, "1.flac", album="A", artist="B")
    await scan(settings)
    reply = {"rate": 44100, "samples": 88200, "delay": 0, "padding": 0, "source": "flac"}
    handler = Info(reply)
    fake.on(Op.AUDIO_INFO, handler)
    payload = {"sha": sha, "library_id": library_id}
    await analysis.run(settings, payload)
    assert await state_of(settings, sha) == ("ready", reply)
    assert handler.calls == [AudioInfoArgs(demuxer="flac", audio_index=0)]
    # klaar: niet nog eens
    await analysis.run(settings, payload)
    assert len(handler.calls) == 1
    # de regroup staat klaar
    async with maker(settings)() as session:
        job = await session.scalar(select(Job).where(Job.type == music.JOB))
        assert job is not None and job.payload == {"library_id": library_id}


@pytest.mark.anyio
async def test_audio_info_skip_failed_en_busy(world, client):  # noqa: F811
    settings, fake, headers = world
    library_id, folder = await library(client, headers, name="Muziek", kind="music")
    _, opus = await song(client, headers, settings, folder, "1.opus", codec="opus", album="A")
    _, broken = await song(client, headers, settings, folder, "2.flac", album="A")
    _, skip = await song(client, headers, settings, folder, "3.flac", album="A")
    await scan(settings)
    fake.on(Op.AUDIO_INFO, Info({"rate": 48000, "samples": 960, "delay": 312, "padding": 0}))
    await analysis.run(settings, {"sha": opus, "library_id": library_id})
    state, data = await state_of(settings, opus)
    assert state == "ready" and data["delay"] == 0 and data["samples"] == 960
    fake.on(Op.AUDIO_INFO, Info(error=Code.UNREADABLE))
    await analysis.run(settings, {"sha": broken, "library_id": library_id})
    assert await state_of(settings, broken) == ("failed", None)
    fake.on(Op.AUDIO_INFO, Info({"skip": "geen gapless"}))
    await analysis.run(settings, {"sha": skip, "library_id": library_id})
    assert await state_of(settings, skip) == ("skip", None)
    # een fout die niet aan het bestand ligt: terug naar none (de volgende keer opnieuw)
    _, other = await song(client, headers, settings, folder, "4.flac", album="A")
    fake.on(Op.AUDIO_INFO, Info(error=Code.FAILED))
    await analysis.run(settings, {"sha": other, "library_id": library_id})
    assert await state_of(settings, other) == ("none", None)
    # druk: de analyse wacht (Defer), de staat blijft none
    fake.on(Op.AUDIO_INFO, Info({"rate": 1, "samples": 1, "delay": 0}))
    fake.fault("busy", op=Op.AUDIO_INFO)
    with pytest.raises(jobs.Defer):
        await analysis.run(settings, {"sha": other, "library_id": library_id})
    assert await state_of(settings, other) == ("none", None)


@pytest.mark.anyio
async def test_audio_info_niet_voor_video_of_films(world, client):  # noqa: F811
    settings, fake, headers = world
    handler = Info({"rate": 44100, "samples": 1, "delay": 0})
    fake.on(Op.AUDIO_INFO, handler)
    films_id, films = await library(client, headers)
    _, sha = await song(client, headers, settings, films, "geluid.flac", album="A")
    await analysis.run(settings, {"sha": sha, "library_id": films_id})
    # zonder bibliotheek (play buiten een bibliotheek) wel
    await analysis.run(settings, {"sha": sha})
    assert len(handler.calls) == 1
    video = Probe(
        container="matroska",
        streams=[Stream(index=0, type="video", codec="h264"), Stream(index=1, type="audio")],
    )
    assert music._audio_applies(video, "music") is False
    assert music._audio_applies(Probe(streams=[]), "music") is False
