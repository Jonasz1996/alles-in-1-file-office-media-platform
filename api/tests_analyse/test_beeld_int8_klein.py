"""De haken van de omzetter voor het int8-rapport van BZ4, en dat rapport zelf, op het kleine model
(fase 6, X3a; x3a.md, "CI op 3a9b07d" en "CI op 011bf2d"; job api-analyse, niet de marker
beeld_echt).

bouw.py heeft opties (fp32_als, per_token, glad, q_max, meet en bij de tekst embedding_fp32) die
op hun standaard de omzetting van VERSIE geven: sinds VERSIE 3 per token, en per_token=False is de
vorm van VERSIE 2; test_beeld_omzetten.py bewijst beide met de bytes. Hier: elke variant bouwt en
rekent (cosinus ≥ 0,99 met de numpy-versie), en elke optie doet wat ze belooft:

- per_token (de standaard): een foto of vraag alleen geeft dezelfde vector als in een batch, ook
  naast een foto met een heel ander bereik (per tensor, DynamicQuantizeMatMul, niet: één schaal
  voor de hele batch);
- fp32_als voor elke Linear (en bij de tekst embedding_fp32): de fp32-graaf, binnen TOL;
- glad met machten van 2: in fp32 precies gelijk; in int8 kwantiseert per_kolom het gladde
  gewicht; een factor die geen macht van 2 is, is een OmzetFout;
- q_max: de grootste |q| per kolom, met de schalen van Keuze 73;
- meet: de invoer van elke Linear en de residu na elke laag als uitvoer, zonder de gewone
  uitvoer te veranderen.

Met één neuron × 100 (E6 van de analyse) eist de test alleen wat zeker is (per token los van de
batch, fp32 blijft fp32); de cosinussen staan in de uitvoer (pytest -s): per token helpt daar
niet, het is een uitschieter per kanaal. Dan de rooktest van het rapport zelf
(test_beeld_int8_lagen.py en _rapport_int8 van test_beeld_kwaliteit.py) op dit model, en de
regels van "CI op 011bf2d" (meer zoals deze per categorie en top 1, de zwakke vragen) met
nepvectoren waarvan de uitkomst vastligt.
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.analyse.onnxw import Model as Onnx
from app.analyse.tokenizer import Tokenizer
from app.analyse.worker import omzetten
from app.analyse.worker.omzetten import OmzetFout, bouw
from app.analyse.worker.omzetten.safetensors import Safetensors
from tests_analyse import siglip_klein as klein
from tests_analyse import test_beeld_kwaliteit as kwaliteit
from tests_analyse.beeld_echt import Model, Verslag, vragen
from tests_analyse.test_beeld_int8_lagen import (
    GLAD,
    IJK_ZINNEN,
    Rapport,
    factoren,
    gewicht,
    kort_label,
    linears,
    probeer,
)
from tests_analyse.test_beeld_kwaliteit import _rapport_int8
from tests_analyse.test_beeld_omzetten import (
    COS,
    TEKSTEN,
    TOL,
    _cos,
    _laag,
    _linears,
    _ln,
    _pixels,
    _q8_per_kolom,
    _w,
    _wt,
    lees_onnx,
    ref_beeld,
    ref_tekst,
)

KOP_Q = "vision_model.head.attention.q"  # de vraag van de MAP-kop: een constante (de probe)
ALLEEN = 1e-6  # een foto of vraag alleen tegen in een batch, met per_token


def _sessie(g):
    import onnxruntime

    o = onnxruntime.SessionOptions()
    o.intra_op_num_threads = 1
    o.inter_op_num_threads = 1
    return onnxruntime.InferenceSession(Onnx(g).to_bytes(), o, providers=["CPUExecutionProvider"])


def _laad(bron: Path) -> tuple[Safetensors, bouw.Vorm]:
    st = Safetensors(bron / omzetten.SAFETENSORS)
    return st, bouw.lees_config(json.loads((bron / omzetten.CONFIG).read_text()))


@pytest.fixture(scope="module")
def km(tmp_path_factory):
    """Het kleine model: de bron, de omzetting met --fp32, de gewichten en de vorm."""
    basis = tmp_path_factory.mktemp("int8_klein")
    w = klein.schrijf_bron(basis / "bron")
    (basis / "uit").mkdir()
    bouw.omzet(basis / "bron", basis / "uit", fp32=True)
    st, vorm = _laad(basis / "bron")
    tok = Tokenizer.load(basis / "uit" / omzetten.TOKENIZER_BIN)
    tokens = np.array([tok.encode(t) for t in TEKSTEN], dtype=np.int64)
    return SimpleNamespace(basis=basis, w=w, st=st, vorm=vorm, tok=tok, tokens=tokens)


def _graaf(km, toren: str, **opties):
    maak = bouw.beeld_graaf if toren == "beeld" else bouw.tekst_graaf
    return maak(km.st, km.vorm, int8=opties.pop("int8", True), **opties)


def _draai(km, toren: str, s, data=None) -> np.ndarray:
    if toren == "beeld":
        return s.run([omzetten.UIT_BEELD], {"pixels": _pixels() if data is None else data})[0]
    return s.run([omzetten.UIT_TEKST], {"tokens": km.tokens if data is None else data})[0]


def _ref(km, toren: str) -> np.ndarray:
    return ref_beeld(_pixels(), km.w) if toren == "beeld" else ref_tekst(km.tokens, km.w)


def _glad_willekeurig(km, toren: str) -> dict[str, np.ndarray]:
    """Een factor 2^k (k van −3 tot 3) per invoerkanaal van elke Linear met een invoer."""
    rng = np.random.default_rng(11)
    labels = [
        x for x in _linears(f"{'vision' if toren == 'beeld' else 'text'}_model") if x != KOP_Q
    ]
    return {
        x: np.exp2(rng.integers(-3, 4, size=_wt(km.w, x).shape[0])).astype(np.float32)
        for x in labels
    }


def _glad_geijkt(k, toren: str) -> dict[str, np.ndarray]:
    """glad zoals het rapport: de probe (elke Linear in fp32, meet) op andere invoer dan die van
    de test (4 willekeurige beelden, of de zinnen om te ijken), dan factoren()."""
    meet: list = []
    probe = _sessie(_graaf(k, toren, fp32_als=lambda _x: True, meet=meet))
    if toren == "beeld":
        rng = np.random.default_rng(5)
        data = {"pixels": rng.uniform(-1, 1, size=(4, 3, klein.BEELD, klein.BEELD))}
        data["pixels"] = data["pixels"].astype(np.float32)
    else:
        data = {"tokens": np.array([k.tok.encode(z) for z in IJK_ZINNEN], dtype=np.int64)}
    invoer = [m for m in meet if m[0] == "invoer"]
    uit = probe.run([m[2] for m in invoer], data)
    return {
        m[1]: factoren(
            np.abs(x.reshape(-1, m[3])).max(axis=0),
            np.abs(gewicht(k.st, k.vorm, m[1])).max(axis=1),
        )
        for m, x in zip(invoer, uit, strict=True)
    }


MEET = "meet"  # in VARIANTEN: een nieuwe lijst per test
VARIANTEN = {
    "standaard": {},  # per token (VERSIE 3)
    "per tensor": {"per_token": False},  # VERSIE 2
    "fc2 fp32": {"fp32_als": lambda x: x.endswith(".mlp.fc2")},
    "kop fp32": {"fp32_als": lambda x: ".head" in x},
    "glad": {"glad": GLAD},
    "per tensor + glad": {"per_token": False, "glad": GLAD},
    "per tensor + q_max 127": {"per_token": False, "q_max": 127},
    "q_max 32": {"q_max": 32},
    "meet": {"meet": MEET},
    "embedding fp32": {"embedding_fp32": True},  # alleen de tekst heeft een embedding
}
GEVALLEN = [
    (toren, naam)
    for naam in VARIANTEN
    for toren in ("beeld", "tekst")
    if toren == "tekst" or naam != "embedding fp32"
]


@pytest.mark.parametrize(("toren", "naam"), GEVALLEN)
def test_elke_variant_bouwt_en_rekent(km, toren, naam):
    opties = dict(VARIANTEN[naam])
    if opties.get("glad") == GLAD:
        opties["glad"] = _glad_geijkt(km, toren)
    if opties.get("meet") == MEET:
        opties["meet"] = []
    uit = _draai(km, toren, _sessie(_graaf(km, toren, **opties)))
    assert np.isfinite(uit).all()
    assert np.allclose(np.linalg.norm(uit, axis=1), 1.0, atol=1e-5)
    c = _cos(uit, _ref(km, toren))
    assert c.min() >= COS, (naam, c)


def _dambord() -> np.ndarray:
    """Een foto met een heel ander bereik dan _pixels: ±1 in vlakken van 32 px, × 8."""
    bord = np.indices((klein.BEELD, klein.BEELD)).sum(0) // klein.PATCH % 2 * 2.0 - 1.0
    return np.broadcast_to(bord * 8, (1, 3, klein.BEELD, klein.BEELD)).astype(np.float32)


def _alleen_en_batch(km, toren: str, s) -> tuple[float, float]:
    """De grootste verschillen: elk item alleen tegen de batch, en het eerste item naast een
    vreemde buur (beeld: het dambord; tekst: een vraag van 200 tekens) tegen alleen."""
    data = _pixels(6) if toren == "beeld" else km.tokens
    batch = _draai(km, toren, s, data)
    alleen = np.concatenate([_draai(km, toren, s, data[i : i + 1]) for i in range(len(data))])
    buur = _dambord() if toren == "beeld" else km.tokens[-1:]
    met = _draai(km, toren, s, np.concatenate([data[:1], buur]))[:1]
    return float(np.abs(alleen - batch).max()), float(np.abs(alleen[:1] - met).max())


@pytest.mark.parametrize("toren", ["beeld", "tekst"])
def test_per_token_alleen_gelijk_aan_in_een_batch(km, toren):
    """De standaard (per token, VERSIE 3) hangt niet af van de batch; per tensor (VERSIE 2) wel,
    dus de test ziet het als de standaard ooit terug zou gaan."""
    per_token = _alleen_en_batch(km, toren, _sessie(_graaf(km, toren)))
    per_tensor = _alleen_en_batch(km, toren, _sessie(_graaf(km, toren, per_token=False)))
    print(f"{toren}: alleen tegen batch, buur: per token {per_token}, per tensor {per_tensor}")
    assert max(per_token) <= ALLEEN, per_token
    assert min(per_tensor) > ALLEEN, per_tensor


def test_per_token_matmulinteger_met_nulpunt_128_en_gewichten_tot_64(km, tmp_path):
    """De standaard, elke Linear: MatMulInteger(u, w8, nul) met een scalair nulpunt 128 (uint8)
    en |q| ≤ 64, zodat een paar van vpmaddubsw (255·64·2) in int16 past; geen
    DynamicQuantizeMatMul meer."""
    for toren, naam in (("beeld", "vision_model"), ("tekst", "text_model")):
        pad = tmp_path / f"{toren}.onnx"
        Onnx(_graaf(km, toren)).write(pad)
        nodes, inits = lees_onnx(pad)
        assert not [n for n in nodes if n[0] == "DynamicQuantizeMatMul"]
        mmi = [n for n in nodes if n[0] == "MatMulInteger"]
        labels = [n[2][1].removesuffix(".w8") for n in mmi]
        assert labels == _linears(naam)
        for (_op, _domein, invoer), label in zip(mmi, labels, strict=True):
            assert invoer[1:] == [f"{label}.w8", f"{label}.nul"]
            nul = inits[f"{label}.nul"]
            assert nul.dtype == np.uint8 and nul.shape == () and int(nul) == 128
            assert int(np.abs(inits[f"{label}.w8"].astype(np.int16)).max()) <= bouw.Q_MAX


PER_TOKEN_LABELS = [
    "text_model.encoder.layers.0.self_attn.out_proj",
    "text_model.encoder.layers.1.mlp.fc2",
    "vision_model.encoder.layers.0.mlp.fc1",
    "vision_model.encoder.layers.1.self_attn.out_proj",
    "vision_model.head.mlp.fc2",
    "text_model.head",
]


@pytest.mark.parametrize("label", PER_TOKEN_LABELS)
def test_per_token_rekent_precies_als_de_keten_in_numpy(km, tmp_path, label):
    """De review (S2): één Linear met per_token tegen de keten in numpy, met de q, s en b uit de
    graaf: a = max |x| / 127 per rij, u = clip(rint(x / a) + 128, 0, 255), y = ((u − 128) @ q) ×
    a × s + b, binnen 1e-5; met uitschieters in een rij en in één kanaal. De enige Add met een
    constante telt 128 op (het nulpunt), geen 127."""
    b = bouw.Bouwer(km.st, "een", int8=True)  # de standaard: per token
    n_in = km.st.info(f"{label}.weight").vorm[1]
    y = b.linear(b.g.input("x", bouw.FLOAT, ["n", 5, n_in]), [bouw._linear(label)], label)
    b.g.output(y, bouw.FLOAT, None)
    pad = tmp_path / "een.onnx"
    Onnx(b.g).write(pad)
    nodes, inits = lees_onnx(pad)
    q = inits[f"{label}.w8"].astype(np.int32)
    s, bias = inits[f"{label}.s"], inits[f"{label}.b"]
    plus = [inits[i] for op, _d, inv in nodes if op == "Add" for i in inv if i in inits]
    plus = [float(p) for p in plus if p.size == 1]
    assert plus == [128.0], plus
    sessie = _sessie(b.g)
    erg = 0.0
    for zaad in range(20):
        rng = np.random.default_rng(zaad)
        x = rng.normal(size=(3, 5, n_in)).astype(np.float32)
        x[1, 2] *= 100  # een rij met een groot bereik
        x[2, 0, 3] = 300  # één kanaal
        a = np.maximum(np.abs(x).max(axis=-1, keepdims=True) / np.float32(127), np.float32(1e-30))
        u = np.clip(np.rint(x / a) + np.float32(128), 0, 255).astype(np.uint8)
        ref = ((u.astype(np.int32) - 128) @ q).astype(np.float32) * a * s + bias
        erg = max(erg, float(np.abs(sessie.run(None, {"x": x})[0] - ref).max()))
    assert erg <= TOL, (label, erg)


@pytest.mark.parametrize("q_max", [65, 127])
def test_per_token_met_een_gewicht_boven_64_is_een_fout(km, monkeypatch, q_max):
    """De review (N5): met per_token (de standaard) past een paar van vpmaddubsw (255·q·2) alleen
    met |q| ≤ 64 in int16; q_max groter (of Q_MAX zelf groter) is een ValueError. 64 en kleiner
    mag; meer alleen per tensor."""
    for opties in ({}, {"per_token": True}):
        with pytest.raises(ValueError, match="per_token"):
            _graaf(km, "tekst", q_max=q_max, **opties)
    monkeypatch.setattr(bouw, "Q_MAX", q_max)
    with pytest.raises(ValueError, match="per_token"):
        _graaf(km, "beeld")
    monkeypatch.undo()
    for goed in (None, 32, 64):
        _graaf(km, "tekst", q_max=goed)
    _graaf(km, "tekst", per_token=False, q_max=q_max)  # per tensor (VERSIE 2) kan het wel


def test_fp32_als_elke_linear_is_de_fp32_graaf(km, tmp_path):
    for toren in ("beeld", "tekst"):
        extra = {"embedding_fp32": True} if toren == "tekst" else {}
        alles = _graaf(km, toren, fp32_als=lambda _x: True, **extra)
        fp32 = _draai(km, toren, _sessie(_graaf(km, toren, int8=False)))
        assert float(np.abs(_draai(km, toren, _sessie(alles)) - fp32).max()) <= TOL, toren
        pad = tmp_path / f"{toren}.onnx"
        Onnx(alles).write(pad)
        nodes, inits = lees_onnx(pad)
        ops = {n[0] for n in nodes}
        assert not ops & {"DynamicQuantizeMatMul", "MatMulInteger"}, toren
        assert not [k for k, a in inits.items() if a.dtype in (np.int8, np.uint8)], toren
    # zonder embedding_fp32 houdt de tekst zijn int8-embedding (de probe van het rapport)
    pad = tmp_path / "probe.onnx"
    Onnx(_graaf(km, "tekst", fp32_als=lambda _x: True)).write(pad)
    _, inits = lees_onnx(pad)
    assert "text_model.embeddings.token_embedding.weight.w8" in inits


def test_glad_met_machten_van_2(km, tmp_path):
    """fp32: x / f en Wᵀ × f geven precies dezelfde uitvoer. int8: per_kolom krijgt het gladde
    gewicht; de initializer `.glad` is 1 / f."""
    for toren in ("beeld", "tekst"):
        glad = _glad_willekeurig(km, toren)
        fp32 = _draai(km, toren, _sessie(_graaf(km, toren, int8=False)))
        met = _draai(km, toren, _sessie(_graaf(km, toren, int8=False, glad=glad)))
        assert np.array_equal(met, fp32), (toren, float(np.abs(met - fp32).max()))
        pad = tmp_path / f"{toren}.onnx"
        Onnx(_graaf(km, toren, glad=glad)).write(pad)
        _, inits = lees_onnx(pad)
        for label, f in glad.items():
            assert np.array_equal(inits[f"{label}.glad"], (1 / f).astype(np.float32)), label
            q, s = _q8_per_kolom(_wt(km.w, label) * f[:, None], bouw.Q_MAX)
            assert np.array_equal(inits[f"{label}.w8"], q), label
            assert np.array_equal(inits[f"{label}.s"], s), label


@pytest.mark.parametrize(
    "fout",
    [
        lambda f: f * 3,  # geen macht van 2
        lambda f: f[:-1],  # te weinig factoren
        lambda f: np.where(np.arange(len(f)) == 0, 0.0, f),  # 0
        lambda f: np.where(np.arange(len(f)) == 0, np.inf, f),
    ],
)
def test_glad_die_niet_klopt_is_een_omzetfout(km, fout):
    label = "text_model.encoder.layers.0.mlp.fc1"
    f = fout(np.ones(klein.BREEDTE, np.float32)).astype(np.float32)
    with pytest.raises(OmzetFout, match="glad"):
        _graaf(km, "tekst", glad={label: f})


@pytest.mark.parametrize("q_max", [32, 127])
def test_q_max_gaat_door_naar_per_kolom(km, tmp_path, q_max):
    """32 per token (de standaard); 127 kan alleen per tensor."""
    vorm = {} if q_max <= bouw.PER_TOKEN_Q_MAX else {"per_token": False}
    for toren, naam in (("beeld", "vision_model"), ("tekst", "text_model")):
        pad = tmp_path / f"{toren}.onnx"
        Onnx(_graaf(km, toren, q_max=q_max, **vorm)).write(pad)
        _, inits = lees_onnx(pad)
        for label in _linears(naam):
            q, s = _q8_per_kolom(_wt(km.w, label), q_max)
            assert np.array_equal(inits[f"{label}.w8"], q), label
            assert np.array_equal(inits[f"{label}.s"], s), label
            assert (np.abs(q.astype(np.int16)).max(axis=0) == q_max).all(), label


def _rest_tekst(km) -> list[np.ndarray]:
    """De residu van de tekst na elke laag, in de numpy-versie (float64)."""
    x = _w(km.w, "text_model.embeddings.token_embedding.weight")[km.tokens]
    x = x + _w(km.w, "text_model.embeddings.position_embedding.weight")[: km.tokens.shape[1]]
    uit = []
    for i in range(klein.LAGEN):
        x = _laag(x, km.w, f"text_model.encoder.layers.{i}")
        uit.append(x)
    return uit


def test_meet_de_invoer_van_elke_linear_en_de_residu_na_elke_laag(km):
    for toren, naam in (("beeld", "vision_model"), ("tekst", "text_model")):
        meet: list = []
        s = _sessie(_graaf(km, toren, meet=meet))
        invoer = [m for m in meet if m[0] == "invoer"]
        rest = [m for m in meet if m[0] == "rest"]
        assert [m[1] for m in invoer] == [x for x in _linears(naam) if x != KOP_Q]
        assert [m[1] for m in rest] == [f"{naam}.{i}" for i in range(klein.LAGEN)]
        hoofd = omzetten.UIT_BEELD if toren == "beeld" else omzetten.UIT_TEKST
        namen = [o.name for o in s.get_outputs()]
        assert namen[0] == hoofd and namen[1:] == list(dict.fromkeys(m[2] for m in meet))
        data = _pixels() if toren == "beeld" else km.tokens
        uit = s.run([hoofd, *namen[1:]], {s.get_inputs()[0].name: data})
        zonder = _draai(km, toren, _sessie(_graaf(km, toren)))
        assert float(np.abs(uit[0] - zonder).max()) <= TOL, toren
        waarden = dict(zip(namen, uit, strict=True))
        for _soort, label, x, n_in in invoer:
            assert waarden[x].shape[0] == len(data) and waarden[x].shape[-1] == n_in, label
        for m in rest:
            assert waarden[m[2]].shape[0] == len(data) and waarden[m[2]].shape[-1] == klein.BREEDTE
    # in fp32 is de residu die van de numpy-versie, en de invoer van de kop de laatste token na
    # final_layer_norm
    meet = []
    s = _sessie(_graaf(km, "tekst", int8=False, meet=meet))
    uit = dict(
        zip([o.name for o in s.get_outputs()], s.run(None, {"tokens": km.tokens}), strict=True)
    )
    ref = _rest_tekst(km)
    for i, m in enumerate(m for m in meet if m[0] == "rest"):
        assert np.allclose(uit[m[2]], ref[i], atol=1e-4, rtol=1e-4), m[1]
    (kop,) = [m for m in meet if m[1] == "text_model.head"]
    eind = _ln(ref[-1], km.w, "text_model.final_layer_norm")[:, -1]
    assert np.allclose(uit[kop[2]], eind, atol=1e-4, rtol=1e-4)


def _met_uitschieter(bron: Path, doel: Path, k: float = 100) -> None:
    """E6 van de analyse: neuron 5 van fc1 in laag 0 van het beeld × k, en zijn kolom in fc2."""
    shutil.copytree(bron, doel)
    st = Safetensors(doel / omzetten.SAFETENSORS)
    t = {n: ("F32", st.info(n).vorm, st.lees(n).astype("<f4")) for n in st.namen()}
    p = "vision_model.encoder.layers.0.mlp"
    a = t[f"{p}.fc1.weight"][2].copy()
    a[5] *= k
    t[f"{p}.fc1.weight"] = ("F32", a.shape, a)
    a = t[f"{p}.fc2.weight"][2].copy()
    a[:, 5] *= k
    t[f"{p}.fc2.weight"] = ("F32", a.shape, a)
    tensors = {n: (d, s, v.tobytes()) for n, (d, s, v) in t.items()}
    (doel / omzetten.SAFETENSORS).write_bytes(klein.safetensors(tensors))


def test_een_neuron_x100_per_token_blijft_los_van_de_batch(km, tmp_path):
    """Alleen wat zeker is; de cosinussen gaan naar de uitvoer (pytest -s)."""
    _met_uitschieter(km.basis / "bron", tmp_path / "bron")
    st, vorm = _laad(tmp_path / "bron")
    k = SimpleNamespace(st=st, vorm=vorm, tok=km.tok, tokens=km.tokens)
    px = _pixels(6)
    fp32 = _draai(k, "beeld", _sessie(_graaf(k, "beeld", int8=False)), px)
    glad = _glad_geijkt(k, "beeld")
    gemeten = {}
    for naam, opties in (
        ("per tensor (v2)", {"per_token": False}),
        ("zoals nu (per token)", {}),
        ("per token + glad", {"glad": glad}),
        ("laag 0 fc2 fp32", {"fp32_als": lambda x: x == "vision_model.encoder.layers.0.mlp.fc2"}),
    ):
        s = _sessie(_graaf(k, "beeld", **opties))
        batch = _draai(k, "beeld", s, px)
        alleen = np.concatenate([_draai(k, "beeld", s, px[i : i + 1]) for i in range(len(px))])
        assert np.isfinite(batch).all() and np.isfinite(alleen).all(), naam
        gemeten[naam] = (
            round(float(_cos(alleen, fp32).min()), 5),
            round(float(_cos(batch, fp32).min()), 5),
            float(np.abs(alleen - batch).max()),
        )
        if opties.get("per_token", True):
            assert gemeten[naam][2] <= ALLEEN, (naam, gemeten[naam])
    print(f"E6 (neuron × 100), cos alleen, cos batch, alleen tegen batch: {gemeten}")
    alles = _draai(k, "beeld", _sessie(_graaf(k, "beeld", fp32_als=lambda _x: True)), px)
    assert float(np.abs(alles - fp32).max()) <= TOL


# --- de rooktest van het rapport -----------------------------------------------------------------


def _set(n: int = 40) -> tuple[list[np.ndarray], list[str], list[set]]:
    """n synthetische foto's: eerst de doelen van de combinaties, dan foto's met één categorie
    (twee per categorie), dan foto's zonder."""
    rijen = vragen()
    combi = [r for r in rijen if r["soort"] == "combinatie"]
    namen = sorted({r["categorieen"] for r in rijen if r["soort"] == "nl"})
    sleutels = [r["doel"] for r in combi]
    cats = [set(r["categorieen"].split(",")) for r in combi]
    for i in range(2 * len(namen)):
        sleutels.append(f"foto-{i}")
        cats.append({namen[i % len(namen)]})
    while len(sleutels) < n:
        sleutels.append(f"leeg-{len(sleutels)}")
        cats.append(set())
    rng = np.random.default_rng(3)
    pixels = list(rng.uniform(-1, 1, size=(n, 3, klein.BEELD, klein.BEELD)).astype(np.float32))
    return pixels, sleutels, cats


