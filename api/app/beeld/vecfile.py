"""Het bestand per eigenaar van zoeken op beeld maken (bouwontwerp fase 6, X3; Keuze 76; X3b).

build() leest de rijen van blob_embed voor de beeldbestanden van één eigenaar en schrijft
`<beeld_dir>/<user_id als 32 hex>.vec` in het formaat van types.py: eerst naar een tijdelijk
bestand (O_EXCL, O_NOFOLLOW) in dezelfde map, dan os.replace. Zo krijgt de worker voor een nieuwe
inhoud altijd een nieuwe inode (zijn cache onthoudt de sha256 per inode, K2-23).

- **Wat erin staat**: elke node van die eigenaar met kind file en ftype image en een vector van de
  versie van nu, ook in de prullenbak (de vraag filtert met own_live, Keuze 78), op num. Niet wat
  Keuze 69 uitsluit (beeld_rows.origin_ok): een upload via een link nooit, die van een ander
  alleen als die ander zelf beeld aan heeft. Een node zonder rij in node_origin (van vóór 0011)
  telt als door de eigenaar zelf geüpload (Vraag 9). De SQL daarvan (beeld_rows.file_rows)
  staat in app/beeld_rows.py, buiten de wachter van own_live: het bestand bevat ook de
  prullenbak.
- **Eén momentopname**: het aantal, gen uit beeld_owner en de rijen komen uit één leestransactie;
  de kop klopt dus altijd met de inhoud. Per CHUNK rijen gelezen (op num), nooit alles tegelijk.
- **Per rij ook het begin van de sha256 van de inhoud** (types.py): prefixes() geeft dat voor de
  treffers van rank, zodat een node met andere inhoud dan in het bestand nooit met de oude
  vector gevonden wordt (§3.9), ook niet voordat het bestand opnieuw is gemaakt.
- **Daarna** beeld_owner: built_gen (de gen van de momentopname), built_rows en built_at.

Geen vector in een log of een fout (K1-16); het log noemt alleen aantallen.
"""

from __future__ import annotations

import array
import bisect
import contextlib
import logging
import os
import secrets
import stat
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import beeld_rows
from app.analysis import VERSIONS, Step
from app.beeld import types as vt
from app.models import BeeldOwner, now

log = logging.getLogger("platform.beeld")

CHUNK = 2000  # rijen per keer lezen (Keuze 76)
SUFFIX = ".vec"
TMP_STALE_S = 3600.0  # een tijdelijk bestand dat zo oud is, is van een gestopte build


def path_for(directory: Path, user_id: uuid.UUID) -> Path:
    return directory / f"{user_id.hex}{SUFFIX}"


def owner_of(path: Path) -> uuid.UUID | None:
    """De eigenaar uit een bestandsnaam van ons (<32 hex>.vec), anders None."""
    if path.suffix != SUFFIX or len(path.stem) != 32:
        return None
    try:
        return uuid.UUID(hex=path.stem)
    except ValueError:
        return None


def stale_tmp(path: Path, at: float | None = None) -> bool:
    """Een tijdelijk bestand van build() (.<32 hex>.<12 hex>.tmp, een gewoon bestand) dat ouder
    is dan TMP_STALE_S: een build die halverwege stopte (de app ging uit). Een build die nog
    loopt, is nooit zo oud (de job stopt na 600 s)."""
    name = path.name
    if not (name.startswith(".") and name.endswith(".tmp")):
        return False
    parts = name[1:-4].split(".")
    if len(parts) != 2 or len(parts[0]) != 32 or len(parts[1]) != 12:
        return False
    try:
        found = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(found.st_mode):
        return False
    return (time.time() if at is None else at) - found.st_mtime > TMP_STALE_S


