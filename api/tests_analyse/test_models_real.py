"""De echte modellen van models.lock, zonder echte gezichten (fase 6, X2 "De tests", laag 3; X2a).

De modellen komen in ANALYSE_MODELLEN (CI: een map in RUNNER_TEMP met een cache op de sha256 van
models.lock; anders een map in de tijdelijke map van het systeem). Wat er nog niet is, haalt haal()
volgens models.lock: eerst naar een .part, dan de grootte en de sha256, en pas dan rename. Een
download is onvertrouwd: alleen bytes met de juiste sha256 komen in de map.

Op gemaakte beelden (ruis, een verloop, een getekend landschap) vindt YuNet niets; de vormen van
de uitvoer kloppen; de tijd per foto en de maxrss van het kind gaan naar het rapport
(ANALYSE_RAPPORT, een JSON-bestand) en naar stdout (-s). De poort: ≥ 250 foto's per minuut per
core met één gezicht (YuNet op de foto plus één keer SFace), op één draad.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.analyse import lock, wire
from app.analyse.worker import models as worker_models
from app.analyse.worker.omzetten import MAP as BEELD_MAP
from app.analyse.worker.ops import faces as F
from app.media.worker import guard
from tests_analyse import echt, fake_models

pytestmark = [pytest.mark.anyio, pytest.mark.analyse_echt]

POORT_PER_MINUUT = 250  # foto's per minuut per core met één gezicht (X2, "Harde poorten")
RAPPORT: dict = {}


def map_modellen() -> Path:
    found = os.environ.get("ANALYSE_MODELLEN")
    return Path(found) if found else Path(tempfile.gettempdir()) / "platform-analyse-modellen"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gezichten(path: Path = lock.LOCK_PATH) -> list[lock.ModelFile]:
    """De modellen van gezichten in models.lock: niet de regels van zoeken op beeld
    (siglip2-b32-256/…). Die bronnen haalt de stap BEELD van X3b, de omzetting maakt de host zelf
    (url "-"); laag 3 haalt ze niet (review golf 5, uitrol F3)."""
    return [m for m in lock.load(path) if not m.name.startswith(f"{BEELD_MAP}/")]


def haal(item: lock.ModelFile, root: Path, *, url: str | None = None) -> Path:
    """Eén model naar root volgens models.lock; ValueError als de grootte of sha256 niet klopt
    (dan staat er niets nieuws in root)."""
    dest = root / item.name
    if dest.is_file() and not dest.is_symlink() and dest.stat().st_size == item.size:
        if _sha(dest) == item.sha256:
            return dest
    source = url or item.url
    assert source is not None
    root.mkdir(parents=True, exist_ok=True)
    part = root / f".{item.name}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(source, timeout=120) as resp, open(part, "wb") as out:  # noqa: S310
            while chunk := resp.read(1 << 20):
                size += len(chunk)
                if size > item.size:
                    raise ValueError(f"{item.name}: groter dan models.lock")
                digest.update(chunk)
                out.write(chunk)
        if size != item.size or digest.hexdigest() != item.sha256:
            raise ValueError(f"{item.name}: de grootte of de sha256 klopt niet")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return dest


@pytest.fixture(scope="module")
def echte_modellen() -> fake_models.Installed:
    root = map_modellen()
    for item in gezichten():
        haal(item, root)
    return fake_models.Installed(root, lock.LOCK_PATH)


@pytest.fixture(scope="module")
def worker(tmp_path_factory, echte_modellen):
    with echt.EchteWorker(tmp_path_factory.mktemp("worker"), echte_modellen) as w:
        yield w


@pytest.fixture(scope="module", autouse=True)
def rapport():
    yield RAPPORT
    if not RAPPORT:
        return
    text = json.dumps(RAPPORT, indent=2, sort_keys=True)
    print("\n=== laag 3 ===\n" + text)
    target = os.environ.get("ANALYSE_RAPPORT")
    if target:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(text + "\n")


# --- gemaakte beelden -----------------------------------------------------------------------


def ruis(w: int, h: int, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 256, (h, w, 3), dtype=np.uint8))


def verloop(w: int, h: int) -> Image.Image:
    x = np.linspace(0, 255, w, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 255, h, dtype=np.float32)[:, None, None]
    arr = np.concatenate([np.broadcast_to(x, (h, w, 1)), np.broadcast_to(y, (h, w, 1)),
                          np.broadcast_to((x + y) / 2, (h, w, 1))], axis=2)  # fmt: skip
    return Image.fromarray(arr.astype(np.uint8))


def landschap(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), (120, 170, 230))
    draw = ImageDraw.Draw(img)
    for i in range(h // 2):  # de lucht, lichter naar de horizon
        c = 150 + i * 80 // (h // 2)
        draw.line([(0, i), (w, i)], fill=(c - 40, c - 10, 240))
    draw.ellipse([w * 0.7, h * 0.08, w * 0.82, h * 0.26], fill=(255, 230, 120))
    draw.polygon([(0, h * 0.6), (w * 0.3, h * 0.35), (w * 0.6, h * 0.6)], fill=(110, 110, 120))
    draw.polygon([(w * 0.4, h * 0.6), (w * 0.75, h * 0.3), (w, h * 0.6)], fill=(90, 95, 110))
    draw.rectangle([0, h * 0.6, w, h], fill=(60, 140, 60))
    for k in range(9):
        x = w * (0.05 + k * 0.11)
        draw.polygon([(x, h * 0.85), (x + 25, h * 0.62), (x + 50, h * 0.85)], fill=(20, 90, 30))
        draw.rectangle([x + 20, h * 0.85, x + 30, h * 0.92], fill=(90, 60, 30))
    return img


def gemaakt() -> list[Image.Image]:
    return [
        ruis(1024, 683, 1),
        ruis(683, 1024, 2),
        verloop(1024, 683),
        verloop(1023, 767),
        landschap(1024, 683),
        landschap(1024, 768),
    ]


# --- de tests ---------------------------------------------------------------------------------


def test_een_download_met_een_verkeerde_sha256_komt_er_niet_in(tmp_path):
    bron = tmp_path / "bron.onnx"
    bron.write_bytes(b"geen model" * 100)
    item = lock.ModelFile(sha256="0" * 64, size=1000, name="nep.onnx", url=None)
    root = tmp_path / "modellen"
    with pytest.raises(ValueError, match="sha256"):
        haal(item, root, url=bron.as_uri())
    assert sorted(p.name for p in root.iterdir()) == []
    groot = lock.ModelFile(sha256="0" * 64, size=10, name="nep.onnx", url=None)
    with pytest.raises(ValueError, match="groter"):
        haal(groot, root, url=bron.as_uri())
    assert sorted(p.name for p in root.iterdir()) == []


def test_alleen_de_modellen_van_gezichten(tmp_path):
    """Ook met de regels van SigLIP in models.lock (de vijf bronnen met een url op
    huggingface.co, de omzetting met "-") haalt laag 3 alleen YuNet en SFace."""
    regels = [f"{'a' * 64} 5 {BEELD_MAP}/bron/{n} https://huggingface.co/x/{n}\n" for n in "ab"]
    regels.append(f"{'b' * 64} 5 {BEELD_MAP}/nep.int8.onnx -\n")
    pad = tmp_path / "models.lock"
    pad.write_text(lock.LOCK_PATH.read_text() + "".join(regels))
    assert len(lock.load(pad)) == len(lock.load()) + 3
    assert [m.name for m in gezichten(pad)] == [fake_models.YUNET, fake_models.SFACE]


def test_de_modellen_kloppen_met_models_lock(echte_modellen):
    items = gezichten()
    assert [m.name for m in items] == [fake_models.YUNET, fake_models.SFACE]
    for item in items:
        path = echte_modellen.root / item.name
        assert path.stat().st_size == item.size
        assert _sha(path) == item.sha256


def test_laden_met_alleen_de_cpu_en_de_vormen(echte_modellen):
    """Via worker/models.py (zoals het kind): alleen CPUExecutionProvider, en de in- en
    uitgangen die faces.py verwacht."""
    models = worker_models.Models(echte_modellen.root, echte_modellen.lock)
    yunet = models.session(fake_models.YUNET)
    sface = models.session(fake_models.SFACE)
    assert yunet.get_providers() == sface.get_providers() == ["CPUExecutionProvider"]
    (inp,) = yunet.get_inputs()
    assert (inp.name, inp.shape) == ("input", [1, 3, "height", "width"])
    names = [o.name for o in yunet.get_outputs()]
    assert names == [f"{k}_{s}" for k in ("cls", "obj", "bbox", "kps") for s in (8, 16, 32)]
    blob = F.yunet_input(np.asarray(landschap(1024, 683)))
    assert blob.shape == (1, 3, 704, 1024)
    outs = dict(zip(names, yunet.run(None, {"input": blob}), strict=True))
    dims = {"cls": 1, "obj": 1, "bbox": 4, "kps": 10}
    for name, arr in outs.items():
        kind, s = name.split("_")
        assert arr.shape == (1, (704 // int(s)) * (1024 // int(s)), dims[kind]), name
    (sin,) = sface.get_inputs()
    assert (sin.name, sin.shape) == ("data", [1, 3, 112, 112])
    (vec,) = sface.run(None, {"data": np.zeros((1, 3, 112, 112), np.float32)})
    assert vec.shape == (1, wire.FACE_DIM)


async def test_nul_gezichten_op_gemaakte_beelden(worker):
    imgs = gemaakt()
    data, raw = await echt.faces(worker.settings(), [echt.webp(i, lossless=False) for i in imgs])
    assert [p.code for p in data.photos] == ["ok"] * len(imgs)
    assert [(p.w, p.h) for p in data.photos] == [i.size for i in imgs]
    assert [p.n for p in data.photos] == [0] * len(imgs)
    assert raw == b""


async def test_de_tijd_per_foto_en_de_maxrss_van_het_kind(worker, echte_modellen):
    """Het rapport, en de poort van ≥ 250 foto's per minuut per core met één gezicht."""
    previews = [echt.webp(landschap(1024, 683), lossless=False)] * 16
    await echt.faces(worker.settings(), previews[:1])  # het kind start en laadt de modellen
    rondes = []
    for _ in range(3):
        started = time.perf_counter()
        data, _ = await echt.faces(worker.settings(), previews)
        rondes.append((time.perf_counter() - started) / len(previews))
        assert [p.n for p in data.photos] == [0] * 16
    per_foto = min(rondes)
    # Eén gezicht kost daarbovenop uitlijnen en SFace: hier gemeten op een uitsnede
    models = worker_models.Models(echte_modellen.root, echte_modellen.lock)
    sface = models.session(fake_models.SFACE)
    rgb = np.asarray(landschap(1024, 683))
    points = fake_models.TEMPLATE
    sface_tijden = []
    for _ in range(10):
        started = time.perf_counter()
        aligned = F.warp(rgb, F.similarity(np.array(points) + 300))
        sface.run(None, {"data": F.sface_input(aligned)})
        sface_tijden.append(time.perf_counter() - started)
    per_gezicht = min(sface_tijden)
    met_een = per_foto + per_gezicht
    (kind,) = worker.children()
    status = guard._status(kind)
    RAPPORT.update(
        {
            "ms_per_foto_zonder_gezicht": round(per_foto * 1000, 1),
            "ms_per_gezicht": round(per_gezicht * 1000, 1),
            "fotos_per_minuut_per_core_met_een_gezicht": round(60 / met_een),
            "maxrss_werk_mb": round(status.get("VmHWM", 0) / 1024),
            "rss_werk_mb": round(status.get("VmRSS", 0) / 1024),
        }
    )
    assert status.get("VmHWM", 0) * 1024 < wire.CHILDREN[wire.Child.WERK].rlimit_data
    assert 60 / met_een >= POORT_PER_MINUUT, RAPPORT
