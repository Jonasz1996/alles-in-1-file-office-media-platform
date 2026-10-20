"""Previews (thumbnails) van bestanden, gemaakt als achtergrondtaak.

Eén preview per blob: dezelfde inhoud krijgt er maar één, ook als hij in tien mappen of bij
verschillende gebruikers staat. Hij staat naast de blobs, in previews/<sha[:2]>/<sha>/, als
256.webp (thumbnail) en 1024.webp (groot), voor zover dat zin heeft: nooit groter dan het
origineel. Wat er verder uit komt (afmetingen, duur, eerste regels tekst, inhoud van een
archief) staat in Blob.preview_meta.

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
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import access, events, jobs
from app.config import Settings
from app.db import get_engine
from app.models import Blob, Node
from app.storage import BlobStore

TYPE = "preview"
THUMB, LARGE = 256, 1024
SIZES = (THUMB, LARGE)
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


def url_key(settings: Settings) -> bytes:
    """Eigen sleutel voor previewadressen, afgeleid van het geheim van de app."""
    return hmac.new(settings.secret().encode(), b"platform-preview-url", hashlib.sha256).digest()


def signature(secret: bytes, sha: str, size: int, exp: int) -> str:
    mac = hmac.new(secret, f"{sha}/{size}/{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def expiry(at: float | None = None) -> int:
    """Naar boven afgerond op 6 uur, plus 6 uur."""
    t = int(time.time() if at is None else at)
    return -(-t // URL_PERIOD) * URL_PERIOD + URL_PERIOD


def url(secret: bytes, sha: str, size: int, exp: int) -> str:
    return f"/api/v1/previews/{sha}/{size}.webp?exp={exp}&sig={signature(secret, sha, size, exp)}"


def valid(secret: bytes, sha: str, size: int, exp: int, sig: str, at: float | None = None) -> bool:
    if exp < (time.time() if at is None else at):
        return False
    expected = signature(secret, sha, size, exp).encode()
    return hmac.compare_digest(expected, sig.encode())  # in constante tijd


def info(secret: bytes, blob: Blob | None, exp: int) -> dict[str, Any]:
    """Wat de web-app over de preview van deze inhoud te zien krijgt."""
    state = (blob.preview if blob else None) or "pending"
    meta = dict((blob.preview_meta if blob else None) or {})
    sizes = meta.get("sizes", []) if state == "ready" and blob else []
    thumb = url(secret, blob.sha256, THUMB, exp) if blob and THUMB in sizes else None
    large = None
    if blob and sizes:
        large = url(secret, blob.sha256, LARGE if LARGE in sizes else THUMB, exp)
    return {"state": state, "thumb": thumb, "large": large, "meta": meta}


async def render(settings: Settings, request: dict[str, Any], cwd: Path) -> dict[str, Any]:
    """Draait app/render.py in een eigen proces. Wordt dit afgebroken (de taak duurt te lang,
    of de app stopt), dan gaat het proces met alles wat het startte meteen dood."""
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
        if blob is None or blob.preview in DONE:
            return  # intussen opgeruimd, of al gedaan (een dubbele taak)
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
        stored = await _store(maker, blobs, sha, result, out)
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
) -> bool:
    """Zet de preview op zijn plek, onder hetzelfde slot als het opruimen: is de blob intussen
    weg, dan ook geen preview (anders bleef die voor altijd staan)."""
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
            if sizes:
                await anyio.Path(target.parent).mkdir(parents=True, exist_ok=True)
                await anyio.Path(out).rename(target)
            blob.preview = result["state"]
            blob.preview_meta = meta
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


jobs.register(TYPE, make, on_dead=mark_failed)
