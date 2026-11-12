"""De afscherming van de worker van de analyse (fase 6, X2 "De tests", laag 2; X2a).

K1-04 (niet-dumpbaar, de sleutel), K1-05 (de omgeving van een kind), K1-07 (geheugen en
pixels), K1-16 (geen vector en geen zoektekst in een log, een fout of stderr), en de weigering
als de zelftest faalt. De container zelf toetst test_analyse_container.sh (uid 10003, geen
netwerk, read_only, CapEff).

Over /proc: een proces met dumpable 0 heeft zijn bestanden in /proc/<pid> van root. Een ander
proces met dezelfde uid krijgt dan EACCES op /proc/<pid>/fd en environ. Dat is alleen te zien
als de test zelf geen root is (CI); root heeft CAP_SYS_PTRACE en mag alles. Daarom meldt het
kind ook zelf zijn dumpable (bij klaar), en dat geldt overal.
"""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import hashlib
import json
import logging
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from app.analyse import client, runtime, wire
from app.analyse.wire import Child, Code, Op
from app.analyse.worker import child as kids
from app.analyse.worker import selftest, server
from tests.analyse import analyse_contract as C
from tests_analyse import echt, fake_models, proef

pytestmark = [pytest.mark.anyio, pytest.mark.analyse_echt]

HIER = Path(__file__).resolve().parent
ROOT = os.geteuid() == 0


@pytest.fixture(scope="module")
def modellen(tmp_path_factory):
    return fake_models.install(tmp_path_factory.mktemp("modellen") / "modellen")


@pytest.fixture(scope="module")
def worker(tmp_path_factory, modellen):
    with echt.EchteWorker(tmp_path_factory.mktemp("worker"), modellen) as w:
        yield w


def _gezicht() -> bytes:
    img = Image.new("RGB", (320, 240), (40, 48, 0))
    img.paste((40, 48, 255), (64, 64, 96, 96))
    return echt.webp(img)


def _healthcheck(sock: Path) -> subprocess.CompletedProcess:
    """De healthcheck van compose: --check, zonder sleutel."""
    return subprocess.run(
        runtime.worker_argv("--check", str(sock)),
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        capture_output=True,
        text=True,
        timeout=30,
    )


def _lees(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _dicht(path: str) -> bool:
    """Waar als dit proces path niet mag lezen (EACCES of EPERM)."""
    try:
        if os.path.isdir(path):
            os.listdir(path)
        else:
            with open(path, "rb") as f:
                f.read(1)
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno in (errno.EACCES, errno.EPERM)
    return False


# --- de sleutel en dumpable -----------------------------------------------------------------


def test_de_sleutel_is_weg_en_pid1_blijft_zonder_numpy(tmp_path, modellen):
    """__main__ haalt de sleutel uit os.environ, zet dumpable op 0 vóór de import van de app,
    en pid 1 laadt numpy en onnxruntime nooit, ook niet na een opdracht. Het kind is bij klaar
    al niet-dumpbaar (vóór het eerste fd van een opdracht)."""
    key = "k" * 16 + os.urandom(16).hex()
    sock = tmp_path / "run" / "worker.sock"
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(HIER / "pid1_proef.py"),
            runtime.API_ROOT,
            str(sock),
            str(modellen.root),
            str(modellen.lock),
        ],  # fmt: skip
        input=_gezicht().hex() + "\n",
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), runtime.KEY_ENV: key},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["key_in_environ"] is False
    assert out["key_sha"] == hashlib.sha256(key.encode()).hexdigest()
    assert out["dumpable"] == 0 and out["sandbox"] is False
    assert out["python_ok"] is True
    assert out["faces"] == ["ok"]
    assert out["caps"] == ["cancel", "caps", "faces"]
    assert out["kind"]["ev"] == "klaar" and out["kind"]["dumpable"] == 0
    assert key not in proc.stdout and key not in proc.stderr


@pytest.mark.parametrize("sleutel", [None, "", "x" * (wire.KEY_MIN - 1)])
def test_zonder_goede_sleutel_stopt_de_worker_met_2(tmp_path, modellen, sleutel):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if sleutel is not None:
        env[runtime.KEY_ENV] = sleutel
    argv = runtime.serve_argv(tmp_path / "w.sock", modellen.root, extra=["--local"])
    proc = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2
    assert runtime.KEY_ENV in proc.stderr
    if sleutel:
        assert sleutel not in proc.stderr
    assert not (tmp_path / "w.sock").exists()


