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
import gc
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import update

from app import cli
from app.config import MediaConfig, get_settings
from app.main import app
from app.media import admin as media_admin
from app.media import analysis, loadtest, runtime, sessions, streams
from app.media.state import MediaPolicy, MediaState
from app.media.wire import Code, Op
from app.models import MediaProbe, Node
from app.routers import files as files_router
from tests.media import fake_worker as FW
from tests.media.media_contract import failed
from tests.test_files import put, root
from tests.test_media_contract import WAIT_S, source_probe
from tests.test_shares import person

pytestmark = pytest.mark.anyio

BASE = "/api/v1/admin/media/loadtest"
FAST = loadtest.Timing(stagger_s=1.0, seek_at_s=2.0, seek_jump_s=3.0, tick_s=0.02, scale=0.1)


def asgi_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def frozen_heap():
    """De heap van dit testproces gaat in gc.freeze zolang de belastingtest loopt (zoals _timed
    in test_media_subconv). In de hele api-set heeft het proces dan ruim een miljoen objecten van
    eerdere tests; een volle ronde van de garbage collector daarover kost tot 0,94 s (gemeten met
    een spion op gc.callbacks), en viel die binnen een gemeten vraag van fase a, dan zag de test
    "de API 1030 ms (p95)". Dat is de test, niet de app: de echte app heeft die objecten niet. Wat
    de app tijdens de run zelf aan afval maakt, ruimt de collector nog steeds op, en de grens van
    200 ms blijft."""
    gc.collect()
    gc.freeze()
    yield
    gc.unfreeze()


@pytest.fixture
async def lt(frozen_heap, settings, client, monkeypatch, tmp_path):
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
        return original(app_, settings, user, data, **kwargs, http=asgi_http, timing=timing)

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
    for player in view["report"]["phases"]["b"]["players"]:
        assert player["stall_at"] == player["stall_after_seek"] == []  # niet gehaperd (de JSON)
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


# --- met echte films (sources, §4.6 punt 1) ------------------------------------------------------


async def film(client, headers, settings, name: str) -> dict[str, Any]:
    """Een film van deze gebruiker, met een opgeslagen probe die transcode vraagt (HEVC) en een
    keyframe-index: zoals een film in een bibliotheek na de analyse."""
    from app.routers.media_play import maker

    top = (await root(client, headers))["folder"]["id"]
    r = await put(client, headers, top, name, b"\x1aE\xdf\xa3" + name.encode() * 5000)
    assert r.status_code == 201, r.text
    node = r.json()
    probe = source_probe("transcode", "h264")
    probe.keyframes_state, probe.max_gop_ms = "ready", 2000
    async with maker(settings)() as session:
        sha = (await session.get(Node, uuid.UUID(node["id"]))).blob_id
    await analysis.save_probe(maker(settings), sha, probe)
    async with maker(settings)() as session:
        await session.execute(
            update(MediaProbe)
            .where(MediaProbe.blob_sha == sha)
            .values(keyframes_state="ready", max_gop_ms=2000)
        )
        await session.commit()
    return node