def _kort(map_: Path) -> list[str]:
    return (map_ / "kort.txt").read_text(encoding="utf-8").splitlines()


def _getallen(x) -> list[int]:
    """De lengte van elke lijst van getallen in een JSON-waarde."""
    if isinstance(x, dict):
        return [n for v in x.values() for n in _getallen(v)]
    if isinstance(x, list):
        eigen = [len(x)] if x and all(isinstance(v, (int, float)) for v in x) else []
        return eigen + [n for v in x for n in _getallen(v)]
    return []


def _rapport(km, tmp_path, monkeypatch, tijd_s: float) -> Rapport:
    monkeypatch.setenv("BEELD_RAPPORT", str(tmp_path))
    pixels, sleutels, cats = _set()
    ijk = list(np.random.default_rng(4).uniform(-1, 1, size=(4, 3, 256, 256)).astype(np.float32))
    return Rapport(
        Verslag("bz4r"),
        bron=km.basis / "bron",
        model=km.basis / "uit",
        pixels=pixels,
        sleutels=sleutels,
        cats=cats,
        ijk_beelden=ijk,
        werk=tmp_path / "werk",
        tijd_s=tijd_s,
    )


def test_het_rapport_per_laag_en_variant_op_het_kleine_model(km, tmp_path, monkeypatch):
    r = _rapport(km, tmp_path, monkeypatch, tijd_s=600)
    r.regel_cpu()
    r.regel_gewicht()
    for toren in ("beeld", "tekst"):
        r.regel_lagen(toren)
    r.los()
    r.regel_varianten()
    regels = _kort(tmp_path)
    print("\n".join(regels))
    assert not [x for x in regels if "mislukt" in x], regels
    namen = [naam for naam, *_ in r.lijst()]
    verwacht = ["BZ4 cpu (rapport): "]
    verwacht += [f"BZ4 gewicht int8 {t} (rapport): fout per Linear" for t in ("beeld", "tekst")]
    for t in ("beeld", "tekst"):
        verwacht += [f"BZ4 invoer {t} (rapport; fout per tensor"]
        verwacht += [f"BZ4 afwijking per laag {t} (rapport): cos"]
    verwacht += [f"BZ4 variant {n} (rapport): " for n in ("fp32", "zoals nu", *namen)]
    assert len(regels) == len(verwacht)
    for regel, begin in zip(regels, verwacht, strict=True):
        assert regel.startswith(begin), (regel, begin)
    assert namen == [
        "per tensor (v2)",
        "fc2 fp32",
        "mlp fp32",
        "aandacht fp32",
        "kop fp32",
        "laag 0-1 fp32",
        "laag 0-1 fp32",
        "per token + glad",
        "embedding fp32",
    ]
    for regel in regels[-len(namen) :]:
        assert " nl " in regel and " ms per vraag" in regel, regel
    doc = json.loads(r.verslag.pad.read_text())
    assert set(doc) == {
        "cpu",
        "gewicht",
        "invoer_beeld",
        "invoer_tekst",
        "afwijking_beeld",
        "afwijking_tekst",
        "varianten",
    }
    assert [v["variant"] for v in doc["varianten"]] == ["fp32", "zoals nu", *namen]
    fp32 = doc["varianten"][0]
    assert fp32["zakt"] == 0 and fp32["cos_beeld"]["p0"] == pytest.approx(1, abs=1e-6)
    assert len(doc["afwijking_beeld"]["na_laag"]) == klein.LAGEN
    assert [x["linear"] for x in doc["invoer_tekst"]] == linears(r.v, "tekst")
    assert [x["linear"] for x in doc["invoer_beeld"]] == [
        x for x in linears(r.v, "beeld") if x != KOP_Q
    ]
    assert {x["soort"] for x in doc["invoer_tekst"]} <= {"inhoud", "eos", "pad", "kop"}
    # nooit een vector of een factor: geen lijst met meer getallen dan er lagen of vragen zijn
    assert max(_getallen(doc)) <= 24, sorted(_getallen(doc))[-5:]
    assert kort_label("text_model.head") == "kop"
    assert kort_label("vision_model.head.attention.kv") == "kop kv"
    assert kort_label("text_model.encoder.layers.11.mlp.fc2") == "laag 11 fc2"


