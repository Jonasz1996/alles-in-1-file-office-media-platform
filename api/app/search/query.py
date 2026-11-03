"""De zoekvraag naar een FTS5-uitdrukking (bouwontwerp fase 6, §3.3; Keuze 5, 6). Z0 legt de
vorm vast; Z1 is de eigenaar en bouwt hem.

Alleen parse() maakt een uitdrukking voor MATCH: nooit een f-string of % naast MATCH
(tests/search/test_search_no_raw.py, Z1). parse() geeft None als er niets te zoeken is, en
nooit een fout van FTS.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Parsed:
    """Een vraag die FTS5 kan draaien. match: voor fts_node; names: voor fts_name (trigram,
    alleen woorden van minstens drie tekens); words: de gevouwen woorden, voor de snippets."""

    match: str
    names: str | None
    words: tuple[str, ...]


def parse(q: str) -> Parsed | None:
    """None: niets om te zoeken. Tot Z1 alleen dat; een echte vraag geeft NotImplementedError
    (niets roept dit aan zolang select_engine LikeFallback geeft)."""
    if not q.strip():
        return None
    raise NotImplementedError("parse wordt gebouwd in Z1")
