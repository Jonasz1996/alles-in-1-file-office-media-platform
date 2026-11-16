"""SigLIP 2 B/32-256 op ware grootte met willekeurige gewichten (fase 6, X3; I2, voor BZ5 en de
100 ms van test_beeld_perf.py met modellen van de echte vorm).

Geen test (pytest verzamelt dit bestand niet). Schrijft de vijf bronbestanden die de omzetter leest
(app/analyse/worker/omzetten), in de vorm van google/siglip2-base-patch32-256:

- **model.safetensors**: elke tensor van SiglipModel met de vorm van B/32-256 (breedte 768, MLP
  3072, 12 koppen, 12 lagen per toren, beeld 256 in patches van 32, 64 tokens, 256.000 woorden),
  F32, op naam gesorteerd en zonder gat; samen ±1,5 GB, zoals het echte bestand (1.507.473.288
  bytes in models.lock). De getallen zijn ruis met vaste seed en de spreiding van siglip_klein.py:
  dezelfde rekensom en hetzelfde geheugen in de omzetter en de worker, niet dezelfde vectoren.
- **tokenizer.json**: BPE in de vorm van Gemma (byte fallback, Lowercase en Replace(" ", "▁"),
  Split op " ", <eos> erachter, merges als [a, b], ingesprongen met 2 spaties) met precies 256.000
  stukken: <pad> <eos> <bos> <unk> <mask>, toegevoegde tokens, 256 bytes, ±15.000 losse tekens
  (latijn, grieks, cyrillisch, CJK, …) en stukken van verzonnen woorden; MERGES merges. Zo is
  tokenizer.bin na de omzetting ±12,4 MB, zoals het echte (12.414.446 bytes in models.lock), en
  tokenizer.json ±35 MB (het echte: 34.363.039).
- **config.json**, **preprocessor_config.json**, **tokenizer_config.json**: zoals het echte model
  (config.json geeft, zoals het echte bestand van 298 bytes, alleen wat afwijkt van de standaard
  van transformers; tokenizer_config.json noemt elk toegevoegd token).

Alleen numpy en de standaardbibliotheek; los van de omzetter geschreven (zoals siglip_klein.py).

    cd api && UV_PROJECT_ENVIRONMENT=.venv-analyse uv run --no-sync \\
        python -m tests_analyse.siglip_vol --uit /tmp/i2/siglip-vol-bron

Op stdout per bestand de grootte; nooit de gewichten of de woorden.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import struct
import sys
import time
from pathlib import Path

import numpy as np

from tests_analyse.siglip_klein import (
    TOKENIZER_CONFIG,
    VOORBEWERKING,
    _spreiding,
)

BREEDTE = 768
MLP = 3072
KOPPEN = 12
LAGEN = 12
BEELD = 256
PATCH = 32
POSITIES = 64
WOORDEN = 256_000  # text_config.vocab_size, en precies zoveel stukken in tokenizer.json
MERGES = 600_000  # zo is tokenizer.bin ±12,4 MB (zie de toelichting)
SEED = 85
BLOK_RIJEN = 16_384  # rijen per keer bij het schrijven (16.384 × 768 × 4 = 50 MB)

SPECIAAL = ["<pad>", "<eos>", "<bos>", "<unk>", "<mask>", "<start_of_turn>", "<end_of_turn>"]
ONGEBRUIKT = 250  # <unused0> … <unused249>: toegevoegde tokens zoals in Gemma
CJK = 14_000  # losse CJK-tekens vanaf U+4E00

# Alle losse tekens: ▁, ASCII, latijn met accenten, grieks, cyrillisch, hebreeuws, arabisch,
# devanagari, thai, hiragana, katakana en CJK
_BEREIKEN = [
    (0x21, 0x7F),
    (0xA1, 0x250),
    (0x370, 0x530),
    (0x5D0, 0x5EB),
    (0x620, 0x64B),
    (0x900, 0x97F),
    (0xE01, 0xE3B),
    (0x3041, 0x3097),
    (0x30A1, 0x30FB),
    (0x4E00, 0x4E00 + CJK),
]
_KLINKERS = "aeiouyaeiouéèäöü"
_MEDEKLINKERS = "bcdfghjklmnpqrstvwxznrstlch"


def config() -> dict:
    """Wat afwijkt van de standaard van transformers (SiglipTextConfig en SiglipVisionConfig:
    768, 3072, 12 koppen, 12 lagen, 64 posities); de omzetter leest dezelfde standaard."""
    return {
        "architectures": ["SiglipModel"],
        "initializer_factor": 1.0,
        "model_type": "siglip",
        "text_config": {"model_type": "siglip_text_model", "vocab_size": WOORDEN},
        "torch_dtype": "float32",
        "transformers_version": "4.49.0",
        "vision_config": {
            "image_size": BEELD,
            "model_type": "siglip_vision_model",
            "patch_size": PATCH,
        },
    }


def vormen() -> dict[str, tuple[int, ...]]:
    """Elke tensor van SiglipModel B/32-256 met zijn vorm, op naam gesorteerd (de volgorde in het
    bestand)."""
    e, f = BREEDTE, MLP
    uit: dict[str, tuple[int, ...]] = {"logit_bias": (1,), "logit_scale": (1,)}
    for toren in ("text_model", "vision_model"):
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
    uit["text_model.embeddings.token_embedding.weight"] = (WOORDEN, e)
    uit["text_model.final_layer_norm.bias"] = (e,)
    uit["text_model.final_layer_norm.weight"] = (e,)
    uit["text_model.head.bias"] = (e,)
    uit["text_model.head.weight"] = (e, e)
    uit["vision_model.embeddings.patch_embedding.bias"] = (e,)
    uit["vision_model.embeddings.patch_embedding.weight"] = (e, 3, PATCH, PATCH)
    uit["vision_model.embeddings.position_embedding.weight"] = ((BEELD // PATCH) ** 2, e)
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
    return dict(sorted(uit.items()))


def schrijf_safetensors(pad: Path, seed: int = SEED) -> int:
    """model.safetensors in blokken (nooit het hele bestand in het geheugen); geeft de grootte."""
    rng = np.random.default_rng(seed)
    v = vormen()
    kop: dict[str, object] = {"__metadata__": {"format": "pt"}}
    pos = 0
    for naam, vorm in v.items():
        n = math.prod(vorm) * 4
        kop[naam] = {"dtype": "F32", "shape": list(vorm), "data_offsets": [pos, pos + n]}
        pos += n
    tekst = json.dumps(kop, separators=(",", ":")).encode()
    tekst += b" " * (-len(tekst) % 8)  # zoals safetensors zelf: de gegevens op 8 bytes
    with pad.open("wb") as f:
        f.write(struct.pack("<Q", len(tekst)) + tekst)
        for naam, vorm in v.items():
            mu, sigma = _spreiding(naam, vorm)
            rijen = vorm[0]
            rest = math.prod(vorm[1:])
            for van in range(0, rijen, BLOK_RIJEN):
                n = min(rijen, van + BLOK_RIJEN) - van
                blok = rng.standard_normal(n * rest, dtype=np.float32)
                blok *= np.float32(sigma)
                blok += np.float32(mu)
                f.write(blok.astype("<f4", copy=False).tobytes())
    return pad.stat().st_size


def _woord(rnd: random.Random, cjk: list[str]) -> str:
    """Een verzonnen woord: lettergrepen in het latijn, of twee à drie CJK-tekens; meestal met ▁
    ervoor (het begin van een woord)."""
    if rnd.random() < 0.12:
        w = "".join(rnd.choice(cjk) for _ in range(rnd.randint(2, 3)))
    else:
        delen = []
        for _ in range(rnd.choices((1, 2, 3, 4), (3, 4, 2, 1))[0]):
            m, k = rnd.choice(_MEDEKLINKERS), rnd.choice(_KLINKERS)
            r = rnd.random()
            if r < 0.4:
                delen.append(m + k)
            elif r < 0.7:
                delen.append(m + k + rnd.choice(_MEDEKLINKERS))
            elif r < 0.85:
                delen.append(k + rnd.choice(_MEDEKLINKERS))
            else:
                delen.append(rnd.choice(_MEDEKLINKERS) + m + k)
        w = "".join(delen)
    return "▁" + w if rnd.random() < 0.75 else w


def woordenlijst_en_merges(seed: int = SEED) -> tuple[dict[str, int], list[list[str]], int]:
    """WOORDEN stukken en MERGES merges; geeft ook hoeveel toegevoegde tokens vooraan staan.

    De stukken van meer tekens zijn alle deelstukken (2 tot 16 tekens) van verzonnen woorden, tot
    de lijst vol is. Een merge (a, b) mag als a, b en a + b in de lijst staan: per stuk eerst één
    willekeurige splitsing (zo is elk stuk te maken), dan andere splitsingen tot MERGES. De rang:
    kortere uitkomsten eerst, binnen een lengte willekeurig."""
    rnd = random.Random(seed)
    vocab: dict[str, int] = {}

    def erbij(s: str) -> None:
        if s not in vocab and len(vocab) < WOORDEN:
            vocab[s] = len(vocab)

    for s in SPECIAAL + [f"<unused{i}>" for i in range(ONGEBRUIKT)]:
        erbij(s)
    toegevoegd = len(vocab)
    for b in range(256):
        erbij(f"<0x{b:02X}>")
    erbij("▁")
    for van, tot in _BEREIKEN:
        for c in range(van, tot):
            erbij(chr(c))
    eerste_woord = len(vocab)
    cjk = [chr(c) for c in range(0x4E00, 0x4E00 + 3000)]  # de woorden gebruiken de eerste 3.000
    while len(vocab) < WOORDEN:
        w = _woord(rnd, cjk)
        for lengte in range(2, min(len(w), 16) + 1):
            for i in range(len(w) - lengte + 1):
                erbij(w[i : i + lengte])

    stukken = list(vocab)[eerste_woord:]
    eerst: list[tuple[str, str]] = []
    andere: list[tuple[str, str]] = []
    for s in stukken:
        kunnen = [(s[:k], s[k:]) for k in range(1, len(s)) if s[:k] in vocab and s[k:] in vocab]
        rnd.shuffle(kunnen)
        if kunnen:
            eerst.append(kunnen[0])
            andere.extend(kunnen[1:])
    if len(eerst) + len(andere) < MERGES:
        raise SystemExit(f"te weinig merges: {len(eerst) + len(andere)} < {MERGES}")
    gekozen = eerst + rnd.sample(andere, MERGES - len(eerst))
    sleutel = {paar: rnd.random() for paar in gekozen}
    gekozen.sort(key=lambda p: (len(p[0]) + len(p[1]), sleutel[p]))
    return vocab, [[a, b] for a, b in gekozen], toegevoegd


def _toegevoegd(i: int, inhoud: str) -> dict:
    return {
        "id": i,
        "content": inhoud,
        "single_word": False,
        "lstrip": False,
        "rstrip": False,
        "normalized": False,
        "special": True,
    }


def tokenizer_json(vocab: dict[str, int], merges: list[list[str]], toegevoegd: int) -> dict:
    lijst = list(vocab)
    return {
        "version": "1.0",
        "truncation": None,
        "padding": None,
        "added_tokens": [_toegevoegd(i, lijst[i]) for i in range(toegevoegd)],
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
        "decoder": {
            "type": "Sequence",
            "decoders": [
                {"type": "Replace", "pattern": {"String": "▁"}, "content": " "},
                {"type": "ByteFallback"},
                {"type": "Fuse"},
                {"type": "Strip", "content": " ", "start": 1, "stop": 0},
            ],
        },
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
            "merges": merges,
        },
    }


def tokenizer_config(vocab: dict[str, int], toegevoegd: int) -> dict:
    """TOKENIZER_CONFIG van siglip_klein.py, met elk toegevoegd token (zoals het echte bestand)."""
    lijst = list(vocab)
    decoder = {}
    for i in range(toegevoegd):
        t = _toegevoegd(i, lijst[i])
        del t["id"]
        decoder[str(i)] = t
    return {"added_tokens_decoder": decoder, **TOKENIZER_CONFIG}


def schrijf_bron(map_: Path, seed: int = SEED) -> dict[str, int]:
    """De vijf bronbestanden in map_ (die mag nog niet bestaan of moet leeg zijn); geeft per
    bestand de grootte."""
    map_.mkdir(parents=True, exist_ok=True)
    if any(map_.iterdir()):
        raise SystemExit(f"{map_} is niet leeg")
    vocab, merges, toegevoegd = woordenlijst_en_merges(seed)
    tok = tokenizer_json(vocab, merges, toegevoegd)
    (map_ / "tokenizer.json").write_text(
        json.dumps(tok, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    del tok, merges
    (map_ / "tokenizer_config.json").write_text(
        json.dumps(tokenizer_config(vocab, toegevoegd), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (map_ / "config.json").write_text(json.dumps(config(), indent=2) + "\n")
    (map_ / "preprocessor_config.json").write_text(json.dumps(VOORBEWERKING, indent=2) + "\n")
    schrijf_safetensors(map_ / "model.safetensors", seed)
    return {p.name: p.stat().st_size for p in sorted(map_.iterdir())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SigLIP 2 B/32-256 op ware grootte, willekeurig")
    ap.add_argument("--uit", required=True, type=Path, help="een nieuwe of lege map")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)
    begin = time.monotonic()
    for naam, grootte in schrijf_bron(args.uit, args.seed).items():
        print(f"{grootte} {naam}")
    print(f"siglip_vol: klaar in {time.monotonic() - begin:.1f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
