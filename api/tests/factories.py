"""Hulpjes voor tests.

message_of(r) en code_of(r): de tekst en de code van een foutantwoord
({"detail": {"message", "code", "params"?}}, zie app/errors.py). Een oud antwoord met alleen
een tekst als detail werkt ook.
"""

from typing import Any


def detail_of(r) -> Any:
    return r.json().get("detail")


def message_of(r) -> str:
    detail = detail_of(r)
    return detail.get("message", "") if isinstance(detail, dict) else detail


def code_of(r) -> str | None:
    detail = detail_of(r)
    return detail.get("code") if isinstance(detail, dict) else None


def params_of(r) -> dict:
    detail = detail_of(r)
    return (detail.get("params") or {}) if isinstance(detail, dict) else {}
