"""De producent: één run van de worker (`stream`) voor één sessie, vanaf één segment (Keuze 22,
23, 25, 26, 46). M4.

- **Start** via `client.call(Op.STREAM, …)`: de bron per opdracht los geopend (bij inbranden
  twee keer), en één pipe terug. De app leest de pipe met asyncio (`connect_read_pipe`), nooit
  blokkerend.
- **Knippen** met fmp4.Cutter (begrensd: init ≤ 1 MB, `moof` ≤ 4 MB, `mdat` ≤ 64 MB).
- **Groeperen** tot segmenten volgens de Layout van de stroom (grid.py), op de pts van het
  eerste videosample (`tfdt` plus de getekende offset, ook bij transcode), met 1 ms speling voor
  het afronden. Een fragment vóór het startsegment gaat weg (remux begint op het keyframe
  ervoor, MPEG-TS eerder); een fragment voorbij N hoort bij het laatste segment. Een segment
  is af zodra het beeld van een fragment tot de grens van het segment loopt (het volgende
  fragment begint dan op of na die grens: zo wacht segment 0 niet op fragment 1), anders als
  het eerste fragment van een later segment komt, of aan het eind van een geslaagde run. Komt
  er na zo'n vroeg einde toch nog iets voor dat segment, dan schrijft de producent het hele
  segment opnieuw. Het laatste segment wacht altijd op het eind.
- **Schrijven** gebeurt door de stroom (`Sink.store`: `<n>.m4s.tmp`, dan atomair hernoemen, en
  de wachtende verzoeken wekken), op volgorde, in een eigen taak.
- **Tegendruk** (Keuze 26): de app leest niet verder als hij `Sink.limit_ms()` voor ligt op de
  speler (60 s in mediatijd, of 10 s zolang een andere sessie nog geen eerste segment heeft:
  startvoorrang), of als er twee segmenten op schrijven wachten. ffmpeg blokkeert dan op de
  pipe en kost niets.
- **Vast** (Keuze 26, 46): 30 s geen fragment terwijl de app leest: de run faalt
  (`Stalled`). **Pauze**: 120 s geen verzoek van de speler: de run stopt (`idle`); de sessie
  blijft. Allebei ziet de waakhond (elke seconde).
- **Stoppen** sluit de verbinding met de worker (die doet killpg) en de leeskant van de pipe
  zelf: een gepauzeerde transport krijgt na een kill geen EOF (spike punt 4).

Een run eindigt één keer, en zegt dat aan de stroom (`Sink.ended(producer, fout)`): geen fout
bij een geslaagde run, bij stoppen en bij pauze; anders de fout (WorkerFailed, WorkerDown,
Stalled, Fmp4Error). De stroom beslist over een nieuwe poging (streams.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path
from typing import Protocol

from app.media import client, fmp4
from app.media.grid import Layout
from app.media.wire import Op, WorkClass
from app.media.worker.args import StreamArgs

log = logging.getLogger("platform.media")

STALL_S = 30.0  # geen fragment terwijl de app leest: vast (Keuze 26)
IDLE_S = 120.0  # geen verzoek van de speler: de producent dicht (Keuze 26)
TICK_S = 1.0  # de waakhond
EOF_WAIT_S = 5.0  # na het einde van de opdracht nog zo lang op EOF van de pipe wachten
WRITE_QUEUE = 2  # meer segmenten dan dit wachten op schrijven: niet verder lezen
START_BUSY_S = 3.0  # de worker zegt busy: zo lang opnieuw proberen (de vorige run stopt nog)
START_RETRY_S = 0.1
SLACK_MS = 1  # het afronden van de pts naar hele ms


class Stalled(Exception):
    """30 s geen fragment terwijl de app las."""


def clock() -> float:
    """De klok van de producent en de stroom (monotoon, seconden). Tests zetten een nepklok."""
    return time.monotonic()


class Sink(Protocol):
    """Wat de producent van zijn stroom nodig heeft (streams.Stream)."""

    layout: Layout

    def generation(self, init: fmp4.Init) -> int: ...

    async def store(self, n: int, gen: int, data: bytes) -> None: ...

    async def store_init(self, gen: int, data: bytes) -> None: ...

    def playhead_ms(self) -> int: ...

    def limit_ms(self) -> int: ...

    def quiet_s(self) -> float: ...

    def ended(self, producer: Producer, error: BaseException | None) -> None: ...


class _Reader(asyncio.Protocol):
    def __init__(self, producer: Producer):
        self.producer = producer

    def data_received(self, data: bytes) -> None:
        self.producer._data(data)

    def eof_received(self) -> bool:
        self.producer._on_eof()
        return False

    def connection_lost(self, exc: Exception | None) -> None:
        self.producer._on_eof()


class Producer:
    """Eén run van de worker vanaf segment `start`."""

    def __init__(self, sink: Sink, start: int, args: StreamArgs, source: Path, *, inputs: int = 1):
        self.sink = sink
        self.start = start
        self.args = args
        self.source = source
        self.inputs = inputs
        self.next = start  # het segment dat nu gevuld wordt (of als volgende komt)
        self.produced_ms = sink.layout.begin(start)  # het eind van het laatste fragment
        self.completed = 0  # hoeveel segmenten deze run afmaakte
        self.gen: int | None = None
        self.error: BaseException | None = None
        self.idle = False
        self.paused = False
        self.stopping = False
        self.job: client.Job | None = None
        self._cutter = fmp4.Cutter()
        self._current: int | None = None
        self._parts: list[bytes] = []
        self._sent: tuple[int, bytes] | None = None  # het laatste segment dat vroeg af was
        self._transport: asyncio.ReadTransport | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._writer_task: asyncio.Task | None = None
        self._eof_seen = asyncio.Event()
        self._eof_at: float | None = None
        self._job_done = asyncio.Event()
        self._done = asyncio.Event()
        self._finishing = False
        self.last_fragment = clock()
        self.started_at = clock()

    # --- starten ----------------------------------------------------------------------------

    async def run(self) -> None:
        """De opdracht starten. WorkerBusy (na START_BUSY_S opnieuw proberen: de vorige run van
        deze sessie stopt misschien nog), WorkerDown en WorkerFailed gaan door."""
        end = clock() + START_BUSY_S
        while True:
            if self.stopping:  # tijdens het opnieuw proberen gestopt (de sessie ging dicht)
                return
            try:
                job, reads = await client.call(
                    Op.STREAM,
                    self.args,
                    source=self.source,
                    outputs=1,
                    inputs=self.inputs,
                )
                break
            except client.WorkerBusy as exc:
                if exc.work_class not in (WorkClass.TRANSCODE, WorkClass.REMUX):
                    raise
                if clock() >= end:
                    raise
                await asyncio.sleep(START_RETRY_S)
        pipe = os.fdopen(reads[0], "rb", buffering=0)
        if self.stopping:  # tijdens het starten gestopt (de sessie ging dicht)
            pipe.close()
            await job.cancel()
            return
        self.job = job
        loop = asyncio.get_running_loop()
        try:
            transport, _ = await loop.connect_read_pipe(lambda: _Reader(self), pipe)
        except BaseException:
            pipe.close()
            await job.cancel()
            raise
        self._transport = transport  # type: ignore[assignment]
        if self.stopping or self._finishing:
            # Gestopt, of al mislukt in de eerste bytes (de opruimtaak zag nog geen transport)
            transport.close()
            await job.cancel()
            return
        self.last_fragment = clock()
        self._writer_task = asyncio.ensure_future(self._writer())
        self._tasks = [
            asyncio.ensure_future(self._events()),
            self._writer_task,
            asyncio.ensure_future(self._watchdog()),
        ]
        self.flow()

    @property
    def alive(self) -> bool:
        return not self._finishing and not self.stopping

    @property
    def ending(self) -> bool:
        """Mislukt of klaar, maar de stroom hoorde het nog niet: sink.ended volgt zo."""
        return self._finishing and not self._done.is_set()

    def covers(self, n: int, ahead: int) -> bool:
        """Komt segment n uit deze run, binnen `ahead` segmenten van wat hij nu maakt?"""
        return self.alive and self.start <= n <= self.next + ahead

    # --- lezen ------------------------------------------------------------------------------

    def _data(self, data: bytes) -> None:
        if self._finishing:
            return
        try:
            items = self._cutter.feed(data)
        except fmp4.Fmp4Error as exc:
            self._fail(exc)
            return
        for item in items:
            if isinstance(item, fmp4.Init):
                self.gen = self.sink.generation(item)
                self._queue.put_nowait(("init", self.gen, item.data))
            else:
                self._fragment(item)
        self.flow()

    def _fragment(self, frag: fmp4.Fragment) -> None:
        self.last_fragment = clock()
        layout = self.sink.layout
        t = frag.video_start_ms
        if t is None:  # alleen geluid (een staart na het laatste beeld): bij het huidige
            if self._current is not None:
                self._reopen()
                self._parts.append(frag.data)
            self._advance(frag)
            return
        n = layout.segment_of(t + SLACK_MS)
        if n < self.start:
            return  # vóór de grens: remux begon op het keyframe ervoor, of MPEG-TS eerder
        if self._current is None:
            self._current = n
        elif n > self._current:
            self._complete()
            self._sent = None
            if n > self._current + 1:
                log.warning("producent: segment %d tot %d ontbreken", self._current + 1, n - 1)
            self._current = n
        else:
            self._reopen()
        self.next = self._current
        self._parts.append(frag.data)
        self._advance(frag)
        end = frag.video_end_ms
        if end is not None and n + 1 < layout.count and end + SLACK_MS >= layout.stop(n):
            # Het beeld loopt tot de grens: een volgend fragment hoort bij n + 1 of later. Dus
            # nu al af, zonder op dat fragment te wachten (scheelt een heel segment bij de
            # start). Het laatste segment wacht wel: alles voorbij N hoort erbij
            self._complete(early=True)

    def _reopen(self) -> None:
        """Toch nog iets voor een segment dat al vroeg af was: opnieuw, met alles erin (het
        volgende _complete schrijft het hele segment nog eens)."""
        if self._sent is not None and self._sent[0] == self._current:
            log.warning("producent: segment %d kreeg na zijn grens nog een fragment", self._current)
            self._parts = [self._sent[1]]
            self._sent = None

    def _advance(self, frag: fmp4.Fragment) -> None:
        for end in (frag.video_end_ms, frag.audio_end_ms, frag.video_start_ms):
            if end is not None:
                self.produced_ms = max(self.produced_ms, end)
                return

    def _complete(self, *, early: bool = False) -> None:
        """Het huidige segment is af: naar de schrijver."""
        if self._current is None or not self._parts or self.gen is None:
            return
        data = b"".join(self._parts)
        self._parts = []
        self._queue.put_nowait(("segment", self._current, self.gen, data))
        self.completed += 1
        self.next = self._current + 1
        self._sent = (self._current, data) if early else None

    def flow(self) -> None:
        """De tegendruk: pauzeren of verder lezen."""
        transport = self._transport
        if transport is None or self._finishing or self.stopping:
            return
        ahead = self.produced_ms - self.sink.playhead_ms()
        hold = ahead > self.sink.limit_ms() or self._queue.qsize() >= WRITE_QUEUE
        if hold and not self.paused:
            with contextlib.suppress(Exception):
                transport.pause_reading()
            self.paused = True
        elif not hold and self.paused:
            with contextlib.suppress(Exception):
                transport.resume_reading()
            self.paused = False
            self.last_fragment = clock()  # de klok van "vast" begint opnieuw

    def _on_eof(self) -> None:
        if not self._eof_seen.is_set():
            self._eof_at = clock()
            self._eof_seen.set()

    # --- de opdracht --------------------------------------------------------------------------

    async def _events(self) -> None:
        assert self.job is not None
        try:
            async for _ in self.job.events():
                pass  # progress: de waakhond kijkt naar de fragmenten, niet naar ffmpeg
        except (client.WorkerFailed, client.WorkerDown) as exc:
            if not self.stopping:
                self._fail(exc)
                return
        finally:
            self._job_done.set()
        if self.stopping:
            return
        # Geslaagd: wacht op het eind van de pipe, en maak dan het laatste segment af
        try:
            await asyncio.wait_for(self._eof_seen.wait(), EOF_WAIT_S)
        except TimeoutError:
            log.warning("producent: geen EOF na het einde van de opdracht")
        await asyncio.sleep(0)  # wat data_received nog had
        if self._finishing:
            return
        try:
            self._cutter.close()
        except fmp4.Fmp4Error as exc:
            self._fail(exc)
            return
        self._complete()
        self._finish(None)

    async def _writer(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            try:
                if item[0] == "init":
                    await self.sink.store_init(item[1], item[2])
                else:
                    await self.sink.store(item[1], item[2], item[3])
            except Exception as exc:  # de schijf: een fout van deze run
                log.exception("producent: segment schrijven mislukt")
                self._fail(exc)
            self.flow()

    async def _watchdog(self) -> None:
        while not self._finishing and not self.stopping:
            await asyncio.sleep(TICK_S)
            if self._finishing or self.stopping:
                return
            now = clock()
            if self.sink.quiet_s() >= IDLE_S:
                self.idle = True
                log.info("producent: %d s geen verzoek, dicht", int(IDLE_S))
                self._finish(None)
                return
            reading = not self.paused and not self._eof_seen.is_set()
            if reading and now - self.last_fragment >= STALL_S:
                log.warning("producent: %d s geen fragment, vast", int(STALL_S))
                self._fail(Stalled(f"{STALL_S:g} s geen fragment"))
                return
            ended = self._eof_at is not None and not self._job_done.is_set()
            if ended and now - self._eof_at >= STALL_S:  # type: ignore[operator]
                log.warning("producent: de pipe is dicht, maar de opdracht eindigt niet")
                self._fail(Stalled("geen einde van de opdracht"))
                return
            self.flow()

    # --- het einde ----------------------------------------------------------------------------

    def _fail(self, exc: BaseException) -> None:
        if self._finishing:
            return
        self._parts = []  # een half segment van een mislukte run telt niet
        self._sent = None
        self._current = None
        self._finish(exc)

    def _finish(self, error: BaseException | None) -> None:
        """Eén keer: de run houdt op (geslaagd, mislukt of pauze). De opruimtaak stopt de
        opdracht en de pipe, laat de schrijver leeglopen, en zegt het dan tegen de stroom."""
        if self._finishing:
            return
        self._finishing = True
        self.error = error
        task = asyncio.ensure_future(self._close(notify=not self.stopping))
        self._tasks.append(task)

    async def _close(self, *, notify: bool) -> None:
        try:
            if self.job is not None:
                with contextlib.suppress(Exception):
                    await self.job.cancel()
            if self._transport is not None:
                self._transport.close()
            self._queue.put_nowait(None)
            current = asyncio.current_task()
            for task in self._tasks:
                if task is current or task.done():
                    continue
                if task is self._writer_task:  # de schrijver: laten leeglopen
                    with contextlib.suppress(Exception):
                        await task
                else:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
        finally:
            self._done.set()
            if notify:
                try:
                    self.sink.ended(self, self.error)
                except Exception:
                    log.exception("producent: het einde melden")

    async def stop(self) -> None:
        """Stoppen (een sprong, de sessie dicht) en wachten tot alles weg is. Daarna zegt de
        run niets meer tegen de stroom."""
        self.stopping = True
        if not self._finishing:
            self._finishing = True
            await self._close(notify=False)
        else:
            await self._done.wait()

    async def wait(self) -> None:
        """Tot de run helemaal klaar is (tests)."""
        await self._done.wait()
