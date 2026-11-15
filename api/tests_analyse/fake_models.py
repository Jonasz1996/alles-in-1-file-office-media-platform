"""Nepmodellen voor laag 2 (fase 6, X2 "De tests"; X2a): YuNet en SFace met precies de namen en
vormen van de echte, gemaakt met app/analyse/onnxw.py (zonder numpy, zonder onnx).

**YuNet** (face_detection_yunet_2026may.onnx): invoer `input` [1, 3, height, width]; twaalf
uitvoeren cls_8 … kps_32 met [1, anchors, 1 | 4 | 10], in de volgorde van het echte model.

- Op stride 8 en 16 is alles 0: geen gezicht.
- Op stride 32 (een cel van 32 × 32): cls en obj zijn het gemiddelde van kanaal 0 van de invoer
  (in de keten is dat blauw: YuNet krijgt BGR) gedeeld door 255, via een Conv met een kern van 32.
  De score √(cls · obj) is dus het gemiddelde blauw van de cel / 255.
- Het kader staat in het midden van de cel (b0 = b1 = 0,5), en zijn breedte en hoogte zijn het
  gemiddelde groen van de cel in px (b2 = b3 = log(groen / 32), via Log).
- De vijf punten liggen in het kader zoals het sjabloon van SFace in 112 × 112 (TEMPLATE): het
  uitlijnen legt het kader dan precies op 112 × 112. Met `draai` (graden) en `verschuif` (px)
  liggen ze gedraaid om het midden van de cel en verschoven (review golf 5, G5-C2): dan legt het
  uitlijnen een gedraaid vierkant van groen × groen px rond midden + verschuif op 112 × 112.

Een vierkant van 32 × 32 op het raster met blauw 255 en groen g is dus één gezicht van g × g px
met score 1. `slow` zet er zoveel Conv-nodes van 1 × 1 voor: dan duurt één run lang, en kan de
worker hem met RunOptions.terminate tussen twee nodes stoppen.

**SFace** (face_recognition_sface_2021dec.onnx): invoer `data` [1, 3, 112, 112], uitvoer `fc1`
[1, 128]. GlobalAveragePool, Flatten en Gemm met vaste gewichten (SFACE_W, SFACE_B). Het derde
kanaal (blauw, want SFace krijgt RGB) heeft gewicht 0: de vector hangt alleen af van het
gemiddelde rood en groen van het uitgelijnde gezicht. expected_vector() rekent hem na.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from pathlib import Path

from app.analyse.onnxw import FLOAT, INT64, Graph, Model, pack

YUNET = "face_detection_yunet_2026may.onnx"
SFACE = "face_recognition_sface_2021dec.onnx"
STRIDES = (8, 16, 32)
TEMPLATE = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)
_R = random.Random(60)
SFACE_W = [[_R.uniform(-1, 1) for _ in range(128)] for _ in range(2)] + [[0.0] * 128]
SFACE_B = [_R.uniform(-0.5, 0.5) for _ in range(128)]


def _zeros(n: int) -> bytes:
    return pack(FLOAT, [0.0] * n)


def _conv(g: Graph, x: str, out: int, s: int, weights: list[float], bias: list[float]) -> str:
    w = g.initializer(None, FLOAT, [out, 3, s, s], pack(FLOAT, weights))
    b = g.initializer(None, FLOAT, [out], pack(FLOAT, bias))
    return g.op("Conv", [x, w, b], kernel_shape=[s, s], strides=[s, s])


def _channel(out: int, s: int, channel: int, per_out: list[float]) -> list[float]:
    """Gewichten [out, 3, s, s]: alleen `channel`, per uitvoer per_out[i] / (s · s)."""
    values = []
    for i in range(out):
        for c in range(3):
            values += [per_out[i] / (s * s) if c == channel else 0.0] * (s * s)
    return values


def _flat(g: Graph, t: str, last: int, transpose: bool) -> str:
    if transpose:
        t = g.op("Transpose", [t], perm=[0, 2, 3, 1])
    shape = g.initializer(None, INT64, [3], pack(INT64, [1, -1, last]))
    return g.op("Reshape", [t, shape])


def yunet(slow: int = 0, draai: float = 0.0, verschuif: tuple[float, float] = (0.0, 0.0)) -> bytes:
    g = Graph("yunet_nep")
    x = g.input("input", FLOAT, [1, 3, "height", "width"])
    for _ in range(slow):
        w = g.initializer(None, FLOAT, [3, 3, 1, 1], pack(FLOAT, [1, 0, 0, 0, 1, 0, 0, 0, 1]))
        x = g.op("Conv", [x, w])
    parts: dict[str, str] = {}
    for s in STRIDES:
        if s != 32:
            for name, n in (("cls", 1), ("obj", 1), ("bbox", 4), ("kps", 10)):
                t = _conv(g, x, n, s, [0.0] * (n * 3 * s * s), [0.0] * n)
                parts[f"{name}_{s}"] = _flat(g, t, n, n > 1)
            continue
        blue = _channel(1, s, 0, [1 / 255])
        parts[f"cls_{s}"] = _flat(g, _conv(g, x, 1, s, blue, [0.0]), 1, False)
        parts[f"obj_{s}"] = _flat(g, _conv(g, x, 1, s, blue, [0.0]), 1, False)
        center = _conv(g, x, 2, s, [0.0] * (2 * 3 * s * s), [0.5, 0.5])
        size = g.op("Log", [_conv(g, x, 2, s, _channel(2, s, 1, [1 / s] * 2), [1e-6] * 2)])
        box = g.op("Concat", [center, size], axis=1)
        parts[f"bbox_{s}"] = _flat(g, box, 4, True)
        cos, sin = math.cos(math.radians(draai)), math.sin(math.radians(draai))
        offsets = []
        for tx, ty in TEMPLATE:
            dx, dy = (tx - 56) / 112, (ty - 56) / 112
            offsets += [(cos * dx - sin * dy) / s, (sin * dx + cos * dy) / s]
        bias = [0.5 + verschuif[0] / s, 0.5 + verschuif[1] / s] * 5
        kps = _conv(g, x, 10, s, _channel(10, s, 1, offsets), bias)
        parts[f"kps_{s}"] = _flat(g, kps, 10, True)
    dims = {"cls": 1, "obj": 1, "bbox": 4, "kps": 10}
    for name in ("cls", "obj", "bbox", "kps"):
        for s in STRIDES:
            out = g.op("Identity", [parts[f"{name}_{s}"]], outputs=f"{name}_{s}")
            g.output(out, FLOAT, [1, "anchors", dims[name]])
    return Model(g, producer="platform-nep").to_bytes()


def sface() -> bytes:
    g = Graph("sface_nep")
    x = g.input("data", FLOAT, [1, 3, 112, 112])
    pooled = g.op("GlobalAveragePool", [x])
    flat = g.op("Flatten", [pooled], axis=1)
    w = g.initializer("w", FLOAT, [3, 128], pack(FLOAT, [v for row in SFACE_W for v in row]))
    b = g.initializer("b", FLOAT, [128], pack(FLOAT, SFACE_B))
    g.op("Gemm", [flat, w, b], outputs="fc1")
    g.output("fc1", FLOAT, [1, 128])
    return Model(g, producer="platform-nep").to_bytes()


def expected_vector(red: float, green: float) -> list[float]:
    """De vector die de keten hoort te geven voor een gezicht met dit gemiddelde rood en groen
    (0–255) in RGB-volgorde, L2-genormaliseerd."""
    raw = [SFACE_W[0][k] * red + SFACE_W[1][k] * green + SFACE_B[k] for k in range(128)]
    norm = math.sqrt(sum(v * v for v in raw))
    return [v / norm for v in raw]


@dataclass(frozen=True)
class Installed:
    root: Path  # de map met de modellen (--models)
    lock: Path  # een eigen models.lock met hun sha256 (--lock)


def install(
    root: Path,
    *,
    slow: int = 0,
    skip: tuple[str, ...] = (),
    draai: float = 0.0,
    verschuif: tuple[float, float] = (0.0, 0.0),
) -> Installed:
    """De twee nepmodellen in root, en een models.lock ernaast met hun sha256 en grootte."""
    root.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, data in ((YUNET, yunet(slow, draai, verschuif)), (SFACE, sface())):
        if name not in skip:
            (root / name).write_bytes(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()} {len(data)} {name} -\n")
    lock = root.parent / f"{root.name}.lock"
    lock.write_text("# nepmodellen van tests_analyse/fake_models.py\n" + "".join(lines))
    return Installed(root, lock)
