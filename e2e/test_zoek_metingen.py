"""De rekenkant van de metingen van §4.5 (fase 6, I1): zoek_backfill --soort photometa en geo, de
poorten van zoek_samen en de invoer uit zoek_foto. Zonder server: alleen de functies die een
uitkomst beoordelen of invoer maken, zodat een fout in het oordeel niet pas in een lange meting
opvalt.
"""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / "perf"))

import zoek_backfill as zb  # noqa: E402
import zoek_foto as zf  # noqa: E402
import zoek_samen as zs  # noqa: E402

pytestmark = pytest.mark.zoek

GIB = 1024**3


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


# --- zoek_samen: de poorten van stap 2 ---


def _ds(**na: Any) -> dict[str, Any]:
    voor = {"geheugen": 2 * GIB, "oom": False, "status": "running", "herstarts": 0,
            "gestart": "2026-11-10T10:00:00Z"}  # fmt: skip
    return {"voor": voor, "na": {**voor, **na}, "host": True}


def _stap2(**over: Any) -> dict[str, Any]:
    procs = {"app": [100, 5], "worker": [120, 7]}
    stap = {
        "media": {"passed": True},
        "zoeken_p95_ms": 80.0,
        "files_root_p95_ms": 40.0,
        "health_p95_ms": 5.0,
        "mem_min_bytes": 3 * GIB,
        "ds": _ds(),
        "processen": {"voor": procs, "na": dict(procs)},
        "hervat_s": 12.0,
        "office": {"opslaan_s": 2.0, "verloren": 0},
    }
    stap.update(over)
    return stap


NUL = {"office": {"opslaan_s": 2.0, "verloren": 0}}


def test_alle_poorten_gemeten_en_gehaald() -> None:
    poorten = zs.oordeel(_stap2(), NUL)
    assert len(poorten) == 10
    assert all(ok is True for _, ok in poorten), poorten
    assert zs.geslaagd(poorten)


def test_een_poort_die_niet_te_meten_is_telt_niet_als_gehaald() -> None:
    poorten = zs.oordeel(_stap2(hervat_s=None), NUL)
    hervat = [ok for regel, ok in poorten if "leest weer" in regel]
    assert hervat == [None]
    assert not zs.geslaagd(poorten)


def test_zonder_office_zijn_de_poorten_van_onlyoffice_en_ds_niet_te_meten() -> None:
    poorten = zs.oordeel(_stap2(office=None, ds=None), {"office": None})
    niet = [regel for regel, ok in poorten if ok is None]
    assert len(niet) == 3
    assert all("zonder OnlyOffice" in regel for regel in niet)
    assert not zs.geslaagd(poorten)
    assert not zs.geslaagd(zs.oordeel(_stap2(office={"fout": "weg"}), NUL))


def test_onlyoffice_opslaan_en_verloren_woorden() -> None:
    traag = dict(zs.oordeel(_stap2(office={"opslaan_s": 2.5, "verloren": 0}), NUL))
    assert traag["OnlyOffice: opslaan ≤ 1,2 × de nulmeting (2.4 s)"] is False
    kwijt = dict(zs.oordeel(_stap2(office={"opslaan_s": 2.0, "verloren": 3}), NUL))
    assert kwijt["OnlyOffice: 0 verloren woorden"] is False


def test_ds_lees_uit_docker_inspect() -> None:
    info = {
        "HostConfig": {"Memory": 2147483648, "NanoCpus": 2_000_000_000},
        "State": {"Status": "running", "OOMKilled": False, "StartedAt": "t0"},
        "RestartCount": 0,
    }
    assert zs.ds_lees(info) == {"geheugen": 2 * GIB, "oom": False, "status": "running",
                                "herstarts": 0, "gestart": "t0"}  # fmt: skip
    assert zs.ds_lees({})["geheugen"] == 0


def test_ds_oordeel() -> None:
    assert zs.ds_oordeel(_ds()) == (True, "OOMKilled false, grens 2048 MiB")
    assert zs.ds_oordeel(_ds(oom=True)) == (False, "DS OOMKilled")
    ok, waarom = zs.ds_oordeel(_ds(gestart="t1", oom=False))  # herstart wist OOMKilled
    assert ok is False and "herstart" in waarom
    assert zs.ds_oordeel(_ds(status="exited"))[0] is False
    assert zs.ds_oordeel(None) == (None, "zonder OnlyOffice")
    zonder = _ds()
    zonder["voor"]["geheugen"] = 0
    ok, waarom = zs.ds_oordeel(zonder)
    assert ok is None and "geen geheugengrens" in waarom
    zonder["host"] = False
    assert "MemoryLimit false" in zs.ds_oordeel(zonder)[1]


