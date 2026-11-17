"""Hulp voor de tests van app/extract: één blob lezen zoals het kind dat doet (read.extract op
een open bestand), met de standaardgrenzen tenzij de test kleinere geeft."""

from __future__ import annotations

import itertools
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.extract import collect, read
from app.extract.result import Extracted

MAX = 1_048_576


def lees(
    data: bytes | Path, kind: str, *, max_chars: int = MAX, cpu_seconds: float = 30.0
) -> tuple[Extracted, str]:
    if isinstance(data, Path):
        with open(data, "rb") as f:
            return read.extract("sha", f, kind, max_chars=max_chars, cpu_seconds=cpu_seconds)
    with tempfile.TemporaryFile() as f:
        f.write(data)
        f.flush()
        f.seek(0)
        return read.extract("sha", f, kind, max_chars=max_chars, cpu_seconds=cpu_seconds)


def vaste_klok(monkeypatch) -> None:
    """De klok van een blob zonder de CPU-tijd van deze machine: elke keer dat de lezer kijkt
    (Clock.over), is er precies één seconde om. Een klok van n s is dan op bij de (n + 1)e keer
    kijken, op elke machine; de echte Clock, Collector, lezer en read.extract draaien gewoon."""
    tik = itertools.count()
    nep = SimpleNamespace(process_time=lambda: float(next(tik)))
    monkeypatch.setattr(collect, "time", nep)
