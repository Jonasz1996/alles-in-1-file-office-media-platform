"""Het contract van de stroom (§3.4, §3.6): scenario's over `play` en `/api/v1/stream/…`. M0
draait ze tegen de nep (`fake_stream.py`, in test_fake_stream.py); M4 draait dezelfde tegen de
echte routes (`test_media_contract.py`, met de nep-worker eronder). Zo blijft de nep eerlijk, en
kan M6 de speler bouwen op wat het echte straks ook doet.

Dit bestand wordt niet zelf verzameld (het heet niet test_*.py):

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.__name__)
    async def test_contract(scenario, world):
        await scenario(world)

Een **World** geeft:
- `client`: een httpx-client op de app (zonder Authorization), en `auth`: de Bearer voor play;
- `case(mode)`: een node en een body voor play die tot die weg leiden (`transcode`, `remux`,
  `direct`, of `ts`: een MPEG-TS-bron met transcode; None als de wereld er geen heeft). Bij het
  echte beslist decide (Keuze 19), dus kiest de wereld het profiel en de bron;
- `direct_source()`: het bestand achter `case("direct")`, voor de bytes van `/file`;
- `wait_s`: hoe lang een segment hooguit op zich laat wachten (20 s; een test zet het lager);
- `pace(speed)`: het tempo van de producent in mediaseconden per seconde (None: zo snel als het
  kan; bij het echte: `fake_worker.speed`). Een scenario met een sprong zet het op 20, zodat de
  sprong echt een herstart is en niet al in de cache staat;
- de haken: `stop(sid, reason)` (de app sluit de sessie: de grafsteen), `replace(node_id)` (een
  nieuwe versie), `new_init_on_restart()` (de volgende herstart geeft een andere `stsd`; bij het
  echte: `fake_worker.fault("new_stsd")`), `stall(on)` (de producent maakt niets meer),
  `fail_transcode()` (de volgende herstart faalt, ook de nieuwe poging), `busy(reason)` (de
  toelating zegt nee) en `begin_stop()` (de app stopt, F3).

De tijden (spike punten 10–13 en 17, Keuze 22, 23): één referentie, mediatijd. Bij transcode
het rooster van 2 s aan `origin`: segment 0 van 0 tot `origin + 2 s`, segment k van `origin +
2k`, N uit het laatste videobeeld, een fragment voorbij N bij het laatste segment. Bij remux
segment 0 vanaf 0 en segment n ≥ 1 vanaf het eerste keyframe ≥ n·6 s. `EXTINF` en `map.json`
beschrijven dezelfde segmenten; de som van de duren tot n is het begin van n. `tfdt` is
mediatijd + 1 s (`offset` −1). Een nieuwe `stsd` is een nieuwe generatie met `X-Init`.
MPEG-TS zoekt niet precies (spike punt 11): na een sprong eist het contract daar alleen dat het
segment in zijn eigen vak van het rooster valt en met een keyframe begint.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import httpx

from app.media.types import MediaMap, PlayOut, SegmentGrid, codec_string_ok
from tests.media import boxes, hls_lint

PREFIX = "/api/v1/stream/"
SID = re.compile(r"^[A-Za-z0-9-]{1,63}\.[A-Za-z0-9_-]{32}$")
MIME = re.compile(r'^(?:video|audio)/[a-z0-9.+-]+; codecs="([^"]+)"$')
NO_STORE = "no-store"
SEGMENT_CACHE = "private, max-age=3600"
FILE_CACHE = "private, max-age=0, must-revalidate"
PLAYLIST_TYPE = "application/vnd.apple.mpegurl"
REMUX_MS = 6000
AHEAD = 3

CaseMode = Literal["transcode", "remux", "direct", "ts"]


@dataclass
class Case:
    node_id: uuid.UUID
    body: dict = field(default_factory=dict)  # PlayIn als JSON (profile, start_ms, …)
    base: str = "web"  # het basisprofiel, voor de lint
    has_audio: bool = True


class StreamWorld(Protocol):
    name: str
    client: httpx.AsyncClient
    auth: dict[str, str]
    wait_s: float

    def case(self, mode: CaseMode) -> Case | None: ...

    def direct_source(self) -> Path: ...

    async def pace(self, speed: float | None) -> None: ...

    async def stop(self, sid: str, reason: str) -> None: ...

    async def replace(self, node_id: uuid.UUID) -> None: ...

    async def new_init_on_restart(self) -> None: ...

    async def stall(self, on: bool) -> None: ...

    async def fail_transcode(self) -> None: ...

    async def busy(self, reason: str, retry_after: float) -> None: ...

    async def begin_stop(self) -> None: ...


Scenario = Callable[[StreamWorld], Awaitable[None]]

# Elke route onder /stream/ (Keuze 21: elke gaat langs de guard)
ROUTES: list[tuple[str, str]] = [
    ("GET", "map.json"),
    ("GET", "master.m3u8"),
    ("GET", "v.m3u8"),
    ("GET", "init.mp4?g=1"),
    ("GET", "0.m4s"),
    ("GET", "file"),
    ("HEAD", "file"),
    ("DELETE", ""),
]


# --- hulpjes ---------------------------------------------------------------------------------


def url(sid: str, rest: str = "") -> str:
    return f"{PREFIX}{sid}/{rest}" if rest else f"{PREFIX}{sid}"


def need(w: StreamWorld, mode: CaseMode) -> Case:
    case = w.case(mode)
    assert case is not None, f"de wereld {w.name} heeft geen {mode}"
    return case


async def play(w: StreamWorld, mode: CaseMode, **extra) -> PlayOut:
    case = need(w, mode)
    body = {**case.body, **extra}
    r = await w.client.post(f"/api/v1/media/{case.node_id}/play", json=body, headers=w.auth)
    assert r.status_code == 200, r.text
    out = PlayOut.model_validate(r.json())
    want = "transcode" if mode == "ts" else mode
    assert out.mode == want, (mode, out.mode, out.reasons)
    return out


async def call(w: StreamWorld, method: str, sid: str, rest: str = "", **kw) -> httpx.Response:
    return await w.client.request(method, url(sid, rest), **kw)


def failed(r: httpx.Response, status: int, code: str, reason: str | None = None) -> dict:
    """Een fout in de vorm van errors.fail; geeft de params."""
    assert r.status_code == status, (r.status_code, r.text)
    if r.request.method == "HEAD":
        return {}
    detail = r.json()["detail"]
    assert detail["code"] == code, detail
    assert isinstance(detail["message"], str) and detail["message"]
    params = detail.get("params") or {}
    if reason is not None:
        assert params.get("reason") == reason, detail
    return params


def stream_headers(r: httpx.Response, cache: str | None = None) -> None:
    """Keuze 35: CORP same-origin, nooit Content-Disposition, de juiste Cache-Control."""
    assert r.headers.get("cross-origin-resource-policy") == "same-origin", dict(r.headers)
    assert "content-disposition" not in r.headers
    if cache is not None:
        assert r.headers.get("cache-control") == cache, (r.url, r.headers.get("cache-control"))


async def media_map(w: StreamWorld, out: PlayOut) -> MediaMap:
    assert out.urls.map is not None
    r = await call(w, "GET", out.sid, out.urls.map)
    assert r.status_code == 200, r.text
    stream_headers(r, NO_STORE)
    return MediaMap.model_validate(r.json())


def spans(m: MediaMap, duration_ms: int) -> list[tuple[int, int]]:
    """(begin, duur) per segment in mediatijd, uit map.json."""
    if isinstance(m.segments, SegmentGrid):
        g = m.segments
        begins = [g.start_of(k) for k in range(g.count)]
        ends = [*begins[1:], duration_ms]
        return [(b, e - b) for b, e in zip(begins, ends, strict=True)]
    return [(b, d) for b, d in m.segments]


async def segment(w: StreamWorld, sid: str, n: int) -> httpx.Response:
    r = await call(w, "GET", sid, f"{n}.m4s", timeout=w.wait_s + 10)
    assert r.status_code == 200, (n, r.status_code, r.text)
    stream_headers(r, SEGMENT_CACHE)
    assert r.headers.get("content-type", "").startswith("video/mp4"), r.headers
    assert r.headers.get("x-init", "").isdigit(), r.headers
    return r


async def init_of(w: StreamWorld, sid: str, gen: int) -> bytes:
    r = await call(w, "GET", sid, f"init.mp4?g={gen}")
    assert r.status_code == 200, r.text
    stream_headers(r, SEGMENT_CACHE)
    return r.content


def fragments(init: bytes, data: bytes) -> list[boxes.Fragment]:
    parsed = boxes.parse(init + data)
    assert parsed.fragments, "een segment zonder fragment"
    assert all(f.sync for f in parsed.fragments), "een fragment zonder keyframe vooraan"
    return parsed.fragments


def frame_ms(master: str) -> int:
    rate = re.search(r"FRAME-RATE=([0-9.]+)", master)
    assert rate is not None, master
    return math.ceil(1000 / float(rate.group(1)))


async def playlists(w: StreamWorld, out: PlayOut) -> tuple[str, str]:
    assert out.urls.master is not None
    r = await call(w, "GET", out.sid, out.urls.master)
    assert r.status_code == 200, r.text
    stream_headers(r, NO_STORE)
    assert r.headers["content-type"].startswith(PLAYLIST_TYPE)
    master = r.text
    uri = [x for x in master.splitlines() if x and not x.startswith("#")]
    assert len(uri) == 1, master
    r = await call(w, "GET", out.sid, uri[0])
    assert r.status_code == 200, r.text
    stream_headers(r, NO_STORE)
    assert r.headers["content-type"].startswith(PLAYLIST_TYPE)
    return master, r.text


def on_grid(frags: list[boxes.Fragment], grid: SegmentGrid, k: int, frame: int) -> None:
    """Segment k begint op zijn grens (hooguit één beeld later), en elk fragment erin hoort bij
    k (een fragment voorbij N bij het laatste segment)."""
    first = frags[0].video_start_ms
    want = grid.origin_ms if k == 0 else grid.start_of(k)
    assert want - 1 <= first <= want + frame, (k, first, want)
    for f in frags:
        assert grid.segment_of(f.video_start_ms) == k, (k, f.video_start_ms)


def unknown_sid(out: PlayOut) -> str:
    node = out.sid.split(".", 1)[0]
    return f"{node}.{secrets.token_urlsafe(24)}"


# --- de scenario's ---------------------------------------------------------------------------


async def play_vorm(w: StreamWorld) -> None:
    for mode in ("transcode", "remux", "direct"):
        out = await play(w, mode)
        assert SID.match(out.sid), "de sid is geen <node>.<24 bytes base64url>"
        m = MIME.match(out.mime)
        assert m is not None, out.mime
        assert all(codec_string_ok(c) for c in m.group(1).split(",")), out.mime
        assert out.duration_ms > 0 and out.started_at < out.expires_at
        assert out.tracks.audio and out.tracks.audio[0].codec
        given = {k: v for k, v in out.urls.model_dump().items() if v is not None}
        for name, rel in given.items():  # relatief aan /api/v1/stream/{sid}/
            assert not rel.startswith("/") and "://" not in rel and out.sid not in rel, name
        if mode == "direct":
            assert set(given) == {"file"} and out.offset == 0
        else:
            assert set(given) == {"map", "master"} and out.offset == -1.0
        for rel in given.values():
            method = "HEAD" if rel == out.urls.file else "GET"
            assert (await call(w, method, out.sid, rel)).status_code == 200, rel
        r = await call(w, "DELETE", out.sid)
        assert r.status_code == 204


async def play_weigert(w: StreamWorld) -> None:
    case = need(w, "transcode")
    path = f"/api/v1/media/{case.node_id}/play"
    failed(await w.client.post(path, json=case.body), 401, "not_logged_in")
    other = f"/api/v1/media/{uuid.uuid4()}/play"
    failed(await w.client.post(other, json=case.body, headers=w.auth), 404, "not_found")
    failed(await w.client.post(path, json={}, headers=w.auth), 422, "invalid_input")
    await w.busy("cpu", 3)
    r = await w.client.post(path, json=case.body, headers=w.auth)
    params = failed(r, 503, "media_busy", "cpu")
    assert r.headers.get("retry-after") == "3" and params.get("retry_after") == 3


async def kaart_en_playlists(w: StreamWorld) -> None:
    """map.json en v.m3u8 beschrijven dezelfde segmenten; de lint met de echte groottes."""
    for mode in ("transcode", "remux"):
        case = need(w, mode)
        out = await play(w, mode)
        m = await media_map(w, out)
        assert m.gen == 1 and m.init == "init.mp4?g=1" and m.offset == -1.0
        assert m.mime == out.mime
        got = spans(m, out.duration_ms)
        assert got[0][0] == 0, "segment 0 begint op 0 (mediatijd)"
        for (b, d), (nb, _) in zip(got, got[1:], strict=False):
            assert d > 0 and b + d == nb, got  # aaneengesloten: de som is het begin
        assert got[-1][0] + got[-1][1] == out.duration_ms
        if mode == "transcode":
            assert isinstance(m.segments, SegmentGrid)
            g = m.segments
            assert g.grid_ms == 2000 and g.origin_ms >= 0
            assert g.count == SegmentGrid.count_for(g.origin_ms, g.last_frame_ms, g.grid_ms)
        else:
            assert isinstance(m.segments, list)
        master, variant = await playlists(w, out)
        sizes = {f"{n}.m4s": len((await segment(w, out.sid, n)).content) for n in range(len(got))}
        ctx = hls_lint.Context(mode=mode, base=case.base, has_audio=case.has_audio)
        hls_lint.assert_set_ok({"master.m3u8": master, "v.m3u8": variant}, ctx, sizes=sizes)
        parsed = hls_lint.parse(variant)
        assert parsed is not None and parsed.segments is not None
        assert parsed.map_uri == m.init
        assert [s.uri for s in parsed.segments] == list(sizes)
        assert [s.duration for s in parsed.segments] == [round(d / 1000, 3) for _, d in got]
        codecs = MIME.match(out.mime)
        assert codecs is not None and f'CODECS="{codecs.group(1)}"' in master
        await call(w, "DELETE", out.sid)


async def segmenten_op_rooster(w: StreamWorld) -> None:
    """Transcode: elk segment op het rooster, met een keyframe vooraan, N uit het laatste
    videobeeld, tfdt = mediatijd + 1 s, X-Init 1."""
    out = await play(w, "transcode")
    m = await media_map(w, out)
    assert isinstance(m.segments, SegmentGrid)
    grid = m.segments
    master, _ = await playlists(w, out)
    frame = frame_ms(master)
    init = await init_of(w, out.sid, 1)
    assert boxes.parse(init).mime() == out.mime
    last: list[boxes.Fragment] = []
    for k in range(grid.count):
        r = await segment(w, out.sid, k)
        assert r.headers["x-init"] == "1"
        last = fragments(init, r.content)
        on_grid(last, grid, k, frame)
        if k == 0:
            # boxes rekent mediatijd als tfdt − 1 s: het eerste beeld op origin bewijst +1 s
            assert abs(last[0].video_start_ms - grid.origin_ms) <= 1, last[0].video_start_ms
    assert last[-1].video_last_ms == grid.last_frame_ms
    failed(await call(w, "GET", out.sid, f"{grid.count}.m4s"), 404, "not_found")
    await call(w, "DELETE", out.sid)


async def zoeken_herstart(w: StreamWorld) -> None:
    """Een sprong verder dan 3 segmenten is een herstart op de grens: dezelfde tijden en, met
    dezelfde stsd, dezelfde generatie (Keuze 22, 25)."""
    await w.pace(20)
    out = await play(w, "transcode")
    m = await media_map(w, out)
    assert isinstance(m.segments, SegmentGrid)
    grid = m.segments
    master, _ = await playlists(w, out)
    frame = frame_ms(master)
    init = await init_of(w, out.sid, 1)
    await segment(w, out.sid, 0)
    k = grid.count - 2
    assert k > AHEAD, "de bron is te kort voor een sprong"
    r = await segment(w, out.sid, k)
    assert r.headers["x-init"] == "1"
    on_grid(fragments(init, r.content), grid, k, frame)
    back = await segment(w, out.sid, 1)  # terug: uit de cache of opnieuw, op dezelfde grens
    on_grid(fragments(init, back.content), grid, 1, frame)
    await call(w, "DELETE", out.sid)
    # Een start midden in een segment: play begint op de grens ervoor
    start = grid.start_of(5) + 300
    out = await play(w, "transcode", start_ms=start)
    assert out.start_ms == start
    r = await segment(w, out.sid, grid.segment_of(start))
    on_grid(fragments(init, r.content), grid, 5, frame)
    await call(w, "DELETE", out.sid)


async def nieuwe_init(w: StreamWorld) -> None:
    """Een herstart met een andere stsd: generatie 2, X-Init 2, de nieuwe init in map.json, en
    de oude init en wat in de cache staat blijven geldig (Keuze 22, spike punt 13)."""
    await w.pace(20)
    await w.new_init_on_restart()
    out = await play(w, "transcode")
    m = await media_map(w, out)
    assert isinstance(m.segments, SegmentGrid)
    grid = m.segments
    master, _ = await playlists(w, out)
    frame = frame_ms(master)
    first = await segment(w, out.sid, 0)
    assert first.headers["x-init"] == "1"
    k = grid.count - 2
    r = await segment(w, out.sid, k)
    assert r.headers["x-init"] == "2"
    init1, init2 = await init_of(w, out.sid, 1), await init_of(w, out.sid, 2)
    assert boxes.parse(init2).stsd() != boxes.parse(init1).stsd()
    on_grid(fragments(init2, r.content), grid, k, frame)
    m2 = await media_map(w, out)
    assert m2.gen == 2 and m2.init == "init.mp4?g=2"
    again = await segment(w, out.sid, 0)
    assert again.headers["x-init"] == "1"
    on_grid(fragments(init1, again.content), grid, 0, frame)
    failed(await call(w, "GET", out.sid, "init.mp4?g=9"), 404, "not_found")
    await call(w, "DELETE", out.sid)


async def remux_op_keyframes(w: StreamWorld) -> None:
    """Remux: segment 0 vanaf 0, segment n ≥ 1 vanaf het eerste keyframe ≥ n·6 s; elk fragment
    in zijn segment; een herstart op n begint precies op b_n (Keuze 23)."""
    out = await play(w, "remux")
    m = await media_map(w, out)
    assert isinstance(m.segments, list)
    got = spans(m, out.duration_ms)
    init = await init_of(w, out.sid, 1)
    keyframes: list[int] = []
    per: list[list[boxes.Fragment]] = []
    for n in range(len(got)):
        frags = fragments(init, (await segment(w, out.sid, n)).content)
        per.append(frags)
        keyframes += [f.video_start_ms for f in frags]
    for n, ((b, d), frags) in enumerate(zip(got, per, strict=True)):
        if n > 0:
            assert abs(frags[0].video_start_ms - b) <= 1, (n, frags[0].video_start_ms, b)
        for f in frags:
            assert b - 1 <= f.video_start_ms < b + d, (n, f.video_start_ms, b, d)
    want, step = [0], 1
    while later := [t for t in keyframes if t >= step * REMUX_MS]:
        if later[0] > want[-1]:
            want.append(later[0])
        step = max(step + 1, later[0] // REMUX_MS + 1)
    assert [b for b, _ in got] == want, (got, keyframes)
    await call(w, "DELETE", out.sid)
    # Eerst het voorlaatste segment (een herstart, of wachten): het begint op zijn keyframe
    out = await play(w, "remux")
    n = len(got) - 2
    assert n >= 1
    frags = fragments(init, (await segment(w, out.sid, n)).content)
    assert abs(frags[0].video_start_ms - got[n][0]) <= 1
    await call(w, "DELETE", out.sid)


async def zoeken_in_ts(w: StreamWorld) -> None:
    """MPEG-TS zoekt niet precies (spike punt 11): na een sprong valt segment n in zijn eigen
    vak van het rooster en begint het met een keyframe; meer eist het contract niet."""
    if w.case("ts") is None:
        return
    await w.pace(20)
    out = await play(w, "ts")
    m = await media_map(w, out)
    assert isinstance(m.segments, SegmentGrid)
    grid = m.segments
    init = await init_of(w, out.sid, 1)
    await segment(w, out.sid, 0)
    k = grid.count - 2
    r = await segment(w, out.sid, k)
    gen = int(r.headers["x-init"])
    frags = fragments(init if gen == 1 else await init_of(w, out.sid, gen), r.content)
    for f in frags:
        assert grid.start_of(k) - 1 <= f.video_start_ms < grid.start_of(k + 1), f.video_start_ms
    await call(w, "DELETE", out.sid)


async def direct_bereik(w: StreamWorld) -> None:
    """Direct play via de sessie: Range, If-Range, HEAD en de sha als ETag (Keuze 21)."""
    out = await play(w, "direct")
    assert out.urls.file is not None
    data = w.direct_source().read_bytes()
    etag = f'"{hashlib.sha256(data).hexdigest()}"'
    kind = out.mime.split(";", 1)[0]
    r = await call(w, "GET", out.sid, out.urls.file)
    assert r.status_code == 200 and r.content == data
    stream_headers(r, FILE_CACHE)
    assert r.headers["etag"] == etag and r.headers.get("accept-ranges") == "bytes"
    assert r.headers["content-type"].startswith(kind)
    r = await call(w, "GET", out.sid, out.urls.file, headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == data[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(data)}"
    stream_headers(r, FILE_CACHE)
    r = await call(w, "HEAD", out.sid, out.urls.file)
    assert r.status_code == 200 and r.content == b""
    assert int(r.headers["content-length"]) == len(data) and r.headers["etag"] == etag
    stream_headers(r, FILE_CACHE)
    same = {"Range": "bytes=0-9", "If-Range": etag}
    r = await call(w, "GET", out.sid, out.urls.file, headers=same)
    assert r.status_code == 206 and r.content == data[:10]
    changed = {"Range": "bytes=0-9", "If-Range": '"iets-anders"'}
    r = await call(w, "GET", out.sid, out.urls.file, headers=changed)
    assert r.status_code == 200 and r.content == data
    r = await call(w, "GET", out.sid, out.urls.file, headers={"If-None-Match": etag})
    assert r.status_code == 304
    await call(w, "DELETE", out.sid)


async def onbekende_sid(w: StreamWorld) -> None:
    """Op elke route: 410 media_session_gone (de speler vraagt stil een nieuwe), no-store."""
    out = await play(w, "transcode")
    sid = unknown_sid(out)
    for method, rest in ROUTES:
        r = await call(w, method, sid, rest)
        failed(r, 410, "media_session_gone")
        stream_headers(r, NO_STORE)
    await call(w, "DELETE", out.sid)


async def sluiten(w: StreamWorld) -> None:
    out = await play(w, "transcode")
    r = await call(w, "DELETE", out.sid)
    assert r.status_code == 204 and r.content == b""
    stream_headers(r)
    params = failed(await call(w, "GET", out.sid, "map.json"), 410, "media_session_gone")
    assert "reason" not in params


async def gestopt_grafsteen(w: StreamWorld) -> None:
    """De app sloot de sessie: 403 met de reden op elke route, niet 410 (anders vraagt de speler
    stil een nieuwe en doet Stop niets; Keuze 21)."""
    for mode in ("transcode", "direct"):
        out = await play(w, mode)
        await w.stop(out.sid, "stopped")
        for method, rest in ROUTES:
            r = await call(w, method, out.sid, rest)
            failed(r, 403, "media_access_gone", "stopped")
            stream_headers(r, NO_STORE)


async def vervangen(w: StreamWorld) -> None:
    """Een nieuwe versie van het bestand: 410 met reden replaced (Keuze 21)."""
    case = need(w, "transcode")
    out = await play(w, "transcode")
    await segment(w, out.sid, 0)
    await w.replace(case.node_id)
    for rest in ("map.json", "1.m4s"):
        failed(await call(w, "GET", out.sid, rest), 410, "media_session_gone", "replaced")


async def wissel_met_replace(w: StreamWorld) -> None:
    """replace in de body sluit de oude sessie in dezelfde beurt (een ander spoor)."""
    old = await play(w, "transcode")
    new = await play(w, "transcode", replace=old.sid)
    assert new.sid != old.sid
    failed(await call(w, "GET", old.sid, "map.json"), 410, "media_session_gone")
    await segment(w, new.sid, 0)
    await call(w, "DELETE", new.sid)


async def geen_segment_op_tijd(w: StreamWorld) -> None:
    """Hooguit wait_s wachten: 503 media_timeout met Retry-After: 2; na 3 keer dicht."""
    await w.stall(True)
    try:
        out = await play(w, "transcode")
        for _ in range(3):
            started = time.monotonic()
            r = await call(w, "GET", out.sid, "0.m4s", timeout=w.wait_s + 10)
            failed(r, 503, "media_timeout")
            assert r.headers.get("retry-after") == "2"
            stream_headers(r, NO_STORE)
            assert time.monotonic() - started <= w.wait_s + 2
        failed(await call(w, "GET", out.sid, "0.m4s"), 410, "media_session_gone")
    finally:
        await w.stall(False)


async def transcode_faalt(w: StreamWorld) -> None:
    """Faalt de herstart (ook de nieuwe poging): 502 media_transcode_failed."""
    await w.pace(20)
    out = await play(w, "transcode")
    m = await media_map(w, out)
    assert isinstance(m.segments, SegmentGrid)
    await segment(w, out.sid, 0)
    await w.fail_transcode()
    r = await call(w, "GET", out.sid, f"{m.segments.count - 2}.m4s", timeout=w.wait_s + 10)
    failed(r, 502, "media_transcode_failed")
    stream_headers(r, NO_STORE)
    await call(w, "DELETE", out.sid)


async def app_stopt(w: StreamWorld) -> None:
    """F3: zodra de app stopt, onder /stream/ 503 media_busy (worker) met Retry-After: 2."""
    out = await play(w, "transcode")
    await w.begin_stop()
    for method, rest in (("GET", "map.json"), ("GET", "0.m4s"), ("DELETE", "")):
        r = await call(w, method, out.sid, rest)
        failed(r, 503, "media_busy", "worker")
        assert r.headers.get("retry-after") == "2"
        stream_headers(r, NO_STORE)


SCENARIOS: list[Scenario] = [
    play_vorm,
    play_weigert,
    kaart_en_playlists,
    segmenten_op_rooster,
    zoeken_herstart,
    nieuwe_init,
    remux_op_keyframes,
    zoeken_in_ts,
    direct_bereik,
    onbekende_sid,
    sluiten,
    gestopt_grafsteen,
    vervangen,
    wissel_met_replace,
    geen_segment_op_tijd,
    transcode_faalt,
    app_stopt,
]
