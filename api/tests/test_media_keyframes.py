"""De keyframe-index aan de kant van de app (Keuze 17, M4): read_now voor play, de stap van de
analyse met de terugval, failed, en het antwoord van de worker dat niet klopt.

De echte database en de nep-worker; de handler van de opdracht keyframes is per test.
"""

from __future__ import annotations

import asyncio
import base64
import random

import pytest

from app.media import analysis, client, keyframes
from app.media.wire import Code, Op
from app.media.worker import containers as C
from app.storage import BlobStore
from tests.media import fake_worker as FW
from tests.media import fixtures as F
from tests.test_media_contract import media_app, source_probe, upload  # noqa: F401
from tests.test_media_sessions import content, maker

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not F.available(), reason="ffmpeg ontbreekt (fixtures)"),
]

TIMES = [0, 2000, 4000, 6000]
SIZES = [65536, 65536, 131072, 65536]


def answer(state: str = "ready", times=TIMES, sizes=SIZES, gop: int = 2000) -> dict:
    data = C.for_wire(C.Index(times, sizes, "cues", end_ms=8000))
    return {**data, "state": state, "max_gop_ms": gop}


class Worker:
    """Een handler die bijhoudt wat hij kreeg, en per keer zegt wat hij antwoordt."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.asked: list[bool] = []  # fallback per vraag
        self.gate: asyncio.Event | None = None

    async def __call__(self, job: FW.Job) -> dict:
        self.asked.append(job.args.fallback)  # type: ignore[attr-defined]
        if self.gate is not None:
            await self.gate.wait()
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
async def serie(media_app, client, admin):  # noqa: F811
    settings, fake = media_app
    headers, _ = admin
    node = await upload(
        client, headers, settings, "serie.mkv", content("serie"), source_probe("remux", "h264")
    )
    probe = await analysis.load(maker(settings), await _sha(settings, node))
    assert probe is not None and probe.keyframes_state == "none"
    return settings, fake, probe.model_copy(), await _sha(settings, node)


async def _sha(settings, node) -> str:
    from tests.test_media_sessions import blob_of

    return await blob_of(settings, str(node))


async def stored(settings, sha: str):
    probe = await analysis.load(maker(settings), sha)
    assert probe is not None
    return probe


# --- read_now ----------------------------------------------------------------------------------


async def test_read_now_slaat_de_index_op(serie):
    settings, fake, _, sha = serie
    worker = Worker(answer())
    fake.on(Op.KEYFRAMES, worker)
    assert await keyframes.read_now(sha) == "ready"
    assert worker.asked == [False]  # alleen de eigen lezers bij play
    probe = await stored(settings, sha)
    assert probe.keyframes_state == "ready" and probe.max_gop_ms == 2000
    assert await keyframes.load(settings, sha) == (TIMES, SIZES)
    # er is al een index: niet opnieuw vragen
    assert await keyframes.read_now(sha) == "ready"
    assert worker.asked == [False]


async def test_een_lezing_per_sha_en_doorlopen_na_het_budget(serie):
    settings, fake, _, sha = serie
    worker = Worker(answer())
    worker.gate = asyncio.Event()
    fake.on(Op.KEYFRAMES, worker)
    first = asyncio.ensure_future(keyframes.read_now(sha))
    second = asyncio.ensure_future(keyframes.read_now(sha))
    # play met zijn budget op: wait_for geeft op, de lezing loopt door
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(keyframes.read_now(sha), 0.1)
    worker.gate.set()
    assert await first == await second == "ready"
    assert worker.asked == [False]
    assert (await stored(settings, sha)).keyframes_state == "ready"


async def test_niets_gevonden_is_none_en_slaat_niets_op(serie):
    settings, fake, _, sha = serie
    fake.on(Op.KEYFRAMES, Worker({"state": "none"}))
    assert await keyframes.read_now(sha) == "none"
    assert (await stored(settings, sha)).keyframes_state == "none"
    assert await keyframes.load(settings, sha) == ([], [])


async def test_geen_lezer_voor_deze_container(media_app, client, admin):  # noqa: F811
    settings, fake = media_app
    headers, _ = admin
    p = source_probe("remux", "h264")
    p.container = "onbekend"  # geen demuxer op de witte lijst: de worker proeft niet zelf
    node = await upload(client, headers, settings, "oud.bin", content("oud"), p)
    worker = Worker(answer())
    fake.on(Op.KEYFRAMES, worker)
    assert await keyframes.read_now(await _sha(settings, node)) == "none"
    assert worker.asked == []


async def test_de_worker_ligt_eruit(serie):
    _, fake, _, sha = serie
    fake.on(Op.KEYFRAMES, Worker(FW.OpError(Code.INTERNAL, "kapot")))
    with pytest.raises(client.WorkerFailed):
        await keyframes.read_now(sha)
    client.bind(None)
    with pytest.raises(client.WorkerDown):
        await keyframes.read_now(sha)


# --- de stap van de analyse --------------------------------------------------------------------


def context(settings, sha: str, probe) -> analysis.StepContext:
    path = BlobStore(settings.blob_dir).path(sha)
    return analysis.StepContext(settings, sha, path, None, "shows", probe)


async def test_de_stap_valt_terug_op_ffprobe(serie):
    settings, fake, probe, sha = serie
    worker = Worker({"state": "none"}, answer())
    fake.on(Op.KEYFRAMES, worker)
    ctx = context(settings, sha, probe)
    await keyframes.step(ctx)
    assert worker.asked == [False, True]
    assert ctx.probe.keyframes_state == "ready"
    assert (await stored(settings, sha)).keyframes_state == "ready"


async def test_sparse_gaat_ook_naar_de_terugval(serie):
    settings, fake, probe, sha = serie
    gaps = answer("sparse", [0, 25_000], [65536, 65536], gop=25_000)
    worker = Worker(gaps, answer())
    fake.on(Op.KEYFRAMES, worker)
    await keyframes.step(context(settings, sha, probe))
    assert worker.asked == [False, True]
    assert (await stored(settings, sha)).keyframes_state == "ready"


async def test_failed_en_niet_steeds_opnieuw(serie):
    settings, fake, probe, sha = serie
    worker = Worker({"state": "none"}, FW.OpError(Code.TIMEOUT, "te lang"))
    fake.on(Op.KEYFRAMES, worker)
    ctx = context(settings, sha, probe)
    await keyframes.step(ctx)
    assert ctx.probe.keyframes_state == "failed"
    again = await stored(settings, sha)
    assert again.keyframes_state == "failed"
    assert await keyframes.load(settings, sha) == ([], [])
    await keyframes.step(context(settings, sha, again))
    assert len(worker.asked) == 2  # failed: de stap vraagt niets meer
    # en play vraagt ook niets (failed is geen none)
    assert await keyframes.read_now(sha) == "failed"
    assert len(worker.asked) == 2


async def test_een_andere_fout_gaat_door_voor_een_nieuwe_poging(serie):
    settings, fake, probe, sha = serie
    fake.on(Op.KEYFRAMES, Worker(FW.OpError(Code.NOT_BUILT, "nog niet")))
    with pytest.raises(client.WorkerFailed):
        await keyframes.step(context(settings, sha, probe))
    assert (await stored(settings, sha)).keyframes_state == "none"


async def test_een_stap_met_een_index_doet_niets(serie):
    settings, fake, probe, sha = serie
    worker = Worker(answer())
    fake.on(Op.KEYFRAMES, worker)
    probe.keyframes_state = "ready"
    await keyframes.step(context(settings, sha, probe))
    assert worker.asked == []


# --- het antwoord van de worker ----------------------------------------------------------------


def test_een_antwoord_dat_niet_klopt_is_none():
    good = answer()
    assert keyframes.parse(good)[0] == "ready"
    for bad in (
        {},
        {"state": "ready"},
        {**good, "state": "klaar"},
        {**good, "keyframes": "!!!"},
        {**good, "keyframes": base64.b64encode(b"\x00" * 10).decode()},
        {**good, "keyframes": ""},
    ):
        assert keyframes.parse(bad) == ("none", None, None), bad
    assert keyframes.parse({**good, "max_gop_ms": True})[2] is None
    assert keyframes.parse({**good, "max_gop_ms": -1})[2] is None


@pytest.mark.parametrize("seed", range(5))
def test_fuzz_parse(seed):
    """Willekeurige antwoorden (vast zaad): nooit een uitzondering, altijd een geldige staat."""
    rnd = random.Random(seed)
    good = answer()
    raw = base64.b64decode(good["keyframes"])
    for _ in range(300):
        data = dict(good)
        choice = rnd.randrange(4)
        if choice == 0:
            blob = bytearray(raw)
            for _ in range(rnd.randrange(1, 8)):
                blob[rnd.randrange(len(blob))] = rnd.randrange(256)
            data["keyframes"] = base64.b64encode(bytes(blob)).decode()
        elif choice == 1:
            data["keyframes"] = base64.b64encode(rnd.randbytes(rnd.randrange(64))).decode()
        elif choice == 2:
            data["max_gop_ms"] = rnd.choice([None, "x", 1.5, -3, 10**12, []])
        else:
            data["state"] = rnd.choice(["ready", "sparse", "none", None, 3])
        state, blob, gop = keyframes.parse(data)
        assert state in ("none", "ready", "sparse")
        assert (blob is None) == (state == "none")
        assert gop is None or (isinstance(gop, int) and gop >= 0)
