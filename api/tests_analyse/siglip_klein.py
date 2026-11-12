"""Een kleine SigLIP-vorm met willekeurige gewichten, alleen met de standaardbibliotheek (fase 6,
X3a, Keuze 83 laag 2).

2 lagen, breedte 64, 4 koppen, MLP 256, beeld 256 × 256 in patches van 32 (64 patches, zoals
B/32-256), 64 tokens, en precies de namen van het echte model.safetensors van
google/siglip2-base-patch32-256. De vijf bronbestanden komen in een map; de gewichten gaan ook
terug als floats per naam, voor de eigen numpy-versie van de test. Geen numpy hier: het bestand
moet onafhankelijk zijn van de omzetter die het leest.
"""

from __future__ import annotations

import json
import math
import random
import struct
from array import array
from pathlib import Path

BREEDTE = 64
MLP = 256
KOPPEN = 4
LAGEN = 2
BEELD = 256
PATCH = 32
POSITIES = 64
EPS = 1e-6
LOGIT_SCALE = math.log(10.0)
LOGIT_BIAS = -10.0

# Een kleine woordenlijst in de vorm van Gemma: <pad> 0, <eos> 1, <bos> 2, <unk> 3, de bytes, en
# een paar stukken voor "hond op het strand"
MERGES = [
    ("o", "n"),
    ("on", "d"),
    ("▁", "o"),
    ("▁o", "p"),
    ("▁", "h"),
    ("e", "t"),
    ("▁h", "et"),
    ("▁", "s"),
    ("t", "r"),
    ("▁s", "tr"),
    ("a", "n"),
    ("an", "d"),
    ("▁str", "and"),
]


def woordenlijst() -> dict[str, int]:
    vocab = {"<pad>": 0, "<eos>": 1, "<bos>": 2, "<unk>": 3, "<mask>": 4}
    for b in range(256):
        vocab[f"<0x{b:02X}>"] = len(vocab)
    for c in "abcdefghijklmnopqrstuvwxyz▁":
        vocab.setdefault(c, len(vocab))
    for a, b in MERGES:
        vocab.setdefault(a + b, len(vocab))
    return vocab


def tokenizer_json() -> dict:
    vocab = woordenlijst()

    def toegevoegd(i: int, inhoud: str) -> dict:
        return {
            "id": i,
            "content": inhoud,
            "single_word": False,
            "lstrip": False,
            "rstrip": False,
            "normalized": False,
            "special": True,
        }

    return {
        "version": "1.0",
        "truncation": None,
        "padding": None,
        "added_tokens": [toegevoegd(i, s) for s, i in vocab.items() if i < 5],
        "normalizer": {
            "type": "Sequence",
            "normalizers": [
                {"type": "Lowercase"},
                {"type": "Replace", "pattern": {"String": " "}, "content": "▁"},
            ],
        },
        "pre_tokenizer": {
            "type": "Split",
            "pattern": {"String": " "},
            "behavior": "MergedWithPrevious",
            "invert": False,
        },
        "post_processor": {
            "type": "TemplateProcessing",
            "single": [
                {"Sequence": {"id": "A", "type_id": 0}},
                {"SpecialToken": {"id": "<eos>", "type_id": 0}},
            ],
            "pair": [],
            "special_tokens": {},
        },
        "decoder": None,
        "model": {
            "type": "BPE",
            "dropout": None,
            "unk_token": "<unk>",
            "continuing_subword_prefix": None,
            "end_of_word_suffix": None,
            "fuse_unk": True,
            "byte_fallback": True,
            "ignore_merges": False,
            "vocab": vocab,
            "merges": [[a, b] for a, b in MERGES],
        },
    }


def config(woorden: int) -> dict:
    gedeeld = {
        "hidden_size": BREEDTE,
        "intermediate_size": MLP,
        "num_attention_heads": KOPPEN,
        "num_hidden_layers": LAGEN,
        "hidden_act": "gelu_pytorch_tanh",
        "layer_norm_eps": EPS,
    }
    return {
        "architectures": ["SiglipModel"],
        "model_type": "siglip",
        "initializer_factor": 1.0,
        "text_config": {
            **gedeeld,
            "model_type": "siglip_text_model",
            "vocab_size": woorden,
            "max_position_embeddings": POSITIES,
            "projection_size": BREEDTE,
        },
        "vision_config": {
            **gedeeld,
            "model_type": "siglip_vision_model",
            "image_size": BEELD,
            "patch_size": PATCH,
            "num_channels": 3,
        },
        "torch_dtype": "float32",
        "transformers_version": "4.49.0",
    }


