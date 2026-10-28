"""De eigen omzetter van ondertitels naar WebVTT (Keuze 29): SRT, ASS, SSA, WebVTT. Draait in de
worker (bytes van een gebruiker). Stub van M0; M5 bouwt hem."""

from __future__ import annotations


def to_vtt(data: bytes, kind: str) -> bytes:
    raise NotImplementedError("M5")
