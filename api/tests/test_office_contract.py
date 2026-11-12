"""De contracttests van office (§4.4) met de nep-DS: elk scenario uit tests/office_contract.py
tegen de Driver en de App van tests/office_world.py. `pytest -k contract` draait ze."""

import pytest

from tests.office_contract import SCENARIOS
from tests.office_world import office_world

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        yield w


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.__name__)
async def test_contract(scenario, world):
    await scenario(world, world)
