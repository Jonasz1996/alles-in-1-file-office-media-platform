"""txt, md en csv (Keuze 17): UTF-8, UTF-16 met BOM, anders cp1252; afgekapt op de grens."""

from __future__ import annotations

import codecs

from tests.extract.lees import MAX, lees


def test_utf8_markdown_en_csv():
    found, text = lees("# Kop\n\nDe große zoekmachine, café;€ 12\n".encode(), "plain")
    assert found.state == "done" and not found.truncated
    assert text == "# Kop\nDe große zoekmachine, café;€ 12"


def test_utf16_met_bom():
    data = codecs.BOM_UTF16_LE + "Straße in UTF-16 met ẞ".encode("utf-16-le")
    found, text = lees(data, "plain")
    assert text == "Straße in UTF-16 met ẞ"


def test_cp1252_als_het_geen_utf8_is():
    found, text = lees("Café, “aanhalingstekens” en € 5".encode("cp1252"), "plain")
    assert text == "Café, “aanhalingstekens” en € 5"


def test_2_mb_tekst_wordt_afgekapt():
    regel = "woord" * 20 + "\n"
    data = (regel * (2 * 1024 * 1024 // len(regel) + 1)).encode()
    found, text = lees(data, "plain")
    assert (found.state, found.truncated, found.code) == ("done", True, None)
    assert len(text) == found.chars <= MAX
    assert found.chars > MAX - 200


def test_lege_tekst_is_none():
    found, text = lees(b"   \n\n ", "plain")
    assert (found.state, text) == ("none", "")
