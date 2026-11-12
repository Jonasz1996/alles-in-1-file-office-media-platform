"""analyse_contract.py tegen de echte worker (fase 6, X2 "De tests", laag 2; X2a).

Dezelfde scenario's als tegen de nep (tests/analyse/test_fake_analyse_worker.py). De echte worker
van X2a doet faces; een scenario dat group of rank vraagt (needs), staat niet in zijn lijst. X2b
(group) en X3 (rank) zetten hun opdracht in GEBOUWD, en de lijst groeit vanzelf.
"""

from __future__ import annotations

import pytest

from app.analyse.wire import Op
from app.analyse.worker import server
from tests.analyse import analyse_contract as C
from tests_analyse import echt, fake_models

pytestmark = [pytest.mark.anyio, pytest.mark.analyse_echt]

GEBOUWD = frozenset({Op.FACES})
LIJST = [s for s in C.SCENARIOS if C.required(s) <= GEBOUWD]


def test_de_lijst_volgt_wat_de_worker_doet():
    """GEBOUWD is wat de worker van X2a meldt (caps.built zonder caps en cancel); elk scenario
    zonder needs staat in de lijst, en geen enkel scenario wordt overgeslagen."""
    assert server.BUILT - {Op.CAPS, Op.CANCEL} == GEBOUWD == echt.EchteDriver.has
    assert [s.__name__ for s in LIJST] == [
        "caps_vorm",
        "handdruk_eigen_label",
        "eerst_hello",
        "hello_zonder_fds",
        "andere_protocol",
        "te_grote_boodschap",
        "onbekende_opdracht",
        "niet_gebouwd",
        "verkeerde_argumenten",
        "verkeerde_deadline",
        "verkeerd_aantal_fds",
        "cancel_onbekend",
    ]


@pytest.fixture(scope="module")
def driver(tmp_path_factory):
    models = fake_models.install(tmp_path_factory.mktemp("modellen") / "modellen")
    with echt.EchteWorker(tmp_path_factory.mktemp("worker"), models) as worker:
        yield echt.EchteDriver(worker)


@pytest.mark.parametrize("scenario", LIJST, ids=lambda s: s.__name__)
async def test_contract_echt(scenario, driver):
    try:
        await scenario(driver)
    finally:
        await driver.los()
