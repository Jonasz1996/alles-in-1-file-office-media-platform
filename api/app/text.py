"""Tekst gelijk maken om op te zoeken: "Één Café" en "een cafe" worden hetzelfde."""

import unicodedata


def fold(text: str) -> str:
    """Zonder hoofdletters en accenten (é -> e, ß -> ss)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
