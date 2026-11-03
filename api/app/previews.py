"""Previews (thumbnails) van bestanden, gemaakt als achtergrondtaak.

Eén preview per blob: dezelfde inhoud krijgt er maar één, ook als hij in tien mappen of bij
verschillende gebruikers staat. Hij staat naast de blobs, in previews/<sha[:2]>/<sha>/, als
256.webp (thumbnail) en 1024.webp (groot), voor zover dat zin heeft: nooit groter dan het
origineel; HEIC, HEIF, TIFF, PSD, JPEG 2000 en camera-RAW krijgen ook 2048.webp (DISPLAY: de
browser kan het origineel niet tonen). Een kantoordocument krijgt doc.json (de inhoud voor de
viewer, app/office/reader.py), en de viewer zet gerenderde PDF-pagina's in pages/
(routers/viewer.py). Wat er verder uit komt (afmetingen, duur, eerste regels tekst, inhoud van
een archief, codecs) staat in Blob.preview_meta. Lijsten krijgen daarvan alleen het lichte deel
(info), de viewer alles (info met full=True, via GET /files/{id}/preview).

RENDER_VERSION gaat omhoog als previews van bestaande inhoud opnieuw gemaakt moeten worden;
bij het starten loopt dan een keten van taken (preview.outdated) door alle blobs met een
lagere Blob.render_version.

Het maken zelf gebeurt in een eigen proces (app/render.py) met een tijds- en geheugengrens:
een kapot of kwaadaardig bestand kan de server zo niet laten crashen of vastlopen. Zolang de app
draait, maakt een voorgeladen forkserver die processen (Forkserver): elk bestand blijft een
eigen proces, maar zonder elke keer Python en Pillow op te starten.

Foto's in twee rondes (na een grote import eerst overal een thumbnail): de taak preview maakt
van een JPEG (of RAW, of HEIC met ingebedde thumbnail) alleen 256.webp, met de opnamedatum en
de rest van de metadata, en zet preview.large klaar voor 1024 (en 2048). preview.large komt in
de rij preview pas als er geen thumbnails meer wachten (rank). Tot dan staat "partial" in
preview_meta en valt de viewer terug op de thumbnail. Wat iemand nu bekijkt, gaat voor
(to_front: de tijdlijn, een map, de viewer).

Niets blijft stil zonder preview: een video die niet kon omdat de mediaworker geen ffmpeg had,
komt terug zodra hij gezond is (enqueue_later); wat mislukte of geen preview kreeg, komt terug
met recheck ("alles nalopen" op /beheer en app.cli previews-recheck), net als een preview
waarvan het bestand op schijf ontbreekt (dat ziet heal ook bij het opvragen); en dezelfde inhoud
nog eens uploaden probeert een mislukte opnieuw (files.add_ref).

Adressen van previews zijn ondertekend (HMAC met het geheim van de app) en verlopen, zodat
een <img> ze zonder Authorization-header kan laden.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import signal
import sys
import tempfile
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
from fastapi import BackgroundTasks
from sqlalchemy import func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import access, events, jobs, photodate
from app.config import Settings
from app.db import get_engine
from app.media import hooks as media_hooks
from app.mime import read_head, sniff
from app.models import Blob, Job, Node
from app.office import convert
from app.render import ERR, OUT
from app.storage import BlobStore

log = logging.getLogger("platform.previews")

TYPE = "preview"
LARGE_TYPE = "preview.large"  # de tweede ronde van een foto: 1024 en 2048
RECHECK = "preview.recheck"
RECHECK_KEY = "preview:recheck"
RECHECK_BATCH = 500
LANE = "preview"
THUMB, LARGE, DISPLAY = 256, 1024, 2048
SIZES = (THUMB, LARGE, DISPLAY)
# Omhoog als bestaande previews opnieuw moeten. 1: kantoordocumenten (doc.json) en 2048 px
# voor HEIC, HEIF en TIFF (fase 3). 2: foto's boven 100 MP, afgekapte JPEG's, camera-RAW, PSD
# en JPEG 2000 (alles wat eerder geen preview kreeg, en TIFF: daar zit RAW in)
RENDER_VERSION = 2
# Zoveel stappen lager draait het renderproces van achtergrondwerk (os.nice): de API en het
# afspelen gaan voor (die van media draaien op 15, live transcoderen op 0)
NICE = 10
OUTDATED = "preview.outdated"
OUTDATED_KEY = "preview:outdated"
OUTDATED_BATCH = 200
# Opnieuw maken wacht even: nieuwe uploads gaan voor
RERENDER_DELAY = 60
# Wat een lijst van de metadata krijgt; tekst, archiefinhoud, codecs en EXIF alleen de viewer
LIGHT = ("width", "height", "duration", "pages", "animated", "sizes")
# Nooit naar buiten, ook niet als een oudere versie het ooit bewaarde (locatie: fase 6)
SECRET_META = ("gps", "location", "latitude", "longitude")
DOC = "doc.json"
SHA = re.compile(r"[0-9a-f]{64}")
DONE = ("ready", "none")
# Een adres blijft hetzelfde tot de volgende grens van zijn termijn, en werkt daarna nog zo lang:
# zo kan de browser de afbeelding cachen. Het is ondertekend op de inhoud, niet op de
# gebruiker: na een ingetrokken deling werkt een uitgegeven adres dus nog hooguit twee termijnen
# (docs/fase-3/p4.md). In de app settings.preview_url_hours (url_period), standaard 6 uur zoals
# sinds fase 3, en nooit korter (URL_PERIOD); achter een publieke link altijd 6 uur
# (PUBLIC_PERIOD).
URL_PERIOD = 6 * 3600
PUBLIC_PERIOD = 6 * 3600
OUTPUT_BYTES = 1024 * 1024
ERROR_BYTES = 64 * 1024
BATCH = 500

# Het proces voor één preview: zonder omgevingsvariabelen van de app (geen geheimen), met -I
# (niets uit de werkmap importeren) en in een eigen procesgroep, zodat stoppen ook ffmpeg raakt
CHILD = "import sys; sys.path.insert(0, sys.argv[1]); from app.render import main; main()"
# De forkserver (render.serve) die die processen maakt zolang de app draait
SERVER = "import sys; sys.path.insert(0, sys.argv[1]); from app.render import serve; serve()"
CODE_ROOT = str(Path(__file__).resolve().parent.parent)
# Zo lang wachten op een kind dat net is doodgemaakt (de forkserver ruimt het op)
REAP_SECONDS = 10


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def key(sha: str) -> str:
    return f"{TYPE}:{sha}"


def large_key(sha: str) -> str:
    return f"{LARGE_TYPE}:{sha}"


async def queue(session: AsyncSession, sha: str) -> None:
    """Een preview laten maken van nieuwe inhoud, in de transactie van de aanroeper; de
    workers beginnen zodra die commit."""
    await jobs.enqueue(session, TYPE, {"sha": sha}, key=key(sha))


AGAIN = ("failed", "none")


async def requeue(session: AsyncSession, sha: str) -> bool:
    """Dezelfde inhoud komt nog eens binnen (files.add_ref): een mislukte preview, of een foto of
    video zonder preview, krijgt een nieuwe kans (misschien kan het nu wel). In de transactie
    van de aanroeper; True als er een taak bij kwam."""
    row = (await session.execute(select(Blob.preview, Blob.mime).where(Blob.sha256 == sha))).first()
    if row is None or row.preview not in AGAIN:
        return False
    if row.preview == "none" and not photodate.is_media(row.mime):
        return False  # een zip of tekst zonder preview blijft zo
    await session.execute(update(Blob).where(Blob.sha256 == sha).values(preview="pending"))
    return await jobs.enqueue(session, TYPE, {"sha": sha}, key=key(sha)) is not None


def to_front(
    background: BackgroundTasks,
    settings: Settings,
    blobs: list[tuple[str, Any, Any]],
    *,
    large: bool = False,
) -> None:
    """Wat iemand nu bekijkt, eerst: de wachtende taken van deze (sha, preview, preview_meta)
    naar voren (de tijdlijn, een map, de viewer). Zonder preview de eerste ronde; met alleen een
    thumbnail de tweede, maar alleen als large (de viewer: die toont de grote versie). Een lijst
    toont alleen thumbnails, en een naar voren gehaalde taak gaat voor alles, ook voor de
    thumbnails van een import (jobs._claim_filters). Pas na het antwoord en in een eigen sessie
    (_front): het schrijven wacht soms op de schrijflock van SQLite (een import, previews die
    klaar komen), en daar hoeft wie kijkt niet op te wachten."""
    keys = []
    for sha, state, meta in blobs:
        if not sha:
            continue
        if state in (None, "pending"):
            keys.append(key(sha))
        elif large and state == "ready" and isinstance(meta, dict) and meta.get("partial"):
            keys.append(large_key(sha))
    if keys:
        background.add_task(_front, settings, list(dict.fromkeys(keys)))


async def _front(settings: Settings, keys: list[str]) -> None:
    """jobs.to_front na het antwoord. Mislukt het, dan blijven de taken op hun plek in de rij."""
    try:
        async with _maker(settings)() as session:
            if await jobs.to_front(session, keys):
                await session.commit()
    except Exception:  # noqa: BLE001 - naar voren zetten is bijzaak
        log.warning("Taken naar voren zetten mislukte", exc_info=True)


def root(blobs: BlobStore) -> Path:
    """Previews staan naast de blobs: <data_dir>/previews."""
    return blobs.root.parent / "previews"


def directory(blobs: BlobStore, sha: str) -> Path:
    return root(blobs) / sha[:2] / sha


async def discard(blobs: BlobStore, sha: str) -> None:
    """Weg met de preview van opgeruimde inhoud. Onder BlobStore.lock, net als het opruimen
    zelf en het neerzetten van een nieuwe preview: zo blijft er nooit een achter."""
    await anyio.to_thread.run_sync(shutil.rmtree, directory(blobs, sha), True)
    await convert.on_discard(blobs, sha)  # de PDF van office erbij (O3)
    # Media (fase 5): eerst de sessies op die sha dicht, dan de cache van M4 en M5; faalt nooit
    await media_hooks.on_discard(sha)


def url_key(settings: Settings) -> bytes:
    """Eigen sleutel voor previewadressen, afgeleid van het geheim van de app."""
    return hmac.new(settings.secret().encode(), b"platform-preview-url", hashlib.sha256).digest()


def signature(secret: bytes, sha: str, size: int, exp: int, v: int = 0) -> str:
    """v is de PDF-generatie van office (Keuze 25). Zonder (v 0) is de handtekening precies
    die van fase 3, dus bestaande adressen blijven geldig."""
    message = f"{sha}/{size}/{exp}" if v == 0 else f"{sha}/{size}/{exp}/{v}"
    mac = hmac.new(secret, message.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def url_period(settings: Settings) -> int:
    """De termijn van een previewadres in de app: settings.preview_url_hours, nooit korter dan
    URL_PERIOD (de web-app vraagt adressen na 5 uur opnieuw, STALE_MS)."""
    return max(URL_PERIOD, settings.preview_url_hours * 3600)


def expiry(at: float | None = None, period: int = URL_PERIOD) -> int:
    """Naar boven afgerond op period, plus period (url_period in de app, PUBLIC_PERIOD achter
    een publieke link)."""
    t = int(time.time() if at is None else at)
    return -(-t // period) * period + period


def url(secret: bytes, sha: str, size: int, exp: int, v: int = 0) -> str:
    """Het getekende adres. v (de PDF-generatie, 0 zonder PDF) staat erin en is mee getekend:
    een nieuwe PDF geeft een nieuw adres, anders hield de browser de oude thumbnail."""
    sig = signature(secret, sha, size, exp, v)
    part = f"&v={v}" if v else ""
    return f"/api/v1/previews/{sha}/{size}.webp?exp={exp}{part}&sig={sig}"


def valid(
    secret: bytes,
    sha: str,
    size: int,
    exp: int,
    sig: str,
    at: float | None = None,
    v: int = 0,
) -> bool:
    if exp < (time.time() if at is None else at) or v < 0:
        return False
    expected = signature(secret, sha, size, exp, v).encode()
    return hmac.compare_digest(expected, sig.encode())  # in constante tijd


def doc_path(blobs: BlobStore, sha: str) -> Path:
    """De inhoud van een kantoordocument voor de viewer (OfficeDoc, hooguit 512 KB)."""
    return directory(blobs, sha) / DOC


def clean_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """preview_meta zonder locatie (GPS hoort bij fase 6), ook niet in de EXIF."""
    meta = {k: v for k, v in meta.items() if k not in SECRET_META}
    exif = meta.get("exif")
    if isinstance(exif, dict):
        meta["exif"] = {
            k: v
            for k, v in exif.items()
            if k not in SECRET_META and not k.lower().startswith("gps")
        }
    return meta


def pdf_generation(blob: Blob | None) -> int:
    """Het deel v van het adres: de pdf_version als office een PDF heeft gemaakt, anders 0."""
    if blob is None or blob.pdf_state != "ready":
        return 0
    return blob.pdf_version or 0


def info(secret: bytes, blob: Blob | None, exp: int, full: bool = False) -> dict[str, Any]:
    """Wat de web-app over de preview van deze inhoud te zien krijgt. Standaard licht (voor
    lijsten: alleen afmetingen, duur, pagina's en of het beweegt); full=True geeft alle
    metadata (zonder locatie) plus display, het adres van 2048.webp als dat er is."""
    state = (blob.preview if blob else None) or "pending"
    meta = dict((blob.preview_meta if blob else None) or {})
    sizes = meta.get("sizes", []) if state == "ready" and blob else []
    v = pdf_generation(blob)
    thumb = url(secret, blob.sha256, THUMB, exp, v) if blob and THUMB in sizes else None
    large = None
    if blob and sizes:
        large = url(secret, blob.sha256, LARGE if LARGE in sizes else THUMB, exp, v)
    if not full:
        light = {k: meta[k] for k in LIGHT if k in meta}
        return {"state": state, "thumb": thumb, "large": large, "meta": light}
    display = url(secret, blob.sha256, DISPLAY, exp, v) if blob and DISPLAY in sizes else None
    return {
        "state": state,
        "thumb": thumb,
        "large": large,
        "display": display,
        "meta": clean_meta(meta),
    }


# Video (fase 5, M1): het renderproces vraagt meta en frame aan de mediaworker. Met media aan
# krijgt het de socket en de sleutel van de worker van de app mee, alleen via stdin en alleen
# als er een video in kan zitten; met media uit start het zelf een worker (render.Media)
DEFER_MAX = 3600.0
VIDEO_UNKNOWN = ("", "application/octet-stream")  # render.kind_of snuffelt dan zelf


def _maybe_video(request: dict[str, Any]) -> bool:
    if request.get("mode") == "meta":
        items = request.get("items") or []
        return any(str(i.get("mime") or "").lower().startswith("video/") for i in items)
    if request.get("mode") or request.get("pdf"):
        return False
    mime = str(request.get("mime") or "").split(";")[0].strip().lower()
    return mime.startswith("video/") or mime in VIDEO_UNKNOWN


def _with_worker(settings: Settings, request: dict[str, Any]) -> dict[str, Any]:
    """Zoals runtime.start de client bindt: de socket van de worker, en geen sleutel als er
    een probleem mee is (dan weigert de client, en wacht de preview: jobs.Defer)."""
    if not settings.media_enabled or not _maybe_video(request):
        return request
    key = "" if settings.media_problem() else settings.media_key()
    return {**request, "worker": {"socket": str(settings.media_socket), "key": key}}


def _env(cwd: Path | str) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8", "HOME": str(cwd)}


class Forkserver:
    """Het voorgeladen renderproces (render.serve) van deze event loop. render() vraagt het om
    een kind per verzoek; dat kind is het oude renderproces (eigen sessie, kale omgeving,
    tijds- en geheugengrens, CHILD op stdin), alleen zonder elke keer Python en Pillow te
    starten. Gestart en gestopt door de lifespan (start_forkserver, stop_forkserver); daarbuiten
    (tests, de cli) start render() gewoon een eigen proces."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc
        self.loop = asyncio.get_running_loop()
        self.calls: dict[int, tuple[asyncio.Future, asyncio.Future]] = {}
        self.next = 0
        self.reader = asyncio.create_task(self._read(), name="forkserver lezen")

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None and not self.reader.done()

    async def _read(self) -> None:
        assert self.proc.stdout is not None
        try:
            while line := await self.proc.stdout.readline():
                message = json.loads(line)
                started, done = self.calls.get(message.get("id"), (None, None))
                if started is None or done is None:
                    continue
                if "pid" in message and not started.done():
                    started.set_result(int(message["pid"]))
                elif "exit" in message or "error" in message:
                    self.calls.pop(message["id"], None)
                    if not started.done():
                        started.set_result(None)
                    if not done.done():
                        done.set_result(message)
        finally:
            for started, done in self.calls.values():
                for future in (started, done):
                    if not future.done():
                        future.set_exception(ConnectionError("de forkserver is gestopt"))
            self.calls.clear()

    def submit(self, code: str, request: dict[str, Any], cwd: Path):
        """Een kind laten maken; geeft (started: pid, done: {"exit"} of {"error"})."""
        assert self.proc.stdin is not None
        self.next += 1
        ident = self.next
        started, done = self.loop.create_future(), self.loop.create_future()
        self.calls[ident] = (started, done)
        line = {"id": ident, "code": code, "cwd": str(cwd), "request": request}
        self.proc.stdin.write((json.dumps(line) + "\n").encode())
        return started, done

    async def close(self) -> None:
        if self.proc.stdin is not None and not self.proc.stdin.is_closing():
            self.proc.stdin.close()  # de server stopt bij het einde van stdin
        try:
            with anyio.fail_after(5):
                await self.proc.wait()
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()
            await self.proc.wait()
        self.reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.reader


_server: Forkserver | None = None


async def start_forkserver() -> Forkserver | None:
    """Bij het starten van de app (lifespan). Lukt het niet, dan maakt render() gewoon zelf
    een proces per preview, zoals voorheen."""
    global _server
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            SERVER,
            CODE_ROOT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd="/",
            env=_env("/"),
            start_new_session=True,
        )
    except OSError as e:
        log.warning("Forkserver voor previews start niet (%s): elk voorbeeld een eigen proces", e)
        return None
    _server = Forkserver(proc)
    return _server


