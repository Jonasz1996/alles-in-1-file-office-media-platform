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
    """Elke test begint zonder tellers, zonder pauze en zonder aanmelding van X1."""
    perhour.reset()
    analysis.PAUSED.clear()
    yield
    perhour.reset()
    analysis.PAUSED.clear()
    hooks._admin.pop("ocr", None)
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


async def test_zonder_x1_x2_en_x3_geeft_hun_schakelaar_501_en_verandert_niets(client, admin):
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