def _lagen(toren: str, *namen: str) -> set[str]:
    t = "vision_model" if toren == "beeld" else "text_model"
    return {f"{t}.encoder.layers.{i}.{n}" for i in range(klein.LAGEN) for n in namen}


KOP = {
    "beeld": {
        "vision_model.head.attention.q",
        "vision_model.head.attention.kv",
        "vision_model.head.attention.out_proj",
        "vision_model.head.mlp.fc1",
        "vision_model.head.mlp.fc2",
    },
    "tekst": {"text_model.head"},
}
# per variant van Rapport.lijst() en per toren: de Linears in fp32 (None: de toren van nu, de
# standaard van de omzetter)
FP32 = {
    "per tensor (v2)": lambda t: set(),
    "fc2 fp32": lambda t: _lagen(t, "mlp.fc2"),
    "mlp fp32": lambda t: _lagen(t, "mlp.fc1", "mlp.fc2"),
    "aandacht fp32": lambda t: _lagen(t, "self_attn.qkv", "self_attn.out_proj"),
    "kop fp32": lambda t: KOP[t],
    "laag 0-1 fp32": lambda t: _lagen(
        t, "self_attn.qkv", "self_attn.out_proj", "mlp.fc1", "mlp.fc2"
    ),
    "per token + glad": lambda t: set(),
    "embedding fp32": lambda t: None if t == "beeld" else set(),
}


