"""De wachter op search_access_mismatch (bouwontwerp fase 6, §4.1; test_no_mismatch.py).

De tweede controle (app/search/access.py, second_check) logt search_access_mismatch <node-id>
als het filter van zoeken een treffer doorliet die je niet mag zien. Dat mag nooit gebeuren:
tests/conftest.py laat daarom elke test falen die zo'n regel logt. Een test die hem expres
uitlokt (een deling die in hetzelfde verzoek wordt ingetrokken), vraagt de fixture
mismatch_verwacht; die test controleert de regel dan zelf.
"""

from __future__ import annotations

import logging

import pytest

LOGGER = "platform.search"
WORD = "search_access_mismatch"


class Mismatches(logging.Handler):
    """Verzamelt de regels met search_access_mismatch van de logger van zoeken (WARNING)."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if WORD in message:
            self.lines.append(message)


def check(lines: list[str], expected: bool) -> None:
    """Faalt de test als er regels zijn en de test ze niet verwachtte."""
    if lines and not expected:
        pytest.fail(
            f"{WORD} in de log ({len(lines)}×): het filter van zoeken liet een treffer door die "
            "de tweede controle weghaalde (§4.1). Een test die dat expres doet, vraagt de "
            f"fixture mismatch_verwacht. Eerste regel: {lines[0]}",
            pytrace=False,
        )
