"""Tests met numpy en onnxruntime: de container analyse en zoeken op beeld (fase 6; K2-10).

Deze map staat buiten de testpaths van de job api (pyproject.toml: testpaths = ["tests"]). Die
job draait zonder de extra analyse, en een import van numpy faalt daar al bij het verzamelen,
nog vóór een marker telt. De job api-analyse draait deze map met de extra, in een eigen venv
(zodat test_no_numpy_in_app.py in de gewone venv blijft gelden):

    UV_PROJECT_ENVIRONMENT=.venv-analyse uv sync --frozen --extra analyse
    UV_PROJECT_ENVIRONMENT=.venv-analyse uv run pytest -q tests_analyse

Ontbreekt een pakket van de extra, of heeft het een andere versie dan uv.lock, dan stopt de run
meteen met een melding: overslaan is hier nooit goed.
"""

from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1]
# De vijf van de extra analyse (§3.11; test_no_numpy_in_app.py legt ze vast in uv.lock)
EXTRA = ("flatbuffers", "numpy", "onnxruntime", "packaging", "protobuf")


def pytest_configure(config):
    lock = tomllib.loads((API / "uv.lock").read_text(encoding="utf-8"))
    pinned = {p["name"]: p["version"] for p in lock["package"]}
    wrong = []
    for name in EXTRA:
        try:
            have = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            have = "ontbreekt"
        if have != pinned[name]:
            wrong.append(f"{name} {have} (uv.lock: {pinned[name]})")
    if wrong:
        raise pytest.UsageError(
            "tests_analyse vraagt de extra analyse: "
            + ", ".join(wrong)
            + ". Draai: UV_PROJECT_ENVIRONMENT=.venv-analyse uv sync --frozen --extra analyse"
        )
    import onnxruntime

    # Geen telemetrie (a0.md, Open 2), vóór de eerste sessie
    onnxruntime.disable_telemetry_events()


@pytest.fixture
def sessie():
    """Maak een InferenceSession van onnxruntime zoals de worker: alleen de CPU, één draad."""
    import onnxruntime

    def maak(model):
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        return onnxruntime.InferenceSession(
            model if isinstance(model, bytes) else str(model),
            options,
            providers=["CPUExecutionProvider"],
        )

    return maak
