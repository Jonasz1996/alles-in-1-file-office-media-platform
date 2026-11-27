"""Het checkpoint van het wissen houdt schrijvers niet op (fase 6, K2-08; X2b).

faces.wipe draait wal_checkpoint(TRUNCATE) op een eigen verbinding met busy_timeout 0, en probeert
het met backoff opnieuw (in een thread, nooit wachtend in SQLite). Met een open lezer lukt het
niet; een upload in die tijd wacht hooguit 100 ms langer dan zonder wissen.
"""

from __future__ import annotations

import asyncio
import gc
import statistics
import time

import pytest

from app import db as app_db
from app import jobs
from app.people.faces import wipe
from tests import test_files
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto, lezer
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
EXTRA = 0.100  # K2-08: hooguit zoveel langer
N = 15


async def uploads(client, h, parent, prefix: str) -> list[float]:
    """De tijd van N uploads. Zoals measuring() in tests/test_search.py: eerst een volle ronde van
    de garbage collector, en tijdens de meting geen; anders meet de grens een pauze van de rest
    van de suite (a0.md, Open na de nacontrole 1), niet het wissen."""
    took = []
    gc.collect()
    enabled = gc.isenabled()
    gc.disable()
    try:
        for i in range(N):
            start = time.perf_counter()
            r = await test_files.put(
                client, h, parent, f"{prefix}-{i}.txt", f"{prefix} {i}".encode()
            )
            took.append(time.perf_counter() - start)
            assert r.status_code == 201, r.text
    finally:
        if enabled:
            gc.enable()
    return took


@pytest.mark.sqlite_only("wal_checkpoint(TRUNCATE) met een open lezer bestaat alleen op SQLite")
async def test_een_upload_tijdens_het_wissen_met_een_open_lezer(
    client, settings, nep, monkeypatch, capsys
):
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        await session.commit()
    for i in range(3):
        await foto(settings, nep, user.id, f"{i}.jpg", [(A, 1), (B, 2)])
    await aan(settings, user.id)
    await draai(settings)
    h = await login(client, "eva@example.nl")
    parent = str(user.root_id)
    await uploads(client, h, parent, "warm")
    zonder = await uploads(client, h, parent, "zonder")

    monkeypatch.setattr(wipe, "CHECKPOINT_BUDGET", 3.0)
    reader = lezer(settings)
    tries: list[float] = []
    real = app_db.wal_truncate

    def counted(p):
        tries.append(time.monotonic())
        return real(p)

    monkeypatch.setattr(app_db, "wal_truncate", counted)
    try:
        job = asyncio.ensure_future(
            wipe.wipe_job(settings, {"owner": user.id.hex, "scope": "checkpoint"})
        )
        await asyncio.sleep(0.02)
        tijdens = await uploads(client, h, parent, "tijdens")
        busy_while = not job.done()
        with pytest.raises(jobs.Defer):
            await job
    finally:
        reader.execute("COMMIT")
        reader.close()
    # Het checkpoint probeerde het echt, terwijl de uploads liepen, en wacht op de lezer
    assert busy_while and len(tries) >= 3
    assert user.id.hex in wipe.WAITING
    base = statistics.median(zonder)
    worst = max(tijdens)
    with capsys.disabled():
        print(
            f"\nwipe_latency: zonder p50 {base * 1000:.1f} ms max {max(zonder) * 1000:.1f} ms; "
            f"tijdens p50 {statistics.median(tijdens) * 1000:.1f} ms "
            f"max {worst * 1000:.1f} ms; pogingen {len(tries)}"
        )
    assert worst - base <= EXTRA, (base, worst)
