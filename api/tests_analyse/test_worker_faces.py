"""faces met de echte worker en de nepmodellen (fase 6, X2 "De tests", laag 2; X2a).

De worker draait als eigen proces met --local (echt.EchteWorker), de modellen komen uit
fake_models.py. Een vierkant van 32 × 32 op het raster met blauw 255 en groen g is één gezicht van
g × g px, midden in zijn cel; de vector hangt af van het gemiddelde rood en groen van het
uitgelijnde gezicht (expected_vector).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import time

import numpy as np
import pytest
from PIL import Image

from app.analyse import client, wire
from app.analyse.wire import Code, Op
from tests_analyse import echt, fake_models

pytestmark = [pytest.mark.anyio, pytest.mark.analyse_echt]

CEL = 32


@pytest.fixture(scope="module")
def modellen(tmp_path_factory):
    return fake_models.install(tmp_path_factory.mktemp("modellen") / "modellen")


@pytest.fixture(scope="module")
def worker(tmp_path_factory, modellen):
    with echt.EchteWorker(tmp_path_factory.mktemp("worker"), modellen) as w:
        yield w


def beeld(w: int, h: int, *, rood: int = 0, groen: int = 0) -> np.ndarray:
    """Een RGB-beeld zonder blauw: geen gezicht."""
    img = np.zeros((h, w, 3), np.uint8)
    img[..., 0], img[..., 1] = rood, groen
    return img


def gezicht(img: np.ndarray, kol: int, rij: int, groen: int, *, blauw: int = 255, rood=None):
    """Eén gezicht van groen × groen px in cel (kol, rij), met score blauw / 255."""
    cel = img[rij * CEL : (rij + 1) * CEL, kol * CEL : (kol + 1) * CEL]
    cel[..., 2] = blauw
    cel[..., 1] = groen
    if rood is not None:
        cel[..., 0] = rood


def als_webp(img: np.ndarray) -> bytes:
    return echt.webp(Image.fromarray(img))


async def zoek(worker, *imgs, args=None, **kw):
    data, raw = await echt.faces(worker.settings(), [als_webp(i) for i in imgs], args, **kw)
    return data, raw


def vectoren(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, wire.FACE_DIM)


def kader(kol: int, rij: int, g: int, w: int, h: int) -> tuple[float, float, float, float]:
    """Het kader zoals de worker het meldt: binnen de preview, als deel van w en h."""
    cx, cy = (kol + 0.5) * CEL, (rij + 0.5) * CEL
    x1, y1 = max(cx - g / 2, 0), max(cy - g / 2, 0)
    x2, y2 = min(cx + g / 2, w), min(cy + g / 2, h)
    return x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h


def box(face: wire.FaceBox) -> tuple[float, float, float, float]:
    return face.x, face.y, face.w, face.h


def plat(kaders) -> list[float]:
    """Gesorteerde kaders als één rij getallen (pytest.approx kent geen rij van rijen)."""
    return [v for k in sorted(kaders) for v in k]


@pytest.mark.parametrize("w,h", [(1024, 683), (683, 1024), (1023, 767)])
async def test_elke_maat_werkt_dankzij_het_opvullen(worker, w, h):
    """K2-05: zonder opvullen tot 32 past de uitvoer van YuNet niet bij de cellen (internal)."""
    img = beeld(w, h, rood=90, groen=60)
    gezicht(img, 3, 2, 60)
    data, raw = await zoek(worker, img)
    (photo,) = data.photos
    assert (photo.code, photo.w, photo.h, photo.n) == ("ok", w, h, 1)
    assert box(photo.faces[0]) == pytest.approx(kader(3, 2, 60, w, h), abs=1e-6)
    assert photo.faces[0].px == 60 and photo.faces[0].score == pytest.approx(1.0, abs=1e-6)
    assert len(raw) == wire.FACE_VEC_BYTES


async def test_kaders_omgerekend_en_afgekapt_aan_de_rand(worker):
    w, h = 1024, 683
    img = beeld(w, h)
    gezicht(img, 0, 0, 64)  # het kader steekt 16 px links en boven buiten de preview
    gezicht(img, 10, 5, 48)
    gezicht(img, 31, 20, 50)  # de laatste hele cel; rechts 7 px over
    data, _ = await zoek(worker, img)
    (photo,) = data.photos
    got = plat(box(f) for f in photo.faces)
    want = plat([kader(0, 0, 64, w, h), kader(10, 5, 48, w, h), kader(31, 20, 50, w, h)])
    assert got == pytest.approx(want, abs=1e-6)
    by_x = {round(f.x * w): f.px for f in photo.faces}
    assert by_x[0] == 48  # 64 breed, maar binnen de preview 48 × 48


async def test_nms_bij_twee_vlakken_die_overlappen(worker):
    w, h = 1024, 683
    # 64 px met de middens 32 px uit elkaar: IoU 1/3 > 0,3, alleen de hoogste score blijft
    img = beeld(w, h)
    gezicht(img, 4, 4, 64, blauw=240)
    gezicht(img, 5, 4, 64, blauw=255)
    # 48 px met de middens 32 px uit elkaar: IoU 0,2, beide blijven
    gezicht(img, 10, 10, 48)
    gezicht(img, 11, 10, 48)
    data, _ = await zoek(worker, img)
    (photo,) = data.photos
    assert photo.n == 3
    got = plat(box(f) for f in photo.faces)
    want = plat([kader(5, 4, 64, w, h), kader(10, 10, 48, w, h), kader(11, 10, 48, w, h)])
    assert got == pytest.approx(want, abs=1e-6)


async def test_een_korte_kant_onder_min_px_valt_af(worker):
    w, h = 1024, 683
    img = beeld(w, h)
    gezicht(img, 2, 2, 39)
    gezicht(img, 6, 2, 40)
    data, raw = await zoek(worker, img)
    assert [f.px for f in data.photos[0].faces] == [40]
    assert len(raw) == wire.FACE_VEC_BYTES
    data, _ = await zoek(worker, img, args=wire.FacesArgs(min_px=30))
    assert sorted(f.px for f in data.photos[0].faces) == [39, 40]


async def test_de_score_onder_de_drempel_valt_af(worker):
    w, h = 1024, 683
    img = beeld(w, h)
    gezicht(img, 2, 2, 50, blauw=round(0.85 * 255))
    gezicht(img, 8, 2, 50, blauw=round(0.95 * 255))
    data, _ = await zoek(worker, img)
    (face,) = data.photos[0].faces
    assert face.score == pytest.approx(round(0.95 * 255) / 255, abs=1e-4)
    data, _ = await zoek(worker, img, args=wire.FacesArgs(score=0.8))
    assert data.photos[0].n == 2


async def test_hooguit_40_de_grootste_eerst(worker):
    w, h = 1024, 683
    img = beeld(w, h)
    sizes = []
    cells = [(k, r) for r in range(1, 21, 2) for k in range(1, 30, 2)]  # niet aan de rand
    for i, (k, r) in enumerate(cells[:45]):
        g = 40 + (i * 7) % 24  # 40 … 63, door elkaar
        gezicht(img, k, r, g)
        sizes.append(g)
    data, raw = await zoek(worker, img)
    (photo,) = data.photos
    got = [f.px for f in photo.faces]
    assert photo.n == len(got) == wire.FACES_MAX
    assert got == sorted(sizes, reverse=True)[: wire.FACES_MAX]
    assert len(raw) == wire.FACES_MAX * wire.FACE_VEC_BYTES
    data, raw = await zoek(worker, img, args=wire.FacesArgs(max=3))
    assert [f.px for f in data.photos[0].faces] == sorted(sizes, reverse=True)[:3]
    assert len(raw) == 3 * wire.FACE_VEC_BYTES


async def test_de_kleurvolgorde_bgr_naar_yunet_en_rgb_naar_sface(worker):
    """Laag 2 legt de volgorde vast (laag 4 beslist of hij juist is): YuNet kijkt naar blauw
    (kanaal 0 van BGR), SFace naar rood en groen (kanaal 0 en 1 van RGB)."""
    w, h = 1024, 683
    rood_vlak = beeld(w, h, rood=0, groen=64)
    rood_vlak[2 * CEL : 3 * CEL, 2 * CEL : 3 * CEL, 0] = 255  # rood, geen blauw: geen gezicht
    data, raw = await zoek(worker, rood_vlak)
    assert data.photos[0].n == 0 and raw == b""

    img = beeld(w, h, rood=200, groen=64)
    gezicht(img, 6, 6, 64)  # blauw 255, rood 200, groen 64
    data, raw = await zoek(worker, img)
    assert data.photos[0].n == 1
    (vec,) = vectoren(raw)
    assert vec == pytest.approx(np.array(fake_models.expected_vector(200, 64)), abs=1e-5)
    # In BGR zou SFace blauw (255) als rood zien: een andere vector
    bgr = np.array(fake_models.expected_vector(255, 64))
    assert float(np.abs(vec - bgr).max()) > 0.01


async def test_de_vector_is_genormaliseerd_en_staat_alleen_in_uit(worker):
    w, h = 1024, 683
    img = beeld(w, h, rood=120, groen=56)
    gezicht(img, 4, 3, 56)
    gezicht(img, 20, 12, 56)
    data, raw = await zoek(worker, img)
    vecs = vectoren(raw)
    assert vecs.shape == (2, wire.FACE_DIM) and np.all(np.isfinite(vecs))
    assert np.linalg.norm(vecs.astype(np.float64), axis=1) == pytest.approx([1, 1], abs=1e-6)
    want = np.array(fake_models.expected_vector(120, 56))
    for vec in vecs:
        assert vec == pytest.approx(want, abs=1e-5)
    # Het antwoord heeft alleen kaders, scores, px en n: geen getal van de vector
    dumped = data.model_dump(mode="json")
    for photo in dumped["photos"]:
        assert set(photo) == {"code", "w", "h", "faces", "n"}
        for face in photo["faces"]:
            assert set(face) == {"x", "y", "w", "h", "score", "px"}
    text = json.dumps(dumped)
    for value in vecs[0][:16]:
        assert f"{float(value):.6f}"[:8] not in text


async def test_zestien_previews_in_volgorde(worker):
    w, h = 683, 1024
    imgs, rood = [], []
    for k in range(16):
        img = beeld(w, h, rood=10 * k + 5, groen=48)
        for j in range(k % 4):
            gezicht(img, 2 + 4 * j, 3 + 2 * (k % 3), 48)
        imgs.append(img)
        rood.append(10 * k + 5)
    data, raw = await zoek(worker, *imgs)
    assert [p.n for p in data.photos] == [k % 4 for k in range(16)]
    assert len(raw) == data.vector_bytes() == sum(k % 4 for k in range(16)) * 512
    vecs = iter(vectoren(raw))
    for k, photo in enumerate(data.photos):
        want = np.array(fake_models.expected_vector(rood[k], 48))
        for _ in range(photo.n):
            assert next(vecs) == pytest.approx(want, abs=1e-5)


async def test_geen_beeld_is_unreadable_en_kapot_is_image_bad(worker):
    img = beeld(320, 240)
    gezicht(img, 2, 2, 48)
    good = als_webp(img)
    png = io.BytesIO()
    Image.fromarray(img).save(png, "PNG")
    previews = [b"", b"geen beeld" * 10, png.getvalue(), good[: len(good) // 2], good]
    data, raw = await echt.faces(worker.settings(), previews)
    codes = [p.code for p in data.photos]
    assert codes == ["unreadable", "unreadable", "unreadable", "image_bad", "ok"]
    assert [p.n for p in data.photos] == [0, 0, 0, 0, 1]
    assert all((p.w, p.h) == (0, 0) for p in data.photos[:4])
    assert len(raw) == wire.FACE_VEC_BYTES


async def test_een_pijp_als_preview_is_unreadable(worker):
    r, w = os.pipe2(os.O_CLOEXEC)
    out = wire.out_memfd("uit")
    try:
        os.write(w, b"RIFF")
        data = await client.call(worker.settings(), Op.FACES, wire.FacesArgs(), fds=[r, out])
    finally:
        wire.close_all([r, w, out])
    assert data["photos"] == [{"code": "unreadable", "w": 0, "h": 0, "faces": [], "n": 0}]


async def test_de_grens_op_pixels(worker):
    """4 MP is de grens (MAX_IMAGE_PIXELS, de waarschuwing als fout): erboven too_big."""
    precies = Image.new("RGB", (2000, 2000), (10, 20, 0))
    erboven = Image.new("RGB", (2001, 2000), (10, 20, 0))
    data, raw = await echt.faces(
        worker.settings(), [echt.webp(precies, lossless=False), echt.webp(erboven, lossless=False)]
    )
    assert [p.code for p in data.photos] == ["ok", "too_big"]
    assert (data.photos[0].w, data.photos[0].h) == (2000, 2000) and raw == b""


async def test_uit_wordt_eerst_leeggemaakt(worker):
    """Een memfd met oude bytes: daarna precies n × 512 B (de app eist dat)."""
    img = beeld(320, 240, rood=30, groen=48)
    gezicht(img, 3, 3, 48)
    fd = echt.preview(als_webp(img))
    out = wire.out_memfd("uit")
    try:
        os.write(out, b"x" * 5000)
        await client.call(worker.settings(), Op.FACES, wire.FacesArgs(), fds=[fd, out])
        assert os.fstat(out).st_size == wire.FACE_VEC_BYTES
    finally:
        wire.close_all([fd, out])


# --- de modellen ------------------------------------------------------------------------------


async def test_een_ontbrekend_model_is_model_missing(tmp_path):
    models = fake_models.install(tmp_path / "modellen", skip=(fake_models.SFACE,))
    with echt.EchteWorker(tmp_path / "w", models) as w:
        caps = await client.caps(w.settings())
        assert {m.name: m.state for m in caps.models} == {
            fake_models.YUNET: "ok",
            fake_models.SFACE: "ontbreekt",
        }
        with pytest.raises(client.AnalyseFailed) as exc:
            await echt.faces(w.settings(), [als_webp(beeld(64, 64))])
        assert exc.value.code == Code.MODEL_MISSING.value


async def test_een_ander_model_of_een_symlink_is_anders(tmp_path):
    models = fake_models.install(tmp_path / "modellen")
    yunet = models.root / fake_models.YUNET
    data = bytearray(yunet.read_bytes())
    data[-1] ^= 1
    yunet.write_bytes(bytes(data))  # dezelfde grootte, een andere sha256
    sface = models.root / fake_models.SFACE
    sface.rename(tmp_path / "elders.onnx")
    sface.symlink_to(tmp_path / "elders.onnx")  # de juiste bytes, maar via een symlink
    with echt.EchteWorker(tmp_path / "w", models) as w:
        caps = await client.caps(w.settings())
        assert {m.state for m in caps.models} == {"anders"}
        with pytest.raises(client.AnalyseFailed) as exc:
            await echt.faces(w.settings(), [als_webp(beeld(64, 64))])
        assert exc.value.code == Code.MODEL_MISSING.value
    assert hashlib.sha256((tmp_path / "elders.onnx").read_bytes()).hexdigest() in (
        models.lock.read_text()
    )


# --- de grens per opdracht ------------------------------------------------------------------


@pytest.fixture(scope="module")
def trage_worker(tmp_path_factory):
    """YuNet met 400 extra nodes: één run op 1024 × 683 duurt ±3 s; terminate stopt ertussen."""
    models = fake_models.install(tmp_path_factory.mktemp("traag") / "modellen", slow=400)
    with echt.EchteWorker(tmp_path_factory.mktemp("traag-w"), models) as w:
        yield w


def _werk(worker) -> int:
    pids = worker.children()
    assert len(pids) == 1, pids
    return pids[0]


async def test_de_deadline_stopt_de_opdracht_met_terminate(trage_worker):
    w = trage_worker
    img = beeld(1024, 683)
    data, _ = await zoek(w, beeld(64, 64))  # het kind start en laadt de modellen
    eerste = _werk(w)
    started = time.monotonic()
    with pytest.raises(client.AnalyseFailed) as exc:
        await zoek(w, img, deadline_ms=1500)
    took = time.monotonic() - started
    assert exc.value.code == Code.TIMEOUT.value
    # terminate tussen twee nodes: ruim binnen de deadline plus KILL_GRACE_S
    assert took < 1.5 + wire.KILL_GRACE_S - 1, took
    # Na een timeout altijd een nieuw kind (bij de volgende opdracht); pid 1 blijft
    assert eerste not in w.children() or _zombie_of_weg(eerste)
    data, _ = await zoek(w, beeld(64, 64))
    assert data.photos[0].code == "ok" and w.proc.poll() is None
    assert _werk(w) != eerste


async def test_een_kind_dat_niet_antwoordt_gaat_weg_met_sigkill(trage_worker):
    w = trage_worker
    await zoek(w, beeld(64, 64))
    eerste = _werk(w)
    os.kill(eerste, signal.SIGSTOP)  # terminate kan niets meer
    started = time.monotonic()
    with pytest.raises(client.AnalyseFailed) as exc:
        await zoek(w, beeld(1024, 683), deadline_ms=500)
    took = time.monotonic() - started
    assert exc.value.code == Code.TIMEOUT.value
    assert 0.5 + wire.KILL_GRACE_S - 0.5 <= took < 0.5 + wire.KILL_GRACE_S + 3, took
    assert _zombie_of_weg(eerste)
    data, _ = await zoek(w, beeld(64, 64))
    assert data.photos[0].code == "ok" and w.proc.poll() is None
    assert _werk(w) != eerste


def _zombie_of_weg(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rpartition(")")[2].split()[0] in ("Z", "X")
    except FileNotFoundError:
        return True