async def test_met_sources_speelt_hij_de_films_van_de_beheerder(lt, client, admin, monkeypatch):
    """Review van de integratie, K1: sources werd aangenomen maar niet gebruikt; de test draaide
    altijd op zijn eigen bronnen. Nu speelt elke speler een film van de beheerder (speler i film
    i modulo het aantal), synth maakt niets, en de sessies zijn gewone sessies op de node (met de
    controle van play) die na afloop dichtgaan."""
    settings, fake = lt
    headers, _ = admin
    one = await film(client, headers, settings, "Een film (2020).mkv")
    seen: list[tuple[Any, bool, bool]] = []
    create = sessions.create

    async def spy(user, source, *args, **kwargs):
        s = await create(user, source, *args, **kwargs)
        seen.append((s.node_id, s.loadtest, loadtest.ours(s)))
        return s

    # na een fase, vóór het opruimen: de omzettingen van de test tellen niet als echte kijkers
    # (anders weigerde /beheer een meting om de test zelf) en /beheer noemt ze belastingtest
    during: list[tuple[int, int]] = []
    judge = loadtest.judge

    def judging(*args, **kwargs):
        live = [st for st in streams.all() if st.plan.mode == "transcode" and not st.closed]
        during.append((len(live), media_admin.transcodes_running()))
        return judge(*args, **kwargs)

    monkeypatch.setattr(sessions, "create", spy)
    monkeypatch.setattr(loadtest, "judge", judging)
    views = media_admin.session_views
    titles: list[str | None] = []

    async def viewing(db):
        out = await views(db)
        titles.extend(v.title for v in out if v.node_id is not None)
        return out

    monkeypatch.setattr(media_admin, "session_views", viewing)
    before = media_admin.reasons.view()
    await start(client, headers, streams=2, seconds=30, sources=[one["id"], one["id"]])
    while loadtest.running() and not titles:  # het overzicht van /beheer terwijl hij loopt
        await client.get("/api/v1/admin/media", headers=headers)
        await asyncio.sleep(0.05)
    report = await finish()
    assert fake.made == []  # geen eigen bronnen
    assert report["sources"] == ["Een film (2020).mkv", "Een film (2020).mkv"]
    assert report["films"] == [one["id"]]
    b = report["phases"]["b"]
    assert b["passed"], b["problems"]
    assert [p["source"] for p in b["players"]] == report["sources"]
    assert all(p["mode"] == "transcode" and not p["error"] for p in b["players"])
    assert seen and all(
        node == uuid.UUID(one["id"]) and not test and mine for node, test, mine in seen
    )
    assert during and all(live > 0 and real == 0 for live, real in during)
    assert titles and set(titles) == {"belastingtest"}
    assert media_admin.reasons.view() == before  # telt niet mee in de redenen per dag
    assert not list(sessions.table().open())  # ook de sessies op de film zijn dicht
    assert not loadtest._families  # de family van de run is weer weg
    assert not list(streams.all())
    assert not loadtest.loadtest_dir(settings).exists()  # niets gemaakt


async def test_sources_alleen_eigen_films_en_een_per_sessie(lt, client, admin):
    settings, fake = lt
    headers, _ = admin
    mine = await film(client, headers, settings, "mijn.mkv")
    bert, _ = await person(client, headers, "bert@example.nl")
    theirs = await film(client, bert, settings, "van-bert.mkv")
    for sources in ([theirs["id"]], [str(uuid.uuid4())], [mine["id"], theirs["id"]]):
        body = {"streams": 2, "seconds": 30, "sources": sources}
        failed(await client.post(BASE, json=body, headers=headers), 404, "not_found")
    two = await film(client, headers, settings, "nog-een.mkv")
    body = {"streams": 1, "seconds": 30, "sources": [mine["id"], two["id"]]}
    r = await client.post(BASE, json=body, headers=headers)
    assert failed(r, 422, "invalid_input") == {"streams": 1}
    await client.delete(f"/api/v1/files/{two['id']}", headers=headers)  # naar de prullenbak
    body = {"streams": 1, "seconds": 30, "sources": [two["id"]]}
    failed(await client.post(BASE, json=body, headers=headers), 404, "not_found")
    assert (await client.get(BASE, headers=headers)).json()["state"] == "idle"
    assert fake.made == []


async def test_een_film_zonder_probe_wacht_op_de_probe(lt, client, admin, monkeypatch):
    """Na-controle van de review (K1): een film zonder opgeslagen probe (nog nooit gespeeld,
    niet in een bibliotheek) krijgt bij de start 409 media_probe_pending als de probe langer
    duurt dan het budget van play. De speler in de app vraagt het dan na Retry-After opnieuw,
    zoals de speler in de browser en die buiten de app (fase c); eerder faalde hij meteen
    ("start: 409") en zakte de fase voor een probe die gewoon doorliep."""
    from app.routers import media_play

    settings, fake = lt
    headers, _ = admin
    top = (await root(client, headers))["folder"]["id"]
    r = await put(client, headers, top, "nieuw.mkv", b"\x1aE\xdf\xa3" + b"nieuw" * 5000)
    assert r.status_code == 201, r.text
    probe = source_probe("transcode", "h264")
    probed: list[str] = []

    async def slow(settings_, sha):  # de worker doet er langer over dan het budget
        probed.append(sha)
        await asyncio.sleep(0.3)
        return probe

    monkeypatch.setattr(analysis, "probe_blob", slow)
    monkeypatch.setattr(media_play, "PROBE_BUDGET", 0.05)
    await start(client, headers, streams=1, seconds=30, sources=[r.json()["id"]])
    report = await finish()
    b = report["phases"]["b"]
    assert [p["error"] for p in b["players"]] == [None], b["players"]
    assert b["passed"], b["problems"]
    assert b["players"][0]["mode"] == "transcode"
    assert len(probed) == 1  # één probe: de tweede poging wachtte op dezelfde
    assert not list(sessions.table().open()) and not loadtest._families


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


