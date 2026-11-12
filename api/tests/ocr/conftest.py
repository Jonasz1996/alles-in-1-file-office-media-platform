"""Fixtures voor de tests van OCR aan de kant van de app (X1). Geen ervan is autouse: de tests
van de worker in deze map (stap 1) gebruiken ze niet."""

from __future__ import annotations

import pytest

from app import analysis
from app.config import get_settings
from app.extract import step as extract_step
from app.main import app
from app.ocr import step as ocr_step
from app.routers import ocr as ocr_router
from app.search import hooks
from tests.test_shares import person


def _clean() -> None:
    analysis.reset()
    hooks.reset()
    ocr_step.reset()
    ocr_router.limits.reset()
    extract_step.register()
    ocr_step.register()


@pytest.fixture
def stappen():
    """De stappen text en ocr aangemeld (zoals main.py), en wat OCR onthoudt leeg."""
    _clean()
    yield
    _clean()


@pytest.fixture
def aan(settings, client, stappen):
    """Media aan (OCR draait): de instellingen van de pomp en van de API."""
    on = settings.model_copy(update={"media_enabled": True})
    app.dependency_overrides[get_settings] = lambda: on
    return on


@pytest.fixture
async def mensen(client, admin):
    """Eva en Bram, elk (headers, id), naast Jonas (de beheerder)."""
    headers, _ = admin
    eva = await person(client, headers, "eva@example.nl")
    bram = await person(client, headers, "bram@example.nl")
    return eva, bram
