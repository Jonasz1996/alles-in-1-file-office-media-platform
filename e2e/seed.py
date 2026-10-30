"""Testdata rechtstreeks in de SQLite van een gestopte server (P0, frozen na P0).

Snel genoeg voor 10.000 of 50.000 rijen in een paar seconden: geen API, geen uploads, geen
preview-taken. Wat erin komt, ziet eruit zoals de app het zelf zou neerzetten:

- make_blobs(data_dir, n): n verschillende kleine afbeeldingen (WebP) als blob, met previews
  van 256 px (allemaal) en 1024 px (de eerste 50) in previews/<sha[:2]>/<sha>/, preview
  "ready" en preview_meta {"width", "height", "sizes"} zoals app.previews._store dat schrijft;
- seed_folder(data_dir, owner, n, blobs): een map met n rijen, om de beurt over de blobs, met
  search_name, 5 % mappen, 50 % afbeeldingen en de rest andere bestanden; ref_count klopt;
- seed_public(data_dir, owner, blobs): een map met 200 items (50 afbeeldingen) en een
  publieke leeslink zonder wachtwoord; geeft "/s/<token>".

Gebruik via de fixture seeded(fn) (die stopt de server eerst en start hem daarna weer), of
Server.seeded(fn) uit conftest.py.
"""

from __future__ import annotations

import colorsys
import contextlib
import hashlib
import io
import secrets
import sys
import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from sqlalchemy import bindparam, create_engine, event, select, update
from sqlalchemy.engine import Connection

# app.* komt uit api/ (ook als dit los wordt geïmporteerd, zonder conftest)
_API = Path(__file__).resolve().parents[1] / "api"
if str(_API) not in sys.path:
    sys.path.insert(0, str(_API))

from app import photodate, previews, render_meta  # noqa: E402
from app.config import Settings  # noqa: E402
from app.models import Blob, Node, Share, User  # noqa: E402
from app.previews import directory  # noqa: E402
from app.storage import BlobStore  # noqa: E402
from app.text import fold  # noqa: E402

LARGE_COUNT = 50  # zoveel blobs krijgen ook een preview van 1024 px
BATCH = 1000
FOLDER_SHARE = 20  # 1 op de 20 rijen is een map (5 %)


@contextlib.contextmanager
def connect(data_dir: Path) -> Iterator[Connection]:
    """Eén transactie op de database van een gestopte server (de migraties zijn al gedraaid)."""
    db = Path(data_dir) / "platform.db"
    if not db.is_file():
        raise RuntimeError(f"Geen database in {data_dir}: start de server eerst één keer")
    eng = create_engine(f"sqlite:///{db}")

    @event.listens_for(eng, "connect")
    def _pragmas(conn, _record):  # dezelfde als app/db.py
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    try:
        with eng.begin() as conn:
            yield conn
    finally:
        eng.dispose()


def _timezone(data_dir: Path) -> str:
    return Settings(data_dir=data_dir).timezone


# --- blobs met previews ---


def _picture(i: int, size: int) -> Image.Image:
    """Een afbeelding die voor elke i anders is: een kleur plus een blokpatroon uit de bits."""
    r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb((i * 0.618034) % 1, 0.55, 0.9))
    img = Image.new("RGB", (size, size), (r, g, b))
    draw = ImageDraw.Draw(img)
    cell = size // 4
    for bit in range(16):
        if (i >> bit) & 1:
            x, y = (bit % 4) * cell, (bit // 4) * cell
            draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(255 - r, 255 - g, 255 - b))
    return img


def _webp(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=70, method=0)
    return buf.getvalue()


