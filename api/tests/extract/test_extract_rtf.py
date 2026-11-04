"""rtf (Keuze 17): een eigen stripper."""

from __future__ import annotations

from tests.extract.lees import lees

# @U staat voor het stuurwoord backslash-u (zo blijft deze bron gewone ASCII)
BACKSLASH_U = bytes([92, 117])


def rtf(data: bytes) -> bytes:
    return data.replace(b"@U", BACKSLASH_U)


RTF = rtf(
    rb"""{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0 Times New Roman;}}
{\colortbl;\red0\green0\blue0;}{\*\generator Msftedit 5.41;}
{\info{\title Het verslag}{\author Eva Jansen}{\company Geheim BV}}
{\header Koptekst met kopwoord\par}
\pard Een caf\'e9 met @U8364? euro en @U-10179?@U-9020? een emoji.\par
Tweede\tab regel\line derde regel \{haakjes\}\par
{\field{\*\fldinst HYPERLINK "http://instructie"}{\fldrslt de linktekst}}\par
{\pict\pngblip 89504e470d0a1a0a0000000d49484452}
{\*\bkmkstart verborgen}Einde\footnote{Een voetnoot met voetnootwoord}.\par
\bin4 ABCD na binair}
"""
)


def test_tekst_meta_en_wat_niet_telt():
    found, text = lees(RTF, "rtf")
    assert found.state == "done"
    assert found.meta == "Het verslag\nEva Jansen"
    assert "Koptekst met kopwoord" in text
    assert "Een café met € euro en \N{PAGE FACING UP} een emoji." in text
    assert "Tweede regel\nderde regel {haakjes}" in text
    assert "de linktekst" in text and "instructie" not in text
    assert "voetnootwoord" in text and "na binair" in text
    for weg in ("Times", "Msftedit", "Geheim", "89504e", "verborgen", "ABCD"):
        assert weg not in text


def test_codetabel_uit_ansicpg():
    found, text = lees(rb"{\rtf1\ansi\ansicpg1251 \'cf\'f0\'e8\'e2\'e5\'f2}", "rtf")
    assert (found.state, text) == ("done", "Привет")


def test_kapotte_rtf_geeft_wat_er_is():
    found, text = lees(rtf(rb"{\rtf1 begin {\b vet} @U99999999? \'zz eind") + b"\\", "rtf")
    assert found.state == "done" and text.startswith("begin vet")


def test_lege_rtf_is_none():
    found, text = lees(b"", "rtf")
    assert (found.state, text) == ("none", "")
