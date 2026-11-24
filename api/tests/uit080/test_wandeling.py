"""Met de analyse uit is golf 6 onzichtbaar (fase 6, de samenvoeging van golf 6; de FEITEN van
golf 6): elke route van 0.8.0 antwoordt zoals in 0.8.0, en er komt geen job, Setting, rij met
inhoud of bestand bij die 0.8.0 niet had.

De wandeling staat in wandel.py; antwoorden_080.json is dezelfde wandeling op de code van 0.8.0
(`3fca440`). Gezichten (X2b) en zoeken op beeld (X3b) geven met de analyse uit dus 501
not_built("X2") en ("X3"), zoals de stubs van Z0b in 0.8.0. De wandeling draait in een nieuw
proces, net als de opname: wat eerdere tests in het proces van pytest achterlieten, telt zo niet
mee (wandel.in_een_nieuw_proces).

Op precies ERBIJ_082 na: velden die 0.8.2 bewust aan de API toevoegt en die niet bij de analyse
horen. De test legt ze op de antwoorden van 0.8.0 (en haalt ze uit de OpenAPI van nu); elk ander
verschil faalt nog steeds.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, NamedTuple

from tests.uit080 import wandel

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


class Erbij(NamedTuple):
    """Een veld dat 0.8.2 bewust aan een goed antwoord van 0.8.0 toevoegt."""

    antwoord: str  # de sleutel in de wandeling: methode, pad en wie
    pad: tuple[str, ...]  # waar het veld in de JSON staat; het laatste deel is zijn naam
    vorm: Any  # zijn vorm zoals wandel.vorm hem geeft
    schema: str  # het schema in de OpenAPI dat hem als property heeft
    openapi: dict[str, Any]  # zijn property in dat schema, precies


# Wat 0.8.2 bewust aan de API van 0.8.0 toevoegt, met de analyse uit, precies. Niets hiervan hoort
# bij de analyse: een veld van de analyse blijft met de analyse uit onzichtbaar, ook in de OpenAPI
# (zoals faces.keys_missing, SkipJsonSchema in app/search/types.py).
ERBIJ_082 = (
    # index.capped van GET /api/v1/admin/search: hoeveel documenten de zoekindex op PostgreSQL
    # inkortte (1 MB per tsvector; wat erna komt, is niet vindbaar). 489ac57, golf 3: de review van
    # PR #6, F10 (plan 0.8.2, P5; vraag 8); /beheer toont het als het niet 0 is. Op SQLite altijd 0
    # (tests/search/test_admin_search.py). Niemand, Eva en Gijs krijgen 401 en 403 zoals in 0.8.0.
    Erbij(
        antwoord="GET /api/v1/admin/search jonas",
        pad=("index", "capped"),
        vorm="int",
        schema="AdminIndex",
        openapi={"default": 0, "title": "Capped", "type": "integer"},
    ),
)
OPENAPI = "GET /api/openapi.json"


def _sha(uit: Any) -> str:
    """Een grote vorm als sha256, zoals wandel._vraag hem bewaart."""
    tekst = json.dumps(uit, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(tekst.encode()).hexdigest()


def met_082(oud: dict[str, Any]) -> dict[str, Any]:
    """De antwoorden van 0.8.0 met precies ERBIJ_082 erbij.

    Van de OpenAPI bewaart de wandeling alleen de sha256 van de vorm (wandel.GROOT). Die van 0.8.0
    moet die van de OpenAPI van nu zijn, min precies de properties van ERBIJ_082; dan verwacht de
    wandeling de sha256 van de OpenAPI van nu (die de vergelijking daarna aan het antwoord van de
    wandeling bindt)."""
    from app.main import app

    verwacht = copy.deepcopy(oud)
    doc = copy.deepcopy(app.openapi())
    sha_nu = _sha(wandel.vorm(doc))
    for e in ERBIJ_082:
        status, soort, plek = verwacht[e.antwoord]
        assert (status, soort) == (200, "application/json"), e.antwoord
        *pad, naam = e.pad
        for deel in pad:
            plek = plek[deel]
        assert naam not in plek, e
        plek[naam] = e.vorm
        schema = doc["components"]["schemas"][e.schema]
        assert schema["properties"].pop(naam) == e.openapi, e
        assert naam not in schema.get("required", []), e
    zonder = [200, "application/json", _sha(wandel.vorm(doc))]
    for wie in wandel.WIE:
        sleutel = f"{OPENAPI} {wie}"
        assert verwacht[sleutel] == zonder, f"{sleutel}: zonder ERBIJ_082 niet die van 0.8.0"
        verwacht[sleutel] = [200, "application/json", sha_nu]
    return verwacht


def test_elke_route_en_de_stand_zoals_in_080(tmp_path):
    nu = wandel.in_een_nieuw_proces(tmp_path)
    # Elke route van 0.8.0 (292 met hun methoden, behalve de stroom /events) voor niemand, Eva,
    # de gast Gijs en de beheerder Jonas: geen route erbij of eraf, en elk antwoord gelijk, op
    # precies ERBIJ_082 na
    assert len(OUD["antwoorden"]) == 291 * len(wandel.WIE)
    assert set(nu["antwoorden"]) == set(OUD["antwoorden"])
    verwacht = met_082(OUD["antwoorden"])
    anders = {
        k: {"verwacht": v, "nu": nu["antwoorden"][k]}
        for k, v in verwacht.items()
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
