"""De fd's van een kind van de worker tellen, pas als het kind zelf klaar is met starten.

Net na de exec opent een kind zelf fd's op het eerste vrije nummer (4, want de bron staat op 3)
en sluit ze weer: de dynamische lader (/etc/ld.so.cache, libc.so.6), en een nep-ffmpeg in
Python ook zijn script, de map site-packages, .pyc's en de tijdzone. Een lijst op dat moment
toont een fd 4 die er even later niet meer is (gemeten met sleep: 3 van 1000 keer; met de
nep-ffmpeg onder een volle kern: 11 tot 17 van 200 keer, en één keer in CI op 0f40abb).

Een fd die van de worker meekwam, gaat nooit vanzelf dicht (sleep en de nep sluiten niets): die
staat er na de grens nog, en dan faalt de test zoals hij moet. Een begrensde lus, geen while."""

from __future__ import annotations

import asyncio
import os


def target(pid: int, fd: int) -> str:
    """Waar fd van pid naar wijst, voor de foutmelding."""
    try:
        return os.readlink(f"/proc/{pid}/fd/{fd}")
    except FileNotFoundError:
        return "(al weer dicht)"


def extra(pid: int, fds: list[int], want: list[int]) -> str:
    """De fd's die er te veel zijn, met hun doel (kort genoeg voor de melding van pytest)."""
    return " ".join(f"{n}={target(pid, n)}" for n in fds if n not in want)


async def fds_after_start(pid: int, want: list[int], limit_s: float = 5.0) -> list[int]:
    """De fd's van het kind zodra ze gelijk zijn aan want, of de laatste lijst na limit_s."""
    fds: list[int] = []
    for _ in range(int(limit_s / 0.01)):
        fds = sorted(int(n) for n in os.listdir(f"/proc/{pid}/fd"))
        if fds == want:
            return fds
        await asyncio.sleep(0.01)
    return fds
