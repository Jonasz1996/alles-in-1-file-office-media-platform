"""Previews (thumbnails) van bestanden, gemaakt als achtergrondtaak.

Eén preview per blob: dezelfde inhoud krijgt er maar één, ook als hij in tien mappen of bij
verschillende gebruikers staat. Hij staat naast de blobs, in previews/<sha[:2]>/<sha>/, als
256.webp (thumbnail) en 1024.webp (groot), voor zover dat zin heeft: nooit groter dan het
origineel; HEIC, HEIF en TIFF krijgen ook 2048.webp (DISPLAY: de browser kan het origineel
niet tonen). Een kantoordocument krijgt doc.json (de inhoud voor de viewer,
app/office/reader.py), en de viewer zet gerenderde PDF-pagina's in pages/ (routers/viewer.py).
Wat er verder uit komt (afmetingen, duur, eerste regels tekst, inhoud van een archief, codecs)
staat in Blob.preview_meta. Lijsten krijgen daarvan alleen het lichte deel (info), de viewer alles
(info met full=True, via GET /files/{id}/preview).

RENDER_VERSION gaat omhoog als previews van bestaande inhoud opnieuw gemaakt moeten worden;
bij het starten loopt dan een keten van taken (preview.outdated) door alle blobs met een
lagere Blob.render_version.

Het maken zelf gebeurt in een eigen proces (app/render.py) met een tijds- en geheugengrens:
een kapot of kwaadaardig bestand kan de server zo niet laten crashen of vastlopen.

Adressen van previews zijn ondertekend (HMAC met het geheim van de app) en verlopen, zodat
een <img> ze zonder Authorization-header kan laden.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
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
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import access, events, jobs, photodate
from app.config import Settings
from app.db import get_engine
from app.media import hooks as media_hooks
from app.models import Blob, Node
from app.office import convert
from app.storage import BlobStore

TYPE = "preview"
THUMB, LARGE, DISPLAY = 256, 1024, 2048
SIZES = (THUMB, LARGE, DISPLAY)
# Omhoog als bestaande previews opnieuw moeten. 1: kantoordocumenten (doc.json) en 2048 px
# voor HEIC, HEIF en TIFF (fase 3)
RENDER_VERSION = 1
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
# Een adres blijft hetzelfde tot de volgende grens van 6 uur, en werkt daarna nog 6 uur:
# zo kan de browser de afbeelding cachen
URL_PERIOD = 6 * 3600
OUTPUT_BYTES = 1024 * 1024
BATCH = 500

# Het proces voor één preview: zonder omgevingsvariabelen van de app (geen geheimen), met -I
# (niets uit de werkmap importeren) en in een eigen procesgroep, zodat stoppen ook ffmpeg raakt
CHILD = "import sys; sys.path.insert(0, sys.argv[1]); from app.render import main; main()"
CODE_ROOT = str(Path(__file__).resolve().parent.parent)


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def key(sha: str) -> str:
    return f"{TYPE}:{sha}"


async def queue(session: AsyncSession, sha: str) -> None:
    """Een preview laten maken van nieuwe inhoud, in de transactie van de aanroeper; de
    workers beginnen zodra die commit."""
    await jobs.enqueue(session, TYPE, {"sha": sha}, key=key(sha))


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


def expiry(at: float | None = None) -> int:
    """Naar boven afgerond op 6 uur, plus 6 uur."""
    t = int(time.time() if at is None else at)
    return -(-t // URL_PERIOD) * URL_PERIOD + URL_PERIOD


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


async def render(settings: Settings, request: dict[str, Any], cwd: Path) -> dict[str, Any]:
    """Draait app/render.py in een eigen proces. Wordt dit afgebroken (de taak duurt te lang,
    of de app stopt), dan gaat het proces met alles wat het startte meteen dood. Is de
    mediaworker weg of druk (een video, fase 5), dan jobs.Defer: later opnieuw, de poging telt
    niet."""
    request = _with_worker(settings, request)
    env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8", "HOME": str(cwd)}
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
        env=env,
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
    if proc.returncode != 0:
        if proc.returncode < 0:
            raise jobs.JobError(
                f"Het voorbeeldproces stopte onverwacht (signaal {-proc.returncode})"
            )
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
    """De taak: preview maken van één blob."""
    sha = str(payload.get("sha", ""))
    if not SHA.fullmatch(sha):
        raise jobs.JobError("Ongeldige taak: geen inhoud opgegeven")
    blobs = BlobStore(settings.blob_dir)
    maker = _maker(settings)
    async with maker() as session:
        blob = await session.get(Blob, sha)
        if blob is None:
            return  # intussen opgeruimd
        if blob.preview in DONE and (blob.render_version or 0) >= RENDER_VERSION:
            return  # al gedaan (een dubbele taak); een oudere versie wordt opnieuw gemaakt
        mime = blob.mime
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
        }
        try:
            result = await render(settings, request, scratch)
        except Exception:
            async with maker() as session:
                if await session.get(Blob, sha) is None:
                    return  # de inhoud werd tijdens het maken opgeruimd: niets meer te doen
            raise
        stored = await _store(maker, blobs, sha, result, out, settings.timezone)
    finally:
        # Ook na de tijdslimiet of bij stoppen: dan is de taak al afgebroken, en zonder schild
        # breekt het opruimen zelf meteen mee af
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(shutil.rmtree, work, True)
    if stored:
        await announce(maker, sha)


async def _store(
    maker: async_sessionmaker[AsyncSession],
    blobs: BlobStore,
    sha: str,
    result: dict[str, Any],
    out: Path,
    tz: str,
) -> bool:
    """Zet de preview op zijn plek, onder hetzelfde slot als het opruimen: is de blob intussen
    weg, dan ook geen preview (anders bleef die voor altijd staan). De opnamedatum uit de
    metadata gaat via photodate naar de blob en zijn bestanden (tz: settings.timezone). Bij
    het opnieuw maken (een oudere RENDER_VERSION) blijft de oude preview staan tot hier."""
    sizes = sorted(
        s for s in result.get("sizes", []) if s in SIZES and (out / f"{s}.webp").is_file()
    )
    meta = {**(result.get("meta") or {}), "sizes": sizes}
    async with blobs.lock:
        async with maker() as session:
            blob = await session.get(Blob, sha)
            if blob is None:
                return False
            target = directory(blobs, sha)
            await anyio.to_thread.run_sync(shutil.rmtree, target, True)
            if sizes or await anyio.Path(out / DOC).is_file():
                await anyio.Path(target.parent).mkdir(parents=True, exist_ok=True)
                await anyio.Path(out).rename(target)
            blob.preview = result["state"]
            blob.render_version = RENDER_VERSION
            capture = photodate.split_capture(meta)
            blob.preview_meta = meta
            await photodate.on_blob_meta(session, blob, capture, None, tz)
            await convert.on_preview_done(session, blob)  # PDF van office nodig? (O3)
            await session.commit()
    return True


async def announce(maker: async_sessionmaker[AsyncSession], sha: str) -> None:
    """preview.ready naar de eigenaren van bestanden met deze inhoud en naar wie er een via
    een deling kan zien, elk met alleen de bestanden die hij ziet."""
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
        events.publish([user_id], "preview.ready", {"sha": sha, "node_ids": list(node_ids)})


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


async def enqueue_missing(settings: Settings) -> int:
    """Bij het starten, vóór de workers: previews voor inhoud die er nog geen heeft (van vóór
    deze versie, of waarvan de taak verdween). Tussenbestanden van een vorige keer gaan weg."""
    blobs = BlobStore(settings.blob_dir)
    await anyio.to_thread.run_sync(shutil.rmtree, root(blobs) / ".tmp", True)
    added = 0
    async with _maker(settings)() as session:
        shas = (
            await session.scalars(
                select(Blob.sha256)
                .where(or_(Blob.preview.is_(None), Blob.preview == "pending"))
                .order_by(Blob.created_at)
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


def needs_rerender(mime: str | None) -> bool:
    """Wordt een preview van deze soort anders met de huidige RENDER_VERSION? Kantoordocumenten
    (nu een doc.json) en HEIC, HEIF en TIFF (nu 2048 px). De rest krijgt alleen het nieuwe
    versienummer: video's, GIF's en tekst hebben dan nog geen codecs, animated of encoding,
    en de web-app valt daarvoor terug op wat de browser zelf ziet."""
    from app import render

    mime = (mime or "").split(";")[0].strip().lower()
    return mime in render.OFFICE_MIMES or mime in ("image/heic", "image/heif", "image/tiff")


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
    een oudere render_version. Wat anders wordt, krijgt een previewtaak (na RERENDER_DELAY);
    de rest alleen het nieuwe versienummer. Daarna de volgende schakel."""
    after = str(payload.get("after") or "")
    async with _maker(settings)() as session:
        rows = (
            await session.execute(
                select(Blob.sha256, Blob.mime)
                .where(Blob.render_version < RENDER_VERSION, Blob.sha256 > after)
                .order_by(Blob.sha256)
                .limit(OUTDATED_BATCH)
            )
        ).all()
        if not rows:
            return
        again = [sha for sha, mime in rows if needs_rerender(mime)]
        bump = [sha for sha, mime in rows if not needs_rerender(mime)]
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


jobs.register(TYPE, make, on_dead=mark_failed)
jobs.register(OUTDATED, rerender_outdated)
