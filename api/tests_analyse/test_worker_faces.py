"""faces met de echte worker en de nepmodellen (fase 6, X2 "De tests", laag 2; X2a).

De worker draait als eigen proces met --local (echt.EchteWorker), de modellen komen uit
fake_models.py. Een vierkant van 32 × 32 op het raster met blauw 255 en groen g is één gezicht van
g × g px, midden in zijn cel; de vector hangt af van het gemiddelde rood en groen van het
uitgelijnde gezicht (expected_vector).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import signal
import time

import numpy as np
import pytest
from PIL import Image

from app.analyse import client, wire
from app.analyse.wire import Code, Op
from app.analyse.worker.ops import faces as F
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
async def test_elke_maat_ook_geen_veelvoud_van_32(worker, w, h):
    """Een preview die geen veelvoud van 32 is: het kader in de coördinaten van de preview, één
    vector. Het opvullen zelf (K2-05: zonder faalt de echte YuNet op 1024 × 683) bewaakt deze test
    niet: het nepmodel geeft met en zonder opvullen dezelfde cellen (review golf 5, G5-C5). Dat
    doet test_models_real.py::test_laden_met_alleen_de_cpu_en_de_vormen, met de echte YuNet op
    yunet_input() van 1024 × 683 (704 hoog)."""
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


# --- de punten decoderen en uitlijnen (review golf 5, G5-C2) ---------------------------------
#
# Het gewone nepmodel zet de vijf punten precies in het kader, op een effen vlak: een kolom en rij
# die verwisseld zijn, een vergeten stride, een draai de verkeerde kant op of een spiegeling in het
# uitlijnen geven daar dezelfde vector. Deze tests rekenen het na zonder faces.py.


def test_de_uitvoer_van_yunet_gedecodeerd_zoals_opencv():
    """Elk anker van elke stride zoals FaceDetectorYN::postProcess, met de hand uitgerekend: de
    cel is rij × kolommen + kolom, het kader (kolom + b0) · stride en exp(b2) · stride, de punten
    (k + kolom, k + rij) · stride, de score √(cls · obj) met beide eerst tussen 0 en 1. Een
    opgevulde invoer die niet vierkant is (64 × 96), zodat rij en kolom niet te verwisselen zijn."""
    pad_h, pad_w = 64, 96
    rng = np.random.default_rng(62)
    outs: dict[str, np.ndarray] = {}
    kaders, scores, punten = [], [], []
    for s in (8, 16, 32):
        rows, cols = pad_h // s, pad_w // s
        n = rows * cols
        outs[f"cls_{s}"] = rng.uniform(-0.2, 1.2, (1, n, 1)).astype(np.float32)
        outs[f"obj_{s}"] = rng.uniform(-0.2, 1.2, (1, n, 1)).astype(np.float32)
        outs[f"bbox_{s}"] = rng.uniform(-1, 1, (1, n, 4)).astype(np.float32)
        outs[f"kps_{s}"] = rng.uniform(-2, 2, (1, n, 10)).astype(np.float32)
        for r in range(rows):
            for c in range(cols):
                i = r * cols + c
                cls = min(max(float(outs[f"cls_{s}"][0, i, 0]), 0.0), 1.0)
                obj = min(max(float(outs[f"obj_{s}"][0, i, 0]), 0.0), 1.0)
                b = [float(v) for v in outs[f"bbox_{s}"][0, i]]
                k = [float(v) for v in outs[f"kps_{s}"][0, i]]
                bw, bh = math.exp(b[2]) * s, math.exp(b[3]) * s
                kaders.append([(c + b[0]) * s - bw / 2, (r + b[1]) * s - bh / 2, bw, bh])
                scores.append(math.sqrt(cls * obj))
                punten.append([[(k[2 * j] + c) * s, (k[2 * j + 1] + r) * s] for j in range(5)])
    got_kaders, got_scores, got_punten = F.decode_yunet(outs, pad_h, pad_w)
    assert got_kaders.shape == (8 * 12 + 4 * 6 + 2 * 3, 4)
    np.testing.assert_allclose(got_kaders, kaders, rtol=0, atol=1e-9)
    np.testing.assert_allclose(got_scores, scores, rtol=0, atol=1e-12)
    np.testing.assert_allclose(got_punten, punten, rtol=0, atol=1e-9)


DRAAI = 20.0  # graden, met de klok mee in beeldcoördinaten (y omlaag)
VERSCHUIF = (5.0, -3.0)  # px


@pytest.fixture(scope="module")
def gedraaide_worker(tmp_path_factory):
    """YuNet met de vijf punten DRAAI gedraaid om het midden van de cel en VERSCHUIF verschoven."""
    root = tmp_path_factory.mktemp("gedraaid") / "modellen"
    models = fake_models.install(root, draai=DRAAI, verschuif=VERSCHUIF)
    with echt.EchteWorker(tmp_path_factory.mktemp("gedraaid-w"), models) as w:
        yield w


def patroon(w: int, h: int) -> np.ndarray:
    """Rood en groen die over de preview golven, zonder blauw (geen gezicht): een ander stuk, een
    andere draai of een spiegeling van het uitgelijnde gezicht geeft een ander gemiddelde."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    img = np.zeros((h, w, 3), np.uint8)
    img[..., 0] = np.round(127.5 + 127.5 * np.sin(xs / 7.0 + ys / 23.0))
    img[..., 1] = np.round(127.5 + 127.5 * np.cos(ys / 5.0 - xs / 17.0) * np.sin(xs / 11.0))
    return img


