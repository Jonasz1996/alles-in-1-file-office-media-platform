"""De query string van zoeken en foto's komt niet in de toegangslog (bouwontwerp fase 6, Keuze 13
en §3.4): onder /api/v1/search, /api/v1/photos en /api/v1/people gaat hij helemaal weg, en q is
overal een geheim zoals token. Het eind-tot-eind-deel (zoeken op een woord, de kaart, een
plaats, en dan de log doorzoeken) is test_search_logs.py van Z1 en Z3.
"""

from __future__ import annotations

import logging

import pytest

from app.main import QUERY_PATHS, SECRET_PARAMS, HideSecrets, _hide


@pytest.mark.parametrize(
    ("path", "want"),
    [
        ("/api/v1/search?q=geheimwoord7&type=pdf", "/api/v1/search?***"),
        ("/api/v1/search/suggest?q=geheim", "/api/v1/search/suggest?***"),
        ("/api/v1/photos?place=2759794&from=2024-01-01", "/api/v1/photos?***"),
        ("/api/v1/photos/map?bbox=4.89,52.37,4.91,52.38&z=14", "/api/v1/photos/map?***"),
        ("/api/v1/photos/memories?day=2026-11-03", "/api/v1/photos/memories?***"),
        ("/api/v1/people?hidden=1", "/api/v1/people?***"),
        ("/api/v1/people/abc/photos?cursor=xyz", "/api/v1/people/abc/photos?***"),
        # Elders alleen q en de geheimen van eerder
        ("/api/v1/files/lookup?q=geheim&limit=5", "/api/v1/files/lookup?q=***&limit=5"),
        ("/api/v1/files/x/pages/1/words?token=T", "/api/v1/files/x/pages/1/words?token=***"),
        ("/api/v1/files/x/children?sort=name", "/api/v1/files/x/children?sort=name"),
        # Zonder query string blijft het pad
        ("/api/v1/search", "/api/v1/search"),
        ("/api/v1/photos/months", "/api/v1/photos/months"),
    ],
)
def test_de_query_string_gaat_weg(path, want):
    assert _hide(path) == want
    assert _hide(f'1.2.3.4 - "GET {path} HTTP/1.1" 200') == f'1.2.3.4 - "GET {want} HTTP/1.1" 200'


def test_een_ander_pad_dat_zo_begint_houdt_zijn_query():
    # /api/v1/photosx en /api/v1/searching zijn geen paden van zoeken of foto's
    assert _hide("/api/v1/photosx?a=1") == "/api/v1/photosx?a=1"
    assert _hide("/api/v1/searching?a=1&q=z") == "/api/v1/searching?a=1&q=***"
    assert "q" in SECRET_PARAMS.pattern and "people" in QUERY_PATHS.pattern


def test_het_filter_van_de_toegangslog():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/api/v1/search?q=geheimwoord7", "1.1", 200),
        None,
    )
    assert HideSecrets().filter(record)
    assert "geheimwoord7" not in record.getMessage()
    assert "/api/v1/search?***" in record.getMessage()
