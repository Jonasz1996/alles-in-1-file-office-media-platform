"""Fixtures voor de container analyse met de nep-worker (fase 6, A0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.analyse import monitor
from app.config import Settings
from tests.analyse.fake_worker import FakeAnalyseWorker

HIER = Path(__file__).resolve().parent


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Elke test hier krijgt marker analyse (§4.6): hij draait gewoon mee, met de nep-worker en
    zonder numpy. tryfirst: vóór de selectie op -m."""
    for item in items:
        if Path(str(item.path)).parent == HIER:
            item.add_marker(pytest.mark.analyse)


@pytest.fixture(autouse=True)
def _stand_terug():
    """De stand van de monitor is er één per proces: elke test begint en eindigt op off."""
    monitor._status = monitor.OFF
    monitor._task = None
    yield
    monitor._status = monitor.OFF
    monitor._task = None


@pytest.fixture
async def fake(tmp_path):
    async with FakeAnalyseWorker(tmp_path / "run" / "worker.sock") as worker:
        yield worker


@pytest.fixture
def settings_fake(tmp_path, fake) -> Settings:
    """Settings die de nep bereiken (fake.settings)."""
    return fake.settings(tmp_path)
