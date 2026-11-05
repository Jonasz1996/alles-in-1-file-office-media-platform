"""Geen zoekvraag en geen plek in de logs (bouwontwerp fase 6, §4.1, Keuze 13; S16).

Zoek op een geheim woord, met filters op plaats, land, persoon en dag; vraag suggesties; de
foto's op een plaats; de kaart op zoom 14 rond een bekende plek; herinneringen op een dag. Daarna
staat geen van die woorden of getallen in de log van de app: niet in een gewone regel, niet bij
search_access_mismatch (alleen het node-id), niet bij een databasefout, en niet in de
toegangslog van uvicorn (die ziet alleen de echte server: tweede test).

test_log_hiding.py (review Z0, veilig F6) bewijst de onderdelen: _hide per pad en
hide_parameters op de engine. Dit is het geheel.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from app.db import get_engine
from app.search import engine
from tests.search import wereld as W
from tests.search.server import App, zonder_2fa
from tests.search.test_search_access import _doe, kop

pytestmark = pytest.mark.anyio

# In delen: anders staan ze in de broncode die een traceback in de log laat zien
WOORD = "".join(("geheim", "woord7"))
PLAATS = str(2_759_000 + 794)
LAT, LON = "".join(("52.", "37021")), "".join(("4.", "89517"))
BBOX = f"{LON},{LAT},{LON[:-1]}9,{LAT[:-1]}9"
DAG = "-".join(("1999", "12", "31"))
PERSOON = str(uuid.uuid4())
GEHEIM = (WOORD, PLAATS, LAT, LON, DAG, PERSOON)


def vragen() -> list[tuple[str, dict]]:
    return [
        ("/api/v1/search", {"q": WOORD}),
        (
            "/api/v1/search",
            {
                "q": WOORD,
                "place": PLAATS,
                "country": "NL",
                "person": PERSOON,
                "from": DAG,
                "type": "image",
            },
        ),
        ("/api/v1/search", {"place": PLAATS}),
        ("/api/v1/search/suggest", {"q": WOORD}),
        ("/api/v1/photos", {"place": PLAATS, "from": DAG}),
        ("/api/v1/photos/map", {"bbox": BBOX, "z": "14"}),
        ("/api/v1/photos/map/items", {"bbox": BBOX, "z": "14"}),
        ("/api/v1/photos/memories", {"day": DAG}),
        ("/api/v1/people", {"q": WOORD}),
    ]


def gedaan(r) -> bool:
    """Een antwoord zonder fout van de server; de kaart en herinneringen zijn tot Z3 een stub
    (501 not_built), en ook dan mag de vraag niet in de log."""
    if r.status_code == 501:
        return r.json()["detail"]["code"] == "not_built"
    return r.status_code < 500


def lekken(tekst: str) -> list[str]:
    return [g for g in GEHEIM if g in tekst]


def _alles(caplog) -> str:
    form = logging.Formatter("%(name)s %(levelname)s %(message)s")
    return "\n".join(form.format(r) for r in caplog.records)


async def test_niets_in_de_log_van_de_app(client, settings, monkeypatch, caplog, mismatch_verwacht):
    w = await W.bouw_async(settings)
    eva, hans = await kop(client, "eva"), await kop(client, "hans")
    # Alles van de app en van uvicorn (ook met PLATFORM_LOG_LEVEL=debug), en de rest vanaf INFO
    # (de app zet de wortel nooit lager; aiosqlite logt op DEBUG elke SQL met zijn parameters)
    caplog.set_level(logging.INFO)
    for naam in ("platform", "uvicorn", "app"):
        caplog.set_level(logging.DEBUG, logger=naam)
    for pad, params in vragen():
        r = await client.get(pad, params=params, headers=eva)
        assert gedaan(r), (pad, r.text)
    # Een mismatch in hetzelfde verzoek: alleen het node-id
    echt = engine.search_ranges

    async def en_dan_intrekken(session, user):
        roots = await echt(session, user)
        if user.id == w.users["hans"]:
            await _doe(settings, W.intrekken, w)
        return roots

    monkeypatch.setattr(engine, "search_ranges", en_dan_intrekken)
    r = await client.get("/api/v1/search", params={"q": W.TEKST_WOORD}, headers=hans)
    assert r.status_code == 200, r.text
    regels = [r.getMessage() for r in caplog.records if "search_access_mismatch" in r.getMessage()]
    assert regels == [f"search_access_mismatch {w.ids['doc']}"]
    assert W.TEKST_WOORD not in _alles(caplog)
    # Een databasefout bij een suggestie (bij zoeken: test_log_hiding.py)
    sync = get_engine(settings).sync_engine

    def op_slot(cursor, statement, parameters, context):
        if any(WOORD in str(p) for p in parameters or ()):
            raise sqlite3.OperationalError("database is locked")

    event.listen(sync, "do_execute", op_slot)
    try:
        with pytest.raises(DBAPIError) as fout:
            await client.get("/api/v1/search/suggest", params={"q": WOORD}, headers=eva)
    finally:
        event.remove(sync, "do_execute", op_slot)
    logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=fout.value)
    assert "database is locked" in _alles(caplog) or any(r.exc_info for r in caplog.records)
    for record in caplog.records:
        tekst = logging.Formatter().format(record)
        assert lekken(tekst) == [], (record.name, lekken(tekst))


def test_niets_in_de_toegangslog(tmp_path):
    """De echte server (python -m app.serve) met de wereld; de toegangslog van uvicorn naar een
    bestand. Elk pad staat erin, zonder query string (?***), en niets van de vragen."""
    from app.config import Settings
    from app.migrate import upgrade

    data = tmp_path / "data"
    data.mkdir()
    settings = Settings(data_dir=data, web_dist=data / "geen-web")
    import anyio

    async def zaai() -> None:
        await upgrade(settings)
        await W.bouw_async(settings)
        await get_engine(settings).dispose()

    anyio.run(zaai)
    zonder_2fa(data / "platform.db")
    app = App(data, tmp_path / "server.log")
    app.start()
    try:
        with httpx.Client(base_url=app.url, trust_env=False, timeout=30) as http:
            login = {"email": W.email("eva"), "password": W.WACHTWOORD}
            r = http.post("/api/v1/auth/login", json=login)
            assert r.status_code == 200, r.text
            kop_ = {"Authorization": f"Bearer {r.json()['access_token']}"}
            for pad, params in vragen():
                r = http.get(pad, params=params, headers=kop_)
                assert gedaan(r), (pad, r.text)
    finally:
        app.stop()
    log = (tmp_path / "server.log").read_text(errors="replace")
    assert lekken(log) == []
    for pad, _ in vragen():
        assert f'"GET {pad}?*** HTTP/1.1"' in log, pad
