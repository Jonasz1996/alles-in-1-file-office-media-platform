"""Testdata voor de bestandsbrowser (P2).

- `seed_big(server, admin)`: een eigen gebruiker met een map van 10.000 rijen (seed.seed_folder:
  5 % mappen, 50 % afbeeldingen met thumbnail, 45 % andere bestanden). Alleen om te lezen:
  tests die iets veranderen, doen dat in een kleine boom.
- `small_tree(server, account)`: via de API een map "Werk …" met de mappen "Doel" (met daarin
  "Sub") en "Archief", en de bestanden a.txt, b.txt en c.txt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from conftest import Account, Server, create_user

API = "/api/v1"


@dataclass
class BigFolder:
    owner: Account
    folder_id: str
    n: int


def seed_big(server: Server, admin: Account, n: int = 10_000) -> BigFolder:
    import seed

    owner = create_user(server, admin, name="Bladeraar")

    def fill(data_dir: Path) -> str:
        shas = seed.make_blobs(data_dir, 200)
        return seed.seed_folder(data_dir, owner, n, shas, name=f"Groot {uuid.uuid4().hex[:6]}")

    folder = server.seeded(fill)
    return BigFolder(owner, folder, n)


def big_name(i: int) -> str:
    """De naam van rij i (in de volgorde waarin seed_folder ze maakt)."""
    if i % 20 == 0:
        return f"Map {i:05d}"
    return f"foto-{i:05d}.webp" if i % 2 == 1 else f"document-{i:05d}.dat"


def mkdir(server: Server, account: Account, parent: str, name: str) -> str:
    r = server.call(
        account, "POST", f"{API}/files/{parent}/folders", json={"name": name}, expect=201
    )
    return r.json()["id"]


def upload(server: Server, account: Account, parent: str, name: str, body: bytes) -> str:
    r = server.call(
        account,
        "PUT",
        f"{API}/files/{parent}/upload",
        params={"name": name},
        content=body,
        expect=201,
    )
    return r.json()["id"]


def children(server: Server, account: Account, folder: str) -> list[str]:
    """De namen in een map (alles, in de volgorde van de server)."""
    names: list[str] = []
    after: str | None = None
    while True:
        params = {"limit": 1000, **({"after": after} if after else {})}
        page = server.call(
            account, "GET", f"{API}/files/{folder}/children", params=params, expect=200
        )
        data = page.json()
        names += [c["name"] for c in data["children"]]
        after = data.get("next")
        if not after:
            return names


@dataclass
class SmallTree:
    work: str
    target: str
    sub: str
    archive: str
    files: dict[str, str]
    name: str


def small_tree(server: Server, account: Account) -> SmallTree:
    name = f"Werk {uuid.uuid4().hex[:6]}"
    work = mkdir(server, account, account.root_id, name)
    target = mkdir(server, account, work, "Doel")
    sub = mkdir(server, account, target, "Sub")
    archive = mkdir(server, account, work, "Archief")
    files = {
        n: upload(server, account, work, n, f"inhoud van {n}\n".encode())
        for n in ("a.txt", "b.txt", "c.txt")
    }
    return SmallTree(work, target, sub, archive, files, name)
