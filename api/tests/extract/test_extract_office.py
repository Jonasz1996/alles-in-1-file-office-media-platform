"""docx, xlsx, pptx, odt, ods en odp (Keuze 17): de stand "alle tekst" van de kantoorlezer,
met kop- en voetteksten, voetnoten, sprekersnotities en alle cellen; de zip-bom en een XML met
DOCTYPE worden geweigerd."""

from __future__ import annotations

import zipfile

import pytest

from app.office import reader
from tests.extract.lees import lees
from tests.search import fixtures

K = fixtures.KANTOOR
META = f"{K['titel']}\n{K['auteur']}"


def test_docx_met_kop_voet_en_voetnoot():
    found, text = lees(fixtures.docx("Een extra alinea."), "office")
    assert found.state == "done" and found.meta == META
    for woord in (K["tekst"], K["voetnoot"], K["kop"], K["voet"], "Een extra alinea.", "Straße"):
        assert woord in text


def test_odt_met_kop_voet_en_voetnoot():
    found, text = lees(fixtures.odt(), "office")
    assert found.state == "done" and found.meta == META
    for woord in (K["tekst"], K["voetnoot"], K["kop"], K["voet"]):
        assert woord in text
    assert "noot Een voetnoot" in text  # het nummer van de noot telt niet, wel een spatie


@pytest.mark.parametrize("maak", [fixtures.pptx, fixtures.odp], ids=["pptx", "odp"])
def test_dia_met_sprekersnotities(maak):
    found, text = lees(maak(), "office")
    assert found.state == "done"
    assert "Kwartaalcijfers" in text and K["tekst"] in text and K["notities"] in text


@pytest.mark.parametrize("maak", [fixtures.xlsx, fixtures.ods], ids=["xlsx", "ods"])
def test_alle_20000_cellen(maak):
    found, text = lees(maak(20_000), "office")
    assert found.state == "done" and not found.truncated
    assert K["laatste_cel"] in text  # de laatste van 20.000 cellen
    assert "rij 1" in text


def test_xml_met_doctype_wordt_geweigerd():
    found, text = lees(fixtures.xml_entiteit(), "office")
    assert (found.state, found.code, text) == ("failed", "xml_refused", "")


def test_zip_bom_wordt_geweigerd_zonder_uit_te_pakken():
    found, text = lees(fixtures.make("kantoor/zip-bom.docx"), "office", cpu_seconds=5)
    assert (found.state, found.code, text) == ("failed", "zip_limit", "")


def _docx_met(delen: dict[str, bytes], tmp_path, naam="x.docx"):
    data = fixtures.docx()
    pad = tmp_path / naam
    with zipfile.ZipFile(pad, "w") as uit, zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
        for info in z.infolist():
            uit.writestr(info, z.read(info))
        for naam_, inhoud in delen.items():
            uit.writestr(naam_, inhoud)
    return pad


def test_meer_dan_2000_onderdelen_is_zip_limit(tmp_path):
    pad = _docx_met({f"extra/{i}.xml": b"<a/>" for i in range(2001)}, tmp_path)
    found, _ = lees(pad, "office")
    assert (found.state, found.code) == ("failed", "zip_limit")


def test_samen_meer_dan_200_mb_gelezen_is_zip_limit(monkeypatch, tmp_path):
    # Klein nagespeeld: de grens van alle gelezen onderdelen samen
    monkeypatch.setattr(reader, "ALL_TOTAL_BYTES", 300)
    found, _ = lees(fixtures.docx("x" * 400), "office")
    assert (found.state, found.code) == ("failed", "zip_limit")


def test_kapot_zip_is_failed_zonder_code():
    found, text = lees(b"PK\x03\x04 kapot", "office")
    assert (found.state, found.code, text) == ("failed", None, "")


def test_de_gewone_stand_merkt_niets_van_de_nieuwe_fouten():
    # XmlRefused en ZipLimit zijn een Unreadable: read() van de preview weigert zoals voorheen
    assert issubclass(reader.XmlRefused, reader.Unreadable)
    assert issubclass(reader.ZipLimit, reader.Unreadable)