def uitgelijnd(img: np.ndarray, midden: tuple[float, float], g: int) -> np.ndarray:
    """Het uitgelijnde gezicht (112 × 112, RGB) zonder faces.py. De punten zijn het sjabloon,
    g / 112 groot, DRAAI gedraaid en naar midden + VERSCHUIF geschoven; het uitlijnen draait dat
    precies terug. Pixel (u, v) komt dus uit midden + VERSCHUIF + g / 112 · R(DRAAI) · (u − 56,
    v − 56) van de preview: bilineair tussen de vier buren, met een rand van nullen om de preview
    (zoals cv2.warpAffine met BORDER_CONSTANT)."""
    rand = 200
    h, w = img.shape[:2]
    groot = np.zeros((h + 2 * rand, w + 2 * rand, 3), np.float64)
    groot[rand : rand + h, rand : rand + w] = img
    cos, sin = math.cos(math.radians(DRAAI)), math.sin(math.radians(DRAAI))
    uit = np.zeros((112, 112, 3), np.float64)
    for v in range(112):
        for u in range(112):
            du, dv = (u - 56) * g / 112, (v - 56) * g / 112
            x = midden[0] + VERSCHUIF[0] + cos * du - sin * dv + rand
            y = midden[1] + VERSCHUIF[1] + sin * du + cos * dv + rand
            x0, y0 = math.floor(x), math.floor(y)
            fx, fy = x - x0, y - y0
            uit[v, u] = (
                groot[y0, x0] * (1 - fx) * (1 - fy)
                + groot[y0, x0 + 1] * fx * (1 - fy)
                + groot[y0 + 1, x0] * (1 - fx) * fy
                + groot[y0 + 1, x0 + 1] * fx * fy
            )
    return uit


def rood_groen(vec: np.ndarray) -> tuple[float, float]:
    """Het gemiddelde rood en groen dat het nep-SFace zag, terug uit de vector: vec · n =
    W0 · rood + W1 · groen + B, drie onbekenden en 128 vergelijkingen."""
    a = np.column_stack([np.array(fake_models.SFACE_W[:2]).T, -vec.astype(np.float64)])
    (rood, groen, _n), *_ = np.linalg.lstsq(a, -np.array(fake_models.SFACE_B), rcond=None)
    return float(rood), float(groen)


async def test_gedraaide_punten_op_een_patroon_zoals_de_rekensom(gedraaide_worker):
    """De hele keten (decoderen, Umeyama, warpAffine, SFace in RGB) tegen uitgelijnd(): een
    gezicht midden in de preview en een aan de rand, waar een deel van het uitgelijnde gezicht
    buiten de preview valt en zwart is."""
    w, h = 352, 288
    img = patroon(w, h)
    gezichten = {(5, 4): 60, (0, 0): 56}  # (kolom, rij): groen; de grootste eerst
    for (kol, rij), g in gezichten.items():
        gezicht(img, kol, rij, g)  # blauw 255 en groen g in de cel, rood blijft het patroon
    data, raw = await zoek(gedraaide_worker, img)
    (photo,) = data.photos
    assert [f.px for f in photo.faces] == [60, 44]  # aan de rand 44 × 44 binnen de preview
    vecs = vectoren(raw)
    assert len(vecs) == 2
    for vec, ((kol, rij), g) in zip(vecs, gezichten.items(), strict=True):
        ref = uitgelijnd(img, ((kol + 0.5) * CEL, (rij + 0.5) * CEL), g)
        rood, groen = float(ref[..., 0].mean()), float(ref[..., 1].mean())
        assert rood_groen(vec) == pytest.approx((rood, groen), abs=0.05)
        assert vec == pytest.approx(np.array(fake_models.expected_vector(rood, groen)), abs=1e-5)


