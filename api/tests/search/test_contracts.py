"""De vaste Python-vormen van fase 6 (bouwontwerp §3.3) die Z0 neerzet: de typen van zoeken, de
keuze van de zoekmachine, de registers (filters, haken, stappen), write_text() als enige
schrijver van blob_text, de foto-instellingen, en de stubs die tot hun pakket niets doen.

Z0b (Keuze 55, 66 en 80) zette de velden en waarden van X2 en X3 erbij, elk met een standaard: de
tests onder "Z0b" laten zien dat een oude vraag hetzelfde antwoord krijgt."""

from __future__ import annotations

import dataclasses
import logging
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import analysis, prefs
from app.config import Settings
from app.db import get_engine
from app.extract.result import Extracted
from app.geo.types import Location, Place
from app.models import Analysis, Blob, BlobText, Job, PhotoPref, User
from app.people.types import AlbumRuleIn
from app.search import deadline, engine, filters, hooks, limits, query
from app.search import types as t
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
from tests.test_files import put, root

pytestmark = pytest.mark.anyio


def sessions(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


@pytest.fixture(autouse=True)
def _schone_registers():
    filters.reset()
    hooks.reset()
    analysis.reset()
    engine.forget()
    prefs.forget_switches()
    yield
    filters.reset()
    hooks.reset()
    analysis.reset()
    engine.forget()
    prefs.forget_switches()


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
        "mode": "text",
        "fallback": None,
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


def test_select_engine_geeft_fts_of_like(monkeypatch):
    """Sinds Z1 is FtsEngine er; LikeFallback zonder fts_node (geen FTS5, of PostgreSQL), met de
    index uit, en zonder FTS-motor."""
    aan = Settings(jwt_secret="test" * 8)
    uit = Settings(jwt_secret="test" * 8, search_index="uit")
    met, zonder = ConnInfo("sqlite", True, "3.46.1"), ConnInfo("sqlite", False, "3.46.1")
    assert isinstance(engine.FTS, engine.FtsEngine) and engine.FTS.name == "fts"
    assert engine.LIKE.name == "like"
    assert select_engine(aan, met) is engine.FTS
    # Zonder fts_node (geen FTS5, of PostgreSQL) of met de index uit: altijd LIKE
    for settings, info in ((aan, zonder), (uit, met), (uit, zonder)):
        assert select_engine(settings, info) is engine.LIKE
    assert select_engine(aan, ConnInfo("postgresql", False)) is engine.LIKE

    class Fts:
        name = "fts"

    monkeypatch.setattr(engine, "FTS", Fts())
    assert select_engine(aan, met) is engine.FTS
    monkeypatch.setattr(engine, "FTS", None)
    assert select_engine(aan, met) is engine.LIKE


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_read_conn_info_ziet_of_er_fts_is(tmp_path, fts5):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "head", fts5=fts5)
    async with get_engine(settings).connect() as conn:
        info = await read_conn_info(conn)
    assert info.dialect == "sqlite" and info.fts is fts5
    assert info.sqlite_version and info.sqlite_version.count(".") == 2


async def test_parse_deadline_en_admit_zijn_gebouwd(settings, client, admin):
    """De stubs van Z0 zijn sinds Z1 gebouwd: parse() geeft een uitdrukking, deadline() een
    grens die niet geraakt is, admit() laat een vraag door."""
    assert query.parse("") is None and query.parse("   \t") is None
    parsed = query.parse("jaarverslag")
    assert parsed is not None and parsed.match == '"jaarverslag"*'
    async with get_engine(settings).connect() as conn:
        async with deadline.deadline(conn, 120) as d:
            assert (await conn.exec_driver_sql("SELECT 1")).scalar() == 1
        assert d.hit is False
    async with sessions(settings)() as session:
        user = await session.scalar(sa.select(User).where(User.role == "admin"))
    assert user is not None
    async with limits.admit(user):
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

    assert [s.value for s in analysis.Step] == ["text", "hash", "ocr", "embed"]
    assert analysis.VERSIONS == {s: 1 for s in analysis.Step}
    analysis.register_step(analysis.Step.TEXT, "index", lambda sha, mime: True, run)
    assert analysis.registered()[analysis.Step.TEXT].lane == "index"
    with pytest.raises(ValueError):
        analysis.register_step(analysis.Step.TEXT, "index", lambda sha, mime: True, run)


