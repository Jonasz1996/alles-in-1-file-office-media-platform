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

    asyncio.run(migreer())
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
