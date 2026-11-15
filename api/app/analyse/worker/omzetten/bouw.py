"""De omzetting zelf (fase 6, X3a, Keuze 73): SigLIP uit safetensors naar twee ONNX-grafen.

**De betekenis van elke laag** komt uit config.json en de namen in safetensors, niet uit ons
geheugen. Een ander `model_type` dan "siglip", een onbekende `hidden_act`, een tensor die
ontbreekt of te veel is, of een vorm die niet past bij config.json, geeft OmzetFout. Waar
config.json een sleutel niet noemt, geldt de standaard van SiglipTextConfig en SiglipVisionConfig
(transformers 5.22.2, gelezen, niet geïmporteerd).

**De rekensom** (SiglipModel, zoals transformers hem uitvoert):

- een encoderlaag: x + attn(LN1(x)), dan x + MLP(LN2(x)); attn = softmax(q·kᵀ·d^-½)·v per kop,
  zonder masker; MLP = fc2(GELU(fc1(x))), met de GELU van `hidden_act` (tanh of erf);
- beeld: Conv met stap p (de patches), + position_embedding, de lagen, post_layernorm, dan de
  MAP-kop: de probe als vraag aan de patches (in_proj: rijen 0:E, E:2E, 2E:3E voor q, k, v),
  out_proj, h + MLP(LN(h)), en de eerste (enige) positie;
- tekst: token_embedding + position_embedding (64 tokens), de lagen, final_layer_norm, de laatste
  positie (zonder masker: met `<pad>` rechts is dat meestal een `<pad>`, zoals getraind), dan
  text_model.head;
- beide: L2-genormaliseerd. De logits zijn cos · exp(logit_scale) + logit_bias (meta.json).

**int8** (zoals de proxy van B6): elke Linear wordt `DynamicQuantizeMatMul` (com.microsoft) met
gewichten in int8, symmetrisch, een schaal per kolom (max |kolom| / 127) en nulpunt 0; de invoer
kwantiseert onnxruntime zelf per oproep. De Conv van de patches blijft fp32. De embedding van de
tekst is int8 met een schaal per rij: Gather, Cast, en Mul met de geschaalde rij. q, k en v van een
laag zijn één MatMul (de kolommen naast elkaar); de schaal per kolom verandert daardoor niet.

**Altijd dezelfde bytes.** Alleen bewerkingen per element (abs, max, delen in float64, rint) en
herschikken; geen BLAS, geen draden, geen tijd of pad in de uitvoer. meta.json met gesorteerde
sleutels. Twee keer omzetten geeft dezelfde sha256 (BZ2 doet het op twee runners).

**Geheugen.** Elke initializer is een callable die onnxw pas bij het schrijven aanroept: er staat
één gewicht tegelijk in het geheugen. De embedding (256.000 × 768) wordt in blokken van rijen
gelezen; alleen zijn int8-vorm (197 MB) staat er even helemaal.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import resource
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.analyse import lock, onnxw
from app.analyse.onnxw import FLOAT, INT8, INT64, Graph, Model
from app.analyse.tokenizer import LENGTE, Tokenizer, TokenizerFout
from app.analyse.worker.omzetten import (
    BEELD,
    BEELD_FP32,
    BRONNEN,
    CONFIG,
    INVOER_BEELD,
    INVOER_TEKST,
    LICENTIE,
    MAP,
    META,
    SAFETENSORS,
    TEKST,
    TEKST_FP32,
    TOKENIZER_BIN,
    TOKENIZER_CONFIG,
    TOKENIZER_JSON,
    UIT_BEELD,
    UIT_TEKST,
    VOORBEWERKING,
    OmzetFout,
)
from app.analyse.worker.omzetten.safetensors import FLOATS, Safetensors

VERSIE = 1  # van de omzetter; staat in meta.json
MAX_JSON = 1_000_000  # config.json en preprocessor_config.json zijn een paar kB
# tokenizer_config.json noemt soms elk toegevoegd token (bij Gemma 3 ±1,2 MB); de echte grootte
# staat in models.lock
MAX_TOKENIZER_CONFIG = 16_000_000
BLOK_RIJEN = 4096  # rijen van de embedding per keer (4096 × 768 × 4 = 12,6 MB)

GELU = {"gelu_pytorch_tanh": "tanh", "gelu_new": "tanh", "gelu": "erf"}
RESAMPLE = {0: "nearest", 1: "lanczos", 2: "bilinear", 3: "bicubic", 4: "box", 5: "hamming"}
VERWERKERS = ("SiglipImageProcessor", "SiglipImageProcessorFast")
# Sleutels van preprocessor_config.json die niets aan de rekensom doen
VOORBEWERKING_RUST = {"image_processor_type", "processor_class", "do_convert_rgb"}


@dataclass(frozen=True)
class Toren:
    """Eén toren (tekst of beeld): de vorm van zijn lagen."""

    naam: str  # "text_model" of "vision_model"
    breedte: int
    mlp: int
    koppen: int
    lagen: int
    eps: float
    gelu: str  # "tanh" of "erf"


@dataclass(frozen=True)
class Vorm:
    tekst: Toren
    beeld: Toren
    woorden: int
    posities: int
    projectie: int
    beeldgrootte: int
    patch: int
    kanalen: int

    @property
    def patches(self) -> int:
        return (self.beeldgrootte // self.patch) ** 2


@dataclass(frozen=True)
class Uitkomst:
    naam: str
    sha256: str
    grootte: int

    def regel(self) -> str:
        """De regel voor models.lock: de host maakt het bestand zelf (url "-")."""
        return f"{self.sha256} {self.grootte} {MAP}/{self.naam} -"


# --- de bronnen lezen ----------------------------------------------------------------------------


def _json(pad: Path, grens: int = MAX_JSON) -> dict:
    try:
        with pad.open("rb") as f:
            data = f.read(grens + 1)
    except OSError as e:
        raise OmzetFout(f"{pad.name}: kan niet gelezen worden ({e.strerror})") from None
    if len(data) > grens:
        raise OmzetFout(f"{pad.name}: groter dan {grens} bytes")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise OmzetFout(f"{pad.name}: geen geldige JSON") from None
    if not isinstance(doc, dict):
        raise OmzetFout(f"{pad.name}: geen object")
    return doc


def _geheel(d: dict, sleutel: str, standaard: int, waar: str) -> int:
    v = d.get(sleutel, standaard)
    if type(v) is not int or not 0 < v <= 1_000_000:
        raise OmzetFout(f"config.json: {waar}.{sleutel} = {v!r}; verwacht een geheel getal > 0")
    return v


def _toren(d: dict, naam: str, waar: str) -> Toren:
    act = d.get("hidden_act", "gelu_pytorch_tanh")
    if not isinstance(act, str) or act not in GELU:  # een lijst is unhashable (veilig F3)
        soort = repr(act[:40]) if isinstance(act, str) else type(act).__name__
        raise OmzetFout(
            f"config.json: {waar}.hidden_act = {soort} kan niet (wel {', '.join(sorted(GELU))})"
        )
    eps = d.get("layer_norm_eps", 1e-6)
    if type(eps) not in (int, float) or not 0 < eps < 1:
        raise OmzetFout(f"config.json: {waar}.layer_norm_eps = {eps!r}; verwacht 0 < eps < 1")
    t = Toren(
        naam=naam,
        breedte=_geheel(d, "hidden_size", 768, waar),
        mlp=_geheel(d, "intermediate_size", 3072, waar),
        koppen=_geheel(d, "num_attention_heads", 12, waar),
        lagen=_geheel(d, "num_hidden_layers", 12, waar),
        eps=float(eps),
        gelu=GELU[act],
    )
    if t.breedte % t.koppen:
        raise OmzetFout(f"config.json: {waar}: {t.koppen} koppen delen {t.breedte} niet")
    return t


def lees_config(doc: dict) -> Vorm:
    """De vorm van het model uit config.json. Alleen SiglipModel (model_type "siglip")."""
    soort = doc.get("model_type")
    if soort != "siglip":
        raise OmzetFout(f"config.json: model_type {soort!r} kan niet; deze omzetter kent 'siglip'")
    arch = doc.get("architectures")
    if arch is not None and (not isinstance(arch, list) or "SiglipModel" not in arch):
        raise OmzetFout(f"config.json: architectures {arch!r}; verwacht SiglipModel")
    tc, vc = doc.get("text_config") or {}, doc.get("vision_config") or {}
    if not isinstance(tc, dict) or not isinstance(vc, dict):
        raise OmzetFout("config.json: text_config en vision_config horen objecten")
    tekst = _toren(tc, "text_model", "text_config")
    beeld = _toren(vc, "vision_model", "vision_config")
    if vc.get("vision_use_head", True) is not True:
        raise OmzetFout("config.json: vision_config.vision_use_head moet aan (de MAP-kop)")
    for sleutel in ("image_size", "patch_size"):
        if type(vc.get(sleutel, 0)) is not int:
            raise OmzetFout(f"config.json: vision_config.{sleutel} hoort één geheel getal")
    vorm = Vorm(
        tekst=tekst,
        beeld=beeld,
        woorden=_geheel(tc, "vocab_size", 32000, "text_config"),
        posities=_geheel(tc, "max_position_embeddings", 64, "text_config"),
        projectie=tc.get("projection_size") or tekst.breedte,
        beeldgrootte=_geheel(vc, "image_size", 224, "vision_config"),
        patch=_geheel(vc, "patch_size", 16, "vision_config"),
        kanalen=_geheel(vc, "num_channels", 3, "vision_config"),
    )
    if type(vorm.projectie) is not int or vorm.projectie <= 0:
        raise OmzetFout("config.json: text_config.projection_size hoort een geheel getal > 0")
    if vorm.beeldgrootte % vorm.patch:
        raise OmzetFout("config.json: image_size is geen veelvoud van patch_size")
    if vorm.posities < LENGTE:
        raise OmzetFout(f"config.json: max_position_embeddings {vorm.posities} < {LENGTE}")
    if vorm.projectie != beeld.breedte:
        raise OmzetFout(
            f"config.json: de tekst geeft {vorm.projectie} getallen, het beeld {beeld.breedte}"
        )
    return vorm


def _lagen(uit: dict[str, tuple[int, ...]], t: Toren) -> None:
    e, f = t.breedte, t.mlp
    for i in range(t.lagen):
        p = f"{t.naam}.encoder.layers.{i}"
        for ln in ("layer_norm1", "layer_norm2"):
            uit[f"{p}.{ln}.weight"] = (e,)
            uit[f"{p}.{ln}.bias"] = (e,)
        for proj in ("q_proj", "k_proj", "v_proj", "out_proj"):
            uit[f"{p}.self_attn.{proj}.weight"] = (e, e)
            uit[f"{p}.self_attn.{proj}.bias"] = (e,)
        uit[f"{p}.mlp.fc1.weight"] = (f, e)
        uit[f"{p}.mlp.fc1.bias"] = (f,)
        uit[f"{p}.mlp.fc2.weight"] = (e, f)
        uit[f"{p}.mlp.fc2.bias"] = (e,)


def verwachte_tensors(v: Vorm) -> dict[str, tuple[int, ...]]:
    """Elke tensor die SiglipModel met deze vorm heeft, met zijn vorm."""
    t, b = v.tekst, v.beeld
    uit: dict[str, tuple[int, ...]] = {"logit_scale": (1,), "logit_bias": (1,)}
    uit["text_model.embeddings.token_embedding.weight"] = (v.woorden, t.breedte)
    uit["text_model.embeddings.position_embedding.weight"] = (v.posities, t.breedte)
    _lagen(uit, t)
    uit["text_model.final_layer_norm.weight"] = (t.breedte,)
    uit["text_model.final_layer_norm.bias"] = (t.breedte,)
    uit["text_model.head.weight"] = (v.projectie, t.breedte)
    uit["text_model.head.bias"] = (v.projectie,)
    e = b.breedte
    uit["vision_model.embeddings.patch_embedding.weight"] = (e, v.kanalen, v.patch, v.patch)
    uit["vision_model.embeddings.patch_embedding.bias"] = (e,)
    uit["vision_model.embeddings.position_embedding.weight"] = (v.patches, e)
    _lagen(uit, b)
    for ln in ("vision_model.post_layernorm", "vision_model.head.layernorm"):
        uit[f"{ln}.weight"] = (e,)
        uit[f"{ln}.bias"] = (e,)
    uit["vision_model.head.probe"] = (1, 1, e)
    uit["vision_model.head.attention.in_proj_weight"] = (3 * e, e)
    uit["vision_model.head.attention.in_proj_bias"] = (3 * e,)
    uit["vision_model.head.attention.out_proj.weight"] = (e, e)
    uit["vision_model.head.attention.out_proj.bias"] = (e,)
    uit["vision_model.head.mlp.fc1.weight"] = (b.mlp, e)
    uit["vision_model.head.mlp.fc1.bias"] = (b.mlp,)
    uit["vision_model.head.mlp.fc2.weight"] = (e, b.mlp)
    uit["vision_model.head.mlp.fc2.bias"] = (e,)
    return uit


def _kort(namen: set[str]) -> str:
    lijst = sorted(namen)
    meer = f" en nog {len(lijst) - 5}" if len(lijst) > 5 else ""
    return ", ".join(lijst[:5]) + meer


def controleer_tensors(st: Safetensors, v: Vorm) -> None:
    """Precies de tensors van SiglipModel met deze vorm: niets te weinig, niets te veel. De
    position_ids (niet persistent in transformers) mogen erbij, als ze 0, 1, 2, … zijn."""
    verwacht = verwachte_tensors(v)
    extra = {
        "text_model.embeddings.position_ids": (1, v.posities),
        "vision_model.embeddings.position_ids": (1, v.patches),
    }
    namen = st.namen()
    mist = set(verwacht) - namen
    if mist:
        raise OmzetFout(f"model.safetensors: deze tensors ontbreken: {_kort(mist)}")
    teveel = namen - set(verwacht) - set(extra)
    if teveel:
        raise OmzetFout(f"model.safetensors: onverwachte tensors: {_kort(teveel)}")
    for naam, vorm in verwacht.items():
        info = st.info(naam)
        if info.vorm != vorm:
            raise OmzetFout(
                f"model.safetensors: {naam} heeft vorm {list(info.vorm)}; config.json geeft "
                f"{list(vorm)}"
            )
        if info.dtype not in FLOATS:
            raise OmzetFout(f"model.safetensors: {naam} is {info.dtype}; verwacht F32, F16, BF16")
    for naam, vorm in extra.items():
        if naam not in namen:
            continue
        info = st.info(naam)
        if info.vorm != vorm or info.dtype != "I64":
            raise OmzetFout(f"model.safetensors: {naam} heeft niet de vorm {list(vorm)} als I64")
        if not np.array_equal(st.lees(naam)[0], np.arange(vorm[1])):
            raise OmzetFout(f"model.safetensors: {naam} is niet 0, 1, 2, …")


def lees_voorbewerking(doc: dict, v: Vorm) -> dict:
    """De voorbewerking van SiglipImageProcessor uit preprocessor_config.json, met de standaard
    van transformers waar een sleutel ontbreekt (bicubic, 224, 1/255, gemiddelde en std 0,5)."""
    soort = doc.get("image_processor_type", "SiglipImageProcessor")
    if soort not in VERWERKERS:
        raise OmzetFout(f"{VOORBEWERKING}: image_processor_type {soort!r} kan niet")
    if doc.get("do_resize", True) is not True:
        raise OmzetFout(f"{VOORBEWERKING}: do_resize moet aan (het model wil een vaste grootte)")
    maat = doc.get("size", {"height": 224, "width": 224})
    if not isinstance(maat, dict) or set(maat) != {"height", "width"}:
        raise OmzetFout(f"{VOORBEWERKING}: size hoort {{height, width}}")
    if maat["height"] != v.beeldgrootte or maat["width"] != v.beeldgrootte:
        raise OmzetFout(
            f"{VOORBEWERKING}: size {maat['height']}×{maat['width']} past niet bij image_size "
            f"{v.beeldgrootte}"
        )
    resample = doc.get("resample", 3)
    if type(resample) is not int or resample not in RESAMPLE:
        raise OmzetFout(f"{VOORBEWERKING}: resample {resample!r} kan niet")
    schaal = doc.get("rescale_factor", 1 / 255) if doc.get("do_rescale", True) else 1.0
    if doc.get("do_normalize", True):
        gem, std = doc.get("image_mean", [0.5] * 3), doc.get("image_std", [0.5] * 3)
    else:
        gem, std = [0.0] * v.kanalen, [1.0] * v.kanalen
    for naam, lijst in (("image_mean", gem), ("image_std", std)):
        if (
            not isinstance(lijst, list)
            or len(lijst) != v.kanalen
            or not all(type(x) in (int, float) and math.isfinite(x) for x in lijst)
        ):
            raise OmzetFout(f"{VOORBEWERKING}: {naam} hoort {v.kanalen} getallen")
    if not all(s > 0 for s in std) or type(schaal) not in (int, float) or not schaal > 0:
        raise OmzetFout(f"{VOORBEWERKING}: image_std en rescale_factor horen > 0")
    return {
        "grootte": [v.beeldgrootte, v.beeldgrootte],
        "resample": RESAMPLE[resample],
        "resample_pil": resample,
        "schaal": float(schaal),
        "gemiddelde": [float(x) for x in gem],
        "std": [float(x) for x in std],
        "rgb": True,
        "volgorde": "NCHW",
    }


def onbekende_voorbewerking(doc: dict) -> list[str]:
    bekend = VOORBEWERKING_RUST | {
        "do_resize",
        "size",
        "resample",
        "do_rescale",
        "rescale_factor",
        "do_normalize",
        "image_mean",
        "image_std",
    }
    return sorted(set(doc) - bekend)


def controleer_tokenizer_config(doc: dict, tok: Tokenizer) -> dict:
    """tokenizer_config.json mag niets zeggen dat afwijkt van Keuze 74: rechts afkappen en
    opvullen, geen <bos>, wel <eos>."""
    for sleutel in ("padding_side", "truncation_side"):
        if doc.get(sleutel, "right") != "right":
            raise OmzetFout(f"{TOKENIZER_CONFIG}: {sleutel} {doc[sleutel]!r}; verwacht 'right'")
    if doc.get("add_bos_token") is True:
        raise OmzetFout(f"{TOKENIZER_CONFIG}: add_bos_token; SigLIP 2 heeft geen <bos>")
    if doc.get("add_eos_token") is False:
        raise OmzetFout(f"{TOKENIZER_CONFIG}: add_eos_token uit; Keuze 74 zet <eos> erachter")
    for sleutel, id_ in (("pad_token", tok.pad), ("eos_token", tok.eos)):
        waarde = doc.get(sleutel)
        if isinstance(waarde, dict):
            waarde = waarde.get("content")
        if waarde is not None and waarde != tok.stuk(id_):
            raise OmzetFout(f"{TOKENIZER_CONFIG}: {sleutel} {waarde!r} past niet bij de tokens")
    return {
        "lengte": tok.lengte,
        "pad": tok.pad,
        "eos": tok.eos,
        "kleine_letters": True,
        "stukken": len(tok),
    }


# --- int8 --------------------------------------------------------------------------------------


def _eindig(a: np.ndarray, naam: str) -> None:
    if not np.isfinite(a).all():
        raise OmzetFout(f"model.safetensors: {naam} bevat NaN of oneindig")


def per_kolom(wt: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """W (in × uit) naar int8 met een schaal per kolom: q = rint(w / s), s = max |kolom| / 127.
    Een kolom van nullen krijgt schaal 1 (q is daar 0)."""
    s = (np.abs(wt).max(axis=0).astype(np.float64) / 127.0).astype(np.float32)
    s[s == 0] = 1.0
    q = np.rint(wt.astype(np.float64) / s.astype(np.float64)[None, :])
    return np.clip(q, -127, 127).astype(np.int8), s


def per_rij(w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Een blok rijen van de embedding naar int8 met een schaal per rij (vorm [r, 1])."""
    s = (np.abs(w).max(axis=1).astype(np.float64) / 127.0).astype(np.float32)
    s[s == 0] = 1.0
    q = np.rint(w.astype(np.float64) / s.astype(np.float64)[:, None])
    return np.clip(q, -127, 127).astype(np.int8), s[:, None]


