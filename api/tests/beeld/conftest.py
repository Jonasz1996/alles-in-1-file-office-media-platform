"""Fixtures voor zoeken op beeld aan de kant van de app (fase 6, X3b).

Elke test hier eindigt met beeld weer afgemeld (jobs.disable): een volgende test met de analyse
uit ziet dan 0.8.0. De fixture beeld zet de analyse aan met de nep-worker en de nep van beeld
(tests/beeld/nep.py), meldt alles van beeld aan (jobs.enable) en zet de stand van de monitor.
"""

from __future__ import annotations

import pytest

from app.analyse import monitor
from app.beeld import cache as bcache
from app.beeld import jobs as bjobs
from app.beeld import limits as blimits
from app.config import get_settings
from app.main import app
from tests.analyse import pgdb
from tests.analyse.fake_worker import FakeAnalyseWorker
from tests.beeld.nep import Nep
from tests.test_shares import person


def _clean() -> None:
    bjobs.disable()
    bcache.forget()
    blimits.reset()
    monitor._status = monitor.OFF
    monitor._task = None


@pytest.fixture(autouse=True)
def _beeld_weer_uit():
    _clean()
    yield
    _clean()


@pytest.fixture
async def beeld(tmp_path, database_url, client):
    """(nep, settings): de analyse aan met de nep, beeld aangemeld, de monitor op ok. De
    database is die van client (database_url; op PostgreSQL met ANALYSE_POSTGRES en de
    triggers van 0013)."""
    async with FakeAnalyseWorker(tmp_path / "run" / "worker.sock") as fake:
        nep = Nep(fake).install()
        on = fake.settings(
            tmp_path, rate_limits=False, beeld_rebuild_s=0, **pgdb.on_db(database_url)
        )
        await pgdb.ready(on)
        app.dependency_overrides[get_settings] = lambda: on
        monitor._status = await monitor.check(on)
        assert monitor.status().state == "ok", monitor.status()
        assert bjobs.enable(on)
        nep.settings = on  # type: ignore[attr-defined]
        yield nep, on
        _clean()


@pytest.fixture
async def mensen(client, admin):
    """Eva en Bram, elk (headers, id), naast Jonas (de beheerder)."""
    headers, _ = admin
    eva = await person(client, headers, "eva@example.nl")
    bram = await person(client, headers, "bram@example.nl")
    return eva, bram
