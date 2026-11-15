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
VERSIE = {m.name: m.sha256[:12] for m in lock.load()}


def caps_regels(yunet: str = "klopt", built: str = "cancel, caps") -> list[str]:
    """De regels van analyse-status na de eerste (X2a, app/analyse/status.py) voor de nep: de
    twee modellen van models.lock, zonder grenzen."""
    return [
        f"  model {YUNET} (versie {VERSIE[YUNET]}): {yunet}",
        f"  model {SFACE} (versie {VERSIE[SFACE]}): klopt",
        "  grenzen: geen (de container heeft geen cgroup-grenzen)",
        f"  opdrachten: {built}",
    ]


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
    assert result.returncode == 0
    assert result.stdout.decode().splitlines() == ["Analyse: ok", *caps_regels()]
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
    anders = "klopt niet (een andere grootte of sha256, of geen gewoon bestand)"
    assert missing == (
        1,
        "\n".join(
            [
                f"Analyse: models_missing (een model ontbreekt of klopt niet: {YUNET})",
                *caps_regels(yunet=anders),
            ]
        ),
    )

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
    assert result == (0, "\n".join(["Analyse: ok", *caps_regels()]))
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


# --- de echte pid 1 (X2a): de standen tegen app/analyse/worker/server.py ---
#
# pid 1 draait hier in dit proces, zonder numpy (caps start geen kind). De modellen zijn twee
# bestanden met een eigen models.lock; de inhoud doet er voor caps niet toe, alleen de grootte en
# de sha256. De cgroup is een map met cpu.max en memory.max.


def _lock_regels(root: Path, files: dict[str, bytes]) -> Path:
    import hashlib

    root.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, data in files.items():
        (root / name).write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        lines.append(f"{sha} {len(data)} {name} https://example.invalid/{name}")
    path = root.parent / "models.lock"
    path.write_text("\n".join(lines) + "\n")
    return path


MODELLEN = {YUNET: b"yunet" * 1000, SFACE: b"sface" * 3000}


@contextlib.asynccontextmanager
async def echte_pid1(tmp_path: Path, *, refuse: str | None = None, cgroup: bool = True):
    from app.analyse.worker import server

    models = tmp_path / "modellen" / "m"
    lock_path = _lock_regels(models, MODELLEN)
    group = tmp_path / "cgroup"
    group.mkdir()
    if cgroup:
        (group / "cpu.max").write_text("150000 100000\n")
        (group / "memory.max").write_text(f"{3 * 1024**3}\n")
    config = server.Config(
        socket=tmp_path / "run" / "worker.sock", models=models, key="k" * 64,
        lock=lock_path, cgroup=group,
    )  # fmt: skip
    worker = server.Worker(config) if refuse is None else server.RefusingWorker(config, refuse)
    await worker.start()
    try:
        yield worker
    finally:
        await worker.stop()


def _echt(worker, tmp_path: Path, **extra) -> Settings:
    values: dict = {
        "data_dir": tmp_path,
        "web_dist": tmp_path / "web",
        "jwt_secret": "test" * 8,
        "analyse_enabled": True,
        "analyse_worker": worker.config.socket,
        "analyse_worker_key": worker.config.key,
    }
    values.update(extra)
    return Settings(**values)


async def test_echt_ok_met_modellen_grenzen_en_opdrachten(tmp_path):
    from app.analyse import state, status

    async with echte_pid1(tmp_path) as worker:
        found = await monitor.check(_echt(worker, tmp_path))
        assert (found.state, found.reason, found.missing) == ("ok", None, ())
        assert found.caps is not None
        assert found.caps.built == ["cancel", "caps", "faces", "group"]
        assert found.built(Op.FACES) and found.built(Op.GROUP) and not found.built(Op.RANK)
        shown = state.view(found, lock_path=worker.config.lock)
        versions = {m.name: m.sha256[:12] for m in lock.load(worker.config.lock)}
        assert [(m.name, m.version, m.sha_ok) for m in shown.models] == [
            (YUNET, versions[YUNET], True),
            (SFACE, versions[SFACE], True),
        ]
        assert (shown.limits.cpus, shown.limits.mem_mb) == (1.5, 3072)
        assert (shown.analyse, shown.reason, shown.problem) == ("ok", None, None)
        assert status.lines(found)[0] == "Analyse: ok"
        assert status.lines(found)[-2:] == [
            "  grenzen: 1.5 cpus, 3072 MB geheugen",
            "  opdrachten: cancel, caps, faces, group",
        ]


async def test_echt_een_model_ontbreekt_of_is_anders(tmp_path):
    from app.analyse import state

    async with echte_pid1(tmp_path) as worker:
        settings = _echt(worker, tmp_path)
        root = worker.config.models
        (root / SFACE).unlink()
        found = await monitor.check(settings)
        assert (found.state, found.reason, found.missing) == ("models_missing", SFACE, (SFACE,))
        assert found.ready([YUNET]) and not found.ready([YUNET, SFACE])
        assert monitor.wait([SFACE], found).seconds == monitor.DEFER_MODEL
        shown = state.view(found, lock_path=worker.config.lock)
        assert [(m.name, m.sha_ok) for m in shown.models] == [(YUNET, True), (SFACE, False)]
        assert shown.reason == SFACE

        # Andere bytes met dezelfde grootte: anders (de sha256 gaat over de bytes)
        (root / SFACE).write_bytes(b"x" * len(MODELLEN[SFACE]))
        assert (await monitor.check(settings)).missing == (SFACE,)
        assert [m.state for m in (await client.caps(settings)).models] == ["ok", "anders"]

        # Een symlink naar het goede bestand telt niet (O_NOFOLLOW)
        (root / SFACE).unlink()
        good = tmp_path / "elders"
        good.write_bytes(MODELLEN[SFACE])
        (root / SFACE).symlink_to(good)
        assert [m.state for m in (await client.caps(settings)).models] == ["ok", "anders"]

        # Terug: weer ok
        (root / SFACE).unlink()
        (root / SFACE).write_bytes(MODELLEN[SFACE])
        assert (await monitor.check(settings)).state == "ok"


