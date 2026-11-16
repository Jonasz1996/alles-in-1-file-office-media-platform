"""Wat de tests met het echte beeldmodel delen (marker beeld_echt; job beeld-model, fase 6, X3a).

De job zet de omgeving. Ontbreekt er iets, dan faalt de test met wat er mist; overslaan telt
nooit als goed:

- BEELD_BRON: de vijf bronbestanden van SigLIP 2 (tokenizer.json voor BZ3);
- BEELD_MODEL: de uitvoer van de omzetter met --fp32 (beeld.int8.onnx, tekst.int8.onnx,
  tokenizer.bin, meta.json, en beeld.fp32.onnx en tekst.fp32.onnx voor BZ4);
- BEELD_SET: de foto's van de set als <sleutel>.jpg (de stap `set` van tools/beeld/beeld_model.py);
- BEELD_SET_TSV: beeld.tsv, of het voorstel van de job zolang beeld.tsv ontbreekt;
- BEELD_RAPPORT: de werkmap van de job. Elke test zet er een verslag in (<naam>.json) en regels
  voor het blok `=== kort ===` (kort.txt).

De voorbewerking volgt meta.json, zoals de container van X3b (Keuze 77): RGB, vierkant schalen
zonder bijsnijden met de resample van Pillow, × schaal, (x − gemiddelde) / std, NCHW. Een sessie
van onnxruntime draait zoals in de container: alleen de CPU, één draad.

In een verslag staan alleen getallen en de vaste vragen uit beeld_vragen.tsv; nooit een vector.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from app.analyse.tokenizer import Tokenizer
from app.analyse.worker import omzetten

API = Path(__file__).resolve().parents[1]
ROOT = API.parent
TOOL = ROOT / "tools" / "beeld" / "beeld_model.py"
SETS = API / "tests" / "analyse" / "sets"
BATCH = 16  # zoals de container: batches van ≤ 16 (Keuze 77)


def omgeving(naam: str) -> Path:
    """Het pad uit de omgeving; zonder faalt de test (nooit overslaan)."""
    waarde = os.environ.get(naam)
    if not waarde:
        pytest.fail(f"{naam} ontbreekt: deze test hoort bij de job beeld-model (marker beeld_echt)")
    pad = Path(waarde)
    if not pad.exists():
        pytest.fail(f"{naam}: {pad} bestaat niet")
    return pad


def tool():
    """tools/beeld/beeld_model.py (alleen de standaardbibliotheek): lees_set en de categorieën."""
    naam = "beeld_model_echt"
    if naam not in sys.modules:
        spec = importlib.util.spec_from_file_location(naam, TOOL)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[naam] = mod
        spec.loader.exec_module(mod)
    return sys.modules[naam]


def sha256_van(pad: Path) -> str:
    h = hashlib.sha256()
    with pad.open("rb") as f:
        for blok in iter(lambda: f.read(1 << 20), b""):
            h.update(blok)
    return h.hexdigest()


def sessie(pad: Path):
    import onnxruntime

    opties = onnxruntime.SessionOptions()
    opties.intra_op_num_threads = 1
    opties.inter_op_num_threads = 1
    return onnxruntime.InferenceSession(str(pad), opties, providers=["CPUExecutionProvider"])


def cpu(pad: Path = Path("/proc/cpuinfo")) -> dict:
    """De naam van de CPU, of hij VNNI heeft (avx512_vnni of avx_vnni) en AMX (amx_int8).

    Zonder VNNI rekent onnxruntime int8 anders (CI op 669f03c), en de tijd van BZ6 hangt aan de
    runner: de regels van BZ4 en BZ6 zeggen daarom op welke CPU ze liepen.
    """
    naam, vlaggen = "onbekend", set()
    try:
        regels = pad.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        regels = []
    for regel in regels:
        sleutel, _, waarde = regel.partition(":")
        if sleutel.strip() == "model name" and naam == "onbekend":
            naam = waarde.strip()
        elif sleutel.strip() == "flags" and not vlaggen:
            vlaggen = set(waarde.split())
    return {
        "model": naam,
        "vnni": bool(vlaggen & {"avx512_vnni", "avx_vnni"}),
        "amx": "amx_int8" in vlaggen,
    }


def voorbewerk(beeld, vb: dict) -> np.ndarray:
    """Een pad of een Image van Pillow naar float32 [3, S, S] volgens meta.json."""
    from PIL import Image

    if isinstance(beeld, (str, Path)):
        with Image.open(beeld) as img:
            return voorbewerk(img.copy(), vb)
    s_h, s_w = vb["grootte"]
    img = beeld.convert("RGB").resize((s_w, s_h), resample=vb["resample_pil"])
    a = np.asarray(img, dtype=np.float32) * np.float32(vb["schaal"])
    a = (a - np.asarray(vb["gemiddelde"], np.float32)) / np.asarray(vb["std"], np.float32)
    return np.ascontiguousarray(a.transpose(2, 0, 1), dtype=np.float32)


class Model:
    """Het omgezette model: beeld en tekst, int8 of fp32, met tokenizer.bin."""

    def __init__(self, map_: Path, *, fp32: bool = False) -> None:
        self.meta = json.loads((map_ / omzetten.META).read_text(encoding="utf-8"))
        beeld, tekst = (
            (omzetten.BEELD_FP32, omzetten.TEKST_FP32) if fp32 else (omzetten.BEELD, omzetten.TEKST)
        )
        for naam in (beeld, tekst, omzetten.TOKENIZER_BIN):
            if not (map_ / naam).is_file():
                pytest.fail(f"{naam} ontbreekt in {map_} (de omzetter met --fp32?)")
        self.beeld = sessie(map_ / beeld)
        self.tekst = sessie(map_ / tekst)
        self.tok = Tokenizer.load(map_ / omzetten.TOKENIZER_BIN)
        self.vb = self.meta["beeld"]["voorbewerking"]
        self.scale = float(np.exp(self.meta["logit_scale"]))
        self.bias = float(self.meta["logit_bias"])

    def beelden(self, pixels: list[np.ndarray]) -> np.ndarray:
        uit = []
        for i in range(0, len(pixels), BATCH):
            blok = np.stack(pixels[i : i + BATCH])
            (v,) = self.beeld.run([omzetten.UIT_BEELD], {omzetten.INVOER_BEELD: blok})
            uit.append(v)
        return np.concatenate(uit).astype(np.float32)

    def teksten(self, vragen: list[str]) -> np.ndarray:
        uit = []
        for i in range(0, len(vragen), BATCH):
            ids = np.asarray([self.tok.encode(v) for v in vragen[i : i + BATCH]], np.int64)
            (v,) = self.tekst.run([omzetten.UIT_TEKST], {omzetten.INVOER_TEKST: ids})
            uit.append(v)
        return np.concatenate(uit).astype(np.float32)

    def kans(self, cos: np.ndarray) -> np.ndarray:
        """SigLIP: sigmoid(scale · cos + bias) per paar."""
        return 1.0 / (1.0 + np.exp(-(self.scale * cos + self.bias)))


def vragen() -> list[dict]:
    """beeld_vragen.tsv: soort, vraag, categorieen, doel."""
    regels = (SETS / "beeld_vragen.tsv").read_text(encoding="utf-8").splitlines()
    rijen = [r.split("\t") for r in regels if r and not r.startswith("#")]
    kop, *rijen = rijen
    return [dict(zip(kop, r, strict=True)) for r in rijen]


def kwantielen(waarden) -> dict[str, float]:
    a = np.asarray(waarden, np.float64)
    if a.size == 0:
        return {}
    punten = (0, 5, 25, 50, 75, 95, 100)
    return {f"p{p}": round(float(np.percentile(a, p)), 4) for p in punten}


class Verslag:
    """<naam>.json en regels voor `=== kort ===` in BEELD_RAPPORT."""

    def __init__(self, naam: str) -> None:
        self.map = omgeving("BEELD_RAPPORT")
        self.pad = self.map / f"{naam}.json"
        self.inhoud: dict = {}

    def zet(self, sleutel: str, waarde) -> None:
        # eerst json.dumps: een waarde die geen JSON is, laat het verslag zoals het was (review N4)
        inhoud = {**self.inhoud, sleutel: waarde}
        tekst = json.dumps(inhoud, indent=2, ensure_ascii=False) + "\n"
        self.inhoud = inhoud
        self.pad.write_text(tekst)

    def kort(self, regel: str) -> None:
        print(regel, flush=True)
        with (self.map / "kort.txt").open("a", encoding="utf-8") as f:
            f.write(regel + "\n")
