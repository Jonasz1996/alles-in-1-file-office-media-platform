"""De rekenkant van de metingen van §4.5 (fase 6, I1): zoek_backfill --soort photometa en geo en
de invoer uit zoek_foto. Zonder server: alleen de functies die een uitkomst beoordelen of invoer
maken, zodat een fout in het oordeel niet pas in een lange meting opvalt.
"""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / "perf"))

import zoek_backfill as zb  # noqa: E402
import zoek_foto as zf  # noqa: E402

pytestmark = pytest.mark.zoek


# --- zoek_backfill: de poorten per soort ---


def test_de_poorten_van_paragraaf_4_5_per_soort() -> None:
    assert zb.POORTEN == {"worker": 1_000, "photometa": 5_000, "geo": 20_000}
    assert zb.POORT_PER_MINUUT == 1_000
    assert (zb.N["worker"], zb.N["photometa"], zb.N["geo"]) == (2_000, 5_000, 55_000)


@pytest.mark.parametrize(
    ("soort", "mediaan", "ok", "tekst"),
    [
        ("photometa", 4_999.9, False, "poort ≥ 5000: NIET gehaald"),
        ("photometa", 5_000.0, True, "poort ≥ 5000: gehaald"),
        ("geo", 19_999.0, False, "poort ≥ 20000: NIET gehaald"),
        ("geo", 23_000.0, True, "poort ≥ 20000: gehaald"),
        ("worker", 999.9, False, "poort ≥ 1000: NIET gehaald"),
        ("terugval", 1.0, None, "rapport"),
    ],
)
def test_oordeel_per_soort(soort: str, mediaan: float, ok: bool | None, tekst: str) -> None:
    assert zb.oordeel(soort, mediaan) == (ok, tekst)


def test_de_regel_van_de_tekst_blijft_zoals_in_ci() -> None:
    runs = [{"per_minuut_per_core": 1_500.0}, {"per_minuut_per_core": 1_400.0}]
    assert zb.kort_regel("worker", 1_450.0, runs) == (
        True,
        "tekst worker: mediaan 1450.0 documenten per minuut per core (1500.0, 1400.0); "
        "poort ≥ 1000: gehaald",
    )
    ok, regel = zb.kort_regel("geo", 21_000.0, [{"per_minuut_per_core": 21_000.0}])
    assert ok is True
    assert regel.startswith("land en plaats: mediaan 21000.0 punten per minuut per core")


def _vol(**over: int) -> dict[str, int]:
    stand = {"open": 0, "te_doen": 0, "gehasht": 100, "plekken": 85, "met_land": 60}
    stand.update(met_plaats=50, mislukt=0, met_gps=85)
    stand.update(over)
    return stand


def test_foto_fout_zegt_wat_er_niet_gemeten_is() -> None:
    assert zb.foto_fout("photometa", _vol(), 100) is None
    assert zb.foto_fout("geo", _vol(gehasht=0), 100) is None  # geo rekent geen pHash
    assert "niet klaar" in (zb.foto_fout("photometa", _vol(te_doen=3), 100) or "")
    assert "niet klaar" in (zb.foto_fout("geo", _vol(open=1), 100) or "")
    assert "geen enkele plek" in (zb.foto_fout("photometa", _vol(plekken=0), 100) or "")
    assert "pHash" in (zb.foto_fout("photometa", _vol(gehasht=10), 100) or "")
    assert "geen plek kreeg een land" in (zb.foto_fout("geo", _vol(met_land=0), 100) or "")


def test_geo_zonder_bronmap_stopt_met_uitleg(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        zb.main(["--soort", "geo", "--n", "10"])
    assert e.value.code == 2
    assert "tools/geo/haal.py" in capsys.readouterr().err


def test_bouw_places_zonder_bronnen_noemt_haal_py(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="tools/geo/haal.py"):
        zb.bouw_places(tmp_path / "leeg", tmp_path / "places.sqlite")
    assert not (tmp_path / "places.sqlite").exists()


# --- zoek_foto: de invoer ---


def test_punten_vast_en_meestal_bij_een_plaats() -> None:
    plaatsen = [(52.09, 5.12), (-33.87, 151.21), (64.0, 179.9)]
    a = zf.punten(400, zaad=3, plaatsen=plaatsen)
    assert a == zf.punten(400, zaad=3, plaatsen=plaatsen)
    assert a != zf.punten(400, zaad=4, plaatsen=plaatsen)
    for i, (lat, lon) in enumerate(a):
        assert -90 < lat < 90 and -180 <= lon < 180
        if i % 4 == 3:
            assert -60 <= lat <= 75
        else:
            # ±0,22° breedte en ±0,3° lengte om een plaats (lengte over de datumgrens heen)
            assert any(
                abs(lat - plat) <= 0.22 + 1e-6 and abs((lon - plon + 180) % 360 - 180) <= 0.3 + 1e-6
                for plat, plon in plaatsen
            )
    # Zonder places.sqlite: rond de vaste steden
    assert all(
        any(abs(lat - s[0]) <= 0.23 for s in zf.STEDEN)
        for i, (lat, _lon) in enumerate(zf.punten(40))
        if i % 4 != 3
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_fotos_delen_en_voorbeelden() -> None:
    fotos = list(zf.fotos(40, zaad=11))
    assert [f.naam for f in fotos] == [f"foto-{i:05d}.jpg" for i in range(40)]
    zonder = [i for i, f in enumerate(fotos) if f.gps is None]
    assert zonder == [4, 9, 14, 24, 29, 34]  # i % 5 == 4, behalve de kopieën (19, 39)
    for i in (19, 39):
        assert fotos[i].gps == fotos[i - 1].gps  # de kopie houdt de plek van de vorige
        groot = Image.open(io.BytesIO(fotos[i - 1].data)).size
        assert Image.open(io.BytesIO(fotos[i].data)).size < groot
    assert len({_sha(f.data) for f in fotos}) == 40  # elk een eigen blob
    for f in fotos[:3]:
        assert f.thumb is not None
        thumb = Image.open(io.BytesIO(f.thumb))
        assert thumb.format == "WEBP" and max(thumb.size) == 256
        assert Image.open(io.BytesIO(f.data)).size == (640, 480)


def test_kleine_fotos_elk_een_eigen_inhoud_met_gps() -> None:
    plekken = zf.punten(300, zaad=5)
    fotos = list(zf.kleine_fotos(plekken))
    assert len({_sha(f.data) for f in fotos}) == 300
    assert [f.gps for f in fotos] == plekken
    assert all(f.thumb is None for f in fotos)
    exif = Image.open(io.BytesIO(fotos[0].data)).getexif()
    assert exif.get_ifd(0x8825)  # GPSInfo
