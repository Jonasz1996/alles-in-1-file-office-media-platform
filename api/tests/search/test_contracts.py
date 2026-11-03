"""De vaste Python-vormen van fase 6 (bouwontwerp §3.3) die Z0 neerzet: de typen van zoeken, de
keuze van de zoekmachine, de registers (filters, haken, stappen), write_text() als enige
schrijver van blob_text, de foto-instellingen, en de stubs die tot hun pakket niets doen."""

from __future__ import annotations

import dataclasses
import logging
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import analysis, prefs
from app.config import Settings
from app.db import get_engine
from app.extract.result import Extracted
from app.geo.types import Location, Place
from app.models import BlobText, PhotoPref
from app.search import deadline, engine, filters, hooks, limits, query
from app.search.engine import ConnInfo, read_conn_info, select_engine
from app.search.types import SearchPage, SearchParams, SearchRule, Suggestions
from tests.search.migratie import (
    SHA_TXT,
    check_fts,
    check_tree,
    fill_0009,
    migrate,
    settings_at,
    words,
)

pytestmark = pytest.mark.anyio


def sessions(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


@pytest.fixture(autouse=True)
def _schone_registers():
    filters.reset()
    hooks.reset()
    analysis.reset()
    engine.forget()
    yield
    filters.reset()
    hooks.reset()
    analysis.reset()
    engine.forget()


# --- typen ---


def test_searchparams_volgt_het_contract():
    p = SearchParams.model_validate({"q": "jaarverslag", "in": str(uuid.uuid4())})
    assert p.in_ is not None and p.limit == 30 and p.offset == 0 and p.after is None
    for bad in ({"limit": 0}, {"limit": 51}, {"offset": 1001}, {"q": "x" * 201}):
        with pytest.raises(ValidationError):
            SearchParams.model_validate(bad)
    with pytest.raises(ValidationError):
        SearchParams.model_validate({"types": ["archive"]})  # geen soort van node.ftype
    with pytest.raises(ValidationError):
        SearchRule.model_validate({"offset": 3})  # een regel heeft geen pagina
    assert not SearchRule(q="x").has_filter()
    assert SearchRule(types=["pdf"]).has_filter() and SearchRule(place=0).has_filter()


def test_de_regel_neemt_de_namen_van_de_querystring():
    """De web stuurt de regel van een slim album met de namen van §3.4 (AlbumRule in
    web/src/photos/people/types.ts); in Python heten ze zoals in §3.3."""
    wie, map_ = uuid.uuid4(), uuid.uuid4()
    rule = SearchRule.model_validate(
        {"q": "zee", "type": ["image", "video"], "from": "2026-06-01", "to": "2026-08-31",
         "person": [str(wie)], "tag": [], "in": str(map_)}
    )  # fmt: skip
    assert rule.types == ["image", "video"] and rule.persons == [wie] and rule.in_ == map_
    assert str(rule.date_from) == "2026-06-01" and str(rule.date_to) == "2026-08-31"
    assert set(rule.model_dump(by_alias=True)) == {
        "q", "type", "from", "to", "person", "place", "country", "tag", "library", "camera", "in"
    }  # fmt: skip
    with pytest.raises(ValidationError):
        SearchRule.model_validate({"types": ["pdf"], "type": ["image"]})


def test_searchpage_en_suggesties():
    page = SearchPage(results=[], more=False)
    assert page.model_dump() == {
        "results": [],
        "more": False,
        "offset": 0,
        "partial": False,
        "ranked": False,
        "names_only": False,
        "after": None,
        "groups": None,
        "indexing": None,
        "engine": "like",
    }
    assert Suggestions().model_dump() == {
        "names": [],
        "people": [],
        "places": [],
        "tags": [],
        "albums": [],
    }
    with pytest.raises(ValidationError):
        Suggestions(albums=[{"id": uuid.uuid4(), "name": str(i)} for i in range(6)])


def test_extracted_location_en_place_staan_vast():
    e = Extracted(sha="a" * 64, state="none", chars=0, truncated=False, scan_pages=[], meta="",
                  code=None)  # fmt: skip
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.state = "done"  # type: ignore[misc]
    assert [f.name for f in dataclasses.fields(Extracted)] == [
        "sha", "state", "chars", "truncated", "scan_pages", "meta", "code"
    ]  # fmt: skip
    assert [f.name for f in dataclasses.fields(Location)] == ["lat", "lon", "alt", "source"]
    assert [f.name for f in dataclasses.fields(Place)] == ["id", "name", "admin1", "cc", "km"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        Place(None, None, None, None, None).name = "Utrecht"  # type: ignore[misc]


# --- de zoekmachine ---


def test_select_engine_geeft_like_tot_z1(monkeypatch):
    aan = Settings(jwt_secret="test" * 8)
    uit = Settings(jwt_secret="test" * 8, search_index="uit")
    met, zonder = ConnInfo("sqlite", True, "3.46.1"), ConnInfo("sqlite", False, "3.46.1")
    assert engine.FTS is None
    for settings, info in ((aan, met), (aan, zonder), (uit, met), (uit, zonder)):
        assert select_engine(settings, info) is engine.LIKE
    assert engine.LIKE.name == "like"

    class Fts:
        name = "fts"

    monkeypatch.setattr(engine, "FTS", Fts())
    assert select_engine(aan, met) is engine.FTS
    # Zonder fts_node (geen FTS5, of PostgreSQL) of met de index uit: altijd LIKE
    assert select_engine(aan, zonder) is engine.LIKE
    assert select_engine(uit, met) is engine.LIKE
    assert select_engine(aan, ConnInfo("postgresql", False)) is engine.LIKE


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_read_conn_info_ziet_of_er_fts_is(tmp_path, fts5):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "head", fts5=fts5)
    async with get_engine(settings).connect() as conn:
        info = await read_conn_info(conn)
    assert info.dialect == "sqlite" and info.fts is fts5
    assert info.sqlite_version and info.sqlite_version.count(".") == 2


async def test_de_stubs_van_z1_zeggen_dat_ze_er_nog_niet_zijn(settings):
    assert query.parse("") is None and query.parse("   \t") is None
    with pytest.raises(NotImplementedError):
        query.parse("jaarverslag")
    async with get_engine(settings).connect() as conn:
        with pytest.raises(NotImplementedError):
            async with deadline.deadline(conn, 120):
                pass
    with pytest.raises(NotImplementedError):
        async with limits.admit(None):  # type: ignore[arg-type]
            pass


# --- registers ---


def test_een_filter_heeft_een_eigenaar():
    def apply(select, n, p, user):
        return select

    filters.register("camera", apply)
    assert filters.FILTERS["camera"].apply is apply and filters.FILTERS["camera"].suggest is None
    with pytest.raises(ValueError):
        filters.register("camera", apply)


async def test_haken_blob_added_faalt_nooit_en_photo_copy_wel(caplog):
    seen = []

    def kapot(sha, owner):
        raise RuntimeError("stuk")

    hooks.register_blob_added(kapot)
    hooks.register_blob_added(lambda sha, owner: seen.append((sha, owner)))
    hooks.register_blob_added(kapot)  # twee keer aanmelden telt één keer
    owner = uuid.uuid4()
    with caplog.at_level(logging.ERROR, logger="platform.search"):
        hooks.blob_added("a" * 64, owner)
    assert seen == [("a" * 64, owner)]
    assert sum("blob_added" in r.getMessage() for r in caplog.records) == 1

    copies = []

    async def copy(session, pairs):
        copies.append(list(pairs))

    async def stuk(session, pairs):
        raise RuntimeError("personen")

    hooks.register_photo_copy(copy)
    await hooks.photo_copy(None, [])  # niets gekopieerd: geen aanroep
    pair = (uuid.uuid4(), uuid.uuid4())
    await hooks.photo_copy(None, [pair])
    assert copies == [[pair]]
    hooks.register_photo_copy(stuk)
    with pytest.raises(RuntimeError):
        await hooks.photo_copy(None, [pair])


def test_een_stap_meldt_zich_een_keer_aan():
    async def run(settings, sha):
        return None

    assert [s.value for s in analysis.Step] == ["text", "hash", "ocr"]
    assert analysis.VERSIONS == {s: 1 for s in analysis.Step}
    analysis.register_step(analysis.Step.TEXT, "index", lambda sha, mime: True, run)
    assert analysis.registered()[analysis.Step.TEXT].lane == "index"
    with pytest.raises(ValueError):
        analysis.register_step(analysis.Step.TEXT, "index", lambda sha, mime: True, run)


async def test_queue_is_van_z2(settings):
    async with sessions(settings)() as db:
        with pytest.raises(NotImplementedError):
            await analysis.queue(db, "a" * 64, [analysis.Step.TEXT], owner=None)


# --- write_text ---


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_write_text_eerst_select_dan_insert_of_update(tmp_path, fts5):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    await fill_0009(settings)
    await migrate(settings, command.upgrade, "head", fts5=fts5)
    maker = sessions(settings)

    async def row() -> tuple:
        async with maker() as db:
            r = (await db.execute(sa.select(BlobText).where(BlobText.blob_sha == SHA_TXT))).one()
            t = r[0]
            return t.body, t.ocr, t.meta, t.truncated

    async def check() -> None:
        async with get_engine(settings).connect() as conn:
            await conn.run_sync(check_tree)
            if fts5:
                await conn.run_sync(check_fts)

    # Nieuw: een INSERT, met "" voor wat niet meekomt
    async with maker() as db:
        await analysis.write_text(db, SHA_TXT, body="Kwartaalcijfers Straße", truncated=True)
    assert await row() == ("Kwartaalcijfers Straße", "", "", 1)
    await check()
    # Alleen ocr: body en truncated blijven
    async with maker() as db:
        await analysis.write_text(db, SHA_TXT, ocr="handgeschreven")
    assert await row() == ("Kwartaalcijfers Straße", "handgeschreven", "", 1)
    await check()
    # Een nieuwe poging (zoals na een crash): weer een UPDATE, geen tweede rij
    async with maker() as db:
        await analysis.write_text(db, SHA_TXT, body="Jaarcijfers", meta="auteur eva")
        await analysis.write_text(db, SHA_TXT, body="Jaarcijfers", meta="auteur eva")
    assert await row() == ("Jaarcijfers", "handgeschreven", "auteur eva", 0)
    await check()
    async with get_engine(settings).connect() as conn:
        n = (await conn.exec_driver_sql("SELECT count(*) FROM blob_text")).scalar()
        assert n == 1
        if fts5:
            # notities.x en los.x hebben deze inhoud; los.x ligt in de prullenbak maar staat
            # wel in de index (de vraag filtert op dead, niet de index)
            found = await conn.run_sync(lambda c: words(c, "jaarcijfers"))
            assert found == {"notities.x", "los.x"}
            assert await conn.run_sync(lambda c: words(c, "kwartaalcijfers")) == set()


# --- foto-instellingen ---


async def test_foto_instellingen_met_standaarden(settings):
    me = uuid.uuid4()
    assert prefs.PHOTO_PREFS == {
        "location": "mij",
        "memories": True,
        "ocr_photos": False,
        "faces": "uit",
    }
    maker = sessions(settings)
    async with maker() as db:
        assert await prefs.photo_prefs(db, me) == prefs.PHOTO_PREFS
        assert await prefs.photo_prefs(db, me) is not prefs.PHOTO_PREFS
    async with maker() as db:
        await db.execute(
            sa.text(
                'INSERT INTO "user" (id, email, display_name, password_hash, role, quota_bytes, '
                "root_id, created_at) VALUES (:id, 'p@example.nl', 'p', 'x', 'user', 0, :root, "
                "CURRENT_TIMESTAMP)"
            ),
            {"id": me.hex, "root": uuid.uuid4().hex},
        )
        db.add(PhotoPref(user_id=me, location="uit", memories=0))
        await db.commit()
    async with maker() as db:
        got = await prefs.photo_prefs(db, me)
    assert got == {"location": "uit", "memories": False, "ocr_photos": False, "faces": "uit"}
    assert set(prefs.PhotoSettings.model_fields) == {"location", "memories", "ocr_photos"}
    with pytest.raises(ValidationError):
        prefs.PhotoSettingsPatch.model_validate({"faces": "aan"})  # X2: niet via de API


async def test_na_de_migratie_is_de_stand_van_de_database_al_gelezen(settings):
    """app.migrate.upgrade leest na de migratie of er FTS is: de eerste zoekvraag hoeft dat niet
    te doen (en telt dus net zoveel vragen als de volgende)."""
    key = engine._key(settings.db_url)
    assert engine._info[key].fts is True
    engine.forget(settings.db_url)
    assert key not in engine._info


async def test_backfill_bij_het_starten_faalt_nooit(caplog):
    seen = []

    async def kapot(settings):
        raise RuntimeError("stuk")

    async def goed(settings):
        seen.append(settings)

    hooks.register_backfill(kapot)
    hooks.register_backfill(goed)
    with caplog.at_level(logging.ERROR, logger="platform.search"):
        await hooks.backfill("s")  # type: ignore[arg-type]
    assert seen == ["s"]
    assert any("backfill" in r.getMessage() for r in caplog.records)
