"""De nep-mediaworker (§3.6): het contract tegen de nep én de echte lokale worker, de opnames
van de echte probe, en de fouten op bestelling.

- test_contract_nep: elk scenario uit tests/media/worker_contract.py tegen de nep (standaard;
  met ffmpeg voor de fixtures, anders overgeslagen);
- test_contract_echt (marker media): dezelfde scenario's tegen de echte lokale worker, voor wat
  M0 heeft (caps, probe, stoppen). stream bouwt M4: die scenario's slaan hier over;
- de opnames (tests/media/recorded): compleet, actueel met PROBE_VERSION en de recepten, en
  geldig. Zijn ze dat niet: `cd api && uv run python -m tests.media.record`;
- de fouten van de nep (crash_after, hang, busy, slow_start, garbage, new_stsd), het tempo, de
  herstart bij remux, eigen antwoorden, en de nep als worker van de app (container).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.config import Settings
from app.media import analysis, client, local, wire
from app.media.client import WorkerBusy, WorkerDown, WorkerFailed
from app.media.types import Probe, SegmentGrid, WorkerCaps
from app.media.wire import Code, Op, WorkClass
from app.media.worker import args as A
from tests.media import boxes, record
from tests.media import fixtures as F
from tests.media import worker_contract as C
from tests.media.fake_worker import GARBAGE, FakeWorker, Job, OpError, premade, running

pytestmark = pytest.mark.anyio

needs_ffmpeg = pytest.mark.skipif(not F.available(), reason="ffmpeg ontbreekt (fixtures)")
SCENARIOS = pytest.mark.parametrize("scenario", C.SCENARIOS, ids=lambda s: s.__name__)
RECORD = "cd api && uv run python -m tests.media.record"


# --- de drivers ------------------------------------------------------------------------------


@dataclass
class FakeDriver:
    fake: FakeWorker
    name: str = "nep"
    has: frozenset[Op] = frozenset({Op.CAPS, Op.PROBE, Op.STREAM})

    @property
    def socket(self) -> Path:
        return self.fake.socket

    @property
    def max_procs(self) -> int:
        return self.fake.max_procs

    @property
    def max_transcodes(self) -> int:
        return self.fake.max_transcodes

    def processes(self) -> int:
        return self.fake.processes

    async def slow(self) -> None:
        self.fake.fault("hang", op=Op.PROBE, times=1_000_000)

    async def fast(self) -> None:
        self.fake.clear_faults()

    def source(self, name: str) -> Path:
        return F.make(name)


# Op een fd slapen zolang de vlag er is (een trage opdracht), anders de echte ffprobe
SLOW_FFPROBE = """#!/bin/sh
if [ -e '{flag}' ]; then
  case " $* " in
    *" fd: "*) exec sleep 60 ;;
  esac