def test_elke_variant_van_het_rapport_meet_wat_zijn_naam_zegt(km, tmp_path, monkeypatch):
    """De review (S3), sinds CI op 011bf2d tegen de standaard per token: per variant van
    Rapport.lijst() precies de Linears in fp32 die de naam zegt (fc2, mlp en aandacht alleen in de
    lagen; de koppen alleen in kop fp32), MatMulInteger in elke variant behalve `per tensor (v2)`
    (die alleen DynamicQuantizeMatMul), `.glad` alleen met glad (elke Linear behalve de vraag van
    de MAP-kop), |q| tot 64 overal, en de embedding van de tekst in fp32 alleen bij embedding
    fp32. Een toren zonder opties is die van de omzetter zelf: per token."""
    r = _rapport(km, tmp_path, monkeypatch, tijd_s=600)
    namen = [naam for naam, *_ in r.lijst()]
    assert sorted(set(namen)) == sorted(FP32)
    for naam, beeld, tekst, _reden in r.lijst():
        for toren, opties in (("beeld", beeld), ("tekst", tekst)):
            verwacht = FP32[naam](toren)
            if verwacht is None:
                assert opties is None, (naam, toren)
                continue
            if opties.get("glad") == GLAD:
                opties = {**opties, "glad": r.glad(toren)}
            pad = tmp_path / f"{toren}.onnx"
            Onnx(r._graaf(toren, **opties)).write(pad)
            nodes, inits = lees_onnx(pad)
            alle = set(linears(r.v, toren))
            fp32 = {k.removesuffix(".w") for k in inits if k.endswith(".w")}
            int8 = {k.removesuffix(".w8") for k in inits if k.endswith(".w8")} & alle
            assert fp32 == verwacht and int8 == alle - verwacht, (naam, toren, fp32 ^ verwacht)
            ops = {n[0] for n in nodes}
            per_token = naam != "per tensor (v2)"
            assert ("MatMulInteger" in ops) == per_token, (naam, toren)
            assert ("DynamicQuantizeMatMul" in ops) == (not per_token), (naam, toren)
            glad = {k.removesuffix(".glad") for k in inits if k.endswith(".glad")}
            assert glad == (alle - {KOP_Q} if "glad" in naam else set()), (naam, toren)
            q = max(int(np.abs(inits[f"{x}.w8"].astype(np.int16)).max()) for x in int8)
            assert q == bouw.Q_MAX, (naam, toren)
            embedding = "text_model.embeddings.token_embedding.weight.w8" in inits
            assert embedding == (toren == "tekst" and naam != "embedding fp32"), (naam, toren)