# De test hierboven ziet alleen het gemiddelde rood en groen (het nep-SFace middelt): een
# uitgelijnd gezicht dat getransponeerd of gespiegeld is, of een invoer van SFace met hoogte en
# breedte verwisseld, heeft hetzelfde gemiddelde. De twee tests hieronder leggen de richting vast
# (nacontrole golf 5: zulke mutaties in warp en sface_input bleven in heel tests_analyse groen).


def test_het_uitgelijnde_gezicht_pixel_voor_pixel_zoals_de_rekensom():
    """warp(similarity(punten)) tegen uitgelijnd(), elke pixel: de punten zoals het gedraaide
    nep-YuNet ze zet (midden + VERSCHUIF + g / 112 · R(DRAAI) · (sjabloon − 56)), zonder
    faces.py uitgerekend; een gezicht midden in de preview en een aan de rand (zwart erbuiten)."""
    w, h = 352, 288
    img = patroon(w, h)
    cos, sin = math.cos(math.radians(DRAAI)), math.sin(math.radians(DRAAI))
    for (kol, rij), g in {(5, 4): 60, (0, 0): 56}.items():
        mx, my = (kol + 0.5) * CEL, (rij + 0.5) * CEL
        punten = []
        for tx, ty in fake_models.TEMPLATE:
            dx, dy = (tx - 56) * g / 112, (ty - 56) * g / 112
            punten.append(
                [mx + VERSCHUIF[0] + cos * dx - sin * dy, my + VERSCHUIF[1] + sin * dx + cos * dy]
            )
        got = F.warp(img, F.similarity(np.array(punten, dtype=np.float64)))
        assert got.shape == (112, 112, 3) and got.dtype == np.float32
        np.testing.assert_allclose(got, uitgelijnd(img, (mx, my), g), rtol=0, atol=1e-3)


def test_sface_krijgt_rgb_in_nchw_zonder_draai():
    """sface_input: (1, 3, 112, 112) float32, kanaal c op rij y en kolom x is pixel (y, x) van
    het uitgelijnde gezicht in RGB: niets gedraaid, gespiegeld of van kleur verwisseld."""
    rng = np.random.default_rng(65)
    aligned = rng.uniform(0, 255, (112, 112, 3)).astype(np.float32)
    blob = F.sface_input(aligned)
    assert blob.shape == (1, 3, 112, 112) and blob.dtype == np.float32
    assert blob.flags["C_CONTIGUOUS"]
    for c in range(3):
        for y, x in ((0, 0), (0, 111), (111, 0), (7, 93), (93, 7), (111, 111)):
            assert blob[0, c, y, x] == aligned[y, x, c]
        np.testing.assert_array_equal(blob[0, c], aligned[:, :, c])


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


TRAAG_DEADLINE_MS = 1500
# Eén run van de trage YuNet duurt hier minstens twee keer de deadline: dan valt de deadline
# zeker midden in de run, en moet terminate hem stoppen.
TRAAG_MIN_S = 2 * TRAAG_DEADLINE_MS / 1000


async def _een_trage_run(w) -> float:
    await zoek(w, beeld(64, 64))  # het kind start en laadt de modellen
    started = time.monotonic()
    await zoek(w, beeld(1024, 683))
    return time.monotonic() - started


@pytest.fixture(scope="module")
def trage_worker(tmp_path_factory):
    """YuNet met extra nodes, zo veel dat één run op 1024 × 683 op deze machine minstens
    TRAAG_MIN_S duurt; terminate stopt ertussen.

    De duur van een node hangt af van de cpu: 400 nodes duren in de sandbox ±2,7 s, en op een
    runner van GitHub soms minder dan 1,5 s (CI op f16ed5a). Daarom meten we één run en zetten we
    er zo nodig meer nodes voor, met 20 % marge; lukt het na drie keer niet, dan faalt de fixture.
    """
    slow, gemeten = 400, []
    for _ in range(3):
        models = fake_models.install(tmp_path_factory.mktemp("traag") / "modellen", slow=slow)
        with echt.EchteWorker(tmp_path_factory.mktemp("traag-w"), models) as w:
            run_s = asyncio.run(_een_trage_run(w))
            gemeten.append((slow, round(run_s, 2)))
            if run_s >= TRAAG_MIN_S:
                yield w
                return
        slow = math.ceil(slow * TRAAG_MIN_S * 1.2 / run_s)
    pytest.fail(f"de trage YuNet blijft korter dan {TRAAG_MIN_S} s: (nodes, s) = {gemeten}")


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
        await zoek(w, img, deadline_ms=TRAAG_DEADLINE_MS)
    took = time.monotonic() - started
    assert exc.value.code == Code.TIMEOUT.value
    # terminate tussen twee nodes: ruim binnen de deadline plus KILL_GRACE_S
    assert took < TRAAG_DEADLINE_MS / 1000 + wire.KILL_GRACE_S - 1, took
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
