"""De query string van zoeken en foto's komt niet in de toegangslog (bouwontwerp fase 6, Keuze 13
en §3.4): onder /api/v1/search, /api/v1/photos en /api/v1/people gaat hij helemaal weg, en q is
overal een geheim zoals token. Het eind-tot-eind-deel (zoeken op een woord, de kaart, een
plaats, en dan de log doorzoeken) is test_search_logs.py van Z1 en Z3.

Z0b: ook zoeken op beeld (mode=image, met q of like; X3) en de routes van gezichten (X2) en van
GET /photos/beeld vallen eronder; dat bewijzen de rijen en de tests hieronder.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from app.db import get_engine
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
        # Z6: de dag of het id van een regel van de herinneringen staat in het pad
        ("/api/v1/photos/memories/hide/day/2024-07-01", "/api/v1/photos/memories/hide/***"),
        (
            "/api/v1/photos/memories/hide/person/4f1c0b2e-0000-4000-8000-000000000001",
            "/api/v1/photos/memories/hide/***",
        ),
        ("/api/v1/photos/memories/hide/day/2024-07-01?x=1", "/api/v1/photos/memories/hide/***?***"),
        ("/api/v1/photos/memories/hide", "/api/v1/photos/memories/hide"),
        # Z0b: zoeken op beeld (X3) en gezichten (X2)
        ("/api/v1/search?mode=image&q=geheimwoord7", "/api/v1/search?***"),
        ("/api/v1/search?q=geheimwoord7&mode=image&type=image", "/api/v1/search?***"),
        ("/api/v1/search?mode=image&like=4f1c0b2e", "/api/v1/search?***"),
        ("/api/v1/photos/beeld?x=1", "/api/v1/photos/beeld?***"),
        (
            "/api/v1/people/faces/suggestions?person=abc&cursor=x",
            "/api/v1/people/faces/suggestions?***",
        ),
        ("/api/v1/people/faces/abc/crop?size=96", "/api/v1/people/faces/abc/crop?***"),
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


def _toegang(path: str, status: int = 200) -> logging.LogRecord:
    """Een regel van de toegangslog zoals uvicorn hem maakt."""
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", path, "1.1", status),
        None,
    )


def test_het_filter_van_de_toegangslog():
    record = _toegang("/api/v1/search?q=geheimwoord7")
    assert HideSecrets().filter(record)
    assert "geheimwoord7" not in record.getMessage()
    assert "/api/v1/search?***" in record.getMessage()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/search?mode=image&q=geheimwoord7",
        "/api/v1/search?q=geheimwoord7&mode=image",
        "/api/v1/search?mode=image&q=geheim%20woord7&like=00000000-0000-0000-0000-000000000007",
    ],
)
def test_zoeken_op_beeld_staat_niet_in_de_toegangslog(path):
    """Z0b: de regel van GET /search?mode=image&q=… (ook met like) zonder de vraag, ook als de
    stub 501 geeft."""
    record = _toegang(path, 501)
    assert HideSecrets().filter(record)
    message = record.getMessage()
    assert "geheim" not in message and "woord7" not in message and "mode=" not in message
    assert message == '1.2.3.4:5 - "GET /api/v1/search?*** HTTP/1.1" 501'


async def test_zoeken_op_beeld_logt_de_vraag_niet(client, admin, caplog):
    """De stub van X3 zet de vraag niet in zijn antwoord en niet in de log van de app."""
    headers, _ = admin
    vraag = "".join(("geheim", "woord7"))
    caplog.set_level(logging.DEBUG)
    r = await client.get("/api/v1/search", params={"mode": "image", "q": vraag}, headers=headers)
    assert r.status_code == 501
    assert vraag not in r.text
    for record in caplog.records:
        assert vraag not in logging.Formatter().format(record), record.name


async def test_een_databasefout_bij_zoeken_zet_q_niet_in_de_log(client, admin, settings, caplog):
    """Een fout van de database tijdens het zoeken (hier: "database is locked") komt met zijn
    SQL in de foutlog van uvicorn. De parameters daarvan, met q, horen er niet in
    (hide_parameters op de engine; review Z0, veilig F6)."""
    headers, _ = admin
    engine = get_engine(settings).sync_engine
    # In delen, anders staat de vraag in de broncode die de traceback in de log laat zien
    vraag = "".join(("geheim", "woord7"))

    def op_slot(cursor, statement, parameters, context):
        if any(vraag in str(p) for p in parameters or ()):
            raise sqlite3.OperationalError("database is locked")

    caplog.set_level(logging.DEBUG)
    event.listen(engine, "do_execute", op_slot)
    try:
        with pytest.raises(DBAPIError) as fout:
            await client.get("/api/v1/search", params={"q": vraag}, headers=headers)
    finally:
        event.remove(engine, "do_execute", op_slot)
    # Zoals uvicorn een fout in de app logt
    logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=fout.value)
    assert "database is locked" in str(fout.value)
    assert vraag not in str(fout.value)
    assert any(r.exc_info for r in caplog.records)
    for record in caplog.records:
        assert vraag not in logging.Formatter().format(record), record.name
