"""caps.beeld en welke regels van models.lock meetellen (fase 6, X3b; Keuze 75, 84).

Zonder numpy, in de job api: pid 1 leest meta.json als die ok is (dezelfde sha256 als in
models.lock) en geeft model, dim, scale (exp van logit_scale) en bias. De bronnen van de
omzetting tellen nooit mee; een model in een map pas als die map er echt is (geen symlink).
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from app.analyse import wire
from app.analyse.wire import Op
from app.analyse.worker import caps, omzetten

MAP = omzetten.MAP
META = f"{MAP}/{omzetten.META}"
BEELD = f"{MAP}/{omzetten.BEELD}"
BRON = f"{MAP}/bron/config.json"
GEZICHT = b"gezicht" * 40


def _meta(**over) -> bytes:
    doc = {"model": MAP, "dim": 768, "logit_scale": math.log(117.33), "logit_bias": -12.9}
    doc.update(over)
    return json.dumps(doc).encode()


def _lock(path: Path, files: dict[str, bytes]) -> None:
    lines = [
        f"{hashlib.sha256(GEZICHT).hexdigest()} {len(GEZICHT)} gezicht.onnx https://x.invalid/g"
    ]
    for name, data in files.items():
        url = "https://x.invalid/b" if "/bron/" in name else "-"
        lines.append(f"{hashlib.sha256(data).hexdigest()} {len(data)} {name} {url}")
    path.write_text("\n".join(lines) + "\n")


def _root(tmp_path: Path, meta: bytes, *, map_er: bool = True) -> tuple[Path, Path]:
    root = tmp_path / "modellen"
    root.mkdir()
    (root / "gezicht.onnx").write_bytes(GEZICHT)
    files = {BRON: b"{}", BEELD: b"beeld" * 30, META: meta}
    if map_er:
        (root / MAP).mkdir()
        (root / BEELD).write_bytes(files[BEELD])
        (root / META).write_bytes(meta)
    lock = tmp_path / "models.lock"
    _lock(lock, files)
    return root, lock


def _gather(root: Path, lock: Path) -> wire.CapsData:
    data = caps.gather(caps.ModelStates(root, lock), frozenset({Op.FACES}), root / "geen")
    return wire.CapsData.model_validate_json(data.model_dump_json())


def test_beeld_uit_meta_json(tmp_path):
    root, lock = _root(tmp_path, _meta())
    data = _gather(root, lock)
    assert [(m.name, m.state) for m in data.models] == [
        ("gezicht.onnx", "ok"),
        (BEELD, "ok"),
        (META, "ok"),
    ]
    assert data.beeld is not None
    assert (data.beeld.model, data.beeld.dim) == (MAP, 768)
    assert math.isclose(data.beeld.scale, 117.33) and data.beeld.bias == -12.9


def test_de_bron_telt_nooit_en_zonder_map_geen_beeld(tmp_path):
    root, lock = _root(tmp_path, _meta(), map_er=False)
    data = _gather(root, lock)
    assert [(m.name, m.state) for m in data.models] == [("gezicht.onnx", "ok")]
    assert data.beeld is None and data.missing() == []


def test_een_meta_json_die_niet_klopt_geeft_geen_beeld(tmp_path):
    root, lock = _root(tmp_path, _meta())
    (root / META).write_bytes(_meta(dim=512))  # andere bytes: anders, en geen beeld
    data = _gather(root, lock)
    assert dict((m.name, m.state) for m in data.models)[META] == "anders"
    assert data.beeld is None
    for kapot in (b"geen json", _meta(logit_scale=1e9), _meta(dim=0), _meta(model="")):
        sub = tmp_path / hashlib.sha256(kapot).hexdigest()[:8]
        sub.mkdir()
        root, lock = _root(sub, kapot)  # de lock klopt bij de bytes, de inhoud niet
        data = _gather(root, lock)
        assert dict((m.name, m.state) for m in data.models)[META] == "ok"
        assert data.beeld is None, kapot


def test_een_symlink_als_map_of_als_meta_json_telt_niet(tmp_path):
    root, lock = _root(tmp_path, _meta(), map_er=False)
    elders = tmp_path / "elders"
    elders.mkdir()
    (elders / omzetten.META).write_bytes(_meta())
    (elders / omzetten.BEELD).write_bytes(b"beeld" * 30)
    (root / MAP).symlink_to(elders)
    assert [m.name for m in _gather(root, lock).models] == ["gezicht.onnx"]
    (root / MAP).unlink()
    (root / MAP).mkdir()
    (root / BEELD).write_bytes(b"beeld" * 30)
    (root / META).symlink_to(elders / omzetten.META)
    data = _gather(root, lock)
    assert dict((m.name, m.state) for m in data.models)[META] == "anders"
    assert data.beeld is None
