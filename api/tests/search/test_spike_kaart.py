"""Spike S6 (bouwontwerp fase 6, §3.7; Keuze 31): tools/kaart/maak_wereld.py met een kleine
eigen GeoJSON. De echte bron (Natural Earth 110m v5.1.2) haalt de job zoek-perf op en meet hem;
de getallen staan in docs/fase-6/spike.md."""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.search import spike


def _land(a3: str, a2: str, naam: str, geometrie: dict) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "ADM0_A3": a3,
            "ISO_A2_EH": a2,
            "NAME": naam,
            "NAME_NL": naam + " (nl)",
            "NAME_EN": naam,
        },  # fmt: skip
        "geometry": geometrie,
    }


VIERKANT = [[5.0, 52.0], [6.004, 52.0], [6.0, 53.0], [5.0, 53.0], [5.0, 52.0]]
GAT = [[5.4, 52.4], [5.6, 52.4], [5.6, 52.6], [5.4, 52.4]]
PUNTJE = [[1.0, 1.0], [1.001, 1.001], [1.0, 1.0]]  # na het afronden één punt
BRON = {
    "type": "FeatureCollection",
    "features": [
        _land("ZZZ", "-99", "Zee", {"type": "Polygon", "coordinates": [VIERKANT]}),
        _land("NLD", "NL", "Netherlands", {
            "type": "MultiPolygon", "coordinates": [[VIERKANT, GAT], [PUNTJE]]}),
    ],
}  # fmt: skip


@pytest.fixture(scope="module")
def kaart():
    return spike.kaart()


def _bytes(bron: dict) -> tuple[bytes, str]:
    data = json.dumps(bron).encode()
    return data, hashlib.sha256(data).hexdigest()


def test_een_ring_in_honderdsten_met_verschillen(kaart):
    r = kaart.ring(VIERKANT)
    # Het sluitpunt is weg; 6,004 wordt 600
    assert r == [500, 5200, 100, 0, 0, 100, -100, 0]
    assert kaart.terug(r) == [(5.0, 52.0), (6.0, 52.0), (6.0, 53.0), (5.0, 53.0)]
    # Een punt dat na het afronden gelijk is aan het vorige telt niet
    assert kaart.ring(PUNTJE) == [100, 100]


def test_de_kaart_is_klein_en_altijd_gelijk(kaart):
    data, sha = _bytes(BRON)
    bron = kaart.lees_bron(data, sha)
    een, twee = kaart.maak(bron, sha), kaart.maak(kaart.lees_bron(data, sha), sha)
    assert een == twee
    assert kaart.naam(een) == f"world-110m.{hashlib.sha256(een).hexdigest()[:12]}.json"
    uit = json.loads(een)
    assert uit["sha256"] == sha
    assert [land["a3"] for land in uit["landen"]] == ["NLD", "ZZZ"]
    nld, zee = uit["landen"]
    assert (nld["a2"], nld["nl"], nld["en"]) == ("NL", "Netherlands (nl)", "Netherlands")
    assert zee["a2"] is None  # -99 is geen code
    # Het vlak met het gat blijft, het puntje (minder dan drie punten) valt weg
    assert len(nld["v"]) == 1 and len(nld["v"][0]) == 2
    m = kaart.meet(een, bron)
    assert (m["landen"], m["punten"], m["punten in de bron"]) == (2, 11, 17)


def test_een_andere_bron_weigert_hij(kaart, tmp_path, capsys):
    data, _ = _bytes(BRON)
    with pytest.raises(kaart.VerkeerdeBron):
        kaart.lees_bron(data)  # de sha256 van Natural Earth, niet van deze
    pad = tmp_path / "bron.geojson"
    pad.write_bytes(data)
    assert kaart.main(["--bron", str(pad), "--uit", str(tmp_path / "uit")]) == 2
    assert "de bron is niet goed" in capsys.readouterr().err
    assert not (tmp_path / "uit").exists()


def test_boven_de_grens_is_exit_1(kaart, tmp_path, monkeypatch, capsys):
    data, sha = _bytes(BRON)
    pad = tmp_path / "bron.geojson"
    pad.write_bytes(data)
    monkeypatch.setattr(kaart, "BRON_SHA256", sha)
    monkeypatch.setattr(kaart, "lees_bron", lambda d, s=sha, f=kaart.lees_bron: f(d, s))
    assert kaart.main(["--bron", str(pad), "--uit", str(tmp_path)]) == 0
    monkeypatch.setattr(kaart, "GRENS_GZIP", 10)
    assert kaart.main(["--bron", str(pad)]) == 1
    regel = capsys.readouterr().out.splitlines()[-1]
    assert regel.startswith("world-110m.") and "(grens 10)" in regel
    assert len(list(tmp_path.glob("world-110m.*.json"))) == 1
