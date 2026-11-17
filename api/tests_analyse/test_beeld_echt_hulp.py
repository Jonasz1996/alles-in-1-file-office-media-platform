"""De rekenstukken van de tests met het echte beeldmodel, zonder dat model (fase 6, X3a).

De poorten van BZ3 en BZ4 draaien alleen in de job beeld-model. Wat ze rekenen, kan hier al: de
precisie@5, "Meer zoals deze", de voorbewerking van meta.json, de eigen BPE en de voorwaarden van
de lijst (op het kleine tokenizer.json van siglip_klein.py). Zo is een rode poort in de job een
uitkomst van het model, niet van de test.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.analyse.tokenizer import Tokenizer
from app.analyse.worker.ops import beeld as B
from tests_analyse import fake_beeld, siglip_klein
from tests_analyse.beeld_echt import cpu, kwantielen, voorbewerk
from tests_analyse.test_beeld_kwaliteit import _meer_zoals, _precisie, _volgorde
from tests_analyse.test_beeld_tokenizer_echt import (
    Echt,
    _lijst,
    _over_een_grens,
    _verwacht,
    _voorwaarde,
    _zinnen,
)


def _een(i: int, n: int = 4) -> np.ndarray:
    v = np.zeros(n, np.float32)
    v[i] = 1
    return v


def test_precisie_telt_goede_fotos_in_de_top_5():
    # 6 foto's van categorie a (as 0), 2 van b (as 1), de rest niets; een vraag per categorie
    e = np.stack([_een(0)] * 6 + [_een(1)] * 2 + [_een(2)] * 4)
    cats = [{"a"}] * 6 + [{"b"}] * 2 + [set()] * 4
    # De derde vraagt b maar lijkt op de foto's zonder categorie: 4 daarvan, dan een a
    t = np.stack([_een(0), _een(1), _een(2)])
    rijen = [{"categorieen": "a"}, {"categorieen": "b"}, {"categorieen": "b"}]
    assert _precisie(t, e, rijen, cats) == [5, 2, 0]


def test_bij_gelijke_score_eerst_de_eerdere_foto():
    assert list(_volgorde(np.array([0.5, 0.9, 0.5, 0.9]))) == [1, 3, 0, 2]


def test_meer_zoals_deze_telt_een_gedeelde_categorie_en_niet_zichzelf():
    e = np.stack([_een(0)] * 5 + [_een(1)] * 5 + [_een(2)] * 2)
    cats = [{"a"}] * 5 + [{"b", "c"}] * 4 + [{"c"}] + [set()] * 2
    uit = _meer_zoals(e, cats)
    assert len(uit) == 10  # de foto's zonder categorie vragen niet
    assert uit[:5] == [1.0] * 5
    assert uit[5:9] == [1.0] * 4  # de vier buren delen b of c
    assert uit[9] == 1.0  # c deelt c met {b, c}


def test_voorbewerking_volgt_meta():
    from PIL import Image

    vb = {"grootte": [8, 8], "resample_pil": 3, "schaal": 1 / 255, "gemiddelde": [0.5] * 3}
    vb["std"] = [0.5] * 3
    a = voorbewerk(Image.new("RGB", (32, 16), (255, 0, 0)), vb)
    assert a.shape == (3, 8, 8) and a.dtype == np.float32
    assert np.allclose(a[0], 1.0) and np.allclose(a[1:], -1.0)


def test_de_worker_voorbewerkt_zoals_voorbewerk():
    """x3a.md, Open 4: de voorbewerking van de worker (ops/beeld.py) is die van
    beeld_echt.voorbewerk, met meta.json in de vorm van bouw.py (de grootte, de resample, de
    schaal, het gemiddelde en std van siglip_klein), op een beeld dat niet vierkant is."""
    from PIL import Image

    raw = fake_beeld.meta_json(fake_beeld.tokenizer())
    m = B.parse_meta(raw)
    vb = json.loads(raw)["beeld"]["voorbewerking"]
    rng = np.random.default_rng(5)
    rgb = rng.integers(0, 256, (37, 61, 3), dtype=np.uint8)
    worker = B.preprocess(rgb, m)
    ref = voorbewerk(Image.fromarray(rgb, mode="RGB"), vb)
    assert worker.shape == ref.shape == (3, *m.size) and worker.dtype == np.float32
    assert np.array_equal(worker, ref)


def test_kwantielen():
    assert kwantielen(range(101))["p50"] == 50.0
    assert kwantielen([]) == {}


def test_cpu_leest_naam_vnni_en_amx(tmp_path):
    """De regels van BZ4 en BZ6 zeggen op welke CPU ze liepen (CI op 669f03c)."""
    info = tmp_path / "cpuinfo"
    info.write_text(
        "processor\t: 0\nmodel name\t: Xeon A\nflags\t\t: fpu avx2 avx512_vnni amx_int8\n\n"
        "processor\t: 1\nmodel name\t: Xeon B\nflags\t\t: fpu\n"
    )
    assert cpu(info) == {"model": "Xeon A", "vnni": True, "amx": True}
    info.write_text("model name\t: Epyc\nflags\t\t: fpu avx_vnni\n")
    assert cpu(info) == {"model": "Epyc", "vnni": True, "amx": False}
    info.write_text("model name\t: Oud\nflags\t\t: fpu avx2 amx_tile\n")
    assert cpu(info) == {"model": "Oud", "vnni": False, "amx": False}
    assert cpu(tmp_path / "weg") == {"model": "onbekend", "vnni": False, "amx": False}


@pytest.fixture
def klein(tmp_path):
    pad = tmp_path / "tokenizer.json"
    doc = siglip_klein.tokenizer_json()
    pad.write_text(json.dumps(doc, ensure_ascii=False))
    return pad, doc


def test_de_eigen_bpe_is_onze_tokenizer_op_het_kleine_bestand(klein):
    pad, _ = klein
    echt, ons = Echt(pad), Tokenizer.from_json(pad)
    assert [ons.stuk(i) for i in echt.bpe("hond op het strand")] == [
        "h",
        "ond",
        "▁op",
        "▁het",
        "▁strand",
    ]
    for zin in _zinnen(200):
        assert echt.bpe(zin) == ons.stukken(zin), zin


def test_de_eigen_bpe_neemt_de_laagste_rang_en_bij_gelijk_de_linkse(tmp_path, klein):
    _, doc = klein
    v = doc["model"]["vocab"]
    for stuk in ("aa", "aaa", "ab"):
        v[stuk] = len(v)
    doc["model"]["merges"] = [["a", "b"], ["a", "a"], ["aa", "a"]]
    pad = tmp_path / "rang.json"
    pad.write_text(json.dumps(doc, ensure_ascii=False))
    echt, ons = Echt(pad), Tokenizer.from_json(pad)
    for zin, stukken in (("aab", ["a", "ab"]), ("aaaa", ["aa", "aa"]), ("aaa", ["aaa"])):
        assert [ons.stuk(i) for i in echt.bpe(zin)] == stukken
        assert echt.bpe(zin) == ons.stukken(zin)


def test_een_stuk_over_een_grens_valt_op(tmp_path, klein):
    pad, doc = klein
    echt = Echt(pad)
    assert _over_een_grens(echt, ["▁op", "▁het", "▁strand"]) == []
    doc["model"]["vocab"]["p▁"] = len(doc["model"]["vocab"])
    pad2 = tmp_path / "grens.json"
    pad2.write_text(json.dumps(doc, ensure_ascii=False))
    assert _over_een_grens(Echt(pad2), ["▁op", "▁het", "▁strand"]) == ["p▁"]


def test_de_voorwaarden_van_de_lijst(klein):
    pad, _ = klein
    echt = Echt(pad)
    # Het kleine bestand heeft geen cijfers, é of σ: die rijen vallen af, de rest past
    fout = {r[1]: _voorwaarde(echt, *r) for r in _lijst() if _voorwaarde(echt, *r)}
    assert set(fout) == {"7", "é", "É", "Σ", "ς"}, fout
    assert _voorwaarde(echt, "bytes", "", ["<0xEE>", "<0x80>", "<0x80>"]) == []
    echt.merge_stukken.add("<0xEE>")
    assert _voorwaarde(echt, "bytes", "", ["<0xEE>", "<0x80>", "<0x80>"]) == [
        "<0xEE> staat in een merge"
    ]
    echt.vocab["x"] = 10_000
    assert "U+E000 staat in een stuk van de woordenlijst" in _voorwaarde(
        echt, "bytes", "", ["<0xEE>", "<0x80>", "<0x80>"]
    )


def test_verwacht_is_stukken_eos_en_pad(klein):
    pad, _ = klein
    echt = Echt(pad)
    v = echt.vocab
    ids = _verwacht(echt, "woorden", ["▁strand"])
    assert ids[:2] == [v["▁strand"], v["<eos>"]] and ids[2:] == [v["<pad>"]] * 62
    ids = _verwacht(echt, "afkappen", ["a", "<0xEE>"])
    assert len(ids) == 64 and ids[-1] == v["<eos>"] and ids[:4] == [v["a"], v["<0xEE>"]] * 2