async def test_queue_zet_de_stap_klaar_en_wekt_de_rij(settings):
    """Sinds Z2 (tot dan NotImplementedError): queue zet de stap op wait, of op skip als hij
    niet bij de inhoud past, en zet de pomp van zijn rij klaar. Een stap die niemand aanmeldde
    en een onbekende inhoud slaat hij over. De rest: tests/test_analysis_versions.py."""

    async def run(settings, sha):
        return None

    pdf, png = "a" * 64, "b" * 64
    analysis.register_step(
        analysis.Step.TEXT, "index", lambda sha, mime: mime == "application/pdf", run
    )
    async with sessions(settings)() as db:
        db.add_all(
            [
                Blob(sha256=pdf, size=1, mime="application/pdf"),
                Blob(sha256=png, size=1, mime="image/png"),
            ]
        )
        await db.commit()
        for sha in (pdf, png, "c" * 64):
            await analysis.queue(db, sha, [analysis.Step.TEXT, analysis.Step.HASH], owner=None)
        await db.commit()
        rows = (
            await db.execute(
                sa.select(Analysis.blob_sha, Analysis.step, Analysis.state, Analysis.version)
            )
        ).all()
        pumps = (await db.execute(sa.select(Job.type, Job.key, Job.status))).all()
    assert sorted(rows) == [(pdf, "text", "wait", 1), (png, "text", "skip", 1)]
    assert pumps == [("analysis.index", "analysis.index", "queued")]


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
        "beeld": "uit",
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
    # beeld heeft nog geen kolom (0012, A0): de standaard
    assert got == {
        "location": "uit",
        "memories": False,
        "ocr_photos": False,
        "faces": "uit",
        "beeld": "uit",
    }
    assert set(prefs.PhotoSettings.model_fields) == {
        "location",
        "memories",
        "ocr_photos",
        "faces",
        "faces_forbidden",
        "faces_available",
        "beeld",
    }
    assert set(prefs.PhotoSettingsPatch.model_fields) == {
        "location",
        "memories",
        "ocr_photos",
        "faces",
        "beeld",
    }
    # faces en beeld via de API (Z0b): alleen aan of uit
    patch = prefs.PhotoSettingsPatch.model_validate({"faces": "aan", "beeld": "uit"})
    assert (patch.faces, patch.beeld) == ("aan", "uit")
    for bad in ({"faces": "ja"}, {"beeld": True}, {"faces_forbidden": False}, {"onbekend": 1}):
        with pytest.raises(ValidationError):
            prefs.PhotoSettingsPatch.model_validate(bad)


def _gebruiker(role: str = "user") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role)


async def _fout(aanroep) -> tuple[int, str]:
    with pytest.raises(HTTPException) as caught:
        await aanroep
    return caught.value.status_code, caught.value.detail["code"]


def _patch(**velden) -> prefs.PhotoSettingsPatch:
    return prefs.PhotoSettingsPatch.model_validate(velden)