def ensure_dir(directory: Path) -> Path:
    """De map (0700, van de app). Een symlink op de plek van de map weigeren we."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or not directory.is_dir():
        raise OSError("de map voor beeld is geen gewone map")
    return directory


@dataclass(frozen=True)
class Built:
    rows: int
    gen: int
    path: Path


def _write_all(fd: int, data: bytes | memoryview) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


async def build(
    maker: async_sessionmaker[AsyncSession], directory: Path, user_id: uuid.UUID
) -> Built:
    """Het bestand van deze eigenaar opnieuw maken; geeft het aantal rijen en de gen."""
    version = VERSIONS[Step.EMBED]
    params = {"me": user_id.hex, "v": version, "size": vt.DIM * 2}
    ensure_dir(directory)
    final = path_for(directory, user_id)
    tmp = directory / f".{user_id.hex}.{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    done = False
    try:
        async with maker() as db:
            # Eén leestransactie: aantal, gen en rijen uit dezelfde momentopname
            count = int(
                await db.scalar(text(f"SELECT count(*) {beeld_rows.file_rows()}"), params) or 0
            )
            gen = int(
                await db.scalar(
                    text("SELECT gen FROM beeld_owner WHERE user_id = :me"), {"me": user_id.hex}
                )
                or 0
            )
            head = vt.Head(vt.MODEL, version, vt.DIM, count, gen)
            _write_all(fd, vt.pack_head(head))
            nums_at, vecs_at, shas_at = head.nums_at(), head.vectors_at(), head.shas_at()
            seen, last = 0, -1
            while seen < count:
                rows = (
                    await db.execute(
                        text(
                            f"SELECT n.num, e.vec, n.blob_id {beeld_rows.file_rows()} "
                            "AND n.num > :last ORDER BY n.num LIMIT :chunk"
                        ),
                        {**params, "last": last, "chunk": CHUNK},
                    )
                ).all()
                if not rows:
                    break
                rows = rows[: count - seen]
                nums = b"".join(vt.NUM.pack(int(r[0])) for r in rows)
                vecs = b"".join(bytes(r[1]) for r in rows)
                shas = b"".join(vt.sha_prefix(str(r[2])) for r in rows)
                os.pwrite(fd, nums, nums_at + seen * vt.NUM.size)
                os.pwrite(fd, vecs, vecs_at + seen * vt.DIM * 2)
                os.pwrite(fd, shas, shas_at + seen * vt.SHA_BYTES)
                seen += len(rows)
                last = int(rows[-1][0])
            if seen != count:
                raise RuntimeError("het aantal rijen veranderde tijdens het lezen")
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp, final)
        done = True
    finally:
        if fd >= 0:
            os.close(fd)
        if not done:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
    async with maker() as db:
        await db.execute(
            update(BeeldOwner)
            .where(BeeldOwner.user_id == user_id)
            .values(built_gen=gen, built_rows=count, built_at=now())
        )
        await db.commit()
    log.info("beeld: bestand gemaakt met %d foto's", count)
    return Built(count, gen, final)


def remove(directory: Path, user_id: uuid.UUID) -> bool:
    """Het bestand van deze eigenaar weg; True als het er was."""
    try:
        path_for(directory, user_id).unlink()
    except FileNotFoundError:
        return False
    return True


def open_for_worker(directory: Path, user_id: uuid.UUID) -> int | None:
    """Een fd (alleen lezen, zonder symlinks) voor rank; None zonder bestand."""
    try:
        return os.open(path_for(directory, user_id), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None


def read_head_of(fd: int) -> vt.Head:
    return vt.read_head(os.pread(fd, vt.HEAD.size, 0))


def _pread_exact(fd: int, size: int, offset: int) -> bytes:
    data = os.pread(fd, size, offset)
    if len(data) != size:
        raise vt.VecFileError(vt.BAD_FILE)
    return data


def prefixes(fd: int, nums: list[int]) -> dict[int, bytes]:
    """Voor deze num's het begin van de sha256 van de inhoud waarmee ze in het bestand staan
    (§3.9: nieuwe inhoud). Een num die er niet in staat, ontbreekt in de uitkomst."""
    head = read_head_of(fd)
    if head.size() != os.fstat(fd).st_size:
        raise vt.VecFileError(vt.BAD_FILE)
    if not nums or not head.count:
        return {}
    every = array.array("q", _pread_exact(fd, head.count * vt.NUM.size, head.nums_at()))
    if sys.byteorder != "little":
        every.byteswap()  # het bestand is little endian
    shas = _pread_exact(fd, head.count * vt.SHA_BYTES, head.shas_at())
    out: dict[int, bytes] = {}
    for num in nums:
        i = bisect.bisect_left(every, num)
        if i < head.count and every[i] == num:
            out[num] = shas[i * vt.SHA_BYTES : (i + 1) * vt.SHA_BYTES]
    return out


def is_current(directory: Path, user_id: uuid.UUID) -> bool:
    """Het bestand is er en is van het model en de versie van nu (een nieuwe versie van de stap
    embed: dan opnieuw, met alleen de nieuwe vectoren)."""
    fd = open_for_worker(directory, user_id)
    if fd is None:
        return False
    try:
        head = read_head_of(fd)
    except (OSError, vt.VecFileError):
        return False
    finally:
        os.close(fd)
    return (head.model, head.version, head.dim) == (vt.MODEL, VERSIONS[Step.EMBED], vt.DIM)
