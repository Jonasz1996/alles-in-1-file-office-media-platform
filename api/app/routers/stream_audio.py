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

# Wat nu gemaakt wordt, per naam in de cache (gedeeld door alle sessies)
_remuxes: dict[str, Remux] = {}


class Stalled(Exception):
    """STALL_S geen bytes uit de pipe."""


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

    def __init__(self, base: Path, name: str, sha: str, source: Path, args: RemuxAudioArgs):
        self.base = base
        self.name = name
        self.sha = sha
        self.source = source
        self.args = args
        self.final = base / name
        self.part = base / f"{name}.part"
        self.size = 0
        self.done = False
        self.error: BaseException | None = None
        self.discarded = False
        loop = asyncio.get_running_loop()
        self.started: asyncio.Future[None] = loop.create_future()
        # Wacht niemand meer op de start (de vraag ging weg), dan toch de fout ophalen
        self.started.add_done_callback(lambda f: f.cancelled() or f.exception())
        self.changed = asyncio.Event()
        self.task = asyncio.ensure_future(self._run())

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
            await asyncio.to_thread(_write_all, out, data)
            self.size += len(data)
            protocol.drained()
            self._signal()
            if ended:
                return

    async def _trim(self) -> None:
        try:
            limit = cache.limit_bytes(admission.config().cache_gb) // CACHE_SHARE
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
    nog gemaakt wordt). Geeft het aantal verwijderde bestanden."""
    entries = []
    try:
        listing = list(os.scandir(base))
    except OSError:
        return 0
    for entry in listing:
        if not entry.name.endswith(".mp4"):
            continue
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        entries.append((st.st_mtime, entry.name, st.st_size))
    total = sum(size for *_, size in entries)
    removed = 0
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
    try:
        fd = await asyncio.to_thread(_open_growing, remux)
    except OSError:
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
        args = RemuxAudioArgs(demuxer=demuxer, audio=position, codec=codec)  # type: ignore[arg-type]
        remux = Remux(base, name, session.sha, session.path, args)
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
