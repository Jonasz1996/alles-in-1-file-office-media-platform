"""BZ6, de ruwe metingen van X3a: het echte model op één core (fase 6, Keuze 77, 78; marker
beeld_echt).

Alleen in de job beeld-model, onder `taskset -c 0` (BEELD_MODEL, BEELD_RAPPORT; beeld_echt.py).
Wat de container van X3b later meet (warm met `werk` vol, bij 55.000 en 100.000 foto's, tijdens de
backfill, de hele GET /search), hoort bij X3b; dit is het model zelf:

- **foto's per minuut per core** met het beeldmodel in int8: 64 foto's die Pillow maakt (1024 ×
  768, ruis op een verloop), door app.render.image naar 256.webp zoals de app; dan per batch van
  16: 256.webp decoderen, de voorbewerking van meta.json, het model. Drie rondes, de mediaan.
  Poort: ≥ 500 (Vraag 14, §4.5);
- **één vraag in woorden**: tokenizer.bin plus het tekstmodel in int8, één vraag per keer, 200
  keer warm. p95 ≤ 250 ms is een voorwaarde voor de poort van GET /search?mode=image (BZ6), niet
  die poort zelf. De eerste vraag (sessie maken plus de eerste run) en maxrss staan in het
  rapport.
"""

from __future__ import annotations

import json
import os
import resource
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

from app import render
from app.analyse.tokenizer import Tokenizer
from app.analyse.worker import omzetten
from tests_analyse.beeld_echt import (
    BATCH,
    Verslag,
    cpu,
    omgeving,
    sessie,
    voorbewerk,
    vragen,
)

pytestmark = pytest.mark.beeld_echt

FOTOS = 64
RONDES = 3
PER_MINUUT = 500  # Vraag 14: ≥ 500 foto's per minuut per core
VRAAG_P95_MS = 250
HERHAAL = 200


def _maak_fotos(map_: Path) -> list[Path]:
    from PIL import Image

    rnd = np.random.default_rng(77)
    uit = []
    y, x = np.mgrid[0:768, 0:1024]
    for i in range(FOTOS):
        basis = np.stack([(x * (i + 1)) % 256, (y * (i + 3)) % 256, (x + y + 16 * i) % 256], -1)
        ruis = rnd.integers(-40, 40, size=basis.shape)
        a = np.clip(basis + ruis, 0, 255).astype(np.uint8)
        bron = map_ / f"bron-{i}.jpg"
        Image.fromarray(a, "RGB").save(bron, quality=90)
        out = map_ / f"p{i}"
        out.mkdir()
        render.image(bron, out, 100_000_000)
        uit.append(out / "256.webp")
    return uit


def test_bz6_fotos_per_minuut_en_een_vraag():
    model = omgeving("BEELD_MODEL")
    verslag = Verslag("bz6")
    cores = len(os.sched_getaffinity(0))
    meta = json.loads((model / omzetten.META).read_text(encoding="utf-8"))
    vb = meta["beeld"]["voorbewerking"]

    with tempfile.TemporaryDirectory() as tmp:
        paden = _maak_fotos(Path(tmp))
        beeld = sessie(model / omzetten.BEELD)

        def ronde() -> float:
            t = time.perf_counter()
            for i in range(0, len(paden), BATCH):
                blok = np.stack([voorbewerk(p, vb) for p in paden[i : i + BATCH]])
                beeld.run([omzetten.UIT_BEELD], {omzetten.INVOER_BEELD: blok})
            return time.perf_counter() - t

        ronde()  # opwarmen
        tijden = [ronde() for _ in range(RONDES)]
    per_foto = statistics.median(tijden) / FOTOS
    per_minuut = 60 / per_foto

    t = time.perf_counter()
    tok = Tokenizer.load(model / omzetten.TOKENIZER_BIN)
    tekst = sessie(model / omzetten.TEKST)
    teksten = [r["vraag"] for r in vragen()]

    def vraag(v: str) -> float:
        t = time.perf_counter()
        ids = np.asarray([tok.encode(v)], np.int64)
        tekst.run([omzetten.UIT_TEKST], {omzetten.INVOER_TEKST: ids})
        return (time.perf_counter() - t) * 1000

    eerste = vraag(teksten[0])  # de eerste run, na het laden
    eerste_totaal = (time.perf_counter() - t) * 1000
    ms = [vraag(teksten[i % len(teksten)]) for i in range(HERHAAL)]
    p50 = statistics.median(ms)
    p95 = float(np.percentile(ms, 95))
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    verslag.zet(
        "beeld",
        {
            "cores": cores,
            "fotos": FOTOS,
            "rondes_s": [round(x, 3) for x in tijden],
            "ms_per_foto": round(per_foto * 1000, 1),
            "per_minuut": round(per_minuut, 1),
        },
    )
    verslag.zet(
        "vraag",
        {
            "eerste_ms": round(eerste, 1),
            "eerste_met_laden_ms": round(eerste_totaal, 1),
            "p50_ms": round(p50, 1),
            "p95_ms": round(p95, 1),
            "keer": HERHAAL,
        },
    )
    verslag.zet("maxrss_mib", round(maxrss, 1))
    c = cpu()
    verslag.zet("cpu", c)
    verslag.kort(
        f"BZ6 beeld int8 op {cores} core(s): {per_foto * 1000:.1f} ms per foto uit 256.webp, "
        f"{per_minuut:.0f} per minuut (poort ≥ {PER_MINUUT}) — "
        f"{'goed' if per_minuut >= PER_MINUUT else 'FOUT'}; cpu {c['model']}, vnni "
        f"{'ja' if c['vnni'] else 'nee'}, amx {'ja' if c['amx'] else 'nee'}"
    )
    verslag.kort(
        f"BZ6 vraag (tokenizer + tekst int8): p50 {p50:.1f} ms, p95 {p95:.1f} ms (voorwaarde ≤ "
        f"{VRAAG_P95_MS} ms), eerste {eerste:.1f} ms, met laden {eerste_totaal:.0f} ms; maxrss "
        f"{maxrss:.0f} MiB — {'goed' if p95 <= VRAAG_P95_MS else 'FOUT'}"
    )
    assert per_minuut >= PER_MINUUT, tijden
    assert p95 <= VRAAG_P95_MS, (p50, p95)
