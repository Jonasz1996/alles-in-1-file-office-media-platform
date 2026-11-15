"""De omzetter van SigLIP 2 (fase 6, X3a, Keuze 73 en 83 laag 2; job api-analyse).

Een kleine SigLIP-vorm met willekeurige gewichten (siglip_klein.py: 2 lagen, breedte 64, de namen
van het echte bestand) gaat door de omzetter zoals compose hem start (`python -I -c OMZET_BOOT
<api> --bron … --uit …`). De test rekent dezelfde voorwaartse stap met een eigen numpy-versie in
float64, rechtstreeks uit de gewichten en de regels van SiglipModel (niet uit de omzetter), en eist:

- fp32 hooguit 1·10⁻⁵ van de numpy-versie, voor beeld en tekst;
- int8 cosinus ≥ 0,99 met de numpy-versie;
- twee omzettingen, in twee processen, geven dezelfde sha256 voor elk bestand;
- maxrss en tijd van het proces worden gemeten (en begrensd);
- een safetensors met een kop die liegt, en elke andere bron die niet klopt, geeft OmzetFout:
  code 2, één zin, geen traceback, en de uitvoermap blijft leeg.

Dit is het eerste deel van de go/no-go van X3 (Keuze 73, K2-12). BZ2 en BZ4 doen het echte model in
de job beeld-model.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from app.analyse import lock
from app.analyse.tokenizer import Tokenizer
from app.analyse.worker.omzetten import (
    BEELD,
    BEELD_FP32,
    LICENTIE,
    MAP,
    META,
    TEKST,
    TEKST_FP32,
    TOKENIZER_BIN,
    OmzetFout,
    bouw,
)
from app.analyse.worker.omzetten.safetensors import Safetensors
from tests_analyse import siglip_klein as klein

API = Path(__file__).resolve().parents[1]
# De opdracht van de dienst analyse-omzetten (compose.analyse.yaml van X2a; daar ook als
# app.analyse.runtime.OMZET_BOOT): letterlijk zo, met /srv als eerste argument
OMZET_BOOT = (
    "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module('app.analyse.worker.omzetten.siglip',run_name='__main__',alter_sys=True)"
)
TOL = 1e-5
COS = 0.99
MAXRSS_MIB = 400  # het kleine model; het echte begrenst BZ2 (≤ 1,5 GB)
TEKSTEN = ["hond op het strand", "Hond Op Het Strand!", "", "€ σ ς", "a" * 200]


# --- de eigen numpy-versie (float64), uit de regels van SiglipModel ------------------------------


def _w(w: dict, naam: str) -> np.ndarray:
    vorm, waarden = w[naam]
    return np.frombuffer(waarden.tobytes(), dtype="<f4").astype(np.float64).reshape(vorm)


def _ln(x, w, p):
    m = x.mean(-1, keepdims=True)
    v = ((x - m) ** 2).mean(-1, keepdims=True)
    return (x - m) / np.sqrt(v + klein.EPS) * _w(w, f"{p}.weight") + _w(w, f"{p}.bias")


def _lin(x, w, p):
    return x @ _w(w, f"{p}.weight").T + _w(w, f"{p}.bias")


def _gelu(x):  # gelu_pytorch_tanh
    return 0.5 * x * (1.0 + np.tanh(math.sqrt(2.0 / math.pi) * (x + 0.044715 * x**3)))


def _aandacht(q, k, v):
    n, sq, e = q.shape
    d = e // klein.KOPPEN

    def koppen(t):
        return t.reshape(t.shape[0], t.shape[1], klein.KOPPEN, d).transpose(0, 2, 1, 3)

    s = koppen(q) @ koppen(k).transpose(0, 1, 3, 2) * d**-0.5
    s = np.exp(s - s.max(-1, keepdims=True))
    s /= s.sum(-1, keepdims=True)
    return (s @ koppen(v)).transpose(0, 2, 1, 3).reshape(n, sq, e)


def _laag(x, w, p):
    h = _ln(x, w, f"{p}.layer_norm1")
    a = _aandacht(*(_lin(h, w, f"{p}.self_attn.{n}") for n in ("q_proj", "k_proj", "v_proj")))
    x = x + _lin(a, w, f"{p}.self_attn.out_proj")
    h = _ln(x, w, f"{p}.layer_norm2")
    return x + _lin(_gelu(_lin(h, w, f"{p}.mlp.fc1")), w, f"{p}.mlp.fc2")


def _norm(x):
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def ref_beeld(pixels: np.ndarray, w: dict) -> np.ndarray:
    n, c, s, _ = pixels.shape
    p, e = klein.PATCH, klein.BREEDTE
    r = s // p
    patches = pixels.astype(np.float64).reshape(n, c, r, p, r, p).transpose(0, 2, 4, 1, 3, 5)
    patches = patches.reshape(n, r * r, c * p * p)
    conv = _w(w, "vision_model.embeddings.patch_embedding.weight").reshape(e, c * p * p)
    x = patches @ conv.T + _w(w, "vision_model.embeddings.patch_embedding.bias")
    x = x + _w(w, "vision_model.embeddings.position_embedding.weight")
    for i in range(klein.LAGEN):
        x = _laag(x, w, f"vision_model.encoder.layers.{i}")
    x = _ln(x, w, "vision_model.post_layernorm")
    # MAP-kop: torch.nn.MultiheadAttention(probe, x, x)
    win = _w(w, "vision_model.head.attention.in_proj_weight")
    bin_ = _w(w, "vision_model.head.attention.in_proj_bias")
    probe = np.broadcast_to(_w(w, "vision_model.head.probe"), (n, 1, e))
    q = probe @ win[:e].T + bin_[:e]
    k = x @ win[e : 2 * e].T + bin_[e : 2 * e]
    v = x @ win[2 * e :].T + bin_[2 * e :]
    h = _lin(_aandacht(q, k, v), w, "vision_model.head.attention.out_proj")
    m = _ln(h, w, "vision_model.head.layernorm")
    h = h + _lin(_gelu(_lin(m, w, "vision_model.head.mlp.fc1")), w, "vision_model.head.mlp.fc2")
    return _norm(h[:, 0])


def ref_tekst(tokens: np.ndarray, w: dict) -> np.ndarray:
    x = _w(w, "text_model.embeddings.token_embedding.weight")[tokens]
    x = x + _w(w, "text_model.embeddings.position_embedding.weight")[: tokens.shape[1]]
    for i in range(klein.LAGEN):
        x = _laag(x, w, f"text_model.encoder.layers.{i}")
    x = _ln(x, w, "text_model.final_layer_norm")
    return _norm(_lin(x[:, -1], w, "text_model.head"))


# --- de omzetter zoals compose hem start ---------------------------------------------------------


def _start_meet(bron: Path, uit: Path, *extra: str) -> tuple[subprocess.CompletedProcess, dict]:
    """De dienst analyse-omzetten na: python -I -c OMZET_BOOT <api> --bron … --uit …, met een
    lege omgeving. Geeft het resultaat en de tijd en maxrss van precies dat proces (wait4)."""
    argv = [sys.executable, "-I", "-c", OMZET_BOOT, str(API), "--bron", str(bron)]
    argv += ["--uit", str(uit), *extra]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
    out_pad, err_pad = uit.parent / f"{uit.name}.out", uit.parent / f"{uit.name}.err"
    begin = time.monotonic()
    with out_pad.open("wb") as o, err_pad.open("wb") as e:
        p = subprocess.Popen(argv, stdout=o, stderr=e, env=env)
        _, status, gebruik = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(status)
    duur = time.monotonic() - begin
    res = subprocess.CompletedProcess(argv, p.returncode, out_pad.read_text(), err_pad.read_text())
    out_pad.unlink()
    err_pad.unlink()
    return res, {"s": duur, "maxrss_mib": gebruik.ru_maxrss / 1024}


def _sha(pad: Path) -> str:
    return hashlib.sha256(pad.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def omgezet(tmp_path_factory):
    """Twee keer omzetten (met --fp32), elk in een eigen proces en een eigen map."""
    basis = tmp_path_factory.mktemp("omzetten")
    w = klein.schrijf_bron(basis / "bron")
    runs = []
    for k in (1, 2):
        uit = basis / f"uit{k}"
        uit.mkdir()
        res, meting = _start_meet(basis / "bron", uit, "--fp32")
        assert res.returncode == 0, res.stderr
        runs.append((uit, res, meting))
    return w, runs


@pytest.fixture(scope="module")
def sessies(omgezet):
    import onnxruntime

    def maak(pad):
        o = onnxruntime.SessionOptions()
        o.intra_op_num_threads = 1
        o.inter_op_num_threads = 1
        return onnxruntime.InferenceSession(str(pad), o, providers=["CPUExecutionProvider"])

    uit = omgezet[1][0][0]
    return {naam: maak(uit / naam) for naam in (BEELD, TEKST, BEELD_FP32, TEKST_FP32)}


def _pixels(n: int = 3) -> np.ndarray:
    return (
        np.random.default_rng(7)
        .uniform(-1, 1, size=(n, 3, klein.BEELD, klein.BEELD))
        .astype(np.float32)
    )


def _tokens(uit: Path) -> np.ndarray:
    tok = Tokenizer.load(uit / TOKENIZER_BIN)
    return np.array([tok.encode(t) for t in TEKSTEN], dtype=np.int64)


# --- de go/no-go: rekent als de numpy-versie, en altijd dezelfde bytes ---------------------------


def test_fp32_beeld_gelijk_aan_numpy(omgezet, sessies):
    w, _ = omgezet
    pixels = _pixels()
    (got,) = sessies[BEELD_FP32].run(None, {"pixels": pixels})
    want = ref_beeld(pixels, w)
    assert got.dtype == np.float32 and got.shape == (3, klein.BREEDTE)
    assert float(np.abs(got - want).max()) <= TOL
    assert np.allclose(np.linalg.norm(got, axis=1), 1.0, atol=1e-6)


def test_fp32_tekst_gelijk_aan_numpy(omgezet, sessies):
    w, runs = omgezet
    tokens = _tokens(runs[0][0])
    (got,) = sessies[TEKST_FP32].run(None, {"tokens": tokens})
    want = ref_tekst(tokens, w)
    assert got.shape == (len(TEKSTEN), klein.BREEDTE)
    assert float(np.abs(got - want).max()) <= TOL


def _cos(a, b):
    return np.sum(a * b, axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))


def test_int8_cosinus_minstens_0_99(omgezet, sessies):
    w, runs = omgezet
    pixels, tokens = _pixels(), _tokens(runs[0][0])
    (beeld,) = sessies[BEELD].run(None, {"pixels": pixels})
    (tekst,) = sessies[TEKST].run(None, {"tokens": tokens})
    cb, ct = _cos(beeld, ref_beeld(pixels, w)), _cos(tekst, ref_tekst(tokens, w))
    assert cb.min() >= COS, cb
    assert ct.min() >= COS, ct


def test_twee_omzettingen_dezelfde_sha256(omgezet):
    _, runs = omgezet
    (uit1, res1, _), (uit2, res2, _) = runs
    namen = sorted(p.name for p in uit1.iterdir())
    assert namen == sorted(p.name for p in uit2.iterdir())
    assert namen == sorted([BEELD, TEKST, BEELD_FP32, TEKST_FP32, TOKENIZER_BIN, META, LICENTIE])
    for naam in namen:
        assert _sha(uit1 / naam) == _sha(uit2 / naam), naam
    assert res1.stdout == res2.stdout


def test_regels_voor_models_lock(omgezet):
    """stdout: per bestand `sha256 bytes siglip2-b32-256/naam -`, wat lock.parse leest."""
    _, runs = omgezet
    uit, res, _ = runs[0]
    regels = lock.parse(res.stdout)
    assert [r.name for r in regels] == [
        f"{MAP}/{n}" for n in (BEELD, TEKST, BEELD_FP32, TEKST_FP32, TOKENIZER_BIN, LICENTIE, META)
    ]
    for r in regels:
        pad = uit / r.name.split("/", 1)[1]
        assert r.url is None and r.size == pad.stat().st_size and r.sha256 == _sha(pad)


def test_maxrss_en_tijd_gemeten(omgezet):
    _, runs = omgezet
    for _, res, meting in runs:
        assert meting["maxrss_mib"] <= MAXRSS_MIB, meting
        assert meting["s"] <= 120, meting
        assert "klaar in" in res.stderr and "maxrss" in res.stderr
    print(f"omzetten klein: {[(round(m['s'], 2), round(m['maxrss_mib'])) for *_, m in runs]}")


def test_meta_json(omgezet):
    _, runs = omgezet
    uit = runs[0][0]
    meta = json.loads((uit / META).read_text())
    assert meta["dim"] == klein.BREEDTE and meta["model"] == MAP
    assert meta["logit_scale"] == float(np.float32(klein.LOGIT_SCALE))
    assert meta["logit_bias"] == float(np.float32(klein.LOGIT_BIAS))
    vb = meta["beeld"]["voorbewerking"]
    assert vb["grootte"] == [256, 256] and vb["resample"] == "bilinear"
    assert vb["gemiddelde"] == [0.5] * 3 and vb["std"] == [0.5] * 3
    assert vb["schaal"] == pytest.approx(1 / 255)
    assert meta["tekst"]["lengte"] == 64 and meta["tekst"]["pad"] == 0 and meta["tekst"]["eos"] == 1
    assert meta["tekst"]["invoer"] == "tokens" and meta["beeld"]["invoer"] == "pixels"
    assert set(meta["bronnen"]) == {
        "model.safetensors",
        "config.json",
        "preprocessor_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
    for naam, v in meta["uitvoer"].items():
        assert v["sha256"] == _sha(uit / naam)
    # gesorteerde sleutels: dezelfde tekst bij dezelfde inhoud
    tekst = json.dumps(meta, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    assert (uit / META).read_text() == tekst


def test_tokenizer_bin_en_licentie(omgezet):
    _, runs = omgezet
    uit = runs[0][0]
    bron = uit.parent / "bron"
    verwacht = Tokenizer.from_json(bron / "tokenizer.json").to_bin()
    assert (uit / TOKENIZER_BIN).read_bytes() == verwacht
    assert (uit / LICENTIE).read_bytes() == (lock.LICENSES / LICENTIE).read_bytes()
    tok = Tokenizer.load(uit / TOKENIZER_BIN)
    v = klein.woordenlijst()
    stukken = ("h", "ond", "▁op", "▁het", "▁strand", "<eos>")
    assert tok.encode("hond op het strand")[:7] == [v[s] for s in stukken] + [0]


def test_int8_echt_int8(omgezet):
    """beeld.int8.onnx gebruikt DynamicQuantizeMatMul, en is kleiner dan fp32."""
    _, runs = omgezet
    uit = runs[0][0]
    for i8, f32 in ((BEELD, BEELD_FP32), (TEKST, TEKST_FP32)):
        assert b"DynamicQuantizeMatMul" in (uit / i8).read_bytes()
        assert b"DynamicQuantizeMatMul" not in (uit / f32).read_bytes()
        assert (uit / i8).stat().st_size < (uit / f32).stat().st_size


def test_zonder_fp32_alleen_de_uitvoer(tmp_path):
    klein.schrijf_bron(tmp_path / "bron")
    (tmp_path / "uit").mkdir()
    uit = bouw.omzet(tmp_path / "bron", tmp_path / "uit")
    assert [u.naam for u in uit] == [BEELD, TEKST, TOKENIZER_BIN, LICENTIE, META]
    assert sorted(p.name for p in (tmp_path / "uit").iterdir()) == sorted(u.naam for u in uit)


@pytest.mark.parametrize("dtype", ["F16", "BF16"])
def test_f16_en_bf16(tmp_path, sessie, dtype):
    """Ook een bestand in F16 of BF16: de numpy-versie rekent met dezelfde afgeronde waarden."""
    w = klein.schrijf_bron(tmp_path / "bron", dtype=dtype)
    (tmp_path / "uit").mkdir()
    bouw.omzet(tmp_path / "bron", tmp_path / "uit", fp32=True)
    pixels = _pixels(2)
    (got,) = sessie(tmp_path / "uit" / BEELD_FP32).run(None, {"pixels": pixels})
    assert float(np.abs(got - ref_beeld(pixels, w)).max()) <= TOL
    tokens = _tokens(tmp_path / "uit")
    (got,) = sessie(tmp_path / "uit" / TEKST_FP32).run(None, {"tokens": tokens})
    assert float(np.abs(got - ref_tekst(tokens, w)).max()) <= TOL


def test_position_ids_mogen_als_ze_kloppen(tmp_path, omgezet):
    """position_ids (niet persistent in transformers) mogen in het bestand, als 0, 1, 2, …; de
    modellen worden er niet anders van."""
    w = klein.schrijf_bron(tmp_path / "bron")
    t = klein.tensors_van({n: (v, a) for n, (v, a) in w.items()})
    t["text_model.embeddings.position_ids"] = ("I64", (1, 64), struct.pack("<64q", *range(64)))
    t["vision_model.embeddings.position_ids"] = ("I64", (1, 64), struct.pack("<64q", *range(64)))
    (tmp_path / "bron" / "model.safetensors").write_bytes(klein.safetensors(t))
    (tmp_path / "uit").mkdir()
    bouw.omzet(tmp_path / "bron", tmp_path / "uit")
    eerder = omgezet[1][0][0]
    for naam in (BEELD, TEKST, TOKENIZER_BIN):
        assert _sha(tmp_path / "uit" / naam) == _sha(eerder / naam), naam


# --- wat niet klopt: OmzetFout, code 2, geen traceback, de uitvoer blijft leeg -----------------


def _kop_en_data(pad: Path) -> tuple[dict, bytes]:
    data = pad.read_bytes()
    (n,) = struct.unpack("<Q", data[:8])
    return json.loads(data[8 : 8 + n]), data[8 + n :]


def _met_kop(kop: dict, data: bytes, n: int | None = None) -> bytes:
    tekst = json.dumps(kop).encode()
    return struct.pack("<Q", len(tekst) if n is None else n) + tekst + data


@pytest.fixture
def bron(tmp_path):
    klein.schrijf_bron(tmp_path / "bron")
    (tmp_path / "uit").mkdir()
    return tmp_path / "bron"


def _liegt(bron: Path, wijzig) -> None:
    pad = bron / "model.safetensors"
    kop, data = _kop_en_data(pad)
    pad.write_bytes(wijzig(kop, data))


def _eerste(kop: dict) -> str:
    return next(k for k in kop if k != "__metadata__")


def _zet(kop, naam, sleutel, waarde):
    kop[naam][sleutel] = waarde
    return kop


LIEGT = {
    "kop langer dan het bestand": (lambda k, d: _met_kop(k, d, n=10**8), "de kop zegt"),
    "kop van 2^63 bytes": (lambda k, d: _met_kop(k, d, n=2**63), "verwacht 2 tot"),
    "kop van 0 bytes": (lambda k, d: struct.pack("<Q", 0) + d, "verwacht 2 tot"),
    "korter dan 8 bytes": (lambda k, d: b"\x01\x02", "korter dan 8"),
    "offset buiten het bestand": (
        lambda k, d: _met_kop(_zet(k, _eerste(k), "data_offsets", [len(d), len(d) + 4]), d),
        "bytes; de vorm|overlapt|gat|eindigen",
    ),
    "eind voorbij het bestand": (
        lambda k, d: _met_kop(
            {**k, "x": {"dtype": "F32", "shape": [4], "data_offsets": [len(d), len(d) + 16]}}, d
        ),
        "eindigen op",
    ),
    "overlap": (
        lambda k, d: _met_kop({**k, "x": {**k[_eerste(k)]}}, d),
        "overlapt",
    ),
    "gat": (lambda k, d: _met_kop(k, d + b"\0" * 8), "eindigen op"),
    "afgekapt": (lambda k, d: _met_kop(k, d[:-100]), "eindigen op|overlapt|gat"),
    "dtype onbekend": (lambda k, d: _met_kop(_zet(k, _eerste(k), "dtype", "F64"), d), "dtype"),
    "lengte past niet bij de vorm": (
        lambda k, d: _met_kop(_zet(k, _eerste(k), "shape", [3]), d),
        "de vorm",
    ),
    "shape niet geheel": (lambda k, d: _met_kop(_zet(k, _eerste(k), "shape", [1.0]), d), "shape"),
    "shape bool": (lambda k, d: _met_kop(_zet(k, _eerste(k), "shape", [True]), d), "shape"),
    "offset negatief": (
        lambda k, d: _met_kop(_zet(k, _eerste(k), "data_offsets", [-4, 0]), d),
        "data_offsets",
    ),
    "sleutel extra": (lambda k, d: _met_kop(_zet(k, _eerste(k), "x", 1), d), "precies dtype"),
    "dubbele sleutel": (
        lambda k, d: _met_kop(k, d).replace(b'{"__metadata__"', b'{"logit_bias":{},"__metadata__"'),
        "geldige JSON|de kop zegt",
    ),
    "geen JSON": (lambda k, d: struct.pack("<Q", 4) + b"{{{{" + d, "geldige JSON"),
    "metadata geen tekst": (
        lambda k, d: _met_kop({**k, "__metadata__": {"a": 1}}, d),
        "__metadata__",
    ),
}


@pytest.mark.parametrize("geval", sorted(LIEGT))
def test_een_kop_die_liegt(bron, geval):
    wijzig, fout = LIEGT[geval]
    _liegt(bron, wijzig)
    with pytest.raises(OmzetFout, match=fout):
        bouw.omzet(bron, bron.parent / "uit")
    assert list((bron.parent / "uit").iterdir()) == []


def test_een_kop_die_liegt_als_proces(bron):
    """Zoals compose: code 2, één regel op stderr, geen traceback, niets in de uitvoermap."""
    _liegt(bron, LIEGT["offset buiten het bestand"][0])
    res, _ = _start_meet(bron, bron.parent / "uit")
    assert res.returncode == 2
    assert "Traceback" not in res.stderr and res.stdout == ""
    assert res.stderr.splitlines()[-1].startswith("omzetten: model.safetensors: tensor")
    assert list((bron.parent / "uit").iterdir()) == []


def test_safetensors_leest_alleen_wat_de_kop_zegt(tmp_path):
    w = klein.gewichten(10)
    pad = tmp_path / "m.safetensors"
    pad.write_bytes(klein.safetensors(klein.tensors_van(w)))
    st = Safetensors(pad)
    naam = "text_model.embeddings.token_embedding.weight"
    alles = st.lees(naam)
    assert alles.shape == (10, 64) and alles.dtype == np.float32
    assert np.array_equal(st.lees(naam, (3, 7)), alles[3:7])
    with pytest.raises(OmzetFout, match="rijen"):
        st.lees(naam, (3, 11))
    with pytest.raises(OmzetFout, match="ontbreekt"):
        st.lees("bestaat.niet")


def _config(bron: Path, **anders) -> None:
    cfg = json.loads((bron / "config.json").read_text())
    for pad, waarde in anders.items():
        d = cfg
        delen = pad.split("__")
        for deel in delen[:-1]:
            d = d[deel]
        d[delen[-1]] = waarde
    (bron / "config.json").write_text(json.dumps(cfg))


def _json(bron: Path, naam: str, **anders) -> None:
    doc = json.loads((bron / naam).read_text())
    doc.update(anders)
    (bron / naam).write_text(json.dumps(doc))


def _weg(bron: Path, naam: str) -> None:
    pad = bron / "model.safetensors"
    st = Safetensors(pad)
    w = {n: st.lees(n) for n in sorted(st.namen()) if n != naam}
    t = {n: ("F32", a.shape, a.astype("<f4").tobytes()) for n, a in w.items()}
    pad.write_bytes(klein.safetensors(t))


def _alle(st: Safetensors) -> dict:
    return {n: ("F32", st.info(n).vorm, st.lees(n).astype("<f4").tobytes()) for n in st.namen()}


def _erbij(bron: Path, naam: str, vorm=(4,), dtype="F32", data=None) -> None:
    pad = bron / "model.safetensors"
    st = Safetensors(pad)
    t = _alle(st)
    t[naam] = (dtype, vorm, data if data is not None else b"\0" * (4 * math.prod(vorm)))
    pad.write_bytes(klein.safetensors(t))


def _nan(bron: Path) -> None:
    pad = bron / "model.safetensors"
    st = Safetensors(pad)
    t = _alle(st)
    naam = "text_model.encoder.layers.1.mlp.fc2.weight"
    a = st.lees(naam).astype("<f4")
    a[3, 5] = np.nan
    t[naam] = ("F32", a.shape, a.tobytes())
    pad.write_bytes(klein.safetensors(t))


BRON_FOUT = {
    "model_type clip": (lambda b: _config(b, model_type="clip"), "model_type 'clip'"),
    "architectures": (lambda b: _config(b, architectures=["CLIPModel"]), "architectures"),
    "hidden_act quick_gelu": (
        lambda b: _config(b, text_config__hidden_act="quick_gelu"),
        "hidden_act = 'quick_gelu'",
    ),
    "hidden_size past niet": (
        lambda b: _config(b, vision_config__hidden_size=128),
        "de tekst geeft 64 getallen, het beeld 128",
    ),
    "intermediate_size past niet": (
        lambda b: _config(b, vision_config__intermediate_size=128),
        "heeft vorm",
    ),
    "projection_size past niet": (
        lambda b: _config(b, text_config__projection_size=32),
        "de tekst geeft 32 getallen, het beeld 64",
    ),
    "koppen delen niet": (lambda b: _config(b, text_config__num_attention_heads=5), "delen"),
    "een laag te veel": (lambda b: _config(b, text_config__num_hidden_layers=3), "ontbreken"),
    "een laag te weinig": (lambda b: _config(b, vision_config__num_hidden_layers=1), "onverwachte"),
    "vocab_size past niet": (lambda b: _config(b, text_config__vocab_size=999), "heeft vorm"),
    "image_size past niet": (lambda b: _config(b, vision_config__image_size=224), "veelvoud|vorm"),
    "zonder MAP-kop": (lambda b: _config(b, vision_config__vision_use_head=False), "MAP-kop"),
    "projectie anders dan beeld": (
        lambda b: _config(b, text_config__projection_size=32),
        "de tekst geeft 32",
    ),
    "tensor ontbreekt": (
        lambda b: _weg(b, "vision_model.head.attention.out_proj.bias"),
        "ontbreken: vision_model.head.attention.out_proj.bias",
    ),
    "onverwachte tensor": (
        lambda b: _erbij(b, "text_model.pooler.weight"),
        "onverwachte tensors: text_model.pooler.weight",
    ),
    "position_ids niet 0..63": (
        lambda b: _erbij(
            b, "text_model.embeddings.position_ids", (1, 64), "I64", struct.pack("<64q", *[0] * 64)
        ),
        "niet 0, 1, 2",
    ),
    "position_ids als float": (
        lambda b: _erbij(b, "vision_model.embeddings.position_ids", (1, 64)),
        "als I64",
    ),
    "NaN in een gewicht": (_nan, "NaN"),
    "voorbewerking ander type": (
        lambda b: _json(b, "preprocessor_config.json", image_processor_type="CLIPImageProcessor"),
        "image_processor_type",
    ),
    "voorbewerking andere maat": (
        lambda b: _json(b, "preprocessor_config.json", size={"height": 224, "width": 224}),
        "past niet bij image_size",
    ),
    "voorbewerking resample 7": (
        lambda b: _json(b, "preprocessor_config.json", resample=7),
        "resample",
    ),
    "voorbewerking zonder resize": (
        lambda b: _json(b, "preprocessor_config.json", do_resize=False),
        "do_resize",
    ),
    "padding links": (
        lambda b: _json(b, "tokenizer_config.json", padding_side="left"),
        "padding_side 'left'",
    ),
    "met <bos>": (lambda b: _json(b, "tokenizer_config.json", add_bos_token=True), "add_bos_token"),
    "zonder <eos>": (lambda b: _json(b, "tokenizer_config.json", add_eos_token=False), "add_eos"),
    "tokenizer.json kapot": (lambda b: (b / "tokenizer.json").write_text("{"), "tokenizer.json"),
    "tokenizer.json Unigram": (
        lambda b: _json(b, "tokenizer.json", model={"type": "Unigram"}),
        "niet BPE",
    ),
    "config.json geen JSON": (lambda b: (b / "config.json").write_text("[1,"), "config.json"),
    "bronbestand ontbreekt": (lambda b: (b / "tokenizer_config.json").unlink(), "ontbreekt"),
    "config.json te groot": (
        lambda b: _json(b, "config.json", opvulling="x" * bouw.MAX_JSON),
        "config.json: groter dan 1000000 bytes",
    ),
    "tokenizer_config.json te groot": (
        lambda b: _json(b, "tokenizer_config.json", opvulling="x" * bouw.MAX_TOKENIZER_CONFIG),
        "tokenizer_config.json: groter dan 16000000 bytes",
    ),
}


def test_tokenizer_config_mag_groter_zijn_dan_config(bron):
    """Een tokenizer_config.json die elk toegevoegd token noemt (Gemma 3: ±1,2 MB) kan wel."""
    _json(bron, "tokenizer_config.json", opvulling="x" * 2 * bouw.MAX_JSON)
    assert [u.naam for u in bouw.omzet(bron, bron.parent / "uit")][-1] == META


@pytest.mark.parametrize("geval", sorted(BRON_FOUT))
def test_een_bron_die_niet_klopt(bron, geval):
    wijzig, fout = BRON_FOUT[geval]
    wijzig(bron)
    with pytest.raises(OmzetFout, match=fout):
        bouw.omzet(bron, bron.parent / "uit")
    assert list((bron.parent / "uit").iterdir()) == []


def test_de_uitvoermap_moet_leeg_zijn(bron):
    (bron.parent / "uit" / "oud.onnx").write_bytes(b"x")
    with pytest.raises(OmzetFout, match="niet leeg"):
        bouw.omzet(bron, bron.parent / "uit")
    assert [p.name for p in (bron.parent / "uit").iterdir()] == ["oud.onnx"]
    with pytest.raises(OmzetFout, match="geen map"):
        bouw.omzet(bron, bron.parent / "bestaat-niet")
    (bron.parent / "link").symlink_to(bron.parent / "uit")
    with pytest.raises(OmzetFout, match="geen map"):
        bouw.omzet(bron, bron.parent / "link")


def test_een_fout_halverwege_laat_niets_achter(bron, monkeypatch):
    """Gaat het mis na het eerste model, dan gaat wat er al stond weer weg."""
    echt = bouw.tekst_graaf

    def kapot(*a, **k):
        raise OmzetFout("kunstmatig")

    monkeypatch.setattr(bouw, "tekst_graaf", kapot)
    with pytest.raises(OmzetFout, match="kunstmatig"):
        bouw.omzet(bron, bron.parent / "uit")
    assert list((bron.parent / "uit").iterdir()) == []
    monkeypatch.setattr(bouw, "tekst_graaf", echt)


def test_een_onbekende_sleutel_in_de_voorbewerking_is_een_melding(bron):
    _json(bron, "preprocessor_config.json", do_pad=False)
    meldingen = []
    bouw.omzet(bron, bron.parent / "uit", melden=meldingen.append)
    assert any("do_pad" in m for m in meldingen)


def test_zonder_numpy_in_de_app():
    """De omzetter staat onder app/analyse/worker/ (test_no_numpy_in_app.py staat dat toe);
    siglip.py zelf importeert numpy pas na /opt/analyse in sys.path."""
    tekst = (API / "app/analyse/worker/omzetten/siglip.py").read_text()
    assert "import numpy" not in tekst and "from app.analyse.worker.omzetten import bouw" in tekst
    assert OMZET_BOOT.endswith(
        "runpy.run_module('app.analyse.worker.omzetten.siglip',run_name='__main__',alter_sys=True)"
    )


def test_opdrachtregel_zonder_bron(tmp_path):
    (tmp_path / "uit").mkdir()
    res, _ = _start_meet(tmp_path / "geen", tmp_path / "uit")
    assert res.returncode == 2 and "Traceback" not in res.stderr
    assert "bronmap" in res.stderr


def test_ruimt_een_kapotte_kopie_niet_op_buiten_de_uitvoer(bron):
    """De bron blijft zoals hij was (alleen-lezen in compose; hier: dezelfde bytes)."""
    voor = {p.name: _sha(p) for p in bron.iterdir()}
    bouw.omzet(bron, bron.parent / "uit")
    assert {p.name: _sha(p) for p in bron.iterdir()} == voor
    shutil.rmtree(bron.parent / "uit")
