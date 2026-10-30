"""Het harnas van het verhaal zelf, zonder browser of server: de 2FA-code van Persoon."""

from __future__ import annotations

import time

import pytest

from verhaal import Persoon


@pytest.mark.parametrize("before", [0.1, 1.0, 4.0])
def test_code_vlak_voor_de_grens_van_een_stap(monkeypatch, before: float) -> None:
    """Een code van de stap vóór nu geldt alleen tot het einde van deze stap. Vlak voor de grens
    kiest code() dus een stap van nu of later: de server ziet de code pas iets later, en dan
    moet hij nog gelden (test_integratie test_10 faalde zo af en toe)."""
    from app import totp

    secret = totp.new_secret()
    boundary = (totp.current_step() + 10) * totp.STEP
    monkeypatch.setattr(time, "time", lambda: boundary - before)
    persoon = Persoon("Bram", "bram@example.com", "x", "user", secret=secret)
    code = persoon.code()
    assert code is not None
    assert totp.verify(secret, code, -1, now=boundary + 0.5) is not None


def test_drie_codes_per_stap_en_dan_herstelcodes(monkeypatch) -> None:
    """Midden in een stap: eerst de stap ervoor, dan nu en de volgende; daarna herstelcodes."""
    from app import totp

    secret = totp.new_secret()
    middle = (totp.current_step() + 10) * totp.STEP + totp.STEP / 2
    monkeypatch.setattr(time, "time", lambda: middle)
    persoon = Persoon("Bram", "bram@example.com", "x", "user", secret=secret, recovery=["r1"])
    now = totp.current_step()
    codes = [persoon.code() for _ in range(3)]
    assert codes == [totp.code_at(secret, step) for step in (now - 1, now, now + 1)]
    assert persoon.code() == "r1"
