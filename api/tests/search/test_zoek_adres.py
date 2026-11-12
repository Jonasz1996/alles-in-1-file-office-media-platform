"""Het adres van de pagina /zoeken komt niet in de toegangslog (fase 6, Z4; Keuze 13 en 47).

De web zet de vraag en de filters in het adres (/zoeken?q=…&person=…&place=…), zodat Terug en een
bladwijzer werken. Wie de pagina herlaadt of opent, stuurt dat adres naar de server (die geeft de
web-app terug). q was al een geheim (SECRET_PARAMS), maar een persoon, een plaats, een land, een
dag, een tag of een map zegt ook iets over iemands foto's: onder /zoeken gaat daarom de hele query
string weg, zoals onder /api/v1/search (QUERY_PATHS in app/main.py).
"""

from __future__ import annotations

import logging

import pytest

from app.main import HideSecrets, _hide

P1 = "4f1c0b2e-0000-4000-8000-000000000001"
T1 = "7a7a7a7a-1111-4111-8111-111111111111"


@pytest.mark.parametrize(
    ("path", "want"),
    [
        ("/zoeken?q=geheimwoord7", "/zoeken?***"),
        (f"/zoeken?person={P1}&place=2759794", "/zoeken?***"),
        ("/zoeken?country=NL&from=2024-01-01&to=2024-12-31", "/zoeken?***"),
        (f"/zoeken?q=strand&mode=image&tag={T1}&camera=fujifilm+x100v", "/zoeken?***"),
        (f"/zoeken?mode=image&like={P1}", "/zoeken?***"),
        (f"/zoeken?q=no&bestand={P1}", "/zoeken?***"),
        ("/zoeken/?type=pdf", "/zoeken/?***"),
        # Zonder query string blijft het pad
        ("/zoeken", "/zoeken"),
    ],
)
def test_de_query_string_van_de_pagina_gaat_weg(path, want):
    assert _hide(path) == want
    line = f'1.2.3.4 - "GET {path} HTTP/1.1" 200'
    assert _hide(line) == f'1.2.3.4 - "GET {want} HTTP/1.1" 200'


def test_alleen_de_pagina_zelf():
    # Een ander pad dat zoeken heet, of /zoeken midden in een pad, houdt zijn query (q blijft
    # een geheim, zoals overal)
    assert _hide("/zoekenx?type=pdf") == "/zoekenx?type=pdf"
    assert _hide("/api/v1/files/zoeken?type=pdf&q=x") == "/api/v1/files/zoeken?type=pdf&q=***"
    assert _hide("/map/zoeken?bestand=1") == "/map/zoeken?bestand=1"


def test_de_regel_van_de_toegangslog():
    """Zoals uvicorn hem logt: het pad is een eigen argument van de regel."""
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", f"/zoeken?q=geheimwoord7&person={P1}&place=2759794", "1.1", 200),
        None,
    )
    assert HideSecrets().filter(record)
    message = record.getMessage()
    assert message == '1.2.3.4:5 - "GET /zoeken?*** HTTP/1.1" 200'
    for secret in ("geheimwoord7", P1, "2759794"):
        assert secret not in message
