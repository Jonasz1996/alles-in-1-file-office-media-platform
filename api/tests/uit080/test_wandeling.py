"""Met de analyse uit is golf 6 onzichtbaar (fase 6, de samenvoeging van golf 6; de FEITEN van
golf 6): elke route van 0.8.0 antwoordt zoals in 0.8.0, en er komt geen job, Setting, rij met
inhoud of bestand bij die 0.8.0 niet had.

De wandeling staat in wandel.py; antwoorden_080.json is dezelfde wandeling op de code van 0.8.0
(`3fca440`). Gezichten (X2b) en zoeken op beeld (X3b) geven met de analyse uit dus 501
not_built("X2") en ("X3"), zoals de stubs van Z0b in 0.8.0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.uit080 import wandel

pytestmark = pytest.mark.anyio

OUD = json.loads((Path(__file__).parent / "antwoorden_080.json").read_text())
# De tabellen van 0011 en 0012 (A0, vast). Met de analyse uit geen gezicht en geen vector; alleen
# de triggers van A0 houden beeld_owner.gen bij (BUMP, Keuze 76: Eva's foto) en node_origin (de
# herkomst van Eva's twee bestanden), zonder iets uit een foto.
NIEUW = {
    "beeld_owner": 1,
    "blob_embed": 0,
    "face": 0,
    "face_owner": 0,
    "face_person": 0,
    "face_scan": 0,
    "node_origin": 2,
}


async def test_elke_route_en_de_stand_zoals_in_080(tmp_path):
    nu = await wandel.wandel(tmp_path)
    # Elke route van 0.8.0 (292 met hun methoden, behalve de stroom /events) voor niemand, Eva,
    # de gast Gijs en de beheerder Jonas: geen route erbij of eraf, en elk antwoord gelijk
    assert len(OUD["antwoorden"]) == 291 * len(wandel.WIE)
    assert set(nu["antwoorden"]) == set(OUD["antwoorden"])
    anders = {
        k: {"0.8.0": v, "nu": nu["antwoorden"][k]}
        for k, v in OUD["antwoorden"].items()
        if nu["antwoorden"][k] != v
    }
    assert anders == {}
    # De stand erna: dezelfde jobs (geen in de rij analyse), Setting en bestanden onder de
    # datamap; dezelfde rijen in de tabellen van 0.8.0, en in de nieuwe precies NIEUW
    oud, stand = OUD["stand"], nu["stand"]
    assert stand["jobs"] == oud["jobs"] == ["preview queued", "preview queued",
                                            "preview.recheck queued"]  # fmt: skip
    assert stand["setting"] == oud["setting"] == ["https", "security"]
    assert stand["bestanden"] == oud["bestanden"]
    assert {t: n for t, n in stand["rijen"].items() if t in oud["rijen"]} == oud["rijen"]
    assert {t: n for t, n in stand["rijen"].items() if t not in oud["rijen"]} == NIEUW
