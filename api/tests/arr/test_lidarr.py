"""Lidarr (fixlijst 26-11, punt 8): paden, wanneer een album klaar is, en de staging."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from app.arr import lidarr


def _album(files: int, tracks: int) -> lidarr.Album:
    return lidarr.Album(1, 2, "Album", "Artiest", True, files, tracks)


def test_local_path_stays_under_music(tmp_path) -> None:
    assert (
        lidarr.local_path(tmp_path, "/music/A/B/01.flac") == tmp_path / "import/lidarr/A/B/01.flac"
    )
    assert lidarr.local_path(tmp_path, "/music/../etc/passwd") is None
    assert lidarr.local_path(tmp_path, "/downloads/x.flac") is None
    assert lidarr.local_path(tmp_path, "/music") is None


def test_ready_complete_or_quiet() -> None:
    now = datetime.now(UTC)
    fresh = [lidarr.TrackFile(i, f"/music/a/{i}.flac", now) for i in range(3)]
    assert lidarr.ready(_album(3, 3), fresh, now)
    assert not lidarr.ready(_album(3, 10), fresh, now)
    old = [lidarr.TrackFile(1, "/music/a/1.flac", now - timedelta(days=2))]
    assert lidarr.ready(_album(1, 10), old, now)
    assert not lidarr.ready(_album(0, 10), [], now)


def test_stage_album_moves_names_out_of_lidarr(tmp_path) -> None:
    album = tmp_path / "import/lidarr/Artiest/Album (2020)"
    album.mkdir(parents=True)
    one = album / "01 - Een.flac"
    one.write_bytes(b"een")
    two = album / "02 - Twee.flac"
    two.write_bytes(b"twee")
    # Nog een naam in de downloads van qBittorrent: dan een kopie (één naam per bestand)
    os.link(two, tmp_path / "torrent-twee.flac")
    source = lidarr.stage_album(tmp_path, 7, [one, two])
    assert str(source) == "arr/lidarr-7"
    staged = tmp_path / "import/arr/lidarr-7/Artiest/Album (2020)"
    assert (staged / "01 - Een.flac").read_bytes() == b"een"
    assert (staged / "02 - Twee.flac").stat().st_nlink == 1
    assert not album.exists() and not album.parent.exists()
    assert lidarr.staged_dirs(tmp_path) == [tmp_path / "import/arr/lidarr-7"]
