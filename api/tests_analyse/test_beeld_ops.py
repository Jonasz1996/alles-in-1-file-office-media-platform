"""embed_image, embed_text, rank en forget met de echte worker en de nepmodellen (fase 6, X3b,
laag 2; Keuze 75, 77, 78; K1-08, K1-16).

De worker draait als eigen proces (echt.EchteWorker), met de nepmodellen van gezichten en van
beeld (fake_beeld.py): een rood vlak geeft KLEUR["rood"], de vraag "rood" ook. Een bestand per
eigenaar maakt de test zelf (fake_beeld.vec_bytes) en gaat als fd mee, zoals de app het doet.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image

from app.analyse import client, wire
from app.analyse.wire import Code, Op
from app.beeld import types as vt
from tests_analyse import echt, fake_beeld, fake_models

pytestmark = [pytest.mark.anyio, pytest.mark.analyse_echt]

EVA = "e" * 32
BRAM = "b" * 32
GEHEIM = "geheimwoord7"
SLOT = fake_beeld.DIM * 2


@pytest.fixture(scope="module")
def modellen(tmp_path_factory):
    gezichten = fake_models.install(tmp_path_factory.mktemp("modellen") / "modellen")
    fake_beeld.install(gezichten)
    return gezichten


@pytest.fixture(scope="module")
def worker(tmp_path_factory, modellen):
    with echt.EchteWorker(tmp_path_factory.mktemp("worker"), modellen) as w:
        yield w


def vlak(rgb: tuple[int, int, int], size: tuple[int, int] = (256, 256)) -> bytes:
    return echt.webp(Image.new("RGB", size, rgb))


def rij(vec: np.ndarray) -> np.ndarray:
    return np.asarray(vec, dtype=np.float32) / np.linalg.norm(vec)


async def embed(settings, previews: list[bytes]) -> tuple[wire.EmbedImageData, bytes]:
    fds = [echt.preview(p) for p in previews]
    out = wire.out_memfd("uit")
    try:
        data = await client.call(settings, Op.EMBED_IMAGE, wire.EmbedImageArgs(), fds=[*fds, out])
        raw = os.pread(out, os.fstat(out).st_size, 0)
    finally:
        wire.close_all([*fds, out])
    return wire.EmbedImageData.model_validate(data), raw


async def embed_text(settings, text: str) -> np.ndarray:
    out = wire.out_memfd("uit")
    try:
        await client.call(settings, Op.EMBED_TEXT, wire.EmbedTextArgs(text=text), fds=[out])
        raw = os.pread(out, os.fstat(out).st_size, 0)
    finally:
        os.close(out)
    return np.frombuffer(raw, dtype="<f2").astype(np.float32)


async def rank(settings, data: bytes, args: dict, **kw) -> list[tuple[int, int]]:
    fd = fake_beeld.vec_fd(data)
    try:
        got = await client.call(settings, Op.RANK, args, fds=[fd], **kw)
    finally:
        os.close(fd)
    return [tuple(h) for h in wire.RankData.model_validate(got).hits]


def wereld(n: int, seed: int) -> tuple[list[int], np.ndarray]:
    """n willekeurige vectoren (lengte 1, f16) met oplopende num's met gaten, plus de drie
    kleurvlakken op vaste plekken."""
    r = np.random.default_rng(seed)
    mat = r.standard_normal((n, fake_beeld.DIM)).astype(np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    for i in range(len(fake_beeld.KLEUREN)):
        mat[10 + i] = rij(fake_beeld.expected_image([255 if c == i else 0 for c in range(3)]))
    nums = sorted(int(x) for x in r.choice(10 * n, size=n, replace=False))
    return nums, mat.astype(np.float16)


def brute(nums: list[int], mat16: np.ndarray, query: np.ndarray, k: int, skip=None):
    """De top-k met numpy in de test: cosinus over de f16-vectoren als f32, de hoogste eerst, bij
    gelijke score de kleinste num."""
    scores = mat16.astype(np.float32) @ np.asarray(query, np.float32)
    order = sorted(
        (i for i in range(len(nums)) if i != skip), key=lambda i: (-float(scores[i]), nums[i])
    )
    return [(nums[i], int(np.rint(np.clip(scores[i], -1, 1) * 10_000))) for i in order[:k]]


async def test_embed_image_geeft_768_f16_met_lengte_1(worker):
    previews = [
        vlak((255, 0, 0)),
        vlak((0, 255, 0), (300, 200)),
        vlak((0, 0, 255)),
        b"RIFF\x10\x00\x00\x00WEBPVP8 rommel",  # WebP-kop, kapotte inhoud
        echt.webp(Image.new("RGB", (64, 64), (200, 200, 200)), lossless=False),
    ]
    data, raw = await embed(worker.settings(), previews)
    assert [p.code for p in data.photos] == ["ok", "ok", "ok", "image_bad", "ok"]
    assert len(raw) == len(previews) * SLOT
    rows = np.frombuffer(raw, dtype="<f2").reshape(len(previews), fake_beeld.DIM)
    for i, rgb in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)]):
        v = rows[i].astype(np.float32)
        assert abs(float(np.linalg.norm(v)) - 1.0) < 2e-3
        assert float(v @ rij(fake_beeld.expected_image(rgb))) > 0.999
    assert not rows[3].any()  # een foto die niet ok is: het vak blijft nul
    grijs = rows[4].astype(np.float32)
    assert float(grijs @ rij(fake_beeld.expected_image((200, 200, 200)))) > 0.995


async def test_embed_image_alleen_webp_en_hooguit_4_mp(worker):
    png = Image.new("RGB", (32, 32), (1, 2, 3))
    import io

    buf = io.BytesIO()
    png.save(buf, "PNG")
    groot = vlak((10, 10, 10), (2100, 2000))  # 4,2 MP
    data, raw = await embed(worker.settings(), [buf.getvalue(), groot, vlak((9, 9, 9))])
    assert [p.code for p in data.photos] == ["unreadable", "too_big", "ok"]
    assert not raw[: 2 * SLOT].strip(b"\0")


async def test_twee_keer_dezelfde_uitkomst(worker):
    previews = [vlak((255, 0, 0)), vlak((12, 200, 40))]
    _, een = await embed(worker.settings(), previews)
    _, twee = await embed(worker.settings(), previews)
    assert een == twee
    nums, mat = wereld(500, 2)
    data = fake_beeld.vec_bytes(nums, mat.astype(np.float32), 7)
    args = {"owner": EVA, "gen": 7, "text": "groen", "k": 50}
    assert await rank(worker.settings(), data, args) == await rank(worker.settings(), data, args)


async def test_embed_text_is_de_tekstvector(worker):
    v = await embed_text(worker.settings(), "ROOD")
    assert v.shape == (fake_beeld.DIM,)
    assert abs(float(np.linalg.norm(v)) - 1.0) < 2e-3
    assert float(v @ rij(fake_beeld.expected_text("rood"))) > 0.999
    assert float(v @ rij(fake_beeld.KLEUR["rood"])) > 0.99


async def test_rank_is_brute_force_met_tekst(worker):
    nums, mat = wereld(3000, 3)
    data = fake_beeld.vec_bytes(nums, mat.astype(np.float32), 11)
    for text, k in (("blauw", 1000), ("hond op het strand", 25), ("rood", 1)):
        hits = await rank(worker.settings(), data, {"owner": EVA, "gen": 11, "text": text, "k": k})
        assert len(hits) == k
        want = brute(nums, mat, rij(fake_beeld.expected_text(text)), k)
        assert [h[0] for h in hits] == [w[0] for w in want]
        assert all(abs(a[1] - b[1]) <= 1 for a, b in zip(hits, want, strict=True))
    (top,) = await rank(worker.settings(), data, {"owner": EVA, "gen": 11, "text": "rood", "k": 1})
    assert top[0] == nums[10]  # het rode vlak bovenaan (de zelftest van Keuze 83)


async def test_rank_like_met_een_num(worker):
    """ "Meer zoals deze" stuurt de num; de worker zoekt de vector zelf op, en de foto zelf valt
    weg (K1-08)."""
    nums, mat = wereld(800, 4)
    data = fake_beeld.vec_bytes(nums, mat.astype(np.float32), 3)
    i = 123
    hits = await rank(worker.settings(), data, {"owner": EVA, "gen": 3, "num": nums[i], "k": 40})
    want = brute(nums, mat, mat[i].astype(np.float32), 40, skip=i)
    assert [h[0] for h in hits] == [w[0] for w in want]
    assert nums[i] not in [h[0] for h in hits]
    # een num die niet in het bestand staat: bad_request met de vaste zin (de app: 409)
    with pytest.raises(client.AnalyseFailed) as exc:
        await rank(worker.settings(), data, {"owner": EVA, "gen": 3, "num": 1, "k": 5})
    assert (exc.value.code, exc.value.detail) == (Code.BAD_REQUEST.value, vt.NO_NUM)


async def test_k_0_warmt_op_en_k_hooguit_1000(worker):
    nums, mat = wereld(1200, 5)
    data = fake_beeld.vec_bytes(nums, mat.astype(np.float32), 1)
    assert await rank(worker.settings(), data, {"owner": BRAM, "gen": 1, "k": 0}) == []
    hits = await rank(worker.settings(), data, {"owner": BRAM, "gen": 1, "text": "x", "k": 1000})
    assert len(hits) == 1000
    for bad in (
        {"owner": BRAM, "gen": 1, "text": "x", "k": 1001},
        {"owner": BRAM, "gen": 1, "k": 5},  # geen tekst en geen num
        {"owner": BRAM, "gen": 1, "text": "x", "num": nums[0], "k": 5},
        {"owner": "B" * 32, "gen": 1, "text": "x", "k": 5},
    ):
        with pytest.raises(client.AnalyseFailed) as exc:
            await rank(worker.settings(), data, bad)
        assert exc.value.code == Code.BAD_REQUEST.value


def _kapot(data: bytes) -> dict[str, bytes]:
    head = vt.read_head(data)
    n, dim = head.count, head.dim

    def kop(**kw) -> bytes:
        fields = {**head.__dict__, **kw}
        return vt.pack_head(vt.Head(**fields)) + data[vt.HEAD.size :]

    nums_at, vec_at = head.nums_at(), head.vectors_at()
    swapped = bytearray(data)
    swapped[nums_at : nums_at + 16] = data[nums_at + 8 : nums_at + 16] + data[nums_at : nums_at + 8]
    nan = bytearray(data)
    nan[vec_at + 2 * dim : vec_at + 2 * dim + 2] = np.array([np.nan], "<f2").tobytes()
    return {
        "magic": b"XXXX" + data[4:],
        "model": kop(model="een-ander-model"),
        "dim": kop(dim=512),
        "afgekapt": data[:-2],
        "langer": data + b"\0\0",
        "aantal": kop(count=n + 1),
        "gen": kop(gen=8),
        "volgorde": bytes(swapped),
        "nan": bytes(nan),
        "leeg": b"",
    }


async def test_een_bestand_dat_niet_klopt_geeft_een_code(worker):
    nums, mat = wereld(300, 6)
    data = fake_beeld.vec_bytes(nums, mat.astype(np.float32), 9)
    for naam, kapot in _kapot(data).items():
        with pytest.raises(client.AnalyseFailed) as exc:
            await rank(worker.settings(), kapot, {"owner": EVA, "gen": 9, "text": GEHEIM, "k": 5})
        assert (exc.value.code, exc.value.detail) == (Code.BAD_REQUEST.value, vt.BAD_FILE), naam
        assert GEHEIM not in str(exc.value)
    # een pijp in plaats van een gewoon bestand
    r, w = os.pipe2(os.O_CLOEXEC)
    try:
        with pytest.raises(client.AnalyseFailed) as exc:
            await client.call(worker.settings(), Op.RANK, {"owner": EVA, "gen": 9, "k": 0}, fds=[r])
        assert exc.value.detail == vt.BAD_FILE
    finally:
        os.close(r)
        os.close(w)


async def test_too_big_boven_de_cache(worker):
    """Meer foto's dan de cache houdt: too_big zonder het bestand te lezen (R43). Het bestand
    is sparse: alleen de kop is geschreven."""
    count = 150_000
    head = vt.Head(fake_beeld.MAP, 1, fake_beeld.DIM, count, 2)
    fd = os.memfd_create("groot", os.MFD_CLOEXEC)
    try:
        os.write(fd, vt.pack_head(head))
        os.ftruncate(fd, head.size())
        with pytest.raises(client.AnalyseFailed) as exc:
            await client.call(
                worker.settings(),
                Op.RANK,
                {"owner": EVA, "gen": 2, "text": "rood", "k": 5},
                fds=[fd],
            )
        assert exc.value.code == Code.TOO_BIG.value
    finally:
        os.close(fd)


async def test_forget_haalt_alleen_die_eigenaar_weg(worker):
    nums, mat = wereld(200, 8)
    data = fake_beeld.vec_bytes(nums, mat.astype(np.float32), 4)
    await rank(worker.settings(), data, {"owner": EVA, "gen": 4, "k": 0})
    s = worker.settings()
    assert (await client.call(s, Op.FORGET, wire.ForgetArgs(owner=EVA)))["found"] is True
    assert (await client.call(s, Op.FORGET, wire.ForgetArgs(owner=EVA)))["found"] is False
    with pytest.raises(client.AnalyseFailed) as exc:
        await client.call(s, Op.FORGET, {"owner": "x"})
    assert exc.value.code == Code.BAD_REQUEST.value


async def test_niets_van_de_vraag_op_stderr(worker):
    """K1-16: de tekst van een vraag komt nergens op stderr van pid 1 of een kind, ook niet bij
    een fout (een bestand dat niet klopt, een num die er niet is)."""
    nums, mat = wereld(100, 9)
    data = fake_beeld.vec_bytes(nums, mat.astype(np.float32), 5)
    await rank(worker.settings(), data, {"owner": EVA, "gen": 5, "text": GEHEIM, "k": 3})
    await embed_text(worker.settings(), GEHEIM)
    with pytest.raises(client.AnalyseFailed):
        await rank(worker.settings(), b"x" + data, {"owner": EVA, "gen": 5, "text": GEHEIM, "k": 3})
    assert GEHEIM.encode() not in worker.stderr()


async def test_zoeken_laadt_nooit_pillow(worker):
    """Het kind zoeken decodeert nooit een beeld (Keuze 75): na rank en embed_text staat Pillow
    niet in zijn geheugen."""
    nums, mat = wereld(50, 10)
    data = fake_beeld.vec_bytes(nums, mat.astype(np.float32), 1)
    await rank(worker.settings(), data, {"owner": BRAM, "gen": 1, "text": "rood", "k": 3})
    (maps,) = [_maps(pid) for pid in worker.children() if b"zoeken" in _argv(pid)]
    assert "onnxruntime" in maps and "PIL" not in maps


def _argv(pid: int) -> list[bytes]:
    with open(f"/proc/{pid}/cmdline", "rb") as f:
        return f.read().split(b"\0")


def _maps(pid: int) -> str:
    with open(f"/proc/{pid}/maps") as f:
        return f.read()


async def test_afbreken_met_cancel_en_de_deadline(tmp_path):
    """Een vraag die wordt afgebroken: cancel geeft cancelled, de deadline timeout; het kind
    zoeken doet daarna gewoon de volgende vraag (Keuze 56, K2-02)."""
    gezichten = fake_models.install(tmp_path / "modellen")
    fake_beeld.install(gezichten, slow=400)
    nums, mat = wereld(100, 11)
    data = fake_beeld.vec_bytes(nums, mat.astype(np.float32), 1)
    with echt.EchteWorker(tmp_path / "worker", gezichten) as w:
        s = w.settings()
        fd = fake_beeld.vec_fd(data)
        try:
            job = await client.start(
                s, Op.RANK, {"owner": EVA, "gen": 1, "text": "rood", "k": 5}, fds=[fd]
            )
            assert await client.cancel(s, job.n) is True
            with pytest.raises(client.AnalyseFailed) as exc:
                await job.result()
            assert exc.value.code == Code.CANCELLED.value
            with pytest.raises(client.AnalyseFailed) as exc:
                await client.call(
                    s,
                    Op.RANK,
                    {"owner": EVA, "gen": 1, "text": "rood", "k": 5},
                    fds=[fd],
                    deadline_ms=300,
                )
            assert exc.value.code == Code.TIMEOUT.value
            assert await client.call(s, Op.RANK, {"owner": EVA, "gen": 1, "k": 0}, fds=[fd]) == {
                "hits": []
            }
        finally:
            os.close(fd)
