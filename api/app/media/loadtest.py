"""De belastingtest in de app (§4.6). Gebouwd door M10.

Te starten met de knop op /beheer (POST /api/v1/admin/media/loadtest), of met
`python -m app.cli media-loadtest [--sessies 3] [--duur 300] [--codec h264] [--json uit.json]
[--extern adres] [--film id …]`. Hij draait in de app, dus met de echte beslissing, toelating,
sessies, controle, producenten, knipper en cache.

1. **Bronnen** (make_sources): de worker maakt ze met synth, na elkaar, in media/loadtest/:
   testsrc2 (seed 0) en twee keer cellauto (seed 1 en 2), 300 s 1080p H.264 6 Mbit/s met AAC.
   De app opent de uitvoer-fd (eerst <naam>.part, na een geslaagde synth hernoemd), dus de worker
   heeft geen schrijfbare map nodig. Een bron die er al is, wordt hergebruikt. Een korte run
   (minder dan SHORT_RUN s, de rooktest van CI) maakt bronnen van SHORT_SOURCE_S s op 720p en
   vraagt de laagste trede (480): goedkoop genoeg voor een gedeelde runner.
   **Met sources** (§4.6 punt 1; review van de integratie, K1) speelt hij echte films van de
   beheerder in plaats daarvan, en maakt hij niets met synth: node_sources zoekt ze op in de
   route (een bestand dat hij mag lezen, anders 404 zoals play; hooguit één per sessie, anders
   422), en speler i krijgt film i modulo het aantal.
2. **Sessies** (play_source): van de beheerder die de test start. Bij de eigen bronnen is de
   bron een LoadtestFile, alleen met PLATFORM_MEDIA_LOADTEST=1 (de controle per verzoek kijkt
   dat ook na, M3); bij een film de node, met de gewone controle van play (toegang, prullenbak,
   nieuwe versie) en de probe en keyframe-index zoals play. Elke virtuele speler telt in de
   toelating als een eigen kijker (een eigen id), anders liet max_user_transcodes (2) de derde
   nooit toe; de sessie zelf blijft van de beheerder. Alle sessies van een run hebben dezelfde
   family; zo gaan ze na afloop dicht, ook die op een film.
3. **Profiel** (profile_for): H.264 via MSE (of VP9 met codec vp9) en max.bitrate_kbps 4000,
   nooit als bestand (direct heeft geen segmenten om te meten). De eigen bron heeft 6 Mbit/s,
   dus de beslissing kiest zelf transcode (reden bitrate), en de toelating kiest de trede. Een
   film kan ook remux krijgen (H.264 onder 4 Mbit/s); het rapport zegt per speler de modus.
4. **Spelers** (Player): per sessie een virtuele speler in Python die via http met de app praat
   (127.0.0.1, of in een test via de ASGI-transport). Hij begint zodra segment 0 er is, vraagt
   daarna de segmenten op volgorde met hooguit 30 s vooruit, en speelt in echte tijd. De
   speeltijd loopt alleen als het segment dat nodig is helemaal binnen is; anders is het
   haperen (aantal en duur). Een 410, 502 of 503, of geen verbinding (de app herstart), vraagt
   een nieuwe sessie op de plek waar hij was (fase c); 503 media_busy bij het openen probeert
   hij na Retry-After opnieuw, zoals de speler in de browser. 409 media_probe_pending (een film
   van sources zonder opgeslagen probe) vraagt elke speler na Retry-After opnieuw, ook die in
   de app (na-controle van de review); de wachttijd telt mee in de start.
5. **Fasen**: a (gespreid op 0, 10 en 20 s; elke sessie springt op 60 s 90 s vooruit, voorbij
   wat de producent al maakte), b (allemaal tegelijk). c (de app herstart) loopt alleen met de
   spelers buiten de app (`--extern`): elke speler meldt dan zijn herstarts. d (DS herstart) kan
   de app niet zelf: het rapport zegt dat, en het runbook hoe Jonas het met de hand doet.
6. **Meet**: de start per sessie, haperen (aantal, duur, en wanneer: sinds de start en sinds de
   sprong), de snelheid van elke run (uit de producent), de trede, de ffmpeg-processen (elke
   0,5 s), bij de lokale worker de CPU en de RSS van zijn kinderen (/proc; in een container ziet
   de app die niet), de cache in MB per minuut, en de p95 van GET /api/v1/files/root (de eerste
   pagina, zoals de web-app een map opent) en /api/v1/media/home elke seconde.
7. **Slaagt als** (GATES): a: elke start ≤ 5 s, 0 keer haperen, elke run gemiddeld ≥ 1,1×, de
   API ≤ 200 ms (p95); b: elke start ≤ 5 s en 0 keer haperen; c: weer spelen ≤ 10 s na "ready",
   en daarna niet haperen.
8. **Veilig**: hij weigert (409 media_bench_busy) als er echte transcodes, een meting of al een
   belastingtest lopen; zolang hij loopt wacht de analyse (de haak analysis.busy_live zegt dan
   minstens 2) en zitten de plaatsen van de worker vol, zodat een preview van een video wacht
   (Defer); hij kijkt elke 0,5 s naar de ffmpeg-processen en stopt bij een vierde (MAX_PROCS);
   na afloop gaan zijn sessies en hun cache weg.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import statistics
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from app import errors
from app.config import MediaConfig, Settings
from app.media import errors as media_errors
from app.media.types import LoadtestFile, LoadtestIn, LoadtestView, Probe
from app.models import Node

log = logging.getLogger("platform.media")

LOADTEST_DIR = "loadtest"
SOURCES: tuple[tuple[Literal["testsrc2", "cellauto"], int], ...] = (
    ("testsrc2", 0),
    ("cellauto", 1),
    ("cellauto", 2),
)
SOURCE_S = 300
SOURCE_HEIGHT: Literal[1080, 720] = 1080
SHORT_RUN = 120  # korter dan dit: de rooktest (geen sprong, kleine bron, laagste trede)
SHORT_SOURCE_S = 60
SHORT_HEIGHT: Literal[1080, 720] = 720
SHORT_MAX_HEIGHT = 480
MAX_BITRATE_KBPS = 4000
AHEAD_MS = 30_000  # zoveel vraagt de speler vooruit
MAX_PROCS = 3  # regel 13: bij een vierde ffmpeg stopt de test
POLL_S = 0.5
SAMPLE_S = 1.0
SYNTH_LIMIT_S = 900.0
OPEN_RETRY_S = 1.0
GATES = {"start_s": 5.0, "speed": 1.1, "api_p95_ms": 200.0, "restart_s": 10.0}
# De API zoals de web-app hem vraagt: van de hoofdmap de eerste pagina (FIRST_PAGE in
# web/src/browser/api.ts), nooit de hele lijst. Zonder limit kostte de lijst van een hoofdmap met
# 5.000 bestanden de app zelf elke seconde ±220-320 ms, en die tijd ging af van de spelers en van
# elke vraag ernaast (I1, M7: zoeken p95 van 23 naar 80-129 ms zonder media); dan mat de test de
# lengte van de map in plaats van de API naast media. De grens van 200 ms is dezelfde.
FIRST_PAGE = 200
API_PATHS = (f"/api/v1/files/root?limit={FIRST_PAGE}", "/api/v1/media/home")


@dataclass
class Timing:
    """De tijden van de fasen; tests maken ze kleiner (scale), de echte test niet."""

    stagger_s: float = 10.0
    seek_at_s: float = 60.0
    seek_jump_s: float = 90.0
    tick_s: float = 0.1
    scale: float = 1.0

    def s(self, seconds: float) -> float:
        return seconds * self.scale


TIMING = Timing()


# --- de bronnen ------------------------------------------------------------------------------


def loadtest_dir(settings: Settings) -> Path:
    return settings.media_dir / LOADTEST_DIR


def short_run(seconds: int) -> bool:
    return seconds < SHORT_RUN


def source_name(kind: str, seed: int, seconds: int, height: int) -> str:
    return f"{kind}-{seed}-{seconds}s-{height}p.mkv"


def specs(count: int, seconds: int) -> list[tuple[str, int, int, int]]:
    """(soort, seed, duur, hoogte) van de bronnen die deze run nodig heeft."""
    if short_run(seconds):
        duration, height = SHORT_SOURCE_S, SHORT_HEIGHT
    else:
        duration, height = SOURCE_S, SOURCE_HEIGHT
    return [(kind, seed, duration, height) for kind, seed in SOURCES[: max(1, min(count, 3))]]


def as_file(path: Path) -> LoadtestFile:
    """Een bron als LoadtestFile. De sha is een eigen sleutel (naam, grootte, tijd): een nieuwe
    bron met dezelfde naam krijgt zo nooit de cache van de oude."""
    st = path.stat()
    key = f"loadtest:{path.name}:{st.st_size}:{st.st_mtime_ns}".encode()
    return LoadtestFile(
        name=path.name, path=path, size=st.st_size, sha=hashlib.sha256(key).hexdigest()
    )


async def make_source(settings: Settings, kind: str, seed: int, seconds: int, height: int) -> Path:
    """Eén bron met synth, naar een fd die de app opent; hergebruikt als hij er al is."""
    from app.media import client
    from app.media.wire import Op
    from app.media.worker.args import SynthArgs

    base = loadtest_dir(settings)
    final = base / source_name(kind, seed, seconds, height)
    if final.is_file() and final.stat().st_size > 0:
        return final
    base.mkdir(parents=True, exist_ok=True)
    part = final.with_name(final.name + ".part")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(part, flags, 0o600)
    try:
        args = SynthArgs(source=kind, seed=seed, duration_s=seconds, height=height)  # type: ignore[arg-type]
        async with asyncio.timeout(SYNTH_LIMIT_S):
            job, _ = await client.call(Op.SYNTH, args, inputs=0, fds=[fd])
            os.close(fd)
            fd = -1
            async with job:
                await job.result()
    except BaseException:
        if fd >= 0:
            os.close(fd)
        part.unlink(missing_ok=True)
        raise
    if part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"synth gaf een leeg bestand ({final.name})")
    os.replace(part, final)
    log.info("belastingtest: bron %s gemaakt (%d MB)", final.name, final.stat().st_size >> 20)
    return final


async def make_sources(settings: Settings, count: int, seconds: int) -> list[LoadtestFile]:
    """De bronnen van deze run, na elkaar (één synth tegelijk)."""
    out = []
    for kind, seed, duration, height in specs(count, seconds):
        path = await make_source(settings, kind, seed, duration, height)
        out.append(as_file(path))
    return out


# Een bron van de test: een eigen bestand van synth, of een film van de beheerder (sources)
Source = LoadtestFile | Node


async def node_sources(session, settings: Settings, user, body: LoadtestIn) -> list[Node] | None:
    """sources van POST /loadtest (§4.6 punt 1): de films van de beheerder, in de volgorde van
    de vraag (dubbele één keer). Elk een bestand dat hij mag lezen en niet in zijn prullenbak
    (anders 404, zoals play: media_play.readable_file); hooguit één per sessie (anders 422
    invalid_input). Zonder sources None: dan maakt de test zijn eigen bronnen."""
    from app.routers import media_play

    if not body.sources:
        return None
    if not settings.media_loadtest:
        raise loadtest_off()
    ids = list(dict.fromkeys(body.sources))
    if len(ids) > body.streams:
        raise errors.fail(422, "invalid_input", "Hooguit één film per sessie", streams=body.streams)
    return [await media_play.readable_file(session, user, node_id) for node_id in ids]


async def node_probe(settings: Settings, node: Node) -> Probe:
    """De probe van een film, zoals play: de opgeslagen, anders nu via de worker (409
    media_probe_pending als dat niet binnen het budget lukt; de speler probeert het dan
    opnieuw), en de keyframe-index in hetzelfde budget."""
    from app.media import analysis
    from app.routers import media_play

    sha = node.blob_id
    assert sha is not None
    deadline = asyncio.get_running_loop().time() + media_play.PROBE_BUDGET
    stored = await analysis.load(media_play.maker(settings), sha)
    probe = stored if analysis.settled(stored) else None
    if probe is None:
        probe = await media_play.probe_now(settings, sha, deadline)
    media_play.check_probe(probe)
    return await media_play.index_now(settings, probe, sha, deadline)


_probes: dict[str, Probe] = {}


async def probe_source(source: LoadtestFile) -> Probe:
    """De probe van een bron (via de worker; per sha onthouden)."""
    from app.media import client

    found = _probes.get(source.sha)
    if found is None:
        found = await client.probe(source.path)
        _probes[source.sha] = found
    return found


# --- het profiel en een sessie -----------------------------------------------------------------


def profile_for(codec: Literal["h264", "vp9"], strings: list[str], short: bool) -> dict[str, Any]:
    """Een speler met H.264 (of VP9) en AAC/Opus via MSE, en max.bitrate_kbps 4000."""
    from app.media import profile as profiles

    if codec == "h264":
        video = ("avc1.",)
        refuse = ("avc1.6e",)  # Hi10P
    else:
        video = ("vp09.00.",)
        refuse = ()

    def plays(s: str) -> bool:
        low = s.lower()
        return low.startswith((*video, "mp4a.40.", "opus")) and not low.startswith(refuse)

    # nooit "file": direct spelen heeft geen segmenten, en de speler van de test meet die
    codecs = {
        s: {"mse": True, "file": False, "smooth": True, "power": True}
        if plays(s)
        else {"mse": False, "file": False}
        for s in dict.fromkeys([*profiles.candidates(), *strings])
    }
    return {
        "v": 1,
        "base": "web",
        "client": {"kind": "web", "name": "belastingtest"},
        "max": {
            "height": SHORT_MAX_HEIGHT if short else 1080,
            "bitrate_kbps": MAX_BITRATE_KBPS,
        },
        "codecs": codecs,
    }


class LoadtestPlayIn(BaseModel):
    """POST /api/v1/admin/media/loadtest/play: een sessie op een bron van de test (voor de
    spelers buiten de app, fase c)."""

    source: int = Field(default=0, ge=0, le=2)
    seconds: int = Field(default=SOURCE_S, ge=30, le=600)
    codec: Literal["h264", "vp9"] = "h264"
    start_ms: int = Field(default=0, ge=0)
    replace: str | None = Field(default=None, max_length=200)
    player: uuid.UUID | None = None  # de kijker in de toelating (een eigen id per speler)


class LoadtestPlay(BaseModel):
    """Wat een speler van de test nodig heeft."""

    sid: str
    mode: str
    reasons: list[str]
    height: int | None = None
    duration_ms: int
    start_ms: int


async def play_source(
    app,
    settings: Settings,
    user,
    family: uuid.UUID | None,
    source: Source,
    *,
    codec: Literal["h264", "vp9"] = "h264",
    short: bool = False,
    start_ms: int = 0,
    replace: str | None = None,
    player: uuid.UUID | None = None,
) -> LoadtestPlay:
    """Een sessie op een bron, zoals play (M3): de probe (van een eigen bron direct via de
    worker, van een film zoals play), de beslissing, de toelating (met de speler als kijker),
    sessions.create en de stroom."""
    from app.media import admission, decide, sessions, streams
    from app.media import profile as profiles
    from app.media import public as media_public
    from app.media.state import MediaPolicy, media_policy, media_state
    from app.media.types import Choice, Rung
    from app.routers import media_play

    if not settings.media_loadtest:
        raise loadtest_off()
    from app.media import client

    state = media_state(app)
    try:
        if isinstance(source, Node):
            probe = await node_probe(settings, source)
        else:
            probe = await probe_source(source)
    except (client.WorkerBusy, client.WorkerDown, client.WorkerFailed) as exc:
        # drie spelers tegelijk vragen drie probes, en de worker heeft 2 plaatsen "kort": dan
        # 503 media_busy {reason: worker} met Retry-After, zoals play (M3), geen 500
        raise media_errors.from_worker(exc) from None
    media_play.check_probe(probe)
    strings = [s.codec_string for s in probe.streams if s.codec_string]
    profile = profiles.merge(profiles.load(profile_for(codec, strings, short)))
    config = MediaPolicy.effective(media_policy(app).config or MediaConfig(), settings)
    policy = media_public.for_source(media_public.policy(config, public=False), config, probe)
    viewer = player or uuid.uuid4()
    choice = Choice(start_ms=start_ms)
    replaced = sessions.replaced_grant(replace, user.id, None)
    with admission.replacing(replaced):
        budget = admission.view(viewer, None)
        plan = media_play.decide_or_worker(state, probe, profile, choice, policy, budget)
        media_play.need_worker(state, plan)
        try:
            grant = admission.reserve(plan, viewer, None)
        except media_errors.MediaBusy as exc:
            raise media_errors.from_busy(exc) from None
    try:
        s = await sessions.create(
            user, source, probe, plan, grant, family=family, replace=replace, settings=settings
        )
    except BaseException:
        admission.release(grant)
        raise
    audio = decide.default_audio(probe)
    duration = probe.duration_ms or 0
    s.mime = media_play.file_mime(decide.mime(probe, plan, audio))
    s.start_ms = min(start_ms, max(0, duration - 1))
    s.app = app
    s.profile, s.audio_index = profile, audio.index if audio is not None else None
    if plan.mode in ("remux", "transcode") and s.closed is None:
        try:
            s.info = await streams.open(s, plan)
        except BaseException:
            sessions.close(s)
            raise
        duration = s.info.duration_ms or duration
    height = plan.video.height if isinstance(plan.video, Rung) else None
    return LoadtestPlay(
        sid=s.sid,
        mode=plan.mode,
        reasons=list(plan.reasons),
        height=height,
        duration_ms=duration,
        start_ms=s.start_ms,
    )


def loadtest_off() -> Exception:
    """503 media_off met reason loadtest: PLATFORM_MEDIA_LOADTEST staat uit."""
    return errors.fail(
        503,
        "media_off",
        "De belastingtest staat uit (PLATFORM_MEDIA_LOADTEST=1 zet hem aan)",
        reason="loadtest",
    )


# --- de virtuele speler --------------------------------------------------------------------------


class SessionGone(Exception):
    """De sessie is weg (410), of de app stopt (503): een nieuwe sessie op de plek."""


@dataclass
class PlayerReport:
    name: str
    phase: str
    source: str
    start_s: float | None = None
    stalls: int = 0
    stall_s: float = 0.0
    # Per keer haperen één waarde, alleen om te lezen (I1, M7; judge en GATES kijken er niet
    # naar): stall_at in s sinds de speler begon te spelen (played_from), stall_after_seek in s
    # sinds de sprong klaar was (waar seek_s gezet wordt), None vóór de sprong en zonder sprong.
    # Net als stall_s: echte seconden gedeeld door scale (start_s en seek_s niet: die blijven
    # echte seconden, met scale ≠ 1 een andere maat). Een nieuwe sessie (gone, _reopen) zet
    # geen van beide klokken terug. Een sprong die in SessionGone eindigt, sprong niet (_reopen
    # opent op de oude plek): daarna blijft het None
    stall_at: list[float] = field(default_factory=list)
    stall_after_seek: list[float | None] = field(default_factory=list)
    seek_s: float | None = None
    # Ook alleen om te lezen (I1, M7e): de voorraad direct na de sprong, in mediaseconden: het
    # eind van het segment waar het doel in valt, min het doel. Een herstart levert eerst dat
    # segment en pas dan het volgende; ligt het doel vlak voor het eind van een segment (het
    # rooster begint op origin_ms, meestal 21 ms), dan hapert de speler meteen, hoe snel de
    # producent ook is. None zonder sprong
    seek_buffer_s: float | None = None
    played_s: float = 0.0
    mode: str | None = None
    reasons: list[str] = field(default_factory=list)
    height: int | None = None
    speeds: list[float] = field(default_factory=list)
    restarts: list[dict[str, float]] = field(default_factory=list)
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        out = asdict(self)
        out["speed"] = round(statistics.fmean(self.speeds), 2) if self.speeds else None
        del out["speeds"]
        out["stall_s"] = round(self.stall_s, 2)
        out["stall_at"] = [round(s, 2) for s in self.stall_at]
        after = self.stall_after_seek
        out["stall_after_seek"] = [None if s is None else round(s, 2) for s in after]
        out["played_s"] = round(self.played_s, 1)
        return out


Opener = Callable[[int, str | None], Awaitable[LoadtestPlay]]


def _segments(found: dict[str, Any]) -> list[tuple[int, int]]:
    """(begin, eind) in ms per segment, uit map.json (het rooster of de lijst)."""
    segs = found["segments"]
    if isinstance(segs, list):
        return [(int(b), int(b) + int(d)) for b, d in segs]
    grid, origin, count = int(segs["grid_ms"]), int(segs["origin_ms"]), int(segs["count"])
    last = int(segs["last_frame_ms"])

    def start(k: int) -> int:
        return 0 if k <= 0 else origin + k * grid

    out = [(start(k), start(k + 1)) for k in range(count)]
    if out:
        out[-1] = (out[-1][0], max(out[-1][0] + 1, last + 1))
    return out


class Player:
    """Eén virtuele speler (§4.6 punt 4)."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        opener: Opener,
        report: PlayerReport,
        *,
        timing: Timing = TIMING,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.http = http
        self.opener = opener
        self.report = report
        self.timing = timing
        self.clock = clock
        self.sid = ""
        self.segs: list[tuple[int, int]] = []
        self.pos = 0.0  # mediatijd in ms
        self.buffered = 0  # tot hier is alles binnen
        self.next = 0
        self.duration = 0
        self.fetcher: asyncio.Task | None = None
        self.gone = asyncio.Event()
        self.moved = asyncio.Event()

    async def _get(self, path: str) -> httpx.Response:
        try:
            r = await self.http.get(f"/api/v1/stream/{self.sid}/{path}", timeout=60)
        except (httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            # de app is weg (een herstart): geen verbinding, of hij viel weg midden in een
            # antwoord. Net als 410: een nieuwe sessie, en niet stil blijven staan
            raise SessionGone(f"{path}: {exc.__class__.__name__}") from None
        if r.status_code in (403, 410, 502, 503):  # 502: de proxy voor een app die herstart
            raise SessionGone(f"{path}: {r.status_code}")
        if r.status_code != 200:
            raise RuntimeError(f"{path}: {r.status_code}")
        return r

    async def _open(self, start_ms: int, replace: str | None = None) -> None:
        """Een sessie, map.json, de init en het segment op start_ms (de start van de speler)."""
        out = await self.opener(start_ms, replace)
        self.sid = out.sid
        self.report.mode, self.report.reasons = out.mode, out.reasons
        self.report.height = out.height
        found = (await self._get("map.json")).json()
        self.segs = _segments(found)
        self.duration = self.segs[-1][1] if self.segs else out.duration_ms
        await self._get(found["init"])
        await self._at(start_ms)

    async def _at(self, start_ms: int) -> None:
        n = next((k for k, (_, end) in enumerate(self.segs) if end > start_ms), 0)
        await self._get(f"{n}.m4s")
        self.pos = float(max(start_ms, self.segs[n][0]))
        self.buffered = self.segs[n][1]
        self.next = n + 1

    async def _fetch(self) -> None:
        try:
            while self.next < len(self.segs):
                begin = self.segs[self.next][0]
                while begin - self.pos > AHEAD_MS:
                    self.moved.clear()  # de speeltijd zet hem bij elke tik
                    await self.moved.wait()
                n = self.next
                await self._get(f"{n}.m4s")
                if n == self.next:  # geen sprong ondertussen
                    self.buffered = self.segs[n][1]
                    self.next = n + 1
        except SessionGone:
            self.gone.set()

    def _start_fetch(self) -> None:
        self.gone.clear()
        self.fetcher = asyncio.ensure_future(self._fetch())

    async def _stop_fetch(self) -> None:
        if self.fetcher is not None:
            self.fetcher.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.fetcher
            self.fetcher = None

    async def _reopen(self) -> None:
        """Fase c: de sessie is weg. Een nieuwe vragen op de plek, tot de app er weer is."""
        lost = self.clock()
        ready: float | None = None
        while True:
            try:
                out = await self.opener(int(self.pos), None)
            except (httpx.HTTPError, SessionGone, OSError):
                await asyncio.sleep(OPEN_RETRY_S)
                continue
            if ready is None:
                ready = self.clock()
            self.sid = out.sid
            try:
                found = (await self._get("map.json")).json()
                self.segs = _segments(found)
                await self._get(found["init"])
                await self._at(int(self.pos))
                break
            except SessionGone:
                await asyncio.sleep(OPEN_RETRY_S)
        back = self.clock()
        self.report.restarts.append(
            {"weg_s": round(back - lost, 2), "na_ready_s": round(back - (ready or back), 2)}
        )

    async def run(self, seconds: float, *, seek_at: float | None = None, jump_ms: int = 0) -> None:
        began = self.clock()
        try:
            await self._open(0)
        except Exception as exc:
            self.report.error = f"start: {exc}"[:200]
            return
        self.report.start_s = round(self.clock() - began, 3)
        self._start_fetch()
        played_from = last = self.clock()
        stalled_at: float | None = None
        seek_done: float | None = None
        seeked = False
        try:
            while True:
                await asyncio.sleep(self.timing.tick_s)
                now = self.clock()
                dt, last = (now - last) * 1000 / self.timing.scale, now
                if self.gone.is_set():
                    await self._stop_fetch()
                    await self._reopen()
                    self._start_fetch()
                    stalled_at, last = None, self.clock()
                    continue
                if self.pos < self.buffered:
                    if stalled_at is not None:
                        self.report.stall_s += (now - stalled_at) / self.timing.scale
                        stalled_at = None
                    # hooguit tot wat binnen is; een late tik telt niet meer dan dat
                    step = min(dt, self.buffered - self.pos)
                    self.pos += step
                    self.report.played_s += step / 1000
                    self.moved.set()
                elif self.pos < self.duration and stalled_at is None:
                    stalled_at = now
                    self.report.stalls += 1
                    self.report.stall_at.append((now - played_from) / self.timing.scale)
                    after = None if seek_done is None else (now - seek_done) / self.timing.scale
                    self.report.stall_after_seek.append(after)
                if self.pos >= self.duration or now - played_from >= self.timing.s(seconds):
                    break
                if seek_at is not None and not seeked and now - played_from >= seek_at:
                    seeked = True
                    target = int(self.pos) + jump_ms
                    if target < self.duration - 2000:
                        await self._stop_fetch()
                        t0 = self.clock()
                        try:
                            await self._at(target)
                        except SessionGone:
                            self.gone.set()
                            continue
                        seek_done = self.clock()
                        self.report.seek_s = round(seek_done - t0, 3)
                        self.report.seek_buffer_s = round((self.buffered - self.pos) / 1000, 3)
                        self._start_fetch()
                        stalled_at, last = None, self.clock()
        except Exception as exc:
            self.report.error = str(exc)[:200]
        finally:
            await self._stop_fetch()

    async def close(self) -> None:
        if self.sid:
            with contextlib.suppress(Exception):
                await self.http.delete(f"/api/v1/stream/{self.sid}")


# --- de loop van de test ------------------------------------------------------------------------


@dataclass
class Run:
    id: uuid.UUID
    streams: int
    seconds: int
    codec: Literal["h264", "vp9"]
    started_at: datetime
    state: Literal["idle", "making_sources", "running", "done", "failed", "stopped"] = "idle"
    phase: Literal["a", "b", "c", "d"] | None = None
    report: dict[str, Any] | None = None
    stop_reason: str | None = None
    task: asyncio.Task | None = None
    max_procs_seen: int = 0
    nodes: list[Node] = field(default_factory=list)  # sources: de films van de beheerder

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def view(self) -> LoadtestView:
        return LoadtestView(
            state=self.state, phase=self.phase, started_at=self.started_at, report=self.report
        )


_current: Run | None = None


def running() -> bool:
    """Loopt er nu een belastingtest (voor de meting, admin.start_bench)?"""
    return _current is not None and _current.running


def view() -> LoadtestView:
    return _current.view() if _current is not None else LoadtestView()


def reset() -> None:
    """Voor tests."""
    global _current
    if _current is not None and _current.task is not None and not _current.task.done():
        _current.task.cancel()
    _current = None
    _probes.clear()


def worker_pid(app) -> int | None:
    local = getattr(app.state, "media_local", None)
    return getattr(local, "pid", None) if local is not None else None


def _children(pid: int) -> list[tuple[int, str, int, int]]:
    """(pid, naam, cpu-ticks, rss in kB) van de kinderen van de lokale worker (/proc)."""
    out = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for entry in names:
        if not entry.isdigit():
            continue
        try:
            stat = Path(f"/proc/{entry}/stat").read_text()
        except OSError:
            continue
        name = stat[stat.find("(") + 1 : stat.rfind(")")]
        rest = stat[stat.rfind(")") + 2 :].split()
        try:
            ppid, utime, stime, rss_pages = (
                int(rest[1]),
                int(rest[11]),
                int(rest[12]),
                int(rest[21]),
            )
        except (IndexError, ValueError):
            continue
        if ppid == pid:
            out.append(
                (int(entry), name, utime + stime, rss_pages * os.sysconf("SC_PAGESIZE") >> 10)
            )
    return out


async def ffmpeg_count(app) -> int:
    """Hoeveel ffmpeg-processen de worker nu heeft: wat hij zelf telt (caps, ook stervende), en
    bij de lokale worker ook wat /proc onder hem ziet."""
    from app.media import client
    from app.media.worker.guard import FFMPEG  # de namen op één plek (regel 4, M1)

    count = 0
    with contextlib.suppress(Exception):
        count = (await client.caps(limit_s=2.0)).procs
    pid = worker_pid(app)
    if pid is not None:
        seen = await asyncio.to_thread(_children, pid)
        count = max(count, sum(1 for _, name, *_ in seen if name in FFMPEG))
    return count


@dataclass
class Samples:
    api_ms: list[float] = field(default_factory=list)
    cache: list[tuple[float, int]] = field(default_factory=list)
    cpu: list[float] = field(default_factory=list)
    rss_kb: int = 0
    procs: int = 0


class Watch:
    """Wat tijdens de test op de achtergrond loopt: de bewaking van ffmpeg (elke 0,5 s), en de
    metingen elke seconde (de API, de cache, de snelheid per speler, de CPU)."""

    def __init__(self, app, run: Run, http: httpx.AsyncClient, token: str | None, timing: Timing):
        self.app, self.run, self.http, self.token, self.timing = app, run, http, token, timing
        self.samples = Samples()
        self.players: dict[str, Player] = {}
        self.stop = asyncio.Event()
        self._ticks: dict[int, int] = {}

    async def guard(self) -> None:
        while not self.stop.is_set():
            count = await ffmpeg_count(self.app)
            self.samples.procs = max(self.samples.procs, count)
            if count > MAX_PROCS:
                self.run.stop_reason = f"{count} ffmpeg-processen (hooguit {MAX_PROCS})"
                log.warning("belastingtest gestopt: %s", self.run.stop_reason)
                self.stop.set()
                return
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), self.timing.s(POLL_S))

    async def sample(self) -> None:
        from app.media import admin, sessions, streams

        began = time.monotonic()
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        while not self.stop.is_set():
            if self.token:
                for path in API_PATHS:
                    t0 = time.monotonic()
                    with contextlib.suppress(httpx.HTTPError):
                        await self.http.get(path, headers=headers, timeout=10)
                        self.samples.api_ms.append((time.monotonic() - t0) * 1000)
            self.samples.cache.append((time.monotonic() - began, streams.cached_bytes()))
            table = sessions.table()
            for player in list(self.players.values()):
                s = table.by_sid.get(player.sid) if player.sid else None
                speed, _ = admin.stream_numbers(streams.get(s)) if s is not None else (None, None)
                if speed is not None:
                    player.report.speeds.append(speed)
            pid = worker_pid(self.app)
            if pid is not None:
                self._cpu(await asyncio.to_thread(_children, pid))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), self.timing.s(SAMPLE_S))

    def _cpu(self, children: list[tuple[int, str, int, int]]) -> None:
        tick = os.sysconf("SC_CLK_TCK")
        used = 0
        for pid, _name, ticks, rss in children:
            used += max(0, ticks - self._ticks.get(pid, ticks))
            self._ticks[pid] = ticks
            self.samples.rss_kb = max(self.samples.rss_kb, rss)
        self.samples.cpu.append(used / tick / max(self.timing.s(SAMPLE_S), 1e-3))


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 1)


