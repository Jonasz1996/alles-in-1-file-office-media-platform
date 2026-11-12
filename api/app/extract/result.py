"""De uitkomst van tekst halen uit één inhoud (bouwontwerp fase 6, §3.3). Z0, vast.

De worker (opdracht text, Z2) stuurt per blob eerst dit, en dan de tekst zelf als eigen stuk op
de pipe, met de lengte vooraf. Zo staat de tekst nooit in een JSON-bericht.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# done: tekst gevonden; none: geen tekst (een foto, een lege pagina); skip: niet voor deze
# soort; failed: kapot of te traag (code zegt wat, zie analysis.code in §3.4)
State = Literal["done", "none", "skip", "failed"]


@dataclass(frozen=True)
class Extracted:
    sha: str
    state: State
    chars: int  # de lengte van de tekst die volgt (0 als er geen is)
    truncated: bool  # afgekapt op PLATFORM_SEARCH_MAX_TEXT
    scan_pages: list[int]  # pagina's zonder tekstlaag (voor OCR, X1)
    meta: str  # titel, auteur en onderwerp uit het document, gevouwen
    code: str | None  # bij failed: too_slow, zip_limit, xml_refused, pages_limit, …