def test_glad_komt_niet_uit_de_set(km, tmp_path, monkeypatch):
    """De review (S3): de factoren van glad komen alleen uit ijk_beelden en IJK_ZINNEN. Met foto's
    van de set die NaN zijn en andere vragen in het Nederlands zijn ze precies dezelfde (en
    eindig, en machten van 2)."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _rapport(km, tmp_path / "a", monkeypatch, tijd_s=600)
    b = _rapport(km, tmp_path / "b", monkeypatch, tijd_s=600)
    b.pixels = [np.full_like(p, np.nan) for p in b.pixels]
    b.rijen = {**b.rijen, "nl": [{**x, "vraag": "een heel andere vraag"} for x in b.rijen["nl"]]}
    for toren in ("beeld", "tekst"):
        fa, fb = a.glad(toren), b.glad(toren)
        assert set(fa) == set(linears(a.v, toren)) - {KOP_Q}, toren
        assert set(fb) == set(fa), toren
        for label, f in fa.items():
            assert np.isfinite(f).all() and (np.frexp(f)[0] == 0.5).all(), label
            assert np.array_equal(f, fb[label]), label


def test_de_varianten_stoppen_na_de_tijd(km, tmp_path, monkeypatch):
    r = _rapport(km, tmp_path, monkeypatch, tijd_s=0)
    r.regel_varianten()
    regels = _kort(tmp_path)
    assert regels[0].startswith("BZ4 variant fp32 (rapport): nl ")
    assert regels[1].startswith("BZ4 variant zoals nu (rapport): nl ")
    assert len(regels) == 2 + len(r.lijst())
    for regel in regels[2:]:
        assert "overgeslagen: " in regel, regel
    assert not r._glad  # niets geijkt: geen variant gebouwd


# --- een rapport dat faalt, is een regel (de review, S4) ---------------------------------------


def test_een_variant_die_faalt_stopt_de_rest_niet(km, tmp_path, monkeypatch):
    r = _rapport(km, tmp_path, monkeypatch, tijd_s=600)
    gebouwd = []

    def variant(naam, beeld, tekst, ref) -> dict:
        gebouwd.append(naam)
        if naam == "fc2 fp32":
            raise RuntimeError("stuk")
        return {"variant": naam}

    monkeypatch.setattr(r, "_variant", variant)
    r.regel_varianten()
    lijst = r.lijst()
    te_bouwen = [naam for naam, _, _, reden in lijst if reden is None]
    assert te_bouwen.index("fc2 fp32") < len(te_bouwen) - 1  # er komen er nog na
    assert gebouwd == te_bouwen
    regels = _kort(tmp_path)
    assert regels[0].startswith("BZ4 variant fp32 (rapport): nl ")
    assert regels[1].startswith("BZ4 variant zoals nu (rapport): nl ")
    assert [x for x in regels if "mislukt" in x] == [
        "BZ4 variant fc2 fp32 (rapport): mislukt (RuntimeError)"
    ]
    uit = json.loads((tmp_path / "bz4r.json").read_text(encoding="utf-8"))["varianten"]
    namen = [n for n, *_ in lijst]
    assert [x["variant"] for x in uit] == ["fp32", "zoals nu", *namen]
    assert uit[2 + namen.index("fc2 fp32")] == {"variant": "fc2 fp32", "mislukt": "RuntimeError"}


def test_probeer_maakt_van_een_fout_een_regel(tmp_path, monkeypatch):
    monkeypatch.setenv("BEELD_RAPPORT", str(tmp_path))
    verslag = Verslag("bz4r")
    gedaan = []

    def faalt() -> None:
        gedaan.append("gewicht")
        raise ValueError("stuk")

    probeer(verslag, "cpu", lambda: gedaan.append("cpu"))
    probeer(verslag, "gewicht int8", faalt)
    assert gedaan == ["cpu", "gewicht"]
    assert _kort(tmp_path) == ["BZ4 gewicht int8 (rapport): mislukt (ValueError)"]


def test_een_int8_rapport_dat_faalt_is_een_regel_en_geen_uitslag(tmp_path, monkeypatch):
    monkeypatch.setenv("BEELD_RAPPORT", str(tmp_path))
    verslag = Verslag("bz4")
    verslag.zet("nl", {"int8": 0.6})
    gekregen = []

    def faalt(v, **kw) -> dict:
        gekregen.append((v, kw))
        raise RuntimeError("stuk")

    monkeypatch.setattr(kwaliteit, "_rapport_int8", faalt)
    kwaliteit._rapport_veilig(verslag, int8="m8", rijen={"nl": []})
    assert gekregen == [(verslag, {"int8": "m8", "rijen": {"nl": []}})]
    assert _kort(tmp_path) == ["BZ4 int8-rapport (rapport): mislukt (RuntimeError)"]
    pad = tmp_path / "bz4.json"
    assert json.loads(pad.read_text(encoding="utf-8")) == {"nl": {"int8": 0.6}}
    # zonder fout: het rapport in het verslag en geen regel
    monkeypatch.setattr(kwaliteit, "_rapport_int8", lambda v, **kw: {"torens": {}})
    kwaliteit._rapport_veilig(verslag)
    assert json.loads(pad.read_text(encoding="utf-8"))["int8_rapport"] == {"torens": {}}
    assert len(_kort(tmp_path)) == 1


def test_de_fixture_uitkomst_rekent_het_int8_rapport_alleen_veilig():
    """`_rapport_int8` alleen in `_rapport_veilig` (met de try), en `uitkomst` roept dat aan."""
    boom = ast.parse(Path(kwaliteit.__file__).read_text(encoding="utf-8"))
    roept: dict[str, set[str]] = {}
    for f in boom.body:
        if isinstance(f, ast.FunctionDef):
            for n in ast.walk(f):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                    roept.setdefault(n.func.id, set()).add(f.name)
    assert roept["_rapport_int8"] == {"_rapport_veilig"}
    assert roept["_rapport_veilig"] == {"uitkomst"}


def test_een_waarde_die_geen_json_is_bederft_het_verslag_niet(tmp_path, monkeypatch):
    """De review, N4: een zet die faalt, laat het verslag (en de volgende zet) zoals het was."""
    monkeypatch.setenv("BEELD_RAPPORT", str(tmp_path))
    verslag = Verslag("bz4")
    verslag.zet("nl", 0.6)
    with pytest.raises(TypeError):
        verslag.zet("int8_rapport", {"torens": np.float32(0.5)})
    pad = tmp_path / "bz4.json"
    assert json.loads(pad.read_text(encoding="utf-8")) == {"nl": 0.6}
    verslag.zet("en", 0.5)
    assert json.loads(pad.read_text(encoding="utf-8")) == {"nl": 0.6, "en": 0.5}


def test_het_int8_rapport_van_bz4_op_het_kleine_model(km, tmp_path, monkeypatch):
    monkeypatch.setenv("BEELD_RAPPORT", str(tmp_path))
    uit = km.basis / "uit"
    int8, fp32 = Model(uit), Model(uit, fp32=True)
    pixels, sleutels, cats = _set()
    rijen = vragen()
    soorten = {"nl": "nl", "c": "combinatie", "en": "en"}
    per = {s: [x for x in rijen if x["soort"] == soort] for s, soort in soorten.items()}
    e8, e32 = int8.beelden(pixels), fp32.beelden(pixels)
    t8 = {s: int8.teksten([x["vraag"] for x in rs]) for s, rs in per.items()}
    t32 = fp32.teksten([x["vraag"] for x in per["nl"]])
    r = _rapport_int8(
        Verslag("bz4"),
        int8=int8,
        fp32=fp32,
        pixels=pixels,
        e8=e8,
        e32=e32,
        t8=t8,
        t32=t32,
        rijen=per,
        cats=cats,
        sleutels=sleutels,
    )
    json.dumps(r)
    assert set(r) == {
        "torens",
        "fp32",
        "cos",
        "een_per_keer",
        "buren_uit_de_batch",
        "nl_per_vraag",
        "en_per_vraag",
    }
    regels = _kort(tmp_path)
    verwacht = [
        "BZ4 torens (rapport): precisie@5 nl tekst int8 + beeld fp32 ",
        "BZ4 fp32 (rapport): engels ",
        "BZ4 cos int8 tegen fp32 (rapport): tekst nl p5 ",
        "BZ4 één per keer (rapport): tekst int8 precisie@5 ",
        "BZ4 buren uit dezelfde batch (rapport): int8 ",
        "BZ4 nl per vraag (rapport, int8/fp32/tekst-fp32/beeld-fp32) 1/3: strand ",
        "BZ4 nl per vraag (rapport, int8/fp32/tekst-fp32/beeld-fp32) 2/3: ",
        "BZ4 nl per vraag (rapport, int8/fp32/tekst-fp32/beeld-fp32) 3/3: ",
        "BZ4 en per vraag (rapport, int8/fp32): beach ",
    ]
    assert len(regels) == len(verwacht)
    for regel, begin in zip(regels, verwacht, strict=True):
        assert regel.startswith(begin), (regel, begin)
    # de 30 foto's met een categorie zitten in de eerste twee batches van 16 (40 foto's: 16, 16,
    # 8): toeval is 15 van de 39 andere
    assert r["buren_uit_de_batch"]["toeval"] == pytest.approx(15 / 39, abs=1e-4)
    assert [x["vraag"] for x in r["nl_per_vraag"]] == [x["vraag"] for x in per["nl"]]
    assert r["fp32"]["combinaties_top"] == sum(p <= 10 for p in r["fp32"]["combinaties_plekken"])
    assert r["cos"]["beeld_laagste"] in sleutels
    assert max(_getallen(r)) <= 24