def judge(phase: str, players: list[PlayerReport], api_p95: float | None) -> dict[str, Any]:
    """De uitkomst van een fase tegen de grenzen van §4.6 punt 7."""
    starts = [p.start_s for p in players]
    problems = []
    if any(p.error for p in players):
        problems.append("een speler faalde")
    if any(s is None or s > GATES["start_s"] for s in starts):
        problems.append(f"een start boven {GATES['start_s']:g} s")
    stalls = sum(p.stalls for p in players)
    if stalls:
        problems.append(f"{stalls} keer haperen")
    if phase == "a":
        speeds = [p.summary()["speed"] for p in players]
        if any(s is not None and s < GATES["speed"] for s in speeds):
            problems.append(f"een run onder {GATES['speed']:g}×")
        if api_p95 is not None and api_p95 > GATES["api_p95_ms"]:
            problems.append(f"de API {api_p95:g} ms (p95)")
    restarts = [r for p in players for r in p.restarts]
    if any(r["na_ready_s"] > GATES["restart_s"] for r in restarts):
        problems.append(f"weer spelen later dan {GATES['restart_s']:g} s na ready")
    return {
        "passed": not problems,
        "problems": problems,
        "starts_s": starts,
        "stalls": stalls,
        "stall_s": round(sum(p.stall_s for p in players), 2),
        "restarts": restarts,
        "players": [p.summary() for p in players],
    }


