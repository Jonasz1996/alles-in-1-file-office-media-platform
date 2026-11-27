"""Nepmodellen van zoeken op beeld voor laag 2 (fase 6, X3b; Keuze 83, laag 1 "de keten").

De vijf uitkomsten van de omzetter (app/analyse/worker/omzetten) onder dezelfde namen, met
dezelfde invoer en uitvoer als de echte, gemaakt met app/analyse/onnxw.py:

- **beeld.int8.onnx**: `pixels` float32 [n, 3, 256, 256] → `beeld` float32 [n, 768], lengte 1.
  GlobalAveragePool, Flatten en Gemm: de vector is Σ_c (gemiddelde van kanaal c in 0–1) · KLEUR[c]
  plus 0,05 · RUIS_BEELD. Een rood vlak geeft dus (bijna) KLEUR["rood"]; expected_image() rekent
  het na. Omdat de voorbewerking (x / 255 − 0,5) / 0,5 doet, is (x + 1) / 2 weer het gemiddelde.
- **tekst.int8.onnx**: `tokens` int64 [n, 64] → `tekst` float32 [n, 768], lengte 1. Gather uit
  een tabel per stuk en ReduceSum over de 64 plekken, plus 0,01 · RUIS_TEKST. De stukken "rood",
  "groen" en "blauw" hebben de rij KLEUR van hun kleur; elk ander stuk een kleine vaste rij;
  <pad> en <eos> nul. Zo vindt "rood" het rode vlak bovenaan (de zelftest van Keuze 83).
- **tokenizer.bin** uit de kleine woordenlijst van siglip_klein.py, met drie stukken erbij;
- **meta.json** zoals bouw.py hem schrijft (de voorbewerking van siglip_klein.VOORBEWERKING);
- de licentie.

`slow` zet er in elk model een keten van MatMuls achter (een rij van 512 × 768 maal de
eenheidsmatrix): de uitkomst blijft gelijk, maar één run duurt lang, en de worker kan hem met
RunOptions.terminate tussen twee nodes stoppen.

install() zet ze onder `<root>/siglip2-b32-256/` naast de nepmodellen van gezichten
(fake_models.install) en zet hun regels in dezelfde lock. vecfile() schrijft een bestand per
eigenaar in de vorm van app/beeld/types.py.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.analyse import lock as lockfile
from app.analyse.onnxw import FLOAT, INT64, Graph, Model, pack
from app.analyse.tokenizer import Tokenizer
from app.analyse.worker import omzetten
from app.beeld import types as vt
from tests_analyse import fake_models, siglip_klein

DIM = 768
LENGTE = 64
MAP = omzetten.MAP
NAMEN = {
    "beeld": f"{MAP}/{omzetten.BEELD}",
    "tekst": f"{MAP}/{omzetten.TEKST}",
    "tokenizer": f"{MAP}/{omzetten.TOKENIZER_BIN}",
    "meta": f"{MAP}/{omzetten.META}",
    "licentie": f"{MAP}/{omzetten.LICENTIE}",
}
KLEUREN = ("rood", "groen", "blauw")  # in de volgorde van de kanalen (RGB)
# Drie stukken voor de kleuren, na de merges van siglip_klein (de volgorde telt: "groen" eerst,
# anders maakt ("r", "o") van "groen" g + ro + e + n)
MERGES = [
    ("g", "r"),
    ("gr", "o"),
    ("gro", "e"),
    ("groe", "n"),
    ("r", "o"),
    ("ro", "o"),
    ("roo", "d"),
    ("b", "l"),
    ("bl", "a"),
    ("bla", "u"),
    ("blau", "w"),
]
RUIS_BEELD = 0.05
RUIS_TEKST = 0.01
STUK = 0.05  # de lengte van de rij van een ander stuk
SLOW_ROWS = 512
LANG_RANK = 2000  # slow voor een rank die langer loopt dan een scenario (echt.EchteDriver)


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _orthonormal(count: int, seed: int) -> list[list[float]]:
    """count vectoren van lengte 1, onderling loodrecht (Gram-Schmidt), met een vaste seed."""
    r = random.Random(seed)
    out: list[list[float]] = []
    while len(out) < count:
        v = [r.gauss(0, 1) for _ in range(DIM)]
        for u in out:
            d = sum(a * b for a, b in zip(v, u, strict=True))
            v = [a - d * b for a, b in zip(v, u, strict=True)]
        out.append(_unit(v))
    return out


_BASIS = _orthonormal(5, 75)
KLEUR = dict(zip(KLEUREN, _BASIS[:3], strict=True))
RUIS = {"beeld": _BASIS[3], "tekst": _BASIS[4]}


def tokenizer_json() -> dict:
    doc = siglip_klein.tokenizer_json()
    vocab = doc["model"]["vocab"]
    for a, b in MERGES:
        vocab.setdefault(a + b, len(vocab))
    doc["model"]["merges"] += [[a, b] for a, b in MERGES]
    return doc


def tokenizer() -> Tokenizer:
    tok = Tokenizer.from_json(json.dumps(tokenizer_json()).encode(), lengte=LENGTE)
    for kleur in KLEUREN:  # elke kleur is één stuk
        ids = tok.encode(kleur)
        assert ids[1] == tok.eos and tok.stuk(ids[0]) == kleur, kleur
    return tok


def tekst_tabel(tok: Tokenizer) -> list[list[float]]:
    """De rij per stuk: KLEUR voor de drie kleuren, nul voor <pad> en <eos>, anders klein."""
    r = random.Random(83)
    kleur_id = {tok.encode(k)[0]: k for k in KLEUREN}
    rows = []
    for i in range(len(tok)):
        if i in kleur_id:
            rows.append(KLEUR[kleur_id[i]])
        elif i in (tok.pad, tok.eos):
            rows.append([0.0] * DIM)
        else:
            rows.append([STUK * x for x in _unit([r.gauss(0, 1) for _ in range(DIM)])])
    return rows


def expected_image(rgb: Sequence[float]) -> list[float]:
    """De vector van een beeld met dit gemiddelde per kanaal (0–255, RGB), lengte 1."""
    v = [RUIS_BEELD * x for x in RUIS["beeld"]]
    for c, kleur in enumerate(KLEUREN):
        v = [a + rgb[c] / 255 * b for a, b in zip(v, KLEUR[kleur], strict=True)]
    return _unit(v)


def expected_text(text: str, tok: Tokenizer | None = None) -> list[float]:
    tok = tok or tokenizer()
    rows = tekst_tabel(tok)
    v = [RUIS_TEKST * x for x in RUIS["tekst"]]
    for i in tok.encode(text):
        v = [a + b for a, b in zip(v, rows[i], strict=True)]
    return _unit(v)


def _slow(g: Graph, x: str, slow: int) -> str:
    """x [n, DIM] door slow MatMuls met de eenheidsmatrix op een rij van SLOW_ROWS × DIM."""
    if not slow:
        return x
    axes = g.initializer(None, INT64, [1], pack(INT64, [1]))
    shape = g.initializer(None, INT64, [3], pack(INT64, [1, SLOW_ROWS, 1]))
    eye = g.initializer(
        None,
        FLOAT,
        [DIM, DIM],
        pack(FLOAT, [1.0 if i == j else 0.0 for i in range(DIM) for j in range(DIM)]),
    )
    t = g.op("Expand", [g.op("Unsqueeze", [x, axes]), shape])
    for _ in range(slow):
        t = g.op("MatMul", [t, eye])
    return g.op("ReduceMean", [t], axes=[1], keepdims=0)


def _unit_out(g: Graph, x: str, name: str) -> None:
    length = g.op("ReduceL2", [x], axes=[-1], keepdims=1)
    tiny = g.initializer(None, FLOAT, [], pack(FLOAT, [1e-12]))
    g.op("Div", [x, g.op("Max", [length, tiny])], outputs=name)
    g.output(name, FLOAT, ["n", DIM])


def beeld_model(slow: int = 0) -> bytes:
    g = Graph("beeld_nep")
    x = g.input(omzetten.INVOER_BEELD, FLOAT, ["n", 3, 256, 256])
    flat = g.op("Flatten", [g.op("GlobalAveragePool", [x])], axis=1)
    w = [0.5 * KLEUR[k][j] for k in KLEUREN for j in range(DIM)]
    b = [
        0.5 * sum(KLEUR[k][j] for k in KLEUREN) + RUIS_BEELD * RUIS["beeld"][j] for j in range(DIM)
    ]
    wi = g.initializer("w", FLOAT, [3, DIM], pack(FLOAT, w))
    bi = g.initializer("b", FLOAT, [DIM], pack(FLOAT, b))
    _unit_out(g, _slow(g, g.op("Gemm", [flat, wi, bi]), slow), omzetten.UIT_BEELD)
    return Model(g, producer="platform-nep").to_bytes()


def tekst_model(tok: Tokenizer, slow: int = 0) -> bytes:
    g = Graph("tekst_nep")
    x = g.input(omzetten.INVOER_TEKST, INT64, ["n", LENGTE])
    rows = tekst_tabel(tok)
    table = g.initializer(
        "tabel", FLOAT, [len(rows), DIM], pack(FLOAT, [v for r in rows for v in r])
    )
    axes = g.initializer(None, INT64, [1], pack(INT64, [1]))
    summed = g.op("ReduceSum", [g.op("Gather", [table, x], axis=0), axes], keepdims=0)
    ruis = g.initializer("ruis", FLOAT, [DIM], pack(FLOAT, [RUIS_TEKST * v for v in RUIS["tekst"]]))
    _unit_out(g, _slow(g, g.op("Add", [summed, ruis]), slow), omzetten.UIT_TEKST)
    return Model(g, producer="platform-nep").to_bytes()


def meta_json(tok: Tokenizer) -> bytes:
    """meta.json in de vorm van bouw.py (formaat 1)."""
    vb = siglip_klein.VOORBEWERKING
    doc = {
        "formaat": 1,
        "omzetter": "nep",
        "model": MAP,
        "dim": DIM,
        "logit_scale": siglip_klein.LOGIT_SCALE,
        "logit_bias": siglip_klein.LOGIT_BIAS,
        "beeld": {
            "bestand": omzetten.BEELD,
            "invoer": omzetten.INVOER_BEELD,
            "uitvoer": omzetten.UIT_BEELD,
            "voorbewerking": {
                "grootte": [vb["size"]["height"], vb["size"]["width"]],
                "resample": "bilinear",
                "resample_pil": vb["resample"],
                "schaal": vb["rescale_factor"],
                "gemiddelde": vb["image_mean"],
                "std": vb["image_std"],
                "rgb": True,
                "volgorde": "NCHW",
            },
        },
        "tekst": {
            "bestand": omzetten.TEKST,
            "invoer": omzetten.INVOER_TEKST,
            "uitvoer": omzetten.UIT_TEKST,
            "tokenizer": omzetten.TOKENIZER_BIN,
            "lengte": tok.lengte,
            "pad": tok.pad,
            "eos": tok.eos,
            "kleine_letters": True,
            "stukken": len(tok),
        },
        "bronnen": {},
        "uitvoer": {},
    }
    return (json.dumps(doc, sort_keys=True, indent=2) + "\n").encode()


@dataclass(frozen=True)
class Installed:
    root: Path
    lock: Path
    files: dict[str, bytes]  # naam onder root → de bytes


def files(*, slow: int = 0) -> dict[str, bytes]:
    tok = tokenizer()
    return {
        NAMEN["beeld"]: beeld_model(slow),
        NAMEN["tekst"]: tekst_model(tok, slow),
        NAMEN["tokenizer"]: tok.to_bin(),
        NAMEN["meta"]: meta_json(tok),
        NAMEN["licentie"]: (lockfile.LICENSES / omzetten.LICENTIE).read_bytes(),
    }


def install(
    models: fake_models.Installed, *, slow: int = 0, skip: tuple[str, ...] = ()
) -> Installed:
    """De vijf bestanden onder models.root/siglip2-b32-256/, en hun regels achter die van de
    gezichten in models.lock (url "-", zoals de pins van de omzetting). skip: namen die in de lock
    staan maar niet op schijf."""
    made = files(slow=slow)
    (models.root / MAP).mkdir(exist_ok=True)
    lines = []
    for name, data in made.items():
        if name not in skip:
            (models.root / name).write_bytes(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()} {len(data)} {name} -\n")
    text = models.lock.read_text()
    kept = "".join(line for line in text.splitlines(keepends=True) if f" {MAP}/" not in line)
    models.lock.write_text(kept + "".join(lines))
    return Installed(models.root, models.lock, made)


def vec_bytes(
    nums: Sequence[int], rows: Sequence[Sequence[float]], gen: int, *, model: str = MAP
) -> bytes:
    """Een bestand per eigenaar (app/beeld/types.py) met deze num's en vectoren (f16)."""
    head = vt.pack_head(vt.Head(model, 1, DIM, len(nums), gen))
    body = struct.pack(f"<{len(nums)}q", *nums)
    f16 = b"".join(struct.pack(f"<{DIM}e", *row) for row in rows)
    shas = b"".join(struct.pack("<q", num) for num in nums)  # het begin van een sha per rij
    return head + body + f16 + shas


def vec_fd(data: bytes) -> int:
    """Een fd die alleen lezen kan, zoals de app hem stuurt (O_RDONLY op een gewoon bestand)."""
    fd = os.memfd_create("vectoren", os.MFD_CLOEXEC)
    os.write(fd, data)
    ro = os.open(f"/proc/self/fd/{fd}", os.O_RDONLY | os.O_CLOEXEC)
    os.close(fd)
    return ro