def test_overleefd() -> None:
    voor = {"app": [100, 5], "worker": [120, 7]}
    assert zs.overleefd(voor, dict(voor)) == (True, "dezelfde processen")
    assert zs.overleefd(voor, {"app": [100, 5], "worker": [130, 9]}) == (
        False, "niet meer hetzelfde proces: worker")  # fmt: skip
    assert zs.overleefd(voor, {"app": None, "worker": None})[0] is False
    assert zs.overleefd(voor, {"app": [100, 6], "worker": [120, 7]})[0] is False  # pid hergebruikt
    assert zs.overleefd({"app": [100, 5], "worker": None}, voor)[0] is None
    assert zs.overleefd(None, voor)[0] is None


def test_kort_blok_noemt_elke_poort() -> None:
    stap = _stap2(office=None, ds=None)
    stap.update(stap="achtergrond", media={"passed": True, "samenvatting": ["fase b: ok"]},
                zoeken_n=10, mem_min_mb=3072, tekst_klaar_voor=0, tekst_klaar_na_media=5,
                tekst_klaar_na=9, fouten={}, ocr={"voor": {}, "na_media": {}, "na": {}},
                fotos={"voor": {"te_doen": 0}, "na_media": {"te_doen": 10},
                       "na": {"te_doen": 0, "gehasht": 300}},
                begin={"herbouw_hash": "gestart", "fotos_te_doen": 300})  # fmt: skip
    poorten = zs.oordeel(stap, {"office": None})
    uitkomst = {"soort": "zonder office", "opzet": "x", "duur_s": 1, "stappen": [stap],
                "poorten": poorten, "geslaagd": zs.geslaagd(poorten),
                "overgeslagen": ["de 20 scans voor X1 (geen tesseract)"]}  # fmt: skip
    blok = zs.kort_blok(uitkomst)
    poortregels = [r for r in blok if r.startswith("poort (stap achtergrond): ")]
    assert len(poortregels) == 10
    assert all(r.endswith((": gehaald", ": niet gehaald", ": niet te meten")) for r in poortregels)
    assert sum(r.endswith(": niet te meten") for r in poortregels) == 3
    assert blok[-1] == "uitkomst: niet geslaagd"
    assert "niet in deze meting: de 20 scans voor X1 (geen tesseract)" in blok
    assert any(
        "photometa te doen 0 → 300 (herbouw hash gestart) → 10 (na media) → 0, gehasht 300" in r
        for r in blok
    )
    # stap 1 (zonder herbouw): voor, na media en na
    stap.update(begin=None, fotos={"voor": {"te_doen": 0}, "na_media": {"te_doen": 0},
                                   "na": {"te_doen": 0}})  # fmt: skip
    assert "photometa te doen 0 → 0 (na media) → 0;" in zs.achtergrond_regel(stap)


def test_klaar_en_open_over_text_en_ocr() -> None:
    standen = {"text": {"done": 3, "wait": 2, "none": 1}, "ocr": {"busy": 1, "failed": 1}}
    assert zs.klaar_en_open(standen) == (5, 3)


def test_tekst_en_ocr_klaar_tellen_skip_niet_mee() -> None:
    """De foto's en de documenten die ocr niet leest, staan vanaf de backfill op skip: in de
    regels "tekst klaar" en "ocr klaar" is dat geen voortgang (review van I1, breken F3). Het
    hervatten telt skip wel mee (klaar_en_open), want dat kijkt naar het verschil."""
    # Zoals de --kort-run: 300 foto's (text skip), 300 documenten en 20 scans
    voor = {"text": {"skip": 300, "wait": 320}, "ocr": {"skip": 481, "wait": 139}}
    na_media = {"text": {"skip": 300, "done": 40, "wait": 280}, "ocr": {"skip": 481, "wait": 139}}
    na = {
        "text": {"skip": 300, "done": 300, "none": 20},
        "ocr": {"skip": 481, "done": 9, "busy": 1, "wait": 129},
    }
    assert zs.gelezen(voor["text"]) == 0
    assert zs.gelezen({"skip": 4, "done": 2, "none": 1, "failed": 1, "wait": 9}) == 4
    v = zs.voortgang(voor, na_media, na)
    assert (v["tekst_klaar_voor"], v["tekst_klaar_na_media"], v["tekst_klaar_na"]) == (0, 40, 320)
    assert v["ocr"] == {"voor": voor["ocr"], "na_media": na_media["ocr"], "na": na["ocr"]}
    stap = {"ocr": v["ocr"], "fotos": None, "processen": None}
    assert zs.achtergrond_regel(stap).startswith("ocr klaar 0 → 0 (na media) → 9, open 130;")
    assert zs.klaar_en_open(voor) == (781, 459)