fi
exec '{real}' "$@"
"""


def _children(ppid: int) -> int:
    """De levende kinderen van de worker (geen zombies)."""
    count = 0
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            text = Path(f"/proc/{name}/stat").read_text()
        except OSError:
            continue
        rest = text[text.rindex(")") + 2 :].split()
        if int(rest[1]) == ppid and rest[0] != "Z":
            count += 1
    return count


@dataclass
class RealDriver:
    worker: local.LocalWorker
    flag: Path
    name: str = "echt"
    # M4 zet hier Op.STREAM bij als de worker stream doet
    has: frozenset[Op] = frozenset({Op.CAPS, Op.PROBE})
    max_procs: int = 3
    max_transcodes: int = 1
    extra: dict = field(default_factory=dict)

    @property
    def socket(self) -> Path:
        return self.worker.socket

    def processes(self) -> int:
        return _children(self.worker.pid)

    async def slow(self) -> None:
        self.flag.touch()

    async def fast(self) -> None:
        self.flag.unlink(missing_ok=True)

    def source(self, name: str) -> Path:
        return F.make(name)


@pytest.fixture
async def fake(tmp_path):
    async with running(tmp_path / "run" / "nep.sock", max_procs=3, max_transcodes=2) as worker:
        yield worker
        assert worker.processes == 0, "de nep heeft nog een opdracht lopen"


@pytest.fixture
async def fake_driver(fake):
    return FakeDriver(fake)


@pytest.fixture
async def real_driver(tmp_path, monkeypatch):
    real = shutil.which("ffprobe")
    assert real, "ffprobe ontbreekt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    flag = tmp_path / "traag"
    wrapper = bin_dir / "ffprobe"
    wrapper.write_text(SLOW_FFPROBE.format(flag=flag, real=real))
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}")
    settings = Settings(
        data_dir=tmp_path,
        web_dist=tmp_path / "web",
        jwt_secret="t" * 32,
        media_max_procs=3,
        media_max_transcodes=1,
    )
    worker = await local.start(settings, tmp_path / "run" / "worker.sock")
    client.bind(worker.socket)
    try:
        yield RealDriver(worker, flag)
    finally:
        flag.unlink(missing_ok=True)
        await local.stop(worker)
        client.bind(None)
    assert not _children(worker.pid)


# --- het contract ----------------------------------------------------------------------------


@needs_ffmpeg
@SCENARIOS
async def test_contract_nep(scenario, fake_driver):
    await scenario(fake_driver)


@pytest.mark.media
@SCENARIOS
async def test_contract_echt(scenario, real_driver):
    missing = C.required(scenario) - real_driver.has
    if missing:
        pytest.skip(f"de echte worker doet {', '.join(sorted(missing))} nog niet (M4)")
    await scenario(real_driver)


# --- de opnames ------------------------------------------------------------------------------


def _recorded() -> dict[str, dict]:
    return {
        record.name_of(f): json.loads(f.read_text())
        for f in sorted(record.PROBE_DIR.glob("*.json"))
        if not f.name.startswith("_")
    }


def test_opnames_actueel():
    """Na een hogere PROBE_VERSION of een ander recept: opnieuw opnemen."""
    meta = record.load_meta()
    assert meta.get("probe_version") == analysis.PROBE_VERSION, (
        f"PROBE_VERSION is {analysis.PROBE_VERSION}, de opnames zijn van "
        f"{meta.get('probe_version')}: {RECORD}"
    )
    assert meta.get("fixtures_version") == F.VERSION, RECORD
    stale = {
        name: hash_
        for name, hash_ in meta.get("recipes", {}).items()
        if name not in F.RECIPES or F.recipe_hash(name) != hash_
    }
    assert not stale, f"recepten veranderd of weg ({', '.join(stale)}): {RECORD}"
    sources = {n.partition("/")[0] for n in _recorded()}
    want = {n for n in F.NAMES if n not in F.HEAVY | F.WORKER_OUTPUT | F.LOOSE_SUBTITLES}
    assert sources == want, f"zonder opname: {sorted(want - sources)}: {RECORD}"
    assert set(meta["recipes"]) == want


@needs_ffmpeg
def test_opnames_compleet():
    """Elke bron (ook elk nummer van album/) heeft een opname, en er is er geen te veel."""
    assert set(_recorded()) == set(record.sources()), RECORD


def test_opnames_geldig_en_zonder_paden():
    recorded = _recorded()
    assert recorded
    for name, found in recorded.items():
        assert set(found) in ({"data"}, {"error"}), name
        if "error" in found:
            assert Code(found["error"]["code"]) in {Code.UNREADABLE, Code.UNKNOWN_FORMAT}, name
            assert name.partition("/")[0] in F.UNREADABLE, name
            continue
        probe = Probe.model_validate(found["data"])
        assert probe.state == "ready" and probe.streams, name
        # past in één antwoord van de worker
        assert len(wire.encode({"ev": "result", "data": found["data"]})) <= wire.MAX_MESSAGE
    text = "".join(f.read_text() for f in record.HERE.rglob("*.json"))
    for bad in ("/home/", "/tmp/", ".pytest_cache", "/root/", str(F.API)):
        assert bad not in text, f"een pad in de opnames: {bad}"
    caps = record.load_caps()
    assert not set(record.CAPS_VOLATILE) & set(caps)
    assert WorkerCaps.model_validate({**caps, "protocol": wire.PROTOCOL}).missing == []


@pytest.mark.media
async def test_opnames_kloppen_met_de_echte_worker(tmp_path):
    """Elke opname tegen een verse probe van de echte worker, na elkaar (regel 13)."""
    settings = Settings(
        data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32, media_max_procs=3
    )
    worker = await local.start(settings, tmp_path / "run" / "worker.sock")
    client.bind(worker.socket)
    differ: list[str] = []
    try:
        for name, found in _recorded().items():
            try:
                fresh = {"data": await record.raw_probe(record.path(name))}
            except WorkerFailed as exc:
                fresh = {"error": {"code": exc.code}}
            differ += record.differences(name, fresh, found)
    finally:
        await local.stop(worker)
        client.bind(None)
    assert not differ, f"de echte worker antwoordt anders ({RECORD}):\n" + "\n".join(differ)


# --- de nep zelf -----------------------------------------------------------------------------


def _transcode(codec: str = "libx264", start_s: float = 0) -> A.StreamArgs:
    return C.transcode(codec, start_s, -0.021)


def _remux(start_s: float = 0) -> A.StreamArgs:
    return A.StreamArgs.model_validate(
        {
            "mode": "remux",
            "demuxer": "matroska",
            "start_s": start_s,
            "video_index": 0,
            "audio_index": 1,
            "video": "copy",
            "audio": "copy",
        }
    )


async def _stream(args: A.StreamArgs) -> tuple[bytes, list[dict], Exception | None]:
    """Een stream helemaal lezen; de uitvoer, de antwoorden en de fout (of None)."""
    job, (out,) = await client.call(Op.STREAM, args, source=F.make(C.SOURCE), outputs=1)
    events: list[dict] = []
    error: Exception | None = None
    async with job:
        reading = asyncio.create_task(C.read_pipe(out))
        try:
            async for event in job.events():
                events.append(event)
        except (WorkerFailed, WorkerDown) as exc:
            error = exc
        data = await asyncio.wait_for(reading, 30)
    return data, events, error


def _cut(data: bytes) -> tuple[bytes, list[bytes]]:
    """init en de fragmenten van een (misschien afgebroken) uitvoer."""
    tops = boxes.top_level(data)
    init = b"".join(box for kind, box in tops if kind in ("ftyp", "moov"))
    pairs = zip(tops, tops[1:], strict=False)
    return init, [a + b for (k1, a), (k2, b) in pairs if (k1, k2) == ("moof", "mdat")]


def test_vooraf_gemaakte_uitvoer_op_het_rooster():
    if not F.available():
        pytest.skip("ffmpeg ontbreekt (fixtures)")
    grid = SegmentGrid(origin_ms=21, count=SegmentGrid.count_for(21, 19979), last_frame_ms=19979)
    assert grid.count == 10
    for variant in ("h264", "vp9"):
        for alt in (False, True):
            parsed = premade(variant, alt)
            C.on_grid(parsed, grid, 0)
            assert parsed.fragments[-1].video_last_ms == 19979
            assert parsed.fragments[-1].end_ms == 20021
        # de andere maat: een andere stsd, dezelfde tijden (spike S4/S5)
        assert premade(variant).stsd() != premade(variant, True).stsd()
        starts = [f.video_start_ms for f in premade(variant).fragments]
        assert starts == [f.video_start_ms for f in premade(variant, True).fragments]
    assert premade("h264").mime() == 'video/mp4; codecs="avc1.64001e,mp4a.40.2"'
    assert premade("vp9").mime().startswith('video/mp4; codecs="vp09.00.')
    assert premade("vp9").mime().endswith(',opus"')


@needs_ffmpeg
async def test_crash_na_n_fragmenten(fake):
    fake.fault("crash_after", op=Op.STREAM, n=3)
    data, _events, error = await _stream(_transcode())
    assert isinstance(error, WorkerFailed) and error.code == Code.FAILED
    init, frags = _cut(data)
    assert init == premade("h264").init and len(frags) == 3
    fake.fault("crash_after", op=Op.STREAM, n=1, how="worker")
    data, events, error = await _stream(_transcode())
    assert isinstance(error, WorkerDown)  # de verbinding viel weg zonder antwoord
    assert len(_cut(data)[1]) == 1 and not any(e["ev"] == "result" for e in events)
    assert await C.until(lambda: fake.processes == 0)


@needs_ffmpeg
async def test_hang_na_n_fragmenten_en_stoppen(fake):
    fake.fault("hang", op=Op.STREAM, after=2)
    job, (out,) = await client.call(Op.STREAM, _transcode(), source=F.make(C.SOURCE), outputs=1)
    reading = asyncio.create_task(C.read_pipe(out))
    await asyncio.sleep(0.5)
    assert not reading.done() and fake.processes == 1
    await job.cancel()
    data = await asyncio.wait_for(reading, 5)  # EOF: de nep sluit de pipe bij stoppen
    assert len(_cut(data)[1]) == 2
    assert await C.until(lambda: fake.processes == 0)


@needs_ffmpeg
async def test_hang_en_de_tijdgrens(fake):
    fake.timeouts[Op.PROBE] = 0.3
    fake.fault("hang", op=Op.PROBE)
    started = time.monotonic()
    with pytest.raises(WorkerFailed) as exc:
        await client.probe(F.make("h264_aac.mp4"))
    assert exc.value.code == Code.TIMEOUT and time.monotonic() - started < 3
    assert (await client.probe(F.make("h264_aac.mp4"))).container == "mp4"  # één keer


@needs_ffmpeg
async def test_busy_met_de_soort_en_caps_werkt(fake):
    fake.fault("busy", op=Op.PROBE, work_class="background")
    with pytest.raises(WorkerBusy) as exc:
        await client.probe(F.make("h264_aac.mp4"))
    assert exc.value.work_class == WorkClass.BACKGROUND
    fake.fault("busy")  # elke opdracht, ook caps: caps telt niet mee, maar de fout is besteld
    with pytest.raises(WorkerBusy):
        await client.call(Op.PROBE, A.ProbeArgs(), source=F.make("h264_aac.mp4"))
    caps = await client.caps()
    assert caps.in_use == {"transcode": 0, "remux": 0, "background": 0, "short": 0}
    # vol: de derde transcode (max_transcodes 2) is busy met de soort
    fake.fault("hang", op=Op.STREAM, times=2)
    jobs = [
        await client.call(Op.STREAM, _transcode(), source=F.make(C.SOURCE), outputs=1)
        for _ in range(2)
    ]
    try:
        with pytest.raises(WorkerBusy) as exc:
            await client.call(Op.STREAM, _transcode(), source=F.make(C.SOURCE), outputs=1)
        assert exc.value.work_class == WorkClass.TRANSCODE
        assert (await client.caps()).in_use["transcode"] == 2
    finally:
        for job, outs in jobs:
            await job.cancel()
            wire.close_all(outs)
    assert await C.until(lambda: fake.processes == 0)


@needs_ffmpeg
async def test_trage_start(fake):
    fake.fault("slow_start", op=Op.PROBE, seconds=0.5)
    started = time.monotonic()
    job, _ = await client.call(Op.PROBE, A.ProbeArgs(), source=F.make("h264_aac.mp4"))
    assert time.monotonic() - started < 0.4  # started komt meteen
    async with job:
        await job.result()
    assert time.monotonic() - started >= 0.5


@needs_ffmpeg
async def test_rommel(fake):
    fake.fault("garbage", op=Op.STREAM)
    data, events, error = await _stream(_transcode())
    assert error is None and events[-1]["ev"] == "result"
    assert data.startswith(premade("h264").init) and data.endswith(GARBAGE)
    with pytest.raises(boxes.BoxError):
        boxes.parse(data)
    fake.fault("garbage", op=Op.CAPS)
    with pytest.raises(WorkerDown):  # geen JSON
        await client.caps()
    assert (await client.caps()).protocol == wire.PROTOCOL


@needs_ffmpeg
async def test_nieuwe_stsd_bij_een_herstart(fake):
    whole, _, _ = await _stream(_transcode())
    fake.fault("new_stsd", op=Op.STREAM)
    first, _, _ = await _stream(_transcode())  # start_s 0: geen herstart, de fout blijft
    assert boxes.parse(first).stsd() == boxes.parse(whole).stsd()
    again, _, _ = await _stream(_transcode(start_s=6.021))
    parsed = boxes.parse(again)
    assert parsed.stsd() != boxes.parse(whole).stsd()
    assert parsed.video is not None and (parsed.video.width, parsed.video.height) == (480, 270)
    assert [f.video_start_ms for f in parsed.fragments][0] == 6021
    plain, _, _ = await _stream(_transcode(start_s=6.021))  # de fout is op
    assert boxes.parse(plain).stsd() == boxes.parse(whole).stsd()


@needs_ffmpeg
async def test_vp9_bij_libvpx(fake):
    data, _, _ = await _stream(_transcode("libvpx-vp9"))
    assert boxes.parse(data).mime() == premade("vp9").mime()


@needs_ffmpeg
async def test_tempo_kiest_de_test(fake):
    fake.speed = 40.0  # 20 s mediatijd in een halve seconde
    started = time.monotonic()
    data, events, _ = await _stream(_transcode())
    took = time.monotonic() - started
    assert len(boxes.parse(data).fragments) == 10
    assert 0.45 <= took < 5, took
    progress = [e for e in events if e["ev"] == "progress"]
    assert [e["t_ms"] for e in progress][-1] == 20021 and progress[0]["speed"] == 40.0


@needs_ffmpeg
async def test_remux_herstart_op_het_keyframe_ervoor(fake):
    data, _, _ = await _stream(_remux(start_s=5.0))
    starts = [f.video_start_ms for f in boxes.parse(data).fragments]
    assert starts[0] == 4021  # het keyframe vóór 5 s (N11), niet het eerste erna
    fake.remux_variant = "vp9"
    data, _, _ = await _stream(_remux())
    assert boxes.parse(data).mime() == premade("vp9").mime()


@needs_ffmpeg
async def test_eigen_antwoord_en_stubs(fake, tmp_path):
    args = A.KeyframesArgs(demuxer="matroska", video_index=0)
    with pytest.raises(WorkerFailed) as exc:
        job, _ = await client.call(Op.KEYFRAMES, args, source=F.make(C.SOURCE))
        async with job:
            await job.result()
    assert exc.value.code == Code.NOT_BUILT

    async def keyframes(job: Job) -> dict:
        assert len(job.inputs) == 1 and not job.outputs
        await job.progress(1000, None)
        return {"keyframes_ms": [21, 2021]}

    fake.on(Op.KEYFRAMES, keyframes)
    job, _ = await client.call(Op.KEYFRAMES, args, source=F.make(C.SOURCE))
    async with job:
        events = [e async for e in job.events()]
    assert [e["ev"] for e in events] == ["progress", "result"]
    assert job.data == {"keyframes_ms": [21, 2021]}

    async def broken(job: Job) -> dict:
        raise OpError(Code.UNREADABLE, "kapot")

    fake.on(Op.KEYFRAMES, broken)
    with pytest.raises(WorkerFailed) as exc:
        job, _ = await client.call(Op.KEYFRAMES, args, source=F.make(C.SOURCE))
        async with job:
            await job.result()
    assert exc.value.code == Code.UNREADABLE
    assert [r.op for r in fake.requests] == ["keyframes"] * 3


@needs_ffmpeg
async def test_aanmelden_en_onbekende_bron(fake, tmp_path):
    copy = tmp_path / "kopie.mp4"
    copy.write_bytes(F.make("h264_aac.mp4").read_bytes() + b"\0")  # een andere inhoud
    with pytest.raises(WorkerFailed) as exc:
        await client.probe(copy)
    assert exc.value.code == Code.UNKNOWN_FORMAT
    fake.register(copy, "h264_aac.mp4")
    probe = await client.probe(copy)
    assert probe.container == "mp4" and probe.video() is not None
    odd = tmp_path / "vreemd"
    odd.write_bytes(b"geen video")
    fake.register(b"geen video", "empty.mkv")
    with pytest.raises(WorkerFailed) as exc:
        await client.probe(odd)
    assert exc.value.code == Code.UNREADABLE  # de opname van empty.mkv, met zijn fout


async def test_synth_alleen_met_loadtest(tmp_path):
    synth = A.SynthArgs(source="testsrc2", seed=0, duration_s=30, height=720)
    target = tmp_path / "uit.mkv"
    target.write_bytes(b"")
    for loadtest, code in ((False, Code.NOT_ALLOWED), (True, Code.NOT_BUILT)):
        async with running(tmp_path / f"s{loadtest}.sock", loadtest=loadtest):
            fd = os.open(target, os.O_RDWR | os.O_CLOEXEC)
            try:
                with pytest.raises(WorkerFailed) as exc:
                    job, _ = await client.call(Op.SYNTH, synth, fds=[fd], inputs=0)
                    async with job:
                        await job.result()
            finally:
                os.close(fd)
            assert exc.value.code == code


async def test_caps_zonder_ffmpeg_in_de_test(fake):
    """caps komt uit de opname, met de plaatsen van deze nep."""
    caps = await client.caps()
    assert caps.mode == "local" and caps.sandbox == "off"
    assert caps.max_procs == 3 and caps.max_transcodes == 2
    assert caps.places == {"transcode": 2, "remux": 6, "background": 1, "short": 2}
    assert caps.ffmpeg == record.load_meta()["ffmpeg"]


class _App:
    def __init__(self):
        self.state = type("State", (), {})()


async def test_de_nep_als_worker_van_de_app(tmp_path, monkeypatch):
    """Als container-worker (PLATFORM_MEDIA_WORKER): de gezondheid ziet hem als ok."""
    from app.media import health, runtime

    monkeypatch.setattr(health, "FIRST", 0.01)
    fake = FakeWorker(tmp_path / "w" / "nep.sock", mode="container")
    await fake.start()
    app = _App()
    settings = Settings(
        data_dir=tmp_path,
        web_dist=tmp_path / "web",
        jwt_secret="t" * 32,
        media_enabled=True,
        media_worker=str(fake.socket),
    )
    try:
        state = await runtime.start(app, settings)
        assert state.mode == "container"
        assert await C.until(lambda: state.worker == "ok"), state.reason
        assert state.caps is not None and state.caps.sandbox == "unchecked"  # geen zelftest
    finally:
        await runtime.stop(app)
        await fake.stop()
    assert not fake.socket.exists()
