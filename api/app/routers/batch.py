"""Bulkacties op veel bestanden en mappen tegelijk (kopiëren, verplaatsen, weggooien,
tags): per id een errors.ItemResult.

Stub van P0: de router staat al in main.py, zonder routes; P2 vult hem.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["bestanden"])
