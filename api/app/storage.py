"""Blob-opslag op de lokale schijf, content-addressed op SHA-256.

Een upload gaat eerst naar een tijdelijk bestand terwijl de hash wordt berekend;
daarna wordt het atomair verplaatst naar blobs/ab/cd/<sha256>. Bestaat die al,
dan wordt het tijdelijke bestand weggegooid (dedupe). Later komt hier een
S3-variant naast voor het cluster.

Replicatie per bibliotheek (app/blobsplit.py; docs/runbooks/replicatie.md): naast blobs/ een
map per aantal kopieën dat afwijkt van de standaard van het cluster, blobs-r1/, blobs-r2/ en
blobs-r3/ (zelfde indeling ab/cd/<sha256>). In het cluster geeft SeaweedFS elke map haar eigen
replicatie (fs.configure). Nieuwe inhoud komt altijd eerst in blobs/; blobsplit verhuist ze.
blobs-media/ van fase 11 (films en series op 2 kopieën) wordt alleen nog gelezen; blobsplit
verhuist wat er staat naar zijn juiste plek. path() zoekt overal.
"""

import hashlib
import os
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import ClassVar

import anyio

from app.dbgate import BoostLock

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


COPIES = (1, 2, 3)  # de mappen blobs-r1/ tot blobs-r3/


class BlobStore:
    _locks: ClassVar[dict[Path, BoostLock]] = {}
    # Of een map naast blobs/ bestaat (blobs-r1/ … en blobs-media/), per map met de
    # monotonic-tijd van de laatste blik: zonder die mappen kost path() geen enkele blik op de
    # schijf extra, zoals vroeger. Een map die er is, blijft er (blobsplit haalt er geen weg)
    _seen: ClassVar[dict[Path, tuple[bool, float]]] = {}

    def __init__(self, root: Path):
        self.root = root
        self.tmp = root / ".tmp"
        # Fase 11: films en series op 2 kopieën. Alleen nog lezen (en leeghalen, blobsplit)
        self.media = root.parent / f"{root.name}-media"

    @property
    def lock(self) -> BoostLock:
        """Eén slot per opslag. Ontdubbelen en ref_count ophogen, ref_count verlagen en
        opruimen gebeuren nooit door elkaar: anders kan het opruimen de enige kopie weggooien
        van inhoud die net opnieuw binnenkwam (de nieuwe kopie is dan al weggegooid). Wie het
        heeft, schrijft als voorgrond (app/dbgate.py): een upload kan erop wachten."""
        return self._locks.setdefault(self.root, BoostLock())

    def copies_dir(self, copies: int) -> Path:
        """De map voor inhoud met dit aantal kopieën als dat afwijkt van de standaard:
        blobs-r1/, blobs-r2/ of blobs-r3/."""
        if copies not in COPIES:
            raise ValueError(f"aantal kopieën {copies} (1, 2 of 3)")
        return self.root.parent / f"{self.root.name}-r{copies}"

    def place(self, copies: int | None) -> Path:
        """De map voor inhoud met dit aantal kopieën; None: blobs/ (de standaard)."""
        return self.root if copies is None else self.copies_dir(copies)

    def others(self) -> list[Path]:
        """De mappen naast blobs/ waar inhoud kan staan, in de volgorde waarin path() zoekt:
        blobs-r1/ tot blobs-r3/, dan blobs-media/ van fase 11."""
        return [*(self.copies_dir(n) for n in COPIES), self.media]

    @staticmethod
    def at(folder: Path, sha256: str) -> Path:
        return folder / sha256[:2] / sha256[2:4] / sha256

    def main_path(self, sha256: str) -> Path:
        return self.at(self.root, sha256)

    def media_path(self, sha256: str) -> Path:
        return self.at(self.media, sha256)

    def copies_path(self, sha256: str, copies: int) -> Path:
        return self.at(self.copies_dir(copies), sha256)

    def exists(self, folder: Path) -> bool:
        """Of deze map naast blobs/ er is; hooguit één blik per minuut zolang ze er niet is."""
        seen, at = self._seen.get(folder, (False, -1e9))
        if seen:
            return True
        clock = time.monotonic()
        if clock - at > 60:
            seen = folder.is_dir()
            self._seen[folder] = (seen, clock)
        return seen

    def saw(self, folder: Path) -> None:
        """Deze map is er nu (blobsplit maakte haar): path() kijkt er meteen ook."""
        self._seen[folder] = (True, time.monotonic())

    def has_media(self) -> bool:
        return self.exists(self.media)

    def path(self, sha256: str) -> Path:
        """Waar de inhoud staat: in blobs/, anders in blobs-r1/ tot blobs-r3/, anders in
        blobs-media/. Staat ze nergens, dan de plek in blobs/ (daar komt nieuwe inhoud). Zonder
        die mappen altijd blobs/, zonder een blik op de schijf."""
        main = self.main_path(sha256)
        folders = [f for f in self.others() if self.exists(f)]
        if not folders or main.exists():
            return main
        for folder in folders:
            found = self.at(folder, sha256)
            if found.exists():
                return found
        return main

    def everywhere(self, sha256: str) -> list[Path]:
        """Elke plek waar deze inhoud kan staan: blobs/ en de mappen ernaast."""
        return [self.main_path(sha256), *(self.at(f, sha256) for f in self.others())]

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
        target = anyio.Path(self.main_path(sha256))
        for place in self.everywhere(sha256):
            if await anyio.Path(place).exists():
                return False
        await target.parent.mkdir(parents=True, exist_ok=True)
        await anyio.Path(path).rename(target)
        return True

    async def give_back(self, path: Path, sha256: str) -> None:
        """Draait een geslaagde adopt() terug, als de database daarna niet bijgewerkt kon worden."""
        await anyio.Path(self.main_path(sha256)).rename(path)

    async def delete(self, sha256: str) -> None:
        for place in self.everywhere(sha256):
            await anyio.Path(place).unlink(missing_ok=True)


def hash_file(path: Path) -> str:
    """SHA-256 van een bestand; draai dit in een thread."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
