"""De nep-stroom (§3.6): het contract van media_contract.py tegen de nep, met een client met
H.264 en een zonder (VP9, zoals de Chromium van Playwright), en wat alleen de nep heeft: de
grenzen van de sessies, de klok, native HLS bij een nieuwe init, de tegendruk en het proces.

Met ffmpeg (de fixtures); zonder slaat het over. M4 draait dezelfde scenario's tegen de echte
routes in test_media_contract.py.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from app.media import errors as media_errors
from app.media.types import MediaMap, PlayOut
from tests.media import fake_stream as FS
from tests.media import fixtures as F
from tests.media import media_contract as MC
from tests.media.fake_stream import (
    DIRECT,
    H264_PROFILE,
    NODE_DIRECT,
    NODE_REMUX,
    NODE_TRANSCODE,
    NODE_TS,
    TOKEN,
    VP9_PROFILE,
    FakeStream,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not F.available(), reason="ffmpeg ontbreekt (fixtures)"),
]

AUTH = {"Authorization": f"Bearer {TOKEN}"}
NODES = {"transcode": NODE_TRANSCODE, "remux": NODE_REMUX, "direct": NODE_DIRECT, "ts": NODE_TS}
PROFILES = {"h264": H264_PROFILE, "vp9": VP9_PROFILE}


@dataclass
class FakeWorld:
    fake: FakeStream
    client: httpx.AsyncClient
    variant: str
    name: str = "nep"
    auth: dict[str, str] = field(default_factory=lambda: dict(AUTH))

    @property
    def wait_s(self) -> float:
        return self.fake.wait_s

    def case(self, mode: MC.CaseMode) -> MC.Case:
        return MC.Case(NODES[mode], {"profile": PROFILES[self.variant]})

    def direct_source(self) -> Path:
        return F.make(DIRECT[self.variant])

    async def pace(self, speed: float | None) -> None:
        self.fake.speed = speed

    async def stop(self, sid: str, reason: str) -> None:
        self.fake.stop(sid, reason)  # type: ignore[arg-type]

    async def replace(self, node_id: uuid.UUID) -> None:
        self.fake.replace(node_id)

    async def new_init_on_restart(self) -> None:
        self.fake.new_init_on_restart()

    async def stall(self, on: bool) -> None:
        self.fake.stall(on)

    async def fail_transcode(self) -> None:
        self.fake.fail_transcode()

    async def busy(self, reason: str, retry_after: float) -> None:
        self.fake.busy(reason, retry_after)

    async def begin_stop(self) -> None:
        self.fake.begin_stop()


def _client(fake: FakeStream) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url="http://nep", timeout=30
    )


@pytest.fixture
async def fake():
    async with FakeStream(wait_s=0.5) as stream:
        yield stream


@pytest.fixture
async def client(fake):
    async with _client(fake) as c:
        yield c


@pytest.fixture(params=["h264", "vp9"])
async def world(request, fake, client):
    return FakeWorld(fake, client, request.param)


@pytest.mark.parametrize("scenario", MC.SCENARIOS, ids=lambda s: s.__name__)
async def test_contract_nep(scenario, world):
    await scenario(world)


# --- wat alleen de nep heeft -----------------------------------------------------------------


async def _play(client, node=NODE_TRANSCODE, profile=H264_PROFILE, **extra) -> PlayOut:
    r = await client.post(f"/api/v1/media/{node}/play", json={"profile": profile, **extra},
                          headers=AUTH)  # fmt: skip
    assert r.status_code == 200, r.text
    return PlayOut.model_validate(r.json())


def test_de_segmenten_van_de_nep():
    """Transcode op het rooster van 2 s aan origin 21; remux volgens de regel van 6 s, met
    segment 0 vanaf 0; de mime met codecs uit de fragmenten zelf."""
    t = FS.layout("transcode", "h264")
    assert t.grid is not None and t.grid.origin_ms == 21 and t.grid.count == 10
    assert t.spans[0] == (0, 2021) and t.spans[1] == (2021, 2000) and t.spans[-1] == (18021, 2000)
    r = FS.layout("remux", "h264")
    assert r.spans == [(0, 6021), (6021, 6000), (12021, 6000), (18021, 2000)]
    assert sum(len(d) for d in r.data) == sum(len(d) for d in t.data)
    assert t.codecs == "avc1.64001e,mp4a.40.2" and FS.layout("transcode", "vp9").codecs.endswith(
        ",opus"
    )
    alt = FS.layout("transcode", "h264", alt=True)
    assert (alt.width, alt.height) == (480, 270) and alt.spans == t.spans


async def test_onbekend_token_en_codecs(client):
    r = await client.post(
        f"/api/v1/media/{NODE_TRANSCODE}/play",
        json={"profile": H264_PROFILE},
        headers={"Authorization": "Bearer iets"},
    )
    assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid_token"
    r = await client.get(f"/api/v1/media/{NODE_TRANSCODE}/codecs", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert "avc1.64001f" in body["candidates"] and body["item"] == ["avc1.64001e", "mp4a.40.2"]
    r = await client.get(f"/api/v1/media/{uuid.uuid4()}/codecs", headers=AUTH)
    assert r.status_code == 404


async def test_fouten_op_bestelling_bij_play(fake, client):
    fake.pending("keyframes", 4)
    r = await client.post(
        f"/api/v1/media/{NODE_REMUX}/play", json={"profile": H264_PROFILE}, headers=AUTH
    )
    assert r.status_code == 409 and r.headers["retry-after"] == "4"
    assert r.json()["detail"]["params"]["reason"] == "keyframes"
    fake.play_error(media_errors.unsupported(["video_codec"]), node_id=NODE_DIRECT)
    await _play(client)  # een andere node: geen fout
    r = await client.post(
        f"/api/v1/media/{NODE_DIRECT}/play", json={"profile": H264_PROFILE}, headers=AUTH
    )
    assert r.status_code == 415 and r.json()["detail"]["code"] == "media_unsupported"
    await _play(client, NODE_DIRECT)  # één keer


async def test_wachten_of_herstarten(fake, client):
    """Hooguit 3 segmenten voor op de producent: wachten; verder: een herstart (Keuze 25)."""
    fake.speed = 40
    out = await _play(client)
    assert (await client.get(MC.url(out.sid, "0.m4s"))).status_code == 200
    assert (await client.get(MC.url(out.sid, "3.m4s"))).status_code == 200
    assert fake.restarts == []  # 3 voor: gewacht
    assert (await client.get(MC.url(out.sid, "9.m4s"))).status_code == 200
    session = fake.session(out.sid)
    assert fake.restarts == [(session.id, 9)]


async def test_native_hls_en_een_nieuwe_init_is_410(fake, client):
    fake.speed = 10
    fake.new_init_on_restart()
    native = {**H264_PROFILE, "engines": {"mse": False, "native_hls": True}}
    out = await _play(client, profile=native)
    assert (await client.get(MC.url(out.sid, "0.m4s"))).status_code == 200
    r = await client.get(MC.url(out.sid, "8.m4s"))
    assert r.status_code == 410 and r.json()["detail"]["code"] == "media_session_gone"
    assert (await client.get(MC.url(out.sid, "map.json"))).status_code == 410


async def test_acht_per_gebruiker(fake, client):
    first = [await _play(client) for _ in range(FS.PER_USER)]
    newest = await _play(client)
    gone = await client.get(MC.url(first[0].sid, "map.json"))
    assert gone.status_code == 410
    for out in [*first[1:], newest]:
        assert (await client.get(MC.url(out.sid, "map.json"))).status_code == 200
    assert len(fake.sessions) == FS.PER_USER


async def test_de_klok_grafsteen_stil_en_twaalf_uur(fake, client):
    now = [1000.0]
    fake.clock = lambda: now[0]
    stopped, idle, old = [await _play(client) for _ in range(3)]
    fake.stop(stopped.sid)
    now[0] += FS.TOMBSTONE_S - 1
    r = await client.get(MC.url(stopped.sid, "map.json"))
    assert r.status_code == 403 and r.json()["detail"]["params"]["reason"] == "stopped"
    assert (await client.get(MC.url(old.sid, "map.json"))).status_code == 200
    now[0] += 2
    assert (await client.get(MC.url(stopped.sid, "map.json"))).status_code == 410  # na 30 min
    assert (await client.get(MC.url(idle.sid, "map.json"))).status_code == 410  # 30 min stil
    for _ in range(int(FS.MAX_AGE_S // (FS.IDLE_S / 2)) + 1):
        now[0] += FS.IDLE_S / 2
        r = await client.get(MC.url(old.sid, "map.json"))
        if r.status_code != 200:
            break
    assert r.status_code == 410
    assert now[0] - 1000 >= FS.MAX_AGE_S  # pas na 12 uur


async def test_stop_user_met_de_reden(fake, client):
    a, b = await _play(client), await _play(client, NODE_DIRECT)
    assert fake.stop_user(TOKEN, "blocked") == 2
    for out, rest in ((a, "map.json"), (b, "file")):
        r = await client.get(MC.url(out.sid, rest))
        assert r.status_code == 403 and r.json()["detail"]["params"]["reason"] == "blocked"


async def test_de_tegendruk(fake, client, monkeypatch):
    """De producent loopt niet verder dan LEAD_MS voor op het laatst gevraagde segment."""
    monkeypatch.setattr(FS, "LEAD_MS", 4000)
    out = await _play(client)
    session = fake.session(out.sid)
    await asyncio.sleep(0.05)
    assert max(session.ready) == 1  # 0 en 1; segment 2 begint 4,021 s na segment 0
    assert (await client.get(MC.url(out.sid, "2.m4s"))).status_code == 200
    await asyncio.sleep(0.05)
    assert max(session.ready) == 4


async def test_map_json_bij_remux_en_vp9(client):
    out = await _play(client, NODE_REMUX, VP9_PROFILE)
    r = await client.get(MC.url(out.sid, "map.json"))
    m = MediaMap.model_validate(r.json())
    assert m.segments == [(0, 6021), (6021, 6000), (12021, 6000), (18021, 2000)]
    assert m.mime.startswith('video/mp4; codecs="vp09.00.') and m.offset == -1.0
    assert out.reasons == ["container"]


async def test_de_nep_als_proces(monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    assert FS.main(["--port", "8799", "--speed", "2"]) == 0
    assert seen["port"] == 8799 and seen["host"] == "127.0.0.1"
    assert seen["app"].title == "nep-stroom"