async def test_gezichten_en_beeld_aanzetten_kan_niet_zonder_x2_en_x3():
    """Review Z0b, veilig V3 en contract F1: PhotoSettingsPatch neemt faces en beeld "aan", maar
    check_switch weigert dat zolang X2 of X3 zich niet aanmeldt, en altijd als de analyse uit
    staat (Keuze 59: 409 faces_unavailable, 409 beeld_not_installed). Z6 roept hem aan vóór hij
    schrijft. "uit" gaat altijd; een gast mag geen van beide (403 guest)."""
    db = None  # zonder aanmelding leest check_switch niets
    eva = _gebruiker()
    for analyse in (False, True):
        check = prefs.check_switch
        assert await _fout(check(db, eva, _patch(faces="aan"), analyse_enabled=analyse)) == (
            409, "faces_unavailable"
        )  # fmt: skip
        assert await _fout(check(db, eva, _patch(beeld="aan"), analyse_enabled=analyse)) == (
            409, "beeld_not_installed"
        )  # fmt: skip
        for patch in (_patch(faces="uit", beeld="uit"), _patch(location="uit"), _patch()):
            await check(db, eva, patch, analyse_enabled=analyse)
    gast = _gebruiker("guest")
    for velden in ({"faces": "aan"}, {"faces": "uit"}, {"beeld": "aan"}, {"beeld": "uit"}):
        got = await _fout(prefs.check_switch(db, gast, _patch(**velden), analyse_enabled=True))
        assert got == (403, "guest"), velden
    await prefs.check_switch(db, gast, _patch(location="uit"), analyse_enabled=True)


async def test_x2_en_x3_melden_hun_schakelaar_aan():
    """Review Z0b, contract F4: X2 en X3 melden bij app.prefs wat hun schakelaar mag en doet;
    Z6 hoeft photo_prefs.py dan niet voor ze te openen. De stand van de analyse gaat vóór X2:
    zonder PLATFORM_ANALYSE_ENABLED is er nooit iets beschikbaar."""
    assert prefs.SWITCHES == {"faces": "X2", "beeld": "X3"}
    gezien: list[tuple[str, str]] = []
    stand = {"faces": prefs.SwitchState(), "beeld": prefs.SwitchState()}

    def aanmelden(key: str) -> None:
        async def state(db, user):
            return stand[key]

        async def changed(db, user, value):
            gezien.append((key, value))

        prefs.register_switch(key, prefs.Switch(state=state, changed=changed))

    aanmelden("faces")
    aanmelden("beeld")
    eva = _gebruiker()
    check = prefs.check_switch
    stand["faces"] = stand["beeld"] = prefs.SwitchState(available=True)
    # Beschikbaar volgens X2 en X3, maar de analyse staat uit: Keuze 59 wint
    assert await _fout(check(None, eva, _patch(faces="aan"), analyse_enabled=False)) == (
        409, "faces_unavailable"
    )  # fmt: skip
    assert await _fout(check(None, eva, _patch(beeld="aan"), analyse_enabled=False)) == (
        409, "beeld_not_installed"
    )  # fmt: skip
    st = await prefs.switch_state(None, eva, "faces", analyse_enabled=False)
    assert st == prefs.SwitchState(forbidden=False, available=False)
    await check(None, eva, _patch(faces="aan", beeld="aan"), analyse_enabled=True)
    # De beheerder verbiedt het (Keuze 64), of zet beeld uit voor iedereen (Keuze 82)
    stand["faces"] = stand["beeld"] = prefs.SwitchState(forbidden=True, available=True)
    assert await _fout(check(None, eva, _patch(faces="aan"), analyse_enabled=True)) == (
        403, "faces_forbidden"
    )  # fmt: skip
    assert await _fout(check(None, eva, _patch(beeld="aan"), analyse_enabled=True)) == (
        409, "beeld_off"
    )  # fmt: skip
    await check(None, eva, _patch(faces="uit", beeld="uit"), analyse_enabled=True)
    # Na het schrijven: alleen wat echt veranderde, in de volgorde faces, beeld
    before = dict(prefs.PHOTO_PREFS)
    await prefs.switched(None, eva, before, _patch(location="uit"))
    await prefs.switched(None, eva, before, _patch(faces="uit", beeld="uit"))
    assert gezien == []
    await prefs.switched(None, eva, before, _patch(beeld="aan", faces="aan"))
    await prefs.switched(None, eva, {**before, "faces": "aan"}, _patch(faces="uit"))
    assert gezien == [("faces", "aan"), ("beeld", "aan"), ("faces", "uit")]
    prefs.forget_switches()
    assert await prefs.switch_state(None, eva, "faces", analyse_enabled=True) == (
        prefs.SwitchState()
    )