# --- de grafen -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Stuk:
    """Een deel van een Linear: de rijen van..tot van een gewicht [uit, in] en zijn bias."""

    gewicht: str
    bias: str
    van: int | None = None
    tot: int | None = None


def _linear(prefix: str) -> Stuk:
    return Stuk(f"{prefix}.weight", f"{prefix}.bias")


class Bouwer:
    """Bouwt één graaf; elke initializer van een gewicht is een callable (lui gelezen)."""

    def __init__(self, st: Safetensors, naam: str, *, int8: bool) -> None:
        self.st = st
        self.g = Graph(naam)
        self.int8 = int8
        self._schalen: dict[str, np.ndarray] = {}

    # constanten
    def f32(self, waarde: float | list[float], dims: list[int] | None = None) -> str:
        waarden = waarde if isinstance(waarde, list) else [waarde]
        return self.g.initializer(None, FLOAT, dims or [], onnxw.pack(FLOAT, waarden))

    def i64(self, waarden: list[int], dims: list[int] | None = None) -> str:
        return self.g.initializer(
            None, INT64, [len(waarden)] if dims is None else dims, onnxw.pack(INT64, waarden)
        )

    def tensor(self, naam: str, *, vorm: tuple[int, ...] | None = None, rijen=None) -> str:
        """Een gewicht zoals het is (fp32), lui gelezen."""
        info = self.st.info(naam)
        dims = list(vorm or info.vorm)
        if rijen is not None:
            dims[0] = rijen[1] - rijen[0]

        def data(naam=naam, rijen=rijen) -> np.ndarray:
            a = self.st.lees(naam, rijen)
            _eindig(a, naam)
            return np.ascontiguousarray(a.reshape(dims))

        label = naam if rijen is None else f"{naam}[{rijen[0]}:{rijen[1]}]"
        return self.g.initializer(label, FLOAT, dims, data)

    def _wt(self, stukken: list[Stuk]) -> np.ndarray:
        delen = []
        for s in stukken:
            w = self.st.lees(s.gewicht)
            _eindig(w, s.gewicht)
            delen.append(w[s.van : s.tot])
        return np.ascontiguousarray(np.concatenate(delen, axis=0).T)

    def _bias(self, stukken: list[Stuk]) -> np.ndarray:
        delen = []
        for s in stukken:
            b = self.st.lees(s.bias)
            _eindig(b, s.bias)
            delen.append(b[s.van : s.tot])
        return np.ascontiguousarray(np.concatenate(delen))

    def linear(self, x: str, stukken: list[Stuk], label: str) -> str:
        """y = x · Wᵀ + b, met de rijen van de stukken naast elkaar als kolommen."""
        n_in = self.st.info(stukken[0].gewicht).vorm[1]
        n_uit = 0
        for s in stukken:
            rijen = self.st.info(s.gewicht).vorm[0]
            n_uit += (s.tot if s.tot is not None else rijen) - (s.van or 0)
        bias = self.g.initializer(f"{label}.b", FLOAT, [n_uit], lambda: self._bias(stukken))
        if not self.int8:
            w = self.g.initializer(f"{label}.w", FLOAT, [n_in, n_uit], lambda: self._wt(stukken))
            return self.g.op("Add", [self.g.op("MatMul", [x, w]), bias])

        def q8() -> np.ndarray:
            q, s = per_kolom(self._wt(stukken))
            self._schalen[label] = s
            return q

        def schaal() -> np.ndarray:
            s = self._schalen.pop(label, None)
            return s if s is not None else per_kolom(self._wt(stukken))[1]

        q = self.g.initializer(f"{label}.w8", INT8, [n_in, n_uit], q8)
        s = self.g.initializer(f"{label}.s", FLOAT, [n_uit], schaal)
        z = self.g.initializer(f"{label}.z", INT8, [n_uit], bytes(n_uit))
        return self.g.op("DynamicQuantizeMatMul", [x, q, s, z, bias], domain=onnxw.MS_DOMAIN)

    def layernorm(self, x: str, prefix: str, eps: float) -> str:
        w, b = self.tensor(f"{prefix}.weight"), self.tensor(f"{prefix}.bias")
        return self.g.op("LayerNormalization", [x, w, b], axis=-1, epsilon=eps)

    def gelu(self, x: str, soort: str) -> str:
        g = self.g
        half = g.op("Mul", [x, self.f32(0.5)])
        if soort == "erf":
            e = g.op("Erf", [g.op("Div", [x, self.f32(math.sqrt(2.0))])])
            return g.op("Mul", [half, g.op("Add", [e, self.f32(1.0)])])
        x3 = g.op("Mul", [g.op("Mul", [x, x]), x])
        binnen = g.op("Add", [x, g.op("Mul", [x3, self.f32(0.044715)])])
        t = g.op("Tanh", [g.op("Mul", [binnen, self.f32(math.sqrt(2.0 / math.pi))])])
        return g.op("Mul", [half, g.op("Add", [t, self.f32(1.0)])])

    def koppen(self, x: str, h: int, d: int, perm: list[int]) -> str:
        """[n, s, E] naar [n, h, s, d] (perm 0 2 1 3) of [n, h, d, s] (perm 0 2 3 1)."""
        vier = self.g.op("Reshape", [x, self.i64([0, 0, h, d])])
        return self.g.op("Transpose", [vier], perm=perm)

    def aandacht(self, q: str, k: str, v: str, h: int, d: int) -> str:
        g = self.g
        q4 = self.koppen(q, h, d, [0, 2, 1, 3])
        k4 = self.koppen(k, h, d, [0, 2, 3, 1])
        v4 = self.koppen(v, h, d, [0, 2, 1, 3])
        s = g.op("Mul", [g.op("MatMul", [q4, k4]), self.f32(d**-0.5)])
        o = g.op("MatMul", [g.op("Softmax", [s], axis=-1), v4])
        o = g.op("Transpose", [o], perm=[0, 2, 1, 3])
        return g.op("Reshape", [o, self.i64([0, 0, h * d])])

    def mlp(self, x: str, prefix: str, gelu: str) -> str:
        h = self.linear(x, [_linear(f"{prefix}.fc1")], f"{prefix}.fc1")
        return self.linear(self.gelu(h, gelu), [_linear(f"{prefix}.fc2")], f"{prefix}.fc2")

    def laag(self, x: str, t: Toren, i: int) -> str:
        g, e, p = self.g, t.breedte, f"{t.naam}.encoder.layers.{i}"
        h = self.layernorm(x, f"{p}.layer_norm1", t.eps)
        qkv = [_linear(f"{p}.self_attn.{n}") for n in ("q_proj", "k_proj", "v_proj")]
        h = self.linear(h, qkv, f"{p}.self_attn.qkv")
        q, k, v = g.op("Split", [h, self.i64([e, e, e])], outputs=3, axis=-1)
        o = self.aandacht(q, k, v, t.koppen, e // t.koppen)
        o = self.linear(o, [_linear(f"{p}.self_attn.out_proj")], f"{p}.self_attn.out_proj")
        x = g.op("Add", [x, o])
        h = self.layernorm(x, f"{p}.layer_norm2", t.eps)
        return g.op("Add", [x, self.mlp(h, f"{p}.mlp", t.gelu)])

    def normaliseer(self, x: str, uit: str) -> str:
        g = self.g
        lengte = g.op("ReduceL2", [x], axes=[-1], keepdims=1)
        lengte = g.op("Max", [lengte, self.f32(1e-12)])
        return g.op("Div", [x, lengte], outputs=uit)


def beeld_graaf(st: Safetensors, v: Vorm, *, int8: bool) -> Graph:
    b = Bouwer(st, "siglip2_beeld", int8=int8)
    g, t = b.g, v.beeld
    e, s, p = t.breedte, v.beeldgrootte, v.patch
    x = g.input(INVOER_BEELD, FLOAT, ["n", v.kanalen, s, s])
    w = b.tensor("vision_model.embeddings.patch_embedding.weight")
    bias = b.tensor("vision_model.embeddings.patch_embedding.bias")
    h = g.op("Conv", [x, w, bias], kernel_shape=[p, p], strides=[p, p])
    h = g.op("Transpose", [g.op("Reshape", [h, b.i64([0, e, -1])])], perm=[0, 2, 1])
    h = g.op("Add", [h, b.tensor("vision_model.embeddings.position_embedding.weight")])
    for i in range(t.lagen):
        h = b.laag(h, t, i)
    h = b.layernorm(h, "vision_model.post_layernorm", t.eps)
    # De MAP-kop: nn.MultiheadAttention met de probe als vraag
    kop, d = "vision_model.head", e // t.koppen
    inw, inb = f"{kop}.attention.in_proj_weight", f"{kop}.attention.in_proj_bias"
    probe = b.tensor(f"{kop}.probe")
    q = b.linear(probe, [Stuk(inw, inb, 0, e)], f"{kop}.attention.q")
    kv = b.linear(h, [Stuk(inw, inb, e, 3 * e)], f"{kop}.attention.kv")
    k, vv = g.op("Split", [kv, b.i64([e, e])], outputs=2, axis=-1)
    o = b.aandacht(q, k, vv, t.koppen, d)
    o = b.linear(o, [_linear(f"{kop}.attention.out_proj")], f"{kop}.attention.out_proj")
    o = g.op("Add", [o, b.mlp(b.layernorm(o, f"{kop}.layernorm", t.eps), f"{kop}.mlp", t.gelu)])
    o = g.op("Gather", [o, b.i64([0], dims=[])], axis=1)
    b.normaliseer(o, UIT_BEELD)
    g.output(UIT_BEELD, FLOAT, ["n", e])
    return g


def tekst_graaf(st: Safetensors, v: Vorm, *, int8: bool) -> Graph:
    b = Bouwer(st, "siglip2_tekst", int8=int8)
    g, t = b.g, v.tekst
    e = t.breedte
    ids = g.input(INVOER_TEKST, INT64, ["n", LENGTE])
    naam = "text_model.embeddings.token_embedding.weight"
    if int8:
        q8, s = _embedding_int8(st, naam, v.woorden, e)
        tabel = g.initializer(f"{naam}.w8", INT8, [v.woorden, e], q8.rijen)
        schalen = g.initializer(f"{naam}.s", FLOAT, [v.woorden, 1], s)
        h = g.op("Cast", [g.op("Gather", [tabel, ids], axis=0)], to=FLOAT)
        h = g.op("Mul", [h, g.op("Gather", [schalen, ids], axis=0)])
    else:
        h = g.op("Gather", [b.tensor(naam), ids], axis=0)
    pos = b.tensor("text_model.embeddings.position_embedding.weight", rijen=(0, LENGTE))
    h = g.op("Add", [h, pos])
    for i in range(t.lagen):
        h = b.laag(h, t, i)
    h = b.layernorm(h, "text_model.final_layer_norm", t.eps)
    h = g.op("Gather", [h, b.i64([LENGTE - 1], dims=[])], axis=1)
    h = b.linear(h, [_linear("text_model.head")], "text_model.head")
    b.normaliseer(h, UIT_TEKST)
    g.output(UIT_TEKST, FLOAT, ["n", v.projectie])
    return g


class _Rijen:
    """De int8-embedding: de schalen (1 MB) meteen, de int8-tabel pas bij het schrijven."""

    def __init__(self, st: Safetensors, naam: str, n: int) -> None:
        self.st, self.naam, self.n = st, naam, n

    def blokken(self):
        for van in range(0, self.n, BLOK_RIJEN):
            w = self.st.lees(self.naam, (van, min(self.n, van + BLOK_RIJEN)))
            _eindig(w, self.naam)
            yield van, per_rij(w)

    def rijen(self) -> np.ndarray:
        uit = None
        for van, (q, _) in self.blokken():
            if uit is None:
                uit = np.empty((self.n, q.shape[1]), dtype=np.int8)
            uit[van : van + len(q)] = q
        return uit


def _embedding_int8(st: Safetensors, naam: str, n: int, e: int) -> tuple[_Rijen, np.ndarray]:
    r = _Rijen(st, naam, n)
    s = np.empty((n, 1), dtype=np.float32)
    for van, (_, schaal) in r.blokken():
        s[van : van + len(schaal)] = schaal
    return r, s


# --- schrijven -----------------------------------------------------------------------------------


def _sha256(pad: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with pad.open("rb") as f:
        while blok := f.read(1 << 20):
            h.update(blok)
            n += len(blok)
    return h.hexdigest(), n


def _schrijf(pad: Path, data: bytes) -> Uitkomst:
    deel = pad.with_name(pad.name + ".deel")
    with open(deel, "xb") as f:
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            deel.unlink(missing_ok=True)
            raise
    os.replace(deel, pad)
    return Uitkomst(pad.name, hashlib.sha256(data).hexdigest(), len(data))


def _schrijf_model(pad: Path, graaf: Graph) -> Uitkomst:
    geschreven = Model(graaf).write(pad)
    return Uitkomst(pad.name, geschreven.sha256, geschreven.size)


def _uitmap(uit: Path) -> None:
    if uit.is_symlink() or not uit.is_dir():
        raise OmzetFout(f"de uitvoermap {uit} bestaat niet of is geen map")
    if any(uit.iterdir()):
        raise OmzetFout(f"de uitvoermap {uit} is niet leeg")


def _bronnen(bron: Path) -> dict[str, dict]:
    if not bron.is_dir():
        raise OmzetFout(f"de bronmap {bron} bestaat niet of is geen map")
    uit = {}
    for naam in BRONNEN:
        pad = bron / naam
        if not pad.is_file():
            raise OmzetFout(f"{naam} ontbreekt in {bron}")
        sha, n = _sha256(pad)
        uit[naam] = {"sha256": sha, "bytes": n}
    return uit


def omzet(
    bron: str | Path,
    uit: str | Path,
    *,
    fp32: bool = False,
    melden: Callable[[str], None] | None = None,
) -> list[Uitkomst]:
    """Zet de vijf bronbestanden in `bron` om naar `uit` (die leeg moet zijn). Geeft de
    uitvoer met sha256 en grootte, in vaste volgorde. Bij een fout blijft `uit` leeg."""
    bron, uit = Path(bron), Path(uit)
    melden = melden or (lambda _tekst: None)
    _uitmap(uit)
    gemaakt: list[Path] = []
    try:
        return _omzet(bron, uit, fp32=fp32, melden=melden, gemaakt=gemaakt)
    except BaseException:
        for pad in gemaakt:
            pad.unlink(missing_ok=True)
        for pad in uit.glob("*.deel"):
            pad.unlink(missing_ok=True)
        raise


def _omzet(bron, uit, *, fp32, melden, gemaakt) -> list[Uitkomst]:
    bronnen = _bronnen(bron)
    melden("bronnen nagerekend")
    vorm = lees_config(_json(bron / CONFIG))
    st = Safetensors(bron / SAFETENSORS)
    controleer_tensors(st, vorm)
    voor_doc = _json(bron / VOORBEWERKING)
    voorbewerking = lees_voorbewerking(voor_doc, vorm)
    for sleutel in onbekende_voorbewerking(voor_doc):
        melden(f"let op: {VOORBEWERKING}: de sleutel {sleutel!r} kent de omzetter niet")
    try:
        tok = Tokenizer.from_json(bron / TOKENIZER_JSON)
    except TokenizerFout as e:
        raise OmzetFout(str(e)) from None
    if len(tok) > vorm.woorden:
        raise OmzetFout(f"{TOKENIZER_JSON}: {len(tok)} stukken, het model kent er {vorm.woorden}")
    tok_config = _json(bron / TOKENIZER_CONFIG, MAX_TOKENIZER_CONFIG)
    tok_meta = controleer_tokenizer_config(tok_config, tok)
    schaal = st.lees("logit_scale")
    bias = st.lees("logit_bias")
    _eindig(schaal, "logit_scale")
    _eindig(bias, "logit_bias")

    uitkomsten: list[Uitkomst] = []

    def model(naam: str, maak: Callable[[], Graph]) -> None:
        gemaakt.append(uit / naam)
        uitkomsten.append(_schrijf_model(uit / naam, maak()))
        melden(f"{naam} geschreven")

    model(BEELD, lambda: beeld_graaf(st, vorm, int8=True))
    model(TEKST, lambda: tekst_graaf(st, vorm, int8=True))
    if fp32:
        model(BEELD_FP32, lambda: beeld_graaf(st, vorm, int8=False))
        model(TEKST_FP32, lambda: tekst_graaf(st, vorm, int8=False))
    gemaakt.append(uit / TOKENIZER_BIN)
    uitkomsten.append(_schrijf(uit / TOKENIZER_BIN, tok.to_bin()))
    licentie = lock.LICENSES / LICENTIE
    gemaakt.append(uit / LICENTIE)
    uitkomsten.append(_schrijf(uit / LICENTIE, licentie.read_bytes()))
    meta = {
        "formaat": 1,
        "omzetter": VERSIE,
        "model": MAP,
        "dim": vorm.projectie,
        "logit_scale": float(schaal[0]),
        "logit_bias": float(bias[0]),
        "beeld": {
            "bestand": BEELD,
            "invoer": INVOER_BEELD,
            "uitvoer": UIT_BEELD,
            "voorbewerking": voorbewerking,
        },
        "tekst": {
            "bestand": TEKST,
            "invoer": INVOER_TEKST,
            "uitvoer": UIT_TEKST,
            "tokenizer": TOKENIZER_BIN,
            **tok_meta,
        },
        "bronnen": bronnen,
        "uitvoer": {u.naam: {"sha256": u.sha256, "bytes": u.grootte} for u in uitkomsten},
    }
    tekst = json.dumps(meta, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    gemaakt.append(uit / META)
    uitkomsten.append(_schrijf(uit / META, tekst.encode()))
    return uitkomsten


def maxrss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
