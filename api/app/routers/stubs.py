"""De stubs van fase 6 (bouwontwerp §3.5): elk eindpunt van §3.4 bestaat met zijn pad, methode,
body en antwoordmodel, en geeft 501 not_built tot zijn pakket het vult. De inlog (en voor beheer
de rol) wordt eerst gecontroleerd, zoals straks bij de echte route.
"""

from fastapi import HTTPException

from app import errors


def not_built(package: str) -> HTTPException:
    """raise not_built("Z3"): het pakket dat deze route bouwt."""
    return errors.fail(501, "not_built", f"Nog niet gebouwd ({package})", package=package)
