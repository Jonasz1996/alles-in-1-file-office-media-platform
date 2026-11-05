"""De regels van de kijkvoortgang en het geheugen met gebundeld schrijven (M7, Keuze 36).

De Store draait hier met een eigen klok en zonder schrijver op de achtergrond: een test laat de
tijd lopen en roept flush_due aan op het moment dat de schrijver dat zou doen. Eén test draait
met de echte schrijver, en één met runtime.stop (de M0-hotfix: netjes stoppen schrijft alles
weg).
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from datetime import UTC

import pytest

from app import events
from app.main import app
from app.media import progress, runtime
from app.media.progress import Entry, Store
from app.media.types import Chapter, Probe, ProgressIn
from app.models import PlaybackState
from tests.test_files import put, root
from tests.test_media_sessions import blob_of, content, maker

pytestmark = pytest.mark.anyio

MIN = 60.0
HOUR = 3600.0
START = dt.datetime(2026, 10, 1, 20, 0, tzinfo=UTC)


class Clock:
    """Een monotone klok en een wandklok die samen lopen."""

    def __init__(self):
        self.t = 1000.0

    def mono(self) -> float:
        return self.t

    def now(self) -> dt.datetime:
        return START + dt.timedelta(seconds=self.t - 1000.0)

    def advance(self, seconds: float) -> None:
        self.t += seconds


def body(position_s: float, duration_s: float = HOUR, event="tick", origin="A", seq=1,
         started: dt.datetime = START, **extra) -> ProgressIn:  # fmt: skip
    return ProgressIn(
        position_ms=int(position_s * 1000),
        duration_ms=int(duration_s * 1000),
        event=event,
        origin=origin,
        seq=seq,
        started_at=started,
        **extra,
    )


class World:
    def __init__(self, settings, user_id, nodes, clock, store):
        self.settings = settings
        self.user_id = user_id
        self.nodes = nodes
        self.clock = clock
        self.store = store
        self.seq = 0

    async def put(self, node, position_s, *, sha=None, **kw) -> str:
        if "seq" not in kw:
            self.seq += 1
            kw["seq"] = self.seq
        return await self.store.put(self.user_id, node, sha, body(position_s, **kw))

    async def row(self, node) -> PlaybackState | None:
        async with maker(self.settings)() as session:
            return await session.get(PlaybackState, (self.user_id, "", node))

    def entry(self, node) -> Entry:
        return self.store.entries[self.user_id][node]


async def files(client, headers, settings, count: int) -> list[uuid.UUID]:
    parent = (await root(client, headers))["folder"]["id"]
    out = []
    for n in range(count):
        r = await put(client, headers, parent, f"film{n}.mkv", content(f"film{n}", 2000))
        assert r.status_code == 201, r.text
        out.append(uuid.UUID(r.json()["id"]))
    return out


@pytest.fixture
async def world(settings, client, admin):
    headers, tokens = admin
    user_id = uuid.UUID(tokens["user"]["id"])
    nodes = await files(client, headers, settings, 20)
    clock = Clock()
    store = Store(maker(settings), clock=clock.mono, wall=clock.now, background=False)
    yield World(settings, user_id, nodes, clock, store)
    await store.close()


# De regels (puur)


def test_gezien_bij_90_procent():
    assert not progress.is_watched(int(0.89 * 3_600_000), 3_600_000, None)
    assert progress.is_watched(int(0.9 * 3_600_000) + 1, 3_600_000, None)
    assert not progress.is_watched(10_000, 0, None)  # zonder duur nooit


def test_gezien_voorbij_de_aftiteling():
    # Aftiteling op 80 %: daarvoor niet, erna wel (ver voor de 90 % en de 3 minuten)
    assert not progress.is_watched(2_800_000, 3_600_000, 2_880_000)
    assert progress.is_watched(2_880_000, 3_600_000, 2_880_000)
    # Een onzinnige aftiteling (0 of voorbij het eind) telt niet
    assert not progress.is_watched(1, 3_600_000, 0)
    assert not progress.is_watched(2_880_000, 3_600_000, 4_000_000)


def test_drie_minuten_van_het_eind_alleen_bij_lange_items():
    # Een aflevering van 20 minuten: 17 minuten is 85 %, en precies 3 minuten van het eind
    episode = 20 * 60_000
    assert not progress.is_watched(episode - 3 * 60_000, episode, None)
    assert progress.is_watched(episode - 3 * 60_000 + 1, episode, None)
    # Een kort item (6 minuten): 2 minuten van het eind is nog niet gezien, 90 % wel
    short = 6 * 60_000
    assert not progress.is_watched(short - 2 * 60_000, short, None)
    assert progress.is_watched(int(0.9 * short), short, None)
    # Precies 10 minuten is niet langer dan 10 minuten
    ten = 10 * 60_000
    assert not progress.is_watched(ten - 2 * 60_000, ten, None)


def test_een_vervangen_bestand():
    a, b = "a" * 64, "b" * 64
    assert progress.replaced(a, 3_600_000, b, 2_400_000)  # andere blob, 33 % korter
    assert not progress.replaced(a, 3_600_000, b, 3_700_000)  # andere blob, < 5 %
    assert not progress.replaced(a, 3_600_000, a, 100)  # dezelfde blob
    assert not progress.replaced(a, 3_600_000, b, None)  # duur nog niet bekend
    assert not progress.replaced(None, 3_600_000, b, 100)


def test_een_lange_origin_past_in_de_kolom():
    assert progress.origin_key("tab-1") == "tab-1"
    long_a, long_b = "x" * 63 + "a", "x" * 63 + "b"
    assert len(progress.origin_key(long_a)) == 36
    assert progress.origin_key(long_a) != progress.origin_key(long_b)
    assert progress.origin_key(long_a) == progress.origin_key(long_a)


async def test_apply_knipt_te_grote_getallen_af(world):
    """Na-controle van golf 4 (S2): wie Store.put zonder de router aanroept (die geeft 422),
    krijgt de getallen afgeknipt tot wat SQLite kan bewaren; de stand komt in de database en
    gaat niet verloren in de schrijver per stand."""
    one, two = world.nodes[0], world.nodes[1]
    # Zonder duur: de positie zelf afgeknipt
    big = body(0, duration_s=0, event="pause", seq=2**70, audio=2**70)
    big = big.model_copy(update={"position_ms": 10**19})
    assert await world.store.put(world.user_id, one, None, big) == "ok"
    # Een duur van 10**19 ms: afgeknipt, en de positie erbinnen (gezien, dus 0)
    long = body(0, event="pause").model_copy(update={"position_ms": 10**19, "duration_ms": 10**19})
    assert await world.store.put(world.user_id, two, None, long) == "ok"
    await world.store.flush_user(world.user_id)
    row = await world.row(one)
    assert row is not None
    assert row.seq == progress.SEQ_MAX and row.audio is None
    assert row.position_ms == progress.MS_MAX and row.duration_ms == 0
    row = await world.row(two)
    assert row is not None and row.watched
    assert row.duration_ms == progress.MS_MAX and row.position_ms == 0


# Gezien, play_count en de positie


async def test_gezien_zet_watched_play_count_en_positie_0(world):
    node = world.nodes[0]
    assert await world.put(node, 30 * MIN) == "ok"
    world.clock.advance(15)
    assert await world.put(node, 55 * MIN) == "ok"  # 91,6 %
    row = await world.row(node)
    assert (row.watched, row.play_count, row.position_ms) == (True, 1, 0)
    assert progress.aware(row.watched_at) == world.clock.now()
    assert progress.aware(row.play_started_at) == START


async def test_de_aftiteling_uit_de_probe(world, settings):
    node = world.nodes[0]
    sha = await blob_of(settings, str(node))
    probe = Probe(
        duration_ms=3_600_000,
        chapters=[
            Chapter(start_ms=0, end_ms=2_700_000, title="Hoofdstuk 1"),
            Chapter(start_ms=2_700_000, end_ms=3_600_000, title="End Credits"),
        ],
    )
    from app.media import analysis

    await analysis.save_probe(maker(settings), sha, probe)
    # Zonder duur in de PUT: de duur uit de probe
    assert await world.put(node, 44 * MIN, sha=sha, duration_s=0) == "ok"
    assert not world.entry(node).watched
    assert world.entry(node).duration_ms == 3_600_000
    world.clock.advance(15)
    assert await world.put(node, 45 * MIN, sha=sha) == "ok"  # 75 %: voorbij de aftiteling
    assert world.entry(node).watched


async def test_een_kort_item_telt_de_drie_minuten_niet(world):
    node = world.nodes[0]
    assert await world.put(node, 4 * MIN, duration_s=6 * MIN) == "ok"
    assert not world.entry(node).watched
    assert world.entry(node).position_ms == 240_000
    world.clock.advance(15)
    assert await world.put(node, 5.5 * MIN, duration_s=6 * MIN) == "ok"
    assert world.entry(node).watched


async def test_play_count_een_keer_per_sessie(world):
    node = world.nodes[0]
    world.clock.advance(30 * MIN)  # de sessie begon een half uur geleden
    assert await world.put(node, 55 * MIN, event="pause") == "ok"
    assert world.entry(node).play_count == 1
    # Dezelfde sessie speelt door tot het eind: telt niet nog eens
    world.clock.advance(60)
    assert await world.put(node, 59 * MIN, event="end") == "ignored"
    assert world.entry(node).play_count == 1
    # Een nieuwe sessie die bij de aftiteling instapt (na een herstart van de speler): ook niet
    world.clock.advance(60)
    late = world.clock.now()
    assert await world.put(node, 56 * MIN, started=late) == "ignored"
    world.clock.advance(15)
    await world.put(node, 59 * MIN, event="end", started=late)
    assert world.entry(node).play_count == 1
    # Een korte clip opnieuw van voren af: dat is een nieuwe kijkbeurt
    clip = world.nodes[1]
    for n in range(3):
        world.clock.advance(60)
        again = world.clock.now()
        await world.put(clip, 10, duration_s=40, started=again)
        world.clock.advance(15)
        await world.put(clip, 40, duration_s=40, event="end", started=again)
        assert world.entry(clip).play_count == n + 1
        assert world.entry(clip).watched


async def test_end_is_gezien(world):
    node = world.nodes[0]
    assert await world.put(node, 20 * MIN, event="end") == "ok"
    e = world.entry(node)
    assert (e.watched, e.position_ms, e.play_count) == (True, 0, 1)


async def test_de_positie_gaat_niet_voorbij_de_duur(world):
    node = world.nodes[0]
    assert await world.put(node, 20 * MIN, duration_s=10 * MIN, event="seek") == "ok"
    # 20 minuten van 10: dat is het eind, dus gezien
    assert world.entry(node).watched


# Gebundeld schrijven


async def test_twintig_ticks_geven_hooguit_twee_schrijfacties(world):
    for n, node in enumerate(world.nodes):
        assert await world.put(node, 5 * MIN + n) == "ok"
        world.clock.advance(0.45)  # 20 ticks in 9 s
    assert world.store.writes == 1  # de eerste meteen (de vorige keer is lang geleden)
    world.clock.advance(1.1)  # 10 s na de eerste schrijfactie
    await world.store.flush_due()
    assert world.store.writes == 2
    for n, node in enumerate(world.nodes):
        assert (await world.row(node)).position_ms == (5 * MIN + n) * 1000


async def test_pauze_schrijft_meteen(world):
    a, b = world.nodes[:2]
    await world.put(a, 5 * MIN)
    world.clock.advance(1)
    await world.put(b, 6 * MIN)  # wacht op zijn beurt
    assert world.store.writes == 1
    assert await world.row(b) is None
    world.clock.advance(1)
    assert await world.put(b, 6 * MIN + 2, event="pause") == "ok"
    assert world.store.writes == 2
    assert (await world.row(b)).position_ms == 362_000
    assert world.store.due == {}


async def test_stop_einde_en_gezien_schrijven_ook_meteen(world):
    a, b, c = world.nodes[:3]
    await world.put(a, 5 * MIN)
    for node, event, position in ((b, "stop", 7 * MIN), (c, "tick", 58 * MIN)):
        world.clock.advance(1)
        writes = world.store.writes
        assert await world.put(node, position, event=event) == "ok"
        assert world.store.writes == writes + 1, event


async def test_een_crash_verliest_hooguit_10_s(world):
    """Elke seconde een tik (de grens op ticks uit, zodat het snel gaat): wat in de database
    staat, loopt nooit meer dan 10 s achter op het geheugen, als de schrijver op zijn beurt
    schrijft. Dan een crash: het geheugen weg zonder te schrijven."""
    node = world.nodes[0]
    world.store.tick_every = 0
    for second in range(1, 121):
        world.clock.advance(1)
        assert await world.put(node, 5 * MIN + second) == "ok"
        await world.store.flush_due()
        row = await world.row(node)
        behind = world.entry(node).position_ms - row.position_ms
        assert 0 <= behind <= 10_000, (second, behind)
    assert world.store.writes <= 13
    # De "crash": een nieuwe Store leest wat er staat
    clock = Clock()
    fresh = Store(maker(world.settings), clock=clock.mono, wall=clock.now, background=False)
    [e] = await fresh._entries(world.user_id, [node])
    assert (5 * MIN + 120) * 1000 - e.position_ms <= 10_000


async def test_de_schrijver_op_de_achtergrond(settings, client, admin):
    """Met de echte klok en een korte beurt: de schrijver schrijft zonder dat er nog iets
    binnenkomt, en close (netjes stoppen) schrijft de rest."""
    headers, tokens = admin
    user_id = uuid.UUID(tokens["user"]["id"])
    a, b = await files(client, headers, settings, 2)
    store = Store(maker(settings))
    store.write_every = 0.3
    try:
        assert await store.put(user_id, a, None, body(5 * MIN, seq=1)) == "ok"
        assert await store.put(user_id, b, None, body(6 * MIN, seq=1)) == "ok"
        assert store.writes == 1 and store.due
        for _ in range(100):
            if store.writes == 2:
                break
            await anyio_sleep(0.02)
        assert store.writes == 2 and not store.due
        async with maker(settings)() as session:
            row = await session.get(PlaybackState, (user_id, "", b))
            assert row.position_ms == 360_000
        store.write_every = 60
        assert await store.put(user_id, a, None, body(7 * MIN, event="seek", seq=2)) == "ok"
    finally:
        await store.close()
    async with maker(settings)() as session:
        row = await session.get(PlaybackState, (user_id, "", a))
        assert row.position_ms == 420_000
    assert store._task is None


async def anyio_sleep(seconds: float) -> None:
    import anyio

    await anyio.sleep(seconds)


async def test_netjes_stoppen_schrijft_alles_weg(settings, client, admin, monkeypatch):
    """M0-hotfix van M7: runtime.stop roept progress.stop aan, en dat schrijft wat nog alleen
    in het geheugen stond."""
    headers, tokens = admin
    user_id = uuid.UUID(tokens["user"]["id"])
    a, b = await files(client, headers, settings, 2)
    progress.reset()
    try:
        await progress.put(settings, user_id, a, None, body(5 * MIN, seq=1))
        await progress.put(settings, user_id, b, None, body(9 * MIN, event="seek", seq=1))
        live = progress.current()
        assert live is not None and live.due  # b wacht op zijn beurt
        await runtime.stop(app)
    finally:
        progress.reset()
    assert progress.current() is None
    async with maker(settings)() as session:
        row = await session.get(PlaybackState, (user_id, "", b))
        assert row is not None and row.position_ms == 540_000


async def test_een_bestand_dat_weg_is_valt_eruit(world):
    """Een stand voor een node die niet (meer) bestaat: het schrijven faalt op de foreign key;
    die stand gaat uit het geheugen en de rest van de gebruiker komt er gewoon in."""
    real = world.nodes[0]
    ghost = uuid.uuid4()
    await world.put(real, 5 * MIN)
    world.clock.advance(1)
    await world.put(ghost, 5 * MIN)
    world.clock.advance(1)
    await world.put(real, 6 * MIN, event="pause")
    assert (await world.row(real)).position_ms == 360_000
    assert ghost not in world.store.entries[world.user_id]


async def test_wat_lang_niet_gebruikt_is_gaat_uit_het_geheugen(world):
    node = world.nodes[0]
    await world.put(node, 5 * MIN, event="pause")
    world.clock.advance(progress.IDLE_S + 1)
    await world.store.flush_due()
    assert world.user_id not in world.store.entries
    # En komt terug uit de database
    world.clock.advance(1)
    assert await world.put(node, 6 * MIN) == "ok"
    assert world.entry(node).play_count == 0


# De grens op ticks


async def test_een_tick_binnen_5_s_is_te_veel(world):
    node = world.nodes[0]
    assert await world.put(node, 5 * MIN) == "ok"
    world.clock.advance(4.9)
    assert await world.put(node, 5 * MIN + 5) == "rate_limited"
    # Pauze, zoeken en stoppen gaan altijd door
    assert await world.put(node, 5 * MIN + 5, event="pause") == "ok"
    assert await world.put(node, 5 * MIN + 9, event="seek") == "ok"
    world.clock.advance(0.2)
    assert await world.put(node, 5 * MIN + 6) == "ok"
    # Per bestand: een ander bestand mag meteen
    assert await world.put(world.nodes[1], 60) == "ok"


# De volgorde (Keuze 36)


async def test_een_late_keepalive_tik_na_gezien_zet_gezien_niet_terug(world):
    node = world.nodes[0]
    await world.put(node, 50 * MIN, origin="tab", seq=1)
    world.clock.advance(15)
    await world.put(node, 56 * MIN, origin="tab", seq=3)  # gezien
    # De keepalive van een tabblad dat dichtging, met een oudere seq: telt niet
    world.clock.advance(1)
    assert await world.put(node, 55 * MIN, origin="tab", seq=2, event="pause") == "ignored"
    # En met een nieuwere seq van dezelfde sessie ook niet (begon vóór watched_at)
    assert await world.put(node, 57 * MIN, origin="tab", seq=4, event="pause") == "ignored"
    e = world.entry(node)
    assert (e.watched, e.position_ms, e.play_count) == (True, 0, 1)
    assert (await world.row(node)).watched


async def test_een_oudere_seq_van_dezelfde_origin_telt_niet(world):
    node = world.nodes[0]
    await world.put(node, 20 * MIN, origin="A", seq=5, event="pause")
    assert await world.put(node, 10 * MIN, origin="A", seq=4, event="pause") == "ignored"
    assert await world.put(node, 10 * MIN, origin="A", seq=5, event="pause") == "ignored"
    assert world.entry(node).position_ms == 1_200_000
    # Een andere origin heeft zijn eigen volgnummers
    assert await world.put(node, 10 * MIN, origin="B", seq=1, event="pause") == "ok"
    assert world.entry(node).position_ms == 600_000


async def test_een_late_tik_van_een_ander_apparaat_van_voor_gezien_telt_niet(world):
    node = world.nodes[0]
    tv_session = START  # de tv begon eerst
    world.clock.advance(60)
    laptop = world.clock.now()
    await world.put(node, 30 * MIN, origin="tv", started=tv_session)
    world.clock.advance(15)
    await world.put(node, 57 * MIN, origin="laptop", started=laptop, event="end")
    assert world.entry(node).watched
    # Nu komt er nog een tik van de tv, van zijn sessie van vóór watched_at
    world.clock.advance(6)
    assert await world.put(node, 31 * MIN, origin="tv", started=tv_session) == "ignored"
    assert world.entry(node).watched


async def test_een_nieuwe_sessie_voorbij_60_s_haalt_gezien_weg(world):
    node = world.nodes[0]
    await world.put(node, 58 * MIN, event="end")
    assert world.entry(node).watched
    world.clock.advance(3600)
    again = world.clock.now()
    # Tot en met 60 s blijft het gezien (opnieuw beginnen en meteen stoppen)
    assert await world.put(node, 30, started=again, event="pause") == "ignored"
    assert await world.put(node, 60, started=again, event="pause") == "ignored"
    assert world.entry(node).watched
    assert await world.put(node, 61, started=again, event="pause") == "ok"
    e = world.entry(node)
    assert (e.watched, e.position_ms, e.play_count) == (False, 61_000, 1)
    assert not (await world.row(node)).watched


async def test_een_starttijd_in_de_toekomst_telt_als_nu(world):
    node = world.nodes[0]
    future = world.clock.now() + dt.timedelta(days=1)
    await world.put(node, 58 * MIN, started=future, event="end")
    assert world.entry(node).play_started_at == world.clock.now()
    assert world.entry(node).watched


async def test_de_volgorde_houdt_bij_willekeurige_puts(world):
    """Een vaste seed, veel PUTs door elkaar (origins, sessies, events, posities). Wat er ook
    komt: geen fout; na een overgang naar gezien verandert een sessie van daarvoor niets meer;
    play_count gaat nooit omlaag; en na het wegschrijven staat in de database wat het geheugen
    zegt."""
    rng = random.Random(7)
    world.store.tick_every = 0
    node = world.nodes[0]
    sessions = [START]
    seqs: dict[str, int] = {}
    for _ in range(600):
        world.clock.advance(rng.choice([0.5, 1, 5, 15, 120]))
        if rng.random() < 0.1:
            sessions.append(world.clock.now())
        started = rng.choice(sessions)
        origin = rng.choice("ABC")
        seqs[origin] = seqs.get(origin, 0) + rng.choice([1, 1, 1, 0, -1])
        event = rng.choice(["tick", "tick", "pause", "seek", "stop", "end"])
        position = rng.uniform(0, 62 * MIN)
        before = dataclass_copy(world.entry(node)) if node in world.store.entries.get(
            world.user_id, {}) else None  # fmt: skip
        outcome = await world.put(
            node, position, origin=origin, seq=max(0, seqs[origin]), started=started, event=event
        )
        e = world.entry(node)
        assert outcome in ("ok", "ignored")
        if before is not None:
            assert e.play_count >= before.play_count
            if before.watched_at is not None and started < before.watched_at:
                assert outcome == "ignored"
                assert (e.position_ms, e.watched) == (before.position_ms, before.watched)
        if e.watched:
            assert e.position_ms == 0
    await world.store.flush_all()
    row = await world.row(node)
    e = world.entry(node)
    assert (row.position_ms, row.watched, row.play_count, row.seq) == (
        e.position_ms, e.watched, e.play_count, e.seq,
    )  # fmt: skip


def dataclass_copy(e: Entry) -> Entry:
    import copy

    return copy.copy(e)


# Batch


async def test_batch_gezien_en_niet_gezien(world):
    a, b = world.nodes[:2]
    await world.put(a, 30 * MIN, event="pause")
    world.clock.advance(1)
    assert await world.store.batch(world.user_id, [a, b], True) == 2
    assert await world.store.batch(world.user_id, [a, b], True) == 0  # al gezien
    row = await world.row(b)
    assert (row.watched, row.play_count, row.position_ms) == (True, 1, 0)
    # Een sessie die nog liep, zet het niet terug
    assert await world.put(a, 31 * MIN, event="pause") == "ignored"
    world.clock.advance(1)
    assert await world.store.batch(world.user_id, [a], False) == 1
    row = await world.row(a)
    assert (row.watched, row.position_ms, row.play_count) == (False, 0, 1)
    assert row.watched_at is not None  # blijft: een late tik van toen telt nog steeds niet
    assert await world.put(a, 32 * MIN, event="pause") == "ignored"


# Events


async def test_het_event_gaat_alleen_naar_de_gebruiker_zelf(world):
    other = uuid.uuid4()
    with events.hub.subscribe(world.user_id) as mine, events.hub.subscribe(other) as theirs:
        assert await world.put(world.nodes[0], 30, origin="tab-1", event="pause") == "ok"
        event = mine.queue.get_nowait()
        assert event["type"] == "playback.progress"
        assert event["data"] == {
            "node_id": str(world.nodes[0]),
            "position_ms": 30_000,
            "duration_ms": 3_600_000,
            "watched": False,
            "origin": "tab-1",
            "updated_at": world.clock.now().isoformat().replace("+00:00", "Z"),
        }
        assert theirs.queue.empty()
        # Wat niet telt, geeft geen event
        assert await world.put(world.nodes[0], 10, origin="tab-1", seq=0) == "ignored"
        assert mine.queue.empty()


async def test_een_batch_stuurt_hooguit_20_events(world, monkeypatch):
    monkeypatch.setattr(progress, "BATCH_EVENTS", 5)
    with events.hub.subscribe(world.user_id) as mine:
        assert await world.store.batch(world.user_id, world.nodes, True) == 20
        got = []
        while not mine.queue.empty():
            got.append(mine.queue.get_nowait())
        assert len(got) == 5
        assert {e["data"]["origin"] for e in got} == {"batch"}
        assert all(e["data"]["watched"] for e in got)
