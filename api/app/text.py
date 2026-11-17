"""Tekst gelijk maken om op te zoeken: "Één Café" en "een cafe" worden hetzelfde. En welke
tekens niet in een naam (van een map, bestand of tag) mogen."""

import re
import unicodedata

# Stuurtekens (ook C1) en tekens die de schrijfrichting omdraaien (een naam die er anders
# uitziet dan hij is, zoals "factuur-\u202efdp.exe"); de zero-width joiner (U+200D) in
# samengestelde emoji mag wel
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def fold(text: str) -> str:
    """Zonder hoofdletters en accenten (é -> e, ß -> ss)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
