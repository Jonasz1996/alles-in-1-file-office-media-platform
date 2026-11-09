"""De rekensom van de poort van §4.5 voor OCR (tests/ocr/poort.py), zonder tesseract.

De poort zelf draait met de echte tesseract in de job ocr (taskset -c 0); hier alleen dat hij
goed telt: pagina's per minuut over alle pagina's, per stand apart, en dat een pagina die niet
gelezen is (een andere stand dan done, of een recall onder 0,9) de poort laat falen, hoe snel
ook.
"""

from __future__ import annotations

from tests.ocr import poort


def row(angle: int, seconds: float, state: str = "done", recall: float = 1.0, **kw) -> dict:
    return {
        "angle": angle,
        "seconds": seconds,
        "state": state,
        "code": kw.get("code"),
        "recall": recall,
        "skew": kw.get("skew", float(angle)),
        "osd": kw.get("osd", False),
        "ms": kw.get("ms", {"render": 100, "deskew": 200, "ocr": 800}),
    }


def test_pagina_s_per_minuut_over_alles_en_per_stand():
    rows = [row(0, 1.0), row(0, 2.0), row(90, 3.0, osd=True, skew=0.0)]
    out = poort.report(rows, core=0, total_s=6.0)
    assert out["pages"] == 3 and out["per_minute"] == 30.0 and out["core"] == 0
    assert out["per_angle"]["0"]["per_minute"] == 40.0
    assert out["per_angle"]["90"]["per_minute"] == 20.0
    assert out["per_angle"]["90"]["osd"] == 1
    assert out["per_angle"]["0"]["ms_median"] == {"render": 100.0, "deskew": 200.0, "ocr": 800.0}
    assert out["not_read"] == []


def test_een_snelle_fout_telt_niet_als_snelheid():
    rows = [
        row(3, 0.1, state="failed", code="ocr_bad_image", recall=0.0),
        row(7, 0.1, recall=0.5),
        row(-5, 0.1),
    ]
    out = poort.report(rows, core=0, total_s=0.3)
    assert out["per_minute"] >= poort.GATE_PAGES_PER_MIN
    assert [(r["angle"], r["state"]) for r in out["not_read"]] == [(3, "failed"), (7, "done")]


def test_de_stappen_van_de_poort():
    assert poort.ANGLES == (0, 3, 7, -5, 90)
    assert poort.PER_ANGLE * len(poort.ANGLES) == 100
    assert poort.GATE_PAGES_PER_MIN == 8.0
