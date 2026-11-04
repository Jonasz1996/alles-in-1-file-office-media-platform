"""Keuze 21: de tekst opschonen (app/extract/normalize.py)."""

from __future__ import annotations

from app.extract.normalize import clean, cut, fold_meta


def test_nfkc_witruimte_en_tekens_zonder_breedte():
    tekst = "zoek­machine ﬁlm​  en\t\tde\r\n\r\n\n  Straße\x00GROẞE eind"
    assert clean(tekst) == "zoekmachine film en de\nStraße GROẞE\neind"


def test_ss_blijft_in_de_opgeslagen_tekst():
    # ß en ẞ als "ss" zoeken doet de view search_src (Keuze 5), niet het opschonen
    assert clean("große GROẞE") == "große GROẞE"


def test_losse_surrogaat_en_stuurtekens_worden_spatie():
    assert clean("a\ud800b\x07c\x9fd") == "a b c d"


def test_afkappen_op_de_grens():
    assert cut("abcdef", 10) == ("abcdef", False)
    assert cut("abc def", 4) == ("abc", True)


def test_meta_gevouwen_zonder_dubbele():
    assert fold_meta("Titel", None, " Titel ", "Eva\nJansen", "") == "Titel\nEva Jansen"
    assert len(fold_meta("x" * 5000, "y" * 5000, "z" * 5000)) <= 2000
