"""onnxw tegen onnxruntime 1.31.1 (fase 6, X3a; job api-analyse).

onnxruntime laadt elk model dat app/analyse/onnxw.py schrijft, en rekent hetzelfde als een eigen
numpy-versie in float64, tot op 1·10⁻⁵: voor elke op die de nepmodellen van X2a (YuNet en SFace)
en de graaf van SigLIP (Keuze 73) nodig hebben, met dynamische hoogte en breedte, alle zes de
typen als raw_data, DynamicQuantizeMatMul van com.microsoft, en een model dat in stukken naar een
bestand geschreven is. De vorm op de draad zelf toetst tests/analyse/test_onnxw.py (zonder numpy).
Daarnaast DynamicQuantizeMatMul zoals een x86-CPU zonder VNNI hem rekent, nagedaan in numpy: met
het gewicht van de omzetter kapt daar niets af (CI op 7569d48).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from numpy.lib.stride_tricks import sliding_window_view

from app.analyse import onnxw
from app.analyse.onnxw import FLOAT, FLOAT16, INT8, INT32, INT64, UINT8, Graph, Model
from app.analyse.worker.omzetten import bouw
from app.analyse.worker.omzetten.bouw import per_kolom

TOL = 1e-5


def rng(seed: int = 73) -> np.random.Generator:
    return np.random.default_rng(seed)


def f32(*shape: int, seed: int = 73, scale: float = 1.0) -> np.ndarray:
    return (rng(seed).standard_normal(shape) * scale).astype(np.float32)


def run(sessie, g: Graph, feeds: dict) -> list[np.ndarray]:
    return sessie(Model(g).to_bytes()).run(None, feeds)


def same(got: np.ndarray, want: np.ndarray) -> None:
    """Dezelfde vorm, float32, en hooguit TOL verschil met de numpy-versie (in float64)."""
    assert got.dtype == np.float32
    assert got.shape == want.shape
    diff = float(np.max(np.abs(got.astype(np.float64) - want))) if got.size else 0.0
    assert diff <= TOL, diff


def one_op(op: str, x: np.ndarray, inits: tuple = (), **attrs) -> Graph:
    """Een graaf met één op: invoer x (FLOAT), daarna de initializers als numpy-arrays."""
    types = {
        np.float32: FLOAT,
        np.int64: INT64,
        np.int8: INT8,
        np.uint8: UINT8,
        np.int32: INT32,
        np.float16: FLOAT16,
    }
    g = Graph(op.lower())
    names = [g.input("x", FLOAT, list(x.shape))]
    for i, arr in enumerate(inits):
        names.append(g.initializer(f"i{i}", types[arr.dtype.type], list(arr.shape), arr))
    g.op(op, names, outputs="y", **attrs)
    g.output("y", FLOAT, None)
    return g


def erf(x: np.ndarray) -> np.ndarray:
    return np.vectorize(math.erf)(x.astype(np.float64))


# --- elementwise ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "ref", "positive"),
    [
        ("Sqrt", np.sqrt, True),
        ("Erf", erf, False),
        ("Tanh", np.tanh, False),
        ("Sigmoid", lambda x: 1 / (1 + np.exp(-x)), False),
    ],
)
def test_een_op_op_elk_element(sessie, op, ref, positive):
    x = f32(2, 3, 5)
    if positive:
        x = np.abs(x) + np.float32(0.01)
    (y,) = run(sessie, one_op(op, x), {"x": x})
    same(y, ref(x.astype(np.float64)))


@pytest.mark.parametrize(
    ("op", "ref"),
    [
        ("Add", np.add),
        ("Sub", np.subtract),
        ("Mul", np.multiply),
        ("Div", np.divide),
        ("Pow", np.power),
    ],
)
@pytest.mark.parametrize("shape", [(4,), (1, 3, 1), ()])
def test_twee_invoeren_met_broadcast(sessie, op, ref, shape):
    x = np.abs(f32(2, 3, 4)) + np.float32(0.5)  # positief: Pow met een gebroken macht
    c = (np.abs(f32(*shape, seed=1)) + np.float32(0.5)).astype(np.float32)
    (y,) = run(sessie, one_op(op, x, (c,)), {"x": x})
    same(y, ref(x.astype(np.float64), c.astype(np.float64)))


# --- lineair ----------------------------------------------------------------------------------


def test_matmul_met_gewichten_en_tussen_invoeren(sessie):
    x = f32(2, 5, 8)
    w = f32(8, 6, seed=2)
    (y,) = run(sessie, one_op("MatMul", x, (w,)), {"x": x})
    same(y, x.astype(np.float64) @ w)
    # zoals de aandacht: [b, koppen, t, d] @ [b, koppen, d, t]
    g = Graph("aandacht")
    g.input("q", FLOAT, [1, 2, "t", 4])
    g.input("k", FLOAT, [1, 2, 4, "t"])
    g.op("MatMul", ["q", "k"], outputs="s")
    g.output("s", FLOAT, [1, 2, "t", "t"])
    q, k = f32(1, 2, 7, 4, seed=3), f32(1, 2, 4, 7, seed=4)
    (s,) = run(sessie, g, {"q": q, "k": k})
    same(s, q.astype(np.float64) @ k)


def test_gemm(sessie):
    a, w, b = f32(3, 8), f32(6, 8, seed=2), f32(6, seed=3)
    (y,) = run(sessie, one_op("Gemm", a, (w, b), transB=1, alpha=0.5, beta=2.0), {"x": a})
    same(y, 0.5 * (a.astype(np.float64) @ w.T) + 2.0 * b)


def _dqmm_invoer() -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
    """De invoer van test_dynamic_quantize_matmul: k op het rooster −128..127 (beide uitersten
    erin), x = k·s met s = 2⁻⁶, en een gewicht W [16, 6]. onnxruntime kiest voor deze x zelf
    schaal s en nulpunt 128: de invoer als uint8 is dan precies k + 128, van 0 tot 255."""
    s = 2.0**-6
    k = rng(5).integers(-128, 128, size=(2, 3, 16))
    k[0, 0, 0], k[0, 0, 1] = -128, 127
    w = rng(6).normal(0, 0.05, size=(16, 6))
    return k, (k * s).astype(np.float32), s, w


@pytest.mark.parametrize("met_nulpunt", [True, False])
def test_dynamic_quantize_matmul(sessie, met_nulpunt):
    """com.microsoft DynamicQuantizeMatMul zoals de omzetter hem gebruikt (Keuze 73): het gewicht
    uit bouw.per_kolom, int8 met een schaal per kolom en |q| ≤ 64. De invoer ligt op het rooster
    dat onnxruntime zelf kiest (min −128·s, max 127·s, s = 2⁻⁶): zo is de kwantisering van de
    invoer exact en is het verschil met numpy alleen afronding. Met |q| tot 127 faalde dit op een
    runner zonder VNNI (CI op 7569d48: verschil 0,167): daar telt onnxruntime twee buren in int16
    op, en dat liep over. Met 64 kan dat niet meer (zie de tests hieronder, die dat op elke CPU
    nadoen)."""
    _, x, _, w = _dqmm_invoer()
    q, scale = per_kolom(w)
    bias = f32(6, seed=7)
    g = Graph("dqmm")
    g.input("x", FLOAT, [2, "t", 16])
    names = ["x", g.initializer("w", INT8, [16, 6], q), g.initializer("s", FLOAT, [6], scale)]
    if met_nulpunt:
        names.append(g.initializer("z", INT8, [6], np.zeros(6, np.int8)))
        names.append(g.initializer("b", FLOAT, [6], bias))
    else:
        names += ["", ""]  # weggelaten optionele invoer
    g.op("DynamicQuantizeMatMul", names, outputs="y", domain=onnxw.MS_DOMAIN)
    g.output("y", FLOAT, [2, "t", 6])
    (y,) = run(sessie, g, {"x": x})
    want = x.astype(np.float64) @ (q.astype(np.float64) * scale)
    same(y, want + bias if met_nulpunt else want)
    assert q.dtype == np.int8 and int(np.abs(q.astype(np.int16)).max()) == 64


# --- DynamicQuantizeMatMul op een CPU zonder VNNI -----------------------------------------------
#
# onnxruntime maakt van de invoer uint8 en rekent met MLAS (uint8 maal int8). Op een x86-CPU zonder
# AVX-VNNI of AVX512-VNNI is dat vpmaddubsw: per paar buren langs K a·b + a'·b' in int16, afgekapt
# op −32.768..32.767, daarna opgeteld in int32. Met VNNI kapt niets af. Deze tests doen het zonder
# VNNI na in numpy, dus op elke CPU, ook op een runner of host met VNNI.

INT16 = (-32768, 32767)
# Het verschil van test_dynamic_quantize_matmul met |q| tot 127, op de runner zonder VNNI van CI
# op 7569d48 (run 43758619443; [True] en [False] gaven 0,16686297…)
CI_7569D48 = 0.166862974


def _paren(a: np.ndarray, q: np.ndarray) -> np.ndarray:
    """a uint8 [m, K], q int8 [K, n]: a_k·q_k + a_k+1·q_k+1 voor elk paar buren (k even), exact,
    als [m, K/2, n]."""
    a, q = a.astype(np.int64), q.astype(np.int64)
    return a[:, 0::2, None] * q[None, 0::2, :] + a[:, 1::2, None] * q[None, 1::2, :]


def _zonder_vnni(a: np.ndarray, nulpunt: int, q: np.ndarray) -> np.ndarray:
    """Σ_k (a_k − nulpunt)·q_k zoals MLAS zonder VNNI: elk paar afgekapt op int16 (vpmaddubsw), de
    paren opgeteld in int32, dan het nulpunt eraf met de som van de kolom (die is exact)."""
    return np.clip(_paren(a, q), *INT16).sum(axis=1) - nulpunt * q.astype(np.int64).sum(axis=0)


def _exact(a: np.ndarray, nulpunt: int, q: np.ndarray) -> np.ndarray:
    return (a.astype(np.int64) - nulpunt) @ q.astype(np.int64)


def _slechtst(q: np.ndarray) -> np.ndarray:
    """De ergste invoer voor elke kolom: 255 waar q > 0 (en verder 0) maakt elk paar zo groot
    mogelijk, 255 waar q < 0 zo klein mogelijk. Twee rijen per kolom, en alles 0 en alles 255."""
    rijen = [np.where(q[:, j] > 0, 255, 0) for j in range(q.shape[1])]
    rijen += [np.where(q[:, j] < 0, 255, 0) for j in range(q.shape[1])]
    rijen += [np.zeros(q.shape[0]), np.full(q.shape[0], 255)]
    return np.array(rijen, dtype=np.uint8)


def _uitersten() -> np.ndarray:
    """Een gewicht W [64, 8] waarin elke kolom twee paren buren op zijn grootste |w| heeft: q wordt
    daar 64, 64 en −64, −64, het ergste geval voor een paar."""
    w = f32(64, 8, seed=8, scale=0.05)
    w[0:2], w[2:4] = 1.0, -1.0
    return w


def test_zonder_vnni_rekent_de_omzetter_exact():
    """Met het gewicht van bouw.per_kolom kapt geen paar af, op de ergste invoer (255 en 0 per
    kolom gekozen) en op het rooster van test_dynamic_quantize_matmul, met elk nulpunt: het model
    zonder VNNI geeft precies het product. Het ergste paar haalt ±255·64·2 = ±32.640."""
    k, _, _, w = _dqmm_invoer()
    grens, top = 0, 0
    for gewicht, rooster in ((w, (k + 128).reshape(-1, 16)), (_uitersten(), None)):
        q, _ = per_kolom(gewicht)
        a = _slechtst(q) if rooster is None else np.concatenate([_slechtst(q), rooster])
        for nulpunt in (0, 128, 255):
            assert np.array_equal(_zonder_vnni(a, nulpunt, q), _exact(a, nulpunt, q)), nulpunt
        grens = max(grens, int(np.abs(_paren(a, q)).max()))
        top = max(top, int(np.abs(q.astype(np.int16)).max()))
    assert grens == 255 * 64 * 2 == 32640
    assert top == 64


@pytest.mark.parametrize("q_max", [65, 127])
def test_zonder_vnni_kapt_een_gewicht_boven_64_af(monkeypatch, q_max):
    """De grens is scherp: met q tot 65 (of 127, zoals tot en met 7569d48) kapt het model zonder
    VNNI een paar af op de ergste invoer, en is de uitkomst niet meer het product."""
    monkeypatch.setattr(bouw, "Q_MAX", q_max)
    q, _ = per_kolom(_uitersten())
    assert int(np.abs(q.astype(np.int16)).max()) == q_max
    a = _slechtst(q)
    assert int(_paren(a, q).max()) == 255 * q_max * 2 > INT16[1]
    assert not np.array_equal(_zonder_vnni(a, 128, q), _exact(a, 128, q))


def test_het_model_zonder_vnni_geeft_het_verschil_van_ci(monkeypatch):
    """Het model is de CPU van CI: met |q| tot 127 geeft het voor test_dynamic_quantize_matmul het
    verschil dat de runner zonder VNNI gaf (op 8 cijfers), met 64 geen verschil."""
    k, x, s, w = _dqmm_invoer()
    a = (k + 128).reshape(-1, 16).astype(np.uint8)
    verschil = {}
    for q_max in (127, 64):
        monkeypatch.setattr(bouw, "Q_MAX", q_max)
        q, scale = per_kolom(w)
        want = x.astype(np.float64).reshape(-1, 16) @ (q.astype(np.float64) * scale)
        got = _zonder_vnni(a, 128, q) * (s * scale.astype(np.float64))
        verschil[q_max] = float(np.abs(got - want).max())
    assert verschil[127] == pytest.approx(CI_7569D48, abs=1e-8)
    assert verschil[64] <= 1e-12


# --- normaliseren -----------------------------------------------------------------------------


def test_softmax(sessie):
    x = f32(2, 3, 7, scale=3.0)
    (y,) = run(sessie, one_op("Softmax", x, axis=-1), {"x": x})
    e = np.exp(x.astype(np.float64) - x.max(axis=-1, keepdims=True))
    same(y, e / e.sum(axis=-1, keepdims=True))


def test_layer_normalization(sessie):
    x, scale, bias = f32(2, 5, 16), f32(16, seed=2), f32(16, seed=3)
    g = one_op("LayerNormalization", x, (scale, bias), axis=-1, epsilon=1e-6)
    (y,) = run(sessie, g, {"x": x})
    x64 = x.astype(np.float64)
    mean, var = x64.mean(axis=-1, keepdims=True), x64.var(axis=-1, keepdims=True)
    same(y, (x64 - mean) / np.sqrt(var + 1e-6) * scale + bias)


@pytest.mark.parametrize("keepdims", [0, 1])
def test_reduce_mean(sessie, keepdims):
    x = f32(2, 6, 4)
    (y,) = run(sessie, one_op("ReduceMean", x, axes=[1], keepdims=keepdims), {"x": x})
    same(y, x.astype(np.float64).mean(axis=1, keepdims=bool(keepdims)))


# --- vormen -----------------------------------------------------------------------------------


def test_transpose(sessie):
    x = f32(1, 4, 2, 3)
    (y,) = run(sessie, one_op("Transpose", x, perm=[0, 2, 1, 3]), {"x": x})
    same(y, x.transpose(0, 2, 1, 3).astype(np.float64))


def test_reshape_met_0_en_min_1(sessie):
    x = f32(2, 3, 8)
    shape = np.array([0, -1, 4], np.int64)
    (y,) = run(sessie, one_op("Reshape", x, (shape,)), {"x": x})
    same(y, x.reshape(2, 6, 4).astype(np.float64))


def test_flatten(sessie):
    x = f32(2, 3, 4)
    (y,) = run(sessie, one_op("Flatten", x, axis=1), {"x": x})
    same(y, x.reshape(2, 12).astype(np.float64))


def test_concat(sessie):
    x, c = f32(1, 3, 4), f32(1, 2, 4, seed=2)
    (y,) = run(sessie, one_op("Concat", x, (c,), axis=1), {"x": x})
    same(y, np.concatenate([x, c], axis=1).astype(np.float64))


def test_gather_een_embedding_en_het_laatste_token(sessie):
    table, ids = f32(10, 4), np.array([[3, 0, 9, 3, 1]], np.int64)
    g = Graph("gather")
    g.input("ids", INT64, [1, "t"])
    g.initializer("tabel", FLOAT, [10, 4], table)
    e = g.op("Gather", ["tabel", "ids"], axis=0)
    g.initializer("laatste", INT64, [], np.array(4, np.int64))
    g.op("Gather", [e, "laatste"], outputs="y", axis=1)
    g.output(e, FLOAT, [1, "t", 4])
    g.output("y", FLOAT, [1, 4])
    emb, last = run(sessie, g, {"ids": ids})
    same(emb, table[ids].astype(np.float64))
    same(last, table[ids][:, 4].astype(np.float64))


def test_slice(sessie):
    x = f32(2, 10, 4)
    inits = tuple(np.array([v], np.int64) for v in (1, 9, 1, 2))  # starts, ends, axes, steps
    (y,) = run(sessie, one_op("Slice", x, inits), {"x": x})
    same(y, x[:, 1:9:2].astype(np.float64))


def test_squeeze_en_unsqueeze(sessie):
    x = f32(2, 1, 3)
    g = Graph("squeeze")
    g.input("x", FLOAT, [2, 1, 3])
    g.initializer("as1", INT64, [1], np.array([1], np.int64))
    g.initializer("as0", INT64, [1], np.array([0], np.int64))
    s = g.op("Squeeze", ["x", "as1"], outputs="s")
    g.op("Unsqueeze", [s, "as0"], outputs="u")
    g.output("s", FLOAT, [2, 3])
    g.output("u", FLOAT, [1, 2, 3])
    sq, un = run(sessie, g, {"x": x})
    same(sq, x[:, 0].astype(np.float64))
    same(un, x[None, :, 0].astype(np.float64))


def test_cast_van_elk_type_als_raw_data(sessie):
    """Alle zes typen als raw_data van een initializer, terug naar float32 met Cast; en een
    uitvoer als int64."""
    values = {
        "f32": (FLOAT, np.array([1.5, -2.25], np.float32)),
        "f16": (FLOAT16, np.array([0.5, -65504.0], np.float16)),
        "i8": (INT8, np.array([-128, 127], np.int8)),
        "u8": (UINT8, np.array([0, 255], np.uint8)),
        "i32": (INT32, np.array([-(2**31), 2**31 - 1], np.int32)),
        "i64": (INT64, np.array([-(2**40), 2**40], np.int64)),
    }
    g = Graph("cast")
    g.input("x", FLOAT, [2])
    for name, (dtype, arr) in values.items():
        g.initializer(name, dtype, [2], arr)
        g.op("Cast", [name], outputs=f"{name}_f", to=FLOAT)
        g.output(f"{name}_f", FLOAT, [2])
    g.op("Cast", ["x"], outputs="geheel", to=INT64)
    g.output("geheel", INT64, [2])
    *floats, geheel = run(sessie, g, {"x": np.array([1.7, -2.7], np.float32)})
    for got, (_, arr) in zip(floats, values.values(), strict=True):
        assert got.dtype == np.float32
        assert got.tolist() == arr.astype(np.float32).tolist()
    assert geheel.dtype == np.int64
    assert geheel.tolist() == [1, -2]


# --- beelden met een dynamische hoogte en breedte ---------------------------------------------


def conv_ref(x: np.ndarray, w: np.ndarray, b: np.ndarray, stride: int, pad: int) -> np.ndarray:
    x = np.pad(x.astype(np.float64), ((0, 0), (0, 0), (pad, pad), (pad, pad)))
    win = sliding_window_view(x, w.shape[2:], axis=(2, 3))[:, :, ::stride, ::stride]
    return np.einsum("ncijkl,ockl->noij", win, w.astype(np.float64)) + b[None, :, None, None]


def pool_ref(x: np.ndarray, kernel: int, stride: int, pad: int) -> np.ndarray:
    """AveragePool met count_include_pad 0: het gemiddelde over de cellen binnen het beeld."""
    widths = ((0, 0), (0, 0), (pad, pad), (pad, pad))
    padded = np.pad(x.astype(np.float64), widths)
    inside = np.pad(np.ones_like(x, np.float64), widths)
    s = sliding_window_view(padded, (kernel, kernel), axis=(2, 3))[:, :, ::stride, ::stride]
    n = sliding_window_view(inside, (kernel, kernel), axis=(2, 3))[:, :, ::stride, ::stride]
    return s.sum(axis=(-2, -1)) / n.sum(axis=(-2, -1))


@pytest.mark.parametrize(
    ("kernel", "stride", "pad"),
    [(3, 2, 1), (4, 4, 0)],  # een gewone conv, en een als patch-embedding (kern = stap)
)
def test_conv_met_dynamische_hoogte_en_breedte(sessie, kernel, stride, pad):
    w, b = f32(4, 3, kernel, kernel, seed=2, scale=0.2), f32(4, seed=3)
    g = Graph("conv")
    g.input("input", FLOAT, [1, 3, "height", "width"])
    g.initializer("w", FLOAT, list(w.shape), w)
    g.initializer("b", FLOAT, [4], b)
    attrs = {"kernel_shape": [kernel, kernel], "strides": [stride, stride]}
    g.op("Conv", ["input", "w", "b"], outputs="y", pads=[pad] * 4, **attrs)
    g.output("y", FLOAT, [1, 4, "h", "w"])
    s = sessie(Model(g).to_bytes())
    assert s.get_inputs()[0].shape == [1, 3, "height", "width"]
    for h, wd in [(32, 48), (37, 21)]:
        x = f32(1, 3, h, wd, seed=h)
        (y,) = s.run(None, {"input": x})
        same(y, conv_ref(x, w, b, stride, pad))


@pytest.mark.parametrize(("kernel", "stride", "pad"), [(2, 2, 0), (8, 8, 0), (3, 1, 1)])
def test_average_pool_met_dynamische_hoogte_en_breedte(sessie, kernel, stride, pad):
    g = Graph("pool")
    g.input("input", FLOAT, [1, 3, "height", "width"])
    attrs = {"kernel_shape": [kernel, kernel], "strides": [stride, stride], "pads": [pad] * 4}
    g.op("AveragePool", ["input"], outputs="y", count_include_pad=0, **attrs)
    g.output("y", FLOAT, [1, 3, "h", "w"])
    s = sessie(Model(g).to_bytes())
    for h, wd in [(32, 48), (40, 17)]:
        x = f32(1, 3, h, wd, seed=wd)
        (y,) = s.run(None, {"input": x})
        same(y, pool_ref(x, kernel, stride, pad))


# --- samen, en uit een bestand ----------------------------------------------------------------


def attention_block(g: Graph, x: str, d: int, heads: int, w: dict, calls: list | None) -> str:
    """Een blok als in SigLIP: LayerNorm, aandacht met koppen, residu, LayerNorm, MLP met GELU
    (tanh), residu. w heeft de gewichten als numpy-arrays; met calls gaat elke matrix als
    callable (zoals de omzetter hem uit numpy.memmap haalt), en telt calls de aanroepen."""

    def c(name: str) -> str:
        arr = w[name]
        if calls is not None and arr.ndim == 2:

            def later() -> np.ndarray:
                calls.append(name)
                return arr

            return g.initializer(name, FLOAT, list(arr.shape), later)
        return g.initializer(name, FLOAT, list(arr.shape), arr)

    def const(value: float) -> str:
        return g.initializer(None, FLOAT, [], np.array(value, np.float32))

    def lin(h: str, name: str) -> str:
        return g.op("Add", [g.op("MatMul", [h, c(name + "_w")]), c(name + "_b")])

    def split(h: str, perm: list[int]) -> str:
        shape = g.initializer(None, INT64, [4], np.array([0, 0, heads, d // heads], np.int64))
        return g.op("Transpose", [g.op("Reshape", [h, shape])], perm=perm)

    h = g.op("LayerNormalization", [x, c("ln1_s"), c("ln1_b")], axis=-1, epsilon=1e-6)
    q = split(lin(h, "q"), [0, 2, 1, 3])
    k = split(lin(h, "k"), [0, 2, 3, 1])
    v = split(lin(h, "v"), [0, 2, 1, 3])
    att = g.op("Mul", [g.op("MatMul", [q, k]), const((d // heads) ** -0.5)])
    o = g.op("MatMul", [g.op("Softmax", [att], axis=-1), v])
    o = g.op("Transpose", [o], perm=[0, 2, 1, 3])
    merge = g.initializer(None, INT64, [3], np.array([0, 0, d], np.int64))
    x = g.op("Add", [x, lin(g.op("Reshape", [o, merge]), "o")])
    h = lin(g.op("LayerNormalization", [x, c("ln2_s"), c("ln2_b")], axis=-1, epsilon=1e-6), "fc1")
    inner = g.op("Add", [h, g.op("Mul", [g.op("Pow", [h, const(3.0)]), const(0.044715)])])
    t = g.op("Tanh", [g.op("Mul", [inner, const(math.sqrt(2 / math.pi))])])
    h = g.op("Mul", [g.op("Mul", [h, const(0.5)]), g.op("Add", [t, const(1.0)])])
    return g.op("Add", [x, lin(h, "fc2")])


def block_ref(x: np.ndarray, d: int, heads: int, w: dict[str, np.ndarray]) -> np.ndarray:
    w = {k: v.astype(np.float64) for k, v in w.items()}
    x = x.astype(np.float64)

    def ln(h, n):
        m, v = h.mean(-1, keepdims=True), h.var(-1, keepdims=True)
        return (h - m) / np.sqrt(v + 1e-6) * w[n + "_s"] + w[n + "_b"]

    def lin(h, n):
        return h @ w[n + "_w"] + w[n + "_b"]

    b, t, _ = x.shape
    hd = d // heads
    h = ln(x, "ln1")
    q, k, v = (lin(h, n).reshape(b, t, heads, hd).transpose(0, 2, 1, 3) for n in "qkv")
    a = q @ k.transpose(0, 1, 3, 2) * hd**-0.5
    a = np.exp(a - a.max(-1, keepdims=True))
    a /= a.sum(-1, keepdims=True)
    x = x + lin((a @ v).transpose(0, 2, 1, 3).reshape(b, t, d), "o")
    h = lin(ln(x, "ln2"), "fc1")
    h = 0.5 * h * (1 + np.tanh(math.sqrt(2 / math.pi) * (h + 0.044715 * h**3)))
    return x + lin(h, "fc2")


def block_weights(d: int, mlp: int) -> dict[str, np.ndarray]:
    shapes = {"ln1_s": (d,), "ln1_b": (d,), "ln2_s": (d,), "ln2_b": (d,)}
    for n in "qko":
        shapes |= {f"{n}_w": (d, d), f"{n}_b": (d,)}
    shapes |= {"v_w": (d, d), "v_b": (d,), "fc1_w": (d, mlp), "fc1_b": (mlp,)}
    shapes |= {"fc2_w": (mlp, d), "fc2_b": (d,)}
    return {n: f32(*s, seed=i, scale=0.2) for i, (n, s) in enumerate(sorted(shapes.items()))}


def test_een_blok_als_siglip_in_stukken_naar_een_bestand(sessie, tmp_path):
    """Het blok van SigLIP in het klein (breedte 16, 2 koppen, MLP 32), met een dynamisch aantal
    tokens; de gewichten als callables (zoals de omzetter ze uit numpy.memmap haalt), in stukken
    naar een bestand geschreven en van dat pad geladen."""
    d, heads = 16, 2
    w = block_weights(d, 32)
    calls: list[str] = []
    graphs = []
    for c in (None, calls):  # eerst met de arrays zelf, dan met callables
        g = Graph("blok")
        g.input("x", FLOAT, [1, "t", d])
        g.output(attention_block(g, "x", d, heads, w, c), FLOAT, [1, "t", d])
        graphs.append(g)
    g, lazy = graphs
    written = Model(lazy).write(tmp_path / "blok.onnx")
    assert calls == ["q_w", "k_w", "v_w", "o_w", "fc1_w", "fc2_w"]  # elk één keer
    assert (tmp_path / "blok.onnx").read_bytes() == Model(g).to_bytes()  # dezelfde bytes
    from_bytes = sessie(Model(g).to_bytes())
    from_file = sessie(tmp_path / "blok.onnx")
    assert written.size == (tmp_path / "blok.onnx").stat().st_size
    for t in (1, 5, 9):
        x = f32(1, t, d, seed=t)
        (a,) = from_bytes.run(None, {"x": x})
        (b,) = from_file.run(None, {"x": x})
        assert np.array_equal(a, b)
        same(a, block_ref(x, d, heads, w))


def test_het_model_zelf(sessie):
    """IR 8, opset 17 en de producer zoals onnxw ze schrijft; onnxruntime leest ze zo terug."""
    g = Graph("meta")
    g.input("x", FLOAT, [1])
    g.op("Relu", ["x"], outputs="y")
    g.output("y", FLOAT, [1])
    s = sessie(Model(g).to_bytes())
    meta = s.get_modelmeta()
    assert (meta.producer_name, meta.graph_name) == ("platform-onnxw", "meta")
    assert [(i.name, i.type, i.shape) for i in s.get_inputs()] == [("x", "tensor(float)", [1])]
    assert [(o.name, o.type, o.shape) for o in s.get_outputs()] == [("y", "tensor(float)", [1])]
