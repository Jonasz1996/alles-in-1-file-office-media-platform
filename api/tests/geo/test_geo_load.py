"""geo-load (bouwontwerp fase 6, Keuze 29): places.sqlite uit een kleine CSV en GeoJSON die de
test zelf maakt (tests/geo/bronnen.py). Een verkeerde sha256 of een kapot bestand: geen
plaatsen, en de app start gewoon."""

import contextlib
import importlib.util
import logging
import sqlite3
from pathlib import Path

import pytest

from app import main
from app.cli import main as cli_main
from app.config import get_settings
from app.geo import load, places
from app.main import app
from app.routers import uploads
from tests.geo import bronnen

REPO = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _vergeten():
    places.forget()
    yield
    places.forget()


def _haal():
    """tools/geo/haal.py als module (hij staat buiten het pakket app)."""
    spec = importlib.util.spec_from_file_location("haal", REPO / "tools" / "geo" / "haal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_de_bronnen_zijn_dezelfde_als_in_tools_geo():
    """Naam, URL, grootte en sha256 staan op twee plekken; ze moeten gelijk zijn."""
    haal = _haal()
    assert [tuple(b) for b in haal.BRONNEN] == [
        (b.naam, b.url, b.grootte, b.sha256) for b in (load.LANDEN, load.PLAATSEN)
    ]
    assert load.LANDEN.sha256 == "239eec57ac17f100a11e2536cffc56752c318b50ae765b0918ff7aab4ce8f255"
    assert load.PLAATSEN.sha256 == (
        "2a2e781b5f69376d922b78fe8978f1350c84fce0ddb07e02c834ecf98b57c75c"
    )
    assert (load.LANDEN.grootte, load.PLAATSEN.grootte) == (13_287_234, 2_246_559)
    assert "/v5.1.2/" in load.LANDEN.url and load.NE_VERSIE == "v5.1.2"


async def test_bouwt_plaatsen_en_landen(settings, plaatsen):
    pad = Path(settings.places_db)
    meta = load.read_meta(pad)
    assert meta["schema"] == load.SCHEMA
    assert (meta["sha_landen"], meta["sha_plaatsen"]) == (
        plaatsen.landen.sha256,
        plaatsen.plaatsen.sha256,
    )
    # Vier landen, vijf vlakken; zeven goede plaatsen (de kapotte regels zijn overgeslagen)
    assert (meta["landen"], meta["vlakken"], meta["plaatsen"]) == ("4", "5", "7")
    assert load.current(pad)
    # Nog eens: al goed, niet opnieuw
    assert load.ensure(settings) == "klaar"

    p = places.get(settings)
    utrecht = p.lookup(52.0907, 5.1214)
    assert (utrecht.name, utrecht.admin1, utrecht.cc) == ("Utrecht", "Utrecht", "NL")
    assert utrecht.id == load.place_id("NL", "Utrecht", "Utrecht", 52.09083, 5.12222)
    assert utrecht.km < 0.1
    # In het gat van NL (een meer): geen land, dus ook geen plaats
    assert p.lookup(*bronnen.MEER) == places.NIETS
    assert p.lookup(*bronnen.ZEE) == places.NIETS
    # Op land zonder geldige code: ook niets
    assert p.lookup(*bronnen.ZONDER_CODE) == places.NIETS
    # Bij de grens: de dichtstbijzijnde plaats in hetzelfde land, niet de nog nabijere in BE
    grens = p.lookup(*bronnen.GRENS)
    assert (grens.name, grens.cc) == ("Grensdorp", "NL")
    assert 4.9 < grens.km < 5.1
    # Over 180° heen: het land aan deze kant, de plaats aan de andere
    over = p.lookup(*bronnen.OVER_180)
    assert (over.name, over.cc) == ("Oostkaap", "FJ")
    assert 15 < over.km < 17
    # Te ver van elke plaats (> 30 km), wel in NL: alleen het land
    leeg = p.lookup(53.4, 7.0)
    assert (leeg.id, leeg.name, leeg.cc) == (None, None, "NL")
    # Namen van de landen: Nederlands als het kan, anders Engels
    assert p.countries(["NL", "FJ", "ZZ", "nl"]) == {"NL": "Nederland", "FJ": "Fiji"}
    assert {r.name for r in p.names([utrecht.id, 12345]).values()} == {"Utrecht"}


async def test_de_ids_blijven_bij_een_nieuwe_bouw(settings, plaatsen):
    utrecht = places.get(settings).lookup(52.0907, 5.1214).id
    assert load.ensure(settings, force=True) == "gebouwd"
    assert places.get(settings).lookup(52.0907, 5.1214).id == utrecht


def _in_geo(settings) -> list[str]:
    """Wat er in geo_src staat (places.sqlite en places.sqlite.part horen er niet bij)."""
    return sorted(p.name for p in Path(settings.geo_src).iterdir())


def _meldt(caplog, tekst: str) -> None:
    regels = [r.getMessage() for r in caplog.records if r.name == "platform.geo"]
    assert any(tekst in r for r in regels), regels


@pytest.mark.parametrize(
    ("geval", "landen", "plaatsen_data", "reden"),
    [
        ("geen json", b"{kapot", None, "landen: geen JSON"),
        ("geen featurecollection", b'{"type": "Feature"}', None, "landen: geen FeatureCollection"),
        ("punt buiten de wereld", None, None, "landen: een punt buiten de wereld"),
        ("geen tar.gz", None, b"dit is geen archief", "plaatsen: geen tar.gz"),
        ("csv ontbreekt", None, b"leeg", "plaatsen: rg_cities1000.csv ontbreekt"),
        ("csv is een symlink", None, "symlink", "rg_cities1000.csv is geen gewoon bestand"),
        ("andere kolommen", None, b"a,b\n1,2\n", "plaatsen: andere kolommen"),
        ("geen utf-8", None, b"lat,lon,name,admin1,admin2,cc\n1,2,\xff,,,NL\n", "kapot"),
    ],
)
async def test_een_kapotte_bron_geeft_geen_plaatsen(
    settings, monkeypatch, caplog, geval, landen, plaatsen_data, reden
):
    """Met een kloppende sha256 (zoals een kwaadwillende bron die toch door de controle komt),
    maar een kapotte inhoud: geen places.sqlite, een regel in het log, geen uitzondering."""
    if geval == "punt buiten de wereld":
        landen = bronnen.geojson().replace(b"[2.5, 49.5]", b"[2.5, 95.5]", 1)
    if plaatsen_data == "symlink":
        plaatsen_data = bronnen.tar_gz(symlink=True)
    elif plaatsen_data == b"leeg":
        plaatsen_data = _tar_zonder_csv()
    elif plaatsen_data is not None and geval in ("andere kolommen", "geen utf-8"):
        plaatsen_data = bronnen.tar_gz(plaatsen_data)
    b = bronnen.maak(settings.geo_src, landen, plaatsen_data)
    bronnen.gebruik(monkeypatch, b)
    caplog.set_level(logging.INFO, "platform.geo")
    assert load.ensure(settings) == "fout"
    _meldt(caplog, reden)
    assert _in_geo(settings) == sorted([load.LANDEN.naam, load.PLAATSEN.naam])  # geen .part
    assert places.get(settings) is None


def _tar_zonder_csv() -> bytes:
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        lid = tarfile.TarInfo("reverse_geocoder-1.5.1/README")
        lid.size = 2
        tar.addfile(lid, io.BytesIO(b"hi"))
    return buf.getvalue()


async def test_verkeerde_sha256(settings, monkeypatch, caplog):
    """De bronnen zelf zijn goed, maar de app verwacht andere (de echte sha256): niets."""
    bronnen.maak(settings.geo_src)
    caplog.set_level(logging.INFO, "platform.geo")
    assert load.ensure(settings) == "fout"
    _meldt(caplog, "verkeerde sha256")
    assert places.get(settings) is None


async def test_een_mislukte_nieuwe_bouw_laat_de_oude_staan(settings, plaatsen, monkeypatch):
    (Path(settings.geo_src) / load.PLAATSEN.naam).write_bytes(b"anders")
    assert load.ensure(settings, force=True) == "fout"
    assert places.get(settings).lookup(52.0907, 5.1214).name == "Utrecht"


async def test_zonder_bronnen_en_met_plaatsen_uit(settings, monkeypatch):
    assert load.ensure(settings) == "geen_bron"
    b = bronnen.maak(settings.geo_src)
    bronnen.gebruik(monkeypatch, b)
    settings.places = "uit"
    assert load.ensure(settings) == "uit"
    assert _in_geo(settings) == sorted([load.LANDEN.naam, load.PLAATSEN.naam])
    assert places.get(settings) is None


async def test_places_sqlite_van_een_ander_schema(settings, plaatsen):
    db = sqlite3.connect(settings.places_db)
    db.execute("UPDATE meta SET value = '0' WHERE key = 'schema'")
    db.commit()
    db.close()
    places.forget()
    assert places.get(settings) is None
    assert not load.current(Path(settings.places_db))
    # Bij de volgende start: opnieuw gebouwd
    assert load.ensure(settings) == "gebouwd"
    assert places.get(settings) is not None


async def test_cli_geo_load(settings, monkeypatch, capsys):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("PLATFORM_JWT_SECRET", "test" * 8)
    get_settings.cache_clear()
    try:
        assert cli_main(["geo-load"]) == 1
        assert "De bronnen staan niet in" in capsys.readouterr().err
        bronnen.gebruik(monkeypatch, bronnen.maak(settings.geo_src))
        assert cli_main(["geo-load"]) == 0
        assert "gebouwd" in capsys.readouterr().out
        assert cli_main(["geo-load"]) == 0
        assert "hoort al bij deze bronnen" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()


@contextlib.asynccontextmanager
async def _gestart(tmp_path, monkeypatch, **env):
    for name, value in {
        "PLATFORM_DATA_DIR": str(tmp_path),
        "PLATFORM_WEB_DIST": str(tmp_path / "web"),
        "PLATFORM_JWT_SECRET": "test" * 8,
        **env,
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    async def idle(_settings):
        import asyncio

        await asyncio.sleep(3600)

    monkeypatch.setattr(uploads, "cleanup_loop", idle)
    try:
        async with main.lifespan(app):
            yield get_settings()
    finally:
        get_settings.cache_clear()
        for name in ("certs", "security"):
            with contextlib.suppress(AttributeError, KeyError):
                delattr(app.state, name)


async def _bouw_klaar() -> None:
    import asyncio

    while load._taken:
        await asyncio.gather(*list(load._taken))


async def test_de_app_start_met_een_kapotte_bron(tmp_path, monkeypatch, caplog):
    """Bij de start bouwt de app op de achtergrond; een kapotte bron: een regel in het log,
    geen plaatsen, en de app draait."""
    b = bronnen.maak(tmp_path / "geo", plaatsen=b"kapot")
    bronnen.gebruik(monkeypatch, b)
    caplog.set_level(logging.INFO, "platform.geo")
    async with _gestart(tmp_path, monkeypatch) as s:
        await _bouw_klaar()
        assert places.get(s) is None
    _meldt(caplog, "Plaatsnamen: niet geladen")


async def test_de_app_bouwt_bij_de_start(tmp_path, monkeypatch, caplog):
    bronnen.gebruik(monkeypatch, bronnen.maak(tmp_path / "geo"))
    caplog.set_level(logging.INFO, "platform.geo")
    async with _gestart(tmp_path, monkeypatch) as s:
        await _bouw_klaar()
        assert places.get(s).lookup(52.0907, 5.1214).name == "Utrecht"
    _meldt(caplog, "Plaatsnamen geladen: 7 plaatsen, 4 landen (5 vlakken)")


async def test_plaatsen_uit_bouwt_niets_bij_de_start(tmp_path, monkeypatch):
    bronnen.gebruik(monkeypatch, bronnen.maak(tmp_path / "geo"))
    async with _gestart(tmp_path, monkeypatch, PLATFORM_PLACES="uit") as s:
        assert not load._taken
        assert places.get(s) is None
    assert not (tmp_path / "geo" / "places.sqlite").exists()
