"""De stand van de container analyse voor /beheer (fase 6, X2a; Keuze 59, Z0b).

Het deel dat de blokken "Gezichten" (X2b, AdminFaces) en "Zoeken op beeld" (X3, AdminBeeld)
delen: de stand, de reden, de modellen en de grenzen. Alles komt uit monitor.status(), dus uit
de laatste caps; deze module praat zelf nooit met de socket en kost geen query.

- analyse: off, down, models_missing of ok (monitor.AnalyseStatus.state).
- reason: bij down de sleutel van monitor.TEXT (key_missing, no_socket, sandbox …), bij
  models_missing de namen van de modellen (komma's); anders None. De web vertaalt de sleutel;
  problem is dezelfde reden als zin in het Nederlands (analyse_problem, voor het log en de cli).
- models: per regel van caps naam, versie en of de sha256 klopt. De versie is het begin van de
  sha256 uit models.lock (VERSION_CHARS tekens): die zegt precies welk bestand het is, ook voor
  een model zonder datum in de naam. Zonder caps (off, down) is de lijst leeg: dan weet de app
  niet wat er in de container staat.
- limits: cpus en geheugen van de container zoals de worker ze in zijn cgroup ziet; None zonder
  caps of zonder grenzen (geen cgroup-controllers).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.analyse import lock, monitor
from app.search.types import AnalyseLimits, AnalyseModel, AnalyseState

VERSION_CHARS = 12


@dataclass(frozen=True)
class AnalyseView:
    analyse: AnalyseState
    reason: str | None
    problem: str | None
    models: list[AnalyseModel]
    limits: AnalyseLimits | None


def _versions(lock_path: Path) -> dict[str, str]:
    try:
        return {m.name: m.sha256[:VERSION_CHARS] for m in lock.load(lock_path)}
    except (OSError, lock.LockError):
        return {}


def view(
    found: monitor.AnalyseStatus | None = None, lock_path: Path = lock.LOCK_PATH
) -> AnalyseView:
    """De stand voor /beheer; found None is monitor.status()."""
    found = monitor.status() if found is None else found
    reason = found.reason if found.state in ("down", "models_missing") else None
    caps = found.caps
    models: list[AnalyseModel] = []
    limits: AnalyseLimits | None = None
    if caps is not None:
        versions = _versions(lock_path)
        models = [
            AnalyseModel(name=m.name, version=versions.get(m.name, "?"), sha_ok=m.state == "ok")
            for m in caps.models
        ]
        if caps.limits is not None:
            limits = AnalyseLimits(cpus=caps.limits.cpus, mem_mb=caps.limits.mem_mb)
    return AnalyseView(
        analyse=found.state,
        reason=reason,
        problem=monitor.analyse_problem(found),
        models=models,
        limits=limits,
    )
