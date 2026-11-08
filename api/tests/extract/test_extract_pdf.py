"""PDF (Keuze 17): de tekstlaag via pypdfium2, hooguit 2.000 pagina's, scans apart."""

from __future__ import annotations

from tests.extract.lees import lees, vaste_klok
from tests.search import fixtures


def test_tekst_meta_en_keuze_21():
    found, text = lees(fixtures.pdf_tekst(3), "pdf")
    assert found.state == "done" and found.code is None and not found.truncated
    assert found.chars == len(text)
    for n in (1, 2, 3):
        assert f"paginawoord{n}" in text
    # ß en ẞ blijven, de zachte afbreking is weg, de emoji blijft
    assert "Straße" in text and "GROẞE" in text and "zoekmachine" in text
    assert "\N{PAGE FACING UP}" in text and "­" not in text
    assert found.meta == "Verslag met tekst\nBram de Vries"
    assert found.scan_pages == []


def test_een_scan_heeft_geen_tekst_maar_wel_een_scanpagina():
    found, text = lees(fixtures.pdf_scan(), "pdf")
    assert (found.state, text, found.scan_pages) == ("none", "", [1])


def test_2001_paginas_geeft_er_2000_en_pages_limit():
    paginas = [fixtures._kolom(50, [f"pagina{n}woord"]) for n in range(1, 2002)]
    found, text = lees(fixtures._pdf(paginas, titel="Lang"), "pdf")
    assert (found.state, found.code, found.truncated) == ("done", "pages_limit", True)
    assert "pagina2000woord" in text and "pagina2001woord" not in text


def test_kapot_is_failed_zonder_code():
    found, text = lees(b"%PDF-1.4\n dit is geen pdf", "pdf")
    assert (found.state, found.code, text) == ("failed", None, "")


def test_de_klok_stopt_met_wat_er_is(monkeypatch):
    """Halverwege op: done, too_slow, truncated, met de tekst tot dan. Een klok van 100 s die bij
    elke keer kijken één seconde verder staat (vaste_klok): de lezer kijkt vóór elke pagina (en
    add() om de 256 stukken, hier pas na pagina 128), dus precies 100 pagina's. Met de echte
    CPU-tijd (0,05 s) las een snelle runner alle 399 (CI run 89, docs/fase-6/z2.md)."""
    vaste_klok(monkeypatch)
    paginas = [fixtures._kolom(50, [f"pagina{n}woord"] * 40) for n in range(1, 400)]
    found, text = lees(fixtures._pdf(paginas), "pdf", cpu_seconds=100)
    assert (found.state, found.code, found.truncated) == ("done", "too_slow", True)
    assert "pagina1woord" in text and "pagina399woord" not in text
    assert "pagina100woord" in text and "pagina101woord" not in text