def make_blobs(data_dir: Path, n: int = 2000) -> list[str]:
    """n verschillende afbeeldingen als blob (WebP; de eerste 50 van 1024 px, de rest 256 px)
    met hun previews op schijf en in de database. Vast per index, dus opnieuw aanroepen geeft
    dezelfde sha's en voegt niets dubbel toe. Geeft de sha's in volgorde."""
    data_dir = Path(data_dir)
    store = BlobStore(data_dir / "blobs")
    now = datetime.now(UTC)
    shas: list[str] = []
    rows: list[dict[str, Any]] = []
    render_version = getattr(previews, "RENDER_VERSION", 0)
    for i in range(n):
        size = previews.LARGE if i < LARGE_COUNT else previews.THUMB
        img = _picture(i, size)
        original = _webp(img)
        sha = hashlib.sha256(original).hexdigest()
        shas.append(sha)
        path = store.path(sha)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original)
        target = directory(store, sha)
        target.mkdir(parents=True, exist_ok=True)
        if size > previews.THUMB:
            (target / f"{previews.LARGE}.webp").write_bytes(original)
            thumb = img.copy()
            thumb.thumbnail((previews.THUMB, previews.THUMB))
            (target / f"{previews.THUMB}.webp").write_bytes(_webp(thumb))
            sizes = [previews.THUMB, previews.LARGE]
        else:
            (target / f"{previews.THUMB}.webp").write_bytes(original)
            sizes = [previews.THUMB]
        rows.append(
            {
                "sha256": sha,
                "size": len(original),
                "ref_count": 0,
                "created_at": now,
                "mime": "image/webp",
                "preview": "ready",
                "preview_meta": {"width": size, "height": size, "sizes": sizes},
                "taken_at": None,
                # Al gelezen en gerenderd met de huidige versies: geen backfill bij het starten
                "meta_version": render_meta.VERSION,
                "render_version": render_version,
            }
        )
    with connect(data_dir) as conn:
        existing = set(conn.scalars(select(Blob.sha256).where(Blob.sha256.in_(shas))))
        new = [r for r in rows if r["sha256"] not in existing]
        for start in range(0, len(new), BATCH):
            conn.execute(Blob.__table__.insert(), new[start : start + BATCH])
    return shas


# --- mappen en bestanden ---


def _owner(conn: Connection, owner: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """(id, root_id) van owner: een Account (met .id), een id of een e-mailadres."""
    key = getattr(owner, "id", owner)
    if isinstance(key, str) and "@" in key:
        row = conn.execute(select(User.id, User.root_id).where(User.email == key.lower())).first()
    else:
        row = conn.execute(
            select(User.id, User.root_id).where(User.id == uuid.UUID(str(key)))
        ).first()
    if row is None or row.root_id is None:
        raise RuntimeError(f"Geen gebruiker met een hoofdmap: {owner!r}")
    return row.id, row.root_id


def _dir(owner_id: uuid.UUID, parent: uuid.UUID, name: str, at: datetime) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "parent_id": parent,
        "owner_id": owner_id,
        "name": name,
        "kind": "dir",
        "mime": None,
        "size": 0,
        "blob_id": None,
        "created_at": at,
        "modified_at": at,
        "deleted_at": None,
        "search_name": fold(name),
        "taken_at": None,
        "taken_day": None,
    }


def _file(
    owner_id: uuid.UUID,
    parent: uuid.UUID,
    name: str,
    mime: str,
    sha: str,
    size: int,
    at: datetime,
    tz: str,
) -> dict[str, Any]:
    # Zoals place_file: afbeeldingen en video's op de tijdlijn met de terugvaldatum
    stamped = photodate.stamp_values(mime, None, at, tz)
    return {
        "id": uuid.uuid4(),
        "parent_id": parent,
        "owner_id": owner_id,
        "name": name,
        "kind": "file",
        "mime": mime,
        "size": size,
        "blob_id": sha,
        "created_at": at,
        "modified_at": at,
        "deleted_at": None,
        "search_name": fold(name),
        "taken_at": stamped["taken_at"],
        "taken_day": stamped["taken_day"],
    }


def _insert(conn: Connection, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), BATCH):
        conn.execute(Node.__table__.insert(), rows[start : start + BATCH])
    refs = Counter(r["blob_id"] for r in rows if r["blob_id"])
    if refs:
        conn.execute(
            update(Blob.__table__)
            .where(Blob.__table__.c.sha256 == bindparam("sha"))
            .values(ref_count=Blob.__table__.c.ref_count + bindparam("k")),
            [{"sha": sha, "k": k} for sha, k in refs.items()],
        )


