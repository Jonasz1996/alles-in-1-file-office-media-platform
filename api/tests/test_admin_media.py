"""Het tabblad Media op /beheer (M10, Keuze 45): routers/admin_media.py en media/admin.py.

Alleen beheerders; het overzicht (ook als media op /beheer uit staat, en 503 media_off zonder
PLATFORM_MEDIA_ENABLED); de instellingen met hun grenzen (cpu_budget 0,5–16, max_transcodes
hooguit het plafond uit env); Stop sluit de sessie (de speler krijgt 403 media_access_gone
stopped); de redenen per dag; de sleutel van TMDB komt nooit terug (niet in een antwoord, niet
in een log); opnieuw proberen; de cache leegmaken zonder een lopende sessie te breken; de meting
(en 409 media_bench_busy); de import via /beheer.

De worker is de nep-worker van M0 (media_app); waar een echte stroom nodig is, de stromen van
M4 met de nep-worker (de fixture case van test_media_producer, die ffmpeg nodig heeft voor de
fixtures).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.config import MediaConfig
from app.main import app
from app.media import admin as media_admin
from app.media import importer, loadtest, sessions, streams
from app.media.providers import secrets
from app.media.providers.base import ProviderAuth, ProviderRate, ProviderUnavailable
from app.media.state import MediaPolicy
from app.media.types import LoadtestFile, MediaAdmin, Plan, WorkerCaps
from app.media.wire import Code, Op
from app.models import Job
from app.routers import stream_audio
from tests.media import fake_worker as FW
from tests.media import fixtures as F
from tests.media.media_contract import failed
from tests.test_files import mkdir, root
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_producer import Case, case, until  # noqa: F401
from tests.test_media_sessions import file, maker, play, session_of, video
from tests.test_shares import person

pytestmark = pytest.mark.anyio

BASE = "/api/v1/admin/media"
needs_fixtures = pytest.mark.skipif(not F.available(), reason="ffmpeg ontbreekt (fixtures)")


def routes() -> list[tuple[str, str, dict | None]]:
    return [
        ("GET", BASE, None),
        ("PUT", f"{BASE}/config", {"cpu_budget": 2}),
        ("POST", f"{BASE}/bench", None),
        ("DELETE", f"{BASE}/sessions/{uuid.uuid4()}", None),
        ("POST", f"{BASE}/retry", None),
        ("POST", f"{BASE}/libraries/{uuid.uuid4()}/scan", None),
        ("POST", f"{BASE}/cache/clear", {"what": "trick"}),
        ("PUT", f"{BASE}/tmdb", {"key": "geheim-sleutel-1234567890"}),
        ("DELETE", f"{BASE}/tmdb", None),
        ("POST", f"{BASE}/tmdb/test", None),
        ("GET", f"{BASE}/import/sources", None),
        ("POST", f"{BASE}/import", {"source": "x", "target_id": str(uuid.uuid4()),
                                    "owner_id": str(uuid.uuid4())}),
        ("GET", f"{BASE}/loadtest", None),
        ("POST", f"{BASE}/loadtest", {"streams": 1, "seconds": 30}),
        ("POST", f"{BASE}/loadtest/play", {"source": 0}),
    ]  # fmt: skip


@pytest.fixture(autouse=True)
def clean():
    media_admin.reasons.reset()
    media_admin._bench = None
    yield
    media_admin.reasons.reset()
    media_admin._bench = None


async def overview(client, headers) -> dict:
    r = await client.get(BASE, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- wie mag het ---------------------------------------------------------------------------------


async def test_alleen_beheerders(media_app, client, admin):  # noqa: F811
    admin_headers, _ = admin
    headers, _ = await person(client, admin_headers, "gewoon@example.nl")
    for method, path, body in routes():
        r = await client.request(method, path, json=body)
        failed(r, 401, "not_logged_in")
        r = await client.request(method, path, json=body, headers=headers)
        failed(r, 403, "admin_only")
    # er is niets veranderd
    assert (await overview(client, admin_headers))["config"][
        "cpu_budget"
    ] == MediaConfig().cpu_budget


# --- het overzicht -------------------------------------------------------------------------------


async def test_het_overzicht(media_app, client, admin):  # noqa: F811
    settings, fake = media_app
    headers, _ = admin
    app.state.media.caps = WorkerCaps.model_validate(fake.caps())
    body = await overview(client, headers)
    view = MediaAdmin.model_validate(body)
    assert view.worker.mode == "local" and view.worker.state == "ok"
    assert view.worker.selftest is not None and view.worker.selftest.sandbox == "off"
    assert view.worker.places["transcode"].max == 3 and view.worker.places["short"].max == 2
    assert view.ffmpeg is not None and view.ffmpeg.version and view.ffmpeg.missing == []
    assert view.budget.total > 0 and view.sessions == [] and view.libraries == []
    assert [p.name for p in view.providers] == ["tmdb", "musicbrainz", "caa"]
    assert view.config["max_transcodes"] <= settings.media_max_transcodes
    assert "import" in body and body["import"] is None
    assert view.cache.free_bytes > 0
    # de lokale worker draait zonder afscherming: dat meldt /beheer
    assert [n.code for n in view.notices] == ["media_sandbox_off"]
    # hoe vaak een native speler opnieuw laadde (M4, streams.native_reloads; M0-hotfix)
    assert view.native_reloads == 0


async def test_native_spelers_die_opnieuw_laden(media_app, client, admin, monkeypatch):  # noqa: F811
    headers, _ = admin
    monkeypatch.setattr(streams, "native_reloads", 3)
    assert (await overview(client, headers))["native_reloads"] == 3


async def test_het_geheugen_en_de_zelftest_van_de_worker(media_app, client, admin):  # noqa: F811
    """M0-hotfix van M10: children, rss_kb en selftest uit de caps (guard.Watch en __main__)
    vielen weg in WorkerCaps; /beheer toont ze nu (tot 30 s oud, de lus van health)."""
    _, fake = media_app
    headers, _ = admin
    children = [{"pid": 4242, "name": "ffmpeg", "rss_kb": 51200},
                {"pid": 4243, "name": "ffprobe", "rss_kb": 2048}]  # fmt: skip
    selftest = {"ok": True, "sandbox": "on", "problems": []}
    found = {**fake.caps(), "mode": "container", "sandbox": "on", "children": children,
             "rss_kb": 53248, "selftest": selftest}  # fmt: skip
    app.state.media.caps = WorkerCaps.model_validate(found)
    view = MediaAdmin.model_validate(await overview(client, headers))
    assert view.worker.rss_kb == 53248
    assert [(c.pid, c.name, c.rss_kb) for c in view.worker.children] == [
        (4242, "ffmpeg", 51200), (4243, "ffprobe", 2048)
    ]  # fmt: skip
    assert view.worker.selftest is not None and view.worker.selftest.sandbox == "on"
    # wat de worker zelf zag, wint; een vreemde vorm valt terug op de caps
    found["selftest"] = {"ok": False, "sandbox": "on", "problems": ["seccomp ontbreekt"]}
    app.state.media.caps = WorkerCaps.model_validate(found)
    view = MediaAdmin.model_validate(await overview(client, headers))
    assert view.worker.selftest is not None and view.worker.selftest.ok is False
    assert view.worker.selftest.problems == ["seccomp ontbreekt"]
    found["selftest"] = {"ok": "misschien", "sandbox": "on"}
    app.state.media.caps = WorkerCaps.model_validate(found)
    view = MediaAdmin.model_validate(await overview(client, headers))
    assert view.worker.selftest is not None and view.worker.selftest.ok is True
    # een oudere worker zonder die velden: leeg, geen fout
    app.state.media.caps = WorkerCaps.model_validate(fake.caps())
    view = MediaAdmin.model_validate(await overview(client, headers))
    assert view.worker.rss_kb == 0 and view.worker.children == []


async def test_een_bibliotheek_in_een_andere_geschoven(media_app, client, admin):  # noqa: F811
    """m2.md C2: het maken weigert nesting, maar een map verschuiven kan daarna nog (de
    bestandsroutes zijn van M0 en weigeren het niet). De binnenste wint al; /beheer waarschuwt
    met de naam van de buitenste (AdminLibrary.inside, M0-hotfix van M10)."""
    headers, _ = admin
    top = (await root(client, headers))["folder"]["id"]
    films = await mkdir(client, headers, top, "Films")
    series = await mkdir(client, headers, top, "Series")
    for root_id, kind in ((films, "movies"), (series, "shows")):
        r = await client.post("/api/v1/media/libraries", json={"root_id": root_id, "kind": kind},
                              headers=headers)  # fmt: skip
        assert r.status_code == 201, r.text
    view = MediaAdmin.model_validate(await overview(client, headers))
    assert [(lib.name, lib.inside) for lib in view.libraries] == [("Films", None), ("Series", None)]
    # Series verhuist naar Films: geen weigering, wel een waarschuwing op /beheer
    r = await client.patch(f"/api/v1/files/{series}", json={"parent_id": films}, headers=headers)
    assert r.status_code == 200, r.text
    view = MediaAdmin.model_validate(await overview(client, headers))
    assert [(lib.name, lib.inside) for lib in view.libraries] == [
        ("Films", None), ("Series", "Films")
    ]  # fmt: skip
    # een nieuwe bibliotheek in de geneste map: het maken weigert nesting nog steeds
    sub = await mkdir(client, headers, series, "Extra")
    r = await client.post("/api/v1/media/libraries", json={"root_id": sub, "kind": "movies"},
                          headers=headers)  # fmt: skip
    assert r.status_code == 409  # maken weigert nesting nog steeds
    # terug: de waarschuwing gaat weg
    r = await client.patch(f"/api/v1/files/{series}", json={"parent_id": top}, headers=headers)
    assert r.status_code == 200, r.text
    view = MediaAdmin.model_validate(await overview(client, headers))
    assert all(lib.inside is None for lib in view.libraries)


async def test_opnieuw_scannen_ook_van_een_ander(media_app, client, admin):  # noqa: F811
    """De knop op /beheer: een volledige ronde, ook voor de bibliotheek van Bert (de route van
    M2 vraagt schrijfrecht, de beheerder heeft dat niet). 404 voor een onbekende."""
    settings, _ = media_app
    admin_headers, _ = admin
    headers, _ = await person(client, admin_headers, "bert@example.nl")
    top = (await root(client, headers))["folder"]["id"]
    films = await mkdir(client, headers, top, "Films")
    r = await client.post("/api/v1/media/libraries", json={"root_id": films, "kind": "movies"},
                          headers=headers)  # fmt: skip
    assert r.status_code == 201, r.text
    lib = r.json()["id"]
    # via M2 mag de beheerder het niet (hij ziet de bibliotheek niet eens)
    r = await client.post(f"/api/v1/media/libraries/{lib}/scan", headers=admin_headers)
    assert r.status_code == 404
    # de eerste scan is gedaan: opnieuw scannen zet een nieuwe klaar
    key = f"media.scan:{lib}"
    async with maker(settings)() as session:
        await session.execute(update(Job).where(Job.key == key).values(status="done"))
        await session.commit()
    r = await client.post(f"{BASE}/libraries/{lib}/scan", headers=admin_headers)
    assert r.status_code == 202, r.text
    assert r.json() == {"library_id": lib}
    async with maker(settings)() as session:
        queued = (
            await session.scalars(select(Job).where(Job.key == key, Job.status == "queued"))
        ).all()
    assert len(queued) == 1 and queued[0].payload.get("full") is True
    failed(await client.post(f"{BASE}/libraries/{uuid.uuid4()}/scan", headers=admin_headers),
           404, "not_found")  # fmt: skip


async def test_het_overzicht_als_media_op_beheer_uit_staat(media_app, client, admin):  # noqa: F811
    headers, _ = admin
    r = await client.put(f"{BASE}/config", json={"enabled": False}, headers=headers)
    assert r.status_code == 200 and r.json()["enabled"] is False
    body = await overview(client, headers)  # daar zet de beheerder het weer aan
    assert body["config"]["enabled"] is False and body["notices"] == []
    # wat media aan nodig heeft, zegt media_off
    failed(await client.post(f"{BASE}/bench", headers=headers), 503, "media_off")
    failed(await client.get(f"{BASE}/import/sources", headers=headers), 503, "media_off")
    r = await client.put(f"{BASE}/config", json={"enabled": True}, headers=headers)
    assert r.json()["enabled"] is True
    # zonder PLATFORM_MEDIA_ENABLED: alles 503 media_off (precies 0.6.0)
    app.state.media.enabled = False
    for method, path, body in routes():
        r = await client.request(method, path, json=body, headers=headers)
        failed(r, 503, "media_off")


# --- de instellingen ----------------------------------------------------------------------------


async def test_de_instellingen_hebben_grenzen(media_app, client, admin):  # noqa: F811
    settings, _ = media_app
    headers, _ = admin
    ceiling = settings.media_max_transcodes
    path = f"{BASE}/config"
    for wrong in ({"cpu_budget": 0.4}, {"cpu_budget": 16.5}, {"max_user_transcodes": 0},
                  {"bench": {"f": 1, "f_dec": 1}}, {"onbekend": 1}, {"cache_gb": 0}):  # fmt: skip
        failed(await client.put(path, json=wrong, headers=headers), 422, "invalid_input")
    params = failed(
        await client.put(path, json={"max_transcodes": ceiling + 1}, headers=headers),
        422,
        "invalid_input",
    )
    assert params == {"field": "max_transcodes", "max": ceiling}
    for ok in ({"cpu_budget": 0.5}, {"cpu_budget": 16}, {"max_transcodes": ceiling}):
        r = await client.put(path, json=ok, headers=headers)
        assert r.status_code == 200, r.text
    # alleen de velden die er staan; de rest blijft
    r = await client.put(path, json={"hdr_1080": True}, headers=headers)
    out = r.json()
    assert out["hdr_1080"] is True and out["cpu_budget"] == 16 and out["max_transcodes"] == ceiling
    assert (await overview(client, headers))["config"] == out
    # bewaard in de database, niet alleen in het geheugen
    async with maker(settings)() as session:
        stored = await MediaPolicy(None).get(session)
        assert stored.hdr_1080 is True and stored.cpu_budget == 16


# --- Stop ---------------------------------------------------------------------------------------


async def test_stop_sluit_de_sessie(media_app, client, admin):  # noqa: F811
    settings, _ = media_app
    admin_headers, _ = admin
    headers, user_id = await person(client, admin_headers, "bert@example.nl")
    node = await video(client, headers, settings, "bert.mp4")
    out = await play(client, headers, node["id"])
    [seen] = (await overview(client, admin_headers))["sessions"]
    assert seen["user"] == {"id": user_id, "display_name": "Bert"}
    assert seen["title"] == "bert.mp4" and seen["mode"] == "direct"
    assert out["sid"] not in str(await overview(client, admin_headers))  # nooit de sid
    r = await client.delete(f"{BASE}/sessions/{seen['id']}", headers=admin_headers)
    assert r.status_code == 204
    # de speler meldt het netjes en vraagt geen nieuwe sessie (web: 'stopped')
    failed(await file(client, out["sid"]), 403, "media_access_gone", "stopped")
    assert (await overview(client, admin_headers))["sessions"] == []
    r = await client.delete(f"{BASE}/sessions/{seen['id']}", headers=admin_headers)
    failed(r, 404, "not_found")
    failed(await client.delete(f"{BASE}/sessions/geen-id", headers=admin_headers), 422,
           "invalid_input")  # fmt: skip


# --- de redenen per dag -------------------------------------------------------------------------


def test_de_redenen_per_dag():
    from datetime import date, timedelta

    reasons = media_admin.Reasons()
    today = date(2026, 11, 1)
    reasons.count(["video_codec", "container"], today - timedelta(days=8))
    reasons.count(["container"], today - timedelta(days=6))
    reasons.count(["video_codec"], today)
    reasons.count(["video_codec", "audio_codec"], today)
    view = reasons.view(today)
    assert [d.day for d in view] == ["2026-10-26", "2026-11-01"]  # 7 dagen, de oudste eerst
    assert view[-1].counts == {"video_codec": 2, "audio_codec": 1}
    assert len(reasons.days) == 2  # wat ouder is dan 7 dagen, ging weg


async def test_de_redenen_tellen_bij_elke_nieuwe_sessie(tmp_path):
    media_admin.install()
    media_admin.install()  # een tweede keer doet niets
    assert getattr(sessions.create, "_counted", False)
    assert not getattr(sessions.create.__wrapped__, "_counted", False)  # type: ignore[attr-defined]
    made: list = []

    async def create(user, source, probe, plan, *args, **kwargs):
        if source == "kapot":
            raise RuntimeError("create faalde")
        made.append(source)
        return "sessie"

    counted = media_admin.counting(create)
    plan = Plan(mode="transcode", reasons=["video_codec", "hdr"])
    assert await counted(None, "film", None, plan) == "sessie"
    source = LoadtestFile(name="testsrc2-0-60s-720p.mkv", path=tmp_path / "x.mkv", size=1,
                          sha="0" * 64)  # fmt: skip
    await counted(None, source, None, plan)
    with pytest.raises(RuntimeError):
        await counted(None, "kapot", None, plan)
    assert made == ["film", source]
    [day] = media_admin.reasons.view()
    assert day.counts == {"video_codec": 1, "hdr": 1}  # de belastingtest en een fout tellen niet


# --- TMDB (Keuze 40) ----------------------------------------------------------------------------

KEY = "eyJhbGciOiJIUzI1NiJ9.geheim-van-jonas.abc123"


async def test_een_lege_sleutel(media_app, client, admin):  # noqa: F811
    """Sinds het samenvoegen van golf 5 is M8 er altijd (de test "zonder M8", met 503
    media_provider_unavailable, is weg); een lege sleutel haalt de validatie niet."""
    headers, _ = admin
    failed(await client.put(f"{BASE}/tmdb", json={"key": ""}, headers=headers), 422,
           "invalid_input")  # fmt: skip


class FakeSecrets:
    """De functies van M8 (providers/secrets.py), met een sleutel in het geheugen."""

    def __init__(self) -> None:
        self.key: str | None = None
        self.ok: bool | None = None
        self.fail: Exception | None = None

    async def save_tmdb(self, session, settings, key: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.key, self.ok = key, True

    async def forget_tmdb(self, session) -> None:
        self.key, self.ok = None, None

    async def verify_tmdb(self, session, settings) -> None:
        if self.fail is not None:
            if isinstance(self.fail, ProviderAuth):
                self.ok = False
            raise self.fail
        self.ok = True

    async def status(self, session, settings) -> dict:
        return {"set": self.key is not None, "set_at": None, "ok": self.ok}


@pytest.fixture
def m8(monkeypatch) -> FakeSecrets:
    """De echte secrets van M8 staan in test_media_beheer_samen.py; hier een nep om elke fout van
    M8 op bestelling te geven."""
    fake = FakeSecrets()
    for name in ("save_tmdb", "forget_tmdb", "verify_tmdb", "status"):
        monkeypatch.setattr(secrets, name, getattr(fake, name))
    return fake


async def test_de_sleutel_komt_nooit_terug(media_app, client, admin, m8, caplog):  # noqa: F811
    headers, _ = admin
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []
    r = await client.put(f"{BASE}/tmdb", json={"key": KEY}, headers=headers)
    assert r.status_code == 204 and r.content == b""
    assert m8.key == KEY
    seen.append(r.text)
    body = await overview(client, headers)
    seen.append(str(body))
    [tmdb] = [p for p in body["providers"] if p["name"] == "tmdb"]
    assert tmdb["key_set"] is True and tmdb["last_error"] is None
    r = await client.post(f"{BASE}/tmdb/test", headers=headers)
    assert r.status_code == 204 and r.content == b""
    seen.append((await client.get("/api/v1/admin/status", headers=headers)).text)
    # werkt hij niet meer (een 401 later): een melding, en nog steeds geen sleutel
    m8.fail = ProviderAuth("tmdb: " + KEY)  # zelfs als M8 hem in de fout zou zetten
    r = await client.post(f"{BASE}/tmdb/test", headers=headers)
    failed(r, 422, "media_provider_auth")
    seen.append(r.text)
    body = await overview(client, headers)
    seen.append(str(body))
    assert [n["code"] for n in body["notices"] if n["code"].startswith("media_provider")] == [
        "media_provider_auth"
    ]
    [tmdb] = [p for p in body["providers"] if p["name"] == "tmdb"]
    assert tmdb["last_error"] == "auth"
    r = await client.get("/api/v1/admin/status", headers=headers)
    assert "media_provider_auth" in [n["code"] for n in r.json()["notices"]]
    seen.append(r.text)
    r = await client.delete(f"{BASE}/tmdb", headers=headers)
    assert r.status_code == 204 and m8.key is None
    for text in [*seen, caplog.text]:
        assert KEY not in text and "geheim-van-jonas" not in text


async def test_de_fouten_van_tmdb(media_app, client, admin, m8):  # noqa: F811
    headers, _ = admin
    path = f"{BASE}/tmdb"
    m8.fail = ProviderAuth("werkt niet")
    failed(await client.put(path, json={"key": KEY}, headers=headers), 422, "media_provider_auth")
    m8.fail = ProviderUnavailable("onbereikbaar")
    r = await client.put(path, json={"key": KEY}, headers=headers)
    failed(r, 503, "media_provider_unavailable")
    m8.fail = ProviderRate(30)
    r = await client.put(path, json={"key": KEY}, headers=headers)
    assert failed(r, 503, "media_provider_rate")["retry_after"] == 30
    assert m8.key is None  # niets bewaard


# --- opnieuw proberen ---------------------------------------------------------------------------


async def test_opnieuw_proberen(media_app, client, admin):  # noqa: F811
    settings, _ = media_app
    headers, _ = admin
    async with maker(settings)() as session:
        session.add_all([
            Job(type="media.analyse", status="failed", attempts=3, error="kapot"),
            Job(type="media.lookup", status="failed", attempts=3, error="kapot"),
            Job(type="media.analyse", status="queued"),
            Job(type="preview", status="failed", attempts=3, error="niet van media"),
        ])  # fmt: skip
        await session.commit()
    assert (await overview(client, headers))["jobs"] == {"queued": 1, "running": 0, "failed": 2}
    r = await client.post(f"{BASE}/retry", headers=headers)
    assert r.status_code == 200 and r.json() == {"retried": 2}
    async with maker(settings)() as session:
        rows = (await session.execute(select(Job.type, Job.status, Job.attempts))).all()
    assert sorted(rows) == [("media.analyse", "queued", 0), ("media.analyse", "queued", 0),
                            ("media.lookup", "queued", 0), ("preview", "failed", 3)]  # fmt: skip
    assert (await client.post(f"{BASE}/retry", headers=headers)).json() == {"retried": 0}


# --- de cache leegmaken -------------------------------------------------------------------------


def old(path, data: bytes = b"x" * 100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    when = time.time() - 3600
    os.utime(path, (when, when))


async def test_audio_en_trick_leegmaken(media_app, client, admin, monkeypatch):  # noqa: F811
    settings, _ = media_app
    headers, _ = admin
    audio = settings.media_dir / "audio"
    for name in ("a" * 64 + ".mp4", "b" * 64 + ".mp4", "c" * 64 + ".1.mp4", "d" * 64 + ".mp4.part"):
        old(audio / name)
    trick = settings.media_dir / "trick"
    old(trick / ("e" * 64) / "0.jpg")
    old(trick / ("e" * 64) / "trick.vtt")
    other = settings.media_dir / "art" / "hoes.jpg"
    old(other)
    # een sessie speelt b nu; een remux maakt c nu
    monkeypatch.setattr(media_admin, "audio_in_use", lambda: {"b" * 64 + ".mp4"})
    monkeypatch.setitem(stream_audio._remuxes, "c" * 64 + ".1.mp4", SimpleNamespace(done=False))
    body = (await overview(client, headers))["cache"]["parts"]
    assert body["audio"] == 400 and body["trick"] == 200 and body["art"] == 100
    r = await client.post(f"{BASE}/cache/clear", json={"what": "audio"}, headers=headers)
    assert r.status_code == 204
    assert sorted(p.name for p in audio.iterdir()) == ["b" * 64 + ".mp4", "c" * 64 + ".1.mp4"]
    r = await client.post(f"{BASE}/cache/clear", json={"what": "trick"}, headers=headers)
    assert r.status_code == 204 and trick.is_dir() and list(trick.iterdir()) == []
    assert other.is_file()  # hoezen horen niet bij de cache
    r = await client.post(f"{BASE}/cache/clear", json={"what": "art"}, headers=headers)
    failed(r, 422, "invalid_input")


def test_audio_in_gebruik(monkeypatch):
    sha = "f" * 64

    def s(mode: str, position: int, closed: bool = False):
        return SimpleNamespace(plan=SimpleNamespace(mode=mode), sha=sha, position=position,
                               closed=closed)  # fmt: skip

    table = SimpleNamespace(open=lambda: [s("audio", 0), s("audio", 2), s("direct", 0)])
    monkeypatch.setattr(sessions, "table", lambda: table)
    monkeypatch.setattr(stream_audio, "_position", lambda session: session.position)
    assert media_admin.audio_in_use() == {f"{sha}.mp4", f"{sha}.2.mp4"}


@needs_fixtures
async def test_sessies_leegmaken_breekt_de_speler_niet(case: Case, monkeypatch):  # noqa: F811
    """De cache van de sessies leeg terwijl er gekeken wordt: wat achter de speler ligt gaat
    weg, de 60 s achter hem en wat hij nu vraagt blijven; daarna speelt hij gewoon door. De
    redenen van die sessie tellen mee op /beheer."""
    from app.media import cache

    monkeypatch.setattr(cache, "KEEP_BEHIND_MS", 2000)
    sid = await case.play()
    st = case.stream(sid)
    for n in range(st.layout.count):
        await case.seg(sid, n)
    last = st.layout.count - 1
    await until(lambda: len(st.segments) == st.layout.count)
    r = await case.client.post(f"{BASE}/cache/clear", json={"what": "sessions"},
                               headers=case.headers)  # fmt: skip
    assert r.status_code == 204
    assert last in st.segments and 0 not in st.segments
    assert sorted(p.name for p in st.dir.glob("*.m4s")) == sorted(f"{n}.m4s" for n in st.segments)
    # de speler gaat gewoon door (ook terug naar het begin: opnieuw gemaakt)
    await case.seg(sid, last)
    await case.seg(sid, 0)
    assert not session_of(sid).closed
    body = await overview(case.client, case.headers)
    [seen] = body["sessions"]
    assert seen["mode"] == "transcode"
    [day] = body["reasons"]
    assert sum(day["counts"].values()) >= 1


# --- de meting ----------------------------------------------------------------------------------


async def test_de_meting(media_app, client, admin, monkeypatch):  # noqa: F811
    settings, fake = media_app
    headers, _ = admin
    gate = asyncio.Event()

    async def bench(job):
        assert job.args.encode_s == media_admin.BENCH_ENCODE_S
        await gate.wait()
        return {"f": 0.82, "f_dec": 0.31, "cores": 2.5, "dec_cores": 1.0}

    fake.on(Op.BENCH, bench)
    r = await client.post(f"{BASE}/bench", headers=headers)
    assert r.status_code == 202, r.text
    job = uuid.UUID(r.json()["job"])
    assert media_admin._bench is not None and media_admin._bench.id == job
    failed(await client.post(f"{BASE}/bench", headers=headers), 409, "media_bench_busy")
    gate.set()
    assert media_admin._bench.task is not None
    await asyncio.wait_for(media_admin._bench.task, 10)
    assert media_admin._bench.error is None
    body = await overview(client, headers)
    assert body["bench"]["f"] == 0.82 and body["bench"]["f_dec"] == 0.31
    assert app.state.media.bench_factor == 0.82
    async with maker(settings)() as session:
        stored = await MediaPolicy(None).get(session)
        assert stored.bench is not None and stored.bench.f == 0.82

    # een meting die faalt, laat de vorige staan
    async def broken(job):
        raise FW.OpError(Code.FAILED, "ffmpeg stopte")

    fake.on(Op.BENCH, broken)
    r = await client.post(f"{BASE}/bench", headers=headers)
    assert r.status_code == 202
    await asyncio.wait_for(media_admin._bench.task, 10)
    assert media_admin._bench.error is not None
    assert (await overview(client, headers))["bench"]["f"] == 0.82
    # niet tijdens de belastingtest, en niet als er iets wordt omgezet
    monkeypatch.setattr(loadtest, "running", lambda: True)
    failed(await client.post(f"{BASE}/bench", headers=headers), 409, "media_bench_busy")
    monkeypatch.setattr(loadtest, "running", lambda: False)
    monkeypatch.setattr(media_admin, "transcodes_running", lambda: 1)
    failed(await client.post(f"{BASE}/bench", headers=headers), 409, "media_bench_busy")


@needs_fixtures
async def test_omzettingen_tellen_voor_de_meting(case: Case):  # noqa: F811
    assert media_admin.transcodes_running() == 0
    sid = await case.play()
    await case.seg(sid, 0)
    assert media_admin.transcodes_running() == 1
    r = await case.client.post(f"{BASE}/bench", headers=case.headers)
    failed(r, 409, "media_bench_busy")
    await streams.close(session_of(sid))
    assert media_admin.transcodes_running() == 0


# --- de import via /beheer ----------------------------------------------------------------------


async def test_de_import_via_beheer(media_app, client, admin):  # noqa: F811
    settings, _ = media_app
    headers, tokens = admin
    top = (await root(client, headers))["folder"]["id"]
    target = await mkdir(client, headers, top, "Films")
    src = importer.import_root(settings)
    old(src / "Bron" / "film.mkv", b"f" * 50)
    old(src / "Bron" / "sub" / "serie.mkv", b"s" * 30)
    (src / "Leeg").mkdir()
    r = await client.get(f"{BASE}/import/sources", headers=headers)
    assert r.status_code == 200, r.text
    found = {s["path"]: s for s in r.json()}
    assert set(found) == {"Bron", "Leeg"}
    assert (found["Bron"]["files"], found["Bron"]["bytes"]) == (2, 80)
    owner = tokens["user"]["id"]
    body = {"source": "Bron", "target_id": target, "owner_id": owner, "move": False}
    r = await client.post(f"{BASE}/import", json=body, headers=headers)
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    failed(await client.post(f"{BASE}/import", json=body, headers=headers), 409,
           "media_import_busy")  # fmt: skip
    view = (await overview(client, headers))["import"]
    assert view["job"] == job and view["state"] in ("queued", "running")
    assert view["source"] == "Bron" and view["move"] is False
    bad = {**body, "source": "../buiten"}
    failed(await client.post(f"{BASE}/import", json=bad, headers=headers), 422,
           "media_import_path")  # fmt: skip