async def test_de_sleutel_staat_nooit_in_een_opdrachtregel(worker):
    await echt.faces(worker.settings(), [_gezicht()])
    pids = [worker.pid, *worker.children()]
    assert len(pids) == 2
    for pid in pids:  # cmdline is voor iedereen te lezen, ook bij dumpable 0
        assert worker.key.encode() not in _lees(f"/proc/{pid}/cmdline")


async def test_proc_van_pid1_en_het_kind_is_dicht(worker):
    """K1-04: pid 1 en het kind zijn niet-dumpbaar, dus /proc/<pid> is van root. Een tweede
    proces van dezelfde uid (deze test, buiten root) krijgt EACCES op fd en environ."""
    await echt.faces(worker.settings(), [_gezicht()])
    for pid in (worker.pid, *worker.children()):
        assert os.stat(f"/proc/{pid}").st_uid == 0
        if not ROOT:
            assert _dicht(f"/proc/{pid}/fd")
            assert _dicht(f"/proc/{pid}/environ")
            assert _dicht(f"/proc/{pid}/mem")


# --- de omgeving van een kind ---------------------------------------------------------------


@pytest.fixture
async def proefworker(tmp_path, modellen):
    built = server.BUILT | {Op.EMBED_TEXT}
    async with echt.binnen(tmp_path, modellen, child_code=proef.BOOT, built=built) as pair:
        yield pair


async def test_het_kind_heeft_geen_sleutel_in_zijn_omgeving(proefworker):
    worker, settings = proefworker
    out = wire.out_memfd("uit")
    try:
        data = await client.call(
            settings, Op.EMBED_TEXT, wire.EmbedTextArgs(text="omgeving"), fds=[out]
        )
    finally:
        os.close(out)
    assert set(data["env"]) == set(kids.env())
    assert runtime.KEY_ENV not in data["env"]
    assert not any(worker.config.key in v for v in data["env"].values())
    assert data["dumpable"] == 0
    pid = data["pid"]
    if ROOT:
        assert worker.config.key.encode() not in _lees(f"/proc/{pid}/environ")
    else:
        assert _dicht(f"/proc/{pid}/environ")


# --- de zelftest ----------------------------------------------------------------------------


async def test_een_zelftest_die_faalt_geeft_een_weigerende_worker(tmp_path, modellen):
    """Zonder --local draait de zelftest; buiten de container faalt hij altijd (uid 10003, /
    schrijfbaar, …). De worker luistert wel, maar elke hello krijgt done/sandbox met de reden;
    de app ziet down: sandbox, en de healthcheck zegt 1."""
    report = selftest.startup(models=modellen.root, socket_path=tmp_path / "run" / "w.sock")
    assert report["ok"] is False and report["sandbox"] == "off"
    with echt.EchteWorker(tmp_path / "w", modellen, local=False) as w:
        with pytest.raises(client.AnalyseDown) as exc:
            await client.caps(w.settings())
        assert exc.value.reason == "sandbox"
        sock = await C.raw(w)
        try:
            await wire.send(sock, wire.hello("ab" * 16))
            reply = await C.answer(sock)
        finally:
            sock.close()
        assert reply["ev"] == "done" and reply["code"] == Code.SANDBOX.value
        assert reply["detail"].startswith("zelftest: ") and "uid 10003" in reply["detail"]
        assert w.children() == []
        check = await asyncio.to_thread(_healthcheck, w.socket)
        assert check.returncode == 1 and "weigert: sandbox" in check.stdout


@pytest.mark.parametrize(
    "status,fout",
    [
        ({"CapEff": "0000000000000000", "NoNewPrivs": "1"}, []),
        ({"CapEff": "00000000a80425fb", "NoNewPrivs": "1"}, ["geen capabilities"]),
        ({"CapEff": "0000000000000000", "NoNewPrivs": "0"}, ["no-new-privileges"]),
        ({"NoNewPrivs": "1"}, ["geen capabilities"]),
    ],
)
async def test_capeff_en_nonewprivs_maken_de_worker_weigerend(tmp_path, modellen, status, fout):
    report = selftest.Report()
    selftest.privileges(report, status=status.get)
    result = report.result()
    assert [p.split(":")[0] for p in result["problems"]] == fout
    if not fout:
        return
    config = server.Config(
        socket=tmp_path / "run" / "w.sock", models=modellen.root, key="k" * 64,
        lock=modellen.lock,
    )  # fmt: skip
    worker = server.RefusingWorker(config, "zelftest: " + "; ".join(result["problems"]))
    await worker.start()
    try:
        settings = echt.settings_for(tmp_path, config.socket, config.key)
        with pytest.raises(client.AnalyseDown) as exc:
            await client.caps(settings)
        assert exc.value.reason == "sandbox"
    finally:
        await worker.stop()


