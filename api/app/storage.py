"""Blob-opslag op de lokale schijf, content-addressed op SHA-256.

Een upload gaat eerst naar een tijdelijk bestand terwijl de hash wordt berekend;
daarna wordt het atomair verplaatst naar blobs/ab/cd/<sha256>. Bestaat die al,
dan wordt het tijdelijke bestand weggegooid (dedupe). Later komt hier een
S3-variant naast voor het cluster.
"""

import hashlib
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import anyio

CHUNK = 1024 * 1024


class TooLarge(Exception):
    pass


class BlobStore:
    def __init__(self, root: Path):
        self.root = root
        self.tmp = root / ".tmp"

    def path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256[2:4] / sha256

    async def put(self, chunks: AsyncIterator[bytes], max_bytes: int) -> tuple[str, int]:
        await anyio.Path(self.tmp).mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.tmp)
        os.close(fd)
        digest = hashlib.sha256()
        size = 0
        try:
            async with await anyio.open_file(tmp_name, "wb") as f:
                async for chunk in chunks:
                    size += len(chunk)
                    if max_bytes and size > max_bytes:
                        raise TooLarge
                    digest.update(chunk)
                    await f.write(chunk)
            sha = digest.hexdigest()
            target = anyio.Path(self.path(sha))
            if await target.exists():
                await anyio.Path(tmp_name).unlink()
            else:
                await target.parent.mkdir(parents=True, exist_ok=True)
                await anyio.Path(tmp_name).rename(target)
            return sha, size
        except BaseException:
            await anyio.Path(tmp_name).unlink(missing_ok=True)
            raise

    async def adopt(self, path: Path, sha256: str) -> None:
        """Neemt een volledig bestand over dat al op dezelfde schijf staat (een afgeronde
        hervatbare upload). Bestaat de inhoud al, dan wordt het bestand weggegooid."""
        target = anyio.Path(self.path(sha256))
        if await target.exists():
            await anyio.Path(path).unlink()
        else:
            await target.parent.mkdir(parents=True, exist_ok=True)
            await anyio.Path(path).rename(target)

    async def delete(self, sha256: str) -> None:
        await anyio.Path(self.path(sha256)).unlink(missing_ok=True)


def hash_file(path: Path) -> str:
    """SHA-256 van een bestand; draai dit in een thread."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
