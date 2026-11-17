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
import uuid

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from app.db import get_engine
from app.people import search as people_search
from app.search import engine, filters_photo
from tests.beeld.conftest import beeld, mensen  # noqa: F401 - de fixtures van tests/beeld
from tests.search import motoren
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


@pytest.fixture(autouse=True)
def _filters():
    """De filters person, place, country en camera staan in het register: een test eerder in de
    run kan filters.reset() gedaan hebben (test_contracts.py), en dan gaf de vraag met person een
    501 in plaats van een antwoord, afhankelijk van de volgorde."""
    people_search.register()
    filters_photo.register()


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
    """Elke vraag is gewoon gelukt: alle routes zijn er nu (de kaart, de plaats en het land sinds
    Z3), dus geen 501 meer."""
    return r.status_code == 200


def lekken(tekst: str) -> list[str]:
    return [g for g in GEHEIM if g in tekst]


def _alles(caplog) -> str:
    form = logging.Formatter("%(name)s %(levelname)s %(message)s")
    return "\n".join(form.format(r) for r in caplog.records)


@motoren.needs_fts
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
    fout_van_de_driver = sync.dialect.dbapi.OperationalError

    def op_slot(cursor, statement, parameters, context):
        if any(WOORD in str(p) for p in parameters or ()):
            raise fout_van_de_driver("database is locked")

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


async def test_niets_in_de_log_met_de_wortel_op_debug(client, settings, caplog):
    """Ook als de wortel-logger op DEBUG staat (een ontwikkelaar, een debugger): aiosqlite logt
    op DEBUG elke SQL met zijn parameters, dus de zoekvraag. main.py zet die logger daarom vast
    op INFO (I1, S5; Keuze 13). Geen enkele regel van welke logger dan ook bevat iets van de
    vragen."""
    await W.bouw_async(settings)
    eva = await kop(client, "eva")
    caplog.set_level(logging.DEBUG)
    assert logging.getLogger().isEnabledFor(logging.DEBUG)
    for pad, params in vragen():
        r = await client.get(pad, params=params, headers=eva)
        assert gedaan(r), (pad, r.text)
    assert caplog.records  # er werd wel gelogd
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


# --- zoeken op beeld (X3b; K1-16) ---------------------------------------------------------------


async def test_niets_in_de_log_met_zoeken_op_beeld(client, mensen, beeld, caplog, capfd):  # noqa: F811
    """mode=image met het geheime woord: gewoon, met filters, meer zoals deze, en de drie
    fallbacks (te laat, de container weg, een fout van rank). De vraag gaat wel naar de
    container (RankArgs.text), maar staat in geen enkele regel van de log, ook niet van de
    nep-worker, en niet op stdout of stderr."""
    from app.analyse import monitor
    from app.analyse.wire import Code, Op
    from tests.analyse.fake_worker import FakeFail
    from tests.beeld import nep

    (eva, _), _ = mensen
    fake, settings = beeld
    caplog.set_level(logging.DEBUG)
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    rood = await nep.foto(client, eva, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    fake.ranks.clear()
    for params in (
        {"q": WOORD},
        {"q": WOORD, "from": DAG, "type": "image"},
        {"q": WOORD, "offset": 1, "limit": 1},
        {"like": rood["id"]},
    ):
        r = await nep.zoek(client, eva, **params)
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "image"
    assert WOORD in [a.text for a in fake.ranks]
    # te laat: gewoon zoeken met dezelfde vraag
    fake.rank_delay = 2.0
    from app.config import get_settings
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"beeld_deadline_ms": 50}
    )
    try:
        r = await nep.zoek(client, eva, WOORD + "x")
    finally:
        app.dependency_overrides[get_settings] = lambda: settings
        fake.rank_delay = 0.0
    assert (r.status_code, r.json()["fallback"]) == (200, "slow"), r.text

    # een fout van rank (BAD_REQUEST met een tekst van de container)
    async def stuk(req):
        raise FakeFail(Code.BAD_REQUEST, "kapot")

    fake.fake.on(Op.RANK, stuk)
    r = await nep.zoek(client, eva, WOORD + "y")
    assert r.status_code == 200, r.text
    fake.install()
    # de container weg
    monitor._status = monitor.AnalyseStatus("down", "gone")
    r = await nep.zoek(client, eva, WOORD + "z")
    assert (r.status_code, r.json()["fallback"]) == (200, "down"), r.text
    assert caplog.records
    for record in caplog.records:
        tekst = logging.Formatter().format(record)
        assert WOORD not in tekst, record.name
    uit, err = capfd.readouterr()
    assert WOORD not in uit and WOORD not in err
