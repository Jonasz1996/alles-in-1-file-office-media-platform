"""SigLIP 2 omzetten naar int8-ONNX (fase 6, X3a, Keuze 73), in de dienst `analyse-omzetten`.

Eén keer, op de LXC: install.sh haalt de vijf bronbestanden van huggingface.co (vast op één
commit, elk met sha256 in models.lock), en deze code zet ze om met numpy en de eigen
ONNX-schrijver (app/analyse/onnxw.py). Geen torch, transformers, onnx of tokenizers.

- siglip.py: de opdrachtregel (`--bron /bron --uit /uit [--fp32]`), zoals compose hem start;
- safetensors.py: de kop lezen met de standaardbibliotheek, streng nagekeken, en de gewichten per
  tensor via numpy.memmap;
- bouw.py: de betekenis van elke laag uit config.json en de namen, de twee grafen, int8, en de
  uitvoer (beeld.int8.onnx, tekst.int8.onnx, tokenizer.bin, meta.json en de licentie).

Een bron die niet klopt, geeft OmzetFout met een Nederlandse zin (code 2), nooit een traceback.
De omzetter kent geen sleutel, geen socket, geen /models en geen netwerk (K1-05).
"""

from __future__ import annotations

MAP = "siglip2-b32-256"  # de map onder MODELLEN_ROOT (models.lock)

# De vijf bronbestanden van google/siglip2-base-patch32-256 (Keuze 73)
SAFETENSORS = "model.safetensors"
CONFIG = "config.json"
VOORBEWERKING = "preprocessor_config.json"
TOKENIZER_JSON = "tokenizer.json"
TOKENIZER_CONFIG = "tokenizer_config.json"
BRONNEN = (SAFETENSORS, CONFIG, VOORBEWERKING, TOKENIZER_JSON, TOKENIZER_CONFIG)

# De uitvoer (Keuze 73); de fp32-modellen alleen met --fp32 (de kwaliteitspoort, BZ4)
BEELD = "beeld.int8.onnx"
TEKST = "tekst.int8.onnx"
TOKENIZER_BIN = "tokenizer.bin"
META = "meta.json"
LICENTIE = "siglip2-Apache-2.0.txt"
BEELD_FP32 = "beeld.fp32.onnx"
TEKST_FP32 = "tekst.fp32.onnx"
UITVOER = (BEELD, TEKST, TOKENIZER_BIN, META, LICENTIE)

# Namen in de grafen: wat de worker (X3b) erin stopt en eruit haalt
INVOER_BEELD = "pixels"  # float32 [n, 3, S, S], na de voorbewerking van meta.json
INVOER_TEKST = "tokens"  # int64 [n, 64], van tokenizer.bin
UIT_BEELD = "beeld"  # float32 [n, dim], lengte 1
UIT_TEKST = "tekst"  # float32 [n, dim], lengte 1


class OmzetFout(Exception):
    """Een bron of een uitvoermap die niet klopt: een zin voor de beheerder, zonder traceback."""
