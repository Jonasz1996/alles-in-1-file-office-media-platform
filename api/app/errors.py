"""Fouten van de API, elk met een vaste code.

Elke fout heeft dezelfde vorm:

    {"detail": {"message": "<Nederlandse tekst>", "code": "<code>", "params": {...}}}

params is er alleen als de tekst iets invult (een naam, een aantal, een tijdstip). In het
Nederlands toont de web-app de tekst van de server; in een andere taal vertaalt hij op code,
met de params ingevuld (web/src/i18n/errors/*.ts). Elke code staat daar in precies één
catalogus; tests/test_error_catalog.py controleert dat.

Een ongeldig verzoek (validatie door FastAPI) krijgt code invalid_input met het eerste veld
dat niet klopt, plus de volledige lijst van pydantic:

    {"detail": {"message": "Ongeldige invoer", "code": "invalid_input",
                "params": {"field": "body.name"}, "errors": [...]}}

Bulkacties geven per item een ItemResult: gelukt (ok, met het resultaat in node) of mislukt,
met dezelfde codes als de losse endpoints.
"""

import uuid
from typing import Any

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

INVALID_INPUT = "Ongeldige invoer"


def fail(
    status: int, code: str, message: str, *, headers: dict[str, str] | None = None, **params: Any
) -> HTTPException:
    """De fout om te raisen: `raise errors.fail(404, "not_found", "Niet gevonden")`."""
    detail: dict[str, Any] = {"message": message, "code": code}
    if params:
        detail["params"] = params
    return HTTPException(status, detail, headers=headers)


class ItemResult(BaseModel):
    """De uitkomst van een bulkactie voor één id."""

    id: uuid.UUID
    ok: bool
    code: str | None = None
    message: str | None = None
    node: Any = None


def item_fail(id: uuid.UUID, code: str, message: str) -> ItemResult:
    return ItemResult(id=id, ok=False, code=code, message=message)


def item_of(id: uuid.UUID, error: HTTPException) -> ItemResult:
    """Een fout van een los endpoint (zie fail) als resultaat voor één item."""
    detail = error.detail if isinstance(error.detail, dict) else {}
    return ItemResult(
        id=id,
        ok=False,
        code=str(detail.get("code") or "error"),
        message=str(detail.get("message") or error.detail),
    )


def message_of(error: HTTPException) -> str:
    detail = error.detail
    return str(detail.get("message", "")) if isinstance(detail, dict) else str(detail)


def _field(location: Any) -> str:
    return ".".join(str(part) for part in location or ())


async def invalid_input(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 voor een verzoek dat niet door de validatie komt, in de vorm van fail()."""
    found = jsonable_encoder(exc.errors())
    first = found[0] if found else {}
    detail = {
        "message": INVALID_INPUT,
        "code": "invalid_input",
        "params": {"field": _field(first.get("loc"))},
        "errors": found,
    }
    return JSONResponse(status_code=422, content={"detail": detail})