async def test_play_buiten_de_app_als_de_worker_druk_is(lt, client, admin, monkeypatch):
    """De metingen (fase c): drie spelers tegelijk vragen drie probes, de worker heeft 2
    plaatsen "kort". De derde kreeg een 500 (WorkerBusy kwam niet door de catalogus); nu 503
    media_busy {reason: worker} met Retry-After, zoals play (M3)."""
    from app.media import client as worker_client
    from app.routers import admin_media

    headers, _ = admin
    monkeypatch.setattr(admin_media, "_making", {})  # niet die van een eerdere test (andere loop)
    body = {"source": 0, "seconds": 30, "player": str(uuid.uuid4())}
    r = await client.post(f"{BASE}/play", json=body, headers=headers)
    failed(r, 409, "media_probe_pending", "loadtest")
    await asyncio.wait_for(asyncio.gather(*admin_media._making.values()), 10)

    async def busy(source):
        raise worker_client.WorkerBusy("short")

    monkeypatch.setattr(loadtest, "probe_source", busy)
    r = await client.post(f"{BASE}/play", json=body, headers=headers)
    failed(r, 503, "media_busy", "worker")
    assert r.headers["retry-after"] == "2"
    assert not [s for s in sessions.table().open() if s.loadtest]


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
        self.lost = "410"  # wat de speler bij de herstart ziet: 410, 502 of geen verbinding
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
            if self.lost == "connect":
                raise httpx.ConnectError("de app is weg", request=request)
            return httpx.Response(int(self.lost))
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
    # Wanneer (I1, M7): één tijdstip per keer haperen, zonder sprong None. Na de 6 s van segment
    # 0 tot 2 (de plek loopt nooit voor op de klok; gemeten 6,3) en ruim vóór segment 3 er is
    # (na 1,5 s: 15 s in de tijd van de test, scale 0,1)
    assert len(r.stall_at) == r.stalls == len(r.stall_after_seek)
    assert r.stall_after_seek == [None]
    assert 6.0 <= r.stall_at[0] < 10.0
    assert found["players"][0]["stall_after_seek"] == [None]