async def stop_forkserver() -> None:
    global _server
    server, _server = _server, None
    if server is not None and server.loop is asyncio.get_running_loop():
        await server.close()


def _forkserver() -> Forkserver | None:
    server = _server
    if server is None or not server.alive or server.loop is not asyncio.get_running_loop():
        return None
    return server


async def _spawn(request: dict[str, Any], cwd: Path) -> tuple[int, bytes, bytes]:
    """Het renderproces als eigen proces: (exitcode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        CHILD,
        CODE_ROOT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=_env(cwd),
        start_new_session=True,
    )
    try:
        out, err = await proc.communicate(json.dumps(request).encode())
    finally:
        if proc.returncode != 0:  # nog bezig, of gecrasht: wat het startte (ffmpeg) ook weg
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            with anyio.CancelScope(shield=True):
                await proc.wait()
    assert proc.returncode is not None
    return proc.returncode, out, err


def _take(cwd: Path, name: str, limit: int) -> bytes:
    """Een uitvoerbestand van een kind van de forkserver lezen en weggooien."""
    path = cwd / name
    try:
        with open(path, "rb") as f:
            data = f.read(limit + 1)
    except FileNotFoundError:
        return b""
    path.unlink(missing_ok=True)
    return data


async def _forked(
    server: Forkserver, request: dict[str, Any], cwd: Path
) -> tuple[int, bytes, bytes]:
    """Het renderproces als kind van de forkserver: (exitcode, stdout, stderr)."""
    started, done = server.submit(CHILD, request, cwd)
    pid: int | None = None
    try:
        pid = await started
        answer = await done
    except asyncio.CancelledError:
        # Afgebroken (de tijdgrens, of de app stopt): het kind met alles wat het startte weg
        if pid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)
        with anyio.CancelScope(shield=True), anyio.move_on_after(REAP_SECONDS):
            with contextlib.suppress(ConnectionError):
                await done
        raise
    except ConnectionError:
        raise jobs.JobError("Het voorbeeldproces stopte onverwacht (forkserver weg)") from None
    if "error" in answer:
        raise jobs.JobError(f"Het voorbeeldproces startte niet: {answer['error']}")
    code = int(answer["exit"])
    if code != 0 and pid is not None:  # gecrasht: wat het startte (ffmpeg) ook weg
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
    out, err = await anyio.to_thread.run_sync(
        lambda: (_take(cwd, OUT, OUTPUT_BYTES), _take(cwd, ERR, ERROR_BYTES))
    )
    return code, out, err


async def render(settings: Settings, request: dict[str, Any], cwd: Path) -> dict[str, Any]:
    """Draait app/render.py in een eigen proces (een kind van de forkserver als die draait).
    Wordt dit afgebroken (de taak duurt te lang, of de app stopt), dan gaat het proces met alles
    wat het startte meteen dood. Is de mediaworker weg of druk (een video, fase 5), dan
    jobs.Defer: later opnieuw, de poging telt niet."""
    request = _with_worker(settings, request)
    server = _forkserver()
    if server is not None:
        code, out, err = await _forked(server, request, cwd)
    else:
        code, out, err = await _spawn(request, cwd)
    if code != 0:
        if code < 0:
            raise jobs.JobError(f"Het voorbeeldproces stopte onverwacht (signaal {-code})")
        lines = err.decode(errors="replace").strip().splitlines() or ["onbekende fout"]
        raise jobs.JobError(f"Het voorbeeldproces gaf een fout: {lines[-1]}")
    if len(out) > OUTPUT_BYTES:
        raise jobs.JobError("Het voorbeeldproces gaf een te groot antwoord")
    result = json.loads(out)
    if isinstance(result, dict) and result.get("state") == "defer":
        try:
            seconds = float(result.get("seconds") or 300)
        except (TypeError, ValueError):
            seconds = 300.0
        raise jobs.Defer(min(max(seconds, 1.0), DEFER_MAX))
    if not isinstance(result, dict) or result.get("state") not in DONE:
        raise jobs.JobError("Het voorbeeldproces gaf een onverwacht antwoord")
    return result


async def make(settings: Settings, payload: dict[str, Any]) -> None:
    """De taak: preview maken van één blob. Van een foto eerst alleen de thumbnail (render met
    first); de grote maten komen dan met preview.large (make_large)."""
    await _make(settings, payload, large=False)


async def make_large(settings: Settings, payload: dict[str, Any]) -> None:
    """De tweede ronde van een foto: alle maten (1024, en 2048 voor wat de browser niet kan
    tonen), naast de thumbnail die er al is."""
    await _make(settings, payload, large=True)


async def _make(settings: Settings, payload: dict[str, Any], large: bool) -> None:
    sha = str(payload.get("sha", ""))
    if not SHA.fullmatch(sha):
        raise jobs.JobError("Ongeldige taak: geen inhoud opgegeven")
    blobs = BlobStore(settings.blob_dir)
    maker = _maker(settings)
    async with maker() as session:
        blob = await session.get(Blob, sha)
        if blob is None:
            return  # intussen opgeruimd
        if large and not (blob.preview == "ready" and (blob.preview_meta or {}).get("partial")):
            return  # al compleet, of intussen opnieuw begonnen (dan komt er een nieuwe)
        if not large and blob.preview in DONE and (blob.render_version or 0) >= RENDER_VERSION:
            return  # al gedaan (een dubbele taak); een oudere versie wordt opnieuw gemaakt
        mime = blob.mime
        # Opnieuw maken (een oudere versie) in één keer: de oude preview blijft tot dan staan
        first = not large and blob.preview != "ready"
        name = await session.scalar(select(Node.name).where(Node.blob_id == sha).limit(1))
    tmp = root(blobs) / ".tmp"
    await anyio.Path(tmp).mkdir(parents=True, exist_ok=True)
    work = Path(await anyio.to_thread.run_sync(lambda: tempfile.mkdtemp(dir=tmp)))
    try:
        out, scratch = work / "out", work / "work"
        await anyio.Path(out).mkdir()
        await anyio.Path(scratch).mkdir()
        request = {
            "source": str(blobs.path(sha)),
            "out": str(out),
            "work": str(scratch),
            "mime": mime,
            "name": name or "",
            "max_pixels": settings.preview_max_pixels,
            "memory_mb": settings.preview_memory_mb,
            "nice": NICE,
            "first": first,
        }
        try:
            result = await render(settings, request, scratch)
        except Exception:
            async with maker() as session:
                if await session.get(Blob, sha) is None:
                    return  # de inhoud werd tijdens het maken opgeruimd: niets meer te doen
            raise
        if large:
            stored = await _store_large(maker, blobs, sha, result, out)
        else:
            stored = await _store(maker, blobs, sha, result, out, settings.timezone)
    finally:
        # Ook na de tijdslimiet of bij stoppen: dan is de taak al afgebroken, en zonder schild
        # breekt het opruimen zelf meteen mee af
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(shutil.rmtree, work, True)
    if stored is not None:
        await announce(maker, sha, dated=stored)


def _sizes(result: dict[str, Any], out: Path) -> list[int]:
    return sorted(
        s for s in result.get("sizes", []) if s in SIZES and (out / f"{s}.webp").is_file()
    )


async def _store(
    maker: async_sessionmaker[AsyncSession],
    blobs: BlobStore,
    sha: str,
    result: dict[str, Any],
    out: Path,
    tz: str,
) -> bool | None:
    """Zet de preview op zijn plek, onder hetzelfde slot als het opruimen: is de blob intussen
    weg, dan ook geen preview (anders bleef die voor altijd staan). De opnamedatum uit de
    metadata gaat via photodate naar de blob en zijn bestanden (tz: settings.timezone). Bij
    het opnieuw maken (een oudere RENDER_VERSION) blijft de oude preview staan tot hier.
    Alleen een thumbnail ("more" in het antwoord): partial in de metadata, en preview.large
    klaar in dezelfde transactie. Geeft None als de blob weg is, anders of er een bestand
    met deze inhoud een andere opnamedatum kreeg (de web-app leest de maanden dan opnieuw)."""
    sizes = _sizes(result, out)
    meta = {**(result.get("meta") or {}), "sizes": sizes}
    more = bool(result.get("more")) and result.get("state") == "ready"
    if more:
        meta["partial"] = True
    async with blobs.lock:
        async with maker() as session:
            blob = await session.get(Blob, sha)
            if blob is None:
                return None
            target = directory(blobs, sha)
            await anyio.to_thread.run_sync(shutil.rmtree, target, True)
            if sizes or await anyio.Path(out / DOC).is_file():
                await anyio.Path(target.parent).mkdir(parents=True, exist_ok=True)
                await anyio.Path(out).rename(target)
            blob.preview = result["state"]
            blob.render_version = RENDER_VERSION
            capture = photodate.split_capture(meta)
            blob.preview_meta = meta
            dated = await _moved(session, sha, photodate.captured_at(capture, tz))
            await photodate.on_blob_meta(session, blob, capture, None, tz)
            await convert.on_preview_done(session, blob)  # PDF van office nodig? (O3)
            if more:
                await jobs.enqueue(session, LARGE_TYPE, {"sha": sha}, key=large_key(sha))
            await session.commit()
    return dated


async def _moved(session: AsyncSession, sha: str, taken: Any) -> bool:
    """Komt een bestand met deze inhoud door de opnamedatum uit de inhoud (taken, nog vóór
    photodate.on_blob_meta) op een andere dag op de tijdlijn?"""
    if taken is None:
        return False
    return bool(
        await session.scalar(
            select(func.count())
            .select_from(Node)
            .where(
                Node.blob_id == sha,
                Node.taken_day.is_not(None),
                Node.taken_day != photodate.day_of(taken),
            )
        )
    )


async def _store_large(
    maker: async_sessionmaker[AsyncSession],
    blobs: BlobStore,
    sha: str,
    result: dict[str, Any],
    out: Path,
) -> bool | None:
    """De tweede ronde: de grote maten erbij, bestand voor bestand (de thumbnail blijft zo
    steeds te laden), en partial weg. De metadata had de eerste ronde al."""
    sizes = _sizes(result, out)
    async with blobs.lock:
        async with maker() as session:
            blob = await session.get(Blob, sha)
            if blob is None:
                return None
            meta = dict(blob.preview_meta or {})
            if blob.preview != "ready" or not meta.get("partial"):
                return None  # intussen opnieuw begonnen: dat wint
            target = directory(blobs, sha)
            await anyio.Path(target).mkdir(parents=True, exist_ok=True)

            def place() -> None:
                for size in sizes:
                    if size == THUMB and (target / f"{THUMB}.webp").is_file():
                        continue  # die van de eerste ronde blijft
                    os.replace(out / f"{size}.webp", target / f"{size}.webp")

            await anyio.to_thread.run_sync(place)
            meta.pop("partial", None)
            meta.pop("large_failed", None)
            meta["sizes"] = sorted(set(meta.get("sizes") or []) | set(sizes))
            blob.preview_meta = meta
            await session.commit()
    return False


async def announce(maker: async_sessionmaker[AsyncSession], sha: str, dated: bool = False) -> None:
    """preview.ready naar de eigenaren van bestanden met deze inhoud en naar wie er een via
    een deling kan zien, elk met alleen de bestanden die hij ziet. dated: de opnamedatum van
    die bestanden is net uit de inhoud gelezen (ze kunnen op de tijdlijn verschoven zijn)."""
    async with maker() as session:
        rows = (
            await session.execute(select(Node.owner_id, Node.id).where(Node.blob_id == sha))
        ).all()
        shared = await access.viewers_of(session, Node.blob_id == sha)
    by_user: dict[Any, dict[str, None]] = defaultdict(dict)
    for owner_id, node_id in rows:
        by_user[owner_id][str(node_id)] = None
    for user_id, node_ids in shared.items():
        by_user[user_id].update((str(n), None) for n in node_ids)
    for user_id, node_ids in by_user.items():
        data: dict[str, Any] = {"sha": sha, "node_ids": list(node_ids)}
        if dated:
            data["dated"] = True
        events.publish([user_id], "preview.ready", data)


async def mark_failed(settings: Settings, payload: dict[str, Any], _error: str) -> None:
    """Na de laatste mislukte poging: preview "failed" (op /beheer opnieuw te starten)."""
    sha = str(payload.get("sha", ""))
    if not SHA.fullmatch(sha):
        return
    maker = _maker(settings)
    async with maker() as session:
        result = await session.execute(
            update(Blob)
            .where(Blob.sha256 == sha, or_(Blob.preview.is_(None), Blob.preview.not_in(DONE)))
            .values(preview="failed")
        )
        await session.commit()
    if result.rowcount:
        await announce(maker, sha)


async def mark_large_failed(settings: Settings, payload: dict[str, Any], _error: str) -> None:
    """Na de laatste mislukte poging van de tweede ronde: de thumbnail blijft staan (partial),
    maar /beheer telt hem niet meer bij "krijgt nog een grote versie" (er wacht niets meer) en
    wel apart als mislukt (large_failed). "Alles nalopen" probeert hem opnieuw."""
    sha = str(payload.get("sha", ""))
    if not SHA.fullmatch(sha):
        return
    async with _maker(settings)() as session:
        blob = await session.get(Blob, sha)
        meta = dict((blob.preview_meta if blob else None) or {})
        if blob is None or blob.preview != "ready" or not meta.get("partial"):
            return
        meta["large_failed"] = True
        blob.preview_meta = meta
        await session.commit()


async def enqueue_missing(settings: Settings) -> int:
    """Bij het starten, vóór de workers: previews voor inhoud die er nog geen heeft (van vóór
    deze versie, of waarvan de taak verdween), de nieuwste eerst (die staan bovenaan de
    tijdlijn). Tussenbestanden van een vorige keer gaan weg."""
    blobs = BlobStore(settings.blob_dir)
    await anyio.to_thread.run_sync(shutil.rmtree, root(blobs) / ".tmp", True)
    added = 0
    async with _maker(settings)() as session:
        shas = (
            await session.scalars(
                select(Blob.sha256)
                .where(or_(Blob.preview.is_(None), Blob.preview == "pending"))
                .order_by(Blob.created_at.desc())
            )
        ).all()
        for i in range(0, len(shas), BATCH):
            batch = list(shas[i : i + BATCH])
            added += await jobs.enqueue_many(session, TYPE, [({"sha": s}, key(s)) for s in batch])
            await session.execute(
                update(Blob)
                .where(Blob.sha256.in_(batch), Blob.preview.is_(None))
                .values(preview="pending")
            )
            await session.commit()
    return added


async def _again(session: AsyncSession, shas: list[str], run_after=None) -> int:
    """Deze blobs opnieuw: preview pending en een taak (een die al klaarstaat, blijft)."""
    if not shas:
        return 0
    await session.execute(update(Blob).where(Blob.sha256.in_(shas)).values(preview="pending"))
    return await jobs.enqueue_many(
        session, TYPE, [({"sha": s}, key(s)) for s in shas], run_after=run_after
    )


async def enqueue_later(settings: Settings) -> int:
    """De video's die geen preview kregen omdat de mediaworker geen ffmpeg had (render.LATER)
    opnieuw. De housekeeping van media roept dit aan zolang de worker gezond is
    (runtime._previews_again). Geeft het aantal nieuwe taken."""
    async with _maker(settings)() as session:
        rows = (
            await session.execute(
                select(Blob.sha256, Blob.preview_meta).where(
                    Blob.preview == "none", Blob.mime.like("video/%")
                )
            )
        ).all()
        shas = [sha for sha, meta in rows if isinstance(meta, dict) and meta.get("later")]
        added = 0
        for i in range(0, len(shas), BATCH):
            added += await _again(session, shas[i : i + BATCH])
        await session.commit()
    return added


async def worker_back(settings: Settings) -> int:
    """De mediaworker is (weer) bereikbaar (media/health.py): de previews van video's die op
    hem wachtten (jobs.Defer, vijf minuten als hij weg was: render.DEFER_DOWN), nu meteen. Bij
    het starten kan een preview van een video al lopen voordat de worker luistert. Geeft het
    aantal taken."""
    at = jobs.now()
    prefix = f"{TYPE}:"
    moved = 0
    async with _maker(settings)() as session:
        rows = (
            await session.execute(
                select(Job.id, Job.key).where(
                    Job.type == TYPE, Job.status == "queued", Job.run_after > at
                )
            )
        ).all()
        waiting = {k.removeprefix(prefix): i for i, k in rows if k and k.startswith(prefix)}
        shas = list(waiting)
        for n in range(0, len(shas), BATCH):
            videos = await session.scalars(
                select(Blob.sha256).where(
                    Blob.sha256.in_(shas[n : n + BATCH]), Blob.mime.like("video/%")
                )
            )
            ids = [waiting[sha] for sha in videos]
            if ids:
                result = await session.execute(
                    update(Job)
                    .where(Job.id.in_(ids), Job.status == "queued")
                    .values(run_after=at)
                    .execution_options(synchronize_session=False)
                )
                moved += result.rowcount
        await session.commit()
    if moved:
        jobs.kick([jobs.lane_of(TYPE)])
    return moved


async def heal(settings: Settings, sha: str) -> bool:
    """Een preview die klaar heet, maar waarvan een bestand op schijf ontbreekt (een kapotte
    schijf, een halve back-up): opnieuw maken in plaats van voor altijd 404. Ontbreekt alleen
    een grote maat, dan blijft de thumbnail staan en komt de tweede ronde terug. Onder hetzelfde
    slot als _store, zodat wat daar net wordt neergezet niet als weg telt. True als hij opnieuw
    klaarstaat."""
    if not SHA.fullmatch(sha):
        return False
    blobs = BlobStore(settings.blob_dir)
    async with blobs.lock:
        async with _maker(settings)() as session:
            blob = await session.get(Blob, sha)
            if blob is None or blob.preview != "ready":
                return False
            meta = dict(blob.preview_meta or {})
            sizes = [s for s in meta.get("sizes") or [] if s in SIZES]
            missing = await anyio.to_thread.run_sync(_missing, blobs, sha, sizes)
            if not missing:
                return False
            if THUMB in missing:
                await _again(session, [sha])
            else:
                meta["sizes"] = [s for s in sizes if s not in missing]
                meta["partial"] = True
                blob.preview_meta = meta
                await jobs.enqueue(session, LARGE_TYPE, {"sha": sha}, key=large_key(sha))
            await session.commit()
    log.warning("Preview van %s was niet compleet op schijf; staat opnieuw klaar", sha[:12])
    return True


def needs_rerender(mime: str | None, preview: str | None = None, version: int = 0) -> bool:
    """Wordt een preview van deze soort (en toestand, gemaakt met deze render_version) anders
    met de huidige RENDER_VERSION? Vanaf 1: kantoordocumenten (nu een doc.json) en HEIC, HEIF
    en TIFF (nu 2048 px). Vanaf 2: elke foto of video zonder preview (none of failed: een foto
    boven 100 MP, een afgekapte JPEG, PSD, JPEG 2000) en camera-RAW (ook als TIFF opgeslagen).
    De rest krijgt alleen het nieuwe versienummer: video's, GIF's en tekst hebben dan nog geen
    codecs, animated of encoding, en de web-app valt daarvoor terug op wat de browser zelf ziet."""
    from app import render

    mime = (mime or "").split(";")[0].strip().lower()
    if version < 1 and (
        mime in render.OFFICE_MIMES or mime in ("image/heic", "image/heif", "image/tiff")
    ):
        return True
    if version < 2:
        if preview in AGAIN and photodate.is_media(mime):
            return True
        return mime in render.RAW_MIMES or mime == "image/tiff"
    return False


UNKNOWN = (None, "", "application/octet-stream")


async def _resniff(settings: Settings, rows: list[tuple[str, str | None]]) -> dict[str, str]:
    """Blobs zonder bekende soort (van vóór een nieuwe handtekening in app/mime.py, zoals RAF,
    ORF en RW2) opnieuw herkennen. Wordt het een foto of video, dan krijgen de blob en zijn
    bestanden die soort, en die bestanden een plek op de tijdlijn (photodate.stamp, met hun
    wijzigingsdatum). Geeft {sha: nieuwe soort} voor wat veranderde."""
    unknown = [sha for sha, kind in rows if kind in UNKNOWN]
    if not unknown:
        return {}
    blobs = BlobStore(settings.blob_dir)
    found: dict[str, str] = {}
    async with _maker(settings)() as session:
        names = dict(
            (
                await session.execute(
                    select(Node.blob_id, func.min(Node.name))
                    .where(Node.blob_id.in_(unknown))
                    .group_by(Node.blob_id)
                )
            ).all()
        )
        for sha in unknown:
            head = await anyio.to_thread.run_sync(read_head, blobs.path(sha))
            kind = sniff(head, names.get(sha) or "")
            if photodate.is_media(kind):
                found[sha] = kind
        if not found:
            return {}
        for sha, kind in found.items():
            await session.execute(update(Blob).where(Blob.sha256 == sha).values(mime=kind))
            nodes = (
                await session.scalars(
                    select(Node).where(
                        Node.blob_id == sha,
                        or_(Node.mime.is_(None), Node.mime.in_(UNKNOWN[1:])),
                    )
                )
            ).all()
            for node in nodes:
                node.mime = kind
                photodate.stamp(
                    node, blob_taken_at=None, fallback=node.modified_at, tz=settings.timezone
                )
        await session.commit()
    return found


async def enqueue_outdated(settings: Settings) -> int:
    """Bij het starten: als er inhoud is met een oudere Blob.render_version, de keten
    preview.outdated klaarzetten (key preview:outdated; een keten die al klaarstaat of loopt,
    krijgt er geen tweede naast). Geeft het aantal nieuwe taken (0 of 1)."""
    async with _maker(settings)() as session:
        outdated = await session.scalar(
            select(Blob.sha256).where(Blob.render_version < RENDER_VERSION).limit(1)
        )
        if outdated is None:
            return 0
        job = await jobs.enqueue(session, OUTDATED, {"after": ""}, key=OUTDATED_KEY)
        await session.commit()
    return 0 if job is None else 1


async def rerender_outdated(settings: Settings, payload: dict[str, Any]) -> None:
    """Eén stuk van de keten: de volgende OUTDATED_BATCH blobs (op sha, na payload.after) met
    een oudere render_version. Een onbekende soort wordt eerst opnieuw herkend (_resniff). Wat
    anders wordt, krijgt een previewtaak (na RERENDER_DELAY); de rest alleen het nieuwe
    versienummer. Daarna de volgende schakel."""
    after = str(payload.get("after") or "")
    async with _maker(settings)() as session:
        rows = (
            await session.execute(
                select(Blob.sha256, Blob.mime, Blob.preview, Blob.render_version)
                .where(Blob.render_version < RENDER_VERSION, Blob.sha256 > after)
                .order_by(Blob.sha256)
                .limit(OUTDATED_BATCH)
            )
        ).all()
    if not rows:
        return
    kinds = await _resniff(settings, [(row.sha256, row.mime) for row in rows])
    again, bump = [], []
    for sha, kind, state, version in rows:
        if needs_rerender(kinds.get(sha, kind), state, version or 0):
            again.append(sha)
        else:
            bump.append(sha)
    async with _maker(settings)() as session:
        if again:
            later = jobs.now() + timedelta(seconds=RERENDER_DELAY)
            await jobs.enqueue_many(
                session, TYPE, [({"sha": sha}, key(sha)) for sha in again], run_after=later
            )
        if bump:
            await session.execute(
                update(Blob)
                .where(Blob.sha256.in_(bump), Blob.render_version < RENDER_VERSION)
                .values(render_version=RENDER_VERSION)
            )
        if len(rows) == OUTDATED_BATCH:
            await jobs.chain(session, OUTDATED, {"after": rows[-1].sha256}, OUTDATED_KEY)
        await session.commit()


async def queue_recheck(session: AsyncSession) -> bool:
    """Alles nalopen (/beheer, app.cli previews-recheck): de keten preview.recheck klaarzetten,
    in de transactie van de aanroeper. False als er al een klaarstaat of loopt."""
    return await jobs.enqueue(session, RECHECK, {"after": ""}, key=RECHECK_KEY) is not None


async def enqueue_recheck(settings: Settings) -> bool:
    """queue_recheck met een eigen sessie (de cli)."""
    async with _maker(settings)() as session:
        added = await queue_recheck(session)
        await session.commit()
    return added


def _missing(blobs: BlobStore, sha: str, sizes: list[int]) -> list[int]:
    target = directory(blobs, sha)
    return [s for s in sizes if not (target / f"{s}.webp").is_file()]


async def recheck(settings: Settings, payload: dict[str, Any]) -> None:
    """Eén stuk van "alles nalopen": de volgende RECHECK_BATCH blobs (op sha, na payload.after).
    Opnieuw: geen preview of een die wacht zonder taak, een mislukte, een foto of video zonder
    preview (none), en een preview waarvan een bestand op schijf ontbreekt (heal). Een foto met
    alleen een thumbnail krijgt zijn tweede ronde terug. Een onbekende soort wordt eerst opnieuw
    herkend; een mislukte tweede ronde (large_failed) ook. Na het laatste stuk ook de metadata
    (photometa) en verouderde previews (enqueue_outdated)."""
    after = str(payload.get("after") or "")
    blobs = BlobStore(settings.blob_dir)
    async with _maker(settings)() as session:
        rows = (
            await session.execute(
                select(Blob.sha256, Blob.mime, Blob.preview, Blob.preview_meta)
                .where(Blob.sha256 > after)
                .order_by(Blob.sha256)
                .limit(RECHECK_BATCH)
            )
        ).all()
    kinds = await _resniff(settings, [(row.sha256, row.mime) for row in rows])
    again, large, retry, ready = [], [], [], []
    for sha, kind, state, meta in rows:
        meta = meta if isinstance(meta, dict) else {}
        if state in (None, "pending", "failed"):
            again.append(sha)
        elif state == "none" and photodate.is_media(kinds.get(sha, kind)):
            again.append(sha)
        elif state == "ready":
            if meta.get("partial"):
                large.append(sha)
                if meta.get("large_failed"):
                    retry.append(sha)
            ready.append((sha, [s for s in meta.get("sizes") or [] if s in SIZES]))
    broken = await anyio.to_thread.run_sync(
        lambda: [sha for sha, sizes in ready if _missing(blobs, sha, sizes)]
    )
    async with _maker(settings)() as session:
        await _again(session, again)
        await jobs.enqueue_many(session, LARGE_TYPE, [({"sha": s}, large_key(s)) for s in large])
        # Weer in de rij: dan komt de grote versie weer (nog), tot hij er is of weer mislukt
        for blob in (await session.scalars(select(Blob).where(Blob.sha256.in_(retry)))).all():
            meta = dict(blob.preview_meta or {})
            meta.pop("large_failed", None)
            blob.preview_meta = meta
        if len(rows) == RECHECK_BATCH:
            await jobs.chain(session, RECHECK, {"after": rows[-1].sha256}, RECHECK_KEY)
        await session.commit()
    for sha in broken:
        await heal(settings, sha)
    if len(rows) < RECHECK_BATCH:
        from app import photometa

        await photometa.enqueue_backfill(settings)
        await enqueue_outdated(settings)


async def photo_counts(session: AsyncSession) -> dict[str, int]:
    """Hoe ver de foto's en video's zijn (de statusregel op /beheer): total, ready (met alle
    maten), partial (alleen de thumbnail: de tweede ronde komt nog), large_failed (alleen de
    thumbnail: de tweede ronde mislukte), waiting (nog geen preview), failed, none (geen preview
    mogelijk) en later (een video die wacht op de mediaworker)."""
    media = or_(Blob.mime.like("image/%"), Blob.mime.like("video/%"))
    rows = (
        await session.execute(
            select(Blob.preview, func.count()).where(media).group_by(Blob.preview)
        )
    ).all()
    states = {state: count for state, count in rows}
    later = await session.scalar(
        select(func.count()).where(
            Blob.mime.like("video/%"),
            Blob.preview == "none",
            Blob.preview_meta["later"].as_boolean().is_(true()),
        )
    )
    halves = (
        await session.execute(
            select(Blob.preview_meta["large_failed"].as_boolean(), func.count())
            .where(
                media,
                Blob.preview == "ready",
                Blob.preview_meta["partial"].as_boolean().is_(true()),
            )
            .group_by(Blob.preview_meta["large_failed"].as_boolean())
        )
    ).all()
    large_failed = sum(count for gave_up, count in halves if gave_up)
    partial = sum(count for gave_up, count in halves if not gave_up)
    total = sum(states.values())
    waiting = states.get(None, 0) + states.get("pending", 0)
    return {
        "total": total,
        "ready": states.get("ready", 0) - partial - large_failed,
        "partial": partial,
        "large_failed": large_failed,
        "waiting": waiting,
        "failed": states.get("failed", 0),
        "none": states.get("none", 0) - (later or 0),
        "later": later or 0,
    }


jobs.register(TYPE, make, on_dead=mark_failed, lane=LANE)
# De tweede ronde: pas als er geen thumbnails meer wachten (rank). Mislukt hij, dan blijft de
# thumbnail gewoon staan (partial, large_failed): "alles nalopen" probeert hem opnieuw
jobs.register(LARGE_TYPE, make_large, on_dead=mark_large_failed, lane=LANE, rank=1)
jobs.register(OUTDATED, rerender_outdated)
jobs.register(RECHECK, recheck)
