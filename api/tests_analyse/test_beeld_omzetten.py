"""De omzetter van SigLIP 2 (fase 6, X3a, Keuze 73 en 83 laag 2; job api-analyse).

Een kleine SigLIP-vorm met willekeurige gewichten (siglip_klein.py: 2 lagen, breedte 64, de namen
van het echte bestand) gaat door de omzetter zoals compose hem start (`python -I -c OMZET_BOOT
<api> --bron … --uit …`). De test rekent dezelfde voorwaartse stap met een eigen numpy-versie in
float64, rechtstreeks uit de gewichten en de regels van SiglipModel (niet uit de omzetter), en eist:

- fp32 hooguit 1·10⁻⁵ van de numpy-versie, voor beeld en tekst;
- int8 cosinus ≥ 0,99 met de numpy-versie (en met de fp32-graaf);
- int8 is sinds VERSIE 3 per token (MatMulInteger, CI op 011bf2d); de vorm van VERSIE 2 (per
  tensor, DynamicQuantizeMatMul) geeft met per_token=False nog byte voor byte de bestanden van toen;
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
from app.analyse.onnxw import Model
from app.analyse.runtime import OMZET_BOOT
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
    UITVOER,
    OmzetFout,
    bouw,
)
from app.analyse.worker.omzetten.safetensors import Safetensors
from tests_analyse import siglip_klein as klein

API = Path(__file__).resolve().parents[1]
# De opdracht van de dienst analyse-omzetten: x-omzet-boot in compose.analyse.yaml is
# runtime.OMZET_BOOT (test_compose_analyse.py), met /srv als eerste argument
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
    """De standaardomzetting (per token, VERSIE 3) tegen de numpy-versie en tegen de fp32-graaf."""
    w, runs = omgezet
    pixels, tokens = _pixels(), _tokens(runs[0][0])
    (beeld,) = sessies[BEELD].run(None, {"pixels": pixels})
    (tekst,) = sessies[TEKST].run(None, {"tokens": tokens})
    cb, ct = _cos(beeld, ref_beeld(pixels, w)), _cos(tekst, ref_tekst(tokens, w))
    assert cb.min() >= COS, cb
    assert ct.min() >= COS, ct
    (beeld32,) = sessies[BEELD_FP32].run(None, {"pixels": pixels})
    (tekst32,) = sessies[TEKST_FP32].run(None, {"tokens": tokens})
    assert _cos(beeld, beeld32).min() >= COS and _cos(tekst, tekst32).min() >= COS


def test_twee_omzettingen_dezelfde_sha256(omgezet):
    _, runs = omgezet
    (uit1, res1, _), (uit2, res2, _) = runs
    namen = sorted(p.name for p in uit1.iterdir())
    assert namen == sorted(p.name for p in uit2.iterdir())
    assert namen == sorted([BEELD, TEKST, BEELD_FP32, TEKST_FP32, TOKENIZER_BIN, META, LICENTIE])
    for naam in namen:
        assert _sha(uit1 / naam) == _sha(uit2 / naam), naam
    assert res1.stdout == res2.stdout


# De sha256 van dit kleine model in de vorm van VERSIE 2 (per tensor), gemeten vóór de opties voor
# het int8-rapport van BZ4 in bouw.py kwamen (x3a.md, "CI op 3a9b07d"). Sinds VERSIE 3 geeft
# omzet(..., per_token=False) nog precies deze bytes (de variant `per tensor (v2)` van het rapport)
VOOR_DE_OPTIES = {
    BEELD: "31fb8622f226cdb17523cd72a1acf10d9a63f3bb633b678fd10138aee64c57ff",
    TEKST: "ebbb3c053be978fc8e8d5904df48729003e0c28864f2afa79744827cf14b37cb",
    BEELD_FP32: "9d58a41e3a7ca75acddd4a479fae84860097a112d944874397d4ec3aeade6404",
    TEKST_FP32: "4037109fabe0222d7e6749721eecb928cb11ad6178917096d0ef7826bbc56904",
    TOKENIZER_BIN: "b70f6c67f9f0365348ccca704a92cb2d2524a32da658206e0bc56194010924ba",
    META: "868e9777827b22330845b27c451303756c35853caa89775b1bb99aaeb7b95763",
    LICENTIE: "0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594",
}


# De sha256 van de standaardomzetting (VERSIE 3, per token), bewust nieuw: uitgerekend na de
# overstap op per token (x3a.md, "CI op 011bf2d"). Alleen beeld.int8.onnx, tekst.int8.onnx en
# meta.json zijn anders dan in VERSIE 2; de fp32-grafen, tokenizer.bin en de licentie niet
V3 = {
    **VOOR_DE_OPTIES,
    BEELD: "3eb97f5fd9775f7eeaeb39a7232f219d1319de63fe0a48b57dc24544041184d2",
    TEKST: "81e231565cc31aba1791578de2641d1f2099434bb6d71adf9cc3b33d0a360f67",
    META: "93e6f02f144aaf3f470e76acb4051dbd839bfd3e4ad00d861cfe96e43a59be50",
}


def test_de_standaard_geeft_de_bytes_van_v3(omgezet):
    uit = omgezet[1][0][0]
    assert {p.name: _sha(p) for p in uit.iterdir()} == V3
    assert {k for k in V3 if V3[k] != VOOR_DE_OPTIES[k]} == {BEELD, TEKST, META}


def test_per_tensor_geeft_byte_voor_byte_de_bestanden_van_v2(tmp_path):
    """omzet(..., per_token=False) is de vorm van VERSIE 2: alle zeven bestanden (met --fp32)
    precies zoals toen, meta.json ook (omzetter 2, zonder `int8`)."""
    klein.schrijf_bron(tmp_path / "bron")
    (tmp_path / "uit").mkdir()
    uitkomsten = bouw.omzet(tmp_path / "bron", tmp_path / "uit", fp32=True, per_token=False)
    assert {p.name: _sha(p) for p in (tmp_path / "uit").iterdir()} == VOOR_DE_OPTIES
    assert {u.naam: u.sha256 for u in uitkomsten} == VOOR_DE_OPTIES
    meta = json.loads((tmp_path / "uit" / META).read_text())
    assert meta["omzetter"] == bouw.VERSIE_PER_TENSOR == 2 and "int8" not in meta


def test_varianten_uit_dezelfde_bytes(tmp_path):
    """De opties van bouw.py expliciet op hun standaard maken precies dezelfde graaf als zonder:
    de bytes van V3. Met per_token=False en de rest op de standaard: die van VOOR_DE_OPTIES (v2).
    De fp32-grafen zijn met en zonder per_token dezelfde."""
    klein.schrijf_bron(tmp_path / "bron")
    st = Safetensors(tmp_path / "bron" / "model.safetensors")
    vorm = bouw.lees_config(json.loads((tmp_path / "bron" / "config.json").read_text()))
    standaard = [
        ({}, V3),
        ({"fp32_als": None, "per_token": True, "glad": None, "q_max": None, "meet": None}, V3),
        ({"fp32_als": lambda _label: False, "glad": {}, "q_max": bouw.Q_MAX}, V3),
        ({"per_token": False}, VOOR_DE_OPTIES),
        ({"fp32_als": None, "per_token": False, "glad": {}, "q_max": None}, VOOR_DE_OPTIES),
    ]
    for int8, (beeld, tekst) in ((True, (BEELD, TEKST)), (False, (BEELD_FP32, TEKST_FP32))):
        for opties, sha in standaard:
            g = bouw.beeld_graaf(st, vorm, int8=int8, **opties)
            assert hashlib.sha256(Model(g).to_bytes()).hexdigest() == sha[beeld], (int8, opties)
            g = bouw.tekst_graaf(st, vorm, int8=int8, embedding_fp32=False, **opties)
            assert hashlib.sha256(Model(g).to_bytes()).hexdigest() == sha[tekst], (int8, opties)


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
    # omzetter 2: het gewicht van een Linear met |q| ≤ 64 (de int8-bytes anders dan bij 1); 3: elke
    # Linear per token (MatMulInteger), en meta.json zegt dat onder `int8`
    assert meta["formaat"] == 1 and meta["omzetter"] == bouw.VERSIE == 3
    assert (
        meta["int8"]
        == bouw.INT8_META
        == {
            "linear": "MatMulInteger",
            "invoer": "uint8 per rij (token): a = max|x|/127, nulpunt 128",
            "gewicht": "int8 per kolom: s = max|kolom|/64, |q| <= 64",
            "embedding": "int8 per rij: s = max|rij|/127, |q| <= 127",
        }
    )
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
    """De standaard (VERSIE 3) van beide torens rekent met MatMulInteger, nooit meer met
    DynamicQuantizeMatMul; de fp32-grafen met geen van beide; int8 is kleiner dan fp32."""
    _, runs = omgezet
    uit = runs[0][0]
    for i8, f32 in ((BEELD, BEELD_FP32), (TEKST, TEKST_FP32)):
        ops8 = {n[0] for n in lees_onnx(uit / i8)[0]}
        ops32 = {n[0] for n in lees_onnx(uit / f32)[0]}
        assert "MatMulInteger" in ops8 and "DynamicQuantizeMatMul" not in ops8, i8
        assert not ops32 & {"MatMulInteger", "DynamicQuantizeMatMul"}, f32
        assert b"DynamicQuantizeMatMul" not in (uit / i8).read_bytes()
        assert (uit / i8).stat().st_size < (uit / f32).stat().st_size


# --- int8: een schaal per kolom en per rij (review golf 5, G5-C3) -------------------------------
#
# De cosinus van ≥ 0,99 merkt een schaal per tensor in een klein model niet altijd op, en
# test_int8_echt_int8 kijkt alleen of de op er is. Hier de grafen zelf: een eigen lezer van het
# protobuf (veldnummers van onnx.proto), zonder onnxw en zonder het pakket onnx.

_DTYPE = {1: "<f4", 2: "u1", 3: "i1", 7: "<i8"}  # FLOAT, UINT8, INT8, INT64


def _varint(b: memoryview, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        shift += 7
        if c < 0x80:
            return n, i


def _velden(b: memoryview) -> list[tuple[int, int | memoryview]]:
    """Een protobuf-bericht als (veld, waarde): een getal (varint) of de bytes (lengte, 32, 64)."""
    i, uit = 0, []
    while i < len(b):
        sleutel, i = _varint(b, i)
        veld, soort = sleutel >> 3, sleutel & 7
        if soort == 0:
            w, i = _varint(b, i)
        elif soort == 2:
            n, i = _varint(b, i)
            w, i = b[i : i + n], i + n
        else:
            n = {1: 8, 5: 4}[soort]
            w, i = b[i : i + n], i + n
        uit.append((veld, w))
    return uit


def _tekst(velden, nummer: int, standaard: str | None = None) -> str:
    gevonden = [bytes(w).decode() for v, w in velden if v == nummer]
    return gevonden[0] if gevonden else standaard


def lees_onnx(pad: Path) -> tuple[list[tuple[str, str, list[str]]], dict[str, np.ndarray]]:
    """ModelProto.graph (7); GraphProto: node 1, initializer 5; NodeProto: input 1, op_type 4,
    domain 7; TensorProto: dims 1, data_type 2, name 8, raw_data 9. Geeft de nodes (op, domein,
    invoer) en de initializers als numpy, met hun type en vorm."""
    (graaf,) = [w for v, w in _velden(memoryview(pad.read_bytes())) if v == 7]
    nodes, inits = [], {}
    for v, w in _velden(graaf):
        f = _velden(w) if v in (1, 5) else []
        if v == 1:
            nodes.append(
                (_tekst(f, 4), _tekst(f, 7, ""), [bytes(x).decode() for k, x in f if k == 1])
            )
        elif v == 5:
            (dtype,) = [x for k, x in f if k == 2]
            dims = tuple(x for k, x in f if k == 1)
            (raw,) = [x for k, x in f if k == 9]
            inits[_tekst(f, 8)] = np.frombuffer(raw, dtype=_DTYPE[dtype]).reshape(dims)
    return nodes, inits


def _linears(toren: str) -> list[str]:
    """Elke Linear van een toren, zoals de omzetter ze noemt (q, k en v van een laag samen)."""
    uit = []
    for i in range(klein.LAGEN):
        p = f"{toren}.encoder.layers.{i}"
        uit += [f"{p}.self_attn.qkv", f"{p}.self_attn.out_proj", f"{p}.mlp.fc1", f"{p}.mlp.fc2"]
    if toren == "text_model":
        return uit + ["text_model.head"]
    kop = "vision_model.head"
    namen = ("attention.q", "attention.kv", "attention.out_proj", "mlp.fc1", "mlp.fc2")
    return uit + [f"{kop}.{n}" for n in namen]


def _wt(w: dict, label: str) -> np.ndarray:
    """Het gewicht van een Linear als [in, uit] (Wᵀ), uit de bron, zonder de omzetter."""
    e = klein.BREEDTE
    if label.endswith(".self_attn.qkv"):
        p = label.removesuffix(".qkv")
        rijen = np.concatenate([_w(w, f"{p}.{n}_proj.weight") for n in ("q", "k", "v")])
    elif label.startswith("vision_model.head.attention.") and label.endswith((".q", ".kv")):
        win = _w(w, "vision_model.head.attention.in_proj_weight")
        rijen = win[:e] if label.endswith(".q") else win[e:]
    else:
        rijen = _w(w, f"{label}.weight")
    return rijen.T


# De grootste |q|: 64 voor een Linear (MatMulInteger, of DynamicQuantizeMatMul per tensor; zonder
# VNNI loopt een paar van uint8 maal int8 met 127 over in int16, CI op 7569d48), 127 voor de
# embedding (geen matmul)
LINEAR, EMBEDDING = 64, 127


def _q8_per_kolom(wt: np.ndarray, top: int) -> tuple[np.ndarray, np.ndarray]:
    """Keuze 73: s = max |kolom| / top (in float32), q = rint(w / s), nulpunt 0."""
    s = (np.abs(wt).max(axis=0) / float(top)).astype(np.float32)
    return np.clip(np.rint(wt / s.astype(np.float64)), -top, top).astype(np.int8), s


# Per vorm de op van elke Linear: (op, domein, invoer na x); de standaard (VERSIE 3) en per tensor
# (VERSIE 2)
VORM = {
    "standaard": ("MatMulInteger", "", lambda x: [f"{x}.w8", f"{x}.nul"]),
    "per tensor": (
        "DynamicQuantizeMatMul",
        "com.microsoft",
        lambda x: [f"{x}.w8", f"{x}.s", f"{x}.z", f"{x}.b"],
    ),
}


@pytest.fixture(scope="module")
def per_tensor(tmp_path_factory, omgezet):
    """De grafen van VERSIE 2 (per_token=False) uit dezelfde bron als `omgezet`."""
    uit = tmp_path_factory.mktemp("per_tensor")
    bouw.omzet(omgezet[1][0][0].parent / "bron", uit, per_token=False)
    return uit


@pytest.mark.parametrize("vorm", sorted(VORM))
def test_int8_een_schaal_per_kolom_en_per_rij(omgezet, per_tensor, vorm):
    """Elke Linear is in de standaard MatMulInteger met B int8 [in, uit] en een scalair nulpunt
    128 (uint8), daarna × a × s + b; per tensor (VERSIE 2) DynamicQuantizeMatMul met B, B_scale
    [uit] en een nulpunt [uit] van nullen. In beide de schalen per kolom tegen de gewichten van de
    bron, |q| ≤ 64 en elke kolom haalt 64. Geen MatMul of Gemm meer met een gewicht. De embedding
    van de tekst: een schaal per rij, [woorden, 1], en elke rij haalt 127."""
    w, runs = omgezet
    uit = runs[0][0] if vorm == "standaard" else per_tensor
    op_naam, domein_naam, verwacht = VORM[vorm]
    for model, toren in ((BEELD, "vision_model"), (TEKST, "text_model")):
        nodes, inits = lees_onnx(uit / model)
        ops = {n[0] for n in nodes}
        assert not ops & ({"MatMulInteger", "DynamicQuantizeMatMul"} - {op_naam}), (model, ops)
        lin = [n for n in nodes if n[0] == op_naam]
        labels = [n[2][1].removesuffix(".w8") for n in lin]
        assert labels == _linears(toren), model
        for (_op, domein, invoer), label in zip(lin, labels, strict=True):
            assert domein == domein_naam
            assert invoer[1:] == verwacht(label), (label, invoer)
            q, s = inits[f"{label}.w8"], inits[f"{label}.s"]
            wt = _wt(w, label)
            assert q.dtype == np.int8 and q.shape == wt.shape, label
            assert s.dtype == np.float32 and s.shape == (wt.shape[1],), label
            assert inits[f"{label}.b"].shape == (wt.shape[1],), label
            if vorm == "standaard":
                nul = inits[f"{label}.nul"]
                assert nul.dtype == np.uint8 and nul.shape == () and int(nul) == 128, label
                assert f"{label}.z" not in inits, label
            else:
                z = inits[f"{label}.z"]
                assert z.dtype == np.int8 and z.shape == (wt.shape[1],) and not z.any(), label
            q_ref, s_ref = _q8_per_kolom(wt, LINEAR)
            assert np.array_equal(s, s_ref), label
            assert np.array_equal(q, q_ref), label
            assert (np.abs(q.astype(np.int16)).max(axis=0) == LINEAR).all(), label
        for op, _domein, invoer in nodes:
            if op in ("MatMul", "Gemm"):
                assert not any(i in inits for i in invoer), (model, op, invoer)
    _, inits = lees_onnx(uit / TEKST)
    naam = "text_model.embeddings.token_embedding.weight"
    tabel, schaal, emb = inits[f"{naam}.w8"], inits[f"{naam}.s"], _w(w, naam)
    assert tabel.dtype == np.int8 and tabel.shape == emb.shape
    assert schaal.dtype == np.float32 and schaal.shape == (emb.shape[0], 1)
    q_ref, s_ref = _q8_per_kolom(emb.T, EMBEDDING)
    assert np.array_equal(schaal[:, 0], s_ref) and np.array_equal(tabel, q_ref.T)
    assert (np.abs(tabel.astype(np.int16)).max(axis=1) == EMBEDDING).all()


@pytest.mark.parametrize("vorm", sorted(VORM))
def test_int8_geen_gewicht_van_een_linear_boven_64(omgezet, per_tensor, vorm):
    """Elk int8-gewicht dat B is van een MatMulInteger (of per tensor van een
    DynamicQuantizeMatMul), in beide grafen, heeft |q| ≤ 64: zo past elk paar van vpmaddubsw
    (255·64·2 = 32.640) in int16, ook op een CPU zonder VNNI. Het enige andere int8-gewicht is de
    embedding van de tekst, en die mag tot 127."""
    uit = omgezet[1][0][0] if vorm == "standaard" else per_tensor
    for model in (BEELD, TEKST):
        nodes, inits = lees_onnx(uit / model)
        b = {invoer[1] for op, _domein, invoer in nodes if op == VORM[vorm][0]}
        int8 = {naam for naam, a in inits.items() if a.dtype == np.int8 and a.ndim == 2}
        assert b and b <= int8, model
        for naam in b:
            assert int(np.abs(inits[naam].astype(np.int16)).max()) <= LINEAR, (model, naam)
        rest = int8 - b
        emb = "text_model.embeddings.token_embedding.weight.w8"
        assert rest == ({emb} if model == TEKST else set()), (model, rest)


def test_int8_een_uitschieter_raakt_alleen_zijn_eigen_kolom(bron, tmp_path):
    """Een uitvoer van een Linear (een rij van het gewicht in de bron) en een rij van de embedding
    100 × zo groot: alleen hun eigen schaal wordt 100 × zo groot, alle andere schalen en int8-
    waarden blijven byte voor byte gelijk. Met een schaal per tensor zou elke andere kolom 100 ×
    grover worden (en bijna alles 0)."""
    anders = tmp_path / "anders"
    shutil.copytree(bron, anders)
    st = Safetensors(anders / "model.safetensors")
    t = _alle(st)
    e = klein.BREEDTE
    # naam in de bron, rij → (label of de embedding, kolom in Wᵀ of rij van de tabel)
    tl, vl = "text_model.encoder.layers.0", "vision_model.encoder.layers.1"
    gevallen = {
        (f"{tl}.mlp.fc1.weight", 3): (f"{tl}.mlp.fc1", 3),
        (f"{vl}.self_attn.k_proj.weight", 5): (f"{vl}.self_attn.qkv", e + 5),  # k na q
        ("text_model.embeddings.token_embedding.weight", 7): (None, 7),
    }
    for naam, rij in gevallen:
        a = st.lees(naam).astype("<f4").copy()
        a[rij] *= 100
        t[naam] = ("F32", a.shape, a.tobytes())
    (anders / "model.safetensors").write_bytes(klein.safetensors(t))
    for map_ in ("uit", "uit-anders"):
        (tmp_path / map_).mkdir(exist_ok=True)
    bouw.omzet(bron, tmp_path / "uit")
    bouw.omzet(anders, tmp_path / "uit-anders")
    for (naam, _rij), (label, k) in gevallen.items():
        model = TEKST if naam.startswith("text_model") else BEELD
        _, voor = lees_onnx(tmp_path / "uit" / model)
        _, na = lees_onnx(tmp_path / "uit-anders" / model)
        if label is None:  # de embedding: een schaal per rij
            q0, s0 = voor[f"{naam}.w8"], voor[f"{naam}.s"][:, 0]
            q1, s1 = na[f"{naam}.w8"], na[f"{naam}.s"][:, 0]
            andere = np.arange(len(s0)) != k
            assert np.array_equal(q0[andere], q1[andere])
        else:
            q0, s0 = voor[f"{label}.w8"], voor[f"{label}.s"]
            q1, s1 = na[f"{label}.w8"], na[f"{label}.s"]
            andere = np.arange(len(s0)) != k
            assert np.array_equal(q0[:, andere], q1[:, andere])
        assert np.array_equal(s0[andere], s1[andere]), naam
        assert float(s1[k] / s0[k]) == pytest.approx(100, rel=1e-5), naam


def test_zonder_fp32_alleen_de_uitvoer(tmp_path):
    klein.schrijf_bron(tmp_path / "bron")
    (tmp_path / "uit").mkdir()
    uit = bouw.omzet(tmp_path / "bron", tmp_path / "uit")
    assert [u.naam for u in uit] == [BEELD, TEKST, TOKENIZER_BIN, LICENTIE, META]
    assert sorted(p.name for p in (tmp_path / "uit").iterdir()) == sorted(u.naam for u in uit)


def test_met_en_zonder_fp32_dezelfde_vijf_bestanden(omgezet, tmp_path):
    """BZ2 zet twee keer om zoals de dienst (zonder --fp32) en een keer met --fp32 voor BZ4. Die
    laatste geeft dezelfde vijf bestanden van UITVOER, byte voor byte, meta.json ook: meta.json
    noemt alleen de andere vier, nooit de fp32-grafen (review golf 5, uitrol F1)."""
    _, runs = omgezet
    met = runs[0][0]
    zonder = tmp_path / "zonder"
    zonder.mkdir()
    res, _ = _start_meet(met.parent / "bron", zonder)
    assert res.returncode == 0, res.stderr
    assert sorted(p.name for p in zonder.iterdir()) == sorted(UITVOER)
    for naam in UITVOER:
        assert _sha(zonder / naam) == _sha(met / naam), naam
    meta = json.loads((zonder / META).read_text())
    assert sorted(meta["uitvoer"]) == sorted([BEELD, TEKST, TOKENIZER_BIN, LICENTIE])


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
    # De review van golf 5 (veilig F2): een dtype dat geen tekst is (eerst TypeError), en een vorm
    # met reuzen van getallen (het product had meer dan 4300 cijfers: ValueError in de f-string)
    "dtype lijst": (lambda k, d: _met_kop(_zet(k, _eerste(k), "dtype", ["F32"]), d), "dtype"),
    "dtype object": (lambda k, d: _met_kop(_zet(k, _eerste(k), "dtype", {"F32": 1}), d), "dtype"),
    "vorm met reuzen": (
        lambda k, d: _met_kop(_zet(k, _eerste(k), "shape", [10**4000] * 8), d),
        "shape",
    ),
    "vorm van 1000 assen": (
        lambda k, d: _met_kop(_zet(k, _eerste(k), "shape", [10**9] * 1000), d),
        "shape",
    ),
    "vorm groter dan het bestand": (
        lambda k, d: _met_kop(_zet(k, _eerste(k), "shape", [2**40, 2**40]), d),
        "groter dan het bestand",
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


def _genest(diep: int) -> dict:
    n: dict = {"type": "Lowercase"}
    for _ in range(diep):
        n = {"type": "Sequence", "normalizers": [n]}
    return n


BRON_FOUT = {
    "model_type clip": (lambda b: _config(b, model_type="clip"), "model_type 'clip'"),
    "architectures": (lambda b: _config(b, architectures=["CLIPModel"]), "architectures"),
    "hidden_act quick_gelu": (
        lambda b: _config(b, text_config__hidden_act="quick_gelu"),
        "hidden_act = 'quick_gelu'",
    ),
    # De review van golf 5 (veilig F3, F4): eerst TypeError en RecursionError, nu een fout
    "hidden_act lijst": (
        lambda b: _config(b, text_config__hidden_act=["gelu"]),
        "hidden_act = list",
    ),
    "hidden_act object": (lambda b: _config(b, vision_config__hidden_act={}), "hidden_act = dict"),
    "tokenizer.json diep genest": (
        lambda b: _json(b, "tokenizer.json", normalizer=_genest(1200)),
        "diep genest",
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


@pytest.mark.parametrize(
    "wijzig",
    [
        lambda b: _liegt(b, LIEGT["dtype lijst"][0]),
        lambda b: _liegt(b, LIEGT["vorm met reuzen"][0]),
        BRON_FOUT["hidden_act lijst"][0],
        BRON_FOUT["tokenizer.json diep genest"][0],
    ],
    ids=["dtype lijst", "vorm met reuzen", "hidden_act lijst", "tokenizer.json diep genest"],
)
def test_wat_eerst_een_traceback_gaf_is_nu_code_2(bron, wijzig):
    """De review van golf 5 (veilig F2-F4): zoals compose, code 2 met één regel, geen traceback
    (code 1), niets in de uitvoermap."""
    wijzig(bron)
    res, _ = _start_meet(bron, bron.parent / "uit")
    assert res.returncode == 2, res.stderr[-300:]
    assert "Traceback" not in res.stderr and res.stdout == ""
    assert res.stderr.splitlines()[-1].startswith("omzetten: ")
    assert list((bron.parent / "uit").iterdir()) == []


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