def summary_lines(report: dict[str, Any]) -> list[str]:
    """Een korte samenvatting in het Nederlands (ook voor het einde van het log in CI)."""
    lines = []
    for name, phase in report.get("phases", {}).items():
        if phase.get("skipped"):
            lines.append(f"fase {name}: niet gedraaid ({phase['skipped']})")
            continue
        starts = ", ".join(f"{s:.2f}" if s is not None else "-" for s in phase["starts_s"])
        word = "geslaagd" if phase["passed"] else "niet geslaagd: " + "; ".join(phase["problems"])
        lines.append(f"fase {name}: start {starts} s, {phase['stalls']} keer haperen, {word}")
    if report.get("api_p95_ms") is not None:
        lines.append(f"API p95: {report['api_p95_ms']:g} ms")
    if report.get("stopped"):
        lines.append(f"gestopt: {report['stopped']}")
    lines.append("uitkomst: " + ("geslaagd" if report.get("passed") else "niet geslaagd"))
    return lines


async def _phase(
    name: str,
    watch: Watch,
    opener_for: Callable[[Source, uuid.UUID], Opener],
    sources: list[Source],
    *,
    seconds: int,
    stagger: bool,
    timing: Timing,
) -> list[PlayerReport]:
    """Eén fase: elke speler op zijn bron; a gespreid met een sprong, b tegelijk."""
    run = watch.run
    run.phase = name  # type: ignore[assignment]
    players = []
    for i, source in enumerate(sources):
        report = PlayerReport(name=f"{name}{i + 1}", phase=name, source=source.name)
        player = Player(watch.http, opener_for(source, uuid.uuid4()), report, timing=timing)
        players.append(player)
        watch.players[report.name] = player

    async def one(i: int, player: Player) -> None:
        if stagger and i:
            await asyncio.sleep(timing.s(timing.stagger_s * i))
        seek = timing.s(timing.seek_at_s) if stagger else None
        await player.run(seconds, seek_at=seek, jump_ms=int(timing.seek_jump_s * 1000))

    tasks = [asyncio.ensure_future(one(i, p)) for i, p in enumerate(players)]
    stopper = asyncio.ensure_future(watch.stop.wait())
    try:
        await asyncio.wait({*tasks, stopper}, return_when=asyncio.FIRST_COMPLETED)
        while not all(t.done() for t in tasks) and not watch.stop.is_set():
            await asyncio.wait({*tasks, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in [*tasks, stopper]:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, stopper, return_exceptions=True)
        for player in players:
            await player.close()
            watch.players.pop(player.report.name, None)
    return [p.report for p in players]


# De family van de run die nu loopt. Een sessie op een film (sources) is een gewone sessie op de
# node (loadtest false); hieraan ziet /beheer dat hij van de test is (ours).
_families: set[uuid.UUID] = set()


def ours(s) -> bool:
    """Een sessie van de belastingtest: op een eigen bron, of op een film van een run die loopt.
    Zo'n sessie telt niet als echte kijker (admin.transcodes_running), niet in de redenen per
    dag (admin.counting) en heet op /beheer "belastingtest"."""
    return bool(s.loadtest) or (s.family is not None and s.family in _families)


def _ours(s, family: uuid.UUID | None) -> bool:
    """Een sessie van de test: op een eigen bron, of (met sources) van deze run (zijn family)."""
    return bool(s.loadtest) or (family is not None and s.family == family)


def _close_loadtest_sessions(family: uuid.UUID | None = None) -> int:
    """Na afloop: de sessies van de test dicht (hun stroom en cache gaan mee weg)."""
    from app.media import sessions

    closed = 0
    for s in list(sessions.table().open()):
        if _ours(s, family):
            sessions.close(s)
            closed += 1
    return closed


async def _drop_streams(family: uuid.UUID | None = None) -> None:
    from app.media import streams

    for st in list(streams.all()):
        if _ours(st.session, family):
            with contextlib.suppress(Exception):
                await streams.close(st.session)


async def execute(
    app,
    settings: Settings,
    run: Run,
    user,
    *,
    http: httpx.AsyncClient,
    token: str | None,
    timing: Timing = TIMING,
) -> dict[str, Any]:
    """De hele test in de app (fase a en b; c en d niet: zie de docstring)."""
    from app.media import analysis

    short = short_run(run.seconds)
    family = uuid.uuid4()
    sources: list[Source]
    if run.nodes:  # sources: de films van de beheerder, speler i op film i modulo het aantal
        sources = [run.nodes[i % len(run.nodes)] for i in range(run.streams)]
    else:
        run.state = "making_sources"
        sources = list(await make_sources(settings, run.streams, run.seconds))
    _families.add(family)  # vanaf hier sessies; finally haalt hem weer weg
    run.state = "running"
    watch = Watch(app, run, http, token, timing)
    original = analysis.busy_live
    analysis.busy_live = lambda: max(original(), 2)  # de analyse wacht zolang de test loopt
    guard = asyncio.ensure_future(watch.guard())
    sample = asyncio.ensure_future(watch.sample())

    def opener_for(source: Source, player: uuid.UUID) -> Opener:
        async def opener(start_ms: int, replace: str | None) -> LoadtestPlay:
            while True:
                try:
                    return await play_source(
                        app, settings, user, family, source, codec=run.codec, short=short,
                        start_ms=start_ms, replace=replace, player=player,
                    )  # fmt: skip
                except HTTPException as exc:
                    # Een film zonder opgeslagen probe (sources): 409 media_probe_pending als
                    # de probe langer duurt dan het budget van play; hij loopt door. Opnieuw na
                    # Retry-After, zoals de speler in de browser en die buiten de app (fase c).
                    # De wachttijd telt mee in de start. Andere fouten blijven fouten.
                    detail = exc.detail if isinstance(exc.detail, dict) else {}
                    if detail.get("code") != "media_probe_pending" or watch.stop.is_set():
                        raise
                    wait = float((exc.headers or {}).get("Retry-After", 2))
                    await asyncio.sleep(timing.s(wait))

        return opener

    phases: dict[str, Any] = {}
    try:
        names = ["b"] if short else ["a", "b"]
        for name in names:
            if watch.stop.is_set():
                break
            began = len(watch.samples.api_ms)
            reports = await _phase(
                name, watch, opener_for, sources, seconds=run.seconds, stagger=name == "a",
                timing=timing,
            )  # fmt: skip
            phases[name] = judge(name, reports, p95(watch.samples.api_ms[began:]))
            _close_loadtest_sessions(family)
            await _drop_streams(family)
    finally:
        watch.stop.set()
        await asyncio.gather(guard, sample, return_exceptions=True)
        analysis.busy_live = original
        _close_loadtest_sessions(family)
        await _drop_streams(family)
        _families.discard(family)
    phases["c"] = {"skipped": "alleen met de spelers buiten de app (--extern)"}
    phases["d"] = {"skipped": "DS herstart Jonas met de hand (runbook media.md)"}
    cache = watch.samples.cache
    per_minute = None
    if len(cache) >= 2 and cache[-1][0] > cache[0][0]:
        per_minute = round(
            (cache[-1][1] - cache[0][1]) / (1 << 20) / ((cache[-1][0] - cache[0][0]) / 60), 1
        )
    report: dict[str, Any] = {
        "streams": run.streams,
        "seconds": run.seconds,
        "codec": run.codec,
        "short": short,
        "sources": [s.name for s in sources],
        "films": [str(n.id) for n in run.nodes],
        "phases": phases,
        "api_p95_ms": p95(watch.samples.api_ms),
        "ffmpeg_max": watch.samples.procs,
        "worker_cpu_cores": round(max(watch.samples.cpu), 2) if watch.samples.cpu else None,
        "worker_rss_kb": watch.samples.rss_kb or None,
        "cache_mb_per_min": per_minute,
        "stopped": run.stop_reason,
    }
    evaluated = [p for p in phases.values() if not p.get("skipped")]
    report["passed"] = (
        bool(evaluated) and run.stop_reason is None and all(p["passed"] for p in evaluated)
    )
    report["summary"] = summary_lines(report)
    return report


def token_for(settings: Settings, user, minutes: int = 60) -> str:
    """Een kort access-token van de beheerder die de test startte (voor de API-metingen)."""
    from app.security import _encode

    claims = {"sub": str(user.id), "role": user.role, "typ": "access", "fam": str(uuid.uuid4())}
    return _encode(settings, claims, minutes)


def busy() -> bool:
    """Lopen er echte transcodes (van kijkers), een meting of al een test?"""
    from app.media import admin

    return admin.transcodes_running() > 0 or admin.bench_running() or running()


HttpFactory = Callable[[], httpx.AsyncClient]


def default_http(settings: Settings) -> httpx.AsyncClient:
    from app.media.status import app_url

    return httpx.AsyncClient(base_url=app_url(settings), timeout=60)


def start(
    app,
    settings: Settings,
    user,
    body: LoadtestIn,
    *,
    http: HttpFactory | None = None,
    timing: Timing = TIMING,
    nodes: list[Node] | None = None,
) -> uuid.UUID:
    """POST /api/v1/admin/media/loadtest: 202 met het id, of 409 media_bench_busy. nodes: de
    films van sources (node_sources, in de route opgezocht)."""
    global _current
    if not settings.media_loadtest:
        raise loadtest_off()
    if busy():
        raise media_errors.bench_busy()
    run = Run(
        id=uuid.uuid4(),
        streams=body.streams,
        seconds=body.seconds,
        codec=body.codec,
        started_at=datetime.now(UTC),
        nodes=list(nodes or []),
    )
    make = http or (lambda: default_http(settings))

    async def go() -> None:
        try:
            async with make() as client:
                run.report = await execute(
                    app, settings, run, user, http=client, token=token_for(settings, user),
                    timing=timing,
                )  # fmt: skip
            run.state = "stopped" if run.stop_reason else "done"
            for line in run.report.get("summary", []):
                log.info("belastingtest: %s", line)
        except asyncio.CancelledError:
            run.state = "stopped"
            raise
        except Exception as exc:
            log.exception("belastingtest mislukt")
            run.state = "failed"
            run.report = {"error": exc.__class__.__name__, "passed": False}
        finally:
            run.phase = None

    run.task = asyncio.get_running_loop().create_task(go(), name="media-loadtest")
    _current = run
    return run.id


# --- de cli -----------------------------------------------------------------------------------


async def _poll(http: httpx.AsyncClient, headers: dict[str, str], limit_s: float) -> dict:
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        r = await http.get("/api/v1/admin/media/loadtest", headers=headers)
        r.raise_for_status()
        found = r.json()
        if found.get("state") in ("done", "failed", "stopped"):
            return found.get("report") or {"passed": False, "state": found.get("state")}
        await asyncio.sleep(2.0)
    return {"passed": False, "error": "de test duurde te lang"}


def _code(r: httpx.Response) -> str | None:
    """De code van een API-fout ({"detail": {"code": ...}}), of None."""
    try:
        detail = r.json().get("detail")
    except ValueError:
        return None
    return detail.get("code") if isinstance(detail, dict) else None


async def _external(
    settings: Settings, base: str, token: str, *, streams: int, seconds: int, codec: str
) -> dict[str, Any]:
    """De spelers buiten de app (fase c): ze vragen hun sessies via
    POST /api/v1/admin/media/loadtest/play en overleven een herstart van de app (410: een nieuwe
    sessie op de plek). Jonas doet de herstart zelf (dc restart platform) terwijl dit loopt."""
    headers = {"Authorization": f"Bearer {token}"}
    timing = TIMING
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=60) as http:

        def opener_for(index: int, player: uuid.UUID) -> Opener:
            async def opener(start_ms: int, replace: str | None) -> LoadtestPlay:
                body = {
                    "source": index, "seconds": seconds, "codec": codec, "start_ms": start_ms,
                    "replace": replace, "player": str(player),
                }  # fmt: skip
                while True:
                    r = await http.post("/api/v1/admin/media/loadtest/play", json=body)
                    if r.status_code == 409 and r.json().get("detail", {}).get("code") == (
                        "media_probe_pending"
                    ):
                        await asyncio.sleep(float(r.headers.get("Retry-After", "5")))
                        continue
                    busy = r.status_code == 503 and _code(r) == "media_busy"
                    if busy and r.headers.get("Retry-After"):
                        # te druk (de plaatsen van de worker, de toelating): later opnieuw,
                        # zoals de speler in de browser
                        await asyncio.sleep(float(r.headers["Retry-After"]))
                        continue
                    if r.status_code in (502, 503, 410):
                        raise SessionGone(str(r.status_code))
                    r.raise_for_status()
                    return LoadtestPlay.model_validate(r.json())

            return opener

        reports = []
        players = []
        for i in range(max(1, min(streams, 3))):
            report = PlayerReport(name=f"c{i + 1}", phase="c", source=str(i))
            players.append(Player(http, opener_for(i, uuid.uuid4()), report, timing=timing))
        await asyncio.gather(*(p.run(seconds) for p in players))
        for p in players:
            await p.close()
            reports.append(p.report)
    phases = {"c": judge("c", reports, None)}
    report = {"streams": streams, "seconds": seconds, "codec": codec, "external": base}
    report["phases"] = phases
    report["passed"] = phases["c"]["passed"]
    report["summary"] = summary_lines(report)
    return report


