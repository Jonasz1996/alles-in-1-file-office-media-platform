"""De commando's van media in app.cli (fase 5, §3.5): elk een dunne aanroep van een functie in het
pakket. Zonder pakket (de stub) zegt een commando dat media uit staat, met exitcode 1; wat er
zonder pakket al kan (media-status, media-parse, media-provider-check), geeft 0.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app import cli
from app.config import get_settings
from app.media import importer, loadtest, names, providers, status


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_media_status_zonder_media(capsys):
    assert cli.main(["media-status"]) == 0
    assert capsys.readouterr().out.strip() == "Media: uit"


def test_media_status_geeft_de_opties_door(monkeypatch, capsys):
    seen = {}

    async def collect(settings, *, sessions=False, wait=0):
        seen.update(sessions=sessions, wait=wait)
        return {"ok": False, "lines": ["Media: aan", "Let op: iemand kijkt"]}

    monkeypatch.setattr(status, "collect", collect)
    assert cli.main(["media-status", "--wait", "20", "--sessions"]) == 1
    assert seen == {"sessions": True, "wait": 20.0}
    assert capsys.readouterr().out.splitlines() == ["Media: aan", "Let op: iemand kijkt"]


def test_media_parse_toont_wat_de_parser_ziet(monkeypatch, capsys):
    assert cli.main(["media-parse", "Films/The Matrix (1999).mkv"]) == 0
    assert json.loads(capsys.readouterr().out) == {"pad": "Films/The Matrix (1999).mkv"}

    monkeypatch.setattr(names, "parse", lambda name: {"title": name.upper()})
    assert cli.main(["media-parse", "a.mkv", "b.mkv"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines == [{"pad": "a.mkv", "title": "A.MKV"}, {"pad": "b.mkv", "title": "B.MKV"}]


def test_media_import_zonder_pakket_zegt_media_uit(capsys):
    code = cli.main(
        ["media-import", "/data/import/Films", "--naar", "/Films", "--eigenaar", "j@example.nl"]
    )
    assert code == 1
    assert "media_off" in capsys.readouterr().err


def test_media_import_geeft_de_argumenten_door(monkeypatch, capsys):
    seen = {}
    job = uuid.uuid4()

    async def start_cli(settings, source, target, owner, *, move=False):
        seen.update(source=source, target=target, owner=owner, move=move)
        return job

    monkeypatch.setattr(importer, "start_cli", start_cli)
    code = cli.main(
        [
            "media-import",
            "/data/import/Films",
            "--naar",
            "/Films",
            "--eigenaar",
            "j@example.nl",
            "--verplaats",
        ]
    )
    assert code == 0
    assert seen == {
        "source": "/data/import/Films",
        "target": "/Films",
        "owner": "j@example.nl",
        "move": True,
    }
    assert str(job) in capsys.readouterr().out


def test_media_provider_check_zonder_sleutel(capsys):
    assert cli.main(["media-provider-check"]) == 0
    assert capsys.readouterr().out.splitlines() == ["tmdb: off", "musicbrainz: off", "caa: off"]


def test_media_provider_check_met_een_fout(monkeypatch, capsys):
    seen = {}

    async def check(settings, *, record=False):
        seen["record"] = record
        return {"tmdb": "auth", "musicbrainz": "ok", "caa": "ok"}

    monkeypatch.setattr(providers, "check", check)
    assert cli.main(["media-provider-check", "--opnemen"]) == 1
    assert seen == {"record": True}
    assert "tmdb: auth" in capsys.readouterr().out


def test_media_loadtest_zonder_pakket_zegt_media_uit(capsys):
    assert cli.main(["media-loadtest"]) == 1
    assert "media_off" in capsys.readouterr().err


def test_media_loadtest_geeft_de_argumenten_door(monkeypatch, tmp_path, capsys):
    seen = {}

    async def run_cli(settings, *, streams, seconds, codec, external):
        seen.update(streams=streams, seconds=seconds, codec=codec, external=external)
        return {"passed": True, "phases": {"a": "ok"}}

    monkeypatch.setattr(loadtest, "run_cli", run_cli)
    out = tmp_path / "uit.json"
    code = cli.main(
        ["media-loadtest", "--sessies", "2", "--duur", "60", "--codec", "vp9", "--json", str(out)]
    )
    assert code == 0
    assert seen == {"streams": 2, "seconds": 60, "codec": "vp9", "external": None}
    assert json.loads(out.read_text()) == {"passed": True, "phases": {"a": "ok"}}
    assert str(out) in capsys.readouterr().out


def test_de_bestaande_commandos_blijven(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    help_text = capsys.readouterr().out
    old = ("reset-2fa", "make-admin", "db-downgrade", "office-checkpoint")
    for name in (*old, *cli.MEDIA_COMMANDS):
        assert name in help_text
