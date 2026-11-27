"""De stubs van fase 6 (bouwontwerp §3.5): elk eindpunt van §3.4 bestaat met zijn pad, methode,
body en antwoordmodel, en geeft 501 not_built tot zijn pakket het vult. De inlog (en voor beheer
de rol) wordt eerst gecontroleerd, zoals straks bij de echte route.

Ook hier: QueryInt, voor een keuze uit vaste getallen in de query (?hidden=1, ?size=96). De
echte route houdt hem.
"""

from typing import Any

from fastapi import HTTPException
from pydantic import BeforeValidator

from app import errors


def not_built(package: str) -> HTTPException:
    """raise not_built("Z3"): het pakket dat deze route bouwt."""
    return errors.fail(501, "not_built", f"Nog niet gebouwd ({package})", package=package)


def _query_int(value: Any) -> Any:
    """Gewone cijfers (hooguit 9) worden een getal; de rest blijft tekst en valt dan op de
    Literal, met de 422 van elke andere fout. Geen int() op alles: zijn ValueError zou de
    melding van invalid_input worden, met de afgewezen waarde erin (review Z0b, veilig V1); en
    int() neemt ook "9_6", " 96" en andere cijfers dan 0-9."""
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 9:
        return int(value)
    return value


# Annotated[Literal[0, 1], QueryInt]: een query string is tekst, en Literal[0, 1] alleen neemt
# geen "1" (dan altijd 422; gevonden bij de samenvoeging van Z0b)
QueryInt = BeforeValidator(_query_int)
