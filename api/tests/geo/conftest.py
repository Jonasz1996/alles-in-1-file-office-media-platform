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


@pytest.fixture(autouse=True)
def _aangemeld():
    """Een test eerder in de run kan search.hooks.reset() of previews.forget_ready() gedaan
    hebben (tests/extract, test_hotspots); dan ontbreken de start, de kopie en de keten na
    preview.ready van Z3. register() opnieuw, zoals app/geo/wire.py het vraagt; twee keer kan."""
    from app.geo import wire

    wire.register()


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
