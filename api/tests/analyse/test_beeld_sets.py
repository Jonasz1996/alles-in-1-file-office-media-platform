"""De vaste lijsten van zoeken op beeld nagekeken zonder het echte model (fase 6, X3a).

- beeld_vragen.tsv (Keuze 83): 24 vragen in het Nederlands met één begrip, precies twee per
  categorie; 6 combinaties met een vaste foto; 6 in het Engels;
- beeld_tokens.tsv (BZ3): elke rij leest, de soort bestaat, en de stukken vormen samen precies
  de tekst na normaliseren (kleine letters per teken, spatie wordt ▁; de bytes als UTF-8). Dat
  vangt een tikfout in de lijst al hier; de ids komen pas in de job beeld-model, uit het echte
  bestand. Deze test gebruikt tokenizer.py niet: de lijst mag nooit van onze tokenizer komen;
- beeld.tsv bestaat nog niet: dan stelt de job hem voor (en is hij rood). Staat hij er, dan komt
  hij door dezelfde strenge controle als in de job.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SETS = Path(__file__).resolve().parent / "sets"
TOOL = Path(__file__).resolve().parents[3] / "tools" / "beeld" / "beeld_model.py"
LENGTE = 64
SOORTEN = ("gemeten", "kleine-letters", "woorden", "teken", "bytes", "afkappen")


def _tool():
    spec = importlib.util.spec_from_file_location("beeld_model_sets", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["beeld_model_sets"] = mod
    spec.loader.exec_module(mod)
    return mod


def _rijen(naam: str) -> list[list[str]]:
    regels = [r for r in (SETS / naam).read_text(encoding="utf-8").splitlines()]
    return [r.split("\t") for r in regels if r and not r.startswith("#")]


def test_beeld_vragen_heeft_de_vorm_van_keuze_83():
    tool = _tool()
    rijen = _rijen("beeld_vragen.tsv")
    assert rijen[0] == ["soort", "vraag", "categorieen", "doel"]
    rijen = rijen[1:]
    assert all(len(r) == 4 for r in rijen)
    nl = [r for r in rijen if r[0] == "nl"]
    combi = [r for r in rijen if r[0] == "combinatie"]
    en = [r for r in rijen if r[0] == "en"]
    assert (len(nl), len(combi), len(en), len(rijen)) == (24, 6, 6, 36)
    assert sorted(r[2] for r in nl) == sorted(list(tool.CATEGORIEEN) * 2)
    assert all(r[3] == "-" for r in nl + en)
    for _, _vraag, cats, doel in combi:
        a, b = cats.split(",")
        assert {a, b} <= set(tool.CATEGORIEEN) and a != b
        assert doel == f"{a}+{b}"
    assert {r[2] for r in en} <= set(tool.CATEGORIEEN)
    vragen = [r[1] for r in rijen]
    assert len(set(vragen)) == len(vragen)
    assert all(v == v.strip() and 0 < len(v) <= 200 and v == v.lower() for v in vragen)


def _normaal(tekst: str) -> str:
    """Kleine letters per teken en ▁ voor een spatie; los van tokenizer.py geschreven."""
    return "".join(c.lower() for c in tekst).replace(" ", "▁")


def _tekst(stukken: list[str]) -> str:
    """De stukken weer achter elkaar, met de bytes (<0xEE>) als UTF-8."""
    uit = bytearray()
    for s in stukken:
        if len(s) == 6 and s.startswith("<0x") and s.endswith(">"):
            uit.append(int(s[3:5], 16))
        else:
            uit += s.encode()
    return uit.decode()


def test_beeld_tokens_is_een_lijst_die_klopt():
    rijen = _rijen("beeld_tokens.tsv")
    assert rijen[0] == ["soort", "tekst", "stukken", "waarom"]
    rijen = rijen[1:]
    assert len(rijen) >= 25
    soorten = set()
    for soort, tekst_json, stukken_json, waarom in rijen:
        assert soort in SOORTEN, soort
        tekst = json.loads(tekst_json)
        stukken = json.loads(stukken_json)
        assert isinstance(tekst, str) and isinstance(stukken, list) and waarom
        assert all(isinstance(s, str) and s for s in stukken)
        assert _tekst(stukken) == _normaal(tekst), (tekst, stukken)
        if soort == "afkappen":
            assert len(stukken) * 100 > LENGTE - 1  # anders kapt 100 keer niets af
        else:
            assert len(stukken) <= LENGTE - 1
        if soort == "teken":
            assert len(_normaal(tekst)) == 1 and stukken == [_normaal(tekst)]
        if soort == "bytes":
            assert any(s.startswith("<0x") for s in stukken)
        soorten.add(soort)
    assert soorten == set(SOORTEN)
    gemeten = [r for r in rijen if r[0] == "gemeten"]
    assert gemeten == [
        [
            "gemeten",
            '"hond op het strand"',
            '["h", "ond", "▁op", "▁het", "▁strand"]',
            "Keuze 74: gemeten in B5 met tokenizers 0.23.3",
        ]
    ]


def test_beeld_tsv_ontbreekt_nog_of_klopt():
    """Zonder beeld.tsv stelt de job hem voor; met beeld.tsv dezelfde strenge controle."""
    tool = _tool()
    pad = SETS / "beeld.tsv"
    if pad.exists():
        fotos = tool.lees_set(pad.read_text(encoding="utf-8"))
        assert len(fotos) == 120
    else:
        assert tool.SET == pad
