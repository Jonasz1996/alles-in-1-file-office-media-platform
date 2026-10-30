"""De producent en de stroom met de nep-worker (M4, §2 M4 test_media_producer.py): de tegendruk,
de herstart bij een sprong, één nieuwe poging na een crash, "vast", te lang wachten, idle, een
andere stsd, de startvoorrang en een sessie die dichtgaat terwijl open nog loopt. Uit de review
van golf 2: tien keer zoeken in 1 s (K1), een segment uit het venster terwijl de run loopt (C1),
een run die een segment te laat begint (C2) en de speling van 1 ms bij een grens (C7).

De echte app (play van M3, de routes van M4) met de nep-worker eronder; de bron is een
HEVC-film (de probe van stream_src.mkv) die dus altijd een transcode is. De nep schrijft
vooraf gemaakte fMP4 van 20 s (10 segmenten van 2 s, ~190 KB per fragment). De tijden van de
producent (30 s vast, 120 s idle, 60 s tegendruk) staan hier korter; de constanten zelf worden
apart nagekeken.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.media import admission, cache, fmp4, grid, sessions, streams
from app.media import producer as producers
from app.media.wire import Op
from app.media.worker.args import StreamArgs
from tests.media import fake_worker as FW
from tests.media import fixtures as F
from tests.test_media_contract import media_app, profile, source_probe, upload  # noqa: F401
from tests.test_media_sessions import content

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not F.available(), reason="ffmpeg ontbreekt (fixtures)"),
]


async def until(cond: Callable[[], bool], seconds: float = 5.0) -> None:
    """Begrensd wachten tot cond waar is (geen while met sleep: ruff ASYNC110)."""
    for _ in range(int(seconds / 0.02)):
        if cond():
            return
        await asyncio.sleep(0.02)
    assert cond(), "te lang gewacht"


async def settle() -> None:
    await asyncio.gather(*list(sessions._tasks), return_exceptions=True)


@dataclass
class Case:
    client: httpx.AsyncClient
    headers: dict[str, str]
    fake: FW.FakeWorker
    settings: Settings
    nodes: list[uuid.UUID]

    async def play(self, i: int = 0, **body) -> str:
        r = await self.client.post(
            f"/api/v1/media/{self.nodes[i]}/play",
            json={"profile": profile("h264"), **body},
            headers=self.headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "transcode"
        return r.json()["sid"]

    async def get(self, sid: str, rest: str) -> httpx.Response:
        return await self.client.get(f"/api/v1/stream/{sid}/{rest}", timeout=30)

    async def seg(self, sid: str, n: int) -> httpx.Response:
        r = await self.get(sid, f"{n}.m4s")
        assert r.status_code == 200, (n, r.status_code, r.text)
        return r

    def stream(self, sid: str) -> streams.Stream:
        st = streams.get(sessions.table().by_sid[sid])
        assert st is not None
        return st

    def runs(self) -> list[float]:
        """De start_s van elke stream-opdracht die de nep kreeg."""
        return [r.args["start_s"] for r in self.fake.requests if r.op == Op.STREAM.value]


@pytest.fixture
async def case(media_app, client, admin):  # noqa: F811
    settings, fake = media_app
    headers, _ = admin
    nodes = []
    for i in range(2):
        name = f"film{i}.mkv"
        nodes.append(
            await upload(
                client, headers, settings, name, content(name), source_probe("transcode", "h264")
            )
        )
    return Case(client, headers, fake, settings, nodes)


def test_de_constanten_van_keuze_25_26_46():
    assert streams.LEAD_MS == 60_000 and streams.PRIORITY_MS == 10_000
    assert streams.PRIORITY_S == 20 and streams.AHEAD == 3 and streams.ATTEMPTS == 2
    assert streams.TIMEOUTS_MAX == 3 and streams.SEEK_MERGE_S == 0.3
    assert producers.STALL_S == 30 and producers.IDLE_S == 120
    assert Settings().media_segment_wait == 20


# --- de tegendruk ------------------------------------------------------------------------------


async def test_tegendruk_bij_de_voorsprong(case, monkeypatch):
    """Meer dan LEAD_MS vóór de speler: de producent leest niet verder, de pipe loopt vol en de
    nep blijft in een schrijfopdracht hangen; schuift de speler op, dan gaat hij verder."""
    monkeypatch.setattr(streams, "LEAD_MS", 4000)
    writes = {"started": 0, "done": 0}
    original = FW._write

    async def counting(fd: int, data: bytes) -> None:
        writes["started"] += 1
        await original(fd, data)
        writes["done"] += 1

    monkeypatch.setattr(FW, "_write", counting)
    sid = await case.play()
    await case.seg(sid, 0)
    st = case.stream(sid)
    await until(lambda: st.producer is not None and st.producer.paused)
    await asyncio.sleep(0.5)
    held = sorted(st.segments)
    assert len(held) <= 5, held  # 4 s voorsprong plus wat in de pipe en de rij zat
    assert writes["started"] > writes["done"], writes  # de nep wacht op de pipe
    await asyncio.sleep(0.3)
    assert sorted(st.segments) == held
    # De speler schuift op: verder lezen, zonder herstart
    r = await case.seg(sid, max(held))
    assert r.headers["x-init"] == "1"
    await until(lambda: max(st.segments) > max(held))
    assert case.runs() == [0.0]


# --- de herstart -------------------------------------------------------------------------------


async def test_herstart_bij_een_sprong(case):
    case.fake.speed = 2.0  # langzaam: segment 8 ligt ver voor de producent
    sid = await case.play()
    await case.seg(sid, 0)
    st = case.stream(sid)
    r = await case.seg(sid, 8)
    assert r.headers["x-init"] == "1"
    starts = case.runs()
    assert len(starts) == 2 and starts[1] == pytest.approx(st.layout.start_s(8))
    # de oude run is weg uit de worker voordat de nieuwe begon (hooguit één tegelijk)
    assert case.fake.processes <= 1
    # binnen 3 segmenten: wachten, geen herstart
    case.fake.speed = None
    await case.seg(sid, 9)
    assert len(case.runs()) == 2


async def test_zoeken_binnen_300_ms_is_een_herstart(case):
    """Twee verzoeken tegelijk (5 en 8): één herstart, op 5 (8 ligt binnen AHEAD). De nep blijft
    langzaam tot de herstart er is: anders maakte de eerste run 5 en 8 zelf, en bewees de test
    niets (review golf 2, K1)."""
    case.fake.speed = 2.0
    sid = await case.play()
    await case.seg(sid, 0)
    st = case.stream(sid)
    got = asyncio.gather(case.seg(sid, 5), case.seg(sid, 8))
    await until(lambda: len(case.runs()) == 2)
    case.fake.speed = None
    assert all(r.status_code == 200 for r in await got)
    assert case.runs() == [0.0, pytest.approx(st.layout.start_s(5))]


async def _scrub(case: Case, sid: str, targets: list[int]) -> list[httpx.Response]:
    """Een speler die scrubt: elke 100 ms een verzoek, en de oude laat hij vallen. De server ziet
    dat niet (uvicorn breekt een verzoek niet af als de client weggaat), dus hier wachten ze
    allemaal door, zonder cancel."""
    tasks = []
    for n in targets:
        tasks.append(asyncio.ensure_future(case.get(sid, f"{n}.m4s")))
        await asyncio.sleep(0.1)
    case.fake.speed = None  # de run na het zoeken mag snel
    return list(await asyncio.gather(*tasks))


async def test_tien_keer_zoeken_in_1_s_is_een_herstart(case):
    """Keuze 12 (review golf 2, K1): tien keer zoeken in 1 s, en geen verzoek wordt afgebroken.
    Eén start na het zoeken, op het laagste doel dat nog telt (7: 8 en 9 liggen erachter); het
    laatste verzoek krijgt zijn segment en de sessie blijft open. Wie is ingehaald (5 en 6),
    start niets en telt niet mee voor het sluiten. Met SEEK_MERGE_S = 0, of met de oude code
    (elk wachtend verzoek zette het doel terug), zijn het er veel meer."""
    case.fake.speed = 2.0
    sid = await case.play()
    await case.seg(sid, 0)
    st = case.stream(sid)
    st.wait_s = 4.0
    targets = [8, 5, 9, 6, 8, 5, 9, 7, 8, 9]
    got = await _scrub(case, sid, targets)
    assert case.runs() == [0.0, pytest.approx(st.layout.start_s(7))]
    status = [(n, r.status_code) for n, r in zip(targets, got, strict=True)]
    assert all(code == 200 for n, code in status if n >= 7), status
    for n, r in zip(targets, got, strict=True):
        if n < 7:  # ingehaald, en die segmenten komen niet: wachten tot wait_s, dan 503
            assert r.status_code == 503 and r.json()["detail"]["code"] == "media_timeout"
    assert got[-1].headers["x-init"] == "1"
    assert st.timeouts == 0 and sessions.table().by_sid[sid].closed is None
    assert case.fake.processes <= 1
    await case.seg(sid, 5)  # de sessie leeft: een nieuw verzoek start gewoon een run
    assert case.runs()[-1] == pytest.approx(st.layout.start_s(5))


# --- het venster en de herstart (review golf 2, C1 en C2) --------------------------------------


async def test_een_segment_uit_het_venster_komt_terug_terwijl_de_run_loopt(case, monkeypatch):
    """Review golf 2, C1: de run is segment 0 al voorbij en leeft nog (gepauzeerd, of nog bezig);
    het venster haalde 0 uit de cache. Een stap terug naar 0 is een herstart op 0, niet 20 s
    wachten op een run die 0 nooit meer maakt."""
    monkeypatch.setattr(cache, "KEEP_BEHIND_MS", 2000)
    case.fake.speed = 3.0
    sid = await case.play()
    for n in range(4):
        await case.seg(sid, n)
    st = case.stream(sid)
    assert st.producer is not None and st.producer.alive
    await st.trim(0)
    assert 0 not in st.segments
    st.wait_s = 4.0
    r = await case.seg(sid, 0)
    assert r.headers["x-init"] == "1"
    assert case.runs() == [0.0, 0.0]
    assert st.timeouts == 0


async def test_terug_voor_het_begin_van_de_run_is_een_herstart(case):
    """Review golf 2, C1: terug naar een segment vóór het begin van de lopende run (nooit
    gemaakt): een herstart daar."""
    case.fake.speed = 2.0
    sid = await case.play()
    await case.seg(sid, 0)
    st = case.stream(sid)
    await case.seg(sid, 8)
    assert 4 not in st.segments
    await case.seg(sid, 4)
    assert case.runs() == [0.0, *(pytest.approx(st.layout.start_s(n)) for n in (8, 4))]


async def test_een_run_die_een_segment_te_laat_begint(case):
    """Review golf 2, C2: MPEG-TS met een GOP boven de marge van 5 s, of een index die een
    keyframe te vroeg zag: het eerste beeld na de sprong ligt een segment verder. Dat eerste
    fragment is dan het startsegment; zonder dat kwam het nooit en startte de stroom steeds
    opnieuw op hetzelfde segment."""

    async def late(job: FW.Job) -> dict:
        args = job.args
        assert isinstance(args, StreamArgs)
        if args.start_s > 0:  # de nep begint 2 s (een segment) na start_s
            args = args.model_copy(update={"start_s": args.start_s + 2.0})
        return await case.fake._stream(dataclasses.replace(job, args=args))

    case.fake.on(Op.STREAM, late)
    case.fake.speed = 2.0
    sid = await case.play()
    await case.seg(sid, 0)
    st = case.stream(sid)
    st.wait_s = 4.0
    case.fake.speed = None
    r = await case.seg(sid, 7)
    assert r.headers["x-init"] == "1"
    assert case.runs() == [0.0, pytest.approx(st.layout.start_s(7))]


# --- crash en nieuwe poging --------------------------------------------------------------------


async def test_een_nieuwe_poging_na_een_crash(case):
    case.fake.fault("crash_after", op=Op.STREAM, n=2)
    sid = await case.play()
    await case.seg(sid, 0)
    await case.seg(sid, 1)
    st = case.stream(sid)
    r = await case.seg(sid, 2)  # de run stopte vóór 2; de nieuwe poging begint op 2
    assert r.headers["x-init"] == "1"
    starts = case.runs()
    assert len(starts) == 2 and starts[1] == pytest.approx(st.layout.start_s(2))
    assert st.attempts == {} and st.failed == {}


async def test_de_nieuwe_poging_faalt_ook_dan_502(case):
    """Twee mislukte runs op hetzelfde segment: 502. De run die tijdens een verzoek faalt telt
    mee (een herstart vóór ended zou die fout overslaan)."""
    case.fake.fault("crash_after", op=Op.STREAM, n=2)
    case.fake.fault("crash_after", op=Op.STREAM, n=0)
    sid = await case.play()
    await case.seg(sid, 0)
    await case.seg(sid, 1)
    r = await case.get(sid, "2.m4s")
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["code"] == "media_transcode_failed"
    assert r.headers["cache-control"] == "no-store"
    assert len(case.runs()) == 2
    # het blijft mislukt, zonder nieuwe run; andere segmenten kunnen nog
    assert (await case.get(sid, "2.m4s")).status_code == 502
    assert len(case.runs()) == 2
    await case.seg(sid, 0)


async def test_een_lege_run_telt_als_mislukt(case):
    """Een run die slaagt maar geen segment maakt (alleen de init): ook een mislukte run."""

    async def only_init(job: FW.Job) -> dict:
        out = job.outputs[0]
        os.set_blocking(out, False)
        await FW._write(out, FW.premade("h264").init)
        FW._close_output(job, out)
        return {"fragments": 0}

    case.fake.on(Op.STREAM, only_init)
    sid = await case.play()
    r = await case.get(sid, "0.m4s")
    assert r.status_code == 502, r.text
    st = case.stream(sid)
    assert isinstance(st.failed[0], streams.EmptyRun)
    assert len(case.runs()) == 2


async def test_vast_na_stall_s_zonder_fragment(case, monkeypatch):
    monkeypatch.setattr(producers, "STALL_S", 0.4)
    monkeypatch.setattr(producers, "TICK_S", 0.05)
    case.fake.fault("hang", op=Op.STREAM, after=1)
    sid = await case.play()
    await case.seg(sid, 0)
    st = case.stream(sid)
    r = await case.seg(sid, 1)  # vast na 0,4 s; de nieuwe poging op 1 levert
    assert r.headers["x-init"] == "1"
    starts = case.runs()
    assert len(starts) == 2 and starts[1] == pytest.approx(st.layout.start_s(1))
    await until(lambda: case.fake.processes <= 1)


async def test_geen_segment_op_tijd_503_en_na_3_keer_dicht(case):
    case.fake.fault("hang", op=Op.STREAM, times=1000)
    sid = await case.play()
    loop = asyncio.get_running_loop()
    for _ in range(streams.TIMEOUTS_MAX):
        began = loop.time()
        r = await case.get(sid, "0.m4s")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "media_timeout"
        assert r.headers["retry-after"] == "2"
        assert loop.time() - began <= case.settings.media_segment_wait + 1.5
    r = await case.get(sid, "0.m4s")
    assert r.status_code == 410
    case.fake.clear_faults()
    await settle()
    assert case.fake.processes == 0
    assert admission.snapshot().grants == []


async def test_idle_dicht_de_producent_maar_de_sessie_blijft(case, monkeypatch):
    monkeypatch.setattr(producers, "IDLE_S", 0.4)
    monkeypatch.setattr(producers, "TICK_S", 0.05)
    case.fake.speed = 2.0
    sid = await case.play()
    await case.seg(sid, 0)
    st = case.stream(sid)
    await until(lambda: st.producer is None)
    await until(lambda: case.fake.processes == 0)
    session = sessions.table().by_sid[sid]
    assert session.closed is None
    assert (await case.get(sid, "map.json")).status_code == 200
    # het volgende verzoek start een nieuwe run op dat segment
    case.fake.speed = None
    r = await case.seg(sid, 4)
    assert r.headers["x-init"] == "1"
    assert case.runs()[-1] == pytest.approx(st.layout.start_s(4))


# --- generaties --------------------------------------------------------------------------------


async def test_een_andere_stsd_geeft_generatie_2(case):
    """Een herstart met een andere stsd: generatie 2 en X-Init 2. Een latere herstart met de
    oude stsd hoort weer bij generatie 1 (de veiligere lezing van Keuze 22: de generatie hangt
    aan de inhoud van de stsd, niet aan de volgorde)."""
    case.fake.speed = 2.0
    case.fake.fault("new_stsd", op=Op.STREAM)
    sid = await case.play()
    await case.seg(sid, 0)
    case.fake.speed = None
    r = await case.seg(sid, 8)
    assert r.headers["x-init"] == "2"
    m = (await case.get(sid, "map.json")).json()
    assert m["gen"] == 2 and m["init"] == "init.mp4?g=2"
    v = (await case.get(sid, "v.m3u8")).text
    assert '#EXT-X-MAP:URI="init.mp4?g=2"' in v
    one = (await case.get(sid, "init.mp4?g=1")).content
    two = (await case.get(sid, "init.mp4?g=2")).content
    assert one != two
    # terug naar 4: een herstart zonder de fout, dus de oude stsd en generatie 1
    r = await case.seg(sid, 4)
    assert r.headers["x-init"] == "1"
    assert (await case.get(sid, "map.json")).json()["gen"] == 2
    assert (await case.get(sid, "init.mp4?g=3")).status_code == 404


# --- de startvoorrang --------------------------------------------------------------------------


async def test_startvoorrang(case, monkeypatch):
    """Zolang een andere sessie op haar eerste segment wacht, leest een lopende producent niet
    verder dan PRIORITY_MS vóór zijn speler; na PRIORITY_S niet meer."""
    monkeypatch.setattr(streams, "PRIORITY_MS", 2000)
    monkeypatch.setattr(streams, "PRIORITY_S", 1.0)
    monkeypatch.setattr(producers, "TICK_S", 0.05)
    case.fake.speed = 4.0
    a = await case.play(0)
    await case.seg(a, 0)
    first = case.stream(a)
    assert first.limit_ms() == streams.LEAD_MS
    case.fake.fault("hang", op=Op.STREAM)  # de tweede krijgt nooit een segment
    b = await case.play(1)
    second = case.stream(b)
    assert second.waiting_first()
    assert first.limit_ms() == 2000
    await until(lambda: first.producer is not None and first.producer.paused)
    held = len(first.segments)
    assert held <= 4, sorted(first.segments)
    # na PRIORITY_S geen voorrang meer: de eerste leest weer door
    await until(lambda: not second.waiting_first(), 3)
    assert first.limit_ms() == streams.LEAD_MS
    await until(lambda: len(first.segments) == first.layout.count, 10)
    case.fake.clear_faults()


# --- dicht -------------------------------------------------------------------------------------


async def test_dicht_terwijl_open_nog_loopt(case, monkeypatch):
    """De sessie gaat dicht terwijl open op de worker wacht (busy, opnieuw proberen): de run
    houdt op met proberen, geen stroom, geen run in de worker, de grant terug; het eerste
    verzoek krijgt de reden."""
    monkeypatch.setattr(producers, "START_BUSY_S", 5.0)
    case.fake.fault("busy", op=Op.STREAM, times=1000)
    task = asyncio.ensure_future(case.play())
    await until(lambda: len(case.runs()) >= 2)
    (s,) = list(sessions.table().open())
    assert sessions.stop(s.id, "stopped")
    sid = await asyncio.wait_for(task, 2)  # niet pas na START_BUSY_S
    tried = len(case.runs())
    await asyncio.sleep(0.3)
    assert len(case.runs()) == tried
    r = await case.get(sid, "map.json")
    assert r.status_code == 403 and r.json()["detail"]["params"]["reason"] == "stopped"
    await settle()
    assert case.fake.processes == 0
    assert streams.get(s) is None and streams.all() == []
    assert admission.snapshot().grants == []


async def test_close_zonder_open_is_veilig(case):
    """streams.close voor een sessie zonder stroom (direct, of open liep nooit): niets."""
    sid = await case.play()
    s = sessions.table().by_sid[sid]
    await streams.close(s)
    await streams.close(s)
    assert streams.get(s) is None
    sessions.close(s)
    await settle()
    assert case.fake.processes == 0


# --- regel 8 -----------------------------------------------------------------------------------


async def test_wachten_op_een_segment_houdt_geen_verbinding_vast(case):
    """Regel 8 (de les van F1): twintig verzoeken wachten op een segment dat niet komt (de nep
    hangt na één fragment), en intussen is er geen verbinding met de database uitgeleend; een
    gewone vraag aan de API gaat gewoon."""
    from app.db import get_engine

    pool = get_engine(case.settings).sync_engine.pool
    case.fake.fault("hang", op=Op.STREAM, after=1)
    sid = await case.play()
    st = case.stream(sid)
    waiting = [asyncio.ensure_future(case.get(sid, f"{1 + i % 2}.m4s")) for i in range(20)]
    try:
        await until(lambda: st.waiting == 20)
        assert pool.checkedout() == 0
        r = await case.client.get("/api/v1/files/root", headers=case.headers, timeout=5)
        assert r.status_code == 200, r.text
        assert st.waiting == 20 and not any(t.done() for t in waiting)
        assert pool.checkedout() == 0
    finally:
        for task in waiting:
            task.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)
    r = await case.client.delete(f"/api/v1/stream/{sid}", headers=case.headers)
    assert r.status_code == 204, r.text
    await settle()


# --- het groeperen (zonder worker) -------------------------------------------------------------


def _frag(start: int, end: int, tag: bytes) -> fmp4.Fragment:
    return fmp4.Fragment(
        data=tag, video_start_ms=start, video_end_ms=end, video_last_ms=end - 41,
        video_cto_ms=0, sync=True,
    )  # fmt: skip


def _bare(layout: grid.Layout, start: int = 0) -> producers.Producer:
    sink = SimpleNamespace(layout=layout, playhead_ms=lambda: 0, limit_ms=lambda: streams.LEAD_MS)
    p = producers.Producer(sink, start, None, Path("bron"))  # type: ignore[arg-type]
    p.gen = 1
    return p


def _queued(p: producers.Producer) -> list[tuple[int, bytes]]:
    out = []
    while not p._queue.empty():
        item = p._queue.get_nowait()
        out.append((item[1], item[3]))
    return out


LAYOUT = grid.Layout("transcode", (0, 2021, 4021), 6000)


def test_een_segment_is_af_zodra_het_beeld_de_grens_haalt():
    p = _bare(LAYOUT)
    p._fragment(_frag(21, 2021, b"a"))
    assert _queued(p) == [(0, b"a")] and p.next == 1  # niet wachten op fragment 1
    p._fragment(_frag(2021, 3000, b"b"))
    assert _queued(p) == []  # nog niet tot 4021
    p._fragment(_frag(3000, 4020, b"c"))  # 1 ms speling voor het afronden
    assert _queued(p) == [(1, b"bc")]
    # het laatste segment wacht op het eind (alles voorbij N hoort erbij)
    p._fragment(_frag(4021, 6000, b"d"))
    assert _queued(p) == []
    p._complete()
    assert _queued(p) == [(2, b"d")]


def test_een_fragment_dat_te_vroeg_ophoudt_wacht_op_het_volgende():
    p = _bare(LAYOUT)
    p._fragment(_frag(21, 2010, b"a"))
    assert _queued(p) == []
    p._fragment(_frag(2021, 4021, b"b"))
    assert _queued(p) == [(0, b"a"), (1, b"b")]


def test_toch_nog_iets_na_een_vroeg_einde_schrijft_het_segment_opnieuw():
    p = _bare(LAYOUT)
    p._fragment(_frag(21, 2021, b"a"))
    assert _queued(p) == [(0, b"a")]
    p._fragment(_frag(1500, 2021, b"x"))  # hoort nog bij 0 (zou niet mogen, maar kan)
    p._fragment(_frag(2021, 3000, b"b"))
    assert _queued(p) == [(0, b"ax")]
    # een staart van alleen geluid na een vroeg einde ook
    p._fragment(_frag(3000, 4021, b"c"))
    assert _queued(p) == [(1, b"bc")]
    tail = fmp4.Fragment(b"t", None, None, None, 0, False, audio_start_ms=4021, audio_end_ms=4100)
    p._fragment(tail)
    p._fragment(_frag(4021, 6000, b"d"))
    assert _queued(p) == [(1, b"bct")]


GRID = grid.Layout("transcode", (0, 2000, 4000, 6000), 8000)


def test_een_ms_voor_de_grens_telt_als_op_de_grens():
    """Review golf 2, C7: echte pts zijn op hele ms afgerond en vallen soms 1 ms vóór een grens.
    Zo'n fragment hoort bij het volgende segment, en een beeld dat 1 ms vóór de grens ophoudt,
    maakt zijn segment toch al af (SLACK_MS)."""
    p = _bare(GRID)
    p._fragment(_frag(0, 1999, b"a"))
    assert _queued(p) == [(0, b"a")] and p.next == 1
    p._fragment(_frag(1999, 3999, b"b"))
    assert _queued(p) == [(1, b"b")] and p.next == 2
    p._fragment(_frag(3999, 5999, b"c"))
    assert _queued(p) == [(2, b"c")]
    p._fragment(_frag(5999, 8000, b"d"))
    p._complete()
    assert _queued(p) == [(3, b"d")]


def test_het_eerste_beeld_na_het_startsegment_is_het_startsegment():
    """Review golf 2, C2: een run vanaf 1 waarvan het eerste beeld pas in 2 valt. Dat fragment
    wordt segment 1 (het eerste beeld na de sprong); de rest volgt het rooster. Alleen het
    eerste fragment van een run: daarna is een gat een gat."""
    p = _bare(GRID, start=1)
    p._fragment(_frag(4500, 6000, b"a"))
    assert _queued(p) == [(1, b"a")] and p.next == 2
    p._fragment(_frag(6000, 8000, b"b"))
    p._complete()
    assert _queued(p) == [(3, b"b")]
    # vóór het startsegment gaat nog altijd weg (remux en MPEG-TS beginnen eerder)
    q = _bare(GRID, start=2)
    q._fragment(_frag(3000, 4000, b"x"))
    q._fragment(_frag(4000, 6000, b"y"))
    assert _queued(q) == [(2, b"y")]


def test_wat_de_run_al_voorbij_is_komt_er_niet_meer_uit():
    """Review golf 2, C1: covers. Wat de run maakt en tot AHEAD verder: ja. Een segment dat af
    is maar nog in de rij van de schrijver staat: ook ja (het komt zo op schijf). Wat de run al
    voorbij is en geschreven: nee, dat maakt hij nooit meer (een herstart)."""
    p = _bare(GRID)
    p._fragment(_frag(0, 2000, b"a"))
    p._fragment(_frag(2000, 4000, b"b"))
    assert p.next == 2
    assert p.covers(0, 3) and p.covers(1, 3)  # af, op weg naar de schijf
    assert p.covers(2, 3) and p.covers(5, 3) and not p.covers(6, 3)
    assert [n for n, _ in _queued(p)] == [0, 1]
    p._queued.clear()  # wat de schrijver doet als ze op schijf staan
    assert not p.covers(0, 3) and not p.covers(1, 3) and p.covers(2, 3)