def _sizes(conn: Connection, blobs: Sequence[str]) -> dict[str, int]:
    rows = conn.execute(select(Blob.sha256, Blob.size).where(Blob.sha256.in_(list(blobs))))
    sizes = {sha: size for sha, size in rows}
    missing = set(blobs) - sizes.keys()
    if missing:
        raise RuntimeError(f"{len(missing)} blobs staan niet in de database (eerst make_blobs)")
    return sizes


def seed_folder(
    data_dir: Path,
    owner: Any,
    n: int = 10_000,
    blobs: Sequence[str] = (),
    *,
    name: str | None = None,
    parent_id: str | None = None,
    photos_only: bool = False,
    step: timedelta = timedelta(minutes=7),
) -> str:
    """Een nieuwe map (in de hoofdmap van owner, of in parent_id) met n rijen. Standaard 5 %
    mappen, 50 % afbeeldingen en 45 % andere bestanden; photos_only: alleen afbeeldingen (voor
    de tijdlijn). De bestanden gaan om de beurt over blobs; de datums lopen terug vanaf nu met
    stappen van step. Geeft het id van de map."""
    if not blobs:
        raise ValueError("seed_folder heeft blobs nodig (make_blobs)")
    data_dir = Path(data_dir)
    tz = _timezone(data_dir)
    now = datetime.now(UTC)
    with connect(data_dir) as conn:
        owner_id, root_id = _owner(conn, owner)
        sizes = _sizes(conn, blobs)
        parent = uuid.UUID(parent_id) if parent_id else root_id
        folder = _dir(owner_id, parent, name or f"Seed {n} {uuid.uuid4().hex[:6]}", now)
        rows = [folder]
        files = 0
        for i in range(n):
            at = now - step * (i + 1)
            if not photos_only and i % FOLDER_SHARE == 0:
                rows.append(_dir(owner_id, folder["id"], f"Map {i:05d}", at))
                continue
            sha = blobs[files % len(blobs)]
            files += 1
            if photos_only or i % 2 == 1:
                fname, mime = f"foto-{i:05d}.webp", "image/webp"
            else:
                fname, mime = f"document-{i:05d}.dat", "application/octet-stream"
            rows.append(_file(owner_id, folder["id"], fname, mime, sha, sizes[sha], at, tz))
        _insert(conn, rows)
    return str(folder["id"])


def seed_public(
    data_dir: Path,
    owner: Any,
    blobs: Sequence[str] | None = None,
    *,
    name: str = "Openbaar",
) -> str:
    """Een map met 200 items (10 mappen, 50 afbeeldingen, 140 andere bestanden) en een publieke
    leeslink zonder wachtwoord. Geeft het pad van de link: "/s/<token>"."""
    data_dir = Path(data_dir)
    blobs = list(blobs) if blobs else make_blobs(data_dir, LARGE_COUNT)
    tz = _timezone(data_dir)
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    with connect(data_dir) as conn:
        owner_id, root_id = _owner(conn, owner)
        sizes = _sizes(conn, blobs)
        folder = _dir(owner_id, root_id, f"{name} {uuid.uuid4().hex[:6]}", now)
        rows = [folder]
        for i in range(200):
            at = now - timedelta(hours=i + 1)
            if i < 10:
                rows.append(_dir(owner_id, folder["id"], f"Map {i:03d}", at))
                continue
            sha = blobs[i % len(blobs)]
            if i < 60:
                fname, mime = f"foto-{i:03d}.webp", "image/webp"
            else:
                fname, mime = f"bestand-{i:03d}.dat", "application/octet-stream"
            rows.append(_file(owner_id, folder["id"], fname, mime, sha, sizes[sha], at, tz))
        _insert(conn, rows)
        conn.execute(
            Share.__table__.insert(),
            {
                "id": uuid.uuid4(),
                "node_id": folder["id"],
                "owner_id": owner_id,
                "grantee_id": None,
                "group_id": None,
                "token": token,
                "permission": "read",
                "password_hash": None,
                "expires_at": None,
                "created_at": now,
                "label": "e2e",
            },
        )
    return f"/s/{token}"
