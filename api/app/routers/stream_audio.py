"""Eén nummer als fMP4 voor gapless (M9, Keuze 38). De router gaat langs sessions.guard (regel 6).

GET /api/v1/stream/{sid}/audio.mp4

- Alleen voor een sessie met mode audio (play koos het muziekpad); anders 404.
- Gemaakt door remux_audio in de worker: `-c:a copy` voor FLAC en AAC, `-c:a flac` voor de rest
  (Opus, Vorbis, ALAC, WAV; S8). Het spoor is dat van play (session.audio_index, de index van
  ffprobe) als het hoeveelste audiospoor (`-map 0:a:N`).
- **De cache:** media_dir/audio/<sha>.mp4, of <sha>.<N>.mp4 voor audiospoor N > 0. Is hij af,
  dan als bestand met Range, If-Range, If-None-Match en de ETag "<sha>.a<N>", zoals /file. Zo
  niet: één remux per bestand, gedeeld door alle sessies, en het antwoord volgt het groeiende
  bestand (200 zonder Content-Length; een Range-kop telt dan niet, wat RFC 9110 toestaat). Een
  remux die mislukt, laat niets achter; het volgende verzoek begint opnieuw.
- **Wachten** (de start van de worker, een nieuw stuk van het bestand) gaat altijd via
  runtime.until_stopped (F3). De respons loopt door sessions.guarded_body: per stuk de toegang
  (Keuze 21). Geen databaseverbinding (regel 8).
- **Vast:** STALL_S geen bytes uit de pipe terwijl de opdracht loopt: de remux stopt en faalt.
- **Grootte:** audio/ houdt hooguit een kwart van cache_gb; na elk nieuw bestand gaan de oudste
  (op mtime; een verzoek zet die van een bestand dat het serveert op nu) weg tot het past. De
  sweep van M4 telt audio/ mee in cache_gb.
- **Een budget per remux** (review golf 4, S1): een kleine Opus van 6 kbps wordt als FLAC ruim
  100 keer zo groot. Elke remux krijgt daarom vooraf een bovengrens in bytes (`budget`: uit de
  probe de duur × rate × kanalen × 3 bytes voor FLAC, anders de bron + een kwart) en stopt als
  hij erboven komt (TooLarge: het .part weg, de volgers StreamCut). Past één nummer niet in de
  helft van het deel van audio/, dan 415 media_unsupported {reasons: [too_large]}. Minder dan
  10 GB vrij op data_dir na het budget, of samen met de remuxen die nog lopen meer dan het deel
  van audio/: 503 media_busy {reason: disk}. Vóór de start ruimt trim de oudste af tot alles
  wat loopt erbij past, en een .part zonder remux (van vóór een crash) gaat weg.
- **Eén remux per naam** (review golf 4, C1): de vraag kijkt na de stat opnieuw of een ander
  verzoek de remux al begon, en registreert hem zonder await ertussen. Staat het bestand er
  intussen toch, dan serveert de remux dat (geen tweede ffmpeg).
- **Niemand leest meer** (de vraag ging weg): na IDLE_S zonder volger stopt de remux.
- **Een blob die weggaat** (previews.discard → hooks.on_discard): zijn bestanden weg, en een
  remux die nog loopt, houdt zijn bestand niet.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app import errors
from app.media import admission, cache, client, hooks, runtime, sessions
from app.media import errors as media_errors
from app.media.wire import Op, WorkClass
from app.media.worker.args import RemuxAudioArgs, demuxer_of
from app.routers.stream_file import FILE_CACHE, byte_range, read_range

log = logging.getLogger("platform.media")

router = APIRouter(
    prefix="/api/v1/stream", tags=["stream"], dependencies=[Depends(sessions.guarded)]
)

NOT_FOUND = {"message": "Niet gevonden", "code": "not_found"}
MIME = "audio/mp4"
PIECE = 256 * 1024  # een stuk van de respons
COPY_CODECS = frozenset({"flac", "aac"})  # de rest wordt FLAC (S8)
CACHE_SHARE = 4  # audio/ hooguit cache_gb / 4
STALL_S = 30.0  # geen bytes uit de pipe terwijl de opdracht loopt: vast
START_BUSY_S = 3.0  # de worker zegt busy: zo lang opnieuw proberen
START_RETRY_S = 0.1
QUEUE_HIGH = 64  # stukken uit de pipe die op schrijven wachten: niet verder lezen
QUEUE_LOW = 8
TRACK_SHARE = 2  # één nummer hooguit de helft van het deel van audio/ (cache_gb / 8)
FLAC_BYTES = 3  # FLAC van ffmpeg: hooguit 24 bits per sample (verbatim), plus 2 % kop
COPY_FACTOR = 1.25  # copy (FLAC, AAC): de bron, plus de boxen van fMP4 per frame
SLACK = 4 * 1024 * 1024  # de init en de boxen van een klein nummer
IDLE_S = 30.0  # zo lang geen volger: de remux stopt

# Wat nu gemaakt wordt, per naam in de cache (gedeeld door alle sessies)
_remuxes: dict[str, Remux] = {}


class Stalled(Exception):
    """STALL_S geen bytes uit de pipe."""


class TooLarge(Exception):
    """De remux kwam boven zijn budget (review golf 4, S1)."""


def share_bytes() -> int:
    """Het deel van cache_gb voor audio/."""
    return cache.limit_bytes(admission.config().cache_gb) // CACHE_SHARE


def track_max() -> int:
    """Hooguit zoveel bytes voor één nummer."""
    return share_bytes() // TRACK_SHARE


def expected_bytes(size: int, duration_ms: int | None, stream: Any, codec: str) -> int | None:
    """Een bovengrens voor audio.mp4, of None als de probe er geen geeft. copy: de bron plus een
    kwart (de boxen van fMP4 per frame). flac: duur × rate × kanalen × 3 bytes (verbatim, de
    grens van de encoder) plus 2 % voor de koppen van de frames."""
    if codec == "copy":
        return int(size * COPY_FACTOR) + SLACK
    rate = stream.sample_rate or 0
    channels = stream.channels or 0
    if not duration_ms or duration_ms <= 0 or rate <= 0 or channels <= 0:
        return None
    pcm = duration_ms / 1000 * rate * channels * FLAC_BYTES
    return int(pcm * 1.02) + SLACK


def reserved(exclude: Remux | None = None) -> int:
    """De budgetten van de remuxen die nog lopen (hun bytes staan nog niet in trim)."""
    return sum(r.budget for r in _remuxes.values() if r is not exclude and not r.done)


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


class Remux:
    """Eén remux_audio naar de cache: eerst naar <naam>.part, dan atomair hernoemd."""

    def __init__(
        self, base: Path, name: str, sha: str, source: Path, args: RemuxAudioArgs, *,
        budget: int | None = None,
    ):  # fmt: skip
        self.base = base
        self.name = name
        self.sha = sha
        self.source = source
        self.args = args
        self.final = base / name
        self.part = base / f"{name}.part"
        self.budget = budget if budget is not None else track_max()
        self.size = 0
        self.done = False
        self.error: BaseException | None = None
        self.discarded = False
        self.followers = 0
        loop = asyncio.get_running_loop()
        self.started: asyncio.Future[None] = loop.create_future()
        # Wacht niemand meer op de start (de vraag ging weg), dan toch de fout ophalen
        self.started.add_done_callback(lambda f: f.cancelled() or f.exception())
        self.changed = asyncio.Event()
        self.task = asyncio.ensure_future(self._run())
        # Tot de eerste volger er is (de vraag wacht nog op de start), loopt de klok al
        self._idle: asyncio.TimerHandle | None = loop.call_later(IDLE_S, self._abandon)

    def join(self) -> None:
        """Een volger begint (follow)."""
        self.followers += 1
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None

    def leave(self) -> None:
        """Een volger stopt. Was het de laatste en is de remux niet af: na IDLE_S stoppen."""
        self.followers -= 1
        if self.followers <= 0 and not self.done and self.error is None and self._idle is None:
            self._idle = asyncio.get_running_loop().call_later(IDLE_S, self._abandon)

    def _abandon(self) -> None:
        self._idle = None
        if self.followers <= 0 and not self.done and not self.task.done():
            log.info("remux_audio gestopt: niemand leest meer")
            self.task.cancel()

    def _signal(self) -> None:
        """Wek wie wacht (die hield het oude Event vast) en begin een nieuw."""
        old, self.changed = self.changed, asyncio.Event()
        old.set()

    def _open(self) -> int:
        self.base.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC
        return os.open(self.part, flags, 0o600)

    async def _call(self) -> tuple[client.Job, list[int]]:
        end = asyncio.get_running_loop().time() + START_BUSY_S
        while True:
            try:
                return await client.call(Op.REMUX_AUDIO, self.args, source=self.source, outputs=1)
            except client.WorkerBusy as exc:
                if exc.work_class != WorkClass.REMUX:
                    raise
                if asyncio.get_running_loop().time() >= end:
                    raise
                await asyncio.sleep(START_RETRY_S)

    async def _run(self) -> None:
        out = -1
        job: client.Job | None = None
        transport: asyncio.BaseTransport | None = None
        try:
            # Kwam het bestand er intussen toch (een remux die net klaar was): dat serveren
            ready = await asyncio.to_thread(_size_of, self.final)
            if ready is not None:
                self.size = ready
                self.done = True
                if not self.started.done():
                    self.started.set_result(None)
                return
            await self._room()
            out = await asyncio.to_thread(self._open)
            job, reads = await self._call()
            pipe = os.fdopen(reads[0], "rb", buffering=0)
            queue: asyncio.Queue[bytes | None] = asyncio.Queue()
            loop = asyncio.get_running_loop()
            try:
                transport, protocol = await loop.connect_read_pipe(lambda: _Pipe(queue), pipe)
            except BaseException:
                pipe.close()
                raise
            if not self.started.done():
                self.started.set_result(None)
            result = asyncio.ensure_future(job.result())
            try:
                await self._copy(queue, protocol, out)
                await result
            finally:
                if not result.done():
                    result.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await result
            await asyncio.to_thread(os.close, out)
            out = -1
            if self.discarded:
                raise FileNotFoundError("de inhoud is weg")
            await asyncio.to_thread(os.replace, self.part, self.final)
            self.done = True
            self._signal()
            await self._trim()
        except BaseException as exc:
            self.error = exc
            if not self.started.done():
                self.started.set_exception(exc)
            if not isinstance(exc, FileNotFoundError):
                log.warning("remux_audio mislukt: %s", exc)
            if job is not None:
                with contextlib.suppress(Exception):
                    await job.cancel()
            with contextlib.suppress(OSError):
                await asyncio.to_thread(self.part.unlink, missing_ok=True)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            if self._idle is not None:
                self._idle.cancel()
                self._idle = None
            if transport is not None:
                transport.close()
            if out >= 0:
                with contextlib.suppress(OSError):
                    os.close(out)
            if _remuxes.get(self.name) is self:
                del _remuxes[self.name]
            self._signal()

    async def _copy(self, queue: asyncio.Queue[bytes | None], protocol: _Pipe, out: int) -> None:
        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), STALL_S)
            except TimeoutError:
                raise Stalled(f"{STALL_S:g} s geen bytes") from None
            if chunk is None:
                return
            parts = [chunk]
            ended = False
            while not queue.empty():
                more = queue.get_nowait()
                if more is None:
                    ended = True
                    break
                parts.append(more)
            data = b"".join(parts)
            if self.size + len(data) > self.budget:
                raise TooLarge(f"meer dan {self.budget} bytes")
            await asyncio.to_thread(_write_all, out, data)
            self.size += len(data)
            protocol.drained()
            self._signal()
            if ended:
                return

    async def _room(self) -> None:
        """Vóór de start: de oudste weg tot alles wat loopt (ook deze) erbij past."""
        try:
            limit = max(0, share_bytes() - reserved())
            await asyncio.to_thread(trim, self.base, limit, set())
        except Exception:
            log.exception("audio/ opruimen")

    async def _trim(self) -> None:
        try:
            limit = max(0, share_bytes() - reserved(exclude=self))
            await asyncio.to_thread(trim, self.base, limit, {self.name})
        except Exception:
            log.exception("audio/ opruimen")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def trim(base: Path, limit: int, keep: set[str]) -> int:
    """De oudste bestanden van audio/ weg tot het samen onder limit blijft (niet keep, niet wat
    nog gemaakt wordt). Een .part zonder remux in dit proces (van vóór een crash of een stop)
    gaat altijd weg. Geeft het aantal verwijderde bestanden."""
    entries = []
    removed = 0
    try:
        listing = list(os.scandir(base))
    except OSError:
        return 0
    for entry in listing:
        if entry.name.endswith(".mp4.part"):
            if entry.name.removesuffix(".part") not in _remuxes:
                with contextlib.suppress(OSError):
                    (base / entry.name).unlink()
                    removed += 1
            continue
        if not entry.name.endswith(".mp4"):
            continue
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        entries.append((st.st_mtime, entry.name, st.st_size))
    total = sum(size for *_, size in entries)
    for _, name, size in sorted(entries):
        if total <= limit:
            break
        if name in keep or name in _remuxes:
            continue
        with contextlib.suppress(OSError):
            (base / name).unlink()
            removed += 1
            total -= size
    return removed


def _remove_sha(base: Path, sha: str) -> None:
    try:
        listing = list(os.scandir(base))
    except OSError:
        return
    for entry in listing:
        if entry.name.startswith(f"{sha}.") and entry.name.endswith((".mp4", ".mp4.part")):
            with contextlib.suppress(OSError):
                (base / entry.name).unlink()


async def discard(sha: str) -> None:
    """De haak van previews.discard: de cache van deze inhoud weg."""
    for remux in list(_remuxes.values()):
        if remux.sha == sha:
            remux.discarded = True
    base = runtime.settings().media_dir / "audio"
    await asyncio.to_thread(_remove_sha, base, sha)


hooks.register_discard(discard)


def reset() -> None:
    """Voor tests: wat nog loopt, stoppen."""
    for remux in list(_remuxes.values()):
        remux.task.cancel()
    _remuxes.clear()


def _position(session: sessions.Session) -> int:
    probe = session.probe
    assert probe is not None
    streams = probe.audio_streams()
    for i, stream in enumerate(streams):
        if stream.index == session.audio_index:
            return i
    return 0


def _size_of(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _stat(path: Path) -> int | None:
    try:
        st = path.stat()
    except OSError:
        return None
    with contextlib.suppress(OSError):
        os.utime(path)  # zo gaat trim eerst wat lang niet gevraagd werd
    return st.st_size


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    tags = [t.strip() for t in header.split(",")]
    return "*" in tags or etag in tags or f"W/{etag}" in tags


def _file(
    session: sessions.Session,
    path: Path,
    size: int,
    etag: str,
    range_: str | None,
    if_range: str | None,
    if_none_match: str | None,
) -> Response:
    headers = {"ETag": etag, "Cache-Control": FILE_CACHE, "Accept-Ranges": "bytes"}
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    status = 200
    start, end = 0, size - 1
    if if_range is None or if_range.strip() == etag:
        found = byte_range(range_, size)
        if found is False:
            headers["Content-Range"] = f"bytes */{size}"
            return Response(status_code=416, headers=headers)
        if found:
            start, end = found
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(max(0, end - start + 1))
    body = sessions.guarded_body(session, read_range(path, start, end))
    return StreamingResponse(body, status_code=status, headers=headers, media_type=MIME)


async def _wait(app: Any, awaitable: Any) -> Any:
    return await runtime.until_stopped(app, awaitable)


def _open_growing(remux: Remux) -> int:
    for path in (remux.part, remux.final):
        try:
            return os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        except FileNotFoundError:
            continue
    raise FileNotFoundError(remux.name)


async def follow(app: Any, remux: Remux) -> AsyncIterator[bytes]:
    """De bytes van een bestand dat nog groeit, tot de remux klaar is. Mislukt hij, of stopt de
    app, dan breekt de respons af (StreamCut)."""
    remux.join()
    try:
        fd = await asyncio.to_thread(_open_growing, remux)
    except OSError:
        remux.leave()
        raise sessions.StreamCut("failed") from None
    offset = 0
    try:
        while True:
            changed = remux.changed
            if offset < remux.size:
                want = min(PIECE, remux.size - offset)
                chunk = await asyncio.to_thread(os.pread, fd, want, offset)
                if not chunk:
                    raise sessions.StreamCut("failed")
                offset += len(chunk)
                yield chunk
                continue
            if remux.error is not None:
                raise sessions.StreamCut("failed")
            if remux.done:
                return
            try:
                await _wait(app, changed.wait())
            except HTTPException:
                raise sessions.StreamCut("stopping") from None
    finally:
        os.close(fd)
        remux.leave()


def _budget(session: sessions.Session, stream: Any, codec: str) -> int:
    """Het budget van een nieuwe remux; 415 too_large als één nummer niet in de cache past."""
    probe = session.probe
    duration = stream.duration_ms or (probe.duration_ms if probe is not None else None)
    limit = track_max()
    expected = expected_bytes(session.size, duration, stream, codec)
    if expected is None:
        return limit  # geen grens uit de probe: dan de grens van één nummer
    if expected > limit:
        raise media_errors.unsupported(["too_large"])
    return expected


@router.get("/{sid}/audio.mp4")
async def audio(
    request: Request,
    session: sessions.Guarded,
    range_: Annotated[str | None, Header(alias="range")] = None,
    if_range: Annotated[str | None, Header()] = None,
    if_none_match: Annotated[str | None, Header()] = None,
):
    probe = session.probe
    if (
        session.plan.mode != "audio"
        or session.path is None
        or probe is None
        or session.settings is None
        or not probe.audio_streams()
    ):
        raise errors.fail(404, **NOT_FOUND)
    position = _position(session)
    demuxer = demuxer_of(probe.container)
    if demuxer is None:
        raise media_errors.unavailable("probe")
    stream = probe.audio_streams()[position]
    codec = "copy" if (stream.codec or "") in COPY_CODECS else "flac"
    base = session.settings.media_dir / "audio"
    name = f"{session.sha}.mp4" if position == 0 else f"{session.sha}.{position}.mp4"
    etag = f'"{session.sha}.a{position}"'
    remux = _remuxes.get(name)
    if remux is None:
        size = await asyncio.to_thread(_stat, base / name)
        if size is not None:
            return _file(session, base / name, size, etag, range_, if_range, if_none_match)
        budget = _budget(session, stream, codec)
        free = await asyncio.to_thread(admission.disk_free, session.settings.data_dir)
        # Een ander verzoek kan de remux tijdens de awaits hierboven begonnen zijn (C1): dan
        # die volgen. Vanaf hier geen await meer tot hij in _remuxes staat.
        remux = _remuxes.get(name)
        if remux is None:
            if free - budget < admission.DISK_MIN or reserved() + budget > share_bytes():
                raise media_errors.busy("disk", admission.RETRY)
            args = RemuxAudioArgs(demuxer=demuxer, audio=position, codec=codec)  # type: ignore[arg-type]
            remux = Remux(base, name, session.sha, session.path, args, budget=budget)
            _remuxes[name] = remux
    try:
        await _wait(request.app, asyncio.shield(remux.started))
    except HTTPException:
        raise
    except Exception as exc:
        raise media_errors.from_worker(exc) from None
    headers = {"Cache-Control": FILE_CACHE}
    body = sessions.guarded_body(session, follow(request.app, remux))
    return StreamingResponse(body, status_code=200, headers=headers, media_type=MIME)
