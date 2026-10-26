"""Lege documenten (Keuze 24). O0-stub; O4 maakt het af.

blank(kind, locale) -> bytes: een lege docx, xlsx of pptx met alleen zipfile, deterministisch
(vaste datum en volgorde), zodat elk leeg document van een soort en taal één blob is.
"""

from typing import Literal


def blank(kind: Literal["docx", "xlsx", "pptx"], locale: str | None) -> bytes:
    raise NotImplementedError("O4")
