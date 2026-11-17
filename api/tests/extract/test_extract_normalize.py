"""Keuze 21: de tekst opschonen (app/extract/normalize.py)."""

from __future__ import annotations

import random
import re
import time
import unicodedata

import pytest

from app.extract import normalize
from app.extract.normalize import clean, cut, fold_meta
from tests.extract.lees import lees


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


# --- Review golf 1, F1: witruimte vouwen is lineair -------------------------------------------
#
# Het oude patroon (\s* voor en na een regeleinde) zocht vanaf elke spatie van een lange reeks
# tot het eind en terug: 40.000 spaties kostten 7 s CPU, buiten de klok van de blob. Een
# document van een paar honderd KB witruimte hield zo het kind tot RLIMIT_CPU bezig.

_OUD_REGELS = re.compile(r"\s*[\n\r\x0b\x0c\x1c-\x1e\x85  ]\s*")
_OUD_SPATIES = re.compile(r"[^\S\n]+")


def _oud(text: str) -> str:
    """clean() van vóór de review (alleen voor korte teksten: kwadratisch)."""
    text = text.translate(normalize._DROP)
    text = unicodedata.normalize("NFKC", text)
    text = normalize._CONTROL.sub(" ", text)
    text = _OUD_REGELS.sub("\n", text)
    return _OUD_SPATIES.sub(" ", text).strip()


def test_hetzelfde_als_het_oude_patroon():
    rng = random.Random(21)
    tekens = " \t\n\r\x0b\x0c\x1c\x1d\x1e\x1f\x85\xa0  　​\xadabé"
    for _ in range(20_000):
        tekst = "".join(rng.choice(tekens) for _ in range(rng.randint(0, 30)))
        assert clean(tekst) == _oud(tekst), repr(tekst)


@pytest.mark.parametrize("teken", [" ", "\t", " \n", "a\t"])
def test_een_miljoen_tekens_witruimte_binnen_een_halve_seconde(teken):
    tekst = teken * (1_000_000 // len(teken))
    begin = time.process_time()
    uit = clean(tekst)
    assert time.process_time() - begin < 0.5
    assert len(uit) <= len(tekst)


def test_meta_kapt_eerst_af():
    begin = time.process_time()
    assert fold_meta(" " * 2_000_000 + "titel") == ""
    assert fold_meta("titel" + " " * 2_000_000 + "auteur") == "titel"
    assert time.process_time() - begin < 0.1


def _document(soort: str, witruimte: str) -> bytes:
    vulling = witruimte * 2_000_000  # net onder de grens van de Collector (2 × 1.048.576)
    if soort == "plain":
        return ("begin" + vulling + "eind").encode()
    if soort == "html":
        return f"<html><title>t{vulling}t</title><body><p>begin{vulling}eind</p></body>".encode()
    return ("{\\rtf1\\ansi {\\info{\\title t" + vulling + "t}}begin" + vulling + "eind}").encode()


@pytest.mark.parametrize("witruimte", [" ", "\t"])
@pytest.mark.parametrize("soort", ["plain", "html", "rtf"])
def test_2_mb_witruimte_blijft_binnen_de_klok(soort, witruimte):
    """2 MB spaties of tabs (ook in de titel) met een klok van 1 s: klaar binnen 2 s CPU."""
    begin = time.process_time()
    found, text = lees(_document(soort, witruimte), soort, cpu_seconds=1.0)
    assert time.process_time() - begin < 2.0
    assert (found.state, found.code, text) == ("done", None, "begin eind"), (found, text[:40])
    if soort != "plain":
        assert found.meta == "t", found.meta  # fold_meta kapt eerst af op 4 × FIELD_CHARS
