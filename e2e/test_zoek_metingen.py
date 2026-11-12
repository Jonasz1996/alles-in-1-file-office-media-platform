"""De rekenkant van de metingen van §4.5 (fase 6, I1): zoek_backfill --soort photometa en geo, de
poorten van zoek_samen en de invoer uit zoek_foto. Zonder server: alleen de functies die een
uitkomst beoordelen of invoer maken, zodat een fout in het oordeel niet pas in een lange meting
opvalt.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sqlite3
import sys
from collections import Counter
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
KAN = {"MemoryLimit": True, "CPUCfsQuota": True}  # docker_grenzen() op een gewone host


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
    return {"voor": voor, "na": {**voor, **na}, "host": KAN}


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
        "office": _office(),
    }
    stap.update(over)
    return stap


def _office(**over: Any) -> dict[str, Any]:
    """Een volledige ronde van 3 bewerkers op 1 document naast de belastingtest (zoals stap() hem
    na bewerkers() heeft)."""
    return {"bewerkers": 3, "open": 3, "opslaan_s": 2.0, "getypt": 10, "opgeslagen": 10,
            "verloren": 0, "status2": [1], "weinig_geheugen": False,
            "na_de_start_van_media": True, **over}  # fmt: skip


NUL = {"office": _office()}


def test_alle_poorten_gemeten_en_gehaald() -> None:
    poorten = zs.oordeel(_stap2(), NUL)
    assert len(poorten) == 10
    assert all(ok is True for _, ok in poorten), poorten
    assert zs.geslaagd(poorten)


def test_de_grenzen_van_paragraaf_4_5_deel_b() -> None:
    """De poorten van §4.5 (deel B) letterlijk, en elke grens precies: erop is gehaald, erover
    niet."""
    assert zs.POORT == {"zoeken_p95_ms": 200.0, "files_root_p95_ms": 200.0,
                        "health_p95_ms": 50.0, "mem_min_bytes": 1.5 * GIB, "hervat_s": 90.0,
                        "opslaan_factor": 1.2}  # fmt: skip
    for sleutel, op, erover in (
        ("zoeken_p95_ms", 200.0, 200.1),
        ("files_root_p95_ms", 200.0, 200.1),
        ("health_p95_ms", 50.0, 50.1),
        ("mem_min_bytes", 1.5 * GIB, 1.5 * GIB - 1),
        ("hervat_s", 90.0, 90.1),
    ):
        assert zs.geslaagd(zs.oordeel(_stap2(**{sleutel: op}), NUL)), sleutel
        assert not zs.geslaagd(zs.oordeel(_stap2(**{sleutel: erover}), NUL)), sleutel
    assert zs.geslaagd(zs.oordeel(_stap2(office=_office(opslaan_s=2.4)), NUL))
    assert not zs.geslaagd(zs.oordeel(_stap2(office=_office(opslaan_s=2.41)), NUL))


@pytest.mark.parametrize("soort", ["zoeken", "files_root", "health"])
def test_een_mislukt_verzoek_haalt_de_poort_niet(soort: str) -> None:
    """Een mislukt verzoek zit niet in de p95. In M7 kreeg het laatste deel van stap 3 alleen
    401's (het token van de stap verliep): de p95 van wat lukte, haalde dan de poort."""
    poorten = dict(zs.oordeel(_stap2(fouten={soort: 3}), NUL))
    mislukt = [(regel, ok) for regel, ok in poorten.items() if regel.endswith("(3 mislukt)")]
    assert len(mislukt) == 1 and mislukt[0][1] is False
    assert not zs.geslaagd(list(poorten.items()))
    assert zs.geslaagd(zs.oordeel(_stap2(fouten={soort: 0}), NUL))


def test_de_poort_van_de_app_noemt_de_eerste_pagina() -> None:
    regels = [regel for regel, _ in zs.oordeel(_stap2(), NUL)]
    assert "GET /files/root?limit=200 p95 ≤ 200 ms" in regels


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
    traag = dict(zs.oordeel(_stap2(office=_office(opslaan_s=2.5)), NUL))
    assert traag["OnlyOffice: opslaan ≤ 1,2 × de nulmeting (2.4 s)"] is False
    kwijt = dict(zs.oordeel(_stap2(office=_office(verloren=3)), NUL))
    assert kwijt["OnlyOffice: 0 verloren woorden"] is False


