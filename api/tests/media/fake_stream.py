"""De nep-stroom (§3.6): een ASGI-app met `play` en `/api/v1/stream/…` in precies het contract
van §3.4, uit segmenten die het harnas bij de start maakt met echte ffmpeg. Zo kan M6 de speler
bouwen voordat M3 en M4 klaar zijn; `media_contract.py` beschrijft wat ook het echte moet doen.

    fake = FakeStream()                                    # de fixtures maken (echte ffmpeg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), …) as c:
        r = await c.post(f"/api/v1/media/{NODE_TRANSCODE}/play", json={"profile": H264},
                         headers={"Authorization": f"Bearer {TOKEN}"})
    cd api && uv run python -m tests.media.fake_stream --port 8790     # als proces (M6)

**De wereld.** Vier nodes, elk met een vaste weg (de nep beslist niet, Keuze 19 is van M3):

| node | mode | bron |
|---|---|---|
| `NODE_TRANSCODE` | transcode | `stream_src.mkv`: de uitvoer van de transcode van §3.3 |
| `NODE_REMUX` | remux | dezelfde fragmenten, in segmenten volgens de regel van 6 s |
| `NODE_DIRECT` | direct | `h264_aac.mp4`, of `vp9_opus.webm` zonder H.264 |
| `NODE_TS` | transcode | als `NODE_TRANSCODE`, voor het scenario met MPEG-TS |

De uitvoer staat in de fixtures `stream_h264.mp4` en `stream_vp9.mp4` (Keuze 23 voor remux).
H.264 + AAC als het profiel een `avc1.…` met `mse` (bij direct: `file`) heeft, anders VP9 + Opus
(zoals de Chromium van Playwright). De probe komt uit de opname van de echte worker
(`recorded/probe/stream_src.mkv.json`); de tijden van de segmenten uit de fragmenten zelf.

**De tijden (spike punten 10, 12 en 17).** Eén referentie voor `EXTINF`, `map.json` en de
segmenten: mediatijd. Het rooster van 2 s hangt aan `origin` (het eerste videobeeld, hier
21 ms): segment 0 loopt van 0 tot `origin + 2 s` (`EXTINF` 2,021), segment k van `origin + 2k`;
N komt uit het laatste videobeeld (`SegmentGrid.count_for`), het laatste segment loopt tot de duur
van de probe. Bij remux begint segment 0 óók op 0 (en niet op het eerste keyframe), zodat de som
van de `EXTINF`'s tot segment n precies het begin `b_n` in mediatijd is, net als bij het rooster;
segment n ≥ 1 begint op het eerste keyframe ≥ n·6 s. In elk segment is `tfdt` mediatijd + 1 s:
de speler zet `timestampOffset` op `offset` (−1).

**De producent (Keuze 25), virtueel.** Per sessie maakt een taak de segmenten na elkaar, in het
tempo `speed` (mediaseconden per seconde; None: meteen). Een segment dat er is, komt meteen. Ligt
n hooguit 3 segmenten voor op wat de producent nu maakt: wachten. Anders een herstart op n
(`restarts`). Wachten duurt hooguit `wait_s` (20 s): dan 503 `media_timeout` met `Retry-After:
2`, en na 3 keer achter elkaar gaat de sessie dicht. Niet verder dan 60 s voor op het laatst
gevraagde segment (de tegendruk).

**De generatie van de init (spike punt 13).** `new_init_on_restart()`: de volgende herstart geeft
een init met een andere `stsd` (de fixtures `stream_*_alt.mp4`: 480×270, dezelfde tijden). De
generatie gaat dan +1; elk segment van die run heeft `X-Init: <gen>`, `map.json` noemt de nieuwe
init, en de oude blijft te halen (wat in de cache staat, blijft geldig). Een native HLS-client
(engines zonder MSE, met native_hls) kan die kop niet lezen: daar sluit de sessie met 410.

**MPEG-TS (spike punt 11).** Zoeken in TS is niet precies (S2: de uitvoer begint op het
volgende keyframe van de bron). Het contract eist bij TS na een sprong alleen dat segment n
binnen zijn eigen vak van het rooster valt en met een keyframe begint. De nep zoekt bij
`NODE_TS` gewoon precies (zoals M4 het zou kunnen oplossen door eerder te beginnen en weg te
gooien wat voor de grens ligt).

**Toegang (Keuze 21).** play vraagt een Bearer uit `tokens`. De sid is
`<node_name>.<base64url van 24 bytes>`, een sleutel in het pad. Elk verzoek onder `/stream/`
gaat langs de guard: onbekend of verlopen (12 uur, 30 minuten stil) is 410 `media_session_gone`;
gestopt door de app (`stop`, `stop_user`) is de grafsteen: 30 minuten 403 `media_access_gone`
met de reden; een nieuwe versie (`replace`) is 410 met `reason: replaced`. `replace` in de body
sluit de oude sessie in dezelfde beurt; meer dan 8 per gebruiker sluit de oudste.

**Koppen (Keuze 35).** Via `StreamHeaders` van de app zelf: `Cross-Origin-Resource-Policy:
same-origin` en nooit `Content-Disposition` onder `/stream/`, en `no-store` waar de route niets
zet. `map.json` en de playlists `no-store`; init en segmenten `private, max-age=3600` met
`X-Init`; `/file` als een download (`private, max-age=0, must-revalidate`, de sha als ETag, Range
en `If-Range` via FileResponse). Een fout heeft de vorm van `errors.fail`.

**Fouten op bestelling:** `play_error` (een API-fout voor de volgende play), `busy`, `pending`,
`stall` (de producent maakt niets meer: wachten tot de tijdgrens), `fail_transcode` (de volgende
herstart faalt: 502 `media_transcode_failed`), `begin_stop` (de app stopt: 503 `media_busy`
`worker` onder `/stream/`, zoals F3).

**Logs.** De nep logt op `tests.media.fake_stream` met het uuid van de sessie, nooit de sid; de
toegangslog van uvicorn gaat door `HideSecrets` van de app (`test_media_logs.py`).

Wat de nep niet doet: beslissen (Keuze 19), ondertitels, sprites, audio (M5, M9), publieke links
(M3), de toelating (M4), en het afbreken van een lopende `/file`-respons als de toegang
midden in de stroom verdwijnt (M3, `sessions.guarded_body`): hier geldt de guard per verzoek.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import hashlib
import logging
import math
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, PlainTextResponse, Response

from app import errors
from app.main import StreamHeaders
from app.media import errors as media_errors
from app.media import profile as media_profile
from app.media.state import media_state
from app.media.types import (
    AudioTrack,
    ClientProfile,
    CodecsOut,
    MediaMap,
    PlayIn,
    PlayOut,
    PlayTracks,
    PlayUrls,
    Probe,
    QualityInfo,
    SegmentGrid,
)
from tests.media import fixtures as F
from tests.media import record
from tests.media.fake_worker import premade

log = logging.getLogger("tests.media.fake_stream")

NODE_TRANSCODE = uuid.UUID("00000000-0000-4000-8000-00000000f001")
NODE_REMUX = uuid.UUID("00000000-0000-4000-8000-00000000f002")
NODE_DIRECT = uuid.UUID("00000000-0000-4000-8000-00000000f003")
NODE_TS = uuid.UUID("00000000-0000-4000-8000-00000000f004")
TOKEN = "nep-token"
SOURCE = "stream_src.mkv"
DIRECT = {"h264": "h264_aac.mp4", "vp9": "vp9_opus.webm"}

AHEAD = 3  # segmenten voor op de producent: wachten in plaats van herstarten (Keuze 25)
LEAD_MS = 60_000  # de tegendruk: niet verder voor op het laatst gevraagde segment
WAIT_S = 20.0
TIMEOUTS_TO_CLOSE = 3
TOMBSTONE_S = 30 * 60
IDLE_S = 30 * 60
MAX_AGE_S = 12 * 3600
PER_USER = 8
GRID_MS = 2000
REMUX_MS = 6000
MAXRATE_K = 1000  # de transcode van de fixtures (fixtures.stream_output)
AUDIO_K = {"h264": 160, "vp9": 128}

NO_STORE = "no-store"
SEGMENT_CACHE = "private, max-age=3600"
FILE_CACHE = "private, max-age=0, must-revalidate"
PLAYLIST_TYPE = "application/vnd.apple.mpegurl"
SEGMENT_TYPE = "video/mp4"

Mode = Literal["direct", "remux", "transcode"]
Variant = Literal["h264", "vp9"]

H264_PROFILE = {
    "v": 1,
    "base": "web",
    "engines": {"mse": True},
    "file": ["video/mp4"],
    "codecs": {
        "avc1.64001e": {"mse": True, "file": True},
        "avc1.64001f": {"mse": True, "file": True},
        "mp4a.40.2": {"mse": True, "file": True},
    },
}
VP9_PROFILE = {
    "v": 1,
    "base": "web",
    "engines": {"mse": True},
    "file": ["video/webm"],
    "codecs": {
        "vp09.00.21.08": {"mse": True, "file": True},
        "vp09.00.40.08": {"mse": True, "file": True},
        "opus": {"mse": True, "file": True},
    },
}


@dataclass(frozen=True)
class Item:
    node_id: uuid.UUID
    mode: Mode
    ts: bool = False


ITEMS = {
    NODE_TRANSCODE: Item(NODE_TRANSCODE, "transcode"),
    NODE_REMUX: Item(NODE_REMUX, "remux"),
    NODE_DIRECT: Item(NODE_DIRECT, "direct"),
    NODE_TS: Item(NODE_TS, "transcode", ts=True),
}


# --- de segmenten, één keer per proces -------------------------------------------------------


@dataclass
class Layout:
    """De segmenten van één weg (transcode of remux) met één variant: begin en duur in mediatijd,
    de bytes per segment en de init, en de waarden voor de playlists."""

    mode: Mode
    spans: list[tuple[int, int]]
    data: list[bytes]
    init: bytes
    mime: str
    codecs: str
    width: int
    height: int
    grid: SegmentGrid | None
    keyframes: list[int]

    @property
    def count(self) -> int:
        return len(self.spans)


@functools.cache
def source_probe() -> Probe:
    found = record.load(SOURCE)
    assert found is not None and "data" in found, f"geen opname van {SOURCE}"
    return Probe.model_validate(found["data"])


@functools.cache
def layout(mode: Mode, variant: Variant, alt: bool = False) -> Layout:
    parsed = premade(variant, alt)
    probe = source_probe()
    video = probe.video()
    assert video is not None and video.start_ms is not None and video.last_frame_ms is not None
    duration = probe.duration_ms or parsed.fragments[-1].end_ms
    keyframes = [f.video_start_ms for f in parsed.fragments]
    grid = None
    if mode == "transcode":
        origin = video.start_ms
        grid = SegmentGrid(
            grid_ms=GRID_MS,
            origin_ms=origin,
            count=SegmentGrid.count_for(origin, video.last_frame_ms, GRID_MS),
            last_frame_ms=video.last_frame_ms,
        )
        begins = [grid.start_of(k) for k in range(grid.count)]
        owner = [grid.segment_of(f.video_start_ms) for f in parsed.fragments]
    else:
        begins = [0]  # segment 0 vanaf 0: de som van de EXTINF's is mediatijd
        n = 1
        while True:
            later = [t for t in keyframes if t >= n * REMUX_MS]
            if not later:
                break
            if later[0] > begins[-1]:
                begins.append(later[0])
            n = max(n + 1, later[0] // REMUX_MS + 1)
        owner = [max(i for i, b in enumerate(begins) if b <= t) for t in keyframes]
    ends = [*begins[1:], duration]
    spans = [(b, e - b) for b, e in zip(begins, ends, strict=True)]
    data = [b"".join(f.data for f, o in zip(parsed.fragments, owner, strict=True) if o == k)
            for k in range(len(spans))]  # fmt: skip
    mime = parsed.mime()
    codecs = mime.split('codecs="', 1)[1].rstrip('"')
    return Layout(
        mode, spans, data, parsed.init, mime, codecs,
        parsed.video.width, parsed.video.height, grid, keyframes,
    )  # fmt: skip


def prepare() -> None:
    """Alles maken wat de nep nodig heeft (de fixtures met echte ffmpeg, na elkaar)."""
    for variant in ("h264", "vp9"):
        for alt in (False, True):
            for mode in ("transcode", "remux"):
                layout(mode, variant, alt)
        F.make(DIRECT[variant])


@functools.cache
def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_of(name: str) -> Probe:
    found = record.load(name)
    assert found is not None and "data" in found, f"geen opname van {name}"
    return Probe.model_validate(found["data"])


def direct_mime(name: str) -> str:
    probe = _probe_of(name)
    kind = "video/webm" if probe.container in ("webm", "matroska") else "video/mp4"
    codecs = ",".join(s.codec_string for s in probe.streams if s.codec_string)
    return f'{kind}; codecs="{codecs}"'


# --- de sessies ------------------------------------------------------------------------------


@dataclass
class _Producer:
    start: int
    gen: int
    next: int
    failing: bool = False
    failed: bool = False
    done: bool = False
    task: asyncio.Task | None = None


@dataclass
class FakeSession:
    id: uuid.UUID
    sid: str
    item: Item
    user: str
    mode: Mode
    variant: Variant
    native: bool
    base: str
    created: float
    seen: float
    gen: int = 1
    alts: dict[int, bool] = field(default_factory=lambda: {1: False})
    ready: dict[int, int] = field(default_factory=dict)  # segment → generatie
    producer: _Producer | None = None
    timeouts: int = 0
    wanted: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    moved: asyncio.Event = field(default_factory=asyncio.Event)

    def notify(self) -> None:
        event, self.changed = self.changed, asyncio.Event()
        event.set()

    def nudge(self) -> None:
        event, self.moved = self.moved, asyncio.Event()
        event.set()


def _supports(profile: ClientProfile, prefix: str, kind: Literal["mse", "file"]) -> bool:
    return any(k.startswith(prefix) and getattr(v, kind) for k, v in profile.codecs.items())


class FakeStream:
    def __init__(
        self,
        *,
        node_name: str = "local",
        tokens: tuple[str, ...] = (TOKEN,),
        wait_s: float = WAIT_S,
        speed: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        prepare()
        self.node_name = node_name
        self.tokens = set(tokens)
        self.wait_s = wait_s
        self.speed = speed
        self.clock = clock
        self.items: dict[uuid.UUID, Item] = dict(ITEMS)
        self.sessions: dict[str, FakeSession] = {}
        self.tombstones: dict[str, tuple[str, float]] = {}  # sid → (reden, tot)
        self.replaced: dict[str, float] = {}  # sid → tot
        self.restarts: list[tuple[uuid.UUID, int]] = []  # (sessie, segment)
        self._play_errors: list[tuple[uuid.UUID | None, HTTPException]] = []
        self._new_init = 0
        self._stall = False
        self._fail = 0
        self.app = self._build()

    # --- voor de test ---

    def session(self, sid: str) -> FakeSession:
        return self.sessions[sid]

    def stop(self, sid: str, reason: Literal["stopped", "revoked", "blocked"] = "stopped") -> None:
        """De app sluit de sessie zelf (Stop op /beheer, een ingetrokken toegang): de grafsteen."""
        self._close(sid, tombstone=reason)

    def stop_user(self, token: str, reason: Literal["revoked", "blocked"] = "revoked") -> int:
        found = [s.sid for s in self.sessions.values() if s.user == token]
        for sid in found:
            self._close(sid, tombstone=reason)
        return len(found)

    def replace(self, node_id: uuid.UUID) -> int:
        """Een nieuwe versie van het bestand: elke sessie erop geeft 410 met reden replaced."""
        found = [s.sid for s in self.sessions.values() if s.item.node_id == node_id]
        for sid in found:
            self._close(sid)
            self.replaced[sid] = self.clock() + TOMBSTONE_S
        return len(found)

    def play_error(self, exc: HTTPException, node_id: uuid.UUID | None = None) -> None:
        """De volgende play (op node_id, of elke) geeft deze fout."""
        self._play_errors.append((node_id, exc))

    def busy(self, reason: str = "cpu", retry_after: float = 2) -> None:
        self.play_error(media_errors.busy(reason, retry_after))

    def pending(self, reason: str = "probe", retry_after: float = 2) -> None:
        self.play_error(media_errors.probe_pending(reason, retry_after))

    def new_init_on_restart(self, times: int = 1) -> None:
        self._new_init += times

    def stall(self, on: bool = True) -> None:
        self._stall = on

    def fail_transcode(self, times: int = 1) -> None:
        self._fail += times

    def begin_stop(self) -> None:
        media_state(self.app).stopping = True

    async def aclose(self) -> None:
        for sid in list(self.sessions):
            self._close(sid)
        await asyncio.sleep(0)

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # --- de sessies ---

    def _user(self, authorization: str | None) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise errors.fail(401, "not_logged_in", "Niet ingelogd")
        token = authorization.split(" ", 1)[1].strip()
        if token not in self.tokens:
            raise errors.fail(401, "invalid_token", "Ongeldig of verlopen token")
        return token

    def _close(self, sid: str, *, tombstone: str | None = None) -> None:
        s = self.sessions.pop(sid, None)
        if tombstone is not None:
            self.tombstones[sid] = (tombstone, self.clock() + TOMBSTONE_S)
        if s is None:
            return
        if s.producer is not None and s.producer.task is not None:
            s.producer.task.cancel()
        s.notify()
        log.info("sessie %s dicht%s", s.id, f" ({tombstone})" if tombstone else "")

    def guard(self, sid: str) -> FakeSession:
        now = self.clock()
        stone = self.tombstones.get(sid)
        if stone is not None:
            if now < stone[1]:
                raise media_errors.access_gone(stone[0])
            del self.tombstones[sid]
        until = self.replaced.get(sid)
        if until is not None:
            if now < until:
                raise media_errors.session_gone("replaced")
            del self.replaced[sid]
        s = self.sessions.get(sid)
        if s is None:
            raise media_errors.session_gone()
        if now - s.created > MAX_AGE_S or now - s.seen > IDLE_S:
            self._close(sid)
            raise media_errors.session_gone()
        s.seen = now
        return s

    def _layout(self, s: FakeSession, gen: int | None = None) -> Layout:
        alt = s.alts[gen if gen is not None else s.gen]
        assert s.mode != "direct"
        return layout(s.mode, s.variant, alt)

    def _start(self, s: FakeSession, n: int, *, restart: bool) -> None:
        old = s.producer
        if old is not None and old.task is not None:
            old.task.cancel()
        if restart and self._new_init > 0:
            self._new_init -= 1
            s.gen += 1
            s.alts[s.gen] = not s.alts[s.gen - 1]
        failing = restart and self._fail > 0
        if failing:
            self._fail -= 1
        p = _Producer(start=n, gen=s.gen, next=n, failing=failing)
        p.task = asyncio.get_running_loop().create_task(self._produce(s, p))
        s.producer = p
        if restart:
            self.restarts.append((s.id, n))
            log.info("sessie %s: herstart op segment %d (generatie %d)", s.id, n, p.gen)

    async def _produce(self, s: FakeSession, p: _Producer) -> None:
        lay = self._layout(s, p.gen)
        k = p.start
        try:
            while k < lay.count:
                if self._stall:
                    await asyncio.Event().wait()
                # de tegendruk: niet verder dan LEAD_MS voor op het laatst gevraagde segment
                while lay.spans[k][0] - lay.spans[min(s.wanted, lay.count - 1)][0] > LEAD_MS:
                    await s.moved.wait()
                if p.failing:
                    p.failed = True
                    s.notify()
                    return
                if self.speed:
                    await asyncio.sleep(lay.spans[k][1] / 1000 / self.speed)
                else:
                    await asyncio.sleep(0)
                s.ready[k] = p.gen
                k += 1
                p.next = k
                s.notify()
            p.done = True
        except asyncio.CancelledError:
            pass

    async def _segment(self, s: FakeSession, n: int) -> int:
        """Wacht tot segment n er is (of herstart); geeft zijn generatie."""
        s.wanted = n
        s.nudge()
        if n not in s.ready:
            p = s.producer
            running = p is not None and not p.done and not p.failed
            if not (running and p is not None and p.start <= n <= p.next + AHEAD):
                self._start(s, n, restart=True)
            try:
                async with asyncio.timeout(self.wait_s):
                    while n not in s.ready:
                        event = s.changed
                        if s.sid not in self.sessions:
                            self.guard(s.sid)  # dicht tijdens het wachten: de reden
                        if s.producer is not None and s.producer.failed:
                            raise media_errors.transcode_failed()
                        await event.wait()
            except TimeoutError:
                s.timeouts += 1
                if s.timeouts >= TIMEOUTS_TO_CLOSE:
                    self._close(s.sid)
                raise media_errors.timeout(2) from None
        s.timeouts = 0
        return s.ready[n]

    # --- de app ---

    def _build(self) -> FastAPI:
        app = FastAPI(title="nep-stroom", openapi_url=None, docs_url=None, redoc_url=None)
        app.add_exception_handler(RequestValidationError, errors.invalid_input)
        app.add_middleware(StreamHeaders)

        @app.get("/api/v1/media/{node_id}/codecs", response_model=CodecsOut)
        async def codecs(node_id: uuid.UUID, authorization: Annotated[str | None, Header()] = None):
            self._user(authorization)
            item = self.items.get(node_id)
            if item is None:
                raise errors.fail(404, "not_found", "Niet gevonden")
            names = [DIRECT["h264"], DIRECT["vp9"]] if item.mode == "direct" else [SOURCE]
            strings = [s.codec_string for n in names for s in _probe_of(n).streams]
            return CodecsOut(
                candidates=media_profile.candidates(),
                item=list(dict.fromkeys(x for x in strings if x)),
            )

        @app.post("/api/v1/media/{node_id}/play", response_model=PlayOut)
        async def play(
            node_id: uuid.UUID,
            body: PlayIn,
            authorization: Annotated[str | None, Header()] = None,
        ):
            user = self._user(authorization)
            for i, (target, exc) in enumerate(self._play_errors):
                if target in (None, node_id):
                    del self._play_errors[i]
                    raise exc
            item = self.items.get(node_id)
            if item is None:
                raise errors.fail(404, "not_found", "Niet gevonden")
            return self._play(user, item, body)

        @app.get("/api/v1/stream/{sid}/map.json", response_model=MediaMap)
        async def map_json(sid: str, response: Response):
            s = self._stream_session(sid)
            lay = self._layout(s)
            response.headers["Cache-Control"] = NO_STORE
            segments = lay.grid if lay.grid is not None else list(lay.spans)
            return MediaMap(
                gen=s.gen, mime=lay.mime, offset=-1.0, init=f"init.mp4?g={s.gen}",
                segments=segments,
            )  # fmt: skip

        @app.get("/api/v1/stream/{sid}/master.m3u8")
        async def master(sid: str):
            s = self._stream_session(sid)
            return _playlist(self._master(s))

        @app.get("/api/v1/stream/{sid}/v.m3u8")
        async def variant(sid: str):
            s = self._stream_session(sid)
            return _playlist(self._variant(s))

        @app.get("/api/v1/stream/{sid}/init.mp4")
        async def init(sid: str, g: Annotated[int, Query(ge=1)] = 1):
            s = self._stream_session(sid)
            if g not in s.alts:
                raise errors.fail(404, "not_found", "Niet gevonden")
            data = self._layout(s, g).init
            return Response(data, media_type=SEGMENT_TYPE, headers={"Cache-Control": SEGMENT_CACHE})

        @app.get("/api/v1/stream/{sid}/{n}.m4s")
        async def segment(sid: str, n: Annotated[int, PathParam(ge=0, le=1_000_000)]):
            s = self._stream_session(sid)
            lay = self._layout(s)
            if n >= lay.count:
                raise errors.fail(404, "not_found", "Niet gevonden")
            gen = await self._segment(s, n)
            if s.native and gen != 1:
                # native HLS leest X-Init niet: de sessie dicht op deze plek (Keuze 22)
                self._close(s.sid)
                raise media_errors.session_gone()
            data = self._layout(s, gen).data[n]
            headers = {"X-Init": str(gen), "Cache-Control": SEGMENT_CACHE}
            return Response(data, media_type=SEGMENT_TYPE, headers=headers)

        @app.api_route("/api/v1/stream/{sid}/file", methods=["GET", "HEAD"])
        async def file(sid: str, if_none_match: Annotated[str | None, Header()] = None):
            s = self.guard(sid)
            if s.mode != "direct":
                raise errors.fail(404, "not_found", "Niet gevonden")
            path = F.make(DIRECT[s.variant])
            etag = f'"{_sha(path)}"'
            if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
                return Response(status_code=304, headers={"ETag": etag})
            mime = direct_mime(DIRECT[s.variant]).split(";", 1)[0]
            return FileResponse(
                path, media_type=mime, headers={"ETag": etag, "Cache-Control": FILE_CACHE}
            )

        @app.delete("/api/v1/stream/{sid}", status_code=204)
        async def close(sid: str):
            self.guard(sid)
            self._close(sid)
            return Response(status_code=204)

        return app

    def _stream_session(self, sid: str) -> FakeSession:
        s = self.guard(sid)
        if s.mode == "direct":
            raise errors.fail(404, "not_found", "Niet gevonden")
        return s

    def _play(self, user: str, item: Item, body: PlayIn) -> PlayOut:
        profile = body.profile
        now = self.clock()
        if body.replace:
            old = self.sessions.get(body.replace)
            if old is not None and old.user == user:
                self._close(body.replace)
        mine = sorted(
            (s for s in self.sessions.values() if s.user == user), key=lambda s: s.created
        )
        for s in mine[: max(0, len(mine) - PER_USER + 1)]:
            self._close(s.sid)  # de oudste gaat dicht
        kind: Literal["mse", "file"] = "file" if item.mode == "direct" else "mse"
        variant: Variant = "h264" if _supports(profile, "avc1.", kind) else "vp9"
        sid = f"{self.node_name}.{secrets.token_urlsafe(24)}"
        native = not profile.engines.mse and profile.engines.native_hls
        s = FakeSession(
            id=uuid.uuid4(), sid=sid, item=item, user=user, mode=item.mode, variant=variant,
            native=native, base=profile.base, created=now, seen=now,
        )  # fmt: skip
        self.sessions[sid] = s
        log.info("sessie %s: %s met %s", s.id, item.mode, variant)
        if item.mode == "direct":
            name = DIRECT[variant]
            probe = _probe_of(name)
            mime = direct_mime(name)
            urls = PlayUrls(file="file")
            offset = 0.0
            reasons: list = []
        else:
            probe = source_probe()
            lay = self._layout(s)
            mime = lay.mime
            urls = PlayUrls(map="map.json", master="master.m3u8")
            offset = -1.0
            reasons = ["container"] if item.mode == "remux" else ["video_codec"]
        duration = probe.duration_ms or 0
        start = min(body.start_ms, max(0, duration - 1))
        if item.mode != "direct":
            lay = self._layout(s)
            first = max(i for i, (b, _) in enumerate(lay.spans) if b <= start)
            self._start(s, first, restart=False)
        audio = probe.audio_streams()
        tracks = PlayTracks(
            audio=[
                AudioTrack(
                    index=a.index,
                    lang=a.lang,
                    title=a.title,
                    codec=a.codec,
                    channels=a.channels,
                    default=a.default or i == 0,
                )  # fmt: skip
                for i, a in enumerate(audio)
            ]
        )
        started = datetime.now(UTC)
        return PlayOut(
            sid=sid,
            mode=item.mode,
            reasons=reasons,
            urls=urls,
            mime=mime,
            duration_ms=duration,
            start_ms=start,
            offset=offset,
            tracks=tracks,
            chapters=probe.chapters,
            quality=QualityInfo(current=body.quality, options=["auto", "source"]),
            started_at=started,
            expires_at=started + timedelta(seconds=MAX_AGE_S),
        )

    # --- de playlists (§2 M4) ---

    def _master(self, s: FakeSession) -> str:
        lay = self._layout(s)
        probe = source_probe()
        video = probe.video()
        fps = video.fps if video is not None and video.fps else 24.0
        total = sum(len(d) for d in lay.data)
        seconds = sum(d for _, d in lay.spans) / 1000
        average = math.ceil(8 * total / seconds)
        target = self._target(lay)
        peak = _peak([(d / 1000, len(b)) for (_, d), b in zip(lay.spans, lay.data, strict=True)],
                     target)  # fmt: skip
        if lay.mode == "transcode":
            bandwidth = max(round((MAXRATE_K + AUDIO_K[s.variant]) * 1000 * 1.1), peak)
        else:
            bandwidth = math.ceil(peak * 1.1)  # de echte piek uit de index
        lines = ["#EXTM3U", "#EXT-X-VERSION:7"]
        if lay.mode == "transcode" and s.base != "webos-5":
            lines.append("#EXT-X-INDEPENDENT-SEGMENTS")
        lines.append(
            f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},AVERAGE-BANDWIDTH={min(average, bandwidth)},"
            f'CODECS="{lay.codecs}",RESOLUTION={lay.width}x{lay.height},'
            f"FRAME-RATE={fps:.3f},VIDEO-RANGE=SDR"
        )
        lines.append("v.m3u8")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _target(lay: Layout) -> int:
        longest = max(d for _, d in lay.spans) / 1000
        if lay.mode == "transcode":  # het eerste en het laatste mogen langer (review C2)
            first, last = lay.spans[0][1], lay.spans[-1][1]
            return max(2, math.floor(first / 1000 + 0.5), math.floor(last / 1000 + 0.5))
        return math.ceil(longest - 1e-9)

    def _variant(self, s: FakeSession) -> str:
        lay = self._layout(s)
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:7",
            f"#EXT-X-TARGETDURATION:{self._target(lay)}",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            "#EXT-X-MEDIA-SEQUENCE:0",
            f'#EXT-X-MAP:URI="init.mp4?g={s.gen}"',
        ]
        for k, (_, d) in enumerate(lay.spans):
            lines += [f"#EXTINF:{d / 1000:.3f},", f"{k}.m4s"]
        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"


def _playlist(text: str) -> PlainTextResponse:
    return PlainTextResponse(text, media_type=PLAYLIST_TYPE, headers={"Cache-Control": NO_STORE})


def _peak(segments: list[tuple[float, int]], target: int) -> int:
    """De piek van RFC 8216 §4.3.4.2: de hoogste bitrate over aaneengesloten segmenten met samen
    0,5–1,5 × TARGETDURATION."""
    best = 0.0
    for i in range(len(segments)):
        seconds, size = 0.0, 0
        for d, b in segments[i:]:
            seconds += d
            size += b
            if seconds > 1.5 * target + 1e-9:
                break
            if seconds >= 0.5 * target - 1e-9:
                best = max(best, 8 * size / seconds)
    return math.ceil(best)


# --- als proces (M6) -------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description="De nep-stroom van fase 5 (§3.6)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--speed", type=float, default=None)
    args = parser.parse_args(argv)
    fake = FakeStream(speed=args.speed)
    print(f"nep-stroom op http://{args.host}:{args.port}  (Bearer {TOKEN})", flush=True)
    for item in ITEMS.values():
        print(f"  {item.mode:9} {item.node_id}", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(fake.app, host=args.host, port=args.port, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
