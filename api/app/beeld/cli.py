"""De commando's van zoeken op beeld (bouwontwerp fase 6, X3; BZ6, BZ9, BZ10; §3.5; X3b).

    python -m app.cli beeld-status
    python -m app.cli beeld-bench [--fotos 200000] [--vragen 40]
    python -m app.cli beeld-kwaliteit --personen [--eigenaar e-mail]

- **beeld-status**: de stand (de analyse, het model, de schakelaar van de beheerder, hoeveel
  foto's een vector hebben, mislukt per code, de ruimte). Alleen getallen en woorden van de app.
- **beeld-bench** (BZ6, BZ9): op deze CPU, met de container. Hoeveel foto's per minuut embed_image
  haalt (batches van 16 nep-previews van 256, in een memfd), en de p95 van rank met een tekst over
  een bestand met --fotos willekeurige vectoren (een eigen eigenaar, alleen in een tijdelijke map;
  forget erna). Schrijft beeld-bench.json ({at, p95_ms, photos_per_minute}) naast de cache, via
  een tijdelijk bestand en os.replace; /beheer leest het.
- **beeld-kwaliteit --personen** (BZ10): met de bevestigde personen van de eigenaar (face_person
  "ja"): per foto van een persoon met minstens twee foto's "meer zoals deze" (rank met de num), en
  hoe vaak een andere foto van dezelfde persoon in de top 5 staat, tegen toeval. Alleen getallen;
  niets verlaat de machine. Schrijft beeld-kwaliteit.json ({at, ok}): ok als het boven toeval
  ligt. Zonder --personen: de kwaliteitspoort van Keuze 83 met de set draait in de CI-job
  beeld-model (tests_analyse/test_beeld_kwaliteit.py); dit commando zegt dat.

Met de analyse uit (een ontbrekende ANALYSE-regel is uit) doen ze niets: een regel op stderr,
code 1, geen bestand. Nooit een vector, een vraag of een naam van een bestand op het scherm of in
een bestand (K1-16); de vragen van de bench zijn vaste woorden van de app zelf.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
import random
import struct
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.analyse import client, monitor, wire
from app.analyse.wire import Op
from app.beeld import admin as badmin
from app.beeld import jobs as bjobs
from app.beeld import types as vt
from app.beeld import vecfile
from app.config import Settings
from app.db import get_engine
from app.models import User

COMMANDS = ("beeld-status", "beeld-bench", "beeld-kwaliteit")
BENCH_QUERIES = ("hond op het strand", "zonsondergang", "een rode auto", "sneeuw in de bergen")
TOP = 5


def parsers(commands: Any) -> None:
    commands.add_parser("beeld-status", help="de stand van zoeken op beeld")
    bench = commands.add_parser("beeld-bench", help="zoeken op beeld op deze CPU meten")
    bench.add_argument("--fotos", type=int, default=100_000, help="zoveel vectoren in het bestand")
    bench.add_argument("--vragen", type=int, default=40, help="zoveel vragen voor de p95")
    bench.add_argument("--embed", type=int, default=64, help="zoveel previews door embed_image")
    kwaliteit = commands.add_parser(
        "beeld-kwaliteit", help="de kwaliteit van zoeken op beeld meten (alleen getallen)"
    )
    kwaliteit.add_argument(
        "--personen", action="store_true", help="met de bevestigde personen (BZ10)"
    )
    kwaliteit.add_argument("--eigenaar", default=None, help="het e-mailadres van de eigenaar")


def run(settings: Settings, args: argparse.Namespace) -> int:
    if not settings.analyse_on:
        print("Zoeken op beeld staat uit: de analyse staat uit (ANALYSE).", file=sys.stderr)
        return 1

    async def go() -> int:
        try:
            if args.command == "beeld-status":
                return await status(settings)
            if args.command == "beeld-bench":
                return await bench(settings, args.fotos, args.vragen, args.embed)
            return await quality(settings, args.personen, args.eigenaar)
        finally:
            await get_engine(settings).dispose()

    return asyncio.run(go())


def _maker(settings: Settings) -> async_sessionmaker:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def write_result(settings: Settings, name: str, data: dict[str, Any]) -> Path:
    """Atomisch naast de cache: een tijdelijk bestand in dezelfde map, dan os.replace."""
    directory = badmin.results_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    fd, tmp = tempfile.mkstemp(prefix=f".{name}.", dir=directory)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(data, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def _found(settings: Settings) -> monitor.AnalyseStatus:
    return await monitor.check(settings)


# --- beeld-status -------------------------------------------------------------------------------


async def status(settings: Settings) -> int:
    found = await _found(settings)
    monitor._set_quiet(found)
    bjobs.enable(settings)
    async with _maker(settings)() as db:
        block = await badmin.block(db)
    problem = monitor.analyse_problem(found)
    print(f"analyse: {block.analyse}" + (f" ({problem})" if problem else ""))
    print(f"model: {block.model}")
    print(f"beheerder: {'aan' if block.on else 'uit'}")
    print(f"foto's met een vector: {block.seen} van {block.photos}")
    for item in block.failed_by_code:
        print(f"mislukt ({item.code or 'onbekend'}): {item.n}")
    print(
        f"ruimte: database {block.space.db_bytes} B, cache {block.space.cache_bytes} B, "
        f"modellen {block.space.models_bytes} B"
    )
    if block.bench is not None:
        print(
            f"bench ({block.bench.at}): p95 {block.bench.p95_ms:.0f} ms, "
            f"{block.bench.photos_per_minute:.0f} foto's per minuut"
        )
    if block.quality is not None:
        print(f"kwaliteit ({block.quality.at}): {'ok' if block.quality.ok else 'niet ok'}")
    return 0


# --- beeld-bench --------------------------------------------------------------------------------


def _ready_or_say(found: monitor.AnalyseStatus) -> bool:
    if not bjobs.ready(found):
        print(f"De container is niet klaar voor beeld ({found.state}).", file=sys.stderr)
        return False
    return True


def _previews(n: int) -> list[bytes]:
    from PIL import Image

    out = []
    rng = random.Random(7)
    for _ in range(n):
        img = Image.new("RGB", (256, 192), tuple(rng.randrange(256) for _ in range(3)))
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=80)
        out.append(buf.getvalue())
    return out


def _memfd(name: str, data: bytes) -> int:
    fd = os.memfd_create(name, os.MFD_CLOEXEC)
    os.write(fd, data)
    os.lseek(fd, 0, os.SEEK_SET)
    return fd


def random_vecfile(path: Path, count: int, dim: int, seed: int = 1) -> None:
    """Een bestand in het formaat van types.py met count willekeurige f16-vectoren (L2 = 1)."""
    rng = random.Random(seed)
    head = vt.Head(vt.MODEL, 1, dim, count, 1)
    with open(path, "wb") as out:
        out.write(vt.pack_head(head))
        out.write(b"".join(vt.NUM.pack(i + 1) for i in range(count)))
        pack = struct.Struct(f"<{dim}e").pack
        for _ in range(count):
            vec = [rng.gauss(0.0, 1.0) for _ in range(dim)]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.write(pack(*(x / norm for x in vec)))
        out.write(bytes(count * vt.SHA_BYTES))


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


async def bench(settings: Settings, fotos: int, vragen: int, embed: int) -> int:
    found = await _found(settings)
    if not _ready_or_say(found):
        return 1
    assert found.caps is not None and found.caps.beeld is not None
    dim = found.caps.beeld.dim
    # embed_image: batches van PHOTOS_PER_OP
    data = _previews(max(1, embed))
    begin = time.perf_counter()
    for start in range(0, len(data), wire.PHOTOS_PER_OP):
        part = data[start : start + wire.PHOTOS_PER_OP]
        fds = [_memfd("preview", d) for d in part]
        out = wire.out_memfd("uit")
        try:
            await client.call(settings, Op.EMBED_IMAGE, wire.EmbedImageArgs(), fds=[*fds, out])
        finally:
            for fd in (*fds, out):
                os.close(fd)
    per_minute = len(data) / max(1e-9, time.perf_counter() - begin) * 60.0
    # rank: een eigen eigenaar, een bestand in een tijdelijke map
    owner = uuid.uuid4().hex
    times: list[float] = []
    with tempfile.TemporaryDirectory(prefix="beeld-bench-") as tmp:
        path = Path(tmp) / f"{owner}.vec"
        random_vecfile(path, max(1, fotos), dim)
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            warm = wire.RankArgs(owner=owner, gen=1, text=None, num=None, k=0)
            await client.call(settings, Op.RANK, warm, fds=[fd])
            for i in range(max(1, vragen)):
                args = wire.RankArgs(
                    owner=owner, gen=1, text=BENCH_QUERIES[i % len(BENCH_QUERIES)], k=1000
                )
                start = time.perf_counter()
                await client.call(settings, Op.RANK, args, fds=[fd])
                times.append((time.perf_counter() - start) * 1000.0)
        finally:
            os.close(fd)
            try:
                await client.call(settings, Op.FORGET, wire.ForgetArgs(owner=owner))
            except (client.AnalyseFailed, client.AnalyseDown, client.AnalyseBusy):
                pass
    result = {"at": _now(), "p95_ms": round(p95(times), 1), "photos_per_minute": round(per_minute)}
    write_result(settings, badmin.BENCH_FILE, result)
    print(
        f"beeld-bench: p95 {result['p95_ms']} ms over {len(times)} vragen en {fotos} foto's; "
        f"{result['photos_per_minute']} foto's per minuut"
    )
    return 0


# --- beeld-kwaliteit ----------------------------------------------------------------------------

_PEOPLE_SQL = text(
    "SELECT fp.person_id, n.num FROM face_person AS fp JOIN face AS f ON f.id = fp.face_id "
    "JOIN node AS n ON n.id = f.node_id "
    "WHERE fp.owner_id = :me AND fp.verdict = 'ja' AND n.owner_id = :me AND n.dead = 0 "
    "AND n.ftype = 'image'"
)


def same_person_rate(
    groups: dict[str, set[int]], ranked: dict[int, list[int]], total: int
) -> tuple[int, float, float]:
    """(aantal vragen, deel met dezelfde persoon in de top TOP, dat deel bij toeval)."""
    asked = hit = 0
    chance = 0.0
    person_of: dict[int, set[str]] = {}
    for person, nums in groups.items():
        for num in nums:
            person_of.setdefault(num, set()).add(person)
    for num, hits in ranked.items():
        mine = person_of.get(num, set())
        others = set().union(*(groups[p] for p in mine)) - {num} if mine else set()
        if not others:
            continue
        asked += 1
        top = [h for h in hits if h != num][:TOP]
        hit += any(h in others for h in top)
        # toeval: TOP trekkingen zonder teruglegging uit total - 1 foto's
        rest = max(1, total - 1)
        none = 1.0
        for i in range(min(TOP, rest)):
            none *= max(0.0, (rest - len(others) - i) / (rest - i))
        chance += 1.0 - none
    if not asked:
        return 0, 0.0, 0.0
    return asked, hit / asked, chance / asked


async def quality(settings: Settings, personen: bool, email: str | None) -> int:
    if not personen:
        print(
            "De kwaliteitspoort van Keuze 83 (met de set) draait in de CI-job beeld-model. "
            "Hier: beeld-kwaliteit --personen.",
            file=sys.stderr,
        )
        return 2
    found = await _found(settings)
    if not _ready_or_say(found):
        return 1
    maker = _maker(settings)
    async with maker() as db:
        query = select(User.id).order_by(User.created_at)
        if email is not None:
            query = select(User.id).where(User.email == email.strip().lower())
        user_id = await db.scalar(query.limit(1))
        if user_id is None:
            print("Die eigenaar bestaat niet.", file=sys.stderr)
            return 1
        groups: dict[str, set[int]] = {}
        for person, num in (await db.execute(_PEOPLE_SQL, {"me": user_id.hex})).all():
            groups.setdefault(str(person), set()).add(int(num))
        groups = {p: nums for p, nums in groups.items() if len(nums) >= 2}
    if not groups:
        print("Geen bevestigde persoon met twee foto's of meer.", file=sys.stderr)
        return 1
    built = await vecfile.build(maker, settings.beeld_dir, user_id)
    ranked: dict[int, list[int]] = {}
    fd = vecfile.open_for_worker(settings.beeld_dir, user_id)
    if fd is None:
        print("Het bestand van zoeken op beeld is er niet.", file=sys.stderr)
        return 1
    try:
        for num in sorted(set().union(*groups.values())):
            args = wire.RankArgs(owner=user_id.hex, gen=built.gen, num=num, k=TOP + 1)
            try:
                data = await client.call(settings, Op.RANK, args, fds=[fd])
            except client.AnalyseFailed:
                continue  # geen vector voor deze foto
            ranked[num] = [int(h[0]) for h in wire.RankData.model_validate(data).hits]
    finally:
        os.close(fd)
    asked, rate, chance = same_person_rate(groups, ranked, built.rows)
    ok = asked > 0 and rate > chance
    write_result(settings, badmin.QUALITY_FILE, {"at": _now(), "ok": ok})
    print(
        f"beeld-kwaliteit --personen: {len(groups)} personen, {asked} vragen; dezelfde persoon in "
        f"de top {TOP}: {rate:.3f}, bij toeval {chance:.3f} ({'boven' if ok else 'niet boven'} "
        "toeval)"
    )
    return 0