async def test_de_foto_instellingen_met_de_stand_van_x2(settings):
    """GET /me/photo-settings van Z6 is prefs.photo_settings(): de vijf instellingen plus
    faces_forbidden en faces_available. Zonder X2 allebei False (zoals PhotoSettings zegt)."""
    eva = _gebruiker()
    async with sessions(settings)() as db:
        got = await prefs.photo_settings(db, eva, analyse_enabled=True)
    assert got == prefs.PhotoSettings(**prefs.PHOTO_PREFS)
    assert (got.faces_forbidden, got.faces_available) == (False, False)

    async def state(db, user):
        return prefs.SwitchState(forbidden=True, available=True)

    async def changed(db, user, value):
        return None

    prefs.register_switch("faces", prefs.Switch(state=state, changed=changed))
    async with sessions(settings)() as db:
        got = await prefs.photo_settings(db, eva, analyse_enabled=True)
        assert (got.faces_forbidden, got.faces_available) == (True, True)
        got = await prefs.photo_settings(db, eva, analyse_enabled=False)
        assert (got.faces_forbidden, got.faces_available) == (True, False)


async def test_x2_en_x3_melden_hun_deel_van_beheer_aan():
    """Review Z0b, contract F4: het blok, de schakelaar en het herbouwen van gezichten en beeld op
    /admin/search gaan via search.hooks, zodat Z7 admin.py en admin_search.py niet voor X2 en X3
    open hoeft. Zonder aanmelding: het blok None (de standaard van AdminSearch), en de schakelaar
    en herbouwen geven 501 not_built van het pakket."""
    assert hooks.ADMIN_PARTS == {"faces": "X2", "beeld": "X3"}
    assert set(hooks.ADMIN_PARTS) == set(t.RebuildWhat.__args__) - {
        "names", "text", "geo", "hash", "ocr"
    }  # fmt: skip
    assert set(hooks.ADMIN_PARTS) == {"faces", "beeld"} <= set(t.AdminSearch.model_fields)
    assert hooks.ADMIN_SWITCHES == {"faces_allowed": "faces", "beeld": "beeld"}
    assert set(hooks.ADMIN_SWITCHES) <= set(t.AdminSearchPatch.model_fields)
    for name, package in hooks.ADMIN_PARTS.items():
        assert await hooks.admin_block(name, None) is None
        for aanroep in (hooks.admin_switch(name, None, True), hooks.admin_rebuild(name, None)):
            with pytest.raises(HTTPException) as caught:
                await aanroep
            assert caught.value.status_code == 501
            assert caught.value.detail["code"] == "not_built"
            assert caught.value.detail["params"] == {"package": package}

    gezien: list[tuple] = []

    async def block(db):
        gezien.append(("block", db))
        return "blok"

    async def switch(db, value):
        gezien.append(("switch", db, value))

    async def rebuild(db):
        gezien.append(("rebuild", db))

    hooks.register_admin("faces", hooks.AdminPart(block=block, switch=switch, rebuild=rebuild))
    assert await hooks.admin_block("faces", "db") == "blok"
    await hooks.admin_switch("faces", "db", False)
    await hooks.admin_rebuild("faces", "db")
    assert gezien == [("block", "db"), ("switch", "db", False), ("rebuild", "db")]
    assert await hooks.admin_block("beeld", "db") is None
    hooks.reset()
    assert await hooks.admin_block("faces", "db") is None


