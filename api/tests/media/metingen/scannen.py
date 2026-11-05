"""Ml (§4.5): een bibliotheek van 1.000 bestanden scannen, zonder analyse. Grens ≤ 60 s.

Een eigen app (kit.App, met media aan). Per run een nieuwe map met 1.000 bestanden: 600 in één map
(een map met meer dan 500 bestanden gaat in delen) en 4 × 100 in submappen, met namen zoals
"Film 0001 (1950).mkv". De bestanden staan als rijen in de database (zoals
test_2000_bestanden_in_stukken): allemaal dezelfde blob (een klein bestand dat via de API is
geüpload), dus één analyse voor allemaal; dat is "zonder analyse". Dan POST
/api/v1/media/libraries (de eerste scan start vanzelf) en de tijd tot de bibliotheek idle is
met 1.000 items.

    cd api && uv run python -m tests.media.metingen.scannen --herhaal 5 --uit /tmp/ml.json
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from tests.media.metingen import kit

BIG = 600
SMALL = 4
PER_SMALL = 100
LIMIT_S = 60.0


async def insert(data_dir: Path, rows: list[tuple[str, str]], sample: str) -> None:
    """De rijen (map, naam) als bestanden met de blob van sample (zoals de test van M2)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app import db
    from app.config import Settings
    from app.models import Node

    settings = Settings(data_dir=data_dir, web_dist=data_dir / "web", jwt_secret="m" * 32)
    engine = db.get_engine(settings)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            first = await session.get(Node, uuid.UUID(sample))
            assert first is not None
            for parent, name in rows:
                session.add(
                    Node(parent_id=uuid.UUID(parent), owner_id=first.owner_id, name=name,
                         kind="file", mime="video/x-matroska", size=1, blob_id=first.blob_id)
                )  # fmt: skip
            await session.commit()
    finally:
        await engine.dispose()
        db._engine.cache_clear()


def library_state(app: kit.App, library_id: str) -> dict[str, Any]:
    for lib in app.call("GET", "/api/v1/media/libraries", expect=200).json():
        if lib["id"] == library_id:
            return lib
    return {}


def one(app: kit.App, root: str, sample: str, i: int) -> dict[str, Any]:
    folder = app.mkdir(root, f"Bibliotheek {i}")
    big = app.mkdir(folder, "Groot")
    small = [app.mkdir(folder, f"Map {k}") for k in range(SMALL)]
    rows = [(big, f"Film {n:04d} ({1950 + n % 70}).mkv") for n in range(BIG)]
    rows += [(small[n % SMALL], f"Ander {n:04d}.mp4") for n in range(SMALL * PER_SMALL)]
    asyncio.run(insert(app.data_dir, rows, sample))
    rest = kit.quiet(0.8, 120)
    began = time.monotonic()
    body = {"root_id": folder, "kind": "movies", "name": f"Meten {i}"}
    library_id = app.call("POST", "/api/v1/media/libraries", json=body, expect=201).json()["id"]
    state: dict[str, Any] = {}
    while time.monotonic() - began < 600:
        state = library_state(app, library_id)
        if state.get("scan_state") == "idle" and state.get("item_count") == len(rows):
            break
        if state.get("scan_state") == "failed":
            break
        time.sleep(0.1)
    took = time.monotonic() - began
    kit.log(f"run {i}: {took:.2f} s, {state.get('item_count')} items, {state.get('scan_state')}")
    return {
        "seconden": round(took, 2),
        "items": state.get("item_count"),
        "staat": state.get("scan_state"),
        "rust_voor_cores": rest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--herhaal", type=int, default=5)
    parser.add_argument("--uit", type=Path, default=None)
    args = parser.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="meet5-scannen-"))
    dog = kit.Watchdog()
    dog.start()
    app = kit.App(work)
    dog.watch(lambda: app.pid)
    result: dict[str, Any] = {"meting": "scannen", "omstandigheden": kit.conditions(), "runs": []}
    try:
        app.start()
        root = app.root_folder()
        sample = app.upload_bytes(app.mkdir(root, "Voorbeeld"), "eerste.mkv", b"\x1a\x45\xdf\xa3")
        sampler = kit.Sampler(app)
        sampler.start()
        try:
            for i in range(args.herhaal):
                began = time.monotonic()
                run = one(app, root, sample, i + 1)
                run["processen"] = sampler.window(began, time.monotonic())
                result["runs"].append(run)
                kit.write_json(args.uit, result)
        finally:
            sampler.stop()
        result["seconden"] = kit.spread(r["seconden"] for r in result["runs"])
        result["grens_s"] = LIMIT_S
    finally:
        dog.stop()
        result["waakhond"] = dog.summary()
        app.close()
        kit.write_json(args.uit, result)
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