VOORBEWERKING = {
    "do_convert_rgb": None,
    "do_normalize": True,
    "do_rescale": True,
    "do_resize": True,
    "image_mean": [0.5, 0.5, 0.5],
    "image_processor_type": "SiglipImageProcessor",
    "image_std": [0.5, 0.5, 0.5],
    "processor_class": "SiglipProcessor",
    "resample": 2,
    "rescale_factor": 0.00392156862745098,
    "size": {"height": BEELD, "width": BEELD},
}

TOKENIZER_CONFIG = {
    "add_bos_token": False,
    "add_eos_token": True,
    "bos_token": "<bos>",
    "eos_token": "<eos>",
    "pad_token": "<pad>",
    "unk_token": "<unk>",
    "padding_side": "right",
    "model_max_length": 1000000000000000019884624838656,
    "tokenizer_class": "GemmaTokenizer",
}


def vormen(woorden: int) -> dict[str, tuple[int, ...]]:
    """Elke tensor met zijn vorm, in de volgorde waarin het bestand ze schrijft."""
    e, f = BREEDTE, MLP
    uit: dict[str, tuple[int, ...]] = {"logit_bias": (1,), "logit_scale": (1,)}

    def lagen(toren: str) -> None:
        for i in range(LAGEN):
            p = f"{toren}.encoder.layers.{i}"
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

    uit["text_model.embeddings.position_embedding.weight"] = (POSITIES, e)
    uit["text_model.embeddings.token_embedding.weight"] = (woorden, e)
    lagen("text_model")
    uit["text_model.final_layer_norm.bias"] = (e,)
    uit["text_model.final_layer_norm.weight"] = (e,)
    uit["text_model.head.bias"] = (e,)
    uit["text_model.head.weight"] = (e, e)
    n = (BEELD // PATCH) ** 2
    uit["vision_model.embeddings.patch_embedding.bias"] = (e,)
    uit["vision_model.embeddings.patch_embedding.weight"] = (e, 3, PATCH, PATCH)
    uit["vision_model.embeddings.position_embedding.weight"] = (n, e)
    lagen("vision_model")
    uit["vision_model.head.attention.in_proj_bias"] = (3 * e,)
    uit["vision_model.head.attention.in_proj_weight"] = (3 * e, e)
    uit["vision_model.head.attention.out_proj.bias"] = (e,)
    uit["vision_model.head.attention.out_proj.weight"] = (e, e)
    uit["vision_model.head.layernorm.bias"] = (e,)
    uit["vision_model.head.layernorm.weight"] = (e,)
    uit["vision_model.head.mlp.fc1.bias"] = (f,)
    uit["vision_model.head.mlp.fc1.weight"] = (f, e)
    uit["vision_model.head.mlp.fc2.bias"] = (e,)
    uit["vision_model.head.mlp.fc2.weight"] = (e, f)
    uit["vision_model.head.probe"] = (1, 1, e)
    uit["vision_model.post_layernorm.bias"] = (e,)
    uit["vision_model.post_layernorm.weight"] = (e,)
    return uit


def _spreiding(naam: str, vorm: tuple[int, ...]) -> tuple[float, float]:
    """(gemiddelde, std) per soort tensor: zo blijft de rekensom in een gewoon bereik."""
    if naam == "logit_scale":
        return LOGIT_SCALE, 0.0
    if naam == "logit_bias":
        return LOGIT_BIAS, 0.0
    if "norm" in naam:
        return (1.0, 0.1) if naam.endswith("weight") else (0.0, 0.05)
    if naam.endswith("bias"):
        return 0.0, 0.02
    if "token_embedding" in naam:
        return 0.0, 0.5
    if "position_embedding" in naam:
        return 0.0, 0.2
    if naam.endswith("probe"):
        return 0.0, 1.0
    return 0.0, 1.0 / math.sqrt(math.prod(vorm[1:]))  # fan_in van een Linear of de Conv


def gewichten(woorden: int, seed: int = 73) -> dict[str, tuple[tuple[int, ...], array]]:
    """Per naam (vorm, float32-waarden) met een vaste seed."""
    rnd = random.Random(seed)
    uit = {}
    for naam, vorm in vormen(woorden).items():
        mu, sigma = _spreiding(naam, vorm)
        uit[naam] = (vorm, array("f", (rnd.gauss(mu, sigma) for _ in range(math.prod(vorm)))))
    return uit


def _f16(waarden: array) -> bytes:
    return struct.pack(f"<{len(waarden)}e", *waarden)


def _bf16(waarden: array) -> bytes:
    """Afkappen naar bfloat16 (de hoogste 16 bits van float32, afgerond naar even)."""
    ruw = struct.unpack(f"<{len(waarden)}I", struct.pack(f"<{len(waarden)}f", *waarden))
    uit = [((r + 0x7FFF + ((r >> 16) & 1)) >> 16) & 0xFFFF for r in ruw]
    return struct.pack(f"<{len(uit)}H", *uit)


def terug(dtype: str, waarden: array) -> array:
    """De waarden zoals ze in het bestand staan, terug als float32 (voor de numpy-versie)."""
    if dtype == "F16":
        return array("f", struct.unpack(f"<{len(waarden)}e", _f16(waarden)))
    if dtype == "BF16":
        b = _bf16(waarden)
        h = struct.unpack(f"<{len(waarden)}H", b)
        ruw = struct.pack(f"<{len(h)}I", *(x << 16 for x in h))
        return array("f", struct.unpack(f"<{len(h)}f", ruw))
    return waarden


def safetensors(
    tensors: dict[str, tuple[str, tuple[int, ...], bytes]], metadata: dict | None = None
) -> bytes:
    """Een safetensors-bestand: per naam (dtype, vorm, bytes), in deze volgorde achter elkaar."""
    kop: dict[str, object] = {}
    if metadata is not None:
        kop["__metadata__"] = metadata
    pos = 0
    for naam, (dtype, vorm, data) in tensors.items():
        kop[naam] = {"dtype": dtype, "shape": list(vorm), "data_offsets": [pos, pos + len(data)]}
        pos += len(data)
    tekst = json.dumps(kop, separators=(",", ":")).encode()
    tekst += b" " * (-len(tekst) % 8)  # zoals safetensors zelf: de gegevens op 8 bytes
    return struct.pack("<Q", len(tekst)) + tekst + b"".join(d for _, _, d in tensors.values())


def tensors_van(
    w: dict[str, tuple[tuple[int, ...], array]], dtype: str = "F32"
) -> dict[str, tuple[str, tuple[int, ...], bytes]]:
    uit = {}
    for naam, (vorm, waarden) in w.items():
        if dtype == "F16":
            data = _f16(waarden)
        elif dtype == "BF16":
            data = _bf16(waarden)
        else:
            data = struct.pack(f"<{len(waarden)}f", *waarden)
        uit[naam] = (dtype, vorm, data)
    return uit


def schrijf_bron(
    map_: Path,
    *,
    seed: int = 73,
    dtype: str = "F32",
    config_anders: dict | None = None,
) -> dict[str, tuple[tuple[int, ...], array]]:
    """De vijf bronbestanden in map_; geeft de gewichten zoals ze in het bestand staan."""
    map_.mkdir(parents=True, exist_ok=True)
    tok = tokenizer_json()
    woorden = len(tok["model"]["vocab"])
    w = gewichten(woorden, seed)
    (map_ / "model.safetensors").write_bytes(safetensors(tensors_van(w, dtype), {"format": "pt"}))
    cfg = config(woorden)
    if config_anders:
        cfg.update(config_anders)
    (map_ / "config.json").write_text(json.dumps(cfg, indent=2))
    (map_ / "preprocessor_config.json").write_text(json.dumps(VOORBEWERKING, indent=2))
    (map_ / "tokenizer.json").write_text(json.dumps(tok, ensure_ascii=False))
    (map_ / "tokenizer_config.json").write_text(json.dumps(TOKENIZER_CONFIG, indent=2))
    return {naam: (vorm, terug(dtype, waarden)) for naam, (vorm, waarden) in w.items()}