async def test_alleen_faces_en_beeld_melden_zich_aan():
    """Nacontrole van Z0b: register_switch neemt alleen de schakelaars van X2 en X3, en
    register_admin alleen hun delen van /beheer. Een andere sleutel (een instelling van Z6, of een
    deel van Z7) geeft ValueError en meldt niets aan."""

    async def state(db, user):
        return prefs.SwitchState(available=True)

    async def changed(db, user, value):
        return None

    async def niets(*args):
        return None

    for key in ("location", "memories", "ocr_photos", "faces_allowed", "Faces", ""):
        with pytest.raises(ValueError):
            prefs.register_switch(key, prefs.Switch(state=state, changed=changed))
    for name in ("names", "text", "geo", "hash", "ocr", "faces_allowed", "Beeld", ""):
        with pytest.raises(ValueError):
            hooks.register_admin(name, hooks.AdminPart(block=niets, switch=niets, rebuild=niets))
    assert prefs._switches == {}
    assert hooks._admin == {}


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


# --- Z0b: de velden van X2 en X3, elk met een standaard ---


def test_een_oude_vraag_en_een_oud_antwoord_blijven_gelijk():
    """Zonder de nieuwe velden: dezelfde vraag, en hetzelfde antwoord met alleen de nieuwe velden
    op hun standaard erbij."""
    oud = {"q": "zee", "type": ["image"], "limit": 20}
    assert SearchParams.model_validate(oud) == SearchParams.model_validate({**oud, "mode": "text"})
    p = SearchParams.model_validate(oud)
    assert (p.mode, p.like) == ("text", None)

    z0 = {
        "engine": "fts",
        "paused": False,
        "index": {"names_done": True, "text_done": 1, "text_total": 2, "failed": 0},
        "rebuild": None,
        "extract_in_sandbox": True,
        "per_hour": {"deadline_b": 0, "deadline_c": 0, "busy": 0},
        "ocr": {"installed": False, "on": False, "pages_queued": 0},
        "places": {"loaded": False, "source": None, "date": None},
        "bench": None,
    }
    assert t.AdminSearch.model_validate(z0).model_dump() == {**z0, "faces": None, "beeld": None}
    assert t.AdminSearchPatch.model_validate({"paused": True}).model_dump(exclude_none=True) == {
        "paused": True
    }

    z0_settings = {"location": "mij", "memories": True, "ocr_photos": False}
    assert prefs.PhotoSettings.model_validate(z0_settings).model_dump() == {
        **z0_settings,
        "faces": "uit",
        "faces_forbidden": False,
        "faces_available": False,
        "beeld": "uit",
    }
    assert t.Hit.model_fields["match"].default == "name"


