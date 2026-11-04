"""De tekst opschonen (bouwontwerp fase 6, Keuze 21).

NFKC, zachte afbreekstreepjes (U+00AD) en tekens zonder breedte weg, stuurtekens worden een
spatie, en witruimte gaat samen: een reeks spaties en tabs wordt één spatie, een reeks met een
regeleinde één regeleinde. ß blijft ß (een snippet toont "große"); dat ß en ẞ als "ss" gezocht
worden, doet de view search_src (Keuze 5), niet deze code. Geen andere taalregels.

Alleen de standaardbibliotheek: dit draait in het kind van de worker (app.render text).
"""

from __future__ import annotations

import re
import unicodedata

# Zacht afbreekstreepje en de tekens zonder breedte (ook de BOM midden in een tekst)
_DROP = dict.fromkeys(map(ord, "­​‌‍⁠﻿᠎"), None)
# Stuurtekens (behalve tab en regeleinde) en losse surrogaten (die kan SQLite niet opslaan)
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x9f\ud800-\udfff]")
_LINES = re.compile(r"\s*[\n\r\x0b\x0c\x1c-\x1e\x85  ]\s*")
_SPACES = re.compile(r"[^\S\n]+")
META_CHARS = 2000  # titel, auteur en onderwerp samen
FIELD_CHARS = 500


def clean(text: str) -> str:
    """De tekst zoals hij in blob_text komt."""
    text = text.translate(_DROP)
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL.sub(" ", text)
    text = _LINES.sub("\n", text)
    text = _SPACES.sub(" ", text)
    return text.strip()


def cut(text: str, limit: int) -> tuple[str, bool]:
    """Hooguit limit tekens; True als er iets af ging."""
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip(), True


def fold_meta(*values: str | None) -> str:
    """Titel, auteur, onderwerp (of titel, artiest, album) als één tekst: elk opgeschoond, op
    één regel, zonder dubbele, samen hooguit META_CHARS tekens."""
    seen: list[str] = []
    for value in values:
        if not value:
            continue
        one = clean(str(value)).replace("\n", " ")[:FIELD_CHARS].strip()
        if one and one not in seen:
            seen.append(one)
    return "\n".join(seen)[:META_CHARS]
