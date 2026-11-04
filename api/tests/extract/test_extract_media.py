"""Audio en video (Keuze 17): titel, artiest en album als meta, geen tekst."""

from __future__ import annotations

from mutagen.id3 import ID3, TALB, TIT2, TPE1

from tests.extract.lees import lees

# Twintig lege MPEG-1 Layer III-frames (128 kbit/s, 44,1 kHz): genoeg voor mutagen
FRAMES = (bytes.fromhex("FFFB9064") + bytes(413)) * 20


def test_titel_artiest_en_album(tmp_path):
    pad = tmp_path / "nummer.mp3"
    pad.write_bytes(FRAMES)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Liedtitel"))
    tags.add(TPE1(encoding=3, text="De Artiest"))
    tags.add(TALB(encoding=3, text="Het Album"))
    tags.save(pad)
    found, text = lees(pad, "media")
    assert (found.state, text, found.meta) == ("done", "", "Liedtitel\nDe Artiest\nHet Album")


def test_zonder_tags_is_none():
    found, text = lees(FRAMES, "media")
    assert (found.state, text, found.meta) == ("none", "", "")


def test_geen_audio_is_none():
    found, text = lees(b"dit is geen audio" * 10, "media")
    assert (found.state, found.meta) == ("none", "")
