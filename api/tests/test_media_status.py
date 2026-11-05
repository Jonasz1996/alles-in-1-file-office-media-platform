"""app.cli media-status [--wait N] [--sessions] (M10, media/status.py).

De uitvoer en de exitcode: 0 als alles goed is of media uit staat; 1 bij een probleem; met
--wait altijd 0, ook bij een probleem (install.sh print dan de regels en gaat door: het stopt
nooit door media). --sessions vraagt de draaiende app wie er kijkt, met een kort token dat de
cli zelf tekent en dat nergens in de uitvoer staat.
"""

from __future__ import annotations

import httpx
import pytest

from app import cli
from app.config import get_settings
from app.main import app
from app.media import client, status
from app.media.types import WorkerCaps
from tests.media import record
from tests.media.fake_worker import FakeWorker
from tests.test_cli_media import KEY, _media_settings
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_scanner import library, scan
from tests.test_media_scanner import world as world  # noqa: F401
from tests.test_media_sessions import play, video
from tests.test_shares import person

# OSC 52 (het klembord), de cursor omhoog, een regel wissen, BEL, en CSI in C1
EVIL = "\x1b]52;c;Y3VybCBldmlsfHNo\x07\x1b[1A\x1b[2KFilm\x9b31m"


@pytest.fixture
def unbind():
    yield
    client.bind(None)


def caps(**changes) -> WorkerCaps:
    return WorkerCaps.model_validate({**record.load_caps(), **changes})


@pytest.fixture
def worker(monkeypatch):
    """De worker antwoordt met deze caps (zonder socket)."""
    found: dict = {"caps": caps(sandbox="on", mode="container")}

    async def fake_caps(limit_s: float = 5.0) -> WorkerCaps:
        return found["caps"]

    monkeypatch.setattr(client, "caps", fake_caps)
    return found


# --- de uitvoer en de exitcode ------------------------------------------------------------------


@pytest.mark.anyio
async def test_alles_goed(tmp_path, worker, unbind):
    found = await status.collect(_media_settings(tmp_path, tmp_path / "w.sock"))
    version = record.load_caps()["ffmpeg"]
    assert found["lines"] == [f"Media: aan (afgeschermd, ffmpeg {version})"]
    assert found["ok"] is True and found["exit"] == 0 and found["worker"] == "ok"


@pytest.mark.anyio
async def test_de_worker_mist_iets(tmp_path, worker, unbind):
    worker["caps"] = caps(sandbox="on", mode="container", missing=["libx264", "zscale"])
    found = await status.collect(_media_settings(tmp_path, tmp_path / "w.sock"))
    assert found["lines"][1] == "Let op: de worker mist libx264, zscale"
    assert found["ok"] is False and found["worker"] == "ffmpeg_missing" and found["exit"] == 1
    # met --wait (na de start in install.sh): dezelfde regels, exitcode 0
    found = await status.collect(_media_settings(tmp_path, tmp_path / "w.sock"), wait=1)
    assert found["lines"][1] == "Let op: de worker mist libx264, zscale" and found["exit"] == 0


@pytest.mark.anyio
async def test_een_gpu_alleen_als_de_worker_hem_ziet(tmp_path, worker, unbind):
    worker["caps"] = caps(sandbox="on", mode="container", hwaccels=["vaapi"], dri=False)
    found = await status.collect(_media_settings(tmp_path, tmp_path / "w.sock"))
    assert len(found["lines"]) == 1  # wat ffmpeg kan, is geen GPU
    worker["caps"] = caps(sandbox="on", mode="container", hwaccels=["vaapi"], dri=True)
    found = await status.collect(_media_settings(tmp_path, tmp_path / "w.sock"))
    assert found["lines"][1] == "GPU: /dev/dri (vaapi) (niet in gebruik)"
    assert found["ok"] is True and found["exit"] == 0  # nooit een eis


@pytest.mark.anyio
async def test_zonder_worker_met_en_zonder_wait(tmp_path, unbind):
    settings = _media_settings(tmp_path, tmp_path / "geen.sock")
    found = await status.collect(settings)
    assert found["exit"] == 1 and found["ok"] is False
    found = await status.collect(settings, wait=0.6)
    assert found["exit"] == 0 and found["ok"] is False
    assert found["lines"][0].startswith("Media: aan, maar de worker is niet bereikbaar (")


def test_de_cli_met_wait_stopt_nooit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PLATFORM_MEDIA_ENABLED", "true")
    monkeypatch.setenv("PLATFORM_MEDIA_WORKER", str(tmp_path / "geen.sock"))
    monkeypatch.setenv("PLATFORM_MEDIA_WORKER_KEY", KEY)
    get_settings.cache_clear()
    try:
        assert cli.main(["media-status"]) == 1
        assert cli.main(["media-status", "--wait", "0.6"]) == 0
    finally:
        client.bind(None)
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert out.count("niet bereikbaar") == 2 and KEY not in out


def test_media_uit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        assert cli.main(["media-status", "--wait", "5", "--sessions"]) == 0
    finally:
        get_settings.cache_clear()
    assert capsys.readouterr().out.splitlines() == ["Media: uit"]


