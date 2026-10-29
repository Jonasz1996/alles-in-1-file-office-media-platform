"""Het contract van de stroom (§3.4, §3.6) tegen de echte routes (M4): dezelfde scenario's als
media_contract.py van M0, die test_fake_stream.py tegen de nep draait.

De app is de echte (play van M3, de stroom van M4, de guard), met de nep-worker eronder
(tests/media/fake_worker.py): die schrijft vooraf gemaakte fMP4 (20 s 360p op het rooster van
2 s, H.264 + AAC of VP9 + Opus) naar de echte pipe, ongeacht de bron. De bronnen hier zijn dus
gewone bytes met een opgeslagen probe die tot de juiste weg leidt:
- transcode: de probe van stream_src.mkv met HEVC als videocodec (geen browser hier kan dat);
- remux: de probe van stream_src.mkv zoals hij is (H.264 + AAC in Matroska), of met VP9 + Opus
  voor de client zonder H.264; de keyframe-index komt bij play van de nep-worker (de keyframes
  van de vooraf gemaakte uitvoer, in de tijd van de container);
- ts: dezelfde probe als transcode, in MPEG-TS (het geluid wordt omgezet, Keuze 22);
- direct: de echte fixture (h264_aac.mp4, of vp9_opus.webm).

De haken van de wereld zijn die van de nep-worker: `pace` is `fake.speed`,
`new_init_on_restart` de fout `new_stsd`, `stall` de fout `hang`, `fail_transcode` twee keer
`crash_after` (de herstart en de nieuwe poging); `busy` zet admission.view op een weigering.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import MediaConfig, get_settings
from app.main import app
from app.media import admission, analysis, runtime, sessions, streams
from app.media import profile as P
from app.media.state import MediaPolicy, MediaState
from app.media.types import BudgetView
from app.media.wire import Op
from app.media.worker import containers as C
from tests.media import fake_worker as FW
from tests.media import fixtures as F
from tests.media import media_contract as MC
from tests.test_files import put, root
from tests.test_media_decide import BROWSERS
from tests.test_media_decide import probe as recorded
from tests.test_media_sessions import blob_of, content, maker

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not F.available(), reason="ffmpeg ontbreekt (fixtures)"),
]

WAIT_S = 2.0  # media_segment_wait in deze tests (20 s in het echt)
DIRECT = {"h264": "h264_aac.mp4", "vp9": "vp9_opus.webm"}
BROWSER = {"h264": "chrome_ci", "vp9": "chromium141"}
HEVC = {"codec": "hevc", "codec_string": "hvc1.1.6.L63.90"}
VP9 = {"codec": "vp9", "codec_string": "vp09.00.21.08"}
OPUS = {"codec": "opus", "codec_string": "opus"}


def source_probe(mode: str, variant: str):
    """De probe van stream_src.mkv, aangepast tot de weg van dit geval."""
    p = recorded("stream_src.mkv")
    video, audio = p.streams[0], p.streams[1]
    if mode in ("transcode", "ts"):
        for key, value in HEVC.items():
            setattr(video, key, value)
        if mode == "ts":
            p.container = "mpegts"
    elif variant == "vp9":
        for key, value in VP9.items():
            setattr(video, key, value)
        for key, value in OPUS.items():
            setattr(audio, key, value)
    return p


def profile(variant: str) -> dict[str, Any]:
    """Het profiel van de client: de kandidaten plus de strings van de bronnen, gemeten met
    het model van de browser (chrome_ci met H.264, chromium141 zonder)."""
    base, name, model, rest = BROWSERS[BROWSER[variant]]
    assert model is not None
    strings = list(P.candidates())
    strings += [HEVC["codec_string"], VP9["codec_string"], "opus", "avc1.64001e", "mp4a.40.2"]
    for fixture in DIRECT.values():
        strings += [s.codec_string for s in recorded(fixture).streams if s.codec_string]
    codecs = {s: model(s).model_dump(exclude_none=True) for s in dict.fromkeys(strings)}
    return {"v": 1, "base": base, "client": {"name": name}, **rest, "codecs": codecs}


def keyframes_handler(variant: str, start_ms: int):
    """De opdracht keyframes van de nep: de keyframes van de vooraf gemaakte uitvoer (één per
    fragment), in de tijd van de container (mediatijd + S), met de bytes van elk fragment."""
    parsed = FW.premade(variant)
    times = [f.video_start_ms + start_ms for f in parsed.fragments]
    sizes = [len(f.data) for f in parsed.fragments]
    end = parsed.fragments[-1].end_ms + start_ms

    async def handler(job: FW.Job) -> dict:
        return C.for_wire(C.Index(times, sizes, "cues", end_ms=end))

    return handler


@dataclass
class RealWorld:
    client: httpx.AsyncClient
    fake: FW.FakeWorker
    variant: str
    headers: dict[str, str]
    cases: dict[str, MC.Case]
    direct: Path
    monkeypatch: pytest.MonkeyPatch
    name: str = "echt"
    wait_s: float = WAIT_S
    replaced: list[uuid.UUID] = field(default_factory=list)

    @property
    def auth(self) -> dict[str, str]:
        return self.headers

    def case(self, mode: MC.CaseMode) -> MC.Case | None:
        return self.cases.get(mode)

    def direct_source(self) -> Path:
        return self.direct

    async def pace(self, speed: float | None) -> None:
        self.fake.speed = speed

    async def stop(self, sid: str, reason: str) -> None:
        s = sessions.table().by_sid[sid]
        assert sessions.stop(s.id, reason)  # type: ignore[arg-type]

    async def replace(self, node_id: uuid.UUID) -> None:
        new = content(f"nieuwe versie {node_id}")
        r = await self.client.put(
            f"/api/v1/files/{node_id}/content", content=new, headers=self.headers
        )
        assert r.status_code == 200, r.text
        self.replaced.append(node_id)

    async def new_init_on_restart(self) -> None:
        self.fake.fault("new_stsd", op=Op.STREAM)

    async def stall(self, on: bool) -> None:
        if on:
            self.fake.fault("hang", op=Op.STREAM, times=1000)
        else:
            self.fake.clear_faults()

    async def fail_transcode(self) -> None:
        self.fake.fault("crash_after", op=Op.STREAM, times=2, n=0)

    async def busy(self, reason: str, retry_after: float) -> None:
        refusal = BudgetView(
            budget=3.0, in_use=0.0, places_free=0, user_free=2, reason=reason,
            retry_after=retry_after,
        )  # fmt: skip
        self.monkeypatch.setattr(admission, "view", lambda user_id, link_id: refusal)

    async def begin_stop(self) -> None:
        runtime.begin_stop(app)


async def upload(client, headers, settings, name: str, data: bytes, probe) -> uuid.UUID:
    parent = (await root(client, headers))["folder"]["id"]
    r = await put(client, headers, parent, name, data)
    assert r.status_code == 201, r.text
    node = r.json()
    sha = await blob_of(settings, node["id"])
    await analysis.save_probe(maker(settings), sha, probe)
    return uuid.UUID(node["id"])


@pytest.fixture
async def media_app(settings, client, monkeypatch, tmp_path):
    """Media aan met een gezonde nep-worker, verse sessies en toelating, en een korte wachttijd
    voor een segment."""
    fast = settings.model_copy(update={"media_enabled": True, "media_segment_wait": WAIT_S})
    app.dependency_overrides[get_settings] = lambda: fast
    sessions.reset()
    streams.reset()
    state = MediaState(enabled=True, worker="ok")
    monkeypatch.setattr(app.state, "media", state, raising=False)
    monkeypatch.setattr(app.state, "media_policy", MediaPolicy(MediaConfig()), raising=False)
    runtime.bind(app, fast)
    async with FW.running(tmp_path / "w.sock") as fake:
        yield fast, fake
        for s in list(sessions.table().open()):
            sessions.close(s)
        for st in streams.all():
            await streams.close(st.session)
    runtime.unbind()
    sessions.reset()
    streams.reset()


@pytest.fixture(params=["h264", "vp9"])
async def world(request, media_app, client, admin, monkeypatch):
    settings, fake = media_app
    variant = request.param
    headers, _ = admin
    fake.remux_variant = variant  # type: ignore[assignment]
    fake.on(Op.KEYFRAMES, keyframes_handler(variant, recorded("stream_src.mkv").start_ms))
    body = {"profile": profile(variant)}
    cases: dict[str, MC.Case] = {}
    for mode, name in (("transcode", "film.mkv"), ("remux", "serie.mkv"), ("ts", "opname.ts")):
        node = await upload(
            client, headers, settings, name, content(f"{variant}-{mode}"),
            source_probe(mode, variant),
        )  # fmt: skip
        cases[mode] = MC.Case(node, dict(body))
    direct = F.make(DIRECT[variant])
    node = await upload(
        client, headers, settings, direct.name, direct.read_bytes(), recorded(direct.name)
    )
    cases["direct"] = MC.Case(node, dict(body))
    return RealWorld(client, fake, variant, headers, cases, direct, monkeypatch)


@pytest.mark.parametrize("scenario", MC.SCENARIOS, ids=lambda s: s.__name__)
async def test_contract_echt(scenario, world):
    await scenario(world)
    # Na elk scenario: geen run meer bij de worker voor een gesloten sessie (het sluiten loopt
    # op de achtergrond; wacht daarop)
    await asyncio.gather(*list(sessions._tasks), return_exceptions=True)
    for s in sessions.table().by_sid.values():
        if s.closed is not None:
            assert streams.get(s) is None


async def test_de_wereld_kiest_de_juiste_weg(world):
    """De bronnen leiden tot de weg die het contract vraagt (anders test het niets)."""
    for mode in ("transcode", "remux", "direct", "ts"):
        out = await MC.play(world, mode)  # type: ignore[arg-type]
        await MC.call(world, "DELETE", out.sid)
