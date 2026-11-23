"""Met de analyse uit is golf 6 onzichtbaar (fase 6, de samenvoeging van golf 6; de FEITEN van
golf 6): elke route van 0.8.0 antwoordt zoals in 0.8.0, en er komt geen job, Setting, rij met
inhoud of bestand bij die 0.8.0 niet had.

De wandeling staat in wandel.py; antwoorden_080.json is dezelfde wandeling op de code van 0.8.0
(`3fca440`). Gezichten (X2b) en zoeken op beeld (X3b) geven met de analyse uit dus 501
not_built("X2") en ("X3"), zoals de stubs van Z0b in 0.8.0. De wandeling draait in een nieuw
proces, net als de opname: wat eerdere tests in het proces van pytest achterlieten, telt zo niet
mee (wandel.in_een_nieuw_proces).
"""

from __future__ import annotations

import json
from pathlib import Path

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
    # De tabellen van fase 11 (0016): de wandeling maakt er geen rijen in
    "arr_cleanup": 0,
    "arr_grab": 0,
    "arr_request": 0,
    "arr_subtitle": 0,
    "arr_upscale": 0,
    "arr_wanted": 0,
    "media_facts": 0,
    "media_intro": 0,
    "media_profile": 0,
    "media_sub_offset": 0,
    # Het logboek van account resetten (0018): de wandeling reset niemand
    "account_reset": 0,
    "reset_request": 0,
}


# De routes die fase 11 toevoegt (na 0.8.0)
FASE11 = (
    "/api/v1/admin/arr",
    "/api/v1/arr/",
    "/api/v1/apps",
    "/api/v1/media/profiles",
    "/api/v1/media/stats",
    "/api/v1/media/collections",
    "/api/v1/media/subtitles",
    "/api/v1/upscaler",
    "/api/v1/admin/media/stats",
    "/api/v1/admin/media/profiles",
    "/api/v1/media/sub-offset/",
    "/api/v1/media/items/{node_id}/collection",
    # Account resetten: de code van "Wachtwoord vergeten of kan niet inloggen?"
    "/api/v1/auth/reset-request",
)


def zonder_reset(waarde):
    """Account resetten (0018) zet in elke gebruiker het veld password_change_required (hier altijd
    false): zonder dat veld moet een antwoord gelijk zijn aan dat van 0.8.0."""
    if isinstance(waarde, dict):
        return {k: zonder_reset(v) for k, v in waarde.items() if k != "password_change_required"}
    if isinstance(waarde, list):
        return [zonder_reset(v) for v in waarde]
    # Een lijst bewaart elke vorm als tekst (wandel.vorm, json.dumps met sort_keys)
    if isinstance(waarde, str) and waarde.startswith("{") and "password_change_required" in waarde:
        return json.dumps(zonder_reset(json.loads(waarde)), sort_keys=True)
    return waarde


def test_elke_route_en_de_stand_zoals_in_080(tmp_path):
    nu = wandel.in_een_nieuw_proces(tmp_path)
    # Elke route van 0.8.0 (292 met hun methoden, behalve de stroom /events) voor niemand, Eva,
    # de gast Gijs en de beheerder Jonas: geen route erbij of eraf, en elk antwoord gelijk
    assert len(OUD["antwoorden"]) == 291 * len(wandel.WIE)
    # Fase 11 (mediabeheer, client-apps) voegt routes toe; die van 0.8.0 blijven er allemaal
    assert set(OUD["antwoorden"]) <= set(nu["antwoorden"])
    erbij = {k.split()[1] for k in set(nu["antwoorden"]) - set(OUD["antwoorden"])}
    assert {p for p in erbij if not p.startswith(FASE11)} == set()
    anders = {
        k: {"0.8.0": v, "nu": nu["antwoorden"][k]}
        for k, v in OUD["antwoorden"].items()
        if zonder_reset(nu["antwoorden"][k]) != v
        # De beschrijving van de API telt de routes van fase 11 mee
        and k.split()[1] != "/api/openapi.json"
    }
    assert anders == {}
    # De stand erna: dezelfde jobs (geen in de rij analyse), Setting en bestanden onder de
    # datamap; dezelfde rijen in de tabellen van 0.8.0, en in de nieuwe precies NIEUW
    oud, stand = OUD["stand"], nu["stand"]
    assert stand["jobs"] == oud["jobs"] == ["preview queued", "preview queued",
                                            "preview.recheck queued"]  # fmt: skip
    # arr_settings: de wandeling roept ook de routes van fase 11 aan (bv. als beheerder een
    # koppeling vergeten), en die schrijven de instellingen van mediabeheer
    assert [k for k in stand["setting"] if k != "arr_settings"] == oud["setting"]
    assert oud["setting"] == ["https", "security"]
    assert stand["bestanden"] == oud["bestanden"]
    rijen = {t: n for t, n in stand["rijen"].items() if t in oud["rijen"]}
    rijen["setting"] -= "arr_settings" in stand["setting"]
    assert rijen == oud["rijen"]
    assert {t: n for t, n in stand["rijen"].items() if t not in oud["rijen"]} == NIEUW
