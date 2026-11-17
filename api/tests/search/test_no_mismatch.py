"""De wachter op search_access_mismatch (bouwontwerp fase 6, §4.1): een fixture in
tests/conftest.py laat elke test falen die de regel logt, behalve een test die hem expres
uitlokt en de fixture mismatch_verwacht vraagt (tests/search/mismatch.py)."""

from __future__ import annotations

import logging

import pytest

from app.search import access
from tests.search.mismatch import LOGGER, Mismatches, check


def test_de_tweede_controle_logt_waar_de_wachter_luistert():
    assert access.log.name == LOGGER


def test_de_wachter_staat_bij_elke_test_aan(_geen_mismatch):
    assert isinstance(_geen_mismatch, Mismatches)
    logging.getLogger(LOGGER).warning("search_access_mismatch %s", "abc")
    logging.getLogger(LOGGER).warning("search_suggest_hook_failed %s", "x")
    assert _geen_mismatch.lines == ["search_access_mismatch abc"]
    # Leeg, anders faalt deze test zelf (dat is het punt van de wachter)
    _geen_mismatch.lines.clear()


def test_een_regel_laat_de_test_falen():
    with pytest.raises(pytest.fail.Exception, match="mismatch_verwacht"):
        check(["search_access_mismatch abc"], expected=False)
    check([], expected=False)
    check(["search_access_mismatch abc"], expected=True)


def test_wie_hem_verwacht_mag_hem_loggen(mismatch_verwacht, _geen_mismatch):
    logging.getLogger(LOGGER).warning("search_access_mismatch %s", "abc")
    assert _geen_mismatch.lines == ["search_access_mismatch abc"]