async def test_echt_zonder_cgroup_geen_grenzen(tmp_path):
    from app.analyse import state, status

    async with echte_pid1(tmp_path, cgroup=False) as worker:
        found = await monitor.check(_echt(worker, tmp_path))
        assert found.state == "ok" and found.caps is not None and found.caps.limits is None
        assert state.view(found).limits is None
        assert "  grenzen: geen (de container heeft geen cgroup-grenzen)" in status.lines(found)


async def test_echt_down_sandbox_handdruk_en_geen_socket(tmp_path):
    from app.analyse import state

    async with echte_pid1(tmp_path, refuse="zelftest: uid 10003: uid 0") as worker:
        found = await monitor.check(_echt(worker, tmp_path))
        assert (found.state, found.reason, found.caps) == ("down", "sandbox", None)
        shown = state.view(found)
        assert (shown.analyse, shown.reason, shown.models, shown.limits) == (
            "down",
            "sandbox",
            [],
            None,
        )
        assert shown.problem == monitor.TEXT["sandbox"]
        assert "uid 0" not in (shown.problem or "")  # de reden van de worker blijft daar
    async with echte_pid1(tmp_path / "2") as worker:
        other = _echt(worker, tmp_path, analyse_worker_key="a" * 64)
        assert (await monitor.check(other)).reason == "handshake"
    gone = (await monitor.check(_echt(worker, tmp_path))).reason
    assert gone == "no_socket"


async def test_echt_de_lus_ziet_de_worker_komen_en_gaan(tmp_path, monkeypatch):
    """De monitor tegen de echte pid 1: ok, dan weg (down: no_socket), en terug."""

    async def kort(seconds: float) -> None:
        await asyncio.sleep(min(seconds, 0.05))

    monkeypatch.setattr(monitor, "_pause", kort)
    async with echte_pid1(tmp_path) as worker:
        settings = _echt(worker, tmp_path)
        monitor.start(settings)
        try:
            await until(lambda: monitor.status().state == "ok")
            await worker.stop()
            await until(lambda: monitor.status().reason == "no_socket")
            await worker.start()
            await until(lambda: monitor.status().state == "ok")
        finally:
            await monitor.stop()


async def test_echt_analyse_status_als_proces(tmp_path):
    async with echte_pid1(tmp_path) as worker:
        result = await run_cli(
            tmp_path,
            PLATFORM_ANALYSE_ENABLED="true",
            PLATFORM_ANALYSE_WORKER=str(worker.config.socket),
            PLATFORM_ANALYSE_WORKER_KEY=worker.config.key,
        )
    out = result.stdout.decode().splitlines()
    assert result.returncode == 0
    assert out[0] == "Analyse: ok"
    # models.lock van de app noemt dezelfde namen; de versie komt uit die lock
    assert out[1:] == [
        f"  model {YUNET} (versie {VERSIE[YUNET]}): klopt",
        f"  model {SFACE} (versie {VERSIE[SFACE]}): klopt",
        "  grenzen: 1.5 cpus, 3072 MB geheugen",
        "  opdrachten: cancel, caps, faces, group",
    ]
    assert worker.config.key not in (result.stdout + result.stderr).decode()


def test_de_stand_voor_beheer_zonder_caps():
    """state.view zonder caps (off, down): geen modellen en geen grenzen, want de app weet dan
    niet wat er in de container staat; reason is de sleutel van TEXT, problem de zin."""
    from app.analyse import state, status

    off = state.view(monitor.OFF)
    assert (off.analyse, off.reason, off.models, off.limits) == ("off", None, [], None)
    assert off.problem == "de analyse staat uit (PLATFORM_ANALYSE_ENABLED)"
    for reason in monitor.TEXT:
        down = state.view(monitor.AnalyseStatus("down", reason))
        assert (down.analyse, down.reason, down.problem) == ("down", reason, monitor.TEXT[reason])
        assert status.lines(monitor.AnalyseStatus("down", reason)) == [
            f"Analyse: down ({monitor.TEXT[reason]})"
        ]
    assert state.view().analyse == "off"  # zonder argument: monitor.status()
    assert status.exit_code(monitor.OFF, 0) == 0
    assert status.exit_code(monitor.AnalyseStatus("down", "no_socket"), 0) == 1
    assert status.exit_code(monitor.AnalyseStatus("down", "no_socket"), 5) == 0


def test_een_model_buiten_de_lock_van_de_app_heeft_versie_vraagteken(tmp_path):
    from app.analyse import state

    caps = wire.CapsData(
        models=[wire.ModelCaps(name="nieuw.onnx", state="ok")],
        built=["cancel", "caps"],
        limits=wire.LimitsCaps(cpus=2, mem_mb=2048),
    )
    shown = state.view(monitor.AnalyseStatus("ok", caps=caps))
    assert [(m.name, m.version, m.sha_ok) for m in shown.models] == [("nieuw.onnx", "?", True)]
    broken = tmp_path / "models.lock"
    broken.write_text("geen lock\n")
    shown = state.view(monitor.AnalyseStatus("ok", caps=caps), lock_path=broken)
    assert shown.models[0].version == "?"
    assert (shown.limits.cpus, shown.limits.mem_mb) == (2.0, 2048)
