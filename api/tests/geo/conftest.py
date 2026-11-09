from pathlib import Path

import pytest

HIER = Path(__file__).resolve().parent


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Elke test hier hoort bij zoeken en foto's (marker zoek, zoals tests/search/), zodat
    e2e/slim.sh ze ook op SQLite 3.46.1 draait; behalve die met de echte bronnen (marker geo,
    niet in de gewone run en niet in slim.sh: die draait de job api apart)."""
    for item in items:
        if Path(str(item.path)).parent != HIER or item.get_closest_marker("geo"):
            continue
        item.add_marker(pytest.mark.zoek)


@pytest.fixture
def plaatsen(settings, monkeypatch):
    """places.sqlite van deze test, gebouwd uit de kleine bronnen van tests/geo/bronnen.py."""
    from app.geo import load, places
    from tests.geo import bronnen

    b = bronnen.maak(settings.geo_src)
    bronnen.gebruik(monkeypatch, b)
    places.forget()
    assert load.ensure(settings) == "gebouwd"
    yield b
    places.forget()