async def test_de_speler_weet_wanneer_hij_hapert_na_de_sprong():
    """Fase a (I1, M7): haperen na de sprong. De sprong van 2 naar 42 s wacht 0,3 s op segment
    21, en segment 23 komt pas 1 s na de sprong; de speler (10× sneller) speelt het restje van 21
    en heel 22 op en staat dan stil. De start wacht 0,5 s op segment 0. stall_after_seek telt
    vanaf het eind van de sprong, stall_at vanaf het begin van het spelen (niet vanaf de start);
    beide in summary() en in de JSON van het rapport. judge telt het zoals altijd."""
    fake = FakeApp(count=40)  # 80 s: met hooguit 30 s vooruit vraagt hij 21 en 23 pas na de sprong
    fake.slow.update({0: 0.5, 21: 0.3, 23: 1.0})
    timing = loadtest.Timing(seek_at_s=2.0, seek_jump_s=40.0, tick_s=0.02, scale=0.1)
    p = player(fake, timing)
    # zoals _phase het fase a meegeeft
    await p.run(20, seek_at=timing.s(timing.seek_at_s), jump_ms=int(timing.seek_jump_s * 1000))
    r = p.report
    assert r.error is None and r.seek_s is not None and "s1/21.m4s" in fake.gets
    assert r.start_s is not None and r.start_s >= 0.5 and r.seek_s >= 0.3  # echte seconden
    assert len(r.stall_at) == r.stalls == len(r.stall_after_seek) == 1
    [after] = r.stall_after_seek
    # De plek loopt nooit voor op de klok: van ~42 s tot 46 s (het restje van 21 en heel 22) is
    # ~4 s (gemeten 4,3); een late tik bij de sprong (hooguit 0,2 s, 2 s in de tijd van de test)
    # schuift het doel op. En vóór segment 23 er is (1 s: 10 s). Zonder / scale: ~0,4
    assert after is not None and 2.0 <= after < 10
    # stall_at - after: van het begin van het spelen tot het eind van de sprong. De 2 s tot de
    # sprong en de sprong zelf (seek_s: echte seconden, dus / scale) zitten erin, de start niet
    # (5 s); 2 s ruimte voor die late tik. Vanaf t0 of vanaf de start valt het erbuiten
    before = r.stall_at[0] - after
    seek = r.seek_s / timing.scale
    assert timing.seek_at_s + seek - 0.01 <= before < timing.seek_at_s + seek + 2.0
    out = r.summary()
    assert out["stall_at"] == [round(r.stall_at[0], 2)]
    assert out["stall_after_seek"] == [round(after, 2)]
    found = json.loads(json.dumps(loadtest.judge("a", [r], None)))
    assert found["problems"] == ["1 keer haperen"]
    [player_json] = found["players"]
    assert player_json["stall_at"] == out["stall_at"]
    assert player_json["stall_after_seek"] == out["stall_after_seek"]


async def test_de_speler_telt_niet_meer_dan_er_binnen_is():
    """Een tik die laat komt (een volle GC-ronde, een drukke runner): de plek gaat hooguit tot
    wat binnen is, en de gespeelde tijd telt alleen wat de plek echt opschoof. Eerder telde
    played_s de hele tik, zodat test_de_speler_telt_haperen in de hele set 10,34 s zag."""
    fake = FakeApp(count=1)  # één segment van 2 s, binnen na de start
    times = iter([0.0, 0.0, 0.0, 0.5, 5.0])  # begin, start_s, played_from; 0,5 s; dan 4,5 s laat
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle), base_url="http://x")
    report = loadtest.PlayerReport(name="c1", phase="c", source="0")
    p = loadtest.Player(
        http,
        fake.open,
        report,
        timing=loadtest.Timing(tick_s=0.001, scale=1.0),
        clock=lambda: next(times, 5.0),
    )
    await p.run(60)
    assert report.error is None and report.stalls == 0
    assert p.pos == 2000
    assert report.played_s == pytest.approx(2.0)


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


@pytest.mark.parametrize("lost", ["connect", "502"])
async def test_de_speler_overleeft_een_app_die_weg_is(monkeypatch, lost):
    """De metingen (fase c): bij een echte herstart is de app even weg. Geen verbinding (de
    sandbox) of 502 van de proxy (de LXC) stopte het ophalen zonder dat de speler het merkte:
    hij speelde zijn buffer op en bleef dan stil staan, zonder nieuwe sessie. Nu net als 410."""
    monkeypatch.setattr(loadtest, "OPEN_RETRY_S", 0.05)
    fake = FakeApp(count=8)
    fake.restart_after = 4
    fake.lost = lost
    p = player(fake, loadtest.Timing(tick_s=0.02, scale=0.25))
    await asyncio.wait_for(p.run(60), 30)
    r = p.report
    assert r.error is None and fake.sessions == 2
    [restart] = r.restarts
    assert restart["na_ready_s"] < 1
    assert r.played_s > 15  # de hele bron (16 s), ook na de herstart


