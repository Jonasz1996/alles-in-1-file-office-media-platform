"""Blob-opslag op de lokale schijf, content-addressed op SHA-256.

Een upload gaat eerst naar een tijdelijk bestand terwijl de hash wordt berekend;
daarna wordt het atomair verplaatst naar blobs/ab/cd/<sha256>. Bestaat die al,
dan wordt het tijdelijke bestand weggegooid (dedupe). Later komt hier een
S3-variant naast voor het cluster.
"""

import asyncio
import hashlib
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import ClassVar

import anyio

CHUNK = 1024 * 1024
# Zo lang (seconden) mag een request-body stilvallen. Een client die verdwijnt zonder de
# verbinding te sluiten (wifi naar 4G, laptop dicht) houdt een verzoek anders voor altijd open.
CHUNK_TIMEOUT = 60.0


class TooLarge(Exception):
    pass


async def with_timeout(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Geeft de stukken door; komt er CHUNK_TIMEOUT seconden niets, dan TimeoutError."""
    it = aiter(chunks)
    while True:
        try:
            with anyio.fail_after(CHUNK_TIMEOUT):
                chunk = await anext(it)
        except StopAsyncIteration:
            return
        yield chunk


class BlobStore:
    _locks: ClassVar[dict[Path, asyncio.Lock]] = {}

    def __init__(self, root: Path):
        self.root = root
        self.tmp = root / ".tmp"

    @property
    def lock(self) -> asyncio.Lock:
        """Eén slot per opslag. Ontdubbelen en ref_count ophogen, ref_count verlagen en
        opruimen gebeuren nooit door elkaar: anders kan het opruimen de enige kopie weggooien
        van inhoud die net opnieuw binnenkwam (de nieuwe kopie is dan al weggegooid)."""
        return self._locks.setdefault(self.root, asyncio.Lock())

    def path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256[2:4] / sha256

    async def receive(self, chunks: AsyncIterator[bytes], max_bytes: int) -> tuple[Path, str, int]:
        """Schrijft de body naar een tijdelijk bestand en berekent intussen de SHA-256.
        Geeft (tijdelijk bestand, sha256, grootte); adopt() zet het daarna op zijn plek."""
        await anyio.Path(self.tmp).mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.tmp)
        os.close(fd)
        digest = hashlib.sha256()
        size = 0
        try:
            async with await anyio.open_file(tmp_name, "wb") as f:
                async for chunk in with_timeout(chunks):
                    size += len(chunk)
                    if max_bytes and size > max_bytes:
                        raise TooLarge
                    digest.update(chunk)
                    await f.write(chunk)
        except BaseException:
            await anyio.Path(tmp_name).unlink(missing_ok=True)
            raise
        return Path(tmp_name), digest.hexdigest(), size

    async def adopt(self, path: Path, sha256: str) -> bool:
        """Neemt een volledig bestand over dat al op dezelfde schijf staat. True: verplaatst.
        False: die inhoud stond er al; het bestand blijft dan liggen, zodat de aanroeper het
        pas weggooit als de database is bijgewerkt. Alleen onder `lock`."""
        target = anyio.Path(self.path(sha256))
        if await target.exists():
            return False
        await target.parent.mkdir(parents=True, exist_ok=True)
        await anyio.Path(path).rename(target)
        return True

    async def give_back(self, path: Path, sha256: str) -> None:
        """Draait een geslaagde adopt() terug, als de database daarna niet bijgewerkt kon worden."""
        await anyio.Path(self.path(sha256)).rename(path)

    async def delete(self, sha256: str) -> None:
        await anyio.Path(self.path(sha256)).unlink(missing_ok=True)


def hash_file(path: Path) -> str:
    """SHA-256 van een bestand; draai dit in een thread."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
