"""Een zip van mappen en bestanden: eerst een link (POST /archive/link), dan een streamende
download.

Stub van P0: de router staat al in main.py, zonder routes; P2 vult hem.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["bestanden"])
