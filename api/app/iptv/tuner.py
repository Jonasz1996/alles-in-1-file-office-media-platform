"""De tuners: één per kanaal dat iemand kijkt, gedeeld door iedereen die ernaar kijkt.

Een tuner:
1. leest de stroom van de aanbieder (upstream.read, in de app: daar staan de logins) en schrijft
   hem in een pipe naar de worker (opdracht live, ops/live.py);
2. leest de fMP4 van de worker terug en knipt hem in init en fragmenten (fmp4.Splitter). Hij
   houdt de laatste KEEP fragmenten bij;
3. een kijker (follow) krijgt de init, dan het nieuwste fragment, en dan elk volgend. Wie te ver
   achter raakt (een trage verbinding), springt naar het nieuwste;
4. is het beeld geen H.264 (MPEG-2, HEVC) en staat video op auto, dan begint hij één keer
   opnieuw met omzetten.

Grenzen: hooguit max_streams tuners tegelijk (de verbindingen die de aanbieder toelaat). Is het
vol, dan stopt een tuner zonder kijkers en anders Busy (503 iptv_busy met de kanalen die
lopen). IDLE_S zonder kijkers: de tuner stopt. STALL_S geen bytes van de worker: de tuner
stopt met een fout. Het platform draait in één proces (app.serve) en in het cluster op de node
met het VIP: de tuners staan dus gewoon in het geheugen.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import os
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.iptv import fmp4, upstream
from app.media import client
from app.media.wire import Op
from app.media.worker.args import AudioEncode, LiveArgs

log = logging.getLogger("platform.iptv")

IDLE_S = 20.0
START_S = 30.0  # zo lang wacht een kijker op de eerste beelden
STALL_S = 20.0
KEEP = 6
QUEUE_HIGH = 64
QUEUE_LOW = 8
PLAYABLE = frozenset({"avc1", "avc3"})


class Busy(Exception):
    def __init__(self, channels: list[str]):
        super().__init__("alle tuners zijn bezet")
        self.channels = channels


class TuneError(Exception):
    """De tuner stopte (vaste reden)."""


class _NeedTranscode(Exception):
    pass


@dataclass(frozen=True)
class Spec:
    channel_id: uuid.UUID
    name: str
    url: str
    user_agent: str | None
    video: str  # auto | transcode
    max_streams: int


class _Pipe(asyncio.Protocol):
    def __init__(self, queue: asyncio.Queue[bytes | None]):
        self.queue = queue
        self.transport: asyncio.ReadTransport | None = None
        self.paused = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def data_received(self, data: bytes) -> None:
        self.queue.put_nowait(data)
        if self.queue.qsize() >= QUEUE_HIGH and not self.paused and self.transport is not None:
            self.transport.pause_reading()
            self.paused = True

    def eof_received(self) -> bool:
        self.queue.put_nowait(None)
        return False

    def connection_lost(self, exc: Exception | None) -> None:
        self.queue.put_nowait(None)

    def drained(self) -> None:
        if self.paused and self.queue.qsize() <= QUEUE_LOW and self.transport is not None:
            with contextlib.suppress(Exception):
                self.transport.resume_reading()
            self.paused = False


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


class Tuner:
    def __init__(self, spec: Spec):
        self.spec = spec
        self.init: bytes | None = None
        self.codecs = ""
        self.mode = "transcode" if spec.video == "transcode" else "copy"
        self.fragments: deque[tuple[int, bytes]] = deque(maxlen=KEEP)
        self.seq = 0
        self.viewers = 0
        self.error: str | None = None
        self.ended = False
        self.since = dt.datetime.now(dt.UTC)
        loop = asyncio.get_running_loop()
        self.ready: asyncio.Future[None] = loop.create_future()
        self.ready.add_done_callback(lambda f: f.cancelled() or f.exception())
        self.changed = asyncio.Event()
        self.task = asyncio.ensure_future(self._run())
        self._idle: asyncio.TimerHandle | None = loop.call_later(IDLE_S, self._abandon)

    # --- kijkers ---

    def join(self) -> None:
        self.viewers += 1
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None

    def leave(self) -> None:
        self.viewers -= 1
        if self.viewers <= 0 and not self.ended and self._idle is None:
            self._idle = asyncio.get_running_loop().call_later(IDLE_S, self._abandon)

    def _abandon(self) -> None:
        self._idle = None
        if self.viewers <= 0 and not self.task.done():
            log.info("live tv: tuner gestopt, niemand kijkt nog")
            self.task.cancel()

    def stop(self) -> None:
        if not self.task.done():
            self.task.cancel()

    def _signal(self) -> None:
        old, self.changed = self.changed, asyncio.Event()
        old.set()

    # --- de stroom ---

    async def _run(self) -> None:
        try:
            try:
                await self._play(self.mode == "transcode")
            except _NeedTranscode:
                log.info("live tv: beeld is geen H.264, opnieuw met omzetten")
                self.mode = "transcode"
                await self._play(True)
            if self.error is None:
                self.error = "de aanbieder stopte de stroom"
        except asyncio.CancelledError:
            self.error = self.error or "gestopt"
            raise
        except upstream.UpstreamError as exc:
            self.error = str(exc)
        except client.WorkerBusy:
            self.error = "de mediaworker is druk"
        except client.WorkerDown:
            self.error = "de mediaworker is niet bereikbaar"
        except client.WorkerFailed as exc:
            self.error = "de stroom kon niet worden omgezet"
            log.warning("live tv: worker: %s", exc.code)
        except TuneError as exc:
            self.error = str(exc)
        except Exception:
            log.exception("live tv: tuner")
            self.error = "interne fout"
        finally:
            self.ended = True
            if not self.ready.done():
                self.ready.set_exception(TuneError(self.error or "gestopt"))
            if self._idle is not None:
                self._idle.cancel()
                self._idle = None
            if _tuners.get(self.spec.channel_id) is self:
                del _tuners[self.spec.channel_id]
            self._signal()

    def _args(self, transcode: bool) -> LiveArgs:
        return LiveArgs(
            video="h264" if transcode else "copy",
            height=720,
            maxrate_k=4000,
            audio=AudioEncode(codec="aac", channels=2, bitrate_k=160),
        )

    async def _feed(self, fd: int) -> None:
        """De bytes van de aanbieder in de pipe naar de worker."""
        try:
            async for chunk in upstream.read(self.spec.url, self.spec.user_agent):
                await asyncio.to_thread(_write_all, fd, chunk)
        except BrokenPipeError:
            return  # de worker stopte; dat ziet _play zelf
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    async def _play(self, transcode: bool) -> None:
        r, w = os.pipe2(os.O_CLOEXEC)
        try:
            job, reads = await client.call(Op.LIVE, self._args(transcode), fds=[r], outputs=1)
        except BaseException:
            os.close(w)
            raise
        finally:
            os.close(r)
        feeder = asyncio.ensure_future(self._feed(w))
        transport: asyncio.BaseTransport | None = None
        result: asyncio.Future | None = None
        try:
            pipe = os.fdopen(reads[0], "rb", buffering=0)
            queue: asyncio.Queue[bytes | None] = asyncio.Queue()
            loop = asyncio.get_running_loop()
            try:
                transport, protocol = await loop.connect_read_pipe(lambda: _Pipe(queue), pipe)
            except BaseException:
                pipe.close()
                raise
            result = asyncio.ensure_future(job.result())
            splitter = fmp4.Splitter()
            while True:
                getter = asyncio.ensure_future(queue.get())
                waiting: set[asyncio.Future] = {getter}
                if not feeder.done():
                    waiting.add(feeder)
                done, _ = await asyncio.wait(waiting, timeout=STALL_S)
                if feeder.done() and feeder.exception() is not None:
                    getter.cancel()
                    raise feeder.exception()  # type: ignore[misc]
                if getter not in done:
                    getter.cancel()
                    if not done:
                        raise TuneError(f"{STALL_S:g} s geen beeld van de aanbieder")
                    continue  # de aanbieder is klaar; de worker maakt zijn laatste stuk af
                chunk = getter.result()
                if chunk is None:
                    break
                try:
                    fragments = splitter.feed(chunk)
                except fmp4.BoxError:
                    raise TuneError("de worker gaf geen geldige fMP4") from None
                protocol.drained()
                if splitter.init is not None and self.init is None:
                    codec = fmp4.video_codec(splitter.init)
                    if not transcode and codec not in PLAYABLE:
                        raise _NeedTranscode()
                    self.init = splitter.init
                    self.codecs = fmp4.codecs(splitter.init)
                for fragment in fragments:
                    self.seq += 1
                    self.fragments.append((self.seq, fragment))
                    if not self.ready.done():
                        self.ready.set_result(None)
                if fragments:
                    self._signal()
            if feeder.done() and feeder.exception() is not None:
                raise feeder.exception()  # type: ignore[misc]
            await result
        finally:
            if not feeder.done():
                feeder.cancel()
                with contextlib.suppress(BaseException):
                    await feeder
            if result is not None and not result.done():
                result.cancel()
                with contextlib.suppress(BaseException):
                    await result
            with contextlib.suppress(Exception):
                await job.cancel()
            if transport is not None:
                transport.close()

    async def follow(self) -> AsyncIterator[bytes]:
        """De init en dan de fragmenten, vanaf het nieuwste. join/leave doet de aanroeper."""
        assert self.init is not None
        yield self.init
        last = self.fragments[-1][0] - 1 if self.fragments else self.seq
        while True:
            waiter = self.changed
            fresh = [f for f in self.fragments if f[0] > last]
            if fresh and fresh[0][0] > last + 1:
                fresh = fresh[-1:]  # te ver achter: naar het nieuwste
            for seq, data in fresh:
                yield data
                last = seq
            if self.ended and not [f for f in self.fragments if f[0] > last]:
                return
            if not fresh:
                await waiter.wait()

    def info(self) -> dict:
        return {
            "channel_id": str(self.spec.channel_id),
            "name": self.spec.name,
            "viewers": max(0, self.viewers),
            "mode": self.mode,
            "since": self.since.isoformat(),
            "codecs": self.codecs,
        }


_tuners: dict[uuid.UUID, Tuner] = {}


def get(channel_id: uuid.UUID) -> Tuner | None:
    return _tuners.get(channel_id)


def running() -> list[Tuner]:
    return [t for t in _tuners.values() if not t.ended]


def tune(spec: Spec) -> Tuner:
    """De tuner van dit kanaal (die al loopt, of een nieuwe). Busy als alles bezet is."""
    tuner = _tuners.get(spec.channel_id)
    if tuner is not None and not tuner.ended:
        return tuner
    active = running()
    if len(active) >= spec.max_streams:
        idle = [t for t in active if t.viewers <= 0]
        if not idle:
            raise Busy([t.spec.name for t in active])
        idle[0].stop()
        del _tuners[idle[0].spec.channel_id]
    tuner = Tuner(spec)
    _tuners[spec.channel_id] = tuner
    return tuner


async def watch(spec: Spec) -> tuple[Tuner, AsyncIterator[bytes]]:
    """Een kijker: wacht tot er beeld is (hooguit START_S) en geeft de tuner en de bytes."""
    tuner = tune(spec)
    tuner.join()
    try:
        await asyncio.wait_for(asyncio.shield(tuner.ready), START_S)
    except TimeoutError:
        tuner.leave()
        raise TuneError(f"geen beeld binnen {START_S:g} s") from None
    except BaseException:
        tuner.leave()
        raise

    async def body() -> AsyncIterator[bytes]:
        try:
            async for data in tuner.follow():
                yield data
        finally:
            tuner.leave()

    return tuner, body()


def stop_all() -> None:
    for tuner in list(_tuners.values()):
        tuner.stop()
    _tuners.clear()
