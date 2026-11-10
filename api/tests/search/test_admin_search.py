"""De tab "Zoeken en foto's" op /beheer (bouwontwerp fase 6, Keuze 51 en §3.4; Z7):
GET en PATCH /admin/search, POST rebuild, GET failed, POST retry, de tellers per uur, de pauze,
search.progress en wat er bij het starten gebeurt (app/search/admin.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import analysis, db, events, jobs, photo_hash, photometa, render_meta
from app.analysis import Step
from app.extract import step as extract_step
from app.geo import load as geo_load
from app.models import Analysis, Blob, BlobHash, Job, Setting
from app.search import admin as search_admin
from app.search import engine as search_engine
from app.search import hooks, perhour
from app.search.engine import Round
from app.search.types import AdminOcr, AdminSearch
from tests.test_files import new_user

pytestmark = pytest.mark.anyio

ADMIN = "/api/v1/admin/search"
ROUTES = [
    ("GET", ADMIN, None),
    ("PATCH", ADMIN, {"paused": True}),
    ("POST", f"{ADMIN}/rebuild", {"what": "text"}),
    ("GET", f"{ADMIN}/failed", None),
    ("POST", f"{ADMIN}/retry", None),
]


def sha(i: int) -> str:
    return hashlib.sha256(f"inhoud-{i}".encode()).hexdigest()


@pytest.fixture(autouse=True)
def schoon():
    """Elke test begint zonder tellers en zonder pauze. De aanmelding van X1 (ocr, bij het
    importeren van app.ocr.step) staat na elke test weer zoals hij was, ook als een test hem
    vervangt of weghaalt."""
    perhour.reset()
    analysis.PAUSED.clear()
    x1 = hooks._admin.get("ocr")
    yield
    perhour.reset()
    analysis.PAUSED.clear()
    hooks._admin.pop("ocr", None)
    if x1 is not None:
        hooks._admin["ocr"] = x1
    for task in list(search_admin._tasks):
        task.cancel()
    search_admin._fts.clear()


def maker(settings):
    return async_sessionmaker(db.get_engine(settings), expire_on_commit=False)


async def add(settings, *rows) -> None:
    async with maker(settings)() as s:
        for row in rows:
            s.add(row)
            await s.flush()
        await s.commit()


async def leeg(settings) -> dict:
    return {
        "engine": "fts",
        "paused": False,
        "index": {"names_done": True, "text_done": 0, "text_total": 0, "failed": 0},
        "rebuild": None,
        "extract_in_sandbox": settings.media_enabled,
        "per_hour": {"deadline_b": 0, "deadline_c": 0, "busy": 0},
        "ocr": {"installed": False, "on": False, "pages_queued": 0},
        "places": {"loaded": False, "source": None, "date": None},
        "bench": None,
        "faces": None,
        "beeld": None,
        "check": None,
    }


# --- Alleen voor beheerders ----------------------------------------------------------------------


async def test_elke_route_alleen_voor_beheerders(client, admin):
    headers, _ = admin
    user = await new_user(client, headers, "bram@example.com")
    for method, path, body in ROUTES:
        r = await client.request(method, path, json=body, headers=user)
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["detail"]["code"] == "admin_only"
        r = await client.request(method, path, json=body)
        assert r.status_code == 401, (method, path)
    # De gewone gebruiker veranderde niets: geen pauze
    assert analysis.PAUSED == set()
    assert (await client.get(ADMIN, headers=headers)).json()["paused"] is False


# --- GET: de vorm en de stand --------------------------------------------------------------------


async def test_de_stand_heeft_precies_de_vorm_van_paragraaf_3_4(client, admin, settings):
    headers, _ = admin
    r = await client.get(ADMIN, headers=headers)
    assert r.status_code == 200
    assert r.json() == await leeg(settings)
    assert set(r.json()) == set(AdminSearch.model_fields)


async def test_tekst_n_van_m_en_mislukt(client, admin, settings):
    headers, _ = admin
    blobs = [Blob(sha256=sha(i), size=10, mime="application/pdf") for i in range(7)]
    states = ["done", "done", "wait", "busy", "skip", "failed", "none"]
    rows = [
        Analysis(blob_sha=sha(i), step="text", version=1, state=state, code=None)
        for i, state in enumerate(states)
    ]
    # Een mislukte embed (X3) telt hier niet; een mislukte ocr wel
    rows.append(Analysis(blob_sha=sha(0), step="embed", version=1, state="failed", code="x"))
    rows.append(Analysis(blob_sha=sha(1), step="ocr", version=1, state="failed", code="ocr_x"))
    await add(settings, *blobs, *rows)
    index = (await client.get(ADMIN, headers=headers)).json()["index"]
    # Zonder skip 6 inhouden; wait en busy staan open
    assert index == {"names_done": True, "text_done": 4, "text_total": 6, "failed": 2}


async def test_de_tellers_per_uur(client, admin, settings, monkeypatch):
    headers, _ = admin
    for kind, n in (("deadline_b", 3), ("deadline_c", 1), ("busy", 2)):
        for _ in range(n):
            perhour.note(kind)
    got = (await client.get(ADMIN, headers=headers)).json()["per_hour"]
    assert got == {"deadline_b": 3, "deadline_c": 1, "busy": 2}
    # Een uur later is alles weg; de lopende minuut telt mee
    start = perhour.clock()
    monkeypatch.setattr(perhour, "clock", lambda: start + 3600)
    perhour.note("busy")
    got = (await client.get(ADMIN, headers=headers)).json()["per_hour"]
    assert got == {"deadline_b": 0, "deadline_c": 0, "busy": 1}


async def test_ronde_b_en_c_aan_de_grens_en_search_busy_tellen(
    client, admin, settings, monkeypatch
):
    """De rondes van FtsEngine en de rij van limits.py tellen zelf (de vraag zelf nooit)."""
    headers, _ = admin

    async def altijd_de_grens(conn, sel, ms, step="fts"):
        return Round([], True)

    monkeypatch.setattr(search_engine, "run", altijd_de_grens)
    r = await client.get("/api/v1/search", params={"q": "factuur"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["partial"] is True
    monkeypatch.setattr(settings, "search_inflight", 0)
    monkeypatch.setattr(settings, "search_queue", 0)
    r = await client.get("/api/v1/search", params={"q": "offerte"}, headers=headers)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "search_busy"
    got = (await client.get(ADMIN, headers=headers)).json()["per_hour"]
    assert got == {"deadline_b": 1, "deadline_c": 1, "busy": 1}


async def test_plaatsnamen_geladen_bron_en_datum(client, admin, settings, monkeypatch):
    headers, _ = admin
    settings.places_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.places_db)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany(
        "INSERT INTO meta VALUES (?, ?)", [("schema", geo_load.SCHEMA), ("gebouwd", "2026-11-01")]
    )
    con.commit()
    con.close()
    got = (await client.get(ADMIN, headers=headers)).json()["places"]
    assert got == {"loaded": True, "source": search_admin.SOURCE, "date": "2026-11-01"}
    assert "GeoNames" in search_admin.SOURCE and geo_load.NE_VERSIE in search_admin.SOURCE
    monkeypatch.setattr(settings, "places", "uit")
    got = (await client.get(ADMIN, headers=headers)).json()["places"]
    assert got == {"loaded": False, "source": None, "date": None}


async def test_de_uitkomst_van_de_zoekbench(client, admin, settings):
    headers, _ = admin
    path = settings.data_dir / search_admin.BENCH
    path.parent.mkdir(parents=True, exist_ok=True)
    uit = {"zoeken": {"eigenaar": {"p95": 12.5, "p50": 3}, "alles": {"p95": 80.1}}}
    path.write_text(json.dumps(uit))
    got = (await client.get(ADMIN, headers=headers)).json()["bench"]
    at = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    assert got == {"at": at, "p95_owner_ms": 12.5, "p95_all_ms": 80.1}
    for kapot in ("{", '{"zoeken": {}}', '{"zoeken": {"eigenaar": {"p95": "x"}, "alles": {}}}'):
        path.write_text(kapot)
        assert (await client.get(ADMIN, headers=headers)).json()["bench"] is None


async def test_een_herbouw_van_de_index_met_voortgang(client, admin, settings):
    headers, _ = admin
    async with db.get_engine(settings).begin() as conn:
        await conn.exec_driver_sql(
            "UPDATE search_state SET cursor = 0, started_at = '2026-11-09T10:00:00+00:00', "
            "what = 'names' WHERE id = 1"
        )
    got = (await client.get(ADMIN, headers=headers)).json()
    assert got["engine"] == "like"  # LikeFallback zolang de herbouw loopt
    assert got["index"]["names_done"] is False
    assert got["rebuild"] == {
        "what": "names",
        "started_at": "2026-11-09T10:00:00+00:00",
        "done": 0.0,  # cursor 0: nog geen stuk klaar
    }
    r = await client.post(f"{ADMIN}/rebuild", json={"what": "text"}, headers=headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "search_rebuild_running"


# --- PATCH: pauze en de schakelaars van X1, X2 en X3 ---------------------------------------------


async def test_pauze_zet_de_rijen_index_en_ocr_stil_en_blijft_na_een_herstart(
    client, admin, settings
):
    headers, _ = admin
    r = await client.patch(ADMIN, json={"paused": True}, headers=headers)
    assert r.status_code == 200
    assert r.json()["paused"] is True
    assert analysis.PAUSED == {"index", "ocr"}
    with pytest.raises(jobs.Defer):
        await analysis.pump(settings, "index")
    with pytest.raises(jobs.Defer):
        await analysis.pump(settings, "ocr")
    # Na een herstart (on_start) staat hij er weer
    analysis.PAUSED.clear()
    await search_admin.on_start(settings)
    assert analysis.PAUSED == {"index", "ocr"}
    r = await client.patch(ADMIN, json={"paused": False}, headers=headers)
    assert r.json()["paused"] is False
    assert analysis.PAUSED == set()
    assert await analysis.pump(settings, "index") == 0


async def queued_pumps(settings) -> dict[str, list[datetime]]:
    """De pompen van de rijen index en ocr die klaarstaan, met hun run_after (in UTC)."""
    pompen = [analysis.pump_type(lane) for lane in search_admin.LANES]
    async with maker(settings)() as s:
        rows = await s.execute(
            sa.select(Job.type, Job.run_after).where(Job.type.in_(pompen), Job.status == "queued")
        )
        found: dict[str, list[datetime]] = {pomp: [] for pomp in pompen}
        for kind, at in rows.all():
            found[kind].append(at if at.tzinfo else at.replace(tzinfo=UTC))
    return found


def lezende_wekkers(settings, monkeypatch) -> list[tuple[str, datetime]]:
    """De workers van de rijen index en ocr (jobs._wakers) vervangen door wekkers die lezen wat
    een worker dan zou zien: de run_after van de pomp die klaarstaat, zoals hij gecommit is. Een
    seintje vóór de commit ziet nog de oude run_after; dan pakt de Runner de pomp pas bij zijn
    rondje (jobs.POLL, 5 s) op. Geeft de lijst (rij, run_after) die bij elk seintje groeit."""
    bestand = settings.db_url.removeprefix("sqlite+aiosqlite:///")
    gewekt: list[tuple[str, datetime]] = []

    def waker(lane: str):
        def wake() -> None:
            con = sqlite3.connect(bestand)
            try:
                rows = con.execute(
                    "SELECT run_after FROM job WHERE type = ? AND status = 'queued'",
                    (analysis.pump_type(lane),),
                ).fetchall()
            finally:
                con.close()
            for (at,) in rows:
                found = datetime.fromisoformat(at)
                gewekt.append((lane, found if found.tzinfo else found.replace(tzinfo=UTC)))

        return wake

    monkeypatch.setattr(jobs, "_wakers", {waker(lane): lane for lane in search_admin.LANES})
    return gewekt


async def test_hervatten_wekt_de_pompen_van_index_en_ocr(client, admin, settings, monkeypatch):
    """Een pomp die tijdens de pauze aan de beurt kwam, week (jobs.Defer) en staat DEFER_S later.
    "Hervatten" zet hem op nu, voor de rij index en de rij ocr, zoals "Mislukte opnieuw" dat doet;
    er komt geen tweede pomp bij. De pomp wordt pas gewekt als de pauze uit het geheugen is (anders
    kan hij nog wijken). Eerst las de index na hervatten pas een halve minuut later weer (z7.md,
    Open na de samenvoeging; I1, S1)."""
    from app.ocr import step as ocr_step

    # zoals main.py; een eerdere test kan analysis.reset() gedaan hebben
    extract_step.register()
    ocr_step.register()
    headers, _ = admin
    r = await client.patch(ADMIN, json={"paused": True}, headers=headers)
    assert r.status_code == 200
    # Werk komt binnen: de pomp staat op nu, draait, en wijkt voor de pauze
    async with maker(settings)() as s:
        for lane in search_admin.LANES:
            await analysis._wake(s, lane, datetime.now(UTC))
        await s.commit()
    for lane in search_admin.LANES:
        assert await jobs.run_pending(settings, lane=lane) == 1, lane
    voor = datetime.now(UTC)
    for pomp, at in (await queued_pumps(settings)).items():
        assert len(at) == 1 and at[0] > voor + timedelta(seconds=analysis.DEFER_S - 5), (pomp, at)

    gezien: list[tuple[str, set[str]]] = []
    echt = analysis._wake

    async def wake(db, lane, at):
        gezien.append((lane, set(analysis.PAUSED)))
        await echt(db, lane, at)

    gewekt = lezende_wekkers(settings, monkeypatch)
    monkeypatch.setattr(analysis, "_wake", wake)
    r = await client.patch(ADMIN, json={"paused": False}, headers=headers)
    assert r.status_code == 200 and r.json()["paused"] is False
    na = datetime.now(UTC)
    for pomp, at in (await queued_pumps(settings)).items():
        assert len(at) == 1 and at[0] <= na, (pomp, at, na)
    assert gezien == [("index", set()), ("ocr", set())]
    # Na de commit gewekt: de workers zien de pomp op nu, en pakken hem meteen op
    for lane in search_admin.LANES:
        assert any(wie == lane and at <= na for wie, at in gewekt), (lane, gewekt)
    # En de pomp van index loopt nu echt (zonder werk: klaar, geen nieuwe job)
    assert await jobs.run_pending(settings, lane="index") == 1
    assert (await queued_pumps(settings))[analysis.pump_type("index")] == []
    # Pauzeren wekt niets
    gezien.clear()
    r = await client.patch(ADMIN, json={"paused": True}, headers=headers)
    assert r.status_code == 200 and gezien == []


async def test_zonder_x1_x2_en_x3_geeft_hun_schakelaar_501_en_verandert_niets(
    client, admin, monkeypatch
):
    """Het contract van search.hooks zonder aanmelding. X1 meldt zich sinds golf 4 bij het
    importeren aan; hier is hij er even niet."""
    monkeypatch.delitem(hooks._admin, "ocr", raising=False)
    headers, _ = admin
    for field, package in (("ocr", "X1"), ("faces_allowed", "X2"), ("beeld", "X3")):
        r = await client.patch(ADMIN, json={"paused": True, field: True}, headers=headers)
        assert r.status_code == 501, field
        assert r.json()["detail"] == {
            "message": f"Nog niet gebouwd ({package})",
            "code": "not_built",
            "params": {"package": package},
        }
    assert analysis.PAUSED == set()
    assert (await client.get(ADMIN, headers=headers)).json()["paused"] is False
    for what, package in (("ocr", "X1"), ("faces", "X2"), ("beeld", "X3")):
        r = await client.post(f"{ADMIN}/rebuild", json={"what": what}, headers=headers)
        assert r.status_code == 501, what
        assert r.json()["detail"]["params"] == {"package": package}
    r = await client.patch(ADMIN, json={"onbekend": True}, headers=headers)
    assert r.status_code == 422


async def test_x1_meldt_zich_aan_met_het_blok_ocr(client, admin):
    """Het contract voor X1: register_admin("ocr", AdminPart); Z7 roept het aan."""
    headers, _ = admin
    gezien: list = []

    async def block(session):
        return AdminOcr(installed=True, on=bool(gezien), pages_queued=7)

    async def switch(session, value):
        gezien.append(("switch", value))

    async def rebuild(session):
        gezien.append(("rebuild",))

    hooks.register_admin("ocr", hooks.AdminPart(block=block, switch=switch, rebuild=rebuild))
    got = (await client.get(ADMIN, headers=headers)).json()["ocr"]
    assert got == {"installed": True, "on": False, "pages_queued": 7}
    r = await client.patch(ADMIN, json={"ocr": True}, headers=headers)
    assert r.status_code == 200
    assert r.json()["ocr"] == {"installed": True, "on": True, "pages_queued": 7}
    r = await client.post(f"{ADMIN}/rebuild", json={"what": "ocr"}, headers=headers)
    assert r.status_code == 202
    assert gezien == [("switch", True), ("rebuild",)]


async def test_na_de_samenvoeging_gaan_schakelaar_en_herbouw_van_ocr_naar_x1(
    client, admin, settings
):
    """Golf 4: X1 (app.ocr.step) meldt zich zelf aan. Het blok telt de open rijen ocr, de
    schakelaar staat in Setting "ocr" en "Alles opnieuw herkennen" zet een mislukte rij terug in
    de rij. Zonder worker (media uit) is OCR niet geïnstalleerd en dus niet aan."""
    from app.ocr import step as ocr_step

    ocr_step.register()  # zoals main.py; een eerdere test kan search.hooks.reset() gedaan hebben
    part = hooks._admin["ocr"]
    assert (part.block, part.switch, part.rebuild) == (
        ocr_step.admin_block,
        ocr_step.admin_switch,
        ocr_step.admin_rebuild,
    )
    headers, _ = admin
    blobs = [Blob(sha256=sha(i), size=10, mime="application/pdf") for i in range(3)]
    rows = [
        Analysis(blob_sha=sha(0), step="ocr", version=1, state="wait", code=None),
        Analysis(blob_sha=sha(1), step="ocr", version=1, state="failed", code="ocr_x"),
        Analysis(blob_sha=sha(2), step="ocr", version=1, state="skip", code=None),
    ]
    await add(settings, *blobs, *rows)
    got = (await client.get(ADMIN, headers=headers)).json()["ocr"]
    assert got == {"installed": False, "on": False, "pages_queued": 1}
    r = await client.patch(ADMIN, json={"ocr": False}, headers=headers)
    assert r.status_code == 200
    async with maker(settings)() as s:
        assert (await s.get(Setting, ocr_step.SETTING)).value == {"on": False}
    r = await client.patch(ADMIN, json={"ocr": True}, headers=headers)
    assert r.status_code == 200
    async with maker(settings)() as s:
        assert (await s.get(Setting, ocr_step.SETTING)).value == {"on": True}
        pomp = analysis.pump_type(ocr_step.LANE)
        assert await s.scalar(sa.select(sa.func.count()).where(Job.type == pomp)) == 1
    r = await client.post(f"{ADMIN}/rebuild", json={"what": "ocr"}, headers=headers)
    assert r.status_code == 202
    async with maker(settings)() as s:
        found = await s.execute(
            sa.select(Analysis.blob_sha, Analysis.state).where(Analysis.step == "ocr")
        )
        states = {sha_: state for sha_, state in found.all()}
    assert states == {sha(0): "wait", sha(1): "wait", sha(2): "skip"}
    got = (await client.get(ADMIN, headers=headers)).json()["ocr"]
    assert got == {"installed": False, "on": False, "pages_queued": 2}


async def test_ocr_weer_aan_haalt_de_pomp_naar_voren(client, admin, settings):
    """Stond OCR uit, dan week de pomp van de rij ocr 300 s (jobs.Defer; zijn job staat dan op
    later). "OCR aanzetten" op /beheer zet die job op nu, zodat de scans meteen gelezen worden en
    niet pas na vijf minuten; er komt geen tweede pomp bij."""
    from app.ocr import step as ocr_step

    ocr_step.register()  # zoals main.py; een eerdere test kan search.hooks.reset() gedaan hebben
    headers, _ = admin
    pomp = analysis.pump_type(ocr_step.LANE)
    later = datetime.now(UTC) + timedelta(seconds=ocr_step.OFF_DEFER_S)
    async with maker(settings)() as s:
        await jobs.chain(s, pomp, {}, key=pomp, run_after=later)
        await s.commit()
    voor = datetime.now(UTC)
    r = await client.patch(ADMIN, json={"ocr": True}, headers=headers)
    assert r.status_code == 200
    async with maker(settings)() as s:
        rows = (
            await s.execute(
                sa.select(Job.run_after).where(Job.type == pomp, Job.status == "queued")
            )
        ).all()
    assert len(rows) == 1
    gepland = rows[0][0] if rows[0][0].tzinfo else rows[0][0].replace(tzinfo=UTC)
    assert gepland <= voor + timedelta(seconds=5), (gepland, voor)


# --- POST rebuild --------------------------------------------------------------------------------


async def test_de_index_herbouwen_loopt_op_de_achtergrond(client, admin, settings):
    headers, _ = admin
    r = await client.post(f"{ADMIN}/rebuild", json={"what": "names"}, headers=headers)
    assert r.status_code == 202
    assert r.content in (b"", b"null")
    await asyncio.gather(*search_admin._fts.values())
    async with db.get_engine(settings).connect() as conn:
        row = (await conn.exec_driver_sql("SELECT cursor, what FROM search_state")).first()
    assert row == (None, "names")
    got = (await client.get(ADMIN, headers=headers)).json()
    assert got["rebuild"] is None and got["engine"] == "fts"


async def test_na_de_202_ziet_een_get_de_herbouw_al(client, admin, settings, monkeypatch):
    """De web-app laadt direct na de 202 opnieuw: dan staat de herbouw er al (cursor 0), ook
    als de taak nog niet liep. De taak gaat daarna verder vanaf die cursor."""
    headers, _ = admin
    gestart: list[str] = []
    monkeypatch.setattr(search_admin, "start_fts", lambda _s, what: gestart.append(what))
    r = await client.post(f"{ADMIN}/rebuild", json={"what": "text"}, headers=headers)
    assert r.status_code == 202 and gestart == ["text"]
    got = (await client.get(ADMIN, headers=headers)).json()
    assert got["engine"] == "like" and got["index"]["names_done"] is False
    assert got["rebuild"]["what"] == "text" and got["rebuild"]["done"] == 0.0
    r = await client.post(f"{ADMIN}/rebuild", json={"what": "names"}, headers=headers)
    assert r.status_code == 409
    # De taak zelf maakt hem af
    await search_admin._run_fts(settings, "text")
    got = (await client.get(ADMIN, headers=headers)).json()
    assert got["rebuild"] is None and got["engine"] == "fts"


async def test_de_pauze_houdt_een_herbouw_tussen_de_stukken_vast(settings, monkeypatch):
    """De herbouw zelf (rebuild.rebuild) heeft een eigen test; hier de wacht tussen de stukken."""
    gedaan: list[str] = []

    async def herbouw(engine, *, what, after_chunk, **kwargs):
        gedaan.append(f"stuk 1 ({what})")
        await after_chunk(2000)
        gedaan.append("stuk 2")
        return [1.0, 1.0]

    monkeypatch.setattr(search_admin.rebuild, "rebuild", herbouw)
    analysis.PAUSED.update(search_admin.LANES)
    taak = asyncio.create_task(search_admin._run_fts(settings, "text"))
    await asyncio.sleep(1.5)
    assert gedaan == ["stuk 1 (text)"] and not taak.done()
    analysis.PAUSED.clear()
    await asyncio.wait_for(taak, 5)
    assert gedaan == ["stuk 1 (text)", "stuk 2"]


async def foto(settings, i: int, *, version: int = photo_hash.VERSION) -> None:
    await add(
        settings,
        Blob(sha256=sha(i), size=10, mime="image/jpeg", meta_version=render_meta.VERSION),
        BlobHash(blob_sha=sha(i), state="ok", phash=1, h0=0, h1=0, h2=0, h3=0, version=version),
    )


async def test_hash_opnieuw_met_voortgang_en_409(client, admin, settings):
    headers, _ = admin
    for i in range(4):
        await foto(settings, i)
    r = await client.post(f"{ADMIN}/rebuild", json={"what": "hash"}, headers=headers)
    assert r.status_code == 202
    async with maker(settings)() as s:
        versions = (await s.execute(sa.select(BlobHash.version))).scalars().all()
        assert versions == [photo_hash.VERSION - 1] * 4
        keten = await s.scalar(sa.select(sa.func.count()).where(Job.key == photometa.KEY))
        assert keten == 1
        mark = (await s.get(Setting, search_admin.KEY)).value["rebuild"]
    rebuild = (await client.get(ADMIN, headers=headers)).json()["rebuild"]
    assert rebuild == {"what": "hash", "started_at": mark["started_at"], "done": 0.0}
    for what in ("hash", "geo"):
        r = await client.post(f"{ADMIN}/rebuild", json={"what": what}, headers=headers)
        assert r.status_code == 409, what
        assert r.json()["detail"]["code"] == "search_rebuild_running"
    # photometa rekende er drie opnieuw: 75%
    async with maker(settings)() as s:
        await s.execute(
            sa.update(BlobHash)
            .where(BlobHash.blob_sha.in_([sha(0), sha(1), sha(2)]))
            .values(version=photo_hash.VERSION)
        )
        await s.commit()
    assert (await client.get(ADMIN, headers=headers)).json()["rebuild"]["done"] == 0.75
    # Allemaal: geen herbouw meer, en een nieuwe mag weer
    async with maker(settings)() as s:
        await s.execute(sa.update(BlobHash).values(version=photo_hash.VERSION))
        await s.commit()
    assert (await client.get(ADMIN, headers=headers)).json()["rebuild"] is None
    r = await client.post(f"{ADMIN}/rebuild", json={"what": "geo"}, headers=headers)
    assert r.status_code == 202


async def test_hash_loopt_ook_als_de_index_tegelijk_herbouwd_wordt(client, admin, settings):
    """GET toont de herbouw van de index eerst; een tweede herbouw van photometa blijft toch 409."""
    headers, _ = admin
    for i in range(2):
        await foto(settings, i)
    r = await client.post(f"{ADMIN}/rebuild", json={"what": "hash"}, headers=headers)
    assert r.status_code == 202
    async with db.get_engine(settings).begin() as conn:
        await conn.exec_driver_sql(
            "UPDATE search_state SET cursor = 0, started_at = '2026-11-09T10:00:00+00:00', "
            "what = 'text' WHERE id = 1"
        )
    assert (await client.get(ADMIN, headers=headers)).json()["rebuild"]["what"] == "text"
    for what in ("hash", "geo"):
        r = await client.post(f"{ADMIN}/rebuild", json={"what": what}, headers=headers)
        assert r.status_code == 409, what
        assert r.json()["detail"]["code"] == "search_rebuild_running"


async def test_geo_leest_plek_camera_en_datum_opnieuw(client, admin, settings):
    headers, _ = admin
    await foto(settings, 1)
    await add(settings, Blob(sha256=sha(2), size=5, mime="application/pdf", meta_version=2))
    r = await client.post(f"{ADMIN}/rebuild", json={"what": "geo"}, headers=headers)
    assert r.status_code == 202
    async with maker(settings)() as s:
        got = dict((await s.execute(sa.select(Blob.sha256, Blob.meta_version))).all())
    # Alleen foto's en video's; een PDF blijft
    assert got == {sha(1): render_meta.VERSION - 1, sha(2): 2}
    rebuild = (await client.get(ADMIN, headers=headers)).json()["rebuild"]
    assert rebuild["what"] == "geo" and rebuild["done"] == 0.0


# --- GET failed en POST retry --------------------------------------------------------------------


async def test_mislukt_per_pagina_de_nieuwste_eerst(client, admin, settings, monkeypatch):
    headers, _ = admin
    monkeypatch.setattr(search_admin, "PAGE", 2)
    t0 = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    await add(settings, *[Blob(sha256=sha(i), size=1, mime="application/pdf") for i in range(4)])
    await add(
        settings,
        Analysis(
            blob_sha=sha(0), step="text", version=1, state="failed", code="pdf_bad", updated_at=t0
        ),
        Analysis(
            blob_sha=sha(1),
            step="ocr",
            version=1,
            state="failed",
            code=None,
            updated_at=t0 + timedelta(minutes=2),
        ),
        Analysis(
            blob_sha=sha(2),
            step="text",
            version=1,
            state="failed",
            code="too_big",
            updated_at=t0 + timedelta(minutes=1),
        ),
        Analysis(blob_sha=sha(3), step="embed", version=1, state="failed", code="x", updated_at=t0),
        Analysis(blob_sha=sha(3), step="text", version=1, state="done", updated_at=t0),
    )
    first = (await client.get(f"{ADMIN}/failed", headers=headers)).json()
    at1, at2 = (t0 + timedelta(minutes=1)).isoformat(), (t0 + timedelta(minutes=2)).isoformat()
    assert first["items"] == [
        {"sha": sha(1), "step": "ocr", "code": "failed", "at": at2},
        {"sha": sha(2), "step": "text", "code": "too_big", "at": at1},
    ]
    assert first["cursor"]
    second = (
        await client.get(f"{ADMIN}/failed", params={"cursor": first["cursor"]}, headers=headers)
    ).json()
    assert second == {
        "items": [{"sha": sha(0), "step": "text", "code": "pdf_bad", "at": t0.isoformat()}],
        "cursor": None,
    }
    for kapot in ("x", "e30", "W10", "WyJhIiwiYiIsImMiXQ"):
        r = await client.get(f"{ADMIN}/failed", params={"cursor": kapot}, headers=headers)
        assert r.status_code == 422, kapot
        assert r.json()["detail"]["code"] == "invalid_cursor"


async def test_mislukte_opnieuw_in_de_rij(client, admin, settings):
    # zoals main.py; een eerdere test kan analysis.reset() gedaan hebben (test_contracts.py)
    extract_step.register()
    headers, _ = admin
    await add(settings, *[Blob(sha256=sha(i), size=1, mime="application/pdf") for i in range(3)])
    await add(
        settings,
        Analysis(blob_sha=sha(0), step="text", version=1, state="failed", tries=3, code="pdf_bad"),
        Analysis(blob_sha=sha(1), step="text", version=1, state="done", tries=1),
        Analysis(blob_sha=sha(2), step="embed", version=1, state="failed", tries=3, code="x"),
    )
    r = await client.post(f"{ADMIN}/retry", headers=headers)
    assert r.status_code == 202
    async with maker(settings)() as s:
        rows = {
            (row.blob_sha, row.step): (row.state, row.tries, row.code)
            for row in (await s.execute(sa.select(Analysis))).scalars()
        }
        pomp = await s.scalar(
            sa.select(sa.func.count()).where(Job.key == analysis.pump_type("index"))
        )
    assert rows == {
        (sha(0), "text"): ("wait", 0, None),
        (sha(1), "text"): ("done", 1, None),
        (sha(2), "embed"): ("failed", 3, "x"),  # van X3, niet van deze knop
    }
    assert pomp == 1
    assert (await client.get(ADMIN, headers=headers)).json()["index"]["failed"] == 0


async def test_een_mislukte_ocr_gaat_terug_in_de_rij_van_ocr(client, admin, settings):
    """Golf 4: "Mislukte opnieuw" zet ook een mislukte ocr (X1) terug op wait, en wekt de pomp
    van de rij ocr (niet die van index). De lijst van mislukte toont hem met stap ocr."""
    from app.ocr import step as ocr_step

    # zoals main.py; een eerdere test kan analysis.reset() of search.hooks.reset() gedaan hebben
    ocr_step.register()
    headers, _ = admin
    await add(settings, Blob(sha256=sha(0), size=1, mime="application/pdf"))
    await add(
        settings,
        Analysis(blob_sha=sha(0), step="ocr", version=1, state="failed", tries=3, code="ocr_x"),
    )
    items = (await client.get(f"{ADMIN}/failed", headers=headers)).json()["items"]
    assert [(i["sha"], i["step"], i["code"]) for i in items] == [(sha(0), "ocr", "ocr_x")]
    r = await client.post(f"{ADMIN}/retry", headers=headers)
    assert r.status_code == 202
    async with maker(settings)() as s:
        row = (await s.execute(sa.select(Analysis))).scalars().one()
        pompen = {
            kind
            for (kind,) in await s.execute(sa.select(Job.type).where(Job.status == "queued"))
            if kind.startswith("analysis.")
        }
    assert (row.step, row.state, row.tries, row.code) == ("ocr", "wait", 0, None)
    assert pompen == {analysis.pump_type(ocr_step.LANE)}
    got = (await client.get(ADMIN, headers=headers)).json()
    assert got["index"]["failed"] == 0 and got["ocr"]["pages_queued"] == 1


async def test_mislukte_opnieuw_wekt_de_pompen_na_de_commit(client, admin, settings, monkeypatch):
    """Staat de pomp van een rij later (een pomp die week, DEFER_S), dan zet "Mislukte opnieuw"
    hem op nu, en wekt de workers van index en ocr pas na de commit: zo zien ze de pomp op nu en
    pakken ze hem meteen op, niet pas bij hun rondje (jobs.POLL, 5 s). Net als "Hervatten"
    (I1, C1)."""
    from app.ocr import step as ocr_step

    # zoals main.py; een eerdere test kan analysis.reset() gedaan hebben
    extract_step.register()
    ocr_step.register()
    headers, _ = admin
    await add(settings, *[Blob(sha256=sha(i), size=1, mime="application/pdf") for i in range(2)])
    await add(
        settings,
        Analysis(blob_sha=sha(0), step="text", version=1, state="failed", tries=3, code="pdf_bad"),
        Analysis(blob_sha=sha(1), step="ocr", version=1, state="failed", tries=3, code="ocr_x"),
    )
    later = datetime.now(UTC) + timedelta(seconds=analysis.DEFER_S)
    async with maker(settings)() as s:
        for lane in search_admin.LANES:
            await analysis._wake(s, lane, later)
        await s.commit()
    voor = datetime.now(UTC)
    for pomp, at in (await queued_pumps(settings)).items():
        assert len(at) == 1 and at[0] > voor + timedelta(seconds=analysis.DEFER_S - 5), (pomp, at)

    gewekt = lezende_wekkers(settings, monkeypatch)
    r = await client.post(f"{ADMIN}/retry", headers=headers)
    assert r.status_code == 202
    na = datetime.now(UTC)
    for pomp, at in (await queued_pumps(settings)).items():
        assert len(at) == 1 and at[0] <= na, (pomp, at, na)
    for lane in search_admin.LANES:
        assert any(wie == lane and at <= na for wie, at in gewekt), (lane, gewekt)


# --- search.progress en het starten --------------------------------------------------------------


async def test_search_progress_alleen_naar_beheerders(client, admin, settings):
    headers, _ = admin
    user = await new_user(client, headers, "eva@example.com")
    ids = {}
    for naam, h in (("admin", headers), ("eva", user)):
        ids[naam] = __import__("uuid").UUID(
            (await client.get("/api/v1/auth/me", headers=h)).json()["id"]
        )
    assert await search_admin.tick(settings) == 0  # niemand luistert
    with (
        events.hub.subscribe(ids["admin"]) as beheer,
        events.hub.subscribe(ids["eva"]) as eva,
    ):
        assert await search_admin.tick(settings) == 0  # niets bezig
        for i in range(2):
            await foto(settings, i, version=photo_hash.VERSION - 1)
        async with maker(settings)() as s:
            await search_admin._store(s, rebuild={"what": "hash", "started_at": "t"})
            await s.commit()
        assert await search_admin.tick(settings) == 1
        assert beheer.queue.get_nowait() == {
            "type": "search.progress",
            "data": {"what": "hash", "done": 0.0},
        }
        assert eva.queue.empty()


async def test_bij_het_starten(client, admin, settings):
    """De pauze terug, een klare herbouw weg, de eerste pHash als herbouw "hash", en een
    onderbroken herbouw van de index gaat verder."""
    # zoals main.py; een eerdere test kan analysis.reset() gedaan hebben (test_contracts.py)
    extract_step.register()
    async with maker(settings)() as s:
        await search_admin._store(
            s, paused=True, rebuild={"what": "geo", "started_at": "2026-11-01T00:00:00+00:00"}
        )
        await s.commit()
    await search_admin.on_start(settings)
    assert analysis.PAUSED == {"index", "ocr"}
    async with maker(settings)() as s:
        assert await search_admin.stored(s) == {"paused": True, "rebuild": None}
    for task in list(search_admin._tasks):
        task.cancel()
    # 50 foto's zonder pHash (na een update): de herbouw "hash" is te zien
    await add(settings, *[Blob(sha256=sha(i), size=1, mime="image/png") for i in range(50)])
    async with db.get_engine(settings).begin() as conn:
        await conn.exec_driver_sql("UPDATE search_state SET cursor = 0, what = 'text' WHERE id = 1")
    await search_admin.on_start(settings)
    analysis.PAUSED.clear()  # anders wacht de herbouw tussen de stukken
    await asyncio.wait_for(asyncio.gather(*search_admin._fts.values()), 10)
    async with db.get_engine(settings).connect() as conn:
        assert (await conn.exec_driver_sql("SELECT cursor FROM search_state")).scalar() is None
    async with maker(settings)() as s:
        mark = (await search_admin.stored(s))["rebuild"]
        assert mark["what"] == "hash"
        assert (await search_admin.progress(s))[-1].model_dump() == {"what": "hash", "done": 0.0}
    assert Step.TEXT in analysis.registered()
