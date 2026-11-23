"""Films en series naar blobs-media/ (app/blobsplit.py): per plek, niet per soort bestand."""

from __future__ import annotations

import os
import time

import pytest

from app import blobsplit
from app.models import MediaLibrary
from app.storage import BlobStore
from tests.factories import create_file, create_folder, create_user, maker

pytestmark = pytest.mark.anyio


async def test_only_library_content_moves_and_comes_back(settings):
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        user = await create_user(session, "jonas@example.nl")
        films = await create_folder(session, user, name="Films")
        music = await create_folder(session, user, name="Muziek")
        own = await create_folder(session, user, name="Eigen video's")
        session.add(MediaLibrary(owner_id=user.id, root_id=films.id, kind="movies", name="Films"))
        session.add(MediaLibrary(owner_id=user.id, root_id=music.id, kind="music", name="Muziek"))
        sub = await create_folder(session, user, films.id, name="Film (2020)")
        film = await create_file(session, user, sub.id, "film.mkv", data=b"film", blobs=blobs)
        both = await create_file(session, user, sub.id, "b.mkv", data=b"ook eigen", blobs=blobs)
        await create_file(session, user, own.id, "b.mkv", data=b"ook eigen", blobs=blobs)
        song = await create_file(session, user, music.id, "lied.flac", data=b"lied", blobs=blobs)
        home = await create_file(
            session, user, own.id, "vakantie.mp4", data=b"vakantie", blobs=blobs
        )
        await session.commit()

        done = await blobsplit.run(session, blobs)
        assert done["naar_media"] == 1
        assert blobs.media_path(film.blob_id).read_bytes() == b"film"
        assert not blobs.main_path(film.blob_id).exists()
        assert blobs.path(film.blob_id) == blobs.media_path(film.blob_id)
        # In de eigen map (ook als het dezelfde inhoud is), muziek: blijven in blobs/
        for node in (both, song, home):
            assert blobs.path(node.blob_id) == blobs.main_path(node.blob_id)
        # De oude kopie ligt opzij tot ze een dag oud is
        aside = blobs.root / blobsplit.TRASH / film.blob_id
        assert aside.exists()

        # Dezelfde film ook in je eigen map: terug naar blobs/ (3 kopieën)
        await create_file(session, user, own.id, "film.mkv", data=b"film")
        await session.commit()
        done = await blobsplit.run(session, blobs)
        assert done["terug"] == 1
        assert blobs.main_path(film.blob_id).read_bytes() == b"film"
        assert not blobs.media_path(film.blob_id).exists()

        # Een tweede ronde doet niets; .weg ouder dan een dag gaat weg
        old = time.time() - blobsplit.GRACE_S - 10
        os.utime(aside, (old, old))
        done = await blobsplit.run(session, blobs)
        assert done == {"naar_media": 0, "terug": 0, "weg": 1}


async def test_delete_removes_both(settings):
    blobs = BlobStore(settings.blob_dir)
    sha = "ab" * 32
    for path in (blobs.main_path(sha), blobs.media_path(sha)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    assert not await blobs.adopt(blobs.root / "niet-nodig", sha)
    await blobs.delete(sha)
    assert not blobs.main_path(sha).exists()
    assert not blobs.media_path(sha).exists()
