"""Waar de plaatsen staan (review Z0, veilig F5): places_db en geo_src volgen data_dir, zoals
blob_dir, in plaats van vast /data/geo. Zo schrijft geo-load in een test of met een andere
datamap nooit naar /data. De omgeving (PLATFORM_GEO_SRC, PLATFORM_PLACES_DB) wint."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings

GEHEIM = "test" * 8


@pytest.fixture(autouse=True)
def _schone_omgeving(monkeypatch):
    for naam in ("PLATFORM_GEO_SRC", "PLATFORM_PLACES_DB"):
        monkeypatch.delenv(naam, raising=False)


def test_de_geo_paden_volgen_data_dir(tmp_path):
    s = Settings(data_dir=tmp_path, jwt_secret=GEHEIM)
    assert s.geo_src == tmp_path / "geo"
    assert s.places_db == tmp_path / "geo" / "places.sqlite"
    # Alleen de bronnen elders: de plaatsen staan erbij
    s = Settings(data_dir=tmp_path, geo_src=tmp_path / "bron", jwt_secret=GEHEIM)
    assert s.places_db == tmp_path / "bron" / "places.sqlite"


def test_de_omgeving_wint(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_GEO_SRC", "/elders/geo")
    monkeypatch.setenv("PLATFORM_PLACES_DB", "/elders/plaatsen.sqlite")
    s = Settings(data_dir=tmp_path, jwt_secret=GEHEIM)
    assert (s.geo_src, s.places_db) == (Path("/elders/geo"), Path("/elders/plaatsen.sqlite"))
