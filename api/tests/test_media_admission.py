"""De toelating (Keuze 26, M4): de regel met de reserve tegen de echte staat, zoals play hem
gebruikt (view → decide → reserve), en de worker die nooit `busy` zegt tegen een goedgekeurd plan.

De rekenvoorbeelden zijn die van de proef (de kostentabel van Keuze 24, f = 1,0): een H.264-bron
van 1080p24 kost 1,42 op 1080 veryfast, 0,95 op 1080 superfast, 0,74 en 0,62 op 720, en de
reserve per vrije plaats is 480 veryfast (0,56). B = 3,0, dus hooguit 2,7 in gebruik.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import BenchResult, MediaConfig
from app.media import admission, public, runtime, sessions, streams
from app.media import decide as D
from app.media import producer as producers
from app.media.errors import MediaBusy
from app.media.state import MediaPolicy, MediaState
from app.media.types import Choice, Plan, Rung, WorkerCaps
from app.media.wire import Op, WorkClass
from app.media.worker.server import Config, Places
from app.office.state import OfficeState
from tests.media import fixtures as F
from tests.test_media_contract import media_app, profile, source_probe, upload  # noqa: F401
from tests.test_media_decide import LOGGED, PUBLIC_TC, film, profile_for, synthetic
from tests.test_media_sessions import content

H1080 = Rung(height=1080, preset="veryfast", codec="h264")
H1080S = Rung(height=1080, preset="superfast", codec="h264")
H720 = Rung(height=720, preset="veryfast", codec="h264")
H720S = Rung(height=720, preset="superfast", codec="h264")
ROOM = admission.DISK_MIN * 4


pytestmark = pytest.mark.anyio


@pytest.fixture
async def host(settings, monkeypatch):
    """Een app met alleen een staat (de toelating leest niets anders), gebonden zoals de
    lifespan doet; de schijf heeft ruim plaats."""
    app = SimpleNamespace(state=SimpleNamespace())
    app.state.media = MediaState(enabled=True, worker="ok")
    app.state.media_policy = MediaPolicy(MediaConfig())
    bound = settings.model_copy(update={"media_enabled": True, "media_bench_fixed": False})
    runtime.bind(app, bound)
    monkeypatch.setattr(admission, "disk_free", lambda path=None: ROOM)
    yield app
    runtime.unbind()


def configure(host, settings=None, **config) -> None:
    host.state.media_policy = MediaPolicy(MediaConfig(**config))
    if settings is not None:
        runtime.bind(host, runtime.settings().model_copy(update=settings))


def watch(p, browser: str, user: uuid.UUID, *, link: uuid.UUID | None = None,
          policy=LOGGED, **choice):  # fmt: skip
    """Wat play doet: view, decide, reserve. Geeft (plan, grant)."""
    view = admission.view(user, link)
    prof = profile_for(browser, p)
    for_source = public.for_source(policy, admission.config(), p)
    plan = D.decide(p, prof, Choice(**choice), for_source, view)
    return plan, admission.reserve(plan, user, link)


def busy(fn) -> tuple[str, float]:
    """(reden, Retry-After) van een weigering, uit decide (HTTPException) of reserve."""
    try:
        fn()
    except MediaBusy as exc:
        return exc.reason, exc.retry_after
    except HTTPException as exc:
        assert exc.status_code == 503, exc.detail
        assert exc.detail["code"] == "media_busy", exc.detail
        return exc.detail["params"]["reason"], float(exc.headers["Retry-After"])
    raise AssertionError("geen weigering")


# --- de regel met de reserve ---------------------------------------------------------------------


async def test_de_reserve_1080_en_twee_keer_720_superfast(host):
    a, b = uuid.uuid4(), uuid.uuid4()
    one, _ = watch(film(), "chrome_ci", a)
    assert one.video == H1080 and one.cost == pytest.approx(1.42)
    two, _ = watch(film(), "chrome_ci", a)
    assert two.video == H720S and "budget" in two.reasons
    three, _ = watch(film(), "chrome_ci", b)
    assert three.video == H720S
    snap = admission.snapshot()
    assert snap.in_use == pytest.approx(1.42 + 0.62 + 0.62)
    assert snap.places_used == 3 and len(snap.grants) == 3
    assert busy(lambda: watch(film(), "chrome_ci", b)) == ("cpu", admission.RETRY)


async def test_max_2_en_dan_een_trede_lager(host):
    configure(host, max_transcodes=2)
    user = uuid.uuid4()
    assert watch(film(), "chrome_ci", user)[0].video == H1080
    # met 1 vrije plaats geen reserve meer: 1080 superfast past nog (1,42 + 0,95 ≤ 2,7)
    second, _ = watch(film(), "chrome_ci", user)
    assert second.video == H1080S and "budget" not in second.reasons
    assert busy(lambda: watch(film(), "chrome_ci", uuid.uuid4()))[0] == "cpu"


async def test_2_per_gebruiker(host):
    configure(host, {"media_max_transcodes": 16}, cpu_budget=16, max_transcodes=16)
    user, other = uuid.uuid4(), uuid.uuid4()
    watch(film(), "chrome_ci", user)
    watch(film(), "chrome_ci", user)
    assert busy(lambda: watch(film(), "chrome_ci", user)) == ("user", admission.RETRY)
    assert watch(film(), "chrome_ci", other)[0].video == H1080


async def test_1_per_link_en_nooit_de_laatste_plek(host):
    owner, link = uuid.uuid4(), uuid.uuid4()
    big = synthetic("hevc", "hvc1.1.6.L120.90", 1920, 1080)
    plan, grant = watch(big, "chrome_ci", owner, link=link, policy=PUBLIC_TC)
    assert plan.video == H720 and grant.link_id == link
    assert busy(lambda: watch(big, "chrome_ci", owner, link=link, policy=PUBLIC_TC))[0] == "link"
    # de eigenaar zelf telt los van zijn link
    assert watch(film(), "chrome_ci", owner)[0].mode == "transcode"
    # een andere link: 1 plaats vrij, en een link neemt nooit de laatste plek
    other = uuid.uuid4()
    assert busy(lambda: watch(big, "chrome_ci", owner, link=other, policy=PUBLIC_TC))[0] == "cpu"


async def test_het_plafond_uit_de_omgeving(host):
    configure(host, {"media_max_transcodes": 1}, max_transcodes=3)
    assert admission.max_transcodes() == 1
    assert admission.config().max_transcodes == 1
    watch(film(), "chrome_ci", uuid.uuid4())
    assert busy(lambda: watch(film(), "chrome_ci", uuid.uuid4()))[0] == "cpu"


async def test_de_plaatsen_van_de_worker_tellen_mee(host):
    host.state.media.caps = WorkerCaps(protocol=1, max_transcodes=1)
    assert admission.max_transcodes() == 1
    watch(film(), "chrome_ci", uuid.uuid4())
    assert busy(lambda: watch(film(), "chrome_ci", uuid.uuid4()))[0] == "cpu"


async def test_de_factor_f(host):
    host.state.media.bench_factor = 1.5  # een tragere machine dan de referentie
    plan, _ = watch(film(), "chrome_ci", uuid.uuid4())
    assert plan.video == H720 and "budget" in plan.reasons
    admission.admission().grants.clear()
    # de meting op /beheer gaat voor de factor van de staat
    measured = BenchResult(f=1.0, f_dec=1.0, at="2026-01-01T00:00:00Z")  # type: ignore[arg-type]
    configure(host, bench=measured)
    assert admission.factors() == (1.0, 1.0)
    assert watch(film(), "chrome_ci", uuid.uuid4())[0].video == H1080
    # met PLATFORM_MEDIA_BENCH_FIXED telt alleen de tabel, wat er ook gemeten is
    slow = BenchResult(f=2.0, f_dec=2.0, at="2026-01-01T00:00:00Z")  # type: ignore[arg-type]
    configure(host, {"media_bench_fixed": True}, bench=slow)
    assert admission.factors() == (1.0, 1.0)


async def test_schijf_vol(host, monkeypatch):
    monkeypatch.setattr(admission, "disk_free", lambda path=None: admission.DISK_MIN - 1)
    user = uuid.uuid4()
    assert busy(lambda: watch(film(), "chrome_ci", user)) == ("disk", admission.RETRY)
    # remux ook (Keuze 31), direct play altijd
    remux = Plan(mode="remux", video="copy", audio="copy", cost=0.05)
    with pytest.raises(MediaBusy) as caught:
        admission.reserve(remux, user, None)
    assert caught.value.reason == "disk"
    direct = Plan(mode="direct", video="copy", audio="copy")
    assert admission.reserve(direct, user, None).cost == 0
    assert admission.snapshot().grants == []


REMUX = Plan(mode="remux", video="copy", audio="copy", cost=0.05)


async def test_hooguit_6_remuxen(host):
    users = [uuid.uuid4(), uuid.uuid4()]
    for user in users:
        for _ in range(admission.REMUX_PER_USER):
            admission.reserve(REMUX, user, None)
    assert len(admission.admission().remuxes()) == admission.REMUX_PLACES
    with pytest.raises(MediaBusy) as caught:
        admission.reserve(REMUX, uuid.uuid4(), None)
    assert (caught.value.reason, caught.value.retry_after) == ("worker", admission.RETRY_WORKER)


async def test_remux_per_gebruiker_en_een_link_zonder_eigen_grens(host):
    """Review golf 2, S3: eerst kon één account (8 sessies) alle 6 plaatsen voor remux nemen,
    en kreeg iedereen daarna 503. Een publieke link heeft geen eigen grens voor remux
    (na-controle golf 2): hij heeft hooguit 2 sessies en de derde sluit de oudste (M3); een
    grens hier weigerde de tweede kijker (test_media_wissel.py)."""
    mallory, alice, link = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for _ in range(admission.REMUX_PER_USER):
        admission.reserve(REMUX, mallory, None)
    with pytest.raises(MediaBusy) as caught:
        admission.reserve(REMUX, mallory, None)
    assert (caught.value.reason, caught.value.retry_after) == ("user", admission.RETRY)
    # een ander kan nog, en een link van mallory telt los van mallory zelf
    assert admission.reserve(REMUX, alice, None).mode == "remux"
    assert admission.reserve(REMUX, mallory, link).link_id == link
    assert admission.reserve(REMUX, mallory, link).link_id == link  # de tweede kijker
    # het totaal blijft 6
    with pytest.raises(MediaBusy) as caught:
        admission.reserve(REMUX, mallory, link)
    assert caught.value.reason == "worker"


async def test_een_wissel_telt_de_oude_plaats_niet(host):
    """Na-controle golf 2: play met replace (een ander audiospoor, een lagere trede). De grant van
    de oude sessie telt niet mee, want die gaat in dezelfde beurt dicht: wie zijn grens of de
    laatste plaats had, wisselt zonder 503, en houdt zijn trede."""
    user, owner, link = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    held = [admission.reserve(REMUX, user, None) for _ in range(admission.REMUX_PER_USER)]
    assert busy(lambda: admission.reserve(REMUX, user, None))[0] == "user"
    with admission.replacing(held[0]):
        assert admission.reserve(REMUX, user, None).mode == "remux"
    assert admission.admission().ignored == set()
    assert busy(lambda: admission.reserve(REMUX, user, None))[0] == "user"
    # een transcode via een link (1 per link)
    big = synthetic("hevc", "hvc1.1.6.L120.90", 1920, 1080)
    plan, grant = watch(big, "chrome_ci", owner, link=link, policy=PUBLIC_TC)
    assert busy(lambda: watch(big, "chrome_ci", owner, link=link, policy=PUBLIC_TC))[0] == "link"
    with admission.replacing(grant):
        again, _ = watch(big, "chrome_ci", owner, link=link, policy=PUBLIC_TC)
    assert again.video == plan.video
    for g in admission.snapshot().grants:
        admission.release(g)
    # dezelfde trede: zonder replacing geeft een tweede 1080 hier 720 superfast (de reserve)
    one, first = watch(film(), "chrome_ci", user)
    assert one.video == H1080
    with admission.replacing(first):
        two, _ = watch(film(), "chrome_ci", user)
    assert two.video == H1080
    # release binnen het blok: dan is de grant echt weg, ook na het blok
    with admission.replacing(first):
        admission.release(first)
    assert first.id not in admission.admission().grants and admission.admission().ignored == set()
    # zonder grant (direct play, of een sid van een ander) verandert er niets
    with admission.replacing(None):
        assert admission.admission().ignored == set()


def _worker_caps(max_procs: int) -> WorkerCaps:
    limits = Config(socket=Path("w.sock"), max_procs=max_procs, max_transcodes=3).limits()
    return WorkerCaps(protocol=1, max_procs=max_procs, max_transcodes=3,
                      places={wc.value: n for wc, n in limits.items()})  # fmt: skip


async def test_wat_de_toelating_goedkeurt_past_altijd_in_de_worker(host):
    """Review golf 2, C5: de plaatsen van de worker (3 + 6 + 1 + 2) zijn samen meer dan
    max_procs (10). De toelating houdt de achtergrond en de korte plaatsen vrij: met die vol
    krijgt elk goedgekeurd plan nog een plaats in de worker (Keuze 12)."""
    configure(host, {"media_max_transcodes": 3}, cpu_budget=16, max_user_transcodes=3)
    host.state.media.caps = _worker_caps(10)
    assert admission.live_max() == 7
    worker = Places(Config(socket=Path("w.sock"), max_procs=10, max_transcodes=3).limits(), 10)
    assert worker.take(WorkClass.BACKGROUND, 1)
    assert worker.take(WorkClass.SHORT, 1) and worker.take(WorkClass.SHORT, 1)
    granted = []
    for _ in range(2):
        user = uuid.uuid4()
        for _ in range(admission.REMUX_PER_USER):
            granted.append(admission.reserve(REMUX, user, None))
    for _ in range(3):
        try:
            granted.append(watch(film(), "chrome_ci", uuid.uuid4())[1])
        except (MediaBusy, HTTPException):
            break
    assert len(granted) == 7  # 6 remuxen en 1 transcode; de tweede transcode niet
    assert busy(lambda: watch(film(), "chrome_ci", uuid.uuid4()))[0] == "worker"
    for grant in granted:
        wc = WorkClass.TRANSCODE if grant.mode == "transcode" else WorkClass.REMUX
        assert worker.take(wc, 1), grant
    # weer een plaats vrij: dan past een transcode wel
    admission.release(granted.pop(0))
    assert watch(film(), "chrome_ci", uuid.uuid4())[0].mode == "transcode"


async def test_een_kleine_worker_heeft_die_grens_niet(host):
    # het harnas en de belastingtest (max_procs 3, regel 13): niets over na achtergrond en kort
    host.state.media.caps = _worker_caps(3)
    assert admission.live_max() is None
    for _ in range(admission.REMUX_PER_USER):
        admission.reserve(REMUX, uuid.uuid4(), None)
    host.state.media.caps = None  # zonder caps: PLATFORM_MEDIA_MAX_PROCS (standaard 10)
    assert admission.live_max() == runtime.settings().media_max_procs - 3


# --- de nieuwe gevallen van §2 M4 -------------------------------------------------------------


async def test_eerste_kijker_vp9_hdr_720(host):
    hdr = synthetic("hevc", "hvc1.2.4.L120.90", 1920, 1080, hdr="hdr10", depth=10)
    assert admission.budget() == 3.0
    plan, grant = watch(hdr, "chromium141", uuid.uuid4())
    assert plan.video == Rung(height=720, preset="veryfast", codec="vp9")
    assert plan.tonemap == "pq" and grant.cost == pytest.approx(1.21)


async def test_een_4k_bron_telt_dubbel_en_daarna_nog_een_kijker(host):
    uhd = synthetic("hevc", "hvc1.1.6.L153.B0", 3840, 2160)
    plan, grant = watch(uhd, "chrome_ci", uuid.uuid4())
    assert plan.video == H1080S and grant.places == 2 and grant.cost == pytest.approx(2.01)
    assert admission.snapshot().places_used == 2
    after, _ = watch(film(), "chrome_ci", uuid.uuid4())
    assert after.video == H720S
    assert admission.snapshot().in_use == pytest.approx(2.63)
    assert busy(lambda: watch(film(), "chrome_ci", uuid.uuid4()))[0] == "cpu"


async def test_ds_start_geeft_b_min_1(host):
    first, _ = watch(film(), "chrome_ci", uuid.uuid4())
    assert first.video == H1080
    admission.admission().grants.clear()
    host.state.office = OfficeState()
    host.state.office.health = "starting"
    assert admission.budget() == pytest.approx(2.0)
    assert admission.snapshot().budget == pytest.approx(2.0)
    # 1,8 te besteden: met de reserve van 2 vrije plaatsen (1,12) past alleen 720 superfast
    lower, grant = watch(film(), "chrome_ci", uuid.uuid4())
    assert lower.video == H720S and "budget" in lower.reasons
    # wie al loopt, loopt door; DS klaar: het hele budget terug
    host.state.office.health = "ok"
    assert admission.budget() == 3.0
    assert grant.id in admission.admission().grants


async def test_reserve_kijkt_opnieuw(host):
    """Tussen view en reserve kan een ander het budget nemen: reserve zegt dan nee."""
    a, b = uuid.uuid4(), uuid.uuid4()
    view = admission.view(a, None)
    plan = D.decide(film(), profile_for("chrome_ci", film()), Choice(),
                    public.for_source(LOGGED, admission.config(), film()), view)  # fmt: skip
    watch(film(), "chrome_ci", b)
    watch(film(), "chrome_ci", b)
    with pytest.raises(MediaBusy) as caught:
        admission.reserve(plan, a, None)
    assert caught.value.reason == "cpu"


async def test_teruggeven_is_idempotent(host):
    user = uuid.uuid4()
    _, grant = watch(film(), "chrome_ci", user)
    _, other = watch(film(), "chrome_ci", user)
    admission.release(grant)
    admission.release(grant)
    assert admission.snapshot().in_use == pytest.approx(other.cost)
    admission.release(other)
    assert admission.snapshot().in_use == 0 and admission.snapshot().places_used == 0
    # een grant die nooit vastlag (direct play)
    direct = admission.reserve(Plan(mode="direct", video="copy", audio="copy"), user, None)
    admission.release(direct)
    assert admission.snapshot().grants == []


# --- de worker (via play, met de nep-worker) ---------------------------------------------------

needs_fixtures = pytest.mark.skipif(not F.available(), reason="ffmpeg ontbreekt (fixtures)")


async def _film(client, headers, settings, name: str = "film.mkv") -> uuid.UUID:
    return await upload(
        client, headers, settings, name, content(name), source_probe("transcode", "h264")
    )


async def _play(client, headers, node: uuid.UUID):
    return await client.post(
        f"/api/v1/media/{node}/play", json={"profile": profile("h264")}, headers=headers
    )


@needs_fixtures
async def test_de_worker_zegt_busy(media_app, client, admin, monkeypatch):  # noqa: F811
    settings, fake = media_app
    headers, _ = admin
    node = await _film(client, headers, settings)
    monkeypatch.setattr(producers, "START_BUSY_S", 0.3)
    fake.fault("busy", op=Op.STREAM, times=1000)
    r = await _play(client, headers, node)
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "media_busy" and detail["params"]["reason"] == "worker"
    assert r.headers["retry-after"] == "2"
    # niets blijft hangen: de grant terug, geen stroom
    await _settle()
    assert admission.snapshot().grants == []
    assert streams.all() == []
    fake.clear_faults()
    assert (await _play(client, headers, node)).status_code == 200


@needs_fixtures
async def test_een_goedgekeurd_plan_krijgt_nooit_busy(media_app, client, admin):  # noqa: F811
    """De worker heeft precies zoveel plaatsen als de toelating: twee kijkers die steeds
    springen (elke sprong een nieuwe run), en nooit een 503 van de worker."""
    settings, fake = media_app
    headers, _ = admin
    fake.limits[WorkClass.TRANSCODE] = 2
    runtime.app().state.media.caps = WorkerCaps(protocol=1, max_transcodes=2)
    fake.speed = 20
    runtime.app().state.media_policy.config = MediaConfig(max_user_transcodes=3)
    ids = [await _film(client, headers, settings, f"film{i}.mkv") for i in range(3)]
    outs = []
    for node in ids[:2]:
        r = await _play(client, headers, node)
        assert r.status_code == 200, r.text
        outs.append(r.json())
    # de derde: nee van de toelating (geen plaats), niet van de worker
    r = await _play(client, headers, ids[2])
    assert r.status_code == 503 and r.json()["detail"]["params"]["reason"] == "cpu"
    for _ in range(3):
        for out in outs:
            for n in (8, 0, 5):
                r = await client.get(f"/api/v1/stream/{out['sid']}/{n}.m4s", timeout=30)
                assert r.status_code == 200, (n, r.status_code, r.text)
    assert fake.processes <= 2
    for out in outs:
        await client.delete(f"/api/v1/stream/{out['sid']}")
    await _settle()
    assert admission.snapshot().grants == []


async def _settle() -> None:
    await asyncio.gather(*list(sessions._tasks), return_exceptions=True)