def test_de_nieuwe_waarden_van_z0b():
    """De vaste woorden van Keuze 66 en 80, en mode en like alleen in de vraag van /search, niet
    in de regel van een slim album (Keuze 80)."""
    assert set(t.Match.__args__) == {"name", "title", "text", "ocr", "meta", "image"}
    assert set(t.SearchMode.__args__) == {"text", "image"}
    assert set(t.BeeldFallback.__args__) == {"slow", "down", "no_model"}
    assert set(t.AnalysisCode.__args__) == {
        "too_slow", "ocr_missing", "ocr_bad_image", "ocr_has_text", "zip_limit", "xml_refused",
        "pages_limit", "no_preview", "image_bad",
    }  # fmt: skip
    assert set(t.RebuildWhat.__args__) == {
        "names", "text", "geo", "hash", "ocr", "faces", "beeld"
    }  # fmt: skip
    assert set(t.AnalyseState.__args__) == {"ok", "down", "models_missing", "off"}
    assert t.RebuildIn(what="faces").what == "faces" and t.RebuildIn(what="beeld").what == "beeld"
    for what in ("faces", "beeld"):
        assert t.SearchProgressEvent(what=what, done=0.5).what == what
    # Geen nieuw type gebeurtenis en geen veld voor embed in de analyse (Keuze 79)
    assert set(t.EVENTS) == {"search.ready", "people.changed", "search.progress"}
    assert set(t.AnalysisOut.model_fields) == {"text", "hash", "ocr", "truncated", "code"}
    assert set(t.FailedItem.model_fields["step"].annotation.__args__) == {
        "text", "hash", "ocr", "geo"
    }  # fmt: skip

    like = uuid.uuid4()
    p = SearchParams.model_validate({"mode": "image", "like": str(like)})
    assert (p.mode, p.like) == ("image", like)
    with pytest.raises(ValidationError):
        SearchParams.model_validate({"mode": "foto"})
    assert set(SearchParams.model_fields) - set(SearchRule.model_fields) == {
        "offset", "limit", "after", "mode", "like"
    }  # fmt: skip
    for extra in ({"mode": "image"}, {"like": str(like)}):
        with pytest.raises(ValidationError):
            SearchRule.model_validate({"q": "zee", **extra})
        with pytest.raises(ValidationError):
            AlbumRuleIn.model_validate({"rule": {"q": "zee", **extra}})

    status = t.BeeldStatus(state="bezig", done=0.25)
    assert status.model_dump() == {"state": "bezig", "done": 0.25}
    for bad in ({"state": "aan", "done": None}, {"state": "ok", "done": 1.5}, {"state": "ok"}):
        with pytest.raises(ValidationError):
            t.BeeldStatus.model_validate(bad)


async def test_zoeken_zonder_mode_geeft_hetzelfde_antwoord(client, admin):
    """GET /search zonder mode is een vraag als in Z0: byte voor byte hetzelfde antwoord als met
    mode=text, met precies de velden van Z0 en mode en fallback op hun standaard."""
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    await put(client, headers, root_id, "Jaarverslag 2025.pdf", b"%PDF-1.4\n")
    zonder = await client.get("/api/v1/search", params={"q": "jaarverslag"}, headers=headers)
    met = await client.get(
        "/api/v1/search", params={"q": "jaarverslag", "mode": "text"}, headers=headers
    )
    assert zonder.status_code == met.status_code == 200, zonder.text
    assert zonder.content == met.content
    body = zonder.json()
    assert list(body) == [
        "results", "more", "offset", "partial", "ranked", "names_only", "after", "groups",
        "indexing", "engine", "mode", "fallback",
    ]  # fmt: skip
    assert (body["mode"], body["fallback"]) == ("text", None)
    assert [h["node"]["name"] for h in body["results"]] == ["Jaarverslag 2025.pdf"]
    # Een vraag van 0.7.0 die niet in SearchParams past: tot Z4 LikeFallback zonder controle,
    # nu een 422 ("De overgang" in §3.4); met limit 50 dezelfde velden
    r = await client.get("/api/v1/search", params={"q": "x", "limit": 200}, headers=headers)
    assert r.status_code == 422 and r.json()["detail"]["params"] == {"field": "query.limit"}
    r = await client.get("/api/v1/search", params={"q": "x", "limit": 50}, headers=headers)
    assert r.status_code == 200 and (r.json()["mode"], r.json()["fallback"]) == ("text", None)


async def test_een_regel_met_mode_geeft_422(client, admin):
    """Een slim album zoekt nooit op beeld: mode of like in de regel geeft 422 (de body wordt
    gelezen voor de route het album zoekt). Sinds Z6 is de route gebouwd: zonder mode en like
    komt de vraag bij het album, en een album dat er niet is, is 404."""
    headers, _ = admin
    url = f"/api/v1/albums/{uuid.uuid4()}/rule"
    for extra in ({"mode": "image"}, {"like": str(uuid.uuid4())}):
        r = await client.put(url, json={"rule": {"q": "zee", **extra}}, headers=headers)
        assert r.status_code == 422, (extra, r.text)
    r = await client.put(url, json={"rule": {"q": "zee"}}, headers=headers)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found", r.text
