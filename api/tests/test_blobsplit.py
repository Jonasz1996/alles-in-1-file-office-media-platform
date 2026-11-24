"""Replicatie per bibliotheek (app/blobsplit.py): het doel van een inhoud is het hoogste aantal
kopieën over al haar verwijzingen; de standaard staat in blobs/, de rest in blobs-r1/ tot
blobs-r3/. blobs-media/ van fase 11 wordt nog gelezen en leeggehaald."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import blobsplit
from app.models import MediaLibrary, Version
from app.storage import BlobStore
from tests.factories import create_blob, create_file, create_folder, create_user, maker

pytestmark = pytest.mark.anyio

NIETS = {
    "naar_blobs": 0,
    "naar_r1": 0,
    "naar_r2": 0,
    "naar_r3": 0,
    "weg": 0,
    "afgebroken": 0,
}


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
    """Staat de inhoud in blobs/, dan kijkt path() nergens anders (ook niet of de mappen ernaast
    er zijn). Ontbreekt ze daar, dan kijkt hij in de mappen ernaast, ook als hij onthouden had
    dat die er niet zijn: een ander proces kan net de eerste inhoud verhuisd hebben."""
    blobs = BlobStore(settings.blob_dir)
    sha = "ef" * 32
    main = blobs.main_path(sha)
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_bytes(b"z")
    looked: list[str] = []
    real = type(blobs.root).is_dir

    def is_dir(self):
        looked.append(self.name)
        return real(self)

    monkeypatch.setattr(type(blobs.root), "is_dir", is_dir)
    for _ in range(5):
        assert blobs.path(sha) == main
    assert looked == []
    # Ontbreekt de inhoud: één blik per map, elke keer opnieuw
    missing = "ee" * 32
    assert blobs.path(missing) == blobs.main_path(missing)
    assert blobs.path(missing) == blobs.main_path(missing)
    assert sorted(looked) == sorted([f.name for f in blobs.others()] * 2)
    monkeypatch.undo()

    # Dit proces onthield "blobs-r1/ is er niet"; een ander proces verhuisde de inhoud erheen
    # en zette de kopie in blobs/ opzij: path() vindt haar toch meteen
    BlobStore._seen[blobs.copies_dir(1)] = (False, time.monotonic())
    path = blobs.copies_path(sha, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"z")
    main.unlink()
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


async def _films_op_2(settings, blobs, data=b"film"):
    """Films op 2 kopieën met één film in blobs/; geeft de sessie-maker en de film."""
    async with maker(settings)() as session:
        user = await create_user(session, "jonas@example.nl")
        films = await create_folder(session, user, name="Films")
        session.add(
            MediaLibrary(owner_id=user.id, root_id=films.id, kind="movies", name="Films", copies=2)
        )
        film = await create_file(session, user, films.id, "f.mkv", data=data, blobs=blobs)
        await session.commit()
        return film


@pytest.mark.parametrize("broken", [b"", b"fi"], ids=["leeg", "half"])
async def test_stuk_doel_gaat_niet_voor_de_goede_kopie(settings, broken):
    """Review replicatie, punt 1: staat er al een kopie in blobs-r2/ maar is die leeg of half
    (stroomstoring na de hernoeming, of met de hand neergezet), dan gaat de goede kopie niet
    opzij. De kapotte wordt vervangen door een hele kopie; pas daarna gaat blobs/ opzij."""
    blobs = BlobStore(settings.blob_dir)
    film = await _films_op_2(settings, blobs)
    dest = blobs.copies_path(film.blob_id, 2)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(broken)

    async with maker(settings)() as session:
        done = await blobsplit.run(session, blobs, default=3)
    assert done == counts(naar_r2=1)
    assert dest.read_bytes() == b"film"
    assert blobs.path(film.blob_id) == dest
    assert blobs.path(film.blob_id).read_bytes() == b"film"
    assert (blobs.root / blobsplit.TRASH / film.blob_id).read_bytes() == b"film"
    assert list((blobs.copies_dir(2) / ".tmp").iterdir()) == []


async def test_stuk_doel_en_stuk_bron_niets_opzij(settings):
    """Is ook de bron stuk (klopt niet met haar SHA-256), dan verhuist er niets: geen kapotte
    kopie op haar plek, en de bron blijft waar ze stond."""
    blobs = BlobStore(settings.blob_dir)
    film = await _films_op_2(settings, blobs)
    src = blobs.main_path(film.blob_id)
    src.write_bytes(b"flim")  # stuk, zelfde grootte
    async with maker(settings)() as session:
        done = await blobsplit.run(session, blobs, default=3)
    assert done == NIETS
    assert src.read_bytes() == b"flim"
    assert not blobs.copies_path(film.blob_id, 2).exists()
    assert list((blobs.copies_dir(2) / ".tmp").iterdir()) == []


async def test_heel_doel_alleen_de_rest_opzij_en_gezien(settings):
    """Staat er al een hele kopie (een ronde stopte tussen neerzetten en opzij leggen), dan gaat
    de rest opzij, en weet path() in dit proces meteen dat blobs-r2/ er is (punt 5)."""
    blobs = BlobStore(settings.blob_dir)
    film = await _films_op_2(settings, blobs)
    dest = blobs.copies_path(film.blob_id, 2)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"film")
    BlobStore._seen[blobs.copies_dir(2)] = (False, time.monotonic())
    async with maker(settings)() as session:
        done = await blobsplit.run(session, blobs, default=3)
    assert done == counts(naar_r2=1)
    assert BlobStore._seen[blobs.copies_dir(2)][0] is True


async def test_doel_veranderd_tijdens_het_nakijken(settings, monkeypatch):
    """Verandert de kopie in blobs-r2/ terwijl we haar nakeken, dan legt deze ronde niets opzij."""
    blobs = BlobStore(settings.blob_dir)
    film = await _films_op_2(settings, blobs)
    dest = blobs.copies_path(film.blob_id, 2)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"")
    real = blobsplit._intact

    def intact(path, sha):
        ok = real(path, sha)
        path.write_bytes(b"x")  # intussen veranderd
        return ok

    monkeypatch.setattr(blobsplit, "_intact", intact)
    async with maker(settings)() as session:
        done = await blobsplit.run(session, blobs, default=3)
    assert done == NIETS
    assert blobs.main_path(film.blob_id).read_bytes() == b"film"
    assert dest.read_bytes() == b"x"


async def test_kopie_en_map_naar_de_schijf(settings, monkeypatch):
    """De kopie gaat met fsync naar de schijf voor ze op haar plek komt, en daarna de map
    (de hernoeming) voor de oude kopie opzij gaat."""
    blobs = BlobStore(settings.blob_dir)
    film = await _films_op_2(settings, blobs)
    order: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        order.append("fsync")
        return real_fsync(fd)

    def replace(src, dst):
        order.append(f"replace:{Path(dst).parent.name}")
        return real_replace(src, dst)

    monkeypatch.setattr(blobsplit.os, "fsync", fsync)
    monkeypatch.setattr(blobsplit.os, "replace", replace)
    async with maker(settings)() as session:
        done = await blobsplit.run(session, blobs, default=3)
    assert done == counts(naar_r2=1)
    dest_dir = blobs.copies_path(film.blob_id, 2).parent.name
    # kopie gesynct, neergezet, map gesynct; dan pas opzij (en ook die map gesynct)
    first = order.index(f"replace:{dest_dir}")
    assert order[first - 1] == "fsync" and order[first + 1] == "fsync"
    assert order.index(f"replace:{blobsplit.TRASH}") > first + 1
    assert order[-1] == "fsync"


async def test_afgebroken_kopie_wordt_opgeruimd(settings):
    """Review replicatie, punt 2: een kopie die bij een herstart halverwege bleef steken
    (split-*.part in <map>/.tmp/), gaat weg zodra er een uur niets meer in geschreven is.
    Een kopie die nog loopt en de uploads in blobs/.tmp/ blijven."""
    blobs = BlobStore(settings.blob_dir)
    old = time.time() - blobsplit.PART_S - 60
    made = {}
    for folder in (blobs.root, blobs.copies_dir(2), blobs.media):
        tmp = folder / ".tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        made[folder] = tmp / "split-oud.part"
        made[folder].write_bytes(b"half")
        os.utime(made[folder], (old, old))
    fresh = blobs.copies_dir(2) / ".tmp" / "split-nu.part"
    fresh.write_bytes(b"bezig")
    upload = blobs.root / ".tmp" / "tmpabc123"
    upload.write_bytes(b"upload")
    os.utime(upload, (old, old))

    async with maker(settings)() as session:
        done = await blobsplit.run(session, blobs, default=3)
    assert done == counts(afgebroken=3)
    assert not any(p.exists() for p in made.values())
    assert fresh.exists() and upload.exists()
