"""caps en de zelftest van de worker van de analyse, zonder numpy (fase 6, X2a; §3.11, K1-04).

In de job api: de stukken van pid 1 die geen kind en geen model nodig hebben. ModelStates (ok,
ontbreekt, anders; een symlink telt niet; de sha256 één keer per bestand), de grenzen uit de
cgroup (v2 en v1, of geen), built, en de controles van de zelftest die buiten de container
altijd dezelfde uitkomst hebben. De zelftest als geheel (een weigerende worker) staat in
tests_analyse/test_worker_sandbox.py.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.analyse import lock, wire
from app.analyse.wire import Op
from app.analyse.worker import caps, selftest

A = b"model a" * 100
B = b"model b" * 300


def _root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "modellen"
    (root / "map").mkdir(parents=True)
    (root / "a.onnx").write_bytes(A)
    (root / "map" / "b.onnx").write_bytes(B)
    lines = [
        f"{hashlib.sha256(A).hexdigest()} {len(A)} a.onnx https://example.invalid/a",
        f"{hashlib.sha256(B).hexdigest()} {len(B)} map/b.onnx -",
    ]
    path = tmp_path / "models.lock"
    path.write_text("\n".join(lines) + "\n")
    return root, path


def _states(states: caps.ModelStates) -> list[tuple[str, str]]:
    return [(m.name, m.state) for m in states.gather()]


def test_modellen_ok_ontbreekt_en_anders(tmp_path):
    root, path = _root(tmp_path)
    states = caps.ModelStates(root, path)
    assert _states(states) == [("a.onnx", "ok"), ("map/b.onnx", "ok")]
    (root / "a.onnx").unlink()
    assert _states(states) == [("a.onnx", "ontbreekt"), ("map/b.onnx", "ok")]
    (root / "a.onnx").write_bytes(A[:-1])  # een andere grootte
    assert _states(states)[0] == ("a.onnx", "anders")
    (root / "a.onnx").write_bytes(A[:-1] + b"X")  # dezelfde grootte, een andere sha256
    assert _states(states)[0] == ("a.onnx", "anders")
    (root / "a.onnx").unlink()
    (root / "a.onnx").mkdir()  # geen gewoon bestand
    assert _states(states)[0] == ("a.onnx", "anders")
    (root / "a.onnx").rmdir()
    elders = tmp_path / "elders.onnx"
    elders.write_bytes(A)
    (root / "a.onnx").symlink_to(elders)  # een symlink naar de goede bytes telt niet
    assert _states(states)[0] == ("a.onnx", "anders")
    (root / "a.onnx").unlink()
    (root / "a.onnx").write_bytes(A)
    assert _states(states)[0] == ("a.onnx", "ok")


def test_de_sha256_één_keer_per_bestand(tmp_path, monkeypatch):
    root, path = _root(tmp_path)
    states = caps.ModelStates(root, path)
    reads: list[int] = []
    real = os.pread

    def counted(fd: int, n: int, offset: int) -> bytes:
        reads.append(offset)
        return real(fd, n, offset)

    monkeypatch.setattr(caps.os, "pread", counted)
    states.gather()
    first = len(reads)
    assert first > 0
    states.gather()
    states.gather()
    assert len(reads) == first  # niets nieuws: dezelfde inode, grootte, mtime en ctime
    (root / "a.onnx").write_bytes(A)  # nieuwe mtime: opnieuw rekenen
    states.gather()
    assert len(reads) > first


def test_een_lock_met_een_onbekend_model(tmp_path):
    root, path = _root(tmp_path)
    path.write_text(path.read_text() + f"{'0' * 64} 5 nieuw.onnx -\n")
    assert _states(caps.ModelStates(root, path))[-1] == ("nieuw.onnx", "ontbreekt")
    assert [m.name for m in lock.load(path)] == ["a.onnx", "map/b.onnx", "nieuw.onnx"]


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text + "\n")
    return root


@pytest.mark.parametrize(
    "files,expected",
    [
        ({"cpu.max": "200000 100000", "memory.max": str(2 * 1024**3)}, (2.0, 2048)),
        ({"cpu.max": "150000 100000", "memory.max": str(3 * 1024**3)}, (1.5, 3072)),
        ({"cpu.max": "max 100000", "memory.max": str(2 * 1024**3)}, None),
        ({"cpu.max": "200000 100000", "memory.max": "max"}, None),
        ({"cpu.max": "200000 0", "memory.max": "1048576"}, None),
        ({"cpu.max": "200000 100000"}, None),
        (
            {
                "cpu/cpu.cfs_quota_us": "100000",
                "cpu/cpu.cfs_period_us": "100000",
                "memory/memory.limit_in_bytes": str(1024**3),
            },
            (1.0, 1024),
        ),
        (
            {
                "cpu/cpu.cfs_quota_us": "-1",
                "cpu/cpu.cfs_period_us": "100000",
                "memory/memory.limit_in_bytes": str(1024**3),
            },
            None,
        ),
        (
            {
                "cpu/cpu.cfs_quota_us": "100000",
                "cpu/cpu.cfs_period_us": "100000",
                "memory/memory.limit_in_bytes": "9223372036854771712",  # v1 zonder grens
            },
            None,
        ),
        ({}, None),
    ],
)
def test_de_grenzen_uit_de_cgroup(tmp_path, files, expected):
    found = caps.limits(_write(tmp_path, files))
    if expected is None:
        assert found is None
    else:
        assert found == wire.LimitsCaps(cpus=expected[0], mem_mb=expected[1])


def test_built_altijd_caps_en_cancel():
    assert caps.built(frozenset()) == ["cancel", "caps"]
    assert caps.built(frozenset({Op.FACES})) == ["cancel", "caps", "faces"]
    assert caps.built(frozenset({Op.FACES, Op.CAPS})) == ["cancel", "caps", "faces"]


def test_gather_is_een_geldige_capsdata(tmp_path):
    root, path = _root(tmp_path)
    group = _write(tmp_path / "cg", {"cpu.max": "100000 100000", "memory.max": "1073741824"})
    data = caps.gather(caps.ModelStates(root, path), frozenset({Op.FACES}), group)
    assert wire.CapsData.model_validate(data.model_dump(mode="json")) == data
    assert data.missing() == [] and data.beeld is None
    assert data.limits == wire.LimitsCaps(cpus=1.0, mem_mb=1024)


# --- de zelftest, stuk voor stuk ---


def test_status_field_uit_een_tekst():
    text = "Name:\tpython\nCapEff:\t0000000000000000\nNoNewPrivs:\t1\n"
    assert selftest.status_field("CapEff", text) == "0000000000000000"
    assert selftest.status_field("NoNewPrivs", text) == "1"
    assert selftest.status_field("Seccomp", text) is None


def _result(fill) -> list[str]:
    report = selftest.Report()
    fill(report)
    return report.result()["problems"]


def test_de_uid_hier_is_niet_die_van_analyse():
    problems = _result(selftest.identity)
    assert len(problems) == 1 and problems[0].startswith("uid 10003: uid ")


def test_data_leeg_keys_weg_en_modellen_niet_schrijfbaar(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    keys = tmp_path / "keys"
    models = tmp_path / "modellen"
    models.mkdir()
    report = selftest.Report()
    selftest.filesystem(report, models, data=data, keys=keys)
    by_name = {c.name: c.ok for c in report.checks}
    assert by_name[f"{data} leeg"] is True
    assert by_name[f"{keys} bestaat niet"] is True
    # Deze map is van ons en schrijfbaar: dat is precies wat de zelftest moet zien
    assert by_name[f"{models} niet schrijfbaar"] is False
    assert list(models.iterdir()) == []  # de proef laat niets achter

    (data / "iets").write_text("x")
    keys.mkdir()
    report = selftest.Report()
    selftest.filesystem(report, models, data=data, keys=keys)
    by_name = {c.name: (c.ok, c.detail) for c in report.checks}
    assert by_name[f"{data} leeg"] == (False, f"1 namen in {data}")
    assert by_name[f"{keys} bestaat niet"] == (False, f"{keys} bestaat")


def test_modellen_die_geen_map_zijn(tmp_path):
    report = selftest.Report()
    selftest.filesystem(report, tmp_path / "nergens", data=tmp_path, keys=tmp_path / "k")
    detail = {c.name: c.detail for c in report.checks}[f"{tmp_path / 'nergens'} niet schrijfbaar"]
    assert detail == f"{tmp_path / 'nergens'} is geen map"


def test_het_proefbestand_van_10001(tmp_path):
    missing = tmp_path / "zelftest-10001"
    problems = _result(lambda r: selftest.probe_file(r, missing))
    assert len(problems) == 1 and "No such file" in problems[0]
    missing.write_text("")
    problems = _result(lambda r: selftest.probe_file(r, missing))
    assert problems == [f"{missing} niet te schrijven: {missing} is van uid {os.getuid()}"]


def test_python_tekst_ontbreekt_buiten_de_container():
    problems = _result(selftest.text_python)
    copy = selftest.kids.copy_path()
    assert problems == [f"python-tekst 0711 van root: {copy} niet gevonden"]


def test_de_map_van_de_socket(tmp_path):
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    os.chmod(run, 0o700)
    problems = _result(lambda r: selftest.socket_dir(r, run / "worker.sock"))
    assert len(problems) == 1 and problems[0].startswith(
        "map van de socket 2750 analyse:platform: modus 0700"
    )


def test_een_rapport_zonder_problemen_is_on():
    report = selftest.Report()
    report.add("een", True)
    assert report.result() == {
        "ok": True,
        "sandbox": "on",
        "problems": [],
        "checks": [{"name": "een", "ok": True, "detail": ""}],
    }
    report.add("twee", False, "x" * 500)
    result = report.result()
    assert (result["ok"], result["sandbox"]) == (False, "off")
    assert result["problems"] == ["twee: " + "x" * 300]  # een detail is hooguit 300 tekens
