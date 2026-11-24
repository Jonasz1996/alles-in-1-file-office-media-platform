"""Replicatie per bibliotheek (app/blobsplit.py): het doel van een inhoud is het hoogste aantal
kopieën over al haar verwijzingen; de standaard staat in blobs/, de rest in blobs-r1/ tot
blobs-r3/. blobs-media/ van fase 11 wordt nog gelezen en leeggehaald."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime

import pytest

from app import blobsplit
from app.models import MediaLibrary, Version
from app.storage import BlobStore
from tests.factories import create_blob, create_file, create_folder, create_user, maker

pytestmark = pytest.mark.anyio

NIETS = {"naar_blobs": 0, "naar_r1": 0, "naar_r2": 0, "naar_r3": 0, "weg": 0}


def counts(**kw: int) -> dict[str, int]:
    return {**NIETS, **kw}


async def test_per_bibliotheek_naar_zijn_map_en_terug(settings):
    """Films x2, Series x3 (de standaard), Muziek x1; dezelfde inhoud in je eigen map telt als
    de standaard; het hoogste aantal wint."""
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        user = await create_user(session, "jonas@example.nl")
        films = await create_folder(session, user, name="Films")
        series = await create_folder(session, user, name="Series")
        music = await create_folder(session, user, name="Muziek")
        own = await create_folder(session, user, name="Eigen video's")
        films_lib = MediaLibrary(
            owner_id=user.id, root_id=films.id, kind="movies", name="Films", copies=2
        )
        session.add(films_lib)
        session.add(MediaLibrary(owner_id=user.id, root_id=series.id, kind="shows", name="Series"))
        session.add(
            MediaLibrary(owner_id=user.id, root_id=music.id, kind="music", name="Muziek", copies=1)
        )
        sub = await create_folder(session, user, films.id, name="Film (2020)")
        film = await create_file(session, user, sub.id, "film.mkv", data=b"film", blobs=blobs)
        both = await create_file(session, user, sub.id, "b.mkv", data=b"ook eigen", blobs=blobs)
        await create_file(session, user, own.id, "b.mkv", data=b"ook eigen", blobs=blobs)
        # Dezelfde aflevering in Films (2) en Series (standaard 3): 3
        shared = await create_file(session, user, sub.id, "s.mkv", data=b"serie", blobs=blobs)
        await create_file(session, user, series.id, "s.mkv", data=b"serie", blobs=blobs)
        # Muziek (1) en Films (2): 2
        mixed = await create_file(session, user, music.id, "m.flac", data=b"mix", blobs=blobs)
        await create_file(session, user, sub.id, "m.flac", data=b"mix", blobs=blobs)
        song = await create_file(session, user, music.id, "lied.flac", data=b"lied", blobs=blobs)
        home = await create_file(
            session, user, own.id, "vakantie.mp4", data=b"vakantie", blobs=blobs
        )
        await session.commit()

        done = await blobsplit.run(session, blobs, default=3)
        assert done == counts(naar_r2=2, naar_r1=1)
        assert blobs.copies_path(film.blob_id, 2).read_bytes() == b"film"
        assert not blobs.main_path(film.blob_id).exists()
        assert blobs.path(film.blob_id) == blobs.copies_path(film.blob_id, 2)
        assert blobs.path(mixed.blob_id) == blobs.copies_path(mixed.blob_id, 2)
        assert blobs.path(song.blob_id) == blobs.copies_path(song.blob_id, 1)
        for node in (both, shared, home):
            assert blobs.path(node.blob_id) == blobs.main_path(node.blob_id)
        # De oude kopie ligt opzij tot ze een dag oud is
        aside = blobs.root / blobsplit.TRASH / film.blob_id
        assert aside.exists()

        # Films naar 1: de film verhuist van blobs-r2/ naar blobs-r1/; mixed (Muziek 1 en Films
        # 1) ook
        films_lib.copies = 1
        await session.commit()
        done = await blobsplit.run(session, blobs, default=3)
        assert done == counts(naar_r1=2)
        assert blobs.path(film.blob_id) == blobs.copies_path(film.blob_id, 1)
        assert not blobs.copies_path(film.blob_id, 2).exists()
        assert (blobs.copies_dir(2) / blobsplit.TRASH / film.blob_id).exists()

        # Dezelfde film ook in je eigen map: terug naar blobs/ (de standaard)
        await create_file(session, user, own.id, "film.mkv", data=b"film")
        await session.commit()
        done = await blobsplit.run(session, blobs, default=3)
        assert done == counts(naar_blobs=1)
        assert blobs.main_path(film.blob_id).read_bytes() == b"film"
        assert not blobs.copies_path(film.blob_id, 1).exists()

        # Een tweede ronde doet niets; .weg ouder dan een dag gaat weg
        old = time.time() - blobsplit.GRACE_S - 10
        os.utime(aside, (old, old))
        done = await blobsplit.run(session, blobs, default=3)
        assert done == counts(weg=1)


async def test_een_andere_standaard(settings):
    """PLATFORM_BLOB_DEFAULT_COPIES=2: blobs/ heeft dan 2 kopieën. Een bibliotheek op 2 of
    zonder keuze blijft in blobs/; een op 3 gaat naar blobs-r3/, ook als de inhoud ook buiten
    de bibliotheek staat (het hoogste wint)."""
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        user = await create_user(session, "jonas@example.nl")
        foto = await create_folder(session, user, name="Foto's")
        films = await create_folder(session, user, name="Films")
        own = await create_folder(session, user, name="Eigen")
        session.add(
            MediaLibrary(owner_id=user.id, root_id=foto.id, kind="videos", name="F", copies=3)
        )
        session.add(
            MediaLibrary(owner_id=user.id, root_id=films.id, kind="movies", name="Films", copies=2)
        )
        clip = await create_file(session, user, foto.id, "a.mp4", data=b"clip", blobs=blobs)
        await create_file(session, user, own.id, "a.mp4", data=b"clip")
        film = await create_file(session, user, films.id, "f.mkv", data=b"film", blobs=blobs)
        await session.commit()

        done = await blobsplit.run(session, blobs, default=2)
        assert done == counts(naar_r3=1)
        assert blobs.path(clip.blob_id) == blobs.copies_path(clip.blob_id, 3)
        assert blobs.path(film.blob_id) == blobs.main_path(film.blob_id)
        assert not blobs.copies_dir(2).exists()


async def test_versies_tellen_mee(settings):
    """Een oudere versie in je eigen map houdt de inhoud op de standaard; alleen een versie in
    de bibliotheek: naar de map van de bibliotheek."""
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        user = await create_user(session, "jonas@example.nl")
        films = await create_folder(session, user, name="Films")
        own = await create_folder(session, user, name="Eigen")
        session.add(
            MediaLibrary(owner_id=user.id, root_id=films.id, kind="movies", name="Films", copies=2)
        )
        film = await create_file(session, user, films.id, "f.mkv", data=b"nieuw", blobs=blobs)
        notes = await create_file(session, user, own.id, "n.txt", data=b"nu", blobs=blobs)
        old = await create_blob(session, b"oud", blobs=blobs)
        when = datetime(2026, 11, 1, tzinfo=UTC)
        session.add(Version(node_id=notes.id, blob_id=old.sha256, size=3, created_at=when))
        session.add(Version(node_id=film.id, blob_id=old.sha256, size=3, created_at=when))
        older = await create_blob(session, b"ouder", blobs=blobs)
        session.add(Version(node_id=film.id, blob_id=older.sha256, size=5, created_at=when))
        await session.commit()

        done = await blobsplit.run(session, blobs, default=3)
        assert done == counts(naar_r2=2)
        assert blobs.path(film.blob_id) == blobs.copies_path(film.blob_id, 2)
        assert blobs.path(older.sha256) == blobs.copies_path(older.sha256, 2)
        assert blobs.path(old.sha256) == blobs.main_path(old.sha256)
        assert await blobsplit.target_of(session, old.sha256, 3) == 3
        assert await blobsplit.target_of(session, older.sha256, 3) == 2
        assert await blobsplit.target_of(session, "0" * 64, 3) == 3  # geen verwijzing


async def test_blobs_media_van_fase_11_verhuist(settings):
    """Wat in blobs-media/ staat (fase 11, BLOB_SPLIT al aan), blijft leesbaar en verhuist naar
    zijn plek: Films op 2 naar blobs-r2/, Series zonder keuze naar blobs/."""
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        user = await create_user(session, "jonas@example.nl")
        films = await create_folder(session, user, name="Films")
        series = await create_folder(session, user, name="Series")
        session.add(
            MediaLibrary(owner_id=user.id, root_id=films.id, kind="movies", name="Films", copies=2)
        )
        session.add(MediaLibrary(owner_id=user.id, root_id=series.id, kind="shows", name="Series"))
        film = await create_file(session, user, films.id, "f.mkv", data=b"film")
        ep = await create_file(session, user, series.id, "e.mkv", data=b"aflevering")
        await session.commit()
        for node, data in ((film, b"film"), (ep, b"aflevering")):
            path = blobs.media_path(node.blob_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        blobs.saw(blobs.media)
        assert blobs.path(film.blob_id) == blobs.media_path(film.blob_id)

        done = await blobsplit.run(session, blobs, default=3)
        assert done == counts(naar_r2=1, naar_blobs=1)
        assert blobs.path(film.blob_id) == blobs.copies_path(film.blob_id, 2)
        assert blobs.path(ep.blob_id) == blobs.main_path(ep.blob_id)
        assert blobs.main_path(ep.blob_id).read_bytes() == b"aflevering"
        assert not blobs.media_path(film.blob_id).exists()
        assert (blobs.media / blobsplit.TRASH / ep.blob_id).exists()


async def test_twee_kopieen_na_een_onderbroken_ronde(settings):
    """Stond de inhoud al op haar plek en nog in blobs/ (een ronde stopte tussen neerzetten en
    opzij leggen), dan gaat alleen de oude kopie opzij."""
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        user = await create_user(session, "jonas@example.nl")
        films = await create_folder(session, user, name="Films")
        session.add(
            MediaLibrary(owner_id=user.id, root_id=films.id, kind="movies", name="Films", copies=2)
        )
        film = await create_file(session, user, films.id, "f.mkv", data=b"film", blobs=blobs)
        await session.commit()
        dest = blobs.copies_path(film.blob_id, 2)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"film")

        done = await blobsplit.run(session, blobs, default=3)
        assert done == counts(naar_r2=1)
        assert not blobs.main_path(film.blob_id).exists()
        assert dest.read_bytes() == b"film"
        assert (blobs.root / blobsplit.TRASH / film.blob_id).exists()


async def test_niets_te_doen_zonder_afwijkende_bibliotheek(settings):
    """Alle bibliotheken op de standaard (of 3 met standaard 3): geen map naast blobs/."""
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        user = await create_user(session, "jonas@example.nl")
        films = await create_folder(session, user, name="Films")
        session.add(
            MediaLibrary(owner_id=user.id, root_id=films.id, kind="movies", name="Films", copies=3)
        )
        await create_file(session, user, films.id, "f.mkv", data=b"film", blobs=blobs)
        await session.commit()
        assert await blobsplit.run(session, blobs, default=3) == NIETS
        assert [f for f in blobs.others() if f.exists()] == []


async def test_delete_en_adopt_kijken_overal(settings):
    blobs = BlobStore(settings.blob_dir)
    sha = "ab" * 32
    for path in blobs.everywhere(sha):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    assert not await blobs.adopt(blobs.root / "niet-nodig", sha)
    await blobs.delete(sha)
    assert [p for p in blobs.everywhere(sha) if p.exists()] == []

    # Alleen in blobs-r3/: adopt zet geen tweede kopie in blobs/
    only = "cd" * 32
    path = blobs.copies_path(only, 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"y")
    assert not await blobs.adopt(blobs.root / "niet-nodig", only)
    assert not blobs.main_path(only).exists()


async def test_path_zonder_mappen_geen_blik_extra(settings, monkeypatch):
    """Zonder mappen naast blobs/ kijkt path() hooguit één keer per minuut of ze er zijn; met
    een map die er is, zoekt hij daar."""
    blobs = BlobStore(settings.blob_dir)
    sha = "ef" * 32
    looked: list[str] = []
    real = type(blobs.root).is_dir

    def is_dir(self):
        looked.append(self.name)
        return real(self)

    monkeypatch.setattr(type(blobs.root), "is_dir", is_dir)
    for _ in range(5):
        assert blobs.path(sha) == blobs.main_path(sha)
    assert sorted(looked) == sorted(f.name for f in blobs.others())  # één keer elk
    monkeypatch.undo()

    path = blobs.copies_path(sha, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"z")
    assert blobs.path(sha) == blobs.main_path(sha)  # de cache: nog niet gezien
    blobs.saw(blobs.copies_dir(1))
    assert blobs.path(sha) == path
    with pytest.raises(ValueError):
        blobs.copies_dir(4)


async def test_onder_het_slot_opnieuw_nakijken(settings, monkeypatch):
    """Is het doel veranderd tussen de kopie en het slot (de film staat intussen ook in je eigen
    map), dan verhuist er niets: de kopie in blobs/ blijft, de tijdelijke kopie is weg."""
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        user = await create_user(session, "jonas@example.nl")
        films = await create_folder(session, user, name="Films")
        session.add(
            MediaLibrary(owner_id=user.id, root_id=films.id, kind="movies", name="Films", copies=2)
        )
        film = await create_file(session, user, films.id, "f.mkv", data=b"film", blobs=blobs)
        await session.commit()

        async def changed(_session, _sha, default):
            return default

        monkeypatch.setattr(blobsplit, "target_of", changed)
        done = await blobsplit.run(session, blobs, default=3)
        assert done == NIETS
        assert blobs.main_path(film.blob_id).read_bytes() == b"film"
        assert not blobs.copies_path(film.blob_id, 2).exists()
        assert list((blobs.copies_dir(2) / ".tmp").iterdir()) == []


async def test_kick_wekt_de_lus():
    blobsplit.kick()  # zonder lus: niets
    blobsplit._wake = asyncio.Event()
    try:
        blobsplit.kick()
        assert blobsplit._wake.is_set()
    finally:
        blobsplit._wake = None
