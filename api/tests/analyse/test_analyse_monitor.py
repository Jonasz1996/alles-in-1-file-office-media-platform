"""Hoe de app merkt of de analyse er is (fase 6, A0; Keuze 59, §3.11): de vier standen en hun
redenen, analyse_problem() in het Nederlands, de lus (30 s, bij down vaker), Defer(300), Defer(30)
en Defer(3600) zonder poging, de lifespan en app.cli analyse-status.

Zonder PLATFORM_ANALYSE_ENABLED is de stand off: geen taak, geen socket, geen sleutel."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import sys
import time
from datetime import timedelta
from pathlib import Path

import anyio
import pytest

from app import cli, jobs, main
from app.analyse import client, lock, monitor, wire
from app.analyse.wire import Child, Code, Op
from app.config import Settings, get_settings
from app.main import app
from app.models import Job
from app.routers import uploads
from tests.analyse.fake_worker import FakeAnalyseWorker

pytestmark = pytest.mark.anyio

API = Path(__file__).resolve().parents[2]
YUNET = "face_detection_yunet_2026may.onnx"
SFACE = "face_recognition_sface_2021dec.onnx"


def _off(tmp_path: Path, **extra) -> Settings:
    return Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8, **extra)


async def until(cond, limit: float = 5.0) -> None:
    end = time.monotonic() + limit
    while not cond():
        assert time.monotonic() < end, "niet op tijd"
        await asyncio.sleep(0.01)


# --- off ---


async def test_off_zonder_taak_socket_of_sleutel(fake, tmp_path, monkeypatch):
    """PLATFORM_ANALYSE_ENABLED uit: de nep luistert op de socket, maar niemand komt."""

    def nooit(*args, **kwargs):
        raise AssertionError("met de analyse uit hoort dit nooit te gebeuren")

    monkeypatch.setattr(client, "_connect", nooit)
    monkeypatch.setattr(Settings, "analyse_key_problem", nooit)
    settings = fake.settings(tmp_path, analyse_enabled=False)
    assert monitor.start(settings) is None
    assert monitor._task is None
    assert monitor.status() is monitor.OFF
    assert await monitor.check(settings) is monitor.OFF
    await asyncio.sleep(monitor.FIRST + 0.1)
    assert fake.hellos == 0
    assert monitor.analyse_problem() == "de analyse staat uit (PLATFORM_ANALYSE_ENABLED)"
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "analyse-monitor"]
    await monitor.stop()
    assert monitor.status() is monitor.OFF


def test_de_instellingen_van_311(monkeypatch):
    for name in [n for n in os.environ if n.startswith(("PLATFORM_ANALYSE", "PLATFORM_FACES"))]:
        monkeypatch.delenv(name)
    settings = Settings()
    assert settings.analyse_enabled is False
    assert settings.analyse_worker == Path("/run/analyse/worker.sock")
    assert settings.analyse_worker_key == ""
    assert settings.faces_keys == ""
    assert (
        settings.faces_batch,
        settings.faces_min_px,
        settings.faces_max_per_photo,
        settings.faces_group_max,
    ) == (16, 40, 40, 60000)
    assert jobs.active_lanes(settings).count("analyse") == 0


def test_de_sleutel_staat_nooit_in_repr(tmp_path):
    key = "a" * 64
    settings = _off(tmp_path, analyse_worker_key=key)
    assert key not in repr(settings) and key not in str(settings)


# --- down, met elke reden ---


async def test_down_zonder_socket(tmp_path):
    found = await monitor.check(
        _off(
            tmp_path,
            analyse_enabled=True,
            analyse_worker_key="k" * 64,
            analyse_worker=tmp_path / "nergens.sock",
        )
    )
    assert (found.state, found.reason) == ("down", "no_socket")


async def test_down_als_niemand_luistert(tmp_path):
    path = tmp_path / "dood.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.close()  # het bestand blijft, er luistert niemand
    settings = _off(
        tmp_path, analyse_enabled=True, analyse_worker_key="k" * 64, analyse_worker=path
    )
    found = await monitor.check(settings)
    assert (found.state, found.reason) == ("down", "refused")


@pytest.mark.parametrize(
    ("fault", "reason"),
    [("wrong_proof", "handshake"), ("media_proof", "handshake"), ("silent", "no_answer")],
)
async def test_down_bij_de_handdruk(fault, reason, fake, settings_fake, monkeypatch):
    monkeypatch.setattr(client, "HELLO_TIMEOUT", 0.2)
    fake.hello_fault = fault
    found = await monitor.check(settings_fake)
    assert (found.state, found.reason) == ("down", reason)


async def test_down_sandbox(fake, settings_fake):
    fake.refuse = Code.SANDBOX
    found = await monitor.check(settings_fake)
    assert (found.state, found.reason) == ("down", "sandbox")
    assert monitor.analyse_problem(found) == monitor.TEXT["sandbox"]


async def test_down_bij_een_caps_die_niet_klopt(fake, settings_fake):
    fake.caps = lambda: {"models": "kapot"}
    found = await monitor.check(settings_fake)
    assert (found.state, found.reason) == ("down", "bad_answer")


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ({"analyse_worker_key": ""}, "key_missing"),
        ({"analyse_worker_key": "k" * 31}, "key_missing"),
        ({"analyse_worker_key": "test" * 8}, "key_reused"),
        ({"analyse_worker_key": "m" * 64, "media_worker_key": "m" * 64}, "key_reused"),
    ],
)
async def test_down_door_de_sleutel(extra, reason, fake, tmp_path):
    """Een sleutel die niet deugt: down bij de start, zonder lus en zonder de socket."""
    settings = fake.settings(tmp_path, **extra)
    assert monitor.start(settings) is None
    assert monitor._task is None
    found = monitor.status()
    assert (found.state, found.reason) == ("down", reason)
    assert (await monitor.check(settings)).reason == reason
    assert fake.hellos == 0
    problem = monitor.analyse_problem()
    assert "PLATFORM_ANALYSE_WORKER_KEY" in problem
    if settings.analyse_worker_key:
        assert settings.analyse_worker_key not in problem


def test_elke_reden_heeft_een_nederlandse_tekst():
    assert set(monitor.TEXT) == set(client.REASONS) | {"bad_answer", "no_sqlite"}
    for reason, text in monitor.TEXT.items():
        found = monitor.AnalyseStatus("down", reason)
        assert monitor.analyse_problem(found) == text
        assert text and not text.endswith(".")
    assert monitor.analyse_problem(monitor.AnalyseStatus("down")) == "de analyse is niet bereikbaar"
    assert monitor.TEXT["no_answer"].endswith("binnen 5 s")


# --- models_missing en ok ---


async def test_models_missing(fake, settings_fake):
    fake.models[YUNET] = "ontbreekt"
    found = await monitor.check(settings_fake)
    assert (found.state, found.reason, found.missing) == ("models_missing", YUNET, (YUNET,))
    assert monitor.analyse_problem(found) == f"een model ontbreekt of klopt niet: {YUNET}"
    # Groeperen heeft geen model nodig; SFace is er wel, YuNet niet
    assert found.ready() and found.ready([SFACE])
    assert not found.ready([YUNET, SFACE])
    fake.models[SFACE] = "anders"
    found = await monitor.check(settings_fake)
    assert found.reason == f"{YUNET}, {SFACE}"


async def test_ok(fake, settings_fake):
    fake.on(Op.FACES, _leeg)
    found = await monitor.check(settings_fake)
    assert (found.state, found.reason, found.missing) == ("ok", None, ())
    assert monitor.analyse_problem(found) is None
    assert found.ready([m.name for m in lock.load()])
    assert found.built(Op.CAPS) and found.built(Op.FACES) and not found.built(Op.RANK)
    assert not monitor.OFF.ready() and not monitor.AnalyseStatus("down", "refused").ready()


# --- Defer zonder poging ---


def test_wachten_voor_een_opdracht():
    for found in (monitor.OFF, monitor.AnalyseStatus("down", "no_socket")):
        assert monitor.wait((), found).seconds == monitor.DEFER_DOWN == 300
    missing = monitor.AnalyseStatus("models_missing", YUNET, (YUNET,))
    assert monitor.wait([YUNET], missing).seconds == monitor.DEFER_MODEL == 3600
    assert monitor.wait([SFACE], missing) is None
    assert monitor.wait((), monitor.AnalyseStatus("ok")) is None
    assert monitor.wait() is not None  # de stand van nu: off


def test_wachten_na_een_fout():
    assert monitor.deferral(client.AnalyseDown("refused")).seconds == 300
    assert monitor.deferral(client.AnalyseBusy("werk")).seconds == monitor.DEFER_BUSY == 30
    assert monitor.deferral(client.AnalyseFailed("model_missing")).seconds == 3600
    for exc in (
        client.AnalyseFailed("timeout"),
        client.AnalyseFailed("unreadable"),
        ValueError("x"),
    ):
        assert monitor.deferral(exc) is None
    assert isinstance(monitor.deferral(client.AnalyseBusy("werk")), jobs.Defer)


async def test_een_job_wacht_zonder_poging(settings, tmp_path, monkeypatch):
    """Een taak in de rij analyse: down is Defer(300), busy Defer(30), en geen van beide telt
    als poging (attempts blijft 0, ook met max_attempts 1)."""
    async with FakeAnalyseWorker(tmp_path / "run" / "w.sock") as fake:
        fake.on(Op.FACES, _leeg)
        fake.busy.add(Child.WERK)
        analyse = fake.settings(settings.data_dir)

        async def run(_settings, payload):
            if payload["soort"] == "down":
                monitor._status = monitor.AnalyseStatus("down", "no_socket")
            else:
                monitor._status = monitor.AnalyseStatus("ok")
            if (later := monitor.wait()) is not None:
                raise later
            fds = _previews()
            try:
                await client.call(analyse, Op.FACES, wire.FacesArgs(), fds=fds)
            except Exception as exc:
                if (later := monitor.deferral(exc)) is not None:
                    raise later from None
                raise
            finally:
                wire.close_all(fds)

        monkeypatch.setitem(jobs._kinds, "test.analyse", jobs.Kind(run, None, 1, "analyse"))
        maker = jobs._maker(settings)
        async with maker() as session:
            down = await jobs.enqueue(session, "test.analyse", {"soort": "down"})
            busy = await jobs.enqueue(session, "test.analyse", {"soort": "busy"})
            await session.commit()
        before = jobs.now()
        assert await jobs.run_pending(settings, lane="analyse") == 2
        async with maker() as session:
            for job_id, seconds in ((down, 300), (busy, 30)):
                job = await session.get(Job, job_id)
                assert (job.status, job.attempts, job.error) == ("queued", 0, None)
                run_after = job.run_after.replace(tzinfo=before.tzinfo)
                assert before + timedelta(seconds=seconds - 1) <= run_after
                assert run_after <= jobs.now() + timedelta(seconds=seconds + 1)
        assert [s.code for s in fake.requests] == [Code.BUSY]


# --- de lus ---


async def test_de_lus_en_het_log(tmp_path, monkeypatch, caplog):
    pauses: list[float] = []
    go: asyncio.Queue = asyncio.Queue()

    async def pause(seconds: float) -> None:
        pauses.append(seconds)
        await go.get()

    async def step() -> None:
        n = len(pauses)
        go.put_nowait(None)
        await until(lambda: len(pauses) > n)

    monkeypatch.setattr(monitor, "_pause", pause)
    caplog.set_level(logging.INFO, logger="platform.analyse")
    fake = FakeAnalyseWorker(tmp_path / "run" / "worker.sock")
    task = monitor.start(fake.settings(tmp_path))
    assert task is not None and task.get_name() == "analyse-monitor"
    try:
        await until(lambda: pauses)
        for _ in range(7):
            await step()
        assert pauses == [monitor.FIRST, 1, 2, 4, 8, 16, 30, 30]
        assert (monitor.status().state, monitor.status().reason) == ("down", "no_socket")
        await fake.start()
        await step()
        assert monitor.status().state == "ok" and pauses[-1] == monitor.INTERVAL
        fake.models[SFACE] = "ontbreekt"
        await step()
        assert monitor.status().state == "models_missing" and pauses[-1] == monitor.INTERVAL
        await fake.stop()
        await step()
        await step()
        assert monitor.status().state == "down" and pauses[-2:] == [1, 2]
    finally:
        await monitor.stop()
        await fake.stop()
    assert task.done() and monitor.status() is monitor.OFF and monitor._task is None
    lines = [r.getMessage() for r in caplog.records if r.name == "platform.analyse"]
    assert lines == [
        "analyse down: " + monitor.TEXT["no_socket"],
        "analyse bereikbaar (was down)",
        f"analyse models_missing: een model ontbreekt of klopt niet: {SFACE}",
        "analyse down: " + monitor.TEXT["no_socket"],
    ]
    assert all(fake.key not in line for line in lines)


async def test_de_lus_stopt_nooit_bij_een_fout(tmp_path, monkeypatch, caplog):
    calls = []

    async def kapot(settings):
        calls.append(1)
        raise RuntimeError("stuk")

    async def pause(seconds: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(monitor, "check", kapot)
    monkeypatch.setattr(monitor, "_pause", pause)
    task = monitor.start(FakeAnalyseWorker(tmp_path / "w.sock").settings(tmp_path))
    await until(lambda: len(calls) >= 3)
    assert not task.done()
    assert (monitor.status().state, monitor.status().reason) == ("down", "bad_answer")
    await monitor.stop()


# --- de lifespan ---


@contextlib.asynccontextmanager
async def started(tmp_path, monkeypatch, **env):
    """De lifespan van de app met deze omgeving (zoals tests/test_rate_limits.py)."""
    for name in [n for n in os.environ if n.startswith("PLATFORM_ANALYSE")]:
        monkeypatch.delenv(name)
    for name, value in {
        "PLATFORM_DATA_DIR": str(tmp_path),
        "PLATFORM_WEB_DIST": str(tmp_path / "web"),
        "PLATFORM_JWT_SECRET": "test" * 8,
        **env,
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    async def idle(_settings):
        await asyncio.sleep(3600)

    monkeypatch.setattr(uploads, "cleanup_loop", idle)
    try:
        async with main.lifespan(app):
            yield
    finally:
        get_settings.cache_clear()
        for name in ("certs", "security"):
            with contextlib.suppress(AttributeError, KeyError):
                delattr(app.state, name)


async def test_de_lifespan_zonder_analyse(fake, tmp_path, monkeypatch):
    async with started(tmp_path, monkeypatch, PLATFORM_ANALYSE_WORKER=str(fake.socket)):
        assert monitor._task is None and monitor.status() is monitor.OFF
        await asyncio.sleep(monitor.FIRST + 0.2)
        assert fake.hellos == 0
        assert not [t for t in asyncio.all_tasks() if t.get_name() == "analyse-monitor"]
    assert monitor.status() is monitor.OFF


async def test_de_lifespan_met_analyse(fake, tmp_path, monkeypatch):
    env = {
        "PLATFORM_ANALYSE_ENABLED": "true",
        "PLATFORM_ANALYSE_WORKER": str(fake.socket),
        "PLATFORM_ANALYSE_WORKER_KEY": fake.key,
    }
    async with started(tmp_path, monkeypatch, **env):
        task = monitor._task
        assert task is not None
        await until(lambda: monitor.status().state == "ok")
        assert fake.hellos >= 1
    assert task.done() and monitor._task is None and monitor.status() is monitor.OFF


# --- app.cli analyse-status ---


async def run_cli(tmp_path: Path, *args: str, **env: str):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_ANALYSE")}
    full = {
        **clean,
        "PLATFORM_DATA_DIR": str(tmp_path),
        "PLATFORM_JWT_SECRET": "test" * 8,
        "PYTHONDONTWRITEBYTECODE": "1",
        **env,
    }
    return await anyio.run_process(
        [sys.executable, "-m", "app.cli", "analyse-status", *args],
        cwd=API,
        env=full,
        check=False,
    )


async def test_cli_als_proces(fake, tmp_path):
    """python -m app.cli analyse-status, met de sleutel uit de omgeving (nooit in argv)."""
    result = await run_cli(
        tmp_path,
        PLATFORM_ANALYSE_ENABLED="true",
        PLATFORM_ANALYSE_WORKER=str(fake.socket),
        PLATFORM_ANALYSE_WORKER_KEY=fake.key,
    )
    assert (result.returncode, result.stdout.decode().strip()) == (0, "Analyse: ok")
    assert fake.hellos == 1
    assert fake.key not in (result.stdout + result.stderr).decode()


async def _status(settings: Settings, capsys, wait: float = 0.0) -> tuple[int, str]:
    """cli._analyse_status in een thread (hij doet zelf asyncio.run); de nep draait hier."""
    code = await anyio.to_thread.run_sync(cli._analyse_status, settings, wait)
    return code, capsys.readouterr().out.strip()


async def test_cli_de_standen(fake, tmp_path, capsys):
    off = await _status(fake.settings(tmp_path, analyse_enabled=False), capsys)
    assert off == (0, "Analyse: off (de analyse staat uit (PLATFORM_ANALYSE_ENABLED))")
    assert fake.hellos == 0

    fake.models[YUNET] = "anders"
    missing = await _status(fake.settings(tmp_path), capsys)
    assert missing == (1, f"Analyse: models_missing (een model ontbreekt of klopt niet: {YUNET})")

    short = await _status(fake.settings(tmp_path, analyse_worker_key="kort"), capsys)
    assert short == (1, "Analyse: down (" + monitor.TEXT["key_missing"] + ")")

    fake.refuse = Code.SANDBOX
    sandbox = await _status(fake.settings(tmp_path), capsys)
    assert sandbox == (1, "Analyse: down (" + monitor.TEXT["sandbox"] + ")")
    assert all(fake.key not in line for _, line in (off, missing, short, sandbox))


async def test_cli_wacht_en_geeft_dan_0(fake, tmp_path, capsys):
    """Met --wait: hooguit zoveel seconden opnieuw kijken, en exitcode 0 (zoals media-status)."""
    nergens = fake.settings(tmp_path, analyse_worker=tmp_path / "nergens.sock")
    started_at = time.monotonic()
    result = await _status(nergens, capsys, wait=1.5)
    assert result == (0, "Analyse: down (" + monitor.TEXT["no_socket"] + ")")
    assert time.monotonic() - started_at >= 1.5

    # Komt hij tijdens het wachten, dan stopt het wachten meteen
    async def later():
        await asyncio.sleep(0.5)
        await moved.start()

    moved = FakeAnalyseWorker(tmp_path / "later" / "worker.sock", key=fake.key)
    started_at = time.monotonic()
    async with anyio.create_task_group() as tg:
        tg.start_soon(later)
        result = await _status(
            fake.settings(tmp_path, analyse_worker=moved.socket), capsys, wait=20
        )
    await moved.stop()
    assert result == (0, "Analyse: ok")
    assert time.monotonic() - started_at < 5


# --- hulpjes ---


async def _leeg(req):
    return wire.FacesData(photos=[wire.FacesPhoto(code="ok") for _ in req.previews])


def _previews() -> list[int]:
    return [wire.sealed_memfd("p", b"x"), wire.out_memfd("uit")]


PG = "postgresql+asyncpg://u:p@127.0.0.1:1/x"


async def test_op_postgresql_blijft_de_analyse_uit(fake, tmp_path):
    """Review A0, veilig F5: op PostgreSQL maken 0011 en 0012 alleen de tabellen, zonder de
    triggers die gezichten en beeld wissen. Daar blijft de analyse dus uit, ook met
    PLATFORM_ANALYSE_ENABLED: down met no_sqlite, zonder taak, socket of sleutel, en de rij
    analyse draait niet."""
    settings = fake.settings(tmp_path, database_url=PG)
    assert settings.analyse_enabled and not settings.analyse_on
    assert monitor.start(settings) is None
    assert monitor._task is None
    assert (monitor.status().state, monitor.status().reason) == ("down", "no_sqlite")
    assert monitor.analyse_problem() == monitor.TEXT["no_sqlite"]
    assert (await monitor.check(settings)).reason == "no_sqlite"
    assert "analyse" not in jobs.active_lanes(settings)
    # Ook zonder sleutel: no_sqlite, de sleutel wordt niet gelezen
    bare = fake.settings(tmp_path, database_url=PG, analyse_worker_key="")
    assert (await monitor.check(bare)).reason == "no_sqlite"
    assert fake.hellos == 0
    # Met SQLite gewoon aan
    sqlite = fake.settings(tmp_path)
    assert sqlite.analyse_on and "analyse" in jobs.active_lanes(sqlite)
    assert (await monitor.check(sqlite)).state == "ok"