@pytest.mark.parametrize(
    ("over", "reden"),
    [
        ({"open": 0}, "0 van 3 bewerkers open"),
        ({"bewerkers": 0, "open": 0}, "0 van 0 bewerkers open"),
        ({"status2": [0]}, "status 2 per document: [0]"),
        ({"status2": [1, 2]}, "status 2 per document: [1, 2]"),
        ({"status2": []}, "status 2 per document: []"),
        ({"getypt": 0, "opgeslagen": 0}, "niets getypt"),
        ({"weinig_geheugen": True}, "te weinig geheugen"),
        ({"weinig_geheugen": None}, "te weinig geheugen"),
        ({"na_de_start_van_media": False}, "de belastingtest liep niet"),
        ({"na_de_start_van_media": None}, "de belastingtest liep niet"),
    ],
)
def test_een_onvolledige_ronde_haalt_de_poorten_van_onlyoffice_niet(
    over: dict[str, Any], reden: str
) -> None:
    """Een ronde waarin niet iedereen opende of opsloeg, telt niet: in M7 haalde een ronde met 0
    bewerkers de poorten (opslaan ±0 s, 0 verloren woorden). In stap 2 is dat niet gehaald, in de
    nulmeting niet te meten; de regel zegt waarom."""
    assert zs.onvolledig(_office()) is None
    assert reden in (zs.onvolledig(_office(**over)) or "")
    for sleutel, reden in (("weinig_geheugen", "te weinig geheugen"),
                           ("na_de_start_van_media", "de belastingtest liep niet")):  # fmt: skip
        zonder = _office()
        del zonder[sleutel]  # niet gemeten telt niet als in orde
        assert reden in (zs.onvolledig(zonder) or "")
    assert zs.onvolledig(None) == "geen bewerkers"
    assert zs.onvolledig({"fout": "de bewerkers kwamen niet terug"}) == (
        "de bewerkers kwamen niet terug")  # fmt: skip

    def oo(poorten: list[tuple[str, bool | None]]) -> list[tuple[str, bool | None]]:
        return [(regel, ok) for regel, ok in poorten if regel.startswith("OnlyOffice")]

    stap2 = oo(zs.oordeel(_stap2(office=_office(opslaan_s=0.0, **over)), NUL))
    assert [ok for _, ok in stap2] == [False, False]
    assert all(f"stap 2: {zs.onvolledig(_office(**over))}" in regel for regel, _ in stap2)
    nul = oo(zs.oordeel(_stap2(), {"office": _office(**over)}))
    assert [ok for _, ok in nul] == [None, True]
    assert "de nulmeting: " in nul[0][0]
    assert not zs.geslaagd(zs.oordeel(_stap2(), {"office": _office(**over)}))


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
    assert "start hem met e2e/office_ds.sh" in waarom
    zonder["host"] = {"MemoryLimit": False, "CPUCfsQuota": False}
    assert "MemoryLimit false" in zs.ds_oordeel(zonder)[1]


def test_docker_grenzen_zoals_office_ds_sh() -> None:
    """office_ds.sh zet --memory alleen als docker info MemoryLimit en CPUCfsQuota allebei true
    zegt; zonder CPUCfsQuota is "start hem met office_ds.sh" dus een raad die niet werkt (review
    van I1, regels F4)."""
    assert zs.grenzen_lees("true true\n") == KAN
    assert zs.grenzen_lees("true false") == {"MemoryLimit": True, "CPUCfsQuota": False}
    assert zs.grenzen_lees("") is None
    assert zs.grenzen_lees("true") is None
    sh = (Path(__file__).resolve().parent / "office_ds.sh").read_text()
    assert "'{{.MemoryLimit}} {{.CPUCfsQuota}}'" in sh and '= "true true" ]' in sh
    zonder_cfs = zs.zonder_grens({"MemoryLimit": True, "CPUCfsQuota": False})
    assert "CPUCfsQuota false" in zonder_cfs and "--memory 2g" in zonder_cfs
    assert "start hem met e2e/office_ds.sh" not in zonder_cfs
    assert "MemoryLimit false" in zs.zonder_grens({"MemoryLimit": False, "CPUCfsQuota": True})
    assert zs.zonder_grens(KAN).endswith("start hem met e2e/office_ds.sh (--memory 2g)")
    assert zs.zonder_grens(None).endswith("start hem met e2e/office_ds.sh (--memory 2g)")


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


# --- zoek_samen: wat main doet voordat er een server start ---


class _Gestart(Exception):
    """zoek_samen.start werd aangeroepen: alles daarvoor is gedaan."""


@pytest.fixture
def zonder_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """main tot aan de server: ffmpeg is er, tesseract niet, en start stopt de test."""

    def start(*_a: Any, **_kw: Any) -> None:
        raise _Gestart

    monkeypatch.setattr(zb, "media_beschikbaar", lambda: True)
    monkeypatch.setattr(zf, "scan_reden", lambda: "geen tesseract met nld, eng en osd")
    monkeypatch.setattr(zs, "start", start)


def _args(**over: Any) -> argparse.Namespace:
    return argparse.Namespace(**{"kort": False, "docs": 5_000, "fotos": 5_000, "scans": 500,
                                 **over})  # fmt: skip


def test_minder_dan_de_last_van_paragraaf_4_5_staat_erbij() -> None:
    """Zonder --kort met --docs, --fotos of --scans kleiner: de poorten zeggen daar niets over,
    dus staat het onder "niet in deze meting" (review van I1, regels F3)."""
    assert zs.kleiner_dan_45(_args()) == []
    assert zs.kleiner_dan_45(_args(fotos=6_000)) == []
    assert zs.kleiner_dan_45(_args(kort=True, docs=300, fotos=300, scans=0)) == []
    assert zs.kleiner_dan_45(_args(docs=2_000, scans=0)) == [
        "de last van §4.5: 2.000 documenten per stap in plaats van 5.000",
        "de last van §4.5: 0 scans voor X1 in plaats van 500",
    ]
    assert zs.kleiner_dan_45(_args(fotos=0)) == ["de last van §4.5: 0 foto's in plaats van 5.000"]