async def run_cli(
    settings: Settings,
    *,
    streams: int = 3,
    seconds: int = 300,
    codec: Literal["h264", "vp9"] = "h264",
    external: str | None = None,
    sources: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """Voor app.cli media-loadtest: vraagt de draaiende app (127.0.0.1) de test te draaien en
    wacht op de uitkomst; met external draaien de spelers hier, tegen dat adres (fase c). Media
    uit: media_off; de test uit: media_off met reason loadtest. sources (--film): eigen films in
    plaats van de bronnen van de test; de spelers buiten de app kennen alleen die bronnen."""
    from app.media import status

    if not settings.media_enabled:
        raise media_errors.off()
    if not settings.media_loadtest:
        raise loadtest_off()
    if sources and external:
        raise errors.fail(422, "invalid_input", "--film kan niet met --extern")
    body = LoadtestIn(streams=streams, seconds=seconds, codec=codec, sources=sources or None)
    token = await status.admin_token(settings, minutes=max(30, body.seconds // 60 * 3 + 30))
    if token is None:
        raise errors.fail(409, "admin_only", "Er is nog geen beheerder")
    if external:
        return await _external(
            settings, external.rstrip("/"), token, streams=body.streams, seconds=body.seconds,
            codec=body.codec,
        )  # fmt: skip
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=status.app_url(settings), timeout=60) as http:
        data = body.model_dump(mode="json", exclude_none=True)
        r = await http.post("/api/v1/admin/media/loadtest", json=data, headers=headers)
        if r.status_code != 202:
            detail = (
                r.json().get("detail", {})
                if r.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            raise errors.fail(
                r.status_code,
                str(detail.get("code") or "media_bench_busy"),
                str(detail.get("message") or f"de app gaf {r.status_code}"),
            )
        limit = 60 * 30 + body.seconds * 4
        return await _poll(http, headers, limit)