@pytest.mark.anyio
async def test_een_echte_nep_worker(tmp_path, unbind):
    async with FakeWorker(tmp_path / "w" / "nep.sock", mode="container", key=KEY) as fake:
        found = await status.collect(_media_settings(tmp_path, fake.socket), wait=2)
    assert found["ok"] is True and found["exit"] == 0


# --- wie er kijkt (--sessions) ------------------------------------------------------------------


def test_de_regels_van_de_kijkers():
    assert status.sessions_lines(None, "de app antwoordt niet") == [
        "Kijkers: onbekend (de app antwoordt niet)"
    ]
    assert status.sessions_lines([], "") == ["Kijkers: niemand"]
    one = {"user": {"display_name": "Bert"}, "title": "Film", "mode": "transcode",
           "rung": {"height": 720}}  # fmt: skip
    two = {"user": None, "title": None, "mode": "direct"}
    lines = status.sessions_lines([one, two], "")
    assert lines[0].startswith("Let op: er kijken nu 2 mensen.")
    assert lines[1:] == ["  Bert: Film (transcode 720p)", "  ?: ? (direct)"]
    assert status.sessions_lines([one], "")[0].startswith("Let op: er kijkt nu 1 persoon.")


def test_stuurtekens_worden_een_vraagteken():
    """Review golf 5, S1: een titel of naam komt via install.sh (watchers) in de terminal van
    root; daar mag hij niets doen."""
    lines = status.sessions_lines(
        [{"user": {"display_name": "Bob\x1b[2K"}, "title": EVIL, "mode": "direct\x07"}], ""
    )
    assert lines[1] == "  Bob?[2K: ?]52;c;Y3VybCBldmlsfHNo??[1A?[2KFilm?31m (direct?)"
    assert not any(status.CONTROL.search(line) for line in lines)


@pytest.mark.anyio
async def test_de_regels_van_collect_zonder_stuurtekens(tmp_path, worker, unbind):
    worker["caps"] = caps(sandbox="on", mode="container", missing=["libx264\x1b[2K"])
    found = await status.collect(_media_settings(tmp_path, tmp_path / "w.sock"))
    assert found["lines"][1] == "Let op: de worker mist libx264?[2K"


@pytest.fixture
def asgi(monkeypatch):
    """httpx naar de app in dit proces (de cli vraagt de draaiende app op 127.0.0.1)."""
    real = httpx.AsyncClient
    seen: list[httpx.Request] = []

    async def log(request: httpx.Request) -> None:
        seen.append(request)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.ASGITransport(app=app)
        kwargs["event_hooks"] = {"request": [log]}
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


@pytest.mark.anyio
async def test_wie_er_kijkt(media_app, client, admin, asgi):  # noqa: F811
    settings, _ = media_app
    admin_headers, _ = admin
    headers, _ = await person(client, admin_headers, "bert@example.nl")
    node = await video(client, headers, settings, "bert.mp4")
    out = await play(client, headers, node["id"])
    found, why = await status.watching(settings)
    assert why == "" and found is not None and len(found) == 1
    assert found[0]["user"]["display_name"] == "Bert" and found[0]["title"] == "bert.mp4"
    lines = status.sessions_lines(found, why)
    assert lines[1] == "  Bert: bert.mp4 (direct)"
    assert out["sid"] not in str(found)
    # het token staat in de kop naar de app en nergens anders
    [request] = asgi
    assert str(request.url) == f"http://127.0.0.1:{settings.http_port}/api/v1/admin/media"
    token = request.headers["authorization"].removeprefix("Bearer ")
    assert token and token not in "\n".join(lines)


@pytest.mark.anyio
async def test_kijkers_zonder_beheerder_of_zonder_app(media_app):  # noqa: F811
    settings, _ = media_app
    found, why = await status.watching(settings)
    assert found is None and why == "er is nog geen beheerder"


@pytest.mark.anyio
async def test_kijkers_als_de_app_niet_antwoordt(media_app, admin):  # noqa: F811
    settings, _ = media_app
    found, why = await status.watching(settings, base="http://127.0.0.1:9")
    assert found is None and why.startswith("de app antwoordt niet (")


@pytest.mark.anyio
async def test_een_titel_met_stuurtekens_komt_niet_in_de_terminal(world, client, asgi):  # noqa: F811
    """Review golf 5, S1, van begin tot eind: wie mag bewerken, zet een titel met ESC en OSC 52
    (PATCH van M8). Die wordt zonder stuurtekens bewaard, en media-status --sessions print geen
    enkel stuurteken."""
    settings, _fake, headers = world
    _library_id, folder = await library(client, headers)
    node = await video(client, headers, settings, "film.mp4", parent=folder)
    await scan(settings)
    r = await client.patch(
        f"/api/v1/media/items/{node['id']}", json={"title": EVIL}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "]52;c;Y3VybCBldmlsfHNo[1A[2KFilm31m"
    await play(client, headers, node["id"])
    found, why = await status.watching(settings)
    assert why == "" and found is not None and len(found) == 1
    lines = status.sessions_lines(found, why)
    assert lines[1] == "  Jonas: ]52;c;Y3VybCBldmlsfHNo[1A[2KFilm31m (direct)"
    assert not any(status.CONTROL.search(line) for line in lines)