def test_een_kleinere_last_zonder_kort_wordt_gemeld(
    zonder_server: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(_Gestart):
        zs.main(["--docs", "2", "--fotos", "10", "--scans", "0"])
    uit = capsys.readouterr().out
    for regel in (
        "de last van §4.5: 2 documenten per stap in plaats van 5.000",
        "de last van §4.5: 10 foto's in plaats van 5.000",
        "de last van §4.5: 0 scans voor X1 in plaats van 500",
    ):
        assert f"zoek_samen: niet in deze meting: {regel}" in uit


def test_zonder_kort_en_zonder_tesseract_stopt_het_voor_de_server(zonder_server: None) -> None:
    """De volle meting van §4.5 heeft de scans nodig: zonder tesseract een duidelijke stop, nog
    voordat er documenten of een server zijn."""
    with pytest.raises(SystemExit) as e:
        zs.main(["--docs", "1"])  # klein: gaat het toch door, dan faalt de test snel
    assert "zonder --kort heeft de 500 scans van X1 nodig: geen tesseract" in str(e.value.code)


def test_kort_zonder_tesseract_slaat_de_scans_over(
    zonder_server: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(_Gestart):
        zs.main(["--kort", "--docs", "1"])
    uit = capsys.readouterr().out
    assert "zoek_samen: de scans voor X1 overgeslagen: geen tesseract" in uit
    assert "300 foto's, 0 scans" in uit
    assert "niet in deze meting: de last" not in uit


# --- zoek_samen: de processen, het begin van stap 2 en 3; zoek_backfill.stand per stap ---


class _Proc:
    def __init__(self, pid: int, klaar: int | None = None) -> None:
        self.pid, self._klaar = pid, klaar

    def poll(self) -> int | None:
        return self._klaar


class _Antwoord:
    def __init__(self, status: int, body: dict[str, Any] | None = None) -> None:
        self.status_code, self._body = status, body or {}

    def json(self) -> dict[str, Any]:
        return self._body


class _NepServer:
    """Wat zoek_samen van een Server gebruikt: proc, data_dir en call (opgeschreven)."""

    def __init__(self, proc: _Proc | None = None, herbouw: int = 202) -> None:
        self.proc, self.herbouw, self.data_dir = proc, herbouw, Path("/bestaat-niet")
        self.verzoeken: list[tuple[str, str, Any]] = []

    def call(self, _account: Any, method: str, path: str, json: Any = None, **_kw: Any) -> Any:
        self.verzoeken.append((method, path, json))
        if method == "PATCH":
            return _Antwoord(200, {"paused": json["paused"]})
        return _Antwoord(self.herbouw)


# pid → (ppid, starttijd, state): de app (100) met zijn forkserver (120) en de worker (300),
# die zelf forkt (250 en 400, en 500 onder 250): een fork erft de opdrachtregel van de worker
TABEL = {
    100: (1, 5, "S"),
    120: (100, 6, "S"),
    300: (100, 7, "S"),
    250: (300, 8, "S"),
    400: (300, 9, "S"),
    500: (250, 10, "S"),
    600: (1, 11, "S"),
}
WORKER = "python -m app.media.worker --socket /run/w.sock"
REGELS = {100: "uvicorn app.main:app", 120: "python -m app.render.forkserver", 300: WORKER,
          250: WORKER, 400: WORKER, 500: WORKER, 600: WORKER}  # fmt: skip


def test_processen_kiest_de_worker_zelf_en_niet_een_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    """De worker is het kind van de app met app.media.worker op de opdrachtregel dat zelf geen
    kind van een worker is; een lagere of hogere pid van een fork doet er niet toe (review van
    I1, breken F2). 600 is een worker buiten de app."""
    monkeypatch.setattr(zs, "process_table", lambda: dict(TABEL))
    monkeypatch.setattr(zs, "cmdline", lambda pid: REGELS.get(pid, ""))
    server = _NepServer(_Proc(100))
    assert zs.processen(server) == {"app": [100, 5], "worker": [300, 7]}
    zonder = {pid: rij for pid, rij in TABEL.items() if pid not in (250, 300, 400, 500)}
    monkeypatch.setattr(zs, "process_table", lambda: dict(zonder))
    assert zs.processen(server) == {"app": [100, 5], "worker": None}
    leeg = {"app": None, "worker": None}
    assert zs.processen(_NepServer(_Proc(100, klaar=0))) == leeg
    assert zs.processen(_NepServer(_Proc(999))) == leeg
    assert zs.processen(_NepServer(None)) == leeg


def test_de_worker_op_3_cores_en_de_app_niet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regel 14 van fase 5 (compose.media-limits.yaml: de worker cpus 3.0, een core vrij voor de
    app): aan het begin van een stap de worker (300) en al zijn kinderen op 0-2, de app (100),
    zijn forkserver (120) en een worker buiten de app (600) niet. In M7 (I1) liep het hele
    script onder taskset -c 0-2 en deelde de app de cores van ffmpeg."""
    gezet: dict[int, set[int]] = {}
    affiniteit = {0: {0, 1, 2, 3}, 100: {0, 1, 2, 3}}
    monkeypatch.setattr(zs, "process_table", lambda: dict(TABEL))
    monkeypatch.setattr(zs, "cmdline", lambda pid: REGELS.get(pid, ""))
    monkeypatch.setattr(zs, "set_affinity", lambda pid, cores: gezet.__setitem__(pid, set(cores)))
    monkeypatch.setattr(zs.os, "sched_getaffinity", lambda pid: affiniteit[pid])
    server = _NepServer(_Proc(100))
    assert zs.worker_op_zijn_cores(server, wacht_s=0) == {"worker": [0, 1, 2], "app": [0, 1, 2, 3]}
    assert gezet == {pid: {0, 1, 2} for pid in (300, 250, 400, 500)}
    # een machine met minder cores: de worker krijgt wat er is
    gezet.clear()
    affiniteit[0] = {0, 1}
    assert zs.worker_op_zijn_cores(server, wacht_s=0)["worker"] == [0, 1]
    assert set(gezet) == {300, 250, 400, 500}
    # geen worker (ook niet na het wachten): niets gezet, en de uitkomst zegt het
    gezet.clear()
    zonder = {pid: rij for pid, rij in TABEL.items() if pid not in (250, 300, 400, 500)}
    monkeypatch.setattr(zs, "process_table", lambda: dict(zonder))
    assert zs.worker_op_zijn_cores(server, wacht_s=0) == {"worker": None, "app": [0, 1, 2, 3]}
    assert gezet == {}


def test_de_cores_wachten_op_de_worker_na_de_herstart(monkeypatch: pytest.MonkeyPatch) -> None:
    """seeded() start de app opnieuw; de worker komt iets later. Zonder wachten bleef hij in die
    stap op alle cores."""
    rij: list[dict[str, Any]] = [{"app": [100, 5], "worker": None}] * 3
    rij.append({"app": [100, 5], "worker": [300, 7]})
    gezet: list[int] = []
    monkeypatch.setattr(zs, "processen", lambda _server: rij.pop(0) if len(rij) > 1 else rij[0])
    monkeypatch.setattr(zs, "process_table", lambda: {300: (100, 7, "S")})
    monkeypatch.setattr(zs, "set_affinity", lambda pid, _cores: gezet.append(pid))
    monkeypatch.setattr(zs.time, "sleep", lambda _s: None)
    monkeypatch.setattr(zs.os, "sched_getaffinity", lambda _pid: {0, 1, 2, 3})
    assert zs.worker_op_zijn_cores(_NepServer(_Proc(100)))["worker"] == [0, 1, 2]
    assert gezet == [300]


def test_onder_taskset_deelt_de_app_de_cores_van_de_worker(
    zonder_server: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Draait het script zelf op alleen de cores van de worker (taskset -c 0-2 rond het script,
    zoals M7) terwijl de machine er meer heeft, dan staat dat onder "niet in deze meting"."""
    monkeypatch.setattr(zs.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(zs.os, "sched_getaffinity", lambda _pid: {0, 1, 2})
    regel = "een core voor de app (regel 14): het script draait op cores 0-2 van 4, dus de app "
    regel += "deelt de cores van de worker"
    assert zs.deelt_de_cores_van_de_worker() == regel
    with pytest.raises(_Gestart):
        zs.main(["--docs", "2", "--scans", "0"])
    assert f"zoek_samen: niet in deze meting: {regel}" in capsys.readouterr().out
    with pytest.raises(_Gestart):
        zs.main(["--kort", "--docs", "1"])  # --kort (CI, onder taskset): een rapport
    assert "regel 14" not in capsys.readouterr().out
    monkeypatch.setattr(zs.os, "sched_getaffinity", lambda _pid: {0, 1, 2, 3})
    assert zs.deelt_de_cores_van_de_worker() is None
    monkeypatch.setattr(zs.os, "cpu_count", lambda: 3)
    monkeypatch.setattr(zs.os, "sched_getaffinity", lambda _pid: {0, 1, 2})
    assert zs.deelt_de_cores_van_de_worker() is None


def test_een_stap_zet_de_worker_op_zijn_cores_voor_hij_de_processen_peilt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stap(): eerst de cores (regel 14), dan de processen; de cores staan in de uitkomst en in
    het blok kort."""
    gebeurd: list[str] = []
    stand = {"text": {}, "ocr": {}}
    cores = {"worker": [0, 1, 2], "app": [0, 1, 2, 3]}

    def worker_op_zijn_cores(_server: Any) -> dict[str, Any]:
        gebeurd.append("cores")
        return cores

    def processen(_server: Any) -> dict[str, Any]:
        gebeurd.append("processen")
        return {"app": [100, 5], "worker": [300, 7]}

    monkeypatch.setattr(zs, "analyse_stand", lambda _server: stand)
    monkeypatch.setattr(zs, "worker_op_zijn_cores", worker_op_zijn_cores)
    monkeypatch.setattr(zs, "processen", processen)
    monkeypatch.setattr(zs, "zoekers", lambda *_a: [])
    monkeypatch.setattr(zs, "belastingtest", lambda *_a: {"passed": True, "summary": ["ok"]})
    opzet = zs.Opzet(server=_NepServer(_Proc(100)), admin=object())  # type: ignore[arg-type]
    args = argparse.Namespace(sessies=1, duur=60, bewerkers=0)
    uit = zs.stap(opzet, "pauze", args, ["a"], Path("/bestaat-niet"), 1)
    assert gebeurd[:2] == ["cores", "processen"]
    assert uit["cores"] == cores
    assert "cores: worker 0-2, app 0-3" in zs.achtergrond_regel(uit)
    assert zs.cores_tekst([0, 2]) == "0,2"
    assert zs.cores_tekst(None) == "-"


def _opzet(server: _NepServer, fotos: int) -> Any:
    return zs.Opzet(server=server, admin=object(), fotos=fotos)


def test_pauze_eraf_en_dan_de_herbouw_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Het begin van stap 2 en 3: eerst de pauze eraf, dan (met foto's) POST rebuild hash, zodat
    photometa alle foto's opnieuw leest; zonder dat zou stap 2 geen photometa meten (review van
    I1, breken F2)."""
    monkeypatch.setattr(zs, "foto_stand", lambda _server: {"open": 1, "te_doen": 300})
    eraf = ("PATCH", "/api/v1/admin/search", {"paused": False})
    herbouw = ("POST", "/api/v1/admin/search/rebuild", {"what": "hash"})
    zonder_fotos = _NepServer()
    assert zs.pauze_eraf(_opzet(zonder_fotos, 0)) is None
    assert zonder_fotos.verzoeken == [eraf]
    met = _NepServer()
    assert zs.pauze_eraf(_opzet(met, 300)) == {"herbouw_hash": "gestart", "fotos_te_doen": 300}
    assert met.verzoeken == [eraf, herbouw]
    bezig = _NepServer(herbouw=409)
    assert zs.pauze_eraf(_opzet(bezig, 300)) == {"herbouw_hash": "liep nog", "fotos_te_doen": 300}


def test_stand_per_stap_van_de_analyse(tmp_path: Path) -> None:
    con = sqlite3.connect(tmp_path / "platform.db")
    con.execute("CREATE TABLE analysis (step TEXT, state TEXT)")
    rijen = [("text", "done")] * 2 + [("text", "wait"), ("ocr", "busy")] + [("ocr", "skip")] * 3
    con.executemany("INSERT INTO analysis VALUES (?, ?)", rijen)
    con.commit()
    con.close()
    assert zb.stand(tmp_path) == Counter({"done": 2, "wait": 1})
    assert zb.stand(tmp_path, "ocr") == Counter({"skip": 3, "busy": 1})
    assert zb.stand(tmp_path, "hash") == Counter()
    leeg = tmp_path / "leeg"
    leeg.mkdir()
    sqlite3.connect(leeg / "platform.db").close()  # de server start net: nog geen tabellen
    assert zb.stand(leeg, "ocr") == Counter()


# --- zoek_foto: de standen op het echte schema (review van I1, breken F1 en regels F2) ---


@pytest.fixture
def leeg(tmp_path: Path) -> tuple[Path, str, str]:
    """Een gemigreerde database zonder server (zoals een gestopte server na make_admin): één
    eigenaar met zijn hoofdmap. Geeft (data_dir, eigenaar, hoofdmap)."""
    import asyncio
    import uuid
    from concurrent.futures import ThreadPoolExecutor

    from app import db
    from app.config import Settings
    from app.migrate import upgrade
    from app.models import Node, User
    from seed import connect

    data = tmp_path / "data"
    data.mkdir()
    settings = Settings(data_dir=data, database_url="", web_dist=tmp_path / "web")

    async def migreer() -> None:
        await upgrade(settings)
        await db.get_engine(settings).dispose()

    # In een eigen draad met een eigen lus: na een test met een browser draait de lus van
    # Playwright nog in deze draad, en dan weigert asyncio.run (CI run 42890910950)
    with ThreadPoolExecutor(1) as pool:
        pool.submit(asyncio.run, migreer()).result()
    db._engine.cache_clear()
    eigenaar, root = uuid.uuid4(), uuid.uuid4()
    with connect(data) as conn:
        conn.execute(User.__table__.insert(), [{"id": eigenaar, "email": "foto@example.nl",
                     "display_name": "Foto", "password_hash": "x", "role": "admin"}])  # fmt: skip
        conn.execute(Node.__table__.insert(), [{"id": root, "owner_id": eigenaar, "name": "",
                     "kind": "dir", "size": 0}])  # fmt: skip
        conn.execute(User.__table__.update().values(root_id=root))
    return data, str(eigenaar), str(root)


def _photometa_todo(data_dir: Path) -> tuple[int, int]:
    """(blobs, plekken) die photometa zelf te doen heeft: zijn _todo() en _nodes_todo(), de
    twee fasen van zijn keten, rechtstreeks op de database."""
    from sqlalchemy import func, select

    from app import photometa
    from app.models import Blob
    from seed import connect

    def tel(conn: Any, waar: tuple[Any, ...]) -> int:
        return conn.execute(select(func.count()).select_from(Blob).where(*waar)).scalar_one()

    with connect(data_dir) as conn:
        return tel(conn, photometa._todo()), tel(conn, photometa._nodes_todo())


N_FOTOS = 20  # met een kopie (19) en drie zonder GPS (4, 9 en 14)


@pytest.mark.parametrize(
    ("stand", "verwacht"),
    [
        # OUD: zoals 0.7.1 (meta_version 1, geen blob_meta/blob_hash): photometa leest alles,
        # en heeft pas na het lezen plekken (blob_meta.gps) om op te zoeken
        (zf.OUD, lambda n, gps: {"te_doen": n, "todo": (n, 0)}),
        # GEO: na de herbouw geo: alles opnieuw lezen, en elke plek nog zonder photo_geo
        (zf.GEO, lambda n, gps: {"te_doen": n, "todo": (n, gps)}),
        # KLAAR: niets te doen, tot de herbouw hash van zoek_samen
        (zf.KLAAR, lambda n, gps: {"te_doen": 0, "todo": (0, 0)}),
    ],
)
def test_zaai_in_elke_stand_zoals_photometa_het_ziet(
    leeg: tuple[Path, str, str], stand: str, verwacht: Any
) -> None:
    """De stand van zaai() en de telling te_doen van foto_stand() (een kopie van photometa._todo
    in SQL) tegen photometa zelf. Bij KLAAR moet photometa in stap 1 van zoek_samen stil zijn;
    bij OUD en GEO stopt zoek_backfill pas als te_doen 0 is (anders wacht het WACHT_S of stopt
    het te vroeg)."""
    data, eigenaar, root = leeg
    fotos = list(zf.fotos(N_FOTOS, zaad=13))
    teller = zf.zaai(data, eigenaar, root, fotos, stand)
    met_gps = sum(f.gps is not None for f in fotos)
    assert teller == {"fotos": N_FOTOS, "met_gps": met_gps}
    assert 0 < met_gps < N_FOTOS
    stand_ = zf.foto_stand(data)
    todo = _photometa_todo(data)
    assert {"te_doen": stand_["te_doen"], "todo": todo} == verwacht(N_FOTOS, met_gps)
    assert stand_["te_doen"] == todo[0]  # dezelfde telling als photometa
    assert stand_["open"] == 0 and stand_["gehasht"] == 0 and stand_["plekken"] == 0
    # Een voorbeeld van 256 per foto, waar de pHash het leest
    store = zf.BlobStore(zf.Settings(data_dir=data).blob_dir)
    for f in fotos[:3]:
        sha = hashlib.sha256(f.data).hexdigest()
        assert (zf.previews.directory(store, sha) / "256.webp").read_bytes() == f.thumb
    assert zf.foto_stand(data, volledig=False) == {"open": 0, "te_doen": stand_["te_doen"]}


def test_foto_stand_kijkt_ook_naar_de_versie_van_de_hash(leeg: tuple[Path, str, str]) -> None:
    """Een blob_hash van een oudere versie is voor photometa nog te doen; foto_stand moet dat
    ook zo tellen (anders stopt zoek_backfill voordat de pHash er is)."""
    data, eigenaar, root = leeg
    zf.zaai(data, eigenaar, root, zf.fotos(10, zaad=17), zf.KLAAR)
    assert zf.foto_stand(data)["te_doen"] == 0
    con = sqlite3.connect(data / "platform.db")
    con.execute("UPDATE blob_hash SET version = ?", (zf.photo_hash.VERSION - 1,))
    con.commit()
    con.close()
    assert zf.foto_stand(data)["te_doen"] == 10 == _photometa_todo(data)[0]


def test_zaai_en_zaai_documenten_in_een_eigen_map(leeg: tuple[Path, str, str]) -> None:
    data, eigenaar, root = leeg
    fotos = list(zf.fotos(4, zaad=19))
    zf.zaai(data, eigenaar, root, fotos + fotos[:2], zf.KLAAR)  # dezelfde inhoud: één keer
    docs = [zb.Document(f"scan-{i}.txt", "text/plain", f"scan {i}".encode()) for i in range(3)]
    assert zf.zaai_documenten(data, eigenaar, root, docs, "Scans") == 3
    con = sqlite3.connect(data / "platform.db")
    try:
        mappen = dict(con.execute("SELECT name, id FROM node WHERE kind = 'dir' AND name != ''"))
        assert set(mappen) == {"Foto's", "Scans"}
        per_map = dict(con.execute("SELECT parent_id, count(*) FROM node WHERE kind = 'file' "
                                   "GROUP BY parent_id"))  # fmt: skip
        assert per_map == {mappen["Foto's"]: 4, mappen["Scans"]: 3}
        assert con.execute("SELECT count(*) FROM blob").fetchone()[0] == 7
        assert con.execute("SELECT count(*) FROM analysis").fetchone()[0] == 0  # zoals vóór Z2
    finally:
        con.close()
    store = zf.BlobStore(zf.Settings(data_dir=data).blob_dir)
    assert store.path(hashlib.sha256(b"scan 2").hexdigest()).read_bytes() == b"scan 2"


def test_plaatsen_uit_places_sqlite_op_id(tmp_path: Path) -> None:
    db = tmp_path / "places.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE place (id INTEGER PRIMARY KEY, lat REAL, lon REAL, name TEXT)")
    con.executemany("INSERT INTO place VALUES (?, ?, ?, ?)",
                    [(3, 52.09, 5.12, "Utrecht"), (1, -33.87, 151.21, "Sydney")])  # fmt: skip
    con.commit()
    con.close()
    assert zf.plaatsen_uit(db) == [(-33.87, 151.21), (52.09, 5.12)]


# --- zoek_samen --office: de bewerkers en de inhaalronde van O3 (I1, M7) ---


def test_de_bewerkers_wachten_niet_op_de_wachtrij(monkeypatch: pytest.MonkeyPatch) -> None:
    """In stap 2 en 3 is de rij nooit leeg. Wachten op de hele wachtrij (make_docs) of op een
    stille machine liet de bewerkers in de eerste volle meting (M7) nooit beginnen: na 300 s gaf
    make_docs op, en de poorten van OnlyOffice waren niet te meten. De meting van fase 4 (quiet,
    standaard) wacht nog wel."""
    import office_bench as ob
    import office_editors as oe

    gewacht: list[str] = []
    monkeypatch.setattr(ob, "wait_background", lambda *_a, **_kw: gewacht.append("wachtrij"))
    monkeypatch.setattr(ob, "wait_quiet", lambda *_a, **_kw: gewacht.append("stil"))
    monkeypatch.setattr(oe.of, "make", lambda kind, seed, budget=None: (f"{kind} {seed}", {}))
    gezet: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        ob, "upload", lambda _server, _owner, _map, naam, data: gezet.append((naam, data)) or naam
    )
    bench = argparse.Namespace(server=object(), clock=argparse.Namespace(exchanges=[]), pid=1)
    squad = argparse.Namespace(owner=object(), folder="map", everyone=[object()])
    assert oe.make_docs(bench, squad, 2, "t", 7, wait=False) == ["t-1.docx", "t-2.docx"]
    assert len(gezet) == 2 and gewacht == []
    oe.make_docs(bench, squad, 1, "t", 7)
    assert gewacht == ["wachtrij"]  # fase 4: standaard wel

    # one_round geeft quiet door aan make_docs en slaat dan ook wait_quiet over; hij komt tot
    # de Sampler (daarna Playwright)
    gevraagd: list[bool] = []

    class Klaar(Exception):
        pass

    def make_docs(*_a: Any, wait: bool = True) -> list[str]:
        gevraagd.append(wait)
        return ["n1"]

    grens: list[int] = []

    def sampler(*_a: Any, min_free: int) -> Any:
        grens.append(min_free)
        raise Klaar

    monkeypatch.setattr(oe, "make_docs", make_docs)
    monkeypatch.setattr(oe.oc, "Sampler", sampler)
    gewacht.clear()
    with pytest.raises(Klaar):
        oe.one_round(bench, squad, 1, 1, 1.0, 1, "ds", "url", quiet=False)
    assert gevraagd == [False] and gewacht == []
    with pytest.raises(Klaar):
        oe.one_round(bench, squad, 1, 1, 1.0, 1, "ds", "url", quiet=False, min_free=7)
    with pytest.raises(Klaar):
        oe.one_round(bench, squad, 1, 1, 1.0, 1, "ds", "url")
    assert gevraagd == [False, False, True] and gewacht == ["stil"]  # fase 4: standaard wel
    assert grens == [oe.LOW_FREE, 7, oe.LOW_FREE]

    # en zoek_samen vraagt quiet=False en zijn eigen geheugenpoort
    rondes: list[dict[str, Any]] = []

    def one_round(*a: Any, **kw: Any) -> dict[str, Any]:
        rondes.append(kw)
        return _ronde()

    monkeypatch.setattr(oe, "one_round", one_round)
    opzet = zs.Opzet(server=object(), admin=object(), office=object(), squad=object())  # type: ignore[arg-type]
    uit = zs.bewerkers(opzet, 3, 1.0, 1)
    assert rondes == [{"quiet": False, "min_free": int(1.5 * GIB)}]
    assert uit["verloren"] == 0 and uit["opslaan_s"] == 1.5
    assert zs.onvolledig({**uit, "na_de_start_van_media": True}) is None


def _ronde(**over: Any) -> dict[str, Any]:
    """Wat office_editors.one_round teruggeeft (de velden die bewerkers() leest): 3 bewerkers
    op 1 document, volledig."""
    return {"per_doc_results": [{"callback_s": [1.0], "status2": 1}], "editors": 3, "opened": 3,
            "save_s": 1.5, "typed": 10, "saved": 10, "lost_saving": 0, "low_memory": False,
            "open_errors": [], **over}  # fmt: skip


@pytest.mark.parametrize(
    ("over", "reden"),
    [
        ({"opened": 2, "open_errors": ["time-out"]}, "2 van 3 bewerkers open"),
        ({"per_doc_results": [{"callback_s": [], "status2": 0}]}, "status 2 per document: [0]"),
        ({"per_doc_results": [{"callback_s": [1.0, 2.0], "status2": 2}]}, "[2]"),
        ({"per_doc_results": []}, "status 2 per document: []"),
        ({"typed": 0, "saved": 0}, "niets getypt"),
        ({"low_memory": True}, "te weinig geheugen"),
    ],
)
def test_een_onvolledige_ronde_van_one_round_komt_door(
    monkeypatch: pytest.MonkeyPatch, over: dict[str, Any], reden: str
) -> None:
    """Van one_round via bewerkers() tot onvolledig(): elk teken van een onvolledige ronde komt
    mee (anders zag M7 een ronde zonder bewerkers als gehaald)."""
    import office_editors as oe

    monkeypatch.setattr(oe, "one_round", lambda *_a, **_kw: _ronde(**over))
    opzet = zs.Opzet(server=object(), admin=object(), office=object(), squad=object())  # type: ignore[arg-type]
    uit = {**zs.bewerkers(opzet, 3, 1.0, 1), "na_de_start_van_media": True}
    assert reden in (zs.onvolledig(uit) or "")


def test_de_bewerkers_typen_pas_als_de_belastingtest_loopt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Het team komt eerst; typen pas na start (stap: de belastingtest loopt), in elke stap op
    hetzelfde moment. In stap 1 maakt de belastingtest eerst ±2 minuten zijn bronnen; zonder dit
    sloegen de bewerkers daar op een ander moment op dan in stap 2 (review van de herstelling
    van M7)."""
    import threading

    import office_bench as ob
    import office_editors as oe

    gebeurd: list[str] = []
    start = threading.Event()

    def team(*_a: Any, members: int) -> Any:
        gebeurd.append(f"team {members + 1}")
        return object()

    def one_round(*_a: Any, **_kw: Any) -> dict[str, Any]:
        gebeurd.append("typen" if start.is_set() else "typen voor de start")
        return {"per_doc_results": [], "editors": 0, "opened": 0, "save_s": 0.0, "typed": 0,
                "saved": 0, "lost_saving": 0, "low_memory": True, "open_errors": ["x"]}  # fmt: skip

    monkeypatch.setattr(ob, "team", team)
    monkeypatch.setattr(oe, "one_round", one_round)
    opzet = zs.Opzet(server=object(), admin=object(), office=object())  # type: ignore[arg-type]
    uit: dict[str, Any] = {}
    draad = threading.Thread(
        target=lambda: uit.update(zs.bewerkers(opzet, 3, 1.0, 1, start=start)), daemon=True
    )
    draad.start()
    try:
        draad.join(timeout=1.0)
        assert draad.is_alive() and gebeurd == ["team 3"]
    finally:
        start.set()
        draad.join(timeout=10)
    assert gebeurd == ["team 3", "typen"]
    assert "0 van 0 bewerkers open" in (zs.onvolledig({**uit, "na_de_start_van_media": True}) or "")


def test_de_app_peilt_de_eerste_pagina_zoals_de_web_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /files/root met limit = FIRST_PAGE van de web-app: zonder limit meet de poort de
    lengte van de hoofdmap (5.000 documenten: p95 ±320 ms zonder enige last; I1, M7)."""
    import re
    import threading

    ts = (Path(__file__).resolve().parents[1] / "web/src/browser/api.ts").read_text()
    eerste = re.search(r"export const FIRST_PAGE = (\d+);", ts)
    assert eerste and int(eerste.group(1)) == zs.EERSTE_PAGINA
    gevraagd: list[tuple[str, dict[str, Any]]] = []
    meter = zs.Meter()
    klaar = threading.Event()

    def get(_http: Any, path: str, **kw: Any) -> float:
        gevraagd.append((path, kw.get("params") or {}))
        if path.endswith("/files/root"):
            klaar.set()
        return 1.0

    tokens: list[str] = []

    def token() -> str:
        tokens.append(f"t{len(tokens)}")
        return tokens[-1]

    monkeypatch.setattr(zs, "_get", get)
    monkeypatch.setattr(zs, "mem_available", lambda: 4 * GIB)
    server = argparse.Namespace(url="http://127.0.0.1:9")
    draden = zs.zoekers(server, token, ["a"], meter)  # type: ignore[arg-type]
    assert klaar.wait(10)
    meter.stop.set()
    for d in draden:
        d.join(timeout=5)
    root = [params for path, params in gevraagd if path == f"{zs.API}/files/root"]
    assert root and all(params == {"limit": zs.EERSTE_PAGINA} for params in root)
    # het token per verzoek (Server.token ververst het na 10 minuten): niet één voor de hele
    # stap, want dat verliep in M7 halverwege stap 3
    met_token = [path for path, _ in gevraagd if path != "/health"]
    assert len(tokens) == len(met_token)


def test_zaai_stap_met_office_zoals_een_07_met_onlyoffice(monkeypatch: pytest.MonkeyPatch) -> None:
    """zoek_samen --office zaait de documenten met hun PDF al klaar (in elke stap), zonder
    --office zoals voorheen."""
    gezaaid: list[bool] = []
    monkeypatch.setattr(zb, "zaai", lambda *_a, office_klaar=False: gezaaid.append(office_klaar))
    admin = argparse.Namespace(id="eigenaar", root_id="root")
    opzet = zs.Opzet(server=object(), admin=admin)  # type: ignore[arg-type]
    for office in (True, False):
        args = argparse.Namespace(office=office, fotos=0, scans=0)
        zs.zaai_stap(Path("/x"), admin, [], args, opzet, last=False)
        zs.zaai_stap(Path("/x"), admin, [], args, opzet, last=True)
    assert gezaaid == [True, True, False, False]


def test_zaai_office_klaar_geeft_de_inhaalronde_van_o3_niets_te_doen(
    leeg: tuple[Path, str, str],
) -> None:
    """Zoals op een 0.7.x met OnlyOffice: de documenten die DS omzet, hebben hun PDF al. Zonder
    office_klaar zette DS in elke stap van zoek_samen (ook de nulmeting) de ±2.700 docx, xlsx,
    pptx en rtf om (I1, M7), werk dat niet bij de upgrade naar 0.8.0 hoort."""
    from sqlalchemy import func, select

    from app.extract import LEGACY_MIMES
    from app.models import Blob
    from app.office import convert
    from app.office.oo import CONVERT_FORMATS
    from seed import connect

    data, eigenaar, root = leeg
    office = argparse.Namespace(convert_formats=CONVERT_FORMATS)

    def kandidaten() -> int:
        """Wat de inhaalronde zou nemen (convert._candidate, zoals _backfill het vraagt)."""
        with connect(data) as conn:
            return conn.execute(
                select(func.count()).select_from(Blob).where(*convert._candidate(office))
            ).scalar_one()

    def rtf(i: int) -> zb.Document:
        return zb.Document(f"rtf-{i}.rtf", "application/rtf", b"{\\rtf1 rtf %d\\par}" % i)

    docs = zb.documenten(60)
    docs = [*docs[:30], rtf(1), *docs[30:], rtf(2)]  # rtf zet DS ook om
    zb.zaai(data, eigenaar, root, docs[:31])
    zonder = kandidaten()
    assert zonder > 0  # zo liep M7
    zb.zaai(data, eigenaar, root, docs[31:], office_klaar=True)
    assert kandidaten() == zonder  # van de nieuwe geen enkele
    con = sqlite3.connect(data / "platform.db")
    try:
        staten = Counter(dict(con.execute(
            "SELECT coalesce(pdf_state, '-'), count(*) FROM blob GROUP BY 1")))  # fmt: skip
    finally:
        con.close()
    omgezet = sum(1 for d in docs[31:] if d.mime in convert.EXT_BY_MIME)
    assert omgezet > 0 and staten["ready"] == omgezet
    with connect(data) as conn:
        rijen = conn.execute(
            select(Blob.pdf_state, Blob.pdf_version).where(Blob.mime == "application/rtf")
        ).all()
    assert {tuple(r) for r in rijen} == {(None, 0), ("ready", zb.PDF_VERSION_07)}  # ook rtf
    # De stap text heeft de PDF (die er niet is) alleen voor doc, xls en ppt nodig
    assert not {d.mime for d in docs} & LEGACY_MIMES
