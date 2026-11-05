"""De stroom van een sessie: producent, segmenten, generaties en playlists (Keuze 22–26, 31,
46). M4.

`open(session, plan)` en `close(session)` zijn voor play en de sessies (M3); de routes onder
/api/v1/stream/{sid}/ (routers/stream.py) vragen een Stream om de kaart, de playlists, een init
of een segment.

**Eén producent per sessie** (producer.py). Een verzoek om segment n:
- staat n in de cache van de sessie: meteen;
- maakt de producent n, of ligt n hooguit `AHEAD` (3) segmenten voor op wat hij maakt: wachten;
- anders een herstart op de grens van n (Keuze 25): de oude producent stoppen en wachten tot
  hij weg is (zijn plaats in de worker komt vrij), en zoekverzoeken binnen 300 ms worden één
  herstart: hij begint pas als er 300 ms geen nieuw verzoek kwam (Keuze 12).

**Zoeken** (review golf 2, K1). Een speler die scrubt, laat oude verzoeken vallen, maar de server
ziet dat niet (uvicorn breekt een verzoek niet af als de client weggaat). Een verzoek om n is
daarom *ingehaald* zodra er een nieuwer verzoek kwam buiten [n, n + AHEAD] (een sprong ergens
anders heen). Een ingehaald verzoek wacht nog op zijn segment, maar start nooit een herstart, en
zijn 503 telt niet mee voor het sluiten. Een herstart begint op het laagste segment waar een
verzoek op wacht dat niet is ingehaald: die liggen allemaal binnen AHEAD van het nieuwste, dus
één run maakt ze allemaal.
Wachten duurt hooguit `media_segment_wait` (20 s): dan 503 media_timeout met Retry-After: 2, en
na 3 keer achter elkaar gaat de sessie dicht (het volgende verzoek: 410).

**Fouten** (Keuze 46): faalt een run (ffmpeg, vast, een kapotte box), dan nog één poging op
hetzelfde segment; faalt ook die, dan krijgt dat segment 502 media_transcode_failed. Een run die
niets maakte, telt ook als fout. busy of down van de worker bij het starten: dat verzoek krijgt
503 (media_busy worker, of media_worker_down), het volgende probeert opnieuw.

**Generaties van de init** (Keuze 22): de sleutel is de `stsd` per spoor (fmp4.Init.key). Een
init met een sleutel die de sessie al kent, is die generatie (ook na een andere ertussen); een
nieuwe sleutel is generatie + 1. Elk segment draagt de generatie van zijn run (`X-Init`); de
kaart en de playlists noemen de nieuwste. Een native HLS-speler kan `X-Init` niet lezen: krijgt
die een segment van een andere generatie dan zijn playlist, dan gaat de sessie dicht (410) en
laadt de speler opnieuw (`native_reloads` telt het, voor /beheer).

**De tegendruk** (Keuze 26): de producent leest niet verder dan 60 s in mediatijd voor het
laatst gevraagde segment, en niet verder dan 10 s zolang een andere sessie nog geen eerste
segment heeft (startvoorrang, hooguit 20 s per sessie).

**De cache** (Keuze 31, cache.py): `sessions/<uuid>/<n>.m4s` (eerst `.tmp`, dan atomair
hernoemd, in een thread), de inits in het geheugen (hooguit 1 MB per stuk, meestal 1 KB). Na elk
segment houdt de stroom zich aan zijn venster: cache_gb gedeeld door het aantal stromen.

Geen databaseverbinding tijdens het wachten (regel 8): open leest de keyframe-index in één korte
sessie, daarna niets meer.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from app.media import analysis, cache, client, decide, fmp4, hls, keyframes, ladder, runtime
from app.media import errors as media_errors
from app.media import producer as producers
from app.media import profile as profiles
from app.media.grid import Layout, remux_layout, transcode_layout
from app.media.types import ClientProfile, MediaMap, Plan, Probe, Rung, StreamInfo
from app.media.types import Stream as Track
from app.media.wire import Code
from app.media.worker.args import AudioEncode, StreamArgs, VideoEncode, demuxer_of

if TYPE_CHECKING:
    from app.media.sessions import Session

log = logging.getLogger("platform.media")

AHEAD = 3  # zoveel segmenten voor op de producent: wachten, geen herstart (Keuze 25)
SEEK_MERGE_S = 0.3  # zoekverzoeken binnen 300 ms: één herstart
LEAD_MS = 60_000  # de tegendruk: zo ver voor het laatst gevraagde segment (mediatijd)
PRIORITY_MS = 10_000  # zolang een andere sessie nog geen eerste segment heeft
PRIORITY_S = 20.0  # de startvoorrang van een sessie duurt hooguit zo lang
TIMEOUTS_MAX = 3  # zoveel keer achter elkaar media_timeout: de sessie dicht
ATTEMPTS = 2  # runs per segment: de eerste en één nieuwe poging (Keuze 46)
AUDIO_COPY_K = 256  # de schatting van gekopieerd geluid zonder bitrate in de probe (kbit/s)
IMPRECISE = frozenset({"mpegts", "mpeg"})  # zoeken landt op het volgende keyframe (punt 11)
RANGE = {"hdr10": "PQ", "dv5": "PQ", "hlg": "HLG"}
CODECS = re.compile(r'codecs="([^"]+)"')

# De stromen van dit proces, per sessie-id
_streams: dict[uuid.UUID, Stream] = {}
# Hoe vaak een native HLS-speler opnieuw moest laden om een nieuwe init (/beheer, M10)
native_reloads = 0


class EmptyRun(Exception):
    """Een run die slaagde, maar geen enkel segment maakte."""


@dataclass
class Segment:
    gen: int
    size: int
    path: Path


@dataclass(eq=False)
class _Want:
    """Een verzoek dat op segment n wacht."""

    n: int
    overtaken: bool = False  # er kwam een nieuwer verzoek buiten [n, n + AHEAD]


class _Signal:
    """Iets veranderde (een segment, een init, een fout, dicht): wie wacht, kijkt opnieuw."""

    def __init__(self) -> None:
        self._fut: asyncio.Future | None = None

    def fire(self) -> None:
        if self._fut is not None and not self._fut.done():
            self._fut.set_result(None)
        self._fut = None

    async def wait(self, seconds: float) -> None:
        if self._fut is None or self._fut.done():
            self._fut = asyncio.get_running_loop().create_future()
        await asyncio.wait({self._fut}, timeout=max(0.0, seconds))


def _gone(session: Session) -> HTTPException:
    """De fout voor wie wacht als de sessie dichtgaat (zoals sessions._stone)."""
    reason = session.closed
    if reason in (None, "gone"):
        return media_errors.session_gone()
    if reason == "replaced":
        return media_errors.session_gone("replaced")
    return media_errors.access_gone(reason)


def _failure(exc: BaseException) -> HTTPException:
    """Een segment dat ook na de nieuwe poging niet kwam: 502, behalve een fout die iets over
    de worker zegt (ffmpeg weg, een andere versie)."""
    if isinstance(exc, client.WorkerFailed) and exc.code not in (
        Code.FAILED,
        Code.INTERNAL,
        Code.TIMEOUT,
    ):
        return media_errors.from_worker(exc)
    if isinstance(exc, client.WorkerDown):
        return media_errors.worker_down()
    return media_errors.transcode_failed()


class Stream:
    """De stroom van één sessie (remux of transcode). Ook de Sink van zijn producent."""

    def __init__(
        self,
        session: Session,
        plan: Plan,
        probe: Probe,
        layout: Layout,
        args: StreamArgs,
        mime: str,
        *,
        source: Path,
        directory: Path,
        variant: hls.Variant,
        native: bool = False,
        independent: bool = True,
        wait_s: float = 20.0,
    ) -> None:
        self.session = session
        self.plan = plan
        self.probe = probe
        self.layout = layout
        self.args = args
        self.mime = mime
        self.source = source
        self.dir = directory
        self.native = native
        self.variant = variant
        self.independent = independent
        self.wait_s = wait_s
        self.inputs = 2 if args.burn else 1
        self.keys: dict[str, int] = {}  # Init.key → generatie
        self.inits: dict[int, bytes] = {}
        self.gen = 0  # de nieuwste generatie (0: nog geen init)
        self.playlist_gen: int | None = None  # wat v.m3u8 een native speler gaf
        self.segments: dict[int, Segment] = {}
        self.bytes = 0
        self.producer: producers.Producer | None = None
        self.target: int | None = None
        self.attempts: dict[int, int] = {}
        self.failed: dict[int, BaseException] = {}
        self.problem: tuple[int, BaseException] | None = None
        self.timeouts = 0
        self.waiting = 0  # verzoeken die nu wachten (dan is de stroom nooit stil)
        self.closed = False
        self.started = producers.clock()
        self.last_request = producers.clock()
        self.playhead = 0
        self.first_pending = True
        self._signal = _Signal()
        self._wants: list[_Want] = []  # de verzoeken die nu op een segment wachten
        self._seek_at = 0.0  # het laatste verzoek of nieuwe doel (de 300 ms van Keuze 12)
        self._restart: asyncio.Task | None = None
        self._again = False
        self._made_dir = False
        self._trimming: asyncio.Task | None = None

    # --- wat de producent nodig heeft (producer.Sink) ---------------------------------------

    def generation(self, init: fmp4.Init) -> int:
        gen = self.keys.get(init.key)
        if gen is None:
            self.gen += 1
            gen = self.gen
            self.keys[init.key] = gen
            self.inits[gen] = init.data
            if gen > 1:
                log.info("mediasessie %s: een andere stsd, generatie %d", self.session.id, gen)
            self._signal.fire()
        return gen

    async def store_init(self, gen: int, data: bytes) -> None:
        self._signal.fire()  # de init staat al in het geheugen (generation)

    async def store(self, n: int, gen: int, data: bytes) -> None:
        if self.closed:
            return
        path = self.dir / f"{n}.m4s"
        await asyncio.to_thread(self._write, path, data)
        if self.closed:  # tijdens het schrijven dicht: de map gaat weg
            return
        old = self.segments.get(n)
        if old is not None:
            self.bytes -= old.size
        self.segments[n] = Segment(gen, len(data), path)
        self.bytes += len(data)
        self.attempts.pop(n, None)
        if self.first_pending:
            self.first_pending = False
            _kick()
        self._signal.fire()
        budget = cache.window(_cache_gb(), len(_streams))
        if self.bytes > budget:
            await self.trim(budget)

    def _write(self, path: Path, data: bytes) -> None:
        if not self._made_dir:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._made_dir = True
        tmp = path.with_name(path.name + ".tmp")
        with builtins.open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)

    def playhead_ms(self) -> int:
        return self.playhead

    def limit_ms(self) -> int:
        """60 s, of 10 s zolang een andere sessie op haar eerste segment wacht."""
        for other in builtins.list(_streams.values()):
            if other is not self and other.waiting_first():
                return PRIORITY_MS
        return LEAD_MS

    def quiet_s(self) -> float:
        """Hoe lang geen verzoek; 0 zolang er een op een segment of init wacht."""
        if self.waiting:
            return 0.0
        return producers.clock() - self.last_request

    def ended(self, producer: producers.Producer, error: BaseException | None) -> None:
        """De producent hield op. Een oude (gestopt bij een herstart) telt niet meer."""
        if self.closed or producer is not self.producer:
            return
        self.producer = None
        count = self.layout.count
        if error is None and not producer.idle and producer.completed == 0:
            if producer.start < count and producer.start not in self.segments:
                error = EmptyRun("de run maakte geen segment")
        if error is not None:
            n = min(max(producer.next, producer.start), count - 1)
            log.warning(
                "mediasessie %s: run vanaf %d faalde bij %d (%s)",
                self.session.id,
                producer.start,
                n,
                error.__class__.__name__,
            )
            self._count_failure(n, error)
        self._signal.fire()

    def waiting_first(self) -> bool:
        """Heeft deze sessie startvoorrang (nog geen eerste segment, hooguit 20 s)?"""
        if not self.first_pending or self.closed:
            return False
        if self.failed or producers.clock() - self.started >= PRIORITY_S:
            self.first_pending = False
            _kick()
            return False
        return True

    # --- de herstart ------------------------------------------------------------------------

    def args_for(self, n: int) -> StreamArgs:
        start = round(self.layout.start_s(n), 3)
        update: dict[str, Any] = {"start_s": start}
        if isinstance(self.args.video, VideoEncode) and self.args.video.burn_index is not None:
            burn = round(max(0.0, start - 30.0), 3)  # spike punt 14
            update["video"] = self.args.video.model_copy(update={"burn_start_s": burn})
        return self.args.model_copy(update=update)

    def ensure(self, n: int) -> None:
        """Zorgen dat segment n komt: wachten op de producent, of een herstart op n."""
        if self.closed or n in self.failed or n in self.segments:
            return
        p = self.producer
        if p is not None and (p.covers(n, AHEAD) or p.ending):
            # Wachten: deze run maakt n, of hij houdt net op en ended komt zo (dat telt een
            # mislukte run; een herstart hier zou die fout overslaan, en de nieuwe poging ook)
            return
        self.schedule(n)

    def schedule(self, n: int) -> None:
        if n != self.target:
            self._seek_at = asyncio.get_running_loop().time()
        self.target = n
        if self._restart is not None and not self._restart.done():
            self._again = True  # de lopende herstart neemt het laatste doel
            return
        self._again = False
        self._restart = asyncio.ensure_future(self._restarts())

    async def _restarts(self) -> None:
        try:
            while not self.closed:
                self._again = False
                await self._restart_once()
                n = self.target
                if not self._again or n is None or n in self.segments or n in self.failed:
                    return
                p = self.producer
                if p is not None and p.covers(n, AHEAD):
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # een fout van de app zelf: nooit een hangende sessie
            log.exception("mediasessie %s: herstart", self.session.id)
        finally:
            self._signal.fire()

    async def _restart_once(self) -> None:
        loop = asyncio.get_running_loop()
        old, self.producer = self.producer, None
        if old is not None:
            await old.stop()  # wacht tot de oude run weg is (zijn plaats in de worker)
            # Zoekverzoeken binnen 300 ms: één herstart, pas als er zo lang geen nieuw verzoek
            # kwam (eerst telde het vanaf de eerste sprong: 10 keer in 1 s gaf 4 runs)
            while not self.closed:
                rest = self._seek_at + SEEK_MERGE_S - loop.time()
                if rest <= 0:
                    break
                await asyncio.sleep(rest)
        n = self._start_at()
        if self.closed or n is None or n in self.segments or n in self.failed:
            return
        await self.start(n)

    def _start_at(self) -> int | None:
        """Waar een herstart begint: het laagste segment waar een verzoek op wacht dat niet is
        ingehaald (ze liggen binnen AHEAD van het nieuwste, dus één run maakt ze allemaal), en
        anders het doel."""
        waiting = [
            w.n
            for w in self._wants
            if not w.overtaken and w.n not in self.segments and w.n not in self.failed
        ]
        return min(waiting) if waiting else self.target

    async def start(self, n: int) -> None:
        """Een producent vanaf segment n. busy of down bij het starten: het verzoek om n krijgt
        de fout; een andere fout telt als een mislukte run."""
        p = producers.Producer(self, n, self.args_for(n), self.source, inputs=self.inputs)
        self.producer = p
        try:
            await p.run()
        except asyncio.CancelledError:
            if self.producer is p:
                self.producer = None
            await p.stop()
            raise
        except (client.WorkerBusy, client.WorkerDown) as exc:
            if self.producer is p:
                self.producer = None
            await p.stop()
            self.problem = (n, exc)
        except Exception as exc:
            if self.producer is p:
                self.producer = None
            await p.stop()
            self._count_failure(n, exc)
        finally:
            self._signal.fire()

    def _count_failure(self, n: int, exc: BaseException) -> None:
        tries = self.attempts.get(n, 0) + 1
        self.attempts[n] = tries
        if tries >= ATTEMPTS:
            log.warning("mediasessie %s: segment %d mislukt na %d runs", self.session.id, n, tries)
            self.failed[n] = exc
            return
        self.schedule(n)  # één nieuwe poging op hetzelfde segment (Keuze 46)

    # --- voor de routes -----------------------------------------------------------------------

    def touch(self, n: int | None = None) -> None:
        self.last_request = producers.clock()
        if n is not None:
            self.playhead = self.layout.begin(n)
            self._trim_soon()  # de speler schoof op: wat nu ver genoeg achter hem ligt mag weg
        p = self.producer
        if p is not None:
            p.flow()

    def _trim_soon(self) -> None:
        """Boven het venster: op de achtergrond trimmen (niet in de weg van het antwoord)."""
        budget = cache.window(_cache_gb(), len(_streams))
        if self.closed or self.bytes <= budget:
            return
        if self._trimming is None or self._trimming.done():
            self._trimming = asyncio.ensure_future(self.trim(budget))

    def _check(self) -> None:
        if self.closed or self.session.closed is not None:
            raise _gone(self.session)

    def _problem(self, n: int | None) -> None:
        found = self.problem
        if found is not None and (n is None or found[0] == n):
            self.problem = None
            raise media_errors.from_worker(found[1])

    def _timeout(self) -> HTTPException:
        """Te lang gewacht: 503, en na 3 keer achter elkaar de sessie dicht (Keuze 25)."""
        self.timeouts += 1
        if self.timeouts >= TIMEOUTS_MAX:
            from app.media import sessions

            log.info("mediasessie %s: %d keer media_timeout, dicht", self.session.id, TIMEOUTS_MAX)
            sessions.close(self.session)
        return media_errors.timeout()

    async def fetch(self, n: int) -> tuple[int, bytes]:
        """Segment n: (generatie, bytes). Wacht hooguit wait_s."""
        self.touch(n)
        want = self._want(n)
        self.waiting += 1
        try:
            return await self._fetch(n, want)
        finally:
            self.waiting -= 1
            self._wants.remove(want)
            self.last_request = producers.clock()

    def _want(self, n: int) -> _Want:
        """Een nieuw verzoek om n: wie op een segment wacht waar n niet vlak achter ligt (een
        sprong ergens anders heen), is ingehaald."""
        for w in self._wants:
            if not w.n <= n <= w.n + AHEAD:
                w.overtaken = True
        want = _Want(n)
        self._wants.append(want)
        if n not in self.segments:  # een verzoek dat de cache beantwoordt, rekt de 300 ms niet
            self._seek_at = asyncio.get_running_loop().time()
        return want

    async def _fetch(self, n: int, want: _Want) -> tuple[int, bytes]:
        global native_reloads
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.wait_s
        while True:
            self._check()
            seg = self.segments.get(n)
            if seg is not None:
                try:
                    data = await asyncio.to_thread(seg.path.read_bytes)
                except FileNotFoundError:  # net weg (het venster): opnieuw maken
                    if self.segments.get(n) is seg:
                        self._forget(n)
                    continue
                if self.native and self.playlist_gen is not None and seg.gen != self.playlist_gen:
                    from app.media import sessions

                    native_reloads += 1
                    log.info("mediasessie %s: native HLS, een nieuwe init: dicht", self.session.id)
                    sessions.close(self.session)
                    raise media_errors.session_gone()
                self.timeouts = 0
                return seg.gen, data
            if n in self.failed:
                raise _failure(self.failed[n])
            self._problem(n)
            if not want.overtaken:
                self.ensure(n)  # een ingehaald verzoek wacht alleen (K1)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise media_errors.timeout() if want.overtaken else self._timeout()
            await self._signal.wait(remaining)

    async def init(self, gen: int) -> bytes:
        """De init van een generatie. Generatie 1 voordat er een is: wachten (hooguit
        wait_s); een generatie die er niet is: 404."""
        self.touch()
        self.waiting += 1
        try:
            return await self._init(gen)
        finally:
            self.waiting -= 1
            self.last_request = producers.clock()

    async def _init(self, gen: int) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.wait_s
        while True:
            self._check()
            data = self.inits.get(gen)
            if data is not None:
                return data
            if gen != 1 or self.gen >= 1:
                raise _not_found()
            if self.failed:
                raise _failure(next(iter(self.failed.values())))
            self._problem(None)
            p = self.producer
            if p is None or not p.alive:
                self.ensure(self.target if self.target is not None else self.first_segment())
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise self._timeout()
            await self._signal.wait(remaining)

    def first_segment(self) -> int:
        return self.layout.segment_of(self.session.start_ms)

    def current_gen(self) -> int:
        return max(1, self.gen)

    def media_map(self) -> MediaMap:
        self.touch()
        return hls.media_map(self.layout, self.current_gen(), self.mime)

    def master(self) -> str:
        self.touch()
        return hls.master(self.variant, independent=self.independent)

    def variant_playlist(self) -> str:
        self.touch()
        gen = self.current_gen()
        if self.native:
            self.playlist_gen = gen
        return hls.variant_playlist(self.layout, gen)

    # --- de cache -----------------------------------------------------------------------------

    def _forget(self, n: int) -> None:
        seg = self.segments.pop(n, None)
        if seg is not None:
            self.bytes -= seg.size

    async def trim(self, budget: int) -> None:
        """Onder budget komen (cache.trim): eerst achter de speler, nooit binnen 60 s."""
        pieces = [
            cache.Piece(n, s.size, self.layout.begin(n), self.layout.stop(n))
            for n, s in self.segments.items()
        ]
        drop = cache.trim(pieces, self.playhead, budget)
        if not drop:
            return
        paths = []
        for n in drop:
            seg = self.segments.get(n)
            if seg is not None:
                paths.append(seg.path)
                self._forget(n)
        await asyncio.to_thread(_unlink, paths)

    # --- dicht --------------------------------------------------------------------------------

    async def shutdown(self) -> None:
        self.closed = True
        for task in (self._restart, self._trimming):
            if task is not None and not task.done() and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        p, self.producer = self.producer, None
        if p is not None:
            await p.stop()
        self._signal.fire()
        if self.first_pending:
            self.first_pending = False
            _kick()
        self.segments.clear()
        self.bytes = 0
        await asyncio.to_thread(shutil.rmtree, self.dir, True)


def _unlink(paths: list[Path]) -> None:
    for path in paths:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def _not_found() -> HTTPException:
    from app import errors

    return errors.fail(404, "not_found", "Niet gevonden")


def _kick() -> None:
    """De startvoorrang veranderde: elke producent kijkt opnieuw of hij mag lezen."""
    for st in builtins.list(_streams.values()):
        p = st.producer
        if p is not None:
            p.flow()


def _cache_gb() -> float:
    from app.media import admission

    try:
        return float(admission.config().cache_gb)
    except Exception:  # geen app (een test): de standaard
        return 20.0


# --- het plan als opdracht --------------------------------------------------------------------


def native_hls(profile: ClientProfile | None) -> bool:
    """Een speler die alleen native HLS kan (geen MSE en geen MMS): hij leest X-Init niet."""
    if profile is None:
        return False
    engines = profile.engines
    return engines.native_hls and not (engines.mse or engines.mms)


def audio_for(
    plan: Plan, demuxer: str, track: Track | None, profile: ClientProfile | None
) -> str | AudioEncode:
    """Het geluid voor de worker. MPEG-TS en MPEG-PS: AAC (ADTS) kopieert de muxer niet, en
    een transcode knipt het geluid met atrim; dan omzetten (AAC, of Opus als de client geen AAC
    kan)."""
    if track is None:
        return "none"
    audio = plan.audio
    if audio is None or audio == "none":
        return "none"
    if isinstance(audio, AudioEncode):
        return audio
    if demuxer in IMPRECISE and (plan.mode == "transcode" or track.codec == "aac"):
        if profile is None or decide.plays(profile, profiles.support(profile, "mp4a.40.2"), "mse"):
            return decide.AAC
        return decide.OPUS
    return "copy"


def build_args(
    plan: Plan, probe: Probe, track: Track | None, profile: ClientProfile | None
) -> tuple[StreamArgs, Plan]:
    """De StreamArgs (vanaf 0) en het plan zoals het geluid echt gaat."""
    video = probe.video()
    demuxer = demuxer_of(probe.container)
    if video is None or demuxer is None:
        raise media_errors.unavailable("probe")
    audio = audio_for(plan, demuxer, track, profile)
    if plan.mode == "transcode":
        if not isinstance(plan.video, Rung):
            raise media_errors.unavailable("probe")
        encode: str | VideoEncode = ladder.encoder().video(
            plan.video, video, tonemap=plan.tonemap, burn_index=plan.burn
        )
    else:
        encode = "copy"
    args = StreamArgs(
        mode=plan.mode,  # type: ignore[arg-type]
        demuxer=demuxer,  # type: ignore[arg-type]
        start_s=0,
        source_start_s=round(probe.start_ms / 1000, 3),
        video_index=video.index,
        audio_index=track.index if audio != "none" and track is not None else None,
        video=encode,  # type: ignore[arg-type]
        audio=audio,  # type: ignore[arg-type]
        grid="2s" if plan.mode == "transcode" else None,
        hvc1=plan.mode == "remux" and video.codec == "hevc",
    )
    return args, plan.model_copy(update={"audio": audio})


def mime_for(probe: Probe, plan: Plan, track: Track | None, args: StreamArgs) -> str:
    mime = decide.mime(probe, plan, track if args.audio != "none" else None)
    if args.hvc1:
        mime = mime.replace('codecs="hev1.', 'codecs="hvc1.')
    return mime


def _audio_k(args: StreamArgs, track: Track | None) -> int:
    if args.audio == "none" or track is None:
        return 0
    if isinstance(args.audio, AudioEncode):
        return args.audio.bitrate_k
    return -(-(track.bitrate or AUDIO_COPY_K * 1000) // 1000)


def remux_sizes(
    layout: Layout, times_ms: list[int], sizes: list[int], audio_k: int, probe: Probe
) -> list[int]:
    """De geschatte grootte per segment bij remux: de GOP's uit de index (mediatijd) plus het
    geluid. Bij Matroska zit het geluid al in de cluster: dan is het een ruime schatting (de
    kant die veilig is voor BANDWIDTH)."""
    out = [0] * layout.count
    for t, size in zip(times_ms, sizes, strict=False):
        out[layout.segment_of(t)] += size
    if not any(out):  # geen groottes in de index: de bitrate van de bron
        rate = probe.bit_rate or 8_000_000
        out = [rate * d // 8000 for _, d in layout.spans()]
    return [s + audio_k * d // 8 for s, (_, d) in zip(out, layout.spans(), strict=True)]


def variant_for(
    probe: Probe,
    args: StreamArgs,
    mime: str,
    layout: Layout,
    track: Track | None,
    sizes: list[int] | None,
) -> hls.Variant:
    video = probe.video()
    assert video is not None
    found = CODECS.search(mime)
    codecs = found.group(1) if found else ""
    fps = min(video.fps or 24.0, float(ladder.FPS_MAX))
    audio_k = _audio_k(args, track)
    if isinstance(args.video, VideoEncode):
        bandwidth, average = hls.transcode_bandwidth(args.video.maxrate_k, audio_k)
        width, height = args.video.width, args.video.height
        video_range = "SDR"
    else:
        bandwidth, average = hls.remux_bandwidth(layout, sizes or [1] * layout.count)
        width, height = video.width or 0, video.height or 0
        video_range = RANGE.get(video.hdr, "SDR")
    return hls.Variant(
        bandwidth=bandwidth,
        average=average,
        codecs=codecs,
        width=width,
        height=height,
        fps=fps,
        video_range=video_range,  # type: ignore[arg-type]
    )


# --- open en close (M3) -----------------------------------------------------------------------


def _settings(session: Session):
    return session.settings if session.settings is not None else runtime.settings()


def _source(session: Session, settings) -> Path:
    if session.path is not None:
        return Path(session.path)
    from app.storage import BlobStore

    return BlobStore(settings.blob_dir).path(session.sha)


async def open(session: Session, plan: Plan) -> StreamInfo:
    """De stroom van een remux of transcode, en de eerste producent op het startsegment.
    busy of down bij het starten: de fout van de worker (play sluit de sessie dan)."""
    if plan.mode not in ("remux", "transcode"):
        raise media_errors.unavailable("probe")
    probe = session.probe
    if probe is None or probe.video() is None:
        raise media_errors.unavailable("probe")
    settings = _settings(session)
    profile: ClientProfile | None = session.profile
    index = session.audio_index
    track = decide.pick_audio(probe, index) if index is not None else None
    if track is None:
        track = decide.default_audio(probe)
    args, real = build_args(plan, probe, track, profile)
    sizes: list[int] | None = None
    if plan.mode == "transcode":
        layout = transcode_layout(probe)
    else:
        times, kf_sizes = await keyframes.load(settings, session.sha)
        times = [t - probe.start_ms for t in times]  # tijd van de container → mediatijd
        if not times:
            raise media_errors.unavailable("probe")
        layout = remux_layout(probe, times)
        sizes = remux_sizes(layout, times, kf_sizes, _audio_k(args, track), probe)
    mime = mime_for(probe, real, track, args)
    base = profile.base if profile is not None else "web"
    independent = plan.mode == "transcode" and profiles.flags(base).get(
        "independent_segments", True
    )
    st = Stream(
        session,
        real,
        probe,
        layout,
        args,
        mime,
        source=_source(session, settings),
        directory=runtime.sessions_dir(settings) / str(session.id),
        variant=variant_for(probe, args, mime, layout, track, sizes),
        native=native_hls(profile),
        independent=independent,
        wait_s=float(settings.media_segment_wait),
    )
    start = st.first_segment()
    st.playhead = layout.begin(start)
    st.target = start
    old = _streams.pop(session.id, None)
    if old is not None:
        await old.shutdown()
    if session.closed is not None:  # tijdens het lezen van de index dicht
        await st.shutdown()
        return _info(st)
    _streams[session.id] = st  # vóór het wachten: een close tijdens open stopt de producent
    p = producers.Producer(st, start, st.args_for(start), st.source, inputs=st.inputs)
    st.producer = p
    try:
        await p.run()
    except (client.WorkerBusy, client.WorkerDown, client.WorkerFailed) as exc:
        await _drop(st)
        raise media_errors.from_worker(exc) from None
    except BaseException:
        await _drop(st)
        raise
    log.info(
        "mediasessie %s: %s, %d segmenten, vanaf %d", session.id, plan.mode, layout.count, start
    )
    return _info(st)


def _info(st: Stream) -> StreamInfo:
    return StreamInfo(
        mode=st.plan.mode,
        url="master.m3u8",
        mime=st.mime,
        duration_ms=st.layout.end,
        offset_ms=-fmp4.OFFSET_MS,
        init_gen=1,
        segments=st.layout.count,
        grid=st.layout.grid,
        map_url="map.json",
    )


async def _drop(st: Stream) -> None:
    if _streams.get(st.session.id) is st:
        del _streams[st.session.id]
    with contextlib.suppress(Exception):
        await st.shutdown()


async def close(session: Session) -> None:
    """De stroom van deze sessie dicht: de producent stoppen (en wachten tot hij weg is) en de
    map weg. Elke keer veilig: zonder stroom (direct, audio, of een open die nooit liep of
    faalde) gebeurt er niets."""
    st = _streams.pop(session.id, None)
    if st is None:
        return
    await st.shutdown()
    log.info("mediasessie %s: stroom dicht", session.id)


def get(session: Session) -> Stream | None:
    return _streams.get(session.id)


def all() -> builtins.list[Stream]:
    """De stromen van nu (cache.sweep)."""
    return builtins.list(_streams.values())


def cached_bytes() -> int:
    return sum(st.bytes for st in _streams.values())


def busy_live() -> int:
    """Hoeveel transcodes er nu lopen (analysis wacht vanaf 2, Keuze 14)."""
    return sum(
        1
        for st in _streams.values()
        if st.plan.mode == "transcode" and st.producer is not None and st.producer.alive
    )


def reset() -> None:
    """Alleen voor tests: alles vergeten zonder iets te sluiten."""
    global native_reloads
    _streams.clear()
    native_reloads = 0


analysis.busy_live = busy_live
