"""De rekensom van de poort van §4.5 voor OCR (tests/ocr/poort.py), zonder tesseract.

De poort zelf draait met de echte tesseract in de job ocr (taskset -c 0); hier alleen dat hij
goed telt: pagina's per minuut over alle pagina's, per dichtheid (de brief en de volle pagina)
en per stand apart, dat een pagina die niet gelezen is (een andere stand dan done, of een recall
onder 0,9) de poort laat falen, hoe snel ook, en dat de volle pagina's meetellen in de poort
(review golf 4, uitrol F4).
"""

from __future__ import annotations

from tests.ocr import poort, scans


def row(angle: int, seconds: float, state: str = "done", recall: float = 1.0, **kw) -> dict:
    return {
        "density": kw.get("density", "brief"),
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
    assert list(out["per_density"]) == ["brief"]
    brief = out["per_density"]["brief"]
    assert brief["pages"] == 3 and brief["per_minute"] == 30.0
    assert brief["per_angle"]["0"]["per_minute"] == 40.0
    assert brief["per_angle"]["90"]["per_minute"] == 20.0
    assert brief["per_angle"]["90"]["osd"] == 1
    assert brief["per_angle"]["0"]["ms_median"] == {"render": 100.0, "deskew": 200.0, "ocr": 800.0}
    assert out["not_read"] == []
    assert poort.passed(out)


def test_per_dichtheid_apart_en_de_volle_pagina_s_tellen_mee():
    # 100 brieven van 2 s (30 per minuut) en 30 volle pagina's van 10 s (6 per minuut): alles
    # samen 15,6 per minuut, gehaald; de volle pagina staat er apart bij
    rows = [row(0, 2.0) for _ in range(100)] + [row(3, 10.0, density="vol") for _ in range(30)]
    out = poort.report(rows, core=0, total_s=500.0)
    assert out["per_minute"] == 15.6
    assert list(out["per_density"]) == ["brief", "vol"]
    assert out["per_density"]["brief"]["per_minute"] == 30.0
    assert out["per_density"]["vol"]["per_minute"] == 6.0
    assert out["per_density"]["vol"]["pages"] == 30
    assert list(out["per_density"]["vol"]["per_angle"]) == ["3"]
    assert out["not_read"] == []
    assert poort.passed(out)
    # Volle pagina's van 60 s: de brieven alleen halen 30, maar alles samen 3,9: niet gehaald
    rows = [row(0, 2.0) for _ in range(100)] + [row(3, 60.0, density="vol") for _ in range(30)]
    out = poort.report(rows, core=0, total_s=2000.0)
    assert out["per_density"]["brief"]["per_minute"] == 30.0 and out["per_minute"] == 3.9
    assert not poort.passed(out)
    # Een volle pagina die niet gelezen is, telt als niet gelezen
    rows = [row(0, 2.0), row(90, 2.0, density="vol", recall=0.5)]
    out = poort.report(rows, core=0, total_s=4.0)
    assert [(r["density"], r["angle"]) for r in out["not_read"]] == [("vol", 90)]
    assert not poort.passed(out)


def test_een_snelle_fout_telt_niet_als_snelheid():
    rows = [
        row(3, 0.1, state="failed", code="ocr_bad_image", recall=0.0),
        row(7, 0.1, recall=0.5),
        row(-5, 0.1),
    ]
    out = poort.report(rows, core=0, total_s=0.3)
    assert out["per_minute"] >= poort.GATE_PAGES_PER_MIN
    assert [(r["angle"], r["state"]) for r in out["not_read"]] == [(3, "failed"), (7, "done")]
    assert not poort.passed(out)


def test_de_stappen_van_de_poort():
    assert poort.ANGLES == (0, 3, 7, -5, 90)
    assert poort.PER_ANGLE * len(poort.ANGLES) == 100
    assert poort.VOL_ANGLES == (0, 3, 90)
    assert poort.VOL_PER_ANGLE * len(poort.VOL_ANGLES) == 30
    assert poort.DENSITIES == ("brief", "vol")
    assert poort.GATE_PAGES_PER_MIN == 8.0


def test_de_volle_pagina_is_vol():
    """≥ 40 regels in een kleinere letter dan de brief, binnen A4 (zonder lettertype te laden)."""
    assert len(scans.VOL_LINES) == 44 and len(scans.LINES) == 12
    assert scans.VOL_SIZE == 34 and scans.SIZE == 46
    assert len(scans.VOL_WORDS) == 696 and len(scans.WORDS) == 68
    assert scans.VOL_TOP + len(scans.VOL_LINES) * scans.VOL_LINE < scans.A4[1] - 200
    assert "dichtwoordx" in scans.VOL_WORDS and "dichtwoordx" not in scans.WORDS
