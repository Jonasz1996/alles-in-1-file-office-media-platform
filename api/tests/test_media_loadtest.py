"""De belastingtest in de app (M10, §4.6; media/loadtest.py) met de nep-worker.

De bronnen komen van synth (de nep schrijft bytes naar de fd van de app; de probe is die van
stream_src.mkv als HEVC, zodat de beslissing transcode kiest), de stromen van M4 met de
nep-worker, en de spelers praten via de ASGI-transport met de app. De tijden zijn kleiner
(Timing.scale); de grenzen (start ≤ 5 s, 0 keer haperen) zijn de echte.

Ook: hij weigert bij echte transcodes en zonder PLATFORM_MEDIA_LOADTEST; hij stopt bij een
vierde ffmpeg; na afloop zijn zijn sessies en hun cache weg en wacht de analyse niet meer; de
route loadtest/play voor de spelers buiten de app (fase c) en een speler die een herstart
overleeft; de cli.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import httpx
import pytest

from app import cli
from app.config import MediaConfig, get_settings
from app.main import app
from app.media import admin as media_admin
from app.media import analysis, loadtest, runtime, sessions, streams
from app.media.state import MediaPolicy, MediaState
from app.media.wire import Code, Op
from tests.media import fake_worker as FW
from tests.media.media_contract import failed
from tests.test_media_contract import WAIT_S, source_probe

pytestmark = pytest.mark.anyio

BASE = "/api/v1/admin/media/loadtest"
FAST = loadtest.Timing(stagger_s=1.0, seek_at_s=2.0, seek_jump_s=3.0, tick_s=0.02, scale=0.1)


def asgi_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def lt(settings, client, monkeypatch, tmp_path):
    """Media aan met de belastingtest aan (PLATFORM_MEDIA_LOADTEST=1), een nep-worker die synth
    mag, en een probe van de bronnen die transcode vraagt."""
    on = settings.model_copy(
        update={"media_enabled": True, "media_loadtest": True, "media_segment_wait": WAIT_S}
    )
    app.dependency_overrides[get_settings] = lambda: on
    sessions.reset()
    streams.reset()
    loadtest.reset()
    media_admin._bench = None
    monkeypatch.setattr(app.state, "media", MediaState(enabled=True, worker="ok"), raising=False)
    monkeypatch.setattr(app.state, "media_policy", MediaPolicy(MediaConfig()), raising=False)
    runtime.bind(app, on)

    async def probe(source):
        return source_probe("transcode", "h264")

    monkeypatch.setattr(loadtest, "probe_source", probe)
    async with FW.running(tmp_path / "w.sock", loadtest=True) as fake:
        made: list[dict] = []

        async def synth(job: FW.Job) -> dict:
            made.append(job.args.model_dump())
            os.write(job.outputs[0], b"\x1aE\xdf\xa3" + b"bron" * 1000)
            return {"source": job.args.source, "cpu_s": 0.1, "max_rss_kb": 1}

        fake.on(Op.SYNTH, synth)
        fake.made = made  # type: ignore[attr-defined]
        yield on, fake
        loadtest.reset()
        for s in list(sessions.table().open()):
            sessions.close(s)
        for st in streams.all():
            await streams.close(st.session)
    runtime.unbind()
    sessions.reset()
    streams.reset()


async def finish(limit_s: float = 60) -> dict[str, Any]:
    run = loadtest._current
    assert run is not None and run.task is not None
    await asyncio.wait_for(asyncio.shield(run.task), limit_s)
    assert run.report is not None
    return run.report


async def start(client, headers, timing=FAST, **body) -> str:
    """POST /loadtest, met de spelers via de ASGI-transport en de kleinere tijden."""
    original = loadtest.start

    def patched(app_, settings, user, data, **kwargs):
        return original(app_, settings, user, data, http=asgi_http, timing=timing)

    loadtest.start = patched  # type: ignore[assignment]
    try:
        r = await client.post(BASE, json=body, headers=headers)
    finally:
        loadtest.start = original  # type: ignore[assignment]
    assert r.status_code == 202, r.text
    return r.json()["job"]


def nothing_left() -> None:
    assert not [s for s in sessions.table().open() if s.loadtest]
    assert not [st for st in streams.all() if st.session.loadtest]


# --- een run --------------------------------------------------------------------------------------


async def test_de_rooktest_van_ci(lt, client, admin):
    """Korter dan 120 s: alleen fase b, kleine bronnen (60 s, 720p), de laagste trede."""
    settings, fake = lt
    headers, _ = admin
    await start(client, headers, streams=3, seconds=30)
    view = (await client.get(BASE, headers=headers)).json()
    assert view["state"] in ("making_sources", "running")
    report = await finish()
    assert report["short"] is True
    assert report["sources"] == [
        "testsrc2-0-60s-720p.mkv", "cellauto-1-60s-720p.mkv", "cellauto-2-60s-720p.mkv",
    ]  # fmt: skip
    assert [(m["source"], m["seed"], m["duration_s"], m["height"]) for m in fake.made] == [
        ("testsrc2", 0, 60, 720), ("cellauto", 1, 60, 720), ("cellauto", 2, 60, 720),
    ]  # fmt: skip
    phases = report["phases"]
    assert set(phases) == {"b", "c", "d"} and phases["c"]["skipped"] and phases["d"]["skipped"]
    b = phases["b"]
    assert b["passed"], b["problems"]
    assert b["stalls"] == 0 and all(s is not None and s <= 5 for s in b["starts_s"])
    for player in b["players"]:
        assert player["mode"] == "transcode" and player["height"] <= 480 and not player["error"]
    assert report["passed"] is True and report["ffmpeg_max"] <= 3
    assert report["summary"][-1] == "uitkomst: geslaagd"
    view = (await client.get(BASE, headers=headers)).json()
    assert view["state"] == "done" and view["report"]["passed"] is True
    nothing_left()
    names = sorted(p.name for p in loadtest.loadtest_dir(settings).iterdir())
    assert names == sorted(report["sources"])  # geen .part
    # de bronnen blijven en worden de volgende keer hergebruikt
    await start(client, headers, streams=1, seconds=30)
    await finish()
    assert len(fake.made) == 3


async def test_fase_a_en_b(lt, client, admin):
    settings, _ = lt
    headers, _ = admin
    await start(client, headers, streams=2, seconds=300)
    report = await finish(120)
    assert report["short"] is False and set(report["phases"]) == {"a", "b", "c", "d"}
    for name in ("a", "b"):
        phase = report["phases"][name]
        assert phase["passed"], (name, phase["problems"])
        assert [p["source"] for p in phase["players"]] == [
            "testsrc2-0-300s-1080p.mkv", "cellauto-1-300s-1080p.mkv",
        ]  # fmt: skip
    # a springt (voorbij wat de producent al maakte), b niet
    assert all(p["seek_s"] is not None for p in report["phases"]["a"]["players"])
    assert all(p["seek_s"] is None for p in report["phases"]["b"]["players"])
    assert report["passed"] is True
    assert report["api_p95_ms"] is not None  # de API werd gemeten
    nothing_left()


async def test_de_analyse_wacht_zolang_hij_loopt(lt, client, admin, monkeypatch):
    headers, _ = admin
    seen: list[int] = []
    count = loadtest.ffmpeg_count

    async def counting(app_):
        seen.append(analysis.busy_live())
        return await count(app_)

    monkeypatch.setattr(loadtest, "ffmpeg_count", counting)
    before = analysis.busy_live
    await start(client, headers, streams=1, seconds=30)
    await finish()
    assert seen and min(seen) >= 2
    assert analysis.busy_live is before and analysis.busy_live() == 0


async def test_stopt_bij_de_vierde_ffmpeg(lt, client, admin, monkeypatch):
    headers, _ = admin
    counts = iter([1, 2, 3, 4])

    async def count(app_):
        return next(counts, 4)

    monkeypatch.setattr(loadtest, "ffmpeg_count", count)
    slow = loadtest.Timing(stagger_s=1, seek_at_s=60, seek_jump_s=90, tick_s=0.02, scale=1.0)
    await start(client, headers, timing=slow, streams=3, seconds=30)
    report = await finish()
    assert report["stopped"] == "4 ffmpeg-processen (hooguit 3)"
    assert (
        report["passed"] is False and "gestopt: 4 ffmpeg-processen (hooguit 3)" in report["summary"]
    )
    assert report["ffmpeg_max"] == 4
    assert (await client.get(BASE, headers=headers)).json()["state"] == "stopped"
    nothing_left()


async def test_een_bron_die_mislukt(lt, client, admin):
    settings, fake = lt
    headers, _ = admin

    async def broken(job):
        os.write(job.outputs[0], b"half")
        raise FW.OpError(Code.FAILED, "ffmpeg stopte")

    fake.on(Op.SYNTH, broken)
    await start(client, headers, streams=1, seconds=30)
    report = await finish()
    assert report == {"error": "WorkerFailed", "passed": False}
    assert (await client.get(BASE, headers=headers)).json()["state"] == "failed"
    assert list(loadtest.loadtest_dir(settings).iterdir()) == []  # geen .part over


# --- weigeren -------------------------------------------------------------------------------------


async def test_weigert_als_er_iets_loopt(lt, client, admin, monkeypatch):
    headers, _ = admin
    monkeypatch.setattr(media_admin, "transcodes_running", lambda: 1)  # echte kijkers
    failed(await client.post(BASE, json={}, headers=headers), 409, "media_bench_busy")
    play = {"source": 0, "seconds": 30}
    r = await client.post(f"{BASE}/play", json=play, headers=headers)
    failed(r, 409, "media_bench_busy")
    monkeypatch.setattr(media_admin, "transcodes_running", lambda: 0)
    monkeypatch.setattr(media_admin, "bench_running", lambda: True)  # een meting
    failed(await client.post(BASE, json={}, headers=headers), 409, "media_bench_busy")
    monkeypatch.setattr(media_admin, "bench_running", lambda: False)
    await start(client, headers, streams=1, seconds=30)
    failed(await client.post(BASE, json={}, headers=headers), 409, "media_bench_busy")  # al bezig
    await finish()
    # grenzen van de vraag
    for wrong in ({"streams": 4}, {"seconds": 20}, {"codec": "av1"}):
        failed(await client.post(BASE, json=wrong, headers=headers), 422, "invalid_input")


async def test_zonder_loadtest_staat_hij_uit(lt, client, admin):
    settings, fake = lt
    headers, _ = admin
    off = settings.model_copy(update={"media_loadtest": False})
    app.dependency_overrides[get_settings] = lambda: off
    params = failed(await client.post(BASE, json={}, headers=headers), 503, "media_off")
    assert params == {"reason": "loadtest"}
    r = await client.post(f"{BASE}/play", json={"source": 0}, headers=headers)
    assert failed(r, 503, "media_off") == {"reason": "loadtest"}
    assert fake.made == []
    with pytest.raises(Exception) as caught:
        await loadtest.run_cli(off, streams=1, seconds=30)
    detail = caught.value.detail  # type: ignore[attr-defined]
    assert detail["code"] == "media_off" and detail["params"] == {"reason": "loadtest"}


# --- de spelers buiten de app (fase c) -----------------------------------------------------------


async def test_play_voor_een_speler_buiten_de_app(lt, client, admin):
    settings, fake = lt
    headers, _ = admin
    player = str(uuid.uuid4())
    body = {"source": 1, "seconds": 30, "player": player}
    r = await client.post(f"{BASE}/play", json=body, headers=headers)
    params = failed(r, 409, "media_probe_pending", "loadtest")
    assert r.headers["retry-after"] == "5" and params["reason"] == "loadtest"
    from app.routers import admin_media

    await asyncio.wait_for(asyncio.gather(*admin_media._making.values()), 10)
    # de redenen staan in het geheugen van het proces (admin.reasons): eerdere tests in dezelfde
    # run tellen al mee, dus vergelijken met vóór de play
    before = (await client.get("/api/v1/admin/media", headers=headers)).json()["reasons"]
    r = await client.post(f"{BASE}/play", json=body, headers=headers)
    assert r.status_code == 200, r.text
    out = loadtest.LoadtestPlay.model_validate(r.json())
    assert out.mode == "transcode" and out.height is not None and out.height <= 480
    s = sessions.table().by_sid[out.sid]
    assert s.loadtest and s.sha != "" and s.path is not None
    assert s.path.name == "cellauto-1-60s-720p.mkv"
    # de beheerder ziet hem als belastingtest; hij telt niet mee in de redenen
    seen = (await client.get("/api/v1/admin/media", headers=headers)).json()
    assert [v["title"] for v in seen["sessions"]] == ["belastingtest"]
    assert seen["reasons"] == before
    r = await client.get(f"/api/v1/stream/{out.sid}/map.json")
    assert r.status_code == 200
    await client.delete(f"/api/v1/stream/{out.sid}")


class FakeApp:
    """Een app voor één speler: map.json, init en segmenten van 2 s; kan een segment laten
    wachten (haperen), en kan 'herstarten' (410 tot er een nieuwe sessie is)."""

    def __init__(self, count: int = 5) -> None:
        self.count = count
        self.sessions = 0
        self.gone: set[str] = set()
        self.slow: dict[int, float] = {}
        self.restart_after: int | None = None
        self.down = 0  # zoveel keer weigert open nog (de app start)
        self.gets: list[str] = []

    async def open(self, start_ms: int, replace: str | None) -> loadtest.LoadtestPlay:
        if self.down:
            self.down -= 1
            raise loadtest.SessionGone("503")
        self.sessions += 1
        return loadtest.LoadtestPlay(sid=f"s{self.sessions}", mode="transcode",
                                     reasons=["bitrate"], height=480,
                                     duration_ms=self.count * 2000, start_ms=start_ms)  # fmt: skip

    async def handle(self, request: httpx.Request) -> httpx.Response:
        _, _, _, _, sid, rest = request.url.path.split("/", 5)
        self.gets.append(f"{sid}/{rest}")
        if sid in self.gone:
            return httpx.Response(410)
        if rest == "map.json":
            segs = [[k * 2000, 2000] for k in range(self.count)]
            return httpx.Response(200, json={"init": "init.mp4?g=1", "segments": segs})
        if rest.startswith("init.mp4"):
            return httpx.Response(200, content=b"init")
        n = int(rest.removesuffix(".m4s"))
        if n in self.slow:
            await asyncio.sleep(self.slow.pop(n))
        if self.restart_after is not None and n >= self.restart_after:
            self.restart_after = None
            self.gone.add(sid)
            self.down = 2
            return httpx.Response(410)
        return httpx.Response(200, content=b"seg")


def player(fake: FakeApp, timing: loadtest.Timing) -> loadtest.Player:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle), base_url="http://x")
    report = loadtest.PlayerReport(name="c1", phase="c", source="0")
    return loadtest.Player(http, fake.open, report, timing=timing)


async def test_de_speler_telt_haperen(monkeypatch):
    fake = FakeApp(count=5)
    fake.slow[3] = 1.5  # segment 3 komt pas na 1,5 s: de speler (10× sneller) staat stil
    p = player(fake, loadtest.Timing(tick_s=0.02, scale=0.1))
    await p.run(60)
    r = p.report
    assert r.error is None and r.start_s is not None and r.start_s < 1
    assert r.stalls == 1 and r.stall_s > 0.5
    assert r.played_s == pytest.approx(10, abs=0.3)
    found = loadtest.judge("b", [r], None)
    assert not found["passed"] and found["problems"] == ["1 keer haperen"]


async def test_de_speler_overleeft_een_herstart(monkeypatch):
    """Fase c: de sessie is weg (410), de app start nog (503): de speler vraagt een nieuwe
    sessie op de plek waar hij was, en meldt hoe lang het duurde."""
    monkeypatch.setattr(loadtest, "OPEN_RETRY_S", 0.05)
    fake = FakeApp(count=8)
    fake.restart_after = 4
    p = player(fake, loadtest.Timing(tick_s=0.02, scale=0.25))
    await p.run(60)
    r = p.report
    assert r.error is None and fake.sessions == 2
    [restart] = r.restarts
    assert restart["weg_s"] >= 0.1 and restart["na_ready_s"] < 1
    assert any(g.startswith("s2/") and g.endswith(".m4s") and g != "s2/0.m4s" for g in fake.gets)
    assert loadtest.judge("c", [r], None)["passed"] is (r.stalls == 0)


# --- de onderdelen --------------------------------------------------------------------------------


def test_segments_uit_map_json():
    grid = {"segments": {"grid_ms": 2000, "origin_ms": 100, "count": 3, "last_frame_ms": 5900}}
    assert loadtest._segments(grid) == [(0, 2100), (2100, 4100), (4100, 5901)]
    listed = {"segments": [[0, 2000], [2000, 1500]]}
    assert loadtest._segments(listed) == [(0, 2000), (2000, 3500)]


def test_p95_en_de_grenzen():
    assert loadtest.p95([]) is None
    assert loadtest.p95([float(i) for i in range(1, 101)]) == 96.0
    ok = loadtest.PlayerReport(name="a1", phase="a", source="x", start_s=1.0, speeds=[1.5, 1.3])
    slow = loadtest.PlayerReport(name="a2", phase="a", source="y", start_s=6.0, speeds=[0.9])
    found = loadtest.judge("a", [ok, slow], 250.0)
    assert found["problems"] == ["een start boven 5 s", "een run onder 1.1×", "de API 250 ms (p95)"]
    assert loadtest.judge("b", [ok], 900.0)["passed"]  # de API en de snelheid tellen in a
    late = loadtest.PlayerReport(name="c1", phase="c", source="z", start_s=1.0,
                                 restarts=[{"weg_s": 40.0, "na_ready_s": 12.0}])  # fmt: skip
    assert loadtest.judge("c", [late], None)["problems"] == ["weer spelen later dan 10 s na ready"]
    lines = loadtest.summary_lines({
        "phases": {"b": loadtest.judge("b", [ok], None), "d": {"skipped": "met de hand"}},
        "api_p95_ms": 12.5, "passed": True,
    })  # fmt: skip
    assert lines == ["fase b: start 1.00 s, 0 keer haperen, geslaagd",
                     "fase d: niet gedraaid (met de hand)", "API p95: 12.5 ms",
                     "uitkomst: geslaagd"]  # fmt: skip


def test_het_profiel_vraagt_transcode():
    profile = loadtest.profile_for("h264", ["hvc1.2.4.L120.90", "avc1.64001f"], short=True)
    assert profile["max"] == {"height": 480, "bitrate_kbps": 4000}
    assert profile["codecs"]["avc1.64001f"]["mse"] is True
    assert profile["codecs"]["hvc1.2.4.L120.90"]["mse"] is False
    vp9 = loadtest.profile_for("vp9", ["vp09.00.31.08"], short=False)
    assert vp9["max"]["height"] == 1080 and vp9["codecs"]["vp09.00.31.08"]["mse"] is True


# --- de cli ---------------------------------------------------------------------------------------


@pytest.fixture
def via_asgi(monkeypatch):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.ASGITransport(app=app)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_de_cli_vraagt_de_draaiende_app(lt, admin, via_asgi, monkeypatch):
    settings, _ = lt
    monkeypatch.setattr(loadtest, "TIMING", FAST)
    original = loadtest.start
    monkeypatch.setattr(loadtest, "start", lambda *a, **k: original(*a, **{**k, "timing": FAST}))
    report = await loadtest.run_cli(settings, streams=1, seconds=30)
    assert report["passed"] is True, report.get("summary")
    nothing_left()


async def test_de_cli_zonder_beheerder_of_met_media_uit(lt, monkeypatch):
    settings, _ = lt
    with pytest.raises(Exception) as caught:
        await loadtest.run_cli(settings, streams=1, seconds=30)
    assert caught.value.detail["code"] == "admin_only"  # type: ignore[attr-defined]
    off = settings.model_copy(update={"media_enabled": False})
    with pytest.raises(Exception) as caught:
        await loadtest.run_cli(off, streams=1, seconds=30)
    assert caught.value.detail["code"] == "media_off"  # type: ignore[attr-defined]


def test_de_cli_zonder_media(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        assert cli.main(["media-loadtest", "--sessies", "1", "--duur", "30"]) == 1
    finally:
        get_settings.cache_clear()
    assert "media_off" in capsys.readouterr().err
