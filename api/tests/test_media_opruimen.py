"""Bij het starten van media (runtime.clear_sessions): de stukken van de sessies weg, en
(M0-hotfix van M10, m9.md) ook een .part van vóór een crash: een remux van audio (M9) en een bron
van de belastingtest (M10). Wat af is, blijft; een symlink wordt niet gevolgd.
"""

from __future__ import annotations

import os

from app.config import Settings
from app.media import runtime


def test_bij_het_starten_gaan_de_halve_bestanden_weg(tmp_path):
    settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32)
    media = settings.media_dir
    (media / "sessions" / "s1").mkdir(parents=True)
    (media / "sessions" / "s1" / "0.m4s").write_bytes(b"oud")
    audio = media / "audio"
    audio.mkdir()
    sha = "a" * 64
    (audio / f"{sha}.mp4").write_bytes(b"af")
    (audio / f"{sha}.mp4.part").write_bytes(b"half")
    (audio / f"{sha}.1.mp4.part").write_bytes(b"half")
    loadtest = media / "loadtest"
    loadtest.mkdir()
    (loadtest / "testsrc2-0-300s-1080p.mkv").write_bytes(b"af")
    (loadtest / "cellauto-1-300s-1080p.mkv.part").write_bytes(b"half")
    # een symlink met de naam van een .part: niet volgen (het doel blijft)
    elsewhere = tmp_path / "van-jonas.mp4.part"
    elsewhere.write_bytes(b"niet van media")
    os.symlink(elsewhere, audio / f"{'b' * 64}.mp4.part")
    trick = media / "trick" / sha
    trick.mkdir(parents=True)
    (trick / "0.jpg.part").write_bytes(b"geen cache van een remux")

    runtime.clear_sessions(settings)

    assert not (media / "sessions").exists()
    assert sorted(p.name for p in audio.iterdir()) == [f"{sha}.mp4", f"{'b' * 64}.mp4.part"]
    assert sorted(p.name for p in loadtest.iterdir()) == ["testsrc2-0-300s-1080p.mkv"]
    assert elsewhere.read_bytes() == b"niet van media"
    assert (trick / "0.jpg.part").exists()  # alleen audio/ en loadtest/


def test_zonder_mappen_niets_aan_de_hand(tmp_path):
    settings = Settings(data_dir=tmp_path / "leeg", web_dist=tmp_path / "web", jwt_secret="t" * 32)
    runtime.clear_sessions(settings)
    assert not (tmp_path / "leeg").exists()