async def test_spelers_buiten_de_app_wachten_op_retry_after(monkeypatch):
    """De metingen (fase c): de derde speler kreeg bij de start 503 media_busy (de worker had
    geen plaats "kort" voor zijn probe) en gaf op. Nu probeert hij het na Retry-After opnieuw,
    zoals de speler in de browser."""
    fake = FakeApp(count=4)
    plays: list[int] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/admin/media/loadtest/play":
            plays.append(1)
            if len(plays) == 3:
                detail = {"code": "media_busy", "message": "druk", "params": {"reason": "worker"}}
                return httpx.Response(503, json={"detail": detail}, headers={"Retry-After": "0.1"})
            out = await fake.open(0, None)
            return httpx.Response(200, json=out.model_dump())
        return await fake.handle(request)

    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        return real(*args, **kwargs)

    monkeypatch.setattr(loadtest.httpx, "AsyncClient", factory)
    monkeypatch.setattr(loadtest, "TIMING", loadtest.Timing(tick_s=0.02, scale=0.1))
    report = await asyncio.wait_for(
        loadtest._external(get_settings(), "http://x", "t", streams=3, seconds=30, codec="h264"),
        30,
    )
    players = report["phases"]["c"]["players"]
    assert [p["error"] for p in players] == [None, None, None], players
    assert len(plays) == 4 and report["passed"] is True, report["summary"]


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


async def test_de_api_vraagt_de_eerste_pagina_zoals_de_web_app():
    """Watch.sample vraagt van de hoofdmap de eerste pagina (FIRST_PAGE), zoals de web-app een
    map opent, en nooit de hele lijst: met 5.000 bestanden in de hoofdmap kostte die de app elke
    seconde ±220-320 ms, ten koste van de spelers en van elke vraag ernaast (I1, M7)."""
    seen: list[str] = []
    watch: loadtest.Watch

    class Http:
        async def get(self, path: str, **_kw: Any) -> httpx.Response:
            seen.append(path)
            if len(seen) == len(loadtest.API_PATHS):
                watch.stop.set()
            return httpx.Response(200)

    app_ = SimpleNamespace(state=SimpleNamespace())
    watch = loadtest.Watch(app_, None, Http(), "token", FAST)
    await asyncio.wait_for(watch.sample(), 5)
    assert seen == [f"/api/v1/files/root?limit={loadtest.FIRST_PAGE}", "/api/v1/media/home"]
    assert len(watch.samples.api_ms) == 2


def test_de_eerste_pagina_is_die_van_de_web_app():
    web = (Path(__file__).resolve().parents[2] / "web" / "src" / "browser" / "api.ts").read_text()
    assert f"export const FIRST_PAGE = {loadtest.FIRST_PAGE};" in web
    assert loadtest.FIRST_PAGE <= files_router.MAX_PAGE


async def test_de_api_paden_antwoorden_200(lt, client, admin):
    """Watch.sample telt de tijd van elk antwoord, ook van een 422: de paden moeten de echte
    routes zijn, met een limit die de app aanneemt (I1, M7)."""
    headers, _ = admin
    for path in loadtest.API_PATHS:
        r = await client.get(path, headers=headers)
        assert r.status_code == 200, (path, r.text[:200])


def test_het_profiel_vraagt_transcode():
    profile = loadtest.profile_for("h264", ["hvc1.2.4.L120.90", "avc1.64001f"], short=True)
    assert profile["max"] == {"height": 480, "bitrate_kbps": 4000}
    assert profile["codecs"]["avc1.64001f"]["mse"] is True
    # nooit als bestand: een film van sources (K1) speelt anders direct, zonder segmenten
    assert not any(c["file"] for c in profile["codecs"].values())
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


async def test_de_cli_met_een_film(lt, client, admin, via_asgi, monkeypatch):
    """--film geeft sources mee (K1); met --extern kan het niet (die spelers kennen alleen de
    bronnen van de test)."""
    settings, fake = lt
    headers, _ = admin
    one = await film(client, headers, settings, "cli.mkv")
    monkeypatch.setattr(loadtest, "TIMING", FAST)
    original = loadtest.start
    monkeypatch.setattr(loadtest, "start", lambda *a, **k: original(*a, **{**k, "timing": FAST}))
    films = [uuid.UUID(one["id"])]
    report = await loadtest.run_cli(settings, streams=1, seconds=30, sources=films)
    assert report["passed"] is True, report.get("summary")
    assert report["films"] == [one["id"]] and fake.made == []
    with pytest.raises(Exception) as caught:
        await loadtest.run_cli(settings, streams=1, seconds=30, sources=films, external="x")
    assert caught.value.detail["code"] == "invalid_input"  # type: ignore[attr-defined]


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
