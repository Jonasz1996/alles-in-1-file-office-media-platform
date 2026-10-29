"""De producent en de stroom met de nep-worker (M4, §2 M4 test_media_producer.py): de tegendruk,
de herstart bij een sprong, één nieuwe poging na een crash, "vast", te lang wachten, idle, een
andere stsd, de startvoorrang en een sessie die dichtgaat terwijl open nog loopt.

De echte app (play van M3, de routes van M4) met de nep-worker eronder; de bron is een
HEVC-film (de probe van stream_src.mkv) die dus altijd een transcode is. De nep schrijft
vooraf gemaakte fMP4 van 20 s (10 segmenten van 2 s, ~190 KB per fragment). De tijden van de
producent (30 s vast, 120 s idle, 60 s tegendruk) staan hier korter; de constanten zelf worden
apart nagekeken.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.media import admission, fmp4, grid, sessions, streams
from app.media import producer as producers
from app.media.wire import Op
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
    case.fake.speed = 2.0
    sid = await case.play()
    await case.seg(sid, 0)
    case.fake.speed = None
    got = await asyncio.gather(case.seg(sid, 5), case.seg(sid, 8))
    assert all(r.status_code == 200 for r in got)
    # vanaf 0, en één herstart (op het laatste doel), niet twee
    assert len(case.runs()) <= 3, case.runs()


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


# --- het groeperen (zonder worker) -------------------------------------------------------------


def _frag(start: int, end: int, tag: bytes) -> fmp4.Fragment:
    return fmp4.Fragment(
        data=tag, video_start_ms=start, video_end_ms=end, video_last_ms=end - 41,
        video_cto_ms=0, sync=True,
    )  # fmt: skip


def _bare(layout: grid.Layout) -> producers.Producer:
    sink = SimpleNamespace(layout=layout, playhead_ms=lambda: 0, limit_ms=lambda: streams.LEAD_MS)
    p = producers.Producer(sink, 0, None, Path("bron"))  # type: ignore[arg-type]
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