# --- geheugen en pixels (K1-07) -------------------------------------------------------------


async def test_een_webp_van_6_mp_is_too_big(worker):
    img = Image.new("RGB", (3000, 2000), (30, 40, 0))
    data, raw = await echt.faces(worker.settings(), [echt.webp(img, lossless=False), _gezicht()])
    assert [p.code for p in data.photos] == ["too_big", "ok"]
    assert len(raw) == wire.FACE_VEC_BYTES


GRENS = 256 << 20  # RLIMIT_DATA van werk in deze proef (het kind gebruikt er ±100 MB van)


@pytest.fixture
async def krappe_worker(tmp_path, modellen):
    children = dict(wire.CHILDREN)
    children[Child.WERK] = dataclasses.replace(children[Child.WERK], rlimit_data=GRENS)
    async with echt.binnen(
        tmp_path, modellen, child_code=proef.BOOT, children=children, watch_interval=0.1
    ) as pair:
        yield pair


async def test_een_kind_dat_te_veel_gedeeld_geheugen_pakt_gaat_weg(krappe_worker):
    """RLIMIT_DATA telt gedeeld geheugen niet; de wachter wel (RssAnon + RssShmem): SIGKILL,
    too_big, en pid 1 blijft staan. De volgende opdracht krijgt een nieuw kind."""
    worker, settings = krappe_worker
    await echt.faces(settings, [_gezicht()])
    eerste = worker.kids[Child.WERK].proc.pid
    with pytest.raises(client.AnalyseFailed) as exc:
        await echt.faces(settings, [b"VREET-SHM 320 "], deadline_ms=60_000)
    assert exc.value.code == Code.TOO_BIG.value
    assert "geheugengrens" in exc.value.detail
    assert eerste in worker.watch.killed
    data, _ = await echt.faces(settings, [_gezicht()])
    assert data.photos[0].code == "ok"
    assert worker.kids[Child.WERK].proc.pid != eerste


async def test_een_malloc_boven_rlimit_data_is_too_big_en_het_kind_leeft(krappe_worker):
    worker, settings = krappe_worker
    await echt.faces(settings, [_gezicht()])
    eerste = worker.kids[Child.WERK].proc.pid
    with pytest.raises(client.AnalyseFailed) as exc:
        await echt.faces(settings, [b"VREET-MALLOC 400 "])
    assert exc.value.code == Code.TOO_BIG.value
    data, _ = await echt.faces(settings, [_gezicht()])
    assert data.photos[0].code == "ok"
    assert worker.kids[Child.WERK].proc.pid == eerste


# --- K1-16: geen vector en geen zoektekst naar buiten ---------------------------------------


def _sporen() -> list[bytes]:
    merk = struct.pack(f"<{len(proef.MERK)}f", *proef.MERK)
    out = [proef.GEHEIM.encode(), merk[:64], merk.hex()[:32].encode()]
    out += [repr(v).encode() for v in proef.MERK[:8]]
    out += [f"{v:.5f}".encode() for v in proef.MERK[1:8]]
    return out


async def test_geen_merkvector_en_geen_zoektekst_in_log_fout_of_stderr(proefworker, caplog, capfd):
    worker, settings = proefworker
    caplog.set_level(logging.DEBUG)
    fouten: list[str] = []
    for preview in (b"FOUT ", b"STDERR "):
        with pytest.raises(client.AnalyseFailed) as exc:
            await echt.faces(settings, [preview])
        assert exc.value.code == Code.INTERNAL.value
        fouten += [str(exc.value), exc.value.detail]
    out = wire.out_memfd("uit")
    try:
        with pytest.raises(client.AnalyseFailed) as exc:
            await client.call(
                settings, Op.EMBED_TEXT, wire.EmbedTextArgs(text=proef.GEHEIM), fds=[out]
            )
    finally:
        os.close(out)
    assert exc.value.code == Code.INTERNAL.value
    fouten += [str(exc.value), exc.value.detail]
    assert fouten[1] == "ValueError" and exc.value.detail == "RuntimeError"
    await asyncio.sleep(0.3)  # de regels van stderr van het kind zijn gelezen
    captured = capfd.readouterr()
    alles = "\n".join([caplog.text, captured.out, captured.err, *fouten]).encode()
    for spoor in _sporen():
        assert spoor not in alles, spoor
    # Wat er wel staat: dat er een regel was, en hoe lang
    assert "een regel op stderr" in caplog.text
