"""De zoekvraag naar een FTS5-uitdrukking (bouwontwerp fase 6, §3.3; Keuze 5, 6). Z0 legde de
vorm vast; Z1 bouwt hem.

Alleen deze module maakt een uitdrukking voor MATCH: nooit een f-string of % naast MATCH
(tests/search/test_search_no_raw.py). parse() geeft None als er niets te zoeken is, en nooit een
fout van FTS: elk woord staat tussen aanhalingstekens, en wat de gebruiker typt is nooit een
operator (AND, NEAR, name:, *, ( en " zijn gewone tekens).

De regels (Keuze 5; dezelfde als tests/search/referentie.py, de definitie):

- hooguit 200 tekens en 10 woorden, anders QueryTooLong (400 search_query_too_long);
- NFKC, dan splitsen op alles wat geen letter of cijfer is; een teken op een letter (Unicode M)
  hoort bij het woord (anders valt "İstanbul" in "i" en "stanbul", S8);
- elk woord in twee vormen: de tekst-vorm (kleine letters, ß en ẞ als "ss": zoals de tekst in
  FTS staat, Keuze 21) en de naam-vorm (fold(), zoals node.search_name). Zijn ze gelijk, dan één
  keer; anders ("a" OR "b");
- wat tussen aanhalingstekens staat, is een frase (een aanhalingsteken zonder sluiter loopt tot
  het eind);
- het laatste woord is een voorvoegsel ("rapp"*) als het ≥ 3 tekens heeft en de vraag eindigt
  op een letter of cijfer (niet op een spatie of een aanhalingsteken);
- er wordt niets geschrapt: korte woorden zoeken als heel woord, en in de namen via instr();
- de termen staan met een expliciete AND aan elkaar: FTS5 kent geen AND zonder woord naast een
  haakje (("a" OR "b") "c" is een fout van FTS5, gevonden met de fuzz van
  test_search_query.py).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.text import fold

MAX_CHARS = 200
MAX_WORDS = 10
PREFIX_MIN = 3
# De kolommen van fts_node zonder pocr (de OCR-tekst van een foto): voor een treffer bij een
# ander dan de eigenaar van de node (Keuze 25a; review Z0, veilig F3)
COLUMNS_NOT_POCR = "{name extra body ocr meta}"
_SPLIT = re.compile(r"[\W_]+")
_ZWSP = "\N{ZERO WIDTH SPACE}"


class QueryTooLong(ValueError):
    """Meer dan 200 tekens of 10 woorden (400 search_query_too_long)."""


@dataclass(frozen=True)
class Parsed:
    """Een vraag die FTS5 kan draaien. match: voor fts_node; names: voor fts_name (trigram,
    alleen woorden van minstens drie tekens); words: de gevouwen woorden (de naam-vorm), voor
    de stukken van namen (instr) en de snippets. terms: per term de gevouwen woorden (meer dan
    één: een frase); prefix: het laatste woord is een voorvoegsel."""

    match: str
    names: str | None
    words: tuple[str, ...]
    terms: tuple[tuple[str, ...], ...] = ()
    prefix: bool = False

    def match_not_pocr(self) -> str:
        """Dezelfde uitdrukking, zonder de kolom pocr: voor nodes van een ander."""
        return COLUMNS_NOT_POCR + " : (" + self.match + ")"


def _text_form(word: str) -> str:
    return word.lower().replace("ß", "ss").replace("ẞ", "ss")


def _is_word_char(c: str) -> bool:
    return c.isalnum() or unicodedata.category(c).startswith("M")


def _raw_words(segment: str) -> list[str]:
    words: list[str] = []
    cur: list[str] = []
    for c in segment:
        if _is_word_char(c):
            cur.append(c)
        elif cur:
            words.append("".join(cur))
            cur = []
    if cur:
        words.append("".join(cur))
    return words


def _forms(segment: str) -> list[tuple[str, str]]:
    """(tekst-vorm, naam-vorm) per woord. Geeft fold() van een woord nog scheidingstekens (een
    teken op een letter dat fold() laat staan), dan telt elk stuk als woord, zoals in de
    referentie, met alleen de naam-vorm."""
    out: list[tuple[str, str]] = []
    for raw in _raw_words(segment):
        name = fold(raw)
        pieces = [p for p in _SPLIT.split(name) if p]
        if len(pieces) == 1 and pieces[0] == name:
            out.append((_text_form(raw), name))
        else:
            out.extend((p, p) for p in pieces)
    return out


def _quote(words: list[str], prefix: bool) -> str:
    return '"' + " ".join(words) + '"' + ("*" if prefix else "")


def _term(forms: list[tuple[str, str]], prefix: bool) -> str:
    text = _quote([t for t, _ in forms], prefix)
    name = _quote([n for _, n in forms], prefix)
    return text if text == name else "(" + text + " OR " + name + ")"


def parse(q: str) -> Parsed | None:
    """None: niets om te zoeken. QueryTooLong bij meer dan 200 tekens of 10 woorden."""
    if len(q) > MAX_CHARS:
        raise QueryTooLong()
    q = unicodedata.normalize("NFKC", q)
    terms: list[list[tuple[str, str]]] = []
    for i, segment in enumerate(q.split('"')):
        forms = _forms(segment)
        if not forms:
            continue
        if i % 2:  # tussen aanhalingstekens: een frase
            terms.append(forms)
        else:
            terms.extend([f] for f in forms)
    words = tuple(n for t in terms for _, n in t)
    if not words:
        return None
    if len(words) > MAX_WORDS:
        raise QueryTooLong()
    last = q.rstrip(_ZWSP)[-1:]
    prefix = bool(last) and last.isalnum() and len(words[-1]) >= PREFIX_MIN
    match = " AND ".join(_term(t, prefix and i == len(terms) - 1) for i, t in enumerate(terms))
    long_words = [w for w in words if len(w) >= PREFIX_MIN]
    names = " AND ".join(_quote([w], False) for w in long_words) if long_words else None
    return Parsed(
        match=match,
        names=names,
        words=words,
        terms=tuple(tuple(n for _, n in t) for t in terms),
        prefix=prefix,
    )
